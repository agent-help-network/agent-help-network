"""D101: a service_error answer (a 5xx or no response) whose first line sends the caller to no path outside AHN is answered
hand_off_to_human, so the first line and next_action.do say the same thing.

Pure: no database (the service wiring is in test_d101_failure_line_matches_do_db.py). The decision code from before D101 is kept as
tests/_pre_d101_triage.py and tests/_pre_d101_response_guide.py. The comparison walks the same input space as D85 (test_response_guide.space())
and asserts both directions: no pair outside the D101 condition differs, and every pair inside it does. A second test checks every
sentence the guide produces against next_action.do by machine (a sentence that says to retry, hand off or use a path has the matching do)."""

from __future__ import annotations

import importlib.util
import itertools
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_d85_edge_refusal import GUIDE_MODES, STAGES, variants  # noqa: E402,F401
from test_response_guide import NOW  # noqa: E402
import _d111_compat as d111  # noqa: E402

from ahn import response_guide, settings as settings_module, triage  # noqa: E402
from ahn.models import NextCheck  # noqa: E402
from ahn.official_paths import customer_url  # noqa: E402
from ahn.response_guide import LINE_MAX, ROWS, guide, select_row  # noqa: E402
from ahn.settings import Settings, parse_d101_switch  # noqa: E402
from ahn.triage import CHECKS, CHECKS_KO, NO_PATH_CODE  # noqa: E402

_spec = importlib.util.spec_from_file_location("pre_d101_triage", HERE / "_pre_d101_triage.py")
pre = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pre)
_spec_rg = importlib.util.spec_from_file_location("pre_d101_response_guide", HERE / "_pre_d101_response_guide.py")
pre_rg = importlib.util.module_from_spec(_spec_rg)
_spec_rg.loader.exec_module(pre_rg)

FULL = os.environ.get("AHN_D101_FULL") == "1"   # the one-off full walk of the grid (the PR numbers); the suite walks every 29th case
HELP = "https://www.example.com/help"
CONSUMER = {"paths": [{"kind": "customer_help", "url": HELP, "source_url": "https://www.example.com/"}], "verified_at": "2026-10-31"}
DEV = {"name": "Example", "api_docs": "https://developer.example.com/docs", "developer_portal": None, "note": None}
NO_DEV = {"name": "Example", "api_docs": None, "developer_portal": None, "note": None}
UNREAD = {"robots_status": "error", "robots": None, "freshness": "stale", "llms_txt_status": "absent"}
REFUSED = {"robots_status": "refused", "robots": None, "freshness": "fresh", "llms_txt_status": "absent"}
ALLOWS = {"robots_status": "absent", "robots": None, "freshness": "fresh", "llms_txt_status": "absent"}
NO_PATH_ROW_IDS = {"U6", "U7", "U9", "U7-O", "U9-O"}
O7_ROW_IDS = {"O7", "O7-N", "O7-K"}
FORBIDDEN = ("bypass", "evade", "spoof", "proxy", "solver", "stealth", "headless", "work around", "get past")
BTR = "before_target_response"


def rec_of(mod, case, stage, pwf, *, ref=False, d101=None, same=False):
    kwargs = dict(profile_not_read=case.get("not_read", False), provider_regional=case.get("regional", False), now=NOW,
                  agent_vendor=case.get("vendor"), failure_stage=stage, proceed_without_failure=pwf, service_error_reference_only=ref)
    if d101 is not None:
        kwargs.update(no_path_handoff=d101, customer_same_site=same, apex_lookup=bool(response_guide.apex_host(case.get("site_host", "example.com"))))
    return mod.recommendation(case.get("error_class"), case.get("provider_status"), case.get("access_profile"), case.get("official"), **kwargs)


