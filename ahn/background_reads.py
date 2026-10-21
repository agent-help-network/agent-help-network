"""Reads of third-party hosts, taken off the lookup path.

A lookup never waits on a target host or a status provider. When it needs a read (a robots.txt and
llms.txt profile, a status-feed refresh, or a first Statuspage discovery) it inserts one row into
``background_reads`` and returns at once with what the cache holds (``pending`` or ``stale``). Two
processors drain the table with the same function, ``process_due``:

* a small worker inside the web process (two daemon threads claiming one job at a time, woken by
  the lookup that queued the row, and polling every POLL_SECONDS for rows queued elsewhere);
  ``AHN_BACKGROUND_READS=0`` turns it off;
* the 5-minute review cron (``python -m ahn.auto_review``), so nothing is lost when the web process
  restarts. Its ``drain`` returns when its time budget is spent, even if a read is still running.

The reads themselves are unchanged: ``access_profile.get_profile``, ``status_refresh.refresh_one``
and ``status_refresh.discover_source`` keep every rule they had (eligible hosts only, public IPs,
byte caps, a whole-body time limit, the 24-hour cache and one-hour retry after a transport failure,
per-host locks).

Queue rules: at most QUEUE_CAP rows (a lookup that would exceed it queues nothing); jobs run in the
order access_profile, status_discovery, status_feed, oldest first. A claim takes rows with
``SELECT ... FOR UPDATE SKIP LOCKED``, counts the attempt and sets a lease (``claimed_at``), and the
lease is renewed just before the job starts, so two processes or threads never read the same row
twice. A read that ends in any result, a transport failure included, is a finished job: the cache
records the outcome and its retry time. A job that raises, or whose processor died, is retried with
backoff (2^attempts minutes, at most 60) and dropped after MAX_ATTEMPTS attempts. Reads are paced to
at most two per second per process.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from . import access_profile, status_refresh, status_scope
from .db import connect
from .provenance import operator_id, vantage

LOG = logging.getLogger(__name__)
KINDS = ("access_profile", "status_discovery", "status_feed")  # also the processing order
QUEUE_CAP = 2000
CLAIM_BATCH = 5            # the cron's claim size; the web worker claims one job at a time
WEB_CLAIM_BATCH = 1
MAX_ATTEMPTS = 5
LEASE_MINUTES = 5          # a claim older than this is considered abandoned (crashed or cut-off processor)
MAX_BACKOFF_MINUTES = 60
MIN_READ_INTERVAL = 0.5    # seconds between external reads in one process: at most 2 per second
POLL_SECONDS = 30.0
WORKER_THREADS = 2
JOB_SECONDS = 2.0          # rough time of one job for the retry estimate (paced reads plus network)
RETRY_AFTER_MIN = 5
RETRY_AFTER_MAX = 300      # also what a lookup says while only the 5-minute cron processes the queue
FEED_STALE_HOURS = 2       # a feed is refreshed behind a lookup once its answer would read as stale
FEED_RETRY_MINUTES = 15    # ... but not again within this long after the last attempt
# While the cached indicator is active, the read is due after FEED_RETRY_MINUTES instead (D43), on the
# lookup path and in the cron (status_refresh.refresh_batch): a resolved problem must not read as active
# for hours. The minimum gap between two attempts stays FEED_RETRY_MINUTES.
FEED_ACTIVE_INDICATORS = status_scope.ACTIVE_INDICATORS
_PRIORITY = "CASE {col} WHEN 'access_profile' THEN 0 WHEN 'status_discovery' THEN 1 ELSE 2 END"

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


def feed_refresh_after(indicator: str | None, idle: timedelta) -> timedelta:
    """How old a successful feed read may get before it is due again: FEED_RETRY_MINUTES while the cached
    indicator is active, otherwise ``idle`` (FEED_STALE_HOURS on the lookup path, one hour in the cron)."""
    return timedelta(minutes=FEED_RETRY_MINUTES) if indicator in FEED_ACTIVE_INDICATORS else idle


FEED_BACKOFF_MAX = timedelta(hours=24)         # refusals only
FEED_FAILURE_BACKOFF_MAX = timedelta(hours=1)  # any other failure: below FEED_STALE_HOURS
FEED_REFUSED_BACKOFF_MINUTES = 60   # a refusal is the provider's answer: start slower and never retry around it
FEED_REFUSALS = frozenset({"http_401", "http_403", "http_429"})


def feed_backoff(failures: int | None, error_detail: str | None, indicator: str | None = None) -> timedelta:
    """Wait after the latest of ``failures`` consecutive failed reads (D44).

    A refusal (401, 403, 429) is the provider's answer: FEED_REFUSED_BACKOFF_MINUTES, doubled with each further
    refusal, at most FEED_BACKOFF_MAX. Any other failure (a timeout, a 5xx, too large) is often the status page
    struggling during the very outage it should report, so it never waits longer than FEED_FAILURE_BACKOFF_MAX
    (shorter than the FEED_STALE_HOURS after which the answer reads as stale and stops advising a wait), and a feed
    whose last successful statement was active (``indicator``) keeps its FEED_RETRY_MINUTES interval."""
    exponent = min(max(1, failures or 1) - 1, 16)
    if error_detail in FEED_REFUSALS:
        return min(FEED_BACKOFF_MAX, timedelta(minutes=FEED_REFUSED_BACKOFF_MINUTES * 2 ** exponent))
    if indicator in FEED_ACTIVE_INDICATORS:
        return timedelta(minutes=FEED_RETRY_MINUTES)
    return min(FEED_FAILURE_BACKOFF_MAX, timedelta(minutes=FEED_RETRY_MINUTES * 2 ** exponent))


def feed_next_due(row, idle: timedelta) -> datetime | None:
    """When a feed is next due, from its status_cache row (indicator, fetched_at, last_error_at, failures,
    last_error_detail); None when it was never attempted (due now). The lookup path (idle FEED_STALE_HOURS) and
    the cron (idle one hour) use this one rule: a success is due again after feed_refresh_after, and a failure
    after feed_backoff; both must have passed."""
    fetched_at, error_at = row.get("fetched_at"), row.get("last_error_at")
    if fetched_at is None and error_at is None:
        return None
    due = [fetched_at + feed_refresh_after(row.get("indicator"), idle)] if fetched_at is not None else []
    if error_at is not None and (fetched_at is None or error_at >= fetched_at):
        due.append(error_at + feed_backoff(row.get("failures"), row.get("last_error_detail"), row.get("indicator")))
    elif error_at is not None:
        due.append(error_at + timedelta(minutes=FEED_RETRY_MINUTES))
    return max(due)


def enabled_by_env() -> bool:
    return os.environ.get("AHN_BACKGROUND_READS", "1").strip() != "0"


def worker_running() -> bool:
    return any(thread.is_alive() for thread in _workers)


def enqueue(conn, jobs) -> list[tuple[str, str]]:
    """Queue (kind, host) jobs without any I/O; returns the jobs that are now in the queue.

    A job already queued stays as it is. A new job is not added once the queue holds QUEUE_CAP rows.
    """
    queued = []
    for kind, site_host in jobs:
        if kind not in KINDS:
            raise ValueError("unknown background read kind")
        exists = "SELECT 1 AS present FROM background_reads WHERE kind=%s AND site_host=%s"
        if conn.execute(exists, (kind, site_host)).fetchone():
            queued.append((kind, site_host))
            continue
        inserted = conn.execute(
            """INSERT INTO background_reads (kind,site_host)
               SELECT %s,%s WHERE (SELECT count(*) FROM background_reads) < %s
               ON CONFLICT (kind,site_host) DO NOTHING RETURNING kind""",
            (kind, site_host, QUEUE_CAP),
        ).fetchone()
        if inserted or conn.execute(exists, (kind, site_host)).fetchone():
            queued.append((kind, site_host))
    return queued


def estimate_wait(conn, kind: str, site_host: str, *, now: datetime | None = None) -> int | None:
    """Seconds until a queued job is likely done, from its place in the queue and its not_before.

    RETRY_AFTER_MIN to RETRY_AFTER_MAX; RETRY_AFTER_MAX while no worker runs in this process (the
    cron comes every five minutes). None when the job is not queued.
    """
    row = conn.execute(
        f"""WITH me AS (SELECT requested_at,not_before,{_PRIORITY.format(col="kind")} AS p
                        FROM background_reads WHERE kind=%s AND site_host=%s)
            SELECT me.not_before,
                   (SELECT count(*) FROM background_reads o
                    WHERE ({_PRIORITY.format(col="o.kind")}, o.requested_at) < (me.p, me.requested_at)) AS ahead
            FROM me""",
        (kind, site_host),
    ).fetchone()
    if not row:
        return None
    if not worker_running():
        return RETRY_AFTER_MAX
    now = now or datetime.now(timezone.utc)
    delay = max(0.0, (row["not_before"] - now).total_seconds()) if row["not_before"] else 0.0
    seconds = delay + (row["ahead"] + 1) * JOB_SECONDS / WORKER_THREADS
    return int(min(RETRY_AFTER_MAX, max(RETRY_AFTER_MIN, math.ceil(seconds))))


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


def _claim(settings, limit: int) -> tuple[list[dict], int]:
    """Drop exhausted rows, then claim up to ``limit`` due rows; returns (jobs in run order, dropped)."""
    with connect(settings) as conn:
        dropped = conn.execute(
            f"""DELETE FROM background_reads WHERE attempts >= %s
                AND (claimed_at IS NULL OR claimed_at < now() - interval '{LEASE_MINUTES} minutes')""",
            (MAX_ATTEMPTS,),
        ).rowcount
        jobs = conn.execute(
            f"""WITH c AS (
                    SELECT kind,site_host FROM background_reads
                    WHERE not_before <= now() AND attempts < %s
                      AND (claimed_at IS NULL OR claimed_at < now() - interval '{LEASE_MINUTES} minutes')
                    ORDER BY {_PRIORITY.format(col="kind")},requested_at,site_host LIMIT %s
                    FOR UPDATE SKIP LOCKED)
                UPDATE background_reads b SET claimed_at=clock_timestamp(), attempts=b.attempts+1
                FROM c WHERE b.kind=c.kind AND b.site_host=c.site_host
                RETURNING b.kind,b.site_host,b.attempts,b.claimed_at,b.requested_at""",
            (MAX_ATTEMPTS, limit),
        ).fetchall()
    jobs.sort(key=lambda job: (KINDS.index(job["kind"]), job["requested_at"], job["site_host"]))
    return jobs, max(dropped or 0, 0)


def _renew(settings, job: dict):
    """Re-check the lease just before a job starts; the new stamp, or None when another processor took it."""
    with connect(settings) as conn:
        row = conn.execute(
            """UPDATE background_reads SET claimed_at=clock_timestamp()
               WHERE kind=%s AND site_host=%s AND claimed_at=%s RETURNING claimed_at""",
            (job["kind"], job["site_host"], job["claimed_at"]),
        ).fetchone()
    return row["claimed_at"] if row else None


def _finish(settings, job: dict) -> None:
    with connect(settings) as conn:
        conn.execute(
            "DELETE FROM background_reads WHERE kind=%s AND site_host=%s AND claimed_at=%s",
            (job["kind"], job["site_host"], job["claimed_at"]),
        )


def _fail(settings, job: dict) -> bool:
    """Back off a job that raised (its attempt was counted at the claim); True when it was dropped."""
    attempts = job["attempts"]
    with connect(settings) as conn:
        if attempts >= MAX_ATTEMPTS:
            conn.execute(
                "DELETE FROM background_reads WHERE kind=%s AND site_host=%s AND claimed_at=%s",
                (job["kind"], job["site_host"], job["claimed_at"]),
            )
            return True
        conn.execute(
            f"""UPDATE background_reads SET claimed_at=NULL,
                   not_before=now() + least(power(2,%s) * interval '1 minute', interval '{MAX_BACKOFF_MINUTES} minutes')
                WHERE kind=%s AND site_host=%s AND claimed_at=%s""",
            (attempts, job["kind"], job["site_host"], job["claimed_at"]),
        )
    return False


def _release(settings, jobs: list[dict]) -> None:
    """Give back claimed jobs that were not started (time budget or shutdown); the attempt is not counted."""
    if not jobs:
        return
    with connect(settings) as conn:
        for job in jobs:
            conn.execute(
                """UPDATE background_reads SET claimed_at=NULL, attempts=greatest(attempts-1,0)
                   WHERE kind=%s AND site_host=%s AND claimed_at=%s""",
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
               reader_version=EXCLUDED.reader_version,body_sha256=EXCLUDED.body_sha256,
               active_incidents=NULL,max_incident_impact=NULL,incident_updated_at=NULL,
               active_maintenances=NULL,affected=NULL,affected_regional=NULL,
               scope_failures=NULL,scope_failed_at=NULL,scope_error=NULL,failures=NULL""",
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
        # provider_discovery_attempted_at is the time discovery actually ran, not when it was queued.
        with connect(settings) as conn:
            conn.execute("UPDATE queried_hosts SET last_discovery_at=now() WHERE site_host=%s", (site_host,))
        _pace()
        digest: dict = {}
        found = discover(site_host, digest)
        if found:
            _store_discovered(settings, site_host, found, digest)


