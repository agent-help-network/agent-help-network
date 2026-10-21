# API 및 도구 계약 v1

[English](api-contract.en.md) · 한국어

작성일: 2026-10-03. 로컬 MVP의 계약이며 [설계서](architecture.md)의 API 표를 구체화한다. 필드명과 오류 코드는 언어와 무관하다. 값에 자유 서술을 넣지 않는다. 아래 키 기반 합성 예시는 로컬 시험용이다. 현재 공개 제보의 차이는 마지막 **공개 접수 계약**을 따른다.

## 공통 규칙

- JSON 요청의 최상위 및 하위 객체에서 정의되지 않은 필드는 `400 invalid_request`로 거부한다.
- `site_host`는 소문자 공개 DNS 호스트명이다. URL, 경로, 쿼리, 포트, IP 주소, 로컬 호스트는 거부한다.
- **공개 집계에 적합한 공개 루트 호스트.** 조회 뒤에서 일어나는 호스트 단위 작업(호스트 등록, `lookup_activity`, `access_profile` 뒤의 `robots.txt`·`llms.txt` 읽기)은 적합한 공개 루트 호스트에서만 일어난다. 적합한 호스트는 `PUBLIC_AGGREGATE_TLDS` 의 최상위 도메인 아래 두 라벨 이름이거나 `PUBLIC_SUFFIXES_2` 의 두 라벨 접미사 아래 세 라벨 이름이다(`ahn/service.py` 의 `aggregate_eligible_host`). 첫 라벨이 `xn--` 로 시작하는 이름과, 이 두 형태보다 라벨이 많은 그 밖의 이름(테넌트·계정 하위 도메인)은 적합하지 않다. 최상위 도메인 목록은 코드에 고정돼 있고, 정리된 호스트(공식 경로·시드·상태 연결 호스트)가 빠진 최상위 도메인 아래에 있으면 넓힌다. 2026-10-19 에 `int`, `so`, `social`, `markets`, `ee`, `pro`, `one`, `mobi`, `gl`, `ms`, `mp`, `page`, `google`, `goog`, `microsoft`, `apple`, `aws` 를 더했다. 현재 목록은 이 문서가 아니라 코드를 본다.
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

동일 제보자의 키가 필요하다. 철회는 조회에서 제외하고 최소 감사 이벤트만 남긴다. 다시 요청해도 성공 상태를 반환하는 멱등 동작으로 한다. 보존 기간은 운영자가 정해 정책 화면에 적는다. 제보는 `AHN_REPORT_RETENTION_DAYS`(30–3650) 를 설정하지 않으면 자동 삭제 기한이 없고, 설정하면 매시간 도는 정리 작업이 그 기간이 지난 제보와 철회 30일이 지난 제보를 삭제한다. 요청하면 더 일찍 삭제할 수도 있다(정책 화면의 지원 주소).

## 사건 목록·상세

`GET /v1/incidents?site_host=&error_class=&limit=`는 최근 7일 공개 승인 사건만 최대 50건 보여준다. `GET /v1/incidents/{id}`는 한 사건의 공개 요약과 제한된 관찰 목록을 반환한다. 미승인·숨김·합성 사건은 공개 목록/상세에서 `404`로 취급한다. 로컬 시험용 조회 경로와 공개 웹 경로를 분리한다.

## 클라이언트

이 저장소의 두 패키지는 `GET /v1/incidents/check` 만 부르며 제보하지 않는다: Claude Code 플러그인 훅(`plugins/claude-code/ahn`, User-Agent `ahn-claude-code-plugin/0.2`)과 Python 패키지 `ahn-client`(`pip install ahn-client`, 소스는 `clients/python`, User-Agent `ahn-client/0.2 (+https://agenthelpnetwork.com)`)다. 둘 다 `site_host` 와 알 때의 `error_class`·`failure_stage` 만 보내며 자세한 것은 각 README 에 있다. 0.2.0 부터 둘 다 `pending`·`profile_pending` 을 받아들이고 계약 밖의 값은 `unknown` 으로 다룬다. 프로필이 `pending` 이면 요약 줄에 읽는 중이며 언제 한 번 더 조회할지를 적고, `ahn-client` 는 그런 답을 `retry_after_seconds` 동안만 캐시한다. 연결 화면에 플러그인, Cursor, OpenAI Agents SDK, 일반 MCP 클라이언트의 설정 방법이 있다. 어느 것도 공식 통합이 아니다.

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

