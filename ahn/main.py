from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock
from time import monotonic, perf_counter
from uuid import UUID

import psycopg
from fastapi import FastAPI, Header, Query, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from .db import connect
from .models import CheckResponse, ErrorClass, FailureStage, ObservationIn, OutcomeIn, Receipt, STEP, normalize_host_input, valid_host
from .owner_auth import OWNER_HEADERS, OWNER_PATHS, valid_owner_authorization, valid_owner_ip
from .owner_hosts import OWNER_HOSTS_DEFAULT_DAYS, OWNER_HOSTS_MAX_DAYS, parse_days, recent_hosts
from .rate_limit import RequestLimiter
from .service import ServiceError, check, public_incident, public_incidents, submit, withdraw, withdraw_public
from .settings import Settings
from .static_assets import OffloadingStaticFiles
from .traffic import client_type, daily_client_digest, request_source
from .triage import CHECKS_KO, MAINTENANCE_CHECK_KO
from .tool_metrics import is_mcp_metric_request, provider_signal_from_result


ROOT = Path(__file__).resolve().parent
AGENT_PROMPT = (ROOT / "agent_instructions.txt").read_text(encoding="utf-8").strip()
LLMS_TXT = (ROOT / "llms.txt").read_text(encoding="utf-8").strip()
MCP_REGISTRY_PROOF = (ROOT / "registry_proof").read_text(encoding="ascii").strip()
OPENAI_APPS_CHALLENGE = (ROOT / "openai_apps_challenge").read_text(encoding="ascii").strip()
templates = Jinja2Templates(directory=str(ROOT / "templates"))
write_key = APIKeyHeader(name="X-API-Key", auto_error=False)
LANG = {
    "en": {
        "brand": "Agent Help Network", "nav_search": "Search", "nav_connect": "Connect", "nav_agents": "For agents", "nav_report": "Report", "nav_policy": "Privacy policy", "nav_disclaimer": "Disclaimer", "nav_terms": "Terms", "nav_support": "Support",
        "hero_tag": "Agent access profile for a public hostname", "hero_title": "What does this host allow agents to do?",
        "hero_text": "Agent Help Network answers one question for a public hostname: what does this host allow agents to do, what is the official path, and is it up right now — plus any recent observations from other agents.",
        "search_label": "Public website hostname", "search_hint": "Hostname only, for example example.org", "search_button": "Check reports",
        "trust_title": "Read the evidence carefully", "trust_text": "No report does not mean a site works. One failure does not establish an outage. A later pass does not prove recovery for everyone.",
        "how_title": "Three layers, two agent paths", "how_text": "Declared policy, official path and provider status, optional observations. REST/OpenAPI and MCP read the same central service.",
        "search_title": "Search public observations", "filter_all": "All access issues", "results_title": "Matching public incidents",
        "empty_title": "No reports found", "empty": "No matching public reports in the 7-day data horizon. The site's current status is unknown.", "invalid_host": "Enter a public hostname without a URL path or account details.", "invalid_filter": "Choose a valid issue type.",
        "detail_title": "Incident observations", "sources": "Reporting sources", "attempts": "Recorded attempts", "latest": "Latest observed hour",
        "evidence_note": "Source counts do not prove independent people or networks. Reports do not establish root cause or current site availability.",
        "connect_title": "Connect an agent", "connect_intro": "This local MVP is not a public service address. Connectors use one central API after a controlled test setup.",
        "muse_title": "Meta Muse", "muse_text": "Use the service OpenAPI document and a separate connection credential. Follow your own operating authority before sharing an observation.",
        "dots_title": "ChatGPT dots", "dots_text": "Use the MCP adapter that calls the same central API. Checking is read-only; follow your own operating authority before sharing an observation.",
        "install_title": "Install", "install_intro": "Four ways to add the read-only lookup to a client you already use. Each links to that client's own documentation; none is an official integration, and Agent Help Network is not affiliated with any of them.",
        "install_cc_title": "Claude Code plugin", "install_cc_text": "Adds a lookup that runs after a failed web step, plus the MCP tool. In your shell:", "install_cc_privacy": "When a web step fails, the plugin's hook sends only the hostname and an error code (error class and failure stage). It sends no URL paths, page content, conversation text, or credentials.", "install_cc_docs": "Claude Code plugin marketplaces documentation",
        "install_cursor_title": "Cursor", "install_cursor_text": "Opens Cursor and offers to add the remote MCP server https://agenthelpnetwork.com/mcp. Cursor asks you to confirm before anything is saved.", "install_cursor_button": "Add to Cursor", "install_cursor_docs": "Cursor MCP install links documentation",
        "install_sdk_title": "OpenAI Agents SDK", "install_sdk_text": "Connects the remote MCP server through a hosted MCP tool, limited to the read-only check_incident tool.", "install_sdk_docs": "OpenAI Agents SDK MCP documentation",
        "install_json_title": "Other MCP clients", "install_json_text": "Any client that supports remote streamable HTTP servers needs only this URL; no account or key. Key names differ by client, so follow your client's documentation for where this goes.", "install_json_docs": "Model Context Protocol documentation",
        "policy_title": "Privacy policy", "policy_intro": "The local build uses synthetic test observations. It is not collecting real public incident reports.",
        "collect_title": "Structured fields only", "collect_text": "The planned report contains a public hostname, general task step, error type, time, attempt actor and environment, and outcome. It does not accept passwords, cookies, names, account or claim numbers, personal URLs, or conversation transcripts.",
        "profile_title": "robots.txt and llms.txt reads", "profile_text": "After a lookup of an eligible public apex hostname we read, in the background and not while the lookup waits, that host's own public /robots.txt (up to 256 KB) and /llms.txt (presence only, up to 4 KB) over HTTPS. Only those two paths are requested, on the host itself or its own www. alias: if the apex read fails, is refused, redirects elsewhere, or returns a web page, www.<host>/robots.txt is read once instead. The only redirect followed is a single hop from the host to its own www. alias; any other redirect is recorded and not followed. A definitive answer (the file, its absence, a refusal, a redirect, an oversized or invalid file, or a non-public address) is cached for 24 hours; a transport failure (DNS, TLS, connection, timeout, or an unexpected HTTP status) is retried after one hour. One attempt makes at most five HTTPS requests. Reads go only to hosts whose addresses are all public, identify themselves as AgentHelpNetwork, and fetch nothing else: no task pages, no login or CAPTCHA pages, no probing. We store the parsed per-agent rules, up to six sample Disallow paths per agent token and for the `*` group (80 characters each), up to five Sitemap URLs, the read status, and a SHA-256 hash of each file as received; the rest of the file is not stored. The result is the host's declared crawler policy and is never an access guarantee; a host may treat a user-directed agent differently. Besides looked-up hosts, we also read the hosts on a public seed list (ahn/seed_hosts.txt in the public source repository) about once a week, even if nobody has looked them up, under the same limits; a transport failure there is also retried after one hour. The local build does not perform these reads.",
        "sharing_title": "Authority and visibility", "sharing_text": "Submitting agents follow their own authority and platform rules. Real reports start in private review. Public pages exclude synthetic and unpublished observations. A reporter can withdraw their own report.",
        "limits_title": "Limits", "limits_text": "Reports are observations, not proof of an outage or intentional block. Retention and public publication thresholds will be set before a real pilot.",
        "not_found": "This public incident is unavailable.", "unavailable": "The service is temporarily unavailable.", "try_later": "Please try again later.", "back": "Back to search", "footer": "Local development build · Observations are not root-cause findings",
    },
    "ko": {
        "brand": "에이전트 도움망", "nav_search": "검색", "nav_connect": "연결 안내", "nav_agents": "에이전트용", "nav_report": "제보", "nav_policy": "개인정보 처리방침", "nav_disclaimer": "이용 안내 및 한계", "nav_terms": "이용약관", "nav_support": "지원",
        "hero_tag": "공개 호스트명의 에이전트 접근 프로필", "hero_title": "이 호스트는 에이전트에게 무엇을 허용하는가?",
        "hero_text": "Agent Help Network 는 공개 호스트명 하나로 묻습니다: 이 호스트는 에이전트에게 무엇을 허용하는가, 공식 경로는 무엇인가, 지금 정상인가 — 그리고 다른 에이전트의 최근 관찰이 있는가.",
        "search_label": "공개 웹사이트 호스트명", "search_hint": "URL 경로 없이, 예: example.org", "search_button": "제보 확인",
        "trust_title": "증거의 한계를 함께 보세요", "trust_text": "제보가 없다고 사이트가 정상인 것은 아닙니다. 실패 한 건은 전체 장애의 증거가 아닙니다. 나중의 통과도 모두에게 복구됐다는 뜻은 아닙니다.",
        "how_title": "세 층, 두 에이전트 연결", "how_text": "선언된 정책, 공식 경로와 업체 상태, 선택적 관찰입니다. REST/OpenAPI 와 MCP 가 같은 중앙 서비스를 읽습니다.",
        "search_title": "공개 관찰 검색", "filter_all": "모든 접속 문제", "results_title": "일치하는 공개 사건",
        "empty_title": "제보 없음", "empty": "최근 7일 데이터 범위에 일치하는 공개 제보가 없습니다. 사이트의 현재 상태는 알 수 없습니다.", "invalid_host": "URL 경로나 계정 정보 없이 공개 호스트명만 입력하세요.", "invalid_filter": "올바른 문제 유형을 선택하세요.",
        "detail_title": "사건 관찰 기록", "sources": "제보 출처", "attempts": "기록된 시도", "latest": "최근 관찰 시간대",
        "evidence_note": "출처 수가 독립된 사람이나 네트워크 수를 증명하지 않습니다. 제보만으로 원인이나 현재 사이트 상태를 확정할 수 없습니다.",
        "connect_title": "에이전트 연결", "connect_intro": "이 로컬 개발판은 공개 서비스 주소가 아닙니다. 통제된 시험 환경에서 두 연결부가 하나의 중앙 API를 사용합니다.",
        "muse_title": "Meta Muse", "muse_text": "서비스 OpenAPI 문서와 별도 연결 자격 증명을 사용합니다. 관찰을 공유하기 전 에이전트 자신의 권한 규칙을 따릅니다.",
        "dots_title": "ChatGPT dots", "dots_text": "같은 중앙 API를 호출하는 MCP 연결부를 사용합니다. 조회는 읽기 전용이며 관찰을 공유할 때는 에이전트 자신의 권한 규칙을 따릅니다.",
        "install_title": "설치", "install_intro": "이미 쓰고 있는 클라이언트에 읽기 전용 조회를 추가하는 네 가지 방법입니다. 각 항목은 해당 클라이언트의 공식 문서로 연결되며, 어느 것도 공식 통합이 아니고 에이전트 도움망은 이들과 제휴 관계가 없습니다.",
        "install_cc_title": "Claude Code 플러그인", "install_cc_text": "웹 단계가 실패한 뒤 조회를 실행하는 훅과 MCP 도구를 추가합니다. 셸에서 실행합니다.", "install_cc_privacy": "웹 단계가 실패하면 플러그인의 훅은 호스트명과 오류 코드(오류 유형·실패 단계)만 보냅니다. URL 경로, 페이지 내용, 대화 내용, 자격 증명은 보내지 않습니다.", "install_cc_docs": "Claude Code 플러그인 마켓플레이스 문서",
        "install_cursor_title": "Cursor", "install_cursor_text": "Cursor 를 열어 원격 MCP 서버 https://agenthelpnetwork.com/mcp 추가를 제안합니다. 저장하기 전에 Cursor 가 확인을 요청합니다.", "install_cursor_button": "Cursor 에 추가", "install_cursor_docs": "Cursor MCP 설치 링크 문서",
        "install_sdk_title": "OpenAI Agents SDK", "install_sdk_text": "호스티드 MCP 도구로 원격 MCP 서버를 연결하며, 읽기 전용 check_incident 도구만 가져옵니다.", "install_sdk_docs": "OpenAI Agents SDK MCP 문서",
        "install_json_title": "다른 MCP 클라이언트", "install_json_text": "원격 streamable HTTP 서버를 지원하는 클라이언트라면 이 URL 만 있으면 됩니다. 계정이나 키는 필요 없습니다. 키 이름은 클라이언트마다 다르니 넣을 위치는 해당 클라이언트 문서를 따르세요.", "install_json_docs": "Model Context Protocol 문서",
        "policy_title": "개인정보 처리방침", "policy_intro": "로컬 개발판은 가상 시험 관찰을 사용하며 실제 공개 사고 제보를 수집하지 않습니다.",
        "collect_title": "구조화된 필드만", "collect_text": "향후 제보에는 공개 호스트명, 일반 작업 단계, 오류 유형, 시각, 시도 주체·환경, 결과만 포함합니다. 비밀번호·쿠키·이름·계정/청구번호·개인 URL·대화 전문은 받지 않습니다.",
        "profile_title": "robots.txt·llms.txt 읽기", "profile_text": "공개 집계에 적합한 공개 루트 호스트명이 조회되면 조회가 기다리는 동안이 아니라 조회 뒤에 따로 그 호스트 자체의 공개 /robots.txt(최대 256 KB)와 /llms.txt(존재 여부만, 최대 4 KB)를 HTTPS 로 읽습니다. 요청하는 경로는 이 두 개뿐이며 대상은 그 호스트 또는 그 호스트 자신의 www. 별칭뿐입니다. 루트 호스트 읽기가 실패하거나, 거절되거나, 다른 곳으로 리디렉션되거나, 웹 페이지가 오면 www.<호스트>/robots.txt 를 대신 한 번 읽습니다. 따라가는 리디렉션은 호스트에서 자기 www. 별칭으로 가는 한 단계뿐이며 그 밖의 리디렉션은 기록만 하고 따라가지 않습니다. 확정된 답(파일, 파일 없음, 거절, 리디렉션, 너무 크거나 잘못된 파일, 공개되지 않은 주소)은 24시간 캐시하고, 전송 실패(DNS·TLS·연결·시간 초과·예상 밖 HTTP 상태)는 1시간 뒤 다시 시도합니다. 한 번의 시도는 HTTPS 요청을 최대 다섯 번 합니다. 모든 주소가 공개 IP 인 호스트에만 접속하고, AgentHelpNetwork 로 자신을 밝히며, 그 밖의 어떤 것도 가져오지 않습니다. 작업 페이지·로그인·CAPTCHA 페이지에 닿지 않고 탐침도 하지 않습니다. 해석한 에이전트 토큰별 규칙, 에이전트 토큰별·`*` 그룹별 Disallow 경로 표본 최대 6개(각 80자), Sitemap URL 최대 5개, 읽기 상태, 받은 파일의 SHA-256 해시만 저장하고 파일의 나머지는 저장하지 않습니다. 결과는 호스트가 선언한 크롤러 정책이며 접근 보장이 아닙니다. 호스트는 사용자 지시 에이전트를 다르게 취급할 수 있습니다. 조회된 호스트 외에, 공개 소스 저장소의 시드 목록(ahn/seed_hosts.txt)에 있는 호스트도 아무도 조회하지 않았더라도 같은 한도 안에서 주 1회 정도 읽으며, 여기서도 전송 실패는 1시간 뒤 다시 시도합니다. 로컬 개발판은 이 읽기를 수행하지 않습니다.",
        "sharing_title": "권한과 공개 범위", "sharing_text": "제출 에이전트는 자신의 권한·플랫폼 규칙을 따릅니다. 실제 제보는 비공개 검토 상태에서 시작합니다. 가상·미공개 기록은 공개 화면에 나오지 않습니다. 제보자는 자기 기록을 철회할 수 있습니다.",
        "limits_title": "한계", "limits_text": "제보는 관찰일 뿐 전체 장애나 의도적 차단의 증거가 아닙니다. 보존 기간과 공개 문턱은 실제 파일럿 전에 결정합니다.",
        "not_found": "이 공개 사건은 제공되지 않습니다.", "unavailable": "서비스를 일시적으로 사용할 수 없습니다.", "try_later": "잠시 후 다시 시도해 주세요.", "back": "검색으로 돌아가기", "footer": "로컬 개발판 · 관찰은 원인 확정이 아닙니다",
    },
}


