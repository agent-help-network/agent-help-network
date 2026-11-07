"""D109 against the dedicated ahn_test database: a cause is stored, held or published by the automatic review, shown only as the
reporting agent's statement, and takes no part in any answer's decision."""

from __future__ import annotations

import copy
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)
os.environ.setdefault("AHN_DATABASE_URL", TEST_URL)
os.environ.setdefault("AHN_KEY_PEPPER", "test-pepper-must-be-at-least-thirty-two-chars")

from fastapi.testclient import TestClient  # noqa: E402

from ahn import explain_text, site_probe  # noqa: E402
from ahn.auto_review import review_batch  # noqa: E402
from ahn.db import connect  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.models import CAUSE_NAMING_SITE_OR_PLATFORM, CAUSE_RULES, ObservationCause  # noqa: E402
from test_cause_db import observation, seed  # noqa: E402
from test_public_intake import post_public, public_client, sample, unpaced_background_reads  # noqa: E402,F401
from test_site_probe_db import db  # noqa: E402,F401

NOT_VERIFIED = "not verified by AHN"


def combo(cause):
    """One accepted (basis, error_class) for a cause."""
    bases, classes = CAUSE_RULES[ObservationCause(cause)]
    return sorted(item.value for item in bases)[0], (sorted(item.value for item in classes)[0] if classes else "site_access")


