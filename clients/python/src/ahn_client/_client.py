"""The lookup call: one GET with a hostname and enum codes, never an exception."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Dict, Optional, Tuple

from ._classify import ERROR_CLASSES, FAILURE_STAGES
from ._hosts import eligible_host
from ._result import Result
from ._signals import AGENT_VENDORS, valid_signals

__all__ = ["check", "clear_cache", "lookup_url"]

API_URL = "https://agenthelpnetwork.com/v1/incidents/check"
# The framework adapters name themselves in the User-Agent: "ahn-client/0.4.0 (+langchain; +https://agenthelpnetwork.com)".
ADAPTER_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")
CACHE_TTL_SECONDS = 600
FAILURE_TTL_SECONDS = 60
CACHE_MAX_ENTRIES = 200
MAX_RESPONSE_BYTES = 262_144

_cache: Dict[Tuple[str, Optional[str], Optional[str], Optional[str]], Tuple[float, Optional[Result]]] = {}
_lock = threading.Lock()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _urlopen(request, timeout):
    return _opener.open(request, timeout=timeout)


def clear_cache() -> None:
    """Forget every cached answer."""
    with _lock:
        _cache.clear()


def lookup_url(host: str, error_class: Optional[str], failure_stage: Optional[str],
               signals: Optional[Dict[str, object]] = None, agent_vendor: Optional[str] = None) -> str:
    query = {"site_host": host}
    if error_class:
        query["error_class"] = error_class
    if failure_stage:
        query["failure_stage"] = failure_stage
    query.update({name: str(value) for name, value in valid_signals(signals).items()})
    if agent_vendor in AGENT_VENDORS:  # not a signal code: it is neither counted nor stored, it only decides the answer
        query["agent_vendor"] = agent_vendor
    return API_URL + "?" + urllib.parse.urlencode(query)


def user_agent(adapter: Optional[str] = None) -> str:
    """The User-Agent for a lookup; ``adapter`` is the integration's name (a short lowercase label), or None."""
    from . import __version__  # Read when used: the package imports this module before it is complete.

    label = f"+{adapter}; " if isinstance(adapter, str) and ADAPTER_NAME.fullmatch(adapter) else ""
    return f"ahn-client/{__version__} ({label}+https://agenthelpnetwork.com)"


def _fetch(host: str, error_class: Optional[str], failure_stage: Optional[str], timeout: float,
           adapter: Optional[str] = None, signals: Optional[Dict[str, object]] = None,
           agent_vendor: Optional[str] = None) -> Optional[Result]:
    # The signal codes ride on the request only. The URL the Result keeps (the "Full JSON" link an agent sees) has none,
    # so following it counts nothing twice. ``agent_vendor`` is kept in it: it is not counted, and the link then gives
    # the answer the line shows.
    url = lookup_url(host, error_class, failure_stage, None, agent_vendor)
    request = urllib.request.Request(lookup_url(host, error_class, failure_stage, signals, agent_vendor), headers={"User-Agent": user_agent(adapter), "Accept": "application/json"})
    try:
        with _urlopen(request, timeout) as response:
            if getattr(response, "status", 200) != 200:
                return None
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            return None
        return Result.from_response(host, url, json.loads(body.decode("utf-8")))
    except Exception:  # Network, TLS, HTTP status, JSON: all mean "no answer", never an exception for the caller.
        return None


def check(host_or_url, error_class: Optional[str] = None, failure_stage: Optional[str] = None, *,
          timeout: float = 4.0, adapter: Optional[str] = None, http_status: Optional[int] = None,
          challenge: Optional[str] = None, retry_after: Optional[str] = None,
          via: Optional[str] = None, agent_vendor: Optional[str] = None) -> Optional[Result]:
    """Ask Agent Help Network about the host of ``host_or_url``; None when there is no answer.

    Only the bare hostname, ``error_class``, ``failure_stage`` and the four optional codes below are sent: scheme,
    credentials, port, path, query and fragment are dropped first. Nothing is requested
    (None comes back) for local, private, special-use, IP-literal, single-label hosts,
    for agenthelpnetwork.com itself, for hosts listed in ``AHN_CLIENT_SKIP_HOSTS``, when
    ``AHN_CLIENT_DISABLE=1``, or when ``error_class`` / ``failure_stage`` is not one of
    the service's values. Network errors, timeouts and unreadable answers also give None;
    this function does not raise, with one exception: a wrong ``agent_vendor`` raises ``ValueError`` (see below),
    also when ``AHN_CLIENT_DISABLE=1`` is set or the host is never looked up. Answers are cached in memory for 10 minutes per host,
    error class, stage and ``agent_vendor`` (a failed lookup for 1 minute, and an answer whose declared policy is
    still being read only for its ``retry_after_seconds``).

    ``adapter`` is for integrations: a short lowercase label such as ``"langchain"`` that is
    added to the User-Agent so the service can count client kinds. It changes nothing else.

    ``http_status`` (401, 402, 403, 407, 408, 429, 451 or 500-599), ``challenge`` (``header`` or ``none``),
    ``retry_after`` (``none``, ``lt_60``, ``60_3600`` or ``gt_3600``) and ``via`` (``http_fetch``, ``browser`` or
    ``unknown``) describe the site's answer; leave out what you do not know. ``ahn.signals(exc)`` fills them from
    an exception. A value outside these lists is dropped (the lookup still happens without it). They are counted
    per hostname and hour with the rest of the lookup and never become a report.

    ``agent_vendor`` is the vendor whose documented ``robots.txt`` token your own web requests send: one of
    ``openai``, ``anthropic``, ``google``, ``perplexity``, ``meta``, ``mistral``, ``amazon``, ``microsoft``, ``apple``,
    ``duckduckgo``, ``diffbot``, ``exa`` or ``other``. The service then judges the host's ``robots.txt`` for that
    vendor's tokens. It is the vendor of the fetcher, not of the model; pass it only when you know it and leave it out
    when unsure. Unlike the other optional codes it is checked here: any other value (including another case or
    surrounding spaces) raises ``ValueError`` before anything is sent. It is not counted or stored, and the
    verdict is the service's: this function decides nothing about robots.txt.
    """
    if agent_vendor is not None and (not isinstance(agent_vendor, str) or agent_vendor not in AGENT_VENDORS):
        raise ValueError("agent_vendor must be one of: " + ", ".join(AGENT_VENDORS))
    try:
        if error_class is not None and error_class not in ERROR_CLASSES:
            return None
        if failure_stage is not None and failure_stage not in FAILURE_STAGES:
            return None
        host = eligible_host(host_or_url)
        if host is None:
            return None
        key = (host, error_class, failure_stage, agent_vendor)
        extra = valid_signals({"http_status": http_status, "challenge": challenge, "retry_after": retry_after, "via": via})
        now = time.monotonic()
        with _lock:
            hit = _cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
        result = _fetch(host, error_class, failure_stage, timeout, adapter, extra, agent_vendor)
        now = time.monotonic()
        with _lock:
            for stale in [k for k, (expires, _) in _cache.items() if expires <= now]:
                del _cache[stale]
            if len(_cache) >= CACHE_MAX_ENTRIES:
                del _cache[min(_cache, key=lambda k: _cache[k][0])]
            ttl = CACHE_TTL_SECONDS if result else FAILURE_TTL_SECONDS
            if result is not None and result.retry_after_seconds is not None:
                # A "being read" answer is kept only until the service expects to have the files.
                ttl = min(ttl, result.retry_after_seconds)
            _cache[key] = (now + ttl, result)
        return result
    except Exception:
        return None
