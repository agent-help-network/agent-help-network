"""D98: a site_access lookup on a host that refused AHN's own plain robots.txt read too, with no developer path, is answered
hand_off_to_human (not unknown / no_clear_action).

Pure: no database (the service wiring is in test_d98_robots_refusal_db.py). The decision code from before D98 is kept as
tests/_pre_d98_triage.py and tests/_pre_d98_response_guide.py. The comparison walks the same input space as D85 (test_response_guide.space())
and asserts both directions: no pair outside the D98 condition differs, and every pair inside it does."""

from __future__ import annotations

import importlib.util
import itertools
import logging
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_d85_edge_refusal import EDGE_STAGES, GUIDE_MODES, STAGES, variants  # noqa: E402
from test_response_guide import NOW  # noqa: E402

from ahn import response_guide, settings as settings_module, triage  # noqa: E402
from ahn.models import NextCheck  # noqa: E402
from ahn.response_guide import EDGE_CONSUMER_ROWS, LINE_MAX, ROWS, guide, select_row  # noqa: E402
from ahn.settings import Settings, parse_d98_switch  # noqa: E402
from ahn.triage import CHECKS, CHECKS_KO, EDGE_ROBOTS_CODE, failure_observed  # noqa: E402

_spec = importlib.util.spec_from_file_location("pre_d98_triage", HERE / "_pre_d98_triage.py")
pre = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pre)
_spec_rg = importlib.util.spec_from_file_location("pre_d98_response_guide", HERE / "_pre_d98_response_guide.py")
pre_rg = importlib.util.module_from_spec(_spec_rg)
_spec_rg.loader.exec_module(pre_rg)

FULL = os.environ.get("AHN_D98_FULL") == "1"   # the one-off full walk of the grid (the PR numbers); the suite walks every 29th case
HELP = "https://www.example.com/help"
CONSUMER = {"paths": [{"kind": "customer_help", "url": HELP, "source_url": "https://www.example.com/"}], "verified_at": "2026-10-31"}
DEV = {"name": "Example", "api_docs": "https://developer.example.com/docs", "developer_portal": None, "note": None}
NO_DEV = {"name": "Example", "api_docs": None, "developer_portal": None, "note": None}
REFUSED = {"robots_status": "refused", "robots": None, "freshness": "fresh", "llms_txt_status": "absent"}
ALLOWS = {"robots_status": "absent", "robots": None, "freshness": "fresh", "llms_txt_status": "absent"}
SENTENCE = ("The site's edge refused both you and AHN's plain robots.txt read. Do not switch tools or user agents to retry; "
            "tell your user, who can open the page themselves.")
SENTENCE_C = ("The site's edge refused both you and AHN's plain robots.txt read. Do not switch tools or user agents to retry; "
              "hand your user the site's customer page, next_action.customer_url.")
CHECK = ("The site refused you and AHN's own plain robots.txt read too, so the refusal is the site's edge, not only your tool. "
         "Do not switch tools or user agents; hand the step to your user.")
FORBIDDEN = ("bypass", "evade", "spoof", "proxy", "solver", "stealth", "headless", "work around", "get past")


def rec_of(mod, case, stage, pwf, *, edge=False, ref=False, d98=None):
    kwargs = dict(profile_not_read=case.get("not_read", False), provider_regional=case.get("regional", False), now=NOW,
                  agent_vendor=case.get("vendor"), failure_stage=stage, proceed_without_failure=pwf, edge_refusal=edge,
                  service_error_reference_only=ref)
    if d98 is not None:
        kwargs["robots_refusal_handoff"] = d98
    return mod.recommendation(case.get("error_class"), case.get("provider_status"), case.get("access_profile"), case.get("official"), **kwargs)


def checks_of(mod, case, stage, pwf, *, robots=None):
    kwargs = dict(profile_not_read=case.get("not_read", False), provider_partial=case.get("regional", False), now=NOW,
                  agent_vendor=case.get("vendor"), proceed_without_failure=pwf)
    if robots is not None:
        kwargs["robots_refusal"] = robots
    return mod.next_checks(case.get("error_class"), case.get("provider_status"), "no_reports", stage, case.get("access_profile"),
                           case.get("official"), **kwargs)


def result_of(case, rec):
    return {"site_host": case.get("site_host", "example.com"), "provider_status": case.get("provider_status"), "official_status_page": None,
            "access_profile": case.get("access_profile"), "official_paths": case.get("official"), "recommendation": rec}


def is_inside(old_rec, error_class, stage):
    return (old_rec["action"] == "unknown" and old_rec["reasons"] == ["robots_fetch_refused"] and error_class == "site_access"
            and stage in EDGE_STAGES)


