"""D108: GET /v1/why against the dedicated ahn_test database. No real host is read: the home-page check is off and the
background reader is not started."""

from __future__ import annotations

import json
import os

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)
os.environ.setdefault("AHN_DATABASE_URL", TEST_URL)
os.environ.setdefault("AHN_KEY_PEPPER", "test-pepper-must-be-at-least-thirty-two-chars")

from fastapi.testclient import TestClient  # noqa: E402

from ahn import site_probe  # noqa: E402
from ahn.db import connect  # noqa: E402
from ahn.main import create_app  # noqa: E402
from test_site_probe_db import db  # noqa: E402,F401

HOST = "login-fixture-host.com"


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("AHN_SITE_PROBE", "0")

    def no_network(*_a, **_k):
        raise AssertionError("the explanation must not reach out to a host")

    monkeypatch.setattr(site_probe, "fetch_home", no_network)
    return TestClient(create_app(db))  # no `with`: the background reader never starts


def _values(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _values(v)
    elif isinstance(node, list):
        for v in node:
            yield from _values(v)
    else:
        yield node


def test_a_plain_request_answers_with_every_section(client):
    r = client.get("/v1/why", params={"site_host": HOST})
    assert r.status_code == 200
    body = r.json()
    assert body["schema_version"] == "ahn-why/1" and body["site_host"] == HOST and body["lang"] == "en"
    for key in ("where_to_look", "person", "others_saw", "server", "what_happened", "do_now", "timeline", "handoff", "basis_text", "links"):
        assert key in body, key
    assert body["links"]["self"].startswith("/v1/why?site_host=" + HOST)


def _null_paths(node, path=""):
    if node is None:
        yield path
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _null_paths(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _null_paths(v, f"{path}[{i}]")


def test_no_field_is_null_and_no_feedback_token_is_issued(client):
    r = client.get("/v1/why", params={"site_host": HOST, "error_class": "login_auth", "stop_point": "site_login"})
    assert r.status_code == 200
    # `server` is the lookup's own answer, copied; its nulls (next_action.url ...) are the lookup's own, so only the rest is held to
    # "omit instead of null".
    assert [p for p in _null_paths(r.json()) if not p.startswith(".server.")] == [] and "feedback_token" not in r.text
    check = client.get("/v1/incidents/check", params={"site_host": HOST, "error_class": "login_auth", "window_hours": 168}).json()
    assert list(_null_paths(check["next_action"])) == list(_null_paths(r.json()["server"]["next_action"]))


def test_a_pasted_address_is_cut_to_its_host(client):
    r = client.get("/v1/why", params={"site_host": f"https://{HOST}/account?id=SECRET"})
    assert r.status_code == 200
    body = r.json()
    assert body["site_host"] == HOST and body["host_normalized"] == "path_dropped"
    assert "SECRET" not in r.text and "/account" not in r.text


def test_only_the_host_can_make_a_400(client):
    for bad in ("", "not a host", "localhost", "127.0.0.1"):
        r = client.get("/v1/why", params={"site_host": bad})
        assert r.status_code == 400, bad
    r = client.get("/v1/why", params={"site_host": HOST, "agent_platform": "no-such", "stop_point": "x", "error_class": "zzz",
                                      "failure_stage": "zzz", "http_status": "999", "since_kind": "q", "since_date": "garbage",
                                      "lang": "fr"})
    assert r.status_code == 200
    body = r.json()
    assert body["input_echo"] == {} and body["lang"] == "en" and "no-such" not in r.text


def test_the_missing_host_is_a_400(client):
    assert client.get("/v1/why").status_code == 400


def test_head_is_not_answered(client):
    assert client.head("/v1/why", params={"site_host": HOST}).status_code == 405


def test_korean_answer(client):
    body = client.get("/v1/why", params={"site_host": HOST, "lang": "ko", "stop_point": "company_login"}).json()
    assert body["lang"] == "ko" and body["input_echo"] == {"stop_point": "company_login"}
    assert any("가" <= ch <= "힣" for ch in body["where_to_look"]["text"])


def test_the_explanation_is_in_the_openapi_document(client):
    spec = client.get("/openapi.json").json()
    assert "/v1/why" in spec["paths"] and "get" in spec["paths"]["/v1/why"]
    names = {p["name"] for p in spec["paths"]["/v1/why"]["get"]["parameters"]}
    assert {"site_host", "lang", "agent_platform", "agent_env", "external_browser", "stop_point", "screen_message", "since_kind",
            "since_date", "task_context"} <= names


def test_what_a_person_chose_is_stored_nowhere(client, db):
    codes = {"agent_platform": "claude_code", "agent_env": "cloud_browser", "external_browser": "yes_same_account",
             "stop_point": "extra_verification", "screen_message": "claude_asks_each_action", "since_kind": "worked_before",
             "since_date": "2026-11-01", "task_context": "my_account_work"}
    r = client.get("/v1/why", params={"site_host": HOST, **codes})
    assert r.status_code == 200
    needles = list(codes.values()) + ["agent_platform", "stop_point", "screen_message", "task_context", "since_kind"]
    with connect(db) as conn:
        tables = [row["table_name"] for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'").fetchall()]
        dump = []
        for table in tables:
            for row in conn.execute(f'SELECT t::text AS r FROM "{table}" t').fetchall():
                dump.append((table, row["r"]))
    for table, text in dump:
        if table in {"schema_migrations", "migrations"}:
            continue
        for needle in needles:
            assert needle not in text, (table, needle)


def _lookups(db) -> int:
    with connect(db) as conn:
        row = conn.execute("SELECT COALESCE(sum(lookups),0) AS n FROM lookup_signals_hourly WHERE site_host=%s", (HOST,)).fetchone()
    return int(row["n"])


def test_the_explanation_counts_like_one_lookup(client, db):
    before = _lookups(db)
    client.get("/v1/why", params={"site_host": HOST, "error_class": "login_auth"})
    after_why = _lookups(db)
    client.get("/v1/incidents/check", params={"site_host": HOST, "error_class": "login_auth"})
    after_check = _lookups(db)
    assert after_why - before == after_check - after_why


def test_the_response_is_json(client):
    r = client.get("/v1/why", params={"site_host": HOST})
    assert r.headers["content-type"].startswith("application/json")
    json.loads(r.text)
