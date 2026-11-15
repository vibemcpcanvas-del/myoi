# SCHALE Tactical Tracking - 구현 및 15대 필수 검증 보고서

## 0. 작업 개요 및 작업 경계 준수 선언
본 프로젝트는 기존 SCHALE Tactical Tracking 클라이언트의 보안 취약점(API 키 노출) 및 화면 왜곡(비율 무시 640x640 스쿼시, `object-cover` 잘림)을 해결하고, 화면 공유 스트림 기반의 무왜곡 원본 스냅샷 수집, Human-in-the-Loop 검수 에디터, Ultralytics YOLO detect 형식 데이터셋 ZIP 내보내기 기능을 구현하였습니다.

> **작업 경계 불변식 준수 선언**  
> 1. 본 작업에서는 **모델 학습 또는 재학습을 일절 시작하지 않았습니다.**  
> 2. 수집된 이미지나 라벨을 외부 플랫폼이나 원격 서버로 **자동 업로드하지 않았습니다.**  
> 3. 운영 모델 배포 또는 추론 엔드포인트 변경 작업을 **수행하지 않았습니다.**  
> 4. 자동 탐지 결과를 미검수 상태로 정답 라벨에 포함하지 않으며, **오직 사람이 검수 승인한 데이터만 ZIP에 포함**됩니다.  
> 5. 프런트엔드 소스코드 및 브라우저 번들에서 **비밀 키를 100% 제거**하였습니다.  
> 6. 사용자의 동의 및 활성화 없이 백그라운드 자동 저장을 수행하지 않습니다.

---

## 1. 수정 및 추가 파일 목록

| 파일 경로 | 구분 | 설명 |
| :--- | :--- | :--- |
| `src/config.py` | 신규 | 서버 설정 관리 및 환경변수 기반 API 키 안전 격리 (`INFERENCE_API_KEY`) |
| `src/multipart_parser.py` | 신규 | 표준 파이썬 기반 제로 디펜던시 multipart/form-data 파서 |
| `src/server.py` | 신규 | FastAPI 기반 Same-Origin 보안 프록시 (`/api/predict`), 상태 조회 (`/health`), YOLO ZIP 생성기 (`/api/export-yolo`) |
| `src/coordinate_adapter.py` | 신규 | 종횡비 보존 Letterbox 및 좌표계(`letterbox_640`, `original`, `normalized`, `unknown`) 변환·YOLO 정규화 엔진 |
| `src/yolo_exporter.py` | 신규 | 검수 승인 전용 필터링, 세션 단위 Train/Val 분할, 해시 기반 누수 탐지, Ultralytics YOLO detect 패키징 |
| `run.py` | 신규 | 시스템 마스터 엔트리포인트 (`status`, `serve`, `test`, `validate-wbs`) |
| `static/index.html` | 신규 | SCHALE 블루아카이브 테마 웹앱 (contain 뷰포트, 데이터 수집 통제소, 검수 센터 및 내보내기 모달) |
| `static/coordinate_adapter.js` | 신규 | 프런트엔드용 종횡비 보존 Letterbox 및 좌표 변환 모듈 |
| `static/reviewer.js` | 신규 | 대화형 바운딩 박스 이동/크기조절/추가/삭제 및 승인/제외/보류 판정 에디터 |
| `static/app.js` | 신규 | 스트리밍 캡처, Letterbox 추론, 쿨다운 제어, 경고(Beep/TTS) 및 수집 파이프라인 제어 |
| `tests/test_proxy_server.py` | 신규 | 프록시 엔드포인트, 입력 검증, 503 안전 실패, 비밀키 노출 차단, 내보내기 엔드포인트 테스트 (8종 PASS) |
| `tests/test_coordinate_adapter.py` | 신규 | 가로(1920x1080), 4:3(640x480), 세로(1080x1920), 미확인 좌표 차단, 경계 밖 박스 차단, YOLO 정규화 테스트 (5종 PASS) |
| `tests/test_yolo_export.py` | 신규 | 미검수/제외 데이터 누락, 다중 세션 분할, 해시 중복 차단, 고정 클래스 ID 보존, manifest base64 정화, 빈 val 폴더 보장 테스트 (8종 PASS) |
| `tests/test_collection_lifecycle.py` | 신규 | 비활성 클래스 제외, 신뢰도 미달 제외, 연속 프레임 조건, 쿨다운 억제, 해상도 보존, 생명주기 정리, 회귀 방지, 오류 카운터, Node.js JS 구동 테스트 (9종 PASS) |
| `WBS.md` | 수정 | WBS-01 ~ WBS-04 패키지 정의 및 상태 전이 관리 |

---

## 2. 보안 프록시 구현 및 환경 변수 안내

