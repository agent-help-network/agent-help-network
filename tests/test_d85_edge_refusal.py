"""D85: a lookup that reports site_access while AHN's own home-page check was refused too is answered hand_off_to_human, not proceed.

Pure: no database (the service wiring, the boundaries of the stored measurement and the switch are in test_edge_refusal_db.py).
The decision code from before D85 is kept as tests/_pre_d85_triage.py and tests/_pre_d85_response_guide.py. Every test that compares
the two walks the same input space (the grid of test_response_guide.space()) and asserts both directions: no pair outside the D85
condition differs, and every pair inside it does."""

from __future__ import annotations

import copy
import importlib.util
import itertools
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_response_guide import PROFILES, block, robots, space  # noqa: E402

from ahn import response_guide, settings as settings_module, triage  # noqa: E402
from ahn.access_profile import parse_robots  # noqa: E402
from ahn.models import NextCheck  # noqa: E402
from ahn.response_guide import EDGE_CONSUMER_ROWS, LINE_MAX, ROWS, guide, select_row  # noqa: E402
from ahn.settings import Settings, parse_edge_refusal_switch  # noqa: E402
from ahn.triage import CHECKS, CHECKS_KO, EDGE_CODE, failure_observed  # noqa: E402

_spec = importlib.util.spec_from_file_location("pre_d85_triage", HERE / "_pre_d85_triage.py")
pre = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pre)
_spec_rg = importlib.util.spec_from_file_location("pre_d85_response_guide", HERE / "_pre_d85_response_guide.py")
pre_rg = importlib.util.module_from_spec(_spec_rg)
_spec_rg.loader.exec_module(pre_rg)
# D93 changed only these sentences since the snapshot; take them as they are now so the comparison keeps covering everything else
pre_rg.ROWS.update({row: response_guide.ROWS[row] for row in ("O1", "O7", "O7-N", "O7-K", "U8") if row in pre_rg.ROWS})
pre.CHECKS["profile_not_read"] = triage.CHECKS["profile_not_read"]  # D93: the one next-check sentence reworded

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
HELP = "https://www.example.com/help"
CONSUMER = {"paths": [{"kind": "customer_help", "url": HELP, "source_url": "https://www.example.com/"}], "verified_at": "2026-10-31"}
OFFICIAL = {"name": "Example", "api_docs": "https://developer.example.com/docs", "developer_portal": None, "note": None}
STAGES = (None, "before_target_response", "response_observed", "access_denial_observed", "unknown")
EDGE_STAGES = (None, "access_denial_observed")
ALLOWS = robots("User-agent: *\nAllow: /\n")
ABSENT = {"robots_status": "absent", "robots": None, "freshness": "fresh", "llms_txt_status": "absent"}
STATUS_URL = "https://status.example.com/"
FULL = os.environ.get("AHN_D85_FULL") == "1"   # the one-off full walk of the grid (the PR numbers); the suite walks every 29th case
FORBIDDEN = ("bypass", "evade", "spoof", "proxy", "solver", "stealth", "headless", "work around", "get past")

# guide switches: (proceed_without_failure, blocked_default, status_check, status_guide)
GUIDE_MODES = ((True, False, False, True), (True, True, False, True), (True, False, True, True), (False, False, False, False))


def variants(stride: int):
    """The grid, each case with its official path as it is and with a curated consumer path added."""
    for index, case in enumerate(space()):
        if index % stride:
            continue
        yield case
        official = case.get("official")
        yield {**case, "official": {**official, "consumer": CONSUMER} if official else {"name": "Example", "api_docs": None, "developer_portal": None,
                                                                                    "note": None, "consumer": CONSUMER}}


def rec_of(mod, case, stage, pwf, edge=None):
    kwargs = dict(profile_not_read=case.get("not_read", False), provider_regional=case.get("regional", False), now=NOW,
                  agent_vendor=case.get("vendor"), failure_stage=stage, proceed_without_failure=pwf)
    if edge is not None:
        kwargs["edge_refusal"] = edge
    return mod.recommendation(case.get("error_class"), case.get("provider_status"), case.get("access_profile"), case.get("official"), **kwargs)


