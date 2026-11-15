# Policy Traceability & Migration Matrix (정책 이관 및 추적성 매트릭스)

본 문서는 선행 작업영역(`c:\train2`)에 파편화되어 있던 기존 5대 정책 문서 및 전역 규칙을 분석하여, 신규 3계층 통합 거버넌스 체계로 재편·이관한 공식 추적성 원장(Traceability Ledger)이다.

---

## 1. 종합 이관 매핑 테이블

| 기존 소스 문서 및 조항 | 요구사항 ID | 신규 반영 위치 | 검증 방법 | 상태 및 사유 |
| :--- | :--- | :--- | :--- | :--- |
| `quota-governance-and-wbs.md` §1 (스케줄링 제한) | `REQ-QG-01` | 01-wbs-quota-governance.md §1 | 크론 등록 인터셉트 검사 | **유지/강화**: 5분 이하 고빈도 스케줄러 금지 및 캐시 토큰 비대화 방지 |
| `quota-governance-and-wbs.md` §2 (WBS 게이트) | `REQ-QG-02` | 01-wbs-quota-governance.md §2-3, WBS.md | WBS 상태 전이 게이트 검사 | **개편/강화**: 단순 원칙 선언에서 `WBS.md` 상태 원장과 표준 패키징 계약(8대 필드)으로 구체화 |
| `quota-governance-and-wbs.md` §3 (타겟 테스트 격리) | `REQ-QG-03` | 01-wbs-quota-governance.md §4 | CLI 명령어 인자 검사 | **유지**: 무인자 `pytest` 금지, 서브패키지 타겟팅 강제 |
| `quota-governance-and-wbs.md` §4 (작업 공간 지정) | `REQ-QG-04` | 01-wbs-quota-governance.md §5 | 디렉터리 경로 격리 확인 | **유지**: 전용 공간 격리 및 SSOT 분리 명시 |
| `astra-preflight-gate.md` §1-2 (사전 검수 및 보고) | `REQ-PG-01` | 02-preflight-audit-gate.md §1, PROJECT.md | Coarena 사전 감사 실행 | **유지/강화**: 실행자-감사자 책임 분리 및 자기승인 원천 금지 명문화 |
| `astra-preflight-gate.md` §3 (2진 판정 원칙) | `REQ-PG-02` | 02-preflight-audit-gate.md §2 | 감사관 판정 토큰 검사 | **유지**: 오직 `[통과]` / `[반려]`만 허용 (조건부 승인/유보 즉시 무효화) |
| `astra-preflight-gate.md` §4 (불변식 & SHA-256) | `REQ-PG-03` | 02-preflight-audit-gate.md §3 | SHA-256 해시 대조 및 불변식 테스트 | **일반화/강화**: 특정 비디오 검증을 범용 시스템의 비면제 데이터 불변식 및 암호학적 해시 검증으로 확장 |
| `astra-preflight-gate.md` §5 (동등 권한 협력) | `REQ-PG-04` | 02-preflight-audit-gate.md §4 | 결함 재검수 루프 검증 | **개편/강화**: 모호한 협력을 명확한 4단계 워크플로우로 재정의 |
| `LEARNINGS.md` §1 (눈먼 장인 안티패턴) | `REQ-AI-01` | 03-anti-fabrication-visual-integrity.md §1 | 육안 산출물 확인 로그 검증 | **유지/강화**: 수치 지표 만능주의 배제 및 실제 산출물 전수 검사 의무화 |
| `LEARNINGS.md` §2 & `RULE[user_global]` A | `REQ-AI-02` | 03-anti-fabrication-visual-integrity.md §2 | 텍스트 선행 관측 여부 감사 | **유지**: Describe-THEN-Label 원칙 (관측 전 라벨 부여 금지) |
| `RULE[user_global]` B (정직한 심각도 평가) | `REQ-AI-03` | 03-anti-fabrication-visual-integrity.md §3 | 버그 분류 기준 교차 검토 | **유지**: CRITICAL/MAJOR/MINOR 엄격 분류 및 임의 축소 보고 금지 |
| `RULE[user_global]` C (모순 자가 검증) | `REQ-AI-04` | 03-anti-fabrication-visual-integrity.md §4 | 주장-증거 1:1 대조 리포트 | **유지**: 보고서 주장과 실제 데이터 1:1 대조 의무화 |
| `game-mechanics-grounding.md` (도메인 고증) | `REQ-DOM-01` | 도메인별 부속 사양서 (`specs/`)로 격리 | 정품 노드 파싱 검증 | **분리/보존**: 코어 거버넌스 규칙에서 분리하여 도메인 작업 시 `specs/`로 주입 |
| `productization-lifecycle-and-airgap.md` (에어갭) | `REQ-SEC-01` | 02-preflight-audit-gate.md §3 및 배포 스펙 | 제로 백엔드/보안 감사 | **분리/보존**: 핵심 안전 가치(Fail-Closed, 무서버 원칙)는 코어에 흡수 |
| `sol-developer-sandbox-vm.md` (VM 샌드박스) | `REQ-ENV-01` | 환경 구성 가이드 (`docs/`) | 환경 헬스체크 스크립트 | **분리**: 인프라 종속적 VM 셋업 매뉴얼이므로 코어 정책에서 분리 보관 |

---

## 2. 불변식 보존 서약 (Invariant Conservation Pledge)
본 리팩토링은 기존 작업영역에서 피땀 흘려 정립된 **안전 통제와 검증 규율의 단 하나도 묵시적으로 완화(Silent Relaxation)하거나 무단 폐기하지 않았음**을 보증한다.
