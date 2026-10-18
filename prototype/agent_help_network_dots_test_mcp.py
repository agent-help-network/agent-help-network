"""Disposable, read-only MCP probe for Agent Help Network.

Install prototype/requirements-mcp.txt, then run from this repository's root:
  python prototype/agent_help_network_dots_test_mcp.py

This serves only fictional observations. It does not read the Muse test key,
accept reports, contact target websites, or store user data.
"""

from __future__ import annotations

import os

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from agent_help_network_muse_test_api import check_result, valid_host


class Observation(BaseModel):
    observed_at: str
    reporter_id: str
    attempt_actor: str
    outcome: str


class CheckResult(BaseModel):
    site_host: str
    generated_at: str
    synthetic: bool
    evidence_level: str
    summary: str
    failures: int
    successes: int
    observations: list[Observation]


mcp = MCPServer(
    name="Agent Help Network Test",
    title="Agent Help Network (synthetic test)",
    version="0.1.0",
    instructions=(
        "This is a connector test with fictional data only. Use check_incident "
        "when a user asks about a site access or verification problem during "
        "their task. It does not diagnose real outages or intentional blocks. "
        "A no_reports response does not imply that the site is working."
    ),
)


@mcp.tool(
    description=(
        "Check a hostname for shared access or verification observations. "
        "Read-only synthetic test: demo.agenthelpnetwork.test has one fictional "
        "failure; case-a.agenthelpnetwork.test and case-b.agenthelpnetwork.test "
        "have contrasting fictional time series; other hosts return no_reports. "
        "Never treat the result "
        "as a real-world status signal. Send only a public hostname, never a URL "
        "path, query, account identifier, or personal information."
    ),
    annotations=ToolAnnotations(
        title="Check incident observations (test)",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    structured_output=True,
)
def check_incident(site_host: str) -> CheckResult:
    if not valid_host(site_host):
        raise ToolError("site_host must be a lowercase hostname without a URL path")
    return CheckResult(**check_result(site_host))


if __name__ == "__main__":
    port = int(os.environ.get("AHN_MCP_PORT", "8766"))
    public_host = os.environ.get("AHN_MCP_PUBLIC_HOST", "").strip()
    allowed_hosts = [f"127.0.0.1:{port}", f"localhost:{port}"]
    if public_host:
        allowed_hosts.append(public_host)
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=allowed_hosts,
            allowed_origins=["https://chatgpt.com"],
        ),
    )
