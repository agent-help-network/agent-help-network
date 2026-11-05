"""D93: wording only. A listed developer path is for API work (an order, return or help page goes to the user), the subdomain
re-lookup sentence asks for the same fields, and two cause lines name AHN's own check or read as their source.

The guide before D93 is kept as tests/_pre_d93_response_guide.py; the comparison walks the D85/D87/D92 input space and asserts both
directions: a pair changes only when its row is O1, O7, O7-N, O7-K or U8 and only in summary_line, and no pair outside those rows differs."""

from __future__ import annotations

import copy
import importlib.util
import itertools
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_d63_decision_order as t63  # noqa: E402
import test_response_guide as trg  # noqa: E402
from test_d85_edge_refusal import FULL, GUIDE_MODES, STAGES, rec_of, result_of, variants  # noqa: E402
import _d97_scope  # noqa: E402

from ahn import cause, response_guide, triage  # noqa: E402
from ahn.response_guide import CONSUMER_ROWS, LINE_MAX, ROWS, guide  # noqa: E402
from ahn.triage import failure_observed  # noqa: E402

_spec = importlib.util.spec_from_file_location("pre_d93_response_guide", HERE / "_pre_d93_response_guide.py")
pre_rg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pre_rg)

CHANGED_ROWS = ("O1", "O7", "O7-N", "O7-K", "U8")
OFFICIAL_ROWS = ("O1", "O7", "O7-N", "O7-K")


@pytest.fixture(autouse=True)
def _stand_in_hosts_are_not_reserved(monkeypatch):
    monkeypatch.setattr(response_guide, "is_reserved_example", lambda host: False)
    monkeypatch.setattr(pre_rg, "is_reserved_example", lambda host: False)


def test_only_four_rows_change_and_only_in_summary_line():
    changed = pairs = 0
    seen_rows = set()
    for case in variants(1 if FULL else 29):
        error_class = case.get("error_class")
        for stage, pwf in itertools.product(STAGES, (True, False)):
            rec = rec_of(triage, case, stage, pwf)
            result = result_of(case, rec)
            for mode in GUIDE_MODES:
                _on, _blocked, status_check, _sg = mode
                if status_check and failure_observed(error_class, stage):
                    continue
                for kind in (True,):
                    old = pre_rg.guide(copy.deepcopy(result), *mode, error_class, stage, kind)
                    row0, _ = response_guide.select_row(rec, result["access_profile"], result["provider_status"], result["site_host"],
                                                        result["official_paths"], variants=mode[0])
                    # D97 later changed U6/U7/U9 after a service_error; the rows D93 changed are not among them
                    old = _d97_scope.adjust(old, row0, mode[0], mode[2], error_class, stage, kind, result.get("official_paths"))
                    new = guide(copy.deepcopy(result), *mode, error_class, stage, kind)
                    pairs += 1
                    assert {k: v for k, v in new.items() if k != "summary_line"} == {k: v for k, v in old.items() if k != "summary_line"}, (case, stage, mode)
                    if new == old:
                        continue
                    changed += 1
                    row, _ = response_guide.select_row(rec, result["access_profile"], result["provider_status"], result["site_host"],
                                                       result["official_paths"], variants=mode[0])
                    seen_rows.add(row)
                    assert row in CHANGED_ROWS, (case, stage, mode, row)
                    n = str(new["next_action"]["retry_after_seconds"] or 0)
                    assert new["summary_line"] == ROWS[row][1].replace("{n}", n) and old["summary_line"] == pre_rg.ROWS[row][1].replace("{n}", n), (case, stage, mode)
    print("D93 comparison", pairs, changed, sorted(seen_rows))
    assert pairs > 20000 and changed > 50
    assert {"O1", "O7"} <= seen_rows  # the grid has no O7-N, O7-K or U8 answer: the tests below cover those


def test_the_four_sentences():
    for row in OFFICIAL_ROWS:
        text = ROWS[row][1]
        assert ROWS[row][0] == pre_rg.ROWS[row][0] == "use_official_path"
        assert "API work" in text and "a task in the user's own account" in text and "hand the step to your user" in text, row
        assert "hand it" not in text, row  # no dangling pronoun
    assert "Prefer that documented API or portal over page automation" in ROWS["O7"][1]
    assert "no-sign-up option" in ROWS["O7-N"][1] and "ask before signing up" in ROWS["O7-K"][1] and "{n}" in ROWS["O1"][1]
    assert "a task in the user's own account" in response_guide._CONSUMER_O7  # the same words as the D61/D63 consumer sentence
    assert "once, with the same fields you sent" in ROWS["U8"][1] and "next_action.lookup_host" in ROWS["U8"][1]
    assert ROWS["U8"][0] == pre_rg.ROWS["U8"][0] == "lookup_apex_host"
    for row in CHANGED_ROWS:
        text = ROWS[row][1].replace("{n}", str(response_guide.RETRY_MAX))
        assert len(text) <= LINE_MAX and re.fullmatch(r"[\x20-\x7E]+", text) and "{" not in text and "http" not in text, row
    # nothing else in the table moved
    assert {k: v for k, v in ROWS.items() if k not in CHANGED_ROWS and k not in ("U7-O", "U9-O")} == {k: v for k, v in pre_rg.ROWS.items() if k not in CHANGED_ROWS}
    assert CONSUMER_ROWS == pre_rg.CONSUMER_ROWS and response_guide.PROCEED_OFFICIAL_ROWS == pre_rg.PROCEED_OFFICIAL_ROWS


