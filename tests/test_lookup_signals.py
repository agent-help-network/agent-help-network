"""Lookup signals (D47): validation, pure helpers, the MCP hop and the retention numbers. No database needed."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn import lookup_signals, mcp_adapter, platform_metrics
from ahn.lookup_signals import (HTTP_STATUS_RANGE, clean_address, client_kind_of, signal_group, source_digest,
                                status_class, valid_http_status)
from ahn.main import create_app
from ahn.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
PEPPER = "test-pepper-must-be-at-least-thirty-two-chars"


def test_http_status_boundaries():
    for code in (401, 402, 403, 407, 408, 429, 451, 500, 503, 599):
        assert valid_http_status(code), code
    # 400, 404 and the rest of 4xx are not access problems; 200/301 are not failures; 600 is not a status.
    for code in (-1, 0, 200, 301, 400, 404, 405, 409, 410, 418, 422, 450, 452, 499, 600, 999):
        assert not valid_http_status(code), code
    assert HTTP_STATUS_RANGE == (500, 599)


def test_status_class_folds_5xx_and_keeps_the_rest():
    assert [status_class(c) for c in (401, 403, 429, 451, 500, 503, 599, None)] == \
           ["401", "403", "429", "451", "5xx", "5xx", "5xx", "unspecified"]


def test_signal_group_status_wins_then_challenge_then_error_class():
    assert signal_group(None, "429", None) == "rate"
    assert signal_group("site_access", "429", "header") == "rate"
    assert signal_group(None, "401", None) == "login" and signal_group(None, "407", None) == "login"
    assert [signal_group(None, c, None) for c in ("403", "451", "402")] == ["block"] * 3
    assert signal_group(None, "5xx", None) == "outage" and signal_group(None, "408", None) == "outage"
    assert signal_group(None, "unspecified", "header") == "block"
    assert signal_group("login_auth", "unspecified", "none") == "login"
    assert signal_group("service_error", "unspecified", None) == "outage"
    assert signal_group("human_verification", "unspecified", None) == "block"
    assert signal_group("site_access", "unspecified", None) == "block"
    assert signal_group(None, "unspecified", None) == "unspecified"
    assert signal_group(None, "unspecified", "none") == "unspecified"


def test_client_kind_mapping():
    assert client_kind_of("plugin_hint") == "plugin" and client_kind_of("ahn_client_hint") == "ahn_client"
    assert client_kind_of("mcp_hint") == "mcp"
    for other in ("browser_hint", "agent_or_script_hint", "crawler_hint", "unknown"):
        assert client_kind_of(other) == "rest_other"
    assert set(lookup_signals.CLIENT_KINDS) == {"plugin", "ahn_client", "mcp", "web", "rest_other"}


def test_clean_address():
    assert clean_address(" 198.51.100.7 ") == "198.51.100.7"
    assert clean_address("2001:0db8:0000:0000:0000:0000:0000:0001") == "2001:db8::1"
    for bad in ("", "not-an-ip", "198.51.100.7, 10.0.0.1", "198.51.100.256"):
        assert clean_address(bad) is None


def test_source_digest_is_specific_to_host_day_and_address():
    day = date(2026, 10, 21)
    base = source_digest(PEPPER, day, "demo-site.io", "198.51.100.7")
    assert re.fullmatch(r"[0-9a-f]{64}", base) and "198.51.100.7" not in base
    assert base == source_digest(PEPPER, day, "demo-site.io", "198.51.100.7")
    assert base != source_digest(PEPPER, day, "other-site.io", "198.51.100.7")           # another host
    assert base != source_digest(PEPPER, date(2026, 10, 22), "demo-site.io", "198.51.100.7")  # another day
    assert base != source_digest(PEPPER, day, "demo-site.io", "198.51.100.8")             # another address
    assert base != source_digest("other-" + PEPPER, day, "demo-site.io", "198.51.100.7")  # another key


@pytest.fixture(scope="module")
def client():
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)
    return TestClient(create_app(settings))  # no `with`: validation answers before any database use


@pytest.mark.parametrize("params,field,allowed", [
    ({"error_class": "nope"}, "error_class", ["site_access", "login_auth", "human_verification", "service_error"]),
    ({"failure_stage": "nope"}, "failure_stage", ["before_target_response", "response_observed", "access_denial_observed", "unknown"]),
])
def test_existing_codes_still_refuse_bad_values_with_the_allowed_values_only(client, params, field, allowed):
    response = client.get("/v1/incidents/check", params={"site_host": "demo-site.io", **params})
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request", "field": field, "allowed_values": allowed}


def test_lenient_codes_keep_valid_values_and_drop_the_rest():
    from ahn.lookup_signals import lenient_codes
    assert lenient_codes("503", "header", "lt_60", "browser") == {"http_status": 503, "challenge": "header", "retry_after": "lt_60", "via": "browser"}
    assert lenient_codes("404", "maybe", "5", "curl") == {"http_status": None, "challenge": None, "retry_after": None, "via": None}
    assert lenient_codes(None, None, None, None) == {"http_status": None, "challenge": None, "retry_after": None, "via": None}
    assert lenient_codes("٣٠٣", None, None, None)["http_status"] is None
    # via=unknown says no more than leaving it out, so it is stored as not sent.
    assert lenient_codes("599", "none", "none", "unknown") == {"http_status": 599, "challenge": "none", "retry_after": "none", "via": None}


@pytest.mark.parametrize("value", ["9" * 5000, "9" * 4, "", "0403", " 403", "40３", 10 ** 5000, True, 429.5, float("nan"), float("inf"), -403, [], {}, None],
                         ids=["long5000", "four", "empty", "zero403", "space403", "fullwidth", "bigint", "bool", "float", "nan", "inf", "neg", "list", "dict", "none"])
def test_lenient_codes_survive_any_input(value):
    from ahn.lookup_signals import lenient_codes
    result = lenient_codes(value, value, value, value)
    assert result == {"http_status": None, "challenge": None, "retry_after": None, "via": None}


def test_lenient_codes_accept_whole_numbers_of_any_numeric_type():
    from ahn.lookup_signals import lenient_codes
    assert lenient_codes(403, None, None, None)["http_status"] == 403
    assert lenient_codes(403.0, None, None, None)["http_status"] == 403
    assert lenient_codes("503", None, None, None)["http_status"] == 503


def test_ipv6_sources_are_counted_per_slash_64():
    day = date(2026, 10, 21)
    same = {source_digest(PEPPER, day, "demo-site.io", f"2001:db8:1:2::{n}") for n in range(1, 6)}
    assert len(same) == 1
    assert source_digest(PEPPER, day, "demo-site.io", "2001:db8:1:3::1") not in same
    assert source_digest(PEPPER, day, "demo-site.io", "2001:db8:1:2:ffff::1") in same  # still inside the /64
    assert "2001:db8" not in next(iter(same))


def test_openapi_lists_the_new_lookup_parameters(client):
    names = {p["name"] for p in client.app.openapi()["paths"]["/v1/incidents/check"]["get"]["parameters"]}
    assert {"http_status", "challenge", "retry_after", "via"} <= names


# --- MCP ----------------------------------------------------------------------------------------

def test_mcp_tool_accepts_the_new_codes_and_hides_the_context():
    code = ("import asyncio, json; from ahn.mcp_adapter import mcp; "
            "print(json.dumps({t.name: [sorted(t.input_schema.get('properties', {})), t.annotations.model_dump()] "
            "for t in asyncio.run(mcp.list_tools())}))")
    env = {**os.environ, "AHN_PUBLIC_MODE": "1", "AHN_REPORTING_ENABLED": "1"}
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    tools = json.loads(done.stdout.strip().splitlines()[-1])
    props, annotations = tools["check_incident"]
    assert props == ["challenge", "error_class", "failure_stage", "http_status", "retry_after", "site_host", "task_step", "via"]
    assert annotations["read_only_hint"] is True
    # Owner decision D-1021-1 #6: a report only adds a record, so it is not marked destructive.
    assert tools["report_observation"][1]["destructive_hint"] is False and tools["report_observation"][1]["read_only_hint"] is False


def test_mcp_tool_drops_bad_signal_codes_instead_of_refusing(monkeypatch):
    seen = {}

    async def fake(method, path, *, params=None, extra_headers=None, **_):
        seen.update(params=params)
        raise mcp_adapter.ToolError("stop")
    monkeypatch.setattr(mcp_adapter, "call_api", fake)
    with pytest.raises(mcp_adapter.ToolError, match="stop"):  # reached the API: not refused
        asyncio.run(mcp_adapter.check_incident("demo-site.io", http_status=404, challenge="yes", retry_after="soon", via="api"))
    assert seen["params"] == {"site_host": "demo-site.io"}
    with pytest.raises(mcp_adapter.ToolError, match="stop"):
        asyncio.run(mcp_adapter.check_incident("demo-site.io", http_status=503, via="unknown"))
    assert seen["params"] == {"site_host": "demo-site.io", "http_status": 503}  # via=unknown is not passed on


@pytest.mark.parametrize("arguments", [
    {"http_status": "abc"}, {"http_status": 429.5}, {"http_status": "9" * 3000}, {"http_status": True}, {"challenge": 1}, {"retry_after": 5.5},
    {"via": False}, {"challenge": "yes", "retry_after": "soon", "via": "api", "http_status": 404}, {"http_status": 403, "challenge": "header"},
])
def test_mcp_call_never_fails_because_of_a_signal_value(monkeypatch, arguments):
    """Through the real tool layer (argument validation included): a wrong or oddly typed signal never costs the answer."""
    seen = {}

    async def fake(method, path, *, params=None, extra_headers=None, **_):
        seen["params"] = params
        return ANSWER
    monkeypatch.setattr(mcp_adapter, "call_api", fake)
    result = asyncio.run(mcp_adapter.mcp.call_tool("check_incident", {"site_host": "demo-site.io", **arguments}))
    assert result.structured_content["site_host"] == "demo-site.io"
    assert seen["params"]["site_host"] == "demo-site.io"
    assert not set(seen["params"]) - {"site_host", "http_status", "challenge", "retry_after", "via"}
    for name, value in seen["params"].items():
        assert name == "site_host" or value in (403, "header")  # only values that were valid reached the API


def test_mcp_tool_passes_only_the_codes_it_was_given(monkeypatch):
    seen = {}

    async def fake(method, path, *, params=None, extra_headers=None, **_):
        seen.update(params=params, headers=extra_headers)
        raise mcp_adapter.ToolError("stop")
    monkeypatch.setattr(mcp_adapter, "call_api", fake)
    with pytest.raises(mcp_adapter.ToolError):
        asyncio.run(mcp_adapter.check_incident("demo-site.io"))
    assert seen["params"] == {"site_host": "demo-site.io"} and seen["headers"] == {}
    with pytest.raises(mcp_adapter.ToolError):
        asyncio.run(mcp_adapter.check_incident("demo-site.io", error_class="site_access", http_status=503, challenge="header",
                                               retry_after="lt_60", via="browser"))
    assert seen["params"] == {"site_host": "demo-site.io", "error_class": "site_access", "http_status": 503,
                              "challenge": "header", "retry_after": "lt_60", "via": "browser"}


ANSWER = {
    "site_host": "demo-site.io", "generated_at": "2026-10-20T00:00:00Z", "synthetic": False, "window_hours": 24,
    "data_horizon_hours": 168, "current_site_status": "unknown", "evidence_level": "no_reports",
    "summary_code": "NO_MATCHING_REPORTS_STATUS_UNKNOWN", "summary": "x", "failures": 0, "successes": 0,
    "reporting_sources": 0, "observations": [], "provider_status": None, "provider_source_state": "not_verified_by_current_methods",
    "recommendation": {"action": "unknown", "confidence": "low", "reasons": []}, "next_checks": [],
}


def _ctx(headers):
    return SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers=headers)))


def test_outer_request_headers_pass_only_the_address_and_the_test_mark():
    outer = {"cf-connecting-ip": " 198.51.100.7 ", "x-ahn-test": "1", "authorization": "Bearer secret", "user-agent": "x"}
    assert mcp_adapter.outer_request_headers(_ctx(outer)) == {"x-ahn-origin-ip": "198.51.100.7", "x-ahn-test": "1"}
    assert mcp_adapter.outer_request_headers(_ctx({"x-ahn-test": "0"})) == {}
    assert mcp_adapter.outer_request_headers(None) == {}
    # A caller cannot choose its own origin: the header it sends on the outer request is never passed on, only the edge address is.
    forged = {"x-ahn-origin-ip": "203.0.113.9", "cf-connecting-ip": "198.51.100.7"}
    assert mcp_adapter.outer_request_headers(_ctx(forged)) == {"x-ahn-origin-ip": "198.51.100.7"}
    assert mcp_adapter.outer_request_headers(_ctx({"x-ahn-origin-ip": "203.0.113.9"})) == {}
    assert mcp_adapter.outer_request_headers(SimpleNamespace(request_context=SimpleNamespace(request=None))) == {}

    class NoContext:
        @property
        def request_context(self):
            raise ValueError("Context is not available outside of a request")
    assert mcp_adapter.outer_request_headers(NoContext()) == {}


# --- retention and the migration ----------------------------------------------------------------

def test_retention_literals_in_the_cleanup_match_the_constants():
    source = inspect.getsource(platform_metrics.prune_analytics)
    assert re.search(r"DELETE FROM lookup_signals_hourly WHERE hour < now\(\) - interval '%d days'" % lookup_signals.HOURLY_RETENTION_DAYS, source)
    # `day < CURRENT_DATE - 1` keeps today and yesterday: two days.
    # The UTC date, not the database session's.
    assert re.search(r"DELETE FROM lookup_sources_daily WHERE day < \(now\(\) AT TIME ZONE 'UTC'\)::date - %d\b" % (lookup_signals.SOURCES_RETENTION_DAYS - 1), source)
    assert (lookup_signals.HOURLY_RETENTION_DAYS, lookup_signals.SOURCES_RETENTION_DAYS) == (35, 2)


def test_migration_only_adds_two_tables():
    sql = (ROOT / "migrations" / "027_lookup_signals.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", code) == ["lookup_signals_hourly", "lookup_sources_daily"]
    assert not re.search(r"\b(ALTER|DROP|UPDATE|DELETE|TRUNCATE)\b", code)  # nothing existing is touched
    assert "Rollback:" in sql
    # No column can hold an address, a path or free text: every text column is a short varchar or the digest.
    assert not re.search(r"\btext\b|\bip\b|inet", code, re.I)


def test_an_enormous_content_length_is_refused_not_a_server_error(client):
    """Same family as the 5000-digit http_status: a digit string past Python's int conversion limit must not become a 500."""
    response = client.post("/v1/observations", content=b"{}", headers={"Content-Type": "application/json", "Content-Length": "9" * 5000})
    assert response.status_code < 500


def test_mcp_schema_shows_the_allowed_values_while_validation_stays_loose():
    tools = {t.name: t for t in asyncio.run(mcp_adapter.mcp.list_tools())}
    props = tools["check_incident"].input_schema["properties"]
    assert (props["challenge"]["type"], props["challenge"]["enum"]) == ("string", ["header", "none"])
    assert (props["retry_after"]["type"], props["retry_after"]["enum"]) == ("string", ["none", "lt_60", "60_3600", "gt_3600"])
    assert (props["via"]["type"], props["via"]["enum"]) == ("string", ["http_fetch", "browser", "unknown"])
    assert props["http_status"]["type"] == "integer" and "500-599" in props["http_status"]["description"]
    assert tools["check_incident"].input_schema["required"] == ["site_host"]


@pytest.mark.parametrize("length", ["²", "4096²"])  # latin-1 superscript two: str.isdigit() is true, int() fails
def test_non_ascii_digit_content_length_is_refused_not_a_server_error(client, length):
    response = client.post("/v1/observations", content=b"{}", headers={"Content-Type": "application/json", "Content-Length": length.encode("latin-1")})
    assert response.status_code < 500
