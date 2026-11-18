"""tests/test_native_wgc_capture.py
Comprehensive test suite for Native Windows Graphics Capture (WGC) Sidecar.
Covers:
- WindowFinder enumeration and target matching
- Anti-Cheat Safe Zero-Injection Invariant verification
- NativeCaptureEngine multi-backend capture and error recovery
- CaptureService thread lifecycle, FPS regulation, and async queues
- FastAPI REST endpoints (/api/capture/*, /api/stream)
- WebSocket streaming route (/ws/stream)
"""

import asyncio
from pathlib import Path
import time
from typing import Generator

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from src.capture.window_finder import WindowFinder, WindowInfo
from src.capture.wgc_capture import NativeCaptureEngine
from src.capture.service import CaptureService, get_capture_service
from src.server import app


@pytest.fixture
def test_client() -> Generator[TestClient, None, None]:
    with TestClient(app) as client:
        yield client
    # Clean up any lingering capture service state
    svc = get_capture_service()
    if svc.is_running:
        svc.stop_capture()


# =====================================================================
# 1. WindowFinder Tests
# =====================================================================

def test_window_finder_mock_mode():
    """Verify WindowFinder correctly produces mock windows in test mode."""
    finder = WindowFinder(mock_mode=True)
    windows = finder.list_windows()
    assert len(windows) >= 3

    titles = [w.title for w in windows]
    assert any("BlueArchive" in t for t in titles)
    assert any("MapleStory" in t for t in titles)

    # Test dictionary conversion
    d = windows[0].to_dict()
    assert "hwnd" in d
    assert "title" in d
    assert "process_name" in d
    assert "width" in d
    assert "height" in d


def test_window_finder_query_matching():
    """Verify find_window matches by HWND, process name, and substring."""
    finder = WindowFinder(mock_mode=True)

    # 1. Exact HWND
    w = finder.find_window(1001)
    assert w is not None
    assert w.hwnd == 1001

    # 2. String numeric HWND
    w2 = finder.find_window("1002")
    assert w2 is not None
    assert w2.hwnd == 1002

    # 3. Process name
    w3 = finder.find_window("BlueArchive.exe")
    assert w3 is not None
    assert w3.process_name == "BlueArchive.exe"

    # 4. Title substring (case-insensitive)
    w4 = finder.find_window("tactical drop")
    assert w4 is not None
    assert "MapleStory" in w4.title

    # 5. Non-existent window
    w_none = finder.find_window("NonExistentWindow_999xyz")
    assert w_none is None


def test_window_finder_native_discovery_runs_safely():
    """Verify live Windows desktop enumeration runs safely without crashing."""
    finder = WindowFinder(mock_mode=False)
    windows = finder.list_windows()
    assert isinstance(windows, list)
    # Each discovered window must have positive dimensions
    for w in windows:
        assert isinstance(w.hwnd, int)
        assert w.width > 0
        assert w.height > 0


# =====================================================================
# 2. Anti-Cheat Safe Zero-Injection Invariant Tests
# =====================================================================

def test_zero_dll_injection_invariant():
    """Enforce REQ-SEC-ANTI-CHEAT: Out-of-process capture must NEVER use injection APIs.
    Verifies that no DLL injection or remote thread APIs exist in src/capture/.
    """
    capture_dir = Path(__file__).resolve().parent.parent / "src" / "capture"
    py_files = list(capture_dir.glob("*.py"))
    assert len(py_files) >= 3, "Expected capture package files"

    prohibited_apis = [
        "CreateRemoteThread",
        "VirtualAllocEx",
        "WriteProcessMemory",
        "SetWindowsHookEx",
        "LoadLibraryA",
        "LoadLibraryW",
        "QueueUserAPC",
        "NtCreateThreadEx",
    ]

    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8")
        for api in prohibited_apis:
            assert api not in content, (
                f"[ANTI-CHEAT VIOLATION] Prohibited injection API '{api}' detected in {py_file}! "
                f"Capture must remain strictly out-of-process and anti-cheat safe."
            )


# =====================================================================
# 3. NativeCaptureEngine Tests
# =====================================================================