def test_cause_lines_name_ahn_as_the_source():
    assert cause.LINES["new_bot_defense"].startswith("Likely a policy block: AHN's home-page check sees a bot-defense page from a new provider {win}")
    assert "what your tool sees may differ" in cause.LINES["new_bot_defense"]
    assert cause.LINES["robots_now_disallows"].startswith("Likely a policy block: AHN's read of the site's robots.txt shows it now disallows")
    for key, text in cause.LINES.items():
        assert "the home page now" not in text, key
    start = datetime(2026, 11, 3, 0, 36, tzinfo=timezone.utc)
    for key in ("new_bot_defense", "robots_now_disallows"):
        line = cause.line_for(key, start)
        assert "(since 11-03 00:36 UTC)" in line and len(line) <= cause.LINE_MAX and line.isascii(), key


def test_a_cause_changes_only_in_its_line(monkeypatch):
    start = datetime(2026, 11, 3, 0, 36, tzinfo=timezone.utc)
    old_lines = dict(cause.LINES)
    old_lines["new_bot_defense"] = "Likely a policy block: the home page now shows a bot-defense page from a new provider {win}."
    old_lines["robots_now_disallows"] = "Likely a policy block: the site's robots.txt now disallows this kind of agent {win}."
    for key in ("new_bot_defense", "robots_now_disallows"):
        new = cause._cause("policy_closed", key, "medium", ["host_access_changes"], key, start=start)
        with monkeypatch.context() as m:
            m.setattr(cause, "LINES", old_lines)
            old = cause._cause("policy_closed", key, "medium", ["host_access_changes"], key, start=start)
        assert {k: v for k, v in new.items() if k != "line"} == {k: v for k, v in old.items() if k != "line"}
        assert new["line"] != old["line"]


def test_documents_name_the_rule():
    for name in ("api-contract.en.md", "api-contract.md"):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert "D93" in text and "once, with the same fields you sent" in text and "ask before signing up" in text, name
    assert "| D93 " in (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")


def only_the_sentence_differs(result, row, **kwargs):
    old = pre_rg.guide(copy.deepcopy(result), **kwargs)
    new = guide(copy.deepcopy(result), **kwargs)
    assert {k: v for k, v in new.items() if k != "summary_line"} == {k: v for k, v in old.items() if k != "summary_line"}
    assert old["summary_line"] == pre_rg.ROWS[row][1] and new["summary_line"] == ROWS[row][1] != old["summary_line"]
    return new


@pytest.mark.parametrize("official,row", [(t63.OFFICIAL, "O7"), (t63.OFFICIAL_N, "O7-N"), (t63.OFFICIAL_K, "O7-K")])
def test_listed_developer_path_rows_change_the_sentence_and_nothing_else(official, row):
    for error_class, stage in (("site_access", None), ("service_error", "before_target_response"), ("service_error", "response_observed")):
        result = t63.answer(official, t63.ALLOWS, ec=error_class, fs=stage)
        assert t63.row_of(result) == row, (row, error_class, stage)
        new = only_the_sentence_differs(result, row, error_class=error_class, failure_stage=stage, failure_kind_lines=True)
        assert new["next_action"]["do"] == "use_official_path" and new["next_action"]["url"] == official["api_docs"]
    # a curated customer path keeps its D61/D63 sentence
    result = t63.answer({**official, "consumer": t63.CONSUMER}, t63.ALLOWS, ec="site_access")
    assert guide(copy.deepcopy(result)) == pre_rg.guide(copy.deepcopy(result))


def test_subdomain_lookup_sentence_asks_for_the_same_error_fields():
    result = trg.answer("app.example.com", not_read=True)
    for kwargs in ({}, {"error_class": "service_error", "failure_stage": "before_target_response", "failure_kind_lines": True}):
        new = only_the_sentence_differs(result, "U8", **kwargs)
        assert new["next_action"] == {"do": "lookup_apex_host", "url": None, "retry_after_seconds": None, "lookup_host": "example.com"}


def test_o1_has_the_same_clause_and_keeps_its_retry():
    result = t63.answer(t63.OFFICIAL, t63.PENDING, ec="site_access")
    assert t63.row_of(result) == "O1"
    new = guide(copy.deepcopy(result))
    old = pre_rg.guide(copy.deepcopy(result))
    assert {k: v for k, v in new.items() if k != "summary_line"} == {k: v for k, v in old.items() if k != "summary_line"}
    assert new["next_action"]["retry_after_seconds"] == 120 and "look up once more in 120 s" in new["summary_line"] and "hand the step to your user" in new["summary_line"]
    assert len(ROWS["O1"][1].replace("{n}", str(response_guide.RETRY_MAX))) <= LINE_MAX


def test_profile_not_read_check_asks_for_the_same_fields():
    text = triage.CHECKS["profile_not_read"]
    assert "once, with the same fields you sent" in text and "site-wide declaration" in text and len(text) < 400


def test_examples_in_the_docs_are_the_current_o7_n_sentence():
    line = ROWS["O7-N"][1]
    for rel in ("README.md", "README.ko.md", "ahn/templates/agents.html", "docs/api-contract.en.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert f'"summary_line": "{line}"' in text, rel
    for rel in ("README.md", "docs/api-contract.en.md"):
        assert "current sentence of its row" in (ROOT / rel).read_text(encoding="utf-8"), rel