def checks_of(mod, case, stage, pwf, *, no_path=None):
    kwargs = dict(profile_not_read=case.get("not_read", False), provider_partial=case.get("regional", False), now=NOW,
                  agent_vendor=case.get("vendor"), proceed_without_failure=pwf)
    if no_path is not None:
        kwargs["no_path"] = no_path
    return mod.next_checks(case.get("error_class"), case.get("provider_status"), "no_reports", stage, case.get("access_profile"),
                           case.get("official"), **kwargs)


def result_of(case, rec):
    return {"site_host": case.get("site_host", "example.com"), "provider_status": case.get("provider_status"), "official_status_page": None,
            "access_profile": case.get("access_profile"), "official_paths": case.get("official"), "recommendation": rec}


def old_row(case, rec, pwf):
    return pre_rg.select_row(rec, case.get("access_profile"), case.get("provider_status"), case.get("site_host", "example.com"),
                             case.get("official"), variants=pwf)[0]


def is_inside(case, old_rec, stage, pwf, same):
    """The D101 condition, stated by the row the answer had before D101 (not by the reasons the implementation reads)."""
    if case.get("error_class") != "service_error" or not pwf:
        return False
    row = old_row(case, old_rec, pwf)
    customer = customer_url(case.get("official")) is not None
    if row in NO_PATH_ROW_IDS:
        return not customer or (same and stage == BTR)
    return row in O7_ROW_IDS and customer and same and stage == BTR


def guide_args(case, stage, pwf):
    return (pwf, False, False, True, case.get("error_class"), stage, True, True)


def compare(stride: int) -> dict:
    counts = dict(rec_pairs=0, rec_changed=0, guide_pairs=0, guide_changed=0, cases=0)
    for case in variants(stride):
        counts["cases"] += 1
        for stage, pwf, ref, same in itertools.product(STAGES, (True, False), (False, True), (False, True)):
            old_rec = rec_of(pre, case, stage, pwf, ref=ref)
            old_checks = checks_of(pre, case, stage, pwf)
            assert rec_of(triage, case, stage, pwf, ref=ref) == old_rec  # the call every other path makes
            assert rec_of(triage, case, stage, pwf, ref=ref, d101=False, same=same) == old_rec  # the switch off
            new_rec = rec_of(triage, case, stage, pwf, ref=ref, d101=True, same=same)
            new_checks = checks_of(triage, case, stage, pwf, no_path=NO_PATH_CODE in new_rec["reasons"])
            counts["rec_pairs"] += 1
            inside = is_inside(case, old_rec, stage, pwf, same)
            if not inside:
                assert new_rec == old_rec and new_checks == old_checks, (case, stage, pwf, ref, same)
            else:
                counts["rec_changed"] += 1
                assert {k: v for k, v in new_rec.items() if k != "agent_vendor"} == {
                    "action": "hand_off_to_human", "confidence": "low", "reasons": [*old_rec["reasons"], NO_PATH_CODE]}, (case, stage)
                assert new_rec.get("agent_vendor") == old_rec.get("agent_vendor"), "the vendor echo stays"
                old_codes = [c["code"] for c in old_checks]
                new_codes = [c["code"] for c in new_checks]
                assert new_codes[1] == NO_PATH_CODE and new_codes[:1] + new_codes[2:] == old_codes, (new_codes, old_codes)
                assert new_checks[1]["text"] == CHECKS[NO_PATH_CODE]
                assert [c for c in new_checks if c["code"] != NO_PATH_CODE] == old_checks
            old_out = pre_rg.guide(result_of(case, old_rec), *guide_args(case, stage, pwf))
            new_out = guide(result_of(case, new_rec), *guide_args(case, stage, pwf))
            counts["guide_pairs"] += 1
            if not inside:
                want = d111.answer(old_out, case.get("site_host", "example.com"), case.get("error_class"), stage,
                                   response_guide.status_url_of(result_of(case, new_rec)))
                assert new_out == want, (case, stage, pwf, same)
                continue
            counts["guide_changed"] += 1
            # D100 only words a status check; with it on, an answer D101 hands off is the same answer
            assert guide(result_of(case, new_rec), *guide_args(case, stage, pwf), d100=True, incidentio_unread=True) == new_out, (case, stage)
            action = new_out["next_action"]
            assert action["do"] == "hand_off_to_human" and action["url"] is None and action["retry_after_seconds"] is None, (case, stage)
            assert len(new_out["summary_line"]) <= LINE_MAX and new_out["summary_line"] != old_out["summary_line"]
            assert "tell your user now" in new_out["summary_line"].lower() and "etry once later" not in new_out["summary_line"]
            curated = customer_url(case.get("official")) is not None
            assert action.get("customer_url") == (HELP if curated else None), (case, stage)
            assert {k: v for k, v in action.items() if k not in ("do", "url", "customer_url")} == \
                {k: v for k, v in old_out["next_action"].items() if k not in ("do", "url", "customer_url")}
            assert new_out.get("links") == old_out.get("links")
            if curated:
                assert "next_action.customer_url" in new_out["summary_line"]
            else:
                assert new_out["summary_line"].endswith(("themselves.", "official_paths."))
    return counts


