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
    """Security Invariant: Verify that no hardcoded secret keys are present in source files or frontend."""
    suspicious_patterns = [
        "ul_sample_redacted_key_1",  # Exposed key from original HTML
    ]
    # We check src/ and static/
    repo_root = Path(__file__).resolve().parent.parent
    check_dirs = [repo_root / "src", repo_root / "static"]

    for d in check_dirs:
        if not d.exists():
            continue
        for file_path in d.rglob("*"):
            if file_path.is_file() and file_path.suffix in (".py", ".js", ".html", ".json"):
                text = file_path.read_text(encoding="utf-8", errors="ignore")
                for pat in suspicious_patterns:
                    assert pat not in text, f"Found exposed secret key in {file_path}: {pat}"
