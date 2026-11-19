"""src/server.py
FastAPI Server and Security Proxy for SCHALE Tactical Tracking.
Provides same-origin /api/predict proxy to keep upstream cloud API credentials secure on the server.
Cross-cutting concerns (Redaction, Circuit Breaker, Telemetry, and Invariants)
are decoupled using Aspect-Oriented Programming (AOP).
"""

from contextlib import asynccontextmanager
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import time

# Ensure repository root is on sys.path for direct execution
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import httpx

from src.aspects.circuit_breaker import (
    circuit_breaker,
    default_inference_circuit_breaker,
)
from src.aspects.redaction import (
    get_global_redactor,
    redact_secrets,
    SecretRedactionMiddleware,
)
from src.aspects.telemetry import (
    default_telemetry_collector,
    measure_telemetry,
)
from src.capture import CaptureService, get_capture_service, WindowFinder, CaptureState
from src.config import AppConfig, get_config
from src.multipart_parser import parse_multipart_body
from src.session_manager import SessionManager, get_session_manager
from src.yolo_exporter import validate_and_build_yolo_dataset


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
PID_FILE = Path(__file__).resolve().parent.parent / ".server.pid"


@asynccontextmanager
async def lifespan(app: FastAPI):
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            pass
    try:
        yield
    finally:
        try:
            capture_svc = get_capture_service()
            if capture_svc.is_running:
                capture_svc.stop_capture()
        except Exception:
            pass

        if not os.environ.get("PYTEST_CURRENT_TEST"):
            try:
                if PID_FILE.exists():
                    PID_FILE.unlink(missing_ok=True)
            except Exception:
                pass


app = FastAPI(
    title="SCHALE Tactical Tracking Proxy Server",
    version="1.0.0",
    description="Secure same-origin proxy for streaming detection snapshot collection and YOLO export.",
    lifespan=lifespan,
)

# AOP Aspect 1: Secret Redaction Middleware
app.add_middleware(SecretRedactionMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
@app.get("/api/status")
@measure_telemetry(metric_name="health_check")
async def get_system_status() -> Dict[str, Any]:
    """Health check endpoint.
    Reports server status, API key status, operational telemetry,
    and circuit breaker health without leaking sensitive credentials.
    """
    config = get_config()
    redactor = get_global_redactor()
    sanitized_upstream = redactor.redact_text(config.inference_api_url.split("?")[0])

    return {
        "status": "online",
        "service": "SCHALE Tactical Tracking Server",
        "version": "1.0.0",
        "apiConfigured": config.is_api_key_configured,
        "mockMode": config.mock_inference,
        "upstreamEndpoint": sanitized_upstream,
        "defaultCoordinateSpace": "letterbox_640",
        "circuitBreaker": default_inference_circuit_breaker.get_telemetry(),
        "telemetry": default_telemetry_collector.get_metrics(),
        "capture": get_capture_service(mock_mode=config.mock_inference).get_status(),
    }


async def _execute_upstream_inference(
    config: AppConfig,
    filename: str,
    file_bytes: bytes,
    file_mime: str,
    imgsz: int,
    conf: Optional[float],
    iou: Optional[float],
) -> Dict[str, Any]:
    """Helper that handles network execution to upstream cloud inference API.
    Protected by circuit breaker and secret redaction aspects.
    """
    headers = {
        "Authorization": f"Bearer {config.inference_api_key}",
    }
    files = {
        "file": (filename, file_bytes, file_mime),
    }
    data: Dict[str, str] = {
        "imgsz": str(imgsz),
    }
    if conf is not None:
        data["conf"] = str(conf)
    if iou is not None:
        data["iou"] = str(iou)

    try:
        async with httpx.AsyncClient(timeout=config.request_timeout_sec) as client:
            upstream_resp = await client.post(
                config.inference_api_url,
                headers=headers,
                files=files,
                data=data,
            )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "error": "upstream_timeout",
                "message": f"Upstream inference request timed out after {config.request_timeout_sec}s.",
            },
        )
    except httpx.RequestError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "upstream_connection_failed",
                "message": "Failed to connect to upstream inference server.",
            },
        )

    if upstream_resp.status_code != 200:
        try:
            upstream_err = upstream_resp.json()
            err_msg = upstream_err.get("message") or upstream_err.get("error") or "Upstream inference error"
        except Exception:
            err_msg = f"Upstream returned HTTP {upstream_resp.status_code}"

        # Error redaction aspect
        redactor = get_global_redactor()
        sanitized_msg = redactor.redact_text(str(err_msg))

        raise HTTPException(
            status_code=(
                upstream_resp.status_code
                if upstream_resp.status_code in (400, 401, 403, 500)
                else status.HTTP_502_BAD_GATEWAY
            ),
            detail={
                "error": "upstream_api_error",
                "message": sanitized_msg,
                "upstreamStatus": upstream_resp.status_code,
            },
        )

    try:
        upstream_data = upstream_resp.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "invalid_upstream_json",
                "message": "Upstream response was not valid JSON.",
            },
        )

    return upstream_data


