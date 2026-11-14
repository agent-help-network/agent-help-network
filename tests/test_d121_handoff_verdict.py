"""D121: two hand-offs.

(1) A human_verification lookup on a host whose provider shows a minor page with an incident list read and a worst incident impact read
as none or minor is handed to the user instead of waiting for the provider. Unknown severity, maintenance, components only, major or
critical pages (a major page over a minor incident included) still wait, and login_auth is not covered.
(2) A lookup that sent error_class=site_access itself (no failure_stage or access_denial_observed, not a lookup-only task_step) and whose
answer would be proceed is handed to the user with the reason caller_reported_refusal and confidence low. D85 (AHN's own home-page
check refused too) comes first and keeps its reason and confidence.

Pure: no database (the service wiring is in test_d121_handoff_db.py). The switch-off answer of recommendation() is the answer before
D121 (its two keyword arguments default to off), so the comparison below walks the input space with both and asserts both directions:
no answer outside the D121 conditions differs, and every answer inside them does."""

from __future__ import annotations

import itertools
import logging
import os
import re
import sys
from datetime import timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from test_d63_decision_order import ABSENT as ALLOWS_PROFILE, NOW, OFFICIAL, OFFICIAL_K, OFFICIAL_N, PROFILES, WITH_CONSUMER, CONSUMER_ONLY  # noqa: E402

from ahn import explain_text, response_guide, settings as settings_module, triage  # noqa: E402
from ahn.response_guide import EDGE_CONSUMER_ROWS, LINE_MAX, ROWS, guide, select_row  # noqa: E402
from ahn.settings import Settings, parse_d121_switch  # noqa: E402
from ahn.triage import CALLER_REFUSAL_CODE, LOOKUP_ONLY_STEPS, minor_incident_page  # noqa: E402

FRESH_T = (NOW - timedelta(hours=2)).isoformat()
OLD_T = (NOW - timedelta(days=10)).isoformat()
BASE = {"source_name": "S", "source_url": "https://status.example.com/", "freshness": "fresh", "source_updated_at": FRESH_T}
INC = dict(BASE, active_incidents=1, active_maintenances=0, basis="incident", incident_updated_at=FRESH_T)
MINOR_OK = {
    "minor page, minor incident": (dict(INC, indicator="minor", max_incident_impact="minor"), False),
    "minor page, incident impact none": (dict(INC, indicator="minor", max_incident_impact="none"), False),
    "minor page, minor incident, regional": (dict(INC, indicator="minor", max_incident_impact="minor"), True),
}
STILL_WAITS = {
    "major page over a minor incident": (dict(INC, indicator="major", max_incident_impact="minor"), False),
    "Atom history incident (no severity)": ({"source_name": "S", "source_url": "https://status.example.com/", "freshness": "fresh",
                                             "source_kind": "history_atom", "indicator": None, "active_incidents": 1,
                                             "incident_updated_at": FRESH_T}, False),
    "minor page, impact not read": (dict(INC, indicator="minor"), False),
    "minor page, impact above the page": (dict(INC, indicator="minor", max_incident_impact="major"), False),
    "minor page, no incident list": ({k: v for k, v in dict(BASE, indicator="minor").items() if k != "source_updated_at"}, False),
    "minor page, maintenance only": (dict(BASE, indicator="minor", active_incidents=0, active_maintenances=1, basis="maintenance",
                                          max_incident_impact="none"), False),
    "maintenance page": (dict(BASE, indicator="maintenance", active_incidents=0, active_maintenances=1, basis="maintenance",
                              max_incident_impact="none"), False),
    "major page, major incident": (dict(INC, indicator="major", max_incident_impact="major"), False),
    "critical page": (dict(INC, indicator="critical", max_incident_impact="critical"), False),
    "major page, components only": (dict(BASE, indicator="major", active_incidents=0, active_maintenances=0, basis="components_only"), False),
}
OTHER = {
    "none": (None, False),
    "clear": (dict(BASE, indicator="none", active_incidents=0, active_maintenances=0), False),
    "minor page, components only": (dict(BASE, indicator="minor", active_incidents=0, active_maintenances=0, basis="components_only",
                                          max_incident_impact="none"), False),
    "minor page, stale incident": (dict(INC, indicator="minor", max_incident_impact="minor", incident_updated_at=OLD_T), False),
}
PROVIDERS = {**MINOR_OK, **STILL_WAITS, **OTHER}
OFFICIALS = (None, OFFICIAL, OFFICIAL_N, OFFICIAL_K, WITH_CONSUMER, CONSUMER_ONLY)
ERROR_CLASSES = (None, "site_access", "service_error", "human_verification", "login_auth")
STAGES = (None, "before_target_response", "response_observed", "access_denial_observed", "unknown")
STEPS = (None, "status_check", "preflight")
ALLOWS = {"robots_status": "absent", "robots": None, "freshness": "fresh", "llms_txt_status": "absent"}
HELP = "https://www.example.com/help"
H4 = ("You reported a refused page, and the site declares no block for listed agent vendors. Do not switch tools or user agents; "
      "tell your user now. Continue the work that does not need this page.")
