"""Map an exception or an error message to the lookup's error_class and failure_stage.

The text rules are those of plugins/claude-code/ahn/hooks/ahn_lookup.py. On top of them
this module reads exception class names (as strings, so Playwright, httpx, requests and
aiohttp are never imported) and an HTTP status carried on the exception.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from ._hosts import normalize_host

URL_IN_TEXT = re.compile(r"https?://[^\s'\"<>()\[\]{}`]+", re.IGNORECASE)
CLASSES = (
    ("human_verification", re.compile(r"captcha|verify (?:that )?you are (?:a )?human|are you a robot|challenge|cloudflare", re.I)),
    ("login_auth", re.compile(r"log[ -]?in\b|sign[ -]?in\b|unauthori[sz]ed|\b401\b|\bauthenticat(?:e|ion)\b", re.I)),
    ("service_error", re.compile(r"\b5\d\d\b|time ?out|timed ?out|unavailable|reset|bad gateway", re.I)),
)
BEFORE_RESPONSE = re.compile(r"connection refused|could not resolve|timed ?out|ENOTFOUND|ECONNREFUSED|getaddrinfo"
                             r"|is unable to fetch from"
                             # Chromium's net:: errors, as Playwright and Puppeteer report them.
                             r"|ERR_NAME_NOT_RESOLVED|ERR_CONNECTION_(?:REFUSED|TIMED_OUT)|ERR_INTERNET_DISCONNECTED",
                             re.I)
# Exception class names that say no connection to the target was made.
CONNECT_NAMES = frozenset({"connecterror", "connecttimeout", "connecttimeouterror", "connectionerror", "connectionrefusederror",
                           "newconnectionerror", "nameresolutionerror", "gaierror", "dnserror", "clientconnectorerror",
                           "clientconnectorcertificateerror", "clientconnectordnserror"})

ERROR_CLASSES = ("site_access", "login_auth", "human_verification", "service_error")
FAILURE_STAGES = ("before_target_response", "response_observed", "access_denial_observed", "unknown")


def without_urls(text: str) -> str:
    """Replace each URL by its hostname so paths and queries never shape the classification."""
    return URL_IN_TEXT.sub(lambda match: normalize_host(match.group(0)) or "", text)


def classify_text(text: str) -> Tuple[str, Optional[str]]:
    text = without_urls(text or "")
    error_class = next((name for name, pattern in CLASSES if pattern.search(text)), "site_access")
    stage = "before_target_response" if BEFORE_RESPONSE.search(text) else None
    return error_class, stage


def _status(exc: BaseException) -> Optional[int]:
    """An HTTP error status (400-599) carried by the exception: httpx/requests .response, aiohttp .status."""
    response = getattr(exc, "response", None)
    for holder, names in ((exc, ("status_code", "status")), (response, ("status_code", "status"))):
        if holder is None:
            continue
        for name in names:
            code = getattr(holder, name, None)
            if isinstance(code, int) and not isinstance(code, bool) and 400 <= code <= 599:
                return code
    return None


def _chain(exc: BaseException):
    seen = []
    while exc is not None and exc not in seen and len(seen) < 3:
        seen.append(exc)
        exc = exc.__cause__ or exc.__context__
    return seen


def classify(exc_or_text) -> Tuple[str, Optional[str]]:
    """``(error_class, failure_stage)`` for an exception object or a message.

    error_class is one of human_verification, login_auth, service_error, site_access;
    failure_stage is before_target_response, response_observed or None. Only the
    exception's class names, its message and its HTTP status are read. URLs in messages
    are reduced to their hostnames, so a path or query never influences the answer.
    """
    if isinstance(exc_or_text, BaseException):
        chain = _chain(exc_or_text)
        names = [type(item).__name__ for item in chain]
        status = _status(exc_or_text)
        text = " ".join(names + [str(item) for item in chain])
        if status:
            text += f" HTTP {status}"
        error_class, stage = classify_text(text)
        if stage is None:
            if any(name.lower() in CONNECT_NAMES for name in names):
                stage = "before_target_response"
            elif status:
                stage = "response_observed"
        return error_class, stage
    if isinstance(exc_or_text, bytes):
        exc_or_text = exc_or_text.decode("utf-8", errors="replace")
    return classify_text(exc_or_text if isinstance(exc_or_text, str) else "")
