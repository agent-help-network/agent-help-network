"""D121 against the ahn_test database: both hand-offs through service.check. The switch restores the answer before D121, nothing but the
decision and what follows from it differs, task_step status_check or preflight, the plain search-page view and a lookup with no failure
signal are the same with the switch on and off, and the answer still validates against the published model."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

if not os.environ.get("AHN_TEST_DATABASE_URL", ""):
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from ahn import background_reads, service, site_probe  # noqa: E402
from ahn.db import connect  # noqa: E402
from ahn.models import CheckResponse  # noqa: E402
from test_site_probe_db import HOST, Profiles, db  # noqa: E402,F401

VOLATILE = ("generated_at", "feedback_token", "lookup_activity")
DECISION = ("recommendation", "summary_line", "next_action")
HELP = "https://help.example.com/"
CUSTOMER = {"name": "Example", "consumer": {"paths": [{"kind": "customer_help", "url": HELP, "source_url": "https://www.example.com/"}],
            "verified_at": "2026-10-31"}}


@pytest.fixture
def open_host(db, monkeypatch):
    """The host's robots.txt allows agents; no official path, no queued read and no stored home-page check remain."""
    def no_request(host):
        raise AssertionError("a request went out to " + host)

    monkeypatch.setattr(site_probe, "fetch_home", no_request)
    monkeypatch.setattr(service, "official_paths", lambda host: None)
    monkeypatch.setattr(background_reads, "read_profile_file", Profiles(status="ok"))
    service.check(db, HOST)
    assert background_reads.process_due(db, limit=5)["done"] >= 1
    with connect(db) as conn:
        conn.execute("DELETE FROM background_reads")
        conn.execute("DELETE FROM site_probe")
    return db


def look(settings, switch, **kwargs):
    body = service.check(replace(settings, d121_handoff=switch), HOST, **kwargs)
    CheckResponse(**body)  # the answer still validates against the published model
    return body


def strip(body, *skip):
    return {k: v for k, v in body.items() if k not in VOLATILE + skip}


@pytest.mark.parametrize("stage", [None, "access_denial_observed"])
def test_a_reported_refusal_is_handed_to_the_user_and_the_switch_restores_proceed(open_host, stage):
    kwargs = {"error_class": "site_access", **({"failure_stage": stage} if stage else {})}
    on = look(open_host, True, **kwargs)
    assert on["access_profile"]["robots_status"] == "ok"
    assert on["recommendation"]["action"] == "hand_off_to_human" and on["recommendation"]["confidence"] == "low"
    assert on["recommendation"]["reasons"][0] == "caller_reported_refusal"
    assert on["next_action"] == {"do": "hand_off_to_human", "url": None, "retry_after_seconds": None}
    assert on["summary_line"].startswith("You reported a refused page,")
    off = look(open_host, False, **kwargs)
    assert off["recommendation"]["action"] == "proceed"
    assert on["recommendation"]["reasons"][1:] == off["recommendation"]["reasons"]
    # next_checks has no caller_reported_refusal code: it, and everything else outside the decision, is the same
    assert strip(on, *DECISION) == strip(off, *DECISION)


def test_a_reported_refusal_with_a_customer_page_hands_the_user_that_page(open_host, monkeypatch):
    monkeypatch.setattr(service, "official_paths", lambda host: CUSTOMER)
    out = look(open_host, True, error_class="site_access")
    assert out["recommendation"]["reasons"][0] == "caller_reported_refusal"
    assert out["next_action"]["customer_url"] == HELP and out["next_action"]["url"] is None
    assert "next_action.customer_url" in out["summary_line"]


@pytest.mark.parametrize("kwargs", [{}, {"error_class": "site_access", "task_step": "status_check"},
                                    {"error_class": "site_access", "task_step": "preflight"},
                                    {"error_class": "site_access", "plain_view": True},
                                    {"error_class": "site_access", "failure_stage": "response_observed"},
                                    {"error_class": "site_access", "failure_stage": "before_target_response"},
                                    {"failure_stage": "access_denial_observed"},
                                    {"error_class": "service_error"}, {"error_class": "login_auth"},
                                    {"error_class": "human_verification"}, {"task_step": "status_check"}])
def test_every_lookup_the_rule_does_not_cover_is_the_same_with_the_switch_on_and_off(open_host, kwargs):
    assert strip(look(open_host, True, **kwargs)) == strip(look(open_host, False, **kwargs))


def feed(provider_status):
    return lambda conn, host, now: service.FeedView(None, provider_status, False, False, False, None)


def page(indicator, impact):
    t = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    status = {"source_name": "Example Status", "source_url": "https://status.example.net/", "indicator": indicator, "freshness": "fresh", "fetched_at": t,
              "source_updated_at": t, "active_incidents": 1, "active_maintenances": 0, "basis": "incident", "incident_updated_at": t}
    if impact:
        status["max_incident_impact"] = impact
    return status


def test_a_verification_screen_under_a_minor_incident_is_handed_to_the_user(open_host, monkeypatch):
    monkeypatch.setattr(service, "feed_view", feed(page("minor", "minor")))
    on = look(open_host, True, error_class="human_verification")
    assert on["recommendation"] == {"action": "hand_off_to_human", "confidence": "medium", "reasons": ["human_handoff", "provider_scope"]}
    assert on["next_action"]["do"] == "hand_off_to_human"
    off = look(open_host, False, error_class="human_verification")
    assert off["recommendation"]["action"] == "wait_provider_incident"
    assert strip(on, *DECISION) == strip(off, *DECISION)


@pytest.mark.parametrize("indicator,impact,error_class", [("major", "minor", "human_verification"), ("minor", None, "human_verification"),
                                                          ("minor", "major", "human_verification"), ("minor", "minor", "login_auth"),
                                                          ("minor", "minor", "site_access"), ("minor", "minor", None)])
def test_a_wait_the_rule_does_not_cover_is_the_same_with_the_switch_on_and_off(open_host, monkeypatch, indicator, impact, error_class):
    monkeypatch.setattr(service, "feed_view", feed(page(indicator, impact)))
    kwargs = {"error_class": error_class} if error_class else {}
    on = look(open_host, True, **kwargs)
    assert on["recommendation"]["action"] == "wait_provider_incident"
    assert strip(on) == strip(look(open_host, False, **kwargs))
