# API 및 도구 계약 v1

[English](api-contract.en.md) · 한국어

작성일: 2026-10-03. 로컬 MVP의 계약이며 [설계서](architecture.md)의 API 표를 구체화한다. 필드명과 오류 코드는 언어와 무관하다. 값에 자유 서술을 넣지 않는다. 아래 키 기반 합성 예시는 로컬 시험용이다. 현재 공개 제보의 차이는 마지막 **공개 접수 계약**을 따른다.

## 공통 규칙

- JSON 요청의 최상위 및 하위 객체에서 정의되지 않은 필드는 `400 invalid_request`로 거부한다.
- `site_host`는 소문자 공개 DNS 호스트명이다. URL, 경로, 쿼리, 포트, IP 주소, 로컬 호스트는 거부한다.
- 시각 입력은 시간대가 있는 ISO 8601이며, `generated_at`은 UTC다. 관찰 시각은 ISO 8601 시간대 오프셋을 유지할 수 있다. 조회 기본 범위는 최근 24시간이고 `window_hours`는 1~168이다. 저장 데이터 탐색 범위는 최근 7일(`data_horizon_hours=168`)로 별도 표시한다. 이 문턱은 로컬 시험값이다.
- `task_step`은 `[a-z][a-z0-9_]{0,63}` 형식의 일반 단계 코드다. 개인별 식별자를 담지 않는다.
- 사건 목록은 최근 순 `limit` 최대 50건으로 제한한다. 로컬 MVP에는 커서 페이지네이션이 아직 없다. 조회 응답의 관찰 목록은 최대 20개다.
- API 요청·응답에는 비밀번호, 쿠키, 토큰, 이름, 이메일, 계정/청구번호, 개인 URL, 대화 내용이 없다.
- 오류는 `{ "error": "code" }` 형식이며 입력값을 다시 출력하지 않는다. 인증 실패는 `401`, 다른 제보자의 관찰 수정/철회는 `404`로 처리해 소유 여부를 노출하지 않는다. DB 연결 오류는 API에서 `503 {"error":"service_unavailable"}`와 `Retry-After: 60`을 반환한다. 웹은 같은 상태 코드로 영한 재시도 안내를 보여준다.

## `GET /v1/incidents/check`

필수 `site_host`; 선택 `error_class`, `task_step`, `window_hours`, `failure_stage`. MCP `check_incident` 는 `window_hours` 를 받지 않고(항상 기본 24시간) 나머지 입력과 응답 구조는 같다. 공개 운영 모드에서는 승인된 공개 관찰만 사용한다. 로컬 시험 모드에서는 합성 관찰을 별도로 읽을 수 있으며 반드시 `synthetic: true`를 반환한다. 두 모드를 한 응답에 혼합하지 않는다. 아래 예시는 관찰 층까지만 보인 것이며, 실제 응답은 영어 계약의 전체 예시와 같은 최상위 키(`provider_status`, `provider_source_state`, `provider_discovery_attempted_at`, `official_status_page`, `lookup_activity`, `access_profile`, `official_paths`, `recommendation`, `failure_stage`, `next_checks`)를 모두 가진다. 로컬 시험 모드에서는 공개 전용 층이 `null` 이다.

**`www.` 와 루트 호스트.** `site_host` 가 공개 집계에 적합한 공개 루트 호스트 앞에 `www.` 라벨 하나만 붙은 이름(`www.nytimes.com`, `www.bbc.co.uk`)이면, 호스트 단위 층은 루트 호스트를 조회한 것과 똑같이 루트 호스트 기준으로 답한다: `provider_status`, `provider_source_state`, `provider_discovery_attempted_at`, `official_status_page`, `lookup_activity`, `access_profile`, `official_paths`, 그리고 이들로 정하는 `recommendation` 과 `next_checks`. 등록·집계·읽기 대상은 루트 호스트이며 `www.` 이름으로는 아무것도 저장하지 않는다. 관찰 층은 합치지 않는다: `observations`, `evidence_level`, 각 집계 수, 사건과 사건 페이지는 정확한 `site_host` 를 키로 유지하고, 응답의 `site_host` 는 조회한 호스트 그대로다. 제보는 한 호스트에서 직접 겪은 사건이고, `www.` 앞단은 루트와 다른 경계(edge) 뒤에 있을 수 있으며, 저장된 사건의 키를 바꾸면 공개된 사건 페이지가 바뀌므로, 사이트 전체를 설명하는 선언·공식 층만 공유한다. 공개 제보의 자동 검토도 `www.<루트>` 제보를 루트 호스트처럼 공개 대상으로 보고, 15분 집중·24시간 공개 창에서는 두 이름을 한 호스트로 센다. 제보는 정확한 호스트로 저장·공개된다([운영 절차](public-intake-operations.md)). 앞의 `www.` 라벨 하나만, 그리고 그 호스트 자체가 적합한 루트 호스트가 아니고 남은 이름이 적합한 루트 호스트일 때만 뗀다: `www.co.uk`(`co.uk` 아래의 루트 호스트), `www.user123.example.org`, `www2.example.com`, `www.www.example.com` 등 다른 모든 서브도메인은 그대로다. `valid_host` 규칙은 바뀌지 않으며 `www.` 이름도 이전처럼 받아 답한다.

