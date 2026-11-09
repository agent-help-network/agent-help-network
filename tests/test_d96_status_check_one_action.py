"""D96: a task_step=status_check lookup is answered with the status page as its one next action.

Before D96 a failure signal sent with status_check switched the status answer off, so the row's own do and url applied (often a developer
document), and on a proceed row the status page was only named in next_action.status_url. Now, with AHN_D96_STATUS_CHECK_ONE_ACTION:
- every status_check answer whose row does not wait, hand off, stop, wait for a re-lookup or rest on a robots block gives
  do=use_official_path with url=the status page;
- a failure signal no longer switches the status answer off (human_verification and login_auth still go to a person);
- when the status page is the host that failed, the step goes to the user (hand_off_to_human, no url).
The recommendation, next_checks and every lookup that is not a status_check lookup are unchanged.

The guide from before D96 is kept as tests/_pre_d96_response_guide.py. The comparison walks the grid of test_response_guide.space() (each case
with and without a curated customer path, on the looked-up host and on its status host, with and without a status page of its own) and asserts
both directions: no pair outside the D96 condition differs, every pair inside it changes to exactly the answer the contract names, and the
switch off gives the old answer for every pair."""

from __future__ import annotations

import copy
import importlib.util
import itertools
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_d85_edge_refusal import ALLOWS, CONSUMER, FULL, NOW, STAGES, variants  # noqa: E402
from test_response_guide import OTHER_PAGE, fresh_none, page, signin_page  # noqa: E402
import _d97_scope  # noqa: E402

from ahn import response_guide, settings as settings_module, triage  # noqa: E402
from ahn.response_guide import LINE_MAX, RETRY_ROWS, ROWS, STATUS_FAILED_ROW, STATUS_ROWS, status_check_applies  # noqa: E402
from ahn.response_guide import guide as _guide  # noqa: E402
import _d111_compat as d111  # noqa: E402
from ahn.settings import Settings, parse_d96_switch  # noqa: E402


def guide(*args, **kwargs):
    """The guide without next_action.status_url_covers (D102 added it; the D96 comparisons are about the action and the sentence)."""
    out = _guide(*args, **kwargs)
    out["next_action"].pop("status_url_covers", None)
    return out

_spec = importlib.util.spec_from_file_location("pre_d96_response_guide", HERE / "_pre_d96_response_guide.py")
pre_rg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pre_rg)

HANDOFF = ("human_verification", "login_auth")
ROBOTS_CODES = response_guide.STATUS_KEEP_REASONS


@pytest.fixture(autouse=True)
def _stand_in_hosts_are_not_reserved(monkeypatch):
    monkeypatch.setattr(response_guide, "is_reserved_example", lambda host: False)
    monkeypatch.setattr(pre_rg, "is_reserved_example", lambda host: False)


def build(case, rec, *, site_host=None, official_page=None):
    return {"site_host": site_host or case.get("site_host", "example.com"), "provider_status": case.get("provider_status"),
            "official_status_page": official_page, "access_profile": case.get("access_profile"), "official_paths": case.get("official"),
            "recommendation": rec}


def recommend(case, stage, pwf):
    return triage.recommendation(case.get("error_class"), case.get("provider_status"), case.get("access_profile"), case.get("official"),
                                 profile_not_read=case.get("not_read", False), provider_regional=case.get("regional", False), now=NOW,
                                 agent_vendor=case.get("vendor"), failure_stage=stage, proceed_without_failure=pwf)


def surfaces(case):
    """The places the grid is asked from: its own host with the stored feed, the feed's own host, and a status page that is not a feed."""
    own = case.get("site_host", "example.com")
    yield own, None
    if "site_host" not in case:
        yield "status.example.com", None
    yield own, page(OTHER_PAGE)
    yield own, signin_page(OTHER_PAGE)
    yield own, {**page(OTHER_PAGE), "covers": "api"}


def status_url_of(result):
    for candidate in ((result["provider_status"] or {}).get("source_url"), (result["official_status_page"] or {}).get("source_url")):
        if candidate:
            return candidate
    return None


def status_id_of(result, url):
    return response_guide.status_row(result["provider_status"], result["official_status_page"], url)


