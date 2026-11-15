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

### [WBS-01] 보안 프록시 백엔드 및 단일 실행 러너 구축

* **목표 및 범위**: 보안 프록시 API(`POST /api/predict`), 서버 환경변수 기반 키 관리, 단일 진입점 러너(`run.py`), 업스트림 통신 및 오류 격리 구축.
* **선행 의존성**: WBS-00 [PASS]
* **담당자**: 실행자 (Antigravity Coordinator) / 감사자 (Coarena Advisor)
* **산출물**: `run.py`, `src/server.py`, `src/config.py`, `tests/test_proxy_server.py`
* **인수 기준**: 프런트엔드 비밀키 완전 제거, 서버 프록시 경유 추론 성공, 타임아웃 및 입력 검증, 단위 테스트 100% PASS.
* **감사 판정**: `[PASS]`
* **롤백 전략**: `git checkout HEAD -- run.py src/ tests/test_proxy_server.py`

---

### [WBS-02] 종횡비 보존 Letterbox 및 좌표 변환 어댑터 구현

* **목표 및 범위**: 원본 해상도 보존 Letterbox 변환기, 다중 좌표계(`letterbox_640`, `original`, `normalized`, `unknown`) 어댑터 및 경계 제한·검증 로직 구현.
* **선행 의존성**: WBS-01 [PASS]
* **담당자**: 실행자 (Antigravity Coordinator) / 감사자 (Coarena Advisor)
* **산출물**: `src/coordinate_adapter.py`, `tests/test_coordinate_adapter.py`
* **인수 기준**: 1920x1080, 640x480, 1080x1920 비율 보존, 역변환 픽셀 정확도 검증, unknown 좌표계 차단, 단위 테스트 100% PASS.
* **감사 판정**: `[PASS]`
* **롤백 전략**: `git checkout HEAD -- src/coordinate_adapter.py tests/test_coordinate_adapter.py`

---

### [WBS-03] SCHALE 스트리밍 스냅샷 수집·검수·YOLO 내보내기 웹앱 구현

* **목표 및 범위**: SCHALE 블루아카이브 스타일 UI 개선, 화면 잘림 방지 `contain` 뷰포트, 스냅샷 수집 트리거 및 쿨다운, 인터랙티브 검수 에디터, Ultralytics YOLO detect ZIP 패키징.
* **선행 의존성**: WBS-02 [PASS]
* **담당자**: 실행자 (Antigravity Coordinator) / 감사자 (Coarena Advisor)
* **산출물**: `static/index.html`, `static/app.js`, `static/coordinate_adapter.js`, `static/reviewer.js`, `src/yolo_exporter.py`
* **인수 기준**: 기존 기능(화면공유, 비프, TTS 등) 무회귀, 원본 해상도 스냅샷 보존, 검수 미완료/제외 항목 ZIP 누락 차단, 세션 단위 split.
* **감사 판정**: `[PASS]`
* **롤백 전략**: `git checkout HEAD -- static/ src/yolo_exporter.py`

---

### [WBS-04] 15대 필수 요구사항 검증 테스트 스위트 및 불변식 검증

* **목표 및 범위**: 명세서에 규정된 15대 필수 테스트 항목 자동화 구현, 거버넌스 불변식 및 Fail-Closed 검증, 전수 PASS 확인.
* **선행 의존성**: WBS-03 [PASS]
* **담당자**: 실행자 (Antigravity Coordinator) / 감사자 (Coarena Advisor)
* **산출물**: `tests/test_yolo_export.py`, `tests/test_collection_lifecycle.py`, `docs/VERIFICATION_REPORT.md`
* **인수 기준**: 15대 필수 테스트 전 항목 100% PASS, 거버넌스 불변식 100% PASS, 실제 산출물 전수 육안/해시 검증.
* **감사 판정**: `[PASS]`
* **롤백 전략**: `git checkout HEAD -- tests/ docs/`

---

### [WBS-05] AOP 횡단 관심사 고도화 및 아키텍처 디커플링

* **목표 및 범위**: AOP(Aspect-Oriented Programming) 원칙을 적용하여 5대 횡단 관심사(비밀값 마스킹/정화, Fail-Closed 서킷 브레이커 및 백오프 정책, 레이턴시/헬스 텔레메트리, 좌표 및 경계 불변식 검증 게이트, 감사 추적 및 매니페스트 정화)를 백엔드와 프런트엔드 전반에서 깔끔하게 분리하고 고도화.
* **선행 의존성**: WBS-04 [PASS]
* **담당자**: 실행자 (Antigravity Coordinator) / 감사자 (Coarena Advisor `gpt-6-astra`)
* **산출물**:
  - `src/aspects/__init__.py`
  - `src/aspects/base.py`
  - `src/aspects/redaction.py`
  - `src/aspects/circuit_breaker.py`
  - `src/aspects/telemetry.py`
  - `src/aspects/invariants.py`
  - `src/aspects/audit.py`
  - `static/aspects.js`
  - `tests/test_aop_aspects.py`
* **인수 기준**:
  1. 기존 49개 단위 및 통합 테스트 100% 무회귀 PASS.
  2. 신규 5대 AOP 횡단 관심사 전용 테스트 17종 100% PASS.
  3. 프런트엔드 `static/aspects.js` Node.js 런타임 검증 100% PASS.
  4. Fail-Closed 서킷 브레이커, 비밀값 정화, 실시간 텔레메트리, 좌표계 차단 및 감사 추적 입증.
  5. WBS 게이트 유효성 검사기(`wbs_gate_validator.py`) 100% PASS.
* **감사 판정**: `[PASS]`
* **롤백 전략**: `git checkout HEAD -- src/ static/ tests/ WBS.md`
