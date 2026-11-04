"""One log line per HTTP request: whether a request reached the service, and what it got back.

    request method=GET path=/v1/incidents/{incident_id} status=404 ms=3 ua=<first 120 characters of the User-Agent, or ->

A pure ASGI layer. It is added last in create_app(), so it wraps every other layer, the HEAD body strip included; the
MCP mount that combined.py adds afterwards sits inside it. It never buffers a body. One request gives one line,
written when the request ends (a normal end, an exception, or a client that went away), so the line can say how it ended:

    method    the request method
    path      the path without its query string, printable ASCII only, at most 200 characters. When the request matched a
              route, the route's own pattern is logged, with the variable segments (an ID, a shard, a handle) as {name} and
              the language segment ({lang}) as sent. A request that matched no route (a 404, the MCP mount, /static) is
              logged exactly as sent, cleaned and cut.
    status    the status code of the response start; `error` when the application raised before any response, `none` when
              it ended without one
    ms        time from the request to the first response byte
    aborted=1 only when the application raised, or the connection was cancelled, after the response had started
    hop=loopback  only when the request carries the X-AHN-MCP-Call header: the MCP adapter's call to this service (the
              header is only checked for presence here; a client can send it, and then only the label changes)
    ua        the first 120 characters of the User-Agent, or `-`; always the last field

It never logs the client address, the query string, any other header or any body. Lifespan and websocket scopes pass
through untouched. AHN_REQUEST_LOG=0 turns it off.
"""

from __future__ import annotations

import logging
import sys
from time import perf_counter

from starlette.routing import Route

LOGGER_NAME = "ahn.request"
PATH_LIMIT = 200
UA_LIMIT = 120

logger = logging.getLogger(LOGGER_NAME)


class StdoutHandler(logging.StreamHandler):
    """Writes to whatever sys.stdout is at the moment of each line (the process stream on Render)."""

    def __init__(self):
        logging.Handler.__init__(self)

    @property
    def stream(self):
        return sys.stdout


def configure_logger() -> logging.Logger:
    """Send this logger alone to stdout (the hosting provider's application log), whatever the root logger does."""
    if not any(getattr(handler, "_ahn_request_log", False) for handler in logger.handlers):
        handler = StdoutHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._ahn_request_log = True
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # the root logger may carry its own handler; one line must not be written twice
    return logger


def printable(value: str, limit: int) -> str:
    """Printable ASCII only (a decoded path or header can hold a newline or a control character), cut to `limit`."""
    return "".join(char if "!" <= char <= "~" else ("_" if char == " " else "?") for char in value[:limit])


def header(scope, name: bytes):
    for key, value in scope.get("headers") or ():
        if key == name:
            return value
    return None


def user_agent(scope) -> str:
    value = header(scope, b"user-agent")
    if value is None:
        return "-"
    text = value.decode("latin-1")[:UA_LIMIT]
    return "".join(char if " " <= char <= "~" else "?" for char in text).strip() or "-"


def logged_path(scope, path: str) -> str:
    """The matched route's pattern with {lang} filled in as sent, or the cleaned path as sent when no route matched."""
    route = scope.get("route")
    if isinstance(route, Route):  # not a Mount (the MCP mount, /static): those are logged as sent
        template = route.path_format
        for name, value in (scope.get("path_params") or {}).items():
            if name == "lang":
                template = template.replace("{lang}", printable(str(value), PATH_LIMIT), 1)
        return printable(template, PATH_LIMIT)
    return printable(path, PATH_LIMIT)


class RequestLog:
    def __init__(self, app):
        self.app = app
        configure_logger()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = perf_counter()
        method = printable(scope.get("method", "-"), 16)
        path = scope.get("path", "")
        ua = user_agent(scope)
        hop = " hop=loopback" if header(scope, b"x-ahn-mcp-call") is not None else ""
        status = None
        first_byte_ms = 0.0
        raised = False

        async def send_logged(message):
            nonlocal status, first_byte_ms
            if message["type"] == "http.response.start" and status is None:
                status = message["status"]
                first_byte_ms = (perf_counter() - started) * 1000
            await send(message)

        try:
            await self.app(scope, receive, send_logged)
        except BaseException:  # includes a cancelled request; it is raised again below
            raised = True
            raise
        finally:
            try:
                shown = status if status is not None else ("error" if raised else "none")
                ms = first_byte_ms if status is not None else (perf_counter() - started) * 1000
                aborted = " aborted=1" if raised and status is not None else ""
                logger.info("request method=%s path=%s status=%s ms=%d%s%s ua=%s", method, logged_path(scope, path), shown, ms, aborted, hop, ua)
            except Exception:  # a logging fault must never change an answer
                pass
