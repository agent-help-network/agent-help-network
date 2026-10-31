"""Cautious next checks derived from evidence, never a site-status verdict."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import status_scope
from .access_profile import AGENT_VENDORS, GOVERNING_TOKENS, VENDOR_ALIASES, ai_input_declared_no

# D45: an incident whose latest update is older than this no longer makes the answer "wait" on its own.
STALE_INCIDENT_DAYS = 7


CHECKS = {
    "locate_failure_stage": "First ask whether your tool received a response from the target or an intermediary. If no response was observed, inspect the client and connection path before attributing a site failure.",
    "human_handoff": "A human verification step needs the user's participation through the normal handoff. Do not bypass the challenge.",
    "account_handoff": "Check whether this step needs the user's sign-in or account permission. Do not request or share credentials through this service.",
    "before_target_response": "No target response was observed. Check the agent tool, browser, and connection path first; this does not establish a site outage.",
    "response_observed": "A response was observed. Check whether it came from the target, an intermediary, or a security layer before attributing the failure.",
    "access_denial_observed": "An access denial was observed. Follow the site's stated policy and do not bypass its controls; this does not establish an outage.",
    "provider_scope": "The provider reports an active issue. Compare its affected services and update time with your blocked step before deciding whether to wait or retry.",
    "provider_components_only": "The provider's page-level status shows an issue, but the provider has announced no incident or maintenance (provider_status.basis is components_only). Some components are marked down or under maintenance; check on the provider's status page whether a component you use is one of them. A minor or maintenance page is not treated as a reason to wait; a major or critical one still is, because an outage can show on components before an incident is posted.",
    "provider_incident_stale": f"The provider's active incidents have had no update for more than {STALE_INCIDENT_DAYS} days (provider_status.incident_updated_at is the latest one) and no maintenance is in progress. An incident left open without updates is not treated as a reason to wait: if provider_status.affected names a service you use, wait for the provider; otherwise continue. A major or critical page above the worst incident's impact is still a reason to wait, because components can show a new outage before an incident is posted.",
    "provider_scope_partial": "The announced issue may reach only part of the provider: its affected components are all regions or points of presence, or the worst incident is less severe than the page-level indicator. Check whether provider_status.affected names a service you use before waiting.",
    "provider_update_time_unknown": "The provider's status page shows a minor issue, lists no incidents this service can read, and carries no page update time it can use (provider_status.source_updated_at), so a current issue cannot be told from one left on the page for weeks. The answer still waits, with low confidence: check the date of the issue on the provider's status page, and if it is old or names no service you use, continue.",
    "provider_maintenance_only": "The provider has announced no incident: its active status rests only on maintenance in progress (provider_status.basis is maintenance, active_incidents is 0). The page-level indicator may still read minor, major or critical, and this service cannot tell from it whether anything besides the maintenance is wrong, so the answer still waits. Compare the maintenance window and affected services on the provider's status page with your blocked step; with provider_scope_partial, only regions or points of presence are listed as affected.",
    "provider_history_incident": f"The provider's status API is not read here (its robots.txt disallows it, or it has not been read again yet); its public incident-history feed (provider_status.source_kind history_atom) lists an open incident updated within the last {STALE_INCIDENT_DAYS} days (provider_status.incident_updated_at). That feed gives no page status, severity or affected services, so the answer waits with low confidence: check the provider's status page for what is affected, and if it names no service you use, continue.",
    "provider_clear": "The provider reports no active issue. Continue diagnosing this specific attempt; the statement does not prove access from your environment.",
    "provider_unknown": "No live provider status is available here. Check an official status page if one is linked, and keep the current status unknown.",
    "reports_unknown": "No matching published observations were found. This does not establish that the site works or that your issue is isolated.",
    "reports_stale": "Only older published observations were found. Compare their times with your task; they do not establish current access.",
    "reports_unverified": "Compare the time and issue type of any published observations with your attempt. Reports are unverified and do not establish a common cause.",
    "robots_blocks_agents": "This host's robots.txt explicitly disallows the site root for your agent's token (agent_vendor), or, when no vendor was sent, for every listed vendor's (see access_profile.robots.agents_blocked). Treat it as the host's declared policy: stop automated retries against pages, prefer an official path, and hand the step to the user where their participation is needed.",
    "robots_blocks_some_agents": "This host's robots.txt explicitly disallows the site root for some agent tokens (access_profile.robots.agents_blocked), and this answer does not know which agent you are. If your requests identify as one of those tokens, treat it as a declared block: stop automated retries against pages and prefer an official path. Otherwise the block is not about you. Send agent_vendor to get an answer for your agent.",
    "robots_blocks_not_your_agent": "Some agent tokens are explicitly disallowed (access_profile.robots.agents_blocked), but not the tokens this answer was decided on (your vendor's governing tokens, or, without agent_vendor, every vendor's). Those blocks do not decide this answer. The host may not welcome some automation, so keep requests modest; this is not an access guarantee. This service reads only the tokens it lists: if your fetcher sends a token of its own, check robots.txt for a group that names it.",
    "robots_names_vendor_alias": "A robots.txt group that disallows the site root names your vendor's agent under a name your documented token does not match (for example a retired name). It is not read as a declared block on your token, and not as permission either: prefer an official path and tell the user.",
    "robots_allows_agents": "This host's robots.txt does not disallow the site root for the agent tokens this answer was decided on. Declared crawler policy is not an access guarantee; CDN challenges and rate limits can still apply, so keep requests modest and honor any Crawl-delay.",
    "robots_unknown": "No robots.txt could be read for this host (redirected, unreachable, invalid or too large within limits; see access_profile.robots_detail). No declared policy is known: this service treats it neither as a block nor as a declaration of no restriction.",
    "robots_wildcard_disallow": "This host's robots.txt disallows the site root for every crawler it does not name (User-agent: *), and at least one agent user agent is named by no group, so it falls under that wildcard (access_profile.robots.agents_allowed lists the tokens the host names and allows). Whether a user-directed fetcher honours that wildcard is your operator's policy; treat the host as not inviting automation, prefer an official path if one exists, and do not read this as permission.",
    "robots_grouping_differs_by_parser": "Parsers disagree on this robots.txt (access_profile.robots.grouping_differs_by_parser lists '*' and the agent tokens whose root decision changes): Google-style parsers merge groups that this answer keeps apart, which usually reads more strictly, or read only the first name of a comma-separated User-agent line, which this answer splits into every listed name. The recommendation uses this service's reading; if your operator follows Google's reading, check those tokens against it before relying on the answer.",
    "robots_fetch_refused": "This host's edge refused even a plain robots.txt read from a data-center address with an honest bot user agent (see access_profile.robots_detail). Expect page automation from a similar environment to be refused too: prefer the official path, and hand the step to the user if it needs them.",
    "profile_not_read": "Declared policy is read only for public apex hosts, and this hostname is a subdomain or tenant host, so no robots.txt was read for it. Look up the apex host for the site-wide declaration; the subdomain may still have its own rules.",
    "profile_pending": "This host's robots.txt and llms.txt are being read now. Repeat the same lookup once after access_profile.retry_after_seconds seconds; the answer will usually carry the declared policy by then. Do not poll repeatedly.",
    "llms_txt_present": "This host publishes llms.txt. Read it first: it is the host's own guidance to language-model agents and may point to preferred machine-readable resources.",
    "content_use_ai_input_declared_no": "This host's robots.txt declares ai-input=no (or AIPREF ai-use=n) for user-directed agents like you (access_profile.content_use). It is a content-use preference, not an access rule; follow your operator's policy and tell the user if it matters.",
    "official_path_available": "An official developer path is listed for this host (official_paths). Prefer the documented API or developer portal over page automation; it is the sanctioned route and usually the more reliable one.",
    "government_registry_listed": "This domain appears in a public government domain list (host_facts.government_registry gives the list, its version and the organisation it names). The list says who registered the domain; it does not vouch for every page or link on it.",
    "government_suffix_restricted": "Registration under this suffix is limited to government bodies by the registry's own rules (host_facts.suffix_rule). That says who may register a domain; it does not vouch for any page or link on it.",
    "government_list_version_missing": "This domain is not in the version of the government domain list shown in host_facts.government_registry. Lists follow the publisher's schedule, so a newly registered domain can be missing; this is a fact about the list version, not a finding about the site. Before entering credentials or payment details, compare the address with one the organisation publishes elsewhere.",
}

CHECKS_KO = {
    "locate_failure_stage": "먼저 대상 사이트나 중간 계층의 응답을 받았는지 확인하세요. 응답을 보지 못했다면 사이트 문제로 판단하기 전에 도구와 연결 경로를 점검하세요.",
    "human_handoff": "사람 확인 단계라면 기존 인계 절차로 사용자 참여를 요청하세요. 확인 절차를 우회하지 마세요.",
    "account_handoff": "사용자 로그인이나 계정 권한이 필요한 단계인지 확인하세요. 이 서비스로 자격 증명을 요청하거나 공유하지 마세요.",
    "before_target_response": "대상 응답을 확인하지 못했습니다. 먼저 에이전트 도구·브라우저·연결 경로를 점검하세요. 사이트 장애의 증거는 아닙니다.",
    "response_observed": "응답을 확인했습니다. 원인을 판단하기 전에 대상 사이트, 중간 프록시, 보안 계층 중 어디서 온 응답인지 확인하세요.",
    "access_denial_observed": "접근 거부를 확인했습니다. 사이트가 밝힌 정책을 따르고 제한을 우회하지 마세요. 장애의 증거는 아닙니다.",
    "provider_scope": "업체가 활성 문제를 발표했습니다. 대기·재시도를 정하기 전에 영향받는 서비스와 발표 시각을 현재 작업과 비교하세요.",
    "provider_components_only": "업체 페이지 전체 표시는 문제를 가리키지만, 업체가 발표한 사건이나 점검은 없습니다(provider_status.basis 가 components_only). 일부 컴포넌트만 장애·점검으로 표시돼 있으니, 업체 상태 페이지에서 사용하는 컴포넌트가 거기 있는지 확인하세요. minor·maintenance 표시는 기다릴 이유로 보지 않지만, major·critical 표시는 사건이 게시되기 전에 컴포넌트에 먼저 장애가 보일 수 있어 계속 기다릴 이유로 봅니다.",
    "provider_incident_stale": f"업체의 활성 사건이 {STALE_INCIDENT_DAYS}일 넘게 업데이트되지 않았고(가장 최근 업데이트는 provider_status.incident_updated_at) 진행 중인 점검도 없습니다. 업데이트 없이 열려 있는 사건은 기다릴 이유로 보지 않습니다. provider_status.affected 에 사용하는 서비스가 있으면 업체를 기다리고, 없으면 진행하세요. 다만 페이지 전체 표시가 가장 심한 사건보다 높은 major·critical 이면 새 장애가 사건 게시 전에 컴포넌트에 먼저 보일 수 있어 계속 기다릴 이유로 봅니다.",
    "provider_scope_partial": "발표된 문제의 범위가 업체 일부일 수 있습니다. 영향받는 컴포넌트가 모두 지역·거점(PoP) 그룹이거나, 가장 심한 사건이 페이지 전체 표시보다 가볍습니다. 기다리기 전에 provider_status.affected 에 사용하는 서비스가 있는지 확인하세요.",
    "provider_update_time_unknown": "업체 상태 페이지가 경미(minor) 문제를 표시하지만 이 서비스가 읽을 수 있는 사건 목록이 없고 쓸 수 있는 페이지 갱신 시각(provider_status.source_updated_at)도 없어, 지금 문제인지 몇 주째 남아 있는 표시인지 구별할 수 없습니다. 대기는 유지하되 확신도는 낮습니다. 업체 상태 페이지에서 그 문제의 날짜를 확인하고, 오래됐거나 사용하는 서비스가 없으면 진행하세요.",
    "provider_maintenance_only": "업체가 발표한 사건은 없습니다. 활성 상태의 근거는 진행 중인 점검뿐입니다(provider_status.basis 가 maintenance, active_incidents 가 0). 페이지 전체 표시는 minor·major·critical 일 수 있고, 이 서비스는 그 표시만으로 점검 말고 다른 문제가 있는지 구별할 수 없어 대기는 유지합니다. 업체 상태 페이지에서 점검 시간대와 영향받는 서비스를 현재 작업과 비교하세요. provider_scope_partial 이 함께 있으면 영향받는 것으로 올라온 것은 지역·거점(PoP)뿐입니다.",
    "provider_history_incident": f"이 서비스는 업체 상태 API 를 읽지 않습니다(그 robots.txt 가 금지하거나 아직 다시 읽지 않음). 업체의 공개 사건 이력 피드(provider_status.source_kind history_atom)에 최근 {STALE_INCIDENT_DAYS}일 안에 갱신된 열린 사건이 있습니다(provider_status.incident_updated_at). 그 피드에는 페이지 상태·등급·영향받는 서비스가 없어 대기하되 신뢰도는 낮습니다. 업체 상태 페이지에서 무엇이 영향받는지 확인하고, 사용하는 서비스가 없으면 진행하세요.",
    "provider_clear":"업체 발표상 활성 문제는 없습니다. 현재 환경에서 접속된다는 증거는 아니므로 이번 시도를 계속 진단하세요.",
    "provider_unknown": "여기에는 실시간 업체 상태가 없습니다. 공식 상태 페이지 링크가 있으면 확인하고 현재 상태는 미상으로 두세요.",
    "reports_unknown": "일치하는 공개 관찰이 없습니다. 사이트가 정상이라거나 이번 문제가 개별적이라는 뜻은 아닙니다.",
    "reports_stale": "오래된 공개 관찰만 있습니다. 현재 작업과 시각을 비교하세요. 현재 접속 가능성의 증거는 아닙니다.",
    "reports_unverified": "공개 관찰의 시간과 문제 유형을 이번 시도와 비교하세요. 제보는 미검증이며 공통 원인을 확정하지 않습니다.",
    "robots_blocks_agents": "이 호스트의 robots.txt가 당신 에이전트의 토큰(agent_vendor)에 대해, 벤더를 보내지 않았다면 나열된 모든 벤더의 토큰에 대해 사이트 루트 접근을 명시적으로 금지합니다(access_profile.robots.agents_blocked). 호스트가 선언한 정책으로 보고, 페이지 자동 재시도를 멈추고 공식 경로를 우선하며 사용자 참여가 필요한 단계는 사용자에게 넘기세요.",
    "robots_blocks_some_agents": "이 호스트의 robots.txt가 일부 에이전트 토큰의 사이트 루트 접근을 명시적으로 금지합니다(access_profile.robots.agents_blocked). 이 답은 당신이 어느 에이전트인지 모릅니다. 당신의 요청이 그 토큰 중 하나로 자신을 밝힌다면 선언된 차단으로 보고 페이지 자동 재시도를 멈추고 공식 경로를 우선하세요. 아니라면 그 차단은 당신 얘기가 아닙니다. agent_vendor 를 보내면 당신 에이전트에 대한 답을 받습니다.",
    "robots_blocks_not_your_agent": "일부 에이전트 토큰이 명시적으로 금지돼 있지만(access_profile.robots.agents_blocked), 이 답이 판정한 토큰(당신 벤더의 판정 토큰, agent_vendor 가 없으면 모든 벤더의 판정 토큰)은 아닙니다. 그 차단은 이 답을 정하지 않습니다. 호스트가 일부 자동화를 반기지 않을 수 있으니 요청을 절제하세요. 접근 보장이 아닙니다. 이 서비스는 목록에 있는 토큰만 읽으므로, 당신의 페처가 자기만의 토큰을 보낸다면 그 이름을 지목한 그룹이 있는지 robots.txt 에서 확인하세요.",
    "robots_names_vendor_alias": "사이트 루트를 금지하는 robots.txt 그룹이 당신 벤더의 에이전트를 문서화된 토큰과 맞지 않는 이름(예: 옛 이름)으로 지목합니다. 당신 토큰에 대한 선언된 차단으로 보지는 않지만 허용으로 읽지도 마세요. 공식 경로를 우선하고 사용자에게 알리세요.",
    "robots_allows_agents": "이 호스트의 robots.txt는 이 답이 판정한 에이전트 토큰의 루트 접근을 금지하지 않습니다. 선언된 크롤러 정책은 접근 보장이 아니며 CDN 챌린지와 속도 제한은 여전히 적용될 수 있으니 요청을 절제하고 Crawl-delay를 지키세요.",
    "robots_unknown": "이 호스트의 robots.txt 를 읽지 못했습니다(리다이렉트, 미도달, 형식 오류 또는 크기 초과; access_profile.robots_detail 참조). 선언된 정책을 알 수 없으며, 이 서비스는 이를 차단으로도 제한 없음 선언으로도 보지 않습니다.",
    "robots_wildcard_disallow": "이 호스트의 robots.txt는 이름을 지정하지 않은 모든 크롤러(User-agent: *)의 사이트 루트 접근을 금지하며, 에이전트 UA 가운데 어떤 그룹도 지목하지 않은 것이 있어 그 와일드카드에 해당합니다(호스트가 지목해 허용한 토큰은 access_profile.robots.agents_allowed 참조). 사용자 지시 페처가 이 와일드카드를 따르는지는 운영사 정책이지만, 호스트가 자동화를 환영하지 않는다고 보고 공식 경로가 있으면 그쪽을 쓰며 허용으로 읽지 마세요.",
    "robots_grouping_differs_by_parser": "이 robots.txt 는 해석기마다 답이 갈립니다(루트 판정이 바뀌는 '*' 와 에이전트 토큰은 access_profile.robots.grouping_differs_by_parser). Google 방식 해석기는 이 답이 나눈 그룹을 합쳐 대개 더 엄격하게 읽거나, 쉼표로 나열한 User-agent 줄에서 첫 이름만 읽습니다(이 답은 나열한 이름을 모두 읽습니다). 권장 행동은 이 서비스의 해석을 따릅니다. 운영사가 Google 방식을 따른다면 답을 쓰기 전에 그 토큰들을 그 방식으로 다시 확인하세요.",
    "robots_fetch_refused": "이 호스트의 경계가 데이터센터 주소·정직한 봇 UA의 단순 robots.txt 읽기조차 거절했습니다(access_profile.robots_detail 참조). 비슷한 환경의 페이지 자동화도 거절될 것으로 보고, 공식 경로를 우선하고 사용자 참여가 필요한 단계는 사용자에게 넘기세요.",
    "profile_not_read": "선언 정책은 공개 apex 호스트에 대해서만 읽으며, 이 호스트명은 서브도메인·테넌트 호스트라 robots.txt를 읽지 않았습니다. 사이트 전체 선언은 apex 호스트로 조회하세요. 서브도메인에 별도 규칙이 있을 수 있습니다.",
    "profile_pending": "이 호스트의 robots.txt·llms.txt 를 지금 읽고 있습니다. access_profile.retry_after_seconds 초 뒤 같은 조회를 한 번 더 하면 대개 선언 정책이 들어 있습니다. 반복 폴링은 하지 마세요.",
    "llms_txt_present": "이 호스트는 llms.txt를 게시합니다. 먼저 읽으세요. 언어 모델 에이전트를 위한 호스트 자체 안내이며 선호하는 기계 판독 자원을 가리킬 수 있습니다.",
    "content_use_ai_input_declared_no": "이 호스트의 robots.txt 가 사용자 지시 에이전트에 ai-input=no(또는 AIPREF ai-use=n)를 선언했습니다(access_profile.content_use). 접근 규칙이 아니라 콘텐츠 이용 선호입니다. 운영사 정책을 따르고, 중요하면 사용자에게 알리세요.",
    "official_path_available": "이 호스트에는 공식 개발자 경로가 등록돼 있습니다(official_paths). 페이지 자동화보다 문서화된 API나 개발자 포털을 우선하세요. 허용된 경로이며 대개 더 안정적입니다.",
    "government_registry_listed": "이 도메인은 공개 정부 도메인 목록에 있습니다(host_facts.government_registry 에 목록·판본·적힌 기관명이 있습니다). 목록은 도메인을 누가 등록했는지를 말할 뿐, 그 안의 모든 페이지·링크를 보증하지 않습니다.",
    "government_suffix_restricted": "이 접미사 아래 등록은 레지스트리 자체 규칙상 정부 기관으로 제한됩니다(host_facts.suffix_rule). 이는 누가 도메인을 등록할 수 있는지를 말할 뿐, 어떤 페이지·링크도 보증하지 않습니다.",
    "government_list_version_missing": "이 도메인은 host_facts.government_registry 에 표시된 판본의 정부 도메인 목록에 없습니다. 목록은 게시자의 일정을 따르므로 새로 등록된 도메인은 빠져 있을 수 있습니다. 이는 목록 판본에 관한 사실이지 사이트에 대한 판정이 아닙니다. 자격 증명이나 결제 정보를 넣기 전에 그 기관이 다른 곳에 공개한 주소와 비교하세요.",
}

# D48 changed the wording of these two codes. AHN_VENDOR_AWARE_BLOCK=0 restores the previous rule and, with it, the
# previous wording, so that switching the rule off gives the answers served before D48 byte for byte.
PREVIOUS_CHECKS = {
    "robots_blocks_agents": "This host's robots.txt explicitly disallows one or more agent user agents at the site root (see access_profile.robots.agents_blocked). Treat it as the host's declared policy: stop automated retries against pages, prefer an official path, and hand the step to the user where their participation is needed.",
    "robots_allows_agents": "This host's robots.txt does not disallow the listed agent user agents at the site root. Declared crawler policy is not an access guarantee; CDN challenges and rate limits can still apply, so keep requests modest and honor any Crawl-delay.",
}

# Same code (provider_scope), different text: the provider announced scheduled work, which is not an incident to diagnose.
# D63: the same code, a different sentence. robots_allows_agents for a host that publishes no robots.txt (HTTP 404 or 410);
# llms_txt_present for an llms.txt larger than this service reads; provider_unknown for an Atom incident history, whose
# feed carries no page status, so indicator stays null by design.
ROBOTS_UNKNOWN_BEFORE = "No robots.txt policy could be read for this host (absent, redirected, or unreachable within limits). Treat the host's agent policy as unknown rather than permissive."
ROBOTS_ABSENT_CHECK = "This host publishes no robots.txt (HTTP 404 or 410; access_profile.robots_status is absent), so it declares no restriction for any agent. This is not an access guarantee: CDN challenges, sign-ins and rate limits can still apply, and the site's terms and your platform's rules are separate."
ROBOTS_ABSENT_CHECK_KO = "이 호스트는 robots.txt 를 게시하지 않습니다(HTTP 404·410, access_profile.robots_status 가 absent). 어떤 에이전트에도 제한을 선언하지 않은 것입니다. 접근 보장은 아니며 CDN 챌린지·로그인·속도 제한은 여전히 적용될 수 있고, 사이트 약관과 플랫폼 규칙은 별개입니다."
LLMS_TOO_LARGE_CHECK = "This host publishes llms.txt, but it is larger than this service reads (access_profile.llms_detail is too_large), so its content was not checked here. If your task needs it, read only the part you need; it is the host's own guidance for language-model agents."
LLMS_TOO_LARGE_CHECK_KO = "이 호스트는 llms.txt 를 게시하지만 이 서비스가 읽는 크기를 넘어(access_profile.llms_detail 이 too_large) 내용을 확인하지 않았습니다. 작업에 필요하면 필요한 부분만 읽으세요. 언어 모델 에이전트를 위한 호스트 자체 안내입니다."
HISTORY_QUIET_CHECK = "The provider's public incident history was read (provider_status.source_kind is history_atom). That feed carries no page status, so indicator stays null, and nothing in it makes this answer wait. This is not a statement that the provider is healthy; check its status page if your step depends on it."
HISTORY_QUIET_CHECK_KO = "업체의 공개 사건 이력 피드를 읽었습니다(provider_status.source_kind 가 history_atom). 그 피드에는 페이지 상태가 없어 indicator 는 null 이며, 이 답을 기다리게 할 내용은 없습니다. 업체가 정상이라는 뜻은 아닙니다. 작업이 업체에 달려 있으면 상태 페이지를 확인하세요."

MAINTENANCE_CHECK = "The provider has announced scheduled maintenance. Compare its window and affected services with your blocked step; wait for the maintenance to end instead of retrying in a loop."
MAINTENANCE_CHECK_KO = "업체가 예정된 점검을 발표했습니다. 점검 시간대와 영향받는 서비스를 현재 작업과 비교하고, 반복 재시도하지 말고 점검이 끝날 때까지 기다리세요."
# Same code (provider_incident_stale), different text: a minor page with no incident list, from a feed whose update
# time is that of its active incidents and maintenances (source_time active_items), where that time is more than a week
# old (D54). The D45 text names incident_updated_at and a maintenance count, which such a feed does not have.
PAGE_STALE_CHECK = f"The provider's status page shows a minor issue, lists no incidents this service can read, and nothing it lists as active (incidents or maintenance) has been updated for more than {STALE_INCIDENT_DAYS} days (provider_status.source_updated_at, source_time active_items). An issue left without updates is not treated as a reason to wait: if the provider's status page names a service you use as affected, wait for the provider; otherwise continue."
PAGE_STALE_CHECK_KO = f"업체 상태 페이지가 경미(minor) 문제를 표시하지만 이 서비스가 읽을 수 있는 사건 목록이 없고, 페이지가 진행 중으로 올린 사건·점검이 {STALE_INCIDENT_DAYS}일 넘게 갱신되지 않았습니다(provider_status.source_updated_at, source_time active_items). 갱신 없이 남아 있는 문제는 기다릴 이유로 보지 않습니다. 업체 상태 페이지에 사용하는 서비스가 영향받는다고 나와 있으면 업체를 기다리고, 없으면 진행하세요."
ACTIVE_INDICATORS = status_scope.ACTIVE_INDICATORS  # minor, major, critical, maintenance
WAIT_DESPITE_COMPONENTS_ONLY = frozenset({"major", "critical"})
# D54: a provider time further ahead than this is treated as no time at all (shared with the public board, D53).
FUTURE_SLACK = timedelta(hours=1)

AGENT_KINDS_FOR_BLOCK = {"user_agent", "search_crawler"}


def incident_stale(provider_status: dict | None, now: datetime | None = None) -> bool:
    """D45: the active status rests on incidents only (basis incident, no maintenance in progress) and the latest
    update of every active incident is more than STALE_INCIDENT_DAYS old. An unknown maintenance count or a missing
    or unreadable update time is not "stale": unknown is not anything."""
    if not provider_status or provider_status.get("basis") != "incident":
        return False
    maintenances = provider_status.get("active_maintenances")
    if isinstance(maintenances, bool) or maintenances != 0:
        return False
    updated = provider_status.get("incident_updated_at")
    if isinstance(updated, str):
        try:
            updated = status_scope._aware(updated)
        except ValueError:
            return False
    if not isinstance(updated, datetime) or updated.tzinfo is None:
        return False
    return (now or datetime.now(timezone.utc)) - updated > timedelta(days=STALE_INCIDENT_DAYS)


def usable_provider_time(value, now: datetime) -> datetime | None:
    """A provider time this service can judge: timezone-aware (an ISO string is parsed) and not more than FUTURE_SLACK
    ahead of now. None for a missing, unreadable, naive or far-future time. Shared with the board (D53, D54)."""
    if isinstance(value, str):
        try:
            value = status_scope._aware(value)
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None or value - now > FUTURE_SLACK:
        return None
    return value


def provider_updated_at(provider_status: dict, now: datetime) -> datetime | None:
    """The latest usable provider time (page or incident): the time the board judges and shows (D53), and the one a
    page with no incident list is judged on (D54)."""
    times = [time for time in (usable_provider_time(provider_status.get("source_updated_at"), now),
                               usable_provider_time(provider_status.get("incident_updated_at"), now)) if time is not None]
    return max(times) if times else None


def _minor_page_without_incident_list(provider_status: dict) -> bool:
    """D54: a minor page whose statement rests on no list this service read: no basis (the incident list, or the
    maintenance count beside an empty one, was not read; D43 basis), not a reading of a page's Atom history (D52, which
    has no indicator), and no maintenance counted in progress (a count of 1 or more, or any value that is not a whole
    number, leaves the answer alone).

    The indicator does not tell whether a maintenance is in progress: on 2026-10-22 www.cloudflarestatus.com had two
    maintenances in progress and showed major. An unread count is still accepted here, because the only feeds a stale
    time can release (page_stale: source_time active_items) store no count: an Instatus time is the latest updatedAt of
    its active incidents and active maintenances, so a maintenance updated within the week keeps the page from being
    stale, and Google's Gmail feed lists incidents only. What this leaves open is in D54 (trade-off c)."""
    if provider_status.get("indicator") != "minor" or provider_status.get("basis") is not None:
        return False
    if provider_status.get("source_kind") == "history_atom":
        return False
    maintenances = provider_status.get("active_maintenances")
    return maintenances is None or (type(maintenances) is int and maintenances == 0)


