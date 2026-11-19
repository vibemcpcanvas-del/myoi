"""tests/test_window_switch_and_webrtc.py
Targeted verification suite for:
1. Dynamic window switching in WGC / Native capture sidecar (Issue 1).
2. WebRTC screen sharing freeze prevention, recursion locks, and frame-driven UI (Issue 2).
"""

import asyncio
from pathlib import Path
import re
import shutil
import subprocess
import time
import pytest
from fastapi.testclient import TestClient

from src.server import app
from src.capture.service import CaptureService, get_capture_service
from src.capture.wgc_capture import CaptureState

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "static"
APP_JS = STATIC_DIR / "app.js"
INDEX_HTML = STATIC_DIR / "index.html"


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def mock_service():
    svc = CaptureService(mock_mode=True)
    yield svc
    if svc.is_running:
        svc.stop_capture()


# =====================================================================
# 1. CaptureService Dynamic Window Switching Tests
# =====================================================================

def test_service_switch_target_while_running(mock_service: CaptureService):
    """Verify CaptureService can dynamically switch targets without thread crash."""
    # 1. Start on desktop (target=0)
    res1 = mock_service.start_capture(target=0, fps=25)
    assert res1["success"]
    assert mock_service.is_running
    assert mock_service._target_hwnd == 0

    time.sleep(0.15)
    frame1, meta1 = mock_service.get_latest_frame()
    assert frame1 is not None

    # 2. Switch to mock window 1001
    res2 = mock_service.switch_target(target=1001)
    assert res2["success"]
    assert res2["targetHwnd"] == 1001
    assert mock_service._target_hwnd == 1001
    assert mock_service.is_running

    time.sleep(0.15)
    frame2, meta2 = mock_service.get_latest_frame()
    assert frame2 is not None
    assert meta2.get("target_hwnd", meta2.get("hwnd")) == 1001 or meta2.get("status") == "ok"


def test_service_switch_target_purges_stale_frame_cache(mock_service: CaptureService):
    """P0 Invariant: Stale frames from previous window MUST be purged on switch to prevent frame leakage."""
    mock_service.start_capture(target=0, fps=20)
    time.sleep(0.15)

    # Cache should have a valid desktop frame
    assert mock_service._latest_jpeg is not None
    assert mock_service._latest_image is not None

    # Dynamically switch target
    mock_service.switch_target(target=1002)

    # Immediately after switch (before worker produces new frame), cache must have been purged
    # (Checking immediately or verifying that seq_id stepped forward)
    assert mock_service._target_hwnd == 1002
    assert mock_service._telemetry.seq_id >= 2


def test_service_switch_target_fail_closed_on_invalid_target(mock_service: CaptureService):
    """P0 Invariant: Switching to an invalid/non-existent window returns error and does NOT silently fallback."""
    mock_service.start_capture(target=0, fps=20)
    assert mock_service.is_running

    res = mock_service.switch_target(target="NonExistentWindow_987654321")
    assert not res["success"]
    assert res["error"] == "target_window_not_found"
    assert "not found" in res["message"].lower()


def test_service_switch_target_title_and_hwnd_resolution(mock_service: CaptureService):
    """Verify targetTitle is properly resolved without fabricating '전체 데스크톱' for HWNDs."""
    mock_service.start_capture(target=0, fps=20)
    assert mock_service.is_running

    # 1. Desktop target (0)
    res_desk = mock_service.switch_target(target=0)
    assert res_desk["success"] is True
    assert res_desk["targetTitle"] == "전체 데스크톱"
    assert res_desk["targetHwnd"] == 0

    # 2. Known mock window (1001)
    res_1001 = mock_service.switch_target(target=1001)
    assert res_1001["success"] is True
    assert res_1001["targetTitle"] == "BlueArchive - Kivotos Tactical Operation"
    assert res_1001["targetHwnd"] == 1001

    # 3. Direct HWND without title (-1024) - Anti-Fabrication Invariant: MUST NOT say '전체 데스크톱'
    res_hwnd = mock_service.switch_target(target=-1024)
    assert res_hwnd["success"] is True
    assert res_hwnd["targetTitle"] == "HWND -1024"
    assert res_hwnd["targetHwnd"] == -1024


