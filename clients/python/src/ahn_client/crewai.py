"""CrewAI: an Agent Help Network line for every tool failure CrewAI reports.

    pip install "ahn-client[crewai]"

    from ahn_client.crewai import register_listener

    listener = register_listener(on_note=print)   # once, before crew.kickoff()

``ToolFailureDetectedEvent`` is the documented event for a tool call that failed
(https://docs.crewai.com/en/concepts/tools, "Reporting tool failures"); CrewAI emits it when a tool raises,
returns a ``ToolFailure`` or an MCP server answers ``isError``, unless ``tool_failure_policy`` is ``ignore``.
The listener looks up the hostname of the URL in the call's arguments (or in the failure message) when the
failure is a block or an outage, and hands you one line.

Two things CrewAI's design decides, and this module does not work around:

* An event listener only observes. The event arrives after CrewAI has built the text the agent will see, so
  the line goes to ``on_note(note, event)``, the ``ahn_client.crewai`` logger (INFO) and ``listener.notes``,
  not into the agent's prompt. To put it into the tool result as well, register ``append_note_to_result``
  as an ``after_tool_call`` hook (below); that hook sees only a tool that returned a ``ToolFailure``.
* The original failure is untouched: nothing is retried or suppressed, and ``tool_failure_policy`` keeps
  working as configured.

A lookup that fails or takes longer than ``timeout`` (2 s) adds nothing and never raises into CrewAI.
Event handlers run on CrewAI's own worker threads, so the wait does not block the crew. Only the hostname
and enum codes leave the machine.
"""

from __future__ import annotations

import collections
import logging
from typing import Any, Callable, Optional

from ._adapter import DEFAULT_TIMEOUT, note_for

ADAPTER = "crewai"
logger = logging.getLogger("ahn_client.crewai")

__all__ = ["ADAPTER", "AHNToolFailureListener", "append_note_to_result", "note_for_event", "register_listener"]


def _failure_text(failure: Any) -> str:
    message = getattr(failure, "message", None)
    code = getattr(failure, "code", None)
    return " ".join(part for part in (code, message) if isinstance(part, str))


def note_for_event(event: Any, *, timeout: float = DEFAULT_TIMEOUT) -> Optional[str]:
    """The line for a ``ToolFailureDetectedEvent`` (or anything with ``tool_args`` and ``failure``), or None."""
    try:
        return note_for(_failure_text(getattr(event, "failure", None)), getattr(event, "tool_args", None),
                        adapter=ADAPTER, timeout=timeout)
    except Exception:
        return None


class AHNToolFailureListener:
    """Collects the lines for tool failures; create it with ``register_listener``."""

    def __init__(self, on_note: Optional[Callable[[str, Any], Any]] = None, *, timeout: float = DEFAULT_TIMEOUT,
                 max_notes: int = 50) -> None:
        self.on_note = on_note
        self.timeout = timeout
        self.notes = collections.deque(maxlen=max_notes)  # the most recent lines, oldest first

    def handle(self, source: Any, event: Any) -> None:
        """The handler registered on the event bus: ``(source, event)`` as CrewAI calls it. Never raises."""
        try:
            note = note_for_event(event, timeout=self.timeout)
            if not note:
                return
            self.notes.append(note)
            logger.info("%s", note)
            if self.on_note is not None:
                self.on_note(note, event)
        except Exception:
            logger.debug("ahn_client.crewai handler failed", exc_info=True)


def register_listener(on_note: Optional[Callable[[str, Any], Any]] = None, *, timeout: float = DEFAULT_TIMEOUT,
                      event_bus: Any = None) -> AHNToolFailureListener:
    """Subscribe to ``ToolFailureDetectedEvent`` on CrewAI's event bus and return the listener."""
    if event_bus is None:
        from crewai.events import crewai_event_bus as event_bus
    from crewai.events import ToolFailureDetectedEvent

    listener = AHNToolFailureListener(on_note, timeout=timeout)
    event_bus.on(ToolFailureDetectedEvent)(listener.handle)
    return listener


def append_note_to_result(context: Any) -> Optional[str]:
    """An ``after_tool_call`` hook that appends the line to the result of a tool that returned a ``ToolFailure``.

        from crewai.hooks import after_tool_call
        after_tool_call(append_note_to_result)

    (https://docs.crewai.com/en/learn/tool-hooks: an after hook returns a string to replace the tool result,
    or None to keep it.) Only a tool that declares its failure by returning ``crewai.tools.ToolFailure`` is
    seen here; an exception a tool raises reaches the agent as CrewAI's own error text before any hook can tell
    it from a normal result, so use the event listener for those. Returns None, leaving the result as it is, in
    every other case and on any error.
    """
    try:
        raw = getattr(context, "raw_tool_result", None)
        # A ToolFailure (crewai.tools.ToolFailure) carries a message and a reason; a plain result has neither.
        if not isinstance(getattr(raw, "message", None), str) or getattr(raw, "reason", None) is None:
            return None
        note = note_for(_failure_text(raw), getattr(context, "tool_input", None), adapter=ADAPTER)
        result = getattr(context, "tool_result", None)
        if not note or not isinstance(result, str):
            return None
        return result + "\n" + note
    except Exception:
        return None
