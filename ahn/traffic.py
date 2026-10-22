"""Bounded, privacy-limited request attribution for the operator dashboard."""

from __future__ import annotations

import hmac
import ipaddress
import re
from datetime import date, datetime, timezone
from hashlib import sha256
from urllib.parse import urlsplit

from starlette.requests import Request

from .owner_auth import valid_owner_ip
from .tool_metrics import is_mcp_metric_request


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


# Attribution recorded for a request from the operator's allowed network. A shared home address is a network
# category only: it is not a person and not proof that the request was a test.
OPERATOR_ATTRIBUTION = ("owner_network", "internal", "none_or_other", "none")
OPERATOR_SOURCE = OPERATOR_ATTRIBUTION[0]

# Our own software announces itself with these User-Agent prefixes (ahn_lookup.py and ahn_client/_client.py).
# Any caller can send them, so like every kind here they are hints, not identities. The values fit varchar(24).
PLUGIN_UA_PREFIX = "ahn-claude-code-plugin/"
AHN_CLIENT_UA_PREFIX = "ahn-client/"
CLIENT_KINDS = ("plugin_hint", "ahn_client_hint", "mcp_hint", "browser_hint", "agent_or_script_hint", "crawler_hint", "unknown")


def is_operator_address(address: str, owner_allowed_ips: tuple[str, ...]) -> bool:
    """True when this address is one of the operator's allowed individual IPs."""
    return valid_owner_ip(address, owner_allowed_ips)


def is_operator_request(request: Request, owner_allowed_ips: tuple[str, ...]) -> bool:
    """True when the edge-provided address is one of the operator's allowed individual IPs.

    One definition for every measurement that must leave the operator out. Nothing is stored: the caller
    decides what to do with the answer. The existing daily lookup totals (lookup_metrics) deliberately do not
    use it; the lookup signals (ahn/lookup_signals.py) do.
    """
    return is_operator_address(request.headers.get("cf-connecting-ip", ""), owner_allowed_ips)


def attribute_request(request: Request, owner_allowed_ips: tuple[str, ...]) -> tuple[str, str, str, str]:
    """request_source(), except that operator-network requests collapse into one internal category."""
    if is_operator_request(request, owner_allowed_ips):
        return OPERATOR_ATTRIBUTION
    return request_source(request)


def client_type(request: Request, pepper: str = "") -> str:
    """Coarse client-software hint from the request path, the MCP hop token and the User-Agent; the raw header is never kept."""
    if request.url.path.startswith("/mcp") or (pepper and is_mcp_metric_request(request.headers.get("x-ahn-mcp-call"), pepper)):
        # The remote MCP endpoint itself, or the adapter's loopback hop that carries one tool call to the REST API.
        return "mcp_hint"
    ua = request.headers.get("user-agent", "").lower()[:512]
    if ua.startswith(PLUGIN_UA_PREFIX):
        return "plugin_hint"
    if ua.startswith(AHN_CLIENT_UA_PREFIX):
        return "ahn_client_hint"
    if any(token in ua for token in ("googlebot", "bingbot", "oai-searchbot", "gptbot", "claudebot", "facebookexternalhit")):
        return "crawler_hint"
    if any(token in ua for token in ("chatgpt", "claude-user", "anthropic", "muse", "python-requests", "httpx", "curl/", "wget/")):
        return "agent_or_script_hint"
    if "mozilla/" in ua:
        return "browser_hint"
    return "unknown"


# Adoption measurement (ahn/adoption.py, docs/decisions.md D49): the same hints, folded into the plan's client
# categories. "crawler" and "web" (the human search page) are stored but are not adoption; see adoption.EXCLUDED.
CLIENT_CATEGORIES = ("plugin", "ahn_client", "mcp_host", "agent_or_script", "browser", "unknown")
CATEGORY_CRAWLER = "crawler"
CATEGORY_WEB = "web"
_CATEGORY_OF_TYPE = {
    "plugin_hint": "plugin", "ahn_client_hint": "ahn_client", "mcp_hint": "mcp_host",
    "agent_or_script_hint": "agent_or_script", "browser_hint": "browser", "crawler_hint": CATEGORY_CRAWLER, "unknown": "unknown",
}

# MCP host families, from the User-Agent of the caller's own request to /mcp (the adapter forwards only this label, never
# the header). Tokens are matched in this order; a host that matches none is "other_mcp", and a call with no User-Agent
# at all is "unknown_mcp" (so an unlisted host and a missing header are not counted as the same thing). Hints, like every kind here.
MCP_HOSTS = ("claude", "chatgpt", "cursor", "vscode", "other_mcp", "unknown_mcp")
MCP_HOST_UNKNOWN = "unknown_mcp"  # no User-Agent (or no label on the hop) to read a family from
MCP_HOST_NONE = "none"
_MCP_HOST_TOKENS = (
    ("claude", ("claude", "anthropic")),
    ("chatgpt", ("openai", "chatgpt")),
    ("cursor", ("cursor",)),
    ("vscode", ("vscode", "visual studio code", "copilot")),
)

# ahn-client names its framework adapter in the User-Agent: "ahn-client/0.4.0 (+langchain; +https://...)". The names the
# shipped adapters use are listed (the Claude Agent SDK and OpenAI Agents SDK ones carry a hyphen, stored with an underscore);
# any other well-formed label is "other", no label is "none". The raw label is never kept.
ADAPTERS = ("langchain", "crewai", "openai_agents", "claude_agent_sdk")
ADAPTER_NONE = "none"
ADAPTER_OTHER = "other"
_ADAPTER_LABEL = re.compile(r"\(\+([a-z][a-z0-9_-]{0,23});")


def client_category(kind: str) -> str:
    """Map client_type() onto the adoption categories; anything not known is "unknown"."""
    return _CATEGORY_OF_TYPE.get(kind, "unknown")


def mcp_host_kind(user_agent: str) -> str:
    """Coarse MCP host family from a User-Agent value (first 512 characters, case-insensitive)."""
    ua = (user_agent or "").lower()[:512]
    if not ua.strip():
        return MCP_HOST_UNKNOWN
    for name, tokens in _MCP_HOST_TOKENS:
        if any(token in ua for token in tokens):
            return name
    return "other_mcp"


def adapter_label(user_agent: str) -> str:
    """The ahn-client framework adapter from a User-Agent value: one of ADAPTERS, "other", or "none"."""
    ua = (user_agent or "").lower()[:512]
    if not ua.startswith(AHN_CLIENT_UA_PREFIX):
        return ADAPTER_NONE
    found = _ADAPTER_LABEL.search(ua)
    if not found:
        return ADAPTER_NONE
    label = found.group(1).replace("-", "_")
    return label if label in ADAPTERS else ADAPTER_OTHER


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
