"""D97: a failed lookup on a host that lists no developer path (U6, U7, U9) is told what to do, not only what robots.txt said.

Only the sentence changes (and next_action.customer_url is added on these rows when a customer path is curated); ``do``, ``url``,
``retry_after_seconds`` and every other key are those of the row. The same switch as D87 (AHN_FAILURE_KIND_LINES) turns it off.

The guide from before D97 is kept as tests/_pre_d97_response_guide.py. The comparison walks the D85/D87 input space (each case with and
without a curated customer path) and asserts both directions: no pair outside the D97 condition differs, every pair inside it differs in
summary_line (and the one added customer_url) alone, and the switch off gives the old answer for every pair."""

from __future__ import annotations

import copy
import importlib.util
import itertools
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _d97_scope  # noqa: E402
from test_d85_edge_refusal import ALLOWS, CONSUMER, FULL, GUIDE_MODES, STAGES, rec_of, result_of, variants  # noqa: E402

from ahn import response_guide, triage  # noqa: E402
from ahn.response_guide import LINE_MAX, STATUS_LINE_MAX, guide  # noqa: E402
from ahn.triage import failure_observed  # noqa: E402

_spec = importlib.util.spec_from_file_location("pre_d97_response_guide", HERE / "_pre_d97_response_guide.py")
pre_rg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pre_rg)

HELP = "https://www.example.com/help"
ERROR_PROFILE = {"robots_status": "error", "robots": None, "freshness": "pending", "llms_txt_status": "absent"}
REFUSED_PROFILE = {"robots_status": "refused", "robots": None, "freshness": "fresh", "llms_txt_status": "absent"}
CUSTOMER_ONLY = {"name": "Example", "api_docs": None, "developer_portal": None, "note": None, "consumer": CONSUMER}
DEV_AND_CUSTOMER = {**CUSTOMER_ONLY, "api_docs": "https://developer.example.com/docs"}
# the lookup shapes of the three rows: (access profile, site is looked up as a plain host with no policy read)
SHAPES = {"U6": (REFUSED_PROFILE, False), "U7": (ERROR_PROFILE, False), "U9": (None, True)}
NO_RESPONSE = "before_target_response"


@pytest.fixture(autouse=True)
def _stand_in_hosts_are_not_reserved(monkeypatch):
    monkeypatch.setattr(response_guide, "is_reserved_example", lambda host: False)
    monkeypatch.setattr(pre_rg, "is_reserved_example", lambda host: False)


# --- the whole grid ------------------------------------------------------------------------------------------------------

def test_the_pairs_that_change_are_exactly_the_d97_condition_in_the_whole_grid():
    counts = dict(pairs=0, changed=0, customer_added=0)
    rows_seen = set()
    for case in variants(1 if FULL else 29):
        error_class = case.get("error_class")
        for stage, pwf in itertools.product(STAGES, (True, False)):
            rec = rec_of(triage, case, stage, pwf)
            result = result_of(case, rec)
            for mode in GUIDE_MODES:
                on, _blocked, status_check, _sg = mode
                if status_check and failure_observed(error_class, stage):
                    continue
                old = pre_rg.guide(copy.deepcopy(result), *mode, error_class, stage, True)
                new = guide(copy.deepcopy(result), *mode, error_class, stage, True)
                off = guide(copy.deepcopy(result), *mode, error_class, stage, False)
                counts["pairs"] += 1
                assert off == pre_rg.guide(copy.deepcopy(result), *mode, error_class, stage, False), (case, stage, mode)  # switch 0: no difference
                row, _ = pre_rg.select_row(rec, result["access_profile"], result["provider_status"], result["site_host"],
                                           result["official_paths"], variants=on)
                signal = error_class is not None or stage is not None
                curated = bool(response_guide.customer_url(result.get("official_paths")))
                inside = bool(on and not status_check and row in ("U6", "U7", "U9")
                              and ((error_class == "service_error") or (not signal and curated)))
                if not inside:
                    assert new == old, (case, stage, mode, row)
                    continue
                counts["changed"] += 1
                rows_seen.add(row)
                assert new["summary_line"] != old["summary_line"] and len(new["summary_line"]) <= LINE_MAX
                # the action is the row's: do, url and the retry value never move; the only added key is customer_url
                for key in ("do", "url", "retry_after_seconds"):
                    assert new["next_action"][key] == old["next_action"][key], (key, case, stage, mode)
                assert new["next_action"]["do"] == "no_clear_action" and new["next_action"]["url"] is None
                extra = set(new["next_action"]) - set(old["next_action"])
                assert extra == ({"customer_url"} if curated else set()), (case, stage, mode)
                counts["customer_added"] += int(bool(extra))
                assert {k: v for k, v in new.items() if k not in ("summary_line", "next_action")} == \
                    {k: v for k, v in old.items() if k not in ("summary_line", "next_action")}
                assert new == _d97_scope.adjust(old, row, on, status_check, error_class, stage, True, result.get("official_paths"))
    print("D97 comparison", counts, sorted(rows_seen))
    assert counts["pairs"] > 20000 and counts["changed"] > 100 and counts["customer_added"] > 20
    assert rows_seen == {"U6", "U7", "U9"}


