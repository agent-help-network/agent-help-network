import asyncio

import httpx

from ahn import mcp_adapter
from ahn.tool_metrics import is_mcp_metric_request, mcp_metric_token


def test_mcp_adapter_marks_its_internal_check_request(monkeypatch):
    pepper = "test-pepper-must-be-at-least-thirty-two-chars"
    monkeypatch.setenv("AHN_KEY_PEPPER", pepper)
    monkeypatch.setenv("AHN_API_BASE", "http://127.0.0.1:8765")
    received = []

    def handler(request):
        received.append(request.headers.get("X-AHN-MCP-Call"))
        return httpx.Response(200, json={"ok": True})

    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(mcp_adapter.httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    assert asyncio.run(mcp_adapter.call_api("GET", "/v1/incidents/check", params={"site_host": "example.org"})) == {"ok": True}
    assert received == [mcp_metric_token(pepper)]
    assert is_mcp_metric_request(received[0], pepper)
    assert not is_mcp_metric_request("0" * 64, pepper)
