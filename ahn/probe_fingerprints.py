"""Markers of bot-defense providers, for the AHN server check of a home page (D67, ahn/site_probe.py).

Two kinds of marker are kept apart on purpose:

* ``presence``: headers and cookie names a provider adds to ordinary, successful responses too (a CDN edge, a bot
  score cookie). They say a provider is in front of the host, never that anything was blocked. They only fill
  ``edge_vendors_seen``.
* ``challenge``: a header the provider documents as sent only when it issued a challenge, or a marker in a blocked
  or tiny response body (a status in ``block_statuses``, or a complete 200 page that is the challenge itself).
  Only these can make a check report a bot challenge.

Every entry carries the vendor's own documentation page and the date it was read (``source`` and ``checked``);
tests/test_site_probe.py fails on an entry without them. A marker that no first-party page documents is not in this
table. Left out for that reason on the 2026-11-01 reading: Akamai Bot Manager, Imperva and Kasada markers, the
``x-vercel-mitigated`` header and the ``x-datadome`` header. Vercel stays as a presence marker only.

Nothing here reads a value: cookies are matched by name, bodies by a fixed substring, and the check stores only the
vendor code, the kind of marker (header, cookie, body) and a confidence.
"""

from __future__ import annotations

from dataclasses import dataclass

VERSION = "ahn-fp/1"
VENDOR_CODES = ("cloudflare", "datadome", "perimeterx", "aws_waf", "vercel", "generic_captcha", "unknown")
CHECKED = "2026-11-01"


@dataclass(frozen=True)
class Marker:
    """One documented marker. ``kind`` is header, cookie or body; ``name`` the header name, cookie-name prefix or
    lowercase body substring; ``value`` an optional lowercase substring the header value must contain."""
    kind: str
    name: str
    value: str | None
    source: str
    checked: str
    role: str  # "presence" or "challenge"
    confidence: str = "high"
    statuses: frozenset[int] | None = None  # challenge markers only: the statuses the marker counts for (None: any)


@dataclass(frozen=True)
class Vendor:
    code: str
    markers: tuple[Marker, ...]


_CF_DETECT = "https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/"
_CF_HEADERS = "https://developers.cloudflare.com/fundamentals/reference/http-headers/"
_CF_COOKIES = "https://developers.cloudflare.com/fundamentals/reference/policies-compliances/cloudflare-cookies/"
_CF_PAGES = "https://developers.cloudflare.com/pages/configuration/serving-pages/"
_CF_CONFIG = "https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/additional-configuration/"
_AWS_ACTIONS = "https://docs.aws.amazon.com/waf/latest/developerguide/waf-captcha-and-challenge-actions.html"
_AWS_TOKEN = "https://docs.aws.amazon.com/waf/latest/developerguide/waf-tokens-details.html"
_DD_TAG = "https://docs.datadome.co/docs/javascript-tag"
_HUMAN_COOKIES = "https://docs.humansecurity.com/applications/use-of-cookies-web-storage"
_HUMAN_ENFORCERS = "https://docs.humansecurity.com/applications/about-enforcers"
_VERCEL_HEADERS = "https://vercel.com/docs/headers/response-headers"
_RECAPTCHA = "https://developers.google.com/recaptcha/docs/display"
_RECAPTCHA_FAQ = "https://developers.google.com/recaptcha/docs/faq"
_HCAPTCHA = "https://docs.hcaptcha.com/"
_TURNSTILE = "https://developers.cloudflare.com/turnstile/get-started/client-side-rendering/"

BLOCK_STATUSES = frozenset({401, 403, 405, 429, 503})

VENDORS: tuple[Vendor, ...] = (
    Vendor("cloudflare", (
        Marker("header", "cf-ray", None, _CF_HEADERS, CHECKED, "presence"),
        Marker("header", "server", "cloudflare", _CF_PAGES, CHECKED, "presence", "medium"),
        Marker("cookie", "__cf_bm", None, _CF_COOKIES, CHECKED, "presence"),
        Marker("header", "cf-mitigated", "challenge", _CF_DETECT, CHECKED, "challenge"),
        # The challenge page's own configuration object (documented as the page's configuration; the page does not say it
        # appears nowhere else, so it counts at medium confidence and only on a blocked or complete small response).
        Marker("body", "_cf_chl_opt", None, _CF_CONFIG, CHECKED, "challenge", "medium"),
    )),
    Vendor("datadome", (
        Marker("cookie", "datadome", None, _DD_TAG, CHECKED, "presence"),
        Marker("body", "captcha-delivery.com", None, _DD_TAG, CHECKED, "challenge", "high"),
    )),
    Vendor("perimeterx", (
        Marker("cookie", "_px", None, _HUMAN_COOKIES, CHECKED, "presence"),
        Marker("body", "captcha.px-cdn.net", None, _HUMAN_ENFORCERS, CHECKED, "challenge", "medium"),
        Marker("body", "captcha.px-cloud.net", None, _HUMAN_ENFORCERS, CHECKED, "challenge", "medium"),
    )),
    Vendor("aws_waf", (
        Marker("cookie", "aws-waf-token", None, _AWS_TOKEN, CHECKED, "presence"),
        Marker("header", "x-amzn-waf-action", "captcha", _AWS_ACTIONS, CHECKED, "challenge", statuses=frozenset({405})),
        Marker("header", "x-amzn-waf-action", "challenge", _AWS_ACTIONS, CHECKED, "challenge", statuses=frozenset({202})),
    )),
    Vendor("vercel", (
        Marker("header", "x-vercel-id", None, _VERCEL_HEADERS, CHECKED, "presence"),
        Marker("header", "server", "vercel", _VERCEL_HEADERS, CHECKED, "presence"),
    )),
    # A human-verification widget script in a blocked response: the provider is not known.
    Vendor("generic_captcha", (
        Marker("body", "google.com/recaptcha/api.js", None, _RECAPTCHA, CHECKED, "challenge", "medium"),
        Marker("body", "recaptcha.net/recaptcha/api.js", None, _RECAPTCHA_FAQ, CHECKED, "challenge", "medium"),
        Marker("body", "js.hcaptcha.com/1/api.js", None, _HCAPTCHA, CHECKED, "challenge", "medium"),
        Marker("body", "challenges.cloudflare.com/turnstile/v0/api.js", None, _TURNSTILE, CHECKED, "challenge", "medium"),
    )),
)

# Tracked response header names (lowercase). Anything else is never looked at.
TRACKED_HEADERS = frozenset({m.name for v in VENDORS for m in v.markers if m.kind == "header"}
                            | {"retry-after", "www-authenticate", "location", "content-type"})
