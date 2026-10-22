#!/usr/bin/env python3
"""Agent Help Network lookup hook for Claude Code. Standard library only, Python 3.7+.

Claude Code pipes a PostToolUseFailure or PostToolUse payload on stdin. When a
web step was blocked or the service failed, this script keeps only the target
hostname, asks Agent Help Network what the host declares and what it recommends,
and prints a short additionalContext for Claude. It sends a hostname and enum
codes, nothing else, it only reads, and it never blocks or fails the tool call:
every path exits 0. An error that is neither a block nor an outage (404, 410, 400,
422 and every other 4xx outside status_class) gets no lookup and adds no text.

Environment:
  AHN_HOOK_DISABLE=1          do nothing
  AHN_HOOK_SKIP_HOSTS=a.com,b  never look up these hosts or their subdomains
  AHN_HOOK_DEBUG=1            one-line note on stderr about what happened
  AHN_HOOK_FAKE_RESPONSE=path read the answer from a JSON file (no network)
  CLAUDE_PLUGIN_DATA          set by Claude Code; the rate-limit cache lives there

A lookup carries the hostname, error_class, failure_stage and, when the tool result already holds them, four more
enum codes: http_status, challenge, retry_after and via. Nothing else is read out of the result for the request: no
path, query, body text or header value leaves the machine.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import ssl
except ImportError:  # A Python built without ssl cannot look anything up; the hook stays silent.
    ssl = None

API_URL = "https://agenthelpnetwork.com/v1/incidents/check"
USER_AGENT = "ahn-claude-code-plugin/0.2"
OWN_HOST = "agenthelpnetwork.com"
SOCKET_TIMEOUT_SECONDS = 4  # each socket operation (connect, read)
TOTAL_TIMEOUT_SECONDS = 4.0  # the whole lookup, DNS included
WORKER_NAME = "ahn-lookup"
CACHE_FILE = "ahn-hook-cache.json"
CACHE_TTL_SECONDS = 600
CACHE_MAX_ENTRIES = 200
CACHE_MAX_BYTES = 131_072
MAX_CONTEXT = 700
MAX_STDIN_BYTES = 8_000_000
MAX_RESPONSE_BYTES = 262_144

HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
# Special-use and private-network names (RFC 6761/6762/8375, Tor, common LAN
# suffixes). Decided by name only: the hook never resolves anything.
SKIP_TLDS = frozenset({"localhost", "local", "internal", "test", "lan", "arpa", "onion", "invalid", "example",
                       "home", "corp", "intranet", "private", "localdomain", "alt",
                       # Cluster-internal names: Kubernetes services (web.default.svc), Consul, cluster DNS.
                       "svc", "consul", "cluster"})
# Wildcard-DNS services that turn an address into a name (10.0.0.1.nip.io, 10-0-0-1.sslip.io): the name stands for an address.
ADDRESS_NAME_SUFFIXES = ("nip.io", "sslip.io", "xip.io")
# An IPv4 address written into a name with dots or dashes (192-168-1-5.example.com, ec2-10-0-0-1.compute.internal).
EMBEDDED_IPV4 = re.compile(r"(?<![0-9])[0-9]{1,3}([.-])[0-9]{1,3}\1[0-9]{1,3}\1[0-9]{1,3}(?![0-9])")
URL_IN_TEXT = re.compile(r"https?://[^\s'\"<>()\[\]{}`]+", re.IGNORECASE)
# Claude Code's own WebFetch refusal ("Claude Code is unable to fetch from <host>") is the only
# place a bare, scheme-less hostname is read from error text. Elsewhere dotted tokens are code
# (document.querySelector, locator.click, urllib3.exceptions...) or path segments (C:\acme.budget).
REFUSAL = re.compile(r"\bis unable to fetch from\s+(?:https?://)?"
                     r"((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62})(?![\w-]|\.\w)", re.IGNORECASE)
FULL_URL = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*)://")
# A scheme-less tool argument: host[:port] then a path, query, fragment or nothing. "about:blank",
# "javascript:..." and "mailto:..." do not match.
SCHEMELESS = re.compile(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:[/?#].*)?", re.DOTALL)
# Fields of an MCP tool's input that name the page it acted on. WebFetch has only "url".
URL_FIELDS = ("url", "uri", "href", "link", "page_url", "pageUrl", "target_url", "targetUrl")
# Not the bare brand name: "cloudflare" also appears in Cloudflare 5xx pages and block pages (error 1020).
HUMAN_VERIFICATION = re.compile(r"captcha|verify (?:that )?you are (?:a )?human|are you a robot|challenge"
                                r"|checking your browser|just a moment", re.I)
# The first match wins. Text that matches none of these is neither a block nor an outage (a 404 page, a 422
# from a bad request, "something failed"), and classify() gives it no class.
CLASSES = (
    ("human_verification", HUMAN_VERIFICATION),
    # 401 and 407: credentials required (RFC 9110 15.5.2, 15.5.8).
    ("login_auth", re.compile(r"log[ -]?in\b|sign[ -]?in\b|unauthori[sz]ed|\b40[17]\b|\bauthenticat(?:e|ion)\b", re.I)),
    # 5xx (RFC 9110 15.6), 408 (RFC 9110 15.5.9) and 429 (RFC 6585 4), for which the contract has no class of its
    # own. "Unavailable For Legal Reasons" is the 451 reason phrase (RFC 7725): a block, not an outage.
    ("service_error", re.compile(r"\b5\d\d\b|\b408\b|\b429\b|too many requests|rate[ -]?limit|time ?out|timed[ _]?out"
                                 r"|unavailable(?! for legal reasons)|reset|bad gateway", re.I)),
    # 403 (RFC 9110 15.5.4), 451 (RFC 7725) and 402 (RFC 9110 15.5.3, see status_class), plus the usual wording
    # of a refusal.
    ("site_access", re.compile(r"\b(?:402|403|451)\b|payment required|forbidden|\bdenied\b|blocked|disallow|legal reasons", re.I)),
)
# Nothing was received from the site: Node errno names, Chrome "net::ERR_*" codes (browser MCP tools), and
# Claude Code's pre-request WebFetch refusals.
BEFORE_RESPONSE = re.compile(r"connection[ _]refused|could not resolve|timed[ _]?out|ENOTFOUND|ECONNREFUSED|getaddrinfo"
                             r"|is unable to fetch from|unable to verify if domain|is blocked by the network"
                             r"|ERR_NAME_NOT_RESOLVED|ERR_INTERNET_DISCONNECTED|ERR_ADDRESS_UNREACHABLE", re.I)
HOST_LIKE = re.compile(r"[A-Za-z0-9.-]+")
# Characters IDNA2003 (Python's "idna" codec) maps to a different domain than IDNA2008 (browsers) uses.
IDNA_DEVIATIONS = frozenset("ßς‌‍")
MAX_AGENT_TOKENS = 64

# Closed enums of the response contract (docs/api-contract.en.md; ahn/models.py is the machine form).
# A value outside them is dropped, never shown.
ACTIONS = frozenset({"proceed", "use_official_path", "hand_off_to_human", "wait_provider_incident",
                     "stop_declared_block", "unknown"})
CONFIDENCES = frozenset({"low", "medium", "high"})
ROBOTS_STATUSES = frozenset({"ok", "absent", "redirected", "refused", "invalid", "error", "too_large", "not_public",
                             "pending"})
LLMS_STATUSES = frozenset({"present", "absent", "redirected", "refused", "error", "not_public", "pending"})
INDICATORS = frozenset({"none", "minor", "major", "critical", "maintenance", "unknown"})
FRESHNESS = frozenset({"fresh", "stale", "pending"})
EVIDENCE_LEVELS = frozenset({"no_reports", "single_observation", "multiple_observations", "later_success_observed",
                             "stale_reports"})
NEXT_CHECKS = frozenset({"locate_failure_stage", "before_target_response", "response_observed", "access_denial_observed",
                         "human_handoff", "account_handoff", "robots_fetch_refused", "robots_unknown",
                         "robots_blocks_agents", "robots_allows_agents", "robots_wildcard_disallow",
                         "robots_grouping_differs_by_parser", "profile_not_read",
                         "profile_pending", "llms_txt_present", "content_use_ai_input_declared_no",
                         "official_path_available", "provider_scope", "provider_components_only",
                         "provider_incident_stale", "provider_scope_partial", "provider_clear", "provider_unknown",
                         "government_registry_listed", "government_suffix_restricted", "government_list_version_missing",
                         "reports_unknown", "reports_stale", "reports_unverified"})
AGENT_KINDS = frozenset({"user_agent", "search_crawler", "training_crawler"})
AGENT_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9-]{1,31}")
# https, a hostname, an optional path of at most 80 characters; no userinfo, port, query or fragment.
OFFICIAL_URL = re.compile(r"https://([A-Za-z0-9.-]{1,253})(/[A-Za-z0-9._~:/+-]{0,79})?")
CLOSING = "Declared policy is not an access guarantee; bypassing site controls is not advised."


def hostname(value: str | None) -> str | None:
    """The bare lowercase hostname in a URL or host string, or None when there is none."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if "\\" in value:
        # Browsers read a backslash as a slash (https://evil.com\@good.com goes to evil.com); urlsplit does not (good.com).
        # With two readings possible, no host is looked up.
        return None
    try:
        parts = urllib.parse.urlsplit(value if "://" in value else "//" + value)
        host = parts.hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.rstrip(".").lower()
    if not host.isascii():
        if IDNA_DEVIATIONS.intersection(host):
            return None
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
    if len(host) > 253 or not all(HOST_LABEL.fullmatch(label) for label in host.split(".")):
        return None
    return host


