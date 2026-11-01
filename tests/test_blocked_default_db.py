"""D66 against the dedicated ahn_test database: the task_step words and the observation filter, the usable-path gate, the
search page view, the feedback token and the home card."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_home_example_db import OFFICIAL as GITHUB, counts, prepare, refuse_network, seed  # noqa: E402

from ahn import service  # noqa: E402
from ahn.db import connect  # noqa: E402
from ahn.service import PUBLIC_REPORTER_ID  # noqa: E402
from ahn.response_guide import BLOCKED_PROCEED_ROWS, PROCEED_OFFICIAL_ROWS  # noqa: E402


def strip(answer: dict) -> dict:
    return {k: v for k, v in answer.items() if k != "generated_at"}


def mode(base, name):
    return replace(base, proceed_mode=name)


def test_default_mode_is_d63_and_d65_changes_only_a_lookup_that_reports_no_failure(monkeypatch):
    base = prepare(monkeypatch)
    seed(base, "github.com")
    d63, d65 = mode(base, "d63"), mode(base, "d65")
    assert base.proceed_mode == "d63"
    old = service.check(d63, "github.com")
    new = service.check(d65, "github.com")
    assert old["recommendation"]["action"] == "proceed" and old["summary_line"] == PROCEED_OFFICIAL_ROWS["P1"]
    assert new["recommendation"]["action"] == "use_official_path" and new["next_action"]["url"] == GITHUB["api_docs"]
    # A reported failure, and a status_check or preflight lookup, are the D63 answers.
    assert strip(service.check(d65, "github.com", "site_access")) == strip(service.check(d63, "github.com", "site_access"))
    for word in ("status_check", "preflight"):
        assert strip(service.check(d65, "github.com", task_step=word)) == strip(service.check(d63, "github.com", task_step=word)), word
    # failure_stage alone is a failure signal and beats the word
    assert strip(service.check(d65, "github.com", failure_stage="before_target_response", task_step="status_check")) == \
        strip(service.check(d63, "github.com", failure_stage="before_target_response", task_step="status_check"))
    # a near-miss word is an ordinary step: blocked is assumed
    assert service.check(d65, "github.com", task_step="statuscheck")["recommendation"]["action"] == "use_official_path"


def test_a_host_without_a_listed_api_still_proceeds_with_the_new_sentence(monkeypatch):
    base = prepare(monkeypatch)
    seed(base, "example.org")
    answer = service.check(mode(base, "d65"), "example.org")
    assert answer["recommendation"]["action"] == "proceed"
    assert answer["summary_line"] in BLOCKED_PROCEED_ROWS.values() and "tell your user" in answer["summary_line"]
    assert "feedback_token" not in answer  # still needs an error_class (D58)


def test_the_home_card_shows_the_answer_an_agent_gets_and_nothing_is_written(monkeypatch):
    base = prepare(monkeypatch)
    seed(base, "target.com")
    refuse_network(monkeypatch)
    before = counts(base)
    assert service.home_example(mode(base, "d63"))["do"] == "proceed"
    card = service.home_example(mode(base, "d65"))
    assert card["do"] == "use_official_path" and card["site_host"] == "target.com"
    assert counts(base) == before


def add_observation(settings, host, task_step="site_open"):
    with connect(settings) as conn:
        incident = conn.execute("INSERT INTO incidents (site_host,task_step,error_class,visibility) VALUES (%s,%s,'site_access','published') "
                                "ON CONFLICT (site_host,task_step,error_class) DO UPDATE SET site_host=EXCLUDED.site_host RETURNING id",
                                (host, task_step)).fetchone()["id"]
        conn.execute("""INSERT INTO observations (incident_id,reporter_id,observed_at,agent_family,attempt_actor,attempt_env,outcome,
                                                  challenge_vendor,challenge_kind,review_state,synthetic)
                        VALUES (%s,%s,now(),'other','agent','agent_cloud_browser','blocked','none','none','published',false)""",
                     (incident, PUBLIC_REPORTER_ID))


def test_the_reserved_words_filter_observations_under_d63_but_not_under_d65(monkeypatch):
    base = prepare(monkeypatch)
    seed(base, "github.com")
    add_observation(base, "github.com", "site_open")
    d63, d65 = mode(base, "d63"), mode(base, "d65")
    assert len(service.check(d63, "github.com", task_step="site_open")["observations"]) == 1
    assert service.check(d63, "github.com", task_step="status_check")["observations"] == []  # D63: an ordinary step filter
    assert len(service.check(d65, "github.com", task_step="status_check")["observations"]) == 1  # d65: a purpose, not a filter
    assert len(service.check(d65, "github.com", task_step="preflight")["observations"]) == 1
    assert service.check(d65, "github.com", task_step="statuscheck")["observations"] == []  # a near miss is an ordinary step


def test_a_host_whose_listed_path_nobody_can_use_is_not_assumed_blocked(monkeypatch):
    base = prepare(monkeypatch)
    seed(base, "amazon.com")  # robots clear for every vendor in the stored file
    d63, d65 = mode(base, "d63"), mode(base, "d65")
    for vendor in (None, "google"):
        old = service.check(d63, "amazon.com", agent_vendor=vendor)
        new = service.check(d65, "amazon.com", agent_vendor=vendor)
        assert old["recommendation"]["action"] == "proceed"
        assert new["recommendation"] == old["recommendation"] and new["next_checks"] == old["next_checks"], vendor
    # a lookup that reports a failure is unchanged either way, and a host with no entry in the table has no gate
    assert service.check(d65, "amazon.com", "site_access")["recommendation"] == service.check(d63, "amazon.com", "site_access")["recommendation"]
    seed(base, "github.com")
    assert service.check(d65, "github.com")["recommendation"]["action"] == "use_official_path"


def test_the_search_page_view_is_answered_like_status_check_until_a_class_is_chosen(monkeypatch):
    base = prepare(monkeypatch)
    seed(base, "github.com")
    d63, d65 = mode(base, "d63"), mode(base, "d65")
    plain = service.check(d65, "github.com", plain_view=True)
    assert strip(plain) == strip(service.check(d63, "github.com"))
    assert service.check(d65, "github.com", "site_access", plain_view=False)["recommendation"]["action"] == "use_official_path"
    assert service.check(d65, "github.com")["recommendation"]["action"] == "use_official_path"  # an agent without the word


def test_a_no_signal_lookup_carries_no_feedback_token_and_a_failed_one_still_does(monkeypatch):
    base = prepare(monkeypatch, collect_lookup_metrics=True)
    seed(base, "github.com")
    d65 = mode(base, "d65")
    quiet = service.check(d65, "github.com", issue_feedback=True)
    assert quiet["recommendation"]["action"] == "use_official_path" and "feedback_token" not in quiet  # D58: needs error_class
    failed = service.check(d65, "github.com", "site_access", issue_feedback=True)
    assert "feedback_token" in failed