# --- the shapes of the lookups ---------------------------------------------------------------------------------------------

def lookup(row, *, error_class="service_error", stage=None, official=None, kind=True, host=None, module=guide, expect_row=True):
    profile, not_read = SHAPES[row]
    host = host or ("airline-example.com" if row == "U9" else "www.airline-example.com")  # U9 is a host with no policy read of its own and no root to look up
    case = {"error_class": error_class, "access_profile": profile, "official": official, "not_read": not_read, "site_host": host}
    result = result_of(case, rec_of(triage, case, stage, True))
    if expect_row:
        assert response_guide.select_row(result["recommendation"], profile, None, host, official, variants=True)[0] == row
    return module(copy.deepcopy(result), True, False, False, True, error_class, stage, kind)


SHAPES_OF_LINES = (
    ("U7", "No response came (timeout or connection), and AHN could not read the site's robots.txt either. Check your tool and path; retry once "
           "later; then tell your user.", "AHN cannot judge a single page's 5xx, and could not read the site's robots.txt. Retry once later; if it "
           "persists, tell your user."),
    ("U9", "No response came (timeout or connection), and AHN has no declared policy for this host. Check your tool and path; retry once later; "
           "then tell your user.", "AHN cannot judge a single page's 5xx, and has no declared policy for this host. Retry once later; if it "
           "persists, tell your user."),
    ("U6", "No response came (timeout or connection), and the site refused AHN's plain robots.txt read too. Check your tool and path; retry once "
           "later; then tell your user.", "AHN cannot judge a single page's 5xx; the site also refused AHN's plain robots.txt read. Retry once "
           "later; if it persists, tell your user."),
)


@pytest.mark.parametrize("row, no_response, five_xx", SHAPES_OF_LINES)
def test_a_host_with_no_listed_path_gets_the_failure_line_after_no_response_and_after_a_5xx(row, no_response, five_xx):
    timeout, seen = lookup(row, stage=NO_RESPONSE), lookup(row, stage="response_observed")
    assert timeout["summary_line"] == no_response and seen["summary_line"] == five_xx
    assert lookup(row, stage=None)["summary_line"] == five_xx  # a service_error with no stage is read as a 5xx, like D87
    for out in (timeout, seen):
        assert out["next_action"] == {"do": "no_clear_action", "url": None, "retry_after_seconds": None}
    # the line before D97 only told the robots story and ended in "no official path is listed" / "follow your operator's rules"
    old = lookup(row, stage=NO_RESPONSE, module=pre_rg.guide)
    assert old["summary_line"] == response_guide.ROWS[row][1] and "No response" not in old["summary_line"]
    assert old["next_action"] == timeout["next_action"]


def test_the_no_response_shape_is_told_to_check_the_tool_then_retry_once_then_tell_the_user():
    out = lookup("U7", stage=NO_RESPONSE)
    line = out["summary_line"].lower()
    assert line.index("check your tool") < line.index("retry once later") < line.index("tell your user")
    assert "robots.txt" in line and "no official path" not in line


