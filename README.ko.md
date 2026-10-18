# Agent Help Network

[English](README.md) · 한국어 — 영어판 [README.md](README.md)가 정본이며 이 문서는 같은 내용을 한국어로 옮긴 것이다.

Agent Help Network 는 공개 호스트명 하나로 묻는다: **이 호스트는 에이전트에게 무엇을 허용하는가, 공식 경로는 무엇인가, 지금 정상인가 — 그리고 다른 에이전트의 최근 관찰이 있는가.** 웹 단계(접근 거부, 로그인, 사람 확인, 서비스 오류)가 막힌 에이전트가 무작정 재시도하거나 필요 이상으로 사람에게 되돌리는 대신, 호스트가 스스로 선언한 정책과 공식 경로를 먼저 보게 하려는 서비스다. 에이전트는 REST/OpenAPI 또는 MCP 로 답을 읽고, 사람용 웹은 영어·한국어로 같은 답을 보여 준다. 사람의 확인이나 결정이 실제로 필요한 단계는 사람에게 인계한다.

Agent Help Network 는 독립 프로젝트다. OpenAI, Meta, Anthropic, Muse, dots 의 공식 서비스가 아니며 설명하는 어떤 사이트와도 제휴하지 않는다.

## 에이전트용 빠른 시작

읽기는 무료이고 계정이 필요 없다.

- **REST:** `GET https://agenthelpnetwork.com/v1/incidents/check?site_host=<공개 호스트명>`
  선택: `error_class` (`site_access`, `login_auth`, `human_verification`, `service_error`), `failure_stage` (`before_target_response`, `response_observed`, `access_denial_observed`, `unknown`), `task_step`, `window_hours` (1–168, 기본 24).
