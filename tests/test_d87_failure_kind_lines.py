"""D87: the first line (summary_line) of a lookup that reports a failure follows the kind of failure. Only the sentence of two kinds of row
changes: a proceed row when error_class is service_error, and U3 when there is any failure signal. Nothing else in the answer changes.

Pure: no database (the REST path is in test_d87_failure_kind_lines_db.py). The guide before D87 is kept as tests/_pre_d87_response_guide.py.
The comparison walks the same input space as the D85 test (the grid of test_response_guide.space(), each case with and without a curated
consumer path, every failure stage and error class the grid carries, the four guide modes) and asserts both directions: no pair outside the D87
condition differs, and every pair inside it does, with the sentence the table gives and with next_action and links equal."""

from __future__ import annotations

import copy
import importlib.util
import itertools
import logging
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_d85_edge_refusal import ABSENT, ALLOWS, FORBIDDEN, FULL, GUIDE_MODES, STAGES, rec_of, result_of, variants  # noqa: E402
from test_response_guide import block, robots  # noqa: E402

from ahn import response_guide, settings as settings_module, triage  # noqa: E402
from ahn.response_guide import FAILED_U3, FAILED_U3_SERVICE_ERROR, LINE_MAX, SERVICE_ERROR_CONSUMER_ROWS, SERVICE_ERROR_ROWS, guide  # noqa: E402
from ahn.settings import Settings, parse_failure_kind_switch  # noqa: E402
from ahn.triage import failure_observed  # noqa: E402

_spec = importlib.util.spec_from_file_location("pre_d87_response_guide", HERE / "_pre_d87_response_guide.py")
pre_rg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pre_rg)
# D93 changed only these sentences since the snapshot; take them as they are now so the comparison keeps covering everything else
pre_rg.ROWS.update({row: response_guide.ROWS[row] for row in ("O7", "O7-N", "O7-K", "U8") if row in pre_rg.ROWS})

P2_PROFILE = robots(block("Claude-SearchBot"))
U3_PROFILE = robots("User-agent: *\nAllow: /\n\nUser-agent: Claude-User\nDisallow: /\n")


@pytest.fixture(autouse=True)
def _stand_in_hosts_are_not_reserved(monkeypatch):
    monkeypatch.setattr(response_guide, "is_reserved_example", lambda host: False)
    monkeypatch.setattr(pre_rg, "is_reserved_example", lambda host: False)


def expected_sentence(row: str, error_class: str | None, customer: bool = False, stage: str | None = None):
    """The sentence the D87 condition gives for a row, else None (the row is outside the condition). Written from the contract, not from guide()."""
    if error_class == "service_error" and stage == "before_target_response":
        # D92: no response at all is not a 5xx; tests/test_d92_no_response_lines.py holds those sentences
        if row.startswith("P"):
            return (response_guide.NO_RESPONSE_CONSUMER_ROWS if customer else response_guide.NO_RESPONSE_ROWS)[row]
        return response_guide.FAILED_U3_NO_RESPONSE if row == "U3" else None
    if row.startswith("P") and error_class == "service_error":
        return (SERVICE_ERROR_CONSUMER_ROWS if customer else SERVICE_ERROR_ROWS)[row]
    if row == "U3":
        return FAILED_U3_SERVICE_ERROR if error_class == "service_error" else FAILED_U3
    return None


