"""Lookup feedback: did the next attempt work? (docs/decisions.md D58)

A blocked lookup (one that sent ``error_class``) that the service counts gets a one-time ``feedback`` token in its
answer. The caller may send it back later with an outcome code (``POST /v1/feedback`` or the MCP tool
``rate_last_lookup``). The request carries no hostname: the token itself says which lookup it answers.

Nothing is stored when a token is issued. The token is a signed, self-contained value (public hostname, the
recommendation action, the client kind, the issue time, whether the lookup was counted, and a random nonce), MACed
with a key derived from the service pepper. When it is used:

- a SHA-256 fingerprint of the token goes into ``lookup_feedback_used`` with the token's expiry time and no hostname,
  so it cannot be used twice. The row is deleted once the token has expired (at the latest at the next cleanup);
- one is added to ``lookup_feedback_hourly`` for (issue hour, hostname, action, client kind, outcome, path), kept 35
  days, when the lookup was a counted one (not the operator's network, not marked ``X-AHN-Test``) and lookup
  metrics are on.

Nothing here is read back into an answer: ``recommendation``, ``next_checks`` and every other field are unchanged.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from .lookup_signals import CLIENT_KINDS, OVERFLOW, OVERFLOW_HOST
from .models import FEEDBACK_TOKEN, FeedbackOutcome, FeedbackPath, valid_host

TOKEN_VERSION = "f1"
TOKEN_TTL_SECONDS = 3600
# Retention, enforced by platform_metrics.prune_analytics and stated in the privacy policy.
HOURLY_RETENTION_DAYS = 35
# Row bound per issue hour: a new combination past it folds into one overflow row (no hostname) for that hour.
HOUR_ROW_CAP = 200
# A token "issued" further than this in the future is refused (clock skew between instances is far smaller).
FUTURE_SKEW_SECONDS = 60

ACTIONS = ("proceed", "use_official_path", "hand_off_to_human", "wait_provider_incident", "stop_declared_block", "unknown")
OUTCOMES = tuple(item.value for item in FeedbackOutcome)
PATHS = tuple(item.value for item in FeedbackPath)


class FeedbackError(Exception):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Claims:
    host: str
    action: str
    client_kind: str
    issued_at: datetime
    counted: bool


def _key(pepper: str) -> bytes:
    # A key of its own, derived from the pepper, so a feedback MAC can never stand in for any other keyed value.
    return hmac.new(pepper.encode("utf-8"), b"ahn-lookup-feedback-v1", sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue(pepper: str, host: str, action: str, client_kind: str, counted: bool, now: datetime) -> str:
    """A one-time feedback token for one answered lookup. Stores nothing."""
    issued = int(now.timestamp())
    payload = "|".join((TOKEN_VERSION, host, action, client_kind, str(issued), "1" if counted else "0", secrets.token_hex(16)))
    raw = payload.encode("ascii")
    return _b64(raw) + "." + _b64(hmac.new(_key(pepper), raw, sha256).digest())


def feedback_field(pepper: str, host: str, action: str, client_kind: str, counted: bool, now: datetime) -> dict:
    """The answer's ``feedback`` object."""
    return {"token": issue(pepper, host, action, client_kind, counted, now), "expires_in_seconds": TOKEN_TTL_SECONDS,
            "outcomes": list(OUTCOMES), "paths": list(PATHS)}


def verify(pepper: str, token: str, now: datetime) -> Claims:
    """The claims of a genuine, unexpired token; raises FeedbackError otherwise. Does not check reuse."""
    if not isinstance(token, str) or not FEEDBACK_TOKEN.fullmatch(token):
        raise FeedbackError("invalid_feedback_token", 400)
    body, mac = token.split(".")
    try:
        raw, sent = _unb64(body), _unb64(mac)
    except (binascii.Error, ValueError):
        raise FeedbackError("invalid_feedback_token", 400) from None
    if not hmac.compare_digest(hmac.new(_key(pepper), raw, sha256).digest(), sent):
        raise FeedbackError("invalid_feedback_token", 400)
    try:
        version, host, action, client_kind, issued, counted, nonce = raw.decode("ascii").split("|")
        issued_at = datetime.fromtimestamp(int(issued), timezone.utc)
        valid_host(host)
    except (UnicodeDecodeError, ValueError, OverflowError, OSError):
        raise FeedbackError("invalid_feedback_token", 400) from None
    if (version != TOKEN_VERSION or action not in ACTIONS or client_kind not in CLIENT_KINDS or counted not in {"0", "1"}
            or len(nonce) != 32 or issued_at > now + timedelta(seconds=FUTURE_SKEW_SECONDS)):
        raise FeedbackError("invalid_feedback_token", 400)
    if now >= issued_at + timedelta(seconds=TOKEN_TTL_SECONDS):
        raise FeedbackError("feedback_token_expired", 410)
    return Claims(host, action, client_kind, issued_at, counted == "1")


def fingerprint(token: str) -> str:
    return sha256(token.encode("ascii")).hexdigest()


def _count(conn, claims: Claims, outcome: str, path: str) -> None:
    hour = claims.issued_at.replace(minute=0, second=0, microsecond=0)
    columns = "hour,site_host,action,client_kind,outcome,path"
    key = (hour, claims.host, claims.action, claims.client_kind, outcome, path)
    bump = "UPDATE lookup_feedback_hourly SET responses=responses+1 WHERE hour=%s AND site_host=%s AND action=%s AND client_kind=%s AND outcome=%s AND path=%s"
    if conn.execute(bump, key).rowcount:
        return
    if conn.execute("SELECT count(*) AS n FROM (SELECT 1 FROM lookup_feedback_hourly WHERE hour=%s LIMIT %s) s",
                    (hour, HOUR_ROW_CAP)).fetchone()["n"] >= HOUR_ROW_CAP:
        key = (hour, OVERFLOW_HOST, OVERFLOW, OVERFLOW, outcome, path)
    conn.execute(
        f"""INSERT INTO lookup_feedback_hourly ({columns},responses) VALUES (%s,%s,%s,%s,%s,%s,1)
            ON CONFLICT ({columns}) DO UPDATE SET responses=lookup_feedback_hourly.responses+1""", key)


def record(conn, pepper: str, token: str, outcome: str, path: str, now: datetime, *, count: bool) -> Claims:
    """Accept one feedback: verify, mark the token used, count it. Raises FeedbackError; one transaction."""
    if outcome not in OUTCOMES or path not in PATHS:
        raise FeedbackError("invalid_request", 400)
    claims = verify(pepper, token, now)
    with conn.transaction():
        # Expired fingerprints are useless (verify refuses their tokens anyway): drop them here as well as in the cleanup.
        conn.execute("DELETE FROM lookup_feedback_used WHERE expires_at < now()")
        made = conn.execute(
            "INSERT INTO lookup_feedback_used (token_digest,expires_at) VALUES (%s,%s) ON CONFLICT (token_digest) DO NOTHING RETURNING 1 AS made",
            (fingerprint(token), claims.issued_at + timedelta(seconds=TOKEN_TTL_SECONDS))).fetchone()
        if made is None:
            raise FeedbackError("feedback_token_used", 409)
        if count and claims.counted:
            _count(conn, claims, outcome, path)
    return claims