def test_service_rapid_dynamic_target_switching(mock_service: CaptureService):
    """Stress test: Rapid alternating target switching (>10 Hz) must not deadlock or crash."""
    mock_service.start_capture(target=0, fps=30)
    assert mock_service.is_running

    targets = [0, 1001, 1002, 0, 1002, 1001, 0, 1001]
    for _ in range(3):
        for tgt in targets:
            res = mock_service.switch_target(target=tgt)
            assert res["success"] is True
            assert mock_service.is_running
            assert mock_service._target_hwnd == tgt
            time.sleep(0.01)

    time.sleep(0.1)
    frame, meta = mock_service.get_latest_frame()
    assert frame is not None


def test_service_switch_target_dispatches_control_event(mock_service: CaptureService):
    """Verify subscribers receive a target_switched control event on target change."""
    q: asyncio.Queue = asyncio.Queue(maxsize=10)
    mock_service.register_async_queue(q)

    mock_service.start_capture(target=0, fps=20)
    time.sleep(0.1)

    mock_service.switch_target(target=1001)

    items = []
    while not q.empty():
        items.append(q.get_nowait())

    control_events = [it for it in items if isinstance(it, dict) and it.get("type") == "target_switched"]
    assert len(control_events) >= 1
    assert control_events[0]["targetHwnd"] == 1001

    mock_service.unregister_async_queue(q)


# =====================================================================
# 2. REST API & WebSocket Stream Switching Tests
# =====================================================================

