"""D108 decision invariance: the explanation never changes what GET /v1/incidents/check says, and its `server` object is that
answer's own words and decision, byte for byte."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)
os.environ.setdefault("AHN_DATABASE_URL", TEST_URL)
os.environ.setdefault("AHN_KEY_PEPPER", "test-pepper-must-be-at-least-thirty-two-chars")

from fastapi.testclient import TestClient  # noqa: E402

from ahn import site_probe  # noqa: E402
from ahn.main import create_app  # noqa: E402
from test_cause_db import hourly, observation, seed  # noqa: E402
from test_site_probe_db import Home, db, measure, read_profile  # noqa: E402,F401

HOST = "login-fixture-host.com"
SAME = ("summary_line", "recommendation", "next_action", "cause", "failure_stage", "next_checks")
# Each row is one fixture: the codes a caller measured and sent.
FIXTURES = [
    {},
    {"error_class": "login_auth", "task_step": "login"},
    {"error_class": "human_verification", "task_step": "human_verification"},
    {"error_class": "service_error", "http_status": "503"},
    {"error_class": "site_access", "failure_stage": "access_denial_observed", "http_status": "403", "challenge": "header"},
    {"error_class": "site_access", "http_status": "429", "retry_after": "60_3600"},
    {"error_class": "service_error", "failure_stage": "before_target_response", "via": "browser"},
    {"error_class": "login_auth", "agent_vendor": "anthropic"},
]
# The same lookups by what a person chose; the mapping fills the codes the lookup understands.
SYMPTOMS = [
    ({"stop_point": "company_login"}, {"error_class": "login_auth", "task_step": "login"}),
    ({"stop_point": "human_verification"}, {"error_class": "human_verification", "task_step": "human_verification"}),
    ({"stop_point": "timeout_or_5xx"}, {"error_class": "service_error", "task_step": "other"}),
    ({"stop_point": "page_blank_or_blocked", "screen_message": "access_denied"},
     {"error_class": "site_access", "failure_stage": "access_denial_observed", "task_step": "site_open"}),
    ({"stop_point": "before_visit"}, {"failure_stage": "before_target_response", "task_step": "site_open"}),
    ({"stop_point": "extra_verification", "agent_platform": "claude_code", "agent_env": "cloud_browser"},
     {"error_class": "login_auth", "task_step": "login"}),
]


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("AHN_SITE_PROBE", "0")

    def no_network(*_a, **_k):
        raise AssertionError("no host is read")

    monkeypatch.setattr(site_probe, "fetch_home", no_network)
    return TestClient(create_app(db))


def lookup(client, params):
    r = client.get("/v1/incidents/check", params={"site_host": HOST, "window_hours": 168, **params})
    assert r.status_code == 200, r.text
    return r.json()


def strip(body):
    """What differs between two identical lookups by design: the time, the one-time token, the activity band."""
    return {k: v for k, v in body.items() if k not in {"generated_at", "feedback_token", "lookup_activity"}}


def canon(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.parametrize("params", FIXTURES)
def test_the_lookup_answer_is_the_same_before_and_after_an_explanation(client, params):
    before = strip(lookup(client, params))
    why = client.get("/v1/why", params={"site_host": HOST, "agent_platform": "claude_code", "stop_point": "company_login", **params})
    assert why.status_code == 200
    after = strip(lookup(client, params))
    assert canon(before) == canon(after)


@pytest.mark.parametrize("params", FIXTURES)
def test_server_is_the_lookups_words_and_decision_byte_for_byte(client, params):
    check = lookup(client, params)
    why = client.get("/v1/why", params={"site_host": HOST, **params}).json()["server"]
    for key in SAME:
        # failure_stage is left out of `server` when the lookup gave none, instead of a null.
        assert canon(check.get(key)) == canon(why.get(key)), key
    assert why["position"] in {"first", "after_checks"}


@pytest.mark.parametrize("chosen,codes", SYMPTOMS)
def test_a_chosen_symptom_gives_the_answer_of_its_codes(client, chosen, codes):
    why = client.get("/v1/why", params={"site_host": HOST, **chosen}).json()["server"]
    check = lookup(client, codes)
    for key in SAME:
        assert canon(check.get(key)) == canon(why.get(key)), key


def test_what_a_person_adds_does_not_change_the_decision_of_the_same_codes(client):
    base = {"site_host": HOST, "error_class": "login_auth", "task_step": "login"}
    plain = client.get("/v1/why", params=base).json()["server"]
    for extra in ({"agent_platform": "chatgpt_agent"}, {"agent_env": "cloud_browser"}, {"external_browser": "yes_same_account"},
                  {"screen_message": "claude_org_policy_banner"}, {"task_context": "my_account_work"}, {"since_kind": "today_first"}):
        chosen = client.get("/v1/why", params={**base, **extra}).json()["server"]
        for key in ("summary_line", "recommendation", "next_action", "cause", "failure_stage"):
            assert canon(plain.get(key)) == canon(chosen.get(key)), (extra, key)


# --- the same on a host that has records -----------------------------------------------------------------------------------
# `server` is the lookup with window_hours=168, so a host with data older than a day (and the default 24-hour window) is the
# case that tells the two apart.
def _seed_everything(db, monkeypatch):
    now = datetime.now(timezone.utc)
    seed(db,
         *observation(HOST, now - timedelta(hours=30), outcome="error", error_class="service_error"),   # outside 24 hours
         *observation(HOST, now - timedelta(days=5), outcome="error", error_class="site_access"),
         hourly(HOST, now - timedelta(hours=3), sources=3, error_class="service_error"),
         ("INSERT INTO status_sources (site_host,source_name,status_page_url,api_url,evidence) VALUES (%s,'Fixture Status',"
          "'https://status.fixture-host.test','https://status.fixture-host.test/api','curated_mapping')", (HOST,)),
         ("INSERT INTO status_cache (site_host,indicator,fetched_at,active_incidents,max_incident_impact) VALUES (%s,'minor',now(),1,'minor')", (HOST,)))
    read_profile(db, monkeypatch, host=HOST)
    measure(db, host=HOST, home=Home(status=200))


@pytest.fixture
def seeded_client(db, monkeypatch):
    _seed_everything(db, monkeypatch)
    # The home page was measured above and a second check of a host waits 6 hours, so nothing is read from here on.

    def no_network(*_a, **_k):
        raise AssertionError("no host is read")

    monkeypatch.setattr(site_probe, "fetch_home", no_network)
    return TestClient(create_app(db))


@pytest.mark.parametrize("params", FIXTURES)
def test_server_equals_a_168_hour_lookup_on_a_host_with_records(seeded_client, params):
    check = lookup(seeded_client, params)
    why = seeded_client.get("/v1/why", params={"site_host": HOST, **params}).json()["server"]
    for key in SAME:
        assert canon(check.get(key)) == canon(why.get(key)), key
    assert check["provider_status"]["indicator"] == "minor" and check["ahn_check"]["status"] == "measured"


def test_the_records_are_older_than_the_default_window_so_the_comparison_has_power(seeded_client):
    day = strip(seeded_client.get("/v1/incidents/check", params={"site_host": HOST}).json())
    week = strip(lookup(seeded_client, {}))
    assert canon(day) != canon(week)