def apex(host: str) -> str:
    """Drop a leading "www." label: AHN reads declared policy for apex names, and "www." is the same site."""
    labels = host.split(".")
    return ".".join(labels[1:]) if len(labels) >= 3 and labels[0] == "www" else host


def normalize_host(value: str | None) -> str | None:
    """The hostname AHN is asked about: hostname() without a leading "www."."""
    host = hostname(value)
    return apex(host) if host else None


def url_host(value) -> str | None:
    """Hostname of an http(s) URL or of a scheme-less host[:port][/path] tool argument; None for other schemes."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    scheme = FULL_URL.match(value)
    if scheme:
        if scheme.group(1).lower() not in ("http", "https"):
            return None
    elif not SCHEMELESS.fullmatch(value):
        return None
    return hostname(value)


def special_use(host: str) -> bool:
    """True for single-label, special-use, private-network, cluster-internal and address-embedding names and for address literals."""
    labels = host.split(".")
    if len(labels) < 2:
        return True
    last = labels[-1]
    # Public TLDs are alphabetic or IDN; an all-digit or hex last label is an
    # address literal in inet_aton form (10.0.0.1, 127.1, 0x7f.1).
    if last in SKIP_TLDS or not (last.isalpha() or last.startswith("xn--")):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if EMBEDDED_IPV4.search(host):
        return True
    return any(host == suffix or host.endswith("." + suffix) for suffix in ADDRESS_NAME_SUFFIXES)


def skip_host(host: str, extra: str = "") -> bool:
    """True for special-use and address-embedding names, AHN's own hosts, and AHN_HOOK_SKIP_HOSTS entries and their subdomains.

    Both the host as given and its "www."-less form are compared, against each entry as given and
    normalised the same way, so "www.secret.example" in the list also covers "secret.example".
    """
    if special_use(host):
        return True
    names = {host, apex(host)}
    suffixes = {OWN_HOST}
    for item in extra.split(","):
        item = item.strip().lstrip("*.")
        if not item:
            continue
        entry = hostname(item) or item.lower().rstrip(".")
        suffixes.update({entry, apex(entry)})
    return any(name == suffix or name.endswith("." + suffix) for name in names for suffix in suffixes)


def host_from_text(text: str, refusal: bool = True) -> str | None:
    """The hostname of the first http(s) URL in an error text, else (when refusal) the host in Claude Code's refusal."""
    for match in URL_IN_TEXT.finditer(text):
        host = hostname(match.group(0).rstrip(".,;:!?"))
        if host:
            return host
    if refusal:
        match = REFUSAL.search(text)
        if match:
            return hostname(match.group(1))
    return None


