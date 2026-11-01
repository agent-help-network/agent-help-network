"""The AHN server check of a host's home page (D67).

Our server requests a public host's home page (``GET /``) unless the host's own robots.txt disallows "/" for us (an
absent file or a 404 does not disallow; a refused or unreadable file does), and keeps codes only: the HTTP status, whether a known bot-protection provider issued a challenge,
whether the page leads to a sign-in, and the response time. Never the page, a header value, a cookie value or a
redirect path.

Manners (docs/decisions.md D67; the privacy policy repeats the numbers and tests/test_policy_coverage.py checks them):

* the request carries our User-Agent, a Host header and an Accept header and nothing else: no cookies, no Referer, no
  scripts; one request per check, except that a redirect from "/" to the host's own www name is followed once (two
  requests at most, see below); every other redirect is recorded and not followed;
* the robots.txt verdict for "/" was decided in memory when the profile was read (access_profile.home_gate) and is
  used only while that read is under 24 hours old; a refused, unreadable or disallowing robots.txt means no request;
* one check per host per 6 hours at most, a refusal or challenge is not asked again for 72 hours (doubling to 7 days),
  a failing host is checked again after 6 to 24 hours; 60 checks an hour and 800 a day in all, 300 a day and 40 an
  hour started by lookups; all checks stop while the bandwidth guard is at its warning level or has no recent record;
* the one redirect followed: the first answer is a redirect whose Location host is exactly ``www.<host>`` and the www
  name's own robots.txt verdict for "/" (its home_gate, read the same way, under 24 hours old) allows the home page for
  us. With no fresh verdict the www profile read is queued and this check keeps the redirect as its result; with a
  disallowing verdict, or too little time left in a lookup's 3 seconds, the second request is not made; a paced check
  (queue, cron) waits the same pause before it as before any other request. A host whose last check followed the
  redirect is checked only while the www name's verdict is fresh (otherwise its profile read is queued and the check
  waits, like an apex with a stale verdict); a check that could not follow for want of a verdict is due again after
  MIN_GAP_HOURS, not a day later. The hourly and daily limits count checks, and a check is up to two requests, so the
  number of requests is at most twice those limits.
  What is kept is the second answer's codes, and the redirect record says ``followed_www: true``. A second redirect is
  not followed;
* a check never solves, answers or repeats a human-verification step, never opens a sign-in page and never submits a form.

Where it runs: the background queue (kind ``site_probe``, drained by the web worker and the 5-minute cron), the cron's
own pass over today's list of hosts (``run_cron``), and, for a lookup, a short synchronous attempt (``for_lookup``)
that waits at most SYNC_WAIT_SECONDS and otherwise leaves the check in the queue with no delay.

Everything is off unless ``AHN_SITE_PROBE=1``. With it off nothing here makes a request, writes a row or adds a field
to an answer.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import logging
import os
import re
import socket
import threading
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import urllib3
from psycopg.types.json import Jsonb

from . import access_profile as ap
from .probe_fingerprints import BLOCK_STATUSES, TRACKED_HEADERS, VENDORS, VERSION as FINGERPRINT_VERSION
from .provenance import operator_id, vantage

LOG = logging.getLogger(__name__)

HOSTS_PATH = Path(__file__).resolve().parent / "probe_hosts.txt"
SEED_PATH = Path(__file__).resolve().parent / "seed_hosts.txt"
TRANCO_MARKER = "# Hosts chosen from the Tranco top 1,000"

# Numbers stated in the privacy policy (tests/test_policy_coverage.py compares them).
PROBE_SET_MAX = 500
SET_REFUSED_MAX = 300        # hosts looked up after a blocked step in the last 14 days
SET_CURATED_MAX = 150        # the public list ahn/probe_hosts.txt
SET_WINDOW_DAYS = 14
MIN_GAP_HOURS = 6
FRESH_HOURS = 24
STALE_DAYS = 8
BODY_CAP = 16_384
BODY_CAP_CHALLENGE = 32_768
HOURLY_CAP = 60
DAILY_CAP = 800
LOOKUP_DAILY_CAP = 300
LOOKUP_HOURLY_CAP = 40
BACKOFF_REFUSED_HOURS = 72
BACKOFF_REFUSED_MAX_DAYS = 7
BACKOFF_FAILED_HOURS = 6
BACKOFF_FAILED_MAX_HOURS = 24
SUCCESS_INTERVAL_HOURS = 24
LOGIN_BACKOFF_HOURS = 72
ROW_RETENTION_DAYS = 30
DAILY_RETENTION_DAYS = 35
RESPONSE_MAX_BYTES = BODY_CAP_CHALLENGE

# Time limits of one request: each step has its own limit, and the whole request ends after WALL_SECONDS of wall-clock
# time whatever the server does (access_profile.WallClock closes its sockets), so a trickling server cannot hold a thread.
DNS_TIMEOUT_SECONDS = 2.0
CONNECT_TIMEOUT_SECONDS = 2.0
READ_TIMEOUT_SECONDS = 3.0
BODY_BUDGET_SECONDS = 5.0
WALL_SECONDS = ap.WALL_SECONDS
REQUEST_WORST_SECONDS = WALL_SECONDS
PAUSE_SECONDS = 2.0
PROBE_WORST_SECONDS = 2 * REQUEST_WORST_SECONDS + PAUSE_SECONDS  # two requests at most per check (the www redirect)
SECOND_REQUEST_MIN_SECONDS = 1.5   # a lookup's second request (the www redirect) starts only with at least this much of its wait left
PENDING_RETRY_SECONDS = 60   # shown on a pending view: ask again after this long, once
PROBE_JOB_SECONDS = 3        # the queue's wait estimate for one check
CRON_SAFE_SECONDS = 280.0
CRON_TIME_BUDGET_SECONDS = 40.0
CRON_PROBES_PER_RUN = 2
CRON_CANDIDATES = 60

# A lookup waits at most this long for its own host's check, DNS and request included; the web process runs at most
# SYNC_CONCURRENCY such checks at a time and one per host however many lookups arrive together.
SYNC_WAIT_SECONDS = 3.0
SYNC_CONCURRENCY = 4

QUEUE_GIVE_UP = timedelta(hours=1)   # a queued check whose host profile never shows up is dropped after this long
DEFER_SECONDS = 30
LOCK_DEFER_SECONDS = 10

SHOW_VIEW_NOTE = ("Measured from AHN's server (a data-center address, declared bot user agent, no cookies or scripts); "
                  "your agent's network and browser may get a different answer. Information only: it does not change "
                  "recommendation.")

OUTCOMES = ("reachable", "redirected_same_host", "bot_challenge", "refused", "rate_limited", "login_wall",
            "redirected_offsite", "server_error", "unreachable", "other_status")
NOT_MEASURED_REASONS = ("robots_disallowed", "robots_refused", "robots_unreadable", "robots_stale", "not_eligible",
                        "budget", "backoff", "disabled")
# Reasons that leave a row behind (the host's own answer, not our limits).
RECORDED_REASONS = frozenset({"robots_disallowed", "robots_refused", "robots_unreadable", "not_eligible"})

_clock = time.monotonic  # patched in tests
_sleep = time.sleep


def enabled() -> bool:
    """AHN_SITE_PROBE=1 turns the check on, including the synchronous lookup attempt and the ahn_check field.
    Off (the default): no request, no row, no field."""
    return os.environ.get("AHN_SITE_PROBE", "").strip() == "1"


def daily_cap() -> int:
    """AHN_PROBE_DAILY_CAP can only lower the daily limit."""
    try:
        value = int(os.environ.get("AHN_PROBE_DAILY_CAP", ""))
    except ValueError:
        return DAILY_CAP
    return max(0, min(DAILY_CAP, value))


# --------------------------------------------------------------------------------------------------------------
# The request


def _failed(label: str) -> dict:
    return {"label": label, "status": None, "headers": {}, "cookies": [], "body": b"", "complete": False, "ms": None,
            "bytes_read": 0}


def _read_prefix(response, cap: int, budget: float) -> tuple[bytes, bool]:
    """At most ``cap`` bytes of the body within ``budget`` seconds; (bytes, complete). A slow or long body is cut,
    not an error: what was read is looked at, and the page counts as incomplete."""
    read1 = getattr(response, "read1", None)
    if read1 is None:
        data = response.read(cap + 1)
        return bytes(data[:cap]), len(data) <= cap
    deadline = _clock() + budget
    data = bytearray()
    while True:
        if _clock() > deadline:
            return bytes(data), False
        chunk = read1(min(65_536, cap + 1 - len(data)))
        if not chunk:
            return bytes(data), True
        data.extend(chunk)
        if len(data) > cap:
            return bytes(data[:cap]), False


def _cookie_names(response) -> list[str]:
    """Names of the cookies the response sets, lowercase, never a value."""
    getlist = getattr(response.headers, "getlist", None)
    raw = getlist("set-cookie") if getlist else ([response.headers.get("set-cookie")] if response.headers.get("set-cookie") else [])
    names = []
    for item in raw[:40]:
        name = str(item).split("=", 1)[0].strip().lower()[:64]
        if name and name not in names:
            names.append(name)
    return names


def fetch_home(site_host: str) -> dict:
    """The request below, ended after WALL_SECONDS of wall-clock time at the latest (a cut-off one counts as read_timeout)."""
    with ap.WallClock(WALL_SECONDS) as wall:
        result = _fetch_home(site_host, wall)
    if wall.fired and result["status"] is None:
        return _failed("read_timeout")
    return result


def _fetch_home(site_host: str, wall: "ap.WallClock") -> dict:
    """One ``GET /`` of a public host from a pinned public IP; never raises. Returns a dict: ``label`` (None, or why no
    answer came: dns_failed, dns_timeout, not_public, connect_error, connect_timeout, tls_error, read_timeout,
    read_error, invalid_input), ``status``, ``headers`` (tracked names only, values cut to 200 characters), ``cookies``
    (names), ``body`` (at most 16 KB, 32 KB for a non-2xx or challenge-header response), ``complete`` (the whole body
    fitted), ``ms`` (until the response headers arrived) and ``bytes_read``. The request carries Host, User-Agent and
    Accept and nothing else; this function follows no redirect (measure() makes the one www request itself)."""
    site_host = site_host.strip().rstrip(".").lower()
    if not ap._HOST_RE.match(site_host):
        return _failed("invalid_input")
    try:
        try:
            addresses = ap._resolve(site_host, DNS_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            return _failed("dns_timeout")
        except (OSError, socket.gaierror):
            return _failed("dns_failed")
        if not addresses or len(addresses) > 16:
            return _failed("dns_failed")
        if any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            return _failed("not_public")
        last_label = "connect_error"
        connect_budget = CONNECT_TIMEOUT_SECONDS * ap.CONNECT_BUDGET_FACTOR
        for address in ap._address_order(addresses):
            if connect_budget <= 0:
                break
            started = _clock()
            pool = ap.tracked_pool(urllib3.HTTPSConnectionPool)(
                address, port=443, server_hostname=site_host, assert_hostname=site_host,
                timeout=urllib3.Timeout(connect=min(CONNECT_TIMEOUT_SECONDS, connect_budget), read=READ_TIMEOUT_SECONDS),
                retries=False)
            wall.watch(pool)
            try:
                try:
                    response = pool.request(
                        "GET", "/", headers={"Host": site_host, "User-Agent": ap.USER_AGENT, "Accept": "text/html,*/*;q=0.5"},
                        redirect=False, preload_content=False)
                except urllib3.exceptions.ConnectTimeoutError as exc:
                    last_label = ap._transport_label(exc)
                    pool.close()
                    connect_budget -= _clock() - started
                    continue
            except Exception:
                pool.close()
                raise
            try:
                ms = int((_clock() - started) * 1000)
                status = response.status
                headers = {name: str(response.headers.get(name))[:200] for name in TRACKED_HEADERS
                           if response.headers.get(name) is not None}
                cookies = _cookie_names(response)
                body, complete = b"", True
                if not 300 <= status < 400:
                    challenge_header = any(h == "cf-mitigated" or h == "x-amzn-waf-action" for h in headers)
                    cap = BODY_CAP if 200 <= status < 300 and not challenge_header else BODY_CAP_CHALLENGE
                    body, complete = _read_prefix(response, cap, BODY_BUDGET_SECONDS)
                return {"label": None, "status": status, "headers": headers, "cookies": cookies, "body": body,
                        "complete": complete, "ms": ms, "bytes_read": len(body)}
            finally:
                response.release_conn()
                pool.close()
        return _failed(last_label)
    except Exception as exc:  # a check must never fail because a third-party host misbehaves
        return _failed(ap._transport_label(exc))


# --------------------------------------------------------------------------------------------------------------
# Classification (pure)

_LOGIN_SEGMENT = re.compile(r"^(login|log-in|signin|sign-in|sign_in|logon|auth|sso|authenticate)(\.[a-z0-9]{1,5})?$")
_LOGIN_LABELS = frozenset({"login", "accounts", "account", "auth", "sso", "signin", "id"})
_LOCALE = re.compile(r"^/[a-z]{2}([-_][a-z]{2,4})?/?$")
_PASSWORD_FIELD = re.compile(rb"""type\s*=\s*["']?password""")