def compare(stride: int) -> dict:
    counts = dict(rec_pairs=0, rec_changed=0, guide_pairs=0, guide_changed=0, cases=0)
    for case in variants(stride):
        counts["cases"] += 1
        for stage, pwf, ref, edge in itertools.product(STAGES, (True, False), (False, True), (False, True)):
            old_rec = rec_of(pre, case, stage, pwf, edge=edge, ref=ref)
            old_checks = checks_of(pre, case, stage, pwf)
            for d98 in (False, True):
                new_rec = rec_of(triage, case, stage, pwf, edge=edge, ref=ref, d98=d98)
                new_checks = checks_of(triage, case, stage, pwf, robots=EDGE_ROBOTS_CODE in new_rec["reasons"])
                counts["rec_pairs"] += 1
                inside = d98 and is_inside(old_rec, case.get("error_class"), stage)
                if not inside:
                    assert new_rec == old_rec and new_checks == old_checks, (case, stage, pwf, ref, edge, d98)
                else:
                    counts["rec_changed"] += 1
                    assert {k: v for k, v in new_rec.items() if k != "agent_vendor"} == {
                        "action": "hand_off_to_human", "confidence": "medium", "reasons": [EDGE_ROBOTS_CODE, "robots_fetch_refused"]}, (case, stage)
                    assert new_rec.get("agent_vendor") == old_rec.get("agent_vendor"), "the vendor echo stays"
                    old_codes = [c["code"] for c in old_checks]
                    new_codes = [c["code"] for c in new_checks]
                    assert new_codes[1] == EDGE_ROBOTS_CODE and new_codes[:1] + new_codes[2:] == old_codes, (new_codes, old_codes)
                    assert new_checks[1]["text"] == CHECKS[EDGE_ROBOTS_CODE]
                    assert [c for c in new_checks if c["code"] != EDGE_ROBOTS_CODE] == old_checks
                    assert "robots_fetch_refused" in new_codes
                for mode in GUIDE_MODES:
                    if mode[2] and failure_observed(case.get("error_class"), stage):
                        continue
                    old_out = pre_rg.guide(result_of(case, old_rec), *mode)
                    new_out = guide(result_of(case, new_rec), *mode)
                    counts["guide_pairs"] += 1
                    if not inside:
                        assert new_out == old_out, (case, stage, edge, mode)
                        continue
                    counts["guide_changed"] += 1
                    action = new_out["next_action"]
                    assert action["do"] == "hand_off_to_human" and action["url"] is None and action["retry_after_seconds"] is None
                    assert action.get("status_url") == old_out["next_action"].get("status_url")
                    assert new_out.get("links") == old_out.get("links")
                    curated = bool(mode[0] and (case.get("official") or {}).get("consumer"))
                    assert action.get("customer_url") == (HELP if curated else None), (case, mode)
                    assert {k: v for k, v in action.items() if k not in ("do", "customer_url")} == \
                        {k: v for k, v in old_out["next_action"].items() if k not in ("do", "customer_url")}
                    assert new_out["summary_line"] == (SENTENCE_C if curated else SENTENCE), (case, mode)
                    assert new_out["summary_line"] != old_out["summary_line"]
    return counts


def test_the_pairs_that_change_are_exactly_the_d98_condition_in_the_whole_grid():
    counts = compare(1 if FULL else 29)
    print("D98 comparison", counts)
    assert counts["rec_changed"] > 50 and counts["guide_changed"] > 50
    assert counts["rec_pairs"] > 20000


def test_the_default_call_is_the_answer_before_d98_for_every_pair():
    # robots_refusal_handoff / robots_refusal left out (the call every other path makes): identical to the frozen pre-D98 code.
    for case in variants(11):
        for stage in STAGES:
            assert rec_of(triage, case, stage, True) == rec_of(pre, case, stage, True)
            assert checks_of(triage, case, stage, True) == checks_of(pre, case, stage, True)


# --- the refused-robots shape and its neighbours ---------------------------------------------------------------------------------------

def rec(profile=REFUSED, official=None, *, ec="site_access", stage=None, pwf=True, d98=True, **kw):
    return triage.recommendation(ec, None, profile, official, now=NOW, failure_stage=stage, proceed_without_failure=pwf,
                                 robots_refusal_handoff=d98, **kw)


def answer(official=None, **kwargs):
    r = rec(official=official, **kwargs)
    return {"site_host": "example.com", "provider_status": None, "official_status_page": None, "access_profile": REFUSED,
            "official_paths": official, "recommendation": r}


