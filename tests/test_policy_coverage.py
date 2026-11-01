"""The privacy policy must keep up with what the code collects, stores and returns.

The ChatGPT plugin review of 2026-10-19 rejected the first listing partly because the policy did
not describe every data use after the product had changed. This module turns that lesson into
tests: when a change adds a tool, an input field, a returned field, a database table or a
retention period, one of these tests fails until the policy page (English and Korean) says so.

Everything here is pure: no database and no network. The policy is rendered by the real app with
a dummy database URL, tool lists come from a subprocess that imports the MCP adapter, and tables
come from reading migrations/*.sql.

When a test fails because a new name is missing from a mapping below, do not just add the name.
First make the policy say what the new data is (ahn/templates/policy.html, both languages), then
map the name to the phrase that now covers it. The phrases are checked against the rendered page.
"""

from __future__ import annotations

import functools
import html
import inspect
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn import background_reads, bundle, gov_registry, platform_metrics
from ahn.main import create_app
from ahn.models import AttemptFields, CheckObservation, CheckResponse, ObservationIn, OutcomeIn, Receipt
from ahn.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
LANGS = ("en", "ko")
# Different from every fixed period in the code, so a hard-coded number cannot pass by accident.
REPORT_RETENTION_DAYS = 45
GRACE_DAYS = platform_metrics.WITHDRAWN_REPORT_GRACE_DAYS


@dataclass(frozen=True)
class Phrase:
    """Wording that covers an item. It must appear in the named policy section, in both languages.

    `{days}` is replaced with the period found in the code, so the wording and the number are
    checked together.
    """
    en: str
    ko: str


@dataclass(frozen=True)
class Exempt:
    """The item adds nothing the policy has to describe. The reason is the review record."""
    reason: str


# --- Rendering ---------------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def policy_html(lang: str, reporting: bool, retention_days: int = 0) -> str:
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True,
                        reporting_enabled=reporting, report_retention_days=retention_days)
    response = TestClient(create_app(settings)).get(f"/{lang}/policy")  # no `with`: lifespan (database) never runs
    assert response.status_code == 200
    return response.text


def plain(markup: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()


def policy_text(lang: str, reporting: bool = True, retention_days: int = 0) -> str:
    return plain(policy_html(lang, reporting, retention_days))


def section(lang: str, number: str, reporting: bool = True, retention_days: int = 0) -> str:
    """Text of numbered policy section 01-08 (02 data collected, 03 returned, 06 retention)."""
    found = re.search(rf'<article><span class="policy-index">{number}</span>.*?</article>',
                      policy_html(lang, reporting, retention_days), re.S)
    assert found, f"policy section {number} not found in the {lang} page"
    return plain(found.group())


def sentence_with(text: str, anchor: str) -> str:
    found = re.search(rf"[^.]*{re.escape(anchor)}[^.]*\.", text)
    assert found, f"no sentence with {anchor!r}"
    return found.group()


def assert_phrases(label: str, text_by_lang: dict[str, str], wanted: dict[str, Phrase], days: dict[str, int] | None = None):
    """Every wanted phrase must be present in the text of each language."""
    missing = []
    for name, phrase in wanted.items():
        for lang in LANGS:
            needle = getattr(phrase, lang).format(days=(days or {}).get(name, ""))
            if needle not in text_by_lang[lang]:
                missing.append(f"{name}: {lang} policy lacks {needle!r}")
    assert not missing, f"{label}:\n  " + "\n  ".join(missing)


# --- 1. MCP tools ------------------------------------------------------------------------------

# What the public server may expose. A new tool changes this table, which sends you to the policy.
PUBLIC_TOOLS = {
    False: {"check_incident", "rate_last_lookup"},                       # reporting off: the lookup and its feedback (D58)
    True: {"check_incident", "rate_last_lookup", "report_observation"},  # reporting on
}


def registered_tools(reporting: bool) -> dict[str, list[str]]:
    """Tool name -> input property names, read from the MCP server as the public deployment builds it.

    A subprocess, because ahn.mcp_adapter chooses its tools from the environment when imported.
    """
    code = ("import asyncio, json; from ahn.mcp_adapter import mcp; "
            "print(json.dumps({t.name: sorted(t.input_schema.get('properties', {})) "
            "for t in asyncio.run(mcp.list_tools())}))")
    env = {**os.environ, "AHN_PUBLIC_MODE": "1", "AHN_REPORTING_ENABLED": "1" if reporting else "0"}
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("reporting", [False, True])
def test_every_public_mcp_tool_is_named_in_the_policy(reporting):
    tools = registered_tools(reporting)
    assert set(tools) == PUBLIC_TOOLS[reporting], (
        f"The MCP tools changed to {sorted(tools)}. Describe the new tool in the privacy policy "
        "(ahn/templates/policy.html, English and Korean), then update PUBLIC_TOOLS here.")
    for lang in LANGS:
        text = policy_text(lang, reporting)
        for name in tools:
            assert name in text, f"the {lang} policy never names the MCP tool {name} (reporting={reporting})"
        if not reporting:
            # The policy must not claim a write tool the deployment does not expose.
            assert "report_observation" not in text, f"the {lang} policy names report_observation while reporting is off"


# --- 2. Input fields ---------------------------------------------------------------------------

# check_incident, GET /v1/incidents/check and the search forms accept these. They are described in
# the sentence of policy section 02 that names check_incident.
LOOKUP_FIELDS = {
    "site_host": Phrase("public hostname (site_host)", "공개 호스트명(site_host)"),
    "error_class": Phrase("error_class", "error_class"),      # optional code from a fixed list
    "task_step": Phrase("task_step", "task_step"),            # optional code from a fixed list
    "failure_stage": Phrase("failure_stage", "failure_stage"),  # optional code from a fixed list
    # Lookup signal codes (D47): optional codes from fixed lists, counted per hostname and hour.
    "http_status": Phrase("http_status", "http_status"),
    "challenge": Phrase("challenge", "challenge"),
    "retry_after": Phrase("retry_after", "retry_after"),
    "via": Phrase("via", "via"),
    # D48: chooses the recommendation only; neither counted nor stored (the policy sentence says so).
    "agent_vendor": Phrase("agent_vendor", "agent_vendor"),
    "window_hours": Exempt("REST only: a whole number 1-168 that narrows the observation window, not data about anyone"),
}

# report_observation, POST /v1/observations and the report form accept these. They are described as a
# group in the sentence of section 02 that names report_observation.
REPORT_FIELDS = {
    "site_host": Phrase("public hostname", "공개 호스트명"),
    "task_step": Phrase("task step", "작업 단계"),
    "error_class": Phrase("error type", "오류 유형"),
    "observed_at": Phrase("observation time", "관찰 시각"),
    "agent_family": Phrase("agent family", "에이전트 계열"),
    "attempt_actor": Phrase("attempt actor and environment", "시도 주체·환경"),
    "attempt_env": Phrase("attempt actor and environment", "시도 주체·환경"),
    "outcome": Phrase("outcome", "결과"),
    "challenge_vendor": Phrase("challenge vendor and kind codes", "사람 확인 업체·종류 코드"),
    "challenge_kind": Phrase("challenge vendor and kind codes", "사람 확인 업체·종류 코드"),
    "synthetic": Phrase("synthetic flag", "가상 여부"),
    "idempotency_key": Phrase("idempotency key", "중복 방지 키"),  # stored in report_submissions for de-duplication
    "schema_version": Exempt("always 1; a protocol constant, not data about anyone"),
    "consent_confirmed": Exempt("legacy yes/no flag the report form still sends; validated but never stored"),
}

# Names in the HTML forms that are not model fields: the form folds them into model fields.
REPORT_FORM_ALIASES = {"attempt_source": "attempt_actor"}  # one select that sets attempt_actor and attempt_env


def form_field_names(*templates: str) -> set[str]:
    names: set[str] = set()
    for template in templates:
        names |= set(re.findall(r'\bname="([a-z_]+)"', (ROOT / "ahn" / "templates" / template).read_text(encoding="utf-8")))
    return names


def rest_check_parameters() -> set[str]:
    schema = create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True)).openapi()
    return {p["name"] for p in schema["paths"]["/v1/incidents/check"]["get"]["parameters"]}


def test_lookup_inputs_are_all_covered_by_the_policy():
    accepted = set(registered_tools(False)["check_incident"]) | rest_check_parameters() | form_field_names("home.html", "search.html")
    unmapped = accepted - set(LOOKUP_FIELDS)
    assert not unmapped, (f"The lookup now accepts {sorted(unmapped)}. Say so in policy section 02 "
                          "(English and Korean), then add the field to LOOKUP_FIELDS.")
    for reporting in (False, True):
        sentences = {lang: sentence_with(section(lang, "02", reporting), "check_incident") for lang in LANGS}
        assert_phrases("lookup inputs", sentences, {n: p for n, p in LOOKUP_FIELDS.items() if isinstance(p, Phrase)})


def test_report_inputs_are_all_covered_by_the_policy():
    tool_inputs = registered_tools(True)["report_observation"]
    assert tool_inputs == ["observation"], f"report_observation takes {tool_inputs}; map each new input to the policy"
    accepted = set(ObservationIn.model_fields) | set(OutcomeIn.model_fields) | set(AttemptFields.model_fields)
    accepted |= {REPORT_FORM_ALIASES.get(n, n) for n in form_field_names("report.html")}
    unmapped = accepted - set(REPORT_FIELDS)
    assert not unmapped, (f"Reports now accept {sorted(unmapped)}. Say so in policy section 02 "
                          "(English and Korean), then add the field to REPORT_FIELDS.")
    # Exempt entries need no wording; everything else must be in the report sentence.
    covered = {name: phrase for name, phrase in REPORT_FIELDS.items() if isinstance(phrase, Phrase)}
    sentences = {lang: sentence_with(section(lang, "02"), "report_observation") for lang in LANGS}
    assert_phrases("report inputs", sentences, covered)


# --- 3. What the tools return ------------------------------------------------------------------

