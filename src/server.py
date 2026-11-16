"""src/server.py
FastAPI Server and Security Proxy for SCHALE Tactical Tracking.
Provides same-origin /api/predict proxy to keep upstream cloud API credentials secure on the server.
Cross-cutting concerns (Redaction, Circuit Breaker, Telemetry, and Invariants)
are decoupled using Aspect-Oriented Programming (AOP).
"""

from contextlib import asynccontextmanager
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import time

# Ensure repository root is on sys.path for direct execution
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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
from src.config import AppConfig, get_config
from src.multipart_parser import parse_multipart_body
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
    for fname, b64_str in images_b64.items():
        if "," in b64_str:
            b64_str = b64_str.split(",", 1)[1]
        try:
            raw_bytes = base64.b64decode(b64_str)
            image_data_map[fname] = raw_bytes
            image_data_map[Path(fname).name] = raw_bytes
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "invalid_image_base64", "message": f"Failed to decode image '{fname}': {e}"},
            )

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

