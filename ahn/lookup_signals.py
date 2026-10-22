"""Lookup signals: optional codes on a lookup, counted per public host and hour (docs/decisions.md D47).

A lookup already says which public host a failed step was about. It may also carry a few more fixed-list
codes the client already knows (the HTTP status the site returned, whether a bot-challenge header was
present, a Retry-After range, plain fetch or browser). They are counted per host and hour in
``lookup_signals_hourly``. To tell separate clients apart without keeping an address, a keyed digest that
is specific to the host and the UTC day goes into ``lookup_sources_daily`` and is deleted after two days.

Shadow mode: nothing here is read back into an answer. ``recommendation``, ``next_checks`` and every other
response field are the same with or without these codes. Reports (``observations``) are never touched.
"""

from __future__ import annotations

import hmac
import ipaddress
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

# Retention, enforced by platform_metrics.prune_analytics and stated in the privacy policy.
HOURLY_RETENTION_DAYS = 35
SOURCES_RETENTION_DAYS = 2

# Row bounds. Each lookup creates at most one new combination row, so the tables are bounded by these caps, not by
# what a client sends: per host and hour at most HOST_HOUR_ROW_CAP combination rows (more fold into one overflow row of
# that host), per hour at most HOUR_ROW_CAP rows in all (more fold into one overflow row for every host), and per UTC
# day at most SOURCES_DAY_ROW_CAP source digests (more lookups are still counted, just without a source).
HOST_HOUR_ROW_CAP = 32
HOUR_ROW_CAP = 500
SOURCES_DAY_ROW_CAP = 20000
OVERFLOW = "overflow"
OVERFLOW_HOST = "(overflow)"

# The last-lookup time of a source is kept to the minute (the 10-60 minute re-lookup window needs it) for at most this
# many minutes and is then cut to the hour by the cleanup job, so the minute never outlives the window it serves.
MINUTE_PRECISION_MINUTES = 61

# HTTP statuses a client may send: the ones that mean "blocked, rate limited or the site failed".
# Anything else (404, 422, ...) is not an access problem and is ignored (counted as not sent).
HTTP_STATUS_VALUES = (401, 402, 403, 407, 408, 429, 451)
HTTP_STATUS_RANGE = (500, 599)
UNSPECIFIED = "unspecified"
NO_ACTION = "none"

# Who sent the lookup, from the User-Agent and the MCP hop mark (ahn/traffic.py), or the human search page.
CLIENT_KINDS = ("plugin", "ahn_client", "mcp", "web", "rest_other")
_CLIENT_KIND_OF_TYPE = {"plugin_hint": "plugin", "ahn_client_hint": "ahn_client", "mcp_hint": "mcp"}

# A source that was given a recommendation and asks about the same host again, blocked again, this many
# minutes later (inclusive on both ends) counts as a re-lookup after that recommendation.
RELOOKUP_MIN_MINUTES = 10
RELOOKUP_MAX_MINUTES = 60

# Hosts the documentation uses as examples: never counted.
EXAMPLE_HOSTS = frozenset({"example.com", "example.net", "example.org"})

# Name of the header that marks a test lookup, and of the one the MCP adapter uses to pass the caller's address
# on its loopback hop (honoured only on an authenticated hop, see ahn/main.py).
TEST_HEADER = "x-ahn-test"
ORIGIN_HEADER = "x-ahn-origin-ip"


def valid_http_status(code: int) -> bool:
    return not isinstance(code, bool) and (code in HTTP_STATUS_VALUES or HTTP_STATUS_RANGE[0] <= code <= HTTP_STATUS_RANGE[1])


def status_class(code: int | None) -> str:
    """Exact 5xx values are folded into one class to keep the number of combinations small."""
    if code is None:
        return UNSPECIFIED
    return "5xx" if code >= HTTP_STATUS_RANGE[0] else str(code)


def client_kind_of(client_type: str) -> str:
    """Map ahn.traffic.client_type() onto the signal's client kinds."""
    return _CLIENT_KIND_OF_TYPE.get(client_type, "rest_other")