# Top-level fields of the lookup answer and of the report receipt, matched to policy section 03.
CHECK_RESPONSE_FIELDS = {
    "access_profile": Phrase("declared robots.txt and llms.txt policy", "robots.txt·llms.txt 로 선언한 정책"),
    "official_paths": Phrase("official developer and status links", "공식 개발자·상태 링크"),
    "official_status_page": Phrase("official developer and status links", "공식 개발자·상태 링크"),
    "provider_status": Phrase("published status", "업체가 발표한 상태"),
    "observations": Phrase("published observations without any reporter identity", "제보자 신원이 없는 공개 관찰"),
    "lookup_activity": Phrase("coarse lookup-activity band", "대략적인 조회 활동 구간"),
    "host_facts": Phrase("registry facts about the domain", "도메인에 관한 레지스트리 사실"),
    "ahn_check": Phrase("what our own server received when it requested the host's home page",
                        "우리 서버가 그 호스트의 홈 페이지(/)를 쿠키와 로그인 없이 요청해 받은 것"),
    "site_host": Exempt("echo of the hostname the caller sent"),
    "generated_at": Exempt("time of the answer"),
    "synthetic": Exempt("flag copied from the stored observations"),
    "window_hours": Exempt("length of the observation window"),
    "data_horizon_hours": Exempt("length of the observation window"),
    "current_site_status": Exempt("constant 'unknown'"),
    "evidence_level": Exempt("category computed from the published observations"),
    "summary_code": Exempt("computed from the data above"),
    "summary": Exempt("computed from the data above"),
    "failures": Exempt("count of the published observations"),
    "successes": Exempt("count of the published observations"),
    "reporting_sources": Exempt("count of the published observations' sources"),
    "provider_source_state": Exempt("metadata of the provider-status lookup"),
    "provider_discovery_attempted_at": Exempt("metadata of the provider-status lookup"),
    "recommendation": Exempt("advice computed from the data above"),
    "failure_stage": Exempt("echo of the optional input of the same name"),
    "next_checks": Exempt("advice computed from the data above"),
    "summary_line": Exempt("advice computed from the data above"),
    "next_action": Exempt("advice computed from the data above"),
    "links": Exempt("constant links to the service's public feedback pages"),
    # D58: a signed one-time token; described in sections 02 and 03.
    "feedback_token": Phrase("one-time feedback token", "일회용 결과 토큰"),
}
RECEIPT_FIELDS = {
    "observation_id": Phrase("observation and incident IDs", "관찰·사건 ID"),
    "incident_id": Phrase("observation and incident IDs", "관찰·사건 ID"),
    "review_state": Phrase("review state", "검토 상태"),
    "withdrawal_token": Phrase("withdrawal token", "철회 토큰"),
    "synthetic": Exempt("echo of the submitted flag"),
}


def test_returned_fields_are_all_covered_by_the_policy():
    # The lookup answer is described by the whole of section 03; the receipt by its report_observation sentence.
    for model, mapping, scope in ((CheckResponse, CHECK_RESPONSE_FIELDS, lambda text: text),
                                  (Receipt, RECEIPT_FIELDS, lambda text: sentence_with(text, "report_observation"))):
        unmapped = set(model.model_fields) - set(mapping)
        assert not unmapped, (f"{model.__name__} now returns {sorted(unmapped)}. Say so in policy section 03 "
                              "(English and Korean), then map the field here.")
        covered = {name: phrase for name, phrase in mapping.items() if isinstance(phrase, Phrase)}
        assert_phrases(f"{model.__name__} fields", {lang: scope(section(lang, "03")) for lang in LANGS}, covered)


def test_published_observations_expose_nothing_that_reports_do_not_collect():
    # A field returned to everyone in an observation but never listed as collected would be a new data use.
    assert set(CheckObservation.model_fields) <= set(ObservationIn.model_fields)


# --- 4. Database tables and retention ----------------------------------------------------------

@dataclass(frozen=True)
class Pruned:
    """Deleted by code after a fixed period. The policy must state the same number."""
    section_06: Phrase           # wording in the Retention section, with {days}
    technical: Phrase | None = None  # repeated wording in the technical details, which carry the number too


@dataclass(frozen=True)
class Kept:
    """Never deleted by code. The policy must say it has no automatic deletion date."""
    wording: Phrase              # names the table's contents; its sentence must say "no automatic deletion date"


@dataclass(frozen=True)
class Transient:
    """A work item deleted by code as soon as it has been handled. The policy must say so."""
    wording: Phrase              # names the table's contents; its sentence must say when it is deleted


@dataclass(frozen=True)
class Replaced:
    """A downloaded copy of a public list: rows are replaced whenever the publisher posts a new version.

    The policy must say what is kept and that the copy is replaced (not kept forever).
    """
    wording: Phrase              # names the copy; the replacement statement follows within the same paragraph
    replaced: Phrase             # says the copy is replaced by a new version


@dataclass(frozen=True)
class UntilSwitchedOff:
    """Kept while a feature's switch is on and deleted by code when it is turned off. Described only while on."""
    wording: Phrase              # names the table's contents; its sentence must say it is deleted when switched off


@dataclass(frozen=True)
class UntilExpiry:
    """A row that is useless once the value it stands for has expired, deleted by code then. The policy must say so."""
    wording: Phrase              # names the table's contents
    when: Phrase                 # says when it is deleted


@dataclass(frozen=True)
class ReportRows:
    """Report rows: deleted after AHN_REPORT_RETENTION_DAYS, withdrawn ones after the grace period."""


# Every table created in migrations/*.sql, with the reason it is or is not described in the policy.
TABLES = {
    # Request-derived data, deleted by prune_analytics after a fixed period.
    "traffic_client_daily": Pruned(
        Phrase("with daily request counts: {days} days", "일별 요청 수: {days}일"),  # keyed IP digests per day
        Phrase("daily request count for {days} days", "일별 요청 수만 {days}일 보관")),
    "traffic_attribution_daily": Pruned(  # referral and campaign categories, no client identity
        Phrase("Referral and campaign category counts, and MCP tool-call counts by result category: {days} days",
               "유입·캠페인 분류 집계와 결과 범주별 MCP 도구 호출 수: {days}일")),
    "mcp_check_metrics": Pruned(  # tool-call counts by result category, no hostname
        Phrase("MCP tool-call counts by result category: {days} days", "결과 범주별 MCP 도구 호출 수: {days}일"),
        Phrase("by evidence and provider-signal category for {days} days", "근거·업체 신호 범주별 일일 합계로 {days}일 보관")),
    "public_intake_host_daily": Pruned(  # hostnames of reports received per day
        Phrase("Per-host daily report-intake counters: {days} days", "호스트별 일일 제보 접수 수: {days}일")),
    "queried_hosts": Pruned(  # hostnames that were looked up
        Phrase("removed after {days} days without a lookup", "{days}일 동안 조회가 없으면 삭제"),
        Phrase("removed after {days} days without a lookup", "{days}일 동안 조회가 없으면 삭제")),
    # Lookup signals (migration 027, ahn/lookup_signals.py, D47): codes counted per hostname and hour, and host- and
    # day-specific keyed IP digests. Deleted by prune_analytics after a fixed period; stated in sections 02 and 06.
    "lookup_signals_hourly": Pruned(Phrase("Hourly per-host counts of lookup codes: {days} days", "호스트별 시간 단위 조회 코드 집계: {days}일"),
                                    Phrase("for {days} days, together with a keyed digest", "{days}일 집계하며")),
    # Lookup feedback (migration 031, ahn/feedback.py, D58): outcome counts per hostname and hour of the lookup.
    "lookup_feedback_hourly": Pruned(Phrase("Hourly per-host counts of feedback outcomes, with the recommendation given and the kind of client: {days} days",
                                            "호스트별 시간 단위 결과 회신 집계(그때 준 권고와 클라이언트 종류 포함): {days}일")),
    # Used-token fingerprints, no hostname: deleted five minutes after the token has expired (one hour after its lookup).
    "lookup_feedback_used": UntilExpiry(Phrase("Fingerprints of used feedback tokens, with no hostname", "쓰인 결과 토큰의 지문(호스트명 없음)"),
                                        Phrase("until five minutes after the token expires, one hour after its lookup", "조회 1시간 뒤 토큰이 만료되고 5분이 지날 때까지")),
    # Adoption measurement (migration 028, ahn/adoption.py, D49): fixed-list categories per day, no hostname, address or digest.
    "lookup_outcome_daily": Pruned(Phrase(
        "Daily counts of answered lookups by client category (plugin, ahn-client, MCP host, script, browser, crawler, the site's own search page, or unknown), the MCP host family or ahn-client integration label where there is one (each read from the User-Agent header, which is not kept), the recommendation given and whether the hostname's declared-policy read was ready, stale, pending or none (no declared-policy layer for that hostname), with no hostname and no address: {days} days",
        "응답한 조회의 일별 집계(클라이언트 종류 — 플러그인·ahn-client·MCP 호스트·스크립트·브라우저·크롤러·사이트 자체 검색 페이지·알 수 없음 —, 해당할 때 MCP 호스트 계열이나 ahn-client 연동 라벨(둘 다 User-Agent 헤더에서 읽고 헤더 자체는 보관하지 않음), 준 권고, 그 호스트명의 선언 정책 읽기가 준비·오래됨·대기 중·없음(그 호스트명에 선언 정책 층이 없음) 중 어느 상태였는지, 호스트명과 주소 없음): {days}일")),
    "lookup_sources_daily": Pruned(Phrase(
        "Keyed IP digests specific to one hostname and one UTC day, with the client's last lookup time, the kind of blocked lookup and the recommendation it was given: {days} days",
        "호스트명과 날짜(UTC)에만 해당하도록 비밀키로 변환한 IP 값과 그 클라이언트의 마지막 조회 시각·막힘 유형·그때 준 권고: {days}일"),
        Phrase("kept for {days} days to count separate clients", "{days}일 보관합니다")),
    # Signal history for curated hosts (migration 024, ahn/signal_history.py): host facts, no request or client data.
    "robots_read_events": Pruned(Phrase(
        "History of robots.txt reads for curated public hostnames (time, host read, status, label, classification, operator, declared network location, and whether the read's window was held and why): {days} days",
        "큐레이션한 공개 호스트명별 robots.txt 읽기 이력(시각, 읽은 호스트, 상태, 라벨, 분류, 운영자, 선언한 네트워크 위치, 그 구간의 보류 여부와 사유): {days}일")),
    "outage_episodes": Pruned(Phrase(
        "Periods in which a curated host's robots.txt could not be read or its provider reported a problem, and each change in a provider's published status: {days} days",
        "큐레이션한 호스트의 robots.txt 를 읽지 못한 구간이나 업체가 문제를 발표한 구간, 그리고 업체가 발표한 상태의 각 변화: {days}일")),
    "provider_status_events": Pruned(Phrase(
        "Periods in which a curated host's robots.txt could not be read or its provider reported a problem, and each change in a provider's published status: {days} days",
        "큐레이션한 호스트의 robots.txt 를 읽지 못한 구간이나 업체가 문제를 발표한 구간, 그리고 업체가 발표한 상태의 각 변화: {days}일")),
    "robots_read_daily": Pruned(Phrase(
        "Daily per-host totals of robots.txt reads, answers, failures, refusals and held reads, with the number of reads that followed a lookup and of seed-list reads: {days} days",
        "호스트별 일일 robots.txt 읽기·응답·실패·거절·보류 합계와 조회 뒤 읽기 수·시드 목록 읽기 수: {days}일")),
    # AHN server check of home pages (migration 033, ahn/site_probe.py, D67): codes about public hosts, no client data.
    "site_probe": Pruned(Phrase("never the page, a header value, a cookie or a path: {days} days after the request",
                                "페이지·헤더 값·쿠키·경로는 저장하지 않습니다: 요청 {days}일 뒤까지")),
    "site_probe_daily": Pruned(Phrase("and the daily counts (with no hostname) that drive the automatic stop: {days} days",
                                      "자동 중지에 쓰는 일별 집계(호스트명 없음): {days}일")),
    "probe_breaker_daily": Pruned(Phrase("and the daily counts (with no hostname) that drive the automatic stop: {days} days",
                                         "자동 중지에 쓰는 일별 집계(호스트명 없음): {days}일")),
    "site_probe_set": Replaced(
        Phrase("The day's list of hostnames to measure (hostname, list and rank)", "그날 잴 호스트명 목록(호스트명, 목록, 순위)"),
        Phrase("is replaced each day", "매일 교체됩니다")),
    "probe_state": Exempt("operator state of the home-page check: the baseline refusal rate and the stop flag, no request data"),
    "render_metric_samples": Pruned(  # hosting-provider totals, no per-request detail
        Phrase("hosting-usage samples from Render (CPU, memory and request totals): {days} days",
               "시간별 사용량 표본(CPU·메모리·요청 합계): {days}일")),
    # Request-derived data that code never deletes; the policy says there is no automatic deletion date.
    "lookup_metrics": Kept(Phrase("Daily lookup counts per public hostname and error type", "공개 호스트명·오류 유형별 일일 조회 수")),
    "request_metrics": Kept(Phrase("per-route request counts", "경로별 요청 수")),
    "host_access_profile": Kept(Phrase("cached robots.txt, llms.txt and provider-status results kept for each public hostname",
                                       "공개 호스트명별로 저장한 robots.txt·llms.txt 해석 결과와 업체 상태 캐시")),
    "status_cache": Kept(Phrase("cached robots.txt, llms.txt and provider-status results kept for each public hostname",
                                "공개 호스트명별로 저장한 robots.txt·llms.txt 해석 결과와 업체 상태 캐시")),
    "status_sources": Kept(Phrase("cached robots.txt, llms.txt and provider-status results kept for each public hostname",
                                  "공개 호스트명별로 저장한 robots.txt·llms.txt 해석 결과와 업체 상태 캐시")),
    "incidents": Kept(Phrase("incident records that group reports by hostname, task step and error type",
                             "제보를 호스트명·작업 단계·오류 유형으로 묶는 사건 기록")),  # survives deletion of its reports
    "audit_events": Kept(Phrase("audit-log entries (event type, report ID and time)", "감사 기록(이벤트 유형·제보 ID·시각)")),
    "public_intake_daily": Kept(Phrase("Daily report-intake totals", "일별 제보 접수 합계")),
    # Work items: a lookup's request for a background robots.txt/llms.txt or status read, deleted once handled.
    "background_reads": Transient(Phrase("background-read request (the public hostname, the kind of read and when it was asked for)",
                                         "뒤에서 읽기 요청(공개 호스트명, 읽기 종류, 요청 시각)")),
    # Public government domain lists: one current copy each, replaced when the publisher posts a new version.
    "gov_domain_registry": Replaced(
        Phrase("the current copy of each public government domain list we download", "내려받는 공개 정부 도메인 목록마다 현재 사본"),
        Phrase("is replaced when its publisher posts a new version", "게시자가 새 판을 내면 교체")),
    "gov_registry_versions": Replaced(
        Phrase("the current copy of each public government domain list we download", "내려받는 공개 정부 도메인 목록마다 현재 사본"),
        Phrase("is replaced when its publisher posts a new version", "게시자가 새 판을 내면 교체")),
    # Status-feed hosts' robots.txt decisions (migration 029, ahn/feed_robots.py, D50): one row per host, rewritten by each read.
    "feed_robots": Replaced(
        Phrase("whether its robots.txt allows each feed path we would request", "그 호스트의 robots.txt 가 우리가 요청할 피드 경로를 허용하는지"),
        Phrase("this record is replaced on each read", "이 기록은 읽을 때마다 교체됩니다")),
    "observations": ReportRows(),
    "report_submissions": ReportRows(),  # idempotency key and request hash of a report; goes with its observation
    # Not request-derived: nothing for the policy to describe.
    "schema_migrations": Exempt("migration bookkeeping"),
    "reporters": Exempt("one shared 'public-unverified' row for all no-account reports; no per-person identity"),
    "service_flags": Exempt("operator on/off switches"),
    "bandwidth_guard_samples": Exempt("hosting bandwidth totals in gigabytes from the provider's API, not request data"),
    "bandwidth_guard_state": Exempt("state of the operator's bandwidth guard"),
    # The offline bundle's one row: the file and the day's served-byte total. Checked in section 6 below.
    "public_bundle": UntilSwitchedOff(Phrase("the latest file and the day's total of bytes served (no client data)",
                                             "최신 파일과 그날 전송한 바이트 합계(클라이언트 정보 없음)")),
}