```json
{
  "site_host": "example.test",
  "generated_at": "2026-10-03T04:00:00Z",
  "synthetic": true,
  "window_hours": 24,
  "data_horizon_hours": 168,
  "current_site_status": "unknown",
  "evidence_level": "no_reports",
  "summary_code": "NO_MATCHING_REPORTS_STATUS_UNKNOWN",
  "summary": "No matching reports in the 7-day data horizon. The site's current status is unknown.",
  "failures": 0,
  "successes": 0,
  "reporting_sources": 0,
  "observations": []
}
```

`evidence_level`은 `no_reports`, `single_observation`, `multiple_observations`, `later_success_observed`, `stale_reports` 중 하나다. `current_site_status`는 이 관찰 API에서 항상 `unknown`이며 서비스 전체 상태 판정이 아니다. `reporting_sources`는 키로 구별되는 출처 수일 뿐 독립된 사람 수가 아니다. 성공과 실패가 모두 있으면 둘 다 보여준다. `no_reports`는 최근 7일 탐색 범위의 일치 기록이 없다는 뜻으로, 정상 또는 전체 기록 부재를 의미하지 않는다.

`observations`의 원소는 `observed_at`, `attempt_actor`, `attempt_env`, `outcome`, `error_class`, `task_step`, `synthetic`만 공개한다. 공개 모드에서는 재식별 위험을 줄이기 위해 `observed_at` 을 정시 단위로 내려서(분·초 절사) 공개한다. 내부 UUID 및 제보자 ID는 공개하지 않는다.

## `POST /v1/observations`

로컬 합성 시험에서는 `X-API-Key`가 필수다. 제출 에이전트는 자신의 사용자·플랫폼 권한 규칙을 따른다. 아래 본문 예시는 전부 가상 데이터다.

```json
{
  "schema_version": 1,
  "site_host": "example.test",
  "task_step": "login_verification",
  "error_class": "human_verification",
  "observed_at": "2026-10-03T03:55:00Z",
  "agent_family": "muse",
  "attempt_actor": "human",
  "attempt_env": "human_in_agent_browser",
  "outcome": "blocked",
  "challenge_vendor": "unknown",
  "challenge_kind": "loop",
  "synthetic": true,
  "idempotency_key": "0a915e22-0a8d-4be0-84a3-4061ade4adab",
  "consent_confirmed": true
}
```

성공하면 `201`과 `{ "observation_id": "uuid", "incident_id": "uuid", "review_state": "pending", "synthetic": true }`를 반환한다. 동일 제보자의 같은 `idempotency_key`를 재전송하면 기존 ID로 `200`을 반환한다. 같은 키에 다른 본문을 사용하면 `409 idempotency_conflict`다. 서버는 제출 키로 제보자를 정하며, 본문에 `reporter_id`가 있으면 거부한다. 기존 클라이언트의 `consent_confirmed`는 선택적으로 받아들이지만 서버는 권한의 증거로 해석하거나 승인 여부를 결정하는 데 쓰지 않는다. 로컬 시험 모드는 `.test` 호스트의 `synthetic: true` 제출만 허용한다.

## `POST /v1/observations/{id}/outcomes`

동일 제보자의 키와 새 제출 승인, 새 멱등 키가 필요하다. 최초 사건의 `site_host`, `task_step`, `error_class`를 재사용하고 본문은 `schema_version`, `observed_at`, `attempt_actor`, `attempt_env`, `outcome`, `challenge_vendor`, `challenge_kind`, `agent_family`, `synthetic`, `idempotency_key`, `consent_confirmed`만 받는다. 새 관찰 행은 `supersedes_id`로 이전 행을 참조한다. 같은 사건의 오래된 실패와 새 성공이 모두 기록된다. 다른 제보자의 ID는 접근할 수 없다.

## `DELETE /v1/observations/{id}`

동일 제보자의 키가 필요하다. 철회는 조회에서 제외하고 최소 감사 이벤트만 남긴다. 다시 요청해도 성공 상태를 반환하는 멱등 동작으로 한다. 실제 저장물 삭제·보존 기간은 공개 운영 정책에서 확정한다.

## 사건 목록·상세

`GET /v1/incidents?site_host=&error_class=&limit=`는 최근 7일 공개 승인 사건만 최대 50건 보여준다. `GET /v1/incidents/{id}`는 한 사건의 공개 요약과 제한된 관찰 목록을 반환한다. 미승인·숨김·합성 사건은 공개 목록/상세에서 `404`로 취급한다. 로컬 시험용 조회 경로와 공개 웹 경로를 분리한다.

## OpenAPI 및 MCP

`/openapi.json`은 실행 모드의 REST 계약을 기술한다. 로컬 합성 시험에서는 `X-API-Key` 보안 스키마가 쓰기에 적용된다. 공개 베타의 제보 POST는 키가 필요 없으며, MCP에는 `check_incident`와 `report_observation`만 나타난다. `check_incident`는 제보를 만들지 않는 조회지만 호스트 등록·집계라는 서버 부수효과가 있다. MCP 주석은 UI 힌트일 뿐이며 에이전트가 어떤 권한으로 제출하는지 서버가 검증할 수는 없다. MCP에는 비밀값·원문 대화·대상 사이트 페이지 내용을 전달하지 않는다.