def _registrable(host: str) -> str:
    from .service import PUBLIC_SUFFIXES_2
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in PUBLIC_SUFFIXES_2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def classify_redirect(site_host: str, location: str) -> dict:
    """Where a redirect points, as codes only: ``to_relation`` (same_host, www_alias, same_site, other_site) and
    ``path_class`` (login, locale, other). The path itself is never kept."""
    try:
        parts = urlsplit((location or "").strip())
        host = (parts.hostname or site_host).lower()
        path = (parts.path or "/").lower()
    except ValueError:
        return {"to_relation": "other_site", "path_class": "other"}
    if host == site_host:
        relation = "same_host"
    elif host == f"www.{site_host}" or site_host == f"www.{host}":
        relation = "www_alias"
    elif _registrable(host) == _registrable(site_host):
        relation = "same_site"
    else:
        relation = "other_site"
    segments = [part for part in path.split("/") if part]
    if any(_LOGIN_SEGMENT.fullmatch(part) for part in segments) or (relation != "same_host" and host.split(".")[0] in _LOGIN_LABELS):
        path_class = "login"
    elif _LOCALE.fullmatch(path):
        path_class = "locale"
    else:
        path_class = "other"
    return {"to_relation": relation, "path_class": path_class}


def fingerprint(status: int, headers: dict, cookies: list[str], body: bytes, complete: bool) -> tuple[list[str], tuple | None]:
    """(edge vendors seen, the strongest challenge marker or None).

    Presence markers fill the first list and never make a challenge. A challenge marker is a header the provider sends only
    when it issued one, or a body marker in a response that was blocked (BLOCK_STATUSES) or is a complete 200 page.
    The challenge is (vendor, signal, confidence); a 200 page is never above medium."""
    lowered = body.lower().decode("latin-1")
    seen: list[str] = []
    challenge = None
    for vendor in VENDORS:
        for marker in vendor.markers:
            hit = False
            if marker.kind == "header":
                value = headers.get(marker.name)
                hit = value is not None and (marker.value is None or marker.value in value.lower())
            elif marker.kind == "cookie":
                hit = any(name.startswith(marker.name) for name in cookies)
            elif marker.kind == "body":
                hit = marker.role == "challenge" and marker.name in lowered
            if not hit:
                continue
            if marker.role == "presence":
                if vendor.code not in seen:
                    seen.append(vendor.code)
                continue
            if marker.statuses is not None and status not in marker.statuses:
                continue
            if vendor.code == "generic_captcha" and status not in BLOCK_STATUSES:
                continue  # a widget script on a page that was served (a login or contact form) is not a challenge
            if marker.kind == "body" and not (status in BLOCK_STATUSES or (status == 200 and complete)):
                continue
            confidence = marker.confidence if not (marker.kind == "body" and status == 200) else "medium"
            if vendor.code not in seen and vendor.code != "generic_captcha":
                seen.append(vendor.code)
            candidate = (vendor.code, marker.kind, confidence)
            if challenge is None or (challenge[2] == "medium" and confidence == "high"):
                challenge = candidate
    return seen[:5], challenge


