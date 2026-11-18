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

---

### [WBS-06] KTOS(Kivotos Tactical Operations System) UI/UX 고도화 및 검수 센터 엔진 구축

* **목표 및 범위**: Day/Night Ops 듀얼 테마 엔진(CSS 변수 및 테마 토글 영속화), 뷰포트 HUD 4각 광학 L-브래킷 레티클 및 캔버스 모노스페이스 텔레메트리 바운딩 박스, 검수 센터 변환 행렬(Pan/Zoom 및 역변환 매핑) 및 전체 키보드 단축키 트리아지 엔진([A]/[R]/[P]/[[/]]/[1-8]/[0]) 구축.
* **선행 의존성**: WBS-05 [PASS]
* **담당자**: 실행자 (Antigravity Coordinator) / 감사자 (Coarena Advisor `gpt-6-astra`)
* **산출물**:
  - `static/index.html`
  - `static/app.js`
  - `static/reviewer.js`
  - `WBS.md`
* **인수 기준**:
  1. Day Ops (라이트) 및 Night Ops (다크) 테마 전환 및 `localStorage` 영속화 100% 정상 작동.
  2. 뷰포트 4각 광학 L-브래킷 레티클 및 챔퍼 코너 마크 + 텔레메트리 뱃지(`${className} [${conf}%] ${w}x${h}`) 렌더링.
  3. 검수 센터 캔버스 Pan/Zoom 변환 행렬(`this.view = { scale, panX, panY }`) 및 커서 중심 휠 줌, 스페이스 드래그 팬, 역변환 좌표 매핑 동기화.
  4. 단축키 트리아지 엔진(`[A]`, `[R]`, `[P]`, `[`, `]`, `[1]`-`[8]`, `[0]`, `Delete`/`Backspace`) 100% 정상 작동.
  5. 69개 전체 단위/통합 테스트 100% 무회귀 PASS 및 JS 구문 검사(`node -c`) 통과.
  6. WBS 게이트 유효성 검사기(`wbs_gate_validator.py`) 및 `run.py status` 100% PASS.
* **감사 판정**: `[PASS]`
* **롤백 전략**: `git checkout HEAD -- static/ WBS.md`

---

### [WBS-07] 상용 수준 원클릭 런처 체계 및 비개발자 배포 거버넌스 수립

* **목표 및 범위**: `src/server.py` 직접 실행 시의 `ModuleNotFoundError` 근본 원인 해결(프로젝트 루트 `sys.path` 자동 주입 및 main 진입점 구축), 비개발자 대상의 원클릭 더블클릭 런처(`실행하기.bat`, `종료하기.bat`, `바탕화면_바로가기_만들기.bat`, `백그라운드_실행하기.vbs`), PID 추적 기반의 안전한 타겟 프로세스 라이프사이클 관리기(`scripts/launcher.py`), 친절한 사용자 설명서(`사용방법.md`) 및 거버넌스 불변식 검증 테스트 스위트 구축.
* **선행 의존성**: WBS-06 [PASS]
* **담당자**: 개발자 (Antigravity Coordinator Gemini) / PM 어드바이저 (Coarena GPT-6 Luna)
* **산출물**:
  - `src/server.py`
  - `run.py`
  - `scripts/launcher.py`
  - `실행하기.bat`
  - `종료하기.bat`
  - `바탕화면_바로가기_만들기.bat`
  - `백그라운드_실행하기.vbs`
  - `requirements.txt`
  - `사용방법.md`
  - `tests/test_launcher_governance.py`
  - `WBS.md`
* **인수 기준**:
  1. `python src/server.py` 직접 실행 시 `ModuleNotFoundError` 발생하지 않고 정상 기동 입증.
  2. `실행하기.bat` 더블클릭 시 Python 환경 점검, 포트 충돌 방지, 백그라운드 헬스체크 확인 후 브라우저(`http://127.0.0.1:8000`) 자동 오픈 100% 정상 작동.
  3. 무차별 `taskkill /IM python.exe` 전면 금지 및 `.server.pid` 기반의 안전한 타겟 프로세스 종료 100% 보장.
  4. 실패 숨김 금지: 오류 발생 시 즉시 콘솔에 원인 명시 및 `logs/server.log` 경로 안내 후 사용자 일시정지(`pause`).
  5. 신규 7종 런처 거버넌스 단위 테스트 100% PASS 및 기존 전체 테스트 무회귀 PASS.
  6. WBS 게이트 유효성 검사기(`python scripts/wbs_gate_validator.py`) 100% 통과.
* **감사 판정**: `[PASS]`
* **롤백 전략**: `git checkout HEAD -- src/server.py run.py WBS.md; rm 실행하기.bat 종료하기.bat 바탕화면_바로가기_만들기.bat 백그라운드_실행하기.vbs 사용방법.md scripts/launcher.py tests/test_launcher_governance.py`

---

### [WBS-08] 저장된 이전 세션 데이터 검수 및 영속화 관리 체계 구축

* **목표 및 범위**: 사용자 요구사항("저장된 이전 데이터 검수도 가능하게끔해줘")을 충족하기 위한 세션 영속화 매니저(`src/session_manager.py`), REST API 엔드포인트(`GET/POST/DELETE /api/sessions`), 프런트엔드 이전 세션 선택 및 불러오기 모달(`static/index.html`, `static/reviewer.js`, `static/app.js`), 검수 판정 실시간 영속 저장, 디스크 기반 YOLO 내보내기 자동 fallback, Fail-Closed 보안 검증(경로 순회 차단, 0바이트 이미지 거부, 바운딩 박스 유효성 검증) 및 종합 테스트 스위트 구축.
* **선행 의존성**: WBS-07 [PASS]
* **담당자**: 개발자 (Antigravity Coordinator Gemini) / PM 어드바이저 (Coarena GPT-6 Luna)
* **산출물**:
  - `src/session_manager.py`
  - `src/config.py`
  - `src/server.py`
  - `static/index.html`
  - `static/reviewer.js`
  - `static/app.js`
  - `tests/test_session_storage.py`
  - `WBS.md`
* **인수 기준**:
  1. `SessionManager` 핵심 메서드 및 `/api/sessions` 전 엔드포인트 100% 정상 작동.
  2. 악의적 경로 순회 공격(`..`, 슬래시, 널 바이트 등) 및 0바이트 손상 이미지 시스템적 차단(Fail-Closed).
  3. 프런트엔드 검수 센터 내 '이전 세션 불러오기' 모달 및 세션 목록(세션 ID, 프레임 수, 승인/대기/제외 통계, 일시) 렌더링 정상 작동.
  4. 이전 세션 선택 시 캔버스 및 라벨 데이터 즉시 로드 및 트리아지 검수/수정 가능.
  5. 세션 데이터 수정 후 서버 영속 저장(`POST /api/sessions/save`) 및 YOLO 데이터셋 ZIP 다운로드 정상 작동.
  6. 스트리밍 스냅샷 수집 시 세션 자동 영속화로 이전 세션 목록에서 즉시 검색 가능.
  7. 신규 53종 세션 스토리지 테스트 포함 전체 139개 테스트 100% PASS 및 무회귀 입증.
  8. WBS 게이트 유효성 검사기(`python scripts/wbs_gate_validator.py`) 100% 통과.
* **감사 판정**: `[AUDIT_PENDING]`
* **롤백 전략**: `git checkout HEAD -- src/config.py src/server.py static/ WBS.md; rm src/session_manager.py tests/test_session_storage.py`



