# SCHALE 시스템 품질 사후 부검 보고서 (Quality Post-Mortem)

**문서 번호**: QPM-2026-11-19-01  
**관련 WBS**: [WBS-11](file:///d:/myoi/WBS.md#wbs-11)  
**작성일**: 2026-11-19  
**상태**: [APPROVED by Advisor PM]  
**작성자**: 개발자 (Antigravity Coordinator Gemini) / 감사자 (Coarena Advisor `gpt-6-luna`)  

---

## 1. 사고 개요 (Incident Overview)

* **사용자 제보 증상**:
  > "실행하기 버튼이 나오고 바로 꺼져. 자꾸 하자있는 결과물을 주는 문제를 기록하라하고, 말한 실행하기 버튼 문제 고쳐줘"
* **영향도 (Severity - Fatal)**:
  - Windows 비개발자 환경에서 `실행하기.bat` 더블클릭 시 콘솔 창이 0.05초 만에 깜빡이고 강제 종료되어 시스템 진입 자체가 불가능함.
  - 웹 대시보드에서 네이티브 연결 클릭 시 서버 핸드셰이크 검증 전에 실행 버튼("자동 작전 개시")이 노출되었다가 연결 이상 시 즉각 소멸되어 사용자 인터랙션 파탄 발생.
  - 단순 문자열 포함 여부(`assert '...' in content`)에 의존한 형식적 단위 테스트로 인해 런타임 구문 파싱 결함이 테스트 스위트를 무결하게 통과하는 "거짓 통과(False Positive)" 발생.

---

## 2. 근본 원인 분석 (Root Cause Analysis: Verified Facts vs Invariants)

### Angle A: Windows 런처 배치 파일 구문 파싱 결함 (확인된 사실)
1. **배치 파일 내부 괄호 파싱 충돌 (`누락 was unexpected at this time`)**:
   - `실행하기.bat` 50-53행:
     ```bat
     if errorlevel 1 (
         echo [*] 필수 라이브러리(websockets) 누락 감지. 자동 자가 치유(Self-Healing) 설치를 진행합니다...
         "%PY_CMD%" -m pip install "websockets>=12.0"
     )
     ```
   - Windows `cmd.exe`는 `if (...)` 복합 블록을 인터프리트할 때 문자열 내의 괄호 `)`를 블록 종결자로 취급합니다.
   - 따라서 `(websockets)`의 닫는 괄호 `)`에서 `if` 블록이 조기 닫히고, 그 뒤의 `누락`이라는 단어가 예상치 못한 토큰으로 인식되어 구문 분석 단계에서 즉시 배치 스크립트가 비정상 크래시되었습니다.
2. **형식적 테스트의 한계 (Shallow Test Anti-Pattern)**:
   - `tests/test_stream_fallback_and_self_healing.py`의 단위 테스트는 `assert 'import websockets' in content` 등 텍스트 내용만을 검사하였으며, 실제 `cmd.exe` 프로세스를 띄워 구문 분석(Syntax Parsing)을 수행하지 않았습니다.
3. **정상 종료 및 기실행 시 무조건 종료 (Missing Pause)**:
   - 서버가 이미 실행 중이거나 `uvicorn`이 정상 종료될 때 `launcher.py run`이 리턴코드 0을 반환하지만, `실행하기.bat` 마지막 줄에 `pause` 없이 `exit /b 0`이 실행되어 사용자가 결과를 확인할 틈도 없이 창이 닫혔습니다.

### Angle B: 프런트엔드 대시보드 버튼 상태 불일치 및 예외 취약성 (확인된 사실)
1. **미성숙 버튼 노출 (Premature Button Exposure)**:
   - `static/app.js`의 `connectNativeWgc` 함수에서 WebSocket `onopen` 이벤트 직후 서버의 캡처 대상 창 유효성 응답(`start_response`)을 기다리지 않고 `$('btnStart').classList.remove('hidden')`을 호출했습니다.
   - 타겟 창이 최소화되었거나 캡처 대상 HWND를 찾을 수 없는 경우 서버가 실패를 반환하고, 클라이언트는 즉시 `stopOps()`를 호출하여 `btnStart`를 숨겼습니다. 이로 인해 사용자의 눈에는 "실행하기 버튼이 잠깐 나왔다가 바로 꺼지는" 증상이 발생했습니다.
2. **웹소켓 단절 시 비대칭적 폴백 누락**:
   - WebSocket 연결 전 실패 시에는 HTTP 스트림으로 자동 폴백되도록 설계되었으나, 일단 연결된(`connectionEstablished = true`) 후 예기치 않게 소켓이 닫히면 HTTP 스트림으로 전환하지 않고 즉각 `stopOps()`로 종료되었습니다.
3. **서버 측 WebSocket 액션 예외 미포획**:
   - `src/server.py`의 `websocket_stream_endpoint` 루프 내부에서 액션 처리(`action == "start"` 등) 중 발생할 수 있는 내부 예외 처리가 부재하여 예외 발생 시 웹소켓 세션 전체가 끊어지는 위험이 존재했습니다.

---

## 3. 시정 조치 및 해결 내역 (Remediation Actions)

1. **`실행하기.bat` 괄호 구문 오류 근절 및 종료 방지**:
   - 배치 파일 블록 내의 모든 괄호를 대괄호 `[...]` 또는 괄호 없는 텍스트로 수정하여 `cmd.exe` 파싱 충돌 원천 차단.
   - `%*` 인자 전달 및 `--no-pause` 플래그 파싱을 추가하여 비대화형 테스트 환경 지원.
   - 대화형 실행 시 성공(`0`) 및 실패(비`0`) 모두 `pause >nul`을 보장하여 창이 강제로 꺼지지 않도록 개정.
2. **`scripts/launcher.py` CLI 인자 확장**:
   - `p_run`에 `--no-pause` 인자를 공식 지원하여 배치 파일과의 파라미터 불일치 방지.
   - 서버 기실행 시 브라우저 자동 오픈과 함께 명확한 안내 출력.
3. **`static/app.js` 버튼 라이프사이클 및 자동 복구 강화**:
   - `onopen`에서의 미성숙 `btnStart` 노출 제거.
   - 서버의 `start_response` 성공(`data.success === true`) 확인 또는 첫 바이너리 프레임 수신 시점에만 안전하게 `btnStart` 노출.
   - 연결 확립 후 WebSocket 예기치 않은 단절 발생 시 즉각 `stopOps()`로 중단하지 않고 HTTP 스트림 프로토콜(`startHttpFallbackCapture`)로 자동 복구.
4. **`src/server.py` WebSocket 액션 처리 예외 격리**:
   - 개별 액션(`start`, `stop` 등) 단위로 `try...except`를 적용하여 단일 작업 실패가 WebSocket 통신 채널 전체를 끊지 않도록 방어.
5. **실제 `cmd.exe` 구문 스모크 테스트 구현**:
   - `tests/test_launcher_governance.py` 및 `tests/test_stream_fallback_and_self_healing.py`에 실제 `cmd.exe /c "실행하기.bat --no-pause --help"`를 구동하여 exit code 0 및 `was unexpected at this time` 부재를 기계적으로 검증하는 테스트 추가.

---

## 4. 엄격한 안티 결함 불변식 (Strict Anti-Defect Invariants)

1. **[INV-BAT-01] 배치 블록 내 괄호 금지 원칙**:
   - Windows 배치 파일의 `if (...)` 및 `for (...)` 블록 내부 `echo` 또는 주석에는 원시 소괄호 `(` `)` 사용을 엄격히 금지하며, 대괄호 `[...]`를 사용한다.
2. **[INV-BAT-02] Fail-Closed 콘솔 유지 원칙**:
   - 대화형 더블클릭 실행 시 성공 및 에러 모든 경로에서 사용자가 키를 누르기 전까지 콘솔 창이 닫히지 않아야 한다.
3. **[INV-UI-01] 핸드셰이크 완료 전 버튼 노출 금지**:
   - 백엔드 캡처 서비스와의 세션 개시 확인(`start_response` 성공) 또는 실제 유효 프레임 렌더링 확인 전에는 실행 버튼을 절대 노출하지 않는다.
4. **[INV-TEST-01] 비신뢰 문자열 테스트 금지 (실제 프로세스 스모크 테스트 의무화)**:
   - 런처, 배치 스크립트, CLI 진입점은 단순 파일 텍스트 정규식 검사에 의존하지 않고, 실제 프로세스 실행 서브프로세스를 통해 종료 코드 및 표준 에러를 검증해야 한다.
5. **[INV-STREAM-01] 웹소켓 단절 시 자동 복구 원칙**:
   - 캡처 세션 진행 중 웹소켓 일시 단절 발생 시 즉시 중단하지 않고 HTTP 대체 스트림으로 무중단 자동 전환한다.

---

## 5. 릴리즈 전 필수 스모크 테스트 체크리스트 (Pre-Release Smoke Test Checklist)

- [x] 1. `cmd.exe /c 실행하기.bat --no-pause --help` 실행 시 오류 문구 없이 종료 코드 0 반환
- [x] 2. `python scripts/launcher.py status` 실행 시 정상 상태 리포트 출력
- [x] 3. `pytest tests/test_*.py` 전체 스위트 실행 시 178개 전 항목 100% PASS
- [x] 4. `python scripts/wbs_gate_validator.py` 실행 시 WBS 무결성 게이트 PASS
- [x] 5. 프런트엔드 구문 검사(`node -c static/app.js`, `node -c static/aspects.js`) 통과
- [x] 6. Advisor PM (`advisor_hook.py`) 감사 평결 승인 완료
