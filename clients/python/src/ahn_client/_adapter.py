"""What every framework adapter shares: from a failed tool call to one line of text, or to nothing.

An adapter hands this module the exception or message of a failed tool call and the tool's
arguments. The hostname is read from the arguments (an http(s) URL) or from the error's request,
the error is classified with ``classify`` (a 404 or a bad request is not looked up), and ``check``
asks Agent Help Network. Only the hostname and enum codes leave the machine.

Nothing here raises (``KeyboardInterrupt`` and other non-``Exception`` signals are not swallowed).
A lookup that fails or is slow gives None, after at most ``timeout`` seconds; the adapter then
leaves the agent's run exactly as it was.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from ._classify import CONNECT_NAMES, _chain, blank_host, classify, signals, without_urls
from ._client import check
from ._hosts import normalize_host
from ._result import Result

DEFAULT_TIMEOUT = 2.0  # seconds for the whole lookup, DNS included; a miss costs the agent at most this
# Argument names that carry the page a tool acted on (the same list as the Claude Code plugin's hook).
URL_FIELDS = ("url", "uri", "href", "link", "page_url", "pageUrl", "target_url", "targetUrl", "website_url", "websiteUrl")
HTTP_URL = ("http://", "https://")
MAX_ARG_VALUES = 32
MAX_TEXT = 20_000  # how much of an error text is read for wording
WORKER_NAME = "ahn-adapter-lookup"

# Failures of the tool's own arguments (a framework validating them before the tool runs): never a web failure.
VALIDATION_CLASSES = frozenset({"toolinvocationerror", "validationerror"})
VALIDATION_TEXT = re.compile(r"validation errors? for|error invoking tool|errors\.pydantic\.dev|field required", re.I)
# What a web failure says in words. A loose word alone ("Permission denied", "Task blocked on mutex", "password reset") is not
# enough: with a URL argument present those would be read as a block. HTTP status codes, reason phrases, challenge wording and
# the network layer's own error names are.
WEB_EVIDENCE = re.compile(
    r"\b(?:40[0-9]|41[0-9]|42[0-9]|43[0-9]|45[0-9]|5[0-9]{2})\b|forbidden|unauthori[sz]ed|payment required|too many requests"
    r"|rate[ -]?limit|service unavailable|bad gateway|gateway time-?out|access denied|captcha|verify (?:that )?you are"
    r"|are you a robot|challenge|just a moment|checking your browser|net::ERR_|ENOTFOUND|ECONNREFUSED|ECONNRESET|ETIMEDOUT"
    r"|getaddrinfo|connection (?:refused|reset|aborted)|could not resolve|name or service not known|timed? ?out|robots\.txt"
    r"|unable to fetch|blocked by (?:the )?(?:site|server|robots|network|firewall|waf|cloudflare)|certificate|\bssl\b"
    # Wording of block pages and login walls that has no status code in it. These phrases are web context by themselves; the
    # loose words they contain (blocked, denied, log in) are not.
    r"|cloudflare|\bray id\b|attention required|security system|perimeterx|datadome|akamai|imperva|incapsula|distil networks"
    r"|(?:you have|you['\u2019]ve|your request has) been (?:blocked|denied)|request (?:has been )?blocked|requests? (?:was|were) blocked"
    r"|access to this (?:page|site|website|resource|content) (?:has been |is )?(?:denied|blocked|restricted)|access (?:has been )?restricted"
    r"|unusual traffic|automated (?:access|requests?|traffic|queries)|bot (?:detection|protection|traffic)"
    r"|(?:log|sign)[ -]?in (?:is )?required|(?:log|sign)[ -]?in to (?:view|continue|access|see|read|download)"
    r"|you must (?:be )?(?:logged|signed)[ -]?in|you must (?:log|sign)[ -]?in|please (?:log|sign)[ -]?in|authentication required",
    re.I)
HTTP_ATTRIBUTES = ("response", "request", "request_info", "status", "status_code")

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


def _attribute(holder: Any, name: str) -> Any:
    """``getattr(holder, name, None)`` that also survives a property that raises: httpx's ``.request`` raises RuntimeError
    ("The .request property has not been set.") on an error built without a request, and a getattr default does not catch that."""
    try:
        return getattr(holder, name, None)
    except Exception:
        return None


def _url_attribute_hosts(error: BaseException):
    """Hostnames of the request that failed, from the attributes httpx, requests, aiohttp and urllib set on their errors."""
    for holder in _chain(error):
        response = _attribute(holder, "response")
        candidates = [_attribute(holder, "request"), response, _attribute(response, "request"), _attribute(holder, "request_info")]
        urls = [_attribute(candidate, "url") for candidate in candidates]
        urls.append(_attribute(holder, "url"))  # urllib.error.HTTPError
        for url in urls:
            if url is not None:
                host = _http_url_host(str(url))
                if host:
                    yield host


def host_from_error(error: Any) -> Optional[str]:
    """The hostname the error is about, or None.

    Only an exception that carries its request tells: the URL of ``.request``, ``.response``, ``.request_info`` or ``.url``.
    The text of an error is never searched for URLs: a message also links to help pages (errors.pydantic.dev,
    docs.mistral.ai, platform.openai.com) and to other hosts, and no list of those can be complete. An error without a
    request or a URL argument is not looked up.
    """
    if isinstance(error, BaseException):
        return next(_url_attribute_hosts(error), None)
    return None


def _is_validation_failure(error: Any) -> bool:
    if isinstance(error, BaseException):
        return any(type(item).__name__.lower() in VALIDATION_CLASSES or VALIDATION_TEXT.search(str(item)) for item in _chain(error))
    text = error.decode("utf-8", errors="replace") if isinstance(error, bytes) else error if isinstance(error, str) else ""
    return bool(VALIDATION_TEXT.search(text[:MAX_TEXT]))


def _web_evidence(error: Any, host: str) -> bool:
    """True when the error looks like it came from the web: an HTTP library's exception, a network error name, or its words."""
    if isinstance(error, BaseException):
        chain = _chain(error)
        if any(type(item).__name__.lower() in CONNECT_NAMES for item in chain):
            return True
        if any(_attribute(item, name) is not None for item in chain for name in HTTP_ATTRIBUTES):
            return True
        text = " ".join([type(item).__name__ for item in chain] + [str(item) for item in chain])
    else:
        text = error.decode("utf-8", errors="replace") if isinstance(error, bytes) else error if isinstance(error, str) else ""
    return bool(WEB_EVIDENCE.search(blank_host(without_urls(text[:MAX_TEXT]), host)))


