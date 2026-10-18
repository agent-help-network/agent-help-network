"""Hourly Render service-bandwidth guard; no LLM or local computer required."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx
import psycopg
from psycopg.rows import dict_row


API_BASE = "https://api.render.com/v1"
RENDER_METRIC_RETENTION = timedelta(days=7)
# Longer than this without a stored check and the retained window can no
# longer reconstruct the billing month: the computed usage is a lower bound.
COVERAGE_GAP = timedelta(days=6)
LOCK_KEY = 81260531004
# Render reports byte-based units, so folding 'Mb' (megabit) into megabytes is
# acceptable. Unknown labels have no default scale: the guard fails closed.
_UNIT_TO_GB = {
    "b": Decimal("1e-9"), "bytes": Decimal("1e-9"), "kb": Decimal("1e-6"),
    "mb": Decimal("0.001"), "gb": Decimal("1"), "tb": Decimal("1000"),
}
_MAX_AMOUNT_GB = Decimal("1e9")


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("bandwidth timestamp invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("bandwidth timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def _api_time(value: datetime) -> str:
    """Use the whole-second UTC format shown in Render's metrics examples."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_bandwidth(data: object, resource_id: str, start: datetime, end: datetime) -> list[tuple[datetime, Decimal]]:
    """Accept only byte-unit time series for the exact configured Render service."""
    if not isinstance(data, list) or not data:
        raise ValueError("bandwidth series missing")
    samples: dict[datetime, Decimal] = {}
    matching = 0
    for series in data:
        if not isinstance(series, dict):
            raise ValueError("bandwidth series invalid")
        unit = series.get("unit")
        scale = _UNIT_TO_GB.get(unit.strip().lower()) if isinstance(unit, str) else None
        if scale is None:
            # Diagnostic contains only the provider's unit label, never data points.
            raise ValueError(f"bandwidth unit invalid: {str(unit)[:24]!r}")
        labels = series.get("labels")
        if not isinstance(labels, list):
            raise ValueError("bandwidth labels invalid")
        label_map = {item.get("field"): item.get("value") for item in labels if isinstance(item, dict)}
        if label_map.get("resource", label_map.get("service")) != resource_id:
            continue
        matching += 1
        values = series.get("values")
        if not isinstance(values, list):
            raise ValueError("bandwidth values invalid")
        for point in values:
            if not isinstance(point, dict):
                raise ValueError("bandwidth point invalid")
            try:
                at = _time(point["timestamp"])
                amount = Decimal(str(point["value"])) * scale
            except (InvalidOperation, KeyError, TypeError) as exc:
                raise ValueError("bandwidth point invalid") from exc
            if not amount.is_finite() or amount < 0 or amount > _MAX_AMOUNT_GB:
                raise ValueError("bandwidth amount invalid")
            # Render's point represents the preceding hour. The sample at
            # month-start belongs to the previous billing month.
            if start < at <= end:
                if at in samples:
                    raise ValueError("duplicate bandwidth point")
                samples[at] = amount
    if matching != 1 or not samples:
        raise ValueError("one matching bandwidth series with samples required")
    return sorted(samples.items())


def _api_client(key: str) -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE,
        timeout=15,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )


def _suspend_service(key: str, resource_id: str) -> None:
    with _api_client(key) as client:
        response = client.post(f"/services/{resource_id}/suspend")
        response.raise_for_status()


def _is_service_suspended(key: str, resource_id: str) -> bool:
    """Confirm the live Render state before any suspend call."""
    with _api_client(key) as client:
        response = client.get(f"/services/{resource_id}")
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, dict) or body.get("id") != resource_id or body.get("suspended") not in {"suspended", "not_suspended"}:
        raise ValueError("Render service suspension state unavailable")
    return body["suspended"] == "suspended"


def _threshold_env(name: str, default: Decimal) -> Decimal:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        # InvalidOperation is an ArithmeticError, not a ValueError.
        raise RuntimeError(f"{name} must be a decimal number of gigabytes") from exc
    if not value.is_finite() or value <= 0:
        raise RuntimeError(f"{name} must be a finite positive number of gigabytes")
    return value