def page_stale(provider_status: dict | None, now: datetime | None = None) -> bool:
    """D54: a minor page with no incident list (D45 has no incident time to judge), from a feed whose update time is
    that of its active incidents and maintenances (source_time active_items: Instatus, Google Gmail), whose latest
    usable provider time is more than STALE_INCIDENT_DAYS old. A missing or unreadable time is not "stale": unknown is
    not anything. Any other feed is never stale here: its stored time is the page's own (an incident.io page's
    page.updated_at stood still while incidents came and went, D54), so an old one says nothing about the incidents."""
    if not provider_status or provider_status.get("source_time") != "active_items":
        return False
    if not _minor_page_without_incident_list(provider_status):
        return False
    now = now or datetime.now(timezone.utc)
    updated = provider_updated_at(provider_status, now)
    return updated is not None and now - updated > timedelta(days=STALE_INCIDENT_DAYS)


def page_time_unknown(provider_status: dict | None, now: datetime | None = None) -> bool:
    """D54: a minor page with no incident list and no usable provider time at all, from any feed. The answer still
    waits (a time we cannot read is not an old one), with low confidence and provider_update_time_unknown. A page that
    carries an old page-wide time (not source_time active_items) is neither this nor page_stale: it waits as before."""
    if not provider_status or not _minor_page_without_incident_list(provider_status):
        return False
    return provider_updated_at(provider_status, now or datetime.now(timezone.utc)) is None


