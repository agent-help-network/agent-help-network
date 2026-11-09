"""D101 against the ahn_test database: the service wiring through service.check. A service_error lookup (a 5xx or no response) on a host whose robots.txt
read was refused or could not be used is handed to the user, at every failure stage, and the switch turns the hand-off off. Only the recommendation, the
first line, next_action and next_checks differ between the switch on and off; a status_check lookup, a plain view, a lookup with no failure signal, a
failure that is not a service_error and the D63 and D87 switches off give the same answer with the D101 switch on and off. A customer page is handed
over only when it is on the host's own site. The home-page-check sentence of the edge hand-off says so once the check is over a day old."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

if not os.environ.get("AHN_TEST_DATABASE_URL", ""):
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from ahn import background_reads, response_guide, service, site_probe  # noqa: E402
from ahn.db import connect  # noqa: E402
from ahn.models import CheckResponse  # noqa: E402
from ahn.triage import CHECKS, NO_PATH_CODE  # noqa: E402
from test_site_probe_db import ALLOW, HOST, Profiles, db  # noqa: E402,F401

VOLATILE = ("generated_at", "feedback_token", "lookup_activity")
CHANGING = ("recommendation", "summary_line", "next_action", "next_checks")
BTR = "before_target_response"
STAGES = (None, BTR, "response_observed", "unknown")
HELP_SAME = "https://www." + HOST + "/help"
HELP_OTHER = "https://help.other-example.net/"
DEV = {"name": "Example", "api_docs": "https://developer.example.net/docs", "developer_portal": None, "note": None}
NO_DEV = {"name": "Example", "api_docs": None, "developer_portal": None, "note": None}


def consumer(url):
    return {"paths": [{"kind": "customer_help", "url": url, "source_url": "https://www.example.net/"}], "verified_at": "2026-10-31"}


def warm(db, monkeypatch, robots_status, official=None):
    """The host's robots.txt read finished with ``robots_status``; no queued read and no stored home-page check remain."""
    def no_request(host):
        raise AssertionError("a request went out to " + host)

    monkeypatch.setattr(site_probe, "fetch_home", no_request)
    monkeypatch.setattr(service, "official_paths", lambda host: official)
    monkeypatch.setattr(background_reads, "read_profile_file", Profiles(robots=ALLOW, status=robots_status))
    service.check(db, HOST)
    assert background_reads.process_due(db, limit=5)["done"] >= 1
    with connect(db) as conn:
        conn.execute("DELETE FROM background_reads")
        conn.execute("DELETE FROM site_probe")
    return db


def look(settings, switch, **kwargs):
    body = service.check(replace(settings, d101_no_path_handoff=switch), HOST, **kwargs)
    CheckResponse(**body)  # the answer still validates against the published model
    return body


def strip(body, *skip):
    out = {k: v for k, v in body.items() if k not in VOLATILE + skip}
    if isinstance(out.get("ahn_check"), dict):
        out["ahn_check"] = {k: v for k, v in out["ahn_check"].items() if k != "age_seconds"}
    return out


def codes(body):
    return [c["code"] for c in body["next_checks"]]


def kwargs_for(stage, error_class="service_error"):
    return {"error_class": error_class, **({"failure_stage": stage} if stage else {})}


def assert_only_the_decision_differs(on, off):
    assert strip(on, *CHANGING) == strip(off, *CHANGING)
    assert [c for c in on["next_checks"] if c["code"] != NO_PATH_CODE] == off["next_checks"]
    assert {k: v for k, v in on["next_action"].items() if k not in ("do", "url")} == \
        {k: v for k, v in off["next_action"].items() if k not in ("do", "url")}


# --- the shapes: robots.txt refused, and robots.txt answered with something that is not a policy --------------------------------

@pytest.mark.parametrize("robots_status,row", [("refused", "U6-H"), ("invalid", "U7-H"), ("redirected", "U7-H"), ("error", "U7-H")])
@pytest.mark.parametrize("stage", STAGES)
def test_a_service_error_with_no_path_is_handed_to_the_user_at_every_stage_and_the_switch_restores_the_old_answer(db, monkeypatch, robots_status, row, stage):
    warm(db, monkeypatch, robots_status)
    on, off = look(db, True, **kwargs_for(stage)), look(db, False, **kwargs_for(stage))
    assert on["recommendation"]["action"] == "hand_off_to_human" and on["recommendation"]["reasons"][-1] == NO_PATH_CODE
    assert on["next_action"] == {"do": "hand_off_to_human", "url": None, "retry_after_seconds": None}
    table = response_guide.HANDOFF_NO_RESPONSE_ROWS if stage == BTR else response_guide.HANDOFF_SERVICE_ERROR_ROWS
    assert on["summary_line"] == table[row] and "tell your user now" in on["summary_line"].lower()
    assert codes(on)[1] == NO_PATH_CODE
    assert off["recommendation"]["action"] == "unknown" and off["next_action"]["do"] == "no_clear_action"
    assert NO_PATH_CODE not in codes(off) and "etry once later" in off["summary_line"]
    assert_only_the_decision_differs(on, off)