def create_app(settings: Settings | None = None, lifespan=None) -> FastAPI:
    settings = settings or Settings.from_env()
    lang_text = {code: dict(values) for code, values in LANG.items()}
    if settings.public_mode:
        lang_text["en"].update(
            connect_intro="Public read-only preview. Agents can check the same central service through REST/OpenAPI or MCP. No real observation submissions are enabled yet.",
            muse_text="Use the public OpenAPI document for read-only checks. No credential is needed to read. Observation submission is not enabled in this preview.",
            dots_text="Use the MCP check_incident tool for read-only checks. Observation submission is not enabled in this preview.",
            policy_intro="This public read-only preview does not collect real incident reports. No matching report leaves a website's current status unknown.",
            sharing_text="Real observation submission is not enabled in this preview. Future submitting agents follow their own authority rules. Reports enter private review before publication; synthetic and unpublished observations are excluded from public pages.",
            footer="Read-only preview · No real incident reports yet · Observations are not root-cause findings",
        )
        lang_text["ko"].update(
            connect_intro="공개 읽기 전용 시험 서비스입니다. 에이전트는 REST/OpenAPI 또는 MCP로 같은 중앙 서비스를 조회할 수 있습니다. 실제 관찰 제보는 아직 받지 않습니다.",
            muse_text="공개 OpenAPI 문서로 읽기 조회를 사용할 수 있습니다. 조회에는 자격 증명이 필요하지 않습니다. 이 시험판에서는 제보를 받지 않습니다.",
            dots_text="MCP check_incident 도구로 읽기 조회를 사용할 수 있습니다. 이 시험판에서는 제보를 받지 않습니다.",
            policy_intro="이 공개 읽기 전용 시험판은 실제 사건 제보를 수집하지 않습니다. 일치하는 제보가 없으면 사이트의 현재 상태는 미상입니다.",
            sharing_text="이 시험판에서는 실제 관찰 제보를 받지 않습니다. 향후 제출 에이전트는 자신의 권한 규칙을 따릅니다. 제보는 공개 전에 비공개 검토를 거치며 가상·미공개 관찰은 공개 화면에서 제외합니다.",
            footer="읽기 전용 시험판 · 실제 사건 제보 없음 · 관찰은 원인 확정이 아닙니다",
        )
    if settings.public_mode and settings.reporting_enabled:
        lang_text["en"].update(
            connect_intro="Agents can read a host's access profile through REST/OpenAPI or MCP: declared robots.txt policy, official paths, provider status, and optional observations. A report is a separate, optional write action governed by each agent's own authority rules and enters private review.",
            muse_text="Use the public OpenAPI document to check observations. An authorized web-enabled client can submit a de-identified structured POST without an account; the report enters private review.",
            dots_text="Use MCP check_incident for reads. MCP report_observation sends a structured report under the agent's own authority rules; it enters private review.",
            policy_intro="We read public robots.txt and llms.txt files of looked-up hosts, cache verified provider status, and accept structured reports into private review. Declared policy is not an access guarantee, and published observations are never proof of current website status.",
            sharing_text="Submitting agents follow their own authority and platform rules. Reports start private. A rules-based review may publish a limited observation as unverified after a short delay; suspicious or repeated submissions remain private for operator review. Anonymous submissions share one unverified source. Keep the withdrawal token to remove your own report.",
            limits_text="Rules can catch malformed or repeated reports but cannot prove an event happened. We cap intake, hold unusual submissions, and can hide a report. Public lookup covers seven days. Stored reports have no automatic deletion schedule yet; use the withdrawal token to exclude your report from reads. Missing reports do not mean a site works, and one failure does not prove an outage.",
            footer="Agent-first beta · Declared policy is not an access guarantee · Website status remains uncertain",
        )
        lang_text["ko"].update(
            connect_intro="에이전트는 REST/OpenAPI 또는 MCP 로 호스트의 접근 프로필을 읽을 수 있습니다. robots.txt 선언 정책, 공식 경로, 업체 상태, 선택적 관찰이 담깁니다. 제보는 각 에이전트의 권한 규칙을 따르는 별도의 선택적 쓰기 작업이며 비공개 검토로 접수됩니다.",
            muse_text="공개 OpenAPI 문서로 관찰을 조회합니다. 권한이 있는 웹 클라이언트는 계정 없이 비식별화된 구조화 POST 제보를 보낼 수 있으며 비공개 검토로 접수됩니다.",
            dots_text="MCP check_incident로 조회합니다. MCP report_observation은 에이전트 자신의 권한 규칙에 따라 구조화된 제보를 전송하며 비공개 검토로 접수됩니다.",
            policy_intro="조회된 호스트의 공개 robots.txt·llms.txt 를 읽고, 확인된 업체 상태를 캐시하며, 구조화된 제보를 비공개 검토 상태로 접수합니다. 선언된 정책은 접근 보장이 아니며 공개된 관찰도 사이트의 현재 상태를 증명하지 않습니다.",
            sharing_text="제출 에이전트는 자신의 권한·플랫폼 규칙을 따릅니다. 제보는 비공개로 접수됩니다. 규칙 검토를 통과한 제한된 관찰은 잠시 뒤 미확인 제보로 자동 공개될 수 있고, 반복·의심 제보는 운영자 검토를 위해 보류합니다. 계정 없는 제보는 하나의 미확인 출처입니다. 철회 토큰으로 자기 제보를 철회할 수 있습니다.",
            limits_text="규칙은 반복·모순된 입력을 걸러도 실제 사건 발생 여부는 증명하지 못합니다. 접수량을 제한하고 의심 제보를 보류하며 잘못된 제보는 숨길 수 있습니다. 공개 조회는 최근 7일이며 저장된 제보의 자동 삭제 일정은 아직 없습니다. 제보가 없다고 사이트가 정상인 것은 아니고 실패 한 건도 전체 장애를 증명하지 않습니다.",
            footer="에이전트 우선 베타 · 선언된 정책은 접근 보장이 아닙니다 · 사이트 상태는 미상일 수 있습니다",
        )
    if settings.report_retention_days:
        days = settings.report_retention_days
        lang_text["en"]["limits_text"] = lang_text["en"]["limits_text"].replace(
            "Stored reports have no automatic deletion schedule yet; use the withdrawal token to exclude your report from reads.",
            f"Reports are deleted {days} days after they are received; a withdrawn report leaves every read at once and is deleted 30 days later.")
        lang_text["ko"]["limits_text"] = lang_text["ko"]["limits_text"].replace(
            "저장된 제보의 자동 삭제 일정은 아직 없습니다.",
            f"제보는 접수 {days}일 뒤 삭제되며, 철회한 제보는 즉시 모든 조회에서 빠지고 30일 뒤 삭제됩니다.")
    if settings.public_mode:
        lang_text["en"]["collect_text"] = "Reports use structured public hostnames, task categories, timestamps, attempt context, and outcomes. Never submit passwords, cookies, names, account identifiers, personal URLs, or conversation text. Our app stores daily page and lookup counts, bounded referral and campaign categories, and coarse client-software hints. When the hosting edge supplies a client IP, we store only a keyed, day-specific digest and daily request count for 14 days; no raw IP, cookie, full Referrer URL, or User-Agent is stored in these analytics tables. Eligible public root hostnames are kept in a lookup register with first and latest lookup times, and are removed after 30 days without a lookup unless the host has a connected official status feed. Hosting infrastructure may process IP addresses and User-Agent headers for delivery and security. These categories and digests do not identify a person or prove an independent agent."
        lang_text["ko"]["collect_text"] = "제보에는 공개 호스트명, 일반 작업 단계, 문제·결과 유형, 관찰 시각, 시도 주체·환경만 포함합니다. 비밀번호·쿠키·이름·계정 식별자·개인 URL·대화 전문은 받지 않습니다. 앱은 일별 페이지·호스트 조회 수와 제한된 유입·캠페인 분류, 대략적인 클라이언트 프로그램 분류를 저장합니다. 호스팅 경계에서 IP가 전달되면 원문 대신 비밀키로 날짜별 변환한 값과 일별 요청 수만 14일 보관합니다. 이 통계 표에는 IP 원문·쿠키·전체 유입 URL·User-Agent 원문을 저장하지 않습니다. 공유 가능한 공개 루트 호스트명은 첫·최근 조회 시각과 함께 조회 목록에 두며, 30일 동안 조회가 없으면 삭제합니다(공식 상태 피드가 연결된 호스트는 예외). 호스팅 인프라는 전송·보안을 위해 IP와 User-Agent를 처리할 수 있습니다. 이 분류나 변환값으로 사람 또는 독립 에이전트를 증명할 수 없습니다."
        lang_text["en"]["collect_text"] += " Successful MCP check tool calls are also counted daily by evidence and provider-signal category for 90 days, without a hostname or client identity in that tool-call table. Requests from an owner-allowlisted network are marked as an aggregate owner-network category; this does not identify a person or prove a test. Hosts on our public seed list are also read about once a week for their declared robots.txt and llms.txt policy even if nobody looked them up; the parsed result is stored the same way."
        lang_text["ko"]["collect_text"] += " 성공한 MCP 조회 도구 실행은 호스트명이나 클라이언트 식별자 없이 근거·업체 신호 범주별 일일 합계로 90일 보관합니다. 운영자 허용 네트워크의 요청은 집계상 운영자 네트워크 범주로 표시하지만 사람이나 시험 여부를 증명하지는 않습니다. 공개 시드 목록의 호스트는 아무도 조회하지 않았더라도 선언된 robots.txt·llms.txt 정책을 주 1회 정도 읽어 해석 결과를 같은 방식으로 저장합니다."
    app = FastAPI(title="Agent Help Network", version="0.1.0", description="Structured observations, not website status or root-cause diagnosis", lifespan=lifespan)
    app.state.settings = settings
    request_limiter = RequestLimiter() if settings.public_mode else None
    report_limiter = RequestLimiter(per_client=3, total=20) if settings.public_mode and settings.reporting_enabled else None
    health_lock = Lock()
    health_cache = {"checked_at": 0.0, "ok": False}
    app.mount("/static", OffloadingStaticFiles(directory=str(ROOT / "static")), name="static")

    if settings.public_mode:
        @app.get("/.well-known/mcp-registry-auth", include_in_schema=False)
        def mcp_registry_auth():
            return Response(MCP_REGISTRY_PROOF + "\n", media_type="text/plain; charset=utf-8")

        @app.get("/.well-known/openai-apps-challenge", include_in_schema=False)
        def openai_apps_challenge():
            return Response(OPENAI_APPS_CHALLENGE + "\n", media_type="text/plain; charset=utf-8")

        @app.get("/agent-instructions.txt", include_in_schema=False)
        def agent_instructions():
            return Response(AGENT_PROMPT + "\n", media_type="text/plain; charset=utf-8")

        @app.get("/llms.txt", include_in_schema=False)
        def llms_txt():
            # Our own llms.txt: the same courtesy we read from other hosts.
            return Response(LLMS_TXT + "\n", media_type="text/plain; charset=utf-8")

        @app.get("/robots.txt", include_in_schema=False)
        def robots():
            return Response(
                "User-agent: *\nAllow: /\nDisallow: /owner\nSitemap: https://agenthelpnetwork.com/sitemap.xml\n",
                media_type="text/plain",
            )

        @app.get("/sitemap.xml", include_in_schema=False)
        def sitemap():
            pages = ("/", "/human", "/agents", "/connect", "/search", "/policy", "/disclaimer", "/terms", "/support") + (("/report",) if settings.reporting_enabled else ())
            urls = "".join(
                f"<url><loc>https://agenthelpnetwork.com/{lang}{page}</loc></url>"
                for lang in ("en", "ko") for page in pages
            )
            return Response(
                f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
                media_type="application/xml",
            )

    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, exc: ServiceError):
        return JSONResponse({"error": exc.code}, status_code=exc.status, headers={"Retry-After": "3600"} if exc.status == 429 else None)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # FastAPI's default response can echo rejected input, including a personal URL.
        if request.url.path in {"/v1/incidents/check", "/v1/incidents"}:
            for issue in exc.errors():
                if tuple(issue.get("loc", ())) == ("query", "error_class"):
                    return JSONResponse({"error": "invalid_request", "field": "error_class",
                                         "allowed_values": [item.value for item in ErrorClass]}, status_code=400)
                if tuple(issue.get("loc", ())) == ("query", "failure_stage"):
                    return JSONResponse({"error": "invalid_request", "field": "failure_stage",
                                         "allowed_values": [item.value for item in FailureStage]}, status_code=400)
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    @app.exception_handler(psycopg.Error)
    async def database_unavailable(request: Request, _exc: psycopg.Error):
        lang = request.url.path.split("/", 2)[1]
        if lang in lang_text:
            return templates.TemplateResponse(request, "unavailable.html", {"lang": lang, "t": lang_text[lang], "page": "unavailable"}, status_code=503, headers={"Retry-After": "60"})
        return JSONResponse({"error": "service_unavailable"}, status_code=503, headers={"Retry-After": "60"})

    def internal_mcp_hop(request: Request) -> bool:
        # The MCP adapter calls this same app over loopback; the outer /mcp
        # request already consumed the limiter, so the hop must not count twice.
        return bool(request.client and request.client.host in {"127.0.0.1", "::1"}
                    and is_mcp_metric_request(request.headers.get("x-ahn-mcp-call"), settings.key_pepper))

    @app.middleware("http")
    async def limit_body_and_measure(request: Request, call_next):
        start = perf_counter()
        exempt = request.url.path == "/health" or internal_mcp_hop(request) or (
            request.url.path in OWNER_PATHS and valid_owner_ip(request.headers.get("cf-connecting-ip", ""), settings.owner_allowed_ips))
        if request_limiter and not exempt and not await request_limiter.allow(request):
            return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": "60", "Cache-Control": "no-store"})
        public_report = settings.public_mode and settings.reporting_enabled and request.method == "POST" and request.url.path == "/v1/observations"
        if public_report:
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                return JSONResponse({"error": "json_required"}, status_code=415)
            size = request.headers.get("content-length", "")
            if not size.isdigit() or int(size) > 4096:
                return JSONResponse({"error": "body_too_large_or_unframed"}, status_code=413)
        if report_limiter and request.method == "POST" and request.url.path == "/v1/observations" and not exempt and not await report_limiter.allow(request):
            return JSONResponse({"error": "report_rate_limited"}, status_code=429, headers={"Retry-After": "60", "Cache-Control": "no-store"})
        rejected_body = False
        if request.method in {"POST", "PUT", "PATCH"}:
            if "transfer-encoding" in request.headers and "content-length" in request.headers:
                return JSONResponse({"error": "conflicting_framing"}, status_code=400)
            size = request.headers.get("content-length", "")
            if not size.isdigit() or int(size) > 4096:
                rejected_body = True
            if not rejected_body:
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 4096:
                        rejected_body = True
                        break
                if not rejected_body:
                    request._body = bytes(body)
        response = JSONResponse({"error": "body_too_large_or_unframed"}, status_code=413) if rejected_body else await call_next(request)
        if settings.collect_request_metrics and request.url.path not in {"/health", *OWNER_PATHS}:
            route = request.scope.get("route")
            path = getattr(route, "path", "other")
            allowed = {"/", "/health", "/openapi.json", "/v1/incidents/check", "/v1/incidents", "/v1/observations", "/v1/observations/{observation_id}/outcomes", "/v1/observations/{observation_id}", "/{lang}/", "/{lang}/human", "/{lang}/search", "/{lang}/incidents/{incident_id}", "/{lang}/connect", "/{lang}/agents", "/{lang}/report", "/{lang}/policy", "/{lang}/disclaimer", "/{lang}/terms", "/{lang}/support"}
            route_code = path if path in allowed else "other"
            if request.url.path.startswith("/mcp"):
                route_code = "/mcp"
            elif request.url.path.startswith("/static/"):
                route_code = "/static"
            elapsed_ms = (perf_counter() - start) * 1000
            bucket = next((limit for limit in (50, 100, 250, 500, 1000, 5000) if elapsed_ms <= limit), 10000)
            source, medium, campaign, referrer = request_source(request)
            if valid_owner_ip(request.headers.get("cf-connecting-ip", ""), settings.owner_allowed_ips):
                # Network category only. A shared home address is not a person or proof of a test.
                source, medium, campaign, referrer = "owner_network", "internal", "none_or_other", "none"
            kind = client_type(request)
            day, digest = daily_client_digest(request, settings.key_pepper)
            def save_metric():
                with connect(settings) as conn:
                    conn.execute(
                        "INSERT INTO request_metrics (day,route_code,method,status_class,latency_bucket_ms,requests) VALUES (CURRENT_DATE,%s,%s,%s,%s,1) ON CONFLICT (day,route_code,method,status_class,latency_bucket_ms) DO UPDATE SET requests=request_metrics.requests+1",
                        (route_code, request.method, f"{response.status_code // 100}xx", bucket),
                    )
                    conn.execute(
                        """INSERT INTO traffic_attribution_daily
                           (day,route_code,method,status_class,source,medium,campaign,referrer,client_type,requests)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
                           ON CONFLICT (day,route_code,method,status_class,source,medium,campaign,referrer,client_type)
                           DO UPDATE SET requests=traffic_attribution_daily.requests+1""",
                        (day, route_code, request.method, f"{response.status_code // 100}xx", source, medium, campaign, referrer, kind),
                    )
                    if digest:
                        conn.execute(
                            """INSERT INTO traffic_client_daily (day,client_digest,client_type,requests)
                               VALUES (%s,%s,%s,1)
                               ON CONFLICT (day,client_digest,client_type)
                               DO UPDATE SET requests=traffic_client_daily.requests+1""",
                            (day, digest, kind),
                        )
            try:
                await run_in_threadpool(save_metric)
            except Exception:
                # Metrics must never change a core API result.
                pass
        return response

    def received_channel(request: Request) -> str:
        """Provenance of a report: the MCP adapter's authenticated loopback hop, the site's report form, or REST."""
        if is_mcp_metric_request(request.headers.get("X-AHN-MCP-Call"), settings.key_pepper):
            return "mcp"
        return "web" if request.headers.get("X-AHN-Channel") == "web" else "rest"

    def render(request: Request, lang: str, page: str, status_code: int = 200, **context):
        return templates.TemplateResponse(request, f"{page}.html", {"lang": lang, "t": lang_text[lang], "page": page, "public_mode": settings.public_mode, "reporting_enabled": settings.reporting_enabled, "operator_name": settings.operator_name, "report_retention_days": settings.report_retention_days, "agent_prompt": AGENT_PROMPT, **context}, status_code=status_code)

    @app.get("/health")
    def health():
        # Render probes stay independent of the public request bucket, while
        # repeated probes and floods share one DB check for a short interval.
        with health_lock:
            if monotonic() - health_cache["checked_at"] >= 10:
                try:
                    with connect(settings) as conn:
                        conn.execute("SELECT 1")
                    health_cache["ok"] = True
                except Exception:
                    health_cache["ok"] = False
                health_cache["checked_at"] = monotonic()
            return {"status": "ok"} if health_cache["ok"] else JSONResponse({"status": "unavailable"}, status_code=503)

    def owner_denied(request: Request) -> Response | None:
        """The one gate for every owner-only route: None when allowed, else the response to send."""
        if settings.owner_allowed_ips:
            if not valid_owner_ip(request.headers.get("cf-connecting-ip", ""), settings.owner_allowed_ips):
                return JSONResponse({"error": "not_found"}, status_code=404, headers=OWNER_HEADERS)
        else:
            if not settings.owner_password:
                return JSONResponse({"error": "not_found"}, status_code=404, headers=OWNER_HEADERS)
            if not valid_owner_authorization(request.headers.get("authorization", ""), settings.owner_password):
                return Response(
                    status_code=401,
                    headers={**OWNER_HEADERS, "WWW-Authenticate": 'Basic realm="Owner monitor", charset="UTF-8"'},
                )
        return None

    @app.get("/owner", include_in_schema=False)
    def owner_dashboard(request: Request):
        denied = owner_denied(request)
        if denied is not None:
            return denied
        try:
            from .monitor import _snapshot

            snapshot = _snapshot(settings)
        except Exception:
            return JSONResponse({"error": "monitor_data_unavailable"}, status_code=503, headers=OWNER_HEADERS)
        return templates.TemplateResponse(request, "monitor.html", snapshot, headers=OWNER_HEADERS)

    @app.get("/owner/hosts", include_in_schema=False)
    def owner_hosts(request: Request):
        # Same gate as /owner. Query validation runs after it, so a caller who is not allowed
        # cannot tell this route from any other unknown path by sending a bad `days`.
        denied = owner_denied(request)
        if denied is not None:
            return denied
        days = parse_days(request.query_params.get("days"))
        if days is None:
            return JSONResponse({"error": "invalid_days", "min": 1, "max": OWNER_HOSTS_MAX_DAYS, "default": OWNER_HOSTS_DEFAULT_DAYS},
                                status_code=422, headers=OWNER_HEADERS)
        try:
            hosts = recent_hosts(settings, days)
        except Exception:
            return JSONResponse({"error": "monitor_data_unavailable"}, status_code=503, headers=OWNER_HEADERS)
        body = "".join(host + "\n" for host in hosts)
        return Response(body, media_type="text/plain; charset=utf-8", headers=OWNER_HEADERS)

    @app.get("/v1/incidents/check", response_model=CheckResponse)
    def check_incident(site_host: str, error_class: ErrorClass | None = None, task_step: str | None = None,
                       window_hours: int = Query(24, ge=1, le=168), failure_stage: FailureStage | None = None,
                       mcp_call: str | None = Header(default=None, alias="X-AHN-MCP-Call", include_in_schema=False)):
        site_host = normalize_host_input(site_host)
        try:
            valid_host(site_host)
        except ValueError:
            raise ServiceError("invalid_site_host", 400)
        if task_step is not None and not STEP.fullmatch(task_step):
            raise ServiceError("invalid_task_step", 400)
        result = check(settings, site_host, error_class.value if error_class else None, task_step, window_hours,
                       failure_stage.value if failure_stage else None)
        if is_mcp_metric_request(mcp_call, settings.key_pepper):
            try:
                with connect(settings) as conn:
                    conn.execute(
                        """INSERT INTO mcp_check_metrics (day,evidence_level,provider_signal,calls)
                           VALUES (CURRENT_DATE,%s,%s,1)
                           ON CONFLICT (day,evidence_level,provider_signal)
                           DO UPDATE SET calls=mcp_check_metrics.calls+1""",
                        (result["evidence_level"], provider_signal_from_result(result)),
                    )
            except Exception:
                # Analytics failure must not turn a successful lookup into an error.
                logging.getLogger(__name__).warning("mcp_check_metrics_unavailable")
        return result

    @app.get("/v1/incidents")
    def list_incidents(site_host: str | None = None, error_class: ErrorClass | None = None, limit: int = Query(20, ge=1, le=50)):
        if site_host is not None:
            site_host = normalize_host_input(site_host)
            try:
                valid_host(site_host)
            except ValueError:
                raise ServiceError("invalid_site_host", 400)
        return {"incidents": public_incidents(settings, site_host, error_class.value if error_class else None, limit)}

    @app.get("/v1/incidents/{incident_id}")
    def incident_detail(incident_id: UUID):
        return public_incident(settings, incident_id)

    if settings.public_mode and settings.reporting_enabled and not settings.test_mode:
        @app.post("/v1/observations", response_model=Receipt, response_model_exclude_none=True, status_code=201, responses={200: {"model": Receipt}})
        def report_observation(body: ObservationIn, response: Response, request: Request):
            result, created = submit(settings, "", body, channel=received_channel(request))
            response.status_code = 201 if created else 200
            response.headers["Cache-Control"] = "no-store"
            return result
    else:
        @app.post("/v1/observations", response_model=Receipt, response_model_exclude_none=True, status_code=201, responses={200: {"model": Receipt}}, include_in_schema=settings.test_mode)
        def report_observation(body: ObservationIn, response: Response, request: Request, x_api_key: str | None = Security(write_key)):
            result, created = submit(settings, x_api_key or "", body, channel=received_channel(request))
            response.status_code = 201 if created else 200
            return result

    @app.post("/v1/observations/{observation_id}/outcomes", response_model=Receipt, status_code=201, responses={200: {"model": Receipt}}, include_in_schema=settings.test_mode)
    def update_outcome(observation_id: UUID, body: OutcomeIn, response: Response, request: Request, x_api_key: str | None = Security(write_key)):
        result, created = submit(settings, x_api_key or "", body, observation_id, channel=received_channel(request))
        response.status_code = 201 if created else 200
        return result

    if settings.public_mode and not settings.test_mode:
        @app.delete("/v1/observations/{observation_id}")
        def delete_observation(observation_id: UUID, x_withdrawal_token: str = Header(default="")):
            return withdraw_public(settings, observation_id, x_withdrawal_token)
    else:
        @app.delete("/v1/observations/{observation_id}", include_in_schema=settings.test_mode)
        def delete_observation(observation_id: UUID, x_api_key: str | None = Security(write_key)):
            return withdraw(settings, x_api_key or "", observation_id)

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/en/", status_code=307)

    @app.get("/{lang}/", include_in_schema=False)
    def home(request: Request, lang: str):
        if lang not in lang_text:
            raise ServiceError("not_found", 404)
        return render(request, lang, "home")

    @app.get("/{lang}/human", include_in_schema=False)
    def human_home(request: Request, lang: str):
        if lang not in lang_text:
            raise ServiceError("not_found", 404)
        return render(request, lang, "human")

    @app.get("/{lang}/report", include_in_schema=False)
    def report_page(request: Request, lang: str, site_host: str = ""):
        if lang not in lang_text:
            raise ServiceError("not_found", 404)
        try:
            site_host = normalize_host_input(site_host)
            report_host = valid_host(site_host) if site_host else ""
        except ValueError:
            report_host = ""
        return render(request, lang, "report", report_host=report_host)

    @app.get("/{lang}/search", include_in_schema=False)
    def search_page(request: Request, lang: str, site_host: str = "", error_class: str = "", failure_stage: str = ""):
        if lang not in lang_text:
            raise ServiceError("not_found", 404)
        error = None
        results = []
        signals = None
        selected_class = None
        selected_stage = None
        if error_class:
            try:
                selected_class = ErrorClass(error_class)
            except ValueError:
                error = lang_text[lang]["invalid_filter"]
        if failure_stage:
            try:
                selected_stage = FailureStage(failure_stage)
            except ValueError:
                error = lang_text[lang]["invalid_filter"]
        site_host = normalize_host_input(site_host)
        if site_host:
            try:
                valid_host(site_host)
            except ValueError:
                error = lang_text[lang]["invalid_host"]
            else:
                if not error:
                    signals = check(settings, site_host, selected_class.value if selected_class else None,
                                    window_hours=168, failure_stage=selected_stage.value if selected_stage else None)
                    results = public_incidents(settings, site_host, selected_class.value if selected_class else None)
        return render(request, lang, "search", site_host=site_host, error_class=selected_class.value if selected_class else "",
                      failure_stage=selected_stage.value if selected_stage else "", results=results, signals=signals,
                      next_check_ko={**CHECKS_KO, "provider_maintenance": MAINTENANCE_CHECK_KO}, error=error)

    @app.get("/{lang}/incidents/{incident_id}", include_in_schema=False)
    def detail_page(request: Request, lang: str, incident_id: UUID):
        if lang not in lang_text:
            raise ServiceError("not_found", 404)
        try:
            incident = public_incident(settings, incident_id)
        except ServiceError:
            return render(request, lang, "not-found", status_code=404)
        return render(request, lang, "detail", incident=incident)

    @app.get("/{lang}/connect", include_in_schema=False)
    def connect_page(request: Request, lang: str):
        if lang not in lang_text:
            raise ServiceError("not_found", 404)
        return render(request, lang, "connect")

    @app.get("/{lang}/agents", include_in_schema=False)
    def agents_page(request: Request, lang: str):
        if lang not in lang_text:
            raise ServiceError("not_found", 404)
        return render(request, lang, "agents")

    @app.get("/{lang}/policy", include_in_schema=False)
    def policy_page(request: Request, lang: str):
        if lang not in lang_text:
            raise ServiceError("not_found", 404)
        return render(request, lang, "policy")

    @app.get("/{lang}/disclaimer", include_in_schema=False)
    def disclaimer_page(request: Request, lang: str):
        if lang not in lang_text:
            raise ServiceError("not_found", 404)
        return render(request, lang, "disclaimer")

    @app.get("/{lang}/terms", include_in_schema=False)
    def terms_page(request: Request, lang: str):
        if lang not in lang_text:
            raise ServiceError("not_found", 404)
        return render(request, lang, "terms")

    @app.get("/{lang}/support", include_in_schema=False)
    def support_page(request: Request, lang: str):
        if lang not in lang_text:
            raise ServiceError("not_found", 404)
        return render(request, lang, "support")

    return app


app = create_app()
