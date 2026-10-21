"""OpenAI Agents SDK: add an Agent Help Network line to the message a failed web tool sends the model.

    pip install "ahn-client[openai-agents]"

    from agents import function_tool
    from ahn_client.openai_agents import ahn_failure_error_function

    @function_tool(failure_error_function=ahn_failure_error_function)
    def fetch_page(url: str) -> str: ...

``failure_error_function`` is the documented hook of ``@function_tool``
(https://openai.github.io/openai-agents-python/tools/): when the tool raises, the SDK calls
``failure_error_function(context, error)`` and sends the returned string to the model. This module
returns what the SDK would have sent (``default_tool_error_function``, or the function you pass as
``base``) and, when the failure is a block or an outage on a web host, one more line.

* The original message is kept as it is. The SDK's default does not show the model the exception text;
  that stays so. To show it, pass a ``base`` that does.
* Not a block or an outage (a 404, a bad request, no URL), an unreachable service or a lookup slower than
  ``timeout`` (2 s): the result is exactly what ``base`` returns.
* The URL is read from the tool's arguments (``context.tool_arguments``) or from the exception. Only the
  hostname and two enum codes leave the machine.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from ._adapter import DEFAULT_TIMEOUT, anote_for, note_for

ADAPTER = "openai-agents"
FALLBACK_MESSAGE = "An error occurred while running the tool. Please try again."  # the SDK's own default text

__all__ = ["ADAPTER", "ahn_failure_error_function", "with_ahn_note"]


def _default_base() -> Callable[[Any, Exception], Any]:
    try:
        from agents import default_tool_error_function

        return default_tool_error_function
    except Exception:
        return lambda context, error: FALLBACK_MESSAGE


def _arguments(context: Any) -> Any:
    return getattr(context, "tool_arguments", None)  # the raw JSON string of a ToolContext; absent on a plain RunContextWrapper


def _join(message: str, note: Optional[str]) -> str:
    return f"{message}\n{note}" if note else message


def with_ahn_note(base: Optional[Callable[[Any, Exception], Any]] = None, *, asynchronous: bool = False,
                  timeout: float = DEFAULT_TIMEOUT) -> Callable[[Any, Exception], Any]:
    """A ``failure_error_function`` that returns ``base(context, error)`` plus an Agent Help Network line.

    ``base`` defaults to the SDK's ``default_tool_error_function``. With ``asynchronous=False`` (the
    documented form) the function is plain and waits at most ``timeout`` seconds, only when the failure is
    a block or an outage that is not cached. With ``asynchronous=True`` it is a coroutine function, so the
    wait never blocks the event loop; SDK versions whose ``ToolErrorFunction`` accepts an awaitable (the
    0.24 line does) await it, and ``base`` may then be async as well.
    """
    chosen = base if base is not None else _default_base()

    if asynchronous:
        async def failure_error_function(context: Any, error: Exception) -> str:
            message = chosen(context, error)
            if inspect.isawaitable(message):
                message = await message
            if not isinstance(message, str):
                return message
            return _join(message, await anote_for(error, _arguments(context), adapter=ADAPTER, timeout=timeout))
    else:
        def failure_error_function(context: Any, error: Exception) -> str:
            message = chosen(context, error)
            if not isinstance(message, str):  # an awaitable from an async base: pass it through untouched
                return message
            return _join(message, note_for(error, _arguments(context), adapter=ADAPTER, timeout=timeout))

    return failure_error_function


ahn_failure_error_function = with_ahn_note()