def compare(stride: int) -> dict:
    """Walk the grid; assert both directions at every pair; return the counts."""
    counts = dict(cases=0, pairs=0, changed=0, service_error_changed=0, service_error_customer_changed=0, u3_changed=0)
    for case in variants(stride):
        counts["cases"] += 1
        for stage, pwf in itertools.product(STAGES, (True, False)):
            error_class = case.get("error_class")  # the grid carries every error class
            case_e = case
            rec = rec_of(triage, case_e, stage, pwf)
            result = result_of(case_e, rec)
            for mode in GUIDE_MODES:
                on, _blocked, status_check, _status_guide = mode
                if status_check and failure_observed(error_class, stage):
                    continue  # the service never sends a status_check lookup that reports a failure
                old = pre_rg.guide(copy.deepcopy(result), *mode)
                new = guide(copy.deepcopy(result), *mode, error_class, stage, True)
                off = guide(copy.deepcopy(result), *mode, error_class, stage, False)
                counts["pairs"] += 1
                assert off == old, ("switch off", case_e, stage, mode)
                row, _listed = pre_rg.select_row(rec, result["access_profile"], result["provider_status"], result["site_host"],
                                                 result["official_paths"], variants=on)
                curated = bool(response_guide.customer_url(result.get("official_paths")))  # the curated consumer path of the case
                want = expected_sentence(row, error_class, curated, stage)
                inside = bool(on and not status_check and failure_observed(error_class, stage) and want is not None)
                if not inside:
                    assert new == old, (case_e, stage, mode)
                    continue
                counts["changed"] += 1
                counts["service_error_changed" if row.startswith("P") else "u3_changed"] += 1
                counts["service_error_customer_changed"] += int(row.startswith("P") and curated)
                assert new["summary_line"] == want and new["summary_line"] != old["summary_line"], (case_e, stage, mode, row)
                assert new["next_action"] == old["next_action"] and new.get("links") == old.get("links"), (case_e, stage, mode)
                assert {k: v for k, v in new.items() if k != "summary_line"} == {k: v for k, v in old.items() if k != "summary_line"}
    return counts


def test_the_pairs_that_change_are_exactly_service_error_proceed_and_failed_u3_in_the_whole_grid():
    counts = compare(1 if FULL else 29)
    print("D87 comparison", counts)
    assert counts["service_error_changed"] > 100 and counts["u3_changed"] > 100 and counts["service_error_customer_changed"] > 10
    assert counts["pairs"] > 20000


# --- the rows -------------------------------------------------------------------------------------------------------------

def answer(profile, *, error_class, stage=None, vendor=None, official=None):
    case = {"access_profile": profile, "official": official, "vendor": vendor, "error_class": error_class}
    return result_of(case, rec_of(triage, case, stage, True))


def lookup(result, error_class, stage=None, *, kind=True, blocked=False):
    return guide(copy.deepcopy(result), True, blocked, False, True, error_class, stage, kind)


@pytest.mark.parametrize("profile,vendor,row", [(ALLOWS, None, "P1"), (ALLOWS, "anthropic", "P1-V"), (P2_PROFILE, None, "P2"),
                                                (P2_PROFILE, "anthropic", "P2-V"), (ABSENT, None, "P3")])
@pytest.mark.parametrize("blocked", [False, True])
def test_every_proceed_row_gets_the_5xx_sentence_for_service_error_and_keeps_everything_else(profile, vendor, row, blocked):
    result = answer(profile, error_class="service_error", vendor=vendor)
    new, old = lookup(result, "service_error", blocked=blocked), lookup(result, "service_error", kind=False, blocked=blocked)
    assert response_guide.select_row(result["recommendation"], profile, None, "example.com", variants=True)[0] == row
    assert new["summary_line"] == SERVICE_ERROR_ROWS[row] and old["summary_line"] != new["summary_line"]
    assert new["next_action"] == old["next_action"] and new["next_action"]["do"] == "proceed"


def test_the_5xx_sentence_keeps_the_way_to_the_person_when_a_customer_path_is_curated():
    help_url = "https://www.example.com/help"
    curated = {"name": "Example", "api_docs": None, "developer_portal": None, "note": None,
               "consumer": {"paths": [{"kind": "customer_help", "url": help_url, "source_url": "https://www.example.com/"}],
                            "verified_at": "2026-10-31"}}
    for official in (None, curated):
        result = answer(ALLOWS, error_class="service_error", official=official)
        new, old = lookup(result, "service_error"), lookup(result, "service_error", kind=False)
        assert new["next_action"] == old["next_action"]
        if official is curated:
            assert new["next_action"]["customer_url"] == help_url
            assert new["summary_line"] == SERVICE_ERROR_CONSUMER_ROWS["P1"]
            assert new["summary_line"].endswith("tell your user and hand them next_action.customer_url.")
        else:
            assert "customer_url" not in new["next_action"] and new["summary_line"] == SERVICE_ERROR_ROWS["P1"]


@pytest.mark.parametrize("error_class", ["site_access", "human_verification", "login_auth"])
def test_a_proceed_row_for_any_other_error_class_keeps_its_sentence(error_class):
    result = answer(ALLOWS, error_class=error_class)
    assert lookup(result, error_class, "response_observed") == lookup(result, error_class, "response_observed", kind=False)