@app.post("/api/predict")
@measure_telemetry(metric_name="api_predict", inject_ping=True)
@redact_secrets()
@circuit_breaker(breaker=default_inference_circuit_breaker)
async def predict_proxy(request: Request) -> Dict[str, Any]:
    """Proxy endpoint for inference.
    Receives an image file from the frontend (multipart/form-data or raw octet-stream),
    relays it to upstream cloud inference API with server-side credentials,
    and returns standardized detection results.
    AOP Aspects applied:
      - @measure_telemetry: tracks execution duration, updates telemetry stats, injects pingMs
      - @redact_secrets: intercepts any exception details or responses to scrub credentials
      - @circuit_breaker: provides fail-closed isolation on 401/403 or repeated upstream failures
    """
    config = get_config()
    content_type_header = request.headers.get("content-type", "")

    file_bytes: Optional[bytes] = None
    filename: str = "frame.jpg"
    file_mime: str = "image/jpeg"
    imgsz: int = 640
    coordinate_space: str = "letterbox_640"
    is_mock: bool = config.mock_inference
    conf: Optional[float] = None
    iou: Optional[float] = None

    # 1. Parse request body
    body_bytes = await request.body()
    if not body_bytes or len(body_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "empty_file", "message": "Uploaded request body is empty (0 bytes)."},
        )

    if len(body_bytes) > config.max_upload_size_bytes:
        raise HTTPException(
            status_code=getattr(status, "HTTP_413_CONTENT_TOO_LARGE", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE),
            detail={
                "error": "payload_too_large",
                "message": f"File size ({len(body_bytes)} bytes) exceeds maximum limit ({config.max_upload_size_bytes} bytes).",
            },
        )

    if "multipart/form-data" in content_type_header.lower():
        form_data = parse_multipart_body(body_bytes, content_type_header)
        if "file" in form_data.files:
            filename, file_bytes, file_mime = form_data.files["file"]
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "missing_file_field", "message": "No 'file' field found in multipart payload."},
            )

        if "imgsz" in form_data.fields:
            try:
                parsed_sz = int(form_data.fields["imgsz"])
                if 32 <= parsed_sz <= 4096:
                    imgsz = parsed_sz
            except ValueError:
                pass

        if "conf" in form_data.fields:
            try:
                parsed_conf = float(form_data.fields["conf"])
                if 0.01 <= parsed_conf <= 1.0:
                    conf = parsed_conf
            except ValueError:
                pass

        if "iou" in form_data.fields:
            try:
                parsed_iou = float(form_data.fields["iou"])
                if 0.0 <= parsed_iou <= 0.95:
                    iou = parsed_iou
            except ValueError:
                pass

        if "coordinateSpace" in form_data.fields:
            coordinate_space = form_data.fields["coordinateSpace"]

        if "mock" in form_data.fields:
            is_mock = form_data.fields["mock"].lower() in ("1", "true", "yes")
    elif content_type_header.startswith("image/"):
        file_bytes = body_bytes
        file_mime = content_type_header
        filename = "stream_frame.jpg"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_mime_type", "message": f"Expected image or multipart/form-data, received: '{content_type_header}'."},
        )

    if not file_bytes or len(file_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "empty_file", "message": "Uploaded image file is empty (0 bytes)."},
        )

    if not (file_mime.startswith("image/") or filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_mime_type", "message": f"Expected image file, received: '{file_mime}' ({filename})."},
        )

    # 2. Mock mode handling
    if is_mock:
        return {
            "success": True,
            "coordinateSpace": coordinate_space,
            "inferenceWidth": imgsz,
            "inferenceHeight": imgsz,
            "speed": {"preprocess": 1.2, "inference": 15.0, "postprocess": 0.5},
            "results": [
                {
                    "class": 0,
                    "name": "Target_A",
                    "confidence": 0.88,
                    "box": {"x1": 100.0, "y1": 80.0, "x2": 260.0, "y2": 280.0},
                },
                {
                    "class": 1,
                    "name": "Target_B",
                    "confidence": 0.76,
                    "box": {"x1": 320.0, "y1": 150.0, "x2": 480.0, "y2": 390.0},
                },
            ],
            "metadata": {"mock": True},
        }

    # 3. Check server-side credential configuration
    if not config.is_api_key_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "api_key_not_configured",
                "message": (
                    "Inference API key is not configured on the server. "
                    "Please set the INFERENCE_API_KEY environment variable on the server host."
                ),
            },
        )

    # 4. Proxy request to upstream cloud inference API
    upstream_data = await _execute_upstream_inference(
        config=config,
        filename=filename,
        file_bytes=file_bytes,
        file_mime=file_mime,
        imgsz=imgsz,
        conf=conf,
        iou=iou,
    )

    # 5. Normalize results schema
    raw_results = []
    if "images" in upstream_data and len(upstream_data["images"]) > 0:
        first_img = upstream_data["images"][0]
        raw_results = first_img.get("results", [])
    elif "results" in upstream_data:
        raw_results = upstream_data.get("results", [])

    standardized_results: List[Dict[str, Any]] = []
    for item in raw_results:
        box = item.get("box", {})
        standardized_results.append({
            "class": int(item.get("class", 0)),
            "name": str(item.get("name", f"Class_{item.get('class', 0)}")),
            "confidence": float(item.get("confidence", 0.0)),
            "box": {
                "x1": float(box.get("x1", 0.0)),
                "y1": float(box.get("y1", 0.0)),
                "x2": float(box.get("x2", 0.0)),
                "y2": float(box.get("y2", 0.0)),
            },
        })

    return {
        "success": True,
        "coordinateSpace": coordinate_space,
        "inferenceWidth": imgsz,
        "inferenceHeight": imgsz,
        "results": standardized_results,
        "metadata": {
            "upstreamTask": upstream_data.get("metadata", {}).get("task", "detect"),
        },
    }