def test_the_pairs_that_change_are_exactly_the_d101_condition_in_the_whole_grid():
    counts = compare(1 if FULL else 29)
    print("D101 comparison", counts)
    assert counts["rec_changed"] > 100 and counts["guide_changed"] > 100
    assert counts["rec_pairs"] > 40000


# --- the shapes the failure was seen on -------------------------------------------------------------------------------------

def answer(error_class, stage, *, profile, official, not_read=False, host="example.com", same=False):
    case = dict(error_class=error_class, access_profile=profile, official=official, not_read=not_read, site_host=host)
    rec = rec_of(triage, case, stage, True, ref=True, d101=True, same=same)
    return rec, guide(result_of(case, rec), *guide_args(case, stage, True))


def test_a_united_shaped_lookup_robots_unread_no_path_no_response_is_handed_to_the_user():
    rec, out = answer("service_error", BTR, profile=UNREAD, official=None)
    assert rec == {"action": "hand_off_to_human", "confidence": "low", "reasons": ["robots_unknown", NO_PATH_CODE]}
    assert out["next_action"] == {"do": "hand_off_to_human", "url": None, "retry_after_seconds": None}
    assert out["summary_line"] == ("No response came (timeout or connection), and AHN could not read the site's robots.txt either. "
                                   "Tell your user now; they can open the page themselves.")


def test_a_dhl_shaped_lookup_a_developer_path_and_robots_unread_keeps_the_api_pointer():
    rec, out = answer("service_error", BTR, profile=UNREAD, official=DEV)
    assert rec["action"] == "hand_off_to_human" and rec["reasons"] == ["robots_unknown", "official_path_available", NO_PATH_CODE]
    assert out["next_action"]["do"] == "hand_off_to_human" and out["next_action"]["url"] is None
    assert out["summary_line"] == ("No response came (timeout or connection), and AHN could not read robots.txt. "
                                   "Tell your user now; they can open the page themselves. API work: official_paths.")


def test_a_bestbuy_shaped_lookup_a_customer_page_on_the_same_site_is_handed_over_with_it():
    official = {**DEV, "consumer": CONSUMER}
    before = pre.recommendation("service_error", None, UNREAD, official, failure_stage=BTR, service_error_reference_only=True, now=NOW)
    assert before["action"] == "use_official_path"
    rec, out = answer("service_error", BTR, profile=UNREAD, official=official, same=True)
    assert rec == {"action": "hand_off_to_human", "confidence": "low", "reasons": ["official_path_available", NO_PATH_CODE]}
    assert out["next_action"] == {"do": "hand_off_to_human", "url": None, "retry_after_seconds": None, "customer_url": HELP}
    assert out["summary_line"] == ("No response came (timeout or connection). The customer page is on the same site and may not open either; "
                                   "tell your user now and hand them next_action.customer_url. API work: official_paths.")
    # the pointer is only there while a developer path is listed
    rec, out = answer("service_error", BTR, profile=UNREAD, official={**NO_DEV, "consumer": CONSUMER}, same=True)
    assert out["summary_line"] == response_guide.HANDOFF_NO_RESPONSE_CUSTOMER
    for stage in (None, "response_observed"):  # a 5xx with the customer page on the same site is answered as before
        rec, out = answer("service_error", stage, profile=UNREAD, official={**DEV, "consumer": CONSUMER}, same=True)
        assert rec["action"] == "use_official_path" and NO_PATH_CODE not in rec["reasons"]


