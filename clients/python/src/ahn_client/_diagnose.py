"""Turn a response object into the lookup's codes without raising: ``diagnose(response)``.

Browser automation (Playwright's ``page.goto``, ``page.request``) and HTTP clients (httpx, requests) hand back a 403 or a
challenge page as an ordinary response, not as an exception, so ``classify(exc)`` never sees it. This module reads such an
object by its attributes (``status_code`` or ``status``, ``headers``, ``text`` or ``content``); none of the libraries is
imported. The status and the header names decide; the first 4,000 characters of a body are tested for a few human-check
markers on your machine and the body is never sent anywhere, stored or returned. The status rules are those of
``classify``; the header rules are those of ``_signals`` plus the AWS WAF marker below.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from ._classify import _attr, classify_text
from ._signals import from_headers, header_entries, header_pairs, valid_signals, via_for_exception

# AWS WAF answers a request that lacks a valid token with the header ``x-amzn-waf-action``: value ``challenge`` with status 202
# (Challenge action) or ``captcha`` with status 405 (CAPTCHA action).
# https://docs.aws.amazon.com/waf/latest/developerguide/waf-captcha-and-challenge-actions.html
AWS_WAF_HEADER = "x-amzn-waf-action"
AWS_WAF_VALUES = ("challenge", "captcha")
BODY_SCAN_CHARS = 4000
# Narrower than the status-text rules on purpose: a normal 200 page may mention "cloudflare" or "challenge".
# The wording is the Claude Code plugin's (HUMAN_VERIFICATION in ahn_lookup.py) without the bare word "challenge".
HUMAN_CHECK_BODY = re.compile(r"captcha|verify (?:that )?you are (?:a )?human|are you a robot|checking your browser|just a moment", re.I)


class Diagnosis(tuple):
    """``(error_class, failure_stage, signals)``; unpack it into three names. ``candidate`` is True when the only
    evidence is a human-check marker in the body of a response that said 200: a hint to look up, not a finding."""

    candidate: bool

    def __new__(cls, error_class: Optional[str], stage: Optional[str], found: Dict[str, Any], candidate: bool = False):
        self = super().__new__(cls, (error_class, stage, found))
        self.candidate = candidate
        return self


def _status(response: Any) -> Optional[int]:
    for name in ("status_code", "status"):
        code = _attr(response, name)
        if isinstance(code, int) and not isinstance(code, bool) and 100 <= code <= 599:
            return code
    return None


def _aws_waf_challenge(headers: Optional[Dict[Any, Any]]) -> bool:
    if not headers:
        return False
    return any(name == AWS_WAF_HEADER and any(text.strip().lower() in AWS_WAF_VALUES for text in texts)
               for name, texts, _ in header_entries(headers))


def _body_head(response: Any, body: Any) -> str:
    """The first characters of the body, only when it is already in memory as text or bytes (a method such as Playwright's
    ``response.text()`` is not called: it is a coroutine on the async API; pass ``body=`` yourself)."""
    for value in (body, _attr(response, "text"), _attr(response, "content")):
        if isinstance(value, str):
            return value[:BODY_SCAN_CHARS]
        if isinstance(value, (bytes, bytearray)):
            return bytes(value[:BODY_SCAN_CHARS * 4]).decode("utf-8", errors="replace")[:BODY_SCAN_CHARS]
    return ""


def diagnose(response: Any, body: Any = None) -> Diagnosis:
    """``(error_class, failure_stage, signals)`` for a Playwright, httpx or requests response (or anything with the same
    attributes), for ``ahn.check(url, error_class, stage, **signals)``.

    ``error_class`` is None when the response is neither a block nor an outage (a 200, a 404, a 405 without a challenge
    marker); do not look it up then. 401 and 407 give login_auth, 402, 403 and 451 site_access, 408, 429 and 5xx
    service_error. A 403 or 451 whose headers or body point to a human check is human_verification, and so is a 202 or 405
    with an AWS WAF ``x-amzn-waf-action: challenge|captcha`` header. A 200 whose body starts with a human-check marker gives
    human_verification with ``.candidate`` True. ``signals`` holds ``http_status``, ``challenge`` (``header`` for
    ``cf-mitigated: challenge`` or the AWS WAF marker; never decided from the body), ``retry_after`` and ``via``.

    ``body`` is optional text or bytes for a response whose body is not an attribute (Playwright: ``page.content()``). It
    is read here and goes nowhere: no body text, header value, URL or path is returned or sent. Never raises.
    """
    try:
        status = _status(response)
        headers = header_pairs(response)
        found = from_headers(status, headers)
        via = via_for_exception([response])
        aws = _aws_waf_challenge(headers)
        if aws:
            found["challenge"] = "header"
        if via:
            found["via"] = via
        if status is None:
            return Diagnosis(None, None, {})
        if aws and status in (202, 405, 403, 429, 503):
            return Diagnosis("human_verification", "response_observed", valid_signals(found))
        text = _body_head(response, body)
        cue = found.get("challenge") == "header" or bool(HUMAN_CHECK_BODY.search(text))
        if status in (403, 451) and cue:
            return Diagnosis("human_verification", "response_observed", valid_signals(found))
        if status >= 400:
            error_class, _ = classify_text(f"HTTP {status}")
            if error_class is None:
                return Diagnosis(None, None, {})
            return Diagnosis(error_class, "response_observed", valid_signals(found))
        if status == 200 and HUMAN_CHECK_BODY.search(text):
            return Diagnosis("human_verification", "response_observed", valid_signals(found), candidate=True)
        return Diagnosis(None, None, {})
    except Exception:
        return Diagnosis(None, None, {})
