"""tests/test_session_storage.py
Comprehensive unit, security, and API tests for SessionManager,
historical session retrieval, fail-closed validation, and session endpoints.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.config import AppConfig, get_config
from src.server import app
from src.session_manager import (
    SessionManager,
    decode_image_base64,
    get_session_manager,
    validate_image_payload,
    validate_record_invariants,
    validate_session_id,
)


def create_test_image_bytes(format_name: str = "JPEG", size: tuple[int, int] = (640, 480), color: tuple[int, int, int] = (100, 150, 200)) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format=format_name)
    return buf.getvalue()


@pytest.fixture
def sample_jpeg():
    return create_test_image_bytes("JPEG", (640, 480), (120, 140, 160))


@pytest.fixture
def sample_jpeg_b64(sample_jpeg):
    b64 = base64.b64encode(sample_jpeg).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


@pytest.fixture
def temp_session_mgr(tmp_path: Path):
    return SessionManager(base_dir=tmp_path)


@pytest.fixture
def test_client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SCHALE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INFERENCE_API_KEY", "test-secret-key")
    # Reset singleton session manager to point to tmp_path
    get_session_manager(base_dir=tmp_path)
    return TestClient(app)


# ============================================================================
# 1. Validation and Security Invariant Tests
# ============================================================================

def test_validate_session_id_valid(tmp_path: Path):
    """Valid session IDs matching [a-zA-Z0-9_-]+ return valid child directories."""
    valid_ids = ["SES_20261118_180000", "test-session-123", "Session_Alpha_01", "simple123"]
    for sid in valid_ids:
        target = validate_session_id(sid, tmp_path)
        assert target == tmp_path / sid
        assert tmp_path in target.parents


@pytest.mark.parametrize("malicious_id", [
    "../evil",
    "..\\evil",
    "../../etc/passwd",
    "foo/bar",
    "foo\\bar",
    "session\x00null",
    "..",
    ".",
    "/root",
    "C:\\Windows",
    "session with spaces",
    "session!@#$",
    "",
    "   ",
])
def test_validate_session_id_rejects_path_traversal_and_illegal_chars(malicious_id, tmp_path: Path):
    """Path traversal markers, control chars, null bytes, and non-alphanumeric chars are rejected."""
    with pytest.raises(ValueError):
        validate_session_id(malicious_id, tmp_path)


def test_validate_session_id_rejects_overlong(tmp_path: Path):
    """Session IDs longer than 128 characters are rejected."""
    overlong = "a" * 129
    with pytest.raises(ValueError) as exc:
        validate_session_id(overlong, tmp_path)
    assert "maximum length" in str(exc.value)


def test_validate_image_payload_formats(sample_jpeg):
    """Valid JPEG, PNG, and WebP payloads pass validation."""
    validate_image_payload(sample_jpeg, "test.jpg")

    png_bytes = create_test_image_bytes("PNG")
    validate_image_payload(png_bytes, "test.png")

    webp_bytes = create_test_image_bytes("WEBP")
    validate_image_payload(webp_bytes, "test.webp")


def test_validate_image_payload_rejects_0_byte_and_corrupt():
    """Fail-closed invariant: 0-byte or corrupted payloads are strictly rejected."""
    # 0 bytes
    with pytest.raises(ValueError) as exc:
        validate_image_payload(b"", "empty.jpg")
    assert "0 bytes" in str(exc.value)

    # < 16 bytes
    with pytest.raises(ValueError) as exc:
        validate_image_payload(b"short", "short.jpg")
    assert "too small" in str(exc.value)

    # Invalid magic bytes
    with pytest.raises(ValueError) as exc:
        validate_image_payload(b"GIF89a" + b"\x00" * 20, "fake.jpg")
    assert "magic bytes" in str(exc.value)


def test_decode_image_base64_variants(sample_jpeg, sample_jpeg_b64):
    """Base64 decoding handles raw base64 and data URLs correctly."""
    # With data URL prefix
    decoded_1 = decode_image_base64(sample_jpeg_b64, "frame.jpg")
    assert decoded_1 == sample_jpeg

    # Without prefix (raw base64)
    raw_b64 = sample_jpeg_b64.split(",", 1)[1]
    decoded_2 = decode_image_base64(raw_b64, "frame.jpg")
    assert decoded_2 == sample_jpeg

    # Empty base64
    with pytest.raises(ValueError):
        decode_image_base64("", "empty.jpg")

    # Corrupt base64
    with pytest.raises(ValueError):
        decode_image_base64("not-valid-base-64!!!", "corrupt.jpg")


def test_validate_record_invariants_valid():
    """Valid record with non-negative class ID and finite bounded boxes passes validation."""
    record = {
        "recordId": "rec_001",
        "originalWidth": 1920,
        "originalHeight": 1080,
        "reviewedAnnotations": [
            {
                "classId": 0,
                "className": "Sensei",
                "confidence": 0.95,
                "box": {"x1": 100.0, "y1": 150.0, "x2": 400.0, "y2": 550.0},
            }
        ],
    }
    validate_record_invariants(record)


@pytest.mark.parametrize("invalid_record,error_substr", [
    ("not-a-dict", "must be a dictionary"),
    ({"recordId": ""}, "Invalid or missing recordId"),
    ({"recordId": "rec/bad"}, "Invalid or missing recordId"),
    ({"recordId": "rec_1", "originalWidth": 0, "originalHeight": 1080}, "invalid dimensions"),
    ({"recordId": "rec_1", "originalWidth": 1920, "originalHeight": -10}, "invalid dimensions"),
    ({"recordId": "rec_1", "originalWidth": 1920, "originalHeight": 1080, "reviewedAnnotations": "not-a-list"}, "must be a list"),
    (
        {"recordId": "rec_1", "originalWidth": 1920, "originalHeight": 1080, "reviewedAnnotations": [{"classId": -1, "box": {"x1": 10, "y1": 10, "x2": 50, "y2": 50}}]},
        "invalid classId",
    ),
    (
        {"recordId": "rec_1", "originalWidth": 1920, "originalHeight": 1080, "reviewedAnnotations": [{"classId": True, "box": {"x1": 10, "y1": 10, "x2": 50, "y2": 50}}]},
        "invalid classId",
    ),
    (
        {"recordId": "rec_1", "originalWidth": 1920, "originalHeight": 1080, "reviewedAnnotations": [{"classId": 0, "box": {"x1": 100, "y1": 100, "x2": 50, "y2": 200}}]},
        "inverted or zero-area",
    ),
    (
        {"recordId": "rec_1", "originalWidth": 1920, "originalHeight": 1080, "reviewedAnnotations": [{"classId": 0, "box": {"x1": 10, "y1": 10, "x2": 10, "y2": 50}}]},
        "inverted or zero-area",
    ),
    (
        {"recordId": "rec_1", "originalWidth": 1920, "originalHeight": 1080, "reviewedAnnotations": [{"classId": 0, "box": {"x1": float("nan"), "y1": 10, "x2": 50, "y2": 50}}]},
        "not a finite number",
    ),
    (
        {"recordId": "rec_1", "originalWidth": 1920, "originalHeight": 1080, "reviewedAnnotations": [{"classId": 0, "box": {"x1": -50.0, "y1": 10, "x2": 50, "y2": 50}}]},
        "exceeds frame boundaries",
    ),
    (
        {"recordId": "rec_1", "originalWidth": 1920, "originalHeight": 1080, "reviewedAnnotations": [{"classId": 0, "box": {"x1": 10, "y1": 10, "x2": 2500.0, "y2": 50}}]},
        "exceeds frame boundaries",
    ),
])
def test_validate_record_invariants_rejects_invalid(invalid_record, error_substr):
    """Data integrity invariants strictly reject corrupted records and bounding boxes."""
    with pytest.raises(ValueError) as exc:
        validate_record_invariants(invalid_record)
    assert error_substr in str(exc.value)


# ============================================================================
# 2. SessionManager Core Functionality Tests
# ============================================================================

def test_session_manager_save_and_load(temp_session_mgr: SessionManager, sample_jpeg_b64):
    """SessionManager saves session metadata, annotations, images atomically, and reloads them."""
    session_id = "SES_20261118_TEST01"
    records = [
        {
            "recordId": "rec_101",
            "sessionId": session_id,
            "capturedAtUtc": "2026-11-18T10:00:00Z",
            "originalWidth": 640,
            "originalHeight": 480,
            "reviewStatus": "pending",
            "origBase64": sample_jpeg_b64,
            "prevBase64": sample_jpeg_b64,
            "reviewedAnnotations": [
                {"classId": 0, "className": "Sensei", "confidence": 0.90, "box": {"x1": 50.0, "y1": 50.0, "x2": 200.0, "y2": 250.0}}
            ],
        },
        {
            "recordId": "rec_102",
            "sessionId": session_id,
            "capturedAtUtc": "2026-11-18T10:01:00Z",
            "originalWidth": 640,
            "originalHeight": 480,
            "reviewStatus": "approved",
            "origBase64": sample_jpeg_b64,
            "reviewedAnnotations": [],
        },
    ]

    classes = {"0": {"name": "Sensei", "on": True, "alt": True, "cnf": 0.5}}
    metadata = {"note": "unit test session"}

    save_res = temp_session_mgr.save_session(session_id, records, classes=classes, metadata=metadata)
    assert save_res["success"] is True
    assert save_res["recordCount"] == 2
    assert save_res["statusCounts"]["all"] == 2
    assert save_res["statusCounts"]["pending"] == 1
    assert save_res["statusCounts"]["approved"] == 1

    # Verify disk files
    sess_dir = temp_session_mgr.get_session_dir(session_id)
    assert (sess_dir / "session.json").exists()
    assert (sess_dir / "images" / "rec_101_orig.jpg").exists()
    assert (sess_dir / "previews" / "rec_101_prev.jpg").exists()
    assert (sess_dir / "images" / "rec_102_orig.jpg").exists()
    assert not (sess_dir / "session.json.tmp").exists()  # Atomic rename cleaned up tmp

    # Load session
    loaded = temp_session_mgr.load_session(session_id, include_base64=True)
    assert loaded["success"] is True
    assert loaded["sessionId"] == session_id
    assert loaded["recordCount"] == 2
    assert loaded["statusCounts"]["approved"] == 1
    assert loaded["statusCounts"]["pending"] == 1
    assert len(loaded["records"]) == 2

    first_rec = loaded["records"][0]
    assert first_rec["recordId"] == "rec_101"
    assert first_rec["origBase64"].startswith("data:image/jpeg;base64,")
    assert first_rec["prevBase64"].startswith("data:image/jpeg;base64,")
    assert len(first_rec["reviewedAnnotations"]) == 1


def test_session_manager_preserves_created_at_across_updates(temp_session_mgr: SessionManager, sample_jpeg_b64):
    """Subsequent saves of the same session preserve the original createdAtUtc timestamp."""
    session_id = "SES_20261118_TIMESTAMP_TEST"
    record = {
        "recordId": "rec_t1",
        "sessionId": session_id,
        "originalWidth": 640,
        "originalHeight": 480,
        "reviewStatus": "pending",
        "origBase64": sample_jpeg_b64,
        "reviewedAnnotations": [],
    }

    res1 = temp_session_mgr.save_session(session_id, [record])
    created_at = res1["createdAtUtc"]

    # Update review status to approved and save again
    record["reviewStatus"] = "approved"
    res2 = temp_session_mgr.save_session(session_id, [record])

    assert res2["createdAtUtc"] == created_at
    assert res2["statusCounts"]["approved"] == 1
    assert res2["statusCounts"]["pending"] == 0


def test_session_manager_save_rejects_missing_image(temp_session_mgr: SessionManager):
    """Saving record with neither origBase64 nor existing disk file raises ValueError."""
    session_id = "SES_NO_IMG"
    record = {
        "recordId": "rec_no_img",
        "sessionId": session_id,
        "originalWidth": 640,
        "originalHeight": 480,
        "reviewStatus": "pending",
        "reviewedAnnotations": [],
    }
    with pytest.raises(ValueError) as exc:
        temp_session_mgr.save_session(session_id, [record])
    assert "neither 'origBase64' image payload nor existing image on disk" in str(exc.value)


def test_session_manager_save_rejects_0_byte_image_on_disk(temp_session_mgr: SessionManager):
    """Fail-closed: 0-byte existing file on disk cannot substitute for image payload."""
    session_id = "SES_ZERO_BYTE"
    sess_dir = temp_session_mgr.get_session_dir(session_id)
    images_dir = sess_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    # Create 0-byte corrupt image
    (images_dir / "rec_zb_orig.jpg").write_bytes(b"")

    record = {
        "recordId": "rec_zb",
        "sessionId": session_id,
        "originalWidth": 640,
        "originalHeight": 480,
        "reviewStatus": "pending",
        "reviewedAnnotations": [],
    }
    with pytest.raises(ValueError) as exc:
        temp_session_mgr.save_session(session_id, [record])
    assert "neither 'origBase64' image payload nor existing image on disk" in str(exc.value)


def test_session_manager_list_sessions(temp_session_mgr: SessionManager, sample_jpeg_b64):
    """list_sessions discovers all saved sessions and aggregates status and object counts."""
    for idx, sid in enumerate(["SES_20261118_A", "SES_20261118_B"]):
        recs = [
            {
                "recordId": f"rec_{idx}_{r_idx}",
                "sessionId": sid,
                "originalWidth": 640,
                "originalHeight": 480,
                "reviewStatus": "approved" if r_idx == 0 else "pending",
                "origBase64": sample_jpeg_b64,
                "reviewedAnnotations": [{"classId": 0, "box": {"x1": 10, "y1": 10, "x2": 50, "y2": 50}}],
            }
            for r_idx in range(2)
        ]
        temp_session_mgr.save_session(sid, recs)

    sessions = temp_session_mgr.list_sessions()
    assert len(sessions) == 2
    sess_ids = [s["sessionId"] for s in sessions]
    assert "SES_20261118_A" in sess_ids
    assert "SES_20261118_B" in sess_ids

    first = sessions[0]
    assert first["recordCount"] == 2
    assert first["statusCounts"]["approved"] == 1
    assert first["statusCounts"]["pending"] == 1
    assert first["totalObjects"] == 2


def test_session_manager_delete_session(temp_session_mgr: SessionManager, sample_jpeg_b64):
    """delete_session removes the session directory and raises FileNotFoundError on missing session."""
    session_id = "SES_TO_DELETE"
    record = {
        "recordId": "rec_del_1",
        "sessionId": session_id,
        "originalWidth": 640,
        "originalHeight": 480,
        "reviewStatus": "pending",
        "origBase64": sample_jpeg_b64,
        "reviewedAnnotations": [],
    }
    temp_session_mgr.save_session(session_id, [record])
    assert temp_session_mgr.get_session_dir(session_id).exists()

    # Delete existing
    assert temp_session_mgr.delete_session(session_id) is True
    assert not temp_session_mgr.get_session_dir(session_id).exists()

    # Deleting again raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        temp_session_mgr.delete_session(session_id)


def test_session_manager_get_image_file_path_security(temp_session_mgr: SessionManager, sample_jpeg_b64):
    """get_image_file_path resolves valid files and rejects path traversal attempts."""
    session_id = "SES_IMAGE_TEST"
    record = {
        "recordId": "rec_img_1",
        "sessionId": session_id,
        "originalWidth": 640,
        "originalHeight": 480,
        "reviewStatus": "pending",
        "origBase64": sample_jpeg_b64,
        "reviewedAnnotations": [],
    }
    temp_session_mgr.save_session(session_id, [record])

    # Valid image access
    resolved = temp_session_mgr.get_image_file_path(session_id, "images/rec_img_1_orig.jpg")
    assert resolved is not None
    assert resolved.exists()

    # Direct filename fallback
    resolved_by_name = temp_session_mgr.get_image_file_path(session_id, "rec_img_1_orig.jpg")
    assert resolved_by_name is not None
    assert resolved_by_name == resolved

    # Path traversal rejected
    with pytest.raises(ValueError):
        temp_session_mgr.get_image_file_path(session_id, "../../../etc/passwd")

    with pytest.raises(ValueError):
        temp_session_mgr.get_image_file_path(session_id, "images/../../outside.jpg")

    # Nonexistent file returns None
    assert temp_session_mgr.get_image_file_path(session_id, "images/nonexistent.jpg") is None


# ============================================================================
# 3. FastAPI HTTP API Endpoints Tests
# ============================================================================

def test_api_list_sessions_endpoint(test_client: TestClient, sample_jpeg_b64):
    """GET /api/sessions returns empty list initially, then populated list after saving."""
    resp = test_client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
    assert resp.json()["sessions"] == []

    # Save a session via POST
    save_payload = {
        "sessionId": "SES_API_01",
        "records": [
            {
                "recordId": "rec_api_1",
                "sessionId": "SES_API_01",
                "originalWidth": 640,
                "originalHeight": 480,
                "reviewStatus": "pending",
                "origBase64": sample_jpeg_b64,
                "reviewedAnnotations": [],
            }
        ],
    }
    post_resp = test_client.post("/api/sessions/save", json=save_payload)
    assert post_resp.status_code == 200
    assert post_resp.json()["success"] is True

    # List again
    resp2 = test_client.get("/api/sessions")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["count"] == 1
    assert data["sessions"][0]["sessionId"] == "SES_API_01"


def test_api_get_session_endpoint(test_client: TestClient, sample_jpeg_b64):
    """GET /api/sessions/{session_id} returns session with records and handles 404/400."""
    session_id = "SES_API_GET_01"
    save_payload = {
        "sessionId": session_id,
        "records": [
            {
                "recordId": "rec_api_g1",
                "sessionId": session_id,
                "originalWidth": 640,
                "originalHeight": 480,
                "reviewStatus": "pending",
                "origBase64": sample_jpeg_b64,
                "reviewedAnnotations": [],
            }
        ],
    }
    test_client.post("/api/sessions/save", json=save_payload)

    # Valid session retrieval
    resp = test_client.get(f"/api/sessions/{session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sessionId"] == session_id
    assert len(body["records"]) == 1
    assert body["records"][0]["origBase64"].startswith("data:image/jpeg;base64,")

    # Nonexistent session -> 404
    resp_404 = test_client.get("/api/sessions/NONEXISTENT_SESSION_XYZ")
    assert resp_404.status_code == 404
    assert resp_404.json()["detail"]["error"] == "session_not_found"

    # Malicious path traversal / illegal characters -> 400
    resp_400 = test_client.get("/api/sessions/session..traversal")
    assert resp_400.status_code == 400
    assert resp_400.json()["detail"]["error"] == "invalid_session_id"


def test_api_save_session_validation_failures(test_client: TestClient):
    """POST /api/sessions/save rejects missing sessionId, path traversal, and invalid records."""
    # Missing session ID
    resp_no_id = test_client.post("/api/sessions/save", json={"records": []})
    assert resp_no_id.status_code == 400
    assert resp_no_id.json()["detail"]["error"] == "missing_session_id"

    # Malicious session ID
    resp_bad_id = test_client.post("/api/sessions/save", json={"sessionId": "../../bad", "records": []})
    assert resp_bad_id.status_code == 400
    assert resp_bad_id.json()["detail"]["error"] == "session_validation_error"

    # Invalid box in record
    bad_record_payload = {
        "sessionId": "SES_BAD_BOX",
        "records": [
            {
                "recordId": "rec_bad_1",
                "originalWidth": 640,
                "originalHeight": 480,
                "reviewedAnnotations": [{"classId": 0, "box": {"x1": 100, "y1": 100, "x2": 50, "y2": 50}}],
            }
        ],
    }
    resp_bad_box = test_client.post("/api/sessions/save", json=bad_record_payload)
    assert resp_bad_box.status_code == 400
    assert resp_bad_box.json()["detail"]["error"] == "session_validation_error"


def test_api_update_review_status_lifecycle(test_client: TestClient, sample_jpeg_b64):
    """Full lifecycle: Save pending session -> review & approve -> persist update -> verify reloaded state."""
    session_id = "SES_REVIEW_LIFECYCLE"
    initial_records = [
        {
            "recordId": "rec_life_1",
            "sessionId": session_id,
            "originalWidth": 640,
            "originalHeight": 480,
            "reviewStatus": "pending",
            "origBase64": sample_jpeg_b64,
            "reviewedAnnotations": [
                {"classId": 0, "className": "Sensei", "confidence": 0.85, "box": {"x1": 20.0, "y1": 20.0, "x2": 100.0, "y2": 100.0}}
            ],
        }
    ]

    # 1. Initial save
    save_resp = test_client.post("/api/sessions/save", json={"sessionId": session_id, "records": initial_records})
    assert save_resp.status_code == 200
    assert save_resp.json()["statusCounts"]["pending"] == 1
    assert save_resp.json()["statusCounts"]["approved"] == 0

    # 2. Simulate human reviewer approving the frame
    updated_records = [dict(initial_records[0])]
    updated_records[0]["reviewStatus"] = "approved"
    updated_records[0]["reviewedAtUtc"] = "2026-11-18T12:00:00Z"
    # Even without resending origBase64, the server can use existing file on disk
    updated_records[0].pop("origBase64", None)

    update_resp = test_client.post(f"/api/sessions/{session_id}/save", json={"records": updated_records})
    assert update_resp.status_code == 200
    assert update_resp.json()["statusCounts"]["approved"] == 1
    assert update_resp.json()["statusCounts"]["pending"] == 0

    # 3. Reload session and verify approved status persisted
    reload_resp = test_client.get(f"/api/sessions/{session_id}")
    assert reload_resp.status_code == 200
    reload_data = reload_resp.json()
    assert reload_data["statusCounts"]["approved"] == 1
    assert reload_data["records"][0]["reviewStatus"] == "approved"
    assert reload_data["records"][0]["origBase64"].startswith("data:image/jpeg;base64,")


def test_api_serve_session_image_endpoint(test_client: TestClient, sample_jpeg_b64):
    """GET /api/sessions/{session_id}/images/{filename} serves image correctly and rejects attacks."""
    session_id = "SES_IMAGE_API"
    save_payload = {
        "sessionId": session_id,
        "records": [
            {
                "recordId": "rec_img_api_1",
                "sessionId": session_id,
                "originalWidth": 640,
                "originalHeight": 480,
                "reviewStatus": "pending",
                "origBase64": sample_jpeg_b64,
                "reviewedAnnotations": [],
            }
        ],
    }
    test_client.post("/api/sessions/save", json=save_payload)

    # 1. Retrieve valid image
    img_resp = test_client.get(f"/api/sessions/{session_id}/images/rec_img_api_1_orig.jpg")
    assert img_resp.status_code == 200
    assert img_resp.headers["content-type"] == "image/jpeg"
    assert len(img_resp.content) > 100

    # 2. 404 for missing image
    missing_resp = test_client.get(f"/api/sessions/{session_id}/images/nonexistent.jpg")
    assert missing_resp.status_code == 404

    # 3. 400 for path traversal
    trav_resp = test_client.get(f"/api/sessions/{session_id}/images/..%2F..%2Fetc%2Fpasswd")
    assert trav_resp.status_code == 400


def test_api_delete_session_endpoint(test_client: TestClient, sample_jpeg_b64):
    """DELETE /api/sessions/{session_id} permanently removes the session from disk."""
    session_id = "SES_TO_REMOVE_VIA_API"
    save_payload = {
        "sessionId": session_id,
        "records": [
            {
                "recordId": "rec_rem_1",
                "sessionId": session_id,
                "originalWidth": 640,
                "originalHeight": 480,
                "reviewStatus": "pending",
                "origBase64": sample_jpeg_b64,
                "reviewedAnnotations": [],
            }
        ],
    }
    test_client.post("/api/sessions/save", json=save_payload)

    # Delete session
    del_resp = test_client.delete(f"/api/sessions/{session_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["success"] is True

    # Subsequent GET returns 404
    get_resp = test_client.get(f"/api/sessions/{session_id}")
    assert get_resp.status_code == 404


def test_api_yolo_export_resolves_image_from_disk_fallback(test_client: TestClient, sample_jpeg_b64):
    """Verify that exporting approved records from a saved session resolves image bytes from disk fallback."""
    session_id = "SES_EXPORT_FALLBACK"
    records = [
        {
            "recordId": "rec_fb_1",
            "sessionId": session_id,
            "originalWidth": 640,
            "originalHeight": 480,
            "reviewStatus": "approved",
            "origBase64": sample_jpeg_b64,
            "reviewedAnnotations": [
                {"classId": 0, "className": "Sensei", "confidence": 0.95, "box": {"x1": 50, "y1": 50, "x2": 200, "y2": 200}}
            ],
        }
    ]

    # Save session to disk first
    save_resp = test_client.post("/api/sessions/save", json={"sessionId": session_id, "records": records})
    assert save_resp.status_code == 200

    # Load session to get clean records as stored on disk
    loaded = test_client.get(f"/api/sessions/{session_id}").json()
    approved_recs = loaded["records"]

    # Now call export WITHOUT sending image base64 in the images map!
    export_payload = {
        "records": approved_recs,
        "images": {},  # Intentionally empty: relies on server-side disk resolution!
        "classes": {"0": {"name": "Sensei", "on": True, "alt": True, "cnf": 0.5}},
    }

    export_resp = test_client.post("/api/export-yolo", json=export_payload)
    assert export_resp.status_code == 200
    assert export_resp.headers["content-type"] == "application/zip"
    assert len(export_resp.content) > 1000


# ============================================================================
# 4. Strict Security, Concurrency, and Invariant Attack Vectors
# ============================================================================

def test_validate_record_invariants_rejects_boolean_dimensions_and_coords():
    """Booleans must not be accepted as integers or floats for dimensions and coordinates."""
    # Boolean width
    with pytest.raises(ValueError) as exc1:
        validate_record_invariants({
            "recordId": "rec_b1",
            "originalWidth": True,
            "originalHeight": 480,
            "reviewedAnnotations": [],
        })
    assert "invalid dimensions" in str(exc1.value)

    # Boolean coordinate
    with pytest.raises(ValueError) as exc2:
        validate_record_invariants({
            "recordId": "rec_b2",
            "originalWidth": 640,
            "originalHeight": 480,
            "reviewedAnnotations": [
                {"classId": 0, "box": {"x1": True, "y1": 10, "x2": 50, "y2": 50}}
            ],
        })
    assert "not a finite number" in str(exc2.value)


def test_save_session_rejects_path_traversal_image_file(temp_session_mgr: SessionManager, tmp_path: Path):
    """save_session strictly rejects path traversal or escaping file references in imageFile."""
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("CONFIDENTIAL_SENSEI_DATA", encoding="utf-8")

    session_id = "SES_TRAV_ATTACK"
    sess_dir = temp_session_mgr.get_session_dir(session_id)
    sess_dir.mkdir(parents=True, exist_ok=True)

    # Attempt path traversal via imageFile
    bad_record = {
        "recordId": "rec_trav_1",
        "sessionId": session_id,
        "originalWidth": 640,
        "originalHeight": 480,
        "imageFile": "../secret.txt",
        "reviewedAnnotations": [],
    }

    with pytest.raises(ValueError) as exc:
        temp_session_mgr.save_session(session_id, [bad_record])
    assert "Path traversal" in str(exc.value)

    # Ensure secret was NOT copied to images dir
    assert not (sess_dir / "images" / "rec_trav_1_orig.jpg").exists()


def test_load_session_path_traversal_containment(temp_session_mgr: SessionManager, tmp_path: Path):
    """load_session strictly refuses to read or leak files outside session directory."""
    secret_file = tmp_path / "outside_secret.key"
    secret_file.write_text("HIGHLY_SECRET_KEY", encoding="utf-8")

    session_id = "SES_LOAD_TRAV"
    sess_dir = temp_session_mgr.get_session_dir(session_id)
    sess_dir.mkdir(parents=True, exist_ok=True)

    # Manually craft session.json pointing to outside file
    crafted_json = {
        "sessionId": session_id,
        "records": [
            {
                "recordId": "rec_leak",
                "imageFile": "../outside_secret.key",
            }
        ],
    }
    (sess_dir / "session.json").write_text(json.dumps(crafted_json), encoding="utf-8")

    loaded = temp_session_mgr.load_session(session_id, include_base64=True)
    rec = loaded["records"][0]
    # origBase64 must NOT be populated from outside file!
    assert "origBase64" not in rec


def test_api_serve_session_image_rejects_non_image_files(test_client: TestClient, sample_jpeg_b64):
    """GET /api/sessions/{session_id}/images/{filename} rejects non-image files such as session.json."""
    session_id = "SES_SECURITY_EXT_TEST"
    save_payload = {
        "sessionId": session_id,
        "records": [
            {
                "recordId": "rec_sec_1",
                "sessionId": session_id,
                "originalWidth": 640,
                "originalHeight": 480,
                "reviewStatus": "pending",
                "origBase64": sample_jpeg_b64,
                "reviewedAnnotations": [],
            }
        ],
    }
    test_client.post("/api/sessions/save", json=save_payload)

    # Attempt to fetch session.json via images endpoint
    resp = test_client.get(f"/api/sessions/{session_id}/images/session.json")
    # Must be rejected (400 or 404), never 200 OK!
    assert resp.status_code in (400, 404)


def test_api_yolo_export_tolerates_none_base64_in_images_map(test_client: TestClient, sample_jpeg_b64):
    """POST /api/export-yolo handles images map with None values without crashing (disk fallback)."""
    session_id = "SES_EXPORT_TOLERANCE"
    records = [
        {
            "recordId": "rec_tol_1",
            "sessionId": session_id,
            "originalWidth": 640,
            "originalHeight": 480,
            "reviewStatus": "approved",
            "origBase64": sample_jpeg_b64,
            "reviewedAnnotations": [
                {"classId": 0, "className": "Sensei", "confidence": 0.95, "box": {"x1": 50, "y1": 50, "x2": 200, "y2": 200}}
            ],
        }
    ]

    test_client.post("/api/sessions/save", json={"sessionId": session_id, "records": records})
    loaded = test_client.get(f"/api/sessions/{session_id}").json()
    approved_recs = loaded["records"]

    # Send payload where images map contains None (e.g. from JSON serialization of undefined)
    export_payload = {
        "sessionId": session_id,
        "records": approved_recs,
        "images": {
            "images/rec_tol_1_orig.jpg": None,
        },
        "classes": {"0": {"name": "Sensei", "on": True, "alt": True, "cnf": 0.5}},
    }

    resp = test_client.post("/api/export-yolo", json=export_payload)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert len(resp.content) > 1000


def test_session_manager_concurrent_saves_no_permission_error(temp_session_mgr: SessionManager, sample_jpeg_b64):
    """Thread-safe locking and unique temp files prevent Windows PermissionError on concurrent saves."""
    import concurrent.futures

    session_id = "SES_CONCURRENT_THREAD_TEST"
    record = {
        "recordId": "rec_conc_1",
        "sessionId": session_id,
        "originalWidth": 640,
        "originalHeight": 480,
        "reviewStatus": "pending",
        "origBase64": sample_jpeg_b64,
        "reviewedAnnotations": [],
    }

    # First initial save to create images on disk
    temp_session_mgr.save_session(session_id, [record])

    def worker_save(idx):
        rec_copy = dict(record)
        rec_copy["reviewStatus"] = "approved" if idx % 2 == 0 else "pending"
        return temp_session_mgr.save_session(session_id, [rec_copy])

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(worker_save, i) for i in range(12)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 12
    for r in results:
        assert r["success"] is True

