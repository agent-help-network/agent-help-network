"""Declared agent policy of a public hostname, read from its own robots.txt and llms.txt.

Safety posture: only eligible public apex hosts, only public (global) IP addresses, no
redirects followed, hard byte caps, at most one read per host per day, and never a probe:
the two well-known files are the only paths ever requested, and the result is reported as
DECLARED policy, never as an access guarantee.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import re
import socket
import ssl
import time
import threading
from datetime import datetime, timedelta, timezone

import urllib3
from urllib.parse import urlparse
from psycopg.types.json import Jsonb

from .db import connect

AGENT_USER_AGENTS: tuple[tuple[str, str], ...] = (
    ("ChatGPT-User", "user_agent"), ("Claude-User", "user_agent"), ("Perplexity-User", "user_agent"),
    ("Meta-ExternalFetcher", "user_agent"), ("MistralAI-User", "user_agent"), ("OAI-SearchBot", "search_crawler"),
    ("Claude-SearchBot", "search_crawler"), ("PerplexityBot", "search_crawler"), ("Googlebot", "search_crawler"),
    ("Bingbot", "search_crawler"), ("DuckAssistBot", "search_crawler"), ("Amazonbot", "search_crawler"),
    ("GPTBot", "training_crawler"), ("ClaudeBot", "training_crawler"), ("anthropic-ai", "training_crawler"),
    ("Google-Extended", "training_crawler"), ("Applebot-Extended", "training_crawler"), ("Meta-ExternalAgent", "training_crawler"),
    ("CCBot", "training_crawler"), ("Bytespider", "training_crawler"), ("cohere-ai", "training_crawler"), ("Diffbot", "training_crawler"),
)
USER_AGENT = "AgentHelpNetwork/0.2 (+https://agenthelpnetwork.com/en/policy)"
ROBOTS_CAP = 262_144
LLMS_CAP = 4_096
MAX_LINES = 5_000
MAX_SITEMAPS = 5
MAX_PATH_SAMPLES = 6
_HTML_MARKERS = (b"<!doctype", b"<html", b"<head", b"<body", b"<?xml")
_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$")
_HOST_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _groups(text: str) -> tuple[list[dict], list[str]]:
    """Tolerant RFC 9309 group split: user-agent lines open a group, rule lines close its agent list."""
    groups: list[dict] = []
    current: dict | None = None
    expecting_agents = False
    sitemaps: list[str] = []
    for line in text.splitlines()[:MAX_LINES]:
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower().replace(" ", ""), value.strip()
        if key in ("user-agent", "useragent"):
            agent = value.split("/", 1)[0].split()[0].lower() if value.split() else ""
            if current is None or not expecting_agents:
                current = {"agents": [], "rules": [], "crawl_delay": None}
                groups.append(current)
            if agent:
                current["agents"].append(agent)
            expecting_agents = True
            continue
        if key == "sitemap":
            if value.startswith(("https://", "http://")) and len(value) <= 300 and len(sitemaps) < MAX_SITEMAPS and value not in sitemaps:
                sitemaps.append(value)
            continue
        expecting_agents = False
        if current is None:
            continue
        if key in ("allow", "disallow"):
            current["rules"].append((key, value))
        elif key == "crawl-delay":
            try:
                delay = float(value)
                if 0 <= delay <= 3600:
                    current["crawl_delay"] = delay
            except ValueError:
                pass
    return groups, sitemaps


def _disallow_samples(rules: list[tuple[str, str]]) -> list[str]:
    """A few disallowed path prefixes, so 'root allowed' is not read as 'everything allowed'."""
    seen: list[str] = []
    for kind, pattern in rules:
        if kind == "disallow" and pattern and pattern not in seen:
            seen.append(pattern[:80])
            if len(seen) >= MAX_PATH_SAMPLES:
                break
    return seen


def _path_matches(pattern: str, path: str) -> bool:
    if not pattern:
        return False
    anchored = pattern.endswith("$")
    core = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in core.split("*")) + ("$" if anchored else "")
    return re.match(regex, path) is not None


def _root_allowed(rules: list[tuple[str, str]]) -> bool:
    """Longest-match precedence; equal length → Allow wins; no match → allowed."""
    best_len, allowed = -1, True
    for kind, pattern in rules:
        if not _path_matches(pattern, "/"):
            continue
        length = len(pattern)
        if length > best_len or (length == best_len and kind == "allow"):
            best_len, allowed = length, kind == "allow"
    return allowed


def _applies(agent: str, token: str) -> bool:
    return agent != "*" and (agent == token or (len(agent) >= 3 and token.startswith(agent)))


def parse_robots(text: str | bytes) -> dict:
    """Pure. Declared root ('/') policy per known agent token; tolerant of junk, BOM, CRLF, comments."""
    if isinstance(text, bytes):
        text = text[:ROBOTS_CAP].decode("utf-8", errors="replace")
    text = text[:ROBOTS_CAP].lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    groups, sitemaps = _groups(text)
    wildcard = [group for group in groups if "*" in group["agents"]]
    wildcard_rules = [rule for group in wildcard for rule in group["rules"]]
    wildcard_root_allowed = _root_allowed(wildcard_rules)
    wildcard_delay = next((g["crawl_delay"] for g in wildcard if g["crawl_delay"] is not None), None)
    rules, blocked, allowed = [], [], []
    for token, kind in AGENT_USER_AGENTS:
        lowered = token.lower()
        matched = [group for group in groups if any(_applies(agent, lowered) for agent in group["agents"])]
        explicit = bool(matched)
        effective = [rule for group in (matched if explicit else wildcard) for rule in group["rules"]]
        root_allowed = _root_allowed(effective) if explicit else wildcard_root_allowed
        delay = next((g["crawl_delay"] for g in matched if g["crawl_delay"] is not None), None) if explicit else wildcard_delay
        rules.append({
            "agent": token, "kind": kind, "explicit_group": explicit, "root_allowed": root_allowed,
            "disallow_count": sum(1 for rule_kind, pattern in effective if rule_kind == "disallow" and pattern),
            "crawl_delay": delay, "disallow_paths": _disallow_samples(effective),
        })
        if explicit:
            (allowed if root_allowed else blocked).append(token)
    return {
        "rules": rules, "wildcard_root_allowed": wildcard_root_allowed, "sitemap": bool(sitemaps),
        "sitemaps": sitemaps, "wildcard_crawl_delay": wildcard_delay, "wildcard_disallow_paths": _disallow_samples(wildcard_rules),
        "agents_blocked": blocked, "agents_allowed": allowed,
    }


def _resolve(site_host: str, timeout: float) -> set[str]:
    """getaddrinfo in a worker thread so a stalled resolver cannot hold the request."""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(socket.getaddrinfo, site_host, 443, type=socket.SOCK_STREAM)
        return {item[4][0] for item in future.result(timeout=timeout)}
    finally:
        executor.shutdown(wait=False)


MAX_ADDRESSES_TRIED = 3
CONNECT_BUDGET_FACTOR = 2  # all connection attempts of one read share timeout_connect * this
_clock = time.monotonic  # patched in tests


def _transport_label(exc: BaseException) -> str:
    """Why a pinned-IP HTTPS read failed, as a short label; a timeout is not a refusal and TLS is not DNS."""
    if isinstance(exc, urllib3.exceptions.NameResolutionError):
        return "dns_failed"
    if isinstance(exc, (urllib3.exceptions.SSLError, ssl.SSLError)):
        return "tls_error"
    if isinstance(exc, urllib3.exceptions.NewConnectionError):  # subclass of ConnectTimeoutError: check first
        return "connect_error"
    if isinstance(exc, urllib3.exceptions.ConnectTimeoutError):
        return "connect_timeout"
    if isinstance(exc, (urllib3.exceptions.ReadTimeoutError, TimeoutError)):
        return "read_timeout"
    if isinstance(exc, (urllib3.exceptions.ProtocolError, urllib3.exceptions.DecodeError)):
        return "read_error"
    return "connect_error"


def _address_order(addresses: set[str]) -> list[str]:
    """IPv4 before IPv6 (the deployment has no IPv6 egress), then a stable order; at most MAX_ADDRESSES_TRIED."""
    return sorted(addresses, key=lambda ip: (ipaddress.ip_address(ip).version != 4, ip))[:MAX_ADDRESSES_TRIED]


def _www_redirect_target(response, site_host: str, path: str) -> str | None:
    """The only redirect we follow: apex → its own www host, same scheme and path (one hop)."""
    location = (response.headers.get("Location") or "").strip()
    for candidate in (f"https://www.{site_host}{path}", f"https://www.{site_host}{path}?"):
        if location == candidate:
            return f"www.{site_host}"
    return None


# An edge that answers robots.txt itself with one of these is refusing automated
# reads, which is a signal in its own right and distinct from a transport failure.
REFUSAL_STATUSES = frozenset({401, 403, 429, 503})
# Transport failures are retried after this long; definitive answers keep max_age.
ERROR_RETRY = timedelta(hours=1)


def fetch_public_file(
    site_host: str, path: str, *, cap_bytes: int, timeout_connect: float = 2.0, timeout_read: float = 3.0,
    _hop: int = 0,
) -> tuple[str, bytes | None, str | None]:
    """Fetch one well-known file from a pinned public IP; never raises for network, DNS, TLS or HTTP failures.

    Returns (status, body, detail). detail is a short machine label such as
    'http_503', 'dns_timeout' or 'connect_error' so the answer can say why.
    """
    site_host = site_host.strip().rstrip(".").lower()
    if not _HOST_RE.match(site_host) or not path.startswith("/") or "//" in path or cap_bytes < 1:
        return ("error", None, "invalid_input")
    try:
        try:
            addresses = _resolve(site_host, 2.0)
        except concurrent.futures.TimeoutError:
            return ("error", None, "dns_timeout")
        except (OSError, socket.gaierror):
            return ("error", None, "dns_failed")
        if not addresses or len(addresses) > 16:
            return ("error", None, "dns_failed")
        if any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            return ("not_public", None, "not_public")
        last_label = "connect_error"
        # One address of a host can be dead from this network while another answers (seen with amazon.com),
        # so a failure to connect moves on to the next address. All attempts share one connection-time
        # budget, so a host that drops every packet costs at most CONNECT_BUDGET_FACTOR connect timeouts.
        connect_budget = timeout_connect * CONNECT_BUDGET_FACTOR
        for address in _address_order(addresses):
            if connect_budget <= 0:
                break
            started = _clock()
            pool = urllib3.HTTPSConnectionPool(
                address, port=443, server_hostname=site_host, assert_hostname=site_host,
                timeout=urllib3.Timeout(connect=min(timeout_connect, connect_budget), read=timeout_read), retries=False,
            )
            try:
                try:
                    response = pool.request(
                        "GET", path, headers={"Host": site_host, "User-Agent": USER_AGENT, "Accept": "text/plain,*/*;q=0.5"},
                        redirect=False, preload_content=False,
                    )
                except urllib3.exceptions.ConnectTimeoutError as exc:
                    # Refused, unreachable or a SYN timeout: nothing was established, try the next address.
                    # Anything after a connection is established is reported as is and never retried.
                    last_label = _transport_label(exc)
                    pool.close()
                    connect_budget -= _clock() - started
                    continue
            except Exception:
                pool.close()
                raise
            try:
                status = response.status
                if status in (404, 410):
                    return ("absent", None, f"http_{status}")
                if 300 <= status < 400:
                    # Many apex hosts answer only on www; that is still the host's own file.
                    www = _www_redirect_target(response, site_host, path) if _hop == 0 else None
                    if www:
                        response.release_conn()
                        pool.close()
                        return fetch_public_file(www, path, cap_bytes=cap_bytes, timeout_connect=timeout_connect,
                                                 timeout_read=timeout_read, _hop=1)
                    target = urlparse((response.headers.get("Location") or "").strip()).hostname or ""
                    return ("redirected", None, (f"redirect:{target}" if target else f"http_{status}")[:32])
                if status in REFUSAL_STATUSES:
                    return ("refused", None, f"http_{status}")
                if status != 200:
                    return ("error", None, f"http_{status}")
                body = response.read(cap_bytes + 1)
                if len(body) > cap_bytes:
                    return ("too_large", None, "too_large")
                if body.lstrip()[:12].lower().startswith(_HTML_MARKERS):
                    # A web page served for an unknown path (soft 404) is not a policy file.
                    return ("invalid", None, "html_body")
                return ("ok", body, "http_200")
            finally:
                response.release_conn()
                pool.close()
        return ("error", None, last_label)
    except Exception as exc:  # a lookup must never fail or hang because a third-party host misbehaves
        return ("error", None, _transport_label(exc))


def read_public_file(site_host: str, path: str, *, cap_bytes: int, timeout_connect: float = 2.0,
                     timeout_read: float = 3.0, _hop: int = 0) -> tuple[str, bytes | None]:
    status, body, _detail = fetch_public_file(site_host, path, cap_bytes=cap_bytes, timeout_connect=timeout_connect,
                                              timeout_read=timeout_read, _hop=_hop)
    return (status, body)


def _read(reader, site_host: str, path: str, cap_bytes: int, **options) -> tuple[str, bytes | None, str | None]:
    """Accept both the 2-tuple reader contract (tests, callers) and the detailed 3-tuple; options reach the reader."""
    result = reader(site_host, path, cap_bytes=cap_bytes, **options)
    if len(result) == 2:
        return (result[0], result[1], None)
    return (result[0], result[1], result[2])


def build_profile(site_host: str, *, reader=fetch_public_file) -> dict:
    robots_status, robots_body, robots_detail = _read(reader, site_host, "/robots.txt", ROBOTS_CAP)
    read_host = site_host
    if robots_status in ("error", "refused", "redirected", "invalid") and not site_host.startswith("www."):
        # Apex failed or refused: the www alias is still the host's own file and often the one that answers.
        # The apex->www step is the one redirect hop a read may spend: the www host has to answer itself.
        alt_status, alt_body, alt_detail = _read(reader, f"www.{site_host}", "/robots.txt", ROBOTS_CAP, _hop=1)
        if alt_status in ("ok", "absent", "too_large"):
            robots_status, robots_body, robots_detail, read_host = alt_status, alt_body, alt_detail, f"www.{site_host}"
    robots = parse_robots(robots_body) if robots_status == "ok" and robots_body is not None else None
    llms_status, _, llms_detail = _read(reader, read_host, "/llms.txt", LLMS_CAP, **({"_hop": 1} if read_host != site_host else {}))
    # A soft-404 page is 'absent' for llms.txt purposes; the detail keeps the evidence.
    llms_txt_status = "present" if llms_status in ("ok", "too_large") else "absent" if llms_status == "invalid" else llms_status
    return {
        "robots_status": robots_status, "robots": robots, "robots_detail": robots_detail,
        "llms_txt_status": llms_txt_status, "llms_detail": llms_detail, "read_host": read_host,
        "source_urls": {"robots_txt": f"https://{read_host}/robots.txt", "llms_txt": f"https://{read_host}/llms.txt"},
    }


def _freshness(fetched_at, error_at, now: datetime, max_age: timedelta) -> str:
    if fetched_at is None:
        return "pending"
    if fetched_at >= now - max_age and (error_at is None or fetched_at >= error_at):
        return "fresh"
    return "stale"


_ROW_COLUMNS = "fetched_at,robots_status,robots,llms_txt_status,last_error_at,robots_detail,llms_detail,read_host"


def _as_profile(site_host: str, row, now: datetime, max_age: timedelta) -> dict:
    # The host that supplied the files: the apex, or its www alias after the fallback. Rows written
    # before the column existed, and rows that never got an answer, name the looked-up host.
    read_host = row["read_host"] or site_host
    return {
        "robots_status": row["robots_status"], "robots": row["robots"], "robots_detail": row["robots_detail"],
        "llms_txt_status": row["llms_txt_status"], "llms_detail": row["llms_detail"], "read_host": read_host,
        "source_urls": {"robots_txt": f"https://{read_host}/robots.txt", "llms_txt": f"https://{read_host}/llms.txt"},
        "fetched_at": row["fetched_at"], "freshness": _freshness(row["fetched_at"], row["last_error_at"], now, max_age),
    }


def _cache_window(row, max_age: timedelta) -> timedelta:
    """A transport failure is retried after ERROR_RETRY; every definitive answer keeps the caller's max_age."""
    failed_last = row["last_error_at"] is not None and (row["fetched_at"] is None or row["last_error_at"] > row["fetched_at"])
    return min(max_age, ERROR_RETRY) if failed_last else max_age