def migration_tables() -> set[str]:
    tables: set[str] = set()
    for path in sorted((ROOT / "migrations").glob("*.sql")):
        tables |= set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?([a-z_]+)", path.read_text(encoding="utf-8")))
    return tables


def deleted_tables_in_code() -> set[str]:
    found: set[str] = set()
    for path in (ROOT / "ahn").glob("*.py"):
        found |= set(re.findall(r"DELETE FROM ([a-z_]+)", path.read_text(encoding="utf-8")))
    return found


def periods_in_prune_analytics() -> dict[str, int]:
    """Days each table is kept, read from the DELETE statements of prune_analytics.

    `day < CURRENT_DATE - 13` keeps today and the 13 days before, so 14 days.
    """
    source = inspect.getsource(platform_metrics.prune_analytics)
    days: dict[str, int] = {}
    for table, back in re.findall(r"DELETE FROM ([a-z_]+) WHERE \w+ < CURRENT_DATE - (\d+)", source):
        days[table] = int(back) + 1
    for table, back in re.findall(r"DELETE FROM ([a-z_]+) WHERE \w+ < \(now\(\) AT TIME ZONE 'UTC'\)::date - (\d+)", source):
        days[table] = int(back) + 1  # the UTC date, not the session's
    for table, count in re.findall(r"DELETE FROM ([a-z_]+) WHERE \w+ < now\(\) - interval '(\d+) days'", source):
        days[table] = int(count)
    statements = [table for table in re.findall(r"DELETE FROM ([a-z_]+)", source) if table not in expiring_in_prune_analytics()]
    assert sorted(statements) == sorted(days), (
        "prune_analytics deletes in a form this test cannot read; use `day < CURRENT_DATE - N` or "
        f"`col < now() - interval 'N days'`, or extend periods_in_prune_analytics. Unread: {sorted(set(statements) - set(days))}")
    return days


def expiring_in_prune_analytics() -> set[str]:
    """Tables prune_analytics empties of expired rows (`expires_at < now()`, optionally a few minutes later), not after a
    fixed period."""
    return set(re.findall(r"DELETE FROM ([a-z_]+) WHERE expires_at < now\(\)(?: - interval '\d+ minutes')?\"",
                          inspect.getsource(platform_metrics.prune_analytics)))


def test_used_feedback_fingerprints_are_kept_the_stated_margin_past_expiry():
    from ahn import feedback
    source = inspect.getsource(platform_metrics.prune_analytics) + inspect.getsource(feedback.record)
    assert feedback.EXPIRY_GRACE == "5 minutes"  # "five minutes" / "5분" in section 06
    assert "lookup_feedback_used WHERE expires_at < now() - interval '5 minutes'" in source
    assert "interval '{EXPIRY_GRACE}'" in inspect.getsource(feedback.record)


def test_every_table_is_mapped_to_a_retention_statement():
    unmapped = migration_tables() - set(TABLES)
    assert not unmapped, (f"New tables {sorted(unmapped)} in migrations/. Decide whether they hold request-derived data: "
                          "if so, state their retention in policy section 06 (English and Korean) and map them in TABLES; "
                          "if not, map them to Exempt(reason).")
    gone = set(TABLES) - migration_tables()
    assert not gone, f"TABLES lists tables no migration creates: {sorted(gone)}"


def test_deleted_tables_match_the_policy_mapping():
    pruned = {t for t, v in TABLES.items() if isinstance(v, Pruned)}
    assert set(periods_in_prune_analytics()) == pruned, (
        "prune_analytics deletes a different set of tables than TABLES marks as Pruned. "
        "Update the mapping and the policy together.")
    # Tables mapped as 'no automatic deletion' must not be deleted anywhere, or the policy would be wrong.
    expected_deleted = pruned | {t for t, v in TABLES.items() if isinstance(v, (ReportRows, Transient, UntilSwitchedOff, UntilExpiry))}
    assert expiring_in_prune_analytics() == {t for t, v in TABLES.items() if isinstance(v, UntilExpiry)}
    # A replaced list copy is rewritten in place by ahn/gov_registry.py; only the domain rows are deleted from.
    expected_deleted |= {"gov_domain_registry"}
    # The day's host list is rebuilt once a day by ahn/site_probe.py (D67); the policy says it is replaced each day.
    expected_deleted |= {"site_probe_set"}
    # Kept without a period, but a provider that leaves the status-mapping table is unregistered (D44,
    # status_refresh.sync_curated_sources); the policy says so next to "no automatic deletion date".
    expected_deleted |= {"status_cache", "status_sources"}
    for lang, removal in (("en", "When a provider leaves our status-mapping table, its registration and cached status are deleted."),
                          ("ko", "업체가 상태 매핑 표에서 빠지면 그 등록과 상태 캐시를 삭제합니다.")):
        assert removal in section(lang, "06"), f"the {lang} retention section does not say when a status mapping is removed"
    assert deleted_tables_in_code() == expected_deleted, (
        f"Code deletes from {sorted(deleted_tables_in_code())}, the mapping expects {sorted(expected_deleted)}.")


def test_retention_periods_in_code_match_the_policy():
    periods = periods_in_prune_analytics()
    pruned = {t: v for t, v in TABLES.items() if isinstance(v, Pruned)}
    days = {t: periods[t] for t in pruned}
    retention = {lang: section(lang, "06") for lang in LANGS}
    assert_phrases("retention section", retention, {t: v.section_06 for t, v in pruned.items()}, days)
    everywhere = {lang: policy_text(lang) for lang in LANGS}
    assert_phrases("technical details", everywhere, {t: v.technical for t, v in pruned.items() if v.technical}, days)


