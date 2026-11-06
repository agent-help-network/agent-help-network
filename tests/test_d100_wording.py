"""D100: a status_check answer for a status page that lists an unresolved incident does not say "no active issue".

Three new status sentences (docs/decisions.md D100), chosen only from provider_status and, for one of them, the stored platform marker:
  T6    a fresh reading whose indicator is none and that lists an unresolved incident (provider_status.active_incidents >= 1);
  T1-U  a fresh "none" reading of an incident.io-shaped page whose incident list AHN did not read;
  T6-F  T6, and the status page itself did not open for the caller (a T1-U page that did not open takes the existing T2-F).
Nothing but the sentence moves: ``do``, ``url`` and the recommendation are those of every other status_check row. The code from before D100
is kept as tests/_pre_d100_response_guide.py and tests/_pre_d100_triage.py; the comparison walks the grid of test_response_guide and
asserts both directions: no pair outside the D100 condition differs, every pair inside it differs exactly by that sentence, and the switch
off gives the old answer for every pair."""

from __future__ import annotations

import copy
import importlib.util
import itertools
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_d85_edge_refusal import CONSUMER, STAGES  # noqa: E402
from test_response_guide import NOW, OTHER_PAGE, main_space, page  # noqa: E402

from ahn import response_guide, triage  # noqa: E402
from ahn.response_guide import LINE_MAX, ROWS, STATUS_FAILED_ROW, STATUS_ROWS, guide, status_check_applies, status_row  # noqa: E402

_spec = importlib.util.spec_from_file_location("pre_d100_response_guide", HERE / "_pre_d100_response_guide.py")
pre_rg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pre_rg)
_spec_t = importlib.util.spec_from_file_location("pre_d100_triage", HERE / "_pre_d100_triage.py")
pre_t = importlib.util.module_from_spec(_spec_t)
_spec_t.loader.exec_module(pre_t)

T6 = ("The provider's status page lists an unresolved incident (provider_status.active_incidents), though its overall indicator reads "
      "operational. Check next_action.status_url; it may be partly affected.")
T1U = ("The provider's status feed's overall indicator reads operational, but AHN did not read its incident list. "
       "Check next_action.status_url before telling your user it is up.")
T6F = ("The provider's status page lists an unresolved incident (provider_status.active_incidents), and that page did not open for you. "
       "Tell your user the service may be partly affected.")
HANDOFF = ("human_verification", "login_auth")
MODES = ((True, False), (True, True))   # (proceed_without_failure, blocked_default)


@pytest.fixture(autouse=True)
def _stand_in_hosts_are_not_reserved(monkeypatch):
    monkeypatch.setattr(response_guide, "is_reserved_example", lambda host: False)
    monkeypatch.setattr(pre_rg, "is_reserved_example", lambda host: False)


def none_cases(stride: int):
    """The grid's cases whose provider reading is a fresh "none" with no incident count (the shape the cron stores for a quiet page)."""
    for index, case in enumerate(c for c in main_space() if (c["provider_status"] or {}).get("indicator") == "none"
                                 and (c["provider_status"] or {}).get("freshness") == "fresh"):
        if index % stride == 0:
            yield case
            official = case.get("official")
            yield {**case, "official": {**official, "consumer": CONSUMER} if official else
                   {"name": "Example", "api_docs": None, "developer_portal": None, "note": None, "consumer": CONSUMER}}


def with_count(case, count):
    status = dict(case["provider_status"])
    if count is not None:
        status.update(active_incidents=count, max_incident_impact="minor" if count else "none")
    return {**case, "provider_status": status}


def surfaces(case):
    own = case.get("site_host", "example.com")
    yield own, None
    yield "status.example.com", None          # the looked-up host is the status page itself (D96: it can have failed to open)
    yield own, page(OTHER_PAGE)


def build(case, rec, site_host, official_page):
    return {"site_host": site_host, "provider_status": case["provider_status"], "official_status_page": official_page,
            "access_profile": case.get("access_profile"), "official_paths": case.get("official"), "recommendation": rec}


def recommend(mod, case, stage, pwf):
    return mod.recommendation(case.get("error_class"), case.get("provider_status"), case.get("access_profile"), case.get("official"),
                              profile_not_read=case.get("not_read", False), provider_regional=case.get("regional", False), now=NOW,
                              agent_vendor=case.get("vendor"), failure_stage=stage, proceed_without_failure=pwf)


def swap(old: dict, before: str, after: str) -> dict:
    """The answer before D100 with its status sentence replaced (the sentence may lead a longer line)."""
    out = copy.deepcopy(old)
    out["summary_line"] = old["summary_line"].replace(before, after, 1)
    return out


# --- the whole grid ----------------------------------------------------------------------------------------------------------

