"""D92: a failed lookup that reports no response at all (error_class=service_error, failure_stage=before_target_response) no longer says "5xx".

Only the sentence changes. The guide before D92 is kept as tests/_pre_d92_response_guide.py; the comparison walks the D85/D87 input space and asserts
both directions: no pair outside the condition differs, and every pair inside it differs in summary_line alone. The 503 sentences of D87 stay."""

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

from test_d85_edge_refusal import ABSENT, ALLOWS, FULL, GUIDE_MODES, STAGES, rec_of, result_of, variants  # noqa: E402
from test_response_guide import block, robots  # noqa: E402
import _d97_scope  # noqa: E402

from ahn import response_guide, triage  # noqa: E402
from ahn.response_guide import (FAILED_U3_NO_RESPONSE, FAILED_U3_SERVICE_ERROR, LINE_MAX, NO_RESPONSE_CONSUMER_ROWS,  # noqa: E402
                                NO_RESPONSE_ROWS, SERVICE_ERROR_CONSUMER_ROWS, SERVICE_ERROR_ROWS, guide)
from ahn.triage import failure_observed  # noqa: E402

_spec = importlib.util.spec_from_file_location("pre_d92_response_guide", HERE / "_pre_d92_response_guide.py")
pre_rg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pre_rg)
# D93 changed only these sentences since the snapshot; take them as they are now so the comparison keeps covering everything else
pre_rg.ROWS.update({row: response_guide.ROWS[row] for row in ("O1", "O7", "O7-N", "O7-K", "U8") if row in pre_rg.ROWS})

U3_PROFILE = robots("User-agent: *\nAllow: /\n\nUser-agent: Claude-User\nDisallow: /\n")
P2_PROFILE = robots(block("Claude-SearchBot"))
MODE = (True, False, False, True)


@pytest.fixture(autouse=True)
def _stand_in_hosts_are_not_reserved(monkeypatch):
    monkeypatch.setattr(response_guide, "is_reserved_example", lambda host: False)
    monkeypatch.setattr(pre_rg, "is_reserved_example", lambda host: False)


def before(result, mode, error_class, stage, kind=True):
    return pre_rg.guide(copy.deepcopy(result), *mode, error_class, stage, kind)


def test_only_service_error_with_no_response_changes_and_only_the_summary_line():
    changed = pairs = 0
    for case in variants(1 if FULL else 29):
        error_class = case.get("error_class")
        for stage, pwf in itertools.product(STAGES, (True, False)):
            rec = rec_of(triage, case, stage, pwf)
            result = result_of(case, rec)
            for mode in GUIDE_MODES:
                on, _blocked, status_check, _sg = mode
                if status_check and failure_observed(error_class, stage):
                    continue
                old = before(result, mode, error_class, stage)
                new = guide(copy.deepcopy(result), *mode, error_class, stage, True)
                off = guide(copy.deepcopy(result), *mode, error_class, stage, False)
                pairs += 1
                assert off == before(result, mode, error_class, stage, False), (case, stage, mode)  # switch 0: byte for byte as before
                row, _ = pre_rg.select_row(rec, result["access_profile"], result["provider_status"], result["site_host"],
                                           result["official_paths"], variants=on)
                inside = bool(on and not status_check and error_class == "service_error" and stage == "before_target_response"
                              and (row.startswith("P") or row == "U3"))
                if not inside:
                    # D97 later changed U6/U7/U9 after a service_error (and with a curated customer path); nothing else differs
                    assert new == _d97_scope.adjust(old, row, on, status_check, error_class, stage, True, result.get("official_paths")), (case, stage, mode)
                    continue
                changed += 1
                assert new["summary_line"] != old["summary_line"] and "5xx" not in new["summary_line"]
                assert {k: v for k, v in new.items() if k != "summary_line"} == {k: v for k, v in old.items() if k != "summary_line"}
    print("D92 comparison", pairs, changed)
    assert pairs > 20000 and changed > 100


