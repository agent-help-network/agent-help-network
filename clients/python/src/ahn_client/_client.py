"""The lookup call: one GET with a hostname and two enum codes, never an exception."""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Dict, Optional, Tuple

from ._classify import ERROR_CLASSES, FAILURE_STAGES
from ._hosts import eligible_host
from ._result import Result

__all__ = ["check", "clear_cache", "lookup_url"]

API_URL = "https://agenthelpnetwork.com/v1/incidents/check"
USER_AGENT = "ahn-client/0.2 (+https://agenthelpnetwork.com)"
CACHE_TTL_SECONDS = 600
FAILURE_TTL_SECONDS = 60
CACHE_MAX_ENTRIES = 200
MAX_RESPONSE_BYTES = 262_144

_cache: Dict[Tuple[str, Optional[str], Optional[str]], Tuple[float, Optional[Result]]] = {}
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


def lookup_url(host: str, error_class: Optional[str], failure_stage: Optional[str]) -> str:
    query = {"site_host": host}
    if error_class:
        query["error_class"] = error_class
    if failure_stage:
        query["failure_stage"] = failure_stage
    return API_URL + "?" + urllib.parse.urlencode(query)


def _fetch(host: str, error_class: Optional[str], failure_stage: Optional[str], timeout: float) -> Optional[Result]:
    url = lookup_url(host, error_class, failure_stage)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
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
          timeout: float = 4.0) -> Optional[Result]:
    """Ask Agent Help Network about the host of ``host_or_url``; None when there is no answer.

    Only the bare hostname, ``error_class`` and ``failure_stage`` are sent: scheme,
    credentials, port, path, query and fragment are dropped first. Nothing is requested
    (None comes back) for local, private, special-use, IP-literal, single-label hosts,
    for agenthelpnetwork.com itself, for hosts listed in ``AHN_CLIENT_SKIP_HOSTS``, when
    ``AHN_CLIENT_DISABLE=1``, or when ``error_class`` / ``failure_stage`` is not one of
    the service's values. Network errors, timeouts and unreadable answers also give None;
    this function does not raise. Answers are cached in memory for 10 minutes per host,
    error class and stage (a failed lookup for 1 minute, and an answer whose declared policy is
    still being read only for its ``retry_after_seconds``).
    """
    try:
        if error_class is not None and error_class not in ERROR_CLASSES:
            return None
        if failure_stage is not None and failure_stage not in FAILURE_STAGES:
            return None
        host = eligible_host(host_or_url)
        if host is None:
            return None
        key = (host, error_class, failure_stage)
        now = time.monotonic()
        with _lock:
            hit = _cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
        result = _fetch(host, error_class, failure_stage, timeout)
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