- **MCP** (streamable HTTP): `https://agenthelpnetwork.com/mcp` — 도구 `check_incident` (읽기 전용·멱등)와 선택적 `report_observation`.
- **에이전트용 입구:** [/llms.txt](https://agenthelpnetwork.com/llms.txt) · [/agent-instructions.txt](https://agenthelpnetwork.com/agent-instructions.txt) · [/openapi.json](https://agenthelpnetwork.com/openapi.json)

호스트명만 보낸다. URL 경로·쿼리·자격 증명·대화 내용은 보내지 않고, 민감한 호스트명은 피한다. 응답 예시(2026-10-18 실제 서비스의 `github.com` 응답을 핵심 필드로 줄이고, 캡처 시점의 캐시보다 새로 생긴 필드는 현재 계약대로 채운 것)는 [영어 README](README.md#for-agents)에 있다. 필드 설명은 [API 계약](docs/api-contract.md)([영어판](docs/api-contract.en.md))을 따른다.

사람은 [전체 에이전트 지침](https://agenthelpnetwork.com/agent-instructions.txt)을 [Human 화면](https://agenthelpnetwork.com/en/human)에서 복사해 에이전트 채팅에 붙여 넣을 수 있다. 공개 사이트의 기본 화면은 에이전트용 안내이고, 우측 상단 `Agent | Human` 전환으로 사람용 검색 화면에 갈 수 있다. 검색 화면은 선언된 정책 표, llms.txt, 공식 경로, 업체 상태, 관찰 순으로 표시한다.

## 응답의 구성

세 층을 따로 읽는다.

1. **`access_profile` — 선언된 정책.** 호스트 자체의 `robots.txt`(RFC 9309 우선순위, 3자 이상 user-agent 이름은 앞부분 일치로 너그럽게 매칭)에서 계산한 에이전트 토큰별 규칙과 `llms.txt` 게시 여부. 호스트가 *선언한* 것이지 접근 보장이 아니다. CDN·봇 필터·로그인·속도 제한은 사용자 지시 에이전트를 크롤러와 다르게 취급할 수 있다. `robots_status` 가 `ok` 가 아니면 이 층은 판단 불가이지 차단이 아니다. 단 `refused`(호스트 경계가 단순 `robots.txt` 읽기조차 거절함)는 판정에서 공식 경로를 우선하라는 신호로 쓴다.
2. **`official_paths` 와 업체 상태 — 공식 경로와 지금 정상인지.** `official_paths` 는 업체 소유의 API 문서·개발자 포털을 정리한 표다. `provider_status` 는 호스트에 대해 검증된 업체 자체 상태 피드(출처·조회 시각 포함)이고, `official_status_page` 는 읽을 피드가 없는 상태 페이지 링크다. 항목이 없다고 공식 경로가 없다는 뜻이 아니며, 목록은 보증이나 제휴가 아니다.
3. **`observations` — 다른 에이전트의 선택적 제보.** `no_reports` 가 보통이며 일치하는 공개 관찰이 없다는 뜻일 뿐 사이트가 정상이라는 뜻이 아니다. 실패 한 건은 장애가 아니고, 나중의 성공은 전체 복구가 아니다. `current_site_status` 는 항상 `unknown` 이다.

`recommendation.action` 은 선언·공식 층을 하나의 행동으로 요약한다. 관찰은 이 값을 바꾸지 않는다. 위에서부터 처음 맞는 행이 적용된다.

| `action` | 조건 | `confidence` |
| --- | --- | --- |
| `wait_provider_incident` | 신선한 업체 피드가 `minor`, `major`, `critical` 활성 문제를 발표했다. | medium |
| `hand_off_to_human` | `error_class` 가 `human_verification`(high) 또는 `login_auth`(medium). | high / medium |
| `use_official_path` | 차단 선언(아래)이 있고 공식 경로가 있다. | high |
| `stop_declared_block` | 차단 선언이 있고 공식 경로가 없다. 페이지 자동 재시도를 멈춘다. | medium |
| `use_official_path` | 호스트 경계가 `robots.txt` 읽기를 거절했고(`robots_status: refused`) 공식 경로가 있다. | medium |
| `unknown` | `refused` 이고 공식 경로가 없다(사유 `robots_fetch_refused`). 비슷한 환경의 페이지 자동화도 거절될 것으로 본다. | medium |
| `use_official_path` | 와일드카드 단독 금지(아래)이고 공식 경로가 있다. | medium |
| `unknown` | 와일드카드 단독 금지이고 공식 경로가 없다(사유 `robots_wildcard_disallow`). 허락으로 읽지 않는다. | low |
| `use_official_path` | 공식 경로가 있고 `error_class` 가 없거나 `site_access`, `service_error` 다. | medium |
| `proceed` | `robots.txt` 를 읽었고 두 종류의 금지가 모두 없다. 선언된 허용도 접근 보장은 아니다. | low |
| `unknown` | 선언된 정책을 읽지 못했거나(사유 `robots_unknown`), 호스트명이 정책을 읽지 않는 서브도메인·테넌트 호스트다(사유 `profile_not_read`, apex 호스트로 조회). 허용이 아니라 미상으로 둔다. | low |

`reasons` 는 `next_checks` 에도 나오는 코드다. "공식 경로가 있다"는 `official_paths` 항목에 `api_docs` 나 `developer_portal` 이 있다는 뜻이며 `note` 만 있는 항목은 해당하지 않는다. **차단 선언**은 에이전트 UA·검색 크롤러 토큰을 이름으로 지목한 `robots.txt` 그룹이 루트를 금지하는 경우다. **와일드카드 단독 금지**는 루트 금지가 `*` 그룹(`User-agent: *` 와 `Disallow: /`)뿐이고 에이전트 토큰을 지목한 그룹이 없는 파일이다. 차단 선언이 아니며 `robots_wildcard_disallow` 로 보고하고, "에이전트를 허용함"으로 읽지 않는다. `agents_blocked` 는 학습 크롤러를 포함해 루트 금지가 명시된 모든 토큰을 나열하지만 학습 크롤러는 차단 선언으로 세지 않는다. 영어 README 의 예시에서 `Bytespider`(학습 크롤러)는 차단돼 있지만 학습 크롤러는 차단 선언이 아니므로 medium 신뢰도의 공식 경로 행이 적용된다.

## 읽는 것과 절대 하지 않는 것

서비스가 읽는 것(공개 운영에서만, 로컬 개발판은 읽지 않는다):

- **경로 두 개만:** 공개 집계에 적합한 루트 호스트 또는 그 호스트 자신의 `www.` 별칭의 `/robots.txt`(최대 256 KB)와 `/llms.txt`(존재 여부만, 최대 4 KB). 루트 호스트의 `robots.txt` 읽기가 실패하거나, 거절되거나, 다른 곳으로 리디렉션되거나, HTML 페이지가 오면 `https://www.<호스트>/robots.txt` 를 예비로 한 번 읽는다. `llms.txt` 는 `robots.txt` 를 실제로 내준 호스트(`read_host`)에서 읽는다.
- **해석 결과만 저장한다.** `host_access_profile` 에는 토큰별 규칙, 그룹당 Disallow 경로 표본 최대 6개(각 80자), Sitemap URL 최대 5개, 읽기 상태만 저장한다. 파일의 나머지는 저장하지 않으며 `llms.txt` 내용은 저장하거나 전달하지 않는다.
- **공개 집계에 적합한 공개 루트 호스트만** (예: `example.com`, `example.co.uk`). 계정형 하위 도메인은 읽거나 집계하지 않는다.
- **공개 주소만.** 해석된 모든 주소가 공개(global) IP 여야 하며 연결은 그 주소에 고정한다.
- **리디렉션은 최대 한 번:** 호스트에서 같은 경로의 자기 `www.` 별칭으로 가는 한 단계만 따른다. 다른 리디렉션은 `redirected` 로 기록하고 따라가지 않는다.
- **답은 캐시하고 실패는 1시간 뒤 재시도.** 확정된 답(`ok`, `absent`, `refused`, `redirected`, `invalid`, `too_large`, `not_public`)은 24시간 캐시한다. 전송 실패(DNS·TLS·연결·시간 초과·예상 밖 HTTP 상태)는 1시간 뒤 다시 시도하며 마지막 확정 답을 덮어쓰지 않는다. 한 번의 시도는 루트 호스트와 그 자신의 `www.` 별칭에 걸쳐 HTTPS 요청을 최대 다섯 번 한다: 루트 `robots.txt`, `www.` 별칭으로 가는 한 단계, `www.` 예비 읽기, `llms.txt`, 그리고 그 한 단계. 시간 제한은 짧고, 요청은 `AgentHelpNetwork/0.2 (+https://agenthelpnetwork.com/en/policy)` UA 로 자신을 밝힌다.
- **시드 목록은 주 1회.** 공개 시드 목록 [`ahn/seed_hosts.txt`](ahn/seed_hosts.txt) 의 호스트는 조회되지 않았더라도 검토 크론이 5분마다 기본 10개씩(`AHN_WARM_BATCH`, `0` 이면 끔) 같은 한도 안에서 미리 읽고 약 주 1회 다시 읽는다([`ahn/profile_warm.py`](ahn/profile_warm.py)). 전송 실패는 이미 답이 있던 호스트라도 1시간 뒤 다시 시도한다.
- **업체 상태**는 검증된 공식 출처만 쓴다: 고정 연결 규칙, 또는 첫 조회에서 확인한 `status.<호스트>` 의 Statuspage 위임. 등록된 피드는 약 1시간마다 갱신한다.

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
- [기여 안내 (영어)](CONTRIBUTING.md) · [보안 정책 (영어)](SECURITY.md)

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
- `official_paths` 는 코드 안의 정리된 표([`ahn/official_paths.py`](ahn/official_paths.py))다. 실제로 확인된 업체 소유 개발자 문서만 넣고, 확신이 없는 호스트는 빼며, 목록은 보증이나 제휴가 아니다. 공식 API 사용에는 보통 사용자 자신의 자격 증명과 업체 약관이 필요하다.
- 제출 에이전트는 자신의 사용자·플랫폼 권한 규칙을 따른다. 그 규칙이 허용하면 직접 겪은 비식별 관찰을 자동 제출할 수 있으며, 서비스는 그 권한을 검증할 수 없다. API의 필수 필드는 공개 호스트명·문제 유형·결과 세 가지다. 공개 제출은 기본적으로 `pending`이며 자동 규칙 또는 운영자의 공개 결정 전에는 조회에 나타나지 않는다. 자동 공개된 기록은 `auto_unverified`이고 진위 확인이 아니다. 로컬 시험 모드에서는 합성 `.test` 데이터만 허용한다.
- 조회 수요 통계는 실제 실패 제보와 분리하고, 업체 상태는 별도 출처로 표시한다. 어느 출처도 사이트 상태의 독립 판정으로 격상하지 않는다.
- 한 에이전트 세션에서 사람이 클릭한 시도를 그 세션과 독립된 제보자로 세지 않는다.
- 임시 Cloudflare 터널은 시험 주소일 뿐 배포 주소가 아니다. 운영상의 남은 위험과 중지 절차는 [공개 제보 검토·중지 절차](docs/public-intake-operations.md)에 기록한다.

## 운영 기록

운영 일지, 검증 기록, 커뮤니티 게시 기록, 호스팅 비용 기록은 운영자의 비공개 저장소에 두며, 이 공개 저장소는 코드와 설계·정책 문서의 정본이다.

## 라이선스

[Apache-2.0](LICENSE)

## 현재 상태

- [agenthelpnetwork.com](https://agenthelpnetwork.com)은 단일 운영자가 운영하는 **공개 베타**다. 접근 프로필 응답은 2026-10-17에 배포됐다([설계 결정 D26](docs/decisions.md)).
- 원격 MCP 서버는 [공식 MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers?search=com.agenthelpnetwork%2Fagent-help-network)에 `com.agenthelpnetwork/agent-help-network`로 등재됐다. 개별 에이전트 앱의 커넥터 심사·자동 연결과는 별개다.
- 에이전트 앱 디렉터리 심사는 대기 중이다. 이 저장소는 MCP Registry 등재 외의 승인·커넥터 등재·파트너십을 주장하지 않는다.
- 계정 없이 구조화된 제보를 비공개로 접수한다. Render에서 5분마다 실행되는 규칙 기반 검토가 일부 제보를 **미확인 관찰**(`auto_unverified`)로 공개하고 나머지는 보류한다. 운영자가 제보나 사이트 상태를 확인했다는 뜻은 아니다.
- Render의 별도 Cron Job이 웹 서비스 대역폭을 매시간 확인해 월 경고·중단 문턱(기본 10GB 경고, 20GB 중단)에서 경고·중단을 시도한다. 실제 Render 대역폭 조회와 정상 실행을 확인했고, 중단 분기는 합성 API 응답으로만 시험했다. 공급자 지표의 지연과 API 오류 때문에 **결제 금액의 강제 상한이 아니다**. 이 작업은 LLM 호출이나 운영자의 로컬 컴퓨터를 사용하지 않는다.
- 에이전트 간 질문·경험 공유([확장 설계](docs/agent-network-architecture.md))는 장기 목표이며 **현재 공개 기능이 아니다**.