## 공개 접수 계약

- `POST /v1/observations`: 계정과 API 키가 필요 없다. **필수 필드는 `site_host`, `error_class`, `outcome` 세 개**다. `error_class`는 `site_access`, `login_auth`, `human_verification`, `service_error` 중 하나이고 `outcome`은 `passed`, `blocked`, `error`, `abandoned` 중 하나다. 생략된 `observed_at`은 서버 수신 시각, `task_step`은 `other`, `attempt_actor`는 `agent`, `attempt_env`는 `unknown`, `agent_family`는 `other`, `synthetic`은 `false`로 채운다. 실제 시각·단계·환경을 알면 명시해 정확도를 높인다. `task_step` 허용값은 `site_open`, `login`, `human_verification`, `service_use`, `other`다. 본문은 4096바이트 이하 JSON이며 정의되지 않은 필드는 거부한다. 실제 제보를 예시나 시험으로 보내지 않는다.
- **자동 공유:** 에이전트 자신의 사용자·플랫폼 권한 규칙이 허용하면 비식별화한 직접 관찰을 추가 건별 승인 없이 보낼 수 있다. 이 API 문서는 권한을 부여하지 않으며, 다른 에이전트에게 특정 승인 절차를 강제하지 않는다. 공개 호스트명에도 개인·조직 식별자가 들어갈 수 있으므로 민감한 호스트는 보내지 않는다. `consent_confirmed`는 과거 클라이언트 호환을 위해 선택적으로 수용하지만 권한 증빙이나 승인 게이트로 사용하지 않는다.
- `idempotency_key`를 생략하면 서버가 새 UUID를 만든다. **동일 제출 재시도 시 중복을 피하려면 클라이언트가 UUID를 생성해 유지하고 같은 본문과 함께 재사용**해야 한다. 키 없는 요청을 다시 보내면 별개 제보가 될 수 있다.
- 응답 `201` 또는 동일한 멱등 요청의 `200`에는 `review_state: pending`, `observation_id`, `incident_id`, `synthetic: false`, `withdrawal_token`이 포함된다. 이 토큰을 다른 사람에게 공개하거나 URL에 넣지 않는다. 새 제보는 비공개로 시작한다. 별도 Render Cron Job이 5분마다 10분 이상 지난 제보를 규칙으로 심사하고, 조건에 맞는 단일 구조화 관찰만 `review_reason: auto_unverified`로 공개한다. 이는 실제 사이트 상태나 제보의 진위를 승인하는 절차가 아니다. 다른 제보는 보류되며 운영자는 숨김·수동 공개를 결정할 수 있다. [운영 절차](public-intake-operations.md)
- `DELETE /v1/observations/{id}`: `X-Withdrawal-Token` 헤더를 사용한다. 일치하지 않는 ID·토큰은 `404`, 철회된 관찰은 공개 조회에서 제외된다. 사람용 `/en/report`, `/ko/report`에도 철회 양식이 있다.
- 공개 운영에서는 `POST /v1/observations/{id}/outcomes`가 비활성화되고 OpenAPI/MCP에도 나타나지 않는다. 새 결과는 새 제출로 처리하며 제출 에이전트 자신의 권한 규칙을 따른다.
- 익명 접수는 모두 하나의 미확인 출처로 집계한다. 호스트당 하루 5건(`www.<루트>`는 루트 호스트와 합쳐 센다), 전체 하루 100건, 앱 인스턴스에서 클라이언트당 분당 3건·전체 분당 20건을 초과하면 `429`다. 운영자는 DB 플래그로 접수를 일시 중지할 수 있으며 그때는 `503 reporting_paused`다.

## 조회 응답의 세 출처

`GET /v1/incidents/check`의 기존 `observations`와 `current_site_status: unknown`은 유지한다. 공개 루트 호스트는 첫 조회 시 내부 목록에 등록한다(`www.<루트>` 조회는 루트 호스트를 등록한다). `provider_status`는 **확인된 공식 상태 출처**가 있을 때만 값이 있으며, `source_name`, `source_url`, `indicator`, `source_updated_at`, `fetched_at`, `freshness`, `error_detail`을 담는다. 고정 연결 규칙이 없더라도 `status.<호스트>`가 Statuspage에 위임된 경우 첫 조회에서 안전하게 공식 API를 발견해 같은 응답에 싣는다. 15초 이내 중복 요청은 같은 결과를 쓴다. 이후 서버 작업이 등록된 공식 출처를 1시간 간격으로 갱신한다. 실패한 최신 조회는 이전 값을 `stale`로 표시하고 실패 사유를 `error_detail`에 짧은 라벨로 남긴다(`http_NNN`은 피드의 HTTP 상태이며 리디렉션은 따라가지 않으므로 `http_301` 같은 값으로 나타난다. 그 밖에 `timeout`, `dns_failed`, `connect_error`, `tls_error`, `read_error`, `too_large`, `invalid_body`, 발견된 Statuspage 출처의 `no_cname`·`id_mismatch`·`not_public`, 예비값 `error`). 이 라벨은 상태 피드 읽기에 대한 것이지 사용자의 대상 사이트에 대한 것이 아니며, 마지막 읽기가 성공이면 `null`이다. 다음 주기 작업에서 다시 시도한다. `indicator`는 `none`, `minor`, `major`, `critical`, `maintenance`, `unknown`, `null` 중 하나이며, `maintenance`는 업체 상태 페이지가 예정된 점검 진행 중이라고 발표했다는 뜻이다(Statuspage 형식의 `maintenance`, Instatus의 `UNDERMAINTENANCE`, Better Stack의 `maintenance`). 장애가 아니라 업체가 알린 작업이므로 `recommendation`과 `next_checks`는 `minor`와 같게 다루며(`wait_provider_incident`, `provider_scope`) `provider_scope` 문구만 예정된 점검임을 밝힌다. 다른 값과 마찬가지로 페이지 전체 상태이며 사용자의 대상 사이트에 대한 값이 아니다. `indicator: none`은 업체 상태 API에 활성 문제가 표시되지 않았다는 뜻이며 개별 에이전트의 로그인·접속 성공을 보증하지 않는다. 다른 출처와 충돌해도 결과를 덮어쓰지 않는다.

