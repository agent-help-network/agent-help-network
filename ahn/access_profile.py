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
import threading
from datetime import datetime, timedelta, timezone

import urllib3
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
ROBOTS_CAP = 65_536
LLMS_CAP = 4_096
MAX_LINES = 2_000
_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$")
_HOST_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _groups(text: str) -> tuple[list[dict], bool]:
    """Tolerant RFC 9309 group split: user-agent lines open a group, rule lines close its agent list."""
    groups: list[dict] = []
    current: dict | None = None
    expecting_agents = False
    sitemap = False
    for line in text.splitlines()[:MAX_LINES]:
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower().replace(" ", ""), value.strip()
        if key in ("user-agent", "useragent"):
            agent = value.split("/", 1)[0].split()[0].lower() if value.split() else ""
            if current is None or not expecting_agents:
                current = {"agents": [], "rules": []}
                groups.append(current)
            if agent:
                current["agents"].append(agent)
            expecting_agents = True
            continue
        if key == "sitemap":
            sitemap = True
            continue
        expecting_agents = False
        if key in ("allow", "disallow") and current is not None:
            current["rules"].append((key, value))
    return groups, sitemap


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
    groups, sitemap = _groups(text)
    wildcard = [group for group in groups if "*" in group["agents"]]
    wildcard_rules = [rule for group in wildcard for rule in group["rules"]]
    wildcard_root_allowed = _root_allowed(wildcard_rules)
    rules, blocked, allowed = [], [], []
    for token, kind in AGENT_USER_AGENTS:
        lowered = token.lower()
        matched = [group for group in groups if any(_applies(agent, lowered) for agent in group["agents"])]
        explicit = bool(matched)
        effective = [rule for group in (matched if explicit else wildcard) for rule in group["rules"]]
        root_allowed = _root_allowed(effective) if explicit else wildcard_root_allowed
        rules.append({
            "agent": token, "kind": kind, "explicit_group": explicit, "root_allowed": root_allowed,
            "disallow_count": sum(1 for rule_kind, pattern in effective if rule_kind == "disallow" and pattern),
        })
        if explicit:
            (allowed if root_allowed else blocked).append(token)
    return {
        "rules": rules, "wildcard_root_allowed": wildcard_root_allowed, "sitemap": sitemap,
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


def _www_redirect_target(response, site_host: str, path: str) -> str | None:
    """The only redirect we follow: apex → its own www host, same scheme and path (one hop)."""
    location = (response.headers.get("Location") or "").strip()
    for candidate in (f"https://www.{site_host}{path}", f"https://www.{site_host}{path}?"):
        if location == candidate:
            return f"www.{site_host}"
    return None


def read_public_file(
    site_host: str, path: str, *, cap_bytes: int, timeout_connect: float = 2.0, timeout_read: float = 3.0,
    _hop: int = 0,
) -> tuple[str, bytes | None]:
    """Fetch one well-known file from a pinned public IP; never raises for network, DNS, TLS or HTTP failures."""
    site_host = site_host.strip().rstrip(".").lower()
    if not _HOST_RE.match(site_host) or not path.startswith("/") or "//" in path or cap_bytes < 1:
        return ("error", None)
    try:
        addresses = _resolve(site_host, 2.0)
        if not addresses or len(addresses) > 16:
            return ("error", None)
        if any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            return ("not_public", None)
        pool = urllib3.HTTPSConnectionPool(
            sorted(addresses)[0], port=443, server_hostname=site_host, assert_hostname=site_host,
            timeout=urllib3.Timeout(connect=timeout_connect, read=timeout_read), retries=False,
        )
        try:
            response = pool.request(
                "GET", path, headers={"Host": site_host, "User-Agent": USER_AGENT, "Accept": "text/plain,*/*;q=0.5"},
                redirect=False, preload_content=False,
            )
            try:
                if response.status in (404, 410):
                    return ("absent", None)
                if 300 <= response.status < 400:
                    # Many apex hosts answer only on www; that is still the host's own file.
                    www = _www_redirect_target(response, site_host, path) if _hop == 0 else None
                    if www:
                        response.release_conn()
                        pool.close()
                        return read_public_file(www, path, cap_bytes=cap_bytes, timeout_connect=timeout_connect,
                                                timeout_read=timeout_read, _hop=1)
                    return ("redirected", None)
                if response.status != 200:
                    return ("error", None)
                body = response.read(cap_bytes + 1)
                return ("too_large", None) if len(body) > cap_bytes else ("ok", body)
            finally:
                response.release_conn()
        finally:
            pool.close()
    except Exception:  # a lookup must never fail or hang because a third-party host misbehaves
        return ("error", None)


def build_profile(site_host: str, *, reader=read_public_file) -> dict:
    robots_status, robots_body = reader(site_host, "/robots.txt", cap_bytes=ROBOTS_CAP)
    robots = parse_robots(robots_body) if robots_status == "ok" and robots_body is not None else None
    llms_status, _ = reader(site_host, "/llms.txt", cap_bytes=LLMS_CAP)
    llms_txt_status = "present" if llms_status in ("ok", "too_large") else llms_status
    return {
        "robots_status": robots_status, "robots": robots, "llms_txt_status": llms_txt_status,
        "source_urls": {"robots_txt": f"https://{site_host}/robots.txt", "llms_txt": f"https://{site_host}/llms.txt"},
    }


def _freshness(fetched_at, error_at, now: datetime, max_age: timedelta) -> str:
    if fetched_at is None:
        return "pending"
    if fetched_at >= now - max_age and (error_at is None or fetched_at >= error_at):
        return "fresh"
    return "stale"


def _as_profile(site_host: str, row, now: datetime, max_age: timedelta) -> dict:
    return {
        "robots_status": row["robots_status"], "robots": row["robots"], "llms_txt_status": row["llms_txt_status"],
        "source_urls": {"robots_txt": f"https://{site_host}/robots.txt", "llms_txt": f"https://{site_host}/llms.txt"},
        "fetched_at": row["fetched_at"], "freshness": _freshness(row["fetched_at"], row["last_error_at"], now, max_age),
    }


def get_profile(settings, site_host: str, *, now=None, max_age_hours: int = 24, reader=read_public_file) -> dict | None:
    """Cached declared policy; at most one read attempt per host per max_age window, success or not."""
    now = now or datetime.now(timezone.utc)
    max_age = timedelta(hours=max_age_hours)
    query = "SELECT fetched_at,robots_status,robots,llms_txt_status,last_error_at FROM host_access_profile WHERE site_host=%s"
    with _LOCKS_GUARD:
        lock = _HOST_LOCKS.setdefault(site_host, threading.Lock())
    with lock:
        with connect(settings) as conn:
            row = conn.execute(query, (site_host,)).fetchone()
        if row:
            latest = max((t for t in (row["fetched_at"], row["last_error_at"]) if t is not None), default=None)
            if latest is not None and latest >= now - max_age:
                return _as_profile(site_host, row, now, max_age)
        profile = build_profile(site_host, reader=reader)
        with connect(settings) as conn:
            if profile["robots_status"] == "error":
                # Network failure: keep the previous declared policy, only record the failed attempt.
                row = conn.execute(
                    """INSERT INTO host_access_profile (site_host,fetched_at,robots_status,robots,llms_txt_status,last_error_at)
                       VALUES (%s,NULL,'error',NULL,%s,%s) ON CONFLICT (site_host) DO UPDATE SET
                       last_error_at=EXCLUDED.last_error_at
                       RETURNING fetched_at,robots_status,robots,llms_txt_status,last_error_at""",
                    (site_host, profile["llms_txt_status"], now),
                ).fetchone()
            else:
                row = conn.execute(
                    """INSERT INTO host_access_profile (site_host,fetched_at,robots_status,robots,llms_txt_status,last_error_at)
                       VALUES (%s,%s,%s,%s,%s,NULL) ON CONFLICT (site_host) DO UPDATE SET
                       fetched_at=EXCLUDED.fetched_at,robots_status=EXCLUDED.robots_status,robots=EXCLUDED.robots,
                       llms_txt_status=EXCLUDED.llms_txt_status,last_error_at=NULL
                       RETURNING fetched_at,robots_status,robots,llms_txt_status,last_error_at""",
                    (site_host, now, profile["robots_status"], Jsonb(profile["robots"]) if profile["robots"] is not None else None,
                     profile["llms_txt_status"]),
                ).fetchone()
        return _as_profile(site_host, row, now, max_age)