def expected_new(result, rec, on, old_plain, old_sc, failed):
    """What D96 must give, from the inputs and the two answers before D96 (one built as the service built it for this lookup, one with the
    status answer forced on). Written from the contract, not from the code."""
    row, _ = response_guide.select_row(rec, result["access_profile"], result["provider_status"], result["site_host"], result["official_paths"],
                                          variants=on)
    do = ROWS[row][0]
    url = status_url_of(result)
    sid = status_id_of(result, url)
    robots = bool(set(rec["reasons"]) & ROBOTS_CODES)
    keeps = do in ("wait_for_provider", "hand_off_to_human", "stop_automation") or row in RETRY_ROWS or row == "P2" or robots
    same_host = bool(url) and re.match(r"https://([^/]+)", url).group(1).lower() == result["site_host"].lower()
    if failed and url and same_host and not keeps:
        out = copy.deepcopy(old_sc)
        out["summary_line"] = STATUS_ROWS[STATUS_FAILED_ROW[sid]]
        out["next_action"].update(do="hand_off_to_human", url=None)
        out["next_action"].pop("lookup_host", None)
        return out, "self_failed"
    if url and not keeps and rec["action"] != "use_official_path" and sid != "T3-S":
        out = copy.deepcopy(old_sc)
        out["next_action"].update(do="use_official_path", url=url)
        out["next_action"].pop("lookup_host", None)
        return out, "widened"
    same_site = bool(url) and response_guide.same_site(url, result["site_host"])
    d76 = bool(url) and rec["action"] == "use_official_path" and row not in RETRY_ROWS and not (robots and (same_site or sid == "T3-S"))
    d69 = (do not in ("wait_for_provider", "hand_off_to_human", "stop_automation") and row not in RETRY_ROWS and not robots and row != "P2"
           and not (do == "lookup_apex_host" and url is None))
    if failed and not (d76 or d69):
        return old_plain, "failed_unchanged"
    out = copy.deepcopy(old_sc)
    action = out["next_action"]
    # no status page is known: a developer path is never the url of a status answer (rule 4); the step is the user's after a failure
    if d69 and not d76 and url is None and action["url"] is not None and action["url"] != action.get("customer_url"):
        action.update(do="hand_off_to_human" if failed else "no_clear_action", url=None)
        action.pop("lookup_host", None)
        out["summary_line"] = STATUS_ROWS["T4-R"] if failed and sid == "T4" else out["summary_line"]
        return out, "dev_path_dropped"
    # a failed lookup is not told to "check the site itself": T4-R says its status is unverified
    if failed and d69 and url is None and sid == "T4":
        out["summary_line"] = STATUS_ROWS["T4-R"]
        return out, "failed_unverified"
    return out, "unchanged_status" if not failed else "failed_status_on"


MODES = ((True, False), (True, True), (False, False))   # (proceed_without_failure, blocked_default)


