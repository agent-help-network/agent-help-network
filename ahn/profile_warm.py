"""Pre-read the declared policy (robots.txt, llms.txt) of the public seed host list.

Run by the review cron a few hosts at a time, so a first lookup of a well-known host finds a
cached access profile instead of an empty one. Every read goes through
``access_profile.get_profile``, which keeps all of its safety limits (eligible public apex
hosts only, public IPs, no redirects, byte caps, at most one attempt per host per window,
success or failure). The seed list is public: ``ahn/seed_hosts.txt``.
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
    and errors (robots_status 'error', or a host whose read raised). One host's failure never
    stops the run.
    """
    now = now or datetime.now(timezone.utc)
    seeds = load_seed_hosts()
    result = {"candidates": 0, "read": 0, "ok": 0, "errors": 0}
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT site_host,fetched_at,last_error_at FROM host_access_profile WHERE site_host=ANY(%s)",
            (seeds,),
        ).fetchall()
    latest_attempt = {
        row["site_host"]: max((t for t in (row["fetched_at"], row["last_error_at"]) if t is not None), default=None)
        for row in rows
    }
    cutoff = now - timedelta(hours=max_age_hours)
    due = [host for host in seeds if latest_attempt.get(host) is None or latest_attempt[host] < cutoff]
    chosen = due[:max(batch, 0)]
    options = {} if reader is None else {"reader": reader}
    started = _clock()
    processed = 0
    for index, host in enumerate(chosen):
        if index:
            if _clock() - started > time_budget:
                break
            time.sleep(PAUSE_SECONDS)
        processed += 1
        try:
            profile = get_profile(settings, host, max_age_hours=max_age_hours, now=now, **options)
        except Exception:  # one misbehaving host or a brief database error must not stop the cron
            result["errors"] += 1
            continue
        if profile is None:
            continue
        result["read"] += 1
        if profile["robots_status"] == "ok":
            result["ok"] += 1
        elif profile["robots_status"] == "error":
            result["errors"] += 1
    result["candidates"] = len(due) - processed
    return result
