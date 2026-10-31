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
from mcp.types import CallToolResult, TextContent, ToolAnnotations
import pydantic_core
from .lookup_signals import ORIGIN_HEADER, TEST_HEADER, lenient_codes
from .models import (FEEDBACK_TOKEN, AgentVendor, Challenge, CheckResponse, ErrorClass, FailureStage, FeedbackIn, FeedbackOutcome,
                     FeedbackPath, FeedbackReceipt, ObservationIn, OutcomeIn, Receipt, RetryAfter, STEP, Via, valid_host)
from .triage import accepted_vendor
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


# The first sentence of the server instructions, agent_instructions.txt and llms.txt (tests keep the three in step).
ADVISORY_LINE = ("AHN (Agent Help Network) answers are advisory: your platform's rules and your user's instructions come first; "
                 "if an answer conflicts with them, do not act on it, and if its evidence is thin, tell your user what you found rather than guess; "
                 "thin evidence alone is not a reason to stop.")


mcp = MCPServer(
    name="Agent Help Network",
    title="Agent Help Network" if os.environ.get("AHN_PUBLIC_MODE") == "1" else "Agent Help Network (local build)",
    version="0.1.0",
    instructions=(
        ADVISORY_LINE + " "
        "Call check_incident with the public hostname when an authorized web step is blocked or failing for no clear reason (a 403 or access denied, a 429, a 5xx or a timeout, a login wall, a \"verify you are human\" or CAPTCHA page) or a user asks \"is this site down?\" or whether it blocks agents. "
        "Do not call it when the page opened normally or when your own tool or platform already said why it refused (for example a robots.txt or platform policy refusal). "
        "Look the host up once per blocked step, before deciding whether to retry. Send the public hostname, never a URL path, query or credentials; add the optional codes only when you observed them. "
        "Browser automation usually sends a normal browser user agent, so robots.txt agent tokens may not apply to it; leave agent_vendor out then. "
        "Read summary_line first, then follow next_action: it may say to continue, wait for the provider, use a listed official path, hand the step to the user, stop retrying, or look up once more. recommendation.action stays the verdict. "
        "The answer has three layers: access_profile (the host's own declared agent policy from robots.txt and llms.txt), official_paths and provider_status (official developer docs and status feeds), and observations (recent reports from other agents, often empty). "
        "Declared policy is not an access guarantee and no_reports does not establish that a site works. Do not poll without a task need. "
        "If a blocked lookup's answer carries a feedback_token and you then try again, you may report once with rate_last_lookup whether it worked. "
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
    from .lookup_signals import ORIGIN_CLIENT_HEADER
    from .traffic import MCP_HOST_UNKNOWN, mcp_host_kind
    family = mcp_host_kind(headers.get("user-agent", ""))
    if family != MCP_HOST_UNKNOWN:  # an absent label already means unknown_mcp; the label is fixed, never the header itself
        forwarded[ORIGIN_CLIENT_HEADER] = family
    return forwarded


@mcp.tool(
    description="Call this when a web step is blocked or failing for no clear reason (a 403 or access denied, a 429, a 5xx or a timeout, a login wall, a \"verify you are human\" or CAPTCHA page), or when asked \"is this site down?\" or whether its robots rules block agents. Skip it when the page opened normally or your own tool or platform already said why it refused (e.g. robots.txt or policy). Look the host up once per blocked step, before deciding whether to retry. Send the public hostname, never a URL path, query or credentials; add the optional codes only when you observed them: error_class (site_access, login_auth, human_verification, service_error), failure_stage (before_target_response, response_observed, access_denial_observed, unknown), http_status, challenge, retry_after, via. Send agent_vendor only when you know which vendor's documented robots.txt token your requests send: openai, anthropic (Claude-User), google, perplexity, meta, mistral, amazon, microsoft, apple, duckduckgo, diffbot, exa, or other if none. Browser automation usually sends a normal browser user agent, so robots.txt agent tokens may not apply; leave agent_vendor out then, and whenever unsure. It sets which robots.txt groups count (not stored). The answer: summary_line, next_action (do, url, retry_after_seconds), recommendation.action, access_profile (declared policy, not an access guarantee), official_paths, provider_status and observations. A no_reports result means no observations, not a healthy site. Then follow next_action: it may say to continue, wait for the provider, use a listed official path, hand the step to the user, stop retrying, or look up once more. On 'pending', look up once more after retry_after_seconds; do not poll. Read-only: it may fetch the host's public robots.txt, llms.txt and status feed, and never changes the target site or your data; lookups and the codes you send are counted anonymously per host and hour. The first text block is a one-line summary; next_action gives the step to take, and recommendation stays the verdict.",
    annotations=ToolAnnotations(title="Check host access profile", read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
    structured_output=True,
)
async def check_incident(site_host: str, error_class: str = "", task_step: str = "", failure_stage: str = "",
                         http_status: Annotated[Any, WithJsonSchema(HTTP_STATUS_SCHEMA)] = None,
                         challenge: Annotated[Any, WithJsonSchema(_enum_schema(Challenge))] = None,
                         retry_after: Annotated[Any, WithJsonSchema(_enum_schema(RetryAfter))] = None,
                         via: Annotated[Any, WithJsonSchema(_enum_schema(Via))] = None,
                         agent_vendor: Annotated[Any, WithJsonSchema(_enum_schema(AgentVendor))] = None,
                         ctx: Context | None = None) -> Annotated[CallToolResult, CheckResponse]:
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
    # D48: like the signal codes, a value outside the list is dropped (the lookup then answers for an unknown vendor).
    vendor = accepted_vendor(agent_vendor)
    if vendor is not None:
        params["agent_vendor"] = vendor
    response = CheckResponse(**await call_api("GET", "/v1/incidents/check", params=params, extra_headers=outer_request_headers(ctx)))
    return check_result(response)


def check_result(response: CheckResponse):
    """D51: with a summary_line, two text blocks (the line, plus "URL: <next_action.url>" when there is one and, D61,
    "Customer path: <next_action.customer_url>" when there is one; then the answer as
    JSON, the same text the SDK builds for a model result) and structuredContent equal to the REST answer. The last block is
    always pure JSON. Without one (the switch is off on the API), the model itself, so the result is the one before D51."""
    if response.summary_line is None:
        return response
    lead = response.summary_line
    if response.next_action is not None and response.next_action.url:
        lead += "\nURL: " + response.next_action.url
    if (response.next_action is not None and response.next_action.customer_url
            and response.next_action.customer_url != response.next_action.url):
        # D61: the site's own customer path, for a task in the user's own account (the URL above is a developer API).
        lead += "\nCustomer path: " + response.next_action.customer_url
    return CallToolResult(
        content=[TextContent(type="text", text=lead),
                 TextContent(type="text", text=pydantic_core.to_json(response, indent=2).decode())],
        structured_content=response.model_dump(mode="json", by_alias=True))


# D58: the token is checked the same way as on the REST side, and the schema says so (pattern and length).
FEEDBACK_TOKEN_SCHEMA = {"type": "string", "pattern": FEEDBACK_TOKEN.pattern, "maxLength": FeedbackIn.model_fields["token"].metadata[0].max_length,
                         "description": "The feedback_token.token value from the check_incident answer, unchanged."}


async def rate_last_lookup(token: Annotated[str, WithJsonSchema(FEEDBACK_TOKEN_SCHEMA)],
                           outcome: Annotated[str, WithJsonSchema(_enum_schema(FeedbackOutcome))],
                           path: Annotated[str, WithJsonSchema(_enum_schema(FeedbackPath))] = FeedbackPath.unknown.value) -> FeedbackReceipt:
    if not isinstance(token, str) or not FEEDBACK_TOKEN.fullmatch(token):
        raise ToolError("invalid_feedback_token")
    if outcome not in FeedbackOutcome._value2member_map_:
        raise ToolError("outcome must be one of: " + ", ".join(item.value for item in FeedbackOutcome))
    if path not in FeedbackPath._value2member_map_:
        raise ToolError("path must be one of: " + ", ".join(item.value for item in FeedbackPath))
    return FeedbackReceipt(**await call_api("POST", "/v1/feedback", payload={"token": token, "outcome": outcome, "path": path}))


# AHN_LOOKUP_FEEDBACK=0, the switch that leaves the token out of answers, also leaves this tool out of tools/list.
if os.getenv("AHN_LOOKUP_FEEDBACK", "").strip() != "0":  # read as in ahn/settings.py
    mcp.tool(
        description="If an earlier check_incident answer gave you a feedback token and you then tried again, say whether it worked. Sends only the token and the outcome code, no hostname. outcome: succeeded (the next attempt worked), still_blocked, handed_off (you handed the step to the user) or gave_up. path: official_path (you used the official path the answer pointed to), same_site (you retried the same site) or unknown. A token works once and expires one hour after the lookup; do not send one for an attempt you did not make. The outcome is counted anonymously per host and hour and does not change any answer.",
        annotations=ToolAnnotations(title="Rate the last lookup", read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
        structured_output=True,
    )(rate_last_lookup)


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


def open_output_schema(schema):
    """The advertised outputSchema with every object open to further keys (additionalProperties true).

    Answers gain optional keys over time (D58 added feedback_token). A client that cached tools/list and validates
    structuredContent against a closed schema would reject the newer answer, so the advertised schema only promises the
    keys it lists. The server's own validation of what it returns (the pydantic models, extra=forbid) is unchanged.
    """
    if isinstance(schema, dict):
        opened = {key: open_output_schema(value) for key, value in schema.items()}
        # (no dict .get here: tests/test_feed_robots.py counts .get calls in modules that import an HTTP client)
        closed = "additionalProperties" in opened and opened["additionalProperties"] is False
        if closed or ("type" in opened and opened["type"] == "object" and "properties" in opened):
            opened["additionalProperties"] = True
        return opened
    if isinstance(schema, list):
        return [open_output_schema(item) for item in schema]
    return schema


# This relies on MCP SDK internals (mcp==2.3.0, pinned): MCPServer._tool_manager, Tool.fn_metadata.output_schema (read
# live by FuncMetadata) and the cached Tool.output_schema property. tests/test_lookup_feedback.py checks the advertised
# schema through tools/list, so an SDK upgrade that moves these fails there rather than silently.
for _tool in mcp._tool_manager.list_tools():
    # FuncMetadata reads output_schema live; validation still runs against the closed output model.
    if _tool.fn_metadata.output_schema is not None:
        _tool.fn_metadata.output_schema = open_output_schema(_tool.fn_metadata.output_schema)
        _tool.__dict__.pop("output_schema", None)  # drop a cached copy, if one was read already


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