@app.post("/api/export-yolo")
@app.post("/api/yolo/export")
@measure_telemetry(metric_name="api_export_yolo")
async def export_yolo_zip(request: Request):
    """Generates an Ultralytics YOLO detect format dataset ZIP archive from reviewed records."""
    import base64
    from fastapi.responses import Response

    body = await request.json()
    records = body.get("records", [])
    images_b64 = body.get("images", {})  # filename -> base64 string
    classes = body.get("classes")  # optional fixed class mapping

    if not records:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "empty_records", "message": "No manifest records provided for export."},
        )

    # Decode base64 images
    image_data_map: Dict[str, bytes] = {}
    if isinstance(images_b64, dict):
        for fname, b64_str in images_b64.items():
            if b64_str is None:
                continue
            if not isinstance(b64_str, str):
                continue
            if "," in b64_str:
                b64_str = b64_str.split(",", 1)[1]
            try:
                raw_bytes = base64.b64decode(b64_str)
                if not raw_bytes or len(raw_bytes) == 0:
                    raise ValueError("Image base64 decoded to 0 bytes.")
                image_data_map[fname] = raw_bytes
                image_data_map[Path(fname).name] = raw_bytes
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": "invalid_image_base64", "message": f"Failed to decode image '{fname}': {e}"},
                )

    # Automatic disk fallback: for any approved records whose images were omitted from payload,
    # attempt resolution from persisted sessions on disk
    cfg = get_config()
    mgr = get_session_manager(base_dir=cfg.data_dir)
    top_sess_id = body.get("sessionId")
    for rec in records:
        if rec.get("reviewStatus") == "approved":
            img_file = rec.get("imageFile", "")
            img_name = Path(img_file).name if img_file else ""
            if (not img_file or img_file not in image_data_map) and (not img_name or img_name not in image_data_map):
                sess_id = rec.get("sessionId") or top_sess_id
                if sess_id and img_file:
                    try:
                        disk_path = mgr.get_image_file_path(sess_id, img_file)
                        if disk_path and disk_path.exists() and disk_path.stat().st_size > 0:
                            raw_b = disk_path.read_bytes()
                            image_data_map[img_file] = raw_b
                            image_data_map[img_name] = raw_b
                    except Exception:
                        pass

    try:
        zip_bytes, report = validate_and_build_yolo_dataset(records, image_data_map, classes=classes)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "validation_failed", "message": str(e)},
        )

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="schale_yolo_dataset.zip"'},
    )