def _retry_after_seconds(value: str | None, now: datetime) -> int | None:
    if not value:
        return None
    value = value.strip()
    try:
        seconds = int(value)
    except ValueError:
        try:
            seconds = int((parsedate_to_datetime(value) - now).total_seconds())
        except (TypeError, ValueError):
            return None
    return max(0, min(seconds, BACKOFF_REFUSED_MAX_DAYS * 86400))


def classify(site_host: str, fetched: dict, now: datetime) -> dict:
    """Pure. The stored codes for one fetch_home result: outcome, http_status, bot_vendor/bot_signal/bot_confidence,
    edge_vendors, redirect, login_signal, retry_after (seconds, or None)."""
    status = fetched["status"]
    if status is not None and not 100 <= status <= 599:
        # Not an HTTP status the answer schema can carry: counted as an answer, no code kept (a host may send anything).
        fetched = {**fetched, "status": 599}
        out = classify(site_host, fetched, now)
        return {**out, "outcome": "other_status", "http_status": None, "bot_vendor": None, "bot_signal": None,
                "bot_confidence": None, "redirect": None, "login_signal": None}
    result = {"outcome": "unreachable", "http_status": status, "bot_vendor": None, "bot_signal": None, "bot_confidence": None,
              "edge_vendors": [], "redirect": None, "login_signal": None, "retry_after": None}
    if status is None:
        return result
    headers = fetched["headers"]
    seen, challenge = fingerprint(status, headers, fetched["cookies"], fetched["body"], fetched["complete"])
    result["edge_vendors"] = seen
    result["retry_after"] = _retry_after_seconds(headers.get("retry-after"), now)
    if challenge is not None:
        result.update(outcome="bot_challenge", bot_vendor=challenge[0], bot_signal=challenge[1], bot_confidence=challenge[2])
        return result
    if 200 <= status < 300:
        if fetched["complete"] and _PASSWORD_FIELD.search(fetched["body"].lower()):
            result.update(outcome="login_wall", login_signal="form")
        else:
            result["outcome"] = "reachable"
    elif 300 <= status < 400 and headers.get("location"):
        target = classify_redirect(site_host, headers["location"])
        result["redirect"] = {"followed_www": False, **target}
        if target["path_class"] == "login":
            result.update(outcome="login_wall", login_signal="redirect")
        elif target["to_relation"] == "other_site":
            result["outcome"] = "redirected_offsite"
        else:
            result["outcome"] = "redirected_same_host"
    elif status == 401 and "www-authenticate" in headers:
        result.update(outcome="login_wall", login_signal="http_auth")
    elif status in (401, 403):
        result["outcome"] = "refused"
    elif status == 429:
        result["outcome"] = "rate_limited"
    elif 500 <= status < 600:
        result["outcome"] = "server_error"
    else:
        result["outcome"] = "other_status"
    return result


def schedule(outcome: str, previous: dict | None, retry_after: int | None) -> tuple[timedelta, int, int]:
    """(wait until the next check, consecutive_refusals, consecutive_failures) after a check with this outcome."""
    refusals = previous["consecutive_refusals"] if previous else 0
    failures = previous["consecutive_failures"] if previous else 0
    if outcome in ("bot_challenge", "refused"):
        refusals += 1
        wait = min(timedelta(hours=BACKOFF_REFUSED_HOURS) * 2 ** (refusals - 1), timedelta(days=BACKOFF_REFUSED_MAX_DAYS))
        wait = max(wait, timedelta(seconds=retry_after or 0))
        return min(wait, timedelta(days=BACKOFF_REFUSED_MAX_DAYS)), refusals, 0
    if outcome == "rate_limited":
        wait = max(timedelta(hours=BACKOFF_REFUSED_HOURS), timedelta(seconds=retry_after or 0))
        return min(wait, timedelta(days=BACKOFF_REFUSED_MAX_DAYS)), 0, 0
    if outcome == "login_wall":
        return timedelta(hours=LOGIN_BACKOFF_HOURS), 0, 0
    if outcome in ("server_error", "unreachable"):
        failures += 1
        wait = min(timedelta(hours=BACKOFF_FAILED_HOURS) * 2 ** (failures - 1), timedelta(hours=BACKOFF_FAILED_MAX_HOURS))
        return wait, 0, failures
    return timedelta(hours=SUCCESS_INTERVAL_HOURS), 0, 0


# --------------------------------------------------------------------------------------------------------------
# Deciding whether to check (database reads only)


def _utc_day(now: datetime) -> date:
    return now.astimezone(timezone.utc).date()