`provider_source_state`는 `feed_connected`, `official_page_only`, `not_verified_by_current_methods` 중 하나다. `official_page_only`일 때 `official_status_page`는 출처명과 URL만 제공하며 현재 상태를 읽은 값이 아니다. 페이지 전용 연결(`ahn/status_refresh.py` 의 `OFFICIAL_STATUS_PAGES`)은 현재 23개 호스트(예: Netflix, Webex, Adobe, Microsoft 365)이며, 그 호스트에 연결된 피드가 없을 때만 쓴다. `not_verified_by_current_methods`는 업체가 API를 제공하지 않는다는 결론이 아니라 현 자동 발견 범위에서 출처를 검증하지 못했다는 뜻이다. `provider_discovery_attempted_at`은 해당 호스트의 마지막 자동 발견 시도 시각이며 `null`이면 기록이 없다. 자동 발견은 `status.<호스트>`의 검증된 Statuspage 위임만 확인하고, 발견되지 않은 다른 공식 API의 존재 여부는 `unknown`으로 남긴다. 연결된 피드의 실제 조회 실패는 `provider_status.freshness`가 `pending` 또는 `stale`로 나타난다.

`lookup_activity`는 공개 집계에 적합한 루트 호스트라면 첫 조회부터 나타난다. 그 `www.` 이름의 조회는 루트 호스트 집계에 더해지고 루트 호스트의 구간을 보여 준다. 값은 정확한 횟수 대신 `volume_band` (`1-4`, `5-9`, `10-49`, `50+`), `period_days: 7`, `unit: requests_not_agents`, 조건을 만족할 때의 `trend: increased`다. 독립 에이전트 수나 장애 발생 수를 세지 않는다. 조회량과 업체 상태는 `observations` 또는 `incidents`로 복사하지 않는다. 초기 데이터 설계(비공개 운영 기록)

`next_checks`는 `code`와 영어 `text`로 된 짧은 점검 목록이다. 사람 확인·로그인, 선택적으로 전달된 실패 단계, 선언 정책 층(`access_profile`)과 공식 경로 층(`official_paths`), 신선한 업체 발표의 신호, 제보 존재 여부로 정해지며 작업의 실제 결과나 원인을 추론하지 않는다. 사람용 화면은 같은 코드를 한국어로 표시한다. 목록은 사이트 우회 권한이나 자동 장애 판정이 아니다. 기존 `current_site_status: unknown` 의미는 유지한다.

선택적 `failure_stage` (`before_target_response`, `response_observed`, `access_denial_observed`, `unknown`)는 **조회자가 확인한 상황**을 점검 안내에 반영한다. 대상 응답의 출처와 접근 거부의 원인은 서비스가 검증하지 않는다. 값은 조회 응답에 되돌려 주지만 제보·업체 상태·호스트별 조회 집계의 근거로 저장하지 않는다. 잘못된 `error_class` 또는 `failure_stage`를 REST 조회에 전달하면 `400 invalid_request`와 안전한 `field`·`allowed_values`만 돌려주며 원래 입력은 반사하지 않는다. MCP 도구는 같은 선택값을 받는다.

MCP `check_incident`는 내부적으로 호스트 등록·조회 집계를 수행하지만 호출자 관점의 결과를 바꾸지 않으므로 2026-10-17부터 `readOnlyHint=true`·`idempotentHint=true` 로 주석한다(아래 접근 프로필 절). 앱이 승인 마찰 없이 적시에 호출하는지는 실제 파일럿에서 검증한다. 성공한 MCP 도구 실행만 중앙 API가 별도 일별 범주로 집계한다. `/mcp` HTTP 접속·REST GET 수와 구분하며, 오류 실행·독립 에이전트 수·작업 결과는 알 수 없다.

## 접근 프로필과 공식 경로 (2026-10-17 추가)

조회 응답과 MCP `check_incident` 결과에 `access_profile` 과 `official_paths` 가 추가된다. 둘 다 `null` 일 수 있다. 공개 운영 모드에서만 채워지며, `access_profile` 은 공개 집계에 적합한 공개 루트 호스트일 때(`www.<루트>` 조회는 같은 캐시 읽기에서 나온 루트 호스트의 프로필), `official_paths` 는 표에 있는 호스트(서브도메인은 루트 호스트 항목)일 때 값이 있다. 읽기 실패는 응답 오류가 아니라 `null` 또는 상태 코드로 나타난다.

