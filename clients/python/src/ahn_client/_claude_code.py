"""Which Claude Code hook payloads are a blocked or failing web step, and for which hostname.

A copy of the decision logic in plugins/claude-code/ahn/hooks/ahn_lookup.py (``failure`` and what it
calls), so the Claude Agent SDK adapter reads a payload exactly as the plugin does. The plugin is a
script that cannot be imported from the wheel, hence the copy; tests/test_ahn_client_adapters.py runs
both on the same payloads and fails when they differ. Change the two together.

Only the hostname and an enum class come out of here. Nothing is sent from this module.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Dict, Optional, Tuple

from ._signals import HTTP_STATUSES, challenge_header, retry_after_band

HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
URL_IN_TEXT = re.compile(r"https?://[^\s'\"<>()\[\]{}`]+", re.IGNORECASE)
REFUSAL = re.compile(r"\bis unable to fetch from\s+(?:https?://)?"
                     r"((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62})(?![\w-]|\.\w)", re.IGNORECASE)
FULL_URL = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*)://")
SCHEMELESS = re.compile(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:[/?#].*)?", re.DOTALL)
URL_FIELDS = ("url", "uri", "href", "link", "page_url", "pageUrl", "target_url", "targetUrl")
HUMAN_VERIFICATION = re.compile(r"captcha|verify (?:that )?you are (?:a )?human|are you a robot|challenge"
                                r"|checking your browser|just a moment", re.I)
CLASSES = (
    ("human_verification", HUMAN_VERIFICATION),
    ("login_auth", re.compile(r"log[ -]?in\b|sign[ -]?in\b|unauthori[sz]ed|\b40[17]\b|\bauthenticat(?:e|ion)\b", re.I)),
    ("service_error", re.compile(r"\b5\d\d\b|\b408\b|\b429\b|too many requests|rate[ -]?limit|time ?out|timed[ _]?out"
                                 r"|unavailable(?! for legal reasons)|reset|bad gateway", re.I)),
    ("site_access", re.compile(r"\b(?:402|403|451)\b|payment required|forbidden|\bdenied\b|blocked|disallow|legal reasons", re.I)),
)
BEFORE_RESPONSE = re.compile(r"connection[ _]refused|could not resolve|timed[ _]?out|ENOTFOUND|ECONNREFUSED|getaddrinfo"
                             r"|is unable to fetch from|unable to verify if domain|is blocked by the network"
                             r"|ERR_NAME_NOT_RESOLVED|ERR_INTERNET_DISCONNECTED|ERR_ADDRESS_UNREACHABLE", re.I)
HOST_LIKE = re.compile(r"[A-Za-z0-9.-]+")
IDNA_DEVIATIONS = frozenset("ßς‌‍")
BODY_FIELDS = ("result", "body", "content", "text", "message")
BODY_SCAN_CHARS = 4000
BROWSER_TOOL = re.compile(r"browser|chrome|playwright|puppeteer|selenium|webdriver|devtools|firefox|camoufox|stagehand", re.I)
FETCH_TOOL = re.compile(r"fetch", re.I)


def hostname(value) -> Optional[str]:
    """The bare lowercase hostname in a URL or host string, or None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or "\\" in value:  # a backslash has two readings (browsers: a slash); look up no host
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


def url_host(value) -> Optional[str]:
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


def host_from_text(text: str, refusal: bool = True) -> Optional[str]:
    for match in URL_IN_TEXT.finditer(text):
        host = hostname(match.group(0).rstrip(".,;:!?"))
        if host:
            return host
    if refusal:
        match = REFUSAL.search(text)
        if match:
            return hostname(match.group(1))
    return None


def without_urls(text: str, host: Optional[str] = None) -> str:
    text = URL_IN_TEXT.sub(" ", text)
    if not host:
        return text
    host = host.lower()

    def blank(match) -> str:
        token = match.group(0).lower().rstrip(".")
        return " " if token == host or token.endswith("." + host) else match.group(0)

    return HOST_LIKE.sub(blank, text)


def classify(text: str, host: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    text = without_urls(text or "", host)
    error_class = next((name for name, pattern in CLASSES if pattern.search(text)), None)
    stage = "before_target_response" if BEFORE_RESPONSE.search(text) else None
    if error_class is None and stage:
        error_class = "site_access"
    return error_class, stage


def http_status(tool_response) -> Optional[int]:
    """WebFetch reports an HTTP error as a successful call with code/codeText; the code when >= 400."""
    if not isinstance(tool_response, dict):
        return None
    for key in ("code", "status", "statusCode"):
        code = tool_response.get(key)
        if isinstance(code, int) and not isinstance(code, bool) and 400 <= code <= 599:
            return code
    return None


def challenge_cue(tool_response: dict, host: str) -> bool:
    headers = tool_response.get("headers")
    if isinstance(headers, dict) and challenge_header(headers):
        return True
    for field in BODY_FIELDS:
        value = tool_response.get(field)
        if isinstance(value, str) and HUMAN_VERIFICATION.search(without_urls(value[:BODY_SCAN_CHARS], host)):
            return True
    return False


def status_class(code: int, tool_response: dict, host: str) -> Optional[str]:
    """error_class for an HTTP status; None for a status that is neither a block nor an outage (404, 422, ...)."""
    if code in (401, 407):
        return "login_auth"
    if code in (403, 451):
        return "human_verification" if challenge_cue(tool_response, host) else "site_access"
    if code == 402:
        return "site_access"
    if code in (408, 429) or 500 <= code <= 599:
        return "service_error"
    return None


def failure(payload) -> Optional[Tuple[str, str, Optional[str]]]:
    """``(hostname, error_class, failure_stage)`` for a blocked or failing web step, or None."""
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


def via_for(tool: str) -> Optional[str]:
    """http_fetch for WebFetch and MCP fetch tools, browser for MCP browser tools, None when the tool name says neither."""
    if tool == "WebFetch":
        return "http_fetch"
    if tool.startswith("mcp__"):
        if BROWSER_TOOL.search(tool):
            return "browser"
        if FETCH_TOOL.search(tool):
            return "http_fetch"
    return None


def signals(payload) -> Dict[str, Any]:
    """The optional lookup codes (http_status, challenge, retry_after, via) the payload already holds; empty when none.

    http_status comes only from a PostToolUse result, never from an error text. challenge and retry_after are
    set only when the result carries a headers object ("none" then means the header was not there); retry_after
    only for 429 and 503. via comes from the tool's name.
    """
    found: Dict[str, Any] = {}
    if not isinstance(payload, dict):
        return found
    tool = payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else ""
    if payload.get("hook_event_name") == "PostToolUse":
        response = payload.get("tool_response")
        code = http_status(response)
        if code in HTTP_STATUSES:
            found["http_status"] = code
            headers = response.get("headers")
            if isinstance(headers, dict):
                found["challenge"] = "header" if challenge_header(headers) else "none"
                if code in (429, 503):
                    found["retry_after"] = retry_after_band(headers)
    via = via_for(tool)
    if via:
        found["via"] = via
    return found