def signal_group(error_class: str | None, http_class: str, challenge: str | None) -> str:
    """Coarse family of a blocked lookup: outage, block, rate or login; unspecified when nothing says.

    A status wins, then a challenge header, then the error class. Used to tell a blocked lookup from a
    plain one (a re-lookup counts only when both lookups were blocked ones).
    """
    if http_class == "429":
        return "rate"
    if http_class in {"401", "407"}:
        return "login"
    if http_class in {"403", "451", "402"}:
        return "block"
    if http_class in {"5xx", "408"}:
        return "outage"
    if challenge == "header":
        return "block"
    return {"login_auth": "login", "human_verification": "block", "site_access": "block",
            "service_error": "outage"}.get(error_class or "", UNSPECIFIED)


def lenient_codes(http_status, challenge, retry_after, via) -> dict:
    """Signal codes as sent, with any value outside its fixed list dropped (treated as not sent).

    A typo in an optional signal must never cost the caller its answer, so these four are not validated with a 400
    (error_class and failure_stage keep theirs). Accepts whatever type arrived (text from the query string, anything
    from an MCP client) and never converts a long digit string to a number. ``via=unknown`` is stored as not sent:
    it says no more than leaving the code out. Names are matched exactly; an unknown query name is ignored by the server.
    """
    from .models import Challenge, RetryAfter, Via
    code = None
    if isinstance(http_status, bool):
        pass
    elif isinstance(http_status, int):
        code = http_status
    elif isinstance(http_status, float):
        code = int(http_status) if http_status.is_integer() and abs(http_status) < 10_000 else None
    elif isinstance(http_status, str) and len(http_status) <= 3 and http_status.isascii() and http_status.isdigit():
        code = int(http_status)

    def listed(value, enum):
        return value if isinstance(value, str) and value in enum._value2member_map_ else None
    via = listed(via, Via)
    return {
        "http_status": code if code is not None and valid_http_status(code) else None,
        "challenge": listed(challenge, Challenge),
        "retry_after": listed(retry_after, RetryAfter),
        "via": None if via == Via.unknown.value else via,
    }


def clean_address(value: str) -> str | None:
    """The compressed form of a valid IP address, else None."""
    try:
        return ipaddress.ip_address(value.strip()).compressed
    except ValueError:
        return None


def source_digest(pepper: str, day, host: str, address: str) -> str:
    """Keyed digest specific to one host and one UTC day: the same address gives a different value for another
    host or another day, so it cannot be followed across either. An IPv6 address is reduced to its /64 first. 64 hex characters."""
    ip = ipaddress.ip_address(address)
    if ip.version == 6:  # one subscriber usually holds a whole /64: count it as one source
        address = ipaddress.ip_network((ip, 64), strict=False).network_address.compressed
    message = f"src-v1|{day.isoformat()}|{host}|{address}".encode("utf-8")
    return hmac.new(pepper.encode("utf-8"), message, sha256).hexdigest()


@dataclass(frozen=True)
class LookupSignal:
    """What a counted lookup brings besides host, error class and failure stage. Every code is optional."""
    client_kind: str
    source_address: str | None = None   # already validated by clean_address; never stored
    http_status: int | None = None
    challenge: str | None = None
    retry_after: str | None = None
    via: str | None = None


def _new_source(conn, day, host, digest, minute, group, action) -> bool:
    """Insert the source's first row of the day; True when this call created it (so concurrent first lookups count once)."""
    if conn.execute("SELECT count(*) AS n FROM (SELECT 1 FROM lookup_sources_daily WHERE day=%s LIMIT %s) s",
                    (day, SOURCES_DAY_ROW_CAP)).fetchone()["n"] >= SOURCES_DAY_ROW_CAP:
        return False
    return conn.execute(
        """INSERT INTO lookup_sources_daily (day,site_host,source_digest,last_at,last_group,last_action)
           VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (day,site_host,source_digest) DO NOTHING RETURNING 1 AS made""",
        (day, host, digest, minute, group, action)).fetchone() is not None


