"""Optional lookup codes that describe the site's answer: http_status, challenge, retry_after, via.

Each is a value from a closed list, taken from what the caller already holds (an exception's status and
headers, a Claude Code hook payload). They are counted per hostname and hour next to ``error_class`` and
``failure_stage``; they never become a report. No path, query, body text or header value is ever read out
for the request: a header is only tested for a marker or a number and reduced to one of the values below.

The lists and the rules for ``challenge`` (Cloudflare's ``cf-mitigated`` and AWS WAF's ``x-amzn-waf-action``) and ``retry_after`` are those of plugins/claude-code/ahn/hooks/
ahn_lookup.py; tests/test_ahn_client_adapters.py runs both on the same input and fails when they differ.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

HTTP_STATUSES = frozenset({401, 402, 403, 407, 408, 429, 451}) | frozenset(range(500, 600))
CHALLENGES = ("header", "none")
RETRY_AFTER_BANDS = ("none", "lt_60", "60_3600", "gt_3600")
VIAS = ("http_fetch", "browser", "unknown")
SIGNAL_NAMES = ("http_status", "challenge", "retry_after", "via")
# The vendors of the service's ``agent_vendor`` parameter (docs/api-contract.en.md, "Agent vendor"). Not a signal code: the
# service neither counts nor stores it; it only chooses the robots part of the answer. tests/test_ahn_client.py compares
# this list with the service's own (the values of ahn.models.AgentVendor).
AGENT_VENDORS = ("openai", "anthropic", "google", "perplexity", "meta", "mistral", "amazon", "microsoft", "apple",
                 "duckduckgo", "diffbot", "exa", "other")
# AWS WAF marks the answer to a request that lacks a valid token with ``x-amzn-waf-action``: ``challenge`` on a 202 (Challenge
# action) and ``captcha`` on a 405 (CAPTCHA action); a 403 or 451 carrying either value is read the same way. Any other
# status (a 429 or 503 included) with the header is not a challenge.
# https://docs.aws.amazon.com/waf/latest/developerguide/waf-captcha-and-challenge-actions.html
AWS_WAF_HEADER = "x-amzn-waf-action"
AWS_WAF_CHALLENGE_VALUES = {202: ("challenge",), 405: ("captcha",), 403: ("challenge", "captcha"), 451: ("challenge", "captcha")}
RETRY_AFTER_SECONDS = re.compile(r"[0-9]{1,10}")
MAX_HEADERS = 200
# The top-level module of an exception class says which kind of client raised it.
HTTP_MODULES = frozenset({"httpx", "httpcore", "requests", "urllib3", "aiohttp", "urllib", "http", "niquests", "curl_cffi"})
BROWSER_MODULES = frozenset({"playwright", "patchright", "selenium", "pyppeteer", "browser_use", "zendriver", "nodriver",
                             "undetected_chromedriver", "splinter"})


def valid_signals(found: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """``found`` without any name or value outside the closed lists, in the order they are sent."""
    found = found if isinstance(found, Mapping) else {}
    checks = (("http_status", lambda value: isinstance(value, int) and not isinstance(value, bool) and value in HTTP_STATUSES),
              ("challenge", lambda value: isinstance(value, str) and value in CHALLENGES),
              ("retry_after", lambda value: isinstance(value, str) and value in RETRY_AFTER_BANDS),
              ("via", lambda value: isinstance(value, str) and value in VIAS))
    return {name: found[name] for name, ok in checks if name in found and ok(found[name])}


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


def header_entries(headers: Mapping[Any, Any]) -> List[Tuple[str, List[str], bool]]:
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


def aws_waf_challenge(status: Optional[int], entries: List[Tuple[str, List[str], bool]]) -> bool:
    """True for an AWS WAF challenge answer: ``x-amzn-waf-action`` is ``challenge`` on a 202, ``captcha`` on a 405, either on
    a 403 or 451. The same header on any other status is not a challenge."""
    wanted = AWS_WAF_CHALLENGE_VALUES.get(status) if isinstance(status, int) and not isinstance(status, bool) else None
    return bool(wanted) and any(name == AWS_WAF_HEADER and any(text.strip().lower() in wanted for text in texts)
                                for name, texts, _ in entries)


def challenge_code(headers: Mapping[Any, Any], status: Optional[int] = None) -> Optional[str]:
    """"header" when a cf-mitigated header (Cloudflare's documented challenge marker) contains "challenge" or, given the
    response ``status``, when AWS WAF's ``x-amzn-waf-action`` marks a challenge (see ``aws_waf_challenge``); "none" when
    headers were read without a marker, None when there is nothing to go by (no headers, or the marker itself unreadable)."""
    entries = header_entries(headers)
    if not entries:
        return None
    if aws_waf_challenge(status, entries):
        return "header"
    unreadable = False
    for name, texts, bad in entries:
        if name == "cf-mitigated":
            if any("challenge" in text.lower() for text in texts):
                return "header"
            unreadable = unreadable or bad
    return None if unreadable else "none"


def retry_after_code(headers: Mapping[Any, Any]) -> Optional[str]:
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


def header_pairs(holder: Any) -> Optional[Dict[Any, Any]]:
    """The headers of a response-like object as a dict, or None when it has none we can read."""
    try:
        items = getattr(getattr(holder, "headers", None), "items", None)
        if not callable(items):
            return None
        pairs: Iterable[Any] = list(items())[:MAX_HEADERS]
        return {key: value for key, value in pairs}
    except Exception:
        return None


def from_headers(status: Optional[int], headers: Optional[Mapping[Any, Any]]) -> Dict[str, Any]:
    """The codes for a status and, when there is a headers object, for its headers (``retry_after`` only for 429 and 503)."""
    found: Dict[str, Any] = {}
    if status not in HTTP_STATUSES:
        return found
    found["http_status"] = status
    if headers is not None:
        challenge = challenge_code(headers, status)
        if challenge:
            found["challenge"] = challenge
        if status in (429, 503):
            band = retry_after_code(headers)
            if band:
                found["retry_after"] = band
    return found


def via_for_exception(chain: Iterable[BaseException]) -> Optional[str]:
    """http_fetch for the common HTTP libraries' errors, browser for browser-automation errors, None for anything else."""
    for item in chain:
        root = (getattr(type(item), "__module__", "") or "").split(".")[0]
        if root in BROWSER_MODULES:
            return "browser"
        if root in HTTP_MODULES:
            return "http_fetch"
    return None
