"""Owner-only list of hosts that agents looked up recently. Read-only; no new data is stored."""

from __future__ import annotations

from .db import connect
from .settings import Settings

OWNER_HOSTS_DEFAULT_DAYS = 8
OWNER_HOSTS_MIN_DAYS = 1
OWNER_HOSTS_MAX_DAYS = 30
OWNER_HOSTS_MAX_ROWS = 5000


def parse_days(raw: str | None) -> int | None:
    """Default when absent; None when it is not a plain integer from 1 to 30."""
    if raw is None:
        return OWNER_HOSTS_DEFAULT_DAYS
    if not (raw.isascii() and raw.isdigit()):
        return None
    days = int(raw)
    return days if OWNER_HOSTS_MIN_DAYS <= days <= OWNER_HOSTS_MAX_DAYS else None


def recent_hosts(settings: Settings, days: int, limit: int = OWNER_HOSTS_MAX_ROWS) -> list[str]:
    """Hostnames from the existing lookup register, newest first, at most `limit`."""
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT site_host FROM queried_hosts WHERE last_seen_at >= now() - make_interval(days => %s) "
            "ORDER BY last_seen_at DESC, site_host LIMIT %s",
            (days, limit),
        ).fetchall()
    return [row["site_host"] for row in rows]