def test_tables_kept_without_deletion_say_so():
    for table, entry in TABLES.items():
        if not isinstance(entry, Kept):
            continue
        for lang, no_deadline in (("en", "no automatic deletion date"), ("ko", "자동 삭제 기한이 없")):
            text = section(lang, "06")
            wording = getattr(entry.wording, lang)
            assert wording in text, f"{table}: the {lang} retention section lacks {wording!r}"
            sentence = sentence_with(text, wording)
            assert no_deadline in sentence, f"{table}: its {lang} sentence does not say it has {no_deadline!r}: {sentence!r}"


def test_transient_tables_say_when_they_are_deleted():
    for table, entry in TABLES.items():
        if not isinstance(entry, Transient):
            continue
        for lang, when in (("en", "deleted as soon as the read is done, or after five failed attempts"),
                           ("ko", "읽기가 끝나면 바로, 다섯 번 실패하면 그때 삭제")):
            text = section(lang, "06")
            wording = getattr(entry.wording, lang)
            assert wording in text, f"{table}: the {lang} retention section lacks {wording!r}"
            assert when in sentence_with(text, wording), f"{table}: its {lang} sentence does not say {when!r}"
    # "five" in that sentence is the code's limit.
    assert background_reads.MAX_ATTEMPTS == 5


def test_replaced_list_copies_say_so():
    for table, entry in TABLES.items():
        if not isinstance(entry, Replaced):
            continue
        for lang in LANGS:
            text = section(lang, "06")
            wording = getattr(entry.wording, lang)
            assert wording in text, f"{table}: the {lang} retention section lacks {wording!r}"
            paragraph = text[text.index(wording): text.index(wording) + 1200]
            assert getattr(entry.replaced, lang) in paragraph, f"{table}: the {lang} policy does not say the copy is replaced"
    # Only the domain rows are deleted by code, and the code replaces them with the publisher's new version.
    assert "DELETE FROM gov_domain_registry" in inspect.getsource(gov_registry)


def test_government_list_limits_in_code_match_the_policy():
    """The technical details state the schedules, size caps and protections that gov_registry enforces."""
    registries = gov_registry.REGISTRIES
    assert registries[gov_registry.US].check_every == timedelta(hours=24)
    assert registries[gov_registry.UK].check_every == timedelta(days=30)
    assert registries[gov_registry.US].max_bytes == 8_000_000 and registries[gov_registry.UK].max_bytes == 2_000_000
    assert gov_registry.MIN_KEEP_RATIO == 0.5 and gov_registry.FAILURE_RETRY == timedelta(hours=6)
    wanted = {
        "us_schedule": Phrase("checked once every 24 hours", "24시간에 한 번 확인"),
        "uk_schedule": Phrase("checked once every 30 days", "30일에 한 번 확인"),
        "caps": Phrase("larger than 8 MB (U.S.) or 2 MB (UK) is refused", "미국 8 MB·영국 2 MB 를 넘는 파일은 거부"),
        "keep": Phrase("fewer than 50% of the stored rows", "저장된 행의 50% 미만"),
        "retry": Phrase("the next attempt follows 6 hours later", "다음 시도는 6시간 뒤"),
        "no_redirect": Phrase("no redirect followed", "리디렉션은 따라가지 않으며"),
        "ogl": Phrase(gov_registry.UK_ATTRIBUTION, gov_registry.UK_ATTRIBUTION),
        "local": Phrase("The local build does not perform these downloads", "로컬 개발판은 이 내려받기를 하지 않습니다"),
    }
    assert_phrases("government list downloads", {lang: policy_text(lang) for lang in LANGS}, wanted)
    # Every host the code may contact is named by the policy (the lists' publishers, nothing else).
    assert gov_registry.ALLOWED_HOSTS == {"raw.githubusercontent.com", "www.gov.uk", "assets.publishing.service.gov.uk"}
    for lang in LANGS:
        assert "raw.githubusercontent.com" in section(lang, "05") and "GOV.UK" in section(lang, "05")


def test_government_registry_policy_makes_no_verdict_claim():
    """The registry facts are described as facts about a domain, never as a judgement of a site, page or link."""
    for lang, denial in (("en", "not a judgement about any site, page or link"), ("ko", "사이트·페이지·링크에 대한 판단이 아닙니다")):
        assert denial in section(lang, "03")


def test_status_scope_reads_and_limits_in_the_policy_match_the_code():
    # D43: two more reads of an Atlassian Statuspage page per feed refresh that reads an active status,
    # and the stored scope's limits.
    from ahn import status_scope
    assert status_scope.SCOPE_CAP == 32_768 and status_scope.MAX_AFFECTED == 3 and status_scope.AFFECTED_CHARS == 80
    assert status_scope.SCOPE_PATHS == ("incidents/unresolved.json", "scheduled-maintenances/active.json")
    reads = {"scope_reads": Phrase(
        "a feed read that finds an active status also reads two small public lists from the same status page, its unresolved incidents and its "
        "maintenances in progress (at most 32 KB each); nothing else is requested from it",
        "피드를 읽어 활성 상태가 나오면 같은 상태 페이지의 작은 공개 목록 두 개, 미해결 사건과 진행 중 점검(각 최대 32 KB)도 읽으며, 그 밖에는 요청하지 않습니다")}
    assert_phrases("status scope reads", {lang: section(lang, "05") for lang in LANGS}, reads)
    kept = {"scope_kept": Phrase(
        f"at most {status_scope.MAX_AFFECTED} incident or component names as the provider published them "
        f"({status_scope.AFFECTED_CHARS} characters each)",
        f"업체가 공개한 사건·컴포넌트 이름 최대 {status_scope.MAX_AFFECTED}개(각 {status_scope.AFFECTED_CHARS}자)"),
        "rest_dropped": Phrase("nothing else from those two lists is kept", "두 목록의 나머지는 저장하지 않습니다"),
        # The lists' own backoff and refusal rule (status_scope.scope_backoff, read_scope).
        "scope_rest": Phrase("we do not ask for the two lists again for a while (15 minutes, or an hour after a refusal, "
                             "doubling with each further failure, at most a day)",
                             "한동안(15분, 거절 뒤에는 1시간부터, 실패할 때마다 두 배, 최대 하루) 두 목록을 다시 요청하지 않습니다"),
        "scope_refusal": Phrase("after a refusal (401, 403 or 429) of one list the other is not asked for either",
                                "한 목록이 거절(401·403·429)되면 다른 목록도 요청하지 않습니다")}
    assert_phrases("status scope storage", {lang: section(lang, "06") for lang in LANGS}, kept)
    from datetime import timedelta as _td
    assert status_scope.scope_backoff(1, "timeout") == _td(minutes=15) and status_scope.scope_backoff(1, "http_429") == _td(hours=1)
    assert status_scope.scope_backoff(40, "too_large") == status_scope.SCOPE_BACKOFF_MAX == _td(days=1)
    assert status_scope.SCOPE_REFUSALS == {"http_401", "http_403", "http_429"}


def test_scheduled_status_feed_reads_in_the_policy_match_the_code():
    # D44: every curated feed is read on a schedule without a lookup, with the cadence and backoff the code uses.
    from datetime import timedelta as _td

    from ahn import status_refresh
    assert status_refresh.CRON_IDLE == _td(hours=1) and background_reads.FEED_RETRY_MINUTES == 15
    assert background_reads.FEED_BACKOFF_MAX == _td(days=1) and status_refresh.SCOPE_GAP_SECONDS >= 1.0
    assert background_reads.FEED_FAILURE_BACKOFF_MAX == _td(hours=1) < _td(hours=background_reads.FEED_STALE_HOURS)
    assert background_reads.FEED_REFUSALS == {"http_401", "http_403", "http_429"}
    assert background_reads.feed_backoff(1, "timeout") == _td(minutes=15)
    assert background_reads.feed_backoff(9, "http_502", "major") == _td(minutes=15)  # last statement active
    assert background_reads.feed_backoff(30, "http_403") == background_reads.FEED_BACKOFF_MAX
    wanted = {
        "schedule": Phrase("we read the status feed of every provider in our status-mapping table (ahn/status_refresh.py in the "
                           "public source repository) whether or not anyone looked the host up, about once an hour, and every "
                           "15 minutes while the feed reports an active status",
                           "상태 매핑 표(공개 소스 저장소의 ahn/status_refresh.py)에 있는 모든 업체의 상태 피드를 호스트 조회 여부와 "
                           "관계없이 약 1시간마다, 피드가 활성 상태를 발표하는 동안은 15분마다 읽습니다"),
        "address": Phrase("Each read requests only the feed's own address (for most pages /api/v2/status.json), at most one "
                          "request per second to one status host",
                          "그 피드 자체의 주소(대부분 /api/v2/status.json)만 요청하고, 한 상태 호스트에는 초당 최대 한 번만 요청합니다"),
        "backoff": Phrase("A feed that fails is read again after 15 minutes and then at most an hour apart (every 15 minutes "
                          "while its last statement was active)",
                          "실패한 피드는 15분 뒤 다시 읽고 그 뒤로는 최대 1시간 간격으로 읽습니다(마지막 발표가 활성이었으면 15분마다)"),
        "refusal": Phrase("a refusal (401, 403 or 429) is never retried another way, and a feed that keeps refusing is read "
                          "less often, down to once a day",
                          "거절(401·403·429)은 다른 방법으로 다시 시도하지 않으며, 계속 거절하는 피드는 점점 드물게(최대 하루 한 번까지) 읽습니다"),
    }
    assert_phrases("scheduled status feed reads", {lang: section(lang, "05") for lang in LANGS}, wanted)
    for lang, old in (("en", "These reads run in the background after a lookup"), ("ko", "이 읽기는 조회가 기다리는 동안이 아니라")):
        assert old not in section(lang, "05"), f"the {lang} policy still says every read follows a lookup"