def test_a_failure_stage_alone_does_not_make_a_proceed_row_a_5xx_but_service_error_with_a_stage_does():
    assert lookup(answer(ALLOWS, error_class="service_error"), "service_error", "response_observed")["summary_line"] == SERVICE_ERROR_ROWS["P1"]
    stage_only = answer(ALLOWS, error_class=None, stage="response_observed")
    assert lookup(stage_only, None, "response_observed") == lookup(stage_only, None, "response_observed", kind=False)


@pytest.mark.parametrize("error_class,stage,want", [("site_access", None, FAILED_U3), ("service_error", None, FAILED_U3_SERVICE_ERROR),
                                                    ("site_access", "response_observed", FAILED_U3), (None, "access_denial_observed", FAILED_U3),
                                                    (None, "unknown", FAILED_U3), ("service_error", "unknown", FAILED_U3_SERVICE_ERROR)])
def test_u3_with_any_failure_signal_follows_the_kind_of_failure(error_class, stage, want):
    result = answer(U3_PROFILE, error_class=error_class, stage=stage)
    assert result["recommendation"]["action"] == "unknown"
    assert response_guide.select_row(result["recommendation"], U3_PROFILE, None, "example.com")[0] == "U3"
    new, old = lookup(result, error_class, stage), lookup(result, error_class, stage, kind=False)
    assert new["summary_line"] == want
    assert "agent_vendor" not in new["summary_line"] and "else send" not in new["summary_line"]
    assert new["next_action"] == old["next_action"] and new["next_action"]["do"] == "stop_if_listed" and new["next_action"]["stop_if_you_are"]


def test_u3_without_a_failure_signal_keeps_the_sentence_that_tells_to_send_agent_vendor():
    result = answer(U3_PROFILE, error_class=None)
    new = lookup(result, None)
    assert new == lookup(result, None, kind=False) and new["summary_line"].endswith("else send agent_vendor.")


def test_the_d63_switch_off_keeps_the_sentences_before_d63_whatever_the_new_switch_says():
    for profile, error_class in ((ALLOWS, "service_error"), (U3_PROFILE, "site_access")):
        result = answer(profile, error_class=error_class)
        assert guide(copy.deepcopy(result), False, False, False, False, error_class, None, True) == \
            pre_rg.guide(copy.deepcopy(result), False, False, False, False)


def test_a_status_check_lookup_without_a_failure_signal_keeps_its_status_sentence():
    # service.py passes status_check=False when a failure signal is present (D69), so the reachable combination is status_check without a signal.
    result = answer(U3_PROFILE, error_class=None)
    assert guide(copy.deepcopy(result), True, False, True, True, None, None, True) ==         pre_rg.guide(copy.deepcopy(result), True, False, True, True)


def test_the_guard_for_status_check_with_a_failure_value_is_defensive_code_the_service_does_not_reach():
    result = answer(U3_PROFILE, error_class=None)
    assert guide(copy.deepcopy(result), True, False, True, True, "service_error", None, True) ==         pre_rg.guide(copy.deepcopy(result), True, False, True, True)


def test_the_default_arguments_give_the_answer_before_d87():
    result = answer(ALLOWS, error_class="service_error")
    assert guide(copy.deepcopy(result)) == pre_rg.guide(copy.deepcopy(result))


# --- the sentences ---------------------------------------------------------------------------------------------------------