def with_cause(cause, **changes):
    basis, error_class = combo(cause)
    changes.setdefault("observed_at", (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat())  # old enough for the review
    return sample(outcome="blocked", cause=cause, cause_basis=basis, error_class=error_class, **changes)


def age(settings, observation_id, minutes=15):
    with connect(settings) as conn:
        conn.execute("UPDATE observations SET received_at=now()-%s*interval '1 minute' WHERE id=%s", (minutes, observation_id))


def row_of(settings, observation_id):
    with connect(settings) as conn:
        return conn.execute("SELECT cause,cause_basis,review_state,review_reason FROM observations WHERE id=%s", (observation_id,)).fetchone()


def publish_by_hand(settings, receipt):
    with connect(settings) as conn:
        conn.execute("UPDATE observations SET review_state='published' WHERE id=%s", (receipt["observation_id"],))
        conn.execute("UPDATE incidents SET visibility='published' WHERE id=%s", (receipt["incident_id"],))


# --- intake and the automatic review ------------------------------------------------------------------------------------------

def test_a_cause_and_its_basis_are_stored_with_the_report(public_client):
    settings, client = public_client
    body = with_cause("verification_on_person_device", site_host="storedcause.org")
    receipt = post_public(client, body)
    assert receipt.status_code == 201, receipt.text
    assert row_of(settings, receipt.json()["observation_id"])["cause"] == "verification_on_person_device"
    assert row_of(settings, receipt.json()["observation_id"])["cause_basis"] == body["cause_basis"]
    plain = post_public(client, sample(site_host="plainreport.org")).json()
    assert row_of(settings, plain["observation_id"])["cause"] is None and row_of(settings, plain["observation_id"])["cause_basis"] is None


@pytest.mark.parametrize("body", [
    {"cause_basis": "site_message"},
    {"cause": "site_said_no_agents"},
    {"cause": "unknown", "cause_basis": "site_message"},
    {"cause": "site_said_no_agents", "cause_basis": "own_platform_message"},
    {"cause": "signed_out", "cause_basis": "site_message", "error_class": "site_access"},
    {"cause": "site_said_no_agents", "cause_basis": "site_message", "outcome": "passed"},
])
def test_a_bad_combination_is_a_422_invalid_cause_and_stores_nothing(public_client, body):
    settings, client = public_client
    response = post_public(client, sample(site_host="badcause.org", **body))
    assert response.status_code == 422 and response.json() == {"error": "invalid_cause"}
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM observations").fetchone()["n"] == 0


def test_a_retry_with_the_same_key_and_cause_is_idempotent_and_a_different_cause_is_a_conflict(public_client):
    _, client = public_client
    body = with_cause("site_bot_check", site_host="idemcause.org", idempotency_key=str(uuid4()))
    first, second = post_public(client, body), post_public(client, body)
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["observation_id"] == first.json()["observation_id"]
    other = dict(body, cause="site_unavailable", cause_basis="site_message", error_class="service_error")
    assert post_public(client, other).status_code == 409
    assert post_public(client, {k: v for k, v in body.items() if k not in ("cause", "cause_basis")}).status_code == 409


@pytest.mark.parametrize("cause", [item.value for item in ObservationCause])
def test_the_four_naming_causes_wait_for_an_operator_and_the_other_five_follow_the_ordinary_review(public_client, cause):
    settings, client = public_client
    receipt = post_public(client, with_cause(cause, site_host=f"review{cause.replace('_', '')}.org")).json()
    age(settings, receipt["observation_id"])
    naming = ObservationCause(cause) in CAUSE_NAMING_SITE_OR_PLATFORM
    assert review_batch(TEST_URL) == ({"published": 0, "held": 1} if naming else {"published": 1, "held": 0})
    row = row_of(settings, receipt["observation_id"])
    if naming:
        assert (row["review_state"], row["review_reason"]) == ("pending", "cause_review")
    else:
        assert (row["review_state"], row["review_reason"]) == ("published", "auto_unverified")


def test_a_cause_does_not_loosen_the_other_review_rules(public_client):
    settings, client = public_client
    ids = [post_public(client, with_cause("signed_out", site_host="burstcause.org")).json()["observation_id"] for _ in range(3)]
    for observation_id in ids:
        age(settings, observation_id)
    assert review_batch(TEST_URL) == {"published": 0, "held": 3}
    assert {row_of(settings, i)["review_reason"] for i in ids} == {"host_burst"}


# --- what is shown ------------------------------------------------------------------------------------------------------------

def test_a_published_cause_is_shown_and_an_unpublished_one_is_not(public_client):
    settings, client = public_client
    host = "showcause.org"
    receipt = post_public(client, with_cause("verification_not_received", site_host=host)).json()
    age(settings, receipt["observation_id"])
    review_batch(TEST_URL)
    shown = client.get("/v1/incidents/check", params={"site_host": host}).json()["observations"]
    assert [(o["cause"], o["cause_basis"]) for o in shown] == [("verification_not_received", combo("verification_not_received")[0])]

    held_host = "heldcause.org"
    held = post_public(client, with_cause("platform_refused", site_host=held_host)).json()
    age(settings, held["observation_id"])
    review_batch(TEST_URL)
    assert client.get("/v1/incidents/check", params={"site_host": held_host}).json().get("observations", []) == []
    assert explain_text.CAUSE_WORDS["platform_refused"]["en"] not in client.get(f"/en/incidents/{held['incident_id']}").text
    publish_by_hand(settings, held)  # the operator looked at it and published it
    after = client.get("/v1/incidents/check", params={"site_host": held_host}).json()["observations"]
    assert after[0]["cause"] == "platform_refused"


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_the_incident_page_and_the_why_answer_label_a_cause_as_the_reporting_agents_own(public_client, lang):
    settings, client = public_client
    host = "pagecause.org"
    receipt = post_public(client, with_cause("signed_out", site_host=host)).json()
    publish_by_hand(settings, receipt)
    words = explain_text.CAUSE_WORDS["signed_out"][lang]
    page = client.get(f"/{lang}/incidents/{receipt['incident_id']}").text
    assert words in page
    assert ("not verified by AHN" if lang == "en" else "검증한 것은 아닙니다") in page
    why = client.get("/v1/why", params={"site_host": host, "error_class": "login_auth", "lang": lang})
    assert why.status_code == 200, why.text
    saw = why.json()["others_saw"]
    text = str(saw)
    assert words in text and explain_text.CAUSE_LEAD[lang].strip("; ") in text
    reports = saw["reports"]
    assert reports and all(not r.get("verified", False) for r in reports)
    assert reports[0]["cause"] == "signed_out"


def test_a_plain_report_is_shown_without_any_cause_text(public_client):
    settings, client = public_client
    receipt = post_public(client, sample(site_host="nocause.org")).json()
    publish_by_hand(settings, receipt)
    assert "cause" not in client.get("/v1/incidents/check", params={"site_host": "nocause.org"}).json()["observations"][0]
    assert "What reporting agents said they found" not in client.get(f"/en/incidents/{receipt['incident_id']}").text


# --- the verdict does not move ------------------------------------------------------------------------------------------------

HOST = "invariance-cause-host.com"
CHECKS = [
    {},
    {"error_class": "login_auth", "task_step": "login"},
    {"error_class": "human_verification", "task_step": "human_verification"},
    {"error_class": "service_error", "http_status": "503"},
    {"error_class": "site_access", "failure_stage": "access_denial_observed", "http_status": "403", "challenge": "header"},
]


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("AHN_SITE_PROBE", "0")
    monkeypatch.setattr(site_probe, "fetch_home", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no host is read")))
    return TestClient(create_app(db))


def strip(body):
    """The answer without what differs between two identical lookups by design, and without the cause fields themselves."""
    body = copy.deepcopy({k: v for k, v in body.items() if k not in {"generated_at", "feedback_token", "lookup_activity"}})
    for item in body.get("observations", []):
        item.pop("cause", None)
        item.pop("cause_basis", None)
    return body


def lookups(client):
    out = []
    for params in CHECKS:
        response = client.get("/v1/incidents/check", params={"site_host": HOST, "window_hours": 168, **params})
        assert response.status_code == 200, response.text
        out.append(response.json())
    return out


def set_cause(settings, cause):
    basis, _ = combo(cause) if cause else (None, None)
    with connect(settings) as conn:
        conn.execute("UPDATE observations SET cause=%s,cause_basis=%s", (cause, basis))


@pytest.mark.parametrize("cause", [item.value for item in ObservationCause])
def test_a_cause_on_published_reports_changes_nothing_but_the_cause_fields(db, client, cause):
    """The same host, the same reports: once without a cause, once with it. Every field of every answer except the cause and
    cause_basis of the listed observations is equal, the recommendation, next_action, the D73 cause hint and the counts included."""
    now = datetime.now(timezone.utc)
    seed(db, *observation(HOST, now - timedelta(hours=3)), *observation(HOST, now - timedelta(hours=2), error_class="human_verification"),
         *observation(HOST, now - timedelta(hours=1), outcome="blocked", error_class="site_access"))
    set_cause(db, None)
    before = lookups(client)
    assert all("cause" not in o for body in before for o in body.get("observations", []))
    set_cause(db, cause)
    after = lookups(client)
    assert any(o.get("cause") == cause for body in after for o in body.get("observations", []))  # it is shown, as the reporter's
    for plain, caused in zip(before, after):
        assert strip(plain) == strip(caused)
        for field in ("recommendation", "next_action", "summary_line", "evidence_level", "failures", "reporting_sources", "cause"):
            assert plain.get(field) == caused.get(field), field


def test_the_why_answer_keeps_every_decision_field_when_a_cause_is_added(db, client):
    now = datetime.now(timezone.utc)
    seed(db, *observation(HOST, now - timedelta(hours=2), error_class="login_auth", outcome="blocked"))
    params = {"site_host": HOST, "error_class": "login_auth"}
    set_cause(db, None)
    plain = client.get("/v1/why", params=params).json()
    set_cause(db, "verification_on_person_device")
    caused = client.get("/v1/why", params=params).json()
    for key in ("server", "do_now", "handoff", "where_to_look", "person", "timeline"):
        assert plain.get(key) == caused.get(key), key
    assert plain["server"]["recommendation"] == caused["server"]["recommendation"]


def test_reporting_the_same_cause_again_and_again_cannot_move_the_answer(db, client):
    """Eleven published reports with the same cause leave the decision where one report with no cause left it."""
    now = datetime.now(timezone.utc)
    seed(db, *observation(HOST, now - timedelta(hours=30), error_class="login_auth", outcome="blocked"))
    set_cause(db, None)
    quiet = lookups(client)
    seed(db, *[stmt for hour in range(2, 12) for stmt in observation(HOST, now - timedelta(hours=hour), error_class="login_auth", outcome="blocked")])
    set_cause(db, "verification_on_person_device")
    loud = lookups(client)
    for a, b in zip(quiet, loud):
        for field in ("recommendation", "next_action", "cause", "failure_stage", "next_checks"):
            assert a.get(field) == b.get(field), field


# --- links.why ----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("params", [{"error_class": "login_auth"}, {"http_status": "429"}, {"challenge": "header"},
                                    {"failure_stage": "before_target_response"}, {"retry_after": "60_3600"}])