def without_urls(text: str, host: str | None = None) -> str:
    """Blank each URL and every token naming host or one of its subdomains, so neither a path, a query nor the
    site's own name (developers.cloudflare.com, login.example.com) shapes the classification."""
    text = URL_IN_TEXT.sub(" ", text)
    if not host:
        return text
    host = host.lower()

    def blank(match) -> str:
        token = match.group(0).lower().rstrip(".")
        return " " if token == host or token.endswith("." + host) else match.group(0)

    return HOST_LIKE.sub(blank, text)


def classify(text: str, host: str | None = None) -> tuple[str | None, str | None]:
    """(error_class, failure_stage) for an error text; (None, None) when it names no block and no outage.

    There is no default class: text that matches nothing (404, 422, "Not Found", an unknown error) is not
    looked up. Nothing received from the site (DNS, refused connection, Claude Code's own refusal) is
    site_access unless the text says timeout or login.
    """
    text = without_urls(text or "", host)
    error_class = next((name for name, pattern in CLASSES if pattern.search(text)), None)
    stage = "before_target_response" if BEFORE_RESPONSE.search(text) else None
    if error_class is None and stage:
        error_class = "site_access"
    return error_class, stage


# Fields of WebFetch's tool_response that can carry page text, and how much of it is read for a cue.
BODY_FIELDS = ("result", "body", "content", "text", "message")
BODY_SCAN_CHARS = 4000
RETRY_AFTER_TEXT = re.compile(r"retry[- ]after\W{0,4}(\d{1,6})\b", re.I)
RETRY_AFTER_VALUE = re.compile(r"\d{1,6}")
MAX_RETRY_SECONDS = 86_400


