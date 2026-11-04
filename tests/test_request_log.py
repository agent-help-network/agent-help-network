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

LINE = re.compile(r"^request method=(\S+) path=(\S+) status=(\d+|error) ms=\d+ ua=(.*)$")


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
    assert [m.group(1) for m in parsed] == ["GET", "HEAD", "POST", "GET"]
    assert [m.group(3) for m in parsed][:2] == ["200", "200"]
    assert parsed[3].group(3) == "404" and parsed[3].group(2) == "/no/such/page"
    assert parsed[0].group(4) == "probe/1.0"


def test_a_missing_user_agent_is_a_dash(lines):
    client = web()
    client.get("/en/policy", headers={"User-Agent": ""})
    assert lines[-1].endswith(" ua=-")


def test_query_string_and_address_are_never_logged(lines):
    client = web()
    client.get("/en/policy?q=secret-query&site_host=example.com", headers={"X-Forwarded-For": "203.0.113.77", "Authorization": "Bearer tokentoken", "Cookie": "a=cookievalue"})
    text = "\n".join(lines)
    for forbidden in ("secret-query", "example.com", "203.0.113.77", "127.0.0.1", "tokentoken", "cookievalue", "?"):
        assert forbidden not in text
    assert "path=/en/policy " in text


def test_a_variable_part_of_the_path_logs_its_name_not_the_value(lines):
    client = web()
    client.get("/v1/incidents/some-private-looking-id")
    client.get("/v1/observations/some-other-id/outcomes")
    joined = "\n".join(lines)
    assert "some-private-looking-id" not in joined and "some-other-id" not in joined
    assert "path=/v1/incidents/{incident_id} " in joined and "path=/v1/observations/{observation_id}/outcomes " in joined


def test_path_and_user_agent_are_cut_and_made_printable(lines):
    client = web()
    client.get("/" + "a" * 500, follow_redirects=False, headers={"User-Agent": "u" * 300})
    match = LINE.match(lines[-1])
    assert len(match.group(2)) == request_log.PATH_LIMIT and len(match.group(4)) == request_log.UA_LIMIT
    client.get("/x%0Ainjected%20line", follow_redirects=False, headers={"User-Agent": "tab\tand\x7fdel"})
    assert len(lines) == 2 and "\n" not in lines[-1] and "\t" not in lines[-1] and "\x7f" not in lines[-1]
    assert LINE.match(lines[-1]).group(2) == "/x?injected_line"


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
    assert LINE.match(lines[-1]).group(3) == "error"


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
