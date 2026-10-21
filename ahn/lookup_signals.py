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

# HTTP statuses a client may send: the ones that mean "blocked, rate limited or the site failed".
# Anything else (404, 422, ...) is not an access problem and is refused with the allowed list.
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
    return code in HTTP_STATUS_VALUES or HTTP_STATUS_RANGE[0] <= code <= HTTP_STATUS_RANGE[1]


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
    (error_class and failure_stage keep theirs). Names are matched exactly; an unknown query name is ignored by the server.
    """
    from .models import Challenge, RetryAfter, Via
    code = int(http_status) if isinstance(http_status, str) and http_status.isascii() and http_status.isdigit() else None
    return {
        "http_status": code if code is not None and valid_http_status(code) else None,
        "challenge": challenge if challenge in Challenge._value2member_map_ else None,
        "retry_after": retry_after if retry_after in RetryAfter._value2member_map_ else None,
        "via": via if via in Via._value2member_map_ else None,
    }


def clean_address(value: str) -> str | None:
    """The compressed form of a valid IP address, else None."""
    try:
        return ipaddress.ip_address(value.strip()).compressed
    except ValueError:
        return None


def source_digest(pepper: str, day, host: str, address: str) -> str:
    """Keyed digest specific to one host and one UTC day: the same address gives a different value for another
    host or another day, so it cannot be followed across either. 64 hex characters."""
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
        day = now.date()
        digest = source_digest(pepper, day, host, signal.source_address)
        previous = conn.execute(
            "SELECT last_at,last_group,last_action FROM lookup_sources_daily WHERE day=%s AND site_host=%s AND source_digest=%s",
            (day, host, digest),
        ).fetchone()
        if previous is None:
            new_source = True
        else:
            new_source = previous["last_at"] < hour
            if group != UNSPECIFIED and previous["last_group"] != UNSPECIFIED:
                waited = (minute - previous["last_at"]).total_seconds() / 60
                if RELOOKUP_MIN_MINUTES <= waited <= RELOOKUP_MAX_MINUTES:
                    after_action = previous["last_action"]
        conn.execute(
            """INSERT INTO lookup_sources_daily (day,site_host,source_digest,first_hour,last_at,last_group,last_action)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (day,site_host,source_digest)
               DO UPDATE SET last_at=EXCLUDED.last_at, last_group=EXCLUDED.last_group, last_action=EXCLUDED.last_action""",
            (day, host, digest, hour, minute, group, action),
        )
    conn.execute(
        """INSERT INTO lookup_signals_hourly
           (hour,site_host,error_class,failure_stage,http_status_class,challenge,retry_after,via,client_kind,action,after_action,lookups,sources)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s)
           ON CONFLICT (hour,site_host,error_class,failure_stage,http_status_class,challenge,retry_after,via,client_kind,action,after_action)
           DO UPDATE SET lookups=lookup_signals_hourly.lookups+1, sources=lookup_signals_hourly.sources+EXCLUDED.sources""",
        (hour, host, error_class or UNSPECIFIED, failure_stage or UNSPECIFIED, http_class, signal.challenge or UNSPECIFIED,
         signal.retry_after or UNSPECIFIED, signal.via or UNSPECIFIED, signal.client_kind, action, after_action, 1 if new_source else 0),
    )
    return after_action