def get_profile(settings, site_host: str, *, now=None, max_age_hours: int = 24, reader=fetch_public_file) -> dict | None:
    """Cached declared policy; one read attempt per host per window (max_age, or ERROR_RETRY after a transport failure)."""
    now = now or datetime.now(timezone.utc)
    max_age = timedelta(hours=max_age_hours)
    query = f"SELECT {_ROW_COLUMNS} FROM host_access_profile WHERE site_host=%s"
    with _LOCKS_GUARD:
        lock = _HOST_LOCKS.setdefault(site_host, threading.Lock())
    with lock:
        with connect(settings) as conn:
            row = conn.execute(query, (site_host,)).fetchone()
        if row:
            latest = max((t for t in (row["fetched_at"], row["last_error_at"]) if t is not None), default=None)
            if latest is not None and latest >= now - _cache_window(row, max_age):
                return _as_profile(site_host, row, now, max_age)
        profile = build_profile(site_host, reader=reader)
        with connect(settings) as conn:
            if profile["robots_status"] == "error":
                # Transport failure: keep the previous declared policy, record the attempt and why it failed.
                row = conn.execute(
                    f"""INSERT INTO host_access_profile (site_host,fetched_at,robots_status,robots,llms_txt_status,last_error_at,robots_detail,llms_detail)
                        VALUES (%s,NULL,'error',NULL,%s,%s,%s,%s) ON CONFLICT (site_host) DO UPDATE SET
                        last_error_at=EXCLUDED.last_error_at,robots_detail=EXCLUDED.robots_detail
                        RETURNING {_ROW_COLUMNS}""",
                    (site_host, profile["llms_txt_status"], now, profile["robots_detail"], profile["llms_detail"]),
                ).fetchone()
            else:
                # ok/absent/redirected/too_large/not_public/refused are answers in their own right.
                row = conn.execute(
                    f"""INSERT INTO host_access_profile (site_host,fetched_at,robots_status,robots,llms_txt_status,last_error_at,robots_detail,llms_detail,read_host)
                        VALUES (%s,%s,%s,%s,%s,NULL,%s,%s,%s) ON CONFLICT (site_host) DO UPDATE SET
                        fetched_at=EXCLUDED.fetched_at,robots_status=EXCLUDED.robots_status,robots=EXCLUDED.robots,
                        llms_txt_status=EXCLUDED.llms_txt_status,last_error_at=NULL,
                        robots_detail=EXCLUDED.robots_detail,llms_detail=EXCLUDED.llms_detail,read_host=EXCLUDED.read_host
                        RETURNING {_ROW_COLUMNS}""",
                    (site_host, now, profile["robots_status"], Jsonb(profile["robots"]) if profile["robots"] is not None else None,
                     profile["llms_txt_status"], profile["robots_detail"], profile["llms_detail"], profile["read_host"]),
                ).fetchone()
        return _as_profile(site_host, row, now, max_age)
