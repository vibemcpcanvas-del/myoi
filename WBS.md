# Work Breakdown Structure (WBS) State Ledger

본 문서는 [`myoi`](file:///WBS.md) 프로젝트의 진행 상태, 인수 기준, 감사 판정 결과를 관리하는 **유일한 진실 공급원(SSOT) 상태 원장**이다.

---

## 1. WBS 상태 전이 라이프사이클 및 게이트 규칙

```
[PLANNED] ---> [IN_PROGRESS] ---> [AUDIT_PENDING] ---> [PASS] (Gate Open)
                                         |
                                         +------------> [REJECT] (Gate Closed -> Defect Fix Loop)
```

1. **엄격한 2진 판정**: 판정 필드는 오직 `[PASS]` 또는 `[REJECT]` 단 두 가지만 허용되며, 조건부 승인은 무효 처리한다.
2. **선행 게이트 차단 (Blocking Gate)**: 선행 WBS의 `[PASS]` 평결 없이는 후속 WBS의 착수(`IN_PROGRESS`)가 시스템적으로 차단된다.
3. **사후 무결성 보존**: `[PASS]` 이후 산출물이 수정된 경우 상태는 즉시 `AUDIT_PENDING`으로 복귀하며 영향 범위 재감사를 거쳐야 한다.

---

## 2. WBS 마스터 원장 (Master Ledger)

### [WBS-00] 코어 WBS 거버넌스 및 정책 리팩토링 체계 수립

* **목표 및 범위**: WBS 통합 거버넌스 규칙, WBS 상태 원장, SSOT 아키텍처, 기계적 테스트 차단 훅 및 부정 테스트 구축.
* **선행 의존성**: 없음 (최초 부트스트랩)
* **담당자**: 실행자 (Antigravity Coordinator) / 감사자 (Coarena Advisor `gpt-6-astra`)
* **할당량 예산**: 캐시 토큰 보존, 5분 이하 크론 0건, 단일 감사 턴 완료
* **산출물**:
  - `.agents/rules/01-wbs-quota-governance.md`
  - `.agents/rules/02-preflight-audit-gate.md`
  - `.agents/rules/03-anti-fabrication-visual-integrity.md`
  - `.agents/rules/04-policy-traceability-matrix.md`
  - `.agents/hooks.json`
  - `PROJECT.md`
  - `WBS.md`
  - `.gitignore`
* **인수 기준**:
  1. 기존 거버넌스 요구사항 100% 매핑 보존.
  2. 실행자-감사자 분리 및 2진 판정(`[PASS]`/`[REJECT]`) 명문화.
  3. 무인자 pytest 및 WBS 조기전이 기계적 차단 입증.
  4. 13개 전체 단위/부정 테스트 100% PASS.
* **검증 증거**:
  - Git 초기화 및 파일 시스템 전수 점검 로그
  - 단위 및 부정 테스트 13종 100% 통과 콘솔 로그
* **감사 판정**: `[PASS]` (초기화 엔진 인증 완료)
* **롤백 전략**: `git reset --hard` 및 디렉터리 원상 복원

---

### [WBS-01] 프로젝트 코어 엔진 및 단일 실행 러너 부트스트랩 (예정)

* **목표 및 범위**: `myoi` 전용 단일 엔트리포인트(`run.py`), 설정 관리, 로컬 환경 헬스체크 및 표준 패키지 구조 초기화.
* **선행 의존성**: WBS-00 [PASS]
* **담당자**: 배정 예정
* **산출물**: `run.py`, `pyproject.toml`, `src/`, `tests/test_runner.py`
* **인수 기준**: `python run.py status` 정상 실행, 단위 테스트 100% PASS
* **감사 판정**: `[PLANNED]` (선행 게이트 개방 대기)
* **롤백 전략**: `git checkout WBS-00-PASS` 태그로 롤백