def test_the_api_pointer_follows_official_path_available_on_every_hand_off_with_a_customer_page():
    official = {**DEV, "consumer": CONSUMER}
    for profile in (UNREAD, None, ALLOWS):
        for stage in (BTR, "response_observed"):
            for not_read in (False, True):
                rec, out = answer("service_error", stage, profile=profile, official=official, not_read=not_read, same=stage == BTR)
                if rec["action"] != "hand_off_to_human" or "customer_url" not in out["next_action"]:
                    continue
                assert "official_path_available" in rec["reasons"]
                assert out["summary_line"].endswith(" API work: official_paths.") and len(out["summary_line"]) <= LINE_MAX
                assert "next_action.customer_url" in out["summary_line"]
    assert len(response_guide.HANDOFF_NO_RESPONSE_CUSTOMER + response_guide._HANDOFF_API) <= LINE_MAX
    assert len(response_guide.HANDOFF_SERVICE_ERROR_CUSTOMER + response_guide._HANDOFF_API) <= LINE_MAX
    assert "official_paths" in CHECKS[NO_PATH_CODE] and "no page of this site" not in CHECKS[NO_PATH_CODE]


def test_a_customer_page_on_another_site_or_after_a_5xx_keeps_the_answer_from_before():
    official = {**NO_DEV, "consumer": CONSUMER}
    for stage, same in ((BTR, False), ("response_observed", True), ("response_observed", False)):
        rec, out = answer("service_error", stage, profile=UNREAD, official=official, same=same)
        assert rec == pre.recommendation("service_error", None, UNREAD, official, failure_stage=stage, service_error_reference_only=True, now=NOW)
        assert out["next_action"]["do"] == "no_clear_action"


FIVE_XX = {
    "U7-H": "AHN cannot judge a single page's 5xx, and could not read the site's robots.txt. Tell your user now; they can open the page themselves.",
    "U6-H": "AHN cannot judge a single page's 5xx; the site also refused AHN's plain robots.txt read. Tell your user now; they can open the page themselves.",
    "U9-H": "AHN cannot judge a single page's 5xx, and has no declared policy for this host. Tell your user now; they can open the page themselves.",
}
FIVE_XX["U7-O-H"] = FIVE_XX["U7-H"] + " API work: official_paths."
FIVE_XX["U9-O-H"] = FIVE_XX["U9-H"] + " API work: official_paths."


@pytest.mark.parametrize("profile,official,not_read,row", [
    (UNREAD, None, False, "U7-H"), (REFUSED, None, False, "U6-H"), (None, None, False, "U9-H"), (None, None, True, "U9-H"),
    (UNREAD, DEV, False, "U7-O-H"), (None, DEV, True, "U9-O-H")])
def test_a_5xx_on_each_row_is_handed_to_the_user_with_one_more_try(profile, official, not_read, row):
    rec, out = answer("service_error", "response_observed", profile=profile, official=official, not_read=not_read)
    assert rec["action"] == "hand_off_to_human" and rec["reasons"][-1] == NO_PATH_CODE
    assert select_row(rec, profile, None, "example.com", official)[0] == row
    assert out["next_action"] == {"do": "hand_off_to_human", "url": None, "retry_after_seconds": None}
    assert out["summary_line"] == FIVE_XX[row]