H4_C = ("You reported a refused page, and the site declares no block for listed agent vendors. Do not switch tools; "
        "hand your user next_action.customer_url. Continue the work that does not need this page.")
FULL = os.environ.get("AHN_D121_FULL") == "1"  # the one-off full walk (the PR numbers); the suite walks every 7th combination
RETRY_WORDS = re.compile(r"retry|try again|one more try|once more|another try|check your tool|tool and path", re.I)


def wiring(ec, fs, step):
    """service.check's D66 wiring with d65: the lookup-only words, blocked_default and assume_failure."""
    failure = triage.failure_observed(ec, fs)
    lookup_only = step in LOOKUP_ONLY_STEPS
    blocked_default = not (lookup_only and not failure)
    return lookup_only, blocked_default, blocked_default and not failure


def rec(ec, provider, profile, official, *, fs=None, step=None, regional=False, edge=False, d121=True, pwf=True):
    lookup_only, _, assume = wiring(ec, fs, step)
    extra = {"minor_incident_handoff": True, "caller_refusal_handoff": not lookup_only} if d121 else {}
    return triage.recommendation(ec, provider, profile, official, profile_not_read=False, provider_regional=regional, now=NOW,
                                 failure_stage=fs, proceed_without_failure=pwf, assume_failure=assume, edge_refusal=edge,
                                 service_error_reference_only=True, robots_refusal_handoff=True, **extra)


def full(ec, provider, profile, official, *, fs=None, step=None, regional=False, edge=False, d121=True, host="example.com"):
    """recommendation, next_checks and guide() as service.check puts them together."""
    _, blocked_default, assume = wiring(ec, fs, step)
    r = rec(ec, provider, profile, official, fs=fs, step=step, regional=regional, edge=edge, d121=d121)
    checks = triage.next_checks(ec, provider, "no_reports", fs, profile, official, profile_not_read=False, provider_partial=regional,
                                now=NOW, assume_failure=assume, edge_refusal="edge_refusal_confirmed" in r["reasons"],
                                robots_refusal="edge_refusal_robots" in r["reasons"])
    result = {"site_host": host, "provider_status": provider, "official_status_page": None, "access_profile": profile,
              "official_paths": official, "recommendation": r, "next_checks": checks}
    sc = response_guide.status_check_applies(step, True, True, ec, fs)
    return {**result, **guide(result, True, blocked_default, sc, True, ec, fs, True, True, d100=True)}


def d121_condition(ec, provider_name, fs, step, before: dict) -> str | None:
    if ec == "human_verification" and provider_name in MINOR_OK and before["action"] == "wait_provider_incident":
        return "minor"
    if (ec == "site_access" and fs in (None, "access_denial_observed") and step not in LOOKUP_ONLY_STEPS
            and before["action"] == "proceed"):
        return "refusal"
    return None


# --- (1) the minor-incident hand-off ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(MINOR_OK))
def test_a_verification_screen_under_a_read_minor_incident_is_handed_to_the_user(name):
    provider, regional = MINOR_OK[name]
    assert minor_incident_page(provider)
    for profile, official in itertools.product(PROFILES, OFFICIALS):
        before = rec("human_verification", provider, profile, official, regional=regional, d121=False)
        assert before["action"] == "wait_provider_incident"
        assert rec("human_verification", provider, profile, official, regional=regional) == {
            "action": "hand_off_to_human", "confidence": "medium", "reasons": ["human_handoff", "provider_scope"]}
    out = full("human_verification", provider, ALLOWS, None, regional=regional)
    assert out["next_action"]["do"] == "hand_off_to_human" and out["summary_line"] == ROWS["H1"][1]
    assert select_row(out["recommendation"], ALLOWS, provider, "example.com") == ("H1", True)


@pytest.mark.parametrize("name", sorted(STILL_WAITS))
def test_counterexample_1_unknown_or_larger_severity_still_waits(name):
    """A major page over a minor incident, an Atom history incident (no severity), and every other shape outside the rule keep the wait,
    byte for byte."""
    provider, regional = STILL_WAITS[name]
    assert not minor_incident_page(provider)
    for profile, official in itertools.product(PROFILES, OFFICIALS):
        on = full("human_verification", provider, profile, official, regional=regional)
        off = full("human_verification", provider, profile, official, regional=regional, d121=False)
        assert on == off
        if name in ("major page over a minor incident", "Atom history incident (no severity)"):
            assert on["recommendation"]["action"] == "wait_provider_incident"