def maintenance_only(provider_status: dict | None) -> bool:
    """D55: a fresh active statement that rests only on maintenance in progress: basis maintenance and an incident
    count read as exactly 0. Information only: it never decides whether the answer waits or how sure it is, and only
    adds the provider_maintenance_only code and its wording. A missing or non-integer count is not 0 (unknown is not
    anything)."""
    if not (provider_status and provider_status.get("freshness") == "fresh"
            and provider_status.get("indicator") in ACTIVE_INDICATORS and provider_status.get("basis") == "maintenance"):
        return False
    incidents = provider_status.get("active_incidents")
    return type(incidents) is int and incidents == 0


def history_incident(provider_status: dict | None, now: datetime | None = None) -> bool:
    """D57: a fresh reading of a page's public Atom history (D52, source_kind history_atom, no indicator) that lists at
    least one open incident whose latest update (incident_updated_at, the newest <updated> of the open incidents) is a
    usable provider time no more than STALE_INCIDENT_DAYS old, from a feed whose window is not blind. Such a reading
    makes the answer wait, with low confidence: the feed has no page indicator, impact or components.

    Everything else stays what it was under D52 (information only, provider_unknown): no open incident (unknown, never
    healthy), a count that is not a whole number of 1 or more, a missing, unreadable or far-future time (unknown is not
    anything), an incident older than the week (the D45 rule; its page-above exception cannot be judged without an
    indicator), and a blind window (window_blind: the feed may be missing incidents, so its view is not trusted)."""
    if not (provider_status and provider_status.get("source_kind") == "history_atom"
            and provider_status.get("freshness") == "fresh" and provider_status.get("indicator") is None):
        return False
    if provider_status.get("window_blind") not in (None, False):
        return False
    incidents = provider_status.get("active_incidents")
    if type(incidents) is not int or incidents < 1:
        return False
    now = now or datetime.now(timezone.utc)
    updated = usable_provider_time(provider_status.get("incident_updated_at"), now)
    return updated is not None and now - updated <= timedelta(days=STALE_INCIDENT_DAYS)