def http_status(tool_response) -> int | None:
    """WebFetch reports an HTTP error as a successful call with code/codeText; return the code when >= 400."""
    if not isinstance(tool_response, dict):
        return None
    for key in ("code", "status", "statusCode"):
        code = tool_response.get(key)
        if isinstance(code, int) and not isinstance(code, bool) and 400 <= code <= 599:
            return code
    return None


def challenge_cue(tool_response: dict, host: str) -> bool:
    """True when a response header or the page text says the refusal is a bot challenge or CAPTCHA."""
    headers = tool_response.get("headers")
    # Cloudflare's documented marker of a challenge response.
    if isinstance(headers, dict) and challenge_code(headers) == "header":
        return True
    for field in BODY_FIELDS:
        value = tool_response.get(field)
        if isinstance(value, str) and HUMAN_VERIFICATION.search(without_urls(value[:BODY_SCAN_CHARS], host)):
            return True
    return False


def status_class(code: int, tool_response: dict, host: str) -> str | None:
    """error_class for an HTTP status, or None when the status is neither a block nor an outage.

    Only these statuses are looked up (RFC 9110 sections 15.5 and 15.6, RFC 6585, RFC 7725):
      401, 407  credentials required                    -> login_auth
      402       payment required (RFC 9110 15.5.3)     -> site_access: a crawl-for-payment answer such as
                                                           Cloudflare Pay per Crawl is the site's declared
                                                           access policy, which a lookup can report
                                                           https://developers.cloudflare.com/ai-crawl-control/features/pay-per-crawl/what-is-pay-per-crawl/
      403, 451  refused / unavailable for legal reasons -> site_access, or human_verification when a header
                                                           or the page text points to a challenge
      408       request timeout (RFC 9110 15.5.9)       -> service_error
      429       too many requests (RFC 6585)            -> service_error (the contract has no rate_limited class)
      5xx       the server failed                       -> service_error
    Every other 4xx (400, 404, 405, 409, 410, 411, 413, 414, 415, 422 ...) says the address or the request
    was wrong, not that access is blocked. It gets no lookup: calling it a block would send the agent to an
    official API because of a typo.
    """
    if code in (401, 407):
        return "login_auth"
    if code in (403, 451):
        return "human_verification" if challenge_cue(tool_response, host) else "site_access"
    if code == 402:
        return "site_access"
    if code in (408, 429) or 500 <= code <= 599:
        return "service_error"
    return None


def retry_note(payload: dict, host: str | None = None) -> str:
    """A short clause when a 429 or 503 came with a Retry-After of whole seconds, else "". Only that integer is kept."""
    seconds, code = None, 429
    if payload.get("hook_event_name") == "PostToolUse":
        response = payload.get("tool_response")
        code = http_status(response)
        if code in (429, 503) and isinstance(response.get("headers"), dict):
            for key, value in response["headers"].items():
                if isinstance(key, str) and key.lower() == "retry-after" and RETRY_AFTER_VALUE.fullmatch(str(value).strip()):
                    seconds = int(str(value).strip())
    else:
        text = payload.get("error")
        # URLs and the site's own name are blanked first: a number in a path or query is not a status or a number of seconds.
        text = without_urls(text, host) if isinstance(text, str) else None
        if text and re.search(r"\b(?:429|503)\b|too many requests|service unavailable", text, re.I):
            code = 503 if re.search(r"\b503\b|service unavailable", text, re.I) else 429
            match = RETRY_AFTER_TEXT.search(text)
            seconds = int(match.group(1)) if match else None
    if seconds is None or not 1 <= seconds <= MAX_RETRY_SECONDS:
        return ""
    return f"HTTP {code}, the site asks to retry after {seconds} s."