```json
{
  "site_host": "example.org",
  "access_profile": {
    "robots_status": "ok",
    "robots": {
      "rules": [
        {"agent": "ChatGPT-User", "kind": "user_agent", "explicit_group": false, "root_allowed": true, "disallow_count": 1,
         "crawl_delay": null, "disallow_paths": ["/search"]},
        {"agent": "GPTBot", "kind": "training_crawler", "explicit_group": true, "root_allowed": false, "disallow_count": 1,
         "crawl_delay": null, "disallow_paths": ["/"]},
        {"agent": "Googlebot", "kind": "search_crawler", "explicit_group": true, "root_allowed": true, "disallow_count": 0,
         "crawl_delay": null, "disallow_paths": []}
      ],
      "wildcard_root_allowed": true,
      "sitemap": true,
      "sitemaps": ["https://example.org/sitemap.xml"],
      "wildcard_crawl_delay": null,
      "wildcard_disallow_paths": ["/search"],
      "agents_blocked": ["GPTBot"],
      "agents_allowed": ["Googlebot"]
    },
    "robots_detail": "http_200",
    "llms_txt_status": "absent",
    "llms_detail": "http_404",
    "read_host": "example.org",
    "source_urls": {"robots_txt": "https://example.org/robots.txt", "llms_txt": "https://example.org/llms.txt"},
    "fetched_at": "2026-10-17T09:00:00Z",
    "freshness": "fresh",
    "note": "Declared crawler policy read from the host's own robots.txt and llms.txt. It is not an access guarantee: user-directed agents may be treated differently by CDNs, and a permissive file does not prove a page is reachable."
  },
  "official_paths": {
    "name": "Example",
    "api_docs": "https://developer.example.org/docs",
    "developer_portal": "https://developer.example.org/",
    "note": null
  }
}
```

