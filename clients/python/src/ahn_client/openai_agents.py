"""OpenAI Agents SDK: add an Agent Help Network line to the message a failed web tool sends the model.

    pip install "ahn-client[openai-agents]"        # openai-agents 0.24 or newer

    from agents import RunConfig, Runner
    from ahn_client.openai_agents import ahn_tool_error_formatter

    result = await Runner.run(agent, "...", run_config=RunConfig(tool_error_formatter=ahn_tool_error_formatter))

``RunConfig.tool_error_formatter`` (https://openai.github.io/openai-agents-python/running_agents/) formats the
model-visible message of a tool error. The SDK calls it with ``ToolErrorFormatterArgs``; for a function tool that
raised, ``kind == "tool_exception"`` and ``error`` is the exception (the 0.24 source and its type annotations; the docs
page lists the approval and tool-not-found kinds). The formatter may be async, so the wait never blocks the event loop.

What it does, for ``kind == "tool_exception"`` when the failure is a block or an outage on a web host:

* The message the model gets is what it would have got (``default_message``, or what your own formatter returns when you
  pass it as ``base``) plus one line. The original exception text is not added: the SDK's default does not show it to
  the model, and that stays.
* The SDK keeps treating the failure as the default one, so traces still show the redacted error, not ``str(error)``.
* Other kinds, a failure that is not a block or an outage (404, a bad request, no URL), an unreachable service and a
  lookup slower than ``timeout`` (2 s) return exactly what ``base`` returns (or None).

The URL is read from the tool's arguments (``run_context.tool_arguments``) or from the exception's request. Only the
hostname and two enum codes leave the machine.

This formatter applies only to function tools that use the SDK's implicit default failure handler. A tool that sets its
own ``failure_error_function``, tools made by ``Agent.as_tool()`` and local MCP tools keep their own policies; for a tool that
sets one, wrap it with ``with_ahn_note(your_function)``. Be aware of what the SDK does then: with an explicit
``failure_error_function`` it no longer applies ``RunConfig.tool_error_formatter`` to that tool (so a formatter you set there
is ignored), and a handled error is recorded in the trace span with its text instead of "redacted".
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from ._adapter import DEFAULT_TIMEOUT, anote_for, note_for

ADAPTER = "openai-agents"
FALLBACK_MESSAGE = "An error occurred while running the tool. Please try again."  # the SDK's own default text

__all__ = ["ADAPTER", "ahn_failure_error_function", "ahn_tool_error_formatter", "tool_error_formatter", "with_ahn_note"]


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


def tool_error_formatter(base: Optional[Callable[[Any], Any]] = None, *, timeout: float = DEFAULT_TIMEOUT
                         ) -> Callable[[Any], Any]:
    """A ``RunConfig.tool_error_formatter`` that adds an Agent Help Network line to a failed web tool's message.

    ``base`` is the formatter you would have set (sync or async); its result is what the line is added to, and what is
    returned when there is no line. Without ``base`` the line is added to the SDK's ``default_message``.
    """

    async def formatter(args: Any) -> Optional[str]:
        message = None
        if base is not None:
            try:
                message = base(args)
                if inspect.isawaitable(message):
                    message = await message
            except Exception:
                message = None  # the SDK falls back to its default message when a formatter fails; so do we
        try:
            if getattr(args, "kind", None) != "tool_exception":
                return message
            text = message if isinstance(message, str) else getattr(args, "default_message", None)
            if not isinstance(text, str):
                return message
            note = await anote_for(getattr(args, "error", None), _arguments(getattr(args, "run_context", None)),
                                   adapter=ADAPTER, timeout=timeout)
            return _join(text, note) if note else message
        except Exception:
            return message

    return formatter


ahn_tool_error_formatter = tool_error_formatter()


def with_ahn_note(base: Optional[Callable[[Any, Exception], Any]] = None, *, asynchronous: bool = True,
                  timeout: float = DEFAULT_TIMEOUT) -> Callable[[Any, Exception], Any]:
    """A ``failure_error_function`` that returns ``base(context, error)`` plus an Agent Help Network line.

    For a tool that sets its own ``failure_error_function``
    (https://openai.github.io/openai-agents-python/tools/: ``(context, error) -> str``). ``base`` defaults to the SDK's
    ``default_tool_error_function``. By default the result is a coroutine function (the SDK awaits an awaitable result in
    0.24 and newer), so the wait never blocks the event loop and ``base`` may be async. ``asynchronous=False`` gives a plain
    function, the form the docs show; it waits up to ``timeout`` seconds on the event loop thread, and only when the failure
    is a block or an outage that is not cached.
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