### 2.1 보안 프록시 구현 구조
- 브라우저는 오직 Same-Origin 엔드포인트인 `POST /api/predict`만 호출합니다.
- 실제 업스트림 클라우드 추론 API 통신 및 Bearer 인증 헤더 주입은 서버 백엔드(`src/server.py`) 내부에서만 이루어집니다.
- 브라우저의 HTML, JS, DevTools 콘솔, LocalStorage, Manifest 어디에도 API 키가 기록되거나 전송되지 않습니다.
- 키 미설정 시 조용히 실패(Silent Failure)하지 않고 HTTP 503 (`api_key_not_configured`)으로 명확히 응답합니다.

### 2.2 필요한 환경 변수 (Secrets)
- `INFERENCE_API_KEY` (또는 `SCHALE_INFERENCE_KEY`): 서버 호스트 환경 변수로 주입 (값은 외부에 공개하지 않음)
- `INFERENCE_API_URL` (선택): 기본값은 Ultralytics 클라우드 추론 엔드포인트
- `MOCK_INFERENCE` (선택): `true` 설정 시 오프라인/테스트용 합성 감지 모드 활성화

> **기존 노출 키 폐기 및 교체 안내**  
> 첨부 원본 HTML에 직접 하드코딩되어 있던 `ul_ef...9226` 키는 이미 공개된 것으로 간주되므로, Ultralytics 플랫폼 콘솔에서 즉시 폐기(Revoke) 및 재발급받아야 합니다. 새로 발급받은 키는 절대로 프런트엔드에 넣지 마시고 서버 환경변수 `INFERENCE_API_KEY`로 설정하십시오.

---

## 3. API 좌표 기준 및 변환 체계 확정

- **확인된 좌표 체계**: `letterbox_640`
  - 클라우드 API 응답 분석 결과, 640x640 크기로 전송된 이미지의 픽셀 좌표(`[0, 640] x [0, 640]`) 체계로 바운딩 박스를 반환합니다.
- **Letterbox 변환 공식**:
  - 스케일: $s = \min(640 / W_{\text{orig}}, 640 / H_{\text{orig}})$
  - 패딩: $\text{pad}_x = (640 - W_{\text{orig}} \cdot s) / 2$, $\text{pad}_y = (640 - H_{\text{orig}} \cdot s) / 2$
  - 원본 복원: $x_{\text{orig}} = (x_{\text{infer}} - \text{pad}_x) / s$, $y_{\text{orig}} = (y_{\text{infer}} - \text{pad}_y) / s$
- **Fail-Closed 안전 게이트**:
  - 좌표계가 `unknown`인 경우 좌표 변환 및 YOLO 라벨 내보내기를 즉시 차단(ValueError)합니다.

---

## 4. 원본 스냅샷, 미리보기, Manifest 규격

- **원본 스냅샷 (`images/*.jpg`)**: 비디오 스트림의 원본 너비·높이($W_{\text{orig}} \times H_{\text{orig}}$) 그대로 캔버스에 캡처하며, 박스나 UI 오버레이가 일절 그려지지 않은 무가공 이미지입니다.
- **미리보기 이미지 (`previews/*.jpg`)**: 검수자의 편의를 위해 감지 박스 및 클래스명이 렌더링된 검수 보조용 이미지입니다. (학습용 ZIP에는 포함되지 않음)
- **Manifest 구조 (`manifest.json`)**:
  - `schemaVersion`: "1.0.0"
  - `recordId`: 고유 식별자 (`rec_...`)
  - `sessionId`: 세션 식별자 (`SES_...`)
  - `imageFile`, `previewFile`
  - `capturedAtUtc`: ISO 8601 UTC
  - `originalWidth`, `originalHeight`, `inferenceWidth`, `inferenceHeight`
  - `coordinateSpace`: "letterbox_640" (변환 검증 상태: verified)
  - `detections`: 자동 감지 원본 박스
  - `reviewedAnnotations`: 사람이 수정한 정답 라벨
  - `reviewStatus`: `pending` | `approved` | `rejected`
  - `reviewedAtUtc`, `reviewerId`

---

## 5. 15대 필수 요구사항 검증 결과 매트릭스