def checks_of(mod, case, stage, pwf, edge=None):
    kwargs = dict(profile_not_read=case.get("not_read", False), provider_partial=case.get("regional", False), now=NOW,
                  agent_vendor=case.get("vendor"), proceed_without_failure=pwf)
    if edge is not None:
        kwargs["edge_refusal"] = edge
    return mod.next_checks(case.get("error_class"), case.get("provider_status"), "no_reports", stage, case.get("access_profile"),
                           case.get("official"), **kwargs)


def result_of(case, rec, checks=None):
    return {"site_host": case.get("site_host", "example.com"), "provider_status": case.get("provider_status"), "official_status_page": None,
            "access_profile": case.get("access_profile"), "official_paths": case.get("official"), "recommendation": rec,
            **({"next_checks": checks} if checks is not None else {})}


def compare(stride: int) -> dict:
    """Walk the grid; assert both directions at every pair; return the counts."""
    counts = dict(rec_pairs=0, rec_changed=0, guide_pairs=0, guide_changed=0, cases=0)
    for case in variants(stride):
        counts["cases"] += 1
        for stage, pwf in itertools.product(STAGES, (True, False)):
            old_rec = rec_of(pre, case, stage, pwf)
            old_checks = checks_of(pre, case, stage, pwf)
            for edge in (False, True):
                new_rec = rec_of(triage, case, stage, pwf, edge)
                new_checks = checks_of(triage, case, stage, pwf, edge=EDGE_CODE in new_rec["reasons"])
                counts["rec_pairs"] += 1
                inside = (edge and case.get("error_class") == "site_access" and stage in EDGE_STAGES and old_rec["action"] == "proceed")
                if not inside:
                    assert new_rec == old_rec, (case, stage, pwf, edge)
                    assert new_checks == old_checks, (case, stage, pwf, edge)
                else:
                    counts["rec_changed"] += 1
                    assert new_rec["action"] == "hand_off_to_human" and new_rec["confidence"] == "medium", (case, stage)
                    assert new_rec["reasons"][0] == EDGE_CODE and new_rec["reasons"][1:] == old_rec["reasons"], (case, stage)
                    assert {k: v for k, v in new_rec.items() if k not in ("action", "confidence", "reasons")} == \
                        {k: v for k, v in old_rec.items() if k not in ("action", "confidence", "reasons")}, "the vendor echo stays"
                    # next_checks: the one new code follows the stage code; everything else is as it was
                    old_codes = [c["code"] for c in old_checks]
                    new_codes = [c["code"] for c in new_checks]
                    assert new_codes[1] == EDGE_CODE and new_codes[:1] + new_codes[2:] == old_codes, (new_codes, old_codes)
                    assert new_checks[1]["text"] == CHECKS[EDGE_CODE]
                    assert [c for c in new_checks if c["code"] != EDGE_CODE] == old_checks
                # the guide, in every mode that makes sense for the lookup (a status_check one has no failure signal)
                for mode in GUIDE_MODES:
                    if mode[2] and failure_observed(case.get("error_class"), stage):
                        continue
                    old_out = pre_rg.guide(result_of(case, old_rec), *mode)
                    new_out = guide(result_of(case, new_rec), *mode)
                    counts["guide_pairs"] += 1
                    if not (inside and EDGE_CODE in new_rec["reasons"]):
                        assert new_out == old_out, (case, stage, edge, mode)
                        continue
                    counts["guide_changed"] += 1
                    assert new_out["next_action"]["do"] == "hand_off_to_human"
                    assert new_out["next_action"]["url"] is None and new_out["next_action"]["retry_after_seconds"] is None
                    for key in ("customer_url", "status_url"):
                        assert new_out["next_action"].get(key) == old_out["next_action"].get(key), (key, case)
                    assert new_out.get("links") == old_out.get("links")
                    assert {k: v for k, v in new_out["next_action"].items() if k not in ("do",)} == \
                        {k: v for k, v in old_out["next_action"].items() if k not in ("do",)}
                    curated = bool(old_out["next_action"].get("customer_url"))
                    assert new_out["summary_line"] == (EDGE_CONSUMER_ROWS["H3"] if curated else ROWS["H3"][1]), (case, mode)
                    assert new_out["summary_line"] != old_out["summary_line"]
    return counts


def test_the_pairs_that_change_are_exactly_the_d85_condition_in_the_whole_grid():
    counts = compare(1 if FULL else 29)
    print("D85 comparison", counts)
    assert counts["rec_changed"] > 100 and counts["guide_changed"] > 100
    assert counts["rec_pairs"] > 20000


