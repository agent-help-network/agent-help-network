"""Call timing of find_task_route: every surface that tells an agent when to call it says the same thing, in the same words.

A task-route lookup is useful before the first help page is opened, not after the agent is stuck, so the first thing a surface says is
when to call it. The wording lives in ahn.mcp_task_routes.CALL_TIMING; the static surfaces carry it as plain text and this test
keeps them in step. The records stay records: nothing here changes the answer, the summary line or check_incident.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn.mcp_task_routes import CALL_TIMING, DESCRIPTION, INSTRUCTIONS_ADDENDUM
from ahn.main import create_app
from ahn.settings import Settings
from ahn.task_routes import SUMMARY_NO_RECORDS

ROOT = Path(__file__).resolve().parents[1]


def flat(path: str) -> str:
    return " ".join((ROOT / path).read_text(encoding="utf-8").split())


def test_the_tool_description_opens_with_the_call_timing():
    first = DESCRIPTION.split(". ", 1)[0]
    assert first.startswith("Call this once per task when you start a customer task")
    assert "return" in first and "warranty" in first and "even when nothing is blocked" in first
    assert "do not know" not in DESCRIPTION  # a "when you do not know the route" condition lets an agent skip the call


def test_the_server_instructions_addendum_carries_the_same_clause():
    assert CALL_TIMING in INSTRUCTIONS_ADDENDUM and CALL_TIMING in DESCRIPTION
    assert INSTRUCTIONS_ADDENDUM.startswith("Call find_task_route once per task " + CALL_TIMING + ", with the public hostname")
    assert "do not know its official document" not in INSTRUCTIONS_ADDENDUM


def test_every_static_surface_carries_the_same_clause():
    for path in ("ahn/agent_instructions_task_routes.txt", "ahn/llms.txt", "llms-install.md",
                 "plugins/cursor/agent-help-network/README.md", "ahn/templates/connect.html",
                 "plugins/claude-code/ahn/skills/ahn-lookup/SKILL.md", "plugins/claude-code/ahn/README.md"):
        assert CALL_TIMING in flat(path), path
    assert "when you start a customer task" in flat("ahn/agent_instructions_task_routes.txt")
    assert "고객 업무(반품·교환·환불·해지·보증 청구·계정 복구·서류 제출 등)를 시작할 때" in flat("ahn/templates/connect.html")
    for path in ("ahn/agent_instructions_task_routes.txt", "ahn/llms.txt", "llms-install.md", "plugins/cursor/agent-help-network/README.md",
                 "ahn/templates/connect.html", "plugins/claude-code/ahn/README.md"):
        assert "once per task" in flat(path), path
    for path in ("plugins/claude-code/ahn/skills/ahn-lookup/SKILL.md", "plugins/claude-code/ahn/README.md"):
        assert "do not know its official document" not in flat(path) and "does not know its official document" not in flat(path), path


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


def openapi(task_routes_on: bool) -> dict:
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True, task_routes=task_routes_on,
                        task_routes_since=date(2026, 11, 1) if task_routes_on else None)
    return create_app(settings).openapi()


def test_the_openapi_document_opens_the_task_route_lookup_with_the_call_timing():
    operation = openapi(True)["paths"]["/v1/task-routes"]["get"]
    assert operation["summary"] == "Call this once per task " + CALL_TIMING
    assert operation["description"].startswith("Call this once per task " + CALL_TIMING + ". ")
    assert operation["description"].split(". ", 1)[0] == DESCRIPTION.split(". ", 1)[0]
    assert "do not know" not in operation["description"]
    assert "records, not a guarantee and not advice; an empty list does not mean there is no route" in operation["description"]
    params = {p["name"]: p for p in operation["parameters"]}
    assert set(params) == {"site_host", "task_kind"} and all(p["description"] for p in params.values())
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/TaskRoutesResponse")
    assert "invalid_site_host" in operation["responses"]["400"]["description"]


def test_the_openapi_document_lists_only_the_read_path_and_only_while_the_switch_is_on():
    paths = openapi(True)["paths"]
    assert set(paths["/v1/task-routes"]) == {"get"}  # no POST, no DELETE
    assert not [path for path in paths if path.startswith("/v1/task-routes/") or path.endswith("/routes") or "/routes/" in path]
    assert "/v1/task-routes" not in openapi(False)["paths"]


def test_the_openapi_document_marks_site_host_required_and_has_no_422():
    operation = openapi(True)["paths"]["/v1/task-routes"]["get"]
    params = {p["name"]: p for p in operation["parameters"]}
    assert params["site_host"]["required"] is True and "default" not in params["site_host"]["schema"]
    assert not params["task_kind"].get("required")
    assert set(operation["responses"]) == {"200", "400"}  # this path never answers 422
    assert openapi(True) == openapi(True)  # repeated builds give the same document


def test_a_missing_site_host_is_still_a_400_at_runtime_although_the_document_says_required():
    from fastapi.testclient import TestClient
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True, task_routes=True,
                        task_routes_since=date(2026, 11, 1))
    response = TestClient(create_app(settings)).get("/v1/task-routes")
    assert response.status_code == 400 and response.json() == {"error": "invalid_site_host"}
