"""Wording only: the sentences that say who a block applies to, what proceed does not mean and what the developer path is for.

Pure: no database. The verdicts (recommendation action, reasons, row) are those of D63/D64; every case below also checks
that nothing but the sentence moved."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from ahn import mcp_adapter, response_guide
from ahn.access_profile import parse_robots
from ahn.models import CheckResponse
from ahn.response_guide import CONSUMER_ROWS, LINE_MAX, ROWS, SAFE_URL, guide
from ahn.triage import CHECKS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_d63_decision_order import ABSENT, ALLOWS, BLOCKS, OFFICIAL, answer, envelope, robots  # noqa: E402
FORBIDDEN = ("get past", "bypass", "evade", "spoof", "work around")


def instructions() -> str:
    return (ROOT / "ahn" / "agent_instructions.txt").read_text(encoding="utf-8")


def test_rule_4_makes_switching_the_users_call_and_names_who_refused():
    text = instructions()
    assert "Do not switch tools or user agents on your own because of a block. Tell your user what was refused and by whom " \
           "(your tool, the site's edge, or robots.txt); switching is their call." in text
    assert "because of a block; follow next_action" not in text
    quick = text.split("Quick rules:")[1].split("\n\n")[0].lower()
    for word in FORBIDDEN:
        assert word not in quick, word


def test_a_lookup_is_usually_a_block_and_proceed_without_a_signal_is_not_all_clear():
    text = instructions()
    assert "A lookup usually means something was blocked or failed, so send error_class and failure_stage when you have them." in text
    assert "A proceed answer without them means no block is declared, not that nothing blocked you." in text


def test_token_rows_say_the_block_applies_to_requests_that_send_the_tokens():
    for row in ("O3", "U3"):
        assert "applies to requests that send them (next_action.stop_if_you_are)" in ROWS[row][1], row
    assert "for requests that send them" in CONSUMER_ROWS["O3"]
    assert "via" not in CONSUMER_ROWS["O3"].lower().split() and "via" not in ROWS["O3"][1].lower().split()  # D47: a code never splits a sentence


def test_wildcard_rows_say_which_tokens_the_wildcard_covers():
    for row in ("O6", "U5"):
        assert "(every token not in robots.agents_allowed)" in ROWS[row][1], row
    assert "tokens not in robots.agents_allowed" in CONSUMER_ROWS["O6"]


def test_every_changed_sentence_fits_the_line_limit_and_is_printable():
    for row in ("O3", "U3", "O6", "U5"):
        assert len(ROWS[row][1]) <= LINE_MAX and re.fullmatch(r"[\x20-\x7E]+", ROWS[row][1]), row
    for row in ("O3", "O6"):
        assert len(CONSUMER_ROWS[row]) <= LINE_MAX, row


def test_the_verdict_rows_and_actions_did_not_move():
    assert (ROWS["O3"][0], ROWS["U3"][0], ROWS["O6"][0], ROWS["U5"][0]) == ("use_official_path", "stop_if_listed", "use_official_path", "no_clear_action")
    assert ROWS["O3"][1] != ROWS["U3"][1]


def test_off_mode_keeps_the_sentences_from_before():
    some = robots("User-agent: Claude-User\nDisallow: /\n\nUser-agent: ChatGPT-User\nDisallow: /\n")
    result = answer(None, some)
    assert result["recommendation"]["reasons"][0] == "robots_blocks_some_agents"
    assert guide(result, False)["summary_line"] == ("The site's robots.txt blocks some agent tokens. If your requests send one in "
                                                    "next_action.stop_if_you_are, stop retrying pages; else send agent_vendor.")
    assert guide(result)["summary_line"] == ROWS["U3"][1]


def test_the_official_path_check_names_what_a_developer_api_is_for():
    text = CHECKS["official_path_available"]
    assert "a developer API for building on the site's data" in text and "next_action.customer_url" in text
    assert "sanctioned route" not in text and "usually the more reliable" not in text


def test_the_changelog_link_points_at_an_anchor_that_exists():
    link = response_guide.links()["changelog"]
    assert link.endswith("docs/feedback-changelog.md#d64") and SAFE_URL.fullmatch(link) and len(link) <= response_guide.URL_MAX
    log = (ROOT / "docs" / "feedback-changelog.md").read_text(encoding="utf-8")
    assert re.search(r"^#{2,3} D64\s*$", log, re.M)


def test_the_mcp_text_adds_the_proceed_note_only_for_proceed():
    for profile, official, ec, expect in ((ALLOWS, None, None, True), (ABSENT, None, None, True), (BLOCKS, OFFICIAL, "site_access", False)):
        result = answer(official, profile, ec=ec)
        out = guide(result)
        response = CheckResponse.model_validate({**envelope(result), **out})
        lines = mcp_adapter.check_result(response).content[0].text.splitlines()
        assert (out["next_action"]["do"] == "proceed") == expect
        assert (lines[-1] == mcp_adapter.PROCEED_NOTE) == expect
        assert lines[0] == out["summary_line"]
    assert "not that nothing blocked you" not in out["summary_line"]
    assert "Proceed means the site declares no block, not that nothing blocked you." in mcp_adapter.PROCEED_NOTE
    assert "tell your user what was refused and by whom" in mcp_adapter.PROCEED_NOTE
    assert chr(10) not in mcp_adapter.PROCEED_NOTE