def test_site_declared_storage_limits_match_the_policy():
    # D59: the numbers come from ahn/site_declared.py, so the policy and the stored shape cannot drift apart.
    from ahn import site_declared as sd
    limits = {
        "en": ["/.well-known/api-catalog", "/.well-known/tdmrep.json", "/.well-known/ai-catalog.json",
               f"at most {sd.DECLARED_CAP // 1024} KB", "each at most once per robots.txt read",
               f"at most {sd.MAX_APIS} APIs from its API catalog with at most {sd.MAX_LINKS} links of each kind",
               f"at most {sd.MAX_ITEMS} bookmark links and at most {sd.MAX_LINKS} links to further catalogs",
               f"at most {sd.MAX_TDM_RULES} text-and-data-mining reservation rules and at most {sd.MAX_TDM_HEADERS} reservation headers",
               f"at most {sd.MAX_MCP_SERVERS} MCP server entries", f"each link is at most {sd.MAX_URL_CHARS} characters",
               f"each name or pattern at most {sd.MAX_TEXT_CHARS}", "The files themselves are not kept"],
        "ko": ["/.well-known/api-catalog", "/.well-known/tdmrep.json", "/.well-known/ai-catalog.json",
               f"최대 {sd.DECLARED_CAP // 1024} KB", "robots.txt 읽기마다 각각 최대 한 번",
               f"API 최대 {sd.MAX_APIS}개와 종류별 링크 최대 {sd.MAX_LINKS}개",
               f"북마크 링크 최대 {sd.MAX_ITEMS}개와 다른 목록 링크 최대 {sd.MAX_LINKS}개",
               f"유보 규칙 최대 {sd.MAX_TDM_RULES}개와 유보 헤더 최대 {sd.MAX_TDM_HEADERS}개", f"MCP 서버 항목 최대 {sd.MAX_MCP_SERVERS}개",
               f"링크는 각 {sd.MAX_URL_CHARS}자", f"이름·패턴은 각 {sd.MAX_TEXT_CHARS}자", "파일 자체는 저장하지 않습니다"],
    }
    for lang in LANGS:
        missing = [phrase for phrase in limits[lang] if phrase not in policy_text(lang)]
        assert not missing, f"the {lang} policy lacks {missing}"
    assert sd.DECLARED_CAP == 65_536
    # The header list is cut where it is stored.
    stored = sd.read_all("example.com", {path: (False, "robots_disallowed") for path in sd.paths()}, None, hop=0,
                         header_seen=[{"path": "/robots.txt", "tdm_reservation": 1}] * 9, read_at="x")
    assert len(stored["tdm_headers"]) == sd.MAX_TDM_HEADERS


def test_feed_robots_decisions_in_the_policy_match_the_code():
    # D50: the status host's own robots.txt is read before any feed request; what is stored and for how long.
    from datetime import timedelta as _td

    from ahn import feed_robots, status_refresh
    assert feed_robots.DECIDED_TTL == _td(hours=24) and feed_robots.RULE_CHARS == 80
    assert feed_robots.STALE_OK_MAX == _td(hours=48) and feed_robots.ERROR_TTL == _td(hours=1)
    assert feed_robots.ERROR_TTL < feed_robots.DECIDED_TTL and status_refresh.SCOPE_GAP_SECONDS >= 1.0
    reads = {"robots_first": Phrase(
        "Before any feed is read, we read the status host's own /robots.txt (a successful read at most once a day for each host, a read that fails "
        "tried again after an hour, a second or more before the feed request) and request a feed path only if that file allows it for AgentHelpNetwork",
        "피드를 읽기 전에 먼저 그 상태 호스트 자신의 /robots.txt 를 읽고(성공한 읽기는 호스트마다 하루 한 번 이하, 읽지 못하면 한 시간 뒤 다시, "
        "피드 요청보다 1초 이상 앞서), 그 파일이 AgentHelpNetwork 에게 허용하는 피드 경로만 요청합니다"),
        "error_is_no": Phrase("if the file cannot be read because of a server error or a timeout, we treat the host as disallowing everything until it can be read, "
                              "except that a file we read successfully less than 48 hours earlier keeps deciding in the meantime",
                              "서버 오류나 시간 초과로 그 파일을 읽지 못하면 읽을 수 있을 때까지 그 호스트가 전부 금지한 것으로 봅니다. "
                              "다만 48시간 안에 읽는 데 성공한 파일은 읽지 못하는 동안에도 계속 그 판정을 따릅니다"),
        "blocked_not_read": Phrase("A feed whose robots.txt disallows it is not read at all, and for that host we show a link to the official status page instead of a reading",
                                   "robots.txt 가 금지한 피드는 읽지 않으며, 그 호스트에는 읽은 값 대신 공식 상태 페이지 링크를 보여 줍니다")}
    assert_phrases("feed robots reads", {lang: section(lang, "05") for lang in LANGS}, reads)
    kept = {"kept": Phrase(
        f"the rule line that decided it (at most {feed_robots.RULE_CHARS} characters), the parser version, a hash of the file and the time it was read",
        f"그것을 정한 규칙 줄(최대 {feed_robots.RULE_CHARS}자), 해석기 버전, 파일 해시, 읽은 시각도 두며"),
        "ended": Phrase("It also keeps how the read ended (found, missing, unavailable or failed, with a short label such as http_503 or, for a redirect "
                        "we do not follow, the host it pointed to), which group of the file decided (the one naming AgentHelpNetwork or the * group), "
                        "and, when a later read failed while an earlier successful one kept deciding, when and why",
                        "읽기가 어떻게 끝났는지(읽음·없음·사용 불가·실패와 http_503 같은 짧은 라벨, 따르지 않은 리다이렉트는 가리킨 호스트), "
                        "판정한 그룹(AgentHelpNetwork 를 지명한 그룹인지 * 그룹인지), 앞서 성공한 읽기가 판정을 이어 가는 동안 뒤의 읽기가 실패했다면 그 시각과 이유도 함께 둡니다")}
    assert_phrases("feed robots storage", {lang: section(lang, "06") for lang in LANGS}, kept)


def test_atom_history_reads_in_the_policy_match_the_code():
    # D52: where only the status API is disallowed, the page's Atom history is read as an open-announcement signal.
    from ahn import feed_robots, status_atom, status_refresh
    assert status_atom.ATOM_CAP == 128 * 1024 and feed_robots.HISTORY_PATH == status_refresh.HISTORY_PATH == "/history.atom"
    # D57: an open Atom incident is read on the active interval.
    from ahn import background_reads
    assert background_reads.FEED_RETRY_MINUTES == 15
    assert background_reads.history_incident_open({"feed_source": "history_atom", "active_incidents": 1})
    reads = {"atom": Phrase(
        "where a Statuspage page's robots.txt disallows its status API but allows its public incident-history feed (/history.atom), "
        "we read that feed instead, at the same frequency (every 15 minutes while it lists an open incident) and under the same "
        "limits (at most 128 KB, no redirect followed)",
        "Statuspage 페이지의 robots.txt 가 상태 API 는 금지하지만 공개 사건 이력 피드(/history.atom)는 허용하면 "
        "같은 빈도(열린 사건이 있는 동안은 15분마다)와 같은 제한(최대 128 KB, 리다이렉트를 따르지 않음)으로 그 피드를 대신 읽습니다"),
        "unknown_not_operational": Phrase(
            "we report only whether an announced incident is currently open; when none is listed we report the provider's status as unknown, not as operational",
            "발표된 사건이 지금 열려 있는지만 알리고, 열린 사건이 없으면 정상이 아니라 알 수 없음으로 알립니다")}
    assert_phrases("atom history reads", {lang: section(lang, "05") for lang in LANGS}, reads)
    kept = {"kept": Phrase(
        "we keep only the number of open incidents, the latest update time of the open incidents, the number of maintenances in progress "
        "(left empty when that feed cannot be trusted for it), whether the feed's window is too short to be trusted, and that its source is that feed",
        "열린 사건 수, 열린 사건의 가장 늦은 갱신 시각, 진행 중 점검 수(그 피드로는 믿을 수 없으면 비워 둠), "
        "피드의 창이 너무 짧아 믿을 수 없는지 여부, 출처가 그 피드라는 표시만 두고")}
    assert_phrases("atom history storage", {lang: section(lang, "06") for lang in LANGS}, kept)


def test_stored_provenance_is_described_in_the_policy():
    # Migration 020 stores, with each cached read, a hash of the bytes received and who read it from where,
    # and, with each report, the interface it arrived through.
    wanted = {"hash": Phrase("SHA-256 hash of the file or feed as received (never the file itself)",
                             "받은 파일·피드의 SHA-256 해시(원문은 저장하지 않음)"),
              "provenance": Phrase("the reader version, and the operator and declared network location that made the read",
                                   "읽기 프로그램 버전, 읽은 운영자와 선언한 네트워크 위치"),
              "channel": Phrase("which interface it arrived through", "제보마다 들어온 경로"),
              "export": Phrase("operator-only export is not a public endpoint, and exported reports contain only the fields the public answer shows",
                               "운영자용 내보내기는 공개 엔드포인트가 아니고 내보낸 제보에는 공개 응답에 나오는 필드만 담깁니다")}
    assert_phrases("provenance", {lang: policy_text(lang) for lang in LANGS}, wanted)


# --- 5. Columns added to existing tables --------------------------------------------------------

# Columns that ALTER TABLE ... ADD COLUMN adds from migration 020 on. Earlier columns predate this
# check and are covered by their table's wording above. A new column must name what it stores here.
FIRST_CHECKED_MIGRATION = 20
_READ_BY = Phrase("the reader version, and the operator and declared network location that made the read",
                  "읽기 프로그램 버전, 읽은 운영자와 선언한 네트워크 위치")
_HASH = Phrase("SHA-256 hash of the file or feed as received (never the file itself)", "받은 파일·피드의 SHA-256 해시(원문은 저장하지 않음)")
_EVIDENCE = Phrase("the evidence that it belongs to the host (its DNS delegation), the page address and the operator that recorded it",
                   "그 호스트의 것이라는 근거(DNS 위임), 페이지 주소, 기록한 운영자")
_RECEIVED_BY = Phrase("the operator that received it together with its own report ID", "그 제보를 받은 운영자 및 그 운영자의 제보 ID")
_SCOPE_FAILURES = Phrase("how many times in a row that happened, when, and a short label for why",
                         "연속 실패 횟수, 시각, 이유를 나타내는 짧은 라벨")