def test_a_subdomain_that_is_sent_to_its_root_domain_is_not_handed_off():
    rec, out = answer("service_error", BTR, profile=None, official=None, not_read=True, host="app.example.com")
    assert rec["action"] == "unknown" and out["next_action"]["do"] == "lookup_apex_host"


def test_other_failures_and_a_failure_free_lookup_are_not_handed_off():
    for error_class in (None, "site_access", "human_verification", "login_auth"):
        case = dict(error_class=error_class, access_profile=UNREAD, official=None)
        assert rec_of(triage, case, None, True, ref=True, d101=True) == rec_of(pre, case, None, True, ref=True)
    # a pending read still waits for the re-lookup
    pending = {"robots_status": "pending", "robots": None, "freshness": "pending", "llms_txt_status": "absent", "retry_after_seconds": 120}
    case = dict(error_class="service_error", access_profile=pending, official=None)
    assert rec_of(triage, case, BTR, True, ref=True, d101=True) == rec_of(pre, case, BTR, True, ref=True)


# --- the machine check: a sentence says what next_action.do is --------------------------------------------------------------

RETRY_RE = re.compile(r"retry once later", re.I)
ONE_MORE = "tell your user now"  # D111: the hand-off line that used to say "if one more try fails"
# D101 leaves the sentences of a customer page on another site, and of a 5xx with a customer page on the same site, as they were (D97): the
# agent is handed the page by next_action.customer_url while do stays no_clear_action. They are the only ones that still say "retry once later".
KNOWN_RETRY_WITH_NO_CLEAR_ACTION = set(response_guide.NO_PATH_NO_RESPONSE_CONSUMER_ROWS.values()) | set(response_guide.NO_PATH_SERVICE_ERROR_CONSUMER_ROWS.values())


def machine_check(do, sentence, customer_url_present):
    if RETRY_RE.search(sentence) and do in ("no_clear_action", "hand_off_to_human", "stop_automation"):
        assert sentence in KNOWN_RETRY_WITH_NO_CLEAR_ACTION and do == "no_clear_action" and customer_url_present, (do, sentence)
        return "known"
    if ONE_MORE in sentence.lower():
        assert do in ("hand_off_to_human", "stop_automation"), (do, sentence)
        return "one_more"
    return None


def test_every_sentence_the_guide_produces_agrees_with_its_do():
    seen = {"known": 0, "one_more": 0}
    for case in variants(1 if FULL else 13):
        for stage, same in itertools.product(STAGES, (False, True)):
            rec = rec_of(triage, case, stage, True, ref=True, d101=True, same=same)
            out = guide(result_of(case, rec), *guide_args(case, stage, True))
            kind = machine_check(out["next_action"]["do"], out["summary_line"], "customer_url" in out["next_action"])
            if kind:
                seen[kind] += 1
    assert seen["known"] > 0 and seen["one_more"] > 100, seen
    # the static tables: the ROWS the table lists carry no "retry once later" on a row that does not retry
    for row, (do, sentence) in ROWS.items():
        machine_check(do, sentence.replace("{n}", "120"), False) if row not in ("U2",) else None
    # the new rows exist for every hand-off the code can pick, and say "tell your user now"
    for table in (response_guide.HANDOFF_NO_RESPONSE_ROWS, response_guide.HANDOFF_SERVICE_ERROR_ROWS):
        assert set(table) == set(response_guide.HANDOFF_ROWS) - {"O7-H"}
        for sentence in table.values():
            assert ONE_MORE in sentence.lower() and len(sentence) <= LINE_MAX
    for sentence in (response_guide.HANDOFF_NO_RESPONSE_CUSTOMER, response_guide.HANDOFF_SERVICE_ERROR_CUSTOMER):
        assert ONE_MORE in sentence.lower() and "next_action.customer_url" in sentence and len(sentence) <= LINE_MAX


