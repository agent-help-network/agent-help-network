"""D55: a wait that rests only on maintenance in progress, with no incident announced, says so.

The rule is wording only. For every provider shape the action, the confidence and whether the answer waits are the
ones the answer had before D55 (pinned literally for the named shapes below and, over the whole grid, against the same
triage with the new rule switched off); only the provider_maintenance_only code, the provider_scope text and the
summary line change. Pure: no database."""

from __future__ import annotations

import itertools
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jinja2
import pytest

from ahn import signal_history, triage
from ahn.models import NextCheck
from ahn.response_guide import ROWS, guide, select_row
from ahn.triage import CHECKS, CHECKS_KO, MAINTENANCE_CHECK, MAINTENANCE_CHECK_KO, maintenance_only, next_checks, recommendation

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 24, 16, 30, tzinfo=timezone.utc)
URL = "https://status.example.com/"
CODE = "provider_maintenance_only"


def status(indicator="major", *, basis="maintenance", incidents=0, maintenances=2, impact="none", freshness="fresh",
           updated=None, **extra) -> dict:
    out = {"source_name": "Example Status", "source_url": URL, "indicator": indicator, "freshness": freshness,
           "source_updated_at": datetime(2026, 8, 27, tzinfo=timezone.utc), "basis": basis,
           "active_incidents": incidents, "max_incident_impact": impact, "active_maintenances": maintenances, **extra}
    if updated is not None:
        out["incident_updated_at"] = updated
    return out


def cloudflare_shape() -> dict:
    # The lookup answer for cloudflare.com on 2026-10-24: page major, no incident, two maintenances on data centers.
    return status("major", affected=["Nairobi, Kenya - (NBO)", "Guatemala City, Guatemala - (GUA)"])


def rec(provider_status, *, regional=False, error_class=None, profile=None, official=None) -> dict:
    return recommendation(error_class, provider_status, profile, official, provider_regional=regional, now=NOW)


def checks(provider_status, *, regional=False) -> list[dict]:
    return next_checks(None, provider_status, "no_reports", None, provider_partial=regional, now=NOW)


def summary(provider_status, *, regional=False) -> tuple[str, str]:
    answer = {"site_host": "example.com", "provider_status": provider_status, "official_status_page": None,
              "access_profile": None, "official_paths": None, "recommendation": rec(provider_status, regional=regional)}
    row, listed = select_row(answer["recommendation"], None, provider_status, "example.com")
    assert listed
    return row, guide(answer)["summary_line"]


# --- the named shapes: action and confidence pinned literally ------------------------------------------------------

def test_the_live_cloudflare_shape_keeps_its_low_wait_and_says_maintenance_only():
    shape = cloudflare_shape()
    assert rec(shape, regional=True) == {"action": "wait_provider_incident", "confidence": "low",
                                         "reasons": ["provider_scope", "provider_scope_partial", CODE]}
    items = checks(shape, regional=True)
    codes = [item["code"] for item in items]
    assert codes[-4:] == ["provider_scope", "provider_scope_partial", CODE, "reports_unknown"]
    texts = {item["code"]: item["text"] for item in items}
    assert texts["provider_scope"] == MAINTENANCE_CHECK  # not "The provider reports an active issue"
    assert "reports an active issue" not in " ".join(texts.values())
    assert texts[CODE] == CHECKS[CODE]
    row, line = summary(shape, regional=True)
    assert row == "W6" and line == ROWS["W6"][1]
    assert "no incident" in line and "maintenance" in line and "regions or points of presence" in line
    assert "reports an issue" not in line
    assert guide({"site_host": "example.com", "provider_status": shape, "official_status_page": None, "access_profile": None,
                  "official_paths": None, "recommendation": rec(shape, regional=True)})["next_action"] == {
        "do": "wait_for_provider", "url": URL, "retry_after_seconds": 900}


def test_a_maintenance_only_page_that_is_not_regional_keeps_its_medium_wait():
    for indicator in ("minor", "major", "critical"):
        shape = status(indicator)
        assert rec(shape) == {"action": "wait_provider_incident", "confidence": "medium", "reasons": ["provider_scope", CODE]}
        assert [item["code"] for item in checks(shape)][-3:] == ["provider_scope", CODE, "reports_unknown"]
        row, line = summary(shape)
        assert row == "W7" and "no incident" in line and "status page shows an issue" in line
        # regional: low, W6
        assert rec(shape, regional=True) == {"action": "wait_provider_incident", "confidence": "low",
                                             "reasons": ["provider_scope", "provider_scope_partial", CODE]}
        assert summary(shape, regional=True)[0] == "W6"


