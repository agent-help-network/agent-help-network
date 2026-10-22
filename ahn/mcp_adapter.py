"""dots MCP adapter. All observations flow through the central REST API."""

from __future__ import annotations

import os
import logging
import re
from uuid import UUID

from typing import Annotated, Any

import httpx
from pydantic import WithJsonSchema
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver import Context
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from .lookup_signals import ORIGIN_HEADER, TEST_HEADER, lenient_codes
from .models import Challenge, CheckResponse, ErrorClass, FailureStage, ObservationIn, OutcomeIn, Receipt, RetryAfter, STEP, Via, valid_host
from .tool_metrics import mcp_metric_token


# HTTPX INFO logs contain the entire request URL, including rejected query values.
logging.getLogger("httpx").setLevel(logging.WARNING)


def api_base() -> str:
    base = os.environ.get("AHN_API_BASE", "http://127.0.0.1:8765").rstrip("/")
    if not (base.startswith("http://127.0.0.1:") or base.startswith("http://localhost:") or base.startswith("https://")):
        raise ToolError("AHN_API_BASE must use local HTTP or HTTPS")
    return base


async def call_api(method: str, path: str, *, params: dict | None = None, payload: dict | None = None, write: bool = False,
                   extra_headers: dict | None = None) -> dict:
    headers = dict(extra_headers or {})
    pepper = os.environ.get("AHN_KEY_PEPPER", "")
    if len(pepper) >= 32:
        # Marks this loopback hop as an already rate-limited MCP request; the
        # REST side counts it as a tool execution only on the check endpoint.
        headers["X-AHN-MCP-Call"] = mcp_metric_token(pepper)
    if write:
        if os.environ.get("AHN_PUBLIC_MODE") == "1" and os.environ.get("AHN_REPORTING_ENABLED") == "1":
            pass
        else:
            key = os.environ.get("AHN_MCP_WRITE_KEY", "")
            if not key:
                raise ToolError("MCP writing is not configured")
            headers["X-API-Key"] = key
    try:
        async with httpx.AsyncClient(base_url=api_base(), timeout=10.0) as client:
            response = await client.request(method, path, params=params, json=payload, headers=headers)
        data = response.json()
    except (httpx.HTTPError, ValueError):
        raise ToolError("Central service unavailable or invalid response") from None
    if response.status_code >= 400:
        # Never echo the request, credentials, or arbitrary server text.
        code = data.get("error", "request_failed") if isinstance(data, dict) else "request_failed"
        raise ToolError(code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) else "request_failed")
    return data


mcp = MCPServer(
    name="Agent Help Network",
    title="Agent Help Network" if os.environ.get("AHN_PUBLIC_MODE") == "1" else "Agent Help Network (local build)",
    version="0.1.0",
    instructions=(
        "When an authorized web task is blocked (access denied, login wall, human verification, service error) or a user asks whether a site is down or blocks agents, call check_incident with the public hostname. "
        "The answer has three layers: access_profile (the host's own declared agent policy from robots.txt and llms.txt), official_paths and provider_status (official developer docs and status feeds), and observations (recent reports from other agents, often empty). "
        "Declared policy is not an access guarantee and no_reports does not establish that a site works. Do not poll without a task need. "
        "Follow your own operating authority and platform rules before submitting an observation. "
        "Send only public hostnames and structured fields, never personal URLs or credentials."
    ),
)


# The signal codes are advertised with their allowed values but accepted loosely (any JSON value): a wrong one is dropped
# by lenient_codes, never refused. The schema is what the model sees; validation is deliberately wider than it.
def _enum_schema(enum) -> dict:
    return {"type": "string", "enum": [item.value for item in enum]}


HTTP_STATUS_SCHEMA = {"type": "integer", "description": "HTTP status the site returned: 401, 402, 403, 407, 408, 429, 451 or 500-599. Leave out if unknown."}


def outer_request_headers(ctx: Context | None) -> dict:
    """Headers of the caller's own request that the central API needs for its anonymous lookup count.

    The adapter calls the API over loopback, where the caller's address is gone. It passes the address on under
    a header the API honours only on this authenticated hop (and the test mark, so a test lookup stays out of the
    count). Both are used for the count only; neither reaches the answer.
    """
    try:
        outer = ctx.request_context.request if ctx is not None else None
        headers = outer.headers if outer is not None else {}
    except (AttributeError, ValueError):
        return {}
    forwarded = {}
    address = headers.get("cf-connecting-ip", "").strip()
    if address:
        forwarded[ORIGIN_HEADER] = address
    if headers.get(TEST_HEADER, "").strip() == "1":
        forwarded[TEST_HEADER] = "1"
    return forwarded


