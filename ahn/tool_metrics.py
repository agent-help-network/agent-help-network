"""Classify authenticated MCP checks without storing a host or client identifier."""

from __future__ import annotations

import hmac
from hashlib import sha256


def mcp_metric_token(pepper: str) -> str:
    return hmac.new(pepper.encode("utf-8"), b"ahn-mcp-check-telemetry-v1", sha256).hexdigest()


def is_mcp_metric_request(provided: str | None, pepper: str) -> bool:
    return bool(provided and len(provided) == 64 and hmac.compare_digest(provided, mcp_metric_token(pepper)))


def provider_signal_from_result(result: dict) -> str:
    provider = result.get("provider_status")
    if not isinstance(provider, dict) or provider.get("freshness") != "fresh":
        return "unavailable"
    if provider.get("indicator") in {"minor", "major", "critical"}:
        return "active"
    return "clear" if provider.get("indicator") == "none" else "unavailable"
