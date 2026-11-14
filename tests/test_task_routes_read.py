"""Task routes read API and MCP tool (PR-B): GET /v1/task-routes and find_task_route, behind AHN_TASK_ROUTES (default off).

No database and no network. The curated table is replaced with monkeypatch, so the tests do not depend on what the real table holds.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn import task_routes, task_routes_data
from ahn.main import create_app
from ahn.settings import Settings, task_routes_env_enabled
from ahn.task_routes import (ROUTE_LINE_NOTICE, SOURCE_LABELS, SUMMARY_MAX, TASK_ROUTE_ENUMS, TASK_ROUTES_MAX, RouteSourceKind,
                             TaskKind, lookup, summary_line)
from ahn.task_routes_data import TASK_ROUTE_MAX_AGE_DAYS, _route

ROOT = Path(__file__).resolve().parents[1]
TODAY = datetime.now(timezone.utc).date()
HOST = "example-retailer.com"
SINCE = date(2026, 11, 20)
PAGE = "https://www.example-retailer.com/help/cancel"
HELP = "https://www.example-retailer.com/help"


def days_ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


def entry(task_kind="cancel_subscription", *, obstacle="wrong_channel", verified=10, routes=("web_form", "phone"), worked="phone",
          **more):
    return _route(task_kind, obstacle=obstacle, routes=routes, worked=worked, official_url=PAGE, source_url=HELP,
                  verified_at=days_ago(verified), **more)


def client(task_routes_on: bool) -> TestClient:
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True, task_routes=task_routes_on,
                        task_routes_since=SINCE if task_routes_on else None)
    return TestClient(create_app(settings))  # no `with`: the lifespan (database) never runs


@pytest.fixture
def on():
    return client(True)


def table(monkeypatch, data):
    monkeypatch.setattr(task_routes_data, "TASK_ROUTES_CURATED", data)


# --- Switch off -------------------------------------------------------------------------------------------------------------

def tools(task_routes_flag: str | None, reporting: str = "1") -> dict:
    code = ("import asyncio, json; from ahn.mcp_adapter import mcp; "
            "print(json.dumps({t.name: {'input_schema': t.input_schema, 'annotations': t.annotations.model_dump(), "
            "'output_schema': t.output_schema} for t in asyncio.run(mcp.list_tools())}))")
    env = {**os.environ, "AHN_PUBLIC_MODE": "1", "AHN_REPORTING_ENABLED": reporting}
    env.pop("AHN_TASK_ROUTES", None)
    env.pop("AHN_TASK_ROUTES_SINCE", None)
    if task_routes_flag is not None:
        env["AHN_TASK_ROUTES"] = task_routes_flag
        env["AHN_TASK_ROUTES_SINCE"] = SINCE.isoformat()
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_switch_off_the_path_does_not_exist(monkeypatch):
    table(monkeypatch, {HOST: [entry()]})
    off = client(False)
    assert off.get("/v1/task-routes", params={"site_host": HOST}).status_code == 404
    assert off.get("/v1/task-routes").status_code == 404


@pytest.mark.parametrize("flag", [None, "", "0", "true"])
def test_switch_off_the_tool_list_is_the_public_snapshot(flag):
    current = tools(flag)
    assert "find_task_route" not in current
    snapshot = json.loads((ROOT / "tests" / "fixtures" / "mcp" / "tool_schemas_public.json").read_text(encoding="utf-8"))
    assert {name: {key: value[key] for key in ("input_schema", "annotations", "output_schema")} for name, value in current.items()} == snapshot


def test_switch_off_the_policy_does_not_mention_task_routes():
    for lang in ("en", "ko"):
        text = client(False).get(f"/{lang}/policy").text
        assert "find_task_route" not in text and "/v1/task-routes" not in text


def test_settings_read_the_switch_from_the_environment(monkeypatch):
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://invalid/unused")
    monkeypatch.setenv("AHN_KEY_PEPPER", "x" * 32)
    monkeypatch.setenv("AHN_TASK_ROUTES_SINCE", "2026-11-20")
    for value, expected in (("", False), ("0", False), ("true", False), ("1", True), (" 1 ", True), ("1\t", True)):
        monkeypatch.setenv("AHN_TASK_ROUTES", value)
        settings = Settings.from_env()
        assert settings.task_routes is expected
        assert settings.task_routes_since == (SINCE if expected else None)
        assert task_routes_env_enabled() is expected  # the MCP adapter reads the switch the same way
    monkeypatch.delenv("AHN_TASK_ROUTES")
    assert Settings.from_env().task_routes is False and task_routes_env_enabled() is False


@pytest.mark.parametrize("since", [None, "", "soon", "2026-13-01"])
def test_the_switch_without_a_valid_since_date_turns_nothing_on(monkeypatch, since):
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://invalid/unused")
    monkeypatch.setenv("AHN_KEY_PEPPER", "x" * 32)
    monkeypatch.setenv("AHN_TASK_ROUTES", "1")
    if since is None:
        monkeypatch.delenv("AHN_TASK_ROUTES_SINCE", raising=False)
    else:
        monkeypatch.setenv("AHN_TASK_ROUTES_SINCE", since)
    assert task_routes_env_enabled() is False  # the MCP adapter lists no tool
    with pytest.raises(RuntimeError, match="AHN_TASK_ROUTES_SINCE"):  # as AHN_PUBLIC_BOARD and AHN_PUBLIC_BUNDLE refuse to start
        Settings.from_env()
    # Built directly (a test, or a caller that skips from_env), the app behaves as if the switch were off.
    app_client = TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, task_routes=True)))
    assert app_client.get("/v1/task-routes", params={"site_host": HOST}).status_code == 404
    assert "find_task_route" not in app_client.get("/en/policy").text


def policy_page(**options) -> str:
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True, **options)
    return " ".join(TestClient(create_app(settings)).get("/en/policy").text.split())


def test_the_policy_date_follows_the_since_date_only_while_task_routes_are_on():
    from ahn.main import POLICY_UPDATED

    base = policy_page()
    assert policy_page(task_routes=True) == base  # no SINCE: the page of the switch off, character for character
    assert policy_page(task_routes_since=SINCE) == base  # a date alone turns nothing on
    moved = POLICY_UPDATED + timedelta(days=30)
    later = policy_page(task_routes=True, task_routes_since=moved)
    assert f"Last updated {moved:%B} {moved.day}, {moved.year}." in later
    assert f"Last updated {moved:%B} {moved.day}, {moved.year}." not in base
    earlier = policy_page(task_routes=True, task_routes_since=POLICY_UPDATED - timedelta(days=30))
    assert f"Last updated {POLICY_UPDATED:%B} {POLICY_UPDATED.day}, {POLICY_UPDATED.year}." in earlier  # never moves back


# --- Switch on: the REST answer ---------------------------------------------------------------------------------------------

def test_apex_www_and_subdomain_find_the_apex_entry(monkeypatch, on):
    table(monkeypatch, {HOST: [entry()]})
    for host in (HOST, "www." + HOST, "help.shop." + HOST, "WWW." + HOST.upper() + "."):
        response = on.get("/v1/task-routes", params={"site_host": host})
        assert response.status_code == 200, host
        body = response.json()
        assert body["counts"]["records"] == 1, host
        assert body["routes"][0]["site_host"] == HOST
        assert body["routes"][0]["official_url"] == PAGE
    assert response.headers["cache-control"] == "public, max-age=300"


def test_an_unrelated_host_gets_an_empty_answer_not_an_error(monkeypatch, on):
    table(monkeypatch, {HOST: [entry()]})
    for host in ("other-example.org", "notexample-retailer.com", HOST + ".evil.org"):
        response = on.get("/v1/task-routes", params={"site_host": host})
        assert response.status_code == 200
        body = response.json()
        assert body["routes"] == [] and body["counts"] == {"records": 0, "by_source_kind": {}}
        assert body["site_host"] == host and "notice" not in body
        assert body["summary_line"] == task_routes.SUMMARY_NO_RECORDS
    assert on.get("/v1/task-routes", params={"site_host": "other-example.org", "task_kind": "refund_request"}).json()["summary_line"] == \
        task_routes.SUMMARY_NO_RECORDS_FOR_KIND


def test_a_tenant_subdomain_gets_the_empty_answer_and_a_generic_one_gets_the_apex_records(monkeypatch, on):
    item = {**entry(), "source_url": "https://www.classlink.com/help", "official_url": "https://www.classlink.com/help/x"}
    table(monkeypatch, {"classlink.com": [item], HOST: [entry()]})
    apex = on.get("/v1/task-routes", params={"site_host": "classlink.com"}).json()
    assert apex["counts"]["records"] == 1
    for tenant in ("springfield.classlink.com", "x.springfield.classlink.com", "support.springfield.classlink.com"):
        body = on.get("/v1/task-routes", params={"site_host": tenant}).json()
        assert body["routes"] == [] and body["counts"]["records"] == 0, tenant
        assert body["summary_line"] == task_routes.SUMMARY_NO_RECORDS
    for generic in ("www.classlink.com", "support.classlink.com", "help.classlink.com", "support.help.classlink.com"):
        body = on.get("/v1/task-routes", params={"site_host": generic}).json()
        assert body["routes"] == apex["routes"] and body["counts"] == apex["counts"], generic
    # A two-label public suffix works as it does elsewhere: the apex is three labels.
    shop = {**entry(), "source_url": "https://www.example-shop.co.uk/help", "official_url": "https://www.example-shop.co.uk/help/x"}
    table(monkeypatch, {"example-shop.co.uk": [shop]})
    assert on.get("/v1/task-routes", params={"site_host": "support.example-shop.co.uk"}).json()["counts"]["records"] == 1
    assert on.get("/v1/task-routes", params={"site_host": "tenant.example-shop.co.uk"}).json()["counts"]["records"] == 0


def test_the_empty_table_answers_200_with_no_records(on):
    body = on.get("/v1/task-routes", params={"site_host": HOST}).json()
    assert body["routes"] == [] and body["counts"]["records"] == 0


def test_an_entry_older_than_ninety_days_is_left_out(monkeypatch):
    table(monkeypatch, {HOST: [entry(verified=TASK_ROUTE_MAX_AGE_DAYS), entry("refund_request", verified=TASK_ROUTE_MAX_AGE_DAYS + 1),
                               entry("return_or_exchange", verified=-1)]})
    answer = lookup(HOST, today=TODAY)
    assert [route.task_kind.value for route in answer.routes] == ["cancel_subscription"]  # 90 days old stays; 91 and a future date go
    assert answer.counts.records == 1
    later = lookup(HOST, today=TODAY + timedelta(days=1))
    assert [route.task_kind.value for route in later.routes] == ["return_or_exchange"]  # the 90-day entry aged out; the dated-tomorrow one is now today's


def test_task_kind_filter(monkeypatch, on):
    table(monkeypatch, {HOST: [entry(), entry("refund_request", obstacle="document_missing", routes=(), worked=None,
                                              document_kind="proof_of_purchase")]})
    both = on.get("/v1/task-routes", params={"site_host": HOST}).json()
    assert both["counts"]["records"] == 2
    one = on.get("/v1/task-routes", params={"site_host": HOST, "task_kind": "refund_request"}).json()
    assert [route["task_kind"] for route in one["routes"]] == ["refund_request"] and one["counts"]["records"] == 1
    assert one["routes"][0]["document_kind"] == "proof_of_purchase"
    none = on.get("/v1/task-routes", params={"site_host": HOST, "task_kind": "prescription_refill"}).json()
    assert none["routes"] == [] and none["counts"]["records"] == 0


@pytest.mark.parametrize("params,error", [
    ({}, "invalid_site_host"), ({"site_host": ""}, "invalid_site_host"), ({"site_host": "not a host"}, "invalid_site_host"),
    ({"site_host": "localhost"}, "invalid_site_host"), ({"site_host": "127.0.0.1"}, "invalid_site_host"),
    ({"site_host": "a" * 300 + ".com"}, "invalid_site_host"),
    ({"site_host": HOST, "task_kind": "not_a_kind"}, "invalid_task_kind"), ({"site_host": HOST, "task_kind": ""}, "invalid_task_kind"),
    ({"site_host": HOST, "task_kind": "Refund_Request"}, "invalid_task_kind"),
    ({"site_host": HOST, "task_kind": "<script>alert(1)</script>"}, "invalid_task_kind"),
])
def test_bad_input_is_a_400_code_that_never_echoes_the_input(on, params, error):
    response = on.get("/v1/task-routes", params=params)
    assert response.status_code == 400
    assert response.json() == {"error": error}


def test_records_are_newest_period_first_and_capped_at_twenty(monkeypatch, on):
    kinds = [kind for kind in TaskKind if kind != TaskKind.other][:12]
    entries = [entry(kind.value, verified=days, routes=(), worked=None, obstacle="eligibility_unclear")
               for kind in kinds for days in (5, 80)]
    assert len(entries) == 24
    table(monkeypatch, {HOST: entries})
    body = on.get("/v1/task-routes", params={"site_host": HOST}).json()
    periods = [route["period"] for route in body["routes"]]
    assert len(periods) == TASK_ROUTES_MAX == 20
    assert periods == sorted(periods, reverse=True)
    assert body["counts"]["records"] == 24 and body["counts"]["by_source_kind"] == {"curated_official": 24}
    assert f"Showing the {TASK_ROUTES_MAX} newest." in body["summary_line"]
    assert len(set((route["task_kind"], route["verified_at"]) for route in body["routes"])) == 20


def test_the_newest_record_comes_first_within_a_period(monkeypatch):
    table(monkeypatch, {HOST: [entry("refund_request", verified=30), entry("cancel_subscription", verified=3)]})
    answer = lookup(HOST, today=TODAY)
    assert answer.routes[0].verified_at >= answer.routes[-1].verified_at
    assert [route.period for route in answer.routes] == sorted((route.period for route in answer.routes), reverse=True)


def test_counts_by_source_kind_and_the_summary_line(monkeypatch, on):
    table(monkeypatch, {HOST: [entry(), entry("refund_request", obstacle="eligibility_unclear", routes=(), worked=None)]})
    body = on.get("/v1/task-routes", params={"site_host": HOST}).json()
    assert body["counts"] == {"records": 2, "by_source_kind": {"curated_official": 2}}
    assert body["summary_line"] == "2 task route records: 2 from the site's own documents. Records, not guarantees."
    single = on.get("/v1/task-routes", params={"site_host": HOST, "task_kind": "cancel_subscription"}).json()
    assert single["summary_line"] == ("1 task route record for cancel subscription: 1 from the site's own documents. "
                                      "Records, not guarantees.")


# --- Switch on: values the server decides -----------------------------------------------------------------------------------

def test_sensitive_is_computed_by_the_server_whatever_the_entry_says(monkeypatch, on):
    risky = entry("billing_dispute", obstacle="eligibility_unclear", routes=(), worked=None)
    risky["sensitive"] = False  # an input value the answer must not trust
    plain = entry("cancel_subscription")
    plain["sensitive"] = True  # nor in the other direction
    table(monkeypatch, {HOST: [risky, plain]})
    body = on.get("/v1/task-routes", params={"site_host": HOST}).json()
    by_kind = {route["task_kind"]: route for route in body["routes"]}
    assert by_kind["billing_dispute"]["sensitive"] is True
    assert by_kind["cancel_subscription"]["sensitive"] is False
    assert body["notice"] == "case_record_not_advice"
    assert body["summary_line"].endswith(" " + ROUTE_LINE_NOTICE)
    only_plain = on.get("/v1/task-routes", params={"site_host": HOST, "task_kind": "cancel_subscription"}).json()
    assert "notice" not in only_plain and ROUTE_LINE_NOTICE not in only_plain["summary_line"]


@pytest.mark.parametrize("detail", [
    {"task_kind": "refund_request", "obstacle": "site_requires_info_from_person", "site_requires": "referral_or_prescription"},
    {"task_kind": "document_submission", "obstacle": "document_rejected", "document_kind": "professional_letter"},
    {"task_kind": "portal_login", "obstacle": "wrong_entry_point", "entry_point_kind": "role_specific_portal"},
    {"task_kind": "complaint_filing", "obstacle": "wrong_channel"},
])
def test_every_sensitive_rule_marks_the_answer(monkeypatch, detail):
    item = entry(detail.pop("task_kind"), routes=(), worked=None, **detail)
    item["sensitive"] = False
    table(monkeypatch, {HOST: [item]})
    answer = lookup(HOST, today=TODAY)
    assert answer.routes[0].sensitive is True and answer.notice == "case_record_not_advice"


def test_a_government_suffix_marks_the_answer(monkeypatch):
    item = entry("refund_request", routes=(), worked=None, obstacle="eligibility_unclear")
    item = {**item, "source_url": "https://www.example-agency.gov/help", "official_url": "https://www.example-agency.gov/help/refund",
            "sensitive": False}
    table(monkeypatch, {"example-agency.gov": [item]})
    answer = lookup("www.example-agency.gov", today=TODAY)
    assert answer.routes[0].sensitive is True and answer.notice == "case_record_not_advice"


def test_source_kind_period_basis_and_outcome_are_the_servers_not_the_entrys(monkeypatch):
    item = entry(verified=10)
    item.update({"source_kind": "external_agent", "period": "1999-Q1", "basis": "own_attempt", "outcome": "resolved",
                 "sensitive": False, "site_host": "attacker.example"})
    table(monkeypatch, {HOST: [item]})
    route = lookup(HOST, today=TODAY).routes[0]
    assert route.source_kind == RouteSourceKind.curated_official
    assert route.period == task_routes.period_for(date.fromisoformat(days_ago(10)))
    assert route.basis.value == "official_doc" and route.outcome.value == "not_applicable"
    assert route.site_host == HOST


def test_one_bad_table_entry_does_not_take_the_answer_down(monkeypatch, caplog):
    broken = [{**entry(), "obstacle": "no_such_obstacle"}, {**entry(), "unknown_field": "x"}, {**entry(), "verified_at": "2026-13-45"},
              {k: v for k, v in entry().items() if k != "task_kind"}]
    table(monkeypatch, {HOST: [*broken, entry("refund_request", obstacle="eligibility_unclear", routes=(), worked=None)]})
    answer = lookup(HOST, today=TODAY)
    assert [route.task_kind.value for route in answer.routes] == ["refund_request"]


# --- Switch on: what the answer may contain ---------------------------------------------------------------------------------

ENUM_VALUES = {item.value for enum in TASK_ROUTE_ENUMS.values() for item in enum} | {"none", "case_record_not_advice"}


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def test_every_string_in_an_answer_is_an_enum_value_a_host_an_https_url_a_period_a_date_or_the_summary_line(monkeypatch, on):
    risky = entry("billing_dispute", obstacle="document_rejected", document_kind="sworn_statement")
    table(monkeypatch, {HOST: [entry(), risky, entry("portal_login", obstacle="verification_step", verification_kind="sms_code",
                                                    routes=("official_app",), worked="official_app")]})
    body = on.get("/v1/task-routes", params={"site_host": "www." + HOST}).json()
    allowed_hosts = {HOST, "www." + HOST}
    assert body["routes"]
    for text in _strings({key: value for key, value in body.items() if key != "summary_line"}):
        assert (text in ENUM_VALUES or text in allowed_hosts or re.fullmatch(r"https://[^\s?#]+", text)
                or re.fullmatch(r"\d{4}-Q[1-4]", text) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", text)), text
    for route in body["routes"]:
        assert set(route) <= {"site_host", "task_kind", "obstacle", "verification_kind", "site_requires", "document_kind",
                              "entry_point_kind", "routes_tried", "route_worked", "basis", "outcome", "duration", "period",
                              "source_kind", "sensitive", "official_url", "source_url", "verified_at"}
    assert set(body) <= {"site_host", "summary_line", "routes", "counts", "notice"}
    assert not re.search(r"https?://|www\.", body["summary_line"])


def test_summary_lines_are_ascii_short_and_name_no_host_or_url():
    kinds = list(RouteSourceKind)
    assert set(SOURCE_LABELS) == set(kinds), "every source kind needs a label"
    worst = {kind: 99 for kind in kinds}
    for task_kind in [None, *TaskKind]:
        for total, by_source in ((0, {}), (1, {kinds[0]: 1}), (99, worst)):
            for notice in (False, True):
                line = summary_line(total, by_source, task_kind, notice)
                assert re.fullmatch(r"[ -~]+", line) and len(line) <= SUMMARY_MAX, line
                assert not re.search(r"https?:|/", line)
                assert not re.search(r"\b(must|should|always|never|guarantee[ds]?|verified|trusted)\b", line.replace("not guarantees", ""))


def test_a_sensitive_answer_is_marked_in_the_json_and_the_line_only_through_fixed_words(monkeypatch):
    table(monkeypatch, {HOST: [entry("billing_dispute", obstacle="fee_required", routes=(), worked=None)]})
    answer = lookup(HOST, "billing_dispute", TODAY)
    assert answer.notice == "case_record_not_advice"
    assert answer.summary_line == ("1 task route record for billing dispute: 1 from the site's own documents. Records, not guarantees. "
                                   "Case record, not advice.")


# --- Switch on: the MCP tool ------------------------------------------------------------------------------------------------

def mcp_with_tool():
    from mcp.server import MCPServer
    from ahn.mcp_task_routes import register

    server = MCPServer(name="test")
    register(server)
    return server


def test_find_task_route_returns_the_line_first_and_json_second_with_urls_only_in_the_json(monkeypatch):
    table(monkeypatch, {HOST: [entry()]})
    server = mcp_with_tool()
    result = asyncio.run(server.call_tool("find_task_route", {"site_host": "www." + HOST, "task_kind": "cancel_subscription"}))
    assert [block.type for block in result.content] == ["text", "text"]
    first, second = (block.text for block in result.content)
    answer = json.loads(second)
    assert first == answer["summary_line"] and "http" not in first
    assert PAGE in second and PAGE not in first
    assert result.structured_content == answer
    assert answer["counts"]["records"] == 1 and answer["routes"][0]["task_kind"] == "cancel_subscription"


def test_find_task_route_with_no_record_and_with_an_empty_task_kind(monkeypatch):
    table(monkeypatch, {})
    server = mcp_with_tool()
    for arguments in ({"site_host": HOST}, {"site_host": HOST, "task_kind": None}):
        result = asyncio.run(server.call_tool("find_task_route", arguments))
        assert result.content[0].text == task_routes.SUMMARY_NO_RECORDS
        assert result.structured_content["routes"] == []


# One list of queries, answered by both entry points: the same answer or the same error code.
PARITY = [
    ({"site_host": HOST}, None), ({"site_host": "WWW." + HOST.upper() + "."}, None), ({"site_host": "https://" + HOST + "/"}, None),
    ({"site_host": " " + HOST + " "}, None), ({"site_host": "support." + HOST}, None),
    ({"site_host": HOST, "task_kind": "cancel_subscription"}, None), ({"site_host": HOST, "task_kind": "refund_request"}, None),
    ({"site_host": ""}, "invalid_site_host"), ({"site_host": "not a host zzqq"}, "invalid_site_host"),
    ({"site_host": "localhost"}, "invalid_site_host"), ({"site_host": "127.0.0.1"}, "invalid_site_host"),
    ({"site_host": HOST + "/path?q=zzqq"}, "invalid_site_host"),
    ({"site_host": HOST, "task_kind": ""}, "invalid_task_kind"), ({"site_host": HOST, "task_kind": "nopezzqq"}, "invalid_task_kind"),
    ({"site_host": HOST, "task_kind": "Cancel_Subscription"}, "invalid_task_kind"),
]


@pytest.mark.parametrize("arguments,error", PARITY)
def test_rest_and_mcp_give_the_same_answer_or_the_same_error_for_the_same_input(monkeypatch, on, arguments, error):
    from mcp.server.mcpserver.exceptions import ToolError

    table(monkeypatch, {HOST: [entry(), entry("refund_request", obstacle="eligibility_unclear", routes=(), worked=None)]})
    rest = on.get("/v1/task-routes", params=arguments)
    server = mcp_with_tool()
    if error is None:
        result = asyncio.run(server.call_tool("find_task_route", arguments))
        assert rest.status_code == 200 and rest.json() == result.structured_content
        assert rest.json()["counts"]["records"] >= 1
    else:
        assert rest.status_code == 400 and rest.json() == {"error": error}
        with pytest.raises(ToolError) as raised:
            asyncio.run(server.call_tool("find_task_route", arguments))
        assert error in str(raised.value)
        for text in (rest.text, str(raised.value)):  # neither echoes what was sent
            assert "zzqq" not in text and HOST not in text


@pytest.mark.parametrize("arguments", [{"site_host": HOST, "task_kind": 5}, {"site_host": HOST, "task_kind": {"a": 1}},
                                       {"site_host": HOST, "task_kind": ["refund_request"]}])
def test_find_task_route_refuses_a_task_kind_that_is_not_a_string_with_the_code_only(arguments):
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as error:
        asyncio.run(mcp_with_tool().call_tool("find_task_route", arguments))
    assert "invalid_task_kind" in str(error.value) and "refund" not in str(error.value)


def test_the_tool_sees_a_tenant_subdomain_as_the_rest_path_does(monkeypatch, on):
    table(monkeypatch, {"classlink.com": [{**entry(), "source_url": "https://www.classlink.com/help", "official_url": "https://www.classlink.com/help/x"}]})
    result = asyncio.run(mcp_with_tool().call_tool("find_task_route", {"site_host": "springfield.classlink.com"}))
    assert result.structured_content["routes"] == []
    assert on.get("/v1/task-routes", params={"site_host": "springfield.classlink.com"}).json() == result.structured_content


def test_find_task_route_is_read_only_and_every_argument_is_described():
    listed = {tool.name: tool for tool in asyncio.run(mcp_with_tool().list_tools())}["find_task_route"]
    hints = listed.annotations.model_dump()
    assert hints["read_only_hint"] is True and hints["destructive_hint"] is False and hints["idempotent_hint"] is True
    assert hints["open_world_hint"] is False
    schema = listed.input_schema
    assert schema["required"] == ["site_host"] and sorted(schema["properties"]) == ["site_host", "task_kind"]
    assert all(prop.get("description") for prop in schema["properties"].values())
    assert schema["properties"]["task_kind"]["enum"] == [item.value for item in TaskKind]


# --- Switch on: the tool list and its schema snapshot -----------------------------------------------------------------------

SNAPSHOT = ROOT / "tests" / "fixtures" / "mcp" / "tool_schemas_public_task_routes.json"


def test_switch_on_adds_only_find_task_route_and_the_snapshot_matches():
    off, on_ = tools(None), tools("1")
    assert set(on_) - set(off) == {"find_task_route"} and set(off) <= set(on_)
    for name in off:  # the other tools are byte-identical
        assert on_[name] == off[name]
    current = {name: {key: value[key] for key in ("input_schema", "annotations", "output_schema")} for name, value in on_.items()}
    assert current == json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    advertised = on_["find_task_route"]["output_schema"]
    assert advertised.get("additionalProperties") is True  # opened like every other output schema


def test_the_server_instructions_do_not_change_with_the_switch():
    code = "from ahn.mcp_adapter import mcp; print(mcp.instructions)"
    outputs = []
    for flag in (None, "1"):
        env = {**os.environ, "AHN_PUBLIC_MODE": "1", "AHN_REPORTING_ENABLED": "1"}
        env.pop("AHN_TASK_ROUTES", None)
        if flag:
            env["AHN_TASK_ROUTES"] = flag
        done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stderr
        outputs.append(done.stdout)
    assert outputs[0] == outputs[1] and "find_task_route" not in outputs[0]


# --- The verdict is untouched -----------------------------------------------------------------------------------------------

def test_the_verdict_modules_do_not_know_task_routes():
    for name in ("service.py", "triage.py", "models.py", "response_guide.py", "cause.py", "official_paths.py"):
        text = (ROOT / "ahn" / name).read_text(encoding="utf-8")
        assert "task_routes" not in text and "task_route" not in text, name