@mcp.tool(
    description="Answer for one public hostname: what the host's robots.txt and llms.txt declare for agent user agents, the official developer documentation path, the provider's official status, and any recent observations from other agents. Use when a web task is blocked or when asked whether a site is down or blocks agents. Read-only for the caller: it never changes the target site or your data. Each lookup, with the optional codes you send, is counted anonymously per host and hour and never changes the answer. Send http_status, challenge, retry_after or via only when you actually observed them; leave them out otherwise. The service may fetch the host's public robots.txt, llms.txt and provider status feed to fill its cache. It answers at once from that cache: a host seen for the first time comes back with access_profile.freshness 'pending' and retry_after_seconds; ask once more after that many seconds, do not poll. A no_reports result means no observations, not a healthy site.",
    annotations=ToolAnnotations(title="Check host access profile", read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
    structured_output=True,
)
async def check_incident(site_host: str, error_class: str = "", task_step: str = "", failure_stage: str = "",
                         http_status: Annotated[Any, WithJsonSchema(HTTP_STATUS_SCHEMA)] = None,
                         challenge: Annotated[Any, WithJsonSchema(_enum_schema(Challenge))] = None,
                         retry_after: Annotated[Any, WithJsonSchema(_enum_schema(RetryAfter))] = None,
                         via: Annotated[Any, WithJsonSchema(_enum_schema(Via))] = None,
                         ctx: Context | None = None) -> CheckResponse:
    try:
        valid_host(site_host)
    except ValueError:
        raise ToolError("site_host must be a public lowercase hostname") from None
    if error_class and error_class not in ErrorClass._value2member_map_:
        raise ToolError("error_class must be one of: " + ", ".join(item.value for item in ErrorClass))
    if task_step and not STEP.fullmatch(task_step):
        raise ToolError("invalid_task_step")
    if failure_stage and failure_stage not in FailureStage._value2member_map_:
        raise ToolError("failure_stage must be one of: " + ", ".join(item.value for item in FailureStage))
    # The four signal codes are never a reason to refuse a lookup: a value outside its list is dropped.
    codes = lenient_codes(http_status, challenge, retry_after, via)
    params = {"site_host": site_host}
    if error_class:
        params["error_class"] = error_class
    if task_step:
        params["task_step"] = task_step
    if failure_stage:
        params["failure_stage"] = failure_stage
    params.update({name: value for name, value in codes.items() if value is not None})
    return CheckResponse(**await call_api("GET", "/v1/incidents/check", params=params, extra_headers=outer_request_headers(ctx)))


async def report_observation(observation: ObservationIn) -> Receipt:
    return Receipt(**await call_api("POST", "/v1/observations", payload=observation.model_dump(mode="json", exclude_unset=True), write=True))


async def update_outcome(observation_id: str, outcome: OutcomeIn) -> Receipt:
    try:
        UUID(observation_id)
    except ValueError:
        raise ToolError("invalid_observation_id") from None
    return Receipt(**await call_api("POST", f"/v1/observations/{observation_id}/outcomes", payload=outcome.model_dump(mode="json", exclude_unset=True), write=True))


if os.environ.get("AHN_PUBLIC_MODE") != "1" or os.environ.get("AHN_REPORTING_ENABLED") == "1":
    mcp.tool(
        description="Submit one de-identified firsthand observation under your own operating authority and platform rules. Public reports enter private review and are not site-status claims. Never include names, account details, URLs, cookies, tokens, or conversation text.",
        annotations=ToolAnnotations(title="Report an authorized observation", read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
        structured_output=True,
    )(report_observation)
if os.environ.get("AHN_PUBLIC_MODE") != "1":
    mcp.tool(
        description="Record a later result for your own earlier observation under your own operating authority and platform rules.",
        annotations=ToolAnnotations(title="Update an authorized outcome", read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True),
        structured_output=True,
    )(update_outcome)


def main():
    port = int(os.environ.get("AHN_MCP_PORT", "8766"))
    public_host = os.environ.get("AHN_MCP_PUBLIC_HOST", "").strip()
    allowed_hosts = [f"127.0.0.1:{port}", f"localhost:{port}"]
    if public_host:
        allowed_hosts.append(public_host)
    mcp.run(
        transport="streamable-http", host="127.0.0.1", port=port,
        streamable_http_path="/mcp", stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=allowed_hosts, allowed_origins=["https://chatgpt.com"]),
    )


if __name__ == "__main__":
    main()