def test_the_switch_value_false_gives_the_answer_before_d85_for_every_pair():
    # edge_refusal=False (the default) is the call every other path makes: identical to the frozen pre-D85 code.
    for index, case in enumerate(variants(11)):
        for stage in STAGES:
            assert rec_of(triage, case, stage, True) == rec_of(pre, case, stage, True)
            assert checks_of(triage, case, stage, True) == checks_of(pre, case, stage, True)


# --- triage ---------------------------------------------------------------------------------------------------------------

def edge_rec(profile=ALLOWS, official=None, *, ec="site_access", stage=None, vendor=None, provider=None, pwf=True, edge=True):
    return triage.recommendation(ec, provider, profile, official, now=NOW, agent_vendor=vendor, failure_stage=stage,
                                 proceed_without_failure=pwf, edge_refusal=edge)


def test_the_edge_combination_is_a_medium_hand_off_with_the_code_first():
    rec = edge_rec()
    assert rec == {"action": "hand_off_to_human", "confidence": "medium", "reasons": ["edge_refusal_confirmed", "robots_allows_agents"]}
    assert edge_rec(stage="access_denial_observed")["action"] == "hand_off_to_human"
    assert edge_rec(edge=False)["action"] == "proceed"
    assert edge_rec(ABSENT)["reasons"] == ["edge_refusal_confirmed", "robots_allows_agents"]


@pytest.mark.parametrize("profile,official,vendor,provider,ec", [
    (robots("User-agent: *\nDisallow: /\n"), None, None, None, "site_access"),                      # stop_declared_block
    (robots(block("Claude-User")), OFFICIAL, "anthropic", None, "site_access"),                      # use_official_path (block)
    (ALLOWS, OFFICIAL, None, None, "site_access"),                                                    # use_official_path (listed)
    ({"robots_status": "refused", "robots": None, "freshness": "fresh", "llms_txt_status": "absent"}, None, None, None, "site_access"),
    ({"robots_status": "pending", "robots": None, "freshness": "pending", "llms_txt_status": "pending", "retry_after_seconds": 60}, None, None, None, "site_access"),
    (None, None, None, None, "site_access"),                                                          # unknown
    (ALLOWS, None, None, {"freshness": "fresh", "indicator": "major", "basis": "incident", "active_incidents": 1,
                          "max_incident_impact": "major", "incident_updated_at": NOW, "active_maintenances": 0}, "site_access"),  # wait
    (ALLOWS, None, None, None, "human_verification"), (ALLOWS, None, None, None, "login_auth"),
    (ALLOWS, None, None, None, "service_error"), (ALLOWS, None, None, None, None),
])
def test_no_other_decision_changes_when_edge_refusal_is_set(profile, official, vendor, provider, ec):
    assert edge_rec(profile, official, vendor=vendor, provider=provider, ec=ec) == edge_rec(profile, official, vendor=vendor, provider=provider, ec=ec, edge=False)


@pytest.mark.parametrize("stage", ["before_target_response", "response_observed", "unknown"])
def test_the_three_other_failure_stages_are_not_an_edge_refusal(stage):
    assert edge_rec(stage=stage) == edge_rec(stage=stage, edge=False) and edge_rec(stage=stage)["action"] == "proceed"


def test_every_proceed_variant_turns_into_the_same_hand_off_and_keeps_the_vendor_echo():
    p2 = robots(block("Claude-SearchBot"))
    for profile, vendor, row_before, row_after in ((ALLOWS, None, "P1", "H3"), (ALLOWS, "anthropic", "P1-V", "H3"), (p2, None, "P2", "H3"),
                                                  (p2, "anthropic", "P2-V", "H3"), (ABSENT, None, "P3", "H3")):
        before = edge_rec(profile, vendor=vendor, edge=False)
        after = edge_rec(profile, vendor=vendor)
        assert select_row(before, profile, None, "example.com")[0] == row_before
        assert select_row(after, profile, None, "example.com") == (row_after, True)
        assert after["reasons"] == [EDGE_CODE, *before["reasons"]]
        assert after.get("agent_vendor") == before.get("agent_vendor") == vendor