def run_guard(database_url: str, api_key: str, resource_id: str, *, now: datetime | None = None,
              warning_gb: Decimal = Decimal("10"), stop_gb: Decimal = Decimal("20"),
              client: httpx.Client | None = None) -> dict:
    if not database_url or not api_key or not resource_id.startswith("srv-"):
        raise ValueError("database URL, Render API key and web service ID are required")
    if not warning_gb.is_finite() or not stop_gb.is_finite() or warning_gb <= 0 or stop_gb <= warning_gb:
        raise ValueError("invalid bandwidth thresholds")
    now = now or datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    query_start = max(month_start, now - RENDER_METRIC_RETENTION + timedelta(hours=1))
    own_client = client is None
    client = client or _api_client(api_key)
    try:
        response = client.get("/metrics/bandwidth", params={"startTime": _api_time(query_start), "endTime": _api_time(now), "resource": resource_id})
        if response.status_code >= 400:
            # Only the short provider error body, never request headers or key.
            detail = " ".join(response.text.replace(api_key, "[redacted]").split())[:240]
            raise RuntimeError(f"Render bandwidth API HTTP {response.status_code}: {detail}")
        samples = parse_bandwidth(response.json(), resource_id, month_start, now)
    finally:
        if own_client:
            client.close()
    latest_sample = max(at for at, _ in samples)
    if latest_sample < now - timedelta(hours=4):
        raise RuntimeError("Render bandwidth metrics are stale")
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        if not conn.execute("SELECT pg_try_advisory_xact_lock(%s) AS ok", (LOCK_KEY,)).fetchone()["ok"]:
            # A concurrent run owns the state row; it will evaluate the same window.
            return {"state": "skipped", "usage_gb": None, "action": "none", "coverage_gap": False, "coverage_gap_new": False}
        previous = conn.execute("SELECT * FROM bandwidth_guard_state WHERE name='web' FOR UPDATE").fetchone()
        same_period = bool(previous and previous["period_start"] == month_start)
        for at, amount in samples:
            conn.execute(
                """INSERT INTO bandwidth_guard_samples (resource_id,sample_at,gigabytes)
                   VALUES (%s,%s,%s) ON CONFLICT (resource_id,sample_at)
                   DO UPDATE SET gigabytes=EXCLUDED.gigabytes""",
                (resource_id, at, amount),
            )
        usage = conn.execute(
            """SELECT COALESCE(sum(gigabytes),0) AS gb FROM bandwidth_guard_samples
               WHERE resource_id=%s AND sample_at > %s AND sample_at <= %s""",
            (resource_id, month_start, now),
        ).fetchone()["gb"]
        # Gap detection runs after the fetch so an overrun inside the retained
        # window is still evaluated; the flag stays set for the rest of the
        # period because the month total remains a lower bound.
        gap_since = previous["coverage_gap_since"] if same_period else None
        gap_now = now - previous["checked_at"] > COVERAGE_GAP if same_period else now - month_start > COVERAGE_GAP
        coverage_gap_new = gap_now and gap_since is None
        if coverage_gap_new:
            gap_since = now
        state = "suspended" if usage >= stop_gb else "warning" if usage >= warning_gb else "normal"
        warning_sent = previous["warning_sent_at"] if same_period else None
        suspended_at = previous["suspended_at"] if same_period else None
        action = "none"
        if state == "suspended":
            was_suspended = same_period and previous["state"] == "suspended"
            if _is_service_suspended(api_key, resource_id):
                action = "already_suspended" if was_suspended else "suspended_externally"
            else:
                _suspend_service(api_key, resource_id)
                suspended_at = now
                action = "re_suspended" if was_suspended else "suspended"
        elif state == "warning" and warning_sent is None:
            warning_sent = now
            action = "warning"
        conn.execute(
            """INSERT INTO bandwidth_guard_state
               (name,period_start,checked_at,latest_sample_at,usage_gb,state,warning_sent_at,suspended_at,
                coverage_gap_since,warning_gb,stop_gb)
               VALUES ('web',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (name) DO UPDATE SET period_start=EXCLUDED.period_start,
                 checked_at=EXCLUDED.checked_at,latest_sample_at=EXCLUDED.latest_sample_at,
                 usage_gb=EXCLUDED.usage_gb,state=EXCLUDED.state,
                 warning_sent_at=EXCLUDED.warning_sent_at,suspended_at=EXCLUDED.suspended_at,
                 coverage_gap_since=EXCLUDED.coverage_gap_since,
                 warning_gb=EXCLUDED.warning_gb,stop_gb=EXCLUDED.stop_gb""",
            (month_start, now, latest_sample, usage, state, warning_sent, suspended_at, gap_since, warning_gb, stop_gb),
        )
    return {"state": state, "usage_gb": usage, "action": action,
            "coverage_gap": gap_since is not None, "coverage_gap_new": coverage_gap_new}


def main() -> None:
    database_url = os.environ.get("AHN_DATABASE_URL", "")
    api_key = os.environ.get("RENDER_API_KEY", "")
    resource_id = os.environ.get("AHN_GUARD_WEB_SERVICE_ID", "")
    warning_gb = _threshold_env("AHN_GUARD_WARNING_GB", Decimal("10"))
    stop_gb = _threshold_env("AHN_GUARD_STOP_GB", Decimal("20"))
    from .platform_metrics import collect_render_metrics, prune_analytics
    prune_analytics(database_url)
    result = run_guard(database_url, api_key, resource_id, warning_gb=warning_gb, stop_gb=stop_gb)
    print(f"bandwidth_guard state={result['state']} usage_gb={result['usage_gb']} action={result['action']} coverage_gap={result['coverage_gap']}")
    metrics = collect_render_metrics(database_url, api_key, resource_id)
    print("render_metrics " + " ".join(f"{name}={state}" for name, state in metrics.items()))
    if result["action"] in {"warning", "suspended", "re_suspended", "suspended_externally"} or result["coverage_gap_new"]:
        # Render's existing failed-run notification sends the owner one email
        # per threshold transition and one per coverage gap. Later runs exit 0.
        raise SystemExit(2)


if __name__ == "__main__":
    main()