`GET /v1/incidents/check`의 기존 `observations`와 `current_site_status: unknown`은 유지한다. 공개 루트 호스트는 첫 조회 시 내부 목록에 등록한다(`www.<루트>` 조회는 루트 호스트를 등록한다). `provider_status`는 **확인된 공식 상태 출처**가 있을 때만 값이 있으며, `source_name`, `source_url`, `indicator`, `source_updated_at`, `fetched_at`, `freshness`, `error_detail`을 담는다. 고정 연결 규칙이 없으면 첫 조회가 `status.<호스트>` 의 Statuspage 위임 확인을 뒤에서 읽기로 넣고(호스트당 하루 한 번), 그 조회는 발견 전 상태로 답하며 확인된 출처는 다음 조회부터 보인다. 연결된 피드는 캐시로 바로 답하고(첫 읽기 전에는 `pending`, `indicator: null`), 발표가 `stale`(2시간 초과)로 읽힐 때가 되면 조회 뒤에서 갱신하되 마지막 시도 뒤 15분 안에는 다시 하지 않는다([뒤에서 읽기](#뒤에서-읽기-stale-while-revalidate-2026-10-19)). 검토 크론도 등록된 공식 출처를 약 1시간 간격으로 갱신한다. 실패한 최신 조회는 이전 값을 `stale`로 표시하고 실패 사유를 `error_detail`에 짧은 라벨로 남긴다(`http_NNN`은 피드의 HTTP 상태이며 리디렉션은 따라가지 않으므로 `http_301` 같은 값으로 나타난다. 그 밖에 `timeout`, `dns_failed`, `connect_error`, `tls_error`, `read_error`, `too_large`, `invalid_body`, 발견된 Statuspage 출처의 `no_cname`·`id_mismatch`·`not_public`, 예비값 `error`). 이 라벨은 상태 피드 읽기에 대한 것이지 사용자의 대상 사이트에 대한 것이 아니며, 마지막 읽기가 성공이면 `null`이다. 다음 주기 작업에서 다시 시도한다. `indicator`는 `none`, `minor`, `major`, `critical`, `maintenance`, `unknown`, `null` 중 하나이며, `maintenance`는 업체 상태 페이지가 예정된 점검 진행 중이라고 발표했다는 뜻이다(Statuspage 형식의 `maintenance`, Instatus의 `UNDERMAINTENANCE`, Better Stack의 `maintenance`). 장애가 아니라 업체가 알린 작업이므로 `recommendation`과 `next_checks`는 `minor`와 같게 다루며(`wait_provider_incident`, `provider_scope`) `provider_scope` 문구만 예정된 점검임을 밝힌다. 다른 값과 마찬가지로 페이지 전체 상태이며 사용자의 대상 사이트에 대한 값이 아니다. `indicator: none`은 업체 상태 API에 활성 문제가 표시되지 않았다는 뜻이며 개별 에이전트의 로그인·접속 성공을 보증하지 않는다. 다른 출처와 충돌해도 결과를 덮어쓰지 않는다.

`provider_source_state`는 `feed_connected`, `official_page_only`, `not_verified_by_current_methods` 중 하나다. `official_page_only`일 때 `official_status_page`는 출처명과 URL만 제공하며 현재 상태를 읽은 값이 아니다. 페이지 전용 연결(`ahn/status_refresh.py` 의 `OFFICIAL_STATUS_PAGES`, 예: Netflix, Webex, Adobe, Microsoft 365)은 그 호스트에 연결된 피드가 없을 때만 쓴다. 피드는 같은 파일의 고정 연결 `SOURCES` 또는 위의 Statuspage 자동 발견에서 온다. 두 표를 만드는 방법과 개수를 세는 방법은 [업체 상태 출처 표의 정리](#업체-상태-출처-표의-정리-2026-10-19)에 있다. `not_verified_by_current_methods`는 업체가 API를 제공하지 않는다는 결론이 아니라 현 자동 발견 범위에서 출처를 검증하지 못했다는 뜻이다. `provider_discovery_attempted_at`은 해당 호스트의 자동 발견이 마지막으로 실제 실행된 시각(큐에 넣은 시각이 아님)이며 `null`이면 기록이 없다. 자동 발견은 `status.<호스트>`의 검증된 Statuspage 위임만 확인하고, 발견되지 않은 다른 공식 API의 존재 여부는 `unknown`으로 남긴다. 연결된 피드의 실제 조회 실패는 `provider_status.freshness`가 `pending` 또는 `stale`로 나타난다.

`lookup_activity`는 공개 집계에 적합한 루트 호스트라면 첫 조회부터 나타난다. 그 `www.` 이름의 조회는 루트 호스트 집계에 더해지고 루트 호스트의 구간을 보여 준다. 값은 정확한 횟수 대신 `volume_band` (`1-4`, `5-9`, `10-49`, `50+`), `period_days: 7`, `unit: requests_not_agents`, 조건을 만족할 때의 `trend: increased`다. 독립 에이전트 수나 장애 발생 수를 세지 않는다. 조회량과 업체 상태는 `observations` 또는 `incidents`로 복사하지 않는다. 초기 데이터 설계(비공개 운영 기록)

`next_checks`는 `code`와 영어 `text`로 된 짧은 점검 목록이다. 사람 확인·로그인, 선택적으로 전달된 실패 단계, 선언 정책 층(`access_profile`)과 공식 경로 층(`official_paths`), 신선한 업체 발표의 신호, 제보 존재 여부로 정해지며 작업의 실제 결과나 원인을 추론하지 않는다. 사람용 화면은 같은 코드를 한국어로 표시한다. 목록은 사이트 우회 권한이나 자동 장애 판정이 아니다. 기존 `current_site_status: unknown` 의미는 유지한다.

선택적 `failure_stage` (`before_target_response`, `response_observed`, `access_denial_observed`, `unknown`)는 **조회자가 확인한 상황**을 점검 안내에 반영한다. 대상 응답의 출처와 접근 거부의 원인은 서비스가 검증하지 않는다. 값은 조회 응답에 되돌려 주지만 제보·업체 상태·호스트별 조회 집계의 근거로 저장하지 않는다. 잘못된 `error_class` 또는 `failure_stage`를 REST 조회에 전달하면 `400 invalid_request`와 안전한 `field`·`allowed_values`만 돌려주며 원래 입력은 반사하지 않는다. MCP 도구는 같은 선택값을 받는다.

MCP `check_incident` 는 호출자에게 읽기 전용이다. 제보를 만들지 않고 대상 사이트나 호출자의 데이터를 바꾸지 않는다. 조회는 익명으로 집계되고(호스트 등록·일별 호스트 조회 수, 조회 자체의 짧은 트랜잭션), 서비스가 캐시를 채우려고 호스트의 공개 `robots.txt`·`llms.txt`·상태 피드를 읽을 수 있으며 이 읽기는 조회가 기다리는 동안이 아니라 뒤에서 한다. 주석은 `readOnlyHint=true`, `idempotentHint=true`, `openWorldHint=true`(공개 웹을 설명함)다. 앱이 승인 마찰 없이 적시에 호출하는지는 실제 파일럿에서 검증한다. 성공한 MCP 도구 실행만 중앙 API가 별도 일별 범주로 집계한다. `/mcp` HTTP 접속·REST GET 수와 구분하며, 오류 실행·독립 에이전트 수·작업 결과는 알 수 없다.

### 업체 상태 출처 표의 정리 (2026-10-19)

`ahn/status_refresh.py` 의 고정 연결 두 표는 사람이 직접 고친다. 스크립트가 찾은 것을 자동으로 반영하지 않는다. 개수는 여기 적힌 숫자 대신 코드에서 센다(`len(SOURCES)`, `len(OFFICIAL_STATUS_PAGES)`).

- `SOURCES` 는 호스트를 피드에 연결한다. 각 출처의 `kind` 가 파서와 읽기 상한을 정한다(`FEED_KINDS`): `statuspage`(페이지 주소 + `api/v2/status.json`, incident.io 페이지도 같은 형식으로 답한다), `instatus`(`summary.json`), `betterstack`(`index.json`), `google_gmail`. 피드 주소가 https 로 200 을 돌려주고 그 종류의 상한 이하이며 해당 파서가 받아들인 경우만 넣는다. 주소는 항상 페이지 주소에 그 종류의 고정 경로를 붙인 것이다(리디렉션되는 피드는 최종 주소로 적는다). `OFFICIAL_STATUS_PAGES` 는 진짜 상태 페이지로 읽혔지만 파서가 읽을 피드는 없는 발행자 상태 페이지에 호스트를 연결한다. 링크는 절대 지표로 바꾸지 않는다. 페이지가 호스트의 일부만 다루면(예: 한 클라우드, 한 제품군) 출처 이름에 범위를 적는다.
- 키는 `www.` 없는 소문자 호스트이며 위의 적합성 검사를 통과해야 한다. 한 호스트가 두 표에 함께 있을 수 없고, URL 은 https 이며, 자리표시자나 상태 모음(aggregator) 주소는 받지 않는다(`tests/test_status_mapping.py`, DB·네트워크 없음).
- `scripts/discover_status_sources.py` 는 정리 보조 도구다. CI 에서 실행하지 않고 서비스가 가져다 쓰지도 않으며, 결과는 git 이 무시하는 `work/status-discovery/<배치>/` 폴더에 쓴다. 호스트 목록을 받아 이미 연결됐거나, 적합하지 않거나, `www.<루트>` 인 호스트는 건너뛰고, `status.<루트>` 의 DNS 대상, 그 호스트의 `OFFICIAL_PATHS` 페이지에 있는 상태 링크, 선택적으로 내려받은 MIT 라이선스 Statusphere 목록, 흔한 이름 규칙에서 후보 상태 페이지를 만든다. 공개 주소로 해석되지 않는 후보는 접속하지 않는다. Statuspage 파서가 `/api/v2/status.json` 을 받아들이면 `feed`(스크립트는 그 형식만 찾으며, `--classify` 는 Instatus·Better Stack 같은 다른 제품을 밝혀 다른 종류의 피드를 사람이 추가하게 한다), 200 이고 상태 페이지처럼 읽히면 `link`, 스크립트를 거절하면(401/403/429/202, 봇 확인) 미검증이다.
- 소유 근거는 규칙으로 정해 행마다 기록한다. **A** 상태 호스트가 발행자의 등록 도메인에 있다(`statuspage.io`, `github.io`, `instatus.com` 같은 공용 호스팅은 인정하지 않는다). **B** 발행자 자신의 `status.<루트>` 가 그 주소로 리디렉션된다. **C** `OFFICIAL_PATHS` 에 이미 있는 발행자 문서 페이지가 그 주소를 링크하며 문서 URL 을 기록한다. 셋 중 어느 것도 없는 후보와, 근거 없이 스크립트를 거절한 페이지는 `needs_review` 로 적고 표에 넣지 않는다.
- 예의는 `scripts/verify_official_paths.py` 와 같다: 전체 초당 HTTPS 요청 1건(그보다 짧은 `--min-interval` 은 거부), 제한 시간 10초, 정리용 User-Agent, 직접 따라가는 리디렉션과 모든 경유지의 공개 주소 확인, 상태 호스트와 공식 경로 표에 이미 있는 발행자 문서 페이지만 읽는다. 대상 사이트의 첫 화면은 조사하지 않고 `robots.txt` 도 가져오지 않는다. 중단된 실행은 `cache.json` 으로 요청을 반복하지 않고 이어 간다. `--classify` 는 링크된 페이지마다 어느 제품이 돌리는지 밝히고, `--candidates-csv` 는 사람이 찾은 주소에 같은 검증을 한다.
- 스크립트 규칙은 `tests/test_discover_status_sources.py` 가 확인한다(네트워크 없음, 끝에서 끝까지의 사례는 가짜 네트워크로 돌린다).

## 접근 프로필과 공식 경로 (2026-10-17 추가)

조회 응답과 MCP `check_incident` 결과에 `access_profile` 과 `official_paths` 가 추가된다. 둘 다 `null` 일 수 있다. 공개 운영 모드에서만 채워지며, `access_profile` 은 공개 집계에 적합한 공개 루트 호스트일 때(`www.<루트>` 조회는 같은 캐시 읽기에서 나온 루트 호스트의 프로필), `official_paths` 는 표에 있는 호스트(서브도메인은 루트 호스트 항목)일 때 값이 있다. 읽기 실패는 응답 오류가 아니라 `null` 또는 상태 코드로 나타난다. 파일은 조회가 기다리는 동안이 아니라 뒤에서 읽으며([뒤에서 읽기](#뒤에서-읽기-stale-while-revalidate-2026-10-19)), 호스트의 첫 읽기가 끝나기 전의 프로필은 `robots_status`·`llms_txt_status`·`freshness` 가 `pending`, `fetched_at`·`robots` 가 `null` 이고 `retry_after_seconds` 가 있는 모양이다.

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
    "note": "Declared crawler policy read from the host's own robots.txt and llms.txt. It is not an access guarantee: user-directed agents may be treated differently by CDNs, and a permissive file does not prove a page is reachable.",
    "content_use": {"status": "none_declared", "reason": null, "source_url": "https://example.org/robots.txt",
                    "fetched_at": "2026-10-17T09:00:00Z", "freshness": "fresh"}
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
- `access_profile.robots_status` 는 `ok`, `absent`(404/410), `redirected`(자기 `www.` 별칭으로 가는 한 단계 외의 3xx, 따라가지 않음), `refused`(공개 데이터센터 주소의 단순 robots.txt 읽기에 호스트 경계가 401/403/429/503 으로 답함 — 선언이 아니라 강한 신호), `invalid`(정책 파일 대신 웹 페이지가 옴, 소프트 404), `error`(DNS·TLS·연결·시간 초과, 또는 200·3xx·401·403·404·410·429·503 이외의 HTTP 상태 같은 전송 실패), `too_large`(256 KB 초과), `not_public`(공개 IP 로만 해석되지 않음), `pending`(아직 끝난 읽기가 없고 읽기가 예약됨) 중 하나다. 전송 실패는 이전 답을 덮어쓰지 않는다. `robots_status`·`robots`·`llms_txt_status`·`llms_detail`·`fetched_at` 은 마지막 확정 읽기를 유지하고, `freshness` 는 `stale` 이 되며, `robots_detail` 에 실패 라벨이 남는다. 따라서 `error` 는 확정 읽기가 한 번도 없을 때만(`freshness: pending`) 나타난다. `robots` 는 `robots_status` 가 `ok` 일 때만 값이 있다. `ok` 가 아니면 정책 층은 **판단 불가**이지 차단이 아니다. 단 `refused` 는 판정에서 공식 경로 우선의 근거로 쓴다.
- `robots_detail` / `llms_detail` 은 읽기가 그렇게 끝난 이유를 짧은 기계 라벨로 적는다 — 모든 HTTP 응답의 `http_<상태>`(`http_200`, `http_401`, `http_403`, `http_404`, `http_410`, `http_429`, `http_500`, `http_503` 등), `redirect:<host>`(라벨 전체를 32자로 자름, 리디렉션에 호스트가 없으면 `http_3xx`), `dns_timeout`, `dns_failed`, `connect_error`(연결 거부·도달 불가), `connect_timeout`, `read_timeout`, `tls_error`, `read_error`, `html_body`, `too_large`, `not_public`, `invalid_input`. 연결 자체가 안 되면 같은 호스트의 다른 주소로 넘어간다(IPv4 우선, 최대 3개, 모든 시도를 합쳐 연결 시간 예산 4초 안에서). 연결된 뒤의 실패는 그대로 기록하고 다른 주소로 다시 시도하지 않는다. 전송 실패 뒤에는 `robots_detail` 이 그 실패를 말하고 나머지 필드는 마지막 확정 읽기를 보여 준다. 이 라벨이 생기기 전에 캐시된 프로필에서는 둘 다 `null` 일 수 있다.
- `read_host` 는 URL 을 읽은 호스트(조회한 루트 호스트 — `www.<루트>` 조회에서도 같다 — 또는 `www.` 예비 읽기가 루트 결과를 대신했을 때의 `www.` 별칭)다. 루트에서 자기 `www.` 별칭으로 가는 한 단계 리디렉션을 따라간 경우에는 바뀌지 않으며, `read_host` 와 `source_urls` 는 그 파일로 리디렉션되는 루트 URL 을 유지한다. `source_urls` 는 `read_host` 기준의 두 파일 URL 이다. `note` 는 고정된 영어 주의문이다.
- `robots.rules[].crawl_delay`(초, 적용 그룹 기준)와 `robots.rules[].disallow_paths`(금지 경로 접두 표본 최대 6개, 각 80자)는 `root_allowed: true` 의 뜻을 좁힌다 — 루트는 열어 두고 검색·장바구니·계정 경로를 막는 호스트가 많다. `robots.sitemaps` 는 선언된 Sitemap URL 최대 5개, `robots.wildcard_crawl_delay`·`robots.wildcard_disallow_paths` 는 `*` 그룹의 값이다. 저장하는 것은 이 해석값과 읽기 상태뿐이며 파일의 나머지는 저장하지 않는다.
- `robots.rules` 는 서비스가 아는 에이전트 토큰 목록(`kind` 는 `user_agent`, `search_crawler`, `training_crawler`)마다 RFC 9309 우선순위(가장 긴 일치 규칙 우선, 같은 길이면 Allow 우선)로 계산하며, user-agent 이름은 너그럽게 앞부분 일치로 맞춘다(user-agent 이름이 3자 이상인 그룹은 그 이름으로 시작하는 모든 알려진 토큰에 적용, 대소문자 무시). 그룹은 RFC 9309 와 Google 참조 파서를 따른다: 그룹은 User-agent 줄 하나 이상과 그 뒤의 Allow/Disallow 줄이다. 그래서 User-agent 줄 사이의 Sitemap·Crawl-delay·Content-Signal·모르는 줄은 그룹을 나누지 않고, 빈 줄만 있어도 나누지 않으며, 첫 User-agent 줄 앞의 규칙은 무시한다. 해석기마다 답이 갈리는 한 경우는 예외다: 그런 줄 뒤에 빈 줄이 오고 다음 User-agent 줄이 오면(`User-agent: *` / `Crawl-delay: 10` / 빈 줄 / `User-agent: BadBot` / `Disallow: /`) 그 User-agent 줄이 새 그룹을 시작한다. 파이썬 `urllib.robotparser` 도 이렇게 읽고, Google 파서는 둘을 합쳐 `*` 까지 막는다. `robots.grouping_differs_by_parser` 는 Google 방식으로 묶으면 `explicit_group` 이나 `root_allowed` 가 달라지는 `*` 와 에이전트 토큰 목록이다(두 해석이 같으면 빈 목록이고, 이 필드가 생기기 전에 읽은 프로필에는 없다). `recommendation` 은 이 목록을 쓰지 않으며, 목록이 비어 있지 않으면 `next_checks` 에 `robots_grouping_differs_by_parser` 가 붙는다. `explicit_group` 은 그 토큰의 그룹이 파일에 명시됐는지, `root_allowed` 는 `/` 를 가져올 수 있는지, `disallow_count` 는 적용되는 비어 있지 않은 Disallow 줄 수다. `agents_blocked` 는 명시 그룹이 있고 루트가 금지된 토큰, `agents_allowed` 는 명시 그룹이 있고 루트가 허용된 토큰이다. 목록에 없는 토큰에 대해서는 아무것도 말하지 않는다. 토큰 목록은 `ahn/access_profile.py` 의 `AGENT_USER_AGENTS`(토큰과 `kind` 의 쌍)이며 개수는 코드에서 센다. 토큰의 철자와 `kind` 는 업체 자체 문서를 따르고, 토큰별 문서 주소는 `AGENT_SOURCES` 에 적는다. 사이트들은 여전히 쓰지만 업체 문서로 확인되지 않는 소수 토큰은 `UNCONFIRMED_AGENT_TOKENS`(모두 `training_crawler`)에 둔다. 차단 선언과 와일드카드 단독 금지를 만들 수 있는 것은 `user_agent`·`search_crawler` 토큰뿐이며(`ahn/triage.py` 의 `AGENT_KINDS_FOR_BLOCK`), `training_crawler` 토큰은 `rules`·`agents_blocked` 에 나오지만 판정을 바꾸지 않는다. 앞부분 일치는 관련 토큰에도 적용된다: `Diffbot` 그룹은 `Diffbot-User` 에, `Applebot` 은 `Applebot-Extended` 에, `MistralAI` 는 미스트랄 토큰 셋 모두에 적용된다. 표가 바뀌기 전에 캐시된 프로필은 호스트를 다시 읽을 때까지(24시간 캐시가 지난 뒤 첫 조회) 예전 `rules` 를 유지한다. `*` 그룹이 루트를 금지하고 에이전트 UA·검색 크롤러 토큰 가운데 어떤 그룹도 지목하지 않은 것이 하나 이상 있는 파일(그런 토큰을 지목해 루트를 금지한 그룹은 없는 경우)은 판정과 `next_checks` 에서 `robots_wildcard_disallow` 로 보고하며 "에이전트를 허용함"으로 보고하지 않는다.
- `llms_txt_status` 는 `present`(4 KB 상한을 넘는 파일 포함, `llms_detail: too_large`), `absent`(소프트 404 페이지 포함, `llms_detail: html_body`), `refused`, `error`, `redirected`, `not_public`, `pending` 중 하나이며 존재 여부만 기록한다. 내용은 저장하거나 전달하지 않는다. 서브도메인·테넌트 호스트(`www.<루트>` 이름은 제외, 루트 호스트의 프로필을 받는다)를 조회하면 프로필을 읽지 않고 `next_checks` 에 `profile_not_read` 를 넣어 apex 호스트 조회를 안내한다.
- `fetched_at` 은 마지막 확정 읽기 시각(UTC), `freshness` 는 `fresh`(24시간 이내의 확정 읽기이고 그 뒤 전송 실패 없음), `stale`(오래됐거나 그 뒤 전송 실패), `pending`(확정 읽기 없음)이다. `retry_after_seconds` 는 `freshness` 가 `pending` 이고 읽기가 예약돼 있을 때만 정수이며(그 밖에는 키 자체가 없고 `null` 로 나오지 않음), 한 번 더 조회하기 전에 기다릴 초다. 폴링 간격이 아니다. 지금까지 실패한 시도만 있는 `pending` 프로필(`robots_status: error`)은 1시간 재시도 시점이 되면 다시 이 값을 단다. 확정된 답(`ok`, `absent`, `refused`, `redirected`, `invalid`, `too_large`, `not_public`)은 24시간 캐시하고, 전송 실패(DNS·TLS·연결·시간 초과·예상 밖 HTTP 상태)는 1시간 뒤 다시 시도한다. 조회는 네트워크에 나가지 않는다. 캐시 창 안이면 캐시로, 밖이면 캐시값(또는 `pending`)으로 답하고 읽기를 예약한다.
- **선언된 정책은 접근 보장이 아니다.** `root_allowed: true` 가 실제 요청의 성공을 뜻하지 않고, CDN·봇 필터·로그인·속도 제한은 사용자 지시 에이전트를 크롤러와 다르게 취급할 수 있다. Allow 줄은 사이트 약관을 대신하지 않는다. 반대로 `agents_blocked` 에 자신의 UA 계열이 있으면 에이전트는 그 페이지의 재시도를 멈추고 인계하거나 공식 경로를 쓰는 것이 기대 행동이다.
- **콘텐츠 이용 선호(`content_use`, 2026-10-20).** `access_profile.content_use` 는 같은 `robots.txt` 안에서 호스트가 선언한 콘텐츠 **이용** 선호다. Cloudflare Content Signals(`Content-Signal: search=yes, ai-input=yes, ai-train=no`)와 IETF AIPREF(`Content-Usage: train-ai=n`, draft-ietf-aipref-attach-05 와 draft-ietf-aipref-vocab-08 어휘, 둘 다 초안) 두 형식을 읽는다. 규칙을 위해 이미 읽은 바이트에서 해석하므로 요청이 늘지 않는다. 정보일 뿐 접근 규칙·허가·법적 해석이 아니며 `recommendation` 을 바꾸지 않는다. 사용자 지시 에이전트에 적용되는지는 두 형식 모두 정하지 않으므로 각 에이전트 운영사가 정한다. 필드 모양과 예는 영어 계약에 있다.
  - `status` 는 `declared`(그룹 안에 `Content-Signal`·`Content-Usage` 줄이 하나 이상), `none_declared`(`robots.txt` 를 읽었고 선언이 없음, 또는 `robots.txt` 자체가 없음: `reason: robots_absent`), `not_read` 중 하나다. **읽지 못한 것을 `none_declared` 로 말하지 않는다.** `declared` 가 아니면 `status`·`reason`·`source_url`·`fetched_at`·`freshness` 만 있는 압축형으로 답한다(`vocab`·`note` 문자열 없음). `not_read` 의 `reason` 은 `pending`(아직 읽지 않음), `robots_<robots_status>`(`robots_refused`, `robots_invalid`, `robots_redirected`, `robots_too_large`, `robots_not_public`, `robots_error`), `read_before_content_use` 중 하나다. 마지막 것은 프로필이 기술하는 `robots.txt` 에 대한 현재 결과가 없다는 뜻이다: 이 필드 전에 캐시됐거나, 저장된 결과가 다른 파일(저장된 `robots_sha256` 이 행의 해시와 다름, 예: 롤백이 행을 다시 쓴 경우) 또는 다른 해석기 버전(`vocab`)의 것이다. 다음 읽기에서 채워진다.
  - `signals`(Cloudflare)와 `usage`(AIPREF)는 `User-agent: *` 그룹의 사이트 전체(경로 없음, `/`, `/*`) 선언이다. 값은 `yes`·`no`·`unset` 으로 정규화한다(`y`/`n` 은 `yes`/`no`, 밝히지 않은 신호는 허락도 제한도 아닌 `unset`). `signals.use` 는 Cloudflare 의 시험용 네 번째 신호(`immediate`, `reference`, `full`) 또는 `null` 이다. `*` 그룹에 그 형식의 사이트 전체 줄이 없으면 객체가 `null` 이다. `formats` 는 파일 어디서든 찾은 형식이다.
  - 같은 범위에 선언이 여럿이면 가장 제한적인 값을 쓴다(`no` 가 `yes` 보다, `immediate` > `reference` > `full`). AIPREF 규칙(vocab-08 5.1절)이며 반복된 `Content-Signal` 줄에도 같게 적용한다.
  - 해석: `Content-Signal` 은 이름·값의 대소문자를 가리지 않고 `yes`/`no` 만 인정한다. `Content-Usage` 는 vocab-08 을 따른다. 키는 대소문자를 구분하고, 사전으로 해석되지 않는 값(대문자 키, 빈 항목)은 모든 범주를 `unset` 으로 두며, 모르는 키는 무시하고, 토큰 `y`/`n` 이 아닌 값은 그 범주를 `unset` 으로 두며, 매개변수는 무시하고, 반복된 키는 마지막 값을 쓴다. `ignored_keys` 는 쓰지 않은 키 이름 최대 5개이며 각각 짧은 토큰이다(아니면 `unparsable`).
  - 그룹은 robots 규칙을 판정하는 그룹과 똑같다(`split_groups` 기본 해석, 빈 줄 예외 포함). 그래서 `Content-*` 줄이 그룹의 에이전트 줄을 끝내지 않는다. 첫 `User-agent` 줄 앞의 선언은 어느 그룹에도 속하지 않아 무시한다. `named_groups`(최대 5개, 전체 수는 `named_groups_total`)는 다른 user-agent 를 지명한 그룹의 선언이며, 같은 선언은 한 항목으로 합치고 `ai-input=no`/`ai-use=n` 선언을 앞에 둔다. `agents` 에는 서비스의 에이전트 토큰 표(`AGENT_USER_AGENTS`)에 있는 이름만 업체 표기로 최대 8개(사용자 지시 에이전트 먼저) 두고, 그 밖의 이름은 `other_agents` 에 개수만 센다.
  - 값이 `/`·`/*` 가 아닌 경로로 시작하는 줄은 사이트 일부에만 해당하므로 `signals`/`usage` 에 합치지 않는다. 그런 경로는 **전체 합쳐** 최대 3개(각 80자)만 두며 `*` 그룹 것을 먼저, 그다음 이름 붙은 그룹 순서로 채운다. `scope` 는 `*` 그룹에 사이트 전체 줄이 있으면 `all_paths`, 경로 한정 줄만 있으면 `path_specific`, 아니면 `null` 이다.
  - `vendor_default_hint` 는 `*` 그룹의 신호가 Cloudflare 관리형 `robots.txt` 가 기본으로 넣는 줄(`search=yes, ai-train=no`, `use=reference` 유무 무관, `ai-input` 없음)과 정확히 같을 때 `true` 다. 사이트 운영자가 고르지 않은 CDN 기본값일 수 있다는 힌트이지 판정이 아니다.
  - `ai_input_no_agents` 는 사이트 전체 **실효** 선호가 `ai-input=no` 또는 `ai-use=n` 인 사용자 지시 에이전트 토큰(`kind: user_agent`) 목록이다. `robots.rules` 와 같은 그룹 규칙으로 계산한다: 어떤 그룹이 지명한 토큰(그 그룹에 `Content-*` 줄이 있든 없든)은 자신을 지명한 그룹의 선언만 따르고, 어느 그룹도 지명하지 않은 토큰만 `*` 그룹을 따른다. 자르기 전에 모든 그룹으로 계산한다. 목록이 비어 있지 않으면 `ai_input_declared_no` 가 `true` 이고 `next_checks` 에 정보성 코드 `content_use_ai_input_declared_no` 가 붙는다. `recommendation` 은 `content_use` 없이 계산한다.
  - `source_url`·`fetched_at`·`freshness` 는 robots 읽기의 값이다. 전송 실패가 나면 `robots` 와 똑같이 마지막 선언을 `freshness: stale` 로 유지한다.
- `official_paths` 는 코드 안의 정리된 표(`ahn/official_paths.py`)에서 온다. `name`, `api_docs`, `developer_portal`, `note` 만 있으며 `name` 을 제외한 세 필드는 `null` 일 수 있고, 표에 없는 호스트면 객체 전체가 `null` 이다. 서브도메인은 루트 호스트의 항목과 맞춘다(`api.github.com` 은 `github.com` 항목). `note` 만 있는 항목(예: 공개 API 없음)은 표시하지만 판정과 `official_path_available` 에서는 사용 가능한 공식 경로로 세지 않으며, 그러려면 `api_docs` 나 `developer_portal` 이 있어야 한다. 공개 개발자 문서가 실제로 확인된 호스트만 넣고 확신이 없으면 뺀다. 없음(`null`)은 공식 경로가 없다는 결론이 아니다. 목록은 보증·제휴가 아니고, 공식 API 사용에는 보통 사용자 자신의 자격 증명과 업체 약관이 필요하다.
- `next_checks.code` 는 다음 순서로 나온다: ① 실패 단계(`failure_stage` 값이 있고 `unknown` 이 아니면 `before_target_response`·`response_observed`·`access_denial_observed`, 아니면 `error_class` 가 `human_verification`·`login_auth` 가 아닐 때 `locate_failure_stage`) → ② 인계(`human_handoff`, `account_handoff`) → ③ 선언 프로필(프로필을 읽지 않은 서브도메인·테넌트 호스트는 `profile_not_read`, `www.<루트>` 이름은 루트 호스트의 프로필을 받는다; `retry_after_seconds` 가 있는 `pending` 프로필이면 `profile_pending`; 그 밖에 프로필이 있으면 `robots_fetch_refused`, `robots_unknown`, `robots_blocks_agents`, `robots_wildcard_disallow`, `robots_allows_agents` 중 하나, 이어서 `robots.grouping_differs_by_parser` 가 비어 있지 않으면 `robots_grouping_differs_by_parser`, 이어서 `llms_txt_present`, 이어서 `content_use_ai_input_declared_no`) → ④ 공식 경로(`official_path_available`) → ④-2 등록부 사실(`host_facts` 가 있을 때만 `government_registry_listed`, `government_list_version_missing`, `government_suffix_restricted` 중 하나, 정보성이며 `recommendation` 을 바꾸지 않음) → ⑤ 업체(`provider_scope`, `provider_clear`, `provider_unknown`) → ⑥ 제보(`reports_unknown`, `reports_stale`, `reports_unverified`). 모두 26개 코드이며 영어 계약의 표에 뜻이 있다. `robots_grouping_differs_by_parser` 는 정보용이다: "해석기마다 이 robots.txt 의 답이 갈린다. Google 방식은 이 답이 나눈 그룹을 합쳐 대개 더 엄격하게 읽는다. 권장 행동은 그룹을 나누는 해석을 따른다"는 뜻이다. `profile_pending` 은 "이 호스트의 robots.txt·llms.txt 를 지금 읽고 있으니 `access_profile.retry_after_seconds` 초 뒤 같은 조회를 한 번 더 하면 선언 정책이 들어 있다, 반복 폴링은 하지 말라"는 뜻이다. 기존 `current_site_status: unknown` 과 `observations` 의 의미는 그대로다.
- 시드 선읽기: 크론(`python -m ahn.auto_review`)이 5분마다 기본 10개(`AHN_WARM_BATCH`, 0이면 끔), 호스트별 주 1회 재읽기, 시드 목록 = `ahn/seed_hosts.txt`(공개 저장소의 공개 목록). 아직 읽지 않은 호스트가 먼저이고, 전송 실패는 이미 답이 있던 호스트라도 이 선읽기에서는 24시간 뒤에 다시 시도한다(조회 경로는 1시간). 조회되지 않은 호스트도 읽으며 읽기 한도는 조회 경로와 같다(`ahn/profile_warm.py`).
- MCP `check_incident` 는 호출자 관점에서 읽기 전용·멱등으로 주석한다(`readOnlyHint=true`, `idempotentHint=true`, `openWorldHint=true`). 서버 측 부수효과(호스트 등록, 일별 조회 집계, 뒤에서 하는 robots/llms·상태 피드 읽기 — 확정 답은 24시간 재사용, 전송 실패는 1시간 뒤 재시도)는 호출자가 보는 결과를 바꾸지 않는다.

## 호스트 사실 (`host_facts`, 정부 도메인 등록부)

`host_facts` 는 **도메인에 관한 레지스트리 사실**을 담는다. 어느 공개 정부 도메인 목록에 그 도메인이 있는지와 목록이 적은 기관명, 그리고 접미사가 정부 기관 전용이라는 레지스트리 규칙이다. 누가 그 이름을 등록했는지에 대한 사실일 뿐, 사이트·페이지·링크가 진짜이거나 안전하다는 말이 아니다. `recommendation` 을 바꾸지 않으며, 필드가 없다는 것은 사이트에 대해 아무것도 말하지 않는다. 정부 목록이나 정부 전용 접미사 규칙이 그 호스트에 해당할 때만 붙고 **그 밖에는 응답에서 키 자체가 빠진다**(`null` 아님). 일반 호스트의 응답은 길어지지 않는다.

```json
{
  "host_facts": {
    "government_registry": {
      "status": "listed",
      "registry": "us_dotgov",
      "registry_name": "U.S. .gov domain list (CISA)",
      "domain_type": "Federal - Executive",
      "organization": "Department of the Treasury",
      "suborganization": "Internal Revenue Service",
      "state": "DC",
      "list_version": "2026-10-20",
      "source_url": "https://github.com/cisagov/dotgov-data/blob/main/current-full.csv",
      "license": "CC0-1.0",
      "fetched_at": "2026-10-20T18:05:00Z"
    },
    "suffix_rule": {
      "suffix": "gov",
      "rule": "registration_limited_to_government",
      "text": "Only verified U.S. government organizations can register a .gov domain.",
      "source_url": "https://get.gov/domains/eligibility/"
    },
    "note": "Registry facts about a domain: which public government list names it, and whether its suffix is reserved for government bodies. They are not a judgement about the site, any page or any link."
  }
}
```

`.go.kr` 호스트에는 접미사 규칙만 있고 목록 항목은 없다. 이 서비스는 개별 `.go.kr` 도메인 목록을 읽지 않기 때문이다.

```json
{
  "host_facts": {
    "suffix_rule": {
      "suffix": "go.kr",
      "rule": "registration_limited_to_government",
      "text": "Under the KRNIC domain name management rules, .go.kr is for administrative agencies and legislative or judicial agencies.",
      "source_url": "https://krnic.kisa.or.kr/jsp/eng/policy/domainNewRules.jsp"
    },
    "note": "Registry facts about a domain: ..."
  }
}
```

- **`government_registry`** 는 `.gov`(미국 목록)와 `.gov.uk`(영국 목록) 아래 이름에 대해, 그 목록이 적재된 뒤에 붙는다. `status` 는 `listed`, 또는 목록의 접미사 아래 이름인데 적재된 판본에 없으면 `not_in_list_version` 이다. 뒤의 것은 `list_version` 에 표시된 판본에 관한 말이지 사이트에 대한 판정이 아니다. 목록은 게시자의 일정을 따르므로 새로 등록된 이름은 빠져 있을 수 있다. `registry` 는 `us_dotgov` 또는 `uk_govuk`. `organization`·`suborganization`·`domain_type`·`state` 는 목록이 적은 값이며 목록에 있고 목록이 그 값을 줄 때만 나온다. 영국 파일에는 도메인 이름만 있어 영국 항목에는 기관명이 없다. `list_version` 은 영국 목록의 "기준일", 미국 목록은 이 서비스가 지금 파일 내용을 처음 받은 UTC 날짜다(게시자는 변동이 있는 날 매일 파일을 갱신한다). `fetched_at` 은 목록을 마지막으로 성공적으로 확인한 시각이다. `source_url` 은 게시자 페이지, `license` 는 `CC0-1.0`(미국) 또는 `OGL-UK-3.0`(영국), 영국 목록에는 라이선스가 요구하는 `attribution` 문구가 붙는다: "Contains public sector information licensed under the Open Government Licence v3.0."
- **`suffix_rule`** 은 `.gov`, `.gov.uk`, `.go.kr` 에 붙는다. 그 접미사 아래 등록은 정부 기관만 할 수 있다는 레지스트리 자체 규칙과 그 근거다. `.or.kr`, `.co.kr` 등 다른 접미사에는 규칙이 없다.
- **어느 호스트에.** 다른 호스트 단위 층과 같은 호스트에 답한다. 적격 공개 루트 호스트와, `www.<루트>` 는 루트로 답한다(`www.irs.gov` 는 `irs.gov` 로). 그 밖의 서브도메인·테넌트 호스트(`tenant.irs.gov`)와 접미사 영역 자체(`go.kr`)에는 `host_facts` 가 없다. 목록은 등록된 도메인을 적을 뿐 그 아래에서 무엇이 돌아가는지는 말하지 않는다.
- **데이터 출처.** 검토 크론이 조회와 무관하게 공개 파일을 통째로 내려받는다. 미국 레지스트리의 `current-full.csv`(CISA, `cisagov/dotgov-data`, CC0-1.0)는 조건부 요청으로 24시간마다, 영국 "List of .gov.uk domain names"(GOV.UK, Open Government Licence v3.0)는 GOV.UK 콘텐츠 API 로 찾아 30일마다 확인한다. 접속하는 곳은 그 게시자뿐이며 목록에 적힌 사이트에는 접속하지 않는다. 파일의 연락처 열은 버린다. 미국 파일의 보안 연락 이메일과 도시는 읽고도 저장하지 않는다.
- **이상한 내려받기는 아무것도 바꾸지 않는다.** 요청 실패, 리디렉션, 크기 상한(미국 8 MB, 영국 2 MB) 초과, 열 변경, 쓸 수 없는 행이 너무 많음, 행이 너무 적음, 저장된 판본의 절반 미만인 행 수는 저장된 데이터를 그대로 두고 짧은 라벨과 함께 경고를 기록하며 6시간 뒤 다시 시도한다. 그동안 `fetched_at` 이 저장된 사본의 나이를 보여 준다.
- **조회는 데이터베이스만 읽는다.** `AHN_HOST_FACTS=0` 은 내려받기와 필드를 모두 끈다.
- 영국 목록 출처 표기: Contains public sector information licensed under the Open Government Licence v3.0.

## 판정 (`recommendation`)

`recommendation` 은 선언 정책 층과 공식 경로 층에서만 정하는 행동 하나다(관찰은 바꾸지 않는다). 위에서부터 처음 맞는 행이 적용된다.

| `action` | 조건 | `confidence` | `reasons` |
| --- | --- | --- | --- |
| `wait_provider_incident` | `provider_status.freshness` 가 `fresh` 이고 `indicator` 가 `minor`, `major`, `critical` | medium | `provider_scope` |
| `hand_off_to_human` | `error_class=human_verification` | high | `human_handoff` |
| `hand_off_to_human` | `error_class=login_auth` | medium | `account_handoff` |
| `use_official_path` | 프로필이 `retry_after_seconds` 가 있는 `pending`(읽기 예약, 선언 정책 아직 없음)이고 공식 경로가 있다 | medium | `profile_pending`, `official_path_available` |
| `unknown` | 프로필이 `retry_after_seconds` 가 있는 `pending` 이고 공식 경로가 없다 | low | `profile_pending` |
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
- 키는 `valid_host` 를 통과하는 소문자 등록 가능 루트 호스트다. 하위 도메인은 가장 가까운 루트 키로 해석한다(`api.github.com` → `github.com`). `gov.uk`, `go.kr`, `police.uk` 같은 공용 접미사는 키로 쓰지 않는다. 그 아래의 서로 다른 기관이 한 항목을 물려받기 때문이다(경찰 데이터 포털의 키는 `data.police.uk`). `tests/test_official_paths.py` 는 `PUBLIC_SUFFIXES_2` 와 별개인 공용 접미사 목록으로 키를 검사하므로 양쪽에 같은 빈틈이 있어도 통과하지 못한다. 별칭은 `x.com`/`twitter.com` 처럼 실제로 따로 운영되는 호스트일 때만 둔다.
- URL 은 발행자(업체·기관)가 직접 운영하는 개발자 문서, API 레퍼런스, 개발자 포털만 넣는다. 제3자 미러, 블로그 글, 마케팅 페이지는 넣지 않는다. 키와 다른 도메인에 있는 공식 문서(`learn.microsoft.com`, `developers.google.com`, 업체의 GitHub 조직 등)는 `scripts/verify_official_paths.py` 의 `PUBLISHER_DOMAINS` 에 하나씩 선언하고, 순수 테스트 `tests/test_official_paths.py` 가 선언되지 않은 도메인을 거부한다.
- **범위: 사람이나 에이전트가 일을 하려고 여는 사이트.** 다른 사이트를 돕는 호스트(CDN, DNS, 광고 기술, 추적, 자산·미디어 전달, 단축 링크, 업데이트 서버)는 운영자가 문서를 내더라도 항목을 두지 않는다. 에이전트가 작업하려고 여는 곳이 아니기 때문이다. `herokuapp.com`, `myshopify.com` 같은 테넌트 사이트 호스트는 호스트의 주인이 플랫폼이 아니므로 뺐고, `pages.dev`, `workers.dev` 같은 공용 호스팅 접미사도 뺐다.
- 공개 API 가 없는 호스트는 사실이고 쓸모 있을 때만 `note` 로 남긴다(예: 공개 API 종료 사실과 공식 상태 페이지). URL 은 추측하지 않는다.
- 검증: `scripts/verify_official_paths.py` 가 표의 모든 고유 URL(`note` 안의 URL 포함)에 HTTPS GET 을 한 번씩 보낸다. 제한 시간 10초, 전체 초당 최대 1요청, User-Agent `AgentHelpNetwork-curation/0.1 (+https://agenthelpnetwork.com/en/policy)`, 전송 오류·429·5xx 에만 재시도 1회다. `--hosts a.com,b.org` 는 그 표 키만 검사하고 `--json 파일` 은 URL 별·호스트 별 결과를 쓴다. 리디렉션은 검증용으로만 따라가며 최종 상태 200, `https`, 발행자 소유 최종 호스트, 로그인·봇 확인 페이지 아님, "not found" 제목 아님을 모두 만족해야 통과한다. `blocked:*` 는 발행자가 자동 확인을 거절했다는 뜻이지 페이지가 사라졌다는 증거가 아니다. 문서 URL 외에는 아무것도 가져오지 않는다(robots.txt·로그인 페이지·크롤링 없음).
- 이 스크립트는 외부 사이트 상태에 CI 가 묶이지 않도록 CI 에서 실행하지 않는다. 표를 바꿀 때 사람이 직접 실행하고 결과를 확인한다. 2026-10-18 전체 검증 결과는 이 절 끝에 적는다.
- `ahn/seed_hosts.txt` 는 공개 집계에 적합한 표의 키를 표 순서대로 맨 앞에 두고 그 뒤에 다른 공개 호스트를 둔다(`tests/test_profile_warm.py`).
- 2026-10-18 전체 검증(날짜가 붙은 기록이다. 표는 그 뒤 늘었고 2026-10-19 에 더한 항목은 새 전체 검증 대신 같은 방식의 `--hosts` 검사를 했다): 당시 표 546개 호스트(URL 이 있는 540개), 고유 URL 722개 중 707개 통과, 13개 `blocked:*`, 2개 실패. 통과하지 못한 URL 은 모두 기존 항목이며 페이지가 사라졌다는 근거가 없어 유지했다. 봇 확인·403: `binance.com`(api_docs), `coupang.com`, `ebay.com`, `nih.gov`(api_docs), `pexels.com`, `salesforce.com`, `stackexchange.com`·`stackoverflow.com`(stackapps.com), `stlouisfed.org`(포털), `unsplash.com`. 연결 끊김: `tumblr.com`(같은 User-Agent 의 curl 은 200). 새로 넣은 항목은 모두 200 으로 확인했다.

### 이용 정보 묶음 `official_paths.access` (2026-10-20)

문서 링크만으로는 부족할 때가 많다. 봇 차단벽이나 로그인 벽에 막힌 에이전트는 공식 경로가 어떤 자격 증명을 쓰는지, 사용자가 그것을 어디서 받는지, 무료 한도나 샌드박스가 있는지, 스펙과 속도 제한 문서가 어디 있는지, 업체가 MCP 서버를 운영하는지, 약관이 어디 있는지도 알아야 한다. 일부 호스트의 `official_paths` 에는 위의 네 필드 외에 이것을 담은 선택 객체 `access` 가 붙는다. 출처는 코드 안의 두 번째 정리 표(`ahn/official_paths.py` 의 `OFFICIAL_ACCESS`)이며 조회 때 새로 읽는 것은 없다. 묶음이 없는 호스트에는 `access` 키 자체가 없고(`null` 이 아님), 묶음 안에서도 모르는 필드는 빼서 응답을 작게 유지한다.

```json
"official_paths": {
  "name": "Stripe", "api_docs": "https://docs.stripe.com/api", "developer_portal": "https://docs.stripe.com/", "note": null,
  "access": {
    "auth": [{"method": "api_key", "source_url": "https://docs.stripe.com/api/authentication"}],
    "key_signup_url": "https://docs.stripe.com/keys",
    "rate_limits_url": "https://docs.stripe.com/rate-limits",
    "sandbox_url": "https://docs.stripe.com/sandboxes",
    "openapi_url": "https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json",
    "official_mcp": [{"endpoint": "https://mcp.stripe.com", "doc_url": "https://docs.stripe.com/mcp"}],
    "terms": {"url": "https://stripe.com/legal/ssa",
              "quote": "If User uses an AI Agent to access the Stripe Services, User is solely responsible for each action initiated by or through the AI Agent."},
    "verified_at": "2026-10-20"
  }
}
```

| 필드 | 뜻 |
| --- | --- |
| `auth` | `{method, source_url}` 목록. `source_url` 의 업체 문서가 적은 방식만 하나씩 넣는다. `method` 는 `none`(일부 끝점은 자격 증명 없이 답함), `api_key`(업체 콘솔에서 받는 고정 키나 앱 토큰), `oauth1`, `oauth2`(사용자가 앱을 승인), `device_code`(OAuth 2.0 기기 승인, RFC 8628: 사용자가 다른 기기에서 승인하므로 페이지 자동화가 필요 없음), `pat`(사용자로 행동하는 개인 액세스 토큰) 중 하나다. |
| `key_signup_url` | 키·토큰을 받는 방법을 설명하는 업체 문서. 로그인 뒤의 콘솔 페이지는 넣지 않는다. |
| `free_tier` | `{quote, source_url}`: 무료로 쓸 수 있는 양이나 조건(예: 하루 기본 할당량)을 말하는 업체 자신의 문장을 그대로 옮긴 인용(200자 이하). 요금 홍보 문구, "무료 체험" 광고, 가격표 머리말은 넣지 않는다. |
| `rate_limits_url` | 속도 제한·할당량에 관한 업체 문서. |
| `sandbox_url` | 시험·샌드박스 환경에 관한 업체 문서. 링크만 주며 시험용 값은 옮기지 않는다. |
| `openapi_url` | 업체 소유 URL 에 있는 기계 판독용 OpenAPI·Swagger 문서. |
| `official_mcp` | `{endpoint, doc_url}` 목록: `doc_url` 의 업체 문서에 적힌 그대로의 MCP 끝점. 이 서비스는 그 끝점에 접속하지 않는다. |
| `terms` | `{url, quote}`: API 에 관한 업체의 약관·정책 문서와 그 문서에서 그대로 옮긴 문장이나 구절 하나(200자 이하). |
| `verified_at` | 묶음의 모든 URL 과 인용을 마지막으로 읽은 날짜(UTC). |

- **약관은 링크하고 인용할 뿐 해석하지 않는다.** `terms` 에는 판정·입장·"자동화 허용" 같은 요약이 없다. 인용은 발췌 하나일 뿐 법률 조언도 문서 전체도 아니다. 링크된 문서를 읽어야 한다.
- **업체 소유, 직접 읽음, 추측 없음.** 모든 URL 은 키의 등록 가능 도메인(표와 같이 공용 접미사 기준), 그 키에 `PUBLISHER_DOMAINS` 로 선언된 도메인, 또는 한 도메인을 여러 주인이 쓰는 `github.com`·`raw.githubusercontent.com` 이라면 `PUBLISHER_GITHUB_ORGS` 에 선언된 업체 자신의 조직 안에 있다(둘 다 `scripts/verify_official_paths.py`). 모든 페이지는 `verified_at` 에 실제로 가져와 읽었다. 읽기를 거절한 페이지(401·403·429·챌린지)는 그 필드를 비우며 거절을 우회하지 않는다.
- **오래된 묶음은 숨긴다.** `verified_at` 이 90일을 넘은 묶음은 다시 확인할 때까지 응답에서 빠지고, `official_paths` 의 나머지는 그대로 남는다.
- **조언은 바뀌지 않는다.** `access` 는 `recommendation` 과 `next_checks` 를 바꾸지 않는다.
- 같은 업체·같은 API 의 호스트는 묶음 하나를 같이 쓴다(`chatgpt.com`↔`openai.com`, `notion.com`↔`notion.so`, `twitter.com`↔`x.com`, `wikimedia.org`↔`wikipedia.org`). 묶음 수는 `len(OFFICIAL_ACCESS)` 로 센다.
- 검증: `python scripts/verify_official_paths.py --access-only [--hosts a.com]` 이 위와 같은 예절로 묶음 안의 모든 문서 URL 을 읽고(모든 경유지가 공개 주소로만 풀려야 함), 각 인용과 MCP 끝점이 페이지의 보이는 텍스트에 그대로 있는지(공백·타이포그래피 따옴표는 맞춰서) 확인하며, `openapi_url` 이 OpenAPI·Swagger 문서인지 본다. MCP 끝점 자체에는 요청하지 않는다. 네트워크 없는 규칙(모양, 방식 목록, 200자 상한, 업체 소유 도메인, 미래 날짜 금지)은 CI 의 `tests/test_official_access.py` 에서도 돈다.
- 첫 검증 2026-10-20(날짜가 붙은 기록): `github.com`, `stripe.com`, `openai.com`, `youtube.com`, `slack.com`, `notion.so`, `reddit.com`, `x.com`, `wikipedia.org`, `cloudflare.com` 묶음. 고유 URL 54개 모두 업체 소유 호스트에서 200, 인용과 MCP 끝점 28개 모두 확인. 같은 날 `free_tier` 를 무료로 쓸 수 있는 양이나 조건을 말하는 문장으로 좁혀 `stripe.com`·`x.com`·`wikipedia.org` 의 인용(요금 홍보 문구, 요금 방식 문장, 자유 콘텐츠에 관한 문장)을 뺐다. 남은 것은 URL 51개, 인용·끝점 23개다. 읽기 거절로 비운 것: OpenAI 약관과 키 콘솔(`openai.com`, `platform.openai.com`: 403), Reddit 속도 제한 위키(403). 스크립트 없이는 본문이 없어 비운 것: Notion 개발자 약관.
- 두 번째 검증 2026-10-20(날짜가 붙은 기록): 40개 업체 더. API 문서나 개발자 포털이 있는 표 키 중 에이전트 수요 순서(D35)로 골랐다: `gitlab.com`, `bitbucket.org`, `google.dev`, `stackexchange.com`(별칭 `stackoverflow.com`), `discord.com`, `telegram.org`, `zoom.us`, `asana.com`, `figma.com`, `twilio.com`, `vercel.com`, `netlify.com`, `digitalocean.com`, `sentry.io`, `datadoghq.com`, `huggingface.co`, `anthropic.com`, `perplexity.ai`, `mistral.ai`, `groq.com`, `together.ai`, `deepseek.com`, `x.ai`, `openrouter.ai`, `elevenlabs.io`, `supabase.com`, `linear.app`, `shopify.com`, `paypal.com`, `squareup.com`, `spotify.com`, `twitch.tv`, `kakao.com`, `tosspayments.com`, `replicate.com`, `coinbase.com`, `plaid.com`, `mongodb.com`, `sendgrid.com`, `render.com`. 고유 URL 136개 모두 업체 소유 호스트에서 200, 인용과 MCP 끝점 30개 모두 확인. 읽기 거절로 비운 것: Stack Exchange API 약관, Discord 개발자 약관·정책, x.ai 법무 페이지(403). 스크립트가 돌아야 본문이 나오거나 https 가 아니어서 비운 것: Twitch 약관, Kakao 개발자 약관(http 로 이동), Atlassian·Vercel·Datadog 의 MCP 끝점(페이지의 보이는 텍스트에 없음). 문서 페이지에 스크립트 없이는 읽을 본문이 없어 묶음을 만들지 않은 곳: `airtable.com`, `dropbox.com`, `hubspot.com`, `cohere.com`, `naver.com`, `data.go.kr`, `mapbox.com`, `nasa.gov`.
- `official_paths` 와 `access` 는 저장된 사실이 아니라 코드이므로 JSON Lines 내보내기(아래)에는 해당 종류가 없다.
- 묶음 추가 절차: `scripts/README.md`.

## 뒤에서 읽기 (stale-while-revalidate, 2026-10-19)

조회는 제3자 호스트를 기다리지 않는다. `GET /v1/incidents/check` 와 MCP `check_incident` 는 연결 하나로 데이터베이스에서만 답한다. 기록(호스트 등록·호스트별 조회 수)을 먼저 커밋하고, 답에 필요한 읽기를 하고, 마지막에 큐에 넣는다. 대상 호스트·상태 업체를 읽는 일은 모두 조회 뒤에서 한다.

- **넣는 것.** `background_reads` 에 종류와 호스트마다 한 행이므로 같은 조회가 여러 번 와도 읽기는 하나다. 조회 등록 목록 상한(`QUERIED_HOST_CAP`) 안의 적격 호스트 가운데 캐시가 없거나 캐시 창(24시간, 전송 실패 뒤에는 1시간) 밖인 호스트의 `robots.txt`·`llms.txt` 프로필, 피드가 없는 호스트의 하루 한 번 Statuspage 발견, 그리고 연결된 피드 가운데 나이로 `stale`(2시간)이 됐거나 한 번도 읽지 않은 피드의 갱신(마지막 시도 뒤 15분 안에는 다시 넣지 않음)이다. 큐는 최대 2,000행이며 가득 차면 새 작업을 넣지 않는다. 큐에 쓰지 못해도 조회는 답한다.
- **읽는 것.** 웹 프로세스 안의 작은 작업자(스레드 둘, 한 번에 작업 하나씩 가져감, 행을 넣은 조회가 깨우고 30초마다 확인)와 5분 검토 크론(`python -m ahn.auto_review`, 한 번에 다섯 개씩, 120초 예산 안에서 큐를 먼저, 시드 목록을 나중에; 예산이 다 되면 진행 중인 읽기가 있어도 돌아온다)이다. 처리 순서는 프로필, 발견, 피드이고 같은 종류는 오래된 것부터다. 가져갈 때 `SELECT ... FOR UPDATE SKIP LOCKED` 로 잡고 시도 횟수를 올리며 5분 임대를 걸고, 작업 직전에 임대를 다시 확인하므로 두 프로세스·스레드가 같은 행을 읽지 않는다. `AHN_BACKGROUND_READS=0` 이면 웹 작업자를 끄고 크론만 처리한다. 외부 읽기는 프로세스당 초당 최대 두 번이다.
- **읽기 규칙은 그대로다**(결정 D27~D29): 같은 `robots.txt`·`llms.txt` 리더·상한·리디렉션 규칙·호스트별 잠금·24시간 캐시·전송 실패 뒤 1시간 재시도, 같은 피드 리더와 Statuspage 발견. 본문 읽기(`robots.txt`, `llms.txt`, 피드, 발견한 상태 페이지)마다 전체 10초 시간 상한도 있어서, 한 바이트씩 흘려보내는 호스트는 리더를 붙잡지 못하고 `read_timeout`(피드는 `timeout`)으로 끝난다.
- **결과.** 어떤 결과로 끝난 읽기든(전송 실패 포함) 끝난 작업이다. 캐시에 기록하고 행을 지운다. 처리 자체가 예외로 실패했거나 처리자가 도중에 멈춘 작업은 2·4·8·16분 뒤(2^시도 분, 최대 60분) 다시 하고 다섯 번째 시도 뒤 지운다.
- **호출자가 보는 것.** 처음 보는 적격 호스트의 첫 조회는 `robots_status: pending`, `llms_txt_status: pending`, `freshness: pending`, `fetched_at: null` 인 `access_profile` 을 돌려준다. 그 읽기가 큐에 들어 있으면 `retry_after_seconds` 도 붙는다. 큐 안의 위치와 가장 이른 시작 시각으로 셈한 5~300 사이 값이며(크론만 처리하면 300), 이때 `recommendation`·`next_checks` 에 `profile_pending` 이 들어간다. 그 초만큼 기다린 뒤 한 번만 다시 조회하고 반복 폴링은 하지 않는다. 큐에 넣지 못했으면(큐가 가득 참, 등록 목록 밖의 호스트, 데이터베이스 오류) 이 필드는 없고 프로필은 미상으로 읽힌다. 만료된 프로필은 캐시값을 `freshness: stale` 로 돌려주며 뒤에서 다시 읽는다. 오래됐거나 실패한 피드도 마지막 발표를 `stale` 로 유지한다. 연결됐지만 아직 읽지 않은 피드는 `provider_status.freshness: pending`, `indicator: null` 이다. 첫 발견은 `not_verified_by_current_methods` 로 보이고, `provider_discovery_attempted_at` 은 발견이 실제로 실행될 때 기록되며, 확인된 출처는 그 뒤 조회부터 보인다. 응답은 200 이므로 `Retry-After` 헤더는 쓰지 않고 본문 필드만 쓴다.

## 내보내기 v1 (운영자 CLI, 2026-10-19)

운영자는 저장된 사실을 JSON Lines(한 줄에 봉투 하나)로 내보낼 수 있다: `python -m ahn.cli export --kind access_profile|status|observation [--since ISO | --all] [--out 파일]`. 이 CLI 는 공개 엔드포인트가 아니다. 공개 데이터셋은 운영자 스위치가 켜져 있을 때만 제공하는 별도 파일인 [오프라인 번들](#오프라인-번들-2026-10-20-준비-스위치를-켜야-제공)이다. 봉투 필드와 예시는 [영어 계약의 Export v1](api-contract.en.md#export-v1-operator-cli)과 같다.

- 필드: `schema_version`(1), `kind`, `record_id`(`ahn:<운영자>:<kind>:<호스트>:<observed_at>`, 제보는 운영자 자신의 관찰 id 의 짧은 다이제스트를 덧붙이며 id 자체는 내보내지 않는다), `origin_operator`, `vantage`, `reader_version`, `observed_at`, `received_at`, `expires_at`(판독 24시간, 상태·제보 7일), `source_url`, `content_sha256`, `payload`, `signature`.
- 판독·상태는 확정 읽기(`fetched_at` 있음)만 내보낸다. 그 읽기 뒤 마지막 시도가 실패한 행은 읽기와 실패 라벨이 섞이므로 빼고, 다음 성공 읽기 뒤에 다시 내보낸다. `content_sha256` 은 받은 `robots.txt`·피드 바이트의 SHA-256 이며 원문은 저장하지 않는다. 출처 열(`origin_operator`, `vantage`, `reader_version`, 해시)은 마이그레이션 020 에서 생겼고 그 전에 읽은 행은 `null` 이며 백필하지 않는다. 운영자가 비어 있는 행은 이 운영자 자신의 읽기다. 판독 봉투의 `payload` 에는 저장된 `content_use`(해석한 선호, 해석기 버전 `vocab`, 해석한 파일의 `robots_sha256`)도 담기며, `robots.txt` 본문이 없던 읽기나 마이그레이션 021 이전 읽기는 `null` 이다.
- 제보는 공개 응답과 같은 규칙만: 공개 사건의 공개·비합성·미철회 제보 가운데 최근 7일(`--all` 이면 저장된 전부), `observed_at`·`received_at` 은 시 단위 절삭, 제보자 정보 없음. 페이로드는 공개 응답의 필드만 담는다: `site_host`, `observed_at`, `task_step`, `error_class`, `attempt_actor`, `attempt_env`, `outcome`.
- **서명.** `AHN_EXPORT_SIGNING_KEY` 가 Ed25519 개인키 파일(PEM, PKCS#8)을 가리킬 때만, `signature` 를 뺀 봉투의 RFC 8785(JCS) 정규형에 서명하고 `{"alg": "ed25519", "key_id": "<운영자>:<YYYY-MM>", "sig": 패딩 없는 base64url}` 로 적는다. `AHN_EXPORT_KEY_ID` 로 키 id 를 정한다(기본: 운영자와 현재 UTC 월). 키가 없으면 `signature: null` 이다.
- 운영자 id 는 `AHN_OPERATOR_ID`(기본 `agenthelpnetwork.com`), 선언 관측 위치는 `AHN_VANTAGE`(JSON, 기본 `{"kind":"datacenter","region":"US","asn":null,"declared":true}`)다.
- 계획만 있고 아직 게시하지 않음: 검증 키 `/.well-known/ahn-operator.json` 의 형식은 `{"operator": "<운영자>", "keys": [{"key_id": "<운영자>:<YYYY-MM>", "alg": "ed25519", "public_key": "<원시 32바이트 base64url>", "not_before": "<ISO>", "not_after": "<ISO 또는 null>"}]}` 이다. 게시 전에는 운영자가 직접 건넨 키로만 서명을 확인할 수 있다.
- **내보내기 v1 읽기.** 소비자는 모르는 kind 와 필드를 무시해야 한다. 새 kind·필드는 `schema_version` 을 바꾸지 않고 추가한다. CLI 의 `access_profile`·`status`·`observation` 말고도 [오프라인 번들](#오프라인-번들-2026-10-20-준비-스위치를-켜야-제공)이 `bundle_meta`·`official_path`·`status_source` 를 쓴다. 이 셋은 읽기가 아니라 큐레이션 표와 파일 자체를 설명한다: `received_at` 은 `null`(받은 것이 없음)이고, `observed_at`(따라서 `record_id`·`expires_at`)은 파일을 만든 시각이라 같은 항목도 빌드마다 `record_id` 가 바뀐다.

## 오프라인 번들 (2026-10-20 준비, 스위치를 켜야 제공)

**운영자 스위치가 켜져 있을 때만 제공한다.** 웹 서비스와 검토 크론 양쪽의 `AHN_PUBLIC_BUNDLE=1` 이 스위치다(웹 서비스에는 그때 개인정보 처리방침이 최종 수정일로 보일 날짜 `AHN_PUBLIC_BUNDLE_SINCE` 도 필요하다). 기본은 꺼짐이다. 꺼져 있으면 아무것도 만들지 않고, 크론은 저장된 파일을 지우며, 아래 두 경로는 없는 경로처럼 `404` 를 돌려준다. agenthelpnetwork.com 에서 스위치가 켜지기 전까지 이 절은 준비된 형식을 설명할 뿐, 받을 수 있는 파일이 아니다.

번들은 gzip JSON Lines 파일 하나다. 네트워크 조회 없이(호스트명을 밖으로 보내지 않고, 기다림 없이) 이 데이터를 쓰려는 에이전트·프레임워크와, 데이터를 미러하려는 운영자를 위한 것이다.

- `GET /v1/bundle/latest.jsonl.gz` 는 파일을 돌려준다(`Content-Type: application/gzip`, `Content-Encoding` 없음: 받은 바이트를 그대로 저장). `GET /v1/bundle/latest.jsonl.gz.sha256` 은 `sha256sum` 형식(`<hex>  ahn-bundle-<UTC 시각>.jsonl.gz`)의 SHA-256 을 돌려준다. 둘 다 `HEAD` 를 받는다. 파일 응답에는 `ETag: "<sha256>"`, `Repr-Digest: sha-256=:<base64>:`, `Last-Modified`, `Cache-Control: public, max-age=3600`, `Accept-Ranges: none` 이 붙는다. ETag 를 `If-None-Match` 로 보내면 파일이 그대로일 때 본문 없이 `304` 를 돌려준다. `Range` 요청에는 전체 파일을 `200` 으로 주고 전체 다운로드로 센다.
- 검토 크론이 하루 한 번 정도(저장된 파일이 23시간 지났을 때) 다시 만들고, 웹 서비스는 마지막으로 저장된 파일을 준다. 하루에 한 번 넘게 받지 말 것. 첫 빌드 전에는 `503 {"error": "bundle_not_ready"}`, 저장된 파일이 48시간을 넘으면 내주지 않고 `503 {"error": "bundle_stale"}` 을 돌려준다(둘 다 `Retry-After: 300`).
- 다운로드 상한: 클라이언트마다(비밀키로 날짜별 변환한 IP 값으로 메모리에서만 셈) UTC 하루 전체 다운로드 3회, 넘으면 `429 {"error": "bundle_client_limit"}`. 모든 전체 다운로드를 합쳐 UTC 하루 25 MiB 이며, 재시작해도 초기화되지 않게 DB 에서 센다. 넘으면 `429 {"error": "bundle_daily_limit"}`. 둘 다 다음 UTC 날까지 `Retry-After` 를 붙인다. `304`·`HEAD` 는 세지 않는다.
- 파일 상한: 레코드 20,000개, 압축 8 MiB, 압축 해제 128 MiB. 하나라도 넘으면 빌드가 실패하고 이전 파일이 남는다. 큐레이션 시드 호스트 전부에 일부러 무겁게 만든 가상 `robots.txt` 해석 결과를 넣어 재면 레코드 약 3,600개에 압축 약 0.84 MB(서명 포함 1.1 MB)다.

**형식.** 모든 줄이 내보내기 v1 봉투다(`schema_version` 1, [내보내기 v1](#내보내기-v1-운영자-cli-2026-10-19)과 같은 필드. 모르는 kind·필드는 무시할 것). 첫 줄은 머리 레코드 `kind: "bundle_meta"` 이고, payload 에 `title`, `creator`(운영자 표시명, 라이선스 권리자), `generated_at`, `valid_until`, `record_count`(머리 다음 레코드 수), 종류별 `counts`, `license: "CC-BY-4.0"`, `license_url`, `notice`, `attribution`, `third_party_quotes`, `status_source_note`, `schema_url`(이 절의 영문판), `includes`, `excludes` 가 있다. 나머지 레코드는 종류, 그다음 호스트명 순으로 정렬한다.

| kind | 내용 | 출처 |
|---|---|---|
| `access_profile` | 최근 확정 `robots.txt`·`llms.txt` 판독. 운영자 내보내기 레코드와 같되 판독 시각을 UTC 날짜로 자른다(`observed_at`·`received_at`·`record_id` 가 `T00:00:00Z`) | 공개 시드 목록([`ahn/seed_hosts.txt`](../ahn/seed_hosts.txt)) 중 Tranco 블록을 여는 `# Hosts chosen from the Tranco top 1,000` 주석 위의 큐레이션 부분 호스트만 |
| `official_path` | `name`, `api_docs`, `developer_portal`, `note`, 그리고 `access`(인증, 키 발급, 한도, 샌드박스, OpenAPI, MCP, 약관). `access` 는 API 와 같은 90일 규칙으로 파일의 유효 기간 내내 유효할 때만 넣는다 | 큐레이션 표 [`ahn/official_paths.py`](../ahn/official_paths.py) |
| `status_source` | `source_name`, `status_page_url`, `api_url`, `feed_kind`(링크만 있는 페이지는 둘 다 `null`) | [`ahn/status_refresh.py`](../ahn/status_refresh.py) 의 큐레이션 `SOURCES`·`OFFICIAL_STATUS_PAGES`. 이 호스트 중 상당수는 Tranco 순위의 구간을 훑어 찾았지만, 매핑마다 그 호스트의 상태 페이지에서 직접 확인했으며 순위는 담지 않는다 |

**신선도.** 번들 레코드의 신선도는 머리 레코드의 `valid_until`(빌드 시각 + 8일: 시드 목록을 주 1회 정도 다시 읽는다)로 판단한다. 각 레코드의 `expires_at` 은 온라인 API 의 캐시 규칙(판독 24시간, 큐레이션 표 7일)이고 봉투를 내보내기 v1 그대로 두려고 남긴 것이다. 오프라인에서는 `valid_until` 이 기준이다. 지나면 새 파일을 받거나, 호스트의 원본 파일을 읽거나, API 를 호출한다.

**레코드가 없다고 선언이 없는 것은 아니다.** `access_profile` 레코드가 없는 호스트는 이 파일을 위해 확정 판독되지 않았을 뿐이다(큐레이션 시드 목록에 없거나 마지막 읽기가 실패). 그 호스트에도 `robots.txt` 가 있을 수 있다. `official_path`·`status_source` 가 없으면 단지 큐레이션하지 않은 것이다. 정부 등록부 사실(`host_facts`)도 번들에 없다. 공개된 전체 파일 목록 두 개(미국 .gov 등록부 CC0, 영국 .gov.uk 목록 Open Government Licence v3.0)에서 오며, 이 파일보다 자주 갱신되는 게시자 원본에서 직접 읽는 것이 낫다.

**번들에 절대 들어가지 않는 것.** 모든 레코드에 필드 허용 목록(봉투, payload, robots 요약과 규칙, `content_use`, `access`), 금지 필드 검사, 호스트 검사를 적용하고, 하나라도 어기면 빌드가 실패한다: 제보와 제보에 관한 모든 것, 시드 목록에 없는 조회된 호스트명(그 판독·발견된 상태 페이지·조회 수 포함. 조회된 호스트가 공개 파일에 들어가면 누가 무엇을 조회했는지가 새어 나간다), 조회 수, 요청 기록, IP 변환값, 제보자 키, 상태 피드의 현재 값(피드는 직접 읽을 것. 번들에는 피드 위치만 있다), 제3자 인기 순위에서만 가져온 시드 호스트(시드 목록 끝의 Tranco 블록. 그 목록의 이용 조건이 정리될 때까지 뺀다. 그 블록 위 주석에 Tranco 가 나와도 빌드가 실패한다), 시드 목록의 순서나 순위(호스트명 순으로 정렬하고 순위는 쓰지 않는다). 상태 매핑은 조회가 있어야 행이 생기는 `status_sources` 표가 아니라 코드에서 가져온다.

**쓰는 법.** 모든 레코드에 확인할 곳이 있다: `source_url`, 판독이면 `content_sha256`. `access_profile` 은 판독 시점에 호스트가 선언한 정책일 뿐 접근 보장이 아니다. 서명은 운영자 CLI 와 같게 `AHN_EXPORT_SIGNING_KEY` 가 설정됐을 때만 하고, 아니면 `signature` 는 `null` 이며 HTTPS 로 받은 SHA-256 파일이 무결성 확인 수단이다. `sha256sum -c ahn-bundle-<시각>.jsonl.gz.sha256` 으로 확인하고 `gzip -dc` 로 읽는다.

**라이선스.** 번들은 `creator`(`AHN_OPERATOR_NAME`)에 적힌 운영자가 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.ko) 으로 제공하고, 코드는 그대로 Apache-2.0 이다. 머리 레코드의 `notice` 가 표시 문구(`Agent Help Network offline bundle © <연도> by <creator> is licensed under CC BY 4.0. To view a copy of this license, visit https://creativecommons.org/licenses/by/4.0/`)이고, `attribution` 은 생성 시각이 든 출처 표기 문구다. 재사용·미러할 때는 creator 와 Agent Help Network 를 표기하고 라이선스를 링크하며, 데이터를 바꿨으면 그렇다고 밝힌다. **업체 문서 인용은 대상이 아니다:** `official_path` 의 `access.free_tier.quote`·`access.terms.quote` 는 업체 페이지를 그대로 옮긴 짧은 인용으로 제3자 저작물이며, 이 라이선스가 허락하지 않는다(파일의 `third_party_quotes` 에도 적는다). 라이선스는 여기서 엮은 데이터셋에 적용된다. 각 레코드는 읽은 공개 원본을 가리키며 사이트의 원본 파일은 담지 않는다.

**운영자.** `python -m ahn.cli bundle --out 파일` 은 DB 에서 번들을 만들어 공개 없이 확인할 수 있게 쓰고, `--store` 는 크론과 같은 DB 잠금을 잡고 웹 서비스가 주는 저장 파일을 바꾼다. 둘 다 `AHN_OPERATOR_NAME` 이 필요하다.

## 다음 계약: 질문·경험 네트워크

사이트 호스트가 없는 질문, 자발적 경험 공유, 답변·정정·검색은 현재 v1 제보 계약에 포함되지 않는다. 이를 위한 별도 `/v2/knowledge/*` 계약과 스키마는 [확장 설계](agent-network-architecture.md)에 제안했다. 실제 API나 MCP 도구가 이미 제공되는 것처럼 안내하지 않는다.
