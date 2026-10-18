"""Cautious next checks derived from evidence, never a site-status verdict."""

from __future__ import annotations


CHECKS = {
    "locate_failure_stage": "First ask whether your tool received a response from the target or an intermediary. If no response was observed, inspect the client and connection path before attributing a site failure.",
    "human_handoff": "A human verification step needs the user's participation through the normal handoff. Do not bypass the challenge.",
    "account_handoff": "Check whether this step needs the user's sign-in or account permission. Do not request or share credentials through this service.",
    "before_target_response": "No target response was observed. Check the agent tool, browser, and connection path first; this does not establish a site outage.",
    "response_observed": "A response was observed. Check whether it came from the target, an intermediary, or a security layer before attributing the failure.",
    "access_denial_observed": "An access denial was observed. Follow the site's stated policy and do not bypass its controls; this does not establish an outage.",
    "provider_scope": "The provider reports an active issue. Compare its affected services and update time with your blocked step before deciding whether to wait or retry.",
    "provider_clear": "The provider reports no active issue. Continue diagnosing this specific attempt; the statement does not prove access from your environment.",
    "provider_unknown": "No live provider status is available here. Check an official status page if one is linked, and keep the current status unknown.",
    "reports_unknown": "No matching published observations were found. This does not establish that the site works or that your issue is isolated.",
    "reports_stale": "Only older published observations were found. Compare their times with your task; they do not establish current access.",
    "reports_unverified": "Compare the time and issue type of any published observations with your attempt. Reports are unverified and do not establish a common cause.",
    "robots_blocks_agents": "This host's robots.txt explicitly disallows one or more agent user agents at the site root (see access_profile.robots.agents_blocked). Treat it as the host's declared policy: stop automated retries against pages, prefer an official path, and hand the step to the user where their participation is needed.",
    "robots_allows_agents": "This host's robots.txt does not disallow the listed agent user agents at the site root. Declared crawler policy is not an access guarantee; CDN challenges and rate limits can still apply, so keep requests modest and honor any Crawl-delay.",
    "robots_unknown": "No robots.txt policy could be read for this host (absent, redirected, or unreachable within limits). Treat the host's agent policy as unknown rather than permissive.",
    "llms_txt_present": "This host publishes llms.txt. Read it first: it is the host's own guidance to language-model agents and may point to preferred machine-readable resources.",
    "official_path_available": "An official developer path is listed for this host (official_paths). Prefer the documented API or developer portal over page automation; it is the sanctioned route and usually the more reliable one.",
}

CHECKS_KO = {
    "locate_failure_stage": "먼저 대상 사이트나 중간 계층의 응답을 받았는지 확인하세요. 응답을 보지 못했다면 사이트 문제로 판단하기 전에 도구와 연결 경로를 점검하세요.",
    "human_handoff": "사람 확인 단계라면 기존 인계 절차로 사용자 참여를 요청하세요. 확인 절차를 우회하지 마세요.",
    "account_handoff": "사용자 로그인이나 계정 권한이 필요한 단계인지 확인하세요. 이 서비스로 자격 증명을 요청하거나 공유하지 마세요.",
    "before_target_response": "대상 응답을 확인하지 못했습니다. 먼저 에이전트 도구·브라우저·연결 경로를 점검하세요. 사이트 장애의 증거는 아닙니다.",
    "response_observed": "응답을 확인했습니다. 원인을 판단하기 전에 대상 사이트, 중간 프록시, 보안 계층 중 어디서 온 응답인지 확인하세요.",
    "access_denial_observed": "접근 거부를 확인했습니다. 사이트가 밝힌 정책을 따르고 제한을 우회하지 마세요. 장애의 증거는 아닙니다.",
    "provider_scope": "업체가 활성 문제를 발표했습니다. 대기·재시도를 정하기 전에 영향받는 서비스와 발표 시각을 현재 작업과 비교하세요.",
    "provider_clear": "업체 발표상 활성 문제는 없습니다. 현재 환경에서 접속된다는 증거는 아니므로 이번 시도를 계속 진단하세요.",
    "provider_unknown": "여기에는 실시간 업체 상태가 없습니다. 공식 상태 페이지 링크가 있으면 확인하고 현재 상태는 미상으로 두세요.",
    "reports_unknown": "일치하는 공개 관찰이 없습니다. 사이트가 정상이라거나 이번 문제가 개별적이라는 뜻은 아닙니다.",
    "reports_stale": "오래된 공개 관찰만 있습니다. 현재 작업과 시각을 비교하세요. 현재 접속 가능성의 증거는 아닙니다.",
    "reports_unverified": "공개 관찰의 시간과 문제 유형을 이번 시도와 비교하세요. 제보는 미검증이며 공통 원인을 확정하지 않습니다.",
    "robots_blocks_agents": "이 호스트의 robots.txt가 하나 이상의 에이전트 UA에 대해 사이트 루트 접근을 명시적으로 금지합니다(access_profile.robots.agents_blocked). 호스트가 선언한 정책으로 보고, 페이지 자동 재시도를 멈추고 공식 경로를 우선하며 사용자 참여가 필요한 단계는 사용자에게 넘기세요.",
    "robots_allows_agents": "이 호스트의 robots.txt는 나열된 에이전트 UA의 루트 접근을 금지하지 않습니다. 선언된 크롤러 정책은 접근 보장이 아니며 CDN 챌린지와 속도 제한은 여전히 적용될 수 있으니 요청을 절제하고 Crawl-delay를 지키세요.",
    "robots_unknown": "이 호스트의 robots.txt 정책을 읽지 못했습니다(없음, 리다이렉트, 또는 제한 내 미도달). 에이전트 정책을 허용이 아니라 미상으로 두세요.",
    "llms_txt_present": "이 호스트는 llms.txt를 게시합니다. 먼저 읽으세요. 언어 모델 에이전트를 위한 호스트 자체 안내이며 선호하는 기계 판독 자원을 가리킬 수 있습니다.",
    "official_path_available": "이 호스트에는 공식 개발자 경로가 등록돼 있습니다(official_paths). 페이지 자동화보다 문서화된 API나 개발자 포털을 우선하세요. 허용된 경로이며 대개 더 안정적입니다.",
}