def page_above_incidents(provider_status: dict) -> bool:
    """A major or critical page indicator higher than the worst active incident (or that worst impact unknown):
    something besides the incidents, such as components flipped by monitoring, holds the page up."""
    if provider_status.get("indicator") not in WAIT_DESPITE_COMPONENTS_ONLY:
        return False
    worst = status_scope.impact_rank(provider_status.get("max_incident_impact"))
    return worst is None or status_scope.impact_rank(provider_status["indicator"]) > worst


def provider_wait_reason(provider_status: dict | None, now: datetime | None = None) -> str | None:
    """Whether the provider's statement alone makes the lookup answer wait, and under which reason code: the first rows
    of the recommendation, shared with the public board (signal_history.board_provider) so the two never part.
    None: no fresh active statement, or one that is not a reason to wait (D43, D45, D54)."""
    if history_incident(provider_status, now):
        # D57: an open incident listed in the page's Atom history and updated within the week. It has no indicator,
        # so none of the rows below apply; recommendation() lowers the confidence and says why.
        return "provider_scope"
    if not (provider_status and provider_status.get("freshness") == "fresh"
            and provider_status.get("indicator") in ACTIVE_INDICATORS):
        return None
    if incident_stale(provider_status, now):
        # Only incidents nobody has updated for over a week (D45): not a reason to wait by themselves. A major or
        # critical page above them still waits, for the same reason as a components_only page (D43).
        return "provider_incident_stale" if page_above_incidents(provider_status) else None
    if page_stale(provider_status, now):
        # A minor page with no incident list whose active incidents and maintenances nobody has updated for over a
        # week (D54, Instatus and Google Gmail feeds only): not a reason to wait, for the reason of D45. A major or
        # critical page keeps waiting, and so do a page with no usable time and any feed whose time is the page's own.
        return None
    if provider_status.get("basis") != "components_only":
        return "provider_scope"
    # Nothing announced (basis components_only, D43). Only a minor or maintenance page stops being a reason to
    # wait: a major or critical page may be a monitoring integration flipping components before anyone posts an
    # incident, or a list read from a cache that lags the indicator, so the wait stays and the check explains it.
    if provider_status.get("indicator") in WAIT_DESPITE_COMPONENTS_ONLY:
        return "provider_components_only"
    return None


