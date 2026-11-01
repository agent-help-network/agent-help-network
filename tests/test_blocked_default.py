"""D66: AHN_PROCEED_WITHOUT_FAILURE=d65 answers a lookup that reports no failure like one that reported site_access.

Pure: no database. The service wiring (task_step words, the observation filter, the search page and the home card) is in
test_blocked_default_db.py."""

from __future__ import annotations

import itertools
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_d63_decision_order import (ABSENT, ALLOWS, CONSUMER_ONLY, ERROR_CLASSES, NOW, OFFICIAL, OFFICIAL_K,  # noqa: E402
                                     OFFICIAL_N, PROFILES, WITH_CONSUMER, row_of)

from ahn import response_guide, triage  # noqa: E402
from ahn.response_guide import BLOCKED_PROCEED_CONSUMER_ROWS, BLOCKED_PROCEED_ROWS, LINE_MAX, ROWS, guide  # noqa: E402
from ahn.settings import parse_proceed_mode  # noqa: E402

FORBIDDEN = ("get past", "bypass", "evade", "spoof", "proxy", "another tool", "different tool", "other browser", "headless",
             "switch to", "user agent", "retry with", "captcha solv", "fake header", "forge")
OFFICIALS = (None, OFFICIAL, OFFICIAL_N, OFFICIAL_K, WITH_CONSUMER, CONSUMER_ONLY)


def answer(official, profile, *, ec=None, fs=None, vendor=None, assume=False) -> dict:
    rec = triage.recommendation(ec, None, profile, official, profile_not_read=False, provider_regional=False, now=NOW,
                                agent_vendor=vendor, failure_stage=fs, assume_failure=assume)
    checks = triage.next_checks(ec, None, "no_reports", fs, profile, official, profile_not_read=False, provider_partial=False,
                                now=NOW, agent_vendor=vendor, assume_failure=assume)
    return {"site_host": "example.com", "provider_status": None, "official_status_page": None, "access_profile": profile,
            "official_paths": official, "recommendation": rec, "next_checks": checks}


def test_switch_parsing_keeps_every_old_value_on_the_d63_answers():
    assert parse_proceed_mode("0") == ("off", "d63")
    for raw in ("", "1", "d63", "D63", "on", "off", "yes", " 1 "):
        assert parse_proceed_mode(raw) == ("on", "d63"), raw
    assert parse_proceed_mode("d65") == parse_proceed_mode(" D65 ") == parse_proceed_mode("d66") == ("on", "d65")


def test_no_signal_under_d65_is_answered_like_site_access_in_every_combination():
    checked = 0
    for profile, official, vendor in itertools.product(PROFILES, OFFICIALS, (None, "anthropic", "openai", "google")):
        blocked = answer(official, profile, vendor=vendor, assume=True)
        refused = answer(official, profile, ec="site_access", vendor=vendor)
        assert blocked["recommendation"] == refused["recommendation"], (profile, official, vendor)
        assert blocked["next_checks"] == refused["next_checks"], (profile, official, vendor)
        assert row_of(blocked) == row_of(refused)
        checked += 1
    assert checked > 100


def test_a_failure_signal_beats_assume_failure():
    for profile, official, ec, fs in itertools.product(PROFILES, OFFICIALS, ERROR_CLASSES, (None, "unknown", "response_observed")):
        if ec is None and fs is None:
            continue
        assert answer(official, profile, ec=ec, fs=fs, assume=True) == answer(official, profile, ec=ec, fs=fs), (profile, official, ec, fs)


def test_hosts_with_a_listed_api_become_use_official_path_and_the_rest_stay_proceed():
    for official, row in ((OFFICIAL_N, "O7-N"), (OFFICIAL_K, "O7-K"), (OFFICIAL, "O7"), (WITH_CONSUMER, "O7")):
        d63 = answer(official, ALLOWS)
        d65 = answer(official, ALLOWS, assume=True)
        assert d63["recommendation"]["action"] == "proceed" and d65["recommendation"]["action"] == "use_official_path"
        assert d65["recommendation"]["confidence"] == "medium" and row_of(d65) == row
        assert "locate_failure_stage" in [c["code"] for c in d65["next_checks"]]
        assert "locate_failure_stage" not in [c["code"] for c in d63["next_checks"]]
    for official in (None, CONSUMER_ONLY):
        for profile in (ALLOWS, ABSENT):
            assert answer(official, profile, assume=True)["recommendation"]["action"] == "proceed"


