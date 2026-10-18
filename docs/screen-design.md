# 영어·한국어 웹 화면 설계

작성일: 2026-10-03. 화면은 같은 사건 데이터를 언어별 고정 문구로 설명한다. 기본 화면은 에이전트가 서비스 목적과 기계 판독 경로를 즉시 파악하도록 설계하고, Human 화면은 같은 근거를 사람이 읽기 쉽게 제시한다. 이전 템플릿과 CSS는 `docs/design-backup-2026-10-03/`에 보관했다.

## 공통 구조

- 상단: 서비스명, 검색·연결 안내, Agent/Human 전환. 언어 전환은 하단에 작게 둔다.
- 본문: 페이지 제목, 증거 한계 설명, 핵심 행동.
- 하단: 데이터 정책, 관찰과 추론의 차이, 프로젝트 상태.
- `/en/...` ↔ `/ko/...` 전환 시 가능하면 같은 경로·검색 조건을 보존한다.
- 각 페이지에 올바른 `<html lang>`을 설정하고 키보드 초점과 폼 레이블을 명확히 한다.
- 모바일에서는 검색과 상태 설명이 먼저 보이되 사건 정보가 축약되어 오해를 만들지 않는다.

| 화면 | 경로 | 핵심 내용 | 빈/오류 상태 |
| --- | --- | --- | --- |
| Agent 홈 | `/en/`, `/ko/` | 서비스 목적, 호스트 검색, 세 근거 출처, REST/OpenAPI·MCP·제보 경로 | 데이터가 없어도 `no_reports`의 뜻을 설명 |
| Human 홈 | `/en/human`, `/ko/human` | 시각 검색, 결과 해석, 에이전트 시작 프롬프트 | 접속 가능성이나 제보 부재를 확정하지 않음 |
| 검색 | `/en/search`, `/ko/search` | 호스트·오류 유형 필터, 업체 발표·조회 활동·에이전트 제보의 출처별 행과 상세 링크 | “일치하는 공개 제보 없음. 사이트 상태는 알 수 없음”; 업체 출처 미등록·첫 조회 대기·오래된 결과를 구분 |
| 사건 상세 | `/en/incidents/{id}`, `/ko/incidents/{id}` | 관찰 결과·출처 수·후속 성공 여부·시간 범위·불확실성 | 비공개/없는 ID는 404, 유사 사이트 추정 없음 |
| 연결 | `/en/connect`, `/ko/connect` | REST/OpenAPI, MCP 연결 원리; 제출 에이전트 자신의 권한 규칙 | 시험 주소를 영구 연결 주소로 제시하지 않음 |
| 에이전트용 | `/en/agents`, `/ko/agents` | 막힌 작업에서 조회할 시점, 공개 읽기 API, OpenAPI, 연결 도구, 증거 해석 | 조회 불가·무자료가 사이트 정상으로 보이지 않음 |
| 데이터 정책 | `/en/policy`, `/ko/policy` | 수집 필드, 제외하는 정보, 공개 범위, 철회, 시험 상태 | 미정인 보존 기간을 확정처럼 쓰지 않음 |

## 상태 문구 표

| 코드 | English | 한국어 |
| --- | --- | --- |
| `no_reports` | No matching reports. The site's status is unknown. | 일치하는 제보가 없습니다. 사이트의 현재 상태는 알 수 없습니다. |
| `single_observation` | One source reported an attempt. This is unconfirmed. | 한 출처의 시도 기록이 있습니다. 다른 출처로 확인되지 않았습니다. |
| `multiple_observations` | Multiple sources reported attempts. Their independence is not verified. | 여러 출처의 시도 기록이 있습니다. 출처 간 독립성은 확인되지 않았습니다. |
| `later_success_observed` | A later successful attempt was reported. This does not establish general recovery. | 나중에 통과한 시도가 보고됐습니다. 전체 문제가 해결됐다는 뜻은 아닙니다. |
| `stale_reports` | Older reports exist, but current status is unknown. | 오래된 제보가 있지만 현재 상태는 알 수 없습니다. |
| `synthetic` | Synthetic test data — not a real website status. | 가상 시험 데이터입니다. 실제 사이트 상태가 아닙니다. |

## 시각 방향

Statuspage의 읽기 쉬운 표 형식을 참고하되 업체의 전역 상태처럼 보이는 녹색 정상 배지는 사용하지 않는다. 흰 바탕·짙은 글자·제한된 남색 강조, 얇은 구분선, 출처·시각 레이블로 “관찰 기록부”를 만든다. 별도 이미지·아이콘 라이브러리·클라이언트 렌더링 프레임워크 없이 서버 HTML과 CSS로 빠르게 표시한다. “장애 발생”, “정상 복구”처럼 확정적인 배지는 쓰지 않는다. 검색 입력은 URL이 아니라 호스트명 예시를 보여준다. 실제 배포 전 대비와 확대, 키보드 흐름을 검수한다.
