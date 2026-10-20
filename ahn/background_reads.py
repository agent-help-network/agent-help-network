"""Reads of third-party hosts, taken off the lookup path.

A lookup never waits on a target host or a status provider. When it needs a read (a robots.txt and
llms.txt profile, a status-feed refresh, or a first Statuspage discovery) it inserts one row into
``background_reads`` and returns at once with what the cache holds (``pending`` or ``stale``). Two
processors drain the table with the same function, ``process_due``:

* a small worker inside the web process (two daemon threads, woken by the lookup that queued the
  row, and polling every POLL_SECONDS for rows queued elsewhere); ``AHN_BACKGROUND_READS=0``
  turns it off;
* the 5-minute review cron (``python -m ahn.auto_review``), so nothing is lost when the web process
  restarts.

The reads themselves are unchanged: ``access_profile.get_profile``, ``status_refresh.refresh_one``
and ``status_refresh.discover_source`` keep every rule they had (eligible hosts only, public IPs,
byte caps, the 24-hour cache and one-hour retry after a transport failure, per-host locks). A row is
claimed with ``SELECT ... FOR UPDATE SKIP LOCKED`` plus a lease (``claimed_at``), so two processes or
threads never read the same row twice. A read that ends in a transport failure is a finished job:
the cache records the failure and its retry time. Only a job that raises is retried, with backoff
(2^attempts minutes, at most 60), and dropped after MAX_ATTEMPTS. Reads are paced to at most
two per second per process.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from psycopg.types.json import Jsonb

from . import access_profile, status_refresh
from .db import connect
from .provenance import operator_id, vantage

LOG = logging.getLogger(__name__)
KINDS = ("access_profile", "status_feed", "status_discovery")
CLAIM_BATCH = 5
MAX_ATTEMPTS = 5
LEASE_MINUTES = 5          # a claim older than this is considered abandoned (crashed process)
MAX_BACKOFF_MINUTES = 60
MIN_READ_INTERVAL = 0.5    # seconds between external reads in one process: at most 2 per second
POLL_SECONDS = 30.0
WORKER_THREADS = 2
RETRY_AFTER_SECONDS = 5    # what a lookup tells the caller while the in-process worker is running
RETRY_AFTER_CRON_SECONDS = 300  # ... and while only the 5-minute cron processes the queue
FEED_REFRESH_SECONDS = 15  # same spacing refresh_one applies (min_interval) when a lookup refreshed inline

_wake = threading.Event()
_pace_lock = threading.Lock()
_last_read = [0.0]
_clock = time.monotonic
_sleep = time.sleep
_workers: list[threading.Thread] = []
_stop = threading.Event()


# The external calls, looked up at call time so tests can replace them (tests/conftest.py blocks them).
def read_profile_file(site_host, path, *, cap_bytes, **options):
    return access_profile.fetch_public_file(site_host, path, cap_bytes=cap_bytes, **options)


def refresh_feed(settings, site_host):
    return status_refresh.refresh_one(settings, site_host, wait=True)


def discover(site_host, digest=None):
    return status_refresh.discover_source(site_host, digest=digest)


def enabled_by_env() -> bool:
    return os.environ.get("AHN_BACKGROUND_READS", "1").strip() != "0"


def worker_running() -> bool:
    return any(thread.is_alive() for thread in _workers)


def retry_after_seconds() -> int:
    """How long a caller should wait before asking again for a queued read."""
    return RETRY_AFTER_SECONDS if worker_running() else RETRY_AFTER_CRON_SECONDS


def enqueue(conn, jobs) -> None:
    """Queue (kind, host) jobs; an already queued job is kept as is. One statement per job, no I/O."""
    for kind, site_host in jobs:
        if kind not in KINDS:
            raise ValueError("unknown background read kind")
        conn.execute(
            "INSERT INTO background_reads (kind,site_host) VALUES (%s,%s) ON CONFLICT (kind,site_host) DO NOTHING",
            (kind, site_host),
        )


def wake() -> None:
    _wake.set()


def _pace() -> None:
    """Block until MIN_READ_INTERVAL has passed since the previous external read in this process."""
    with _pace_lock:
        wait = _last_read[0] + MIN_READ_INTERVAL - _clock()
        if wait > 0:
            _sleep(wait)
        _last_read[0] = _clock()


def _paced_reader(site_host, path, *, cap_bytes, **options):
    _pace()
    return read_profile_file(site_host, path, cap_bytes=cap_bytes, **options)


def _claim(settings, limit: int) -> list[dict]:
    with connect(settings) as conn:
        return conn.execute(
            f"""UPDATE background_reads SET claimed_at=clock_timestamp()
                WHERE (kind,site_host) IN (
                    SELECT kind,site_host FROM background_reads
                    WHERE not_before <= now()
                      AND (claimed_at IS NULL OR claimed_at < now() - interval '{LEASE_MINUTES} minutes')
                    ORDER BY requested_at,kind,site_host LIMIT %s
                    FOR UPDATE SKIP LOCKED)
                RETURNING kind,site_host,attempts,claimed_at""",
            (limit,),
        ).fetchall()


def _finish(settings, job: dict) -> None:
    with connect(settings) as conn:
        conn.execute(
            "DELETE FROM background_reads WHERE kind=%s AND site_host=%s AND claimed_at=%s",
            (job["kind"], job["site_host"], job["claimed_at"]),
        )


def _fail(settings, job: dict) -> bool:
    """Back off a job that raised; True when it was dropped after MAX_ATTEMPTS."""
    attempts = job["attempts"] + 1
    with connect(settings) as conn:
        if attempts >= MAX_ATTEMPTS:
            conn.execute(
                "DELETE FROM background_reads WHERE kind=%s AND site_host=%s AND claimed_at=%s",
                (job["kind"], job["site_host"], job["claimed_at"]),
            )
            return True
        conn.execute(
            f"""UPDATE background_reads SET attempts=%s,claimed_at=NULL,
                   not_before=now() + least(power(2,%s) * interval '1 minute', interval '{MAX_BACKOFF_MINUTES} minutes')
                WHERE kind=%s AND site_host=%s AND claimed_at=%s""",
            (attempts, attempts, job["kind"], job["site_host"], job["claimed_at"]),
        )
    return False


def _release(settings, jobs: list[dict]) -> None:
    """Give back claimed jobs that were not started (time budget or shutdown)."""
    if not jobs:
        return
    with connect(settings) as conn:
        for job in jobs:
            conn.execute(
                "UPDATE background_reads SET claimed_at=NULL WHERE kind=%s AND site_host=%s AND claimed_at=%s",
                (job["kind"], job["site_host"], job["claimed_at"]),
            )


def _store_discovered(settings, site_host: str, found, digest: dict) -> None:
    source, indicator, updated_at = found
    with connect(settings) as conn:
        if not status_refresh.register_source(conn, site_host, source):
            return
        conn.execute(
            "UPDATE status_sources SET curated_by=%s,evidence='discovered_cname',evidence_url=%s WHERE site_host=%s",
            (operator_id(), source.page_url, site_host),
        )
        conn.execute(
            """INSERT INTO status_cache (site_host,indicator,source_updated_at,fetched_at,last_error_at,last_error_detail,
                                         origin_operator,vantage,reader_version,body_sha256)
               VALUES (%s,%s,%s,now(),NULL,NULL,%s,%s,%s,%s) ON CONFLICT (site_host) DO UPDATE SET
               indicator=EXCLUDED.indicator,source_updated_at=EXCLUDED.source_updated_at,
               fetched_at=EXCLUDED.fetched_at,last_error_at=NULL,last_error_detail=NULL,
               origin_operator=EXCLUDED.origin_operator,vantage=EXCLUDED.vantage,
               reader_version=EXCLUDED.reader_version,body_sha256=EXCLUDED.body_sha256""",
            (site_host, indicator, updated_at, operator_id(), Jsonb(vantage()), access_profile.USER_AGENT,
             digest.get("body_sha256")),
        )


def _run(settings, job: dict) -> None:
    """One job; raises only when processing itself failed (the read outcome lives in the cache)."""
    kind, site_host = job["kind"], job["site_host"]
    if kind == "access_profile":
        access_profile.get_profile(settings, site_host, reader=_paced_reader)
    elif kind == "status_feed":
        _pace()
        refresh_feed(settings, site_host)
    elif kind == "status_discovery":
        _pace()
        digest: dict = {}
        found = discover(site_host, digest)
        if found:
            _store_discovered(settings, site_host, found, digest)


def process_due(settings, *, limit: int = CLAIM_BATCH, time_budget: float | None = None,
                stop: threading.Event | None = None) -> dict:
    """Claim up to ``limit`` due jobs and run them; the per-call counts are returned.

    No new job starts after ``time_budget`` seconds or once ``stop`` is set; unstarted claims are
    released for the next processor.
    """
    result = {"claimed": 0, "done": 0, "failed": 0, "dropped": 0}
    jobs = _claim(settings, limit)
    result["claimed"] = len(jobs)
    started = _clock()
    for index, job in enumerate(jobs):
        if (stop is not None and stop.is_set()) or (time_budget is not None and _clock() - started > time_budget):
            _release(settings, jobs[index:])
            break
        try:
            _run(settings, job)
        except Exception as exc:  # one host or a brief database error must not stop the queue
            LOG.warning("background_read_failed kind=%s error=%s", job["kind"], type(exc).__name__)
            result["failed"] += 1
            try:
                if _fail(settings, job):
                    result["dropped"] += 1
            except Exception:
                LOG.warning("background_read_backoff_unavailable")
            continue
        _finish(settings, job)
        result["done"] += 1
    return result


def drain(settings, *, time_budget: float, batch: int = CLAIM_BATCH) -> dict:
    """Process due jobs until none is left or ``time_budget`` seconds have passed (the cron's step)."""
    total = {"claimed": 0, "done": 0, "failed": 0, "dropped": 0}
    started = _clock()
    while True:
        remaining = time_budget - (_clock() - started)
        if remaining <= 0:
            break
        step = process_due(settings, limit=batch, time_budget=remaining)
        for key in total:
            total[key] += step[key]
        if not step["claimed"]:
            break
    with connect(settings) as conn:
        total["remaining"] = conn.execute("SELECT count(*) AS n FROM background_reads").fetchone()["n"]
    return total


def _worker_loop(settings, stop: threading.Event) -> None:
    while not stop.is_set():
        _wake.wait(POLL_SECONDS)
        _wake.clear()
        if stop.is_set():
            break
        try:
            while not stop.is_set() and process_due(settings, stop=stop)["claimed"]:
                pass
        except Exception as exc:  # database outage: keep the thread alive and try again later
            LOG.warning("background_reads_unavailable error=%s", type(exc).__name__)
            stop.wait(POLL_SECONDS)


def start_worker(settings) -> bool:
    """Start the in-process worker for a public, non-test deployment unless AHN_BACKGROUND_READS=0."""
    if not (settings.public_mode and not settings.test_mode and enabled_by_env()) or worker_running():
        return False
    _stop.clear()
    _workers.clear()
    for number in range(WORKER_THREADS):
        thread = threading.Thread(target=_worker_loop, args=(settings, _stop), name=f"ahn-background-reads-{number}", daemon=True)
        thread.start()
        _workers.append(thread)
    _wake.set()  # pick up anything left from before a restart
    return True


def stop_worker(timeout: float = 5.0) -> None:
    _stop.set()
    _wake.set()
    for thread in _workers:
        thread.join(timeout)
    _workers.clear()