def _parse_time(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _guard_ok(conn) -> bool:
    """The bandwidth guard has a recent record and is not at its warning level. Unknown means stop."""
    try:
        with conn.transaction():
            row = conn.execute("SELECT state,checked_at FROM bandwidth_guard_state WHERE name='web'").fetchone()
    except Exception:
        return False
    if not row or row["state"] != "normal":
        return False
    from .bandwidth_guard import COVERAGE_GAP
    return row["checked_at"] >= datetime.now(timezone.utc) - COVERAGE_GAP


def _breaker_stopped(conn) -> bool:
    """The automatic stop is on. If its state cannot be read it counts as on: unknown means stop."""
    try:
        with conn.transaction():
            row = conn.execute("SELECT value FROM probe_state WHERE name='breaker'").fetchone()
    except Exception:
        return True
    return bool(row and row["value"].get("stopped"))


def caps_ok(conn, trigger: str, now: datetime) -> str | None:
    """None when the limits allow another check, else 'budget'."""
    day = _utc_day(now)
    counts = conn.execute(
        """SELECT COALESCE(sum(probes),0) AS total,
                  COALESCE(sum(probes) FILTER (WHERE trigger_kind='lookup'),0) AS lookup
           FROM site_probe_daily WHERE day=%s AND outcome<>'not_measured'""", (day,)).fetchone()
    if counts["total"] >= daily_cap() or (trigger == "lookup" and counts["lookup"] >= LOOKUP_DAILY_CAP):
        return "budget"
    hourly = conn.execute(
        """SELECT count(*) AS total, count(*) FILTER (WHERE trigger_kind='lookup') AS lookup
           FROM site_probe WHERE probed_at > %s AND outcome<>'not_measured'""", (now - timedelta(hours=1),)).fetchone()
    if hourly["total"] >= HOURLY_CAP or (trigger == "lookup" and hourly["lookup"] >= LOOKUP_HOURLY_CAP):
        return "budget"
    return None


def _fresh_gate(profile: dict | None, now: datetime) -> dict | None:
    """The profile row's home_gate when it was decided within FRESH_HOURS (and not in the future), else None."""
    gate = profile["home_gate"] if profile else None
    gated_at = _parse_time(gate["fetched_at"]) if isinstance(gate, dict) and "fetched_at" in gate else None
    if gated_at is None or gated_at < now - timedelta(hours=FRESH_HOURS) or gated_at > now + timedelta(minutes=5):
        return None
    return gate


def decide(conn, site_host: str, trigger: str, now: datetime) -> tuple[str, str | None, dict | None]:
    """(verdict, reason, context) for one host. verdict: 'go' (context has read_host and previous), 'record' (leave a
    not_measured row with this reason), 'defer' (no usable robots.txt verdict yet: the profile must be read first) or 'skip'."""
    if not enabled():
        return "skip", "disabled", None
    profile = conn.execute(
        "SELECT fetched_at,read_host,home_gate FROM host_access_profile WHERE site_host=%s", (site_host,)).fetchone()
    gate = _fresh_gate(profile, now)
    if gate is None:
        return "defer", "robots_stale", None
    previous = conn.execute("SELECT * FROM site_probe WHERE site_host=%s", (site_host,)).fetchone()
    if not gate.get("allowed"):
        reason = gate.get("reason")
        reason = reason if reason in RECORDED_REASONS else "robots_unreadable"
        if previous and previous["outcome"] == "not_measured" and previous["not_measured_reason"] == reason \
                and previous["next_due_at"] > now:
            return "skip", "backoff", None
        return "record", reason, {"previous": previous, "read_host": gate.get("read_host") or profile["read_host"] or site_host}
    if previous and previous["outcome"] != "not_measured":
        if previous["next_due_at"] > now or previous["probed_at"] > now - timedelta(hours=MIN_GAP_HOURS):
            return "skip", "backoff", None
    if caps_ok(conn, trigger, now):
        return "skip", "budget", None
    if not _guard_ok(conn):
        return "skip", "budget", None
    if _breaker_stopped(conn):  # lookup-started checks stop with the list-driven ones
        return "skip", "budget", None
    return "go", None, {"previous": previous, "read_host": gate.get("read_host") or profile["read_host"] or site_host}


# --------------------------------------------------------------------------------------------------------------
# Storing


def _store(conn, site_host: str, row: dict, now: datetime) -> None:
    conn.execute(
        """INSERT INTO site_probe (site_host,read_host,probed_at,outcome,http_status,bot_vendor,bot_signal,bot_confidence,
                                   edge_vendors,redirect,login_signal,response_ms,bytes_read,not_measured_reason,
                                   fingerprint_version,next_due_at,consecutive_refusals,consecutive_failures,trigger_kind,
                                   origin_operator,vantage,user_agent)
           VALUES (%(site_host)s,%(read_host)s,%(probed_at)s,%(outcome)s,%(http_status)s,%(bot_vendor)s,%(bot_signal)s,
                   %(bot_confidence)s,%(edge_vendors)s,%(redirect)s,%(login_signal)s,%(response_ms)s,%(bytes_read)s,
                   %(not_measured_reason)s,%(fingerprint_version)s,%(next_due_at)s,%(consecutive_refusals)s,
                   %(consecutive_failures)s,%(trigger_kind)s,%(origin_operator)s,%(vantage)s,%(user_agent)s)
           ON CONFLICT (site_host) DO UPDATE SET read_host=EXCLUDED.read_host,probed_at=EXCLUDED.probed_at,
               outcome=EXCLUDED.outcome,http_status=EXCLUDED.http_status,bot_vendor=EXCLUDED.bot_vendor,
               bot_signal=EXCLUDED.bot_signal,bot_confidence=EXCLUDED.bot_confidence,edge_vendors=EXCLUDED.edge_vendors,
               redirect=EXCLUDED.redirect,login_signal=EXCLUDED.login_signal,response_ms=EXCLUDED.response_ms,
               bytes_read=EXCLUDED.bytes_read,not_measured_reason=EXCLUDED.not_measured_reason,
               fingerprint_version=EXCLUDED.fingerprint_version,next_due_at=EXCLUDED.next_due_at,
               consecutive_refusals=EXCLUDED.consecutive_refusals,consecutive_failures=EXCLUDED.consecutive_failures,
               trigger_kind=EXCLUDED.trigger_kind,origin_operator=EXCLUDED.origin_operator,vantage=EXCLUDED.vantage,
               user_agent=EXCLUDED.user_agent""",
        {**row, "site_host": site_host, "redirect": Jsonb(row["redirect"]) if row["redirect"] is not None else None,
         "vantage": Jsonb(row["vantage"])})
    conn.execute(
        """INSERT INTO site_probe_daily (day,site_host,outcome,bot_vendor,trigger_kind,probes)
           VALUES (%s,%s,%s,%s,%s,1) ON CONFLICT (day,site_host,outcome,bot_vendor,trigger_kind)
           DO UPDATE SET probes=site_probe_daily.probes+1""",
        (_utc_day(now), site_host, row["outcome"], row["bot_vendor"] or "", row["trigger_kind"]))


def _vantage_codes() -> dict:
    declared = vantage()
    return {"kind": declared["kind"], "region": declared["region"]}


def _row(read_host: str, now: datetime, trigger: str, *, outcome: str, next_due: datetime, previous: dict | None,
         refusals: int = 0, failures: int = 0, reason: str | None = None, fetched: dict | None = None,
         classified: dict | None = None) -> dict:
    classified = classified or {}
    fetched = fetched or {}
    return {
        "read_host": read_host, "probed_at": now, "outcome": outcome, "http_status": classified.get("http_status"),
        "bot_vendor": classified.get("bot_vendor"), "bot_signal": classified.get("bot_signal"),
        "bot_confidence": classified.get("bot_confidence"), "edge_vendors": classified.get("edge_vendors") or [],
        "redirect": classified.get("redirect"), "login_signal": classified.get("login_signal"),
        "response_ms": fetched.get("ms"), "bytes_read": fetched.get("bytes_read"), "not_measured_reason": reason,
        "fingerprint_version": FINGERPRINT_VERSION, "next_due_at": next_due, "consecutive_refusals": refusals,
        "consecutive_failures": failures, "trigger_kind": trigger, "origin_operator": operator_id(),
        "vantage": _vantage_codes(), "user_agent": ap.USER_AGENT,
    }


# --------------------------------------------------------------------------------------------------------------
# One check


_pace_lock = threading.Lock()
_last_request_at = 0.0
_INFLIGHT: dict[str, concurrent.futures.Future] = {}
_INFLIGHT_LOCK = threading.Lock()
_SLOTS = threading.BoundedSemaphore(SYNC_CONCURRENCY)


def _pace() -> None:
    """Wait until PAUSE_SECONDS have passed since the last check this process started."""
    global _last_request_at
    with _pace_lock:
        wait = _last_request_at + PAUSE_SECONDS - _clock()
        if wait > 0:
            _sleep(wait)
        _last_request_at = _clock()


def _lock_key(site_host: str) -> str:
    return f"ahn.site_probe:{site_host}"


def _defer(job: dict | None, seconds: float):
    """In a queued job, put it back (the attempt is not counted); otherwise the check simply did not run."""
    if job is None:
        return None
    from .background_reads import Deferred
    raise Deferred(seconds)


def _www_target(read_host: str, fetched: dict, classified: dict) -> str | None:
    """``www.<read_host>`` when the first answer is a redirect whose Location host is exactly that name (https or a
    scheme-relative Location, no other port, no credentials); otherwise None. The path of the Location is not used."""
    redirect = classified["redirect"]
    if classified["outcome"] != "redirected_same_host" or not redirect or redirect["to_relation"] != "www_alias":
        return None
    if read_host.startswith("www."):
        return None
    try:
        parts = urlsplit((fetched["headers"]["location"] or "").strip())
        host, port = (parts.hostname or "").lower(), parts.port
    except (KeyError, ValueError):
        return None
    if host != f"www.{read_host}" or parts.scheme not in ("", "https") or port not in (None, 443) or parts.username or parts.password:
        return None
    return host


def _www_gate(conn, www_host: str, now: datetime) -> dict | None:
    """The www name's fresh home_gate, or None; when there is none its profile read is queued (nothing else refreshes it)."""
    profile = conn.execute("SELECT fetched_at,read_host,home_gate FROM host_access_profile WHERE site_host=%s", (www_host,)).fetchone()
    conn.commit()
    gate = _fresh_gate(profile, now)
    if gate is None:
        from . import background_reads
        try:
            queued = background_reads.enqueue(conn, [("access_profile", www_host)])
            conn.commit()
            if queued:
                background_reads.wake()
        except Exception:
            conn.rollback()
            LOG.warning("site_probe_www_enqueue_unavailable")
    return gate


def _follow_www(conn, www_host: str, now: datetime, reader, paced: bool, deadline: float | None):
    """(result, no_verdict): result is (fetched, classified) of the one request to the www name, or None when it is not
    made; no_verdict is True when that is for want of a fresh home_gate (its profile read is queued)."""
    gate = _www_gate(conn, www_host, now)
    if gate is None:
        return None, True
    if not ("allowed" in gate and gate["allowed"]):
        return None, False
    if deadline is not None and deadline - _clock() < SECOND_REQUEST_MIN_SECONDS:
        return None, False
    if paced:
        _pace()
    fetched = reader(www_host)
    if "label" in fetched and fetched["label"] == "not_public":
        return None, False
    return (fetched, classify(www_host, fetched, now)), False


def measure(settings, site_host: str, trigger: str, *, now: datetime | None = None, reader=None, job: dict | None = None,
            paced: bool = True, deadline: float | None = None) -> dict | None:
    """Decide, request and store one check of ``site_host``; returns the stored row, or None when nothing was checked
    (the reason is not stored: a limit or a backoff is not a fact about the host). A queued ``job`` is deferred
    (background_reads.Deferred) while the host's profile read is still to come or another process is checking the host.
    ``deadline`` (a _clock() time) is set for a lookup's synchronous attempt: the second request (the www redirect) is not
    made when less than SECOND_REQUEST_MIN_SECONDS of it is left."""
    from .db import connect
    now = now or datetime.now(timezone.utc)
    reader = reader or fetch_home
    if job is not None and site_host in _INFLIGHT:
        return _defer(job, LOCK_DEFER_SECONDS)
    with connect(settings) as conn:
        locked = conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS ok", (_lock_key(site_host),)).fetchone()["ok"]
        conn.commit()
        if not locked:
            return _defer(job, LOCK_DEFER_SECONDS)
        try:
            verdict, reason, context = decide(conn, site_host, trigger, now)
            conn.commit()
            if verdict == "defer":
                return _wait_for_profile(conn, site_host, job, trigger)
            if verdict == "skip":
                return None
            previous = context["previous"]
            if verdict == "record":
                row = _row(context["read_host"], now, trigger, outcome="not_measured", reason=reason, previous=previous,
                           next_due=now + timedelta(hours=MIN_GAP_HOURS))
                _store(conn, site_host, row, now)
                conn.commit()
                return row
            if paced:
                _pace()
            read_host = context["read_host"]
            if previous and previous["read_host"] == f"www.{read_host}" and _www_gate(conn, previous["read_host"], now) is None:
                # The last check followed the redirect to www: wait for the www verdict (queued) instead of storing a redirect.
                if job is not None and "requested_at" in job and job["requested_at"] is not None \
                        and job["requested_at"] < datetime.now(timezone.utc) - QUEUE_GIVE_UP:
                    return None
                return _defer(job, DEFER_SECONDS)
            fetched = reader(read_host)
            if fetched.get("label") == "not_public":
                row = _row(read_host, now, trigger, outcome="not_measured", reason="not_eligible", previous=previous,
                           next_due=now + timedelta(hours=MIN_GAP_HOURS))
            else:
                classified = classify(read_host, fetched, now)
                www = _www_target(read_host, fetched, classified)
                followed, no_verdict = _follow_www(conn, www, now, reader, paced, deadline) if www else (None, False)
                if followed is not None:
                    first = classified["redirect"]
                    fetched, classified = followed
                    # followed_www says the answer is the www name's; the target kept is the last redirect seen (codes only)
                    classified["redirect"] = {**(classified["redirect"] or first), "followed_www": True}
                    read_host = www
                wait, refusals, failures = schedule(classified["outcome"], previous, classified["retry_after"])
                if no_verdict:
                    wait = min(wait, timedelta(hours=MIN_GAP_HOURS))  # the www verdict is on its way: look again soon
                row = _row(read_host, now, trigger, outcome=classified["outcome"], next_due=now + wait,
                           previous=previous, refusals=refusals, failures=failures, fetched=fetched, classified=classified)
            _store(conn, site_host, row, now)
            conn.commit()
            return row
        finally:
            try:
                conn.rollback()
                conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (_lock_key(site_host),))
                conn.commit()
            except Exception:
                pass  # closing the connection releases the lock


