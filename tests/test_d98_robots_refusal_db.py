"""D98 against the ahn_test database: the refused-robots shape through service.check. The switch turns the hand-off off, a status_check
lookup that reports the failure gets the same hand-off (with its status_url where one is known), a status_check lookup with no failure
signal (D96) and every other lookup the rule does not cover are the same with the switch on and off, and the answer still validates."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

if not os.environ.get("AHN_TEST_DATABASE_URL", ""):
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from ahn import background_reads, service, site_probe  # noqa: E402
from ahn.db import connect  # noqa: E402
from ahn.models import CheckResponse  # noqa: E402
from test_site_probe_db import HOST, Profiles, db  # noqa: E402,F401

VOLATILE = ("generated_at", "feedback_token", "lookup_activity")


@pytest.fixture
def refused_host(db, monkeypatch):
    """The host's robots.txt read was refused (an edge 403); no queued read and no stored home-page check remain."""
    def no_request(host):
        raise AssertionError("a request went out to " + host)

    monkeypatch.setattr(site_probe, "fetch_home", no_request)
    monkeypatch.setattr(service, "official_paths", lambda host: None)
    monkeypatch.setattr(background_reads, "read_profile_file", Profiles(status="refused"))
    service.check(db, HOST)
    assert background_reads.process_due(db, limit=5)["done"] >= 1
    with connect(db) as conn:
        conn.execute("DELETE FROM background_reads")
        conn.execute("DELETE FROM site_probe")
    return db


def look(settings, switch, **kwargs):
    body = service.check(replace(settings, d98_robots_refusal_handoff=switch), HOST, **kwargs)
    CheckResponse(**body)  # the answer still validates against the published model
    return body


def strip(body, *skip):
    return {k: v for k, v in body.items() if k not in VOLATILE + skip}


def test_the_refused_robots_shape_is_handed_to_the_user_and_the_switch_restores_the_old_answer(refused_host):
    on = look(refused_host, True, error_class="site_access")
    assert on["access_profile"]["robots_status"] == "refused"
    assert on["recommendation"] == {"action": "hand_off_to_human", "confidence": "medium",
                                    "reasons": ["edge_refusal_robots", "robots_fetch_refused"]}
    assert on["next_action"] == {"do": "hand_off_to_human", "url": None, "retry_after_seconds": None}
    assert on["summary_line"].startswith("The site's edge refused both you and AHN's plain robots.txt read.")
    codes = [c["code"] for c in on["next_checks"]]
    assert codes[:3] == ["locate_failure_stage", "edge_refusal_robots", "robots_fetch_refused"]
    off = look(refused_host, False, error_class="site_access")
    assert off["recommendation"]["action"] == "unknown" and off["next_action"]["do"] == "no_clear_action"
    assert "edge_refusal_robots" not in [c["code"] for c in off["next_checks"]]
    # nothing but the decision and what follows from it differs
    keep = ("recommendation", "summary_line", "next_action", "next_checks")
    assert strip(on, *keep) == strip(off, *keep)


def test_the_access_denial_stage_gives_the_same_hand_off_and_the_other_stages_do_not(refused_host):
    assert look(refused_host, True, error_class="site_access", failure_stage="access_denial_observed")["recommendation"]["action"] == "hand_off_to_human"
    for stage in ("before_target_response", "response_observed"):
        out = look(refused_host, True, error_class="site_access", failure_stage=stage)
        assert strip(out) == strip(look(refused_host, False, error_class="site_access", failure_stage=stage))


@pytest.mark.parametrize("kwargs", [{"error_class": "service_error"}, {"error_class": "login_auth"}, {"error_class": "human_verification"},
                                    {}, {"error_class": "site_access", "plain_view": True},
                                    {"task_step": "status_check"}, {"task_step": "preflight"}])
def test_every_other_lookup_is_the_same_with_the_switch_on_and_off(refused_host, kwargs):
    assert strip(look(refused_host, True, **kwargs)) == strip(look(refused_host, False, **kwargs))


CUSTOMER = {"name": "Example", "consumer": {"paths": [{"kind": "customer_help", "url": "https://help.example.com/", "source_url": "https://www.example.com/"}],
            "verified_at": "2026-10-31"}}


@pytest.mark.parametrize("page,status_url", [(None, None), (("Other status", "https://status.other-example.net/"), "https://status.other-example.net/"),
                                             (("Own status", "https://" + HOST + "/status"), "https://" + HOST + "/status")])
@pytest.mark.parametrize("stage", [None, "access_denial_observed"])
def test_a_status_check_that_reports_the_failure_gets_the_hand_off_with_any_status_url(refused_host, monkeypatch, page, status_url, stage):
    monkeypatch.setattr(service, "official_status_page_for", lambda host: page)
    kwargs = {"task_step": "status_check", "error_class": "site_access"}
    if stage:
        kwargs["failure_stage"] = stage
    on = look(refused_host, True, **kwargs)
    assert on["recommendation"]["action"] == "hand_off_to_human"
    assert on["recommendation"]["reasons"] == ["edge_refusal_robots", "robots_fetch_refused"]
    assert on["next_action"]["do"] == "hand_off_to_human"
    assert on["summary_line"].startswith("The site's edge refused both you and AHN's plain robots.txt read.")
    assert on["next_action"].get("status_url") == status_url
    off = look(refused_host, False, **kwargs)
    assert off["recommendation"]["action"] == "unknown"


def test_a_status_check_that_reports_the_failure_names_the_customer_page_when_one_is_curated(refused_host, monkeypatch):
    monkeypatch.setattr(service, "official_paths", lambda host: CUSTOMER)
    out = look(refused_host, True, task_step="status_check", error_class="site_access")
    assert out["next_action"]["do"] == "hand_off_to_human"
    assert out["next_action"].get("customer_url") == "https://help.example.com/"
    assert "next_action.customer_url" in out["summary_line"]
