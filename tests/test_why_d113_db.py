"""D113 (F4): the second read of GET /v1/why, the lookup of the host as sent when it was answered from its apex, writes nothing:
one /v1/why is one lookup, of the apex. Runs against the dedicated ahn_test database; no real host is read."""

from __future__ import annotations

import os

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)
os.environ.setdefault("AHN_DATABASE_URL", TEST_URL)
os.environ.setdefault("AHN_KEY_PEPPER", "test-pepper-must-be-at-least-thirty-two-chars")

from fastapi.testclient import TestClient  # noqa: E402

from ahn import service, site_probe  # noqa: E402
from ahn.db import connect  # noqa: E402
from ahn.main import create_app  # noqa: E402
from test_site_probe_db import db  # noqa: E402,F401

APEX = "login-fixture-host.com"
SUB = "status." + APEX


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("AHN_SITE_PROBE", "0")

    def no_network(*_a, **_k):
        raise AssertionError("the explanation must not reach out to a host")

    monkeypatch.setattr(site_probe, "fetch_home", no_network)
    return TestClient(create_app(db))


def _state(db):
    with connect(db) as conn:
        return {
            "metrics": {r["site_host"]: r["checks"] for r in conn.execute("SELECT site_host, sum(checks)::int AS checks FROM lookup_metrics GROUP BY 1").fetchall()},
            "queried": sorted(r["site_host"] for r in conn.execute("SELECT site_host FROM queried_hosts").fetchall()),
            "signals": {r["site_host"]: r["n"] for r in conn.execute("SELECT site_host, sum(lookups)::int AS n FROM lookup_signals_hourly GROUP BY 1").fetchall()},
            "jobs": sorted((r["kind"], r["site_host"]) for r in conn.execute("SELECT kind, site_host FROM background_reads").fetchall()),
        }


def test_a_subdomain_why_counts_one_lookup_of_the_apex_only(db, client):
    response = client.get("/v1/why", params={"site_host": SUB, "error_class": "site_access"})
    assert response.status_code == 200 and response.json()["host_normalized"] == "apex"
    state = _state(db)
    assert state["metrics"] == {APEX: 1}
    assert state["signals"] == {APEX: 1}
    assert state["queried"] == [APEX]
    assert all(host != SUB for _kind, host in state["jobs"])


def test_a_second_why_adds_one_more_to_the_apex_and_still_nothing_for_the_subdomain(db, client):
    for _ in range(2):
        assert client.get("/v1/why", params={"site_host": SUB, "error_class": "site_access"}).status_code == 200
    state = _state(db)
    assert state["metrics"] == {APEX: 2} and state["signals"] == {APEX: 2} and state["queried"] == [APEX]


def test_check_without_record_writes_nothing(db):
    service.check(db, APEX, error_class="site_access")
    assert _state(db)["metrics"] == {APEX: 1}  # a recorded lookup of an apex does write
    with connect(db) as conn:
        conn.execute("TRUNCATE lookup_metrics, queried_hosts, lookup_signals_hourly, background_reads RESTART IDENTITY CASCADE")
    quiet = service.check(db, APEX, error_class="site_access", record=False)
    assert _state(db) == {"metrics": {}, "queried": [], "signals": {}, "jobs": []}
    # A read a recorded lookup would have queued is not queued, and the answer says so as it is: not pending.
    assert quiet["recommendation"]["reasons"] == ["robots_unknown"]


def test_for_the_host_the_why_asks_about_the_answer_is_the_same_with_and_without_the_record(db):
    # The F4 read is of a subdomain: nothing is registered or queued for it by a recorded lookup either, so the two answers are one.
    recorded = service.check(db, SUB, error_class="site_access")
    quiet = service.check(db, SUB, error_class="site_access", record=False)
    for answer in (recorded, quiet):
        answer.pop("generated_at")
    assert quiet == recorded


def _root_block(db, **kw):
    """The root domain's own (recorded) lookup, as explain() asks for it and as the route serializes it."""
    from ahn.models import CheckResponse

    answer = service.check(db, APEX, kw.get("error_class"), None, 168, None, None, agent_vendor=None, issue_feedback=False, plain_view=False,
                           fold_rules=True, test_marked=False)
    return CheckResponse.model_validate(answer).model_dump(mode="json")


@pytest.mark.parametrize("host", ["app." + APEX, "status." + APEX])
def test_a_subdomain_why_has_the_root_domains_judgment(db, client, host):
    root = _root_block(db, error_class="site_access")
    body = client.get("/v1/why", params={"site_host": host, "error_class": "site_access"}).json()
    assert body["host_normalized"] == "apex" and body["site_host"] == APEX
    server = body["server"]
    assert server["recommendation"] == root["recommendation"]
    assert server["next_action"]["do"] == root["next_action"]["do"]
    assert server["next_action"]["do"] != "lookup_apex_host" or root["next_action"]["do"] == "lookup_apex_host"
    for key, value in root["next_action"].items():
        if not key.startswith("status_url"):
            assert server["next_action"].get(key) == value, key


def test_the_root_domains_stop_automation_stays_in_a_subdomain_why(db, client, monkeypatch):
    from ahn.models import CheckResponse

    real = service.check

    def fake(settings, host, *args, **kw):
        answer = real(settings, host, *args, **kw)
        if host == APEX:
            answer["recommendation"] = {"action": "stop_declared_block", "confidence": "high", "reasons": ["declared_block"]}
            answer["next_action"] = {"do": "stop_automation"}
        return answer

    monkeypatch.setattr(service, "check", fake)
    body = client.get("/v1/why", params={"site_host": "app." + APEX, "error_class": "site_access"}).json()
    assert body["server"]["recommendation"]["action"] == "stop_declared_block" and body["server"]["next_action"]["do"] == "stop_automation"
