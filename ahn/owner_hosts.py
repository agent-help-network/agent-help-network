"""Owner-only list of hosts that agents looked up recently. Read-only; no new data is stored.

Two orders: the lookup register, newest first (default), or the hourly lookup-code counts, hosts with the most
blocked lookups first. Either list can leave out hosts that already have a status mapping, so curation can start
where agents were actually blocked and the status board has nothing to show.
"""

from __future__ import annotations

from .db import connect
from .settings import Settings

OWNER_HOSTS_DEFAULT_DAYS = 8
OWNER_HOSTS_MIN_DAYS = 1
OWNER_HOSTS_MAX_DAYS = 30
OWNER_HOSTS_MAX_ROWS = 5000
OWNER_HOSTS_ORDERS = ("recent", "blocked")


def parse_days(raw: str | None) -> int | None:
    """Default when absent; None when it is not a plain integer from 1 to 30."""
    if raw is None:
        return OWNER_HOSTS_DEFAULT_DAYS
    if not (raw.isascii() and raw.isdigit()):
        return None
    days = int(raw)
    return days if OWNER_HOSTS_MIN_DAYS <= days <= OWNER_HOSTS_MAX_DAYS else None


def parse_order(raw: str | None) -> str | None:
    """'recent' when absent; None when it is not one of OWNER_HOSTS_ORDERS."""
    if raw is None:
        return OWNER_HOSTS_ORDERS[0]
    return raw if raw in OWNER_HOSTS_ORDERS else None


def parse_unmapped(raw: str | None) -> bool | None:
    """False when absent, True for '1'; None for anything else."""
    if raw is None:
        return False
    return True if raw == "1" else None


def mapped_hosts() -> list[str]:
    """Hosts that already have a status feed or an official status-page link."""
    from .status_refresh import OFFICIAL_STATUS_PAGES, SOURCES

    return sorted(set(SOURCES) | set(OFFICIAL_STATUS_PAGES))


def recent_hosts(settings: Settings, days: int, limit: int = OWNER_HOSTS_MAX_ROWS, *, unmapped: bool = False) -> list[str]:
    """Hostnames from the existing lookup register, newest first, at most `limit`."""
    skip = mapped_hosts() if unmapped else []
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT site_host FROM queried_hosts WHERE last_seen_at >= now() - make_interval(days => %s) "
            "AND NOT (site_host = ANY(%s::text[])) ORDER BY last_seen_at DESC, site_host LIMIT %s",
            (days, skip, limit),
        ).fetchall()
    return [row["site_host"] for row in rows]


def blocked_hosts(settings: Settings, days: int, limit: int = OWNER_HOSTS_MAX_ROWS, *,
                  unmapped: bool = False) -> list[tuple[str, int, int]]:
    """(host, sources, lookups) for hosts with blocked lookups in the window, most first, at most `limit`.

    A blocked lookup is one that sent an error class, as for the daily check list (ahn/site_probe.py). `sources`
    counts a source once per host and hour, so one client repeating a lookup weighs less than several clients;
    it sorts first, then lookups, then the name. Overflow rows are left out: their codes are folded and do not
    say whether the lookups were blocked ones.
    """
    from .lookup_signals import EXAMPLE_HOSTS, OVERFLOW, OVERFLOW_HOST, UNSPECIFIED

    skip = sorted(set(mapped_hosts() if unmapped else ()) | EXAMPLE_HOSTS | {OVERFLOW_HOST})
    with connect(settings) as conn:
        rows = conn.execute(
            """SELECT site_host, sum(sources)::bigint AS sources, sum(lookups)::bigint AS lookups
               FROM lookup_signals_hourly
               WHERE hour >= date_trunc('hour', now()) - make_interval(days => %s)
                 AND error_class <> ALL(%s::text[]) AND NOT (site_host = ANY(%s::text[]))
               GROUP BY site_host ORDER BY sources DESC, lookups DESC, site_host LIMIT %s""",
            (days, [UNSPECIFIED, OVERFLOW], skip, limit),
        ).fetchall()
    return [(row["site_host"], row["sources"], row["lookups"]) for row in rows]