def test_a_maintenance_page_keeps_w2_and_its_text_and_gains_only_the_code():
    shape = status("maintenance", maintenances=1)
    assert rec(shape) == {"action": "wait_provider_incident", "confidence": "medium", "reasons": ["provider_scope", CODE]}
    texts = {item["code"]: item["text"] for item in checks(shape)}
    assert texts["provider_scope"] == MAINTENANCE_CHECK
    assert summary(shape) == ("W2", ROWS["W2"][1])
    assert rec(shape, regional=True)["confidence"] == "low" and summary(shape, regional=True)[0] == "W6"


def test_an_incident_next_to_a_maintenance_is_not_maintenance_only():
    # basis incident (status_scope.basis gives incident whenever an incident is open, maintenances or not)
    shape = status("major", basis="incident", incidents=1, maintenances=1, impact="minor", updated=NOW - timedelta(hours=2))
    assert not maintenance_only(shape)
    assert rec(shape) == {"action": "wait_provider_incident", "confidence": "medium", "reasons": ["provider_scope"]}
    assert rec(shape, regional=True) == {"action": "wait_provider_incident", "confidence": "low",
                                         "reasons": ["provider_scope", "provider_scope_partial"]}
    texts = {item["code"]: item["text"] for item in checks(shape)}
    assert CODE not in texts and texts["provider_scope"] == CHECKS["provider_scope"]
    assert summary(shape)[0] == "W1" and summary(shape, regional=True)[0] == "W3"


@pytest.mark.parametrize("incidents", [None, 1, True, False, "0", 0.0, -1])
def test_a_count_that_is_not_exactly_zero_never_reads_as_maintenance_only(incidents):
    # Unknown is not anything: a missing, non-integer or non-zero count leaves the answer as it was.
    shape = status("major", incidents=incidents)
    if incidents is None:
        shape.pop("active_incidents")
    assert not maintenance_only(shape)
    assert CODE not in rec(shape)["reasons"]
    assert CODE not in [item["code"] for item in checks(shape)]
    assert {item["code"]: item["text"] for item in checks(shape)}["provider_scope"] == CHECKS["provider_scope"]


def test_no_fresh_active_statement_never_reads_as_maintenance_only():
    for shape in (status(freshness="stale"), status("none"), status("unknown"), status(basis="components_only"),
                  status(basis=None), None, {}):
        assert not maintenance_only(shape)
        assert CODE not in rec(shape)["reasons"]
        assert CODE not in [item["code"] for item in checks(shape)]


# --- the whole grid: nothing but the code, the provider_scope text and the line changes -----------------------------

def grid():
    for indicator, basis, incidents, maintenances, impact, freshness in itertools.product(
            ("minor", "major", "critical", "maintenance", "none", None), ("incident", "maintenance", "components_only", None),
            (0, 1, None), (0, 2, None), ("none", "minor", "critical"), ("fresh", "stale")):
        for updated in (NOW - timedelta(hours=1), NOW - timedelta(days=30)):
            shape = status(indicator, basis=basis, incidents=incidents, maintenances=maintenances, impact=impact,
                           freshness=freshness, updated=updated)
            for key in ("active_incidents", "active_maintenances"):
                if shape[key] is None:
                    shape.pop(key)
            yield shape


def test_action_confidence_and_wait_are_those_of_the_rule_switched_off(monkeypatch):
    shapes = list(grid())
    profiles = (None, {"robots_status": "absent", "robots": None, "freshness": "fresh", "llms_txt_status": "absent"})
    official = {"name": "Example", "api_docs": "https://developer.example.com/docs", "developer_portal": None, "note": None}
    after = {}
    for i, shape in enumerate(shapes):
        for regional, error_class, profile, off in itertools.product((False, True), (None, "human_verification"), profiles, (None, official)):
            after[i, regional, error_class, id(profile), id(off)] = (
                rec(shape, regional=regional, error_class=error_class, profile=profile, official=off),
                checks(shape, regional=regional), triage.provider_wait_reason(shape, NOW),
                signal_history.board_provider(shape, NOW) if shape.get("indicator") is not None else None)
    monkeypatch.setattr(triage, "maintenance_only", lambda _status: False)
    added = 0
    for i, shape in enumerate(shapes):
        for regional, error_class, profile, off in itertools.product((False, True), (None, "human_verification"), profiles, (None, official)):
            new_rec, new_checks, new_wait, new_board = after[i, regional, error_class, id(profile), id(off)]
            old_rec = rec(shape, regional=regional, error_class=error_class, profile=profile, official=off)
            assert new_rec["action"] == old_rec["action"] and new_rec["confidence"] == old_rec["confidence"], shape
            assert [r for r in new_rec["reasons"] if r != CODE] == old_rec["reasons"], shape
            old_checks = checks(shape, regional=regional)
            assert [c["code"] for c in new_checks if c["code"] != CODE] == [c["code"] for c in old_checks], shape
            for new, old in zip([c for c in new_checks if c["code"] != CODE], old_checks):
                if new["text"] != old["text"]:
                    assert new["code"] == "provider_scope" and new["text"] == MAINTENANCE_CHECK, shape
            # the board (D53) and the lookup share provider_wait_reason, which D55 does not touch
            assert triage.provider_wait_reason(shape, NOW) == new_wait
            if shape.get("indicator") is not None:
                assert signal_history.board_provider(shape, NOW) == new_board
            if CODE in new_rec["reasons"]:
                added += 1
                assert new_rec["reasons"][-1] == CODE and new_rec["action"] == "wait_provider_incident"
    assert added > 0