# ============================================================================
# Session Persistence and Historical Data Retrieval Endpoints
# ============================================================================

@app.get("/api/sessions")
@measure_telemetry(metric_name="api_list_sessions")
async def list_sessions_endpoint() -> Dict[str, Any]:
    """Lists all historical and current sessions persisted on disk."""
    cfg = get_config()
    mgr = get_session_manager(base_dir=cfg.data_dir)
    sessions = mgr.list_sessions()
    return {
        "success": True,
        "count": len(sessions),
        "sessions": sessions,
    }


@app.get("/api/sessions/{session_id}")
@measure_telemetry(metric_name="api_get_session")
async def get_session_endpoint(session_id: str) -> Dict[str, Any]:
    """Retrieves a historical session with all records and base64 image data URLs."""
    cfg = get_config()
    mgr = get_session_manager(base_dir=cfg.data_dir)
    try:
        session_data = mgr.load_session(session_id, include_base64=True)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_session_id", "message": str(e)},
        )
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "session_not_found", "message": str(e)},
        )
    return session_data


@app.post("/api/sessions/save")
@app.post("/api/sessions/{session_id}/save")
@measure_telemetry(metric_name="api_save_session")
async def save_session_endpoint(request: Request, session_id: Optional[str] = None) -> Dict[str, Any]:
    """Persists or updates a review session with frame images, bounding boxes, and metadata."""
    cfg = get_config()
    mgr = get_session_manager(base_dir=cfg.data_dir)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_json_body", "message": "Expected JSON payload."},
        )

    target_session_id = session_id or body.get("sessionId")
    if not target_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "missing_session_id", "message": "sessionId is required."},
        )

    records = body.get("records", [])
    classes = body.get("classes")
    metadata = body.get("metadata")

    try:
        result = mgr.save_session(
            session_id=target_session_id,
            records=records,
            classes=classes,
            metadata=metadata,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "session_validation_error", "message": str(e)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "session_save_failed", "message": str(e)},
        )

    return result


@app.delete("/api/sessions/{session_id}")
@measure_telemetry(metric_name="api_delete_session")
async def delete_session_endpoint(session_id: str) -> Dict[str, Any]:
    """Deletes a historical session and its assets from disk."""
    cfg = get_config()
    mgr = get_session_manager(base_dir=cfg.data_dir)
    try:
        mgr.delete_session(session_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_session_id", "message": str(e)},
        )
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "session_not_found", "message": str(e)},
        )
    return {"success": True, "message": f"Session '{session_id}' deleted successfully."}


@app.get("/api/sessions/{session_id}/images/{filename:path}")
async def get_session_image_endpoint(session_id: str, filename: str):
    """Serves a static image belonging to a saved session."""
    cfg = get_config()
    mgr = get_session_manager(base_dir=cfg.data_dir)
    try:
        img_path = mgr.get_image_file_path(session_id, filename)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_path", "message": str(e)},
        )
    if not img_path or not img_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "image_not_found", "message": f"Image '{filename}' not found in session '{session_id}'."},
        )
    media_type = "image/jpeg"
    if img_path.suffix.lower() == ".png":
        media_type = "image/png"
    elif img_path.suffix.lower() == ".webp":
        media_type = "image/webp"

    return FileResponse(img_path, media_type=media_type)


# =====================================================================
# Native WGC / DWM Capture Sidecar Endpoints
# =====================================================================