def test_links_why_is_on_a_failed_lookup_and_is_the_address_the_mcp_line_uses(client, params):
    from ahn import mcp_adapter

    body = client.get("/v1/incidents/check", params={"site_host": HOST, **params}).json()
    assert body["links"]["why"] == mcp_adapter.why_link({"site_host": HOST, **params})
    assert body["links"]["why"].startswith("https://agenthelpnetwork.com/v1/why?site_host=" + HOST)


@pytest.mark.parametrize("params", [{}, {"task_step": "status_check"}, {"agent_vendor": "anthropic"}])
def test_links_why_is_absent_when_the_lookup_sent_no_failure_code(client, params):
    response = client.get("/v1/incidents/check", params={"site_host": HOST, **params})
    assert response.status_code == 200
    assert "why" not in response.json().get("links", {})


def test_a_value_outside_the_error_class_list_is_refused_before_any_link_is_built(client):
    response = client.get("/v1/incidents/check", params={"site_host": HOST, "error_class": "none"})
    assert response.status_code == 400 and "links" not in response.json()


def test_links_why_carries_the_test_mark_from_the_header_like_the_mcp_line(client):
    from ahn import mcp_adapter

    body = client.get("/v1/incidents/check", params={"site_host": HOST, "error_class": "login_auth"}, headers={"X-AHN-Test": "1"}).json()
    assert body["links"]["why"] == mcp_adapter.why_link({"site_host": HOST, "error_class": "login_auth", "ahn_test": "1"})