@pytest.mark.parametrize("name", sorted(PROVIDERS))
def test_counterexample_2_login_auth_is_not_covered(name):
    provider, regional = PROVIDERS[name]
    for profile, official, fs, step in itertools.product(PROFILES, OFFICIALS, STAGES, STEPS):
        assert (full("login_auth", provider, profile, official, fs=fs, step=step, regional=regional)
                == full("login_auth", provider, profile, official, fs=fs, step=step, regional=regional, d121=False))


def test_the_public_board_rule_is_untouched():
    """provider_wait_reason() is shared with the public board; D121 decides inside _recommendation only."""
    for provider, _ in MINOR_OK.values():
        assert triage.provider_wait_reason(provider, NOW) == "provider_scope"
    source = (ROOT / "ahn" / "triage.py").read_text(encoding="utf-8")
    body = source.split("def provider_wait_reason", 1)[1].split("\ndef ", 1)[0]
    assert "minor_incident" not in body and "human_verification" not in body


# --- (2) the reported-refusal hand-off ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("fs", [None, "access_denial_observed"])
def test_a_reported_refusal_on_a_proceed_row_is_a_low_hand_off(fs):
    before = rec("site_access", None, ALLOWS, None, fs=fs, d121=False)
    assert before["action"] == "proceed"
    after = rec("site_access", None, ALLOWS, None, fs=fs)
    assert after == {"action": "hand_off_to_human", "confidence": "low", "reasons": [CALLER_REFUSAL_CODE, *before["reasons"]]}
    out = full("site_access", None, ALLOWS, None, fs=fs)
    assert out["summary_line"] == H4 and out["next_action"] == {"do": "hand_off_to_human", "url": None, "retry_after_seconds": None}
    consumer = {"name": "Example", "api_docs": None, "developer_portal": None, "note": None,
                "consumer": {"paths": [{"kind": "customer_help", "url": HELP, "source_url": "https://www.example.com/"}], "verified_at": "2026-10-31"}}
    out = full("site_access", None, ALLOWS, consumer, fs=fs)
    assert out["summary_line"] == H4_C and out["next_action"]["customer_url"] == HELP and out["next_action"]["url"] is None
    # next_checks are those of the proceed answer: the reason is not a next_checks code (the published code list is unchanged)
    assert [c["code"] for c in out["next_checks"]] == [c["code"] for c in full("site_access", None, ALLOWS, consumer, fs=fs, d121=False)["next_checks"]]


def test_counterexample_3_no_signal_and_status_check_are_byte_identical_and_d85_comes_first():
    for name, (provider, regional) in PROVIDERS.items():
        for profile, official in itertools.product(PROFILES, OFFICIALS):
            assert full(None, provider, profile, official, regional=regional) == full(None, provider, profile, official, regional=regional, d121=False)
            for step, fs in itertools.product(("status_check", "preflight"), STAGES):
                assert (full("site_access", provider, profile, official, fs=fs, step=step, regional=regional)
                        == full("site_access", provider, profile, official, fs=fs, step=step, regional=regional, d121=False))
            for fs in (None, "access_denial_observed"):
                on = full("site_access", provider, profile, official, fs=fs, regional=regional, edge=True)
                assert on == full("site_access", provider, profile, official, fs=fs, regional=regional, edge=True, d121=False)
                if on["recommendation"]["reasons"][:1] == ["edge_refusal_confirmed"]:
                    assert on["recommendation"]["confidence"] == "medium" and CALLER_REFUSAL_CODE not in on["recommendation"]["reasons"]
    out = full("site_access", None, ALLOWS, None, edge=True)
    assert out["recommendation"]["reasons"][0] == "edge_refusal_confirmed" and out["recommendation"]["confidence"] == "medium"
    assert select_row(out["recommendation"], ALLOWS, None, "example.com") == ("H3", True)


@pytest.mark.parametrize("fs", ["before_target_response", "response_observed", "unknown"])
def test_other_stages_keep_proceed_and_the_d116_sentence(fs):
    assert full("site_access", None, ALLOWS, None, fs=fs) == full("site_access", None, ALLOWS, None, fs=fs, d121=False)
    assert full("site_access", None, ALLOWS, None, fs=fs)["next_action"]["do"] == "proceed"