def failure_target(error: Any, args: Any = None) -> Optional[Tuple[str, str, Optional[str]]]:
    """``(host, error_class, failure_stage)`` for a failed tool call, or None when there is nothing to look up.

    None: no http(s) hostname in the arguments or the error's request; a tool-argument validation failure; an error that is
    neither a block nor an outage (404, 410, 422 and the like); or an error with nothing web-like in it (a PermissionError, a
    task "blocked on a mutex") that merely happens to be raised by a tool that was given a URL.
    """
    host = host_from_args(args) or host_from_error(error)
    if not host or _is_validation_failure(error):
        return None
    error_class, stage = classify(error, host)
    if not error_class or not _web_evidence(error, host):
        return None
    return host, error_class, stage


def failure_signals(error: Any) -> Dict[str, Any]:
    """The optional lookup codes (``http_status``, ``challenge``, ``retry_after``, ``via``) the error already holds; empty when none."""
    try:
        return signals(error) if isinstance(error, BaseException) else {}
    except Exception:
        return {}


def _spawn(host: str, error_class: str, stage: Optional[str], adapter: str, timeout: float,
           done: Callable[[Optional[Result]], None], extra: Optional[Dict[str, Any]] = None) -> bool:
    """Start ``check`` in a daemon thread and call ``done(result)`` from it. False when none was started.

    A second lookup for the same host, class and stage while one still runs is not started, so a stuck network never
    piles up threads. ``done`` must not raise.
    """
    key = (host, error_class, stage, (extra or {}).get("task_step"))
    with _inflight_lock:
        if key in _inflight:
            return False
        _inflight.add(key)

    def work() -> None:
        result = None
        try:
            result = check(host, error_class, stage, timeout=timeout, adapter=adapter, **(extra or {}))
        except Exception:
            pass
        finally:
            with _inflight_lock:
                _inflight.discard(key)
        done(result)

    try:
        threading.Thread(target=work, name=WORKER_NAME, daemon=True).start()
    except BaseException as signal:
        with _inflight_lock:
            _inflight.discard(key)
        if isinstance(signal, Exception):
            return False
        raise  # KeyboardInterrupt, SystemExit and the like are not ours to swallow
    return True


def bounded_check(host: str, error_class: str, stage: Optional[str], adapter: str,
                  timeout: float = DEFAULT_TIMEOUT, extra: Optional[Dict[str, Any]] = None) -> Optional[Result]:
    """``check`` in a daemon thread, abandoned after ``timeout`` seconds; None when it did not finish."""
    box = {}
    finished = threading.Event()

    def done(result: Optional[Result]) -> None:
        box["result"] = result
        finished.set()

    if not _spawn(host, error_class, stage, adapter, timeout, done, extra):
        return None
    return box.get("result") if finished.wait(timeout) else None


async def abounded_check(host: str, error_class: str, stage: Optional[str], adapter: str,
                         timeout: float = DEFAULT_TIMEOUT, extra: Optional[Dict[str, Any]] = None) -> Optional[Result]:
    """``bounded_check`` for async code: the event loop is never blocked and ``timeout`` holds exactly (no executor queue)."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def deliver(result: Optional[Result]) -> None:
        if not future.done():
            future.set_result(result)

    def done(result: Optional[Result]) -> None:
        try:
            loop.call_soon_threadsafe(deliver, result)
        except RuntimeError:  # the loop is closed: nobody is waiting
            pass

    if not _spawn(host, error_class, stage, adapter, timeout, done, extra):
        return None
    try:
        return await asyncio.wait_for(future, timeout)
    except asyncio.TimeoutError:
        return None


def note_for(error: Any, args: Any = None, *, adapter: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[str]:
    """One line from Agent Help Network for a failed web tool call, or None. Never raises, never waits past ``timeout``."""
    try:
        target = failure_target(error, args)
        if target is None:
            return None
        codes = failure_signals(error)
        result = bounded_check(*target, adapter, timeout, codes or None)
        # The site's Retry-After band stays on this machine: it only lengthens the wait in the line, never the request.
        return result.summary(codes.get("retry_after")) if result else None
    except Exception:
        return None


async def anote_for(error: Any, args: Any = None, *, adapter: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[str]:
    """``note_for`` for async code: the wait never blocks the event loop and ends at ``timeout``."""
    try:
        target = failure_target(error, args)
        if target is None:
            return None
        codes = failure_signals(error)
        result = await abounded_check(*target, adapter, timeout, codes or None)
        return result.summary(codes.get("retry_after")) if result else None
    except Exception:
        return None