_SCOPE_COUNTS = Phrase("the number of unresolved incidents and of maintenances in progress", "미해결 사건 수와 진행 중 점검 수")
COLUMNS = {
    "status_cache.failures": Phrase("the number of consecutive failed reads of its feed", "그 피드를 연속으로 읽지 못한 횟수"),
    "status_cache.active_incidents": _SCOPE_COUNTS,
    "status_cache.active_maintenances": _SCOPE_COUNTS,
    "status_cache.max_incident_impact": Phrase("the highest incident impact", "가장 높은 사건 영향"),
    "status_cache.incident_updated_at": Phrase("the latest incident update time", "가장 늦은 사건 갱신 시각"),
    "status_cache.affected": Phrase("at most 3 incident or component names as the provider published them (80 characters each)",
                                    "업체가 공개한 사건·컴포넌트 이름 최대 3개(각 80자)"),
    "status_cache.affected_regional": Phrase("whether every affected component is in a region or point-of-presence group",
                                             "영향받는 컴포넌트가 모두 지역·거점 그룹에 속하는지"),
    "status_cache.feed_source": Phrase("and that its source is that feed", "출처가 그 피드라는 표시"),
    "status_cache.window_blind": Phrase("whether the feed's window is too short to be trusted", "피드의 창이 너무 짧아 믿을 수 없는지 여부"),
    "status_cache.scope_failures": _SCOPE_FAILURES,
    "status_cache.scope_failed_at": _SCOPE_FAILURES,
    "status_cache.scope_error": _SCOPE_FAILURES,
    "host_access_profile.origin_operator": _READ_BY,
    "host_access_profile.vantage": _READ_BY,
    "host_access_profile.reader_version": _READ_BY,
    "host_access_profile.robots_sha256": _HASH,
    "host_access_profile.llms_sha256": _HASH,
    "host_access_profile.content_use": Phrase(
        "content-use preferences it declares (Content-Signal and Content-Usage lines) as normalized yes, no or unset values",
        "콘텐츠 이용 선호(Content-Signal·Content-Usage 줄)를 yes·no·unset 으로 정규화한 값"),
    "host_access_profile.home_gate": Phrase(
        "whether the host's robots.txt allows the home page", "그 호스트의 robots.txt 가 홈 페이지를 허용하는지"),
    "host_access_profile.site_declared": Phrase(
        "what the host publishes for agents at those three standard locations",
        "호스트가 그 세 표준 위치에 에이전트용으로 공개한 것"),
    "status_cache.origin_operator": _READ_BY,
    "status_cache.vantage": _READ_BY,
    "status_cache.reader_version": _READ_BY,
    "status_cache.body_sha256": _HASH,
    "status_sources.curated_by": _EVIDENCE,
    "status_sources.evidence": _EVIDENCE,
    "status_sources.evidence_url": _EVIDENCE,
    "observations.origin_operator": _RECEIVED_BY,
    "observations.origin_observation_id": _RECEIVED_BY,
    "observations.received_channel": Phrase("which interface it arrived through", "제보마다 들어온 경로"),
}


def migration_added_columns() -> set[str]:
    found: set[str] = set()
    for path in sorted((ROOT / "migrations").glob("*.sql")):
        if int(path.name[:3]) < FIRST_CHECKED_MIGRATION:
            continue
        text = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))
        for table, body in re.findall(r"ALTER TABLE\s+(?:IF EXISTS\s+)?([a-z_]+)(.*?);", text, re.S):
            found |= {f"{table}.{column}" for column in re.findall(r"ADD COLUMN\s+(?:IF NOT EXISTS\s+)?([a-z_0-9]+)", body)}
    return found


def test_every_added_column_is_described_in_the_policy():
    added = migration_added_columns()
    assert added, "no ADD COLUMN found: the parser no longer reads the migrations"
    unmapped = added - set(COLUMNS)
    assert not unmapped, (f"Migrations add columns {sorted(unmapped)}. Say what they store in the privacy policy "
                          "(English and Korean), then map them in COLUMNS.")
    assert not set(COLUMNS) - added, f"COLUMNS lists columns no migration adds: {sorted(set(COLUMNS) - added)}"
    assert_phrases("added columns", {lang: policy_text(lang) for lang in LANGS},
                   {name: phrase for name, phrase in COLUMNS.items() if isinstance(phrase, Phrase)})


def test_report_retention_periods_match_the_policy():
    source = inspect.getsource(platform_metrics.prune_reports)
    assert "WITHDRAWN_REPORT_GRACE_DAYS" in source, "prune_reports no longer uses the grace constant; update the policy test"
    assert {t for t in re.findall(r"DELETE FROM ([a-z_]+)", source)} == {t for t, v in TABLES.items() if isinstance(v, ReportRows)}
    # With a retention period configured, the policy and the technical details both state it and the grace.
    wording = {
        "section_06": Phrase(f"deleted {REPORT_RETENTION_DAYS} days after they are received; a withdrawn report is excluded from every read at once and deleted {GRACE_DAYS} days after withdrawal",
                             f"접수 {REPORT_RETENTION_DAYS}일 뒤 삭제됩니다. 철회한 제보는 즉시 모든 조회에서 빠지고 철회 {GRACE_DAYS}일 뒤 삭제됩니다"),
    }
    assert_phrases("report retention", {lang: section(lang, "06", True, REPORT_RETENTION_DAYS) for lang in LANGS}, wording)
    technical = {"limits": Phrase(f"deleted {REPORT_RETENTION_DAYS} days after they are received; a withdrawn report leaves every read at once and is deleted {GRACE_DAYS} days later",
                                   f"접수 {REPORT_RETENTION_DAYS}일 뒤 삭제되며, 철회한 제보는 즉시 모든 조회에서 빠지고 {GRACE_DAYS}일 뒤 삭제됩니다")}
    assert_phrases("report retention in the technical details", {lang: policy_text(lang, True, REPORT_RETENTION_DAYS) for lang in LANGS}, technical)
    # Without one, the policy must say there is no deletion date instead of promising a period.
    none = {"en": "no automatic deletion date during the beta", "ko": "베타 기간 동안 자동 삭제 기한이 없"}
    for lang in LANGS:
        assert none[lang] in section(lang, "06", True, 0), f"the {lang} policy omits the no-deletion statement for reports"


def test_content_use_storage_limits_match_the_policy():
    # The numbers come from the code, so the policy and the stored shape cannot drift apart.
    from ahn import access_profile as ap
    limits = {
        "en": [f"at most {ap.MAX_USE_PATH_SAMPLES} sample paths in total", f"agent names of at most {ap.MAX_USE_NAMED_GROUPS} user-agent groups",
               "only names on our list of known agent tokens", f"at most {ap.MAX_USE_GROUP_AGENTS} per group",
               "any other name is only counted", f"at most {ap.MAX_USE_IGNORED} names of keys we could not read",
               "the parser version and the robots.txt hash"],
        "ko": [f"경로 표본 전체 최대 {ap.MAX_USE_PATH_SAMPLES}개", f"user-agent 그룹 최대 {ap.MAX_USE_NAMED_GROUPS}개의 에이전트 이름",
               "알려진 에이전트 토큰 목록에 있는 이름만", f"그룹당 최대 {ap.MAX_USE_GROUP_AGENTS}개", "그 밖의 이름은 개수만",
               f"읽지 못한 키 이름 최대 {ap.MAX_USE_IGNORED}개", "해석기 버전과 robots.txt 해시"],
    }
    for lang in LANGS:
        for where in (section(lang, "06"), policy_text(lang)):
            missing = [phrase for phrase in limits[lang] if phrase not in where]
            assert not missing, f"the {lang} policy lacks {missing}"
    # And the parser keeps to those numbers on a file built to exceed every one of them.
    groups = "".join(f"User-agent: bot{i}\nUser-agent: ChatGPT-User\nUser-agent: GPTBot\nContent-Signal: ai-train={'no' if i % 2 else 'yes'}, k{i}=1, search={'yes' if i % 3 else 'no'}\n"
                     f"Content-Usage: /p{i}a/ train-ai=n\nContent-Usage: /p{i}b/ train-ai=n\n\n" for i in range(40))
    parsed = ap.parse_content_use("User-agent: *\n" + "".join(f"Content-Usage: /s{i}/ train-ai=n\n" for i in range(2)) + "\n" + groups)
    paths = len(parsed["path_specific_samples"]) + sum(len(group["path_specific_samples"]) for group in parsed["named_groups"])
    assert paths <= ap.MAX_USE_PATH_SAMPLES and len(parsed["named_groups"]) <= ap.MAX_USE_NAMED_GROUPS
    assert len(parsed["ignored_keys"]) <= ap.MAX_USE_IGNORED
    known = {token for token, _kind in ap.AGENT_USER_AGENTS}
    for group in parsed["named_groups"]:
        assert len(group["agents"]) <= ap.MAX_USE_GROUP_AGENTS and set(group["agents"]) <= known and group["other_agents"] >= 1


# --- 6. Offline data bundle ---------------------------------------------------------------------

# What ahn/bundle.py puts in the file and what it never does, matched to policy section 05. A new kind or a
# changed exclusion in the code fails here until the policy says so.
BUNDLE_INCLUDES = {
    "access_profile": Phrase("the robots.txt and llms.txt reads of the hosts on the curated part of our public seed list",
                             "공개 시드 목록 중 큐레이션한 부분에 있는 호스트의 robots.txt·llms.txt 판독 결과"),
    "official_path": Phrase("the curated official developer paths", "큐레이션한 공식 개발자 경로"),
    "status_source": Phrase("the curated status-page mappings", "큐레이션한 상태 페이지 매핑"),
}
BUNDLE_EXCLUDES = {
    "reports": Phrase("reports", "제보"),
    "looked_up_hosts_outside_seed_list": Phrase("hostnames people looked up that are not on the seed list", "시드 목록에 없는 조회된 호스트명"),
    "lookup_counts": Phrase("lookup counts", "조회 수"),
    "request_logs": Phrase("request logs", "요청 기록"),
    "ip_digests": Phrase("IP digests", "IP 변환값"),
    "reporter_keys": Phrase("reporter keys", "제보자 키"),
    "status_feed_values": Phrase("a status feed's current value", "상태 피드의 현재 값"),
    "ranking_list_seed_hosts": Phrase("seed hosts taken only from a third-party popularity ranking", "제3자 인기 순위에서만 가져온 시드 호스트"),
    "seed_order_or_rank": Phrase("the seed list's order or rank", "시드 목록의 순서나 순위"),
}


BUNDLE_SINCE = date(2026, 11, 3)  # later than the policy's own date, so the switch must move "Last updated"
BUNDLE_SELECTION = Phrase("Many of those mapped hosts were found by scanning bands of the Tranco popularity ranking, but each "
                          "mapping was checked on that host's own status page, and no rank is included",
                          "매핑한 호스트 중 상당수는 Tranco 인기 순위의 구간을 훑어 찾았지만, 매핑마다 그 호스트의 상태 페이지에서 "
                          "직접 확인했으며 순위는 담지 않습니다")
BUNDLE_QUOTES = Phrase("short quotes from publisher pages stay the publishers' material and are not covered by that license",
                       "업체 문서 짧은 인용은 업체의 저작물이라 이 라이선스 대상이 아닙니다")
BUNDLE_DAY = Phrase("Read times are given to the day only", "판독 시각은 날짜까지만 적습니다")


def bundle_policy_text(lang: str, public_bundle: bool, since: date | None = BUNDLE_SINCE) -> str:
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True,
                        public_bundle=public_bundle, public_bundle_since=since if public_bundle else None)
    response = TestClient(create_app(settings)).get(f"/{lang}/policy")
    assert response.status_code == 200
    return response.text


