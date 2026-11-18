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
* **감사 판정**: `[PASS]`
* **롤백 전략**: `git checkout HEAD -- src/config.py src/server.py static/ WBS.md; rm src/session_manager.py tests/test_session_storage.py`

---

### [WBS-09] 네이티브 WGC 캡처 사이드카 및 스트리밍 화면 은폐 탐지 무결성 엔진 구축

* **목표 및 범위**: 브라우저 WebRTC 프레임 프리징 및 모달 멈춤을 근본적으로 해결하기 위한 out-of-process 네이티브 WGC/DWM 캡처 사이드카(`src/capture/`), 단일 타겟 게임창/프로세스/데스크톱 캡처 엔진, 안티치트 안전 불변식(Vanguard/GameGuard/EAC 호환 Zero DLL Injection), 로컬 고속 스트리밍(`/ws/stream`, `/api/stream`, `/api/capture/*`), KTOS 대시보드 캡처 엔진 선택기 및 원클릭 런처 연동, 그리고 "스트리밍 화면 가리기" 체크 시 비디오 디코더 정지 및 빈 프레임 전송으로 인한 탐지 실패 버그 근본 해결(시각적 전술 은폐 오버레이 `#streamConcealOverlay` 및 연속 60 FPS 디코딩 보존).
* **선행 의존성**: WBS-08 [PASS]
* **담당자**: 개발자 (Antigravity Coordinator Gemini) / PM 어드바이저 (Coarena GPT-6 Luna)
* **산출물**:
  - `src/capture/__init__.py`
  - `src/capture/window_finder.py`
  - `src/capture/wgc_capture.py`
  - `src/capture/service.py`
  - `src/server.py`
  - `static/index.html`
  - `static/app.js`
  - `run.py`
  - `tests/test_native_wgc_capture.py`
  - `tests/test_stream_concealment_bug.py`
  - `WBS.md`
* **인수 기준**:
  1. 스트리밍 화면 가리기 활성화 시 `opacity: 0`, `display: none` 등 비디오 디코더를 중단시키는 DOM 조작이 완전히 제거되고 전술 차단 쉴드 오버레이가 활성화되어 화면은 은폐되면서 AI 탐지 및 캡처는 100% 정상 작동 입증.
  2. Zero DLL Injection 불변식 준수: 타겟 프로세스에 대한 원격 스레드/DLL 주입 일절 없이 안전한 DWM 아웃오브프로세스 캡처 보증.
  3. `src/capture/` 모듈 및 REST/WebSocket API(`/api/capture/*`, `/api/stream`, `/ws/stream`) 100% 정상 작동.
  4. 브라우저 WebRTC 모드와 네이티브 WGC 모드 간 전환 및 하위 호환성 100% 유지.
  5. 신규 20종 단위/통합 테스트 포함 전체 159개 테스트 100% PASS 및 무회귀 입증.
  6. WBS 게이트 유효성 검사기(`python scripts/wbs_gate_validator.py`) 100% 통과.
* **감사 판정**: `[PASS]`
* **롤백 전략**: `git checkout HEAD -- src/ static/ run.py WBS.md; rm src/capture/ tests/test_native_wgc_capture.py tests/test_stream_concealment_bug.py`

---

### [WBS-10] 런처 의존성 자가 치유(Self-Healing) 및 프런트엔드 멀티 프로토콜 폴백 & 서킷 브레이커 구축

* **목표 및 범위**: uvicorn 구동 시 WebSocket 라이브러리(`websockets`) 누락으로 인한 404 거부 및 브라우저 무한 재연결 콘솔 플러드 근본 해결. `websockets>=12.0` 의존성 명시 선언, 원클릭 런처(`실행하기.bat`, `scripts/launcher.py`) 사전 검사 시 누락 패키지 자동 설치(Self-Healing), 프런트엔드(`static/app.js`) WebSocket 연결 실패 시 지수 백오프 및 서킷 브레이커(최대 3회 재시도 후 OPEN) 적용, 그리고 HTTP MJPEG 스트림(`/api/stream`) 및 프레임 폴링(`/api/capture/frame`)으로의 끊김 없는 무중단 자동 폴백 구축.
* **선행 의존성**: WBS-09 [PASS]
* **담당자**: 개발자 (Antigravity Coordinator Gemini) / PM 어드바이저 (Coarena GPT-6 Luna)
* **산출물**:
  - `requirements.txt`
  - `scripts/launcher.py`
  - `실행하기.bat`
  - `src/server.py`
  - `static/app.js`
  - `tests/test_stream_fallback_and_self_healing.py`
  - `WBS.md`