def test_the_pairs_that_change_are_exactly_the_d96_condition_in_the_whole_grid():
    counts = dict(pairs=0, changed=0, off=0, other_step=0)
    kinds = {}
    # every case with a developer path and no status feed is walked (the answers that must not name that path), the rest on a stride
    no_feed_dev_path = (c for c in variants(1) if c.get("provider_status") is None and c.get("official")
                        and (c["official"].get("api_docs") or c["official"].get("developer_portal")))
    for case in itertools.chain(variants(1 if FULL else 701), () if FULL else no_feed_dev_path):
        for site_host, official_page in surfaces(case):
            for stage, (pwf, blocked) in itertools.product(STAGES, MODES):
                rec = recommend(case, stage, pwf)
                result = build(case, rec, site_host=site_host, official_page=official_page)
                ec = case.get("error_class")
                failed = ec is not None or stage is not None
                for kind in (True, False):
                    args = (pwf, blocked)
                    tail = (True, ec, stage, kind)
                    # the answer before D96: the service asked for the status answer only without a failure signal
                    old_plain = pre_rg.guide(copy.deepcopy(result), *args, False, *tail)
                    old_sc = pre_rg.guide(copy.deepcopy(result), *args, True, *tail)
                    # D97 later changed U6/U7/U9 after a service_error where no status answer applies (and with a curated customer path)
                    row0, _ = response_guide.select_row(rec, result["access_profile"], result["provider_status"], result["site_host"],
                                                        result["official_paths"], variants=pwf)
                    old_plain = _d97_scope.adjust(old_plain, row0, pwf, False, ec, stage, kind, result.get("official_paths"))
                    # D111: the hand-off wording, and no status_url that points back at the host that failed
                    old_plain = d111.answer(old_plain, site_host, ec, stage, response_guide.status_url_of(result))
                    old_sc = d111.answer(old_sc, site_host, ec, stage, response_guide.status_url_of(result))
                    old = old_sc if not failed else old_plain
                    # switch off: the answer before D96, for every pair
                    sc_off = status_check_applies("status_check", True, False, ec, stage)
                    assert sc_off is (not failed)
                    assert guide(copy.deepcopy(result), *args, sc_off, *tail, one_action=False) == old, (case, stage, args)
                    # not a status_check lookup: the answer before D96, switch on or off
                    assert not status_check_applies("login", True, True, ec, stage) and not status_check_applies(None, True, True, ec, stage)
                    assert guide(copy.deepcopy(result), *args, False, *tail, one_action=True) == old_plain
                    counts["other_step"] += 1
                    counts["off"] += 1
                    # switch on
                    applies = status_check_applies("status_check", True, True, ec, stage)
                    assert applies is (not failed or ec not in HANDOFF)
                    new = guide(copy.deepcopy(result), *args, applies, *tail, one_action=True)
                    counts["pairs"] += 1
                    if not applies:
                        assert new == old_plain, (case, stage, args)       # human_verification / login_auth with a failure
                        continue
                    want, kind_name = expected_new(result, rec, pwf, old_plain, old_sc, failed)
                    assert new == want, (kind_name, case, site_host, stage, args)
                    kinds[kind_name] = kinds.get(kind_name, 0) + 1
                    if new != old:
                        counts["changed"] += 1
                        assert len(new["summary_line"]) <= response_guide.STATUS_LINE_MAX
                        action = new["next_action"]
                        assert action["do"] in ("use_official_path", "hand_off_to_human", "no_clear_action") or kind_name in ("failed_status_on", "unchanged_status", "failed_unverified")
                        assert action.get("url") in (None, action.get("status_url")) or kind_name in ("failed_status_on", "unchanged_status", "failed_unverified")
                        # a developer page is never the url of a changed answer that D96 made
                        if kind_name in ("self_failed", "widened", "dev_path_dropped"):
                            official = case.get("official") or {}
                            assert action["url"] not in (official.get("api_docs"), official.get("developer_portal")) or action["url"] is None
    print("D96 comparison", counts, kinds)
    assert counts["pairs"] > 20000 and counts["changed"] > 2000
    assert set(kinds) == {"self_failed", "widened", "unchanged_status", "failed_status_on", "failed_unchanged", "dev_path_dropped",
                          "failed_unverified"}, kinds


# --- the shapes of the lookups ---------------------------------------------------------------------------------------------

REPLIT = {"name": "Replit", "api_docs": "https://docs.replit.com/", "developer_portal": None, "note": None}
OPENAI = {"name": "OpenAI", "api_docs": "https://platform.openai.com/docs", "developer_portal": None, "note": None}
SLACK = {"name": "Slack", "api_docs": "https://api.slack.com/", "developer_portal": None, "note": None}


def lookup(host, *, provider=None, status_page=None, official=None, profile=ALLOWS, error_class=None, stage=None, task_step="status_check",
           one_action=True):
    case = {"error_class": error_class, "provider_status": provider, "access_profile": profile, "official": official}
    rec = recommend(case, stage, True)
    result = build(case, rec, site_host=host, official_page=status_page)
    applies = status_check_applies(task_step, True, one_action, error_class, stage)
    out = guide(copy.deepcopy(result), True, False, applies, True, error_class, stage, True, one_action)
    return rec, out


def pair(host, **kw):
    """(before D96, with D96) for the same lookup."""
    return lookup(host, **kw, one_action=False)[1], lookup(host, **kw, one_action=True)[1]


