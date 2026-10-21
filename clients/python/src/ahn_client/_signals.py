"""Optional lookup codes that describe the site's answer: http_status, challenge, retry_after, via.

Each is a value from a closed list, taken from what the caller already holds (an exception's status and
headers, a Claude Code hook payload). They are counted per hostname and hour next to ``error_class`` and
``failure_stage``; they never become a report. No path, query, body text or header value is ever read out
for the request: a header is only tested for a marker or a number and reduced to one of the values below.

The lists and the rules for ``challenge`` and ``retry_after`` are those of plugins/claude-code/ahn/hooks/
ahn_lookup.py; tests/test_ahn_client_adapters.py runs both on the same input and fails when they differ.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Mapping, Optional

HTTP_STATUSES = frozenset({401, 402, 403, 407, 408, 429, 451}) | frozenset(range(500, 600))
CHALLENGES = ("header", "none")
RETRY_AFTER_BANDS = ("none", "lt_60", "60_3600", "gt_3600")
VIAS = ("http_fetch", "browser", "unknown")
SIGNAL_NAMES = ("http_status", "challenge", "retry_after", "via")
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


def challenge_header(headers: Mapping[Any, Any]) -> bool:
    """True when a header says the answer is a bot challenge: Cloudflare's documented ``cf-mitigated: challenge``."""
    return any(isinstance(key, str) and key.lower() == "cf-mitigated" and isinstance(value, str) and "challenge" in value.lower()
               for key, value in headers.items())


def retry_after_band(headers: Mapping[Any, Any]) -> str:
    """none (no header, an HTTP date or anything else), lt_60, 60_3600 or gt_3600 for a Retry-After of whole seconds."""
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == "retry-after":
            value = str(value).strip()
            if not RETRY_AFTER_SECONDS.fullmatch(value):
                return "none"
            seconds = int(value)
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
        found["challenge"] = "header" if challenge_header(headers) else "none"
        if status in (429, 503):
            found["retry_after"] = retry_after_band(headers)
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
