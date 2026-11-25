"""tests/test_onnx_and_session_preservation.py
Comprehensive tests for:
1. Local ONNX Runtime YOLO inference engine (src/onnx_detector.py)
2. Dual Cloud/ONNX independent ON/OFF toggling in /api/predict and model endpoints
3. Guaranteed local session file preservation across session close & unload events
"""

import io
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest

from src.onnx_detector import OnnxYoloDetector, sanitize_onnx_filename
from src.server import app
from src.session_manager import SessionManager


def _make_jpeg_bytes(width: int = 640, height: int = 640) -> bytes:
    img = Image.new("RGB", (width, height), color=(114, 114, 114))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_sanitize_onnx_filename_blocks_traversal(tmp_path: Path):
    models_dir = tmp_path / "models"
    models_dir.mkdir()

    with pytest.raises(ValueError):
        sanitize_onnx_filename("../secret.onnx", models_dir)
    with pytest.raises(ValueError):
        sanitize_onnx_filename("sub/model.onnx", models_dir)
    with pytest.raises(ValueError):
        sanitize_onnx_filename("weights.pt", models_dir)
    with pytest.raises(ValueError):
        sanitize_onnx_filename("bad\x00.onnx", models_dir)

    valid = sanitize_onnx_filename("best_yolo11n.onnx", models_dir)
    assert valid == (models_dir / "best_yolo11n.onnx").resolve()


def test_onnx_postprocess_ultralytics_and_nms_shapes(tmp_path: Path):
    detector = OnnxYoloDetector(models_dir=tmp_path / "models")
    names = {0: "Target_A", 1: "Target_B"}

    # 1. Standard Ultralytics [1, 6, 100] -> (4 + 2 classes, 100 anchors)
    raw = np.zeros((1, 6, 100), dtype=np.float32)
    # Anchor 0: cx=320, cy=320, w=100, h=120, cls0=0.92, cls1=0.05
    raw[0, :, 0] = [320.0, 320.0, 100.0, 120.0, 0.92, 0.05]
    # Anchor 1: overlapping box with lower conf (should be suppressed by NMS)
    raw[0, :, 1] = [322.0, 322.0, 98.0, 118.0, 0.70, 0.02]
    # Anchor 2: distinct class 1 box
    raw[0, :, 2] = [100.0, 100.0, 40.0, 60.0, 0.10, 0.85]
    # Anchor 3: below threshold
    raw[0, :, 3] = [500.0, 500.0, 20.0, 20.0, 0.05, 0.05]

    res = detector._postprocess(
        outputs=[raw],
        conf_thres=0.25,
        iou_thres=0.45,
        pad_x=0,
        pad_y=0,
        scale=1.0,
        orig_w=640,
        orig_h=640,
        class_names=names,
    )
    assert len(res) == 2
    assert res[0]["class"] == 0 and res[0]["name"] == "Target_A"
    assert res[0]["source"] == "onnx_local"
    assert res[1]["class"] == 1 and res[1]["name"] == "Target_B"

    # 2. End-to-end NMS-embedded [1, 2, 6] -> [x1, y1, x2, y2, conf, cls]
    e2e = np.array([[[10.0, 20.0, 110.0, 120.0, 0.88, 1.0], [0.0, 0.0, 5.0, 5.0, 0.10, 0.0]]], dtype=np.float32)
    res_e2e = detector._postprocess(
        outputs=[e2e],
        conf_thres=0.25,
        iou_thres=0.45,
        pad_x=0,
        pad_y=0,
        scale=1.0,
        orig_w=640,
        orig_h=640,
        class_names=names,
    )
    assert len(res_e2e) == 1
    assert res_e2e[0]["class"] == 1
    assert res_e2e[0]["box"] == {"x1": 10.0, "y1": 20.0, "x2": 110.0, "y2": 120.0}


