"""Write a domain-by-domain follow-up from ignored local probe outputs.

This is a research ledger, not a source of runtime mappings. Human-reviewed
publisher evidence and product scope must be checked before changing SOURCES.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ahn.status_refresh import OFFICIAL_STATUS_PAGES, SOURCES

WORK = ROOT / "work" / "status-discovery"
OUT = ROOT / "docs" / "status-candidate-followup.md"
PRIOR = {"github.com", "cloudflare.com", "openai.com", "dropbox.com", "digitalocean.com", "tailscale.com", "discord.com", "shopify.com"}
FALSE_MATCH = {"youtube.com", "bloomberg.com", "docker.com", "docker.io", "avito.ru", "resolver.arpa", "state.gov", "people.com", "trendyol.com", "ngenix.net"}
NARROW_SCOPE = {
    "amazon.com": "후보는 AWS/MWS이며 Amazon 소매 사이트 전체를 대표하지 않음",
    "apple.com": "후보는 Apple Developer 또는 일부 Apple 서비스; 전체 Apple.com 범위 아님",
    "atlassian.com": "제품별 상태 페이지가 다수이며 전체 Atlassian.com 단일 지표로 합칠 수 없음",
    "avast.com": "후보는 AVG CloudCare 제품",
    "bitdefender.net": "후보는 GravityZone 제품",
    "cisco.com": "후보는 Meraki·IoT·Umbrella 등 특정 제품",
    "dell.com": "후보는 Boomi이며 Dell 사이트 상태가 아님",
    "fontawesome.com": "후보는 Fort Awesome 브랜드; Font Awesome 전체 범위 미확인",
    "globo.com": "후보는 HelloGlobo로 전체 Globo 범위 미확인",
    "google.com": "Cloud·Maps·Firebase·Play·Workspace 후보는 각기 다른 제품",
    "ibm.com": "후보는 IBM Cloud 제품",
    "intuit.com": "후보는 Intuit Developer Group",
    "microsoft.com": "후보는 Azure 제품",
    "nvidia.com": "후보는 GeForce NOW 제품",
    "oracle.com": "후보는 Oracle Fusion Cloud Applications",
    "skyhigh.cloud": "후보는 Skyhigh Security; 호스트 소유·범위 추가 확인 필요",
    "wordpress.com": "후보는 WordPress VIP 제품",
    "wordpress.org": "후보는 WordPress VIP 제품",
    "yandex.com": "후보는 Yandex Cloud이며 자동 조사에 CAPTCHA 표시",
}


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def main() -> None:
    domains = rows(WORK / "candidate-domain-results.csv")
    first = rows(WORK / "candidate-probe-results.csv")
    follow = rows(WORK / "candidate-followup-results.csv")
    by_domain: dict[str, list[dict[str, str]]] = {}
    for row in first:
        by_domain.setdefault(row["ranked_domain"], []).append(row)
    follow_by_url = {row["candidate_url"]: row for row in follow}
    review = [row for row in domains if row["auto_discovery_eligible"] != "True" and row["ranked_domain"] not in PRIOR]
    if len(review) != 70:
        raise ValueError(f"expected 70 remaining candidate domains, got {len(review)}")
    lines = [
        "# 상태 출처 후보 70개 후속 조사",
        "",
        "작성일: 2026-10-03. `work/status-discovery/`의 읽기 전용 탐색 결과를 정리했다. "
        "**후보 페이지의 존재는 게시자 소유권·적용 범위·실제 사이트 장애를 증명하지 않는다.** "
        "목록의 도메인은 이용자 조회로 간주하지 않았고 운영 DB에 등록하지 않았다.",
        "",
        "## 판정 기준과 결과",
        "",
        "- `API 연결`: 공식 출처와 범위를 확인하고 고정 매핑을 코드에 추가했다. "
        "표시되는 지표는 업체 발표이며 개별 방문자의 접속 성공을 뜻하지 않는다.",
        "- `공식 페이지`: 업체 도메인 또는 공식 문서로 페이지를 확인했지만 현재 파서가 읽는 상태 API는 검증하지 못했다. "
        "사용자에게 페이지 링크와 `unknown`만 제공한다.",
        "- `제품 범위`: 후보가 더 좁은 제품·서비스만 가리킨다. 부모 도메인 전체 상태로 연결하지 않았다.",
        "- `오탐`: 제3자 집계/다른 단체의 페이지 등으로 공식 출처 후보에서 제외했다.",
        "- `추가 검증`: 현재 읽기 방식으로 업체가 공개한 호환 피드나 소유·범위를 확인하지 못했다. "
        "'제공하지 않는다'는 결론이 아니다.",
        "",
        "| 도메인 | 판정 | 확인한 후보/연결 위치 | 범위·이유 |",
        "| --- | --- | --- | --- |",
    ]
    counts: dict[str, int] = {}
    for domain_row in review:
        domain = domain_row["ranked_domain"]
        candidates = by_domain[domain]
        candidate = next((row for row in candidates if row["api_page_id"]),
                         next((row for row in candidates if row["status_url"].startswith("https://")), candidates[0]))
        follow_row = follow_by_url.get(candidate["status_url"])
        candidate_url = candidate["status_url"]
        if follow_row and follow_row["final_url"] and len(follow_row["final_url"]) < 150 and "showcaptcha" not in follow_row["final_url"]:
            candidate_url = follow_row["final_url"]
        if domain in FALSE_MATCH:
            verdict, note = "오탐", "제3자 집계 또는 이름만 일치하는 다른 기관"
        elif domain in NARROW_SCOPE:
            verdict, note = "제품 범위", NARROW_SCOPE[domain]
        elif domain in SOURCES:
            verdict, note = "API 연결", "공식 상태 API 읽기 검증; 실사용 환경의 접속 여부는 별개"
            candidate_url = SOURCES[domain].page_url
        elif domain in OFFICIAL_STATUS_PAGES:
            verdict, note = "공식 페이지", "연결 가능한 상태 API 형식은 미검증; 현재 상태는 unknown"
            candidate_url = OFFICIAL_STATUS_PAGES[domain][1]
        else:
            verdict = "추가 검증"
            if follow_row and follow_row["http"]:
                note = f"페이지 HTTP {follow_row['http']}; 호환 상태 API/업체 범위 미확인"
            elif candidate["probe_note"]:
                note = candidate["probe_note"].replace("_", " ")
            else:
                note = "호환 상태 API/업체 범위 미확인"
        counts[verdict] = counts.get(verdict, 0) + 1
        lines.append(f"| `{domain}` | {verdict} | [후보/페이지]({candidate_url}) | {note} |")
    lines.extend([
        "", "## 확인 가능한 공식 근거 예", "",
        "- [Reddit 도움말](https://support.reddithelp.com/hc/en-us/p/redditor_help_center)은 Reddit 상태 페이지를 공식 링크로 제공한다.",
        "- [Twitch 도움말](https://help.twitch.tv/s/article/how-to-provide-feedback)은 `status.twitch.com`을 서비스 상태 출처로 명시한다.",
        "- [Wikimedia 운영 문서](https://wikitech.wikimedia.org/wiki/Wikimediastatus.net)는 외부 호스팅 상태 페이지와 주요 장애 중심 범위를 설명한다.",
        "- [hCaptcha 이용약관](https://www.hcaptcha.com/terms)은 `hcaptchastatus.com`을 서비스 상태 페이지로 지정한다.",
        "- [Akamai 안내](https://eaa-status.akamai.com/)는 모든 Akamai 서비스 사고를 `akamaistatus.com`에서 공지한다고 설명한다.",
        "- [Netlify 공식 지원 가이드](https://answers.netlify.com/t/important-is-netlify-down-keeping-up-with-netlify-status/35476)는 `netlifystatus.com`을 전체 시스템 문제의 출처로 안내한다.",
        "- [Fastly 문서](https://www.fastly.com/documentation/guides/getting-started/concepts/using-fastlys-global-pop-network/)는 `fastlystatus.com`을 네트워크 상태 페이지로 연결한다.",
        "- [SurveyMonkey 도움말](https://help.surveymonkey.com/en/)은 `surveymonkey.statuspage.io`를 사이트 상태 페이지로 연결한다.",
        "", "## 남은 작업과 한계", "",
        "- 제품별 후보는 관련 제품 호스트를 따로 매핑할 때만 지표를 연결한다. 부모 도메인에 잘못된 단일 상태를 붙이지 않는다.",
        "- Google·Microsoft·Apple·AWS·Stripe·PlayStation 등은 자체 상태 형식이 있을 수 있다. "
        "공식 API 규격, 항목별 범위와 갱신 의미를 확인한 뒤 전용 파서를 추가한다.",
        "- 리디렉션 또는 Statuspage 형태 응답만으로 새 출처를 자동 승격하지 않는다. "
        "이 조사 도구는 로컬 연구용이고 서버 조회 경로에는 추가 네트워크 탐색을 넣지 않았다.",
        "- 원본 후보·수치: [1,000개 도메인 파일럿](status-discovery-seed-pilot.md). "
        "로컬 재현: `python scripts/probe_status_candidates.py`, "
        "`python scripts/investigate_status_candidates.py`, `python scripts/write_status_candidate_review.py`.",
        "", "## 배포 검수", "",
        "커밋 `9b1316c`의 배포 후 공개 읽기 API에서 Reddit·SurveyMonkey는 `feed_connected`와 "
        "`fresh`를 반환했다. Office는 `official_page_only`, Cisco는 "
        "`not_verified_by_current_methods`를 반환했다. 이 요청들은 기능 검수용이며 "
        "다른 이용자의 조회 수요나 실제 장애 관찰을 뜻하지 않는다.",
        "", f"집계: {', '.join(f'{key} {value}개' for key, value in counts.items())}. 총 {sum(counts.values())}개.", "",
    ])
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} | " + ", ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
