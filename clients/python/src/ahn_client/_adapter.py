"""What every framework adapter shares: from a failed tool call to one line of text, or to nothing.

An adapter hands this module the exception or message of a failed tool call and the tool's
arguments. The hostname is read from the arguments (an http(s) URL) or from the error, the
error is classified with ``classify`` (a 404 or a bad request is not looked up), and ``check``
asks Agent Help Network. Only the hostname and two enum codes leave the machine.

Nothing here raises. A lookup that fails or is slow gives None, after at most ``timeout``
seconds; the adapter then leaves the agent's run exactly as it was.
"""

from __future__ import annotations

import asyncio
import functools
import json
import threading
from typing import Any, Mapping, Optional, Tuple

from ._classify import URL_IN_TEXT, _chain, classify
from ._client import check
from ._hosts import normalize_host
from ._result import Result

DEFAULT_TIMEOUT = 2.0  # seconds for the whole lookup, DNS included; a miss costs the agent at most this
# Argument names that carry the page a tool acted on (the same list as the Claude Code plugin's hook).
URL_FIELDS = ("url", "uri", "href", "link", "page_url", "pageUrl", "target_url", "targetUrl", "website_url", "websiteUrl")
HTTP_URL = ("http://", "https://")
MAX_ARG_VALUES = 32
MAX_TEXT = 20_000
WORKER_NAME = "ahn-adapter-lookup"

_inflight = set()
_inflight_lock = threading.Lock()


def _http_url_host(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value[:8].lower().startswith(HTTP_URL) or any(ch.isspace() for ch in value):
        return None
    return normalize_host(value)


def host_from_args(args: Any) -> Optional[str]:
    """The hostname of the http(s) URL a tool was called with, or None.

    ``args`` is a dict, or a JSON object as a string. A field named like a URL is read first; then any
    other string argument that is, as a whole, an http(s) URL. Nothing else is read, and only the
    hostname is kept.
    """
    if isinstance(args, (str, bytes)):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    if not isinstance(args, Mapping):
        return None
    for name in URL_FIELDS:
        host = _http_url_host(args.get(name))
        if host:
            return host
    for index, value in enumerate(args.values()):
        if index >= MAX_ARG_VALUES:
            break
        host = _http_url_host(value)
        if host:
            return host
    return None


def _url_attribute_hosts(error: BaseException):
    """Hostnames of the request that failed, from the attributes httpx, requests and aiohttp set on their errors."""
    for holder in _chain(error):
        response = getattr(holder, "response", None)
        for candidate in (getattr(holder, "request", None), response, getattr(response, "request", None),
                          getattr(holder, "request_info", None)):
            url = getattr(candidate, "url", None)
            if url is not None:
                host = _http_url_host(str(url))
                if host:
                    yield host


def host_from_error(error: Any) -> Optional[str]:
    """The hostname of the first http(s) URL an error names: its request, then its message."""
    if isinstance(error, BaseException):
        host = next(_url_attribute_hosts(error), None)
        if host:
            return host
        text = " ".join(str(item) for item in _chain(error))
    elif isinstance(error, bytes):
        text = error.decode("utf-8", errors="replace")
    else:
        text = error if isinstance(error, str) else ""
    for match in URL_IN_TEXT.finditer(text[:MAX_TEXT]):
        host = _http_url_host(match.group(0).rstrip(".,;:!?"))
        if host:
            return host
    return None


def failure_target(error: Any, args: Any = None) -> Optional[Tuple[str, str, Optional[str]]]:
    """``(host, error_class, failure_stage)`` for a failed tool call, or None when there is nothing to look up.

    None: no http(s) hostname in the arguments or the error, or the error is neither a block nor an outage
    (404, 410, 422 and the like).
    """
    host = host_from_args(args) or host_from_error(error)
    if not host:
        return None
    error_class, stage = classify(error, host)
    if not error_class:
        return None
    return host, error_class, stage


def bounded_check(host: str, error_class: str, stage: Optional[str], adapter: str,
                  timeout: float = DEFAULT_TIMEOUT) -> Optional[Result]:
    """``check`` in a daemon thread, abandoned after ``timeout`` seconds; None when it did not finish.

    A second lookup for the same host, class and stage while one is still running gets None at once, so a
    stuck network never piles up threads.
    """
    key = (host, error_class, stage)
    with _inflight_lock:
        if key in _inflight:
            return None
        _inflight.add(key)
    box = {}

    def work():
        try:
            box["result"] = check(host, error_class, stage, timeout=timeout, adapter=adapter)
        except BaseException:
            pass
        finally:
            with _inflight_lock:
                _inflight.discard(key)

    try:
        worker = threading.Thread(target=work, name=WORKER_NAME, daemon=True)
        worker.start()
        worker.join(timeout)
    except BaseException:
        with _inflight_lock:
            _inflight.discard(key)
        return None
    return None if worker.is_alive() else box.get("result")


def note_for(error: Any, args: Any = None, *, adapter: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[str]:
    """One line from Agent Help Network for a failed web tool call, or None. Never raises, never waits past ``timeout``."""
    try:
        target = failure_target(error, args)
        if target is None:
            return None
        result = bounded_check(*target, adapter, timeout)
        return result.summary() if result else None
    except Exception:
        return None


async def anote_for(error: Any, args: Any = None, *, adapter: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[str]:
    """``note_for`` for async code: the wait happens in a worker thread, never on the event loop."""
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(note_for, error, args, adapter=adapter, timeout=timeout))
    except Exception:
        return None