def _count_lookup(conn, hour, host, key: tuple, sources: int) -> None:
    """Add one lookup to its combination row; a new combination is subject to the row caps (overflow rows past them)."""
    columns = "hour,site_host,error_class,failure_stage,http_status_class,challenge,retry_after,via,client_kind,action,after_action"
    where = columns.replace(",", "=%s AND ") + "=%s"
    bump = f"UPDATE lookup_signals_hourly SET lookups=lookups+1, sources=sources+%s WHERE {where}"
    if conn.execute(bump, (sources, hour, host, *key)).rowcount:
        return
    use_host, use_key = host, key
    over = (OVERFLOW,) * len(key)
    if conn.execute("SELECT count(*) AS n FROM (SELECT 1 FROM lookup_signals_hourly WHERE hour=%s LIMIT %s) s",
                    (hour, HOUR_ROW_CAP)).fetchone()["n"] >= HOUR_ROW_CAP:
        use_host, use_key = OVERFLOW_HOST, over
    elif conn.execute("SELECT count(*) AS n FROM (SELECT 1 FROM lookup_signals_hourly WHERE hour=%s AND site_host=%s LIMIT %s) s",
                      (hour, host, HOST_HOUR_ROW_CAP)).fetchone()["n"] >= HOST_HOUR_ROW_CAP:
        use_key = over
    marks = ",".join(["%s"] * (len(use_key) + 2))
    conn.execute(
        f"""INSERT INTO lookup_signals_hourly ({columns},lookups,sources) VALUES ({marks},1,%s)
            ON CONFLICT ({columns}) DO UPDATE SET lookups=lookup_signals_hourly.lookups+1, sources=lookup_signals_hourly.sources+EXCLUDED.sources""",
        (hour, use_host, *use_key, sources))


def record(conn, pepper: str, host: str, error_class: str | None, failure_stage: str | None,
           signal: LookupSignal, action: str, now: datetime) -> str:
    """Write one counted lookup. Returns the action it was a re-lookup after, or NO_ACTION.

    ``action`` is the recommendation the lookup was answered with. Runs inside the caller's savepoint and
    raises on a database error; the caller decides what a failure means (the lookup must never fail).
    """
    hour = now.replace(minute=0, second=0, microsecond=0)
    minute = now.replace(second=0, microsecond=0)
    http_class = status_class(signal.http_status)
    group = signal_group(error_class, http_class, signal.challenge)
    after_action = NO_ACTION
    new_source = False
    if signal.source_address:
        day = now.date()  # `now` is UTC
        digest = source_digest(pepper, day, host, signal.source_address)
        if _new_source(conn, day, host, digest, minute, group, action):
            new_source = True
        else:
            previous = conn.execute(
                "SELECT last_at,last_group,last_action FROM lookup_sources_daily WHERE day=%s AND site_host=%s AND source_digest=%s FOR UPDATE",
                (day, host, digest)).fetchone()
            if previous is not None:
                new_source = previous["last_at"] < hour
                if group != UNSPECIFIED and previous["last_group"] != UNSPECIFIED:
                    waited = (minute - previous["last_at"]).total_seconds() / 60
                    if RELOOKUP_MIN_MINUTES <= waited <= RELOOKUP_MAX_MINUTES:
                        after_action = previous["last_action"]
                conn.execute("UPDATE lookup_sources_daily SET last_at=%s,last_group=%s,last_action=%s WHERE day=%s AND site_host=%s AND source_digest=%s",
                             (minute, group, action, day, host, digest))
    _count_lookup(conn, hour, host, (error_class or UNSPECIFIED, failure_stage or UNSPECIFIED, http_class, signal.challenge or UNSPECIFIED,
                                     signal.retry_after or UNSPECIFIED, signal.via or UNSPECIFIED, signal.client_kind, action, after_action),
                  1 if new_source else 0)
    return after_action