def test_predict_both_cloud_and_onnx_off_standby(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SCHALE_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("INFERENCE_API_KEY", "")
    client = TestClient(app)

    jpg = _make_jpeg_bytes()
    res = client.post(
        "/api/predict",
        files={"file": ("frame.jpg", jpg, "image/jpeg")},
        data={"useCloud": "false", "useOnnx": "false", "coordinateSpace": "letterbox_640"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["results"] == []
    assert body["metadata"]["cloudEnabled"] is False
    assert body["metadata"]["onnxEnabled"] is False
    assert body["metadata"]["mode"] == "standby_both_off"


def test_predict_local_onnx_only_works_offline_without_api_key(tmp_path: Path, monkeypatch):
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "custom_yolo.onnx").write_bytes(b"FAKE_ONNX_HEADER")
    monkeypatch.setenv("SCHALE_MODELS_DIR", str(models_dir))
    monkeypatch.setenv("INFERENCE_API_KEY", "")

    mock_session = MagicMock()
    mock_input = MagicMock()
    mock_input.name = "images"
    mock_input.shape = [1, 3, 640, 640]
    mock_session.get_inputs.return_value = [mock_input]
    mock_session.get_providers.return_value = ["CPUExecutionProvider"]
    mock_meta = MagicMock()
    mock_meta.custom_metadata_map = {"names": "{0: 'Drone', 1: 'Turret'}"}
    mock_session.get_modelmeta.return_value = mock_meta

    fake_out = np.zeros((1, 6, 100), dtype=np.float32)
    fake_out[0, :, 0] = [320.0, 240.0, 80.0, 80.0, 0.91, 0.01]
    mock_session.run.return_value = [fake_out]

    client = TestClient(app)
    with patch("src.onnx_detector.ort.InferenceSession", return_value=mock_session):
        load_res = client.post("/api/models/onnx/load", json={"modelName": "custom_yolo.onnx"})
        assert load_res.status_code == 200
        assert load_res.json()["loaded"] is True

        jpg = _make_jpeg_bytes()
        pred_res = client.post(
            "/api/predict",
            files={"file": ("frame.jpg", jpg, "image/jpeg")},
            data={"useCloud": "false", "useOnnx": "true", "onnxModel": "custom_yolo.onnx"},
        )
        assert pred_res.status_code == 200
        data = pred_res.json()
        assert data["metadata"]["cloudEnabled"] is False
        assert data["metadata"]["onnxEnabled"] is True
        assert data["metadata"]["onnxModel"] == "custom_yolo.onnx"
        assert len(data["results"]) == 1
        assert data["results"][0]["name"] == "Drone"
        assert data["results"][0]["source"] == "onnx_local"


def test_predict_local_onnx_without_model_fails_closed(tmp_path: Path, monkeypatch):
    models_dir = tmp_path / "models_empty"
    models_dir.mkdir()
    monkeypatch.setenv("SCHALE_MODELS_DIR", str(models_dir))

    # Reset singleton so it picks up empty models_dir
    import src.onnx_detector as od
    od._detector_instance = None

    client = TestClient(app)
    jpg = _make_jpeg_bytes()
    res = client.post(
        "/api/predict",
        files={"file": ("frame.jpg", jpg, "image/jpeg")},
        data={"useCloud": "false", "useOnnx": "true"},
    )
    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "onnx_model_not_loaded"


def test_predict_hybrid_cloud_and_onnx_merges_results(tmp_path: Path, monkeypatch):
    models_dir = tmp_path / "models_hybrid"
    models_dir.mkdir()
    (models_dir / "hybrid.onnx").write_bytes(b"FAKE_ONNX")
    monkeypatch.setenv("SCHALE_MODELS_DIR", str(models_dir))
    monkeypatch.setenv("INFERENCE_API_KEY", "ul_test_valid_mock_key_1234567890")

    import src.onnx_detector as od
    od._detector_instance = None

    mock_session = MagicMock()
    mock_input = MagicMock()
    mock_input.name = "images"
    mock_input.shape = [1, 3, 640, 640]
    mock_session.get_inputs.return_value = [mock_input]
    mock_session.get_providers.return_value = ["CPUExecutionProvider"]
    mock_meta = MagicMock()
    mock_meta.custom_metadata_map = {"names": "{0: 'LocalTarget'}"}
    mock_session.get_modelmeta.return_value = mock_meta
    fake_out = np.zeros((1, 5, 100), dtype=np.float32)
    fake_out[0, :, 0] = [100.0, 100.0, 50.0, 50.0, 0.89]
    mock_session.run.return_value = [fake_out]

    cloud_payload = {
        "results": [
            {"class": 2, "name": "CloudTarget", "confidence": 0.94, "box": {"x1": 200, "y1": 200, "x2": 300, "y2": 300}}
        ],
        "metadata": {"task": "detect"},
    }

    client = TestClient(app)
    with patch("src.onnx_detector.ort.InferenceSession", return_value=mock_session), patch(
        "src.server._execute_upstream_inference", new=AsyncMock(return_value=cloud_payload)
    ):
        res = client.post(
            "/api/predict",
            files={"file": ("frame.jpg", _make_jpeg_bytes(), "image/jpeg")},
            data={"useCloud": "true", "useOnnx": "true", "onnxModel": "hybrid.onnx"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["metadata"]["cloudEnabled"] is True
        assert body["metadata"]["onnxEnabled"] is True
        sources = {r["source"] for r in body["results"]}
        assert sources == {"cloud", "onnx_local"}


def test_onnx_upload_and_list_endpoints(tmp_path: Path, monkeypatch):
    models_dir = tmp_path / "models_up"
    models_dir.mkdir()
    monkeypatch.setenv("SCHALE_MODELS_DIR", str(models_dir))
    import src.onnx_detector as od
    od._detector_instance = None

    mock_session = MagicMock()
    mock_input = MagicMock()
    mock_input.name = "images"
    mock_input.shape = [1, 3, 640, 640]
    mock_session.get_inputs.return_value = [mock_input]
    mock_session.get_providers.return_value = ["CPUExecutionProvider"]
    mock_meta = MagicMock()
    mock_meta.custom_metadata_map = {}
    mock_session.get_modelmeta.return_value = mock_meta

    client = TestClient(app)
    with patch("src.onnx_detector.ort.InferenceSession", return_value=mock_session):
        up_res = client.post(
            "/api/models/onnx/upload",
            files={"file": ("uploaded_model.onnx", b"ONNX_BINARY_WEIGHTS", "application/octet-stream")},
        )
        assert up_res.status_code == 200
        assert up_res.json()["activeModel"] == "uploaded_model.onnx"
        assert (models_dir / "uploaded_model.onnx").exists()

        list_res = client.get("/api/models/onnx")
        assert list_res.status_code == 200
        assert any(m["name"] == "uploaded_model.onnx" for m in list_res.json()["models"])

        bad_res = client.post(
            "/api/models/onnx/upload",
            files={"file": ("../evil.onnx", b"BAD", "application/octet-stream")},
        )
        assert bad_res.status_code == 400


def test_session_files_preserved_locally_after_restart(tmp_path: Path):
    data_dir = tmp_path / "sessions"
    mgr1 = SessionManager(base_dir=data_dir)
    import base64
    b64 = "data:image/jpeg;base64," + base64.b64encode(_make_jpeg_bytes(64, 64)).decode("ascii")
    mgr1.save_session(
        session_id="SES_PRESERVE_01",
        records=[{
            "recordId": "rec_1",
            "sessionId": "SES_PRESERVE_01",
            "imageFile": "images/rec_1_orig.jpg",
            "originalWidth": 64,
            "originalHeight": 64,
            "reviewStatus": "pending",
            "origBase64": b64,
            "reviewedAnnotations": [],
        }],
    )
    del mgr1

    # Simulate fresh server restart with new SessionManager instance
    mgr2 = SessionManager(base_dir=data_dir)
    sessions = mgr2.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["sessionId"] == "SES_PRESERVE_01"
    loaded = mgr2.load_session("SES_PRESERVE_01", include_base64=True)
    assert len(loaded["records"]) == 1
    assert (data_dir / "SES_PRESERVE_01" / "images" / "rec_1_orig.jpg").exists()


def test_node_reviewer_flush_unsaved_on_exit():
    node_script = r"""
    const fs = require('fs');
    const vm = require('vm');
    const reviewerSrc = fs.readFileSync('d:/myoi/static/reviewer.js', 'utf8');

    const domStub = () => ({
        classList: { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false },
        style: {},
        querySelector: () => ({ innerText: '' }),
        getContext: () => ({ clearRect: () => {}, save: () => {}, restore: () => {}, setTransform: () => {} }),
        addEventListener: () => {},
        appendChild: () => {},
        innerHTML: '',
        innerText: '',
        value: '',
        width: 640,
        height: 480,
    });
    const fetchCalls = [];
    const context = {
        window: { addEventListener: () => {} },
        document: {
            getElementById: () => domStub(),
            addEventListener: () => {},
            querySelectorAll: () => [],
        },
        fetch: (url, opts) => {
            fetchCalls.push({ url, opts });
            return Promise.resolve({ ok: true, json: async () => ({ success: true }) });
        },
        Blob: function(parts, opts) { return { parts, opts }; },
        navigator: {},
        localStorage: { getItem: () => null, setItem: () => {} },
        Image: function() { return {}; },
        console,
        setTimeout,
        clearTimeout,
        JSON,
        Date,
        Math,
        Set,
        Object,
        Array,
        Error,
        addEventListener: () => {},
    };
    context.window = context;
    vm.createContext(context);
    vm.runInContext(reviewerSrc, context);

    const rm = new context.window.ReviewManager();
    rm.currentSessionId = 'SES_EXIT_TEST';
    rm.addRecord({
        recordId: 'rec_unsaved_1',
        sessionId: 'SES_EXIT_TEST',
        imageFile: 'images/rec_unsaved_1_orig.jpg',
        originalWidth: 640,
        originalHeight: 480,
        reviewStatus: 'pending',
        origBase64: 'data:image/jpeg;base64,/9j/4AAQ',
        reviewedAnnotations: [],
        _savedOnServer: false,
    });
    rm.flushUnsavedRecordsOnExit();

    if (fetchCalls.length !== 1) throw new Error('Expected 1 keepalive fetch, got ' + fetchCalls.length);
    if (fetchCalls[0].url !== '/api/sessions/save') throw new Error('Wrong URL');
    if (fetchCalls[0].opts.keepalive !== true) throw new Error('Expected keepalive: true');
    const payload = JSON.parse(fetchCalls[0].opts.body);
    if (payload.sessionId !== 'SES_EXIT_TEST' || payload.records.length !== 1) {
        throw new Error('Invalid exit payload');
    }
    console.log('NODE_EXIT_FLUSH_OK');
    """
    proc = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert proc.returncode == 0, f"Node failed: {proc.stderr}"
    assert "NODE_EXIT_FLUSH_OK" in proc.stdout
