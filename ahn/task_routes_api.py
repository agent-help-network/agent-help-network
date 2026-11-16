"""Task routes API (D117, D118): ``GET /v1/task-routes``, and with the write switch ``POST /v1/task-routes`` and
``DELETE /v1/task-routes/{route_id}``.

Off by default. With ``AHN_TASK_ROUTES`` unset the path is never registered (404) and nothing here runs. The answer comes from
ahn.task_routes.lookup: the curated table in code, no database and no request to any site (D33). It is a separate layer from
the lookup verdict: nothing here is read by check(), and the verdict does not change when this is on.

Query: ``site_host`` (a public hostname, required) and ``task_kind`` (one value of ahn.task_routes.TaskKind, optional).
Errors are codes only and never echo the input: invalid_site_host (400), invalid_task_kind (400).

The write paths exist only while settings.task_route_reports_served (AHN_TASK_ROUTE_REPORTS=1 on top of AHN_TASK_ROUTES=1, with public
reporting on); otherwise they are 404. A report is the closed values of ahn.task_routes.TaskRouteIn as JSON. Any other field, including
the ones the server sets (source_kind, period, sensitive, review_state) and any link, is refused with 422 invalid_task_route; the
answer never repeats what was sent. With the switch on, answers and pages also carry the published stored records (ahn.task_route_intake).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from time import monotonic
from urllib.parse import urlencode

from uuid import UUID

from fastapi import Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import ValidationError

from .mcp_task_routes import CALL_TIMING, SITE_HOST_DESCRIPTION, TASK_KIND_DESCRIPTION
from .task_routes import (ROUTE_WORKED_NONE, DocumentKind, EntryPointKind, RouteBasis, RouteKind, RouteOutcome, RouteSourceKind,
                          SiteRequires, TaskKind, TaskObstacle, TaskRouteIn, TaskRoutesResponse, TaskRouteInputError, VerificationKind, is_current,
                          lookup, parse_query, registrable, route_host, server_route, site_from_input, site_records, stored_route,
                          suffix_name)

LOG = logging.getLogger(__name__)

PATH = "/v1/task-routes"
CACHE_CONTROL = "public, max-age=300"
OPENAPI_DESCRIPTION = ("Call this once per task " + CALL_TIMING + ". Look up recorded task routes for a public hostname: for a kind of customer task "
                       "(a refund, a cancellation, a booking and so on), an obstacle and the routes the site's own documents name. The answer is a list "
                       "of records, not a guarantee and not advice; an empty list does not mean there is no route. Sends only the hostname and an "
                       "optional task kind, and changes nothing. Read summary_line first. field_notes explains outcome, period and routes_tried. "
                       "The same lookup is the MCP tool find_task_route.")
REPORT_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def add_routes(app, settings, render=None, lang_text=None) -> None:
    """Register GET /v1/task-routes. Called only while AHN_TASK_ROUTES=1; otherwise the path does not exist (404). The write paths
    are registered too while settings.task_route_reports_served (D118).

    ``render`` and ``lang_text`` are the page helpers of the app; with both given, the two human pages are registered too."""
    from .service import ServiceError
    from .settings import task_route_reports_served

    headers = {"Cache-Control": CACHE_CONTROL, "X-Content-Type-Options": "nosniff"}

    # Only this read path is in the OpenAPI document (D129): it is registered only while this function runs, so with the switch off it is
    # not listed either. The write paths and the human pages stay out of the schema. The documented answer is the model the body is
    # built from; the body itself is still the JSONResponse below, so the bytes do not change.
    @app.get(PATH, summary="Call this once per task " + CALL_TIMING,
             description=OPENAPI_DESCRIPTION,
             responses={200: {"model": TaskRoutesResponse, "description": "The recorded task routes for the site: a list of records, not a guarantee and not advice."},
                        400: {"description": "An error code only: invalid_site_host or invalid_task_kind. The input is never echoed."}})
    def task_routes(request: Request,
                    site_host: str = Query("", description=SITE_HOST_DESCRIPTION + " Required: without a valid public hostname the answer is 400 invalid_site_host."),
                    task_kind: str | None = Query(None, description=TASK_KIND_DESCRIPTION + " Values: " + ", ".join(kind.value for kind in TaskKind) + ".")):
        try:
            host, kind = parse_query(site_host, task_kind)
        except TaskRouteInputError as error:
            raise ServiceError(error.code, 400) from None
        answer = lookup(host, kind, settings=settings)
        return JSONResponse(answer.model_dump(mode="json"), headers=headers)

    if task_route_reports_served(settings):
        add_write_routes(app, settings)
    else:  # the write paths do not exist: 404, as with the read switch off (not 405 because GET shares the path)
        @app.post(PATH, include_in_schema=False)
        def task_routes_write_off():
            raise ServiceError("not_found", 404)

        @app.delete(PATH + "/{route_id}", include_in_schema=False)
        def task_routes_withdraw_off(route_id: str):
            raise ServiceError("not_found", 404)
    if render is not None and lang_text is not None:
        add_pages(app, settings, render, lang_text)


def add_write_routes(app, settings) -> None:
    """POST /v1/task-routes and DELETE /v1/task-routes/{route_id} (D118). Called only while settings.task_route_reports_served."""
    from .rate_limit import RequestLimiter
    from .service import ServiceError
    from .task_route_intake import submit_task_route, withdraw_task_route
    from .tool_metrics import is_mcp_metric_request

    from .lookup_signals import ORIGIN_HEADER, clean_address, limiter_network

    def internal_hop(request: Request) -> bool:
        return bool(request.client and request.client.host in {"127.0.0.1", "::1"}
                    and is_mcp_metric_request(request.headers.get("x-ahn-mcp-call"), settings.key_pepper))

    class ReportLimiter(RequestLimiter):
        """The per-client report limit for both ways in. On the MCP adapter's loopback hop the client is the address the adapter
        passes on (ORIGIN_HEADER, honoured only on that authenticated hop); without one, every such report shares one slot set.
        An IPv6 client counts by its /64, as source_digest does, so one subscriber cannot take a fresh slot set per address;
        an IPv4 client counts by its address."""

        def client_key(self, request: Request) -> str:
            if internal_hop(request):
                origin = clean_address(request.headers.get(ORIGIN_HEADER, ""))
                return "mcp:" + (limiter_network(origin) if origin else "unknown")
            return limiter_network(RequestLimiter.client_key(request))

    # Its own limiter, as tight as the observation one: a few reports a minute per client, over REST and over MCP alike.
    limiter = ReportLimiter(per_client=3, total=20)

    @app.post(PATH, include_in_schema=False)
    async def report_task_route(request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return JSONResponse({"error": "json_required"}, status_code=415, headers=REPORT_HEADERS)
        if not await limiter.allow(request):
            return JSONResponse({"error": "report_rate_limited"}, status_code=429, headers={**REPORT_HEADERS, "Retry-After": "60"})
        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400, headers=REPORT_HEADERS)
        if not isinstance(payload, dict):
            return JSONResponse({"error": "invalid_task_route"}, status_code=422, headers=REPORT_HEADERS)
        try:
            data = TaskRouteIn.model_validate(payload)
        except ValidationError:  # unknown or server-set fields, values outside the lists, or a combination the rules refuse
            return JSONResponse({"error": "invalid_task_route"}, status_code=422, headers=REPORT_HEADERS)
        try:
            receipt, created = await run_in_threadpool(submit_task_route, settings, data)
        except ServiceError as error:
            extra = {"Retry-After": "3600"} if error.status == 429 else {}
            return JSONResponse({"error": error.code}, status_code=error.status, headers={**REPORT_HEADERS, **extra})
        return JSONResponse(receipt, status_code=201 if created else 200, headers=REPORT_HEADERS)

    @app.delete(PATH + "/{route_id}", include_in_schema=False)
    async def delete_task_route(request: Request, route_id: str):
        try:
            parsed = UUID(route_id)
        except ValueError:
            return JSONResponse({"error": "not_found"}, status_code=404, headers=REPORT_HEADERS)
        token = request.headers.get("x-withdrawal-token", "")
        try:
            result = await run_in_threadpool(withdraw_task_route, settings, parsed, token)
        except ServiceError as error:
            return JSONResponse({"error": error.code}, status_code=error.status, headers=REPORT_HEADERS)
        return JSONResponse(result, headers=REPORT_HEADERS)


# --- Human pages (PR-F): GET /{lang}/routes and /{lang}/routes/{host} ----------------------------------------------------
# Same flag as the API. The pages read the in-code table through task_routes.lookup / server_route and, while the write switch is
# served (D118), the published stored records: they never call check() or reach a site, and they show the server-decided values
# (sensitive, source_kind, period) as the server computed them.
PAGE_ROWS_MAX = 100
ROUTES_DATALIST_MAX = 300  # the host suggestions of the site box are listed only while the table holds at most this many sites
RECORDS_TTL = 60  # seconds the curated records are kept between requests
SOURCE_ORDER = (RouteSourceKind.operator_panel, RouteSourceKind.curated_official, RouteSourceKind.external_agent,
                RouteSourceKind.curated_community)
SOURCE_PHRASE = {
    "en": {"operator_panel": "AHN operator-side agents", "curated_official": "the sites' own documents",
           "external_agent": "agents reporting in", "curated_community": "community sources"},
    "ko": {"operator_panel": "운영 측 에이전트", "curated_official": "공식 문서",
           "external_agent": "외부 에이전트", "curated_community": "커뮤니티 자료"},
}
# English words are the value with underscores replaced, except where listed here; Korean words are listed for every closed value.
EN_LABELS = {
    "outcome": {"not_applicable": "not applicable (document record)"},
    "source_kind": {"operator_panel": "AHN operator-side agent", "curated_official": "site's own documents",
                    "external_agent": "reporting agent", "curated_community": "community source"},
    "basis": {"official_doc": "official document"},
    "entry_point_kind": {"seller_or_provider": "seller or provider listed on the site"},
}
KO_LABELS = {
    "task_kind": {
        "claim_unclaimed_property": "미청구 재산 찾기", "warranty_claim": "보증 수리 청구", "warranty_dispute": "보증 분쟁",
        "refund_request": "환불 요청", "return_or_exchange": "반품·교환", "cancel_subscription": "구독 해지",
        "account_closure": "계정 해지", "account_recovery": "계정 복구", "portal_login": "포털 로그인",
        "address_or_profile_change": "주소·프로필 변경", "appointment_booking": "예약", "appointment_change_or_cancel": "예약 변경·취소",
        "records_request": "기록 열람 요청", "complaint_filing": "민원 접수", "billing_dispute": "청구 이의",
        "fee_waiver_or_adjustment": "수수료 면제·조정", "insurance_claim": "보험금 청구",
        "government_benefit_application": "정부 혜택 신청", "tax_or_government_filing": "세금·정부 신고",
        "id_or_license_renewal": "신분증·면허 갱신", "accommodation_request": "편의 제공 요청", "prescription_refill": "처방 재조제",
        "document_submission": "서류 제출", "other": "기타"},
    "obstacle": {
        "document_missing": "서류 없음", "document_rejected": "서류 반려", "substitute_document_needed": "대체 서류 필요",
        "form_unclear": "양식이 불분명", "wrong_entry_point": "잘못된 진입점", "wrong_channel": "잘못된 창구",
        "wrong_department_or_jurisdiction": "담당 부서·관할 불일치", "eligibility_unclear": "자격이 불분명",
        "eligibility_conditions_stated": "자격·적용 대상 조건 명시",
        "verification_step": "인증 단계", "code_expired": "코드 만료", "deadline_or_window": "기한·신청 기간",
        "fee_required": "수수료 필요", "waiting_reply": "답변 대기", "no_reply": "답변 없음", "request_denied": "요청 거절",
        "site_requires_info_from_person": "사람이 제공할 정보 필요", "technical_block": "기술적 막힘", "none": "걸림돌 없음"},
    "verification_kind": {
        "sms_code": "문자 코드", "email_code": "이메일 코드", "app_approval": "앱 승인", "new_device": "새 기기 확인",
        "security_question": "보안 질문", "account_locked": "계정 잠금", "consent_screen": "동의 화면",
        "id_document_upload": "신분증 업로드"},
    "site_requires": {
        "referral_or_prescription": "의뢰서·처방전", "prior_approval_record": "사전 승인 기록",
        "account_holder_step": "계정 소유자가 직접 하는 단계", "original_document": "원본 서류", "preference_only": "선호 사항만"},
    "document_kind": {
        "proof_of_identity": "신원 증명", "proof_of_address": "주소 증명", "proof_of_ownership": "소유 증명",
        "proof_of_purchase": "구매 증명", "proof_of_authority": "권한 증명", "business_registration_or_closure": "사업자 등록·폐업 서류",
        "sworn_statement": "선서 진술서", "approval_or_authorization_letter": "승인·위임 서한", "professional_letter": "전문가 서한",
        "prior_correspondence": "이전 서신", "account_statement": "계정 명세서", "official_form": "공식 양식", "other": "기타"},
    "entry_point_kind": {
        "role_specific_portal": "역할별 포털", "regional_site": "지역 사이트", "separate_subdomain": "별도 하위 도메인",
        "mobile_app_only": "모바일 앱 전용", "partner_or_third_party_portal": "제휴·제3자 포털",
        "seller_or_provider": "사이트에 등록된 판매자·제공자"},
    "route_kind": {
        "support_chat": "지원 채팅", "phone": "전화", "email": "이메일", "web_form": "웹 양식", "office_visit": "방문",
        "mail_or_fax": "우편·팩스", "consumer_agency": "소비자 기관", "regulator": "규제 기관",
        "designated_dispute_program": "지정 분쟁 프로그램", "official_app": "공식 앱", "different_entry_point": "다른 진입점",
        "escalation_within_site": "사이트 내 상위 단계", "other": "기타"},
    "outcome": {"resolved": "해결", "progressed": "진전", "no_progress": "진전 없음", "gave_up": "포기",
                "not_applicable": "해당 없음(문서 기록)"},
    "basis": {"official_doc": "공식 문서", "agency_reply": "기관 회신", "outcome_observed": "결과 관찰", "own_attempt": "직접 시도"},
    "source_kind": {"operator_panel": "운영 측 에이전트", "curated_official": "공식 문서", "external_agent": "외부 에이전트",
                    "curated_community": "커뮤니티 자료"},
}
LABEL_ENUMS = {"task_kind": TaskKind, "obstacle": TaskObstacle, "verification_kind": VerificationKind, "site_requires": SiteRequires,
               "document_kind": DocumentKind, "entry_point_kind": EntryPointKind, "route_kind": RouteKind, "outcome": RouteOutcome,
               "basis": RouteBasis, "source_kind": RouteSourceKind}
FILTERS = ("site", "task_kind", "outcome", "source_kind")  # the only query parameters the pages read
DETAIL_FIELDS = ("verification_kind", "site_requires", "document_kind", "entry_point_kind")


def label(group: str, value, lang: str) -> str:
    """A closed value in words. A value without an entry (cannot happen with a validated record) shows its own spelling."""
    value = getattr(value, "value", value)
    if lang == "ko":
        return KO_LABELS[group].get(value, str(value).replace("_", " "))
    return EN_LABELS.get(group, {}).get(value, str(value).replace("_", " "))


_curated_cache: dict = {"table": None, "day": None, "at": 0.0, "records": []}
_curated_lock = threading.Lock()


def curated_records() -> list:
    """Every current record of the curated table (a malformed entry is skipped). The answer is kept for RECORDS_TTL seconds, so
    a page request does not validate the whole table again; the cache is tied to the table object and to the day (a record
    expires by its date), so a table that is swapped, as the tests do, is read at once. Stored records are never cached."""
    from . import task_routes_data

    table = task_routes_data.TASK_ROUTES_CURATED
    today = datetime.now(timezone.utc).date()
    with _curated_lock:
        cache = _curated_cache
        if cache["table"] is table and cache["day"] == today and monotonic() - cache["at"] < RECORDS_TTL:
            return list(cache["records"])
    records = []
    for host, entries in table.items():
        for item in entries:
            if not is_current(item, today):
                continue
            try:
                records.append(server_route(host, item))
            except ValueError:
                LOG.warning("task_route_entry_invalid")
    with _curated_lock:
        _curated_cache.update(table=table, day=today, at=monotonic(), records=records)
    return list(records)


def current_records(settings=None) -> list:
    """Every current record of the curated table and, while the write switch is served, every published stored record, newest
    period first. A malformed entry is skipped. If the database cannot be read, the curated records alone (a warning is
    logged)."""
    from .settings import task_route_reports_served

    records = curated_records()
    if settings is not None and task_route_reports_served(settings):
        from .task_route_intake import all_published_rows

        try:
            rows = all_published_rows(settings)
        except Exception:  # the database is down or not migrated yet: the curated records alone
            LOG.warning("task_route_db_unavailable")
            rows = []
        for row in rows:
            if suffix_name(row["site_host"]):  # never shown under a public or shared hosting suffix (co.uk, github.io)
                continue
            try:
                records.append(stored_route(row))
            except ValueError:
                LOG.warning("task_route_row_invalid")
    records.sort(key=lambda r: (r.period, r.verified_at or "", r.site_host, r.task_kind.value, r.obstacle.value), reverse=True)
    return records


def read_filters(params, hosts) -> dict:
    """The filter values of a query: each is kept only when it is exactly one closed value (or a host in the table); anything else
    is ignored and never echoed. The first value of a repeated parameter is read."""
    allowed = {"site": set(hosts), "task_kind": {item.value for item in TaskKind}, "outcome": {item.value for item in RouteOutcome},
               "source_kind": {item.value for item in RouteSourceKind}}
    picked = {}
    for name in FILTERS:
        values = params.getlist(name) if hasattr(params, "getlist") else [params.get(name)]
        value = values[0] if values else None
        picked[name] = value if isinstance(value, str) and value in allowed[name] else ""
    return picked


def keep(record, picked: dict) -> bool:
    return ((not picked["site"] or record.site_host == picked["site"])
            and (not picked["task_kind"] or record.task_kind.value == picked["task_kind"])
            and (not picked["outcome"] or record.outcome.value == picked["outcome"])
            and (not picked["source_kind"] or record.source_kind.value == picked["source_kind"]))


def headline(lang: str, lang_text: dict, total: int, by_source: dict) -> str:
    """The sentence above the table, computed from the counts ("Of N records, M from operator-side agents, K from the sites' own
    documents"). The first two sources are always named (a zero is a fact); the others only when present."""
    text = lang_text[lang]
    if total == 0:
        return text["routes_head_empty"]
    parts = []
    for kind in SOURCE_ORDER:
        count = by_source.get(kind, 0)
        if count or kind in SOURCE_ORDER[:2]:
            parts.append(text["routes_part"].format(n=count, label=SOURCE_PHRASE[lang][kind.value]))
    return text["routes_head"].format(total=total, s="" if total == 1 else "s", parts=", ".join(parts))


def row_for(record, lang: str, per_site: dict) -> dict:
    detail = next((label(name, getattr(record, name), lang) for name in DETAIL_FIELDS if getattr(record, name) is not None), None)
    worked = record.route_worked
    return {
        "host": record.site_host, "task": label("task_kind", record.task_kind, lang), "obstacle": label("obstacle", record.obstacle, lang),
        "detail": detail if record.obstacle != TaskObstacle.none else None,
        "worked": None if worked is None else ("none" if worked == ROUTE_WORKED_NONE else label("route_kind", worked, lang)),
        "tried": [label("route_kind", item, lang) for item in record.routes_tried],
        "outcome": label("outcome", record.outcome, lang), "basis": label("basis", record.basis, lang), "period": record.period,
        "count": per_site.get(record.site_host, 0), "source": label("source_kind", record.source_kind, lang),
        "official_url": record.official_url, "source_url": record.source_url, "verified_at": record.verified_at,
        "condition_text": record.condition_text, "sensitive": record.sensitive,
        "document": record.outcome == RouteOutcome.not_applicable,  # a record taken from a document: nobody's result, so no "route that worked"
    }


def choices(group: str, lang: str) -> list:
    return [(item.value, label(group, item, lang)) for item in LABEL_ENUMS[group]]


def page_context(records: list, shown: list, lang: str, lang_text: dict) -> dict:
    """What both pages hand to the template. ``records`` are all current records in scope (they set the counts and the per-site
    numbers); ``shown`` are the ones after the filters."""
    per_site: dict[str, int] = {}
    by_source: dict[RouteSourceKind, int] = {}
    for record in records:
        per_site[record.site_host] = per_site.get(record.site_host, 0) + 1
        by_source[record.source_kind] = by_source.get(record.source_kind, 0) + 1
    cut = len(shown) > PAGE_ROWS_MAX
    shown = shown[:PAGE_ROWS_MAX]
    return {
        "rows": [row_for(record, lang, per_site) for record in shown], "total": len(records), "shown_count": len(shown), "cut": cut,
        "headline": headline(lang, lang_text, len(records), by_source), "any_sensitive": any(record.sensitive for record in shown),
        "opts_task": choices("task_kind", lang), "opts_outcome": choices("outcome", lang), "opts_source": choices("source_kind", lang),
    }


def add_pages(app, settings, render, lang_text) -> None:
    """Register the two human pages. Both answer 404 for an unknown language."""
    from .service import ServiceError

    def site_headers(response):
        """An answer to a typed site is never kept or indexed: the address carries what a person typed."""
        response.headers["X-Robots-Tag"] = "noindex"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/{lang}/routes", include_in_schema=False)
    def routes_page(request: Request, lang: str):
        if lang not in lang_text:
            raise ServiceError("not_found", 404)
        records = current_records(settings)
        hosts = sorted({record.site_host for record in records})
        site_options = hosts if len(hosts) <= ROUTES_DATALIST_MAX else []
        picked = read_filters(request.query_params, [])  # the site box is read below, not as a closed value
        values = request.query_params.getlist("site")
        typed = site_from_input(values[0] if values else "")
        others = urlencode([(n, picked[n]) for n in FILTERS if n != "site" and picked[n]])
        state, name, matches = "", "", []
        if typed.kind == "invalid":
            state = "invalid"
        elif typed.kind == "label":
            matches = [host for host in hosts if registrable(host).split(".")[0] == typed.value]
            if len(matches) == 1:
                return site_headers(RedirectResponse(f"/{lang}/routes/{matches[0]}" + (f"?{others}" if others else ""), status_code=302))
            state = "several" if matches else "label_none"
        elif typed.kind == "host":
            key, _rows = site_records(typed.value, settings)
            if key is not None:
                return site_headers(RedirectResponse(f"/{lang}/routes/{key}" + (f"?{others}" if others else ""), status_code=302))
            name = route_host(typed.value) or ""
            state = "none" if name else "none_plain"
            if name and values[0].strip() != name and route_host(name) == name:  # a pasted address: send the hostname alone, so no path or query stays in the address bar
                return site_headers(RedirectResponse(f"/{lang}/routes?" + urlencode([("site", name)] + ([(n, picked[n]) for n in FILTERS if n != "site" and picked[n]])), status_code=302))
            picked["site"] = name
        context = page_context(records, [] if state else [record for record in records if keep(record, picked)], lang, lang_text)
        response = render(request, lang, "routes", picked=picked, lang_query=urlencode([(n, picked[n]) for n in FILTERS if picked[n]]),
                          site_options=site_options, site_shown=name, site_state=state, site_name=name, site_matches=matches,
                          site_count=len(hosts), **context)
        return site_headers(response) if typed.kind != "empty" else response

    @app.get("/{lang}/routes/{host}", include_in_schema=False)
    def routes_host_page(request: Request, lang: str, host: str):
        if lang not in lang_text:
            raise ServiceError("not_found", 404)
        # The page exists only for a key with records, found by the same walk as GET /v1/task-routes (site_records and table_key,
        # which count the published stored records while the write switch is served). Any other name (a bad host, a site with no
        # entry, a tenant subdomain) is a plain 404 and appears nowhere in the answer.
        try:
            site, _kind = parse_query(host)
        except TaskRouteInputError:
            raise ServiceError("not_found", 404) from None
        key, _rows = site_records(site, settings)
        if key is None:
            raise ServiceError("not_found", 404)
        picked = read_filters(request.query_params, [])
        query = urlencode([(name, picked[name]) for name in FILTERS if picked[name]])
        if host != key:  # www., upper case, a trailing dot, support.: one address per site
            return RedirectResponse(f"/{lang}/routes/{key}" + (f"?{query}" if query else ""), status_code=301)
        whole = lookup(key, settings=settings)
        # With a task chosen the records of that task are asked for, so a task past the newest TASK_ROUTES_MAX of the site is found
        # too; the counts above the table stay those of the whole site.
        answer = lookup(key, TaskKind(picked["task_kind"]), settings=settings) if picked["task_kind"] else whole
        # lookup() shows at most TASK_ROUTES_MAX records; the counts cover all of them.
        shown = [record for record in answer.routes if keep(record, picked)]
        context = page_context(answer.routes, shown, lang, lang_text)
        context["total"] = whole.counts.records
        for row in context["rows"]:
            row["count"] = whole.counts.records  # the site's whole count, not only the shown ones
        context["headline"] = headline(lang, lang_text, whole.counts.records, whole.counts.by_source_kind)
        hosts = sorted({record.site_host for record in curated_records()})  # suggestions only: no database read for them here
        return render(request, lang, "routes_host", site_host=key, picked=picked, lang_query=query,
                      site_options=hosts if len(hosts) <= ROUTES_DATALIST_MAX else [], site_shown="", site_state="",
                      other_kinds=bool(picked["task_kind"]) and answer.counts.records == 0 and whole.counts.records > 0,
                      newest_only=answer.counts.records > len(answer.routes), shown_max=len(answer.routes),
                      newest_total=answer.counts.records, **context)