def test_native_capture_engine_mock_frame():
    """Verify mock frame capture generates valid RGB image and tactical metadata."""
    engine = NativeCaptureEngine(mock_mode=True)
    img, meta = engine.capture_frame(target_hwnd=1001)

    assert img is not None
    assert isinstance(img, Image.Image)
    assert img.mode == "RGB"
    assert img.size == (1280, 720)

    assert meta["status"] == "ok"
    assert meta["hwnd"] == 1001
    assert meta["backend"] == "mock_synthetic"
    assert meta["elapsed_ms"] >= 0.0
    assert "timestamp" in meta


def test_native_capture_engine_jpeg_encoding():
    """Verify JPEG compression creates valid JPEG byte stream."""
    engine = NativeCaptureEngine(mock_mode=True)
    img, _ = engine.capture_frame(target_hwnd=0)
    assert img is not None

    jpeg_bytes = engine.encode_jpeg(img, quality=80)
    assert isinstance(jpeg_bytes, bytes)
    assert len(jpeg_bytes) > 1000

    # JPEG Start of Image (SOI) marker 0xFFD8
    assert jpeg_bytes[:2] == b"\xff\xd8"
    # JPEG End of Image (EOI) marker 0xFFD9
    assert jpeg_bytes[-2:] == b"\xff\xd9"


def test_native_capture_engine_invalid_window_fails_closed():
    """Verify non-existent window HWND returns explicit status instead of crashing."""
    engine = NativeCaptureEngine(mock_mode=False)
    # HWND 999999999 is guaranteed invalid
    img, meta = engine.capture_frame(target_hwnd=999999999)

    assert img is None
    assert meta["status"] in ("window_not_found", "capture_failed")
    assert "error" in meta


def test_native_capture_engine_desktop_capture():
    """Verify live desktop capture executes on Windows."""
    engine = NativeCaptureEngine(mock_mode=False)
    img, meta = engine.capture_frame(target_hwnd=0)

    assert meta["status"] == "ok"
    assert meta["hwnd"] == 0
    assert img is not None
    assert img.width > 0
    assert img.height > 0


# =====================================================================
# 4. CaptureService Tests
# =====================================================================

def test_capture_service_lifecycle_and_telemetry():
    """Verify background capture worker lifecycle, FPS regulation, and telemetry."""
    service = CaptureService(mock_mode=True)
    assert not service.is_running
    status_idle = service.get_status()
    assert not status_idle["running"]

    # Start capture
    res = service.start_capture(target=1001, fps=25, quality=85)
    assert res["success"]
    assert service.is_running

    # Let worker capture frames for a short duration
    time.sleep(0.4)

    status_running = service.get_status()
    assert status_running["running"]
    assert status_running["status"] == "capturing"
    assert status_running["totalFrames"] > 0
    assert status_running["targetFps"] == 25

    # Check latest frame
    jpeg_bytes, meta = service.get_latest_frame()
    assert jpeg_bytes is not None
    assert meta["status"] == "ok"

    latest_img = service.get_latest_image()
    assert latest_img is not None
    assert latest_img.size == (1280, 720)

    # Stop capture
    stop_res = service.stop_capture()
    assert stop_res["success"]
    assert not service.is_running

    status_stopped = service.get_status()
    assert not status_stopped["running"]
    assert status_stopped["status"] == "stopped"


def test_capture_service_async_queue_dispatch():
    """Verify async queues receive dispatched frames."""
    service = CaptureService(mock_mode=True)
    q: asyncio.Queue = asyncio.Queue(maxsize=3)

    service.register_async_queue(q)
    service.start_capture(target=0, fps=30)

    time.sleep(0.2)

    assert not q.empty()
    item = q.get_nowait()
    assert item is not None
    frame_bytes, meta = item
    assert isinstance(frame_bytes, bytes)
    assert meta["status"] == "ok"

    service.stop_capture()
    service.unregister_async_queue(q)


# =====================================================================
# 5. Server REST & WebSocket Integration Tests
# =====================================================================

def test_api_capture_windows(test_client: TestClient):
    """Verify GET /api/capture/windows returns list of capturable windows."""
    resp = test_client.get("/api/capture/windows")
    assert resp.status_code == 200
    data = resp.json()
    assert "windows" in data
    assert "count" in data
    assert isinstance(data["windows"], list)


