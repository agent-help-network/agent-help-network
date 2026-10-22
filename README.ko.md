# Agent Help Network

[English](README.md) · 한국어 — 영어판 [README.md](README.md)가 정본이며 이 문서는 같은 내용을 한국어로 옮긴 것이다.

Agent Help Network 는 공개 호스트명 하나로 묻는다: **이 호스트는 에이전트에게 무엇을 허용하는가, 공식 경로는 무엇인가, 지금 정상인가 — 그리고 다른 에이전트의 최근 관찰이 있는가.** 웹 단계(접근 거부, 로그인, 사람 확인, 서비스 오류)가 막힌 에이전트가 무작정 재시도하거나 필요 이상으로 사람에게 되돌리는 대신, 호스트가 스스로 선언한 정책과 공식 경로를 먼저 보게 하려는 서비스다. 에이전트는 REST/OpenAPI 또는 MCP 로 답을 읽고, 사람용 웹은 영어·한국어로 같은 답을 보여 준다. 사람의 확인이나 결정이 실제로 필요한 단계는 사람에게 인계한다.

Agent Help Network 는 독립 프로젝트다. OpenAI, Meta, Anthropic, Muse, dots 의 공식 서비스가 아니며 설명하는 어떤 사이트와도 제휴하지 않는다.

## 에이전트용 빠른 시작

읽기는 무료이고 계정이 필요 없다.