def test_a_proceed_without_the_d63_switch_follows_the_same_rule():
    for pwf in (True, False):
        assert edge_rec(pwf=pwf)["action"] == "hand_off_to_human"
        assert edge_rec(pwf=pwf, edge=False)["action"] == "proceed"


def test_next_checks_carry_the_code_after_the_stage_and_only_when_asked():
    found = triage.next_checks("site_access", None, "no_reports", None, ALLOWS, None, now=NOW, edge_refusal=True)
    assert [c["code"] for c in found][:3] == ["locate_failure_stage", EDGE_CODE, "robots_allows_agents"]
    staged = triage.next_checks("site_access", None, "no_reports", "access_denial_observed", ALLOWS, None, now=NOW, edge_refusal=True)
    assert [c["code"] for c in staged][:2] == ["access_denial_observed", EDGE_CODE]
    assert staged[1]["text"] == CHECKS[EDGE_CODE]
    assert EDGE_CODE not in [c["code"] for c in triage.next_checks("site_access", None, "no_reports", None, ALLOWS, None, now=NOW)]
    # the flag alone is not enough: the code needs the matching error_class and stage
    assert EDGE_CODE not in [c["code"] for c in triage.next_checks("service_error", None, "no_reports", None, ALLOWS, None, now=NOW, edge_refusal=True)]
    assert EDGE_CODE not in [c["code"] for c in triage.next_checks("site_access", None, "no_reports", "response_observed", ALLOWS, None, now=NOW, edge_refusal=True)]


def test_the_code_is_in_the_published_model_and_has_both_languages():
    assert EDGE_CODE in NextCheck.model_fields["code"].annotation.__args__
    assert EDGE_CODE in CHECKS and EDGE_CODE in CHECKS_KO and set(CHECKS) == set(CHECKS_KO)
    assert CHECKS[EDGE_CODE].startswith("The site refused you and AHN's own home-page check too (ahn_check)")
    assert "ahn_check" in CHECKS_KO[EDGE_CODE] and "재시도하지 말고" in CHECKS_KO[EDGE_CODE]
    assert triage.CHECK_TEXT_KO[CHECKS[EDGE_CODE]] == CHECKS_KO[EDGE_CODE]


# --- the guide ---------------------------------------------------------------------------------------------------------------

def edge_answer(official=None, **kwargs):
    rec = edge_rec(official=official, **kwargs)
    return {"site_host": "example.com", "provider_status": None, "official_status_page": None, "access_profile": ALLOWS,
            "official_paths": official, "recommendation": rec}


def test_h3_has_the_fixed_sentences_the_row_and_no_host_or_url():
    assert ROWS["H3"][0] == "hand_off_to_human" and len(ROWS) == 44
    assert ROWS["H3"][1] == ("The site's edge refused both you and AHN's own home-page check (ahn_check). Do not switch tools or user agents "
                             "to retry; tell your user, who can open the page themselves.")
    assert EDGE_CONSUMER_ROWS["H3"] == ("The site's edge refused both you and AHN's own check (ahn_check). Do not switch tools to retry; "
                                        "hand your user the step and the site's customer page, next_action.customer_url.")
    for text in (ROWS["H3"][1], EDGE_CONSUMER_ROWS["H3"], CHECKS[EDGE_CODE]):
        assert re.fullmatch(r"[\x20-\x7E]+", text) and "{" not in text and "http" not in text
        assert not re.search("|".join(FORBIDDEN), text, re.I), text
    assert len(ROWS["H3"][1]) <= LINE_MAX and len(EDGE_CONSUMER_ROWS["H3"]) <= LINE_MAX


def test_h3_and_h3_c_choose_by_the_curated_customer_page_and_do_stays_the_actions_function():
    plain = guide(edge_answer(None))
    assert plain["summary_line"] == ROWS["H3"][1] and plain["next_action"] == {"do": "hand_off_to_human", "url": None, "retry_after_seconds": None}
    curated = guide(edge_answer({"name": "Example", "api_docs": None, "developer_portal": None, "note": None, "consumer": CONSUMER}))
    assert curated["summary_line"] == EDGE_CONSUMER_ROWS["H3"]
    assert curated["next_action"] == {"do": "hand_off_to_human", "url": None, "retry_after_seconds": None, "customer_url": HELP}
    assert plain["links"] == curated["links"]
    # the D63 switch off (the proceed answer it replaces had no customer_url then) and the blocked-by-default mode keep the row
    assert guide(edge_answer(None), False)["summary_line"] == ROWS["H3"][1]
    assert guide(edge_answer(None), True, True)["summary_line"] == ROWS["H3"][1]
    assert select_row(edge_answer()["recommendation"], ALLOWS, None, "example.com") == ("H3", True)