def test_status_page_host_that_failed_after_a_response_goes_to_the_user_and_no_developer_document_is_named():
    kw = dict(provider=fresh_none("https://status.replit.com/"), official=REPLIT, stage="response_observed")
    rec, new = lookup("status.replit.com", **kw)
    old = pair("status.replit.com", **kw)[0]
    assert rec["action"] == "use_official_path"                                            # the recommendation is not changed
    assert old["next_action"]["do"] == "use_official_path" and old["next_action"]["url"] == "https://docs.replit.com/"
    # D111: the status page is the host that failed, so status_url does not send the caller back to it
    assert new["next_action"] == {"do": "hand_off_to_human", "url": None, "retry_after_seconds": None}
    assert new["summary_line"] == STATUS_ROWS["T1-F"]
    assert "docs.replit.com" not in json.dumps({"next_action": new["next_action"], "summary_line": new["summary_line"]})


def test_the_same_host_with_no_failure_signal_gets_the_status_page_as_its_one_action():
    kw = dict(provider=fresh_none("https://status.replit.com/"), official=REPLIT)
    old, new = pair("status.replit.com", **kw)
    assert new["next_action"]["do"] == "use_official_path" and new["next_action"]["url"] == "https://status.replit.com/"
    assert new["summary_line"] == STATUS_ROWS["T1"] == old["summary_line"]
    assert old["next_action"]["url"] is None or old["next_action"]["url"] == new["next_action"]["url"]   # before: proceed with no url


def test_openai_com_status_check_has_the_status_page_as_url():
    kw = dict(provider=fresh_none("https://status.openai.com/"), official=OPENAI)
    old, new = pair("openai.com", **kw)
    assert old["next_action"]["do"] == "proceed" and old["next_action"]["url"] is None
    assert new["next_action"]["do"] == "use_official_path" and new["next_action"]["url"] == "https://status.openai.com/"
    assert new["summary_line"] == STATUS_ROWS["T1"]
    failed_old, failed_new = pair("openai.com", **kw, error_class="service_error", stage="response_observed")
    assert failed_old["next_action"]["url"] == "https://platform.openai.com/docs" and failed_old["summary_line"] not in STATUS_ROWS.values()
    assert failed_new["next_action"]["url"] == "https://status.openai.com/" and failed_new["summary_line"] == STATUS_ROWS["T1"]


def test_no_feed_and_only_a_status_url_still_gives_the_status_page_as_the_action():
    kw = dict(status_page={"source_name": "Slack Status", "source_url": "https://slack-status.com/"}, official=SLACK)
    old, new = pair("slack.com", **kw)
    assert old["next_action"]["do"] == "proceed" and old["next_action"]["url"] is None
    assert new["next_action"]["do"] == "use_official_path" and new["next_action"]["url"] == "https://slack-status.com/"
    assert new["summary_line"] == STATUS_ROWS["T3"] and new["next_action"]["status_url"] == "https://slack-status.com/"


@pytest.mark.parametrize("provider,status_page,row", [
    (fresh_none("https://status.example.com/"), None, "T1-F"),
    ({**fresh_none("https://status.example.com/"), "indicator": None}, None, "T2-F"),
    ({**fresh_none("https://status.example.com/"), "freshness": "stale"}, None, "T5-F"),
    (None, {"source_name": "S", "source_url": "https://status.example.com/"}, "T3-F"),
])
def test_the_status_page_itself_failed_one_sentence_for_each_reading(provider, status_page, row):
    for stage in ("response_observed", "before_target_response"):
        _, new = lookup("status.example.com", provider=provider, status_page=status_page, official=REPLIT, stage=stage, error_class="service_error")
        assert new["summary_line"] == STATUS_ROWS[row]
        assert new["next_action"]["do"] == "hand_off_to_human" and new["next_action"]["url"] is None
        assert "status_url" not in new["next_action"]  # D111: the status page is the host that failed


def test_a_failure_elsewhere_than_the_status_page_is_not_a_status_page_failure():
    # the feed's host is another host than the one that failed: the status page is the way to check, not the thing that broke
    _, new = lookup("app.example.com", provider=fresh_none("https://status.example.com/"), official=REPLIT, stage="response_observed")
    assert new["next_action"]["url"] == "https://status.example.com/" and new["next_action"]["do"] == "use_official_path"


def test_a_status_page_failure_without_a_failure_signal_cannot_happen_and_points_at_the_page():
    _, new = lookup("status.example.com", provider=fresh_none("https://status.example.com/"), official=REPLIT)
    assert new["next_action"]["do"] == "use_official_path" and new["next_action"]["url"] == "https://status.example.com/"


