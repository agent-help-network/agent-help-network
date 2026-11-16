"""Call timing of find_task_route: every surface that tells an agent when to call it says the same thing, in the same words.

A task-route lookup is useful before the first help page is opened, not after the agent is stuck, so the first thing a surface says is
when to call it. The wording lives in ahn.mcp_task_routes.CALL_TIMING; the static surfaces carry it as plain text and this test
keeps them in step. The records stay records: nothing here changes the answer, the summary line or check_incident.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn.mcp_task_routes import CALL_TIMING, DESCRIPTION, INSTRUCTIONS_ADDENDUM
from ahn.task_routes import SUMMARY_NO_RECORDS

ROOT = Path(__file__).resolve().parents[1]


def flat(path: str) -> str:
    return " ".join((ROOT / path).read_text(encoding="utf-8").split())


def test_the_tool_description_opens_with_the_call_timing():
    first = DESCRIPTION.split(". ", 1)[0]
    assert first.startswith("Call this once when you start a customer task")
    assert "return" in first and "warranty" in first and "even when nothing is blocked" in first
    assert "do not know" not in DESCRIPTION  # a "when you do not know the route" condition lets an agent skip the call


def test_the_server_instructions_addendum_carries_the_same_clause():
    assert CALL_TIMING in INSTRUCTIONS_ADDENDUM and CALL_TIMING in DESCRIPTION
    assert "do not know its official document" not in INSTRUCTIONS_ADDENDUM


def test_every_static_surface_carries_the_same_clause():
    for path in ("ahn/agent_instructions_task_routes.txt", "ahn/llms.txt", "llms-install.md",
                 "plugins/cursor/agent-help-network/README.md", "ahn/templates/connect.html"):
        assert CALL_TIMING in flat(path), path
    assert "when you start a customer task" in flat("ahn/agent_instructions_task_routes.txt")
    assert "고객 업무(반품·환불·해지·보증 청구 등)를 시작할 때" in flat("ahn/templates/connect.html")


def test_the_call_timing_promises_nothing_about_the_answer():
    lowered = CALL_TIMING.lower()
    for promise in ("deadline", "days", "step", "guarantee", "advice", "will tell"):
        assert promise not in lowered


def test_the_kept_sentences_are_unchanged():
    for text in (DESCRIPTION, INSTRUCTIONS_ADDENDUM):
        assert "records, not a guarantee and not advice; an empty list does not mean there is no route" in text
    assert INSTRUCTIONS_ADDENDUM.endswith("The answer does not change when to call check_incident or what check_incident recommends.")
    assert "Sends only the hostname and an optional task kind, and changes nothing. Read summary_line first." in DESCRIPTION
    assert SUMMARY_NO_RECORDS == "No task route records for this site. A missing record does not mean there is no route."
    assert "not guarantees and not advice" in flat("ahn/agent_instructions_task_routes.txt")
    assert "records, not guarantees or advice" in flat("ahn/llms.txt")
