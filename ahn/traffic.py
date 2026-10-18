"""Bounded, privacy-limited request attribution for the operator dashboard."""

from __future__ import annotations

import hmac
import ipaddress
from datetime import date, datetime, timezone
from hashlib import sha256
from urllib.parse import urlsplit

from starlette.requests import Request


SOURCES = {"moltychan", "getpostingboard", "campfire", "agentboard", "openai", "muse", "google", "bing"}
CAMPAIGNS = {"beta_launch", "beta_2026_10"}


def _known_host(host: str) -> str | None:
    host = host.lower().rstrip(".")
    for suffix, source in (
        ("moltychan.org", "moltychan"),
        ("getpostingboard.dev", "getpostingboard"),
        ("agentsboard.org", "campfire"),
        ("agentboard.ai", "agentboard"),
        ("chatgpt.com", "openai"),
        ("openai.com", "openai"),
        ("muse.ai", "muse"),
        ("google.com", "google"),
        ("bing.com", "bing"),
    ):
        if host == suffix or host.endswith("." + suffix):
            return source
    return None


def request_source(request: Request) -> tuple[str, str, str, str]:
    """Return bounded categories only; never retain arbitrary URL/header content."""
    tag = request.query_params.get("utm_source", "").lower()
    medium = request.query_params.get("utm_medium", "").lower()
    campaign = request.query_params.get("utm_campaign", "").lower()
    referrer = request.headers.get("referer", "")
    try:
        ref_host = urlsplit(referrer).hostname or ""
    except ValueError:
        ref_host = ""
    known_referrer = _known_host(ref_host)
    source = tag if tag in SOURCES else ("tagged_other" if tag else known_referrer or ("other_referrer" if ref_host else "unknown_direct"))
    return (
        source,
        medium if medium in {"community", "search", "agent", "email"} else "unknown",
        campaign if campaign in CAMPAIGNS else "none_or_other",
        known_referrer or ("other" if ref_host else "none"),
    )


def client_type(request: Request) -> str:
    ua = request.headers.get("user-agent", "").lower()[:512]
    if any(token in ua for token in ("googlebot", "bingbot", "oai-searchbot", "gptbot", "claudebot", "facebookexternalhit")):
        return "crawler_hint"
    if any(token in ua for token in ("chatgpt", "claude-user", "anthropic", "muse", "python-requests", "httpx", "curl/", "wget/")):
        return "agent_or_script_hint"
    if "mozilla/" in ua:
        return "browser_hint"
    return "unknown"


def daily_client_digest(request: Request, pepper: str) -> tuple[date, str | None]:
    """Pseudonymize the edge-provided address for this UTC day only."""
    day = datetime.now(timezone.utc).date()
    forwarded = request.headers.get("cf-connecting-ip", "").strip()
    try:
        address = ipaddress.ip_address(forwarded)
    except ValueError:
        return day, None
    message = f"traffic-v1:{day.isoformat()}:{address.compressed}".encode("ascii")
    return day, hmac.new(pepper.encode("utf-8"), message, sha256).hexdigest()
