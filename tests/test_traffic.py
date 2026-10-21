import importlib.util
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from starlette.requests import Request

from ahn.monitor import summarize_lookup_kinds
from ahn.platform_metrics import latest_sample
from ahn.tool_metrics import mcp_metric_token
from ahn.traffic import (CLIENT_KINDS, OPERATOR_ATTRIBUTION, attribute_request, client_type, daily_client_digest,
                         is_operator_request, request_source)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "clients" / "python" / "src"))
from ahn_client import _client  # noqa: E402

_spec = importlib.util.spec_from_file_location("ahn_hook_for_ua", ROOT / "plugins" / "claude-code" / "ahn" / "hooks" / "ahn_lookup.py")
_hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hook)
PEPPER = "test-pepper-must-be-at-least-thirty-two-chars"


def _request(path: str, headers: dict[str, str] | None = None) -> Request:
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": path.split("?", 1)[0],
                    "query_string": path.partition("?")[2].encode(), "headers": raw_headers})


def test_attribution_bounds_untrusted_input():
    req = _request("/en/agents?utm_source=moltychan&utm_medium=community&utm_campaign=beta_launch",
                   {"Referer": "https://moltychan.org/tool/33?secret=do-not-store", "User-Agent": "Mozilla/5.0"})
    assert request_source(req) == ("moltychan", "community", "beta_launch", "moltychan")
    assert client_type(req) == "browser_hint"
    unknown = _request("/?utm_source=" + "x" * 1000, {"Referer": "https://private.example/path?token=secret"})
    assert request_source(unknown) == ("tagged_other", "unknown", "none_or_other", "other")
    assert request_source(_request("/en/?utm_source=campfire&utm_medium=community"))[0] == "campfire"
    assert request_source(_request("/en/", {"Referer": "https://www.agentboard.ai/t/example"}))[0] == "agentboard"


def test_daily_digest_uses_only_edge_ip():
    pepper = "test-pepper-must-be-at-least-thirty-two-chars"
    trusted = _request("/", {"CF-Connecting-IP": "198.51.100.23", "X-Forwarded-For": "1.2.3.4"})
    other = _request("/", {"CF-Connecting-IP": "198.51.100.24"})
    day, digest = daily_client_digest(trusted, pepper)
    assert day == datetime.now(timezone.utc).date()
    assert digest and digest != daily_client_digest(other, pepper)[1]
    assert daily_client_digest(_request("/", {"X-Forwarded-For": "1.2.3.4"}), pepper)[1] is None


def test_render_sample_sums_distinct_series_at_latest_time():
    sample = latest_sample([
        {"labels": [{"field": "service", "value": "srv-test"}], "unit": "requests",
         "values": [{"timestamp": "2026-10-04T13:00:00Z", "value": 3}]},
        {"labels": [{"field": "service", "value": "srv-test"}], "unit": "requests",
         "values": [{"timestamp": "2026-10-04T13:00:00Z", "value": 2}]},
    ], "srv-test")
    assert sample == (datetime(2026, 10, 4, 13, tzinfo=timezone.utc), Decimal(5), "requests")


def test_our_clients_get_their_own_kind_from_their_real_user_agents():
    # The strings are read from the shipped code, so a User-Agent change there fails here instead of silently going `unknown`.
    assert _hook.USER_AGENT.startswith("ahn-claude-code-plugin/")
    assert _client.USER_AGENT.startswith("ahn-client/")
    path = "/v1/incidents/check?site_host=example.org"
    assert client_type(_request(path, {"User-Agent": _hook.USER_AGENT})) == "plugin_hint"
    assert client_type(_request(path, {"User-Agent": _client.USER_AGENT})) == "ahn_client_hint"
    assert client_type(_request(path, {"User-Agent": "ahn-claude-code-plugin/0.2"})) == "plugin_hint"
    assert client_type(_request(path, {"User-Agent": "ahn-client/0.2 (+https://agenthelpnetwork.com)"})) == "ahn_client_hint"
    assert client_type(_request(path, {"User-Agent": "AHN-Client/0.9"})) == "ahn_client_hint"  # case-insensitive, any version