@pytest.mark.parametrize("stage", [None, "access_denial_observed"])
def test_the_refused_robots_shape_is_a_medium_hand_off_with_the_new_code_first(stage):
    r = rec(stage=stage)
    assert r == {"action": "hand_off_to_human", "confidence": "medium", "reasons": ["edge_refusal_robots", "robots_fetch_refused"]}
    out = guide(answer(stage=stage), True, False, False, True, "site_access", stage, True, True)
    assert out["summary_line"] == SENTENCE
    assert out["next_action"] == {"do": "hand_off_to_human", "url": None, "retry_after_seconds": None}
    assert select_row(r, REFUSED, None, "example.com") == ("H3-R", True)
    assert rec(stage=stage, d98=False) == {"action": "unknown", "confidence": "medium", "reasons": ["robots_fetch_refused"]}


def test_a_curated_customer_page_is_handed_over_in_the_second_sentence():
    out = guide(answer({**NO_DEV, "consumer": CONSUMER}), True, False, False, True, "site_access", None, True, True)
    assert out["summary_line"] == SENTENCE_C
    assert out["next_action"] == {"do": "hand_off_to_human", "url": None, "retry_after_seconds": None, "customer_url": HELP}


def test_without_the_d63_switch_the_row_is_the_same_hand_off_and_names_no_customer_page():
    # like H3: the customer sentence is only reached with the D63 switch on
    out = guide(answer({**NO_DEV, "consumer": CONSUMER}, pwf=False), False, False, False, False, "site_access", None, False, False)
    assert out["summary_line"] == SENTENCE and out["next_action"]["do"] == "hand_off_to_human"
    assert "customer_url" not in out["next_action"]


@pytest.mark.parametrize("ec", ["service_error", "human_verification", "login_auth", None])
def test_another_failure_kind_or_no_failure_is_not_changed(ec):
    assert rec(ec=ec) == rec(ec=ec, d98=False)
    assert EDGE_ROBOTS_CODE not in rec(ec=ec)["reasons"]


def test_service_error_keeps_the_u6_sentence_and_no_clear_action():
    for stage in (None, "before_target_response", "response_observed"):
        r = rec(ec="service_error", stage=stage)
        assert r["action"] == "unknown" and r["reasons"] == ["robots_fetch_refused"]
        out = guide(answer(ec="service_error", stage=stage), True, False, False, True, "service_error", stage, True, True)
        assert out["next_action"]["do"] == "no_clear_action"
        assert out == guide(answer(ec="service_error", stage=stage, d98=False), True, False, False, True, "service_error", stage, True, True)


@pytest.mark.parametrize("stage", ["before_target_response", "response_observed", "unknown"])
def test_the_other_failure_stages_are_not_changed(stage):
    assert rec(stage=stage) == rec(stage=stage, d98=False) and rec(stage=stage)["action"] == "unknown"


def test_a_developer_path_keeps_its_use_official_path_answer():
    for official in (DEV, {**DEV, "consumer": CONSUMER}):
        r = rec(official=official)
        assert r == rec(official=official, d98=False)
        assert r == {"action": "use_official_path", "confidence": "medium", "reasons": ["robots_fetch_refused", "official_path_available"]}


@pytest.mark.parametrize("profile", [ALLOWS, {**ALLOWS, "robots_status": "error"}, {**ALLOWS, "robots_status": "pending", "freshness": "pending"}, None])
def test_a_host_whose_robots_was_not_refused_is_not_changed(profile):
    assert rec(profile) == rec(profile, d98=False) and EDGE_ROBOTS_CODE not in rec(profile)["reasons"]


def test_no_failure_signal_is_not_changed():
    assert rec(ec=None, stage=None) == rec(ec=None, stage=None, d98=False)
    assert rec(ec=None)["action"] == "unknown"


def test_a_provider_wait_still_comes_first():
    provider = {"source_name": "Example Status", "source_url": "https://status.example.com/", "indicator": "major", "freshness": "fresh",
                "basis": "incident", "active_incidents": 1, "max_incident_impact": "major", "incident_updated_at": NOW, "active_maintenances": 0}
    r = triage.recommendation("site_access", provider, REFUSED, None, now=NOW, robots_refusal_handoff=True)
    assert r["action"] == "wait_provider_incident"


def test_the_vendor_echo_stays():
    r = triage.recommendation("site_access", None, REFUSED, None, now=NOW, agent_vendor="anthropic", robots_refusal_handoff=True)
    assert r["action"] == "hand_off_to_human" and r["agent_vendor"] == "anthropic"


