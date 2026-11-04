"""The one-line-per-request log (ahn/request_log.py): a line for each answer, never an address, a query or a body. No database."""

from __future__ import annotations

import asyncio
import logging
import os
import re

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn import request_log  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.request_log import RequestLog  # noqa: E402
from ahn.settings import Settings  # noqa: E402

LINE = re.compile(r"^request method=(?P<method>\S+) path=(?P<path>\S+) status=(?P<status>\d+|error|none) ms=\d+(?P<aborted> aborted=1)?(?P<hop> hop=loopback)? ua=(?P<ua>.*)$")


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def lines():
    handler = Capture()
    request_log.logger.addHandler(handler)
    try:
        yield handler.lines
    finally:
        request_log.logger.removeHandler(handler)


def web(**fields) -> TestClient:
    return TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, **fields)))


def test_get_head_post_and_404_each_leave_one_line(lines):
    client = web()
    assert client.get("/en/policy", headers={"User-Agent": "probe/1.0"}).status_code == 200
    assert client.head("/en/policy").status_code == 200
    assert client.post("/v1/incidents/check", json={}).status_code in (404, 405, 422)
    assert client.get("/no/such/page").status_code == 404
    parsed = [LINE.match(line) for line in lines]
    assert all(parsed), lines
    assert [m['method'] for m in parsed] == ["GET", "HEAD", "POST", "GET"]
    assert [m['status'] for m in parsed][:2] == ["200", "200"]
    assert parsed[3]["status"] == "404" and parsed[3]["path"] == "/no/such/page"
    assert parsed[0]["ua"] == "probe/1.0"


def test_a_missing_user_agent_is_a_dash(lines):
    client = web()
    client.get("/en/policy", headers={"User-Agent": ""})
    assert lines[-1].endswith(" ua=-")


def test_query_string_address_and_other_headers_are_never_logged(lines):
    client = TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)), client=("198.51.100.9", 5000))
    client.get("/en/policy?q=secret-query&site_host=example.com", headers={
        "X-Forwarded-For": "203.0.113.77", "Forwarded": "for=203.0.113.78", "X-Real-IP": "203.0.113.79", "CF-Connecting-IP": "203.0.113.80",
        "Authorization": "Bearer tokentoken", "Cookie": "a=cookievalue", "Referer": "https://referrer.example/page", "User-Agent": "agent/2"})
    assert len(lines) == 1
    for forbidden in ("secret-query", "example.com", "198.51.100.9", "203.0.113", "tokentoken", "cookievalue", "referrer", "?"):
        assert forbidden not in lines[0], forbidden
    assert lines[0].startswith("request method=GET path=/en/policy status=200 ") and lines[0].endswith(" ua=agent/2")


@pytest.mark.parametrize("method, requested, logged", [
    ("GET", "/v1/incidents/v1", "/v1/incidents/{incident_id}"),            # the value is also a piece of the fixed part
    ("GET", "/en/incidents/e", "/en/incidents/{incident_id}"),             # ... and of the language segment
    ("POST", "/v1/observations/o/outcomes", "/v1/observations/{observation_id}/outcomes"),
    ("GET", "/.well-known/agentboard-verify/well-known", "/.well-known/agentboard-verify/{handle}"),
    ("GET", "/en/incidents/some-private-looking-id", "/en/incidents/{incident_id}"),
    ("GET", "/zz/policy", "/zz/policy"),                                  # the language segment stays as sent, even when unknown
])
def test_a_variable_part_logs_its_name_in_its_own_place(lines, method, requested, logged):
    web().request(method, requested, follow_redirects=False)
    assert LINE.match(lines[-1])["path"] == logged and len(lines) == 1


def test_an_unmatched_path_is_logged_as_sent(lines):
    client = web()
    for requested in ("/static/nope.css", "/mcp", "/.well-known/mcp/server-card", "/wp-login.php"):
        client.get(requested, follow_redirects=False)
        assert LINE.match(lines[-1])["path"] == requested
    assert len(lines) == 4


def test_path_and_user_agent_are_cut_and_made_printable(lines):
    client = web()
    client.get("/" + "a" * 500, follow_redirects=False, headers={"User-Agent": "u" * 300})
    match = LINE.match(lines[-1])
    assert len(match["path"]) == request_log.PATH_LIMIT and len(match["ua"]) == request_log.UA_LIMIT
    client.get("/x%0Ainjected%20line", follow_redirects=False, headers={"User-Agent": "tab\tand\x7fdel"})
    assert len(lines) == 2 and "\n" not in lines[-1] and "\t" not in lines[-1] and "\x7f" not in lines[-1]
    assert LINE.match(lines[-1])["path"] == "/x?injected_line"


