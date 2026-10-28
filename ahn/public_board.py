"""Public "Site problems right now" board (docs/decisions.md D53): ``/v1/now`` and ``/{lang}/now``.

Off by default. With ``AHN_PUBLIC_BOARD`` unset the paths are never registered (404), the policy text is the one
before D53, and nothing here runs. The board is built from ``signal_history.board_summary``, the same numbers the
owner sees, and adds no request to any site and no row to any table: it reads the database only, and at most once
per CACHE_SECONDS per web process.

What it lists (D-1020-9):

* Provider statements: curated hosts whose own status page currently makes the lookup answer wait, and only while
  that statement carries an update time no older than a week (``signal_history.board_provider``). The word "outage"
  appears only as the provider's own indicator, quoted.
* AHN check (robots.txt reads): a separate switch, ``AHN_PUBLIC_BOARD_AHN_CHECK``, off by default. Off, AHN-check-only
  hosts are not listed, no AHN-check field appears, and the 7-day numbers leave the AHN-check counts out.
* Never: refused reads (401, 403, 429, 503), reports, lookup counts, or who looked a host up.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import Request
from fastapi.responses import Response

SCHEMA = "ahn-now/1"
CACHE_SECONDS = 60
NEXT_UPDATE = timedelta(minutes=5)  # feeds are read by the 5-minute cron; asking sooner returns the same list
LIST_MAX = 20
INCIDENT_INDICATORS = ("critical", "major", "minor")
PATH = "/v1/now"
LEGEND = {
    "provider": "The provider's own status page, quoted with its source and update time. Listed only while that "
                "statement would make an AHN lookup answer wait and was updated by the provider within 7 days. A line with "
                "source_kind history_atom and indicator null is an open incident announced in the page's public incident "
                "history: the provider gives no severity level there, and none is made up.",
    "ahn_check": "AHN's own robots.txt reads from one datacenter location, updated only when a read happens; not a "
                 "report from agents and not a verdict on the site.",
    "not_listed": "Refused reads (401, 403, 429, 503), agent reports and lookup counts are never listed here.",
}
NOT_LISTED_NOTE = "A host missing from this list is not known to be working."


POLICY_BEFORE = {
    "en": "For now only the operator sees them, on a private dashboard that shows the hostnames of current problem "
          "candidates and aggregate counts; they are not shown on public pages or in lookup answers.",
    "ko": "지금은 운영자만, 현재 문제 후보의 호스트명과 집계를 보여 주는 비공개 화면에서 보며, 공개 페이지나 조회 응답에는 나오지 않습니다.",
}


def policy_sentences(ahn_check: bool) -> dict[str, tuple[str, str]]:
    """The policy sentence this switch replaces, per language, and its replacement (checked by test_policy_coverage)."""
    if ahn_check:
        en = ("The operator sees them on a private dashboard. A public page (“Site problems right now”, /en/now, /ko/now "
              "and /v1/now) also shows, for curated hosts only, the hosts whose robots.txt we repeatedly could not read "
              "(a DNS, connection, TLS, timeout or server (5xx) error) from one datacenter location, labelled as AHN's own "
              "check, with the time of the last read, next to the provider statements described under provider status, and "
              "7-day totals that name no host. Refused reads are never shown there. Lookup answers do not show this history.")
        ko = ("운영자는 비공개 화면에서 봅니다. 공개 페이지(“지금 문제가 있는 사이트”, /en/now, /ko/now, /v1/now)에는 큐레이션 "
              "호스트에 한해, 데이터센터 한 곳에서 robots.txt 를 거듭 읽지 못한(DNS·연결·TLS·시간 초과·서버 오류(5xx)) 호스트를 "
              "AHN 자체 점검으로 표시하고 마지막 읽기 시각과 함께, 업체 상태에서 설명한 업체 발표와 나란히, 호스트명 없는 7일 합계와 "
              "함께 보여 줍니다. 거절된 읽기는 그곳에 나오지 않습니다. 조회 응답에는 이 이력이 나오지 않습니다.")
    else:
        en = ("Only the operator sees them, on a private dashboard; they are not shown on public pages or in lookup answers. "
              "A public page (“Site problems right now”, /en/now, /ko/now and /v1/now) lists curated hosts whose connected "
              "provider status page currently reports a problem: the same provider statement a lookup answer shows, only "
              "while the provider updated it within the last 7 days, with 7-day totals that name no host. Where we read a "
              "page's public incident-history feed instead of its status API, a line says only that the provider announced "
              "an open incident there, with no severity level. It shows no "
              "robots.txt read result, no refused read and nothing about who looked a host up.")
        ko = ("운영자만 비공개 화면에서 보며, 공개 페이지나 조회 응답에는 나오지 않습니다. 공개 페이지(“지금 문제가 있는 사이트”, "
              "/en/now, /ko/now, /v1/now)는 연결된 업체 상태 페이지가 지금 문제를 발표한 큐레이션 호스트를 보여 줍니다. 조회 응답이 "
              "보여 주는 것과 같은 업체 발표이며, 업체가 최근 7일 안에 갱신한 동안만 올리고, 호스트명 없는 7일 합계를 함께 둡니다. "
              "상태 API 대신 공개 사건 이력 피드를 읽는 페이지는 업체가 그곳에 열린 사건을 발표했다는 것만 등급 없이 보여 줍니다. "
              "robots.txt 읽기 결과, 거절된 읽기, 누가 호스트를 조회했는지는 보여 주지 않습니다.")
    return {"en": (POLICY_BEFORE["en"], en), "ko": (POLICY_BEFORE["ko"], ko)}


def llms_section(ahn_check: bool) -> str:
    what = ("curated hosts whose own status page reports a problem (quoted, updated by the provider within 7 days), and "
            "hosts whose robots.txt AHN could not read (DNS, connection, TLS, timeout or 5xx) on repeated attempts from "
            "one datacenter; AHN checks are not reports from agents and not a verdict on the site"
            if ahn_check else
            "curated hosts whose own status page reports a problem, quoted with its source and update time, listed only "
            "while the provider updated it within 7 days (a line with `source_kind: history_atom` and `indicator: null` is an "
            "open incident from the page's public incident history, which gives no severity)")
    return ("## Site problems now\n\n"
            f"- `https://agenthelpnetwork.com{PATH}` (JSON, schema `{SCHEMA}`; do not fetch more often than "
            f"`next_update_after`): {what}. A host missing from the list is not known to be working. Human page: "
            "https://agenthelpnetwork.com/en/now\n\n")


def _provider(entry: dict) -> dict:
    out = {key: entry["provider"][key]
           for key in ("indicator", "basis", "source_kind", "active_incidents", "updated_at", "source_name", "source_url",
                       "source_updated_at", "fetched_at")
           if entry["provider"].get(key) is not None}
    if entry["provider"].get("source_kind") == "history_atom":
        # D57: an open incident announced in the page's public incident history, which gives no level: say so with an
        # explicit null (as the lookup answer does, D52) instead of leaving a reader to guess one.
        out = {"indicator": None, **out}
    return out


def snapshot(board: dict, *, ahn_check: bool, now: datetime) -> dict:
    """The public JSON from the owner board numbers. Only fields named here leave the owner view."""
    from .signal_history import _iso

    problems, maintenance = [], []
    # Without the AHN-check column the board reads every provider line (uncut), so AHN-check-only candidates neither
    # push provider lines out of the owner list's cut nor show through `truncated`.
    for entry in (board["candidates"] if ahn_check else board["provider_candidates"]):
        provider = entry.get("provider")
        indicator = provider["indicator"] if provider else None
        if provider and provider.get("basis") == "maintenance":
            indicator = "maintenance"  # a page indicator raised by maintenance only (D43 basis) is not a problem line
        if provider is None and not (ahn_check and entry.get("ahn_check")):
            continue  # an AHN-check-only line while that column is off
        line = {"site_host": entry["site_host"]}
        if provider is not None:
            line["provider"] = _provider(entry)
        if ahn_check and entry.get("ahn_check"):
            check = entry["ahn_check"]
            line["ahn_check"] = {"result": check["kind"], "since": check["since"], "last_read_at": check["last_read_at"],
                                 "failed_reads": check["failed_reads"]}
        (maintenance if indicator == "maintenance" else problems).append(line)
    coverage = {"provider_feeds_read": board["coverage"]["registered_feeds_curated"],
                "feed_scope": "looked_up_hosts_only", "curated_hosts": board["coverage"]["target_hosts"]}
    if ahn_check:
        coverage["ahn_check_vantage"] = {"kind": "datacenter", "count": 1}
    stats = {"days": board["days"], "provider_periods": board["p_episodes"],
             # Fresh provider problem statements left off the list right now, by reason (no host named).
             "provider_left_out_now": board["provider_excluded_now"]}
    if ahn_check:
        stats.update({key: board[key] for key in ("a_episodes", "a_matched_feed", "a_ahead_of_feed", "a_likely_false_positive")})
    legend = {key: text for key, text in LEGEND.items() if ahn_check or key != "ahn_check"}
    body = {
        "schema": SCHEMA, "generated_at": _iso(now), "next_update_after": _iso(now + NEXT_UPDATE),
        "coverage": coverage, "legend": legend,
        "problems": problems[:LIST_MAX], "maintenance": maintenance[:LIST_MAX],
        "truncated": len(problems) > LIST_MAX or len(maintenance) > LIST_MAX or (ahn_check and board["truncated"]),
        "stats_7d": stats, "note": NOT_LISTED_NOTE,
    }
    if ahn_check:
        body["checks_paused"] = board["held_now"]
    return body


class _Cache:
    def __init__(self):
        self.lock = threading.Lock()
        self.value: tuple[float, dict, bytes, str] | None = None

    def get(self, settings, clock) -> tuple[dict, bytes, str]:
        with self.lock:
            if self.value is not None and time.monotonic() - self.value[0] < CACHE_SECONDS:
                return self.value[1:]
            from .db import connect
            from .signal_history import board_summary

            now = clock()
            with connect(settings) as conn:
                board = board_summary(conn, now=now, days=7)
            data = snapshot(board, ahn_check=settings.public_board_ahn_check, now=now)
            body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
            self.value = (time.monotonic(), data, body, etag)
            return data, body, etag


def add_routes(app, settings, render, lang_text, *, clock=lambda: datetime.now(timezone.utc)) -> None:
    """Register /v1/now and /{lang}/now. Called only while AHN_PUBLIC_BOARD=1; otherwise they do not exist (404)."""
    cache = _Cache()
    headers = {"Cache-Control": f"public, max-age={CACHE_SECONDS}", "X-Content-Type-Options": "nosniff"}

    @app.get(PATH, include_in_schema=False)
    def now_json(request: Request):
        _data, body, etag = cache.get(settings, clock)
        tags = {tag.strip().removeprefix("W/") for tag in request.headers.get("if-none-match", "").split(",")}
        if etag in tags:
            return Response(status_code=304, headers={**headers, "ETag": etag})
        return Response(body, media_type="application/json", headers={**headers, "ETag": etag})

    @app.get("/{lang}/now", include_in_schema=False)
    def now_page(request: Request, lang: str):
        from .service import ServiceError

        if lang not in lang_text:
            raise ServiceError("not_found", 404)
        data, _body, _etag = cache.get(settings, clock)
        response = render(request, lang, "now", board=data, ahn_check=settings.public_board_ahn_check)
        response.headers["Cache-Control"] = headers["Cache-Control"]
        return response
