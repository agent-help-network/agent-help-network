"""Sign-in that fails after credentials were entered, or an extra verification step, is a reason to look a host up.

The condition is worded in every place an agent reads it: the agent instructions, llms.txt, the MCP server instructions, the
check_incident tool description and the plugin skill. Wording only: no answer changes.
"""
import asyncio
from pathlib import Path

from ahn import mcp_adapter

ROOT = Path(__file__).resolve().parent.parent
STEPS = ("a code", "app approval", "new-device check")
FORBIDDEN = ("receive the code", "session cookie", "skip the verification", "skip the step")


def _read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def _tool_description():
    tools = asyncio.run(mcp_adapter.mcp.list_tools())
    return next(t.description for t in tools if t.name == "check_incident")


def _surfaces():
    return {
        "agent_instructions": _read("ahn", "agent_instructions.txt"),
        "mcp_instructions": mcp_adapter.mcp.instructions,
        "tool_description": _tool_description(),
        "skill": _read("plugins", "claude-code", "ahn", "skills", "ahn-lookup", "SKILL.md"),
    }


def test_each_surface_names_the_failed_sign_in_and_the_extra_steps():
    for name, text in _surfaces().items():
        assert "login_auth" in text, name
        assert "sign-in" in text, name
        for step in STEPS:
            assert step in text, (name, step)
    assert "a code by text or email" in _surfaces()["agent_instructions"]


def test_llms_txt_names_the_condition():
    text = _read("ahn", "llms.txt")
    assert "entered credentials that did not sign in" in text
    for step in ("a code by text or email", "an authenticator app approval", "a new-device check"):
        assert step in text


def test_user_line_says_which_step_stopped_and_whether_the_user_is_needed():
    for name in ("agent_instructions", "mcp_instructions"):
        text = _surfaces()[name]
        assert "which step stopped you and whether it needs them personally" in text, name
    assert "which step stopped you" in _surfaces()["skill"]


def test_no_workaround_wording_and_password_rule_stays():
    for name, text in _surfaces().items():
        low = text.lower()
        for bad in FORBIDDEN:
            assert bad not in low, (name, bad)
    assert "never a URL path, query or credentials" in _surfaces()["mcp_instructions"]
    assert "do not ask for passwords" in _surfaces()["skill"]