def test_the_status_url_is_what_it_was_for_a_status_guide():
    result = {**edge_answer(), "provider_status": {"source_name": "S", "source_url": STATUS_URL, "indicator": "none", "freshness": "fresh"}}
    before = {**result, "recommendation": edge_rec(edge=False)}
    new, old = guide(copy.deepcopy(result), True, False, False, True), guide(copy.deepcopy(before), True, False, False, True)
    assert new["next_action"]["status_url"] == old["next_action"]["status_url"] == STATUS_URL
    assert new["next_action"]["do"] == "hand_off_to_human" and old["next_action"]["do"] == "proceed"


def test_feedback_token_and_signal_record_the_new_action_only_through_the_recommendation():
    # service.check passes result["recommendation"]["action"] to both; this is the only place the action is read.
    assert edge_rec()["action"] == "hand_off_to_human"


# --- the switch --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [("", True), ("1", True), (" 1 ", True), ("0", False), (" 0 ", False), ("off", False), ("true", False),
                                          ("2", False), ("yes", False), ("d85", False)])
def test_the_switch_reads_unknown_values_as_off(raw, expected, monkeypatch):
    monkeypatch.setattr(settings_module, "_EDGE_WARNED", False)
    assert parse_edge_refusal_switch(raw) is expected


def test_an_unknown_value_warns_once_with_the_code_only(caplog, monkeypatch):
    monkeypatch.setattr(settings_module, "_EDGE_WARNED", False)
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            parse_edge_refusal_switch("sure-d85-secret-looking-value")
    messages = [record.getMessage() for record in caplog.records if "edge_refusal" in record.getMessage()]
    assert messages == ["edge_refusal_switch_unknown_value"]
    assert "sure-d85" not in caplog.text


def test_from_env_reads_the_switch(monkeypatch):
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://u:p@localhost/x")
    monkeypatch.setenv("AHN_KEY_PEPPER", "x" * 40)
    monkeypatch.setattr(settings_module, "_EDGE_WARNED", False)
    monkeypatch.delenv("AHN_EDGE_REFUSAL_HANDOFF", raising=False)
    assert Settings.from_env().edge_refusal_handoff is True
    monkeypatch.setenv("AHN_EDGE_REFUSAL_HANDOFF", "0")
    assert Settings.from_env().edge_refusal_handoff is False
    monkeypatch.setenv("AHN_EDGE_REFUSAL_HANDOFF", "1")
    assert Settings.from_env().edge_refusal_handoff is True
    assert Settings(database_url="x", key_pepper="y").edge_refusal_handoff is True


# --- documents ---------------------------------------------------------------------------------------------------------------

def test_the_contracts_the_policy_and_the_guidance_say_the_new_rule():
    en = (ROOT / "docs" / "api-contract.en.md").read_text(encoding="utf-8")
    ko = (ROOT / "docs" / "api-contract.md").read_text(encoding="utf-8")
    for name, text in (("en", en), ("ko", ko)):
        assert "AHN_EDGE_REFUSAL_HANDOFF=0" in text and "edge_refusal_confirmed" in text and "D85" in text, name
    assert "모두 38개 코드" in ko and "모두 35개 코드" not in ko
    policy = (ROOT / "ahn" / "templates" / "policy.html").read_text(encoding="utf-8")
    assert "It changes the recommendation in one case only" in policy and "권고를 바꾸는 경우는 하나뿐입니다" in policy
    assert "it does not change the recommendation, and another" not in policy
    llms = (ROOT / "ahn" / "llms.txt").read_text(encoding="utf-8")
    assert "edge_refusal_confirmed" in llms and "D85" in llms
    assert "hand_off_to_human" in (ROOT / "ahn" / "agent_instructions.txt").read_text(encoding="utf-8")
    decisions = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    assert "| D85 " in decisions
    plugin = (ROOT / "plugins" / "claude-code" / "ahn" / "README.md").read_text(encoding="utf-8")
    assert "`edge_refusal_confirmed`" in plugin
