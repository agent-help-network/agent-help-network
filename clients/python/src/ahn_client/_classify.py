"""Map an exception or an error message to the lookup's error_class and failure_stage.

The text rules are those of plugins/claude-code/ahn/hooks/ahn_lookup.py. On top of them
this module reads exception class names (as strings, so Playwright, httpx, requests and
aiohttp are never imported) and an HTTP status carried on the exception.
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, Optional, Tuple

from ._hosts import normalize_host
from ._signals import from_headers, header_pairs, valid_signals, via_for_exception

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


def _attr(holder: Any, name: str) -> Any:
    """``getattr(holder, name, None)`` that also survives a property that raises."""
    try:
        return getattr(holder, name, None)
    except Exception:
        return None


def _status(exc: BaseException) -> Optional[int]:
    """An HTTP error status (400-599) carried by the exception: httpx/requests .response, aiohttp .status."""
    response = _attr(exc, "response")
    for holder, names in ((exc, ("status_code", "status")), (response, ("status_code", "status"))):
        if holder is None:
            continue
        for name in names:
            code = _attr(holder, name)
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


def signals(exc: Any, *, via: Optional[str] = None) -> Dict[str, Any]:
    """The optional lookup codes an exception already holds, as keyword arguments for ``check``.

    ``ahn.check(url, error_class, stage, **ahn.signals(exc))``. Possible keys: ``http_status`` (the HTTP status the
    exception carries, only 401, 402, 403, 407, 408, 429, 451 and 500-599), ``challenge`` (``header`` when the response
    has a ``cf-mitigated: challenge`` header, or, given its status, an AWS WAF ``x-amzn-waf-action`` that marks a challenge
    (``challenge`` or ``captcha`` on a 403 or 451, the only statuses here that can carry one; whole value, any case); ``none`` when it has headers without a marker), ``retry_after`` (for 429 and
    503: ``none``, ``lt_60``, ``60_3600`` or ``gt_3600`` seconds; an HTTP date counts as ``none``) and ``via``
    (``http_fetch`` for httpx, requests, aiohttp and urllib errors, ``browser`` for Playwright, Selenium and similar
    ones; pass ``via="browser"`` or ``"http_fetch"`` yourself when you know). A key whose value is not known is left
    out. ``http_status`` and the header codes are empty for text, for an exception without a status and for a status
    outside that list (``via`` can still be there, from the exception's module or from your argument). An empty
    headers object counts as not seen, so ``challenge`` and ``retry_after`` are left out. No header value, URL or
    message text is read into the result, and this function never raises (it gives ``{}`` instead).
    """
    try:
        found: Dict[str, Any] = {}
        if isinstance(exc, BaseException):
            headers = header_pairs(_attr(exc, "response"))
            found = from_headers(_status(exc), headers if headers is not None else header_pairs(exc))
            via = via or via_for_exception(_chain(exc))
        if via:
            found["via"] = via
        return valid_signals(found)
    except Exception:
        return {}


# --- classify_body: the text of a page that a fetch tool returned as an ordinary result ------------------------------------
# A page body is far noisier than an error message: documentation about HTTP, a login form with a CAPTCHA widget, a normal
# page of a site that runs bot protection or a blog post about it use the same words and often the same scripts. So the rules
# are conservative and read only the start of the body (BODY_HEAD characters), never the whole page:
#   * a challenge page of a known bot-protection vendor is recognised by a marker that only a challenge page carries
#     (VENDOR_MARKERS); that is enough alone. It is looked for in the markup only (tag names and attributes: ids, classes,
#     hrefs, script src; the <title> text too), never in script bodies or in the words of an article. Markers that a normal
#     page of a protected site also loads (Cloudflare's /cdn-cgi/challenge-platform and Turnstile scripts, DataDome's
#     tags.js, Imperva's _Incapsula_Resource, PerimeterX's px-cdn.net sensor) are not markers. The Akamai "Reference #"
#     number and Imperva's "Incapsula incident ID" have a distinctive form and are also read in the visible text (VENDOR_TEXT);
#   * otherwise the visible text must say so in words: a status line ("403 Forbidden", "Error 429"), a refusal ("Access
#     Denied") or a request to prove you are human ("verify you are human", "complete the CAPTCHA"). A short page (at most
#     BODY_HEAD characters in all, block pages are a few hundred) needs one such signal; any longer page needs two different
#     kinds, one of them in its <title> or at its start, so a long article that merely mentions "forbidden" or "403 Forbidden"
#     further down is not read as a block.
# The word "forbidden" or "blocked" alone, and the words "captcha" or "reCAPTCHA" alone ("protected by reCAPTCHA"), are never a signal.
BODY_HEAD = 4096  # characters of the body that are read; a longer body is never a "short page"
SHORT_PAGE = 800  # visible characters up to which one signal is enough
EARLY_TEXT = 200  # visible characters at the start of a page that count as its heading
VENDOR_MARKERS = re.compile(
    r"cf-chl-|__cf_chl|cf-browser-verification"  # Cloudflare
    r"|attention required! \| cloudflare"
    r"|captcha-delivery\.com"  # DataDome
    r"|px-captcha|_pxcaptcha"  # PerimeterX (HUMAN)
    r"|errors\.edgesuite\.net",  # Akamai
    re.I)
# Phrases of a challenge page that are printed as visible text and have a form no ordinary page uses.
VENDOR_TEXT = re.compile(
    r"reference\s*#\s*\d+\.[0-9a-f]{8}\.\d{10}\.[0-9a-f]+"  # Akamai
    r"|incapsula incident id",  # Imperva Incapsula
    re.I)
BODY_STATUS = re.compile(
    r"\b(?:(?P<a>403|429|503)\s+(?:forbidden|too many requests|service (?:temporarily )?unavailable)"
    r"|(?:http\s*)?(?:error|status)(?:\s*code)?\s*[:#]?\s*(?P<b>403|429|503)\b)", re.I)
BODY_DENIAL = re.compile(
    r"access denied|you (?:don.t|do not) have permission to access|you have been blocked|(?:your )?request (?:has been )?blocked"
    r"|access to this (?:page|site|website) (?:has been |is )?(?:denied|blocked)", re.I)
BODY_HUMAN = re.compile(
    r"verify (?:that )?you are (?:a )?human|are you a (?:robot|human)|checking your browser|just a moment\.\.\."
    r"|unusual traffic"
    r"|(?:complete|solve|pass|prove)\b[^.<>]{0,40}?\bcaptcha|captcha (?:challenge|verification) (?:is )?required", re.I)
_SCRIPT_STYLE = re.compile(r"<(script|style)\b.*?(?:</\1\s*>|$)", re.I | re.S)
_SCRIPT_STYLE_BODY = re.compile(r"(<(script|style)\b[^>]*>).*?(?:</\2\s*>|$)", re.I | re.S)
_TAG = re.compile(r"<[^>]*>?")
_TITLE = re.compile(r"<title[^>]*>(.*?)(?:</title|$)", re.I | re.S)


def classify_body(body: Any) -> Tuple[Optional[str], Optional[str]]:
    """``(error_class, failure_stage)`` for the text a fetch tool returned as an ordinary result, or ``(None, None)``.

    Only the first ``BODY_HEAD`` characters are read, locally; nothing is sent. The answer is ``human_verification`` for a
    bot-protection challenge or a human check, ``site_access`` for a 403 or a refusal and ``service_error`` for a 429 or 503,
    each with the stage ``response_observed``. A page that is not clearly a block page, however many of the words it
    contains, gives ``(None, None)`` (see the rules above). Use it for a page that came back as a success, and ``classify``
    for an exception or an error message.
    """
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if not isinstance(body, str) or not body.strip():
        return None, None
    head = body[:BODY_HEAD]
    title_match = _TITLE.search(head)
    title = " ".join(html.unescape(_TAG.sub(" ", title_match.group(1))).split()) if title_match else ""
    visible = " ".join(html.unescape(_TAG.sub(" ", _SCRIPT_STYLE.sub(" ", head))).split())
    markup = " ".join(_TAG.findall(_SCRIPT_STYLE_BODY.sub(r"\1", head)))  # tags and attributes only, no script or style bodies
    vendor = bool(VENDOR_MARKERS.search(markup) or VENDOR_MARKERS.search(title) or VENDOR_TEXT.search(visible))
    if title:
        visible = visible.replace(title, " ", 1).strip()  # the title is read on its own, as the start of the page
    kinds, early, status_code = set(), set(), None
    for is_early, text in ((True, title), (True, visible[:EARLY_TEXT]), (False, visible[EARLY_TEXT:])):
        for kind, pattern in (("status", BODY_STATUS), ("denial", BODY_DENIAL), ("human", BODY_HUMAN)):
            found = pattern.search(text)
            if not found:
                continue
            kinds.add(kind)
            if is_early:
                early.add(kind)
            if kind == "status":
                status_code = status_code or found.group("a") or found.group("b")
    short = len(body) <= BODY_HEAD and len(visible) + len(title) <= SHORT_PAGE
    if not vendor and not (kinds if short else len(kinds) >= 2 and early):
        return None, None
    if vendor or "human" in kinds:
        return "human_verification", "response_observed"
    if status_code in ("429", "503") and "denial" not in kinds:
        return "service_error", "response_observed"
    return "site_access", "response_observed"