def declared_block(access_profile: dict | None) -> list[str]:
    """Agent tokens whose own robots group disallows the site root (declared policy, not a measurement)."""
    if not access_profile or access_profile.get("robots_status") != "ok" or not access_profile.get("robots"):
        return []
    return [rule["agent"] for rule in access_profile["robots"].get("rules", [])
            if rule.get("explicit_group") and not rule.get("root_allowed") and rule.get("kind") in AGENT_KINDS_FOR_BLOCK]


def wildcard_block(access_profile: dict | None) -> list[str]:
    """Agent tokens that no group names and that the wildcard group disallows at the root: not a declared agent block, not permission either."""
    if not access_profile or access_profile.get("robots_status") != "ok" or not access_profile.get("robots"):
        return []
    return [rule["agent"] for rule in access_profile["robots"].get("rules", [])
            if not rule.get("explicit_group") and not rule.get("root_allowed") and rule.get("kind") in AGENT_KINDS_FOR_BLOCK]


# D48 vendor states, from strictest to most permissive.
EXPLICIT, ALIAS, WILDCARD, ALLOWED = "explicit", "alias", "wildcard", "allowed"


def accepted_vendor(value) -> str | None:
    """agent_vendor as sent when it is one of the listed values (exact match), else None: treated as not sent, never an
    error, like the D47 signal codes. Accepts whatever type arrived (query text or any MCP JSON value)."""
    from .models import AgentVendor
    return value if isinstance(value, str) and value in AgentVendor._value2member_map_ else None


