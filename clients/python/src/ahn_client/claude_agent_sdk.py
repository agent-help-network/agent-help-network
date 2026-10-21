"""Claude Agent SDK (Python): add an Agent Help Network line when a web tool fails.

    pip install "ahn-client[claude-agent-sdk]"

    from claude_agent_sdk import ClaudeAgentOptions
    from ahn_client.claude_agent_sdk import ahn_hooks

    options = ClaudeAgentOptions(hooks=ahn_hooks())

The hook is the documented ``PostToolUseFailure`` event
(https://code.claude.com/docs/en/agent-sdk/hooks): its callback gets the failed tool's name, input and
error text, and returns ``hookSpecificOutput.additionalContext``, which the SDK adds to Claude's context.
The decision of *which* failures to look up is the Claude Code plugin's (plugins/claude-code/ahn/hooks/
ahn_lookup.py): a WebFetch call, or an MCP browser/fetch tool whose input or error names an http(s) URL,
that failed with a block or an outage. A 404 or a bad request is not looked up.

``ahn_hooks()`` also registers the plugin's second case, ``PostToolUse`` for ``WebFetch``, because
WebFetch reports an HTTP 401, 403, 429 or 5xx as a successful call with a status code.

The original error is never changed or hidden, nothing is retried, and the hook always returns (an
empty ``{}`` when there is nothing to add), within ``timeout`` seconds (2 by default). Only the hostname
and enum codes leave the machine: the error class and stage, and when the tool result already holds them the
HTTP status, whether a challenge header was present, a Retry-After band and whether the tool was a fetch or a browser.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._adapter import DEFAULT_TIMEOUT, abounded_check
from ._claude_code import failure, signals

ADAPTER = "claude-agent-sdk"
# The plugin's matcher for PostToolUseFailure (plugins/claude-code/ahn/hooks/hooks.json).
FAILURE_MATCHER = r"^WebFetch$|^mcp__(?!plugin_ahn_).*([Bb]rowser|[Cc]hrome|[Pp]laywright|[Pp]uppeteer|[Ff]etch)((?!__).)*__"
SUCCESS_MATCHER = "WebFetch"
HOOK_TIMEOUT_SECONDS = 10

__all__ = ["ADAPTER", "FAILURE_MATCHER", "ahn_hooks", "make_hook", "post_tool_use_failure_hook"]


def make_hook(*, timeout: float = DEFAULT_TIMEOUT):
    """A hook callback ``(input_data, tool_use_id, context) -> dict`` with the given lookup time limit."""

    async def ahn_hook(input_data: Any, tool_use_id: Optional[str] = None, context: Any = None) -> Dict[str, Any]:
        try:
            found = failure(input_data)
            if found is None:
                return {}
            host, error_class, stage = found
            result = await abounded_check(host, error_class, stage, ADAPTER, timeout, signals(input_data) or None)
            if result is None:
                return {}
            return {"hookSpecificOutput": {"hookEventName": input_data["hook_event_name"],
                                           "additionalContext": result.summary()}}
        except Exception:
            return {}

    return ahn_hook


post_tool_use_failure_hook = make_hook()


def ahn_hooks(existing: Optional[Dict[str, List[Any]]] = None, *, matcher: str = FAILURE_MATCHER,
              timeout: float = DEFAULT_TIMEOUT) -> Dict[str, List[Any]]:
    """The ``hooks`` dict for ``ClaudeAgentOptions``: ``PostToolUseFailure`` and, for WebFetch, ``PostToolUse``.

    Pass your own hooks as ``existing`` to merge them: ``ClaudeAgentOptions(hooks=ahn_hooks(my_hooks))``.
    ``matcher`` replaces the tool-name pattern of the failure hook (it is matched against the tool name).
    """
    from claude_agent_sdk import HookMatcher

    callback = make_hook(timeout=timeout)
    merged = {event: list(matchers) for event, matchers in (existing or {}).items()}
    merged.setdefault("PostToolUseFailure", []).append(
        HookMatcher(matcher=matcher, hooks=[callback], timeout=HOOK_TIMEOUT_SECONDS))
    merged.setdefault("PostToolUse", []).append(
        HookMatcher(matcher=SUCCESS_MATCHER, hooks=[callback], timeout=HOOK_TIMEOUT_SECONDS))
    return merged