def test_switch_off_leaves_no_line_and_no_layer(lines, monkeypatch):
    monkeypatch.setenv("AHN_REQUEST_LOG", "0")
    assert Settings.from_env().request_log is False
    client = web(request_log=False)
    client.get("/en/policy")
    assert lines == []
    assert all(item.cls is not RequestLog for item in client.app.user_middleware)
    monkeypatch.delenv("AHN_REQUEST_LOG")
    assert Settings.from_env().request_log is True
    assert web().app.user_middleware[0].cls is RequestLog  # on by default, and outermost


def test_an_exception_before_the_response_logs_status_error_and_still_raises(lines):
    async def broken(scope, receive, send):
        raise RuntimeError("boom")

    async def run():
        async def receive():
            return {"type": "http.request"}

        async def send(message):
            pass

        await RequestLog(broken)({"type": "http", "method": "GET", "path": "/x", "headers": []}, receive, send)

    with pytest.raises(RuntimeError):
        asyncio.run(run())
    assert LINE.match(lines[-1])["status"] == "error"


def test_lifespan_and_websocket_scopes_pass_through_untouched(lines):
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    async def run():
        for kind in ("lifespan", "websocket"):
            await RequestLog(inner)({"type": kind}, None, None)

    asyncio.run(run())
    assert seen == ["lifespan", "websocket"] and lines == []


def test_the_logger_writes_to_stdout_on_its_own_and_does_not_double_through_the_root(capsys):
    request_log.configure_logger()
    request_log.configure_logger()  # idempotent: still one handler of ours
    assert sum(1 for h in request_log.logger.handlers if getattr(h, "_ahn_request_log", False)) == 1
    assert request_log.logger.propagate is False and request_log.logger.level == logging.INFO
    web().get("/en/policy")
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and LINE.match(out[0])


def test_the_internal_mcp_hop_is_marked_and_ua_stays_last(lines):
    client = web()
    client.get("/en/policy", headers={"X-AHN-MCP-Call": "anything", "User-Agent": "python-httpx/0.28"})
    client.get("/en/policy", headers={"User-Agent": "python-httpx/0.28"})
    assert LINE.match(lines[0])["hop"] and lines[0].endswith(" hop=loopback ua=python-httpx/0.28")
    assert LINE.match(lines[1])["hop"] is None and "hop=" not in lines[1]


def drive(app, scope=None):
    async def run():
        async def receive():
            return {"type": "http.request"}

        async def send(message):
            pass

        await RequestLog(app)({"type": "http", "method": "GET", "path": "/s", "headers": [], **(scope or {})}, receive, send)

    asyncio.run(run())


def test_an_application_that_ends_without_a_response_logs_none(lines):
    async def silent(scope, receive, send):
        return

    drive(silent)
    assert len(lines) == 1 and LINE.match(lines[0])["status"] == "none"


def test_a_cancelled_request_without_a_response_logs_error_once(lines):
    async def cancelled(scope, receive, send):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        drive(cancelled)
    assert len(lines) == 1 and LINE.match(lines[0])["status"] == "error"


def test_an_exception_after_the_response_started_keeps_one_line_marked_aborted(lines):
    async def broken_stream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"a", "more_body": True})
        raise RuntimeError("cut")

    with pytest.raises(RuntimeError):
        drive(broken_stream)
    match = LINE.match(lines[0])
    assert len(lines) == 1 and match["status"] == "200" and match["aborted"]


def test_a_streaming_response_gets_one_line_and_the_body_is_not_buffered(lines):
    from starlette.responses import StreamingResponse
    from starlette.routing import Route

    seen = []

    async def chunks():
        for index in range(3):
            seen.append(len(lines))  # one line per request, written when it ends, never one per chunk
            yield f"chunk{index}".encode()

    async def stream(request):
        return StreamingResponse(chunks(), media_type="text/plain")

    app = create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True))
    app.router.routes.insert(0, Route("/test-stream", stream))
    response = TestClient(app).get("/test-stream")
    assert response.text == "chunk0chunk1chunk2" and seen == [0, 0, 0]
    assert len(lines) == 1 and LINE.match(lines[0])["status"] == "200" and LINE.match(lines[0])["path"] == "/test-stream"


def test_the_combined_shape_with_a_mount_at_the_root_gets_one_line_per_request(lines):
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def mcp_like(request):
        return JSONResponse({"ok": True})

    app = create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True))
    app.mount("/", Starlette(routes=[Route("/mcp", mcp_like, methods=["GET", "POST"])]))  # as ahn/combined.py mounts the MCP app
    client = TestClient(app)
    assert client.post("/mcp", json={}).status_code == 200
    assert client.get("/nothing-here").status_code == 404
    assert client.get("/en/policy").status_code == 200
    got = [LINE.match(line) for line in lines]
    assert [(m["method"], m["path"], m["status"]) for m in got] == [("POST", "/mcp", "200"), ("GET", "/nothing-here", "404"), ("GET", "/en/policy", "200")]
