"""Pre-read the declared policy (robots.txt, llms.txt) of the public seed host list.

Run by the review cron a few hosts at a time, so a first lookup of a well-known host finds a
cached access profile instead of an empty one. Every read goes through
``access_profile.get_profile``, which keeps all of its safety limits (eligible public apex
hosts and their own www. alias only, public IPs, no redirect beyond one hop to that alias, byte
caps, at most one attempt per host per window, success or failure). The seed list is public:
``ahn/seed_hosts.txt``.

Order of work. Hosts never attempted come first, then hosts whose definitive answer is older than the
window, and hosts whose last attempt was a transport failure come last and wait ``WARM_ERROR_RETRY``.
Before this, dead names (a domain that no longer resolves, a host that drops every packet) came due again
every hour at their place in the file, ahead of every host not yet read, and used the whole batch of every
run: the log showed ``ok=0 errors=10`` and the backlog did not shrink. A lookup of such a host still
retries after ``ERROR_RETRY``; only this background pre-read waits longer.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .access_profile import get_profile
from .db import connect
from .models import valid_host
from .service import aggregate_eligible_host

SEED_PATH = Path(__file__).resolve().parent / "seed_hosts.txt"
PAUSE_SECONDS = 0.5
# How long the pre-read leaves a host whose last attempt failed at the transport level. get_profile (the lookup
# path) keeps its own, shorter ERROR_RETRY, so a host someone asks about is still retried within the hour.
WARM_ERROR_RETRY = timedelta(hours=24)
TIME_BUDGET_SECONDS = 120.0  # keep the 5-minute review cron from overrunning its own schedule
_clock = time.monotonic


def load_seed_hosts(path: str | Path | None = None) -> list[str]:
    """Seed hosts in file order: no comments or blanks, valid public names, eligible apex hosts, no duplicates."""
    hosts: list[str] = []
    seen: set[str] = set()
    with open(path or SEED_PATH, encoding="utf-8-sig") as handle:
        for line in handle:
            host = line.strip()
            if not host or host.startswith("#") or host in seen:
                continue
            try:
                valid_host(host)
            except ValueError:
                continue
            if aggregate_eligible_host(host):
                seen.add(host)
                hosts.append(host)
    return hosts


def warm_profiles(
    settings, *, batch: int = 10, max_age_hours: int = 168, reader=None, now: datetime | None = None,
    time_budget: float = TIME_BUDGET_SECONDS,
) -> dict:
    """Read up to ``batch`` seed hosts that have no profile row or whose last attempt is older than ``max_age_hours``.

    No new host is started once ``time_budget`` seconds have passed; the rest stay due. Returns
    candidates (due hosts left unprocessed), read (profiles returned), ok (robots_status 'ok')
    and errors (robots_status 'error', or a host whose read raised). When errors is not 0, causes
    counts them by label: the stored robots_detail (dns_failed, connect_timeout, ...) or
    ``exception:<type name>`` for a read that raised; labels only, never a host name. One host's
    failure never stops the run.
    """
    now = now or datetime.now(timezone.utc)
    seeds = load_seed_hosts()
    result = {"candidates": 0, "read": 0, "ok": 0, "errors": 0}
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT site_host,fetched_at,last_error_at,robots_status FROM host_access_profile WHERE site_host=ANY(%s)",
            (seeds,),
        ).fetchall()
    latest_attempt = {
        row["site_host"]: max((t for t in (row["fetched_at"], row["last_error_at"]) if t is not None), default=None)
        for row in rows
    }
    # A transport failure after an earlier answer leaves robots_status alone and only moves last_error_at,
    # so "failed last" is judged by the timestamps, the same way get_profile's cache window does.
    failed = {row["site_host"] for row in rows
              if row["last_error_at"] is not None and (row["fetched_at"] is None or row["last_error_at"] > row["fetched_at"])}
    cutoff = now - timedelta(hours=max_age_hours)
    retry_cutoff = now - WARM_ERROR_RETRY
    # Never attempted first, then definitive answers (a refusal included) older than the window, then
    # transport failures that have waited WARM_ERROR_RETRY. A failure always comes last, so dead names
    # cannot keep hosts that have never been read out of the batch.
    never = [host for host in seeds if latest_attempt.get(host) is None]
    expired = [host for host in seeds
               if latest_attempt.get(host) is not None and host not in failed and latest_attempt[host] < cutoff]
    retry = [host for host in seeds if host in failed and latest_attempt[host] < retry_cutoff]
    due = never + expired + retry
    chosen = due[:max(batch, 0)]
    options = {} if reader is None else {"reader": reader}
    started = _clock()
    processed = 0
    causes: dict[str, int] = {}
    for index, host in enumerate(chosen):
        if index:
            if _clock() - started > time_budget:
                break
            time.sleep(PAUSE_SECONDS)
        processed += 1
        try:
            profile = get_profile(settings, host, max_age_hours=max_age_hours, now=now, trigger="seed", **options)
        except Exception as exc:  # one misbehaving host or a brief database error must not stop the cron
            result["errors"] += 1
            label = f"exception:{type(exc).__name__}"
            causes[label] = causes.get(label, 0) + 1
            continue
        if profile is None:
            continue
        result["read"] += 1
        if profile["robots_status"] == "ok":
            result["ok"] += 1
        elif profile["robots_status"] == "error":
            result["errors"] += 1
            label = str(profile.get("robots_detail") or "no_detail")[:32]
            causes[label] = causes.get(label, 0) + 1
    result["candidates"] = len(due) - processed
    if causes:
        result["causes"] = causes
    return result


def format_causes(causes: dict[str, int]) -> str:
    """``dns_failed:7,connect_timeout:3`` (most frequent first) for the cron log line; labels only, no host names."""
    return ",".join(f"{label}:{count}" for label, count in sorted(causes.items(), key=lambda item: (-item[1], item[0])))