- 읽는 범위: 공개 루트 호스트 또는 그 호스트 자신의 `www.` 별칭의 `/robots.txt`(상한 256 KB)와 `/llms.txt`(상한 4 KB, 존재 여부만)뿐이다. 3xx 는 같은 경로의 자기 `www.` 별칭을 가리킬 때만 한 단계 따라가고, 다른 리디렉션은 `redirected` 로 기록하고 따라가지 않는다. 루트 호스트의 `robots.txt` 결과가 `error`·`refused`·`redirected`·`invalid` 면 `https://www.<호스트>/robots.txt` 를 예비로 한 번 읽고, 그 결과가 `ok`·`absent`·`too_large` 면 루트 결과를 대신한다. `llms.txt` 는 `robots.txt` 를 내준 호스트에서 읽는다. 한 번의 시도는 HTTPS 요청을 최대 다섯 번 한다(루트 `robots.txt`, `www.` 별칭으로 가는 한 단계, `www.` 예비 읽기, `llms.txt`, 그 한 단계).
- `access_profile.robots_status` 는 `ok`, `absent`(404/410), `redirected`(자기 `www.` 별칭으로 가는 한 단계 외의 3xx, 따라가지 않음), `refused`(공개 데이터센터 주소의 단순 robots.txt 읽기에 호스트 경계가 401/403/429/503 으로 답함 — 선언이 아니라 강한 신호), `invalid`(정책 파일 대신 웹 페이지가 옴, 소프트 404), `error`(DNS·TLS·연결·시간 초과, 또는 200·3xx·401·403·404·410·429·503 이외의 HTTP 상태 같은 전송 실패), `too_large`(256 KB 초과), `not_public`(공개 IP 로만 해석되지 않음), `pending` 중 하나다. 전송 실패는 이전 답을 덮어쓰지 않는다. `robots_status`·`robots`·`llms_txt_status`·`llms_detail`·`fetched_at` 은 마지막 확정 읽기를 유지하고, `freshness` 는 `stale` 이 되며, `robots_detail` 에 실패 라벨이 남는다. 따라서 `error` 는 확정 읽기가 한 번도 없을 때만(`freshness: pending`) 나타난다. `robots` 는 `robots_status` 가 `ok` 일 때만 값이 있다. `ok` 가 아니면 정책 층은 **판단 불가**이지 차단이 아니다. 단 `refused` 는 판정에서 공식 경로 우선의 근거로 쓴다.
- `robots_detail` / `llms_detail` 은 읽기가 그렇게 끝난 이유를 짧은 기계 라벨로 적는다 — 모든 HTTP 응답의 `http_<상태>`(`http_200`, `http_401`, `http_403`, `http_404`, `http_410`, `http_429`, `http_500`, `http_503` 등), `redirect:<host>`(라벨 전체를 32자로 자름, 리디렉션에 호스트가 없으면 `http_3xx`), `dns_timeout`, `dns_failed`, `connect_error`(연결 거부·도달 불가), `connect_timeout`, `read_timeout`, `tls_error`, `read_error`, `html_body`, `too_large`, `not_public`, `invalid_input`. 연결 자체가 안 되면 같은 호스트의 다른 주소로 넘어간다(IPv4 우선, 최대 3개, 모든 시도를 합쳐 연결 시간 예산 4초 안에서). 연결된 뒤의 실패는 그대로 기록하고 다른 주소로 다시 시도하지 않는다. 전송 실패 뒤에는 `robots_detail` 이 그 실패를 말하고 나머지 필드는 마지막 확정 읽기를 보여 준다. 이 라벨이 생기기 전에 캐시된 프로필에서는 둘 다 `null` 일 수 있다.
- `read_host` 는 URL 을 읽은 호스트(조회한 루트 호스트 — `www.<루트>` 조회에서도 같다 — 또는 `www.` 예비 읽기가 루트 결과를 대신했을 때의 `www.` 별칭)다. 루트에서 자기 `www.` 별칭으로 가는 한 단계 리디렉션을 따라간 경우에는 바뀌지 않으며, `read_host` 와 `source_urls` 는 그 파일로 리디렉션되는 루트 URL 을 유지한다. `source_urls` 는 `read_host` 기준의 두 파일 URL 이다. `note` 는 고정된 영어 주의문이다.
- `robots.rules[].crawl_delay`(초, 적용 그룹 기준)와 `robots.rules[].disallow_paths`(금지 경로 접두 표본 최대 6개, 각 80자)는 `root_allowed: true` 의 뜻을 좁힌다 — 루트는 열어 두고 검색·장바구니·계정 경로를 막는 호스트가 많다. `robots.sitemaps` 는 선언된 Sitemap URL 최대 5개, `robots.wildcard_crawl_delay`·`robots.wildcard_disallow_paths` 는 `*` 그룹의 값이다. 저장하는 것은 이 해석값과 읽기 상태뿐이며 파일의 나머지는 저장하지 않는다.
- `robots.rules` 는 서비스가 아는 에이전트 토큰 목록(`kind` 는 `user_agent`, `search_crawler`, `training_crawler`)마다 RFC 9309 우선순위(가장 긴 일치 규칙 우선, 같은 길이면 Allow 우선)로 계산하며, user-agent 이름은 너그럽게 앞부분 일치로 맞춘다(user-agent 이름이 3자 이상인 그룹은 그 이름으로 시작하는 모든 알려진 토큰에 적용, 대소문자 무시). `explicit_group` 은 그 토큰의 그룹이 파일에 명시됐는지, `root_allowed` 는 `/` 를 가져올 수 있는지, `disallow_count` 는 적용되는 비어 있지 않은 Disallow 줄 수다. `agents_blocked` 는 명시 그룹이 있고 루트가 금지된 토큰, `agents_allowed` 는 명시 그룹이 있고 루트가 허용된 토큰이다. 목록에 없는 토큰에 대해서는 아무것도 말하지 않는다. `*` 그룹이 루트를 금지하고 에이전트 UA·검색 크롤러 토큰 가운데 어떤 그룹도 지목하지 않은 것이 하나 이상 있는 파일(그런 토큰을 지목해 루트를 금지한 그룹은 없는 경우)은 판정과 `next_checks` 에서 `robots_wildcard_disallow` 로 보고하며 "에이전트를 허용함"으로 보고하지 않는다.
- `llms_txt_status` 는 `present`(4 KB 상한을 넘는 파일 포함, `llms_detail: too_large`), `absent`(소프트 404 페이지 포함, `llms_detail: html_body`), `refused`, `error`, `redirected`, `not_public`, `pending` 중 하나이며 존재 여부만 기록한다. 내용은 저장하거나 전달하지 않는다. 서브도메인·테넌트 호스트(`www.<루트>` 이름은 제외, 루트 호스트의 프로필을 받는다)를 조회하면 프로필을 읽지 않고 `next_checks` 에 `profile_not_read` 를 넣어 apex 호스트 조회를 안내한다.
- `fetched_at` 은 마지막 확정 읽기 시각(UTC), `freshness` 는 `fresh`(24시간 이내의 확정 읽기이고 그 뒤 전송 실패 없음), `stale`(오래됐거나 그 뒤 전송 실패), `pending`(확정 읽기 없음)이다. 확정된 답(`ok`, `absent`, `refused`, `redirected`, `invalid`, `too_large`, `not_public`)은 24시간 캐시하고, 전송 실패(DNS·TLS·연결·시간 초과·예상 밖 HTTP 상태)는 1시간 뒤 다시 시도한다. 캐시 기간 안의 조회는 네트워크에 나가지 않는다.
- **선언된 정책은 접근 보장이 아니다.** `root_allowed: true` 가 실제 요청의 성공을 뜻하지 않고, CDN·봇 필터·로그인·속도 제한은 사용자 지시 에이전트를 크롤러와 다르게 취급할 수 있다. Allow 줄은 사이트 약관을 대신하지 않는다. 반대로 `agents_blocked` 에 자신의 UA 계열이 있으면 에이전트는 그 페이지의 재시도를 멈추고 인계하거나 공식 경로를 쓰는 것이 기대 행동이다.
- `official_paths` 는 코드 안의 정리된 표(`ahn/official_paths.py`)에서 온다. `name`, `api_docs`, `developer_portal`, `note` 만 있으며 `name` 을 제외한 세 필드는 `null` 일 수 있고, 표에 없는 호스트면 객체 전체가 `null` 이다. 서브도메인은 루트 호스트의 항목과 맞춘다(`api.github.com` 은 `github.com` 항목). `note` 만 있는 항목(예: 공개 API 없음)은 표시하지만 판정과 `official_path_available` 에서는 사용 가능한 공식 경로로 세지 않으며, 그러려면 `api_docs` 나 `developer_portal` 이 있어야 한다. 공개 개발자 문서가 실제로 확인된 호스트만 넣고 확신이 없으면 뺀다. 없음(`null`)은 공식 경로가 없다는 결론이 아니다. 목록은 보증·제휴가 아니고, 공식 API 사용에는 보통 사용자 자신의 자격 증명과 업체 약관이 필요하다.
- `next_checks.code` 는 다음 순서로 나온다: ① 실패 단계(`failure_stage` 값이 있고 `unknown` 이 아니면 `before_target_response`·`response_observed`·`access_denial_observed`, 아니면 `error_class` 가 `human_verification`·`login_auth` 가 아닐 때 `locate_failure_stage`) → ② 인계(`human_handoff`, `account_handoff`) → ③ 선언 프로필(프로필을 읽지 않은 서브도메인·테넌트 호스트는 `profile_not_read`, `www.<루트>` 이름은 루트 호스트의 프로필을 받는다; 프로필이 있으면 `robots_fetch_refused`, `robots_unknown`, `robots_blocks_agents`, `robots_wildcard_disallow`, `robots_allows_agents` 중 하나, 이어서 `llms_txt_present`) → ④ 공식 경로(`official_path_available`) → ⑤ 업체(`provider_scope`, `provider_clear`, `provider_unknown`) → ⑥ 제보(`reports_unknown`, `reports_stale`, `reports_unverified`). 모두 20개 코드이며 영어 계약의 표에 뜻이 있다. 기존 `current_site_status: unknown` 과 `observations` 의 의미는 그대로다.
- 시드 선읽기: 크론(`python -m ahn.auto_review`)이 5분마다 기본 10개(`AHN_WARM_BATCH`, 0이면 끔), 호스트별 주 1회 재읽기, 시드 목록 = `ahn/seed_hosts.txt`(공개 저장소의 공개 목록). 전송 실패는 이미 답이 있던 호스트라도 1시간 뒤 다시 시도한다. 조회되지 않은 호스트도 읽으며 읽기 한도는 조회 경로와 같다(`ahn/profile_warm.py`).
- MCP `check_incident` 는 호출자 관점에서 읽기 전용·멱등으로 주석한다(`readOnlyHint=true`, `idempotentHint=true`). 서버 측 부수효과(호스트 등록, 일별 조회 집계, 캐시되는 robots/llms 읽기 — 확정 답은 24시간 재사용, 전송 실패는 1시간 뒤 재시도)는 호출자가 보는 결과를 바꾸지 않는다.