def test_mcp_is_recognised_by_path_and_by_the_authenticated_loopback_hop():
    hop = {"User-Agent": "python-httpx/0.28.1", "X-AHN-MCP-Call": mcp_metric_token(PEPPER)}
    # The adapter's own httpx call to the REST API is an MCP tool call, not a script.
    assert client_type(_request("/v1/incidents/check?site_host=example.org", hop), PEPPER) == "mcp_hint"
    # The same header with a wrong token, or when no pepper is configured, earns nothing.
    assert client_type(_request("/v1/incidents/check", {**hop, "X-AHN-MCP-Call": "0" * 64}), PEPPER) == "agent_or_script_hint"
    assert client_type(_request("/v1/incidents/check", hop), "") == "agent_or_script_hint"
    # The remote MCP endpoint, whatever the connecting app calls itself.
    for agent in ("openai-mcp/1.0.0", "claude-code/2.1 (cli)", "Cursor/1.2", ""):
        assert client_type(_request("/mcp", {"User-Agent": agent}), PEPPER) == "mcp_hint"


def test_other_user_agents_keep_their_earlier_kinds():
    cases = {
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36": "browser_hint",
        "python-requests/2.32.3": "agent_or_script_hint", "curl/8.5.0": "agent_or_script_hint",
        "Mozilla/5.0 AppleWebKit/537.36 (compatible; GPTBot/1.1; +https://openai.com/gptbot)": "crawler_hint",
        "": "unknown", "SomethingElse/1.0": "unknown",
    }
    for agent, kind in cases.items():
        assert client_type(_request("/en/", {"User-Agent": agent}), PEPPER) == kind, agent
    assert set(cases.values()) <= set(CLIENT_KINDS)
    assert all(len(kind) <= 24 for kind in CLIENT_KINDS)  # traffic_attribution_daily.client_type is varchar(24)


def test_operator_request_is_one_function_for_every_measurement():
    owner = ("198.51.100.23", "2001:db8::1")
    assert is_operator_request(_request("/", {"CF-Connecting-IP": "198.51.100.23"}), owner)
    assert is_operator_request(_request("/", {"CF-Connecting-IP": " 2001:0db8:0:0:0:0:0:1 "}), owner)  # normalised
    for headers in ({}, {"CF-Connecting-IP": "198.51.100.24"}, {"CF-Connecting-IP": "198.51.100.23, 203.0.113.9"},
                    {"CF-Connecting-IP": "garbage"}, {"X-Forwarded-For": "198.51.100.23"}):
        assert not is_operator_request(_request("/", headers), owner)
    assert not is_operator_request(_request("/", {"CF-Connecting-IP": "198.51.100.23"}), ())  # no list, no operator


def test_attribution_collapses_operator_traffic_only():
    tagged = "/en/agents?utm_source=moltychan&utm_medium=community&utm_campaign=beta_launch"
    owner = {"CF-Connecting-IP": "198.51.100.23"}
    other = {"CF-Connecting-IP": "198.51.100.24"}
    assert attribute_request(_request(tagged, owner), ("198.51.100.23",)) == OPERATOR_ATTRIBUTION
    assert attribute_request(_request(tagged, other), ("198.51.100.23",)) == ("moltychan", "community", "beta_launch", "none")
    assert attribute_request(_request(tagged, owner), ()) == request_source(_request(tagged, owner))


def test_lookup_summary_groups_kinds_and_keeps_the_parts_summing_to_the_total():
    rows = [
        {"client_type": "plugin_hint", "total": 12, "external": 10}, {"client_type": "ahn_client_hint", "total": 3, "external": 3},
        {"client_type": "mcp_hint", "total": 7, "external": 7}, {"client_type": "browser_hint", "total": 2, "external": 1},
        {"client_type": "unknown", "total": 4, "external": 4},
        {"client_type": "agent_or_script_hint", "total": 5, "external": 4}, {"client_type": "crawler_hint", "total": 1, "external": 1},
    ]
    summary = summarize_lookup_kinds(rows)
    assert [(p["label"], p["total"], p["external"]) for p in summary["parts"]] == [
        ("플러그인", 12, 10), ("ahn-client", 3, 3), ("MCP", 7, 7), ("브라우저", 2, 1), ("unknown", 4, 4), ("기타", 6, 5)]
    assert (summary["total"], summary["external"]) == (34, 30)
    empty = summarize_lookup_kinds([])
    assert (empty["total"], empty["external"]) == (0, 0) and len(empty["parts"]) == 6
