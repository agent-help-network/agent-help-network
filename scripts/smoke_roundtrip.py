"""Synthetic REST ↔ live MCP round trip on local loopback services."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def observation(host: str, family: str, outcome: str, minutes_ago: int):
    return {
        "schema_version": 1,
        "site_host": host,
        "task_step": "login_verification",
        "error_class": "human_verification",
        "observed_at": (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(),
        "agent_family": family,
        "attempt_actor": "agent",
        "attempt_env": "agent_cloud_browser",
        "outcome": outcome,
        "challenge_vendor": "unknown",
        "challenge_kind": "loop",
        "synthetic": True,
        "idempotency_key": str(uuid4()),
        "consent_confirmed": True,
    }


async def main():
    api = os.environ.get("AHN_API_BASE", "http://127.0.0.1:8875").rstrip("/")
    mcp_url = os.environ.get("AHN_MCP_URL", "http://127.0.0.1:8876/mcp")
    muse_key = os.environ["AHN_MUSE_KEY"]
    host = f"smoke-{uuid4().hex[:8]}.agenthelpnetwork.test"
    with httpx.Client(base_url=api, timeout=10) as rest:
        first = rest.post("/v1/observations", json=observation(host, "muse", "blocked", 3), headers={"X-API-Key": muse_key})
        assert first.status_code == 201, first.status_code
        async with streamable_http_client(mcp_url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert {"check_incident", "report_observation", "update_outcome"} <= names, names
                checked = await session.call_tool("check_incident", {"site_host": host})
                assert not checked.is_error, checked.content
                assert checked.structured_content["failures"] == 1, checked.structured_content
                second = await session.call_tool("report_observation", {"observation": observation(host, "dots", "passed", 1)})
                assert not second.is_error, second.content
        final = rest.get("/v1/incidents/check", params={"site_host": host})
        assert final.status_code == 200
        result = final.json()
        assert result["synthetic"] is True
        assert result["failures"] == 1 and result["successes"] == 1
        assert result["reporting_sources"] == 2
        assert result["evidence_level"] == "later_success_observed"
        print("PASS: REST muse submission → live MCP read; live MCP dots submission → REST read")
        print("PASS: synthetic label, two sources, failure and later pass without recovery claim")


if __name__ == "__main__":
    asyncio.run(main())