def test_a_service_error_on_a_host_whose_status_page_covers_only_its_api_goes_to_that_page_and_beats_d95():
    api_page = {"source_name": "DHL API Status", "source_url": "https://status.api.dhl.com/", "covers": "api"}
    case = {"error_class": "service_error", "access_profile": ALLOWS, "official": REPLIT}
    rec = triage.recommendation("service_error", None, ALLOWS, REPLIT, now=NOW, failure_stage="before_target_response",
                                service_error_reference_only=True)
    result = build(case, rec, site_host="dhl.com", official_page=api_page)
    out = guide(copy.deepcopy(result), True, False, True, True, "service_error", "before_target_response", True, True)
    assert rec["action"] == "proceed"                                                                  # D95: robots clear gives proceed
    assert out["next_action"]["do"] == "use_official_path" and out["next_action"]["url"] == "https://status.api.dhl.com/"
    assert out["summary_line"] == STATUS_ROWS["T3-A"]
    before = guide(copy.deepcopy(result), True, False, False, True, "service_error", "before_target_response", True, False)
    assert before["summary_line"] == response_guide.NO_RESPONSE_ROWS["P1"] and before["next_action"]["url"] is None


@pytest.mark.parametrize("error_class, stage", [(None, "response_observed"), ("site_access", "response_observed"),
                                                 ("service_error", "response_observed"), ("service_error", "before_target_response")])
def test_a_failed_status_check_with_no_status_page_and_a_developer_path_names_no_developer_path_and_says_unverified(error_class, stage):
    old, new = pair("example.com", official=REPLIT, profile=None, error_class=error_class, stage=stage)
    assert old["next_action"]["url"] == "https://docs.replit.com/"          # the answer before D96 sent the caller to the developer document
    action = new["next_action"]
    assert (action["do"], action["url"]) == ("hand_off_to_human", None)
    assert new["summary_line"] == STATUS_ROWS["T4-R"] and "check the site itself" not in new["summary_line"].lower()
    assert "docs.replit.com" not in json.dumps({"summary_line": new["summary_line"], "next_action": action})


def test_a_status_check_with_no_failure_no_status_page_and_a_developer_path_has_no_url_and_no_action():
    old, new = pair("example.com", official=REPLIT, profile=None)
    assert old["next_action"]["url"] == "https://docs.replit.com/"
    assert (new["next_action"]["do"], new["next_action"]["url"]) == ("no_clear_action", None)
    assert new["summary_line"] == STATUS_ROWS["T4"]


def test_a_failed_proceed_row_is_not_told_to_check_the_site_itself():
    old, new = pair("example.com", stage="response_observed")
    assert new["next_action"]["do"] == "proceed" and new["next_action"]["url"] is None
    assert new["summary_line"] == STATUS_ROWS["T4-R"]
    # without a failure the sentence is the one it always was
    assert pair("example.com")[1]["summary_line"] == STATUS_ROWS["T4"]


def test_a_lookup_that_is_not_a_status_check_is_as_before():
    kw = dict(provider=fresh_none("https://status.replit.com/"), official=REPLIT, stage="response_observed")
    assert lookup("status.replit.com", **kw, task_step=None)[1] == lookup("status.replit.com", **kw, task_step=None, one_action=False)[1]
    assert lookup("status.replit.com", **kw, task_step="preflight")[1] == lookup("status.replit.com", **kw, task_step=None, one_action=False)[1]
    out = lookup("status.replit.com", **kw, task_step="login")[1]
    assert out["next_action"]["url"] == "https://docs.replit.com/" and out["summary_line"] not in STATUS_ROWS.values()


@pytest.mark.parametrize("error_class", ["human_verification", "login_auth"])
def test_a_human_verification_or_sign_in_failure_is_handed_over_as_before(error_class):
    kw = dict(provider=fresh_none("https://status.example.com/"), official=REPLIT, error_class=error_class, stage="response_observed")
    old, new = pair("status.example.com", **kw)
    assert new == old and new["next_action"]["do"] == "hand_off_to_human" and new["summary_line"] not in STATUS_ROWS.values()


