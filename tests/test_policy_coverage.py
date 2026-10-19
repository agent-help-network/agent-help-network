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
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn import platform_metrics
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
    False: {"check_incident"},                       # reporting off: read-only
    True: {"check_incident", "report_observation"},  # reporting on
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
    "observations": ReportRows(),
    "report_submissions": ReportRows(),  # idempotency key and request hash of a report; goes with its observation
    # Not request-derived: nothing for the policy to describe.
    "schema_migrations": Exempt("migration bookkeeping"),
    "reporters": Exempt("one shared 'public-unverified' row for all no-account reports; no per-person identity"),
    "service_flags": Exempt("operator on/off switches"),
    "bandwidth_guard_samples": Exempt("hosting bandwidth totals in gigabytes from the provider's API, not request data"),
    "bandwidth_guard_state": Exempt("state of the operator's bandwidth guard"),
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
    for table, count in re.findall(r"DELETE FROM ([a-z_]+) WHERE \w+ < now\(\) - interval '(\d+) days'", source):
        days[table] = int(count)
    statements = re.findall(r"DELETE FROM ([a-z_]+)", source)
    assert sorted(statements) == sorted(days), (
        "prune_analytics deletes in a form this test cannot read; use `day < CURRENT_DATE - N` or "
        f"`col < now() - interval 'N days'`, or extend periods_in_prune_analytics. Unread: {sorted(set(statements) - set(days))}")
    return days


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
    expected_deleted = pruned | {t for t, v in TABLES.items() if isinstance(v, ReportRows)}
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