def test_api_capture_lifecycle_endpoints(test_client: TestClient):
    """Verify start, status, frame retrieval, and stop endpoints."""
    # 1. Start capture
    start_resp = test_client.post("/api/capture/start", json={"target": 0, "fps": 20, "quality": 80})
    assert start_resp.status_code == 200
    assert start_resp.json()["success"]

    # 2. Get status
    status_resp = test_client.get("/api/capture/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["running"]

    # 3. Retrieve single frame
    frame_resp = test_client.get("/api/capture/frame")
    assert frame_resp.status_code == 200
    assert frame_resp.headers["content-type"] == "image/jpeg"
    assert len(frame_resp.content) > 100
    assert frame_resp.content[:2] == b"\xff\xd8"

    # 4. Stop capture
    stop_resp = test_client.post("/api/capture/stop")
    assert stop_resp.status_code == 200
    assert stop_resp.json()["success"]

    # 5. Verify stopped
    final_status = test_client.get("/api/capture/status").json()
    assert not final_status["running"]


def test_health_includes_capture_telemetry(test_client: TestClient):
    """Verify /health reports capture sidecar telemetry."""
    resp = test_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "capture" in data
    assert "running" in data["capture"]
    assert "status" in data["capture"]


def test_websocket_stream_control_protocol(test_client: TestClient):
    """Verify WebSocket /ws/stream control messages and streaming."""
    with test_client.websocket_connect("/ws/stream") as ws:
        # 1. Ping / Pong
        ws.send_json({"action": "ping"})
        msg = ws.receive_json()
        assert msg["type"] == "pong"

        # 2. List windows
        ws.send_json({"action": "list_windows"})
        win_msg = ws.receive_json()
        assert win_msg["type"] == "windows"
        assert isinstance(win_msg["data"], list)

        # 3. Start capture
        ws.send_json({"action": "start", "target": 0, "fps": 20})
        start_msg = ws.receive_json()
        assert start_msg["type"] == "start_response"
        assert start_msg["data"]["success"]

        # 4. Receive binary frame
        received_frame = False
        for _ in range(5):
            packet = ws.receive()
            if "bytes" in packet and packet["bytes"]:
                assert packet["bytes"][:2] == b"\xff\xd8"
                received_frame = True
                break

        # 5. Stop capture
        ws.send_json({"action": "stop"})
        stop_msg = None
        for _ in range(10):
            packet = ws.receive()
            if "text" in packet and packet["text"]:
                import json
                stop_msg = json.loads(packet["text"])
                if stop_msg.get("type") == "stop_response":
                    break
        assert stop_msg is not None
        assert stop_msg["type"] == "stop_response"


def test_capture_state_machine_and_invalidation():
    """Verify explicit CaptureState transitions and stale frame invalidation."""
    from src.capture.wgc_capture import CaptureState

    service = CaptureService(mock_mode=True)
    assert service.current_state == CaptureState.IDLE

    # Start capture -> CAPTURING
    service.start_capture(target=1001, fps=25)
    time.sleep(0.2)
    assert service.current_state == CaptureState.CAPTURING
    status = service.get_status()
    assert status["state"] == CaptureState.CAPTURING.value
    assert status["seqId"] > 0
    assert not status["stalled"]

    # Invalidate frame if state changed to PAUSED_MINIMIZED
    with service._lock:
        service._current_state = CaptureState.PAUSED_MINIMIZED
    
    jpeg, meta = service.get_latest_frame()
    assert jpeg is None
    assert meta["state"] == CaptureState.PAUSED_MINIMIZED.value
    assert meta["error"] == "capture_not_active"

    # Stop capture -> STOPPED
    service.stop_capture()
    assert service.current_state == CaptureState.STOPPED


def test_process_name_resolution_win32_safe():
    """Verify WindowFinder._get_process_name_by_pid resolves without crashing."""
    import os
    current_pid = os.getpid()
    proc_name = WindowFinder._get_process_name_by_pid(current_pid)
    assert isinstance(proc_name, str)
    assert len(proc_name) > 0
    assert "python" in proc_name.lower() or "py" in proc_name.lower() or ".exe" in proc_name.lower()


def test_launcher_capture_status_integration():
    """Verify scripts/launcher.py cmd_status integrates Native WGC Sidecar."""
    from scripts.launcher import cmd_status
    import argparse

    args = argparse.Namespace(host=None, port=None)
    # cmd_status should run without error
    ret = cmd_status(args)
    assert ret in (0, 1)