def test_a_wait_a_stop_and_a_re_lookup_keep_their_action():
    wait = {**fresh_none("https://status.example.com/"), "indicator": "major", "freshness": "fresh", "basis": "incident",
            "active_incidents": 1, "max_incident_impact": "major", "incident_updated_at": NOW}
    _, out = lookup("example.com", provider=wait, official=REPLIT, error_class="service_error", stage="response_observed")
    assert out["next_action"]["do"] == "wait_for_provider"
    pending = {"robots_status": "pending", "robots": None, "freshness": "pending", "llms_txt_status": "pending", "retry_after_seconds": 5}
    old, new = pair("example.com", provider=fresh_none("https://status.example.com/"), official=REPLIT, profile=pending)
    assert new == old and new["next_action"]["retry_after_seconds"] == 5 and new["next_action"]["url"] != "https://status.example.com/"


def test_a_status_page_that_needs_sign_in_is_never_the_url():
    page_in = {"source_name": "S", "source_url": OTHER_PAGE, "requires_sign_in": True}
    old, new = pair("example.com", status_page=page_in, official=None)
    assert new["summary_line"] == STATUS_ROWS["T3-S"] and new["next_action"]["do"] == "proceed" and new["next_action"]["url"] is None
    assert new["next_action"]["status_url"] == OTHER_PAGE and new == old


def test_a_subdomain_with_a_status_page_of_its_own_answers_with_that_page_not_with_the_apex_lookup():
    rec = triage.recommendation(None, fresh_none("https://status.example.com/"), None, None, profile_not_read=True, now=NOW)
    result = build({"provider_status": fresh_none("https://status.example.com/"), "official": None}, rec, site_host="app.example.com")
    old = guide(copy.deepcopy(result), True, False, True, True)
    new = guide(copy.deepcopy(result), True, False, True, True, None, None, True, True)
    assert old["next_action"]["do"] == "lookup_apex_host"
    assert new["next_action"]["do"] == "use_official_path" and new["next_action"]["url"] == "https://status.example.com/"
    assert "lookup_host" not in new["next_action"] and new["summary_line"] == old["summary_line"]


# --- sentences, switch, documents ---------------------------------------------------------------------------------------------

def test_the_new_sentences_are_short_plain_and_carry_no_host_or_url():
    new = {row: STATUS_ROWS[row] for row in ("T1-F", "T2-F", "T3-F", "T5-F")}
    assert set(STATUS_FAILED_ROW.values()) == set(new) | {"T6-F"}   # D100 added T6-F (tests/test_d100_wording.py)
    for row, text in new.items():
        assert len(text) <= LINE_MAX and re.fullmatch(r"[\x20-\x7E]+", text) and "{" not in text and "http" not in text, row
        assert "status page did not open for you" in text.replace("\n", " ") or "status page did not open" in text
    assert new["T1-F"] == ("The provider's status feed reports no active issue (provider_status, see its fetched_at). "
                           "That status page did not open for you; give your user this reading, not a guess.")
    assert {k: v for k, v in STATUS_ROWS.items() if k not in new and k not in ("T6", "T1-U", "T6-F")} == pre_rg.STATUS_ROWS
    assert {k: v for k, v in ROWS.items() if k not in ("H3", "H3-R") and k not in response_guide.HANDOFF_ROWS} == {k: v for k, v in pre_rg.ROWS.items() if k != "H3"}                                                          # no row of the main table moved


def test_the_switch_parses_like_its_siblings(monkeypatch):
    monkeypatch.setattr(settings_module, "_D96_WARNED", False)
    assert parse_d96_switch("") and parse_d96_switch("1") and not parse_d96_switch("0")
    assert not parse_d96_switch("yes")  # an unknown value is off, never on
    monkeypatch.delenv("AHN_D96_STATUS_CHECK_ONE_ACTION", raising=False)
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://x/y")
    monkeypatch.setenv("AHN_KEY_PEPPER", "p" * 40)
    assert Settings.from_env().d96_status_check_one_action is True
    monkeypatch.setenv("AHN_D96_STATUS_CHECK_ONE_ACTION", "0")
    assert Settings.from_env().d96_status_check_one_action is False


def test_documents_name_the_rule():
    for name in ("api-contract.en.md", "api-contract.md"):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert "D96" in text and "AHN_D96_STATUS_CHECK_ONE_ACTION" in text, name
    assert "| D96 " in (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