@app.get("/api/capture/windows")
@measure_telemetry(metric_name="api_capture_windows")
async def list_capturable_windows() -> Dict[str, Any]:
    """Lists discoverable visible windows on the active desktop."""
    cfg = get_config()
    capture_svc = get_capture_service(mock_mode=cfg.mock_inference)
    windows = capture_svc.list_windows()
    return {"windows": windows, "count": len(windows)}


@app.post("/api/capture/start")
@measure_telemetry(metric_name="api_capture_start")
async def start_capture_endpoint(request: Request) -> Dict[str, Any]:
    """Starts out-of-process capture for the target window or desktop."""
    cfg = get_config()
    capture_svc = get_capture_service(mock_mode=cfg.mock_inference)

    try:
        body = await request.json()
    except Exception:
        body = {}

    target = body.get("target") or body.get("hwnd") or 0
    fps = int(body.get("fps") or 20)
    quality = int(body.get("quality") or 80)

    result = capture_svc.start_capture(target=target, fps=fps, quality=quality)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result,
        )
    return result


@app.post("/api/capture/switch")
@measure_telemetry(metric_name="api_capture_switch")
async def switch_capture_endpoint(request: Request) -> Dict[str, Any]:
    """Dynamically switches active capture target window."""
    cfg = get_config()
    capture_svc = get_capture_service(mock_mode=cfg.mock_inference)

    try:
        body = await request.json()
    except Exception:
        body = {}

    target = body.get("target") if "target" in body else body.get("hwnd", 0)
    result = capture_svc.switch_target(target=target)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if result.get("error") == "target_window_not_found" else status.HTTP_400_BAD_REQUEST,
            detail=result,
        )
    return result


@app.post("/api/capture/stop")
@measure_telemetry(metric_name="api_capture_stop")
async def stop_capture_endpoint() -> Dict[str, Any]:
    """Stops the active capture session."""
    cfg = get_config()
    capture_svc = get_capture_service(mock_mode=cfg.mock_inference)
    return capture_svc.stop_capture()


@app.get("/api/capture/status")
@measure_telemetry(metric_name="api_capture_status")
async def get_capture_status_endpoint() -> Dict[str, Any]:
    """Returns real-time telemetry and state of the capture sidecar."""
    cfg = get_config()
    capture_svc = get_capture_service(mock_mode=cfg.mock_inference)
    return capture_svc.get_status()


@app.get("/api/capture/frame")
async def get_capture_frame_endpoint():
    """Retrieves the latest captured frame as JPEG."""
    cfg = get_config()
    capture_svc = get_capture_service(mock_mode=cfg.mock_inference)
    jpeg_bytes, meta = capture_svc.get_latest_frame()

    if not jpeg_bytes:
        if capture_svc.current_state in (CaptureState.PAUSED_MINIMIZED, CaptureState.FAILED):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "capture_degraded_or_paused", "meta": meta},
            )
        target = capture_svc._target_hwnd if capture_svc.is_running else 0
        img, meta = capture_svc.engine.capture_frame(target)
        if img:
            jpeg_bytes = capture_svc.engine.encode_jpeg(img)
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "no_frame_available", "meta": meta},
            )

    return Response(content=jpeg_bytes, media_type="image/jpeg")