def test_offline_bundle_is_described_while_it_is_served():
    assert set(BUNDLE_INCLUDES) == set(bundle.KINDS), "bundle kinds changed: describe them in policy section 05"
    assert set(BUNDLE_EXCLUDES) == set(bundle.EXCLUDED), "bundle exclusions changed: state them in policy section 05"
    pages = {lang: bundle_policy_text(lang, True) for lang in LANGS}
    sections = {}
    for lang, markup in pages.items():
        found = re.search(r'<article><span class="policy-index">05</span>.*?</article>', markup, re.S)
        assert found, f"policy section 05 not found in the {lang} page"
        sections[lang] = plain(found.group())
    for lang in LANGS:
        assert bundle.PATH in sections[lang] and "CC BY 4.0" in sections[lang], f"the {lang} policy does not name the bundle and its license"
    assert_phrases("bundle contents", sections, BUNDLE_INCLUDES)
    assert_phrases("bundle selection, quotes and times", sections,
                   {"selection": BUNDLE_SELECTION, "quotes": BUNDLE_QUOTES, "day": BUNDLE_DAY})
    never = {"en": sentence_with(sections["en"], "It never contains"), "ko": sentence_with(sections["ko"], "절대 담지 않습니다")}
    assert_phrases("bundle exclusions", never, BUNDLE_EXCLUDES)


def test_offline_bundle_is_not_described_while_it_is_off():
    for lang in LANGS:
        text = plain(bundle_policy_text(lang, False))
        assert "/v1/bundle/" not in text and "CC BY 4.0" not in text, f"the {lang} policy describes a bundle that is not served"
        for entry in TABLES.values():
            if isinstance(entry, UntilSwitchedOff):
                assert getattr(entry.wording, lang) not in text


def test_offline_bundle_row_retention_is_stated_while_it_is_served():
    for lang, when, memory in (("en", "deleted when the bundle is switched off", "counted in memory only"),
                               ("ko", "번들을 끄면 삭제", "메모리에서만 세고 저장하지 않습니다")):
        found = re.search(r'<article><span class="policy-index">06</span>.*?</article>', bundle_policy_text(lang, True), re.S)
        text = plain(found.group())
        for table, entry in TABLES.items():
            if isinstance(entry, UntilSwitchedOff):
                wording = getattr(entry.wording, lang)
                assert wording in text, f"{table}: the {lang} retention section lacks {wording!r}"
                assert when in sentence_with(text, wording), f"{table}: its {lang} sentence does not say {when!r}"
        assert memory in text, f"the {lang} retention section does not say per-client download limits stay in memory"


def test_policy_date_moves_with_the_bundle_switch():
    from ahn.main import POLICY_UPDATED

    off = {"en": f"Last updated {POLICY_UPDATED:%B} {POLICY_UPDATED.day}, {POLICY_UPDATED.year}.",
           "ko": f"최종 수정: {POLICY_UPDATED.year}년 {POLICY_UPDATED.month}월 {POLICY_UPDATED.day}일."}
    on = {"en": "Last updated November 3, 2026.", "ko": "최종 수정: 2026년 11월 3일."}
    for lang in LANGS:
        assert off[lang] in plain(bundle_policy_text(lang, False)), f"the {lang} policy date changed without the switch"
        assert on[lang] in plain(bundle_policy_text(lang, True)), f"the {lang} policy date does not follow AHN_PUBLIC_BUNDLE_SINCE"
        # A since date before the policy's own date never moves the date backwards.
        assert off[lang] in plain(bundle_policy_text(lang, True, date(2026, 1, 1)))


# --- 7. Signal history (D41) -------------------------------------------------------------------

def test_signal_history_is_described_as_host_facts_kept_private_for_now():
    """The history of reads and provider changes: what it holds, that refusals are not problems, that it is not public."""
    from ahn import signal_history
    assert signal_history.RAW_RETENTION_DAYS == periods_in_prune_analytics()["robots_read_events"] == 14
    for table in ("outage_episodes", "provider_status_events", "robots_read_daily"):
        assert periods_in_prune_analytics()[table] == signal_history.ROLLUP_RETENTION_DAYS == 90
    wanted = {
        "scope": Phrase("For curated public hostnames only", "큐레이션한 공개 호스트명"),
        "what": Phrase("a DNS, connection, TLS, timeout or server (5xx) error", "DNS·연결·TLS·시간 초과·서버 오류(5xx)"),
        "refusal": Phrase("A refused read (401, 403, 429 or 503) is never counted as a problem with the site",
                          "거절된 읽기(401·403·429·503)는 사이트의 문제로 세지 않습니다"),
        "no_people": Phrase("they contain no lookup, report, IP address or client information",
                            "조회·제보·IP 주소·클라이언트 정보가 없으며"),
        "trigger": Phrase("whether a read followed a lookup is kept only as a daily count per host",
                          "읽기가 조회 뒤의 읽기였는지는 호스트별 일일 개수로만 둡니다"),
        "no_request": Phrase("Keeping them adds no request to any site", "어떤 사이트에도 요청이 늘지 않습니다"),
        "owner_view": Phrase("a private dashboard that shows the hostnames of current problem candidates",
                             "현재 문제 후보의 호스트명과 집계를 보여 주는 비공개 화면"),
        "private": Phrase("they are not shown on public pages or in lookup answers", "공개 페이지나 조회 응답에는 나오지 않습니다"),
        "open_provider_period": Phrase("a provider-reported problem that is still open is kept until it ends",
                                       "업체가 아직 문제를 발표 중인 구간은 끝날 때까지 둡니다"),
    }
    assert_phrases("signal history", {lang: policy_text(lang) for lang in LANGS}, wanted)


def board_policy_text(lang: str, ahn_check: bool) -> str:
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True,
                        public_board=True, public_board_since=date(2026, 10, 27), public_board_ahn_check=ahn_check)
    response = TestClient(create_app(settings)).get(f"/{lang}/policy")
    assert response.status_code == 200
    return plain(response.text)


def test_public_board_policy_states_its_window_and_what_it_never_shows():
    """D53: while the board is on, the policy names it, its 7-day window (the code constant) and what stays private."""
    from ahn import public_board
    from ahn.triage import STALE_INCIDENT_DAYS

    assert STALE_INCIDENT_DAYS == 7
    window = {"en": f"within the last {STALE_INCIDENT_DAYS} days", "ko": f"최근 {STALE_INCIDENT_DAYS}일 안에"}
    for ahn_check in (False, True):
        for lang in LANGS:
            text = board_policy_text(lang, ahn_check)
            assert public_board.PATH in text and f"/{lang}/now" in text, f"the {lang} policy does not name the board"
            assert public_board.POLICY_BEFORE[lang] not in text, f"the {lang} policy still says the history is private only"
            if not ahn_check:
                assert window[lang] in text, f"the {lang} policy does not state the {STALE_INCIDENT_DAYS}-day window"
                never = {"en": "no refused read", "ko": "거절된 읽기"}[lang]
                anchor = {"en": "It shows no robots.txt read result", "ko": "robots.txt 읽기 결과"}[lang]
                assert never in sentence_with(text, anchor), f"the {lang} policy does not exclude refused reads"
            else:
                never = {"en": "Refused reads are never shown there", "ko": "거절된 읽기는 그곳에 나오지 않습니다"}[lang]
                assert never in text, f"the {lang} policy does not exclude refused reads from the AHN-check column"


# Every column of the signal-history tables (migration 024) and the policy wording that names it.
_SH_READ = {
    "site_host": Phrase("we keep the hostname", "호스트명"), "read_at": Phrase("the time", "시각"),
    "read_host": Phrase("the host that was read (the host itself or its www. alias)", "읽은 호스트(그 호스트 또는 www. 별칭)"),
    "robots_status": Phrase("the read status", "읽기 상태"),
    "detail": Phrase("for an answer its HTTP status code or, for a redirect, the target hostname",
                     "응답이면 HTTP 상태 코드, 리디렉션이면 대상 호스트명"),
    "a_outcome": Phrase("our classification of the result", "우리가 매긴 분류"),
    "origin_operator": Phrase("the operator and declared network location that made the read", "읽은 운영자와 선언한 네트워크 위치"),
    "vantage": Phrase("the operator and declared network location that made the read", "읽은 운영자와 선언한 네트워크 위치"),
    "applied_at": Phrase("once our summary job has processed it (we keep that time)", "요약 작업이 처리한 뒤에는 처리 시각"),
    "held": Phrase("whether its 15-minute window was held as a possible problem on our side and why",
                   "그 15분 구간을 우리 쪽 문제 가능성 때문에 보류했는지와 그 사유"),
    "hold_reason": Phrase("whether its 15-minute window was held as a possible problem on our side and why",
                          "그 15분 구간을 우리 쪽 문제 가능성 때문에 보류했는지와 그 사유"),
}
_SH_PERIOD_TIMES = Phrase("start, confirmation and end times", "시작·확인·종료 시각")
_SH_PERIOD_FEED = Phrase("whether a provider feed was registered for the host and reported a problem at the same time",
                         "그 호스트에 업체 피드가 등록돼 있었는지와 같은 때 문제를 발표했는지")
_SH_PERIOD_COUNTS = Phrase("the numbers of failed, held and answering reads in it", "그 안의 실패·보류·응답 읽기 수")
_SH_DAILY = Phrase("daily totals of reads, answers, failures, refusals and held reads", "일일 읽기·응답·실패·거절·보류 합계")
_SH_TRIGGER = Phrase("with the number of reads that followed a lookup and of seed-list reads", "조회 뒤의 읽기 수와 시드 목록 읽기 수")
_SH_PROVIDER = Phrase("each change of its published indicator", "발표한 상태 값이 바뀔 때마다 그 변화")
SIGNAL_HISTORY_COLUMNS = {
    "robots_read_events": {"id": None, **_SH_READ},
    "provider_status_events": {
        "id": None, "site_host": _SH_PROVIDER, "at": _SH_PROVIDER, "from_indicator": _SH_PROVIDER, "to_indicator": _SH_PROVIDER,
        "source_updated_at": Phrase("with the provider's own update time", "업체 자신의 갱신 시각"),
        "applied_at": None,  # when the summary job applied the change: bookkeeping about our own processing
    },
    "outage_episodes": {
        "id": None, "site_host": Phrase("periods in which a host's robots.txt could not be read", "호스트의 robots.txt 를 읽지 못한 구간"),
        "signal": Phrase("Periods in which a curated host's robots.txt could not be read or its provider reported a problem",
                         "큐레이션한 호스트의 robots.txt 를 읽지 못한 구간이나 업체가 문제를 발표한 구간"),
        "kind": Phrase("for a failure its kind", "실패면 DNS·연결·TLS·시간 초과·서버 오류(5xx) 같은 종류"),
        "first_fail_at": _SH_PERIOD_TIMES, "confirmed_at": _SH_PERIOD_TIMES, "ended_at": _SH_PERIOD_TIMES,
        "last_event_at": Phrase("90 days after their last change", "90일(마지막 변화 기준)"),
        "ended_reason": Phrase("how the period ended", "끝난 방식"),
        "reads": _SH_PERIOD_COUNTS, "ok_streak": _SH_PERIOD_COUNTS, "held_overlap": _SH_PERIOD_COUNTS,
        "feed_connected": _SH_PERIOD_FEED, "matched_episode_id": _SH_PERIOD_FEED,
    },
    "robots_read_daily": {
        "site_host": _SH_DAILY, "day": _SH_DAILY, "reads": _SH_DAILY, "responding": _SH_DAILY, "failed": _SH_DAILY,
        "refused": _SH_DAILY, "held": _SH_DAILY, "lookup_reads": _SH_TRIGGER, "seed_reads": _SH_TRIGGER,
    },
}