def vendor_state(access_profile: dict, vendor: str) -> str | None:
    """robots state of one vendor (D48), read from the stored rules; None when it cannot be read (missing).

    Missing: the profile was parsed before vendor_aliases_blocked existed, or a governing token is not in its rules
    (the token table changed after the read). Missing is never read as allowed (unknown is not anything)."""
    robots = access_profile.get("robots") or {}
    if "vendor_aliases_blocked" not in robots:
        return None
    if vendor == "other":
        # A token outside the table cannot be named by a group this service reads; only the * group applies to it.
        return WILDCARD if robots.get("wildcard_root_allowed") is False else ALLOWED
    rules = {rule.get("agent"): rule for rule in robots.get("rules") or []}
    governing = [rules.get(token) for token in GOVERNING_TOKENS[vendor]]
    if any(rule is None for rule in governing):
        return None
    if any(rule.get("explicit_group") and not rule.get("root_allowed") for rule in governing):
        return EXPLICIT
    # An alias counts only while no group names the vendor's own token: a host that names Claude-User and allows it has
    # said what it wants for that agent, and an old claude-web block next to it does not override that.
    if (not any(rule.get("explicit_group") for rule in governing)
            and set(robots.get("vendor_aliases_blocked") or ()) & VENDOR_ALIASES.get(vendor, frozenset())):
        return ALIAS
    if any(not rule.get("explicit_group") and not rule.get("root_allowed") for rule in governing):
        return WILDCARD
    return ALLOWED


def _legacy_robots(access_profile: dict) -> tuple[str, list[str]]:
    if declared_block(access_profile):
        return "block", ["robots_blocks_agents"]
    if wildcard_block(access_profile):
        return "caution", ["robots_wildcard_disallow"]
    return "clear", ["robots_allows_agents"]


def robots_verdict(access_profile: dict, agent_vendor: str | None = None, *, vendor_aware: bool = True) -> tuple[str, list[str]]:
    """The robots.txt part of an answer for a profile with robots_status ok: ("block" | "caution" | "clear", codes).

    block: the declared-block rows; caution: official path medium or unknown low; clear: the official-path row, then
    proceed. ``codes`` are the reasons the robots part adds, in order, and also next_checks item 3. With
    ``vendor_aware`` false, or when a needed vendor state is missing, the rule before D48 applies."""
    if not vendor_aware:
        return _legacy_robots(access_profile)
    others_blocked = bool(declared_block(access_profile))
    if agent_vendor is not None:
        state = vendor_state(access_profile, agent_vendor)
        if state is None:
            return robots_verdict(access_profile, None)
        if state == EXPLICIT:
            return "block", ["robots_blocks_agents"]
        if state == ALIAS:
            return "caution", ["robots_names_vendor_alias"]
        if state == WILDCARD:
            return "caution", ["robots_wildcard_disallow"]
        return "clear", ["robots_blocks_not_your_agent" if others_blocked else "robots_allows_agents"]
    states = [vendor_state(access_profile, vendor) for vendor in AGENT_VENDORS]
    if None in states:
        return _legacy_robots(access_profile)
    # Without a vendor, the agreement of every vendor's answer: never more permissive than the answer for any vendor
    # (the caller may be the blocked one) and never stricter than the answer for every vendor. "other" is not in it:
    # an unknown caller is not assumed to be outside the table.
    if all(state == EXPLICIT for state in states):
        return "block", ["robots_blocks_agents"]
    wildcard = ["robots_wildcard_disallow"] if WILDCARD in states else []
    if EXPLICIT in states:
        return "caution", ["robots_blocks_some_agents"] + wildcard
    if ALIAS in states or WILDCARD in states:
        return "caution", (["robots_names_vendor_alias"] if ALIAS in states else []) + wildcard
    return "clear", ["robots_blocks_not_your_agent" if others_blocked else "robots_allows_agents"]