# Lookup signals: enum codes taken from what the tool result already holds. They describe the site's answer, never the
# request: no path, query, body text or header value is sent. ahn-client has the same lists (_signals.py); a test
# runs both and fails when they differ.
SIGNAL_STATUSES = frozenset({401, 402, 403, 407, 408, 429, 451}) | frozenset(range(500, 600))
CHALLENGES = ("header", "none")
RETRY_AFTER_BANDS = ("none", "lt_60", "60_3600", "gt_3600")
VIAS = ("http_fetch", "browser", "unknown")
RETRY_AFTER_SECONDS = re.compile(r"[0-9]{1,10}")
BROWSER_TOOL = re.compile(r"browser|chrome|playwright|puppeteer|selenium|webdriver|devtools|firefox|camoufox|stagehand", re.I)
FETCH_TOOL = re.compile(r"fetch", re.I)


def _header_text(value):
    """A header value as text: str, utf-8 bytes or a whole number; None for anything else (never str() of an object)."""
    try:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        if isinstance(value, str):
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    except Exception:
        pass
    return None


def header_entries(headers):
    """(lowercase name, readable texts, any unreadable value) per header. A list value is read element by element,
    bytes names and values are decoded; an entry whose name cannot be read is skipped."""
    entries = []
    for key, value in list(headers.items())[:200]:
        if isinstance(key, bytes):
            try:
                key = key.decode("ascii")
            except UnicodeError:
                continue
        if not isinstance(key, str):
            continue
        items = list(value) if isinstance(value, (list, tuple)) else [value]
        texts = [_header_text(item) for item in items]
        entries.append((key.lower(), [text for text in texts if text is not None], any(text is None for text in texts)))
    return entries


def challenge_code(headers):
    """"header" when a cf-mitigated header (Cloudflare's documented challenge marker) contains "challenge", "none" when
    headers were read without it, None when there is nothing to go by (no headers, or the marker itself unreadable)."""
    entries = header_entries(headers)
    if not entries:
        return None
    unreadable = False
    for name, texts, bad in entries:
        if name == "cf-mitigated":
            if any("challenge" in text.lower() for text in texts):
                return "header"
            unreadable = unreadable or bad
    return None if unreadable else "none"


def retry_after_code(headers):
    """none (no header, an HTTP date or anything else), lt_60, 60_3600 or gt_3600 for a Retry-After of whole seconds;
    None when there is nothing to go by (no headers, or the Retry-After value unreadable)."""
    entries = header_entries(headers)
    if not entries:
        return None
    for name, texts, bad in entries:
        if name == "retry-after":
            if not texts:
                return None
            text = texts[0].strip()
            if not RETRY_AFTER_SECONDS.fullmatch(text):
                return "none"
            seconds = int(text)
            return "lt_60" if seconds < 60 else "60_3600" if seconds <= 3600 else "gt_3600"
    return "none"


def via_for(tool: str) -> str | None:
    """http_fetch for WebFetch and for an MCP server named like a fetch server, browser for one named like a browser
    server, None otherwise. Like the hook matcher, only the server part of mcp__<server>__<tool> is read: a tool called
    fetch_issue on a GitHub server is not a web fetch."""
    if tool == "WebFetch":
        return "http_fetch"
    if tool.startswith("mcp__"):
        body = tool[5:]
        server = body.rsplit("__", 1)[0] if "__" in body else body
        if BROWSER_TOOL.search(server):
            return "browser"
        if FETCH_TOOL.search(server):
            return "http_fetch"
    return None


def signals(payload: dict) -> dict:
    """_signals(payload), or {} if reading it fails: a code that cannot be read must never cost the lookup."""
    try:
        return _signals(payload)
    except Exception:
        return {}


def _signals(payload: dict) -> dict:
    """The optional lookup codes this payload already holds, as {parameter: value}; empty when it holds none.

    http_status: the code of a PostToolUse result (never one read out of an error text).
    challenge, retry_after: only when the result carries readable headers (an empty headers object is "not seen", not
    "no header"); "none" then means the header was not there. retry_after: only for 429 and 503. via: from the tool's name.
    """
    found = {}
    if not isinstance(payload, dict):
        return found
    tool = payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else ""
    if payload.get("hook_event_name") == "PostToolUse":
        response = payload.get("tool_response")
        code = http_status(response)
        if code in SIGNAL_STATUSES:
            found["http_status"] = code
            headers = response.get("headers")
            if isinstance(headers, dict):
                challenge = challenge_code(headers)
                if challenge:
                    found["challenge"] = challenge
                if code in (429, 503):
                    band = retry_after_code(headers)
                    if band:
                        found["retry_after"] = band
    via = via_for(tool)
    if via:
        found["via"] = via
    return found