AGENT_KINDS_FOR_BLOCK = {"user_agent", "search_crawler"}


def declared_block(access_profile: dict | None) -> list[str]:
    """Agent tokens whose own robots group disallows the site root (declared policy, not a measurement)."""
    if not access_profile or access_profile.get("robots_status") != "ok" or not access_profile.get("robots"):
        return []
    return [rule["agent"] for rule in access_profile["robots"].get("rules", [])
            if rule.get("explicit_group") and not rule.get("root_allowed") and rule.get("kind") in AGENT_KINDS_FOR_BLOCK]


def recommendation(error_class: str | None, provider_status: dict | None, access_profile: dict | None,
                   official: dict | None) -> dict:
    """One action for the calling agent, derived only from declared and official sources."""
    reasons: list[str] = []
    if provider_status and provider_status.get("freshness") == "fresh" and provider_status.get("indicator") in {"minor", "major", "critical"}:
        return {"action": "wait_provider_incident", "confidence": "medium", "reasons": ["provider_scope"]}
    if error_class == "human_verification":
        return {"action": "hand_off_to_human", "confidence": "high", "reasons": ["human_handoff"]}
    if error_class == "login_auth":
        return {"action": "hand_off_to_human", "confidence": "medium", "reasons": ["account_handoff"]}
    blocked = declared_block(access_profile)
    if blocked:
        reasons.append("robots_blocks_agents")
        if official and (official.get("api_docs") or official.get("developer_portal")):
            return {"action": "use_official_path", "confidence": "high", "reasons": reasons + ["official_path_available"]}
        return {"action": "stop_declared_block", "confidence": "medium", "reasons": reasons}
    if official and (official.get("api_docs") or official.get("developer_portal")) and error_class in (None, "site_access", "service_error"):
        return {"action": "use_official_path", "confidence": "medium", "reasons": ["official_path_available"]}
    if access_profile and access_profile.get("robots_status") == "ok":
        return {"action": "proceed", "confidence": "low", "reasons": ["robots_allows_agents"]}
    return {"action": "unknown", "confidence": "low", "reasons": ["robots_unknown"] if access_profile else []}


def next_checks(error_class: str | None, provider_status: dict | None, evidence_level: str,
                failure_stage: str | None = None, access_profile: dict | None = None,
                official: dict | None = None) -> list[dict[str, str]]:
    codes: list[str] = []
    if failure_stage and failure_stage != "unknown":
        codes.append(failure_stage)
    elif error_class not in {"human_verification", "login_auth"}:
        codes.append("locate_failure_stage")
    if error_class == "human_verification":
        codes.append("human_handoff")
    elif error_class == "login_auth":
        codes.append("account_handoff")
    if access_profile:
        if access_profile.get("robots_status") != "ok":
            codes.append("robots_unknown")
        elif declared_block(access_profile):
            codes.append("robots_blocks_agents")
        else:
            codes.append("robots_allows_agents")
        if access_profile.get("llms_txt_status") == "present":
            codes.append("llms_txt_present")
    if official and (official.get("api_docs") or official.get("developer_portal")):
        codes.append("official_path_available")
    if provider_status and provider_status.get("freshness") == "fresh":
        indicator = provider_status.get("indicator")
        if indicator in {"minor", "major", "critical"}:
            codes.append("provider_scope")
        elif indicator == "none":
            codes.append("provider_clear")
        else:
            codes.append("provider_unknown")
    else:
        codes.append("provider_unknown")
    codes.append("reports_stale" if evidence_level == "stale_reports" else "reports_unknown" if evidence_level == "no_reports" else "reports_unverified")
    return [{"code": code, "text": CHECKS[code]} for code in codes]
