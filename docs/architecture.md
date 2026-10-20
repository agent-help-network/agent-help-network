# Agent Help Network 로컬 MVP 시스템 설계서

상태: 구현 기준 초안, 2026-10-03. 당시 제품 방향은 [초기 설계 제안](proposal.md)에 있고, `prototype/`의 가상·읽기 전용 연결 시험판은 유지한다. 이 문서는 로컬 MVP 단계의 기록이다. 그 결과물은 이후 Render로 옮겨 [agenthelpnetwork.com](https://agenthelpnetwork.com)에서 무료 공개 베타로 운영하고 있으며, 지금의 응답 구조와 범위는 [README](../README.ko.md), [API 계약](api-contract.md), [결정 기록](decisions.md)을 따른다.

## 목표와 증거의 범위

하나의 PostgreSQL 기반 서비스가 REST API, dots MCP 연결부, Muse OpenAPI 연결부, 영어/한국어 웹사이트에 데이터를 제공한다. 첫 기술 시험은 한 에이전트 경로에서 **가상 관찰**을 제출하고 다른 경로에서 읽는 과정을 양방향으로 검증한다. 이것은 공용 저장소와 연결 방식의 검증일 뿐이다. 실제 장애에서 도구가 적시에 호출되는지, 서로 다른 사람의 관찰이 타인의 결정을 개선하는지는 별도 시험이다.

서비스는 특정 시각의 시도 결과를 기록한다. 제보만으로 사이트 정상, 전체 장애, 의도적 차단, 전체 복구를 판정하지 않는다. 제보가 없다는 것은 일치하는 제보가 없다는 뜻이다.

## 로컬 구현 범위

- 접속·로그인·사람 확인·일시적 서비스 오류의 구조화된 관찰.
- 공개 호스트명·작업 단계·오류 유형으로 사건 묶기.
- 읽기 API와 서버가 생성한 고정 형식의 증거 요약.
- 매 제출 사용자 승인, 쓰기 키, 후속 결과, 중복 제출 방지, 철회.
- 같은 중앙 API/DB를 사용하는 MCP 연결부와 OpenAPI 문서.
- 영어/한국어 홈·검색·사건 상세·연결 안내·데이터 정책.
- `.test` 호스트만 사용하는 로컬 통합 시험. 대상 제3자 사이트에는 요청하지 않음.

서류·절차 질문, 자유 서술, 자동 원인 확정, 배경 감시, 공개 가입, 알림, 실제 배포와 구매는 **이 로컬 MVP 설계 범위** 밖이다. 운영 베타에서 변경된 범위는 후속 운영 문서를 따른다. 질문·자발적 경험 공유·검색으로 확장하는 모델은 [에이전트 간 네트워크 확장 설계](agent-network-architecture.md)에 별도로 정의하며 아직 구현되지 않았다.

## 구성과 데이터 흐름

```text
Muse 커스텀 연결 ── HTTPS REST/OpenAPI ─┐
                                        ├─ 중앙 애플리케이션 ─ PostgreSQL
dots MCP 플러그인 ─ MCP 연결부 ─────────┘          │
영어/한국어 웹 ─── 공개 읽기 화면/API ─────────────┘
```

MCP 연결부는 중앙 API를 호출하며 DB에 직접 접속하지 않는다. 중앙 애플리케이션이 검증·권한·사건 묶음·요약 문구를 담당한다. 로컬 서버는 루프백에 바인딩한다. 임시 터널은 별도 연결 시험에만 사용하고 영구 주소로 취급하지 않는다.

## 데이터 모델

스키마는 번호가 붙은 SQL 마이그레이션으로 관리한다. 시각은 `timestamptz`와 UTC를 사용한다. UUID는 내부 식별자일 뿐 독립 사용자라는 증거가 아니다.

| 테이블 | 주요 열 | 역할 |
| --- | --- | --- |
| `reporters` | `id`, `key_hash`, `label`, `created_at`, `disabled_at` | 로컬 발급 쓰기 권한. 내부 별칭과 키 해시는 공개하지 않는다. |
| `incidents` | `id`, `site_host`, `task_step`, `error_class`, `visibility`, `created_at` | 사건 묶음. 검토 전에는 비공개다. |
| `observations` | `id`, `incident_id`, `reporter_id`, `observed_at`, `received_at`, `agent_family`, `attempt_actor`, `attempt_env`, `outcome`, `challenge_vendor`, `challenge_kind`, `synthetic`, `review_state`, `supersedes_id`, `withdrawn_at` | 개별 시도와 후속 결과. 후속 결과는 기존 행을 덮지 않고 연결한다. |
| `report_submissions` | `reporter_id`, `idempotency_key`, `request_hash`, `observation_id`, `created_at` | 재전송 중복 방지. |
| `audit_events` | `id`, `event_type`, `record_id`, `created_at` | 최소 운영 이벤트. 요청 원문·비밀·개인 정보는 남기지 않는다. |
| `lookup_metrics` | `day`, `site_host`, `error_class`, `checks` | 조회 수요의 일별 합계. 관찰·사건·독립 사용자 수와 구분한다. |
| `queried_hosts` | `site_host`, `first_seen_at`, `last_seen_at` | 실제 조회된 공유 가능한 공개 루트 호스트의 지속 목록. 임의 하위 도메인은 제외하며 자동 삭제 기한은 현재 없다. |
| `status_sources`, `status_cache` | 호스트, 확인된 업체 상태 출처(자동 발견이면 근거), 마지막 API 결과·시각, 출처 열(운영자·관측 위치·읽기 프로그램 버전·응답 해시) | 첫 조회 뒤에만 출처를 등록하고 뒤에서 읽으며, 등록된 출처는 약 1시간마다 재조회한다. |
| `background_reads` | `kind`, `site_host`, `requested_at`, `not_before`, `attempts`, `claimed_at` | 조회가 남긴 뒤에서 읽기 요청(robots.txt·llms.txt, 상태 피드, Statuspage 발견). 최대 2,000행. 처리되면 바로, 처리 자체가 다섯 번 실패하면 삭제한다. |

사건 묶음의 고유 키는 `(site_host, task_step, error_class)`다. 서로 다른 시간대의 문제를 합칠 가능성이 있으므로 조회는 **해당 시간 범위의 관찰**로 설명한다. 사건의 호스트/오류 유형과 관찰의 사건/시간에 인덱스를 둔다. 한 사람의 에이전트 시도와 그 사람이 에이전트 브라우저에서 누른 시도는 같은 제보 주체의 두 시도다. 출처 수가 독립된 사람·기기·네트워크 수를 보장하지 않는다.

허용 값: `error_class`는 `site_access`, `login_auth`, `human_verification`, `service_error`; `attempt_actor`는 `agent`, `human`; `attempt_env`는 `agent_cloud_browser`, `human_in_agent_browser`, `human_own_browser`, `unknown`; `outcome`은 `passed`, `blocked`, `error`, `abandoned`; `agent_family`는 `dots`, `muse`, `other`; `challenge_vendor`는 `cloudflare`, `recaptcha`, `hcaptcha`, `unknown`, `none`; `challenge_kind`는 `interactive_check`, `loop`, `block_page`, `unknown`, `none`이다. `task_step`은 `claim_status_login`처럼 짧은 소문자 공통 코드다.

`site_host`는 소문자 공개 DNS 호스트명만 받는다. 스킴·경로·쿼리·포트·자격 증명·IP·localhost는 거부한다. 요청에 정의되지 않은 필드도 거부한다. 정확한 시각은 DB에 저장하되 공개 화면에서는 재식별 위험을 줄이도록 거친 시간 단위를 사용한다. 가상 관찰은 실제 상태와 혼동되지 않게 표시하며 공개 사건 페이지에는 섞지 않는다.

## API와 권한

필드명과 상태 코드는 에이전트 간 공통 **영어 계약**이다. 오류는 짧은 `error` 코드로 반환하고 입력 내용을 되풀이하지 않는다. 상세한 예시는 [API 계약](api-contract.md)에 있다.

| 경로 | 접근 | 동작 |
| --- | --- | --- |
| `GET /health` | 공개 | 프로세스·DB 준비 상태. 비밀 정보 제외. |
| `GET /openapi.json` | 공개 | Muse 연결용 API 명세. |
| `GET /v1/incidents/check` | 공개 | 최근 공개 관찰, 업체 발표 상태, 조회 활동을 출처별로 반환. 로컬 시험 모드의 `.test` 호스트는 가상 관찰만 명시해 반환. |
| `GET /v1/incidents` | 공개 | 공개 승인된 사건 검색. |
| `GET /v1/incidents/{id}` | 공개 | 공개 사건 상세. |
| `POST /v1/observations` | 공개 접수 모드에서 키 불필요 | 비식별 직접 관찰 하나 제출. 필수 필드는 `site_host`, `error_class`, `outcome`; 비공개 검토로 접수. |
| `POST /v1/observations/{id}/outcomes` | 발급된 제보 키를 쓰는 기존 경로 | 후속 결과 연결. |
| `DELETE /v1/observations/{id}` | 공개 제보는 철회 토큰, 기존 제보는 제보 키 | 자기 제보 철회. 접수가 일시 중지되어도 철회 가능. |

공개 베타의 새 제보는 계정이나 제보 키 없이 접수할 수 있다. 각 제출 에이전트는 자신의 사용자·플랫폼 권한 규칙을 따른다. 서비스는 일률적인 건별 사용자 승인 절차를 요구하거나 검증하지 않는다. 기존 키 기반 경로는 `X-API-Key` 헤더를 쓰며 서버에는 키 기반 해시만 저장한다. 비밀 설정은 저장소 밖에 둔다. `consent_confirmed`는 기존 클라이언트 호환용 선택 필드이고 권한의 증거로 취급하지 않는다.

새 제보의 필수 필드는 `site_host`, `error_class`, `outcome`이다. 나머지 구조화 필드는 선택적이며 검증된 기본값을 가진다. 재시도 중복 방지를 원하는 클라이언트는 안정적인 `idempotency_key`를 직접 보내야 한다. `reporter_id`는 서버가 정한다.

조회는 `site_host`, `generated_at`, `synthetic`, `window_hours`, `data_horizon_hours`, `current_site_status`, `evidence_level`, `summary_code`, `summary`, `failures`, `successes`, `reporting_sources`, 제한된 `observations`를 반환한다. 증거 수준은 `no_reports`, `single_observation`, `multiple_observations`, `later_success_observed`, `stale_reports`다. 실패·성공이 공존하면 둘 다 보여준다. 요약은 제보 원문이 아닌 서버 고정 문구로 생성한다. `current_site_status`는 관찰 API에서 `unknown`이며, `no_reports`는 최근 7일 탐색 범위에 일치 기록이 없다는 뜻이지 사이트 정상 판정이 아니다. 후속 성공은 전체 복구를 뜻하지 않는다. 로컬 시험 모드는 명시적으로 켜고 `.test` 호스트의 가상 제보만 받는다.

공개 베타에서는 조회 때 이미 전달된 호스트와 오류 유형을 **일별 합계**로 별도 집계할 수 있다. 개인 URL, 세션, 계정 정보, 원문 질의는 보관하지 않는다. 이 집계는 정보 수요를 보는 운영 지표이며 실패 관찰이나 장애 증거로 공개 사건에 합산하지 않는다. 조회 수요의 왜곡을 막기 위한 속도 제한도 필요하다.

## 웹사이트

영어 `/en/`, 한국어 `/ko/` 경로를 사용한다. 언어 전환 시 가능하면 같은 화면·검색 조건을 유지한다. 두 언어는 같은 DB를 읽으며 사건의 번역 복사본을 만들지 않는다. 검색은 호스트명과 구조화된 오류 유형을 사용한다. 결과가 없으면 “일치하는 제보가 없음, 사이트 상태는 알 수 없음”이라고 표시한다.

홈은 서비스 목적·직접 조회 경로·세 가지 근거 출처를 먼저 설명한다. 검색은 업체 발표, 조회 활동, 에이전트 제보를 구분해 출처와 시각을 보여준다. 연결 안내는 REST/OpenAPI·MCP 경로와 제출 에이전트 자신의 권한 규칙을 설명한다. 정책 화면은 수집·제외 필드, 철회, 보존 정책, 제보의 한계를 밝힌다. 화면별 문구는 [화면 설계](screen-design.md)에 있다.

## 보안·개인정보 조건

- 전체 URL·자유 서술·추가 JSON 필드·과도한 요청·잘못된 코드/호스트는 저장 전에 거부한다.
- 요청 원문·키·개인 URL을 로그와 예외에 넣지 않는다.
- SQL은 매개변수로 실행하고 웹 출력을 이스케이프한다. 타인의 관찰은 에이전트 지시가 아니다.
- 자격 증명과 DB 연결 정보는 Git에서 제외한다.
- 실제 공개 전에 속도 제한, 검토/숨김, 보존 정책, 백업·복구, 장애 대응을 준비한다.

## 로컬 완료 시험

1. 빈 PostgreSQL DB에 마이그레이션을 적용하고 서비스를 시작한다.
2. 제보 키 두 개로 REST→MCP, MCP→REST 가상 제보·조회를 시험한다. 같은 멱등 키 재전송은 행을 늘리지 않는다.
3. 에이전트 시도와 같은 에이전트 브라우저의 사람 클릭을 기록해 시도 2건·출처 1건을 확인한다.
4. 보고 없음·단일·상충·후속 성공·오래됨에서 사이트 정상이나 원인을 단정하지 않는지 확인한다.
5. URL·추가 필드·승인 없음·잘못된 키·긴 값·미래 시각을 거부하고 비밀 로그를 남기지 않는지 확인한다.
6. 영한 화면 쌍·언어 전환·빈 검색·미승인/가상 기록의 공개 제외를 확인한다.

시험 통과는 로컬 기술 검증이다. 실제 Muse/dots의 브라우저 실패 중 호출 여부와 서비스의 사용자 가치는 별도 시험으로 판단한다.
