# WBS Quota Governance & Execution Discipline (할당량 거버넌스 및 WBS 실행 규율)

## 1. 할당량 보존 및 스케줄링 제한 (Quota Conservation)
- **고빈도 스케줄링 엄금**: 사용자의 명시적 지시가 없는 한 5분 이하의 고빈도 자동 크론(`schedule`) 스케줄링 등록을 일절 금지한다.
- **캐시 토큰 비대화 차단**: 백그라운드 자동 기상에 의한 무한 루프 및 캐시 토큰 비대화(수십억 토큰 낭비)를 원천 차단한다.
- **수동 검증 루프**: 자동 반복 대신 명확한 WBS 패키지 완성 보고 후 사용자 및 감사관의 승인 루프를 거쳐 다음 작업으로 진행한다.

## 2. 순차적 WBS 게이트 방식 (Sequential WBS Gates)
- **독립 작업 패키지화**: 모든 대규모/다단계 시스템 구축은 독립적이고 검증 가능한 작업 패키지(WBS) 단위로 분할하여 착수한다.
- **선행 패키지 완료 필수 (Blocking Gate)**: 선행 패키지가 사전에 정의된 단위 테스트 인수 기준(100% PASS) 및 감사관의 `[통과]` 판정을 획득하기 전에는 어떠한 후속 패키지도 착수할 수 없다.
- **계약 정합성 유지**: 하위 패키지 구현 시 상위 아키텍처 명세서 및 데이터 계약(Contracts)의 정합성을 엄격히 유지하며, 스키마 변경 시 영향받는 전 패키지의 회귀 검증을 선행한다.

## 3. 표준 WBS 패키징 계약 및 상태 전이 (Packaging Contract & State Machine)
모든 WBS 작업 항목은 [`WBS.md`](file:///WBS.md)에 등록되어 관리되며, 다음 표준 계약 필드를 갖추어야 한다:
- **필수 필드**: `WBS-ID`, `목표 및 범위`, `선행 의존성`, `담당자(실행자/감사자)`, `산출물 경로`, `인수 기준(Acceptance Criteria)`, `감사 판정`, `롤백 전략`
- **상태 전이 라이프사이클**:
  $$\text{PLANNED} \longrightarrow \text{IN\_PROGRESS} \longrightarrow \text{AUDIT\_PENDING} \longrightarrow [\text{PASS}] \mid [\text{REJECT}]$$
  - `[REJECT]` 시: 결함 사유 명시 $\rightarrow$ 결함 수정 $\rightarrow$ `AUDIT_PENDING` 재신청.
  - 사후 변경 시: 이미 `[PASS]`된 패키지의 산출물이 수정된 경우, 상태를 즉시 `AUDIT_PENDING`으로 복귀시키고 영향 범위 재감사를 강제한다.

## 4. 테스트 스위트 격리 및 무인자 실행 금지 (Targeted Test Execution)
- **무인자 pytest 엄격 금지**: 프로젝트 루트 전체를 무분별하게 탐색하는 무인자 `pytest` 명령을 금지한다.
- **타겟 경로 명시 의무**: 반드시 현재 작업 중인 패키지의 테스트 경로(예: `pytest tests/test_wbs1_*.py`)를 명시하거나, `pyproject.toml`에 testpaths를 한정하여 무관한 레거시 테스트 수집 실패 및 토큰 낭비를 차단한다.

## 5. 작업 공간 격리 및 단일 진실 공급원 (Workspace & SSOT Separation)
- **SSOT 역할 분리**:
  - `PROJECT.md`: 전체 시스템 구조, 아키텍처, 정책 및 절차의 단일 진입점(SSOT).
  - `WBS.md`: 진행 상황, 상태 전이, 검증 증거의 유일한 진실 공급원(SSOT).
- **작업 데이터 격리**: 사용자가 지정한 전용 작업영역 내에서만 소스 및 산출물을 배치하며, 타 프로젝트 디렉터리를 직접 오염시키지 않는다.