* **인수 기준**:
  1. `requirements.txt`에 `websockets>=12.0` 명시 선언 및 Python 환경 설치 완료.
  2. `scripts/launcher.py` 환경 점검 시 `websockets` 부재 시 pip 자동 설치 및 자가 치유(Self-Healing) 정상 작동 입증.
  3. `실행하기.bat` 사전 실행 점검에서 `import websockets` 누락 시 자동 설치 및 서버 정상 기동 입증.
  4. 프런트엔드 WebSocket 실패 시 서버 무한 폭격 차단: 지수 백오프 및 3회 초과 시 서킷 브레이커 OPEN 전이 입증.
  5. WebSocket 미지원/404 시 HTTP 스트림 프로토콜(`/api/capture/frame` / `/api/stream`)으로 자동 전환되어 화면 캡처 및 YOLO 탐지 중단 없이 연속 작동 보장.
  6. 신규 12종 단위/통합 테스트(중복 실패 방지, 런처 의존성 역전 차단, 음수/문자열 HWND 스트림 라우팅 포함) 포함 전체 176개 테스트 100% PASS 및 무회귀 입증.
  7. WBS 게이트 유효성 검사기(`python scripts/wbs_gate_validator.py`) 100% 통과.
* **감사 판정**: `[PASS]`
* **롤백 전략**: `git checkout HEAD -- requirements.txt scripts/launcher.py 실행하기.bat src/server.py static/app.js WBS.md; rm tests/test_stream_fallback_and_self_healing.py`

---

### [WBS-11] 런처 및 프런트엔드 실행 결함 품질 사후 부검(Post-Mortem) 및 안티 결함 불변식 수립

* **목표 및 범위**: 사용자 제보("실행하기 버튼이 나오고 바로 꺼져. 자꾸 하자있는 결과물을 주는 문제를 기록하라하고, 말한 실행하기 버튼 문제 고쳐줘")에 따른 품질 부검 및 결함 근본 해결. 배치 파일 블록 내 소괄호 파싱 충돌(`누락 was unexpected at this time`) 및 대화형 실행 시 조기 종료 문제 완전 해결, 프런트엔드 캡처 핸드셰이크 전 미성숙 실행 버튼 노출 차단 및 웹소켓 단절 시 HTTP 스트림 자동 복구, 서버 측 웹소켓 액션 예외 격리, 공식 품질 사후 부검 보고서(`docs/QUALITY_POSTMORTEM.md`) 및 교훈집(`LESSONS.md`) 작성, 실제 `cmd.exe` 프로세스 구동 스모크 테스트 구현.
* **선행 의존성**: WBS-10 [PASS]
* **담당자**: 개발자 (Antigravity Coordinator Gemini) / PM 어드바이저 (Coarena GPT-6 Luna)
* **산출물**:
  - `docs/QUALITY_POSTMORTEM.md`
  - `LESSONS.md`
  - `실행하기.bat`
  - `scripts/launcher.py`
  - `src/server.py`
  - `static/app.js`
  - `tests/test_launcher_governance.py`
  - `tests/test_stream_fallback_and_self_healing.py`
  - `WBS.md`
* **인수 기준**:
  1. `실행하기.bat` 내 `cmd.exe` 예약 괄호 파싱 충돌 완전 근절 및 `cmd.exe /c "실행하기.bat --no-pause --help"` 실제 구동 시 오류 코드 0 반환 입증.
  2. 대화형 실행 시 성공(`0`) 및 오류(비`0`) 모든 경로에서 `pause >nul`을 보장하여 사용자가 키를 누르기 전에 창이 꺼지지 않음 입증.
  3. `scripts/launcher.py`에 `--no-pause` 인자 지원 및 기실행 중인 서버 감지 시 안내 출력.
  4. 프런트엔드(`static/app.js`)에서 캡처 시작 응답 성공(`start_response`) 또는 첫 바이너리 프레임 수신 전까지 `btnStart` 노출 차단(핸드셰이크 선행 원칙).
  5. 웹소켓 세션 도중 예기치 않은 단절 발생 시 즉시 중단되지 않고 HTTP 스트림 프로토콜로 무중단 자동 복구.
  6. 서버 측 `websocket_stream_endpoint` 액션별 예외 처리 격리로 웹소켓 세션 보호.
  7. `docs/QUALITY_POSTMORTEM.md` 및 `LESSONS.md`에 근본 원인 분석, 5대 안티 결함 불변식, 릴리즈 전 필수 스모크 체크리스트 기록.
  8. 신규 실제 프로세스 스모크 테스트 포함 전체 178개 테스트 100% PASS 및 무회귀 입증.
  9. WBS 게이트 유효성 검사기(`python scripts/wbs_gate_validator.py`) 100% 통과.
* **감사 판정**: `[PASS]`
* **롤백 전략**: `git checkout HEAD~1`