def test_the_new_sentences_are_short_plain_ascii_without_host_url_placeholder_or_a_way_around_a_refusal():
    assert set(SERVICE_ERROR_ROWS) == {"P1", "P1-V", "P2", "P2-V", "P3"}
    assert set(SERVICE_ERROR_CONSUMER_ROWS) == set(SERVICE_ERROR_ROWS)
    for text in (*SERVICE_ERROR_ROWS.values(), *SERVICE_ERROR_CONSUMER_ROWS.values(), FAILED_U3, FAILED_U3_SERVICE_ERROR):
        assert len(text) <= LINE_MAX and re.fullmatch(r"[\x20-\x7E]+", text), text
        assert "{" not in text and "http" not in text and not re.search("|".join(FORBIDDEN), text, re.I), text
    for text in (*SERVICE_ERROR_ROWS.values(), *SERVICE_ERROR_CONSUMER_ROWS.values()):
        # a 5xx is not a refusal: the old "came from your tool, the site's edge or a login" is gone
        assert not re.search(r"refus|your tool|edge|login|sign-in", text, re.I), text
        assert "5xx" in text and "Retry once later" in text and "tell your user" in text
    assert "robots.agents_blocked" in SERVICE_ERROR_ROWS["P2"] and "robots.agents_blocked" in SERVICE_ERROR_CONSUMER_ROWS["P2"]
    assert all("next_action.customer_url" in text for text in SERVICE_ERROR_CONSUMER_ROWS.values())
    assert not any("customer_url" in text for text in SERVICE_ERROR_ROWS.values())
    # robots.txt may block tokens outside next_action.stop_if_you_are (only listed vendors' root blocks are listed, at most 16): never say "only"
    for text in (FAILED_U3, FAILED_U3_SERVICE_ERROR):
        assert not re.search(r"only", text) and "some tokens, including those in next_action.stop_if_you_are" in text
    assert "do not switch tools or user agents" in FAILED_U3 and "stop_if_you_are" in FAILED_U3 and "Retry" not in FAILED_U3
    assert "5xx" in FAILED_U3_SERVICE_ERROR and "switch" not in FAILED_U3_SERVICE_ERROR


def test_the_tables_the_new_sentences_replace_are_untouched():
    assert response_guide.ROWS == pre_rg.ROWS and response_guide.ROWS_BEFORE == pre_rg.ROWS_BEFORE
    assert response_guide.BLOCKED_PROCEED_ROWS == pre_rg.BLOCKED_PROCEED_ROWS
    assert response_guide.PROCEED_OFFICIAL_ROWS == pre_rg.PROCEED_OFFICIAL_ROWS
    assert response_guide.PROCEED_CONSUMER_ROWS == pre_rg.PROCEED_CONSUMER_ROWS


# --- the switch --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [("", True), ("1", True), (" 1 ", True), ("0", False), (" 0 ", False), ("off", False), ("true", False),
                                          ("2", False), ("yes", False), ("d87", False)])
def test_the_switch_reads_unknown_values_as_off(raw, expected, monkeypatch):
    monkeypatch.setattr(settings_module, "_KIND_WARNED", False)
    assert parse_failure_kind_switch(raw) is expected


def test_an_unknown_value_warns_once_with_the_code_only(caplog, monkeypatch):
    monkeypatch.setattr(settings_module, "_KIND_WARNED", False)
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            parse_failure_kind_switch("sure-d87-secret-looking-value")
    messages = [record.getMessage() for record in caplog.records if "failure_kind" in record.getMessage()]
    assert messages == ["failure_kind_switch_unknown_value"]
    assert "sure-d87" not in caplog.text


def test_from_env_reads_the_switch(monkeypatch):
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://u:p@localhost/x")
    monkeypatch.setenv("AHN_KEY_PEPPER", "x" * 40)
    monkeypatch.setattr(settings_module, "_KIND_WARNED", False)
    monkeypatch.delenv("AHN_FAILURE_KIND_LINES", raising=False)
    assert Settings.from_env().failure_kind_lines is True
    monkeypatch.setenv("AHN_FAILURE_KIND_LINES", "0")
    assert Settings.from_env().failure_kind_lines is False
    monkeypatch.setenv("AHN_FAILURE_KIND_LINES", "1")
    assert Settings.from_env().failure_kind_lines is True
    assert Settings(database_url="x", key_pepper="y").failure_kind_lines is True


# --- documents ---------------------------------------------------------------------------------------------------------------

def test_the_contracts_and_the_decision_log_say_the_new_rule():
    en = (ROOT / "docs" / "api-contract.en.md").read_text(encoding="utf-8")
    ko = (ROOT / "docs" / "api-contract.md").read_text(encoding="utf-8")
    for name, text in (("en", en), ("ko", ko)):
        assert "AHN_FAILURE_KIND_LINES=0" in text and "D87" in text and "SERVICE_ERROR_ROWS" in text, name
    decisions = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    assert "| D87 " in decisions and "AHN_FAILURE_KIND_LINES" in decisions
    assert "SERVICE_ERROR_CONSUMER_ROWS" in en and "SERVICE_ERROR_CONSUMER_ROWS" in ko and "SERVICE_ERROR_CONSUMER_ROWS" in decisions
    assert "robots.txt blocks only the tokens" not in en + ko + decisions
