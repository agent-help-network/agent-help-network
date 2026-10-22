"""Adoption measurement: which kinds of client got an answer, and what the answer was (docs/decisions.md D49).

One row per UTC day and combination of fixed-list values in ``lookup_outcome_daily`` (migration 028): the client
category (plugin, ahn-client, MCP host, script, browser, unknown, crawler, web), the ahn-client adapter label, the MCP
host family, the recommendation action and the state of the host's declared-policy layer. No hostname, address or
digest is stored, so a row says nothing about who asked or about which site.

Counted: every answered lookup that is not from the operator's network and not marked as a test (the caller drops
those before this module is reached, see ``main.lookup_signal``), minus lookups about a test host. Crawlers and the
human search page are stored under their own categories and left out of M1.

Nothing here is read back into an answer: ``recommendation``, ``next_checks`` and every other response field are the
same with or without this record. The metric names (M1, M2, M3) are those of the internal adoption plan.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone

from . import traffic
from .lookup_signals import EXAMPLE_HOSTS, source_digest

LOG = logging.getLogger(__name__)

# Retention, enforced by platform_metrics.prune_analytics and stated in the privacy policy.
RETENTION_DAYS = 90

ACTIONS = ("proceed", "use_official_path", "hand_off_to_human", "wait_provider_incident", "stop_declared_block", "unknown")
OTHER = "other"
PROFILE_STATES = ("ready", "stale", "pending", "none")
# Categories that are stored but are not adoption: M1 leaves them out.
EXCLUDED = (traffic.CATEGORY_CRAWLER, traffic.CATEGORY_WEB)
# The categories whose lookups need no human step once installed (plan section 4-3).
AUTOMATIC = ("plugin", "ahn_client", "mcp_host")

OWN_DOMAIN = "agenthelpnetwork.com"


def is_test_host(host: str) -> bool:
    """Hosts a lookup about is never counted: documentation examples (example.*), our own domain, reserved test names."""
    host = (host or "").lower().rstrip(".")
    labels = host.split(".")
    return (host in EXAMPLE_HOSTS or "example" in labels or host == OWN_DOMAIN or host.endswith("." + OWN_DOMAIN)
            or labels[-1] in {"test", "invalid", "localhost"})


def profile_state(access_profile: dict | None) -> str:
    """ready, stale, pending or none, from the access_profile layer of the answer being sent."""
    if not access_profile:
        return "none"
    freshness = access_profile.get("freshness")
    return freshness if freshness in {"stale", "pending"} else "ready"


def action_of(result: dict) -> str:
    action = (result.get("recommendation") or {}).get("action")
    return action if action in ACTIONS else OTHER


def _source_exists(conn, pepper: str, day: date, host: str, address: str) -> bool:
    return conn.execute("SELECT 1 AS found FROM lookup_sources_daily WHERE day=%s AND site_host=%s AND source_digest=%s",
                        (day, host, source_digest(pepper, day, host, address))).fetchone() is not None


def prepare(conn, pepper: str, host: str, signal, result: dict, now: datetime, *, count_source: bool) -> tuple | None:
    """The row to count for one answered lookup, or None when it is not counted (a test host).

    This only reads (no lock): ``count_source`` says that lookup_signals will keep a source digest for this lookup, so a
    lookup whose digest is not there yet is the first of its source for that host and day. Run it before
    lookup_signals.record, inside the caller's savepoint. The write itself is ``write``, after the lookup's commit.
    """
    if is_test_host(host):
        return None
    day = now.date()  # `now` is UTC
    new_source = bool(count_source and signal.source_address and not _source_exists(conn, pepper, day, host, signal.source_address))
    return (day, signal.category or "unknown", signal.adapter or traffic.ADAPTER_NONE, signal.mcp_host or traffic.MCP_HOST_NONE,
            action_of(result), profile_state(result.get("access_profile")), 1 if new_source else 0)


_dropped = 0
_dropped_lock = threading.Lock()


def dropped_since_start() -> int:
    """Lookups whose count could not be written since this process started (lock timeout, database error)."""
    return _dropped


def _count_drop() -> None:
    global _dropped
    with _dropped_lock:
        _dropped += 1


def write(conn, row: tuple | None, *, lock_timeout_ms: int = 200) -> bool:
    """Add one prepared row to the daily counts in a savepoint on the caller's connection; never raises.

    The statements are exactly: set the lock timeout, one upsert, reset the timeout. The caller commits right after, so
    the hot daily row is locked for that one statement only. A failure (lock timeout, database error) is rolled back to
    the savepoint, logged and counted in ``dropped_since_start``; only failures are counted, not successful writes.
    """
    if row is None:
        return False
    try:
        with conn.transaction():
            conn.execute(f"SET LOCAL lock_timeout = '{int(lock_timeout_ms)}ms'")
            conn.execute(
                """INSERT INTO lookup_outcome_daily (day,client_category,adapter,mcp_host,action,profile_state,lookups,sources)
                   VALUES (%s,%s,%s,%s,%s,%s,1,%s)
                   ON CONFLICT (day,client_category,adapter,mcp_host,action,profile_state)
                   DO UPDATE SET lookups=lookup_outcome_daily.lookups+1, sources=lookup_outcome_daily.sources+EXCLUDED.sources""",
                row)
            conn.execute("SET LOCAL lock_timeout = DEFAULT")
        return True
    except Exception:
        _count_drop()
        LOG.warning("lookup_adoption_unavailable")
        return False


def parse_days(raw: str | None) -> int | None:
    """Default (7) when absent; None when it is not a plain integer from 1 to RETENTION_DAYS (90)."""
    if raw is None:
        return 7
    if not (raw.isascii() and raw.isdigit()):
        return None
    days = int(raw)
    return days if 1 <= days <= RETENTION_DAYS else None


def _share(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def summary(conn, days: int = 7, today: date | None = None) -> dict:
    """The owner's adoption numbers for the last ``days`` UTC days including today (default 7, at most RETENTION_DAYS).

    m1 is every counted lookup outside EXCLUDED; m1_by_category always lists the six categories. m2 is the sum of the
    daily source counts (an upper bound, see the migration). m3 is the shape of the answers: how many went out with the
    declared-policy layer still pending, and how many carried the action "unknown" once that layer was there.
    """
    days = max(1, min(int(days), RETENTION_DAYS))
    today = today or datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    rows = conn.execute(
        """SELECT day,client_category,adapter,mcp_host,action,profile_state,lookups,sources
           FROM lookup_outcome_daily WHERE day >= %s AND day <= %s""", (start, today)).fetchall()
    counted = [r for r in rows if r["client_category"] not in EXCLUDED]
    by_category = {name: 0 for name in traffic.CLIENT_CATEGORIES}
    by_adapter = {name: 0 for name in (*traffic.ADAPTERS, traffic.ADAPTER_OTHER)}
    by_mcp_host = {name: 0 for name in traffic.MCP_HOSTS}
    by_action = {name: 0 for name in (*ACTIONS, OTHER)}
    by_state = {name: 0 for name in PROFILE_STATES}
    daily = {(start + timedelta(days=i)).isoformat(): {"lookups": 0, "sources": 0} for i in range(days)}
    for r in counted:
        n = int(r["lookups"])
        by_category[r["client_category"]] = by_category.get(r["client_category"], 0) + n
        if r["client_category"] == "ahn_client" and r["adapter"] != traffic.ADAPTER_NONE:
            by_adapter[r["adapter"]] = by_adapter.get(r["adapter"], 0) + n
        if r["client_category"] == "mcp_host":
            by_mcp_host[r["mcp_host"]] = by_mcp_host.get(r["mcp_host"], 0) + n
        by_action[r["action"]] = by_action.get(r["action"], 0) + n
        by_state[r["profile_state"]] = by_state.get(r["profile_state"], 0) + n
        slot = daily[r["day"].isoformat()]
        slot["lookups"] += n
        slot["sources"] += int(r["sources"])
    m1 = sum(by_category.values())
    answered = m1 - by_state["pending"]
    unknown_answered = sum(int(r["lookups"]) for r in counted if r["action"] == "unknown" and r["profile_state"] != "pending")
    automatic = sum(by_category[name] for name in AUTOMATIC)
    return {
        "days": days, "dropped_since_start": dropped_since_start(), "from": start.isoformat(), "to": today.isoformat(),
        "m1": m1, "m1_by_category": by_category, "m1_by_adapter": by_adapter, "m1_by_mcp_host": by_mcp_host,
        "automatic_share": _share(automatic, m1),
        "m1_by_day": [{"day": day, **values} for day, values in daily.items()],
        "m2_sources_upper_bound": sum(v["sources"] for v in daily.values()),
        "m3": {"by_action": by_action, "by_profile_state": by_state, "pending_share": _share(by_state["pending"], m1),
               "unknown_share_excluding_pending": _share(unknown_answered, answered)},
        "left_out": {name: sum(int(r["lookups"]) for r in rows if r["client_category"] == name) for name in EXCLUDED},
        "definition": ("answered lookups (REST check and MCP check_incident) not from the operator's network, not marked as a test, "
                       "not about a test host; crawlers and the human search page are listed under left_out; sources are summed "
                       "per host and day, so m2 is an upper bound"),
    }