def blocked_governing_tokens(access_profile: dict) -> list[str]:
    """The agent tokens (from this service's token table, never a name from the site's own groups) of every vendor whose
    robots state is an explicit block at the root, in vendor-table order without repeats (D51, next_action.stop_if_you_are).

    Reads the same per-vendor states as robots_verdict, so the list and the ``robots_blocks_some_agents`` reason cannot
    disagree. Empty when a vendor state cannot be read (the rule before D48 applies then)."""
    states = [(vendor, vendor_state(access_profile, vendor)) for vendor in AGENT_VENDORS]
    if any(state is None for _, state in states):
        return []
    tokens: list[str] = []
    for vendor, state in states:
        if state == EXPLICIT:
            tokens.extend(token for token in GOVERNING_TOKENS[vendor] if token not in tokens)
    return tokens


def profile_pending(access_profile: dict | None) -> bool:
    """A read of robots.txt and llms.txt is queued and no definitive answer exists yet."""
    return bool(access_profile and access_profile.get("freshness") == "pending"
                and access_profile.get("retry_after_seconds") is not None)


def failure_observed(error_class: str | None, failure_stage: str | None) -> bool:
    """D63: the lookup reports a failure. Only error_class and failure_stage count (any value, ``unknown`` too): the
    optional signal codes are counted and never change the answer (D47)."""
    return error_class is not None or failure_stage is not None


def _robots_clear(access_profile: dict | None, verdict: str | None) -> bool:
    """D63: robots.txt declares no restriction for the agent: read and clear, or absent (HTTP 404 or 410)."""
    if not access_profile:
        return False
    status = access_profile.get("robots_status")
    if status == "absent":
        return True
    return status == "ok" and (not access_profile.get("robots") or verdict == "clear")


def _wait(reason: str, provider_regional: bool) -> dict:
    # D45: an announced issue whose affected components are all regions or points of presence still waits, with low
    # confidence. "Worst incident below the page" is not enough: a major page over a fresh minor incident is how an
    # outage often starts, and must not read as less certain than the same page with no incident at all.
    if provider_regional:
        return {"action": "wait_provider_incident", "confidence": "low", "reasons": [reason, "provider_scope_partial"]}
    return {"action": "wait_provider_incident", "confidence": "medium", "reasons": [reason]}


def recommendation(error_class: str | None, provider_status: dict | None, access_profile: dict | None,
                   official: dict | None, *, profile_not_read: bool = False, provider_regional: bool = False,
                   now: datetime | None = None, agent_vendor: str | None = None, vendor_aware: bool = True,
                   failure_stage: str | None = None, proceed_without_failure: bool = True) -> dict:
    """One action for the calling agent, derived only from declared and official sources.

    provider_regional: the stored scope's affected_regional (every affected component is in a region or
    point-of-presence group, status_scope.REGIONAL_GROUPS); now: the lookup time (D45).
    agent_vendor: an accepted agent_vendor value (accepted_vendor) or None; vendor_aware: AHN_VENDOR_AWARE_BLOCK (D48).
    The accepted vendor is echoed as recommendation.agent_vendor only while the vendor-aware rule is on.
    failure_stage, proceed_without_failure: D63. With the switch on, a lookup that reports no failure (neither error_class
    nor failure_stage) and a robots.txt that is clear or absent gets proceed even where an official path is listed."""
    vendor = agent_vendor if vendor_aware else None
    result = _recommendation(error_class, provider_status, access_profile, official, profile_not_read=profile_not_read,
                             provider_regional=provider_regional, now=now, agent_vendor=vendor, vendor_aware=vendor_aware,
                             failure_stage=failure_stage, proceed_without_failure=proceed_without_failure)
    if vendor is not None:
        result["agent_vendor"] = vendor
    return result


def _recommendation(error_class: str | None, provider_status: dict | None, access_profile: dict | None,
                    official: dict | None, *, profile_not_read: bool, provider_regional: bool, now: datetime | None,
                    agent_vendor: str | None, vendor_aware: bool, failure_stage: str | None = None,
                    proceed_without_failure: bool = True) -> dict:
    wait_reason = provider_wait_reason(provider_status, now)
    if wait_reason == "provider_components_only":
        return {"action": "wait_provider_incident", "confidence": "medium", "reasons": ["provider_components_only"]}
    if wait_reason == "provider_scope" and history_incident(provider_status, now):
        # D57: an announced open incident read from the Atom history: no severity and no components (so no regional
        # scope either), so less sure.
        return {"action": "wait_provider_incident", "confidence": "low", "reasons": ["provider_scope", "provider_history_incident"]}
    if wait_reason is not None:
        result = _wait(wait_reason, provider_regional)
        if wait_reason == "provider_scope" and page_time_unknown(provider_status, now):
            # D54: a minor page with no incident list and no usable time may be a leftover; still wait, less sure.
            result["confidence"] = "low"
            result["reasons"].append("provider_update_time_unknown")
        if wait_reason == "provider_scope" and maintenance_only(provider_status):
            # D55: no incident announced, only maintenance in progress. Say so; the action and confidence stay as they are.
            result["reasons"].append("provider_maintenance_only")
        return result
    if error_class == "human_verification":
        return {"action": "hand_off_to_human", "confidence": "high", "reasons": ["human_handoff"]}
    if error_class == "login_auth":
        return {"action": "hand_off_to_human", "confidence": "medium", "reasons": ["account_handoff"]}
    official_listed = bool(official and (official.get("api_docs") or official.get("developer_portal")))
    if profile_pending(access_profile):
        # No declared-policy evidence yet: decide as for an unreadable robots.txt, and say a read is under way.
        if official_listed:
            return {"action": "use_official_path", "confidence": "medium", "reasons": ["profile_pending", "official_path_available"]}
        return {"action": "unknown", "confidence": "low", "reasons": ["profile_pending"]}
    verdict, robots_codes = None, []
    if access_profile and access_profile.get("robots_status") == "ok" and access_profile.get("robots"):
        verdict, robots_codes = robots_verdict(access_profile, agent_vendor, vendor_aware=vendor_aware)
    if verdict == "block":
        if official_listed:
            return {"action": "use_official_path", "confidence": "high", "reasons": robots_codes + ["official_path_available"]}
        return {"action": "stop_declared_block", "confidence": "medium", "reasons": robots_codes}
    if access_profile and access_profile.get("robots_status") == "refused":
        # The edge would not even serve robots.txt to an automated reader: a strong hint, not a declaration.
        if official and (official.get("api_docs") or official.get("developer_portal")):
            return {"action": "use_official_path", "confidence": "medium", "reasons": ["robots_fetch_refused", "official_path_available"]}
        return {"action": "unknown", "confidence": "medium", "reasons": ["robots_fetch_refused"]}
    if verdict == "caution":
        # "Disallow: /" for everyone it does not name (D28), a block on some vendors only, or a vendor named under an
        # alias (D48): the host did not invite automation, but it did not name the caller's token either.
        if official_listed:
            return {"action": "use_official_path", "confidence": "medium", "reasons": robots_codes + ["official_path_available"]}
        return {"action": "unknown", "confidence": "low", "reasons": robots_codes}
    clear = proceed_without_failure and _robots_clear(access_profile, verdict)
    if clear and not failure_observed(error_class, failure_stage):
        # D63: nothing sent reports a failure and robots.txt is clear (or absent): continue, and name the official path.
        return {"action": "proceed", "confidence": "low",
                "reasons": (robots_codes or ["robots_allows_agents"]) + (["official_path_available"] if official_listed else [])}
    if official_listed and error_class in (None, "site_access", "service_error"):
        # A block that is not the caller's (D48) stays visible next to the official path.
        not_yours = [code for code in robots_codes if code == "robots_blocks_not_your_agent"]
        return {"action": "use_official_path", "confidence": "medium", "reasons": not_yours + ["official_path_available"]}
    if clear or (not proceed_without_failure and access_profile and access_profile.get("robots_status") == "ok"):
        return {"action": "proceed", "confidence": "low", "reasons": robots_codes or ["robots_allows_agents"]}
    if access_profile:
        return {"action": "unknown", "confidence": "low", "reasons": ["robots_unknown"]}
    return {"action": "unknown", "confidence": "low", "reasons": ["profile_not_read"] if profile_not_read else []}