def test_the_proceed_rows_under_d65_say_a_refusal_is_the_callers_to_report():
    absent = guide(answer(None, ABSENT, assume=True), True, True)
    assert absent["summary_line"] == BLOCKED_PROCEED_ROWS["P3"] and absent["next_action"]["do"] == "proceed"
    assert absent["summary_line"].startswith("This site publishes no robots.txt, so nothing declared blocks agents.")
    assert "the site's edge or a login; tell your user" in absent["summary_line"]
    consumer = guide(answer(CONSUMER_ONLY, ALLOWS, assume=True), True, True)
    assert consumer["summary_line"] == BLOCKED_PROCEED_CONSUMER_ROWS["P1"] and "next_action.customer_url" in consumer["summary_line"]
    # Off by default: the D63 sentence is untouched.
    assert guide(answer(None, ABSENT), True)["summary_line"] == ROWS["P3"][1]
    assert guide(answer(None, ABSENT, assume=True), True)["summary_line"] == ROWS["P3"][1]


def test_every_blocked_proceed_sentence_is_short_printable_plain_and_not_a_way_around_a_control():
    assert set(BLOCKED_PROCEED_ROWS) == set(BLOCKED_PROCEED_CONSUMER_ROWS) == {"P1", "P1-V", "P2", "P2-V", "P3"}
    for table in (BLOCKED_PROCEED_ROWS, BLOCKED_PROCEED_CONSUMER_ROWS):
        for row, text in table.items():
            assert len(text) <= LINE_MAX and re.fullmatch(r"[\x20-\x7E]+", text), (row, len(text))
            assert not re.search(r"\{[^}]*\}|https?://|\b[a-z0-9-]+\.(com|org|io|net)\b", text), row
            low = text.lower()
            for word in FORBIDDEN:
                assert word not in low, (row, word)
            assert "tell your user" in low


def test_blocked_proceed_sentences_follow_the_verdict_for_every_proceed_row():
    seen = set()
    for profile, official, vendor in itertools.product(PROFILES, OFFICIALS, (None, "anthropic", "google")):
        result = answer(official, profile, vendor=vendor, assume=True)
        out = guide(result, True, True)
        if out["next_action"]["do"] != "proceed":
            continue
        row = row_of(result)
        seen.add(row)
        table = BLOCKED_PROCEED_CONSUMER_ROWS if "customer_url" in out["next_action"] else BLOCKED_PROCEED_ROWS
        assert out["summary_line"] == table[row]
    assert {"P1", "P3"} <= seen


def test_the_reserved_step_words_are_exact():
    assert triage.LOOKUP_ONLY_STEPS == {"status_check", "preflight"}
    for near in ("Status_check", "status-check", "statuscheck", "check", "health"):
        assert near not in triage.LOOKUP_ONLY_STEPS


def test_an_unknown_switch_value_warns_once_with_the_code_only_and_keeps_d63(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="ahn.settings"):
        assert parse_proceed_mode("dd65") == ("on", "d63")
        for known in ("", "1", "d63", "0", "d65", "d66", " D65 "):
            parse_proceed_mode(known)
    assert [r.getMessage() for r in caplog.records] == ["proceed_mode_unknown_value"]  # one record, and not the value


def test_the_mcp_text_leaves_out_the_proceed_note_when_the_sentence_already_says_it():
    from ahn import mcp_adapter
    from ahn.models import CheckResponse
    from test_d63_decision_order import answer, envelope
    result = answer(None, ALLOWS, ec=None)
    for blocked_default, note in ((False, True), (True, False)):
        out = guide(result, blocked_default=blocked_default)
        response = CheckResponse.model_validate({**envelope(result), **out})
        text = mcp_adapter.check_result(response).content[0].text
        assert (mcp_adapter.PROCEED_NOTE in text) == note, blocked_default


def test_the_status_check_line_is_in_both_instruction_files():
    line = "send task_step=status_check"
    for name in ("agent_instructions.txt", "llms.txt"):
        assert line in (ROOT / "ahn" / name).read_text(encoding="utf-8"), name


def test_the_gate_table_carries_its_evidence_and_the_instructions_name_the_reserved_word():
    from ahn import mcp_adapter
    from ahn.official_paths import USABLE_NOW_BLOCKED
    assert set(USABLE_NOW_BLOCKED) == {"amazon.com", "reddit.com"}
    for host, entry in USABLE_NOW_BLOCKED.items():
        assert entry["source_url"].startswith("https://") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["verified_at"]), host
    assert "send task_step=status_check" in mcp_adapter.mcp.instructions
    assert "unless it cited robots.txt, which is the site's refusal" in (ROOT / "ahn" / "agent_instructions.txt").read_text(encoding="utf-8")
