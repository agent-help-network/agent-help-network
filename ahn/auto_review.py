"""Conservative, rule-based publication of unverified public observations.

This is a standalone Render Cron Job. It checks consistency and abuse signals;
it cannot establish whether the reported website event actually happened.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from psycopg.rows import dict_row

from .db import connect_url
from .service import aggregate_eligible_host, policy_host


MIN_REVIEW_AGE = timedelta(minutes=10)
MAX_OBSERVATION_AGE = timedelta(hours=24)
HOST_WINDOW = timedelta(hours=24)
BURST_WINDOW = timedelta(minutes=15)
PUBLIC_STEPS = {"site_open", "login", "human_verification", "service_use", "other"}


def _review_hosts(site_host: str) -> list[str]:
    """The stored hosts whose reports share site_host's burst and publication windows.

    www.<eligible apex> reviews as its apex, and the two names count together so a pair of
    reports split across them cannot double the publication rate for one site. This only
    groups the abuse counts; reports and incidents stay keyed by their exact site_host.
    """
    apex = policy_host(site_host)
    return [apex, f"www.{apex}"]


def _decision(conn, row: dict, now: datetime) -> str:
    """Return 'publish' or a short reason for private operator review."""
    if row["outcome"] not in {"blocked", "error", "passed"}:
        return "uncertain_outcome"
    if not aggregate_eligible_host(policy_host(row["site_host"])):
        return "unreviewed_subdomain"
    hosts = _review_hosts(row["site_host"])
    if row["task_step"] not in PUBLIC_STEPS:
        return "unexpected_step"
    if row["observed_at"] < row["received_at"] - MAX_OBSERVATION_AGE:
        return "old_observation"
    if row["observed_at"] > row["received_at"] + timedelta(minutes=5):
        return "future_observation"
    burst = conn.execute(
        """SELECT count(*) AS n FROM observations o JOIN incidents i ON i.id=o.incident_id
        WHERE i.site_host = ANY(%s) AND o.synthetic=false AND o.withdrawn_at IS NULL
          AND o.received_at >= %s AND o.received_at <= %s""",
        (hosts, row["received_at"] - BURST_WINDOW, row["received_at"] + BURST_WINDOW),
    ).fetchone()["n"]
    if burst > 2:
        return "host_burst"
    published = conn.execute(
        """SELECT count(*) AS n FROM observations o JOIN incidents i ON i.id=o.incident_id
        WHERE i.site_host = ANY(%s) AND o.outcome=%s AND o.review_state='published' AND o.synthetic=false
          AND o.withdrawn_at IS NULL AND o.received_at >= %s""",
        (hosts, row["outcome"], now - HOST_WINDOW),
    ).fetchone()["n"]
    if published:
        return "recent_host_publication"
    prior = conn.execute(
        """SELECT count(*) AS n FROM observations
        WHERE incident_id=%s AND outcome=%s AND id<>%s AND synthetic=false AND withdrawn_at IS NULL
          AND (received_at < %s OR (received_at = %s AND id < %s))
          AND received_at >= %s""",
        (row["incident_id"], row["outcome"], row["id"], row["received_at"], row["received_at"], row["id"], now - HOST_WINDOW),
    ).fetchone()["n"]
    if prior:
        return "repeat_incident"
    return "publish"


def review_batch(database_url: str, *, now: datetime | None = None, limit: int = 100) -> dict[str, int]:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be 1-500")
    now = now or datetime.now(timezone.utc)
    result = {"published": 0, "held": 0}
    with connect_url(database_url, row_factory=dict_row) as conn:
        acquired = conn.execute("SELECT pg_try_advisory_xact_lock(81260531003) AS ok").fetchone()["ok"]
        if not acquired:
            return result
        publication = conn.execute("SELECT enabled FROM service_flags WHERE name='public_publication' FOR SHARE").fetchone()
        if not publication or not publication["enabled"]:
            return result
        # A concurrent manual action may change a selected row; row locks prevent
        # publication over a withdrawal or manual hide in the same transaction.
        rows = conn.execute(
            """SELECT o.id,o.incident_id,o.observed_at,o.received_at,o.outcome,
                      o.attempt_env,i.site_host,i.task_step
               FROM observations o JOIN incidents i ON i.id=o.incident_id
               WHERE o.review_state='pending' AND o.review_reason IS NULL
                 AND o.synthetic=false AND o.withdrawn_at IS NULL
                 AND o.received_at <= %s
               ORDER BY o.received_at,o.id LIMIT %s FOR UPDATE OF o SKIP LOCKED""",
            (now - MIN_REVIEW_AGE, limit),
        ).fetchall()
        for row in rows:
            decision = _decision(conn, row, now)
            if decision == "publish":
                conn.execute(
                    "UPDATE observations SET review_state='published',review_reason='auto_unverified',reviewed_at=%s WHERE id=%s",
                    (now, row["id"]),
                )
                conn.execute("UPDATE incidents SET visibility='published' WHERE id=%s AND visibility<>'hidden'", (row["incident_id"],))
                conn.execute(
                    "INSERT INTO audit_events (event_type,record_id) VALUES ('observation_auto_published',%s)",
                    (row["id"],),
                )
                result["published"] += 1
            else:
                conn.execute(
                    "UPDATE observations SET review_reason=%s,reviewed_at=%s WHERE id=%s",
                    (decision, now, row["id"]),
                )
                conn.execute(
                    "INSERT INTO audit_events (event_type,record_id) VALUES ('observation_auto_held',%s)",
                    (row["id"],),
                )
                result["held"] += 1
    return result


def main() -> None:
    database_url = os.environ.get("AHN_DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("AHN_DATABASE_URL is required")
    result = review_batch(database_url)
    print(f"auto_review published={result['published']} held={result['held']}")
    from .settings import Settings
    from .status_refresh import refresh_batch, sync_curated_sources
    # Every curated feed mapping is registered and read on a schedule, without a lookup (D44).
    try:
        synced = sync_curated_sources(Settings(database_url, "unused-by-status-refresh"))
        print(f"status_sources curated={synced['curated']} added={synced['added']} changed={synced['changed']} removed={synced['removed']}")
    except Exception as exc:  # the lookup-registered feeds below are still read
        print(f"status_sources failed={type(exc).__name__}")
    try:
        source_result = refresh_batch(Settings(database_url, "unused-by-status-refresh"))
        print(f"status_refresh checked={source_result['checked']} updated={source_result['updated']} "
              f"failed={source_result['failed']} remaining={source_result['remaining']}")
    except Exception as exc:  # one step's failure (e.g. a migration the web service has not applied yet) must not skip the rest
        print(f"status_refresh failed={type(exc).__name__}")
    # Reads queued by lookups first, then the seed pre-read, within one shared time budget.
    from .background_reads import drain
    from .profile_warm import TIME_BUDGET_SECONDS, format_causes, warm_profiles
    started = time.monotonic()
    try:
        queue = drain(Settings(database_url, "unused-by-status-refresh"), time_budget=TIME_BUDGET_SECONDS)
        print(f"background_reads done={queue['done']} failed={queue['failed']} dropped={queue['dropped']} "
              f"cut={queue.get('cut', 0)} remaining={queue['remaining']}")
    except Exception as exc:  # e.g. migration 020 not applied yet: the seed step still runs
        print(f"background_reads failed={type(exc).__name__}")
    try:
        warm_batch = max(0, min(50, int(os.environ.get("AHN_WARM_BATCH", "10"))))
    except ValueError:
        warm_batch = 10
    budget = TIME_BUDGET_SECONDS - (time.monotonic() - started)
    if warm_batch and budget <= 0:
        print("profile_warm skipped=time_budget")
    elif warm_batch:
        try:
            warm = warm_profiles(Settings(database_url, "unused-by-status-refresh"), batch=warm_batch, time_budget=budget)
            line = f"profile_warm read={warm['read']} ok={warm['ok']} errors={warm['errors']} remaining={warm['candidates']}"
            if warm.get("causes"):  # why the errors happened: labels such as dns_failed:7,connect_timeout:3, no host names
                line += f" causes={format_causes(warm['causes'])}"
            print(line)
        except Exception as exc:  # the seed pre-read is best effort and must not fail the review cron
            print(f"profile_warm failed={type(exc).__name__}")
    # Last, because it does real work only about once a day: the public government domain lists
    # (U.S. .gov daily, UK .gov.uk monthly). A failed or odd download keeps the stored version.
    try:
        from .gov_registry import refresh_due
        for result in refresh_due(Settings(database_url, "unused-by-status-refresh")):
            print(f"gov_registry registry={result['registry']} outcome={result['outcome']} rows={result['rows']} reason={result['reason']}")
    except Exception as exc:  # e.g. migration 022 not applied yet: nothing else in the cron depends on this step
        print(f"gov_registry failed={type(exc).__name__}")
    # The offline bundle, about once a day, only while AHN_PUBLIC_BUNDLE=1 (ahn/bundle.py).
    from .bundle import refresh_if_due
    try:
        print("bundle " + " ".join(f"{key}={value}" for key, value in refresh_if_due(database_url).items()))
    except Exception as exc:  # a failed build keeps the previous file and must not fail the review cron
        print(f"bundle failed={type(exc).__name__}")


if __name__ == "__main__":
    main()
