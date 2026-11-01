"""D66 against the dedicated ahn_test database: the task_step words, the observation filter and the home card."""

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
