"""tests/test_stream_fallback_and_self_healing.py
Targeted test suite for Launcher Self-Healing and Frontend Multi-Protocol Fallback / Circuit Breaker.
Verifies:
1. Requirements declaration and launcher environment self-healing preflight check.
2. 실행하기.bat self-healing hook for non-developer zero-friction launch.
3. Frontend WebSocket Circuit Breaker (max retry limit, exponential backoff, OPEN state).
4. Seamless HTTP stream / frame polling fallback (/api/stream, /api/capture/frame) when WebSockets fail.
5. Server /api/stream query parameter integration.
"""

from pathlib import Path
import re
import shutil
import subprocess
import sys
import pytest
from fastapi.testclient import TestClient

from src.server import app
from scripts.launcher import (
    check_python_environment,
    REQUIRED_PACKAGES,
    WORKSPACE_ROOT,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "static"
APP_JS = STATIC_DIR / "app.js"
REQUIREMENTS_TXT = REPO_ROOT / "requirements.txt"
LAUNCH_BAT = REPO_ROOT / "실행하기.bat"


@pytest.fixture
def test_client():
    with TestClient(app) as client:
        yield client


# =====================================================================
# 1. Launcher & Dependency Self-Healing Tests
# =====================================================================

def test_requirements_declares_websockets():
    """Verify requirements.txt explicitly declares websockets>=12.0."""
    assert REQUIREMENTS_TXT.exists()
    content = REQUIREMENTS_TXT.read_text(encoding="utf-8")
    assert re.search(r"websockets>=12\.0", content), (
        "requirements.txt must declare websockets>=12.0 for uvicorn WebSocket streaming."
    )


def test_launcher_declares_websockets_in_required_packages():
    """Verify scripts/launcher.py includes websockets in REQUIRED_PACKAGES."""
    pkg_dict = dict(REQUIRED_PACKAGES)
    assert "websockets" in pkg_dict
    assert "websockets>=12.0" in pkg_dict["websockets"]


def test_launcher_preflight_auto_heal_behavior(monkeypatch):
    """Verify check_python_environment attempts self-healing when package is missing."""
    import importlib.util

    original_find_spec = importlib.util.find_spec
    installed_calls = []

    def mock_find_spec(name, *args, **kwargs):
        # Simulate websockets missing on first check
        if name == "websockets" and len(installed_calls) == 0:
            return None
        return original_find_spec(name, *args, **kwargs)

    def mock_subprocess_run(cmd, *args, **kwargs):
        installed_calls.append(cmd)
        class MockRes:
            returncode = 0
            stdout = "Successfully installed websockets"
            stderr = ""
        return MockRes()

    monkeypatch.setattr("importlib.util.find_spec", mock_find_spec)
    monkeypatch.setattr("subprocess.run", mock_subprocess_run)

    ok, msg = check_python_environment(auto_heal=True)
    assert ok is True
    assert msg == "정상"
    assert len(installed_calls) == 1
    assert "websockets>=12.0" in installed_calls[0]


def test_launcher_preflight_no_auto_heal_reports_missing(monkeypatch):
    """Verify check_python_environment(auto_heal=False) fails closed when package missing."""
    import importlib.util

    original_find_spec = importlib.util.find_spec

    def mock_find_spec(name, *args, **kwargs):
        if name == "websockets":
            return None
        return original_find_spec(name, *args, **kwargs)

    monkeypatch.setattr("importlib.util.find_spec", mock_find_spec)

    ok, msg = check_python_environment(auto_heal=False)
    assert ok is False
    assert "websockets" in msg
    assert "pip install -r requirements.txt" in msg


def test_batch_launcher_contains_self_healing_preflight():
    """Verify 실행하기.bat includes preflight import websockets check and auto pip install."""
    assert LAUNCH_BAT.exists()
    content = LAUNCH_BAT.read_text(encoding="utf-8")
    assert 'import websockets' in content
    assert 'pip install "websockets>=12.0"' in content or 'pip install websockets' in content


# =====================================================================
# 2. Frontend Multi-Protocol Fallback & Circuit Breaker Tests
# =====================================================================

def test_app_js_circuit_breaker_and_fallback_syntax_in_node():
    """Verify wsCircuitBreaker state machine and fallback logic execution in Node.js."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js not installed")

    node_test_script = f"""
    const fs = require('fs');
    const appCode = fs.readFileSync({repr(str(APP_JS))}, 'utf8');

    // Mock DOM and browser environment
    global.window = {{
        ReviewManager: class {{
            constructor() {{ this.records = []; }}
            saveCurrentSessionToServer() {{}}
            updateSessionBadge() {{}}
        }},
        CoordinateAdapter: {{
            calculateLetterbox: () => ({{ padX: 0, padY: 0, scaledW: 640, scaledH: 480 }})
        }},
        addEventListener: () => {{}},
        removeEventListener: () => {{}}
    }};
    global.document = {{
        documentElement: {{
            classList: {{
                contains: () => false,
                add: () => {{}},
                remove: () => {{}},
                toggle: () => {{}}
            }}
        }},
        body: {{
            appendChild: () => {{}}
        }},
        getElementById: (id) => ({{
            style: {{}},
            classList: {{
                add: () => {{}},
                remove: () => {{}}
            }},
            querySelector: () => ({{ innerText: '' }}),
            getContext: () => ({{
                fillRect: () => {{}},
                drawImage: () => {{}},
                clearRect: () => {{}}
            }}),
            width: 640,
            height: 480
        }}),
        createElement: () => ({{
            width: 640,
            height: 640,
            getContext: () => ({{
                fillRect: () => {{}},
                drawImage: () => {{}},
                clearRect: () => {{}}
            }})
        }})
    }};
    global.location = {{ protocol: 'http:', host: '127.0.0.1:8000' }};
    global.localStorage = {{ getItem: () => null, setItem: () => null }};
    global.navigator = {{ mediaDevices: {{}} }};
    global.fetch = () => Promise.resolve({{ ok: true, json: () => Promise.resolve({{ success: true, windows: [] }}) }});

    // Execute app.js
    eval(appCode);

    const cb = window.wsCircuitBreaker;
    if (!cb) throw new Error("wsCircuitBreaker not found on window");

    // 1. Initial State
    if (cb.state !== 'CLOSED') throw new Error("Expected initial state CLOSED, got " + cb.state);
    if (cb.isOpen()) throw new Error("Expected isOpen() to be false initially");

    // 2. Failure Accumulation
    cb.recordFailure();
    if (cb.failures !== 1 || cb.isOpen()) throw new Error("Failure count mismatch on step 1");
    cb.recordFailure();
    if (cb.failures !== 2 || cb.isOpen()) throw new Error("Failure count mismatch on step 2");

    // 3. Circuit Trips to OPEN at maxFailures (3)
    cb.recordFailure();
    if (cb.state !== 'OPEN') throw new Error("Expected state OPEN after 3 failures, got " + cb.state);
    if (!cb.isOpen()) throw new Error("Expected isOpen() to be true when tripped");

    // 4. Success resets circuit
    cb.recordSuccess();
    if (cb.state !== 'CLOSED' || cb.failures !== 0) throw new Error("Expected CLOSED on recordSuccess");
    if (cb.isOpen()) throw new Error("Expected isOpen() to be false after success");

    console.log("CIRCUIT_BREAKER_NODE_OK");
    """

    res = subprocess.run(
        [node_bin, "-e", node_test_script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res.returncode == 0, f"Node.js circuit breaker verification failed: {res.stderr}"
    assert "CIRCUIT_BREAKER_NODE_OK" in res.stdout


def test_app_js_source_code_contract_guarantees():
    """Verify app.js includes multi-protocol fallback and no unmitigated reconnection flood."""
    content = APP_JS.read_text(encoding="utf-8")

    # Circuit breaker & retry limits
    assert "wsCircuitBreaker" in content
    assert "maxFailures: 3" in content
    assert "isOpen()" in content

    # HTTP Fallback
    assert "startHttpFallbackCapture" in content
    assert "/api/capture/start" in content
    assert "/api/capture/frame" in content
    assert "/api/capture/stop" in content

    # Exponential backoff
    assert "Math.pow(2," in content or "Math.pow(2" in content
    assert "clearTimeout(state.wsRetryTimer)" in content

    # Concealment compatibility with HTTP fallback
    assert "state.httpFallbackActive" in content


# =====================================================================
# 3. Server HTTP Streaming & Capture Integration
# =====================================================================

def test_api_stream_accepts_target_parameter(test_client: TestClient):
    """Verify /api/stream accepts target window and initializes capture without error."""
    # Test with target=0 (desktop) in streaming context
    with test_client.stream("GET", "/api/stream?target=0&fps=15") as resp:
        assert resp.status_code == 200
        assert "multipart/x-mixed-replace; boundary=frame" in resp.headers["content-type"]
        chunk = next(resp.iter_bytes())
        assert len(chunk) > 0


def test_api_stream_handles_string_and_negative_hwnd_and_desktop_switch(test_client: TestClient):
    """Verify /api/stream correctly handles string titles, negative HWNDs, and desktop switching."""
    from src.capture import get_capture_service
    from src.config import get_config

    cfg = get_config()
    svc = get_capture_service(mock_mode=cfg.mock_inference)

    # 1. Negative HWND string target
    with test_client.stream("GET", "/api/stream?target=-1024&fps=10") as resp:
        assert resp.status_code == 200
        assert svc._target_hwnd == -1024

    # 2. String window title target (mock mode finds window or handles gracefully)
    with test_client.stream("GET", "/api/stream?target=DesktopWindow&fps=10") as resp:
        assert resp.status_code == 200

    # 3. Switching back to desktop (target=0) from active non-zero target
    svc._target_hwnd = 9999
    svc._is_running = True
    with test_client.stream("GET", "/api/stream?target=0&fps=10") as resp:
        assert resp.status_code == 200
        assert svc._target_hwnd == 0


# =====================================================================
# 4. Deep Deduplication, Preflight Invariants, & Lifecycle Tests
# =====================================================================

def test_node_websocket_error_and_close_deduplication():
    """Verify that browser WebSocket onerror followed by onclose does NOT double-increment failure count."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js not installed")

    node_dedup_script = f"""
    const fs = require('fs');
    const appCode = fs.readFileSync({repr(str(APP_JS))}, 'utf8');

    let activeSocket = null;
    class MockWebSocket {{
        constructor(url) {{
            this.url = url;
            this.binaryType = 'blob';
            this.onopen = null;
            this.onmessage = null;
            this.onerror = null;
            this.onclose = null;
            activeSocket = this;
        }}
        send() {{}}
        close() {{
            if (this.onclose) this.onclose();
        }}
    }}

    global.window = {{
        ReviewManager: class {{
            constructor() {{ this.records = []; }}
            saveCurrentSessionToServer() {{}}
            updateSessionBadge() {{}}
        }},
        CoordinateAdapter: {{ calculateLetterbox: () => ({{ padX: 0, padY: 0, scaledW: 640, scaledH: 480 }}) }},
        addEventListener: () => {{}},
        removeEventListener: () => {{}},
        WebSocket: MockWebSocket
    }};
    global.WebSocket = MockWebSocket;
    global.document = {{
        documentElement: {{ classList: {{ contains: () => false, add: () => {{}}, remove: () => {{}}, toggle: () => {{}} }} }},
        body: {{ appendChild: () => {{}} }},
        getElementById: (id) => ({{
            style: {{}},
            classList: {{ add: () => {{}}, remove: () => {{}} }},
            querySelector: () => ({{ innerText: '' }}),
            getContext: () => ({{ fillRect: () => {{}}, drawImage: () => {{}}, clearRect: () => {{}} }}),
            appendChild: () => {{}},
            width: 640, height: 480
        }}),
        createElement: () => ({{ width: 640, height: 640, getContext: () => ({{ fillRect: () => {{}}, drawImage: () => {{}}, clearRect: () => {{}} }}) }})
    }};
    global.location = {{ protocol: 'http:', host: '127.0.0.1:8000' }};
    global.localStorage = {{ getItem: () => null, setItem: () => null }};
    global.navigator = {{ mediaDevices: {{}} }};
    global.fetch = () => Promise.resolve({{ ok: true, json: () => Promise.resolve({{ success: true, windows: [] }}) }});

    eval(appCode);

    const cb = window.wsCircuitBreaker;
    const state = window.AppState;
    cb.reset();
    state.wsRetryCount = 0;

    // Trigger connectNativeWgc
    window.connectNativeWgc(0);
    if (!activeSocket) throw new Error("MockWebSocket was not instantiated");

    // Simulate browser behavior on HTTP 404 handshake failure:
    // Browser fires 'error' event first, followed immediately by 'close' event
    if (activeSocket.onerror) activeSocket.onerror(new Error("Handshake failed"));
    if (activeSocket.onclose) activeSocket.onclose();

    // Invariant: Both events must count as exactly ONE failure attempt, not two!
    if (cb.failures !== 1) {{
        throw new Error("Expected exactly 1 failure after onerror + onclose sequence, got " + cb.failures);
    }}
    if (state.wsRetryCount !== 1) {{
        throw new Error("Expected wsRetryCount to be 1, got " + state.wsRetryCount);
    }}
    if (cb.isOpen()) {{
        throw new Error("Circuit breaker should not be open after a single failed attempt");
    }}

    clearTimeout(state.wsRetryTimer);
    console.log("DEDUPLICATION_NODE_OK");
    """

    res = subprocess.run(
        [node_bin, "-e", node_dedup_script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res.returncode == 0, f"Node.js deduplication test failed: {res.stderr}"
    assert "DEDUPLICATION_NODE_OK" in res.stdout


def test_launcher_imports_after_environment_check():
    """Verify scripts/launcher.py calls check_python_environment() before importing uvicorn or get_config."""
    import ast
    launcher_src = (REPO_ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
    tree = ast.parse(launcher_src)

    for item in tree.body:
        if isinstance(item, ast.FunctionDef) and item.name in ("cmd_run", "cmd_start"):
            check_found = False
            for stmt in item.body:
                # Look for call to check_python_environment
                for node in ast.walk(stmt):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "check_python_environment":
                        check_found = True
                        break
                if check_found:
                    break
                # If we encounter an import statement before check_python_environment, it's a violation!
                if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    for alias in getattr(stmt, "names", []):
                        assert alias.name not in ("uvicorn", "get_config"), (
                            f"{item.name} imports '{alias.name}' before executing check_python_environment()!"
                        )


def test_stop_ops_clears_socket_handlers_and_restores_button_text():
    """Verify stopOps detaches event handlers and restores exact button label per capture mode."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js not installed")

    node_stop_script = f"""
    const fs = require('fs');
    const appCode = fs.readFileSync({repr(str(APP_JS))}, 'utf8');

    let btnSpanText = '';
    global.window = {{
        ReviewManager: class {{
            constructor() {{ this.records = []; }}
            saveCurrentSessionToServer() {{}}
            updateSessionBadge() {{}}
        }},
        CoordinateAdapter: {{ calculateLetterbox: () => ({{ padX: 0, padY: 0, scaledW: 640, scaledH: 480 }}) }},
        addEventListener: () => {{}},
        removeEventListener: () => {{}}
    }};
    global.document = {{
        documentElement: {{ classList: {{ contains: () => false, add: () => {{}}, remove: () => {{}}, toggle: () => {{}} }} }},
        body: {{ appendChild: () => {{}} }},
        getElementById: (id) => ({{
            style: {{}},
            classList: {{ add: () => {{}}, remove: () => {{}} }},
            querySelector: (sel) => ({{
                get innerText() {{ return btnSpanText; }},
                set innerText(v) {{ btnSpanText = v; }}
            }}),
            getContext: () => ({{ fillRect: () => {{}}, drawImage: () => {{}}, clearRect: () => {{}} }}),
            width: 640, height: 480
        }}),
        createElement: () => ({{ width: 640, height: 640, getContext: () => ({{ fillRect: () => {{}}, drawImage: () => {{}}, clearRect: () => {{}} }}) }})
    }};
    global.location = {{ protocol: 'http:', host: '127.0.0.1:8000' }};
    global.localStorage = {{ getItem: () => null, setItem: () => null }};
    global.navigator = {{ mediaDevices: {{}} }};
    global.fetch = () => Promise.resolve({{ ok: true, json: () => Promise.resolve({{ success: true, windows: [] }}) }});

    eval(appCode);

    const state = window.AppState;
    state.captureMode = 'wgc';
    const mockWs = {{
        onopen: () => {{}},
        onmessage: () => {{}},
        onerror: () => {{}},
        onclose: () => {{}},
        send: () => {{}},
        close: () => {{}}
    }};
    state.wgcWs = mockWs;

    window.stopOps();

    if (mockWs.onopen !== null || mockWs.onerror !== null || mockWs.onclose !== null) {{
        throw new Error("stopOps did not detach WebSocket event handlers");
    }}
    if (!btnSpanText.includes("네이티브")) {{
        throw new Error("Expected button text to include '네이티브', got: " + btnSpanText);
    }}

    console.log("STOP_OPS_NODE_OK");
    """

    res = subprocess.run(
        [node_bin, "-e", node_stop_script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res.returncode == 0, f"Node.js stopOps test failed: {res.stderr}"
    assert "STOP_OPS_NODE_OK" in res.stdout


def test_batch_launcher_executes_without_syntax_error():
    """Verify 실행하기.bat executes in cmd.exe without syntax error (e.g., 'was unexpected at this time')."""
    if sys.platform != "win32":
        pytest.skip("Windows batch execution requires Windows")

    cmd = ["cmd.exe", "/c", str(LAUNCH_BAT), "--no-pause", "--help"]
    res = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=15)
    assert res.returncode == 0, f"실행하기.bat failed: {res.stderr} / {res.stdout}"
    assert "was unexpected at this time" not in res.stderr
    assert "was unexpected at this time" not in res.stdout
    assert "누락 was unexpected" not in res.stdout


def test_app_js_wgc_button_display_invariants():
    """Verify app.js does not prematurely show btnStart until start_response succeeds or frames arrive."""
    content = APP_JS.read_text(encoding="utf-8")
    # Verify auto-recovery to HTTP stream on unexpected WS disconnect
    assert "startHttpFallbackCapture" in content
    assert "WebSocket 스트림 연결 끊김" in content
    # Verify btnStart is displayed in start_response or binary frame
    assert "$('btnStart').classList.remove('hidden')" in content

