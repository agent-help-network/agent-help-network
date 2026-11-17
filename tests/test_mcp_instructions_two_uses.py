"""The MCP server instructions and the manifest descriptions name two uses side by side: recorded routes for a customer task on a site
(find_task_route) and the cause of a blocked or failing web step (check_incident). An agent that is not blocked must still read the
text as being about it, and a refusal must name the tool it refuses."""
import json
import re
from pathlib import Path

import pytest

from ahn import mcp_adapter

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def instructions(monkeypatch):
    monkeypatch.setenv("AHN_TASK_ROUTES", "1")
    monkeypatch.setenv("AHN_TASK_ROUTES_SINCE", "2026-11-16")
    return mcp_adapter._instructions()


def test_both_uses_appear_and_the_task_start_use_is_not_pushed_to_the_end(instructions):
    assert "find_task_route" in instructions and "check_incident" in instructions
    assert instructions.index("find_task_route") < instructions.index("check_incident")
    assert "even when nothing is blocked" in instructions[: instructions.index("check_incident")]
    assert not instructions.rstrip().endswith("find_task_route.")


def test_no_refusal_without_a_named_tool(instructions):
    for match in re.finditer(r"[Dd]o not call (\w+)", instructions):
        assert match.group(1) == "check_incident", match.group(0)
    assert "Do not call it" not in instructions


def test_a_task_over_several_hosts_is_looked_up_once_per_host(instructions):
    assert "Call find_task_route once per task when you start" in instructions
    assert "if the task spans several hosts, look up each host once" in instructions


def test_no_forcing_words(instructions):
    assert not re.search(r"\b(ALWAYS|MUST|NEVER)\b", instructions)
    assert "before any other tool" not in instructions.lower()
    for hyped in ("official", "guaranteed"):
        assert hyped not in instructions.lower().replace("official path", "").replace("official developer", "")
    assert "advisory" in instructions and "not a guarantee and not advice" in instructions and "an empty list does not mean there is no route" in instructions


def test_the_text_is_not_longer_than_needed(instructions):
    assert len(instructions) <= 2650


def test_the_switch_off_text_names_no_task_route_tool(monkeypatch):
    monkeypatch.delenv("AHN_TASK_ROUTES", raising=False)
    assert "find_task_route" not in mcp_adapter._instructions()


TASK_VERBS = ("refund", "return", "cancellation")


def _descriptions():
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    claude = json.loads((ROOT / "plugins/claude-code/ahn/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))["plugins"][0]
    cursor = json.loads((ROOT / "plugins/cursor/agent-help-network/.cursor-plugin/plugin.json").read_text(encoding="utf-8"))
    chatgpt = json.loads((ROOT / "plugins/agent-help-network/plugin.json").read_text(encoding="utf-8"))
    return {"server.json": server["description"], "claude-code": claude["description"], "marketplace": market["description"],
            "cursor": cursor["description"], "chatgpt": chatgpt["description"]}


@pytest.mark.parametrize("name", ["server.json", "claude-code", "marketplace", "cursor", "chatgpt"])
def test_manifest_descriptions_open_with_the_task_use(name):
    text = _descriptions()[name].lower()
    first_half = text[: max(len(text) // 2, 110)]
    for verb in TASK_VERBS:
        assert verb in first_half, (name, verb)
    assert "blocked" in text