## 판정 (`recommendation`)

`recommendation` 은 선언 정책 층과 공식 경로 층에서만 정하는 행동 하나다(관찰은 바꾸지 않는다). 위에서부터 처음 맞는 행이 적용된다.

| `action` | 조건 | `confidence` | `reasons` |
| --- | --- | --- | --- |
| `wait_provider_incident` | `provider_status.freshness` 가 `fresh` 이고 `indicator` 가 `minor`, `major`, `critical` | medium | `provider_scope` |
| `hand_off_to_human` | `error_class=human_verification` | high | `human_handoff` |
| `hand_off_to_human` | `error_class=login_auth` | medium | `account_handoff` |
| `use_official_path` | 차단 선언(아래)이 있고 공식 경로가 있다 | high | `robots_blocks_agents`, `official_path_available` |
| `stop_declared_block` | 차단 선언이 있고 공식 경로가 없다 | medium | `robots_blocks_agents` |
| `use_official_path` | `robots_status` 가 `refused` 이고 공식 경로가 있다 | medium | `robots_fetch_refused`, `official_path_available` |
| `unknown` | `robots_status` 가 `refused` 이고 공식 경로가 없다 | medium | `robots_fetch_refused` |
| `use_official_path` | 와일드카드 단독 금지(아래)이고 공식 경로가 있다 | medium | `robots_wildcard_disallow`, `official_path_available` |
| `unknown` | 와일드카드 단독 금지이고 공식 경로가 없다 | low | `robots_wildcard_disallow` |
| `use_official_path` | 공식 경로가 있고 `error_class` 가 없거나 `site_access`, `service_error` | medium | `official_path_available` |
| `proceed` | `robots_status` 가 `ok` 이고 차단 선언도 와일드카드 단독 금지도 없다 | low | `robots_allows_agents` |
| `unknown` | 그 밖의 모든 경우 | low | 접근 프로필이 있으면 `robots_unknown`(예: `robots.txt` 를 읽지 못함), 공개 모드에서 서브도메인·테넌트 호스트면(`www.<루트>` 이름은 제외) `profile_not_read`, 그 밖에는 빈 목록 |

