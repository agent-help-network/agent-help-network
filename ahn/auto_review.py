"""Conservative, rule-based publication of unverified public observations.

This is a standalone Render Cron Job. It checks consistency and abuse signals;
it cannot establish whether the reported website event actually happened.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from .service import aggregate_eligible_host


MIN_REVIEW_AGE = timedelta(minutes=10)
MAX_OBSERVATION_AGE = timedelta(hours=24)
HOST_WINDOW = timedelta(hours=24)
BURST_WINDOW = timedelta(minutes=15)
PUBLIC_STEPS = {"site_open", "login", "human_verification", "service_use", "other"}


def _decision(conn, row: dict, now: datetime) -> str:
    """Return 'publish' or a short reason for private operator review."""
    if row["outcome"] not in {"blocked", "error", "passed"}:
        return "uncertain_outcome"
    if not aggregate_eligible_host(row["site_host"]):
        return "unreviewed_subdomain"
    if row["task_step"] not in PUBLIC_STEPS:
        return "unexpected_step"
    if row["observed_at"] < row["received_at"] - MAX_OBSERVATION_AGE:
        return "old_observation"
    if row["observed_at"] > row["received_at"] + timedelta(minutes=5):
        return "future_observation"
    burst = conn.execute(
        """SELECT count(*) AS n FROM observations o JOIN incidents i ON i.id=o.incident_id
        WHERE i.site_host=%s AND o.synthetic=false AND o.withdrawn_at IS NULL
          AND o.received_at >= %s AND o.received_at <= %s""",
        (row["site_host"], row["received_at"] - BURST_WINDOW, row["received_at"] + BURST_WINDOW),
    ).fetchone()["n"]
    if burst > 2:
        return "host_burst"
    published = conn.execute(
        """SELECT count(*) AS n FROM observations o JOIN incidents i ON i.id=o.incident_id
        WHERE i.site_host=%s AND o.outcome=%s AND o.review_state='published' AND o.synthetic=false
          AND o.withdrawn_at IS NULL AND o.received_at >= %s""",
        (row["site_host"], row["outcome"], now - HOST_WINDOW),
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
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
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
    from .status_refresh import refresh_batch
    source_result = refresh_batch(Settings(database_url, "unused-by-status-refresh"))
    print(f"status_refresh checked={source_result['checked']} updated={source_result['updated']} failed={source_result['failed']}")
    from .profile_warm import warm_profiles
    try:
        warm_batch = max(0, min(50, int(os.environ.get("AHN_WARM_BATCH", "10"))))
    except ValueError:
        warm_batch = 10
    if warm_batch:
        try:
            warm = warm_profiles(Settings(database_url, "unused-by-status-refresh"), batch=warm_batch)
            print(f"profile_warm read={warm['read']} ok={warm['ok']} errors={warm['errors']} remaining={warm['candidates']}")
        except Exception as exc:  # the seed pre-read is best effort and must not fail the review cron
            print(f"profile_warm failed={type(exc).__name__}")


if __name__ == "__main__":
    main()
