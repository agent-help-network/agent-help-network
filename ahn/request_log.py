"""One log line per HTTP request: whether a request reached the service, and what it got back.

    request method=GET path=/v1/incidents/{incident_id} status=404 ms=3 ua=<first 120 characters of the User-Agent, or ->

A pure ASGI layer, the outermost one: it wraps the whole application, writes the line when the response starts, and
never buffers a body. It logs the method, the path without its query string (at most 200 characters, and a value matched by a variable
segment such as an ID replaced by its name), the status, the time to the first response
byte and the first 120 characters of the User-Agent. It never logs the client address, the query string, any other
header or any body. Lifespan and websocket scopes pass through untouched. AHN_REQUEST_LOG=0 turns it off.
"""

from __future__ import annotations

import logging
import sys
from time import perf_counter

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


def user_agent(scope) -> str:
    for name, value in scope.get("headers") or ():
        if name == b"user-agent":
            text = value.decode("latin-1")[:UA_LIMIT]
            return "".join(char if " " <= char <= "~" else "?" for char in text).strip() or "-"
    return "-"


def logged_path(scope, path: str) -> str:
    """The path as requested, except that a value matched by a variable segment (an ID) is replaced by its `{name}`.
    The language segment stays: it is one of two codes when it matches, and a path nobody serves is what we want to see."""
    for name, value in (scope.get("path_params") or {}).items():
        if name != "lang" and isinstance(value, str) and value:
            path = path.replace(value, "{" + name + "}", 1)
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
        written = False

        def write(status):
            nonlocal written
            written = True
            try:
                logger.info("request method=%s path=%s status=%s ms=%d ua=%s", method, logged_path(scope, path), status, (perf_counter() - started) * 1000, ua)
            except Exception:  # a logging fault must never change an answer
                pass

        async def send_logged(message):
            if message["type"] == "http.response.start" and not written:
                write(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_logged)
        except BaseException:
            if not written:
                write("error")
            raise