async def _mjpeg_stream_generator(capture_svc: CaptureService):
    """Asynchronous generator streaming live JPEG frames with multipart boundary."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=3)
    capture_svc.register_async_queue(queue)
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=0.5)
                if isinstance(item, tuple):
                    frame_bytes, _ = item
                else:
                    continue
            except asyncio.TimeoutError:
                if not capture_svc.is_running or capture_svc.current_state != CaptureState.CAPTURING:
                    await asyncio.sleep(0.08)
                    continue
                frame_bytes, _ = capture_svc.get_latest_frame()
                if not frame_bytes:
                    await asyncio.sleep(0.05)
                    continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(frame_bytes)).encode("ascii") + b"\r\n\r\n"
                + frame_bytes
                + b"\r\n"
            )
    except (asyncio.CancelledError, GeneratorExit):
        pass
    finally:
        capture_svc.unregister_async_queue(queue)


@app.get("/api/stream")
async def get_mjpeg_stream(target: Optional[str] = None, fps: Optional[int] = 20):
    """Live MJPEG video stream route (multipart/x-mixed-replace)."""
    cfg = get_config()
    capture_svc = get_capture_service(mock_mode=cfg.mock_inference)

    parsed_target: int | str = 0
    if target is not None:
        target_str = str(target).strip()
        if target_str.lstrip("-").isdigit():
            parsed_target = int(target_str)
        else:
            parsed_target = target_str

    if not capture_svc.is_running:
        capture_svc.start_capture(target=parsed_target, fps=fps or 20)
    elif target is not None:
        current_target = capture_svc._target_hwnd
        if parsed_target != current_target:
            capture_svc.start_capture(target=parsed_target, fps=fps or 20)

    return StreamingResponse(
        _mjpeg_stream_generator(capture_svc),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.websocket("/ws/stream")
@app.websocket("/api/stream/ws")
async def websocket_stream_endpoint(websocket: WebSocket):
    """High-throughput, low-latency WebSocket stream for native frames and control state."""
    await websocket.accept()
    cfg = get_config()
    capture_svc = get_capture_service(mock_mode=cfg.mock_inference)
    queue: asyncio.Queue = asyncio.Queue(maxsize=3)
    capture_svc.register_async_queue(queue)

    async def sender_task():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.2)
                    if isinstance(item, tuple):
                        frame_bytes, meta = item
                        if frame_bytes:
                            await websocket.send_bytes(frame_bytes)
                    elif isinstance(item, dict):
                        await websocket.send_json(item)
                except asyncio.TimeoutError:
                    if websocket.client_state.name != "CONNECTED":
                        break
        except (asyncio.CancelledError, WebSocketDisconnect):
            pass
        except Exception:
            pass

    sender = asyncio.create_task(sender_task())

    try:
        while True:
            msg = await websocket.receive_text()
            try:
                payload = json.loads(msg)
                action = payload.get("action") or payload.get("type")
                if action == "list_windows":
                    windows = capture_svc.list_windows()
                    await websocket.send_json({"type": "windows", "data": windows})
                elif action == "start":
                    target = payload.get("target") or payload.get("hwnd") or 0
                    try:
                        fps = int(payload.get("fps") or 20)
                    except (ValueError, TypeError):
                        fps = 20
                    try:
                        quality = int(payload.get("quality") or 80)
                    except (ValueError, TypeError):
                        quality = 80
                    try:
                        res = capture_svc.start_capture(target=target, fps=fps, quality=quality)
                    except Exception as exc:
                        logger.warning(f"Failed to start capture: {exc}")
                        res = {"success": False, "error": "capture_start_error", "message": str(exc)}
                    await websocket.send_json({"type": "start_response", "data": res})
                elif action in ("switch_target", "switch"):
                    target = payload.get("target") if "target" in payload else payload.get("hwnd", 0)
                    try:
                        res = capture_svc.switch_target(target=target)
                    except Exception as exc:
                        logger.warning(f"Failed to switch capture target: {exc}")
                        res = {"success": False, "error": "capture_switch_error", "message": str(exc)}
                    await websocket.send_json({"type": "switch_response", "data": res})
                elif action == "stop":
                    try:
                        res = capture_svc.stop_capture()
                    except Exception as exc:
                        logger.warning(f"Failed to stop capture: {exc}")
                        res = {"success": False, "error": "capture_stop_error", "message": str(exc)}
                    await websocket.send_json({"type": "stop_response", "data": res})
                elif action == "status":
                    status_data = capture_svc.get_status()
                    await websocket.send_json({"type": "status", "data": status_data})
                elif action == "ping":
                    await websocket.send_json({"type": "pong", "time": time.time()})
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON payload."})
            except Exception as e:
                logger.error(f"Unhandled error in websocket message handler: {e}")
                try:
                    await websocket.send_json({"type": "error", "message": f"Server processing error: {e}"})
                except Exception:
                    break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        sender.cancel()
        try:
            await sender
        except (asyncio.CancelledError, Exception):
            pass
        capture_svc.unregister_async_queue(queue)



if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse({
        "status": "SCHALE Tactical Tracking Server is running.",
        "ui": "static/index.html is being prepared.",
    })


if __name__ == "__main__":
    import uvicorn
    cfg = get_config()
    print(f"[*] Starting SCHALE Tactical Tracking Server on http://{cfg.host}:{cfg.port}")
    uvicorn.run("src.server:app", host=cfg.host, port=cfg.port)