def test_next_checks_carry_the_code_after_the_stage_and_before_robots_fetch_refused():
    found = triage.next_checks("site_access", None, "no_reports", None, REFUSED, None, now=NOW, robots_refusal=True)
    assert [c["code"] for c in found][:3] == ["locate_failure_stage", EDGE_ROBOTS_CODE, "robots_fetch_refused"]
    staged = triage.next_checks("site_access", None, "no_reports", "access_denial_observed", REFUSED, None, now=NOW, robots_refusal=True)
    assert [c["code"] for c in staged][:3] == ["access_denial_observed", EDGE_ROBOTS_CODE, "robots_fetch_refused"]
    assert staged[1]["text"] == CHECK
    assert EDGE_ROBOTS_CODE not in [c["code"] for c in triage.next_checks("site_access", None, "no_reports", None, REFUSED, None, now=NOW)]
    for ec, stage in (("service_error", None), ("site_access", "response_observed")):
        assert EDGE_ROBOTS_CODE not in [c["code"] for c in triage.next_checks(ec, None, "no_reports", stage, REFUSED, None, now=NOW,
                                                                              robots_refusal=True)]


# --- the sentences, the code, the switch and the documents ----------------------------------------------------------------------

def test_the_code_is_in_the_published_model_and_has_both_languages():
    assert EDGE_ROBOTS_CODE == "edge_refusal_robots" and EDGE_ROBOTS_CODE in NextCheck.model_fields["code"].annotation.__args__
    assert CHECKS[EDGE_ROBOTS_CODE] == CHECK and EDGE_ROBOTS_CODE in CHECKS_KO and set(CHECKS) == set(CHECKS_KO)
    assert "robots.txt" in CHECKS_KO[EDGE_ROBOTS_CODE] and "재시도하지 말고" in CHECKS_KO[EDGE_ROBOTS_CODE]
    assert triage.CHECK_TEXT_KO[CHECKS[EDGE_ROBOTS_CODE]] == CHECKS_KO[EDGE_ROBOTS_CODE]


def test_the_new_sentences_are_short_plain_and_carry_no_host_or_url():
    assert ROWS["H3-R"] == ("hand_off_to_human", SENTENCE) and EDGE_CONSUMER_ROWS["H3-R"] == SENTENCE_C
    assert len(SENTENCE) == 160 and len(SENTENCE_C) == 177
    for text in (SENTENCE, SENTENCE_C, CHECK):
        assert len(text) <= LINE_MAX and re.fullmatch(r"[\x20-\x7E]+", text) and "{" not in text and "http" not in text, text
        assert not any(word in text.lower() for word in FORBIDDEN), text
    assert ROWS["H3"] == pre_rg.ROWS["H3"] and EDGE_CONSUMER_ROWS["H3"] == pre_rg.EDGE_CONSUMER_ROWS["H3"]


def test_no_other_row_or_table_changed():
    assert {k: v for k, v in ROWS.items() if k != "H3-R" and k not in response_guide.HANDOFF_ROWS} == pre_rg.ROWS
    assert {k: v for k, v in EDGE_CONSUMER_ROWS.items() if k != "H3-R"} == pre_rg.EDGE_CONSUMER_ROWS
    assert {k: v for k, v in response_guide.STATUS_ROWS.items() if k not in ("T6", "T1-U", "T6-F")} == pre_rg.STATUS_ROWS
    assert response_guide.CONSUMER_ROWS == pre_rg.CONSUMER_ROWS
    assert response_guide.NO_PATH_NO_RESPONSE_ROWS == pre_rg.NO_PATH_NO_RESPONSE_ROWS


def test_the_switch_parser_and_setting(monkeypatch, caplog):
    assert parse_d98_switch("") is True and parse_d98_switch("1") is True and parse_d98_switch(" 1 ") is True
    assert parse_d98_switch("0") is False
    monkeypatch.setattr(settings_module, "_D98_WARNED", False)
    with caplog.at_level(logging.WARNING):
        assert parse_d98_switch("off") is False and parse_d98_switch("true") is False
    assert caplog.text.count("d98_switch_unknown_value") == 1
    monkeypatch.delenv("AHN_D98_ROBOTS_REFUSAL_HANDOFF", raising=False)
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("AHN_KEY_PEPPER", "x" * 32)
    assert Settings.from_env().d98_robots_refusal_handoff is True
    monkeypatch.setenv("AHN_D98_ROBOTS_REFUSAL_HANDOFF", "0")
    assert Settings.from_env().d98_robots_refusal_handoff is False


def test_documents_and_the_plugin_name_the_rule():
    for name in ("api-contract.en.md", "api-contract.md"):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert "D98" in text and "edge_refusal_robots" in text and "AHN_D98_ROBOTS_REFUSAL_HANDOFF" in text, name
    assert "| D98 " in (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    plugin = ROOT / "plugins" / "claude-code" / "ahn"
    assert "| `edge_refusal_robots` |" in (plugin / "README.md").read_text(encoding="utf-8")
    assert "edge_refusal_robots" in (plugin / "hooks" / "ahn_lookup.py").read_text(encoding="utf-8")