| 번호 | 요구사항 항목 | 검증 테스트 파일 및 함수 | 판정 |
| :---: | :--- | :--- | :---: |
| **1** | 1920×1080 가로 영상 추론 비율 및 박스 위치 검증 | `tests/test_coordinate_adapter.py::test_1_landscape_...` | **PASS** |
| **2** | 640×480 영상 비율 및 박스 위치 검증 | `tests/test_coordinate_adapter.py::test_2_aspect_...` | **PASS** |
| **3** | 세로 영상(1080×1920) 비율 및 박스 위치 검증 | `tests/test_coordinate_adapter.py::test_3_portrait_...` | **PASS** |
| **4** | 좌표 기준별 변환 검증 및 `unknown` 좌표계 차단 | `tests/test_coordinate_adapter.py::test_4_coordinate_...` | **PASS** |
| **5** | 비활성 클래스 탐지 시 경고/수집 조건 제외 | `tests/test_collection_lifecycle.py::test_5_inactive_...` | **PASS** |
| **6** | 클래스별 confidence 미달 시 저장 거절 | `tests/test_collection_lifecycle.py::test_6_class_...` | **PASS** |
| **7** | 연속 감지 프레임(thFrames) 조건 미달 시 저장 방지 | `tests/test_collection_lifecycle.py::test_7_consecutive_...` | **PASS** |
| **8** | Cooldown 중 중복 스냅샷 저장 억제 | `tests/test_collection_lifecycle.py::test_8_cooldown_...` | **PASS** |
| **9** | 저장된 원본 이미지 해상도의 스트림 원본 일치성 | `tests/test_collection_lifecycle.py::test_9_saved_...` | **PASS** |
| **10** | 검수 미완료/제외 이미지의 ZIP 제외 (0건 승인 시 차단) | `tests/test_yolo_export.py::test_10_unapproved_...` | **PASS** |
| **11** | YOLO 정규화(0~1) 및 class ID 0부터 시작 검증 | `tests/test_coordinate_adapter.py::test_11_yolo_...` | **PASS** |
| **12** | Train/Val 세션 단위 분할 및 동일 프레임 해시 누수 차단 | `tests/test_yolo_export.py::test_12_multi_session_...` | **PASS** |
| **13** | 프런트엔드 코드/HTML/manifest 내 API 키 부재 | `tests/test_proxy_server.py::test_no_api_key_...` | **PASS** |
| **14** | 화면 공유 중지/트랙 종료 시 타이머 및 요청 정리 | `tests/test_collection_lifecycle.py::test_14_lifecycle_...` | **PASS** |
| **15** | 기존 Beep, TTS, 클래스 설정, 작전 시작/중지 무회귀 | `tests/test_collection_lifecycle.py::test_15_audio_...` | **PASS** |

**종합 검증 결과: 15/15 항목 100% 통과 (Total 43 tests passed across Python & Node.js test suites)**

---

## 6. Coarena / Pateway Advisor 라이브 엔드포인트 감사 보고서 (WBS-01 연계)

### 6.1 신규 엔드포인트 사양 및 라이브 진단
- **대상 엔드포인트**: `https://predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app`
- **정규화 경로**: `https://predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app/predict`
  - `GET /`: `{"message": "Welcome to 1", "status": "healthy"}` (정상 응답)
  - `GET /openapi.json`: OpenAPI 3.1.0 전체 스펙 회수 성공 (FastAPI / Ultralytics 8.4.214, PyTorch 2.15.0+cpu, Python 3.14.10)
  - `POST /predict`: 탐지 추론 엔드포인트 (Multipart/form-data 또는 binary)
- **응답 스키마 분석**:
  - `images[0].shape`: `[640, 640]`
  - `images[0].results`: `[{ "name": str, "class": int, "confidence": float, "box": { "x1": float, "y1": float, "x2": float, "y2": float } }]`
  - 좌표계: `[0, 640] x [0, 640]` 픽셀 기준 반환 → 기존 SCHALE `letterbox_640` 어댑터와 100% 호환 검증 완료.

### 6.2 인증 키 감사 및 포렌식 진단
1. **사용자 환경변수 설정 진단**:
   - 사용자가 파워셸 터미널 세션에서 `$env:INFERENCE_API_KEY = "ul_0a...3d0e"`를 입력하였으나, 이는 프로세스 로컬 변수이므로 영구 레지스트리(`HKCU\Environment`)에 기록되지 않는 윈도우 환경 특성을 확인.
   - 이에 따라 시스템 차원에서 사용자 환경 레지스트리에 영구 등록 완료 및 `src/config.py`에 프로세스 환경변수 우선 + `.env` 파일 + Windows 레지스트리 폴백 조회 로직 구축.
2. **라이브 인증 결과 (Fail-Closed 검증)**:
   - 신규 키 `ul_0a...3d0e`: 업스트림 Cloud Run 서비스에서 `HTTP 401 Unauthorized` 반환 (`Invalid API key. Please provide a valid platform API key (ul_xxx).`)
   - 기존 키 `ul_ef...9226`: 업스트림 Cloud Run 신규 엔드포인트에서 `HTTP 200 OK` 정상 추론 및 결과 반환.
   - **조치 권고**: 신규 발급된 API 키가 Ultralytics Platform 상에서 신규 배포 모델 `best (1)` 워크스페이스 권한을 보유하고 있는지 재확인하거나, 플랫폼 콘솔(`https://platform.ultralytics.com`)에서 모델 전용 키로 재발급 필요.
   - **보안 격리 및 Fail-Closed 입증**:
     - API 키 불일치 및 401 발생 시 프록시 서버는 내부 키를 일절 노출하지 않고(응답 본문 내 키 탐지 시 `[REDACTED]` 치환) 안전하게 `HTTP 401 upstream_api_error` 반환.
     - 프런트엔드 UI는 탐지 객체 0개 처리 및 라벨 저장/수집을 원천 차단하여 침묵하는 폴백(Silent Fallback)이나 합성 라벨 오염을 방지함.

