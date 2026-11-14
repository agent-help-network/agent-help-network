"""Task routes read API (D117): ``GET /v1/task-routes``.

Off by default. With ``AHN_TASK_ROUTES`` unset the path is never registered (404) and nothing here runs. The answer comes from
ahn.task_routes.lookup: the curated table in code, no database and no request to any site (D33). It is a separate layer from
the lookup verdict: nothing here is read by check(), and the verdict does not change when this is on.

Query: ``site_host`` (a public hostname, required) and ``task_kind`` (one value of ahn.task_routes.TaskKind, optional).
Errors are codes only and never echo the input: invalid_site_host (400), invalid_task_kind (400).
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from .task_routes import (ROUTE_WORKED_NONE, DocumentKind, EntryPointKind, RouteBasis, RouteKind, RouteOutcome, RouteSourceKind,
                          SiteRequires, TaskKind, TaskObstacle, TaskRouteInputError, VerificationKind, is_current, lookup,
                          parse_query, server_route)

LOG = logging.getLogger(__name__)

PATH = "/v1/task-routes"
CACHE_CONTROL = "public, max-age=300"


def add_routes(app, settings, render=None, lang_text=None) -> None:
    """Register GET /v1/task-routes. Called only while AHN_TASK_ROUTES=1; otherwise the path does not exist (404).

    ``render`` and ``lang_text`` are the page helpers of the app; with both given, the two human pages are registered too."""
    from .service import ServiceError

    headers = {"Cache-Control": CACHE_CONTROL, "X-Content-Type-Options": "nosniff"}

    @app.get(PATH, include_in_schema=False)
    def task_routes(request: Request, site_host: str = "", task_kind: str | None = None):
        try:
            host, kind = parse_query(site_host, task_kind)
        except TaskRouteInputError as error:
            raise ServiceError(error.code, 400) from None
        answer = lookup(host, kind)
        return JSONResponse(answer.model_dump(mode="json"), headers=headers)

    if render is not None and lang_text is not None:
        add_pages(app, settings, render, lang_text)


# --- Human pages (PR-F): GET /{lang}/routes and /{lang}/routes/{host} ----------------------------------------------------
# Same flag as the API. The pages read the in-code table through task_routes.lookup / server_route only: they never call check(),
# open a database or reach a site, and they show the server-decided values (sensitive, source_kind, period) as the server computed them.
PAGE_ROWS_MAX = 500
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
        "mobile_app_only": "모바일 앱 전용", "partner_or_third_party_portal": "제휴·제3자 포털"},
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


def current_records() -> list:
    """Every current record of the curated table, newest period first. Reads the table at call time; a malformed entry is skipped."""
    from . import task_routes_data

    records = []
    for host, entries in task_routes_data.TASK_ROUTES_CURATED.items():
        for item in entries:
            if not is_current(item):
                continue
            try:
                records.append(server_route(host, item))
            except ValueError:
                LOG.warning("task_route_entry_invalid")
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
        "sensitive": record.sensitive,
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
    shown = shown[:PAGE_ROWS_MAX]
    return {
        "rows": [row_for(record, lang, per_site) for record in shown], "total": len(records), "shown_count": len(shown),
        "headline": headline(lang, lang_text, len(records), by_source), "any_sensitive": any(record.sensitive for record in shown),
        "opts_task": choices("task_kind", lang), "opts_outcome": choices("outcome", lang), "opts_source": choices("source_kind", lang),
    }


def add_pages(app, settings, render, lang_text) -> None:
    """Register the two human pages. Both answer 404 for an unknown language."""
    from .service import ServiceError

    @app.get("/{lang}/routes", include_in_schema=False)
    def routes_page(request: Request, lang: str):
        if lang not in lang_text:
            raise ServiceError("not_found", 404)
        records = current_records()
        hosts = sorted({record.site_host for record in records})
        picked = read_filters(request.query_params, hosts)
        context = page_context(records, [record for record in records if keep(record, picked)], lang, lang_text)
        return render(request, lang, "routes", picked=picked, opts_site=[(host, host) for host in hosts], **context)

    @app.get("/{lang}/routes/{host}", include_in_schema=False)
    def routes_host_page(request: Request, lang: str, host: str):
        if lang not in lang_text:
            raise ServiceError("not_found", 404)
        try:
            site, _kind = parse_query(host)
        except TaskRouteInputError:
            return render(request, lang, "not-found", status_code=404)  # the input is never echoed
        answer = lookup(site)
        # lookup() shows at most TASK_ROUTES_MAX records; the counts cover all of them.
        picked = read_filters(request.query_params, [])
        shown = [record for record in answer.routes if keep(record, picked)]
        context = page_context(answer.routes, shown, lang, lang_text)
        context["total"] = answer.counts.records
        for row in context["rows"]:
            row["count"] = answer.counts.records  # the site's whole count, not only the shown ones
        context["headline"] = headline(lang, lang_text, answer.counts.records, answer.counts.by_source_kind)
        return render(request, lang, "routes_host", site_host=answer.site_host, picked=picked, opts_site=[],
                      newest_only=answer.counts.records > len(answer.routes), shown_max=len(answer.routes), **context)