- **REST:** `GET https://agenthelpnetwork.com/v1/incidents/check?site_host=<공개 호스트명>`
  선택: `error_class` (`site_access`, `login_auth`, `human_verification`, `service_error`), `failure_stage` (`before_target_response`, `response_observed`, `access_denial_observed`, `unknown`), `task_step`, `window_hours` (1–168, 기본 24), 그리고 클라이언트가 이미 아는 값만 붙이는 선택 조회 코드 넷: `http_status` (401, 402, 403, 407, 408, 429, 451 또는 500–599), `challenge` (`header`, `none`), `retry_after` (`none`, `lt_60`, `60_3600`, `gt_3600`), `via` (`http_fetch`, `browser`, `unknown`). 호스트·시간별로 집계할 뿐 답은 바꾸지 않는다([계약](docs/api-contract.md#조회-신호-코드)). 답을 바꾸는 선택 코드가 하나 더 있다: `agent_vendor` (`openai`, `anthropic`, `google`, `perplexity`, `meta`, `mistral`, `amazon`, `microsoft`, `apple`, `duckduckgo`, `diffbot`, `exa`, `other`), 자기 웹 요청이 보내는 `robots.txt` 토큰의 벤더다. 판정이 그 벤더의 토큰을 읽는다. 모르면 생략한다. 집계·저장하지 않고 목록 밖 값은 무시한다([계약](docs/api-contract.md#에이전트-벤더-agent_vendor-d48)).
- **MCP** (streamable HTTP): `https://agenthelpnetwork.com/mcp` — 도구 `check_incident` (읽기 전용·멱등)와 선택적 `report_observation`.
- **에이전트용 입구:** [/llms.txt](https://agenthelpnetwork.com/llms.txt) · [/agent-instructions.txt](https://agenthelpnetwork.com/agent-instructions.txt) · [/openapi.json](https://agenthelpnetwork.com/openapi.json)
- **클라이언트에 설치:** [연결 화면](https://agenthelpnetwork.com/ko/connect#install)에 Claude Code 플러그인, Cursor, OpenAI Agents SDK, 그 밖의 원격 MCP 클라이언트 설정이 있다. Python 코드(Playwright, Browser Use, 일반 HTTP)에는 PyPI 의 읽기 전용 [`ahn-client`](clients/python/README.md) 패키지를 쓴다. 의존성이 없고 호스트명과 고정된 enum 코드만 보낸다. 0.3.0 부터는 LangChain v1(`[langchain]`), CrewAI(`[crewai]`), OpenAI Agents SDK(`[openai-agents]`), Claude Agent SDK(`[claude-agent-sdk]`)에서 웹 도구가 실패했을 때 한 줄 답을 붙여 주는 선택형 어댑터와 Browser Use 예제도 있다. 이 중 어느 것도 OpenAI, Meta, Anthropic, Muse, dots 의 공식 통합이 아니다.

  ```bash
  claude plugin marketplace add agent-help-network/agent-help-network   # Claude Code 플러그인 (소스: plugins/claude-code/ahn)
  claude plugin install ahn@agent-help-network
  pip install ahn-client                                                 # Python 클라이언트 (소스: clients/python)
  pip install "ahn-client[langchain]"                                    # 또는 [crewai], [openai-agents], [claude-agent-sdk]: 프레임워크 어댑터
  ```

  **플러그인 데모 영상:** [Claude Code 플러그인 동작 영상](https://cdn.jsdelivr.net/gh/agent-help-network/agent-help-network@1d2c2dd0693259ebd6701277e81b04290958a959/docs/media/agent-help-network-demo-v4.webm) (WebM, 1분 18초, 영어 자막 포함, [자막 파일](https://cdn.jsdelivr.net/gh/agent-help-network/agent-help-network@1d2c2dd0693259ebd6701277e81b04290958a959/docs/media/agent-help-network-demo-v4.vtt)). GitHub 은 WebM 을 화면 안에서 재생하지 않아 파일이 열린다. 같은 영상이 [연결 화면](https://agenthelpnetwork.com/ko/connect#demo)에도 들어 있다.

호스트명만 보낸다. URL 경로·쿼리·자격 증명·대화 내용은 보내지 않고, 민감한 호스트명은 피한다. 응답 예시(2026-10-18 실제 서비스의 `github.com` 응답을 핵심 필드로 줄이고, 캡처 시점의 캐시보다 새로 생긴 필드는 현재 계약대로 채운 것)는 [영어 README](README.md#for-agents)에 있다. 필드 설명은 [API 계약](docs/api-contract.md)([영어판](docs/api-contract.en.md))을 따른다.

사람은 [전체 에이전트 지침](https://agenthelpnetwork.com/agent-instructions.txt)을 [Human 화면](https://agenthelpnetwork.com/en/human)에서 복사해 에이전트 채팅에 붙여 넣을 수 있다. 공개 사이트의 기본 화면은 에이전트용 안내이고, 우측 상단 `Agent | Human` 전환으로 사람용 검색 화면에 갈 수 있다. 검색 화면은 선언된 정책 표, llms.txt, 공식 경로, 업체 상태, 관찰 순으로 표시한다.

## 응답의 구성

세 층을 따로 읽는다.

1. **`access_profile` — 선언된 정책.** 호스트 자체의 `robots.txt`(RFC 9309 우선순위, 3자 이상 user-agent 이름은 앞부분 일치로 너그럽게 매칭)에서 계산한 에이전트 토큰별 규칙과 `llms.txt` 게시 여부. 토큰 목록은 [`ahn/access_profile.py`](ahn/access_profile.py) 의 `AGENT_USER_AGENTS` 이며, 토큰의 철자와 `kind`(`user_agent`, `search_crawler`, `training_crawler`)는 업체 자체 문서를 따르고 그 문서 주소는 `AGENT_SOURCES` 에 적는다. 업체 문서로 확인되지 않는 토큰은 `UNCONFIRMED_AGENT_TOKENS` 에 두며(모두 학습 크롤러라 차단으로 세지 않는다), 호스트가 *선언한* 것이지 접근 보장이 아니다. CDN·봇 필터·로그인·속도 제한은 사용자 지시 에이전트를 크롤러와 다르게 취급할 수 있다. `robots_status` 가 `ok` 가 아니면 이 층은 판단 불가이지 차단이 아니다. 단 `refused`(호스트 경계가 단순 `robots.txt` 읽기조차 거절함)는 판정에서 공식 경로를 우선하라는 신호로 쓴다. `access_profile.content_use` 는 같은 `robots.txt` 가 선언한 콘텐츠 이용 선호(Cloudflare `Content-Signal`, IETF AIPREF `Content-Usage`)를 `yes`/`no`/`unset` 으로 정규화해 보여 주며, 정보일 뿐 판정을 바꾸지 않는다.
2. **`official_paths` 와 업체 상태 — 공식 경로와 지금 정상인지.** `official_paths` 는 사람이나 에이전트가 일을 하려고 여는 사이트의 업체 소유 API 문서·개발자 포털을 정리한 표이며 CDN·광고 기술·자산·단축 링크 호스트는 넣지 않는다. `provider_status` 는 호스트에 대해 검증된 업체 자체 상태 피드(출처·조회 시각 포함)이고, `official_status_page` 는 읽을 피드가 없는 발행자 상태 페이지 링크이며 읽은 값으로 바꾸지 않는다. 두 상태 연결 표는 발행자가 소유하거나 링크한 페이지를 근거로 사람이 정리한다([정리 방법](docs/api-contract.md#업체-상태-출처-표의-정리-2026-10-19)). 항목이 없다고 공식 경로가 없다는 뜻이 아니며, 목록은 보증이나 제휴가 아니다.
3. **`observations` — 다른 에이전트의 선택적 제보.** `no_reports` 가 보통이며 일치하는 공개 관찰이 없다는 뜻일 뿐 사이트가 정상이라는 뜻이 아니다. 실패 한 건은 장애가 아니고, 나중의 성공은 전체 복구가 아니다. `current_site_status` 는 항상 `unknown` 이다.

`recommendation.action` 은 선언·공식 층을 하나의 행동으로 요약한다. 관찰은 이 값을 바꾸지 않는다. 위에서부터 처음 맞는 행이 적용된다.

| `action` | 조건 | `confidence` |
| --- | --- | --- |
| `wait_provider_incident` | 신선한 업체 피드가 `minor`, `major`, `critical`, `maintenance` 활성 상태를 발표했고, 그 근거가 발표된 사건이나 점검이다(`provider_status.basis` 가 `components_only` 가 아님). 발표가 없어도 페이지가 `major`·`critical` 이면 대기한다. 7일 넘게 업데이트되지 않은 사건만 있고 진행 중인 점검이 없으면, 그보다 높은 `major`·`critical` 페이지 표시가 없는 한 대기 이유로 보지 않는다(`provider_incident_stale`). 영향 컴포넌트가 모두 지역·거점이면(`provider_scope_partial`) 대기의 confidence 는 `low` 다. | medium(지역·거점 전용이면 low) |
| `hand_off_to_human` | `error_class` 가 `human_verification`(high) 또는 `login_auth`(medium). | high / medium |
| `use_official_path` | 차단 선언(아래)이 있고 공식 경로가 있다. | high |
| `stop_declared_block` | 차단 선언이 있고 공식 경로가 없다. 페이지 자동 재시도를 멈춘다. | medium |
| `use_official_path` | 호스트 경계가 `robots.txt` 읽기를 거절했고(`robots_status: refused`) 공식 경로가 있다. | medium |
| `unknown` | `refused` 이고 공식 경로가 없다(사유 `robots_fetch_refused`). 비슷한 환경의 페이지 자동화도 거절될 것으로 본다. | medium |
| `use_official_path` | 부분 차단이나 와일드카드 단독 금지(아래)이고 공식 경로가 있다. | medium |
| `unknown` | 부분 차단이나 와일드카드 단독 금지이고 공식 경로가 없다(사유 `robots_blocks_some_agents`, `robots_names_vendor_alias`, `robots_wildcard_disallow`). 허락으로 읽지 않는다. | low |
| `use_official_path` | 공식 경로가 있고 `error_class` 가 없거나 `site_access`, `service_error` 다. | medium |
| `proceed` | `robots.txt` 를 읽었고 답이 판정한 토큰에 대한 금지가 없다(`robots_allows_agents`, 다른 에이전트의 토큰만 막혔으면 `robots_blocks_not_your_agent`). 선언된 허용도 접근 보장은 아니다. | low |
| `unknown` | 선언된 정책을 읽지 못했거나(사유 `robots_unknown`), 호스트명이 정책을 읽지 않는 서브도메인·테넌트 호스트다(사유 `profile_not_read`, apex 호스트로 조회). 허용이 아니라 미상으로 둔다. | low |

`reasons` 는 `next_checks` 에도 나오는 코드다. "공식 경로가 있다"는 `official_paths` 항목에 `api_docs` 나 `developer_portal` 이 있다는 뜻이며 `note` 만 있는 항목은 해당하지 않는다. **차단 선언**(D48)은 루트를 금지하는 `robots.txt` 그룹이 호출자 벤더(`agent_vendor`)의 판정 토큰(사용자 대리 페처, 그런 토큰이 없으면 검색 크롤러)을, 벤더를 보내지 않았으면 나열된 모든 벤더의 판정 토큰을 지목한 경우다. 벤더를 보내지 않았고 일부 벤더만 막혔으면 **부분 차단**이다: 답은 `unknown`(공식 경로가 있으면 medium 의 공식 경로)과 `robots_blocks_some_agents` 이며, 자기 요청이 `agents_blocked` 의 토큰으로 자신을 밝히면 멈추라는 뜻이다. 벤더의 에이전트를 옛 이름·변형 이름으로만 지목한 그룹(`robots.vendor_aliases_blocked`, 예: `claude-web`)은 `robots_names_vendor_alias` 를 주고 `proceed` 를 주지 않는다. **와일드카드 단독 금지**는 `*` 그룹(`User-agent: *` 와 `Disallow: /`)이 루트를 금지하고, 에이전트 UA·검색 크롤러 토큰 가운데 어떤 그룹도 지목하지 않아 그 와일드카드에 해당하는 토큰이 하나 이상 있으며, 그런 토큰을 지목한 그룹 중 루트를 금지하는 것은 없는(그것은 차단 선언이다) 파일이다. 토큰을 지목해 허용한 그룹이나 학습 크롤러 금지는 이 판정을 바꾸지 않는다. 차단 선언이 아니며 `robots_wildcard_disallow` 로 보고하고, "에이전트를 허용함"으로 읽지 않는다. `agents_blocked` 는 학습 크롤러를 포함해 루트 금지가 명시된 모든 토큰을 나열하지만 학습 크롤러는 차단 선언으로 세지 않는다. 영어 README 의 예시에서 `Bytespider`(학습 크롤러)는 차단돼 있지만 학습 크롤러는 차단 선언이 아니므로 medium 신뢰도의 공식 경로 행이 적용된다.

## 읽는 것과 절대 하지 않는 것

서비스가 읽는 것(공개 운영에서만, 로컬 개발판은 읽지 않는다):

- **경로 두 개만:** 공개 집계에 적합한 루트 호스트 또는 그 호스트 자신의 `www.` 별칭의 `/robots.txt`(최대 256 KB)와 `/llms.txt`(존재 여부만, 최대 4 KB). 루트 호스트의 `robots.txt` 읽기가 실패하거나, 거절되거나, 다른 곳으로 리디렉션되거나, HTML 페이지가 오면 `https://www.<호스트>/robots.txt` 를 예비로 한 번 읽는다. `llms.txt` 는 `robots.txt` 를 실제로 내준 호스트(`read_host`)에서 읽는다.
- **해석 결과만 저장한다.** `host_access_profile` 에는 토큰별 규칙, 에이전트 토큰별·`*` 그룹별 Disallow 경로 표본 최대 6개(각 80자), Sitemap URL 최대 5개, 선언된 콘텐츠 이용 선호(정규화한 값, 경로 표본 전체 최대 3개, 알려진 에이전트 토큰 표에 있는 에이전트 이름만, 해석기 버전과 robots.txt 해시), 읽기 상태와 출처(받은 파일의 SHA-256 해시, 읽기 프로그램 버전, 운영자, 선언한 네트워크 위치)만 저장한다. 파일의 나머지는 저장하지 않으며 `llms.txt` 내용은 저장하거나 전달하지 않는다.
- **공개 집계에 적합한 공개 루트 호스트만** (예: `example.com`, `example.co.uk`). 적합하다는 것은 고정 목록 `PUBLIC_AGGREGATE_TLDS` 의 최상위 도메인 아래 두 라벨 이름이거나 `co.uk` 같은 두 라벨 접미사(`PUBLIC_SUFFIXES_2`) 아래 세 라벨 이름이라는 뜻이며 둘 다 [`ahn/service.py`](ahn/service.py) 에 있다. 첫 라벨이 `xn--` 로 시작하는 이름은 적합하지 않다. 계정형 하위 도메인은 읽거나 집계하지 않는다. 적합한 루트 호스트 앞에 `www.` 하나만 붙은 이름은 그 루트 호스트 기준으로 답한다.
- **공개 주소만.** 해석된 모든 주소가 공개(global) IP 여야 하며 연결은 그 주소에 고정한다.
- **리디렉션은 최대 한 번:** 호스트에서 같은 경로의 자기 `www.` 별칭으로 가는 한 단계만 따른다. 다른 리디렉션은 `redirected` 로 기록하고 따라가지 않는다.
- **답은 캐시하고 실패는 1시간 뒤 재시도.** 확정된 답(`ok`, `absent`, `refused`, `redirected`, `invalid`, `too_large`, `not_public`)은 24시간 캐시한다. 전송 실패(DNS·TLS·연결·시간 초과·예상 밖 HTTP 상태)는 1시간 뒤 다시 시도하며 마지막 확정 답을 덮어쓰지 않는다. 한 번의 시도는 루트 호스트와 그 자신의 `www.` 별칭에 걸쳐 HTTPS 요청을 최대 다섯 번 한다: 루트 `robots.txt`, `www.` 별칭으로 가는 한 단계, `www.` 예비 읽기, `llms.txt`, 그리고 그 한 단계. 시간 제한은 짧고, 요청은 `AgentHelpNetwork/0.2 (+https://agenthelpnetwork.com/en/policy)` UA 로 자신을 밝힌다.
- **조회가 기다리는 동안에는 읽지 않는다.** 조회는 데이터베이스만으로 답한다. 없거나 만료된 읽기는 `background_reads` 에 넣고, 웹 프로세스 안의 작은 작업자(`AHN_BACKGROUND_READS=0` 이면 끔)와 5분 검토 크론이 프로세스당 초당 최대 두 번 읽는다. 큐는 최대 2,000건이고 파일·피드 본문마다 10초 시간 상한이 있다([`ahn/background_reads.py`](ahn/background_reads.py)). 처음 보는 호스트의 첫 조회는 `access_profile.freshness: pending` 을, 읽기가 큐에 들어가면 `retry_after_seconds` 도 함께 돌려주고, 만료된 프로필·피드는 캐시값을 `stale` 로 돌려주면서 다시 읽는다. 피드 읽기가 실패해도 마지막 업체 발표는 그것만으로 끝나지 않고 2시간이 될 때까지 `fresh`(`error_detail` 동반)로 남는다(D46).
- **시드 목록은 주 1회.** 공개 시드 목록 [`ahn/seed_hosts.txt`](ahn/seed_hosts.txt) 의 호스트는 조회되지 않았더라도 검토 크론이 5분마다 기본 10개씩(`AHN_WARM_BATCH`, `0` 이면 끔) 같은 한도 안에서 미리 읽고 약 주 1회 다시 읽는다([`ahn/profile_warm.py`](ahn/profile_warm.py)). 아직 읽지 않은 호스트가 먼저이고, 마지막 시도가 전송 실패였던 호스트(예: 더는 해석되지 않는 이름)는 이미 답이 있던 호스트라도 이 선읽기에서는 24시간 뒤에 다시 시도해, 없어진 이름이 배치를 다 쓰지 않게 한다. 그 호스트를 직접 조회하면 여전히 1시간 뒤 다시 읽는다.
- **업체 상태**는 검증된 공식 출처만 쓴다: [`ahn/status_refresh.py`](ahn/status_refresh.py) 의 고정 연결 규칙(파서가 읽는 피드는 `SOURCES`, 링크만 있는 것은 `OFFICIAL_STATUS_PAGES`), 또는 첫 조회 뒤 백그라운드에서 확인하고 찾을 때까지 하루 한 번까지 다시 확인하는 `status.<호스트>` 의 Statuspage 위임. 고정 연결 규칙의 모든 피드는 조회를 기다리지 않고 검토 크론이 등록해 약 1시간마다, 활성 상태를 발표하는 동안은 15분마다 읽는다(D44). 실패하는 피드는 최대 1시간 간격으로(마지막 발표가 활성이었으면 15분마다) 다시 읽고, 거절은 다른 방법으로 다시 시도하지 않으며 최대 하루 한 번까지 간격을 늘린다. 조회된 호스트에서 발견한 피드도 같은 주기로 읽는다. Atlassian Statuspage 피드는 갱신해서 활성 상태가 나오면 그 페이지의 미해결 사건과 진행 중 점검 목록(각 32 KB 상한)도 읽어, 그 수·가장 높은 사건 영향·영향받는 이름 최대 3개와, 활성 상태의 근거가 사건·점검·컴포넌트 상태뿐 중 무엇인지(`basis`)를 알린다([API 계약](docs/api-contract.md)).
- **robots.txt 가 상태 API 만 금지한 Statuspage 페이지**는 호스트가 허용하는 공개 `/history.atom` 만 읽어 발표된 사건이 열려 있는지만 알린다(`provider_status.source_kind: history_atom`, `indicator` 는 항상 `null`). 피드가 비어 있으면 정상이 아니라 알 수 없음이며 `recommendation` 은 바꾸지 않는다(D52).
- **정부 도메인 목록은 통째로 내려받는다.** 검토 크론이 미국 레지스트리의 `current-full.csv`(`cisagov/dotgov-data`, CC0-1.0)를 약 하루에 한 번, GOV.UK 의 "List of .gov.uk domain names"(GOV.UK 콘텐츠 API 로 찾음)를 약 한 달에 한 번 내려받으며, 접속하는 곳은 그 게시자뿐이고 목록에 적힌 사이트에는 접속하지 않는다([`ahn/gov_registry.py`](ahn/gov_registry.py)). 저장하는 것은 도메인 이름과, 미국 목록은 도메인 유형·기관명·하위 기관명·주이며, 미국 파일의 보안 연락 이메일과 도시는 버린다. 내려받기가 실패하거나 이상하면(열 변경, 행이 너무 적음, 저장된 행의 절반 미만, 크기 상한 초과) 저장된 데이터를 그대로 둔다. 조회는 저장된 사본만 조인하고 게시자를 기다리지 않는다. 이 정보는 응답의 `host_facts` 로, 해당하는 호스트에만 붙는다. 누가 그 이름을 등록했는지에 대한 사실이며 사이트·페이지·링크에 대한 판단이 아니고 `recommendation` 을 바꾸지 않는다.
- **영국 목록 출처 표기.** Contains public sector information licensed under the Open Government Licence v3.0.

서비스가 절대 하지 않는 것:

- 탐침하지 않는다: 작업을 대신 시도하지 않고, 대상 페이지나 다른 경로를 가져오지 않으며, 로그인·CAPTCHA 페이지에 닿지 않는다.
- 실패를 만들어 내거나 시드 관찰을 지어내지 않고, 에이전트에게 그렇게 하라고 하지 않는다.
- OpenAI, Meta, Anthropic 또는 설명하는 어떤 호스트와의 제휴·보증도 주장하지 않는다.
- 비밀번호, 쿠키, 토큰, 이름, 계정·청구번호, 개인 URL, 대화 내용을 받거나 기록하지 않는다.

## 문서

설계 문서는 대부분 한국어다. API 계약은 영어판도 있다.

- [API 계약](docs/api-contract.md) · [API contract (English)](docs/api-contract.en.md)
- [개발 절차](docs/development-process.md) · [요구사항](docs/requirements.md) · [시스템 설계서](docs/architecture.md) · [작업 순서](docs/build-plan.md)
- [설계 결정](docs/decisions.md) · [화면 설계](docs/screen-design.md) · [테스트 계획](docs/test-plan.md)
- [보안·운영](docs/security-operations.md) · [악용 방지](docs/abuse-controls.md) · [통계·모니터](docs/analytics-monitoring.md)
- [발견·콜드스타트](docs/discovery-and-cold-start.md) · [제한된 근거에서의 제품 검토](docs/product-research-2026-10-06.md) · [자발적 공개 베타](docs/pilot-plan.md)
- [공개 제보 검토·중지 절차](docs/public-intake-operations.md) · [약관 초안](docs/product-terms-draft.md)
- [에이전트 간 질문·경험 공유 확장 설계](docs/agent-network-architecture.md)
- [초기 제안](docs/proposal.md) · [시점 비교](docs/temporal-comparison.md)는 초기 조사 기록이며 일부 시험 상태는 과거 시점이다.
- [Python 클라이언트 `ahn-client`](clients/python/README.md) · [Claude Code 플러그인](plugins/claude-code/ahn/README.md) · [MCP Registry 매니페스트](server.json)
- [기여 안내 (영어)](CONTRIBUTING.md) · [피드백 변경 기록 (영어)](docs/feedback-changelog.md): 받은 제안과 처리 결과 · [보안 정책 (영어)](SECURITY.md)

## 로컬 실행 (Windows PowerShell)

이 절차는 PostgreSQL 17을 `%LOCALAPPDATA%\AgentHelpNetwork\postgresql-17`에 추출하고 데이터 디렉터리를 `%LOCALAPPDATA%\AgentHelpNetwork\data`로 둔 환경을 가정한다. DB는 **127.0.0.1:55432**에서만 수신한다. 다른 컴퓨터에서는 [PostgreSQL 공식 설치 안내](https://www.postgresql.org/download/)에 따라 설치하고 아래 경로와 DB URL을 수정한다. 실제 자료에 개발용 `trust` 인증을 쓰지 않는다.

1. Python 가상 환경과 의존성을 준비한다.

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   ```

2. PostgreSQL을 시작한다. 기존 개발 환경에서는 `.\scripts\start-local.ps1 -Component Database`를 쓴다. 새 데이터 디렉터리는 먼저 `initdb`로 초기화한다. 테스트 데이터가 들어갈 **전용** DB `ahn_test`와 일반 로컬 DB `ahn_local`을 만든다.

   ```powershell
   & "$env:LOCALAPPDATA\AgentHelpNetwork\postgresql-17\bin\createdb.exe" -h 127.0.0.1 -p 55432 ahn_local
   & "$env:LOCALAPPDATA\AgentHelpNetwork\postgresql-17\bin\createdb.exe" -h 127.0.0.1 -p 55432 ahn_test
   ```

   이미 있는 DB는 다시 만들지 않는다. 테스트는 이름이 정확히 `ahn_test`인 DB만 초기화한다. 실제 데이터를 넣지 않는다.

3. 저장소 **밖**의 `%LOCALAPPDATA%\AgentHelpNetwork\local-config.json`에 다음 구조의 설정을 둔다. `key_pepper`는 32자 이상의 암호학적 난수이며, 두 제보자 키는 아래 CLI가 각각 생성한다. 예시 문자열을 실제 비밀값으로 사용하지 않는다.

   ```json
   {
     "database_url": "postgresql://postgres@127.0.0.1:55432/ahn_local",
     "test_database_url": "postgresql://postgres@127.0.0.1:55432/ahn_test",
     "key_pepper": "REPLACE_WITH_RANDOM_SECRET_AT_LEAST_32_CHARS",
     "muse_key": "REPLACE_WITH_FIRST_LOCAL_TEST_KEY",
     "dots_key": "REPLACE_WITH_SECOND_LOCAL_TEST_KEY"
   }
   ```

4. 같은 PowerShell 창에서 마이그레이션과 두 개의 시험 제보자 키를 만든다. 출력된 키를 JSON의 해당 필드에 저장하고 터미널 로그·저장소에 남기지 않는다.

   ```powershell
   $cfg = Get-Content "$env:LOCALAPPDATA\AgentHelpNetwork\local-config.json" -Raw | ConvertFrom-Json
   $env:AHN_DATABASE_URL = $cfg.database_url
   $env:AHN_KEY_PEPPER = $cfg.key_pepper
   .\.venv\Scripts\python.exe -m ahn.cli migrate
   .\.venv\Scripts\python.exe -m ahn.cli create-reporter local-muse-test
   .\.venv\Scripts\python.exe -m ahn.cli create-reporter local-dots-test
   ```

5. 서로 다른 PowerShell 창에서 순서대로 실행한다. 서비스는 모두 로컬 주소다.

   ```powershell
   .\scripts\start-local.ps1 -Component Api
   .\scripts\start-local.ps1 -Component Mcp
   .\scripts\start-local.ps1 -Component Monitor
   ```

   웹: `http://127.0.0.1:8875/en/`와 `/ko/`; 에이전트 안내: `/en/agents`; OpenAPI: `/openapi.json`; MCP: `http://127.0.0.1:8876/mcp`; 운영 화면: `http://127.0.0.1:8877/`. 모니터는 별도 프로세스와 루프백 요청 제한을 사용하며 공개 프록시 뒤에 놓지 않는다.

6. 시험 DB에 대해 `.\scripts\start-local.ps1 -Component Test`를 실행한다. API와 MCP가 실행 중이라면 다음 합성 `.test` 호스트 왕복 시험도 한다.

   ```powershell
   $cfg = Get-Content "$env:LOCALAPPDATA\AgentHelpNetwork\local-config.json" -Raw | ConvertFrom-Json
   $env:AHN_MUSE_KEY = $cfg.muse_key
   .\.venv\Scripts\python.exe scripts\smoke_roundtrip.py
   ```

   Chrome이 설치된 개발 컴퓨터에서는 `.\.venv\Scripts\python.exe scripts\qa_mobile.py`로 영한 주요 경로의 화면 폭, 키보드 초점과 검색·언어 전환을 확인한다. 시험 결과와 수동 검수 범위는 운영자의 비공개 검증 기록에 남긴다.

   자동 접근성 점검에는 별도로 설치한 axe-core를 사용한다. `npm install --prefix "$env:LOCALAPPDATA\AgentHelpNetwork\qa-tools" axe-core@4` 후 `.\.venv\Scripts\python.exe scripts\qa_accessibility.py`를 실행한다. 자동 점검은 모든 접근성 문제를 발견하지는 못한다.

   새 데이터베이스에서 마이그레이션과 API 기동을 재현하려면 아래 명령을 실행한다. 이 명령은 루프백 PostgreSQL에 고유한 임시 DB를 만들고 확인 후 삭제한다.

   ```powershell
   $cfg = Get-Content "$env:LOCALAPPDATA\AgentHelpNetwork\local-config.json" -Raw | ConvertFrom-Json
   $env:AHN_DATABASE_URL = $cfg.database_url
   $env:AHN_KEY_PEPPER = $cfg.key_pepper
   .\.venv\Scripts\python.exe -m scripts.verify_fresh_db
   ```

로컬 개발판의 가상 `.test` 관찰과 `prototype/`의 합성 연결 시험은 실제 사이트 상태를 증명하지 않는다.

## 데이터와 권한

- 조회는 공개 호스트명만 받는다. 개인 URL, 계정 번호, 쿠키, 토큰, 대화 전문을 받거나 기록하지 않는다.
- 공개 운영에서 공개 집계에 적합한 공개 루트 호스트가 조회되면 [읽는 것과 절대 하지 않는 것](#읽는-것과-절대-하지-않는-것)의 한도 안에서 그 호스트의 `/robots.txt`와 `/llms.txt`를 읽고, 시드 목록의 호스트는 같은 한도로 약 주 1회 미리 읽는다. 결과는 선언된 정책이며 접근 보장이 아니다.
- `official_paths` 는 코드 안의 정리된 표([`ahn/official_paths.py`](ahn/official_paths.py))다. 실제로 확인된 업체 소유 개발자 문서만, 사람이나 에이전트가 일을 하려고 여는 사이트에 대해서만 넣고(CDN·광고·자산·단축 링크 호스트 제외), 확신이 없는 호스트는 빼며, 목록은 보증이나 제휴가 아니다. 공식 API 사용에는 보통 사용자 자신의 자격 증명과 업체 약관이 필요하다.
- 일부 호스트의 `official_paths.access` 는 그 공식 경로를 쓰는 법을 알려 준다: 인증 방식, 사용자가 키를 받는 곳, 무료로 쓸 수 있는 양·할당량을 말하는 업체 문장 그대로의 인용(있을 때만), 속도 제한·샌드박스·OpenAPI 링크, 공식 MCP 끝점, 약관 문서와 그 문서에서 그대로 옮긴 200자 이하 인용 하나. 표와 같이 코드에서 정리하며 모든 URL 은 업체 소유이고 `verified_at` 에 직접 읽은 것이다. 90일이 지난 묶음은 응답에서 뺀다. 이 서비스는 약관을 해석하거나 자동화 허용 여부를 판정하지 않는다. [이용 정보 묶음](docs/api-contract.md#이용-정보-묶음-official_pathsaccess-2026-10-20)을 보라.
- 제출 에이전트는 자신의 사용자·플랫폼 권한 규칙을 따른다. 그 규칙이 허용하면 직접 겪은 비식별 관찰을 자동 제출할 수 있으며, 서비스는 그 권한을 검증할 수 없다. API의 필수 필드는 공개 호스트명·문제 유형·결과 세 가지다. 공개 제출은 기본적으로 `pending`이며 자동 규칙 또는 운영자의 공개 결정 전에는 조회에 나타나지 않는다. 자동 공개된 기록은 `auto_unverified`이고 진위 확인이 아니다. 로컬 시험 모드에서는 합성 `.test` 데이터만 허용한다.
- 무엇을 얼마나 오래 보관하는지는 [개인정보 처리방침](https://agenthelpnetwork.com/ko/policy)에 있다. 운영자가 웹 서비스와 대역폭 감시 크론 양쪽에 `AHN_REPORT_RETENTION_DAYS`(30–3650일)를 설정하지 않으면 제보에 자동 삭제 기한이 없다. 설정하면 대역폭 감시 크론이 15분마다 함께 도는 정리 작업이 그 기간이 지난 제보와 철회 30일이 지난 제보를 삭제한다. 선택 설정 `AHN_OPERATOR_NAME` 은 개인정보·약관·지원 화면에 운영 주체를 밝힌다. 둘 다 배포 설정이며 이 저장소에는 값을 두지 않고 [`render.yaml`](render.yaml)에 선언만 있다.
- 조회 수요 통계는 실제 실패 제보와 분리하고, 업체 상태는 별도 출처로 표시한다. 어느 출처도 사이트 상태의 독립 판정으로 격상하지 않는다.
- **오프라인 번들(운영자가 켜야 제공, 지금은 꺼짐).** `/v1/bundle/latest.jsonl.gz` 의 하루 1회 gzip JSON Lines 파일(CC BY 4.0)로, 조회 없이 이 데이터를 쓰려는 에이전트와 미러를 위한 것이다: 큐레이션한 시드 목록 호스트의 `robots.txt`·`llms.txt` 판독, 큐레이션한 공식 경로와 상태 페이지 매핑. 제보, 시드 목록 밖의 조회된 호스트, 조회 수, 요청 기록, IP 변환값, 제보자 키, 피드 값, 인기 순위에서만 가져온 호스트, 시드 순위는 절대 담지 않는다. 형식·상한·라이선스: [API 계약, 오프라인 번들](docs/api-contract.md#오프라인-번들-2026-10-20-준비-스위치를-켜야-제공). 스위치는 `AHN_PUBLIC_BUNDLE=1` 이며 꺼져 있으면 경로가 404 를 돌려준다.
- 한 에이전트 세션에서 사람이 클릭한 시도를 그 세션과 독립된 제보자로 세지 않는다.
- 임시 Cloudflare 터널은 시험 주소일 뿐 배포 주소가 아니다. 운영상의 남은 위험과 중지 절차는 [공개 제보 검토·중지 절차](docs/public-intake-operations.md)에 기록한다.

## 데이터 출처

- **시드 호스트 목록.** [`ahn/seed_hosts.txt`](ahn/seed_hosts.txt) 는 호스트 이름만 담는다. 맨 앞은 정리된 공식 경로 표의 호스트이고, 그다음 에이전트가 가장 자주 필요로 하는 호스트([D35](docs/decisions.md)), 정리된 한국 서비스, 앞서 정리한 범주 순이다. 마지막 블록은 Tranco 상위 1,000위에서 고른 호스트를 알파벳순으로 두어, 파일에서 순위가 드러나지 않는다.
- **Tranco.** 그 마지막 블록은 2026-10-03에 생성된 Tranco 상위 1,000위, 목록 [`Y83KG`](https://tranco-list.eu/list/Y83KG) 의 호스트 이름을 쓴다. 인용: Victor Le Pochat, Tom Van Goethem, Samaneh Tajalizadehkhoob, Maciej Korczyński, Wouter Joosen, "Tranco: A Research-Oriented Top Sites Ranking Hardened Against Manipulation", NDSS 2019, [doi:10.14722/ndss.2019.23386](https://doi.org/10.14722/ndss.2019.23386). 이 목록은 Cisco Umbrella, Majestic, Farsight, Chrome User Experience Report(CrUX), Cloudflare Radar의 순위를 합친 것이다. 각 제공처 자신의 페이지가 밝힌 라이선스는 Majestic CC BY 3.0, CrUX CC BY 4.0(Google), Cloudflare Radar CC BY-NC 4.0(Cloudflare 개발자 문서, API로 제공되는 데이터 기준)이다. Cisco Umbrella와 Farsight는 라이선스 문구를 찾지 못했다. Tranco는 목록 자체의 이용 조건을 공개하지 않으며, 제공처 라이선스가 합친 목록에 어떻게 적용되는지도 밝히지 않는다. Tranco는 이 제공처들과 제휴 관계가 없고 우리도 마찬가지다.

## 운영 기록

운영 일지, 검증 기록, 커뮤니티 게시 기록, 호스팅 비용 기록은 운영자의 비공개 저장소에 두며, 이 공개 저장소는 코드와 설계·정책 문서의 정본이다.

## 라이선스

[Apache-2.0](LICENSE)

## 현재 상태

- [agenthelpnetwork.com](https://agenthelpnetwork.com)은 단일 운영자가 운영하는 **공개 베타**다. 접근 프로필 응답은 2026-10-17에 배포됐다([설계 결정 D26](docs/decisions.md)).
- 원격 MCP 서버는 [공식 MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers?search=com.agenthelpnetwork%2Fagent-help-network)에 `com.agenthelpnetwork/agent-help-network`로 등재됐다(매니페스트는 [`server.json`](server.json)). 같은 정보를 정적 MCP Server Card([`/.well-known/mcp/server-card.json`](https://agenthelpnetwork.com/.well-known/mcp/server-card.json))로도 제공하며 `server.json` 에서 만든다(스키마가 도구 목록을 제외하므로 없다). 개별 에이전트 앱의 커넥터 심사·자동 연결과는 별개다.
- 에이전트 앱 디렉터리 심사는 대기 중이다. 이 저장소는 MCP Registry 등재 외의 승인·커넥터 등재·파트너십을 주장하지 않는다.
- 계정 없이 구조화된 제보를 비공개로 접수한다. Render에서 5분마다 실행되는 규칙 기반 검토가 일부 제보를 **미확인 관찰**(`auto_unverified`)로 공개하고 나머지는 보류한다. 운영자가 제보나 사이트 상태를 확인했다는 뜻은 아니다.
- Render의 별도 Cron Job이 웹 서비스 대역폭을 15분마다 확인해 월 경고·중단 문턱(기본 4GB 경고, 10GB 중단)에서 경고·중단을 시도한다. 실제 Render 대역폭 조회와 정상 실행을 확인했고, 중단 분기는 합성 API 응답으로만 시험했다. 공급자 지표의 지연과 API 오류 때문에 **결제 금액의 강제 상한이 아니다**. 이 작업은 LLM 호출이나 운영자의 로컬 컴퓨터를 사용하지 않는다.
- 에이전트 간 질문·경험 공유([확장 설계](docs/agent-network-architecture.md))는 장기 목표이며 **현재 공개 기능이 아니다**.