def test_the_new_sentences_are_plain_text_without_hosts_urls_or_bypass_advice():
    texts = [*response_guide.HANDOFF_NO_RESPONSE_ROWS.values(), *response_guide.HANDOFF_SERVICE_ERROR_ROWS.values(),
             response_guide.HANDOFF_NO_RESPONSE_CUSTOMER, response_guide.HANDOFF_SERVICE_ERROR_CUSTOMER,
             response_guide.H3_STALE, response_guide.H3_STALE_CONSUMER, CHECKS[NO_PATH_CODE]]
    for text in texts:
        assert len(text) <= LINE_MAX and "http" not in text and "{" not in text, text
        assert not any(word in text.lower() for word in FORBIDDEN), text
    assert CHECKS_KO[NO_PATH_CODE] and CHECKS_KO[NO_PATH_CODE] != CHECKS[NO_PATH_CODE]


# --- H3 sentence when the home-page check is over a day old -----------------------------------------------------------------

def h3(official, stale):
    rec = {"action": "hand_off_to_human", "confidence": "medium", "reasons": ["edge_refusal_confirmed", "robots_allows_agents"]}
    case = dict(error_class="site_access", access_profile=ALLOWS, official=official)
    return guide(result_of(case, rec), True, False, False, True, "site_access", None, True, True, edge_stale=stale)


def test_the_h3_sentence_says_when_the_home_page_check_is_over_a_day_old_and_nothing_else_changes():
    fresh, stale = h3(None, False), h3(None, True)
    assert fresh["summary_line"] == ROWS["H3"][1]
    assert stale["summary_line"] == response_guide.H3_STALE and "over a day ago" in stale["summary_line"]
    assert "Do not switch tools or user agents; " in stale["summary_line"] and "or user agents" in ROWS["H3"][1]
    assert stale["next_action"] == fresh["next_action"] and stale.get("links") == fresh.get("links")
    curated = {**NO_DEV, "consumer": CONSUMER}
    fresh_c, stale_c = h3(curated, False), h3(curated, True)
    assert stale_c["summary_line"] == response_guide.H3_STALE_CONSUMER and stale_c["next_action"] == fresh_c["next_action"]
    assert stale_c["next_action"]["customer_url"] == HELP
    # only the H3 row is affected
    rec = {"action": "hand_off_to_human", "confidence": "medium", "reasons": ["human_handoff"]}
    case = dict(error_class="human_verification", access_profile=ALLOWS, official=None)
    assert guide(result_of(case, rec), True, False, False, True, edge_stale=True) == guide(result_of(case, rec), True, False, False, True)
    assert guide(result_of(case, rec), True, False, False, True, edge_stale=True) == pre_rg.guide(result_of(case, rec), True, False, False, True)


def test_edge_stale_is_keyword_only_so_a_later_positional_argument_cannot_land_in_it():
    rec = {"action": "hand_off_to_human", "confidence": "medium", "reasons": ["edge_refusal_confirmed", "robots_allows_agents"]}
    case = dict(error_class="site_access", access_profile=ALLOWS, official=None)
    with pytest.raises(TypeError):
        guide(result_of(case, rec), True, False, False, True, "site_access", None, True, True, True, True, True)


# --- contract and wiring ----------------------------------------------------------------------------------------------------

def test_the_new_code_is_a_published_next_check_and_the_switch_parses_like_the_others(monkeypatch, caplog):
    assert NO_PATH_CODE in NextCheck.model_json_schema()["properties"]["code"]["enum"]
    assert parse_d101_switch("") is True and parse_d101_switch("1") is True and parse_d101_switch("0") is False
    monkeypatch.setattr(settings_module, "_D101_WARNED", False)
    with caplog.at_level("WARNING"):
        assert parse_d101_switch("false") is False and parse_d101_switch("maybe") is False
    assert [r.getMessage() for r in caplog.records].count("d101_switch_unknown_value") == 1 and "maybe" not in caplog.text
    assert Settings.__dataclass_fields__["d101_no_path_handoff"].default is True