# --- the operator's queue shows the cause ---------------------------------------------------------------------------------------

def test_the_operator_queue_and_the_monitor_show_a_held_cause_and_how_often_it_was_said(public_client, monkeypatch, capsys):
    """The operator decides on a held cause by whether the same host got the same cause twice in seven days, so the queue shows the
    cause, its basis and that count, and plain reports show a dash."""
    import sys

    from ahn import monitor
    from ahn.cli import main as cli_main

    settings, client = public_client
    host = "queuecause.org"
    ids = [post_public(client, with_cause("platform_refused", site_host=host)).json()["observation_id"] for _ in range(2)]
    plain = post_public(client, sample(site_host="queueplain.org")).json()["observation_id"]
    for observation_id in ids:
        age(settings, observation_id)
    review_batch(TEST_URL)
    for name, value in (("AHN_DATABASE_URL", TEST_URL), ("AHN_KEY_PEPPER", settings.key_pepper), ("AHN_TEST_MODE", "0"), ("AHN_PUBLIC_MODE", "1"),
                        ("AHN_REPORTING_ENABLED", "1")):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["ahn.cli", "pending-reports"])
    cli_main()
    lines = {line.split(" | ")[0]: line for line in capsys.readouterr().out.splitlines() if " | " in line}
    basis = combo("platform_refused")[0]
    assert all(lines[i].endswith(f"cause_review | platform_refused | {basis} | 2") for i in ids), lines
    assert lines[plain].endswith("| None | None | None")
    held = monitor._snapshot(settings)["held_reports"]
    assert {(row["cause"], row["cause_basis"], row["same_cause_7d"]) for row in held} == {("platform_refused", basis, 2)}
