"""The requests the hook and ahn-client really build, replayed into the app: what ends up in lookup_signals_hourly.

Needs the dedicated ahn_test database. The clients' transports are replaced by a function that hands the request to
the app's TestClient, so the URL and the User-Agent are exactly the ones the clients made.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import types
import urllib.parse
from dataclasses import replace
from pathlib import Path

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)
os.environ.setdefault("AHN_DATABASE_URL", TEST_URL)
PEPPER = "test-pepper-must-be-at-least-thirty-two-chars"
os.environ.setdefault("AHN_KEY_PEPPER", PEPPER)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "clients" / "python" / "src"))

from fastapi.testclient import TestClient  # noqa: E402

import ahn_client as ahn  # noqa: E402
from ahn_client import _adapter, _client  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.settings import Settings  # noqa: E402

spec = importlib.util.spec_from_file_location("ahn_claude_code_hook_e2e", ROOT / "plugins" / "claude-code" / "ahn" / "hooks" / "ahn_lookup.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

HOST = "demo-site.io"
CALLER_IP = "198.51.100.7"
TABLES = "lookup_signals_hourly, lookup_sources_daily"


class Replayed(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    settings = replace(Settings(TEST_URL, PEPPER, public_mode=True, reporting_enabled=True, collect_lookup_metrics=True))
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute(f"TRUNCATE {TABLES}, lookup_metrics, request_metrics, traffic_attribution_daily, traffic_client_daily, "
                     "mcp_check_metrics, status_cache, status_sources, queried_hosts, host_access_profile, background_reads RESTART IDENTITY CASCADE")
    client = TestClient(create_app(settings))
    seen = []

    def replay(request, timeout):
        parts = urllib.parse.urlsplit(request.full_url)
        assert (parts.scheme, parts.netloc, parts.path) == ("https", "agenthelpnetwork.com", "/v1/incidents/check")
        assert request.get_method() == "GET" and request.data is None
        seen.append(request.full_url)
        response = client.get(parts.path + "?" + parts.query,
                              headers={"User-Agent": request.get_header("User-agent"), "CF-Connecting-IP": CALLER_IP})
        assert response.status_code == 200, response.text
        return Replayed(response.content)

    ahn.clear_cache()
    monkeypatch.setattr(_client, "_urlopen", replay)
    monkeypatch.setattr(hook, "urlopen", replay)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    for name in ("AHN_HOOK_DISABLE", "AHN_HOOK_SKIP_HOSTS", "AHN_HOOK_FAKE_RESPONSE", "AHN_CLIENT_DISABLE", "AHN_CLIENT_SKIP_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    yield settings, seen
    ahn.clear_cache()


def stored(settings):
    with connect(settings) as conn:
        return conn.execute("SELECT error_class, failure_stage, http_status_class, challenge, retry_after, via, client_kind, "
                            "lookups, sources FROM lookup_signals_hourly ORDER BY 1,2,3").fetchall()


def http_error(status, headers):
    error = type("HTTPStatusError", (Exception,), {"__module__": "httpx"})("failed")
    error.response = types.SimpleNamespace(status_code=status, headers=headers)
    return error


def test_ahn_client_check_with_signals_is_one_counted_row(app_client):
    settings, seen = app_client
    error = http_error(429, {"Retry-After": "30"})
    error_class, stage = ahn.classify(error)
    answer = ahn.check(f"https://{HOST}/private?token=x", error_class, stage, **ahn.signals(error))
    assert answer is not None
    assert "http_status=429" in seen[0] and "token" not in seen[0] and "private" not in seen[0]
    assert "http_status" not in answer.summary()
    assert [dict(row) for row in stored(settings)] == [{
        "error_class": "service_error", "failure_stage": "response_observed", "http_status_class": "429", "challenge": "none",
        "retry_after": "lt_60", "via": "http_fetch", "client_kind": "ahn_client", "lookups": 1, "sources": 1}]


def test_the_plugin_hook_request_is_counted_with_its_codes(app_client):
    settings, seen = app_client
    payload = {"hook_event_name": "PostToolUse", "tool_name": "WebFetch", "tool_input": {"url": f"https://{HOST}/a/b?q=1"},
               "tool_response": {"code": 403, "codeText": "Forbidden", "headers": {"cf-mitigated": "challenge"}}}
    output, _ = hook.run(json.dumps(payload).encode("utf-8"))
    line = output["hookSpecificOutput"]["additionalContext"]
    assert "http_status=403" in seen[0] and "http_status" not in line
    assert [dict(row) for row in stored(settings)] == [{
        "error_class": "human_verification", "failure_stage": "response_observed", "http_status_class": "403", "challenge": "header",
        "retry_after": "unspecified", "via": "http_fetch", "client_kind": "plugin", "lookups": 1, "sources": 1}]


def test_an_adapter_request_is_counted_as_ahn_client_and_following_the_link_adds_nothing(app_client):
    settings, seen = app_client
    error = http_error(503, {"Retry-After": "7200", "X-Other": "kept-out"})
    note = _adapter.note_for(error, {"url": f"https://{HOST}/x"}, adapter="langchain")
    assert note and "7200" not in seen[0] and "kept-out" not in seen[0]
    rows = [dict(row) for row in stored(settings)]
    assert rows == [{
        "error_class": "service_error", "failure_stage": "response_observed", "http_status_class": "5xx", "challenge": "none",
        "retry_after": "gt_3600", "via": "http_fetch", "client_kind": "ahn_client", "lookups": 1, "sources": 1}]
    # The "Full JSON" link in the note is the same lookup without the codes: opening it is one more plain lookup, not a double count of these.
    link = note.split("Full JSON: ")[1].split(" ")[0].rstrip(".")
    assert "http_status" not in link and "retry_after" not in link