def test_the_code_follows_every_maintenance_only_wait_and_nothing_else():
    for shape in grid():
        for regional in (False, True):
            result = rec(shape, regional=regional)
            codes = [item["code"] for item in checks(shape, regional=regional)]
            expected = maintenance_only(shape)
            assert (CODE in codes) == expected, shape
            if result["action"] == "wait_provider_incident" and "provider_scope" in result["reasons"]:
                assert (CODE in result["reasons"]) == expected, shape
            else:
                assert CODE not in result["reasons"], shape


# --- texts, contracts, plugin, Korean page --------------------------------------------------------------------------

def test_the_texts_say_no_incident_and_maintenance_and_keep_the_wait():
    en, ko = CHECKS[CODE], CHECKS_KO[CODE]
    assert "announced no incident" in en and "maintenance in progress" in en and "still waits" in en
    assert "사건은 없습니다" in ko and "진행 중인 점검뿐" in ko and "대기는 유지" in ko
    for text in (en, ko):
        assert "provider_scope_partial" in text and "basis" in text and "active_incidents" in text
        assert not re.search(r"continue|진행하세요", text)  # the answer still waits: the text never says to go ahead
    for row in ("W6", "W7"):
        do, line = ROWS[row]
        assert do == "wait_for_provider" and "posted no incident, only maintenance in progress" in line
        assert len(line) <= 200


def test_the_code_is_in_the_model_the_contracts_the_readmes_and_the_plugin():
    assert CODE in NextCheck.model_fields["code"].annotation.__args__
    en = (ROOT / "docs" / "api-contract.en.md").read_text(encoding="utf-8")
    ko = (ROOT / "docs" / "api-contract.md").read_text(encoding="utf-8")
    assert f"| `{CODE}` |" in en and f"`{CODE}`" in ko and "모두 35개 코드" in ko
    for name in ("README.md", "README.ko.md", "ahn/llms.txt", "plugins/claude-code/ahn/README.md"):
        assert CODE in (ROOT / name).read_text(encoding="utf-8"), name
    assert "| D55 (2026-10-24" in (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    hook = (ROOT / "plugins" / "claude-code" / "ahn" / "hooks" / "ahn_lookup.py").read_text(encoding="utf-8")
    assert f'"{CODE}"' in hook


def test_the_korean_search_page_shows_the_maintenance_text_for_a_maintenance_only_wait():
    template = (ROOT / "ahn" / "templates" / "search.html").read_text(encoding="utf-8")
    found = re.search(r"\{\{ item\.text if lang == 'en' else (next_check_ko\.get\(.*?, item\.text\)) \}\}", template)
    assert found
    expression = jinja2.Environment().compile_expression(found.group(1))
    ko = {**CHECKS_KO, "provider_maintenance": MAINTENANCE_CHECK_KO, "provider_page_stale": "unused"}

    def korean(code, provider_status):
        return expression(item={"code": code, "text": "english"}, signals={"provider_status": provider_status}, next_check_ko=ko)

    assert korean("provider_scope", cloudflare_shape()) == MAINTENANCE_CHECK_KO
    assert korean("provider_scope", status("maintenance", basis=None, incidents=None)) == MAINTENANCE_CHECK_KO
    assert korean("provider_scope", status("major", basis="incident", incidents=1)) == CHECKS_KO["provider_scope"]
    assert korean(CODE, cloudflare_shape()) == CHECKS_KO[CODE]