def _empty_result() -> dict:
    return {"claimed": 0, "done": 0, "failed": 0, "dropped": 0, "skipped": 0}


def process_due(settings, *, limit: int = CLAIM_BATCH, time_budget: float | None = None,
                stop: threading.Event | None = None) -> dict:
    """Claim up to ``limit`` due jobs and run them; the per-call counts are returned.

    No new job starts after ``time_budget`` seconds or once ``stop`` is set; unstarted claims are
    released for the next processor. A job whose lease was taken over in the meantime is skipped.
    """
    result = _empty_result()
    jobs, result["dropped"] = _claim(settings, limit)
    result["claimed"] = len(jobs)
    started = _clock()
    for index, job in enumerate(jobs):
        if (stop is not None and stop.is_set()) or (time_budget is not None and _clock() - started > time_budget):
            try:
                _release(settings, jobs[index:])
            except Exception:
                LOG.warning("background_read_release_unavailable")
            break
        try:
            stamp = _renew(settings, job)
            if stamp is None:
                result["skipped"] += 1
                continue
            job = {**job, "claimed_at": stamp}
            _run(settings, job)
            _finish(settings, job)
        except Exception as exc:  # one host or a brief database error must not stop the queue
            LOG.warning("background_read_failed kind=%s error=%s", job["kind"], type(exc).__name__)
            result["failed"] += 1
            try:
                if _fail(settings, job):
                    result["dropped"] += 1
            except Exception:
                LOG.warning("background_read_backoff_unavailable")
            continue
        result["done"] += 1
    return result