def _wait_for_profile(conn, site_host: str, job: dict | None, trigger: str):
    """No robots.txt verdict for "/" yet (or it is over 24 hours old). The host's profile read is queued if nothing else
    did; a queued check waits for it (and is dropped after QUEUE_GIVE_UP), a list-driven one simply tries again on a later pass."""
    if job is None and trigger == "lookup":
        return None  # the lookup path queues the check itself
    if job is not None and job.get("requested_at") is not None and job["requested_at"] < datetime.now(timezone.utc) - QUEUE_GIVE_UP:
        return None
    from . import background_reads
    background_reads.enqueue(conn, [("access_profile", site_host)])
    conn.commit()
    return _defer(job, DEFER_SECONDS)


def run_queued(settings, site_host: str, job: dict) -> None:
    """The background worker's entry (kind ``site_probe``): a check started by a lookup, with no delay."""
    measure(settings, site_host, "lookup", job=job)


# --------------------------------------------------------------------------------------------------------------
# The lookup path


def start_sync(settings, site_host: str, *, now: datetime | None = None, reader=None):
    """The in-flight check of ``site_host`` (a Future of the stored row or None), started on a thread if there is none;
    None when this process already runs SYNC_CONCURRENCY checks. Lookups that arrive while a check runs share it."""
    return _start(settings, site_host, now=now, reader=reader)[0]