def test_the_pairs_that_change_are_exactly_the_d100_condition_in_the_grid():
    counts = dict(pairs=0, t6=0, t1u=0, t6f=0, t2f=0, same=0, off=0)
    for base in none_cases(7):
        for site_host, official_page in surfaces(base):
            for stage, (pwf, blocked) in itertools.product(STAGES, MODES):
                ec = base.get("error_class")
                failed = ec is not None or stage is not None
                applies = status_check_applies("status_check", True, True, ec, stage)
                if not applies:
                    continue
                tail = (True, ec, stage, True)
                for count, unread in ((None, False), (None, True), (0, False), (0, True), (1, False), (3, False), (1, True)):
                    case = with_count(base, count)
                    rec = recommend(triage, case, stage, pwf)
                    assert rec == recommend(pre_t, case, stage, pwf)                        # the recommendation is not D100's
                    assert rec == recommend(triage, base, stage, pwf)                       # and an incident count does not move it
                    result = build(case, rec, site_host, official_page)
                    old = pre_rg.guide(copy.deepcopy(result), pwf, blocked, applies, *tail[:1], ec, stage, True, True)
                    # the switch off, whatever the other two inputs say: the answer before D100
                    assert guide(copy.deepcopy(result), pwf, blocked, applies, True, ec, stage, True, True, False, unread) == old
                    # the options not passed: the same (the defaults are the old answer too)
                    assert guide(copy.deepcopy(result), pwf, blocked, applies, True, ec, stage, True, True) == old
                    new = guide(copy.deepcopy(result), pwf, blocked, applies, True, ec, stage, True, True, True, unread)
                    counts["pairs"] += 1
                    listed = type(count) is int and count >= 1
                    has_t1, has_t1f = STATUS_ROWS["T1"] in old["summary_line"], STATUS_ROWS["T1-F"] in old["summary_line"]
                    if listed and (has_t1 or has_t1f):
                        want = swap(swap(old, STATUS_ROWS["T1"], T6), STATUS_ROWS["T1-F"], T6F)
                        kind = "t6f" if has_t1f else "t6"
                    elif unread and count is None and (has_t1 or has_t1f):
                        want = swap(swap(old, STATUS_ROWS["T1"], T1U), STATUS_ROWS["T1-F"], STATUS_ROWS["T2-F"])
                        kind = "t2f" if has_t1f else "t1u"
                    else:
                        want, kind = old, "same"
                    assert new == want, (kind, count, unread, site_host, stage, ec, base.get("access_profile"))
                    if kind in ("t6", "t1u"):
                        assert new["summary_line"].startswith(T6 if kind == "t6" else T1U)
                        assert new["summary_line"] != old["summary_line"]
                    if kind == "t6f":
                        assert new["summary_line"] == T6F and new["next_action"]["do"] == "hand_off_to_human" and new["next_action"]["url"] is None
                    # only the sentence moved: do, url and the other next_action keys are those of the answer before D100
                    assert new["next_action"] == old["next_action"] and new.get("links") == old.get("links")
                    assert len(new["summary_line"]) <= response_guide.STATUS_LINE_MAX
                    counts[kind] += 1
    print("D100 comparison", counts)
    assert counts["t6"] > 50 and counts["t1u"] > 50 and counts["t6f"] > 5 and counts["t2f"] > 5 and counts["same"] > 100, counts


def test_a_lookup_that_is_not_a_status_check_is_as_before():
    for base in none_cases(53):
        for count, unread in ((1, False), (None, True)):
            case = with_count(base, count)
            for stage, (pwf, blocked) in itertools.product(STAGES, MODES):
                ec = base.get("error_class")
                rec = recommend(triage, case, stage, pwf)
                result = build(case, rec, "example.com", None)
                old = pre_rg.guide(copy.deepcopy(result), pwf, blocked, False, True, ec, stage, True, True)
                assert guide(copy.deepcopy(result), pwf, blocked, False, True, ec, stage, True, True, True, unread) == old


def test_next_checks_say_scope_not_clear_for_a_fresh_none_that_lists_an_incident():
    seen = 0
    for base in none_cases(23):
        for count in (None, 0, 1, 3):
            case = with_count(base, count)
            for stage, pwf in itertools.product(STAGES, (True, False)):
                kwargs = dict(profile_not_read=case.get("not_read", False), provider_partial=case.get("regional", False), now=NOW,
                              agent_vendor=case.get("vendor"), proceed_without_failure=pwf)
                old = pre_t.next_checks(case.get("error_class"), case["provider_status"], "no_reports", stage, case.get("access_profile"),
                                        case.get("official"), **kwargs)
                args = (case.get("error_class"), case["provider_status"], "no_reports", stage, case.get("access_profile"), case.get("official"))
                assert triage.next_checks(*args, **kwargs) == old                                  # not asked for: as before
                assert triage.next_checks(*args, unresolved_listed=False, **kwargs) == old
                new = triage.next_checks(*args, unresolved_listed=True, **kwargs)
                if type(count) is int and count >= 1:
                    seen += 1
                    want = [{"code": "provider_scope", "text": triage.CHECKS["provider_scope"]} if c["code"] == "provider_clear" else c for c in old]
                    assert new == want
                    assert "provider_clear" not in [c["code"] for c in new] and [c["code"] for c in new].count("provider_scope") == 1
                else:
                    assert new == old
    assert seen > 100


