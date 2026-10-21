"""LangChain v1: add an Agent Help Network line to a web tool call that failed.

    pip install "ahn-client[langchain]"

    from langchain.agents import create_agent
    from ahn_client.langchain import ahn_tool_failure_middleware

    agent = create_agent(model, tools=[fetch_page], middleware=[ahn_tool_failure_middleware()])

The middleware implements the documented ``wrap_tool_call`` / ``awrap_tool_call`` hooks
(https://docs.langchain.com/oss/python/langchain/middleware/custom). It acts on a tool call that failed with a
block or an outage (403, 429, a challenge, a login wall, 5xx, a timeout, a refused connection) and whose URL names
a host (in the call's arguments, or in the exception's request):

* A tool call that comes back as a ``ToolMessage`` with ``status="error"`` (a ``ToolNode`` with
  ``handle_tool_errors``, ``ToolRetryMiddleware``'s default after its last attempt, or a tool that returns one):
  the line is appended to its content. The original content stays, in front.
* A tool that raises: **the exception is re-raised unchanged**, so the run behaves exactly as it does without the
  middleware, whether or not Agent Help Network answers. (An exception cannot carry text to the model.)

``ahn_tool_failure_middleware(convert_exceptions=True)`` also handles raised exceptions, and always the same way, for
every block or outage with a host, whatever the lookup returned: the exception becomes
``ToolMessage(status="error")`` whose content is ``Error: <exception class> (<error class>)`` followed by the line
when there is one. The exception's text, URL, path and query are not put into the message. That changes how the
run continues (it goes on instead of stopping), which is why it is off by default.

Order matters: middleware listed first is outermost. List this one **before** ``ToolRetryMiddleware``, so retries
happen first and this middleware sees the final failure once:
``middleware=[ahn_tool_failure_middleware(), ToolRetryMiddleware()]``. Listed after it, with ``convert_exceptions=True``,
it would turn the first exception into a message and the retries would never run.

Nothing else is touched: a failure that is not a block or an outage (404, 422, a typo, a tool-argument validation error),
an unreachable service, a slow lookup (``timeout``, 2 s) and a missing URL all leave the call exactly as the tool produced it.
A tool that returns the text of a 403 page as an ordinary result is not an error to LangChain, so nothing is added; raise
from the tool (``response.raise_for_status()``) or return ``ToolMessage(..., status="error")`` to opt in. Only the
hostname and two enum codes leave the machine.
"""

from __future__ import annotations

from typing import Any, Optional

from ._adapter import DEFAULT_TIMEOUT, anote_for, failure_target, note_for

ADAPTER = "langchain"

__all__ = ["ADAPTER", "ahn_tool_failure_middleware", "handle_tool_call", "ahandle_tool_call"]

# LangGraph's control-flow signals travel as exceptions through a tool call: GraphBubbleUp (and its subclasses GraphInterrupt,
# NodeInterrupt, ParentCommand, GraphDrained), the node cancel and timeout errors, and the pydantic ValidationError that
# interrupt() raises for a resume value of the wrong shape (marked with the attribute below; see langgraph.errors.is_invalid_resume).
CONTROL_FLOW = frozenset({"GraphBubbleUp", "NodeCancelledError", "NodeTimeoutError"})
INVALID_RESUME_MARK = "_langgraph_invalid_resume"


def _tool_message_cls():
    from langchain.messages import ToolMessage  # the import the LangChain docs use

    return ToolMessage


def _control_flow(error: BaseException) -> bool:
    """True for LangGraph's control-flow signals; they are never tool failures and always propagate."""
    if getattr(error, INVALID_RESUME_MARK, False) is True:
        return True
    return any(cls.__name__ in CONTROL_FLOW for cls in type(error).__mro__)


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


def _converted(request: Any, error: Exception, error_class: str, note: Optional[str]) -> Any:
    """The error ToolMessage that stands for an exception: its class and the error class, never its text."""
    call = _call(request)
    content = f"Error: {type(error).__name__} ({error_class})" + (f"\n{note}" if note else "")
    return _tool_message_cls()(content=content, name=call.get("name"), tool_call_id=call.get("id"), status="error")


def handle_tool_call(request: Any, handler: Any, *, convert_exceptions: bool = False,
                     timeout: float = DEFAULT_TIMEOUT) -> Any:
    """The body of the middleware's ``wrap_tool_call``; usable on its own inside your own middleware."""
    args = _call(request).get("args")
    try:
        result = handler(request)
    except Exception as error:
        if not convert_exceptions or _control_flow(error):
            raise
        target = failure_target(error, args)
        if target is None:
            raise
        note = note_for(error, args, adapter=ADAPTER, timeout=timeout)
        try:
            message = _converted(request, error, target[1], note)
        except Exception:
            message = None
        if message is None:
            raise
        return message
    text = _error_text(result)
    if text:
        note = note_for(text, args, adapter=ADAPTER, timeout=timeout)
        if note:
            return _annotated(result, note)
    return result


async def ahandle_tool_call(request: Any, handler: Any, *, convert_exceptions: bool = False,
                            timeout: float = DEFAULT_TIMEOUT) -> Any:
    """The async twin of ``handle_tool_call``: the wait never blocks the event loop and ends at ``timeout``."""
    args = _call(request).get("args")
    try:
        result = await handler(request)
    except Exception as error:
        if not convert_exceptions or _control_flow(error):
            raise
        target = failure_target(error, args)
        if target is None:
            raise
        note = await anote_for(error, args, adapter=ADAPTER, timeout=timeout)
        try:
            message = _converted(request, error, target[1], note)
        except Exception:
            message = None
        if message is None:
            raise
        return message
    text = _error_text(result)
    if text:
        note = await anote_for(text, args, adapter=ADAPTER, timeout=timeout)
        if note:
            return _annotated(result, note)
    return result


def ahn_tool_failure_middleware(*, convert_exceptions: bool = False, timeout: float = DEFAULT_TIMEOUT):
    """A LangChain agent middleware (pass it in ``create_agent(..., middleware=[...])``); needs ``langchain`` v1.

    List it before ``ToolRetryMiddleware`` (see the module documentation). ``convert_exceptions=True`` turns a raised
    block or outage into an error ``ToolMessage`` without the exception's text; the default re-raises it unchanged.
    """
    from langchain.agents.middleware import AgentMiddleware

    class AHNToolFailureMiddleware(AgentMiddleware):
        """Adds an Agent Help Network line to the result of a web tool call that was blocked or failed."""

        def wrap_tool_call(self, request, handler):
            return handle_tool_call(request, handler, convert_exceptions=convert_exceptions, timeout=timeout)

        async def awrap_tool_call(self, request, handler):
            return await ahandle_tool_call(request, handler, convert_exceptions=convert_exceptions, timeout=timeout)

    return AHNToolFailureMiddleware()