def _start(settings, site_host: str, *, now: datetime | None = None, reader=None):
    """(future or None, started): ``started`` is False when the future was already running for another lookup."""
    with _INFLIGHT_LOCK:
        existing = _INFLIGHT.get(site_host)
        if existing is not None:
            return existing, False
        if not _SLOTS.acquire(blocking=False):
            return None, False
        future: concurrent.futures.Future = concurrent.futures.Future()
        _INFLIGHT[site_host] = future

    deadline = _clock() + SYNC_WAIT_SECONDS

    def work() -> None:
        try:
            future.set_result(measure(settings, site_host, "lookup", now=now, reader=reader, paced=False, deadline=deadline))
        except BaseException as exc:  # the lookup that started it has moved on; nothing may escape this thread
            LOG.warning("site_probe_sync_failed error=%s", type(exc).__name__)
            future.set_result(None)
        finally:
            with _INFLIGHT_LOCK:
                _INFLIGHT.pop(site_host, None)
            _SLOTS.release()

    try:
        threading.Thread(target=work, name="ahn-site-probe", daemon=True).start()
    except BaseException:
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(site_host, None)
        _SLOTS.release()
        raise
    return future, True


def sync_measure(settings, site_host: str, *, wait: float | None = None, now: datetime | None = None, reader=None):
    """(row or None, why): waits at most ``wait`` seconds (SYNC_WAIT_SECONDS) for the check, only the lookup that started
    it; a lookup that finds the host's check already running does not wait. why: 'done', 'busy' (no free slot), 'joined'
    (another lookup's check is running) or 'timeout' (the check goes on in the background and stores its own result)."""
    wait = SYNC_WAIT_SECONDS if wait is None else wait
    future, started = _start(settings, site_host, now=now, reader=reader)
    if future is None:
        return None, "busy"
    if not started:
        return (future.result(timeout=0), "done") if future.done() else (None, "joined")
    try:
        return future.result(timeout=wait), "done"
    except concurrent.futures.TimeoutError:
        return None, "timeout"


def _lookup_wanted(conn, site_host: str, now: datetime) -> bool:
    """Would a lookup of this host start a check now? The database part of the gate (the profile's robots.txt verdict is
    checked again where the check runs)."""
    row = conn.execute("SELECT outcome,next_due_at,probed_at FROM site_probe WHERE site_host=%s", (site_host,)).fetchone()
    if row and (row["next_due_at"] > now or (row["outcome"] != "not_measured" and row["probed_at"] > now - timedelta(hours=MIN_GAP_HOURS))):
        return False
    return caps_ok(conn, "lookup", now) is None and _guard_ok(conn) and not _breaker_stopped(conn)


def for_lookup(settings, conn, site_host: str, now: datetime, *, access_profile: dict | None) -> dict | None:
    """The ``ahn_check`` view for a lookup of an eligible host, or None. Never raises. May start a check and wait for it
    up to SYNC_WAIT_SECONDS; if it is not done by then (or cannot start) the check is queued with no delay and the view
    says ``pending``. The host's recommendation and next steps are decided before this and do not depend on it."""
    if not enabled():
        return None
    try:
        row = conn.execute("SELECT * FROM site_probe WHERE site_host=%s", (site_host,)).fetchone()
        if row is not None and _fresh_or_waiting(row, now):
            conn.commit()
            return _valid(view(row, now))
        wanted = _lookup_wanted(conn, site_host, now)
        conn.commit()
        if not wanted:
            return _valid(view(row, now)) if row is not None else None
        profile_ready = bool(access_profile) and access_profile.get("freshness") == "fresh"
        if profile_ready:
            # The lookup's transaction is closed above: nothing of this connection is held while it waits.
            done, why = sync_measure(settings, site_host, now=None)
            if done is not None:
                return _valid(view(done, datetime.now(timezone.utc)))
            if why == "joined":
                return _valid(view(row, now) if row is not None else pending_view(_profile_retry(access_profile)))
        _queue(conn, site_host)
        return _valid(view(row, now) if row is not None else pending_view(_profile_retry(access_profile)))
    except Exception:
        LOG.warning("site_probe_lookup_unavailable")
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def _valid(shown: dict | None) -> dict | None:
    """The view only if the answer schema accepts it; otherwise no ahn_check (the lookup itself never fails for it)."""
    if shown is None:
        return None
    from .models import AhnCheck
    try:
        AhnCheck(**shown)
    except Exception:
        LOG.warning("site_probe_view_invalid")
        return None
    return shown


def _fresh_or_waiting(row: dict, now: datetime) -> bool:
    """A stored row that needs no new check yet: its next check is not due (fresh, or under a backoff)."""
    return row["next_due_at"] > now


def _queue(conn, site_host: str) -> None:
    from . import background_reads
    try:
        with conn.transaction():
            queued = background_reads.enqueue(conn, [("site_probe", site_host)])
        conn.commit()
        if queued:
            background_reads.wake()
    except Exception:
        LOG.warning("site_probe_enqueue_unavailable")


# --------------------------------------------------------------------------------------------------------------
# The answer's view


def _profile_retry(access_profile: dict | None) -> int:
    """The wait a pending view shows: the answer's own pending access_profile wait when it has one (so the answer gives
    one number, not two), otherwise PENDING_RETRY_SECONDS."""
    if isinstance(access_profile, dict) and "freshness" in access_profile and access_profile["freshness"] == "pending" \
            and "retry_after_seconds" in access_profile:
        wait = access_profile["retry_after_seconds"]
        if isinstance(wait, int) and not isinstance(wait, bool):
            return min(max(wait, 1), 3600)
    return PENDING_RETRY_SECONDS


def _fixed_view(status: str) -> dict:
    return {"status": status, "basis": "ahn_server", "vantage": _vantage_codes(), "ua_token": "AgentHelpNetwork",
            "path_kind": "home", "fingerprint_version": FINGERPRINT_VERSION, "note": SHOW_VIEW_NOTE}


def pending_view(retry_after_seconds: int = PENDING_RETRY_SECONDS) -> dict:
    return {**_fixed_view("pending"), "retry_after_seconds": retry_after_seconds}


def not_measured_view(reason: str) -> dict:
    """A not_measured view built in memory (nothing stored, nothing requested), e.g. for documentation example hosts."""
    return {**_fixed_view("not_measured"), "not_measured_reason": reason}