def test_a_stale_or_active_reading_is_not_touched_by_the_incident_count():
    status = {"source_name": "S", "source_url": "https://status.example.com/", "indicator": "none", "freshness": "stale",
              "active_incidents": 2, "max_incident_impact": "minor"}
    assert status_row(status, None, status["source_url"], True, True) == status_row(status, None, status["source_url"]) == "T5"
    pending = {**status, "freshness": "pending"}
    assert status_row(pending, None, pending["source_url"], True, True) == "T2"
    for indicator in ("minor", "major", "critical", "maintenance", "unknown"):
        active = {**status, "freshness": "fresh", "indicator": indicator}
        assert status_row(active, None, status["source_url"], True, True) == status_row(active, None, status["source_url"]) == "T2"
    for odd in (True, 1.0, "1", -1, 0, None):
        fresh = {**status, "freshness": "fresh", "active_incidents": odd}
        assert status_row(fresh, None, status["source_url"], True, False) == "T1", odd
    assert status_row({**status, "freshness": "fresh", "active_incidents": 1}, None, status["source_url"], True, False) == "T6"
    assert status_row({**status, "freshness": "fresh", "active_incidents": 1}, None, status["source_url"], False, True) == "T1"
    assert status_row({**status, "freshness": "fresh", "active_incidents": 0}, None, status["source_url"], True, True) == "T1"
    # an unread list on a page that has a count is not "unread"; the service only sets the flag when the count is missing
    assert status_row({**status, "freshness": "fresh", "active_incidents": None}, None, status["source_url"], True, True) == "T1-U"


# --- the sentences ----------------------------------------------------------------------------------------------------------

def test_the_sentences_are_the_ones_named_short_plain_and_carry_no_host_or_url():
    assert STATUS_ROWS["T6"] == T6 and STATUS_ROWS["T1-U"] == T1U and STATUS_ROWS["T6-F"] == T6F
    assert (len(T6), len(T1U), len(T6F)) == (196, 169, 178)
    for text in (T6, T1U, T6F):
        assert len(text) <= LINE_MAX and re.fullmatch(r"[\x20-\x7E]+", text) and "{" not in text and "http" not in text
        assert not re.search(r"\b(bypass|evade|spoof|proxy|solver|stealth|headless|work around|get past)\b", text, re.I)
    assert STATUS_FAILED_ROW["T6"] == "T6-F" and STATUS_FAILED_ROW["T1-U"] == "T2-F"
    # the first sentence tells what to do: a status page to check (T6, T1-U) or what to tell the user (T6-F)
    assert "Check next_action.status_url" in T6 and "Check next_action.status_url" in T1U and "Tell your user" in T6F


def test_every_new_sentence_fits_before_every_row_sentence_that_can_follow_it():
    after = list(response_guide.STATUS_AFTER_ROWS.values()) + list(response_guide.STATUS_AFTER_ROWS_BEFORE.values())
    for lead in (T6, T1U, T6F):
        for tail in [text for _, text in ROWS.values()] + after:
            assert len(lead) + 1 + len(tail) <= response_guide.STATUS_LINE_MAX


def test_no_table_other_than_the_status_rows_moved():
    assert {k: v for k, v in STATUS_ROWS.items() if k not in ("T6", "T1-U", "T6-F")} == pre_rg.STATUS_ROWS
    assert ROWS == pre_rg.ROWS and response_guide.STATUS_AFTER_ROWS == pre_rg.STATUS_AFTER_ROWS
    assert response_guide.CONSUMER_ROWS == pre_rg.CONSUMER_ROWS and response_guide.PROCEED_CONSUMER_ROWS == pre_rg.PROCEED_CONSUMER_ROWS
    assert {k: v for k, v in STATUS_FAILED_ROW.items() if k not in ("T6", "T1-U")} == pre_rg.STATUS_FAILED_ROW
    assert triage.CHECKS == pre_t.CHECKS and triage.CHECKS_KO == pre_t.CHECKS_KO


def test_documents_name_the_rule():
    root = HERE.parent
    decisions = (root / "docs" / "decisions.md").read_text(encoding="utf-8")
    assert "| D100 " in decisions and "AHN_D100_INCIDENTIO_INCIDENTS" in decisions and "summary.json" in decisions
    for name in ("api-contract.md", "api-contract.en.md"):
        text = (root / "docs" / name).read_text(encoding="utf-8")
        assert "D100" in text and "summary.json" in text, name
