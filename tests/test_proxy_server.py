"""tests/test_proxy_server.py
Unit and Security tests for FastAPI Proxy Server (WBS-01).
Tests input validation, error handling, mock inference, and API key leak prevention.
"""

import io
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.server import app
from src.config import AppConfig


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_jpeg_bytes():
    img = Image.new("RGB", (640, 640), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_health_check(client):
    """Verify health endpoint reports correct structure and does not leak keys."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "online"
    assert "apiConfigured" in data
    assert "defaultCoordinateSpace" in data
    # Ensure no secret strings in response
    assert "key" not in data or data.get("key") is None
    assert "Authorization" not in resp.headers


def test_predict_rejects_empty_file(client):
    """Verify proxy rejects 0-byte uploaded files."""
    files = {"file": ("empty.jpg", b"", "image/jpeg")}
    resp = client.post("/api/predict", files=files)
    assert resp.status_code == 400
    data = resp.json()
    assert data["detail"]["error"] == "empty_file"


def test_predict_rejects_non_image_mime(client):
    """Verify proxy rejects invalid mime types like text/plain."""
    files = {"file": ("test.txt", b"plain text content", "text/plain")}
    resp = client.post("/api/predict", files=files)
    assert resp.status_code == 400
    data = resp.json()
    assert data["detail"]["error"] == "invalid_mime_type"


def test_predict_mock_mode(client, sample_jpeg_bytes):
    """Verify mock inference returns valid detection schema without calling upstream."""
    files = {"file": ("test.jpg", sample_jpeg_bytes, "image/jpeg")}
    data = {"mock": "true", "imgsz": "640", "coordinateSpace": "letterbox_640"}
    resp = client.post("/api/predict", files=files, data=data)
    assert resp.status_code == 200
    res_json = resp.json()
    assert res_json["success"] is True
    assert res_json["coordinateSpace"] == "letterbox_640"
    assert res_json["inferenceWidth"] == 640
    assert len(res_json["results"]) > 0
    first_res = res_json["results"][0]
    assert "class" in first_res
    assert "name" in first_res
    assert "confidence" in first_res
    assert "box" in first_res
    assert all(k in first_res["box"] for k in ("x1", "y1", "x2", "y2"))


def test_predict_unconfigured_api_key(client, monkeypatch, sample_jpeg_bytes):
    """Verify that if the API key is not configured, the proxy returns 503 instead of failing silently."""
    monkeypatch.setenv("INFERENCE_API_KEY", "")
    monkeypatch.setenv("MOCK_INFERENCE", "false")
    files = {"file": ("test.jpg", sample_jpeg_bytes, "image/jpeg")}
    data = {"mock": "false"}
    resp = client.post("/api/predict", files=files, data=data)
    assert resp.status_code == 503
    err = resp.json()
    assert err["detail"]["error"] == "api_key_not_configured"


def test_no_api_key_in_frontend_or_source():
    """Security Invariant: Verify that no hardcoded secret keys are present in source files, frontend, or docs."""
    suspicious_patterns = [
        "ul_sample_redacted_key_1",  # Exposed key from original HTML
        "ul_sample_redacted_key_2",  # User inputted key
    ]
    # We check src/, static/, and docs/
    repo_root = Path(__file__).resolve().parent.parent
    check_dirs = [repo_root / "src", repo_root / "static", repo_root / "docs"]

    for d in check_dirs:
        if not d.exists():
            continue
        for file_path in d.rglob("*"):
            if file_path.is_file() and file_path.suffix in (".py", ".js", ".html", ".json", ".md"):
                text = file_path.read_text(encoding="utf-8", errors="ignore")
                for pat in suspicious_patterns:
                    assert pat not in text, f"Found exposed secret key in {file_path}: {pat}"


def test_export_yolo_endpoint_success_with_classes(client, sample_jpeg_bytes):
    """Verify /api/export-yolo produces valid zip with classes mapping preserved."""
    import base64
    import zipfile
    import yaml

    b64_img = base64.b64encode(sample_jpeg_bytes).decode("utf-8")
    payload = {
        "records": [
            {
                "recordId": "rec_api_1",
                "sessionId": "SES_PROXY",
                "imageFile": "images/frame1.jpg",
                "reviewStatus": "approved",
                "originalWidth": 640,
                "originalHeight": 640,
                "detections": [{"classId": 2, "className": "Sniper", "box": {"x1": 50, "y1": 50, "x2": 200, "y2": 200}}],
            }
        ],
        "images": {
            "images/frame1.jpg": f"data:image/jpeg;base64,{b64_img}",
        },
        "classes": {
            "2": {"name": "Sniper"},
            "0": {"name": "Striker"},
        },
    }

    resp = client.post("/api/export-yolo", json=payload)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        data_yaml = yaml.safe_load(zf.read("data.yaml").decode("utf-8"))
        assert data_yaml["names"][2] == "Sniper"
        assert data_yaml["names"][0] == "Striker"
        txt_content = zf.read("labels/train/frame1.txt").decode("utf-8").strip()
        assert txt_content.startswith("2 ")


def test_export_yolo_endpoint_blocks_unknown_coord_space(client, sample_jpeg_bytes):
    """Verify /api/export-yolo rejects records with coordinateSpace: 'unknown'."""
    import base64

    b64_img = base64.b64encode(sample_jpeg_bytes).decode("utf-8")
    payload = {
        "records": [
            {
                "recordId": "rec_unknown",
                "sessionId": "SES_PROXY",
                "imageFile": "images/frame1.jpg",
                "coordinateSpace": "unknown",
                "reviewStatus": "approved",
                "originalWidth": 640,
                "originalHeight": 640,
                "detections": [{"classId": 0, "className": "Target", "box": {"x1": 10, "y1": 10, "x2": 50, "y2": 50}}],
            }
        ],
        "images": {
            "images/frame1.jpg": b64_img,
        },
    }

    resp = client.post("/api/export-yolo", json=payload)
    assert resp.status_code == 400
    err = resp.json()
    assert err["detail"]["error"] == "validation_failed"
    assert "coordinateSpace is unverified" in err["detail"]["message"]


def test_endpoint_url_normalization():
    """Verify endpoint URL normalization handles bare hostnames, trailing slashes, query params, fragments, and paths."""
    from src.config import normalize_inference_url, DEFAULT_INFERENCE_API_URL

    # Bare domain without path -> appends /predict
    url1 = "https://predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app"
    assert normalize_inference_url(url1) == "https://predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app/predict"

    # Trailing slash -> appends /predict cleanly
    url2 = "https://predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app/"
    assert normalize_inference_url(url2) == "https://predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app/predict"

    # Already has /predict -> preserved
    url3 = "https://predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app/predict"
    assert normalize_inference_url(url3) == "https://predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app/predict"

    # Preserves query parameters
    url4 = "https://predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app?token=123"
    assert normalize_inference_url(url4) == "https://predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app/predict?token=123"

    # Trailing slash with query parameter
    url5 = "https://predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app/?conf=0.25"
    assert normalize_inference_url(url5) == "https://predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app/predict?conf=0.25"

    # Missing scheme -> defaults to https://
    url6 = "predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app"
    assert normalize_inference_url(url6) == "https://predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app/predict"

    # Preserves fragment
    url7 = "https://predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app#live"
    assert normalize_inference_url(url7) == "https://predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app/predict#live"

    # Preserves custom sub-paths
    url8 = "http://127.0.0.1:8000/custom/inference"
    assert normalize_inference_url(url8) == "http://127.0.0.1:8000/custom/inference"

    # None or empty string -> returns DEFAULT_INFERENCE_API_URL
    assert normalize_inference_url(None) == DEFAULT_INFERENCE_API_URL
    assert normalize_inference_url("") == DEFAULT_INFERENCE_API_URL
    assert normalize_inference_url("   ") == DEFAULT_INFERENCE_API_URL


def test_health_reports_updated_endpoint(client):
    """Verify health endpoint dynamically reflects normalized live endpoint without secret leakage."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app" in data["upstreamEndpoint"]
    assert "Authorization" not in resp.headers
    assert "key" not in data or data.get("key") is None


def test_resolve_api_key_scenarios(monkeypatch, tmp_path):
    """Verify multi-tier API key resolution precedence, quotes stripping, and empty string handling."""
    from src.config import resolve_api_key

    # 1. Direct process environment takes precedence with quotes stripped
    monkeypatch.setenv("INFERENCE_API_KEY", '"ul_test_env_key"')
    assert resolve_api_key() == "ul_test_env_key"

    # 2. Explicit empty string in environment does NOT fallback (fail-closed)
    monkeypatch.setenv("INFERENCE_API_KEY", "")
    assert resolve_api_key() is None

    # 3. SCHALE_INFERENCE_KEY secondary environment fallback
    monkeypatch.delenv("INFERENCE_API_KEY", raising=False)
    monkeypatch.setenv("SCHALE_INFERENCE_KEY", "'ul_schale_fallback'")
    assert resolve_api_key() == "ul_schale_fallback"

    monkeypatch.delenv("SCHALE_INFERENCE_KEY", raising=False)

    # 4. Local .env file parsing with spaces around = and inline comment
    repo_root = Path(__file__).resolve().parent.parent
    dotenv_path = repo_root / ".env"
    original_dotenv_content = dotenv_path.read_text(encoding="utf-8") if dotenv_path.exists() else None
    try:
        dotenv_path.write_text(
            "INFERENCE_API_KEY = ul_from_dotenv # secret key\n"
            "INFERENCE_API_URL = https://custom-endpoint.a.run.app # endpoint\n",
            encoding="utf-8",
        )
        assert resolve_api_key() == "ul_from_dotenv"

        from src.config import get_config, parse_dotenv
        env_dict = parse_dotenv()
        assert env_dict.get("INFERENCE_API_KEY") == "ul_from_dotenv"
        assert env_dict.get("INFERENCE_API_URL") == "https://custom-endpoint.a.run.app"

        monkeypatch.delenv("INFERENCE_API_URL", raising=False)
        monkeypatch.delenv("SCHALE_INFERENCE_URL", raising=False)
        cfg = get_config()
        assert cfg.inference_api_key == "ul_from_dotenv"
        assert cfg.inference_api_url == "https://custom-endpoint.a.run.app/predict"
    finally:
        if original_dotenv_content is not None:
            dotenv_path.write_text(original_dotenv_content, encoding="utf-8")
        elif dotenv_path.exists():
            dotenv_path.unlink()


def test_predict_forwards_conf_and_iou(client, monkeypatch, sample_jpeg_bytes):
    """Verify that conf and iou parameters in form data are forwarded to upstream."""
    monkeypatch.setenv("INFERENCE_API_KEY", "ul_dummy_key")
    monkeypatch.setenv("MOCK_INFERENCE", "false")

    captured_data = {}

    class MockResponse:
        status_code = 200

        def json(self):
            return {
                "images": [{"shape": [640, 640], "results": []}],
                "metadata": {"task": "detect"},
            }

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def post(self, url, headers=None, files=None, data=None):
            captured_data.update(data or {})
            return MockResponse()

    monkeypatch.setattr("httpx.AsyncClient", MockAsyncClient)

    files = {"file": ("test.jpg", sample_jpeg_bytes, "image/jpeg")}
    data = {"imgsz": "640", "conf": "0.15", "iou": "0.45"}
    resp = client.post("/api/predict", files=files, data=data)
    assert resp.status_code == 200
    assert captured_data.get("conf") == "0.15"
    assert captured_data.get("iou") == "0.45"
    assert captured_data.get("imgsz") == "640"



def test_upstream_401_redacts_secret_in_error(client, monkeypatch, sample_jpeg_bytes):
    """Verify that when upstream returns 401 with a body echoing the key, the key is redacted."""
    monkeypatch.setenv("INFERENCE_API_KEY", "ul_super_secret_key_12345")
    monkeypatch.setenv("MOCK_INFERENCE", "false")

    class MockResponse:
        status_code = 401

        def json(self):
            return {
                "error": "Unauthorized",
                "message": "Invalid token: ul_super_secret_key_12345 provided",
            }

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def post(self, *args, **kwargs):
            return MockResponse()

    monkeypatch.setattr("httpx.AsyncClient", MockAsyncClient)

    files = {"file": ("test.jpg", sample_jpeg_bytes, "image/jpeg")}
    resp = client.post("/api/predict", files=files)
    assert resp.status_code == 401
    err = resp.json()
    assert err["detail"]["error"] == "upstream_api_error"
    # Ensure raw secret is NEVER in response body
    assert "ul_super_secret_key_12345" not in str(err)
    assert "[REDACTED]" in err["detail"]["message"]