def view(row: dict, now: datetime) -> dict | None:
    """The ahn_check object for a stored row: None when it is over 8 days old. Codes, numbers and times only."""
    age = now - row["probed_at"]
    if age > timedelta(days=STALE_DAYS):
        return None
    out = {"status": "not_measured" if row["outcome"] == "not_measured" else "measured", "basis": "ahn_server",
           "vantage": {key: value for key, value in (row.get("vantage") or _vantage_codes()).items() if key in ("kind", "region")},
           "ua_token": "AgentHelpNetwork", "path_kind": "home", "fingerprint_version": row["fingerprint_version"],
           "note": SHOW_VIEW_NOTE}
    if row["outcome"] == "not_measured":
        out["not_measured_reason"] = row["not_measured_reason"]
        return out
    out.update(measured_at=row["probed_at"], age_seconds=max(0, int(age.total_seconds())),
               freshness="fresh" if age <= timedelta(hours=FRESH_HOURS) else "stale", outcome=row["outcome"])
    if row["http_status"] is not None:
        out["http_status"] = row["http_status"]
    if row["bot_vendor"]:
        out["bot_defense"] = {"vendor": row["bot_vendor"], "signal": row["bot_signal"], "confidence": row["bot_confidence"]}
    vendors = [code for code in (row["edge_vendors"] or [])][:5]
    if vendors:
        out["edge_vendors_seen"] = vendors
    if row["redirect"]:
        out["redirect"] = row["redirect"]
    if row["login_signal"]:
        out["login_signal"] = row["login_signal"]
    if row["response_ms"] is not None:
        out["response_ms"] = row["response_ms"]
    return out


# --------------------------------------------------------------------------------------------------------------
# Today's list of hosts, the cron pass, the automatic stop


@lru_cache(maxsize=1)
def curated_hosts() -> tuple[str, ...]:
    """Hosts in ahn/probe_hosts.txt: names only, valid public eligible apex hosts, no duplicates."""
    from .models import valid_host
    from .service import aggregate_eligible_host
    seen: dict[str, None] = {}
    with open(HOSTS_PATH, encoding="utf-8-sig") as handle:
        for line in handle:
            host = line.strip()
            if not host or host.startswith("#") or host in seen:
                continue
            try:
                valid_host(host)
            except ValueError:
                continue
            if aggregate_eligible_host(host):
                seen[host] = None
    return tuple(seen)


@lru_cache(maxsize=1)
def tranco_hosts() -> frozenset[str]:
    """The seed file's last block (Tranco top 1,000 names): hosts we do not check, read as the control group."""
    hosts: set[str] = set()
    inside = False
    with open(SEED_PATH, encoding="utf-8-sig") as handle:
        for line in handle:
            text = line.strip()
            if text.startswith(TRANCO_MARKER):
                inside = True
            elif inside and text and not text.startswith("#"):
                hosts.add(text)
    return frozenset(hosts)


def compute_set(conn, now: datetime) -> dict:
    """Replace today's list of hosts to check: R (looked up after a blocked step in the last 14 days, most first, 300),
    C (the curated public list, 150), O (hosts we list an official or customer path for) up to 500 in all. Database
    reads and one table rewrite; no request."""
    from .lookup_signals import EXAMPLE_HOSTS, UNSPECIFIED
    from .models import valid_host
    from .official_paths import CONSUMER_PATHS, OFFICIAL_PATHS
    from .service import aggregate_eligible_host

    def usable(host: str) -> bool:
        try:
            valid_host(host)
        except ValueError:
            return False
        return aggregate_eligible_host(host) and host not in EXAMPLE_HOSTS

    chosen: dict[str, str] = {}
    rows = conn.execute(
        """SELECT site_host, sum(lookups) AS n FROM lookup_signals_hourly
           WHERE hour >= %s AND error_class <> %s GROUP BY site_host ORDER BY n DESC, site_host LIMIT 1000""",
        (now - timedelta(days=SET_WINDOW_DAYS), UNSPECIFIED)).fetchall()
    for row in rows:
        if len([1 for code in chosen.values() if code == "R"]) >= SET_REFUSED_MAX:
            break
        if usable(row["site_host"]):
            chosen.setdefault(row["site_host"], "R")
    count_c = 0
    for host in curated_hosts():
        if count_c >= SET_CURATED_MAX or len(chosen) >= PROBE_SET_MAX:
            break
        if host not in chosen and usable(host):
            chosen[host] = "C"
            count_c += 1
    for host in sorted(set(OFFICIAL_PATHS) | set(CONSUMER_PATHS)):
        if len(chosen) >= PROBE_SET_MAX:
            break
        if host not in chosen and usable(host):
            chosen[host] = "O"
    day = _utc_day(now)
    conn.execute("DELETE FROM site_probe_set")
    for rank, (host, code) in enumerate(chosen.items(), start=1):
        conn.execute("INSERT INTO site_probe_set (site_host,set_code,rank,set_day) VALUES (%s,%s,%s,%s)", (host, code, rank, day))
    return {code: sum(1 for value in chosen.values() if value == code) for code in "RCO"}


def _set_due(conn, now: datetime) -> bool:
    if now.astimezone(timezone.utc).hour < 6:
        return False
    row = conn.execute("SELECT max(set_day) AS day FROM site_probe_set").fetchone()
    return row["day"] is None or row["day"] < _utc_day(now)


def ensure_baseline(conn, now: datetime) -> None:
    """The refusal rate of the control hosts (seed hosts that are not checked) before the first check, kept once."""
    if conn.execute("SELECT 1 FROM probe_state WHERE name='baseline'").fetchone():
        return
    hosts = sorted(tranco_hosts())
    row = conn.execute(
        """SELECT count(*) AS n, count(*) FILTER (WHERE robots_status='refused') AS refused
           FROM host_access_profile WHERE site_host = ANY(%s) AND fetched_at IS NOT NULL
             AND site_host NOT IN (SELECT site_host FROM site_probe_set)""", (hosts,)).fetchone()
    rate = (row["refused"] / row["n"]) if row["n"] else 0.0
    conn.execute("INSERT INTO probe_state (name,value) VALUES ('baseline',%s) ON CONFLICT (name) DO NOTHING",
                 (Jsonb({"n": row["n"], "refused": row["refused"], "rate": rate, "taken_at": now.isoformat()}),))


BREAKER_MARGIN = 0.03        # control group refusals rise by this much over the baseline, two days running
BREAKER_MIN_READS = 20       # robots.txt reads in a day for its rate to count
BREAKER_FEED_MIN = 10        # failed feed reads (403/429) in the last two days for the doubling to count


BREAKER_RELEASE_AFTER = timedelta(hours=24)   # a stop is lifted by the next evaluation after this if its cause is gone


def evaluate_breaker(conn, now: datetime) -> str | None:
    """Stop every check when our own reads of unrelated hosts start being refused more (a sign our address is being
    marked as a bot), or status-feed 403/429 failures double. Returns the reason when it stops (also stored), else None.
    A stop is lifted again (and logged) by an evaluation at least a day later at which no cause is left."""
    row = conn.execute("SELECT value FROM probe_state WHERE name='breaker'").fetchone()
    if row and row["value"].get("stopped"):
        since = _parse_time(row["value"].get("since"))
        if since is not None and now - since >= BREAKER_RELEASE_AFTER and _breaker_cause(conn, now) is None:
            conn.execute("UPDATE probe_state SET value=%s,updated_at=now() WHERE name='breaker'",
                         (Jsonb({"stopped": False, "released_at": now.isoformat(), "was": row["value"].get("reason")}),))
            LOG.warning("site_probe_breaker_released reason=%s", row["value"].get("reason"))
        return None
    reason = _breaker_cause(conn, now)
    if reason:
        conn.execute("INSERT INTO probe_state (name,value) VALUES ('breaker',%s) ON CONFLICT (name) DO UPDATE SET value=EXCLUDED.value,updated_at=now()",
                     (Jsonb({"stopped": True, "reason": reason, "since": now.isoformat()}),))
    return reason