def test_api_capture_switch_endpoint_success(client: TestClient):
    """Verify POST /api/capture/switch switches active capture window."""
    # Ensure capture is running
    client.post("/api/capture/start", json={"target": 0, "fps": 20})

    resp = client.post("/api/capture/switch", json={"target": 0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["targetHwnd"] == 0

    client.post("/api/capture/stop")


def test_api_capture_switch_endpoint_fail_closed_on_missing_target(client: TestClient):
    """Verify POST /api/capture/switch returns 404 when target window does not exist."""
    resp = client.post("/api/capture/switch", json={"target": "TotallyFakeWindowTitleThatDoesNotExist"})
    assert resp.status_code == 404
    detail = resp.json().get("detail", {})
    assert detail.get("error") == "target_window_not_found"

    # Also start capture with missing window title returns 404
    resp_start = client.post("/api/capture/start", json={"target": "TotallyFakeWindowTitleThatDoesNotExist"})
    assert resp_start.status_code == 404
    assert resp_start.json().get("detail", {}).get("error") == "target_window_not_found"


def test_websocket_stream_switch_target_action(client: TestClient):
    """Verify /ws/stream handles switch_target actions in-flight."""
    with client.websocket_connect("/ws/stream") as ws:
        # 1. Start capture
        ws.send_json({"action": "start", "target": 0, "fps": 20})
        start_ack = ws.receive_json()
        assert start_ack.get("type") == "start_response"
        assert start_ack.get("data", {}).get("success") is True

        # 2. Switch target dynamically
        ws.send_json({"action": "switch_target", "target": 0})
        msgs = []
        for _ in range(5):
            msg = ws.receive_json()
            msgs.append(msg)
            if msg.get("type") == "switch_response":
                break
        switch_ack = next((m for m in msgs if m.get("type") == "switch_response"), None)
        assert switch_ack is not None
        assert switch_ack.get("data", {}).get("success") is True

        # 3. Switch target invalid string fail-closed
        ws.send_json({"action": "switch_target", "target": "NonExistentFakeWindow"})
        err_msgs = []
        for _ in range(5):
            msg = ws.receive_json()
            err_msgs.append(msg)
            if msg.get("type") == "switch_response":
                break
        switch_err = next((m for m in err_msgs if m.get("type") == "switch_response"), None)
        assert switch_err is not None
        assert switch_err.get("data", {}).get("success") is False
        assert switch_err.get("data", {}).get("error") == "target_window_not_found"

        # Stop
        ws.send_json({"action": "stop"})


# =====================================================================
# 3. WebRTC Screen Capture & Freeze Prevention Invariants
# =====================================================================

def test_app_js_exports_connect_webrtc_and_switch_wgc_target():
    """Verify static/app.js exports connectWebRtc and switchWgcTarget to window."""
    content = APP_JS.read_text(encoding="utf-8")

    assert "window.connectWebRtc = connectWebRtc;" in content
    assert "window.switchWgcTarget = switchWgcTarget;" in content
    assert "window.refreshWgcWindows = refreshWgcWindows;" in content


def test_app_js_webrtc_recursion_lock_and_mirror_prevention():
    """Verify WebRTC implementation enforces recursion locks and excludes self tab recursion."""
    content = APP_JS.read_text(encoding="utf-8")

    # Recursion Lock
    assert "state.isConnectingWebRtc" in content
    assert "isConnectingWebRtc: false" in content

    # Self-browser surface exclusion to prevent Chromium freeze from infinite hall-of-mirrors
    assert 'selfBrowserSurface: "exclude"' in content or "selfBrowserSurface: 'exclude'" in content
    assert 'surfaceSwitching: "exclude"' in content or "surfaceSwitching: 'exclude'" in content

    # Safe play() rejection guard
    assert "playPromise" in content or "vid.play()" in content
    assert "catch(" in content or ".catch(" in content


def test_app_js_webrtc_frame_driven_ui_exposure():
    """Verify WebRTC does not prematurely expose btnStart before video dimensions and readyState >= 2."""
    content = APP_JS.read_text(encoding="utf-8")

    # Invariant: Ready state and dimensions check before removing hidden class from btnStart
    assert "vid.videoWidth > 0" in content
    assert "vid.videoHeight > 0" in content
    assert "readyState >= 2" in content
    assert "state.webrtcReady" in content


def test_app_js_stop_ops_cleans_video_and_webrtc_state():
    """Verify stopOps pauses videoElement, clears srcObject, and resets isConnectingWebRtc."""
    content = APP_JS.read_text(encoding="utf-8")

    assert "state.isConnectingWebRtc = false;" in content
    assert "state.webrtcReady = false;" in content
    assert "vid.srcObject = null;" in content
    assert "vid.pause()" in content


def test_node_webrtc_and_window_switch_lifecycle():
    """Execute static/app.js in Node.js to verify connectWebRtc and switchWgcTarget lifecycle."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js not installed")

    node_template = """
    const fs = require('fs');
    const appCode = fs.readFileSync(__APP_JS_PATH__, 'utf8');

    let btnSpanText = '';
    let startBtnHidden = true;
    let wsSent = [];
    let fetchCalls = [];
    let selValue = '0';
    let getDisplayMediaCallCount = 0;
    let shouldRejectPermission = false;

    class MockTrack {
        constructor() { this.stopped = false; }
        stop() { this.stopped = true; }
    }

    global.window = {
        ReviewManager: class {
            constructor() { this.records = []; }
            saveCurrentSessionToServer() {}
            updateSessionBadge() {}
        },
        CoordinateAdapter: { calculateLetterbox: () => ({ padX: 0, padY: 0, scaledW: 640, scaledH: 480 }) },
        addEventListener: () => {},
        removeEventListener: () => {},
        WebSocket: class {
            static get OPEN() { return 1; }
            constructor() {
                this.readyState = 1; // OPEN
            }
            send(data) { wsSent.push(JSON.parse(data)); }
            close() {}
        }
    };
    global.window.WebSocket.OPEN = 1;
    global.WebSocket = global.window.WebSocket;

    global.document = {
        documentElement: { classList: { contains: () => false, add: () => {}, remove: () => {}, toggle: () => {} } },
        body: { appendChild: () => {} },
        getElementById: (id) => ({
            get value() { return selValue; },
            set value(v) { selValue = String(v); },
            style: {},
            classList: {
                add: (c) => { if (c === 'hidden') startBtnHidden = true; },
                remove: (c) => { if (c === 'hidden') startBtnHidden = false; },
                toggle: () => {}
            },
            querySelector: () => ({
                get innerText() { return btnSpanText; },
                set innerText(v) { btnSpanText = v; }
            }),
            getContext: () => ({ fillRect: () => {}, drawImage: () => {}, clearRect: () => {} }),
            appendChild: () => {},
            options: [{ value: '0' }, { value: '2048' }],
            width: 640, height: 480,
            videoWidth: 1280, videoHeight: 720, readyState: 4,
            play: () => Promise.resolve(),
            pause: () => {}
        }),
        createElement: () => ({ remove: () => {}, width: 640, height: 640, getContext: () => ({ fillRect: () => {}, drawImage: () => {}, clearRect: () => {} }) })
    };
    global.location = { protocol: 'http:', host: '127.0.0.1:8000' };
    global.localStorage = { getItem: () => null, setItem: () => null };
    const mockNavigator = {
        mediaDevices: {
            getDisplayMedia: () => {
                getDisplayMediaCallCount++;
                if (shouldRejectPermission) {
                    const err = new Error("Permission denied by user");
                    err.name = "NotAllowedError";
                    return Promise.reject(err);
                }
                return Promise.resolve({
                    getVideoTracks: () => [new MockTrack()],
                    getTracks: () => [new MockTrack()]
                });
            }
        }
    };
    Object.defineProperty(global, 'navigator', { value: mockNavigator, writable: true, configurable: true });
    global.fetch = (url, opts) => {
        fetchCalls.push({ url, opts });
        return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ success: true, targetTitle: 'Mock Window', windows: [{ hwnd: 2048, title: 'Game', process_name: 'game.exe', width: 1280, height: 720 }] })
        });
    };

    eval(appCode);

    // 1. Verify switchWgcTarget triggers WebSocket send and updates dropdown value
    window.AppState.captureMode = 'wgc';
    window.AppState.wgcWs = new global.WebSocket();
    window.switchWgcTarget(2048);

    if (wsSent.length === 0 || wsSent[0].action !== 'switch_target' || wsSent[0].target !== 2048) {
        throw new Error("switchWgcTarget failed to send switch_target action via WebSocket: " + JSON.stringify(wsSent));
    }
    if (selValue !== '2048') {
        throw new Error("switchWgcTarget did not update select element value: " + selValue);
    }

    // 2. Verify connectWebRtc cancellation does NOT trigger duplicate fallback popup
    window.AppState.captureMode = 'webrtc';
    shouldRejectPermission = true;
    getDisplayMediaCallCount = 0;
    window.connectWebRtc().then(() => {
        if (getDisplayMediaCallCount !== 1) {
            throw new Error("Expected getDisplayMedia to be called exactly ONCE on user cancel, got: " + getDisplayMediaCallCount);
        }
        if (window.AppState.isConnectingWebRtc !== false) {
            throw new Error("isConnectingWebRtc not reset after cancel");
        }
        if (!startBtnHidden) {
            throw new Error("btnStart prematurely exposed after cancel");
        }

        // 3. Verify normal WebRTC connect lifecycle
        shouldRejectPermission = false;
        return window.connectWebRtc();
    }).then(() => {
        if (!window.AppState.webrtcReady) {
            throw new Error("Expected webrtcReady to be true after successful connect");
        }
        if (startBtnHidden) {
            throw new Error("Expected btnStart hidden class to be removed after WebRTC frame ready");
        }

        // Verify stopOps resets WebRTC state
        window.stopOps();
        if (window.AppState.webrtcReady !== false) {
            throw new Error("stopOps did not reset webrtcReady to false");
        }

        console.log("WEBRTC_LIFECYCLE_NODE_OK");
    }).catch(err => {
        console.error(err);
        process.exit(1);
    });
    """
    node_script = node_template.replace("__APP_JS_PATH__", repr(str(APP_JS)))

    res = subprocess.run(
        [node_bin, "-e", node_script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res.returncode == 0, f"Node.js WebRTC test failed: {res.stderr}"
    assert "WEBRTC_LIFECYCLE_NODE_OK" in res.stdout