def host_fact_codes(host_facts: dict | None) -> list[str]:
    """Informational codes for the registry facts. They never change ``recommendation``."""
    if not host_facts:
        return []
    registry = host_facts.get("government_registry")
    if registry and registry.get("status") == "listed":
        return ["government_registry_listed"]
    if registry and registry.get("status") == "not_in_list_version":
        return ["government_list_version_missing"]
    return ["government_suffix_restricted"] if host_facts.get("suffix_rule") else []


def next_checks(error_class: str | None, provider_status: dict | None, evidence_level: str,
                failure_stage: str | None = None, access_profile: dict | None = None,
                official: dict | None = None, *, profile_not_read: bool = False,
                host_facts: dict | None = None, provider_partial: bool = False,
                now: datetime | None = None, agent_vendor: str | None = None,
                vendor_aware: bool = True, proceed_without_failure: bool = True) -> list[dict[str, str]]:
    """provider_partial: status_scope.scope_partial for the provider's stored scope; now: the lookup time (D45);
    agent_vendor, vendor_aware: as for recommendation() (D48); proceed_without_failure: D63."""
    codes: list[str] = []
    handoff = error_class in {"human_verification", "login_auth"}
    failed = failure_observed(error_class, failure_stage) if proceed_without_failure else True
    if failure_stage and failure_stage != "unknown":
        codes.append(failure_stage)
    elif failed and not handoff:
        codes.append("locate_failure_stage")
    if error_class == "human_verification":
        codes.append("human_handoff")
    elif error_class == "login_auth":
        codes.append("account_handoff")
    if access_profile is None and profile_not_read:
        codes.append("profile_not_read")
    if profile_pending(access_profile):
        codes.append("profile_pending")
    elif access_profile:
        status = access_profile.get("robots_status")
        if not handoff or not proceed_without_failure:
            if status == "refused":
                codes.append("robots_fetch_refused")
            elif status == "absent" and proceed_without_failure:
                codes.append("robots_allows_agents")
            elif status != "ok":
                codes.append("robots_unknown")
            elif access_profile.get("robots"):
                codes.extend(robots_verdict(access_profile, agent_vendor if vendor_aware else None, vendor_aware=vendor_aware)[1])
            else:
                codes.append("robots_allows_agents")
            if status == "ok" and (access_profile.get("robots") or {}).get("grouping_differs_by_parser"):
                # Information only: recommendation() reads the default grouping and never this list.
                codes.append("robots_grouping_differs_by_parser")
        if access_profile.get("llms_txt_status") == "present":
            codes.append("llms_txt_present")
        if ai_input_declared_no(access_profile.get("content_use")):
            # Information only: the recommendation above never reads content_use.
            codes.append("content_use_ai_input_declared_no")
    if official and (official.get("api_docs") or official.get("developer_portal")):
        codes.append("official_path_available")
    codes.extend(host_fact_codes(host_facts))
    if history_incident(provider_status, now):
        # D57: the Atom history lists a fresh open incident (no indicator, so no scope or maintenance codes).
        codes.extend(("provider_scope", "provider_history_incident"))
    elif provider_status and provider_status.get("freshness") == "fresh":
        indicator = provider_status.get("indicator")
        if indicator in ACTIVE_INDICATORS:
            if provider_status.get("basis") == "components_only":
                codes.append("provider_components_only")
            else:
                stale = incident_stale(provider_status, now) or page_stale(provider_status, now)
                codes.append("provider_incident_stale" if stale else "provider_scope")
            if provider_partial:
                codes.append("provider_scope_partial")
            if page_time_unknown(provider_status, now):
                codes.append("provider_update_time_unknown")
            if maintenance_only(provider_status):
                codes.append("provider_maintenance_only")
        elif indicator == "none":
            codes.append("provider_clear")
        else:
            codes.append("provider_unknown")
    else:
        codes.append("provider_unknown")
    codes.append("reports_stale" if evidence_level == "stale_reports" else "reports_unknown" if evidence_level == "no_reports" else "reports_unverified")
    # provider_scope says "maintenance", not "an active issue", for a maintenance page and, since D55, for any page whose
    # active status rests only on maintenance in progress (no incident announced).
    maintenance = bool(provider_status and provider_status.get("freshness") == "fresh"
                       and (provider_status.get("indicator") == "maintenance" or maintenance_only(provider_status)))
    texts = CHECKS if vendor_aware else {**CHECKS, **PREVIOUS_CHECKS}
    if page_stale(provider_status, now):
        texts = {**texts, "provider_incident_stale": PAGE_STALE_CHECK}
    # D63: the same code with a different sentence. The absent sentence needs the switch: off, the code is robots_unknown.
    if proceed_without_failure and access_profile and access_profile.get("robots_status") == "absent":
        texts = {**texts, "robots_allows_agents": ROBOTS_ABSENT_CHECK}
    if not proceed_without_failure:
        texts = {**texts, "robots_unknown": ROBOTS_UNKNOWN_BEFORE}  # the switch off gives the sentences from before D63 too
    else:
        if access_profile and access_profile.get("llms_detail") == "too_large":
            texts = {**texts, "llms_txt_present": LLMS_TOO_LARGE_CHECK}
        if (provider_status and provider_status.get("source_kind") == "history_atom" and provider_status.get("freshness") == "fresh"
                and not history_incident(provider_status, now)):
            texts = {**texts, "provider_unknown": HISTORY_QUIET_CHECK}
    return [{"code": code, "text": MAINTENANCE_CHECK if maintenance and code == "provider_scope" else texts[code]} for code in codes]