def _breaker_cause(conn, now: datetime) -> str | None:
    """'control_refusals', 'feed_refusals' or None for the last full days. The feed comparison needs the seven days
    before the last two to have been counted, i.e. the first baseline to be at least nine days old."""
    base = conn.execute("SELECT value FROM probe_state WHERE name='baseline'").fetchone()
    today = _utc_day(now)
    reason = None
    if base:
        rates = []
        for back in (1, 2):
            row = conn.execute("SELECT robots_reads,robots_refused FROM probe_breaker_daily WHERE day=%s AND grp='control'",
                               (today - timedelta(days=back),)).fetchone()
            rates.append(row["robots_refused"] / row["robots_reads"] if row and row["robots_reads"] >= BREAKER_MIN_READS else None)
        if all(rate is not None and rate >= base["value"]["rate"] + BREAKER_MARGIN for rate in rates):
            reason = "control_refusals"
    taken = _parse_time(base["value"].get("taken_at")) if base else None
    if reason is None and taken is not None and now - taken >= timedelta(days=9):
        recent = conn.execute("SELECT COALESCE(sum(feed_403+feed_429),0) AS n FROM probe_breaker_daily WHERE grp='feed' AND day>=%s AND day<%s",
                              (today - timedelta(days=2), today)).fetchone()["n"]
        before = conn.execute("SELECT COALESCE(sum(feed_403+feed_429),0) AS n FROM probe_breaker_daily WHERE grp='feed' AND day>=%s AND day<%s",
                              (today - timedelta(days=9), today - timedelta(days=2))).fetchone()["n"]
        if recent >= BREAKER_FEED_MIN and recent >= 2 * (before / 7 * 2):
            reason = "feed_refusals"
    return reason


def _in_probe_group(conn, site_host: str) -> bool:
    return conn.execute("SELECT 1 FROM site_probe_set WHERE site_host=%s UNION ALL SELECT 1 FROM site_probe WHERE site_host=%s LIMIT 1",
                        (site_host, site_host)).fetchone() is not None


def record_robots_read(conn, site_host: str, robots_status: str, now: datetime) -> None:
    """Count one robots.txt read for the automatic stop: probe_set (hosts we check) or control (seed hosts we do not).
    Inside a savepoint; never raises; counts only while the switch is on."""
    if not enabled():
        return
    try:
        with conn.transaction():
            group = "probe_set" if _in_probe_group(conn, site_host) else "control" if site_host in tranco_hosts() else None
            if group:
                conn.execute(
                    """INSERT INTO probe_breaker_daily (day,grp,robots_reads,robots_refused) VALUES (%s,%s,1,%s)
                       ON CONFLICT (day,grp) DO UPDATE SET robots_reads=probe_breaker_daily.robots_reads+1,
                           robots_refused=probe_breaker_daily.robots_refused+EXCLUDED.robots_refused""",
                    (_utc_day(now), group, 1 if robots_status == "refused" else 0))
    except Exception:
        LOG.warning("site_probe_counter_unavailable")


def record_feed_failure(conn, label: str) -> None:
    """Count one failed status-feed read with 403 or 429 (the label is status_cache.last_error_detail). Never raises."""
    if not enabled() or label not in ("http_403", "http_429"):
        return
    try:
        with conn.transaction():
            conn.execute(
                """INSERT INTO probe_breaker_daily (day,grp,feed_403,feed_429) VALUES (%s,'feed',%s,%s)
                   ON CONFLICT (day,grp) DO UPDATE SET feed_403=probe_breaker_daily.feed_403+EXCLUDED.feed_403,
                       feed_429=probe_breaker_daily.feed_429+EXCLUDED.feed_429""",
                (datetime.now(timezone.utc).date(), 1 if label == "http_403" else 0, 1 if label == "http_429" else 0))
    except Exception:
        LOG.warning("site_probe_counter_unavailable")


def run_cron(settings, *, cron_started: float | None = None, now: datetime | None = None, reader=None,
             limit: int = CRON_PROBES_PER_RUN, time_budget: float = CRON_TIME_BUDGET_SECONDS) -> dict:
    """The review cron's pass: refresh today's list once a day, evaluate the stop, then check up to ``limit`` due hosts of
    the list within ``time_budget`` seconds (a second one only if it still ends before CRON_SAFE_SECONDS counted from
    ``cron_started``). Returns counts, no host names."""
    from .db import connect
    result = {"set": 0, "done": 0, "reachable": 0, "challenge": 0, "refused": 0, "login": 0, "not_measured": 0,
              "budget_skips": 0, "breaker": "off"}
    if not enabled():
        result["breaker"] = "disabled"
        return result
    started = _clock()
    cron_started = started if cron_started is None else cron_started
    now = now or datetime.now(timezone.utc)
    with connect(settings) as conn:
        if _set_due(conn, now):  # the list first: the baseline leaves its hosts out
            compute_set(conn, now)
        ensure_baseline(conn, now)
        conn.commit()
        stopped = evaluate_breaker(conn, now)
        conn.commit()
        result["set"] = conn.execute("SELECT count(*) AS n FROM site_probe_set").fetchone()["n"]
        if _breaker_stopped(conn):
            result["breaker"] = "on:" + (stopped or conn.execute("SELECT value FROM probe_state WHERE name='breaker'").fetchone()["value"].get("reason", "stopped"))
            conn.commit()
            return result
        candidates = conn.execute(
            """SELECT s.site_host,s.set_code FROM site_probe_set s LEFT JOIN site_probe p ON p.site_host=s.site_host
               WHERE p.site_host IS NULL OR p.next_due_at <= %s ORDER BY s.rank,s.site_host LIMIT %s""",
            (now, CRON_CANDIDATES)).fetchall()
        conn.commit()
    for candidate in candidates:
        if result["done"] >= limit or _clock() - started >= time_budget:
            break
        if (_clock() - cron_started) + PROBE_WORST_SECONDS > CRON_SAFE_SECONDS:
            break
        host = candidate["site_host"]
        try:
            row = measure(settings, host, f"set_{candidate['set_code']}", now=None, reader=reader)
        except Exception as exc:  # one host must not stop the pass
            LOG.warning("site_probe_failed error=%s", type(exc).__name__)
            continue
        if row is None:
            result["budget_skips"] += 1
            continue
        result["done"] += row["outcome"] != "not_measured"
        outcome = row["outcome"]
        if outcome == "not_measured":
            result["not_measured"] += 1
        elif outcome == "reachable":
            result["reachable"] += 1
        elif outcome == "bot_challenge":
            result["challenge"] += 1
        elif outcome == "refused":
            result["refused"] += 1
        elif outcome == "login_wall":
            result["login"] += 1
    return result


def format_result(result: dict) -> str:
    return " ".join(f"{key}={value}" for key, value in result.items())


def prune(conn) -> None:
    """Retention: the current rows 30 days after their check, daily counts and breaker counts 35 days."""
    conn.execute("DELETE FROM site_probe WHERE probed_at < now() - interval '30 days'")
    conn.execute("DELETE FROM site_probe_daily WHERE day < CURRENT_DATE - 34")
    conn.execute("DELETE FROM probe_breaker_daily WHERE day < CURRENT_DATE - 34")