def test_the_switch_off_mode_without_d63_hands_off_too():
    """The verdict does not depend on the D63 switch (the sentence then has no customer page)."""
    from test_d63_decision_order import ALLOWS as ROBOTS_OK
    assert rec("site_access", None, ROBOTS_OK, None, pwf=False, d121=False)["action"] == "proceed"
    assert rec("site_access", None, ROBOTS_OK, None, pwf=False)["reasons"][0] == CALLER_REFUSAL_CODE


# --- the whole input space, both directions -------------------------------------------------------------------------------
def test_every_change_is_inside_a_d121_condition_and_every_answer_inside_one_changes():
    counts = {"minor": 0, "refusal": 0}
    grid = itertools.product(("example.com", "status.example.com"), PROFILES, OFFICIALS, ERROR_CLASSES, STAGES, STEPS, PROVIDERS)
    for index, (host, profile, official, ec, fs, step, name) in enumerate(grid):
        if not FULL and index % 7 and not (ec in ("site_access", "human_verification") and profile is ALLOWS_PROFILE):
            continue
        provider, regional = PROVIDERS[name]
        for edge in ((False, True) if ec == "site_access" and fs in (None, "access_denial_observed") else (False,)):
            off = full(ec, provider, profile, official, fs=fs, step=step, regional=regional, edge=edge, d121=False, host=host)
            on = full(ec, provider, profile, official, fs=fs, step=step, regional=regional, edge=edge, host=host)
            kind = d121_condition(ec, name, fs, step, off["recommendation"])
            if kind is None:
                assert on == off, (host, ec, fs, step, name, edge)
                continue
            counts[kind] += 1
            assert on["next_action"]["do"] == "hand_off_to_human" and off["next_action"]["do"] != "hand_off_to_human"
            assert on["next_checks"] == off["next_checks"]
            assert {k: v for k, v in on.items() if k not in ("recommendation", "summary_line", "next_action")} == \
                   {k: v for k, v in off.items() if k not in ("recommendation", "summary_line", "next_action")}
            if kind == "refusal":
                assert on["recommendation"] == {"action": "hand_off_to_human", "confidence": "low",
                                                "reasons": [CALLER_REFUSAL_CODE, *off["recommendation"]["reasons"]]}
                assert on["summary_line"] in (H4, H4_C)
            else:
                assert on["recommendation"] == {"action": "hand_off_to_human", "confidence": "medium", "reasons": ["human_handoff", "provider_scope"]}
    assert counts["minor"] > 0 and counts["refusal"] > 0


# --- sentences, switch, documents -----------------------------------------------------------------------------------------
def test_the_h4_sentences_fit_say_the_rest_continues_and_offer_no_retry():
    for sentence in (ROWS["H4"][1], EDGE_CONSUMER_ROWS["H4"]):
        assert len(sentence) <= LINE_MAX
        assert "Continue the work that does not need this page." in sentence
        assert not RETRY_WORDS.search(sentence)
        assert explain_text.summary_line_ko(sentence) is not None
        assert "이 페이지가 필요 없는 나머지 작업은 계속하세요." in explain_text.summary_line_ko(sentence)
    assert ROWS["H4"][0] == "hand_off_to_human"


def test_the_switch(caplog, monkeypatch):
    assert parse_d121_switch("") and parse_d121_switch("1") and parse_d121_switch(" 1 ")
    assert not parse_d121_switch("0")
    monkeypatch.setattr(settings_module, "_D121_WARNED", False)
    with caplog.at_level(logging.WARNING, logger=settings_module.LOG.name):
        assert not parse_d121_switch("yes") and not parse_d121_switch("on")
    assert [r.getMessage() for r in caplog.records] == ["d121_switch_unknown_value"]
    assert Settings("postgresql://x", "p" * 32).d121_handoff is True


def test_the_contract_and_decisions_say_it():
    ko = (ROOT / "docs" / "api-contract.md").read_text(encoding="utf-8")
    en = (ROOT / "docs" / "api-contract.en.md").read_text(encoding="utf-8")
    decisions = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    for text in (ko, en):
        assert CALLER_REFUSAL_CODE in text and "AHN_D121_HANDOFF" in text and "D121" in text
    assert H4 in en and H4_C in en
    row = next(line for line in decisions.splitlines() if line.startswith("| D121 "))
    assert CALLER_REFUSAL_CODE in row and "AHN_D121_HANDOFF" in row
    d66 = next(line for line in decisions.splitlines() if line.startswith("| D66 "))
    # D66 said a lookup with no failure signal gets the same answer as one that sent site_access; since D121 it no longer does
    assert "D121" in d66