def test_every_signal_history_column_is_named_in_the_policy():
    text = (ROOT / "migrations" / "024_signal_history.sql").read_text(encoding="utf-8")
    for table, columns in SIGNAL_HISTORY_COLUMNS.items():
        body = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", text, re.S).group(1)
        found = {line.split()[0] for line in body.strip().splitlines() if line.strip() and not line.strip().startswith("PRIMARY KEY")}
        assert found == set(columns), f"{table}: columns {sorted(found)} differ from the policy mapping {sorted(columns)}"
    wanted = {f"{table}.{column}": phrase for table, columns in SIGNAL_HISTORY_COLUMNS.items()
              for column, phrase in columns.items() if phrase is not None}
    assert_phrases("signal history columns", {lang: policy_text(lang) for lang in LANGS}, wanted)
    # Whether a read followed a lookup is never kept per read (only as a daily count).
    assert "trigger" not in SIGNAL_HISTORY_COLUMNS["robots_read_events"]


def test_seed_read_retry_in_the_policy_matches_the_code():
    from ahn import profile_warm
    assert profile_warm.WARM_ERROR_RETRY == timedelta(hours=24)
    wanted = {"seed_retry": Phrase("a transport failure there is retried after 24 hours",
                                   "여기서는 전송 실패를 24시간 뒤 다시 시도합니다")}
    assert_phrases("seed read retry", {lang: policy_text(lang) for lang in LANGS}, wanted)
    for lang, stale in (("en", "a transport failure there is also retried after one hour"), ("ko", "여기서도 전송 실패는 1시간 뒤")):
        assert stale not in policy_text(lang)


_LS_COUNT = Phrase("We count a lookup and these codes per public hostname and hour", "조회와 이 코드는 공개 호스트명과 시간 단위로")
_LS_DIGEST = Phrase("Keyed IP digests specific to one hostname and one UTC day", "호스트명과 날짜(UTC)에만 해당하도록 비밀키로 변환한 IP 값")
_LS_ACTION = Phrase("the recommendation it was answered with", "그때 준 권고")
LOOKUP_SIGNAL_COLUMNS = {
    "lookup_signals_hourly": {
        "hour": _LS_COUNT, "site_host": _LS_COUNT, "error_class": _LS_COUNT, "failure_stage": _LS_COUNT,
        "http_status_class": _LS_COUNT, "challenge": _LS_COUNT, "retry_after": _LS_COUNT, "via": _LS_COUNT, "lookups": _LS_COUNT,
        "client_kind": Phrase("together with the kind of client software that sent it", "보낸 클라이언트 프로그램의 종류와 함께 셉니다"),
        "action": _LS_ACTION,
        "after_action": Phrase("so that a second lookup of the same hostname soon after can be counted", "곧 이어 같은 호스트명을 다시 조회한 것을 셀 수 있습니다"),
        "sources": Phrase("how many separate clients sent them", "그것을 보낸 서로 다른 클라이언트 수"),
    },
    "lookup_sources_daily": {
        "day": _LS_DIGEST, "site_host": _LS_DIGEST, "source_digest": _LS_DIGEST,
        "last_at": Phrase("last lookup of it (kept to the minute for about an hour, until the next cleanup after it is 61 minutes old, then cut to the hour)",
                          "마지막 조회 시각(약 1시간 동안, 61분이 지난 뒤 처음 도는 정리 때까지만 분 단위로 두고 이후 시 단위로 낮춤)"),
        "last_group": Phrase("the kind of blocked lookup it was (outage, block, rate limit or sign-in)", "막힘 유형(장애·차단·속도 제한·로그인 중 하나)"),
        "last_action": _LS_ACTION,
    },
}


def test_every_lookup_signal_column_is_named_in_the_policy():
    text = (ROOT / "migrations" / "027_lookup_signals.sql").read_text(encoding="utf-8")
    for table, columns in LOOKUP_SIGNAL_COLUMNS.items():
        body = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", text, re.S).group(1)
        found = {line.split()[0] for line in body.strip().splitlines() if line.strip() and not line.strip().startswith("PRIMARY KEY")}
        assert found == set(columns), f"{table}: columns {sorted(found)} differ from the policy mapping {sorted(columns)}"
    wanted = {f"{table}.{column}": phrase for table, columns in LOOKUP_SIGNAL_COLUMNS.items() for column, phrase in columns.items()}
    assert_phrases("lookup signal columns", {lang: policy_text(lang) for lang in LANGS}, wanted)
    # The minute-precision claim: the cleanup literal and the constant agree with the policy's 61.
    from ahn import lookup_signals
    assert lookup_signals.MINUTE_PRECISION_MINUTES == 61 and "interval '61 minutes'" in inspect.getsource(platform_metrics.cut_source_minutes)
    for lang, claim in (("en", "A cleanup job that runs every 15 minutes deletes expired rows"), ("ko", "15분마다 도는 정리 작업")):
        assert claim in section(lang, "06"), f"the {lang} policy does not say how often the cleanup runs"


def test_lookup_signal_retention_and_exclusions_match_the_code():
    from ahn import lookup_signals
    periods = periods_in_prune_analytics()
    assert periods["lookup_signals_hourly"] == lookup_signals.HOURLY_RETENTION_DAYS == 35
    assert periods["lookup_sources_daily"] == lookup_signals.SOURCES_RETENTION_DAYS == 2
    wording = {
        "en": ("Lookups from the operator's own network and requests marked as tests are left out of these counts",
               "the hourly counts hold no IP address", "never become reports",
               "the lookup-code counts described under Data we collect are not shown publicly or in answers at present",
               "only when at least three separate clients contributed", "Feedback outcome counts are not shown publicly or in any answer"),
        "ko": ("운영자 자신의 네트워크에서 온 조회와 시험으로 표시된 요청은 이 집계에서 뺍니다", "시간 단위 집계에는 IP 주소가 없습니다",
               "제보가 되지 않고", "조회 코드 집계는 현재 공개 화면이나 답변에 나오지 않으며", "서로 다른 클라이언트 세 곳 이상이 있을 때만",
               "결과 회신 집계는 공개 화면에도 어떤 답변에도 나오지 않습니다"),
    }
    for lang, phrases in wording.items():
        text = policy_text(lang)
        for phrase in phrases:
            assert phrase in text or phrase[0].upper() + phrase[1:] in text, f"the {lang} policy lacks {phrase!r}"
    # The policy promises the shadow-mode behaviour: no response field shows the codes yet.
    assert "lookup_signals" not in CheckResponse.model_fields


# --- Lookup feedback (D58) -----------------------------------------------------------------------

def test_lookup_feedback_is_described_with_the_code_limits():
    from ahn import feedback
    from ahn.models import FeedbackIn, FeedbackOutcome, FeedbackPath
    assert periods_in_prune_analytics()["lookup_feedback_hourly"] == feedback.HOURLY_RETENTION_DAYS == 35
    assert feedback.TOKEN_TTL_SECONDS == 3600  # "one hour" / "1시간" below
    # The tool and the REST body take these and nothing else; each is named in section 02.
    assert set(registered_tools(False)["rate_last_lookup"]) == set(FeedbackIn.model_fields) == {"token", "outcome", "path"}
    wanted = {
        "tool": Phrase("rate_last_lookup tool or POST /v1/feedback", "rate_last_lookup 도구나 POST /v1/feedback"),
        "outcome": Phrase("outcome code (succeeded, still blocked, handed off or gave up)", "결과 코드(성공·여전히 막힘·사용자에게 넘김·포기)"),
        "path": Phrase("optional path code (official path, same site or unknown)", "선택 경로 코드(공식 경로·같은 사이트·알 수 없음)"),
        "no_host": Phrase("the request carries no hostname, URL or text", "이 요청에는 호스트명·URL·문장이 없습니다"),
        "stateless": Phrase("Nothing is stored when a token is issued", "토큰을 발급할 때는 아무것도 저장하지 않습니다"),
        "one_time": Phrase("until shortly after the token expires one hour after its lookup, so that it cannot be used twice",
                           "조회 1시간 뒤 토큰이 만료되고 조금 지날 때까지 둡니다"),
        "excluded": Phrase("is accepted but not counted", "받기만 하고 세지 않습니다"),
    }
    for reporting in (False, True):
        assert_phrases("lookup feedback", {lang: section(lang, "02", reporting) for lang in LANGS}, wanted)
    # The enum lists in the policy are the code's lists, in order.
    assert [item.value for item in FeedbackOutcome] == ["succeeded", "still_blocked", "handed_off", "gave_up"]
    assert [item.value for item in FeedbackPath] == ["official_path", "same_site", "unknown"]
    for table, entry in TABLES.items():
        if isinstance(entry, UntilExpiry):
            for lang in LANGS:
                text = section(lang, "06")
                assert getattr(entry.wording, lang) in text and getattr(entry.when, lang) in text, f"{table}: {lang} retention"
    # Written as an optional write action in "Your controls".
    for lang, claim in (("en", "rate_last_lookup is an optional write action"), ("ko", "rate_last_lookup 은 토큰과 결과 코드만 보내는 선택적 쓰기 작업")):
        assert claim in section(lang, "07"), f"the {lang} policy does not describe rate_last_lookup under the controls"


def test_the_home_page_check_states_the_one_www_redirect_it_follows():
    """D67: the policy says which single redirect the home-page request follows, and does not say none is followed."""
    wanted = {
        "www_only": Phrase("at most one redirect followed, only to the same host's www name and only when its robots.txt allows the home page for us",
                           "리다이렉트는 같은 호스트의 www 이름으로 가는 것 하나만, 그 호스트의 robots.txt 가 홈 페이지를 허용할 때만 따라감"),
    }
    assert_phrases("home-page check redirect", {lang: section(lang, "04") for lang in LANGS}, wanted)
    for lang, stale in (("en", "no cookies, no sign-in and no redirect followed"), ("ko", "리다이렉트는 따라가지 않음, 요청 빈도는")):
        assert stale not in section(lang, "04")
    assert "the kind of redirect" in section("en", "03") and "리다이렉트 종류" in section("ko", "03")