@pytest.mark.parametrize("row", ["U6", "U7", "U9"])
def test_a_host_with_a_customer_path_gets_the_same_line_with_the_customer_page_and_no_url(row):
    timeout = lookup(row, stage=NO_RESPONSE, official=CUSTOMER_ONLY)
    seen = lookup(row, stage="response_observed", official=CUSTOMER_ONLY)
    assert timeout["summary_line"] == _d97_scope.no_path_sentence(row, "service_error", NO_RESPONSE, HELP)
    assert seen["summary_line"] == _d97_scope.no_path_sentence(row, "service_error", "response_observed", HELP)
    for out in (timeout, seen):
        assert out["next_action"] == {"do": "no_clear_action", "url": None, "retry_after_seconds": None, "customer_url": HELP}
        assert out["summary_line"].endswith("hand your user next_action.customer_url.") or out["summary_line"].endswith("hand them next_action.customer_url.")
        assert len(out["summary_line"]) <= LINE_MAX
    assert "retry once later" in timeout["summary_line"].lower() and "Retry once later" in seen["summary_line"]
    assert "customer_url" not in lookup(row, stage=NO_RESPONSE)["summary_line"]


@pytest.mark.parametrize("row", ["U6", "U7", "U9"])
def test_a_failure_that_is_not_a_service_error_is_not_changed(row):
    for error_class, stage in (("site_access", "access_denial_observed"), ("human_verification", "response_observed"), ("login_auth", None),
                               (None, NO_RESPONSE), (None, "response_observed")):
        for official in (None, CUSTOMER_ONLY):
            # a refusal or a sign-in wall lands on another row (hand-off) on these profiles; where it stays on the row, D97 leaves it alone
            kw = dict(error_class=error_class, stage=stage, official=official, expect_row=False)
            assert lookup(row, **kw) == lookup(row, **kw, module=pre_rg.guide), (row, error_class, stage, official)


@pytest.mark.parametrize("row", ["U6", "U7", "U9"])
def test_no_failure_signal_and_no_customer_path_is_byte_for_byte_as_before(row):
    assert lookup(row, error_class=None, stage=None) == lookup(row, error_class=None, stage=None, module=pre_rg.guide)
    # a developer-only host (no customer path) keeps U7-O / U9-O / U6 untouched
    assert lookup(row, error_class=None, stage=None, official={**CUSTOMER_ONLY, "consumer": None}) == \
        lookup(row, error_class=None, stage=None, official={**CUSTOMER_ONLY, "consumer": None}, module=pre_rg.guide)


@pytest.mark.parametrize("row", ["U6", "U7", "U9"])
def test_no_failure_signal_with_a_customer_path_names_the_page_in_place_of_no_official_path(row):
    out = lookup(row, error_class=None, stage=None, official=CUSTOMER_ONLY)
    old = lookup(row, error_class=None, stage=None, official=CUSTOMER_ONLY, module=pre_rg.guide)
    assert "A customer page is listed (next_action.customer_url) for tasks a person does on the site." in out["summary_line"]
    assert "no official path is listed" not in out["summary_line"].lower() and len(out["summary_line"]) <= LINE_MAX
    assert out["next_action"] == {**old["next_action"], "customer_url": HELP} and out["next_action"]["url"] is None
    assert out["next_action"]["do"] == old["next_action"]["do"] == "no_clear_action"


@pytest.mark.parametrize("row", ["U6", "U7", "U9"])
def test_the_switch_off_gives_the_answer_before_d97_for_every_lookup(row):
    for official in (None, CUSTOMER_ONLY):
        for error_class, stage in (("service_error", NO_RESPONSE), ("service_error", "response_observed"), ("service_error", None), (None, None)):
            assert lookup(row, error_class=error_class, stage=stage, official=official, kind=False) == \
                lookup(row, error_class=error_class, stage=stage, official=official, kind=False, module=pre_rg.guide)


def test_a_host_with_a_developer_path_keeps_its_d95_rows():
    # U7-O (a developer path is listed and robots.txt is unread) is D95's row, not D97's
    case = {"error_class": "service_error", "access_profile": ERROR_PROFILE, "official": {"name": "E", "api_docs": None,
            "developer_portal": "https://developer.example.com/", "note": None}}
    rec = triage.recommendation("service_error", None, ERROR_PROFILE, case["official"], failure_stage=NO_RESPONSE, proceed_without_failure=True,
                                service_error_reference_only=True)
    assert rec["reasons"] == ["robots_unknown", "official_path_available"]
    result = result_of(case, rec)
    out = guide(copy.deepcopy(result), True, False, False, True, "service_error", NO_RESPONSE, True)
    assert out["summary_line"] == response_guide.U7_O_NO_RESPONSE


