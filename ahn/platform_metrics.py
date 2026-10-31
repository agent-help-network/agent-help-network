"""Hourly, bounded snapshots from Render's metrics API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx
import psycopg

from .db import connect_url


API_BASE = "https://api.render.com/v1"
METRICS = {
    "cpu": "/metrics/cpu",
    "memory": "/metrics/memory",
    "http_requests": "/metrics/http-requests",
}


def latest_sample(data: object, resource_id: str) -> tuple[datetime, Decimal, str]:
    if not isinstance(data, list):
        raise ValueError("Render metric response is not a list")
    points: dict[datetime, tuple[Decimal, str]] = {}
    for series in data:
        if not isinstance(series, dict) or not isinstance(series.get("labels"), list):
            raise ValueError("Render metric series is invalid")
        labels = {item.get("field"): item.get("value") for item in series["labels"] if isinstance(item, dict)}
        if labels.get("resource", labels.get("service")) != resource_id:
            continue
        unit = series.get("unit")
        if not isinstance(unit, str) or not 1 <= len(unit) <= 20 or not unit.isascii():
            raise ValueError("Render metric unit is invalid")
        values = series.get("values")
        if not isinstance(values, list):
            raise ValueError("Render metric values are invalid")
        for point in values:
            if not isinstance(point, dict):
                raise ValueError("Render metric point is invalid")
            stamp = point.get("timestamp")
            if not isinstance(stamp, str):
                raise ValueError("Render metric timestamp is invalid")
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if when.tzinfo is None:
                raise ValueError("Render metric timestamp lacks timezone")
            when = when.astimezone(timezone.utc)
            try:
                value = Decimal(str(point["value"]))
            except (InvalidOperation, KeyError) as exc:
                raise ValueError("Render metric value is invalid") from exc
            if not value.is_finite() or value < 0:
                raise ValueError("Render metric value is invalid")
            previous, previous_unit = points.get(when, (Decimal(0), unit))
            if previous_unit != unit:
                raise ValueError("Render metric units differ")
            points[when] = (previous + value, unit)
    if not points:
        raise ValueError("Render metric has no matching points")
    latest = max(points)
    value, unit = points[latest]
    return latest, value, unit


def collect_render_metrics(database_url: str, api_key: str, resource_id: str,
                           *, now: datetime | None = None, client: httpx.Client | None = None) -> dict[str, str]:
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(hours=4)
    own_client = client is None
    client = client or httpx.Client(base_url=API_BASE, timeout=15,
                                    headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    results: dict[str, str] = {}
    try:
        for metric, endpoint in METRICS.items():
            try:
                response = client.get(endpoint, params={
                    "startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "endTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "resource": resource_id,
                    "resolutionSeconds": 3600,
                })
                response.raise_for_status()
                when, value, unit = latest_sample(response.json(), resource_id)
                if when < start:
                    raise ValueError("Render metric sample is stale")
                with connect_url(database_url) as conn:
                    conn.execute(
                        """INSERT INTO render_metric_samples (metric,sample_at,value,unit)
                           VALUES (%s,%s,%s,%s) ON CONFLICT (metric,sample_at)
                           DO UPDATE SET value=EXCLUDED.value,unit=EXCLUDED.unit,collected_at=now()""",
                        (metric, when, value, unit),
                    )
                results[metric] = "ok"
            except (httpx.HTTPError, ValueError, KeyError, TypeError, psycopg.Error):
                # A Pro-only or temporarily unavailable metric must not interrupt
                # the bandwidth guard. The dashboard labels old samples as stale.
                results[metric] = "unavailable"
    finally:
        if own_client:
            client.close()
    return results


# A withdrawn report is already excluded from every read; the row itself goes after this grace.
WITHDRAWN_REPORT_GRACE_DAYS = 30


def prune_reports(conn: psycopg.Connection, retention_days: int) -> int:
    """Delete reports past the retention period and withdrawn reports past the grace.

    Does nothing while retention_days is 0, which the policy page states as
    "no automatic deletion date". Returns the number of deleted observations.
    """
    if retention_days <= 0:
        return 0
    expired = ("SELECT id FROM observations WHERE received_at < now() - make_interval(days => %s)"
               " OR withdrawn_at < now() - make_interval(days => %s)")
    params = (retention_days, WITHDRAWN_REPORT_GRACE_DAYS)
    # Later outcomes may point at an expired observation; keep them, drop the link.
    conn.execute(f"UPDATE observations SET supersedes_id=NULL WHERE supersedes_id IN ({expired})", params)
    conn.execute(f"DELETE FROM report_submissions WHERE observation_id IN ({expired})", params)
    return conn.execute(f"DELETE FROM observations WHERE id IN ({expired})", params).rowcount


def cut_source_minutes(database_url: str) -> None:
    """Cut the minute of a source's last lookup to the hour once it is older than 61 minutes (D47).

    The minute is only needed for the 10-60 minute re-lookup window (lookup_signals.MINUTE_PRECISION_MINUTES). It runs
    first and on its own connection, so a failure in any other cleanup step cannot leave minutes in place; the cron
    that calls it runs every 15 minutes, so a minute lives about an hour, at most about 76 minutes. 'UTC' makes the
    cut independent of the database session's time zone.
    """
    try:
        with connect_url(database_url) as conn:
            conn.execute("UPDATE lookup_sources_daily SET last_at = date_trunc('hour', last_at, 'UTC')"
                         " WHERE last_at < now() - interval '61 minutes' AND last_at <> date_trunc('hour', last_at, 'UTC')")
    except psycopg.Error as exc:
        print(f"lookup_sources_cut failed={type(exc).__name__}")


def prune_analytics(database_url: str, report_retention_days: int = 0) -> None:
    """Keep daily pseudonyms short-lived even if Render's metrics API is down."""
    cut_source_minutes(database_url)
    with connect_url(database_url) as conn:
        prune_reports(conn, report_retention_days)
        conn.execute("DELETE FROM traffic_client_daily WHERE day < CURRENT_DATE - 13")
        conn.execute("DELETE FROM traffic_attribution_daily WHERE day < CURRENT_DATE - 89")
        conn.execute("DELETE FROM mcp_check_metrics WHERE day < CURRENT_DATE - 89")
        conn.execute("DELETE FROM render_metric_samples WHERE sample_at < now() - interval '90 days'")
        conn.execute("DELETE FROM public_intake_host_daily WHERE day < CURRENT_DATE - 7")
        # queried_hosts has a hard 10,000-row cap and no other eviction. Hosts
        # backing an enabled status source must survive: refresh_batch joins on them.
        conn.execute(
            """DELETE FROM queried_hosts WHERE last_seen_at < now() - interval '30 days'
               AND NOT EXISTS (SELECT 1 FROM status_sources s
                               WHERE s.site_host = queried_hosts.site_host AND s.enabled = true)"""
        )
    # Signal history (migration 024, ahn/signal_history.py): raw read events 14 days, periods, provider-status
    # changes and daily totals 90 days. Its own transaction: if this cron runs before the web service has applied
    # the migration, the missing tables must neither undo the deletions above nor stop the bandwidth guard.
    # A provider period that is still open (the provider still reports the problem) is kept until it ends.
    try:
        with connect_url(database_url) as conn:
            conn.execute("DELETE FROM robots_read_events WHERE read_at < now() - interval '14 days'")
            conn.execute("DELETE FROM provider_status_events WHERE at < now() - interval '90 days'")
            conn.execute("DELETE FROM outage_episodes WHERE last_event_at < now() - interval '90 days'"
                         " AND (signal = 'A' OR ended_at IS NOT NULL)")
            conn.execute("DELETE FROM robots_read_daily WHERE day < CURRENT_DATE - 89")
    except psycopg.Error as exc:
        print(f"signal_history_prune failed={type(exc).__name__}")
    # Lookup signals (migration 027, ahn/lookup_signals.py, D47): hourly counts per host 35 days, the host- and
    # day-specific source digests 2 days (today and yesterday). The literals match lookup_signals.HOURLY_RETENTION_DAYS
    # and SOURCES_RETENTION_DAYS; tests/test_lookup_signals.py and the policy-coverage test check that.
    # Own transaction for the same reason as above.
    try:
        with connect_url(database_url) as conn:
            conn.execute("DELETE FROM lookup_signals_hourly WHERE hour < now() - interval '35 days'")
            conn.execute("DELETE FROM lookup_sources_daily WHERE day < (now() AT TIME ZONE 'UTC')::date - 1")
    except psycopg.Error as exc:
        print(f"lookup_signals_prune failed={type(exc).__name__}")
    # Daily adoption counts (migration 028, ahn/adoption.py, D49): no host, address or digest, kept 90 days (today and
    # the 89 days before; the literal matches adoption.RETENTION_DAYS, checked by the policy-coverage test).
    # Own transaction for the same reason as above.
    try:
        with connect_url(database_url) as conn:
            conn.execute("DELETE FROM lookup_outcome_daily WHERE day < CURRENT_DATE - 89")
    except psycopg.Error as exc:
        print(f"lookup_outcome_prune failed={type(exc).__name__}")
