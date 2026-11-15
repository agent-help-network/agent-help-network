"""``task_routes``: read the task routes Agent Help Network records for a site (``GET /v1/task-routes``).

It only reads. A hostname and, optionally, one task kind leave the machine; a URL path, a query, a body and credentials
never do. The service answers with fixed values only (closed lists, no free text from a site), and this module keeps just
those fields. A deployment that does not serve the endpoint answers 404; a network error, a timeout and an unreadable
answer are handled the same way: the call never raises and returns ``available=False`` with no routes, so a missing
answer never stops the task it was meant to help.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import _client
from ._client import MAX_RESPONSE_BYTES, user_agent
from ._hosts import eligible_host

__all__ = ["TaskRoutes", "task_routes", "task_routes_url"]

API_URL = "https://agenthelpnetwork.com/v1/task-routes"
DEFAULT_TIMEOUT = 4.0
MAX_ROUTES = 20
TASK_KIND = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SUMMARY_LINE = re.compile(r"^[ -~]{1,200}$")
VALUE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
URL_VALUE = re.compile(r"^https://[^\s?#]{1,192}$")
DATE_VALUE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NOTICES = ("case_record_not_advice",)
# The fields of one route as the service sends them. Anything else is dropped.
ROUTE_VALUE_FIELDS = ("site_host", "task_kind", "obstacle", "verification_kind", "site_requires", "document_kind",
                      "entry_point_kind", "route_worked", "basis", "outcome", "duration", "period", "source_kind")
ROUTE_URL_FIELDS = ("official_url", "source_url")


@dataclass(frozen=True)
class TaskRoutes:
    """The answer of ``task_routes``. ``available`` is False when there is no usable answer; ``routes`` is then empty."""

    site_host: Optional[str]
    available: bool = False
    routes: List[Dict[str, Any]] = field(default_factory=list)
    summary_line: Optional[str] = None
    records: int = 0
    notice: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"site_host": self.site_host, "available": self.available, "routes": self.routes, "records": self.records}
        if self.summary_line:
            out["summary_line"] = self.summary_line
        if self.notice:
            out["notice"] = self.notice
        return out

    def summary(self) -> str:
        """One line for a log or a model: the service's own sentence, or a plain note that there is none."""
        if not self.available:
            return "No task routes available (no answer, or this service does not serve them)."
        return self.summary_line or f"{self.records} task route records."


def task_routes_url(host: str, task_kind: Optional[str] = None) -> str:
    query = {"site_host": host}
    if task_kind:
        query["task_kind"] = task_kind
    return API_URL + "?" + urllib.parse.urlencode(query)


def _clean_route(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    route: Dict[str, Any] = {}
    for name in ROUTE_VALUE_FIELDS:
        value = item.get(name)
        if value is None:
            continue
        if not isinstance(value, str) or not VALUE.fullmatch(value):
            return None
        route[name] = value
    for name in ROUTE_URL_FIELDS:
        value = item.get(name)
        if value is None:
            continue
        if not isinstance(value, str) or not URL_VALUE.fullmatch(value):
            return None
        route[name] = value
    verified = item.get("verified_at")
    if verified is not None:
        if not isinstance(verified, str) or not DATE_VALUE.fullmatch(verified):
            return None
        route["verified_at"] = verified
    tried = item.get("routes_tried", [])
    if not isinstance(tried, list) or len(tried) > 20 or not all(isinstance(v, str) and VALUE.fullmatch(v) for v in tried):
        return None
    route["routes_tried"] = list(tried)
    sensitive = item.get("sensitive")
    if not isinstance(sensitive, bool):
        return None
    route["sensitive"] = sensitive
    if "site_host" not in route or "task_kind" not in route:
        return None
    return route


def _parse(host: str, body: Any) -> Optional[TaskRoutes]:
    if not isinstance(body, dict) or not isinstance(body.get("routes"), list) or len(body["routes"]) > MAX_ROUTES:
        return None
    routes = [_clean_route(item) for item in body["routes"]]
    if any(route is None for route in routes):
        return None
    line = body.get("summary_line")
    line = line if isinstance(line, str) and SUMMARY_LINE.fullmatch(line) else None
    counts = body.get("counts")
    records = counts.get("records") if isinstance(counts, dict) else None
    if not isinstance(records, int) or isinstance(records, bool) or records < 0:
        records = len(routes)
    notice = body.get("notice")
    return TaskRoutes(site_host=host, available=True, routes=routes, summary_line=line, records=records,
                      notice=notice if notice in NOTICES else None)


def _fetch(host: str, task_kind: Optional[str], timeout: float, adapter: Optional[str]) -> Optional[TaskRoutes]:
    request = urllib.request.Request(task_routes_url(host, task_kind),
                                     headers={"User-Agent": user_agent(adapter), "Accept": "application/json"})
    try:
        with _client._urlopen(request, timeout) as response:
            if getattr(response, "status", 200) != 200:
                return None
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            return None
        return _parse(host, json.loads(body.decode("utf-8")))
    except Exception:  # 404 (switch off), network, TLS, HTTP status, JSON: all mean "no answer", never an exception.
        return None


def task_routes(host_or_url: object, task_kind: Optional[str] = None, *, timeout: float = DEFAULT_TIMEOUT,
                adapter: Optional[str] = None) -> TaskRoutes:
    """The task routes recorded for the site of a hostname or URL, optionally for one ``task_kind``.

    Never raises. Returns ``available=False`` and ``routes=[]`` when the host is never looked up (local, private, an address,
    this service's own, lookups disabled), when ``task_kind`` is not a plain lowercase word, on a 404 (the service does not
    serve task routes), a network error, a timeout beyond ``timeout`` seconds, or an answer that does not have the expected
    shape. A route is a dictionary of closed values (``task_kind``, ``obstacle``, ``basis``, ``outcome``, ``source_kind``,
    ``sensitive`` and the like); nothing in it is text from a site. A record is a case record, not advice for the next task.
    """
    try:
        host = eligible_host(host_or_url)
        if host is None:
            return TaskRoutes(site_host=None)
        if task_kind is not None and not (isinstance(task_kind, str) and TASK_KIND.fullmatch(task_kind)):
            return TaskRoutes(site_host=host)
        try:
            wait = float(timeout)
        except (TypeError, ValueError):
            wait = DEFAULT_TIMEOUT
        wait = min(max(wait, 0.1), 30.0)
        box: Dict[str, Optional[TaskRoutes]] = {}
        finished = threading.Event()

        def work() -> None:
            box["result"] = _fetch(host, task_kind, wait, adapter)
            finished.set()

        threading.Thread(target=work, name="ahn-client-task-routes", daemon=True).start()
        result = box.get("result") if finished.wait(wait) else None
        return result if result is not None else TaskRoutes(site_host=host)
    except Exception:
        return TaskRoutes(site_host=None)