def drain(settings, *, time_budget: float, batch: int = CLAIM_BATCH) -> dict:
    """The cron's step: process due jobs until none is left or ``time_budget`` seconds have passed.

    The jobs run in a daemon thread. When the budget is spent this returns at once, even if a read is
    still running ("cut": 1); that job's lease then expires and its attempt stays counted.
    """
    total = _empty_result()
    stop = threading.Event()
    failure: list[BaseException] = []

    def work():
        started = _clock()
        try:
            while not stop.is_set():
                remaining = time_budget - (_clock() - started)
                if remaining <= 0:
                    break
                step = process_due(settings, limit=batch, time_budget=remaining, stop=stop)
                for key in total:
                    total[key] += step[key]
                if not step["claimed"]:
                    break
        except BaseException as exc:  # handed to the caller below
            failure.append(exc)

    thread = threading.Thread(target=work, name="ahn-background-reads-drain", daemon=True)
    thread.start()
    thread.join(max(time_budget, 0.0))
    result = dict(total)
    result["cut"] = 0
    if thread.is_alive():
        stop.set()
        result["cut"] = 1
    elif failure:
        raise failure[0]
    with connect(settings) as conn:
        result["remaining"] = conn.execute("SELECT count(*) AS n FROM background_reads").fetchone()["n"]
    return result


def _worker_loop(settings, stop: threading.Event) -> None:
    while not stop.is_set():
        _wake.wait(POLL_SECONDS)
        _wake.clear()
        if stop.is_set():
            break
        try:
            while not stop.is_set() and process_due(settings, limit=WEB_CLAIM_BATCH, stop=stop)["claimed"]:
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