"공식 경로가 있다"는 `official_paths` 에 `api_docs` 나 `developer_portal` 이 있다는 뜻이며 `note` 만 있는 항목은 해당하지 않는다. **차단 선언**은 `robots_status` 가 `ok` 이고 `user_agent` 또는 `search_crawler` 토큰 하나 이상을 이름으로 지목한 그룹이 루트를 금지하는 경우다. 학습 크롤러의 금지는 `agents_blocked` 에 나오지만 차단 선언으로 세지 않는다. **와일드카드 단독 금지**는 `robots_status` 가 `ok` 이고 차단 선언이 없으며, 어떤 그룹도 지목하지 않은 `user_agent` 또는 `search_crawler` 토큰 하나 이상이 루트를 금지하는 `*` 그룹(`User-agent: *` 와 `Disallow: /`)을 물려받는 경우다. 다른 토큰을 지목한 그룹(허용된 검색 크롤러, 금지된 학습 크롤러)이 있어도 성립한다. 차단 선언은 아니지만 "에이전트를 허용함"으로도 보고하지 않는다. 자동화를 청하지 않은 것으로 보고 공식 경로를 우선하며 허락으로 읽지 않는다.

### 공식 경로 표의 정리와 검증 (2026-10-18)

- **표에 없음은 "정리되지 않음"이지 "API 없음"이 아니다.** `official_paths: null` 은 이 서비스가 그 호스트를 아직 정리하지 않았다는 뜻일 뿐이며, 에이전트는 업체 사이트에서 공식 경로를 직접 찾을 수 있다.
- 키는 `valid_host` 를 통과하는 소문자 등록 가능 루트 호스트다. 하위 도메인은 가장 가까운 루트 키로 해석한다(`api.github.com` → `github.com`). `gov.uk`, `go.kr` 같은 공용 접미사는 키로 쓰지 않는다. 그 아래의 서로 다른 기관이 한 항목을 물려받기 때문이다. 별칭은 `x.com`/`twitter.com` 처럼 실제로 따로 운영되는 호스트일 때만 둔다.
- URL 은 발행자(업체·기관)가 직접 운영하는 개발자 문서, API 레퍼런스, 개발자 포털만 넣는다. 제3자 미러, 블로그 글, 마케팅 페이지는 넣지 않는다. 키와 다른 도메인에 있는 공식 문서(`learn.microsoft.com`, `developers.google.com`, 업체의 GitHub 조직 등)는 `scripts/verify_official_paths.py` 의 `PUBLISHER_DOMAINS` 에 하나씩 선언하고, 순수 테스트 `tests/test_official_paths.py` 가 선언되지 않은 도메인을 거부한다.
- 공개 API 가 없는 호스트는 사실이고 쓸모 있을 때만 `note` 로 남긴다(예: 공개 API 종료 사실과 공식 상태 페이지). URL 은 추측하지 않는다.
- 검증: `scripts/verify_official_paths.py` 가 표의 모든 고유 URL(`note` 안의 URL 포함)에 HTTPS GET 을 한 번씩 보낸다. 제한 시간 10초, 전체 초당 최대 1요청, User-Agent `AgentHelpNetwork-curation/0.1 (+https://agenthelpnetwork.com/en/policy)`, 전송 오류·429·5xx 에만 재시도 1회다. 리디렉션은 검증용으로만 따라가며 최종 상태 200, `https`, 발행자 소유 최종 호스트, 로그인·봇 확인 페이지 아님, "not found" 제목 아님을 모두 만족해야 통과한다. `blocked:*` 는 발행자가 자동 확인을 거절했다는 뜻이지 페이지가 사라졌다는 증거가 아니다. 문서 URL 외에는 아무것도 가져오지 않는다(robots.txt·로그인 페이지·크롤링 없음).
- 이 스크립트는 외부 사이트 상태에 CI 가 묶이지 않도록 CI 에서 실행하지 않는다. 표를 바꿀 때 사람이 직접 실행하고 결과를 확인한다. 2026-10-18 전체 검증 결과는 이 절 끝에 적는다.
- `ahn/seed_hosts.txt` 는 공개 집계에 적합한 표의 키를 표 순서대로 맨 앞에 둔다(`tests/test_profile_warm.py`).
- 2026-10-18 전체 검증: 표 546개 호스트(URL 이 있는 540개), 고유 URL 722개 중 707개 통과, 13개 `blocked:*`, 2개 실패. 통과하지 못한 URL 은 모두 기존 항목이며 페이지가 사라졌다는 근거가 없어 유지했다. 봇 확인·403: `binance.com`(api_docs), `coupang.com`, `ebay.com`, `nih.gov`(api_docs), `pexels.com`, `salesforce.com`, `stackexchange.com`·`stackoverflow.com`(stackapps.com), `stlouisfed.org`(포털), `unsplash.com`. 연결 끊김: `tumblr.com`(같은 User-Agent 의 curl 은 200). 새로 넣은 항목은 모두 200 으로 확인했다.

## 다음 계약: 질문·경험 네트워크

사이트 호스트가 없는 질문, 자발적 경험 공유, 답변·정정·검색은 현재 v1 제보 계약에 포함되지 않는다. 이를 위한 별도 `/v2/knowledge/*` 계약과 스키마는 [확장 설계](agent-network-architecture.md)에 제안했다. 실제 API나 MCP 도구가 이미 제공되는 것처럼 안내하지 않는다.
