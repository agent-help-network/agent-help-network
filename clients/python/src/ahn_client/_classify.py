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
# The first match wins. Text that matches none of these is neither a block nor an outage (a 404 page, a 422
# from a bad request, "something failed"), and there is no default class for it.
CLASSES = (
    ("human_verification", re.compile(r"captcha|verify (?:that )?you are (?:a )?human|are you a robot|challenge|cloudflare", re.I)),
    # 401 and 407: credentials required (RFC 9110 15.5.2, 15.5.8).
    ("login_auth", re.compile(r"log[ -]?in\b|sign[ -]?in\b|unauthori[sz]ed|\b40[17]\b|\bauthenticat(?:e|ion)\b", re.I)),
    # 5xx (RFC 9110 15.6), 408 (RFC 9110 15.5.9) and 429 (RFC 6585 4), for which the contract has no class of its
    # own. "Unavailable For Legal Reasons" is the 451 reason phrase (RFC 7725): a block, not an outage.
    ("service_error", re.compile(r"\b5\d\d\b|\b408\b|\b429\b|too many requests|rate[ -]?limit|time ?out|timed ?out"
                                 r"|unavailable(?! for legal reasons)|reset|bad gateway", re.I)),
    # 403 (RFC 9110 15.5.4), 451 (RFC 7725) and 402 (RFC 9110 15.5.3): a crawl-for-payment answer such as Cloudflare
    # Pay per Crawl is the site's declared access policy
    # (https://developers.cloudflare.com/ai-crawl-control/features/pay-per-crawl/what-is-pay-per-crawl/),
    # plus the usual wording of a refusal.
    ("site_access", re.compile(r"\b(?:402|403|451)\b|payment required|forbidden|\bdenied\b|blocked|disallow|legal reasons", re.I)),
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


HOST_LIKE = re.compile(r"[A-Za-z0-9.-]+")


def blank_host(text: str, host: Optional[str]) -> str:
    """Blank every token naming ``host`` or one of its subdomains, so the site's own name
    (login.example.com, developers.cloudflare.com) never shapes the classification."""
    if not host:
        return text
    host = host.lower()

    def blank(match) -> str:
        token = match.group(0).lower().rstrip(".")
        return " " if token == host or token.endswith("." + host) else match.group(0)

    return HOST_LIKE.sub(blank, text)


def classify_text(text: str, host: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    text = blank_host(without_urls(text or ""), host)
    error_class = next((name for name, pattern in CLASSES if pattern.search(text)), None)
    stage = "before_target_response" if BEFORE_RESPONSE.search(text) else None
    if error_class is None and stage:
        error_class = "site_access"
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


def classify(exc_or_text, host: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """``(error_class, failure_stage)`` for an exception object or a message.

    error_class is one of human_verification, login_auth, service_error, site_access;
    failure_stage is before_target_response, response_observed or None. Only the
    exception's class names, its message and its HTTP status are read. URLs in messages
    are reduced to their hostnames, so a path or query never influences the answer.

    ``(None, None)`` means the error is neither a block nor an outage: a 404, 410, 400 or
    422, or a message that names nothing. Do not look such an error up; a typo in an
    address is not a site that refuses agents. 401 and 407 give login_auth, 403 and 451
    site_access (402 too), 408, 429 and 5xx service_error (the contract has no rate-limit class).

    ``host``, when given, is the hostname the error is about: tokens naming it are ignored,
    so a site called ``login.example.com`` is not read as a login wall.
    """
    if isinstance(exc_or_text, BaseException):
        chain = _chain(exc_or_text)
        names = [type(item).__name__ for item in chain]
        status = _status(exc_or_text)
        text = " ".join(names + [str(item) for item in chain])
        if status:
            text += f" HTTP {status}"
        error_class, stage = classify_text(text, host)
        if stage is None and any(name.lower() in CONNECT_NAMES for name in names):
            stage = "before_target_response"
            error_class = error_class or "site_access"
        if error_class is None:
            return None, None
        if stage is None and status:
            stage = "response_observed"
        return error_class, stage
    if isinstance(exc_or_text, bytes):
        exc_or_text = exc_or_text.decode("utf-8", errors="replace")
    return classify_text(exc_or_text if isinstance(exc_or_text, str) else "", host)
