"""LangChain v1: add an Agent Help Network line to a web tool call that failed.

    pip install "ahn-client[langchain]"

    from langchain.agents import create_agent
    from ahn_client.langchain import ahn_tool_failure_middleware

    agent = create_agent(model, tools=[fetch_page], middleware=[ahn_tool_failure_middleware()])

The middleware implements the documented ``wrap_tool_call`` / ``awrap_tool_call`` hooks
(https://docs.langchain.com/oss/python/langchain/middleware/custom). When a tool call fails with a
block or an outage (403, 429, a challenge, a login wall, 5xx, a timeout, a refused connection), it
looks up the hostname of the call's URL and adds one line to the tool's result:

* A tool that raises: the exception becomes a ``ToolMessage`` with ``status="error"`` whose content is
  ``Error: <the original exception's repr>`` and then the line. This is what ``ToolNode`` itself does
  with ``handle_tool_errors=True`` (``convert_exceptions=False`` re-raises instead and never changes
  an exception into a message).
* A tool that returns a ``ToolMessage`` with ``status="error"``: the line is appended to its content.

Nothing else is touched: a failure that is not a block or an outage (404, 422, a typo in an address),
an unreachable service, a slow lookup (``timeout``, 2 s) and a missing URL all leave the tool call exactly
as the tool produced it. A tool that returns the text of a 403 page as an ordinary result is not an
error to LangChain, so nothing is added; raise from the tool (``response.raise_for_status()``) or return
``ToolMessage(..., status="error")`` to opt in. Only the hostname and two enum codes leave the machine.
"""

from __future__ import annotations

from typing import Any, Optional

from ._adapter import DEFAULT_TIMEOUT, anote_for, note_for

ADAPTER = "langchain"

__all__ = ["ADAPTER", "ahn_tool_failure_middleware", "handle_tool_call", "ahandle_tool_call"]


def _tool_message_cls():
    from langchain.messages import ToolMessage  # the import the LangChain docs use

    return ToolMessage


def _control_flow(error: BaseException) -> bool:
    """LangGraph's interrupt and command signals travel as exceptions; they are never tool failures."""
    return any(cls.__name__ in ("GraphBubbleUp", "ParentCommand") for cls in type(error).__mro__)


def _call(request: Any) -> dict:
    call = getattr(request, "tool_call", None)
    return call if isinstance(call, dict) else {}


def _append(content: Any, note: str) -> Any:
    if isinstance(content, str):
        return content + "\n" + note
    if isinstance(content, list):
        return list(content) + [{"type": "text", "text": note}]
    return content


def _annotated(message: Any, note: str) -> Any:
    """A copy of an error ToolMessage with the line appended; the original object is left alone."""
    content = _append(getattr(message, "content", None), note)
    if content is getattr(message, "content", None):
        return message
    copy = getattr(message, "model_copy", None)
    return copy(update={"content": content}) if callable(copy) else message


def _error_text(message: Any) -> Optional[str]:
    """The text of an error ToolMessage's content, or None when there is no text to read."""
    if getattr(message, "status", None) != "error":
        return None
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part if isinstance(part, str) else str(part.get("text", "")) for part in content
                        if isinstance(part, (str, dict)))
    return None


def _error_message(request: Any, error: Exception, note: str) -> Any:
    call = _call(request)
    return _tool_message_cls()(content=f"Error: {error!r}\n{note}", name=call.get("name"),
                               tool_call_id=call.get("id"), status="error")


def handle_tool_call(request: Any, handler: Any, *, convert_exceptions: bool = True,
                     timeout: float = DEFAULT_TIMEOUT) -> Any:
    """The body of the middleware's ``wrap_tool_call``; usable on its own inside your own middleware."""
    args = _call(request).get("args")
    try:
        result = handler(request)
    except Exception as error:
        if not convert_exceptions or _control_flow(error):
            raise
        note = note_for(error, args, adapter=ADAPTER, timeout=timeout)
        if note is None:
            raise
        try:
            return _error_message(request, error, note)
        except Exception:
            raise error from None
    text = _error_text(result)
    if text:
        note = note_for(text, args, adapter=ADAPTER, timeout=timeout)
        if note:
            return _annotated(result, note)
    return result


async def ahandle_tool_call(request: Any, handler: Any, *, convert_exceptions: bool = True,
                            timeout: float = DEFAULT_TIMEOUT) -> Any:
    """The async twin of ``handle_tool_call``: the lookup runs in a worker thread, not on the event loop."""
    args = _call(request).get("args")
    try:
        result = await handler(request)
    except Exception as error:
        if not convert_exceptions or _control_flow(error):
            raise
        note = await anote_for(error, args, adapter=ADAPTER, timeout=timeout)
        if note is None:
            raise
        try:
            return _error_message(request, error, note)
        except Exception:
            raise error from None
    text = _error_text(result)
    if text:
        note = await anote_for(text, args, adapter=ADAPTER, timeout=timeout)
        if note:
            return _annotated(result, note)
    return result


def ahn_tool_failure_middleware(*, convert_exceptions: bool = True, timeout: float = DEFAULT_TIMEOUT):
    """A LangChain agent middleware (pass it in ``create_agent(..., middleware=[...])``); needs ``langchain`` v1."""
    from langchain.agents.middleware import AgentMiddleware

    class AHNToolFailureMiddleware(AgentMiddleware):
        """Adds an Agent Help Network line to the result of a web tool call that was blocked or failed."""

        def wrap_tool_call(self, request, handler):
            return handle_tool_call(request, handler, convert_exceptions=convert_exceptions, timeout=timeout)

        async def awrap_tool_call(self, request, handler):
            return await ahandle_tool_call(request, handler, convert_exceptions=convert_exceptions, timeout=timeout)

    return AHNToolFailureMiddleware()