def answer(profile, *, error_class, stage=None, vendor=None, official=None):
    case = {"access_profile": profile, "official": official, "vendor": vendor, "error_class": error_class}
    return result_of(case, rec_of(triage, case, stage, True))


def lookup(result, error_class, stage=None, *, kind=True):
    return guide(copy.deepcopy(result), *MODE[:3], True, error_class, stage, kind)


@pytest.mark.parametrize("profile,vendor,row", [(ALLOWS, None, "P1"), (ALLOWS, "anthropic", "P1-V"), (P2_PROFILE, None, "P2"),
                                                (P2_PROFILE, "anthropic", "P2-V"), (ABSENT, None, "P3")])
def test_proceed_rows_say_no_response_came_and_a_503_keeps_its_d87_sentence(profile, vendor, row):
    result = answer(profile, error_class="service_error", vendor=vendor)
    timeout = lookup(result, "service_error", "before_target_response")
    seen = lookup(result, "service_error", "response_observed")
    assert timeout["summary_line"] == NO_RESPONSE_ROWS[row] and "5xx" not in timeout["summary_line"]
    assert seen["summary_line"] == SERVICE_ERROR_ROWS[row] == pre_rg.SERVICE_ERROR_ROWS[row]  # 503 regression
    assert lookup(result, "service_error")["summary_line"] == SERVICE_ERROR_ROWS[row]  # no stage: unchanged
    assert timeout["next_action"] == seen["next_action"] and timeout["next_action"]["do"] == "proceed"


def test_a_stage_without_error_class_keeps_its_sentence():
    result = answer(ALLOWS, error_class=None, stage="before_target_response")
    assert lookup(result, None, "before_target_response") == before(result, MODE, None, "before_target_response")


def test_customer_path_variant():
    help_url = "https://www.example.com/help"
    curated = {"name": "Example", "api_docs": None, "developer_portal": None, "note": None,
               "consumer": {"paths": [{"kind": "customer_help", "url": help_url, "source_url": "https://www.example.com/"}],
                            "verified_at": "2026-10-31"}}
    result = answer(ALLOWS, error_class="service_error", official=curated)
    line = lookup(result, "service_error", "before_target_response")
    assert line["summary_line"] == NO_RESPONSE_CONSUMER_ROWS["P1"] and line["next_action"]["customer_url"] == help_url
    assert lookup(result, "service_error", "response_observed")["summary_line"] == SERVICE_ERROR_CONSUMER_ROWS["P1"]


def test_u3_variant():
    result = answer(U3_PROFILE, error_class="service_error")
    assert lookup(result, "service_error", "before_target_response")["summary_line"] == FAILED_U3_NO_RESPONSE
    assert lookup(result, "service_error", "response_observed")["summary_line"] == FAILED_U3_SERVICE_ERROR


def test_sentences_are_short_and_say_no_5xx():
    texts = [*NO_RESPONSE_ROWS.values(), *NO_RESPONSE_CONSUMER_ROWS.values(), FAILED_U3_NO_RESPONSE]
    for text in texts:
        assert len(text) <= LINE_MAX and re.fullmatch(r"[\x20-\x7E]+", text) and "{" not in text and "http" not in text, text
        assert "5xx" not in text and "no response came" in text.lower(), text
    assert all("next_action.customer_url" in t for t in NO_RESPONSE_CONSUMER_ROWS.values())
    assert not any("customer_url" in t for t in NO_RESPONSE_ROWS.values())
    assert "robots.agents_blocked" in NO_RESPONSE_ROWS["P2"]
    assert {k: v for k, v in response_guide.ROWS.items() if k not in ("U7-O", "U9-O", "H3", "H3-R", *response_guide.HANDOFF_ROWS)} == {k: v for k, v in pre_rg.ROWS.items() if k != "H3"} and response_guide.SERVICE_ERROR_ROWS == pre_rg.SERVICE_ERROR_ROWS


def test_documents_name_the_rule():
    for name in ("api-contract.en.md", "api-contract.md"):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert "D92" in text and "NO_RESPONSE_ROWS" in text, name
    assert "| D92 " in (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
