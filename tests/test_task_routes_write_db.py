"""Task-route write path (D118): POST /v1/task-routes, DELETE /v1/task-routes/{route_id}, report_task_route, review, retention.

Isolated local database tests (AHN_TEST_DATABASE_URL, a database named ahn_test on a throwaway cluster); the reports are invented
and use example names only. Tests that need no database are at the end and run without one.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import subprocess
import sys
from argparse import Namespace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn import bundle, export, rate_limit, task_route_intake, task_routes, task_routes_data  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.settings import Settings  # noqa: E402
from ahn.task_routes import (DocumentKind, EntryPointKind, RouteBasis, RouteDuration, RouteKind, RouteOutcome,  # noqa: E402
                             RouteSourceKind, SiteRequires, TaskKind, TaskObstacle, VerificationKind, is_sensitive, lookup)

ROOT = Path(__file__).resolve().parents[1]
TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
PEPPER = "test-pepper-must-be-at-least-thirty-two-chars"
SINCE = date(2026, 11, 20)
needs_db = pytest.mark.skipif(not TEST_URL, reason="AHN_TEST_DATABASE_URL is required")


def write_settings(url: str = TEST_URL, **changes) -> Settings:
    values = dict(public_mode=True, reporting_enabled=True, task_routes=True, task_routes_since=SINCE, task_route_reports=True,
                  task_route_reports_since=SINCE)
    values.update(changes)
    return Settings(url, PEPPER, **values)


def report(**changes) -> dict:
    body = {"site_host": "example.com", "task_kind": "refund_request", "obstacle": "wrong_channel",
            "routes_tried": ["web_form", "phone"], "route_worked": "phone", "basis": "own_attempt", "outcome": "resolved",
            "idempotency_key": str(uuid4())}
    body.update(changes)
    return {key: value for key, value in body.items() if value is not None}


@pytest.fixture
def unlimited(monkeypatch):
    """The per-client limiter allows a few reports a minute; most tests send more from one test client."""
    async def allow(_self, _request):
        return True
    monkeypatch.setattr(rate_limit.RequestLimiter, "allow", allow)


@pytest.fixture
def db():
    from ahn.db import connect, migrate

    settings = write_settings()
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute("TRUNCATE task_route_submissions, task_routes, task_route_intake_daily, audit_events RESTART IDENTITY CASCADE")
        conn.execute("UPDATE service_flags SET enabled=true WHERE name IN ('public_intake','public_publication')")
    return settings


@pytest.fixture
def api(db, unlimited, monkeypatch):
    monkeypatch.setattr(task_routes_data, "TASK_ROUTES_CURATED", {})
    with TestClient(create_app(db)) as client:
        yield db, client


def rows(settings, sql="SELECT * FROM task_routes ORDER BY received_at", params=()):
    from ahn.db import connect

    with connect(settings) as conn:
        return conn.execute(sql, params).fetchall()


def run(settings, sql, params=()):
    from ahn.db import connect

    with connect(settings) as conn:
        conn.execute(sql, params)


def post(client, body):
    return client.post("/v1/task-routes", json=body)


# --- Intake ----------------------------------------------------------------------------------------------------------------

@needs_db
def test_a_report_is_stored_with_the_values_the_server_sets(api):
    settings, client = api
    response = post(client, report(site_host="www.example.com", task_kind="prescription_refill"))
    assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
    receipt = response.json()
    assert set(receipt) == {"route_id", "review_state", "withdrawal_token"} and receipt["review_state"] == "pending"
    [row] = rows(settings)
    assert row["site_host"] == "example.com"  # kept under the root domain
    assert row["source_kind"] == "external_agent" and row["review_state"] == "pending" and row["official_url"] is None
    assert row["period"] == task_routes.period_for(datetime.now(timezone.utc).date())
    assert row["sensitive"] is is_sensitive("prescription_refill") is True
    assert [event["event_type"] for event in rows(settings, "SELECT event_type FROM audit_events")] == ["task_route_created"]


@needs_db
def test_the_same_key_and_body_is_a_replay_and_a_changed_body_is_a_conflict(api):
    settings, client = api
    body = report()
    first, again = post(client, body), post(client, body)
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json() == again.json() and again.headers["cache-control"] == "no-store"
    conflict = post(client, {**body, "outcome": "progressed"})
    assert conflict.status_code == 409 and conflict.json() == {"error": "idempotency_conflict"}
    assert len(rows(settings)) == 1
    assert rows(settings, "SELECT reports FROM task_route_intake_daily WHERE site_host=''")[0]["reports"] == 1  # a replay takes no slot


@needs_db
def test_the_fifth_report_for_a_site_is_taken_and_the_sixth_is_refused(api):
    settings, client = api
    kinds = ["refund_request", "refund_request", "cancel_subscription", "cancel_subscription", "account_recovery"]
    for kind in kinds:
        assert post(client, report(task_kind=kind)).status_code == 201
    sixth = post(client, report(task_kind="portal_login"))
    assert sixth.status_code == 429 and sixth.json() == {"error": "report_rate_limited"} and sixth.headers["retry-after"] == "3600"
    assert len(rows(settings)) == 5
    # The refused report took no slot anywhere (its transaction was rolled back).
    counts = {(row["site_host"], row["task_kind"]): row["reports"] for row in rows(settings, "SELECT * FROM task_route_intake_daily")}
    assert counts[("", "")] == 5 and counts[("example.com", "")] == 5 and ("example.com", "portal_login") not in counts
    assert post(client, report(site_host="example.org", task_kind="portal_login")).status_code == 201  # another site has its own


@needs_db
def test_the_third_report_for_a_site_and_task_kind_is_taken_and_the_fourth_is_refused(api):
    _settings, client = api
    for _ in range(3):
        assert post(client, report()).status_code == 201
    assert post(client, report()).status_code == 429
    assert post(client, report(task_kind="cancel_subscription")).status_code == 201


@needs_db
def test_www_and_the_root_domain_share_one_count(api):
    settings, client = api
    for host, kind in [("www.example.com", "refund_request"), ("example.com", "refund_request"),
                       ("www.example.com", "refund_request")]:
        assert post(client, report(site_host=host, task_kind=kind)).status_code == 201
    assert post(client, report(site_host="example.com", task_kind="refund_request")).status_code == 429
    for host in ("www.example.com", "example.com"):
        assert post(client, report(site_host=host, task_kind="cancel_subscription")).status_code == 201
    assert post(client, report(site_host="www.example.com", task_kind="account_recovery")).status_code == 429
    assert {row["site_host"] for row in rows(settings)} == {"example.com"}


@needs_db
def test_the_day_cap_is_taken_to_its_last_slot_and_no_further(api):
    settings, client = api
    cap = task_route_intake.TASK_ROUTE_DAILY_CAP
    run(settings, "INSERT INTO task_route_intake_daily (day,site_host,task_kind,reports) VALUES (CURRENT_DATE,'','',%s)", (cap - 1,))
    assert post(client, report()).status_code == 201
    assert post(client, report(site_host="example.org")).status_code == 429


@needs_db
@pytest.mark.parametrize("host", ["springfield.classlink.com", "shop.example.com", "support.example.com", "my.school.example.org"])
def test_a_tenant_or_account_subdomain_is_refused(api, host):
    settings, client = api
    response = post(client, report(site_host=host))
    assert response.status_code == 422 and response.json() == {"error": "site_host_not_accepted"}
    assert host not in response.text and rows(settings) == []
    assert rows(settings, "SELECT * FROM task_route_intake_daily") == []


@needs_db
@pytest.mark.parametrize("field, value", [
    ("source_kind", "operator_panel"), ("source_kind", "external_agent"), ("period", "2026-Q4"), ("sensitive", False),
    ("review_state", "published"), ("official_url", "https://www.example.com/help"), ("source_url", "https://www.example.com/h"),
    ("verified_at", "2026-11-01"), ("note", "free text here"), ("received_at", "2026-11-01T00:00:00Z"),
])
def test_a_report_that_sends_a_server_field_or_any_other_field_is_refused(api, field, value):
    settings, client = api
    response = post(client, report(**{field: value}))
    assert response.status_code == 422 and response.json() == {"error": "invalid_task_route"}
    assert rows(settings) == []


@needs_db
@pytest.mark.parametrize("changes", [
    {"basis": "official_doc"}, {"outcome": "not_applicable"}, {"task_kind": "refund_request_with_my_order_123456"},
    {"obstacle": "verification_step", "document_kind": "proof_of_identity"},
    {"routes_tried": ["web_form"] * 2}, {"route_worked": "email"}, {"site_host": "Example Store <x@y.com>"},
])
def test_an_invalid_report_is_refused_without_echoing_what_was_sent(api, changes):
    settings, client = api
    response = post(client, report(**changes))
    assert response.status_code == 422 and response.json() == {"error": "invalid_task_route"}
    for value in changes.values():
        for part in (value if isinstance(value, list) else [value]):
            assert str(part) not in response.text
    assert rows(settings) == []


@needs_db
def test_a_body_that_is_not_json_is_refused_with_a_code(api):
    _settings, client = api
    secret = "order-123456789-from-a-person"
    response = client.post("/v1/task-routes", content=secret, headers={"content-type": "text/plain"})
    assert response.status_code == 415 and secret not in response.text
    response = client.post("/v1/task-routes", content="{" + secret, headers={"content-type": "application/json"})
    assert response.status_code == 400 and response.json() == {"error": "invalid_request"} and secret not in response.text
    response = client.post("/v1/task-routes", json=[secret])
    assert response.status_code == 422 and secret not in response.text


@needs_db
def test_intake_paused_answers_503_and_withdrawal_still_works(api):
    settings, client = api
    receipt = post(client, report()).json()
    run(settings, "UPDATE service_flags SET enabled=false WHERE name='public_intake'")
    paused = post(client, report())
    assert paused.status_code == 503 and paused.json() == {"error": "reporting_paused"}
    gone = client.delete(f"/v1/task-routes/{receipt['route_id']}", headers={"X-Withdrawal-Token": receipt["withdrawal_token"]})
    assert gone.status_code == 200 and gone.json() == {"withdrawn": True}


@needs_db
def test_the_per_client_limiter_refuses_the_fourth_report_in_a_minute(db, monkeypatch):
    monkeypatch.setattr(task_routes_data, "TASK_ROUTES_CURATED", {})
    with TestClient(create_app(db)) as client:
        kinds = ["refund_request", "cancel_subscription", "account_recovery", "portal_login"]
        codes = [post(client, report(task_kind=kind)).status_code for kind in kinds]
    assert codes == [201, 201, 201, 429]


# --- Review, reads and withdrawal -------------------------------------------------------------------------------------------

def publish(settings, route_id):
    run(settings, "UPDATE task_routes SET review_state='published',reviewed_at=now() WHERE id=%s", (route_id,))


@needs_db
def test_pending_and_held_reports_are_never_shown_and_a_published_one_is(api):
    settings, client = api
    pending = post(client, report()).json()
    held = post(client, report(task_kind="cancel_subscription")).json()
    run(settings, "UPDATE task_routes SET review_state='held' WHERE id=%s", (held["route_id"],))
    answer = client.get("/v1/task-routes", params={"site_host": "example.com"}).json()
    assert answer["routes"] == [] and answer["counts"]["records"] == 0
    assert client.get("/en/routes/example.com").status_code == 404
    publish(settings, pending["route_id"])
    answer = client.get("/v1/task-routes", params={"site_host": "www.example.com"}).json()
    assert [route["task_kind"] for route in answer["routes"]] == ["refund_request"]
    route = answer["routes"][0]
    assert route["source_kind"] == "external_agent" and "official_url" not in route and "received_at" not in route
    assert answer["counts"]["by_source_kind"] == {"external_agent": 1}
    assert client.get("/en/routes/example.com").status_code == 200
    assert client.get("/en/routes/www.example.com", follow_redirects=False).status_code == 301
    assert "example.com" in client.get("/en/routes").text


@needs_db
def test_withdrawal_takes_a_report_out_of_every_answer_at_once(api):
    settings, client = api
    receipt = post(client, report()).json()
    publish(settings, receipt["route_id"])
    assert client.get("/v1/task-routes", params={"site_host": "example.com"}).json()["counts"]["records"] == 1
    url = f"/v1/task-routes/{receipt['route_id']}"
    assert client.delete(url, headers={"X-Withdrawal-Token": "0" * 64}).status_code == 404
    assert client.delete(url).status_code == 404
    gone = client.delete(url, headers={"X-Withdrawal-Token": receipt["withdrawal_token"]})
    assert gone.status_code == 200 and gone.headers["cache-control"] == "no-store"
    assert client.get("/v1/task-routes", params={"site_host": "example.com"}).json()["counts"]["records"] == 0
    assert client.delete(url, headers={"X-Withdrawal-Token": receipt["withdrawal_token"]}).json() == {"withdrawn": True}
    assert client.delete(f"/v1/task-routes/{uuid4()}", headers={"X-Withdrawal-Token": receipt["withdrawal_token"]}).status_code == 404
    assert client.delete("/v1/task-routes/not-a-uuid", headers={"X-Withdrawal-Token": receipt["withdrawal_token"]}).status_code == 404
    events = [row["event_type"] for row in rows(settings, "SELECT event_type FROM audit_events ORDER BY id")]
    assert events == ["task_route_created", "task_route_withdrawn"]


@needs_db
def test_the_review_holds_every_report_while_review_is_manual(api):
    settings, client = api
    post(client, report())
    run(settings, "UPDATE task_routes SET received_at=now() - interval '1 hour'")
    assert task_route_intake.review_task_routes(TEST_URL) == {"published": 0, "held": 1}
    [row] = rows(settings)
    assert row["review_state"] == "pending" and row["review_reason"] == "manual_review"
    assert task_route_intake.review_task_routes(TEST_URL) == {"published": 0, "held": 0}  # reviewed once
    assert client.get("/v1/task-routes", params={"site_host": "example.com"}).json()["routes"] == []


@needs_db
def test_the_rule_review_publishes_only_a_plain_report_and_never_while_publication_is_paused(api, monkeypatch):
    settings, client = api
    monkeypatch.setattr(task_route_intake, "TASK_ROUTE_MANUAL_REVIEW", False)
    post(client, report())
    post(client, report(task_kind="prescription_refill", site_host="example.org"))
    run(settings, "UPDATE task_routes SET received_at=now() - interval '1 hour'")
    run(settings, "UPDATE service_flags SET enabled=false WHERE name='public_publication'")
    assert task_route_intake.review_task_routes(TEST_URL) == {"published": 0, "held": 0}
    run(settings, "UPDATE service_flags SET enabled=true WHERE name='public_publication'")
    assert task_route_intake.review_task_routes(TEST_URL) == {"published": 1, "held": 1}
    states = {row["site_host"]: (row["review_state"], row["review_reason"]) for row in rows(settings)}
    assert states == {"example.com": ("published", "auto_unverified"), "example.org": ("pending", "sensitive")}


@needs_db
def test_curated_and_stored_records_share_one_answer_and_one_site_key(api, monkeypatch):
    settings, client = api
    curated = task_routes_data._route("cancel_subscription", obstacle="wrong_channel", routes=("web_form", "phone"), worked="phone",
                                      official_url="https://www.example.com/help/cancel", source_url="https://www.example.com/help",
                                      verified_at=(datetime.now(timezone.utc).date() - timedelta(days=5)).isoformat())
    monkeypatch.setattr(task_routes_data, "TASK_ROUTES_CURATED", {"example.com": [curated]})
    publish(settings, post(client, report()).json()["route_id"])
    answer = lookup("support.example.com", settings=settings)
    assert answer.site_host == "support.example.com" and answer.counts.records == 2
    assert answer.counts.by_source_kind == {RouteSourceKind.curated_official: 1, RouteSourceKind.external_agent: 1}
    rest = client.get("/v1/task-routes", params={"site_host": "support.example.com"}).json()
    assert rest == json.loads(answer.model_dump_json())
    assert lookup("example.com").counts.records == 1  # without settings: the curated table alone, as before


@needs_db
def test_mcp_find_task_route_reads_the_stored_records_while_the_switch_is_on(api, monkeypatch):
    from mcp.server import MCPServer
    from ahn import mcp_task_routes

    settings, client = api
    publish(settings, post(client, report()).json()["route_id"])
    monkeypatch.setattr(mcp_task_routes, "_settings", lambda: settings)
    server = MCPServer(name="test")
    mcp_task_routes.register(server)
    result = asyncio.run(server.call_tool("find_task_route", {"site_host": "example.com"}))
    rest = client.get("/v1/task-routes", params={"site_host": "example.com"}).json()
    assert result.structured_content == rest and rest["counts"]["records"] == 1


# --- Retention ---------------------------------------------------------------------------------------------------------------

@needs_db
def test_reports_follow_the_report_retention_and_withdrawn_records_the_grace(api):
    from ahn.db import connect

    settings, client = api
    old, fresh, withdrawn = (post(client, report(task_kind=kind)).json()["route_id"]
                             for kind in ("refund_request", "cancel_subscription", "account_recovery"))
    run(settings, "UPDATE task_routes SET received_at=now() - interval '50 days' WHERE id=%s", (old,))
    run(settings, "UPDATE task_routes SET withdrawn_at=now() - interval '31 days' WHERE id=%s", (withdrawn,))
    operator = uuid4()
    run(settings, """INSERT INTO task_routes (id,site_host,task_kind,obstacle,basis,outcome,period,source_kind,sensitive,received_at)
                     VALUES (%s,'example.com','refund_request','wrong_channel','own_attempt','resolved','2026-Q4','operator_panel',false,
                     now() - interval '400 days')""", (operator,))
    with connect(settings) as conn:
        assert task_route_intake.prune_task_route_reports(conn, 0) == 0
        assert task_route_intake.prune_task_route_reports(conn, 45) == 2
    assert {str(row["id"]) for row in rows(settings)} == {fresh, str(operator)}
    assert len(rows(settings, "SELECT * FROM task_route_submissions")) == 1  # the idempotency rows went with their records


@needs_db
def test_prune_analytics_keeps_eight_days_of_intake_counters(db):
    from ahn.platform_metrics import prune_analytics

    for back in (0, 7, 8, 30):
        run(db, "INSERT INTO task_route_intake_daily (day,site_host,task_kind,reports) VALUES (CURRENT_DATE - %s,'example.com','',1)",
            (back,))
    prune_analytics(TEST_URL, 0)
    days = sorted((date.today() - row["day"]).days for row in rows(db, "SELECT day FROM task_route_intake_daily"))
    assert days == [0, 7]


# --- Operator command ---------------------------------------------------------------------------------------------------------

def operator_args(**changes):
    values = dict(route_command="add", site_host="www.example.com", task_kind="cancel_subscription", obstacle="wrong_entry_point",
                  verification_kind=None, site_requires=None, document_kind=None, entry_point_kind="regional_site",
                  route_worked="different_entry_point", duration=None, official_url="https://www.example.com/help/cancel",
                  routes_tried="web_form,different_entry_point", basis="own_attempt", outcome="resolved", source_kind="operator_panel",
                  limit=20, route_id=None)
    values.update(changes)
    return Namespace(**values)


@needs_db
def test_operator_add_publish_and_hold(api, capsys):
    from ahn.cli import _task_route_command

    settings, client = api
    _task_route_command(settings, operator_args(), None)
    route_id = capsys.readouterr().out.splitlines()[-1]
    [row] = rows(settings)
    assert (row["site_host"], row["source_kind"], row["review_state"]) == ("example.com", "operator_panel", "pending")
    assert client.get("/v1/task-routes", params={"site_host": "example.com"}).json()["routes"] == []
    _task_route_command(settings, operator_args(route_command="publish", route_id=route_id), None)
    assert capsys.readouterr().out.splitlines()[-1] == "published"
    [route] = client.get("/v1/task-routes", params={"site_host": "example.com"}).json()["routes"]
    assert route["source_kind"] == "operator_panel" and route["official_url"] == "https://www.example.com/help/cancel"
    _task_route_command(settings, operator_args(route_command="hold", route_id=route_id), None)
    assert capsys.readouterr().out.splitlines()[-1] == "held"
    assert client.get("/v1/task-routes", params={"site_host": "example.com"}).json()["routes"] == []


@needs_db
def test_operator_add_refuses_an_invalid_record_or_a_tenant(api):
    from ahn.cli import _task_route_command

    settings, _client = api
    for changes in ({"site_host": "springfield.classlink.com"}, {"official_url": "https://www.example.com/orders/123456789"},
                    {"basis": "official_doc"}, {"entry_point_kind": None, "verification_kind": "sms_code"}):
        with pytest.raises(SystemExit):
            _task_route_command(settings, operator_args(**changes), None)
    assert rows(settings) == []


# --- Schema ------------------------------------------------------------------------------------------------------------------

def values(enum, *, drop=(), add=()):
    return sorted({item.value for item in enum} - set(drop) | set(add))


CHECKS = {
    "task_routes_task_kind_values": values(TaskKind),
    "task_routes_obstacle_values": values(TaskObstacle),
    "task_routes_verification_kind_values": values(VerificationKind),
    "task_routes_site_requires_values": values(SiteRequires),
    "task_routes_document_kind_values": values(DocumentKind),
    "task_routes_entry_point_kind_values": values(EntryPointKind),
    "task_routes_routes_tried_values": values(RouteKind),
    "task_routes_route_worked_values": values(RouteKind, add=("none",)),
    "task_routes_basis_values": values(RouteBasis, drop=("official_doc",)),
    "task_routes_outcome_values": values(RouteOutcome, drop=("not_applicable",)),
    "task_routes_duration_values": values(RouteDuration),
    "task_routes_source_kind_values": values(RouteSourceKind, drop=("curated_official", "curated_community")),
    "task_route_intake_daily_task_kind_values": values(TaskKind, add=("",)),
}


@needs_db
def test_every_enum_check_constraint_lists_exactly_the_model_values(db):
    found = {row["conname"]: row["def"] for row in rows(db, """SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint
        WHERE conrelid IN ('task_routes'::regclass, 'task_route_intake_daily'::regclass) AND contype='c'""")}
    for name, wanted in CHECKS.items():
        assert name in found, f"no CHECK {name}"
        assert sorted(set(re.findall(r"'([a-z_0-9]*)'", found[name]))) == wanted, name
    assert sorted(set(re.findall(r"'([a-z_]+)'", found["task_routes_review_state_values"]))) == ["held", "pending", "published"]


@needs_db
@pytest.mark.parametrize("column, value", [("task_kind", "unknown_kind"), ("basis", "official_doc"), ("source_kind", "curated_official"),
                                           ("review_state", "hidden"), ("period", "2026-13")])
def test_the_database_refuses_a_value_outside_the_lists(db, column, value):
    import psycopg

    sql = ("INSERT INTO task_routes (id,site_host,task_kind,obstacle,basis,outcome,period,source_kind,sensitive,review_state) "
           "VALUES (%(id)s,'example.com',%(task_kind)s,'wrong_channel',%(basis)s,'resolved',%(period)s,%(source_kind)s,false,%(review_state)s)")
    params = {"id": uuid4(), "task_kind": "refund_request", "basis": "own_attempt", "period": "2026-Q4", "source_kind": "external_agent",
              "review_state": "pending", column: value}
    with pytest.raises(psycopg.errors.CheckViolation):
        run(db, sql, params)


@needs_db
def test_a_public_report_row_cannot_carry_a_link(db):
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        run(db, """INSERT INTO task_routes (id,site_host,task_kind,obstacle,basis,outcome,period,source_kind,sensitive,official_url)
                   VALUES (%s,'example.com','refund_request','wrong_channel','own_attempt','resolved','2026-Q4','external_agent',false,
                   'https://www.example.com/help')""", (uuid4(),))


# --- Without a database --------------------------------------------------------------------------------------------------------

def test_the_new_tables_are_not_in_the_bundle_or_the_export():
    for module in (bundle, export):
        assert "task_route" not in inspect.getsource(module)
    assert bundle.KINDS == ("access_profile", "official_path", "status_source")
    assert export.KINDS == ("access_profile", "status", "observation")
    assert export.BUNDLE_KINDS == ("bundle_meta", "official_path", "status_source")


def test_the_verdict_code_does_not_read_the_new_tables():
    from ahn import service, triage

    for module in (service, triage):
        source = inspect.getsource(module)
        assert "task_route" not in source and "task_routes" not in source


@pytest.mark.parametrize("changes", [
    {"task_route_reports": False}, {"task_route_reports_since": None}, {"task_routes": False}, {"task_routes_since": None},
    {"reporting_enabled": False}, {"public_mode": False}, {"test_mode": True},
])
def test_the_write_paths_are_404_unless_every_switch_is_on(changes):
    client = TestClient(create_app(write_settings("postgresql://invalid/unused", **changes)))  # no `with`: no database
    for response in (client.post("/v1/task-routes", json=report()),
                     client.delete(f"/v1/task-routes/{uuid4()}", headers={"X-Withdrawal-Token": "0" * 64})):
        assert response.status_code == 404 and "invalid_task_route" not in response.text


def policy_page(lang: str, **changes) -> str:
    response = TestClient(create_app(write_settings("postgresql://invalid/unused", **changes))).get(f"/{lang}/policy")
    assert response.status_code == 200
    return response.text


def test_the_write_switch_off_leaves_the_policy_byte_identical():
    for lang in ("en", "ko"):
        off = policy_page(lang, task_route_reports=False, task_route_reports_since=None)
        assert policy_page(lang, task_route_reports_since=None) == off  # the switch without its date is off
        # With public reporting off the write switch is not served: the page is the reporting-off page, byte for byte.
        assert policy_page(lang, reporting_enabled=False) == policy_page(lang, reporting_enabled=False, task_route_reports=False,
                                                                         task_route_reports_since=None)
        on = policy_page(lang)
        assert on != off and "report_task_route" in on and "report_task_route" not in off


def mcp_tools(**env) -> list[str]:
    code = "import asyncio, json; from ahn.mcp_adapter import mcp; print(json.dumps(sorted(t.name for t in asyncio.run(mcp.list_tools()))))"
    full = {**os.environ, "AHN_PUBLIC_MODE": "1", "AHN_REPORTING_ENABLED": "1", "AHN_TASK_ROUTES": "1",
            "AHN_TASK_ROUTES_SINCE": SINCE.isoformat(), "AHN_TASK_ROUTE_REPORTS": "1", "AHN_TASK_ROUTE_REPORTS_SINCE": SINCE.isoformat(),
            "AHN_TEST_MODE": "0", **env}
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=full, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_report_task_route_is_registered_only_when_every_switch_is_on():
    assert "report_task_route" in mcp_tools()
    for env in ({"AHN_TASK_ROUTE_REPORTS": " 0 "}, {"AHN_TASK_ROUTE_REPORTS_SINCE": "not-a-date"}, {"AHN_TASK_ROUTES": "0"},
                {"AHN_REPORTING_ENABLED": "0"}):
        assert "report_task_route" not in mcp_tools(**env), env
    assert "report_task_route" in mcp_tools(AHN_TASK_ROUTE_REPORTS=" 1 ")  # spaces are ignored, as for the read switch


def test_report_task_route_checks_the_report_and_sends_only_the_report_fields():
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from ahn import mcp_task_routes

    sent = []

    async def call_api(method, path, *, payload=None, write=False, **_kw):
        sent.append((method, path, payload, write))
        return {"route_id": str(uuid4()), "review_state": "pending", "withdrawal_token": "a" * 64}

    server = MCPServer(name="test")
    mcp_task_routes.register_reports(server, call_api)
    body = report()
    result = asyncio.run(server.call_tool("report_task_route", {"report": body}))
    assert result.structured_content["review_state"] == "pending"
    [(method, path, payload, write)] = sent
    assert (method, path, write) == ("POST", "/v1/task-routes", True) and payload == body
    secret = "my order 123456789"
    for bad in ({**body, "source_kind": "external_agent"}, {**body, "official_url": "https://www.example.com/x"},
                {**body, "task_kind": secret}, {**body, "note": secret}, secret):
        with pytest.raises(ToolError) as caught:
            asyncio.run(server.call_tool("report_task_route", {"report": bad}))
        assert "invalid_task_route" in str(caught.value) and secret not in str(caught.value)
    assert len(sent) == 1


def test_the_lookup_answers_from_the_curated_table_when_the_database_cannot_be_read(monkeypatch, caplog):
    curated = task_routes_data._route("cancel_subscription", obstacle="wrong_channel", routes=("web_form", "phone"), worked="phone",
                                      official_url="https://www.example.com/help/cancel", source_url="https://www.example.com/help",
                                      verified_at=(datetime.now(timezone.utc).date() - timedelta(days=5)).isoformat())
    monkeypatch.setattr(task_routes_data, "TASK_ROUTES_CURATED", {"example.com": [curated]})

    def down(*_args, **_kwargs):
        raise OSError("database unavailable")

    monkeypatch.setattr(task_route_intake, "published_rows", down)
    with caplog.at_level(logging.WARNING, logger="ahn.task_routes"):
        answer = lookup("example.com", settings=write_settings("postgresql://invalid/unused"))
    assert answer.counts.records == 1 and "task_route_db_unavailable" in caplog.text
    client = TestClient(create_app(write_settings("postgresql://invalid/unused")))
    assert client.get("/v1/task-routes", params={"site_host": "example.com"}).json()["counts"]["records"] == 1