# --- a status_check answer is not touched (D96 T4-R) ------------------------------------------------------------------------

def status_lookup(*, error_class, stage, official=None, kind=True, profile=ALLOWS, module=response_guide, host="example.com"):
    case = {"error_class": error_class, "access_profile": profile, "official": official, "not_read": profile is None}
    result = result_of(case, rec_of(triage, case, stage, True))
    result["site_host"] = host
    applies = response_guide.status_check_applies("status_check", True, True, error_class, stage)
    return module.guide(copy.deepcopy(result), True, False, applies, True, error_class, stage, kind, True)


@pytest.mark.parametrize("error_class, stage", [("service_error", "response_observed"), ("service_error", NO_RESPONSE),
                                                ("site_access", "response_observed"), (None, "response_observed"), (None, None)])
@pytest.mark.parametrize("official", [None, DEV_AND_CUSTOMER])
def test_a_status_check_answer_with_no_status_page_is_byte_identical_to_the_one_before_d97(error_class, stage, official):
    # the replit shape (status_check, service_error, no status page, a developer path only) among them: D97 does not change status_check rows
    assert status_lookup(error_class=error_class, stage=stage, official=official) ==         status_lookup(error_class=error_class, stage=stage, official=official, module=pre_rg)
    assert {k: v for k, v in response_guide.STATUS_ROWS.items() if k not in ("T6", "T1-U", "T6-F")} == pre_rg.STATUS_ROWS
    assert not hasattr(response_guide, "STATUS_FAILED_VERIFY_ROWS")


# --- the sentences and the documents ---------------------------------------------------------------------------------------

def test_the_new_sentences_are_short_plain_and_carry_no_host_or_url():
    texts = [*response_guide.NO_PATH_NO_RESPONSE_ROWS.values(), *response_guide.NO_PATH_NO_RESPONSE_CONSUMER_ROWS.values(),
             *response_guide.NO_PATH_SERVICE_ERROR_ROWS.values(), *response_guide.NO_PATH_SERVICE_ERROR_CONSUMER_ROWS.values(),
             *response_guide.NO_PATH_CONSUMER_ROWS.values()]
    assert len(texts) == 3 * 5
    for text in texts:
        assert len(text) <= LINE_MAX and re.fullmatch(r"[\x20-\x7E]+", text) and "{" not in text and "http" not in text, text
        assert not any(word in text.lower() for word in ("bypass", "evade", "spoof", "proxy", "work around", "get past")), text
    assert response_guide.NO_PATH_NO_RESPONSE_ROWS["U7"] == response_guide.U7_O_NO_RESPONSE  # the D95 constant, as the contract names it
    assert all("customer_url" in t for t in (*response_guide.NO_PATH_NO_RESPONSE_CONSUMER_ROWS.values(), *response_guide.NO_PATH_CONSUMER_ROWS.values()))
    assert not any("customer_url" in t for t in (*response_guide.NO_PATH_NO_RESPONSE_ROWS.values(), *response_guide.NO_PATH_SERVICE_ERROR_ROWS.values()))
    assert STATUS_LINE_MAX > LINE_MAX


def test_no_row_and_no_status_sentence_of_the_table_changed():
    assert {k: v for k, v in response_guide.ROWS.items() if k not in ("H3", "H3-R") and k not in response_guide.HANDOFF_ROWS} == {k: v for k, v in pre_rg.ROWS.items() if k != "H3"}
    assert {k: v for k, v in response_guide.STATUS_ROWS.items() if k not in ("T6", "T1-U", "T6-F")} == pre_rg.STATUS_ROWS
    assert response_guide.NO_PATH_ROWS == ("U6", "U7", "U9") and set(response_guide.NO_PATH_ROWS) <= set(response_guide.ROWS)
    assert all(response_guide.ROWS[row][0] == "no_clear_action" for row in response_guide.NO_PATH_ROWS)


def test_the_new_rule_has_no_switch_of_its_own():
    from ahn.settings import Settings
    assert not [name for name in Settings.__dataclass_fields__ if "d97" in name]


def test_documents_name_the_rule():
    for name in ("api-contract.en.md", "api-contract.md"):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert "D97" in text and "NO_PATH" in text and "AHN_FAILURE_KIND_LINES" in text, name
    assert "| D97 " in (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