def failure(payload: dict) -> tuple[str, str, str | None] | None:
    """(hostname, error_class, failure_stage) for a blocked or failing web step, or None. The hostname keeps any "www."."""
    if not isinstance(payload, dict) or payload.get("tool_name") == "WebSearch":
        return None
    tool = payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else ""
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    event = payload.get("hook_event_name")
    if event == "PostToolUseFailure":
        if payload.get("is_interrupt"):
            return None
        text = payload.get("error") if isinstance(payload.get("error"), str) else ""
        mcp = tool.startswith("mcp__")
        # An MCP failure is looked up only when its input or its error names an http(s) URL.
        candidates = (url_host(tool_input.get(field)) for field in (URL_FIELDS if mcp else ("url",)))
        host = next((name for name in candidates if name), None) or host_from_text(text, refusal=not mcp)
        if not host:
            return None
        error_class, stage = classify(text, host)
        return (host, error_class, stage) if error_class else None
    if event == "PostToolUse":
        code = http_status(payload.get("tool_response"))
        host = url_host(tool_input.get("url"))
        if not code or not host:
            return None
        error_class = status_class(code, payload["tool_response"], host)
        return (host, error_class, "response_observed") if error_class else None
    return None


def cache_dir(env=None, windows: bool | None = None) -> str | None:
    """$CLAUDE_PLUGIN_DATA, else a per-user directory: %LOCALAPPDATA%\\ahn on Windows, ~/.cache/ahn elsewhere.

    None when no absolute directory is known; a relative one would land in the project being worked on.
    """
    env = os.environ if env is None else env
    windows = os.name == "nt" if windows is None else windows
    directory = env.get("CLAUDE_PLUGIN_DATA") or ""
    if not os.path.isabs(directory):
        home = os.path.expanduser("~")
        if windows:
            base = env.get("LOCALAPPDATA") or ""
            base = base if os.path.isabs(base) else os.path.join(home, "AppData", "Local")
            directory = os.path.join(base, "ahn")
        else:
            directory = os.path.join(home, ".cache", "ahn")
    return directory if os.path.isabs(directory) else None


def cache_path(env=None, windows: bool | None = None) -> str | None:
    directory = cache_dir(env, windows)
    return os.path.join(directory, CACHE_FILE) if directory else None


def recently_seen(host: str, now: float | None = None, env=None) -> bool:
    """True when host was looked up within the TTL; otherwise records it. Cache errors never stop a lookup."""
    now = time.time() if now is None else now
    path = cache_path(env)
    if not path:
        return False
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read(CACHE_MAX_BYTES + 1)
        entries = json.loads(text) if len(text) <= CACHE_MAX_BYTES else {}
        if not isinstance(entries, dict):
            entries = {}
    except (OSError, ValueError, RecursionError):
        entries = {}
    # The magnitude bound keeps now - stamp from overflowing on a hand-written huge integer.
    entries = {key: stamp for key, stamp in entries.items()
               if isinstance(key, str) and isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
               and -1e12 < stamp < 1e12 and 0 <= now - stamp < CACHE_TTL_SECONDS}
    if host in entries:
        return True
    entries[host] = now
    if len(entries) > CACHE_MAX_ENTRIES:
        entries = dict(sorted(entries.items(), key=lambda item: item[1])[-CACHE_MAX_ENTRIES:])
    directory = os.path.dirname(path)
    temporary = None
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        # mkstemp: an unpredictable name, created 0600, in the user's own directory.
        handle, temporary = tempfile.mkstemp(prefix=".ahn-hook-cache.", suffix=".tmp", dir=directory)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(entries, stream)
        os.replace(temporary, path)
        temporary = None
    except (OSError, ValueError, TypeError):
        pass
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return False


def valid_signals(found: dict | None) -> dict:
    """found without any parameter or value outside the closed lists, in the order they are sent."""
    found = found if isinstance(found, dict) else {}
    allowed = (("http_status", lambda value: isinstance(value, int) and not isinstance(value, bool) and value in SIGNAL_STATUSES),
               ("challenge", lambda value: value in CHALLENGES),
               ("retry_after", lambda value: value in RETRY_AFTER_BANDS),
               ("via", lambda value: value in VIAS))
    return {name: found[name] for name, ok in allowed if name in found and ok(found[name])}


def lookup_url(host: str, error_class: str, stage: str | None, extra: dict | None = None) -> str:
    query = {"site_host": host, "error_class": error_class}
    if stage:
        query["failure_stage"] = stage
    query.update(valid_signals(extra))
    return API_URL + "?" + urllib.parse.urlencode(query)


class RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Every 3xx is an error: the answer must come from API_URL itself, never another URL or plain HTTP."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def build_opener() -> urllib.request.OpenerDirector:
    """An opener that verifies TLS certificates and hostnames and follows no redirect."""
    if ssl is None:
        raise RuntimeError("this Python has no ssl module")
    return urllib.request.build_opener(RefuseRedirects(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def urlopen(request, timeout):
    return build_opener().open(request, timeout=timeout)


def fetch(url: str) -> dict:
    fake = os.environ.get("AHN_HOOK_FAKE_RESPONSE")
    if fake:
        with open(fake, encoding="utf-8") as handle:
            return json.load(handle)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=SOCKET_TIMEOUT_SECONDS) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("response over the size limit")
    return json.loads(body.decode("utf-8"))


def fetch_within(url: str, seconds: float | None = None) -> dict:
    """fetch(url) in a daemon thread; TimeoutError when it has not finished in time, DNS included."""
    seconds = TOTAL_TIMEOUT_SECONDS if seconds is None else seconds
    result = {}

    def work():
        try:
            result["data"] = fetch(url)
        except BaseException as exc:  # Handed to the caller below.
            result["error"] = exc

    worker = threading.Thread(target=work, name=WORKER_NAME, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise TimeoutError("no answer in time")
    if "error" in result:
        raise result["error"]
    return result["data"]


def _enum(value, allowed: frozenset, default: str | None = "unknown") -> str | None:
    return value if isinstance(value, str) and value in allowed else default


def retry_after(profile: dict) -> int | None:
    """Seconds to wait before one more lookup while the host's files are being read, or None."""
    value = profile.get("retry_after_seconds")
    if profile.get("freshness") != "pending" or isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3600:
        return None
    return value


def official_url(value) -> str | None:
    """The URL rebuilt from its validated parts, or None."""
    match = OFFICIAL_URL.fullmatch(value) if isinstance(value, str) else None
    if not match:
        return None
    host = match.group(1).lower()
    if not all(HOST_LABEL.fullmatch(label) for label in host.split(".")) or special_use(host):
        return None
    return "https://" + host + (match.group(2) or "")


def blocked_agents(robots: dict) -> tuple[list[str], int]:
    """(declared-block agent tokens, number of training-crawler tokens) from robots.agents_blocked.

    Training-crawler blocks are not declared blocks for user-directed agents (contract: Recommendation),
    so they are counted separately when robots.rules gives each token's kind.
    """
    rules = robots.get("rules") if isinstance(robots.get("rules"), list) else []
    kinds = {rule.get("agent"): _enum(rule.get("kind"), AGENT_KINDS, None)
             for rule in rules if isinstance(rule, dict) and isinstance(rule.get("agent"), str)}
    listed = robots.get("agents_blocked") if isinstance(robots.get("agents_blocked"), list) else []
    tokens, seen = [], set()
    for item in listed:
        if len(tokens) >= MAX_AGENT_TOKENS:
            break
        if isinstance(item, str) and item not in seen and AGENT_TOKEN.fullmatch(item):
            seen.add(item)
            tokens.append(item)
    declared = [token for token in tokens if kinds.get(token) != "training_crawler"]
    return declared, len(tokens) - len(declared)


def summary(host: str, data: dict, url: str, note: str = "") -> str:
    """The additionalContext line, at most MAX_CONTEXT characters, built only from contract enums and validated values."""
    if not isinstance(data, dict):
        raise ValueError("unexpected response")
    rec = data.get("recommendation") if isinstance(data.get("recommendation"), dict) else {}
    profile = data.get("access_profile") if isinstance(data.get("access_profile"), dict) else None
    official = data.get("official_paths") if isinstance(data.get("official_paths"), dict) else {}
    provider = data.get("provider_status") if isinstance(data.get("provider_status"), dict) else None
    checks = data.get("next_checks") if isinstance(data.get("next_checks"), list) else []

    blocked, training = None, 0
    if profile:
        robots_status = _enum(profile.get("robots_status"), ROBOTS_STATUSES)
        policy = robots_status + (" (stale)" if profile.get("freshness") == "stale" else "")
        wait = retry_after(profile)
        if wait is not None:
            # The service answers at once and reads robots.txt/llms.txt in the background.
            policy += f" (being read now; look up once more in {wait} s, do not poll)"
        llms = _enum(profile.get("llms_txt_status"), LLMS_STATUSES)
        # Agent lists mean something only when robots.txt was actually read.
        if robots_status == "ok":
            blocked, training = blocked_agents(profile.get("robots") if isinstance(profile.get("robots"), dict) else {})
    else:
        policy, llms = "not read for this hostname", "unknown"
    path = official_url(official.get("api_docs")) or official_url(official.get("developer_portal"))
    path_listed = bool(official.get("api_docs") or official.get("developer_portal"))
    if provider:
        status = f"{_enum(provider.get('indicator'), INDICATORS)}/{_enum(provider.get('freshness'), FRESHNESS)}"
    else:
        status = "not connected (official page listed)" if isinstance(data.get("official_status_page"), dict) else "not connected"
    codes = []
    for item in checks:
        code = _enum(item.get("code"), NEXT_CHECKS, None) if isinstance(item, dict) else None
        if code and code not in codes:
            codes.append(code)
        if len(codes) == 2:
            break

    def render(agent_limit: int | None, show_path: bool, show_url: bool) -> str:
        if blocked is None:
            agents = "unknown"
        elif not blocked:
            agents = "none"
        elif agent_limit is None or len(blocked) <= agent_limit:
            agents = ", ".join(blocked)
        elif agent_limit == 0:
            agents = f"{len(blocked)} agent tokens"
        else:
            agents = ", ".join(blocked[:agent_limit]) + f" (+{len(blocked) - agent_limit} more)"
        if training:
            agents += f" (+{training} training crawler{'s' if training != 1 else ''})"
        official_text = path if path and show_path else "listed" if path_listed else "none"
        full = url if show_url else "GET /v1/incidents/check on agenthelpnetwork.com"
        return (f"AHN {host}: {note + ' ' if note else ''}recommendation={_enum(rec.get('action'), ACTIONS)} ({_enum(rec.get('confidence'), CONFIDENCES)}). "
                f"Declared policy: {policy}; blocked agents: {agents}; llms.txt: {llms}. "
                f"Official path: {official_text}. Provider status: {status}. "
                f"Reports: {_enum(data.get('evidence_level'), EVIDENCE_LEVELS)}. Next: {', '.join(codes) or 'none'}. "
                f"Full JSON: {full}. {CLOSING}")

    for options in ((6, True, True), (3, True, True), (0, True, True), (0, False, True), (0, False, False)):
        text = render(*options)
        if len(text) <= MAX_CONTEXT:
            return text
    tail = " " + CLOSING
    return text[:MAX_CONTEXT - len(tail) - 1].rstrip() + "…" + tail


def run(raw: bytes, env=os.environ) -> tuple[dict | None, str]:
    """Return (hook output or None, debug note). Raises nothing the caller must handle besides bugs."""
    if env.get("AHN_HOOK_DISABLE") == "1":
        return None, "disabled"
    payload = json.loads(raw.decode("utf-8", errors="replace"))
    found = failure(payload)
    if not found:
        return None, "no failed web step with a hostname"
    name, error_class, stage = found
    if skip_host(name, env.get("AHN_HOOK_SKIP_HOSTS", "")):
        return None, "skipped host"
    host = apex(name)
    if recently_seen(host, env=env):
        return None, f"{host} looked up within 10 minutes"
    url = lookup_url(host, error_class, stage)
    # The signals ride on the request only. The "Full JSON" link in the line stays without them, so following it counts nothing twice.
    text = summary(host, fetch_within(lookup_url(host, error_class, stage, signals(payload))), url, retry_note(payload, name) if error_class == "service_error" else "")
    event = payload.get("hook_event_name")
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}, f"{host} {error_class}"


def main() -> int:
    note = ""
    try:
        output, note = run(sys.stdin.buffer.read(MAX_STDIN_BYTES))
        if output:
            sys.stdout.write(json.dumps(output, ensure_ascii=True))
            sys.stdout.flush()
    except BaseException as exc:  # The hook must never fail or block the tool call.
        note = f"no output ({type(exc).__name__})"
    if os.environ.get("AHN_HOOK_DEBUG") == "1" and note:
        try:
            sys.stderr.write(f"ahn-hook: {note}\n")
        except Exception:
            pass
    return 0


def entry() -> int:
    """main(), then leave at once: a lookup still stuck in DNS or connect must not keep the process alive."""
    status = main()
    if any(thread.name == WORKER_NAME and thread.is_alive() for thread in threading.enumerate()):
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(status)
    return status


if __name__ == "__main__":
    sys.exit(entry())