def test_a_pending_read_still_waits_for_the_re_lookup(db, monkeypatch):
    monkeypatch.setattr(service, "official_paths", lambda host: None)
    monkeypatch.setattr(background_reads, "read_profile_file", Profiles(robots=ALLOW, status="ok"))
    service.check(db, HOST)  # the first lookup queues the read; it has not run, so the profile is pending
    on, off = look(db, True, **kwargs_for(BTR)), look(db, False, **kwargs_for(BTR))
    assert strip(on) == strip(off) and NO_PATH_CODE not in on["recommendation"]["reasons"]


# --- what the switch never touches --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"task_step": "status_check", "error_class": "service_error"}, {"task_step": "status_check", "error_class": "service_error", "failure_stage": BTR},
    {"task_step": "status_check"}, {"task_step": "preflight"}, {}, {"error_class": "service_error", "plain_view": True},
    {"error_class": "site_access"}, {"error_class": "human_verification"}, {"error_class": "login_auth"},
    {"error_class": "site_access", "plain_view": True, "failure_stage": BTR}])
@pytest.mark.parametrize("robots_status", ["refused", "invalid"])
def test_a_status_check_a_plain_view_a_lookup_without_failure_and_other_failures_are_the_same_with_the_switch_on_and_off(db, monkeypatch, robots_status, kwargs):
    warm(db, monkeypatch, robots_status)
    assert strip(look(db, True, **kwargs)) == strip(look(db, False, **kwargs))


@pytest.mark.parametrize("changes", [{"proceed_without_failure": False}, {"failure_kind_lines": False},
                                     {"proceed_without_failure": False, "failure_kind_lines": False}])
@pytest.mark.parametrize("stage", STAGES)
def test_with_the_d63_or_d87_switch_off_the_answer_is_the_same_with_the_d101_switch_on_and_off(db, monkeypatch, changes, stage):
    warm(db, monkeypatch, "refused")
    settings = replace(db, **changes)
    on, off = look(settings, True, **kwargs_for(stage)), look(settings, False, **kwargs_for(stage))
    assert strip(on) == strip(off) and NO_PATH_CODE not in codes(on)


# --- a customer page: handed over only when it is on the site that did not answer ---------------------------------------------

@pytest.mark.parametrize("stage,hands_off", [(BTR, True), (None, False), ("response_observed", False), ("unknown", False)])
def test_a_customer_page_on_the_same_site_is_handed_over_after_no_response_only(db, monkeypatch, stage, hands_off):
    warm(db, monkeypatch, "refused", {**NO_DEV, "consumer": consumer(HELP_SAME)})
    on, off = look(db, True, **kwargs_for(stage)), look(db, False, **kwargs_for(stage))
    if not hands_off:
        assert strip(on) == strip(off) and on["next_action"]["do"] == "no_clear_action"
        return
    assert on["next_action"] == {"do": "hand_off_to_human", "url": None, "retry_after_seconds": None, "customer_url": HELP_SAME}
    assert on["summary_line"] == response_guide.HANDOFF_NO_RESPONSE_CUSTOMER
    assert off["next_action"]["do"] == "no_clear_action" and off["next_action"]["customer_url"] == HELP_SAME
    assert_only_the_decision_differs(on, off)


@pytest.mark.parametrize("stage", STAGES)
def test_a_customer_page_on_another_site_leaves_the_answer_as_it_was(db, monkeypatch, stage):
    warm(db, monkeypatch, "refused", {**NO_DEV, "consumer": consumer(HELP_OTHER)})
    on, off = look(db, True, **kwargs_for(stage)), look(db, False, **kwargs_for(stage))
    assert strip(on) == strip(off) and on["next_action"]["do"] == "no_clear_action" and on["next_action"]["customer_url"] == HELP_OTHER


def test_a_developer_path_beside_a_customer_page_on_the_same_site_keeps_the_api_pointer_in_the_line_and_the_check(db, monkeypatch):
    warm(db, monkeypatch, "ok", {**DEV, "consumer": consumer(HELP_SAME)})
    on, off = look(db, True, **kwargs_for(BTR)), look(db, False, **kwargs_for(BTR))
    assert off["recommendation"]["action"] == "use_official_path"
    assert on["recommendation"]["action"] == "hand_off_to_human" and on["recommendation"]["reasons"] == ["official_path_available", NO_PATH_CODE]
    assert on["next_action"]["do"] == "hand_off_to_human" and on["next_action"]["customer_url"] == HELP_SAME and on["next_action"]["url"] is None
    assert on["summary_line"] == response_guide.HANDOFF_NO_RESPONSE_CUSTOMER + " API work: official_paths."
    assert len(on["summary_line"]) <= response_guide.LINE_MAX
    check = next(c for c in on["next_checks"] if c["code"] == NO_PATH_CODE)
    assert check["text"] == CHECKS[NO_PATH_CODE] and "official_paths" in check["text"] and "no page of this site" not in check["text"]
    assert "official_path_available" in codes(on)
    assert_only_the_decision_differs(on, off)


# --- the H3 sentence when the home-page check is over a day old ---------------------------------------------------------------

def seed_probe(settings, age, *, refusals):
    now = datetime.now(timezone.utc)
    with connect(settings) as conn:
        conn.execute("""INSERT INTO site_probe (site_host,read_host,probed_at,outcome,http_status,fingerprint_version,next_due_at,
                                                consecutive_refusals,trigger_kind)
                        VALUES (%s,%s,%s,'refused',403,'v1',%s,%s,'daily')
                        ON CONFLICT (site_host) DO UPDATE SET probed_at=EXCLUDED.probed_at, outcome=EXCLUDED.outcome,
                            http_status=EXCLUDED.http_status, next_due_at=EXCLUDED.next_due_at,
                            consecutive_refusals=EXCLUDED.consecutive_refusals""",
                     (HOST, HOST, now - age, now + timedelta(days=30), refusals))


H3_KWARGS = {"error_class": "site_access"}


def test_the_h3_line_says_the_check_is_over_a_day_old_and_nothing_else_changes(db, monkeypatch):
    warm(db, monkeypatch, "ok")
    seed_probe(db, timedelta(days=3), refusals=2)
    on, off = look(db, True, **H3_KWARGS), look(db, False, **H3_KWARGS)
    assert on["ahn_check"]["freshness"] == "stale" and "edge_refusal_confirmed" in on["recommendation"]["reasons"]
    assert on["summary_line"] == response_guide.H3_STALE and "or user agents" in on["summary_line"] and "over a day ago" in on["summary_line"]
    assert off["summary_line"] == response_guide.ROWS["H3"][1]
    assert strip(on, "summary_line", "ahn_check") == strip(off, "summary_line", "ahn_check")


def test_the_h3_line_with_a_customer_page(db, monkeypatch):
    warm(db, monkeypatch, "ok", {**NO_DEV, "consumer": consumer(HELP_SAME)})
    seed_probe(db, timedelta(days=3), refusals=2)
    on, off = look(db, True, **H3_KWARGS), look(db, False, **H3_KWARGS)
    assert on["summary_line"] == response_guide.H3_STALE_CONSUMER and on["next_action"]["customer_url"] == HELP_SAME
    assert strip(on, "summary_line", "ahn_check") == strip(off, "summary_line", "ahn_check")


def test_a_check_under_a_day_old_keeps_the_h3_line(db, monkeypatch):
    warm(db, monkeypatch, "ok")
    seed_probe(db, timedelta(hours=2), refusals=1)
    on, off = look(db, True, **H3_KWARGS), look(db, False, **H3_KWARGS)
    assert on["ahn_check"]["freshness"] == "fresh" and on["summary_line"] == response_guide.ROWS["H3"][1]
    assert strip(on, "ahn_check") == strip(off, "ahn_check")


@pytest.mark.parametrize("changes,kwargs", [
    ({"proceed_without_failure": False}, {}), ({"failure_kind_lines": False}, {}),
    ({"proceed_without_failure": False, "failure_kind_lines": False}, {}),
    ({}, {"task_step": "status_check"}), ({"proceed_without_failure": False}, {"task_step": "status_check"})])
def test_the_h3_line_is_unchanged_for_a_status_check_and_with_the_d63_or_d87_switch_off(db, monkeypatch, changes, kwargs):
    """The contract needs the D63 and D87 switches on and no status_check lookup; the first line must not move without them."""
    warm(db, monkeypatch, "ok")
    seed_probe(db, timedelta(days=3), refusals=2)
    settings = replace(db, **changes)
    kw = {**H3_KWARGS, **kwargs}
    on, off = look(settings, True, **kw), look(settings, False, **kw)
    assert strip(on, "ahn_check") == strip(off, "ahn_check")
    assert "over a day ago" not in on["summary_line"]
