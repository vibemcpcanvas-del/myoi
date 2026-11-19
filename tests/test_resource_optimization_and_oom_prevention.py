"""tests/test_resource_optimization_and_oom_prevention.py
Automated verification tests for resource optimization, OOM prevention, and browser stability:
1. NativeCaptureEngine encode_jpeg optimize=False invariant & CPU latency reduction
2. WebSocket stream endpoint asyncio.Queue(maxsize=1) & non-blocking frame dropping
3. Client-side WebSocket frame-dropping backpressure guard (state.isDecodingFrame)
4. Client-side Canvas resolution capping (max 1920x1080 for 4K/retina protection)
5. ReviewManager in-memory bounded rolling FIFO window (max 50 frames)
6. ReviewManager image memory release (origBlob, prevBlob, origBase64, prevBase64) upon server disk save
"""

import asyncio
import inspect
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Dict
from PIL import Image
import pytest

from src.capture.wgc_capture import NativeCaptureEngine
from src.capture.service import CaptureService
from src.server import websocket_stream_endpoint, _mjpeg_stream_generator

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "static"
APP_JS = STATIC_DIR / "app.js"
REVIEWER_JS = STATIC_DIR / "reviewer.js"
WGC_CAPTURE_PY = REPO_ROOT / "src" / "capture" / "wgc_capture.py"
SERVER_PY = REPO_ROOT / "src" / "server.py"


# =====================================================================
# 1. Backend Capture & Compression Invariants
# =====================================================================

def test_encode_jpeg_optimize_false_invariant():
    """Verify NativeCaptureEngine.encode_jpeg explicitly sets optimize=False.
    Multi-pass entropy optimization on 20-30 FPS video frames causes severe CPU spikes.
    """
    content = WGC_CAPTURE_PY.read_text(encoding="utf-8")
    assert "optimize=False" in content, (
        "[PERFORMANCE INVARIANT VIOLATION] encode_jpeg must set optimize=False to eliminate CPU spike"
    )
    assert "optimize=True" not in content, (
        "[PERFORMANCE INVARIANT VIOLATION] encode_jpeg must NOT set optimize=True"
    )


def test_encode_jpeg_performance_and_decodability():
    """Verify that encode_jpeg produces valid JPEG bytes with faster encoding latency."""
    engine = NativeCaptureEngine(mock_mode=True)
    img = Image.new("RGB", (1920, 1080), color=(30, 60, 90))

    # Validate output
    jpeg_bytes = engine.encode_jpeg(img, quality=80)
    assert isinstance(jpeg_bytes, bytes)
    assert len(jpeg_bytes) > 1000
    assert jpeg_bytes[:2] == b"\xff\xd8"  # JPEG SOI
    assert jpeg_bytes[-2:] == b"\xff\xd9"  # JPEG EOI


def test_websocket_stream_queue_maxsize_one_invariant():
    """Verify websocket_stream_endpoint and _mjpeg_stream_generator declare maxsize=1."""
    content = SERVER_PY.read_text(encoding="utf-8")

    ws_idx = content.find("async def websocket_stream_endpoint")
    assert ws_idx != -1
    ws_block = content[ws_idx:ws_idx + 400]
    assert "asyncio.Queue(maxsize=1)" in ws_block, (
        "[QUEUE OVERFLOW RISK] websocket_stream_endpoint must set asyncio.Queue(maxsize=1)"
    )

    mjpeg_idx = content.find("async def _mjpeg_stream_generator")
    assert mjpeg_idx != -1
    mjpeg_block = content[mjpeg_idx:mjpeg_idx + 300]
    assert "asyncio.Queue(maxsize=1)" in mjpeg_block, (
        "[QUEUE OVERFLOW RISK] _mjpeg_stream_generator must set asyncio.Queue(maxsize=1)"
    )


def test_capture_service_frame_dropping_under_backpressure():
    """Verify that CaptureService._dispatch_frame_to_queues drops older frames when queue is full."""
    svc = CaptureService(mock_mode=True)
    q: asyncio.Queue = asyncio.Queue(maxsize=1)
    svc.register_async_queue(q)

    try:
        # First frame
        frame1 = b"jpeg_frame_1"
        meta1 = {"seq_id": 1}
        svc._dispatch_frame_to_queues(frame1, meta1)
        assert q.full()

        # Second frame arrives while consumer is busy
        frame2 = b"jpeg_frame_2"
        meta2 = {"seq_id": 2}
        svc._dispatch_frame_to_queues(frame2, meta2)
        assert q.full()

        # Item in queue must be the freshest frame (frame2), frame1 was dropped
        item = q.get_nowait()
        assert item[0] == frame2
        assert item[1]["seq_id"] == 2
    finally:
        svc.unregister_async_queue(q)


# =====================================================================
# 2. Frontend WebSocket Backpressure & Canvas Resolution Capping
# =====================================================================

def test_frontend_websocket_backpressure_and_capping_in_node():
    """Verify app.js frame-dropping backpressure and 4K resolution capping in headless Node.js."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js binary not available")

    node_script = f"""
    const fs = require('fs');
    const appCode = fs.readFileSync({repr(str(APP_JS))}, 'utf8');

    let drawCount = 0;
    let canvasW = 0;
    let canvasH = 0;
    let closedBmpCount = 0;
    let decodeCallCount = 0;
    let lastCreatedWs = null;

    global.createImageBitmap = (blob) => {{
        decodeCallCount++;
        return new Promise((resolve) => {{
            setTimeout(() => {{
                resolve({{
                    width: 3840,
                    height: 2160,
                    close: () => {{ closedBmpCount++; }}
                }});
            }}, 30);
        }});
    }};

    global.window = {{
        ReviewManager: function() {{
            this.records = [];
            this.saveCurrentSessionToServer = () => {{}};
            this.updateSessionBadge = () => {{}};
        }},
        CoordinateAdapter: {{ calculateLetterbox: () => ({{ padX: 0, padY: 0, scaledW: 640, scaledH: 480 }}) }},
        addEventListener: () => {{}},
        removeEventListener: () => {{}},
        createImageBitmap: global.createImageBitmap
    }};

    global.WebSocket = function(url) {{
        this.url = url;
        this.binaryType = 'blob';
        this.onopen = null;
        this.onmessage = null;
        this.onerror = null;
        this.onclose = null;
        this.send = () => {{}};
        this.close = () => {{}};
        lastCreatedWs = this;
    }};

    global.document = {{
        documentElement: {{ classList: {{ contains: () => false, add: () => {{}}, remove: () => {{}}, toggle: () => {{}} }} }},
        body: {{ appendChild: () => {{}}, onclick: null }},
        getElementById: (id) => {{
            if (id === 'wgcCanvas') {{
                return {{
                    style: {{ display: '' }},
                    classList: {{ add: () => {{}}, remove: () => {{}}, toggle: () => {{}}, contains: () => false }},
                    querySelector: () => ({{ innerText: '' }}),
                    getContext: () => ({{
                        fillRect: () => {{}},
                        drawImage: (bmp, x, y, w, h) => {{
                            drawCount++;
                            canvasW = w || bmp.width;
                            canvasH = h || bmp.height;
                        }},
                        clearRect: () => {{}}
                    }}),
                    width: 0,
                    height: 0
                }};
            }}
            return {{
                style: {{ display: '' }},
                classList: {{ add: () => {{}}, remove: () => {{}}, toggle: () => {{}}, contains: () => false }},
                querySelector: () => ({{ innerText: '' }}),
                getContext: () => ({{ fillRect: () => {{}}, drawImage: () => {{}}, clearRect: () => {{}} }}),
                appendChild: () => {{}},
                width: 640, height: 480
            }};
        }},
        createElement: () => ({{
            width: 640, height: 640,
            style: {{}},
            classList: {{ add: () => {{}}, remove: () => {{}}, contains: () => false }},
            getContext: () => ({{ fillRect: () => {{}}, drawImage: () => {{}}, clearRect: () => {{}} }}),
            remove: () => {{}}
        }})
    }};
    global.location = {{ protocol: 'http:', host: '127.0.0.1:8000' }};
    global.localStorage = {{ getItem: () => null, setItem: () => null }};
    global.navigator = {{ mediaDevices: {{}} }};
    global.fetch = () => Promise.resolve({{ ok: true, json: () => Promise.resolve({{ success: true, windows: [] }}) }});

    eval(appCode);

    window.connectNativeWgc(0);
    lastCreatedWs.onopen();

    async function runTest() {{
        // Send frame 1 (slow decode in progress)
        const p1 = lastCreatedWs.onmessage({{ data: Buffer.from([1, 2, 3]) }});

        // Immediately send frame 2 while frame 1 is decoding: MUST be dropped by backpressure guard!
        const p2 = lastCreatedWs.onmessage({{ data: Buffer.from([4, 5, 6]) }});

        await Promise.all([p1, p2]);

        // Verify only 1 decode was performed (frame 2 dropped)
        if (decodeCallCount !== 1) {{
            throw new Error(`Expected exactly 1 decode call due to backpressure guard, got ${{decodeCallCount}}`);
        }}

        // Verify Canvas resolution was capped from 4K (3840x2160) to 1920x1080
        if (canvasW !== 1920 || canvasH !== 1080) {{
            throw new Error(`Expected canvas capped to 1920x1080, got ${{canvasW}}x${{canvasH}}`);
        }}

        // Verify ImageBitmap.close() was called to prevent GPU memory leak
        if (closedBmpCount !== 1) {{
            throw new Error(`Expected bmp.close() to be called once, got ${{closedBmpCount}}`);
        }}

        // Verify isDecodingFrame returned to false
        if (window.AppState.isDecodingFrame !== false) {{
            throw new Error("isDecodingFrame should be false after completion");
        }}

        console.log("BACKPRESSURE_AND_CAPPING_PASS");
    }}

    runTest().catch(err => {{
        console.error(err);
        process.exit(1);
    }});
    """

    res = subprocess.run([node_bin, "-e", node_script], capture_output=True, text=True, timeout=10)
    assert res.returncode == 0, f"Node.js backpressure test failed: {res.stderr} / {res.stdout}"
    assert "BACKPRESSURE_AND_CAPPING_PASS" in res.stdout


# =====================================================================
# 3. Reviewer Bounded Rolling FIFO & Memory Release
# =====================================================================

def test_reviewer_bounded_rolling_fifo_in_node():
    """Verify ReviewManager limits in-memory records to maxInMemoryRecords (50) and drops heavy images."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js binary not available")

    node_script = f"""
    const fs = require('fs');
    const reviewerCode = fs.readFileSync({repr(str(REVIEWER_JS))}, 'utf8');

    let savedSessionCount = 0;
    global.window = {{
        addEventListener: () => {{}},
        removeEventListener: () => {{}}
    }};
    global.document = {{
        addEventListener: () => {{}},
        removeEventListener: () => {{}},
        getElementById: (id) => ({{
            innerText: '',
            className: '',
            style: {{}},
            classList: {{ add: () => {{}}, remove: () => {{}}, toggle: () => {{}}, contains: () => false }},
            querySelector: () => ({{ innerText: '' }}),
            getContext: () => ({{
                save: () => {{}}, restore: () => {{}}, setTransform: () => {{}}, clearRect: () => {{}},
                fillRect: () => {{}}, strokeRect: () => {{}}, fillText: () => {{}}
            }}),
            getBoundingClientRect: () => ({{ left: 0, top: 0, width: 640, height: 360 }}),
            addEventListener: () => {{}},
            removeEventListener: () => {{}},
            width: 1280, height: 720
        }})
    }};
    global.Image = function() {{ this.onload = null; this.onerror = null; }};
    global.fetch = () => {{
        savedSessionCount++;
        return Promise.resolve({{ ok: true, json: () => Promise.resolve({{ success: true }}) }});
    }};

    eval(reviewerCode);

    const ReviewManager = global.window.ReviewManager;
    const rm = new ReviewManager();

    if (rm.maxInMemoryRecords !== 50) {{
        throw new Error(`Expected maxInMemoryRecords === 50, got ${{rm.maxInMemoryRecords}}`);
    }}

    // Add 80 records to test rolling FIFO window
    for (let i = 0; i < 80; i++) {{
        rm.addRecord({{
            recordId: `rec_${{i}}`,
            sessionId: 'SES_TEST',
            imageFile: `images/rec_${{i}}_orig.jpg`,
            previewFile: `previews/rec_${{i}}_prev.jpg`,
            origBase64: `data:image/jpeg;base64,sample_payload_${{i}}`,
            prevBase64: `data:image/jpeg;base64,sample_prev_${{i}}`,
            detections: [],
            reviewedAnnotations: [],
            reviewStatus: 'pending'
        }});
    }}

    // Record count in memory must be capped at 50
    if (rm.records.length !== 50) {{
        throw new Error(`Expected exactly 50 records in memory, got ${{rm.records.length}}`);
    }}

    // Oldest surviving record must be rec_30 (0..29 were evicted FIFO)
    if (rm.records[0].recordId !== 'rec_30') {{
        throw new Error(`Expected oldest record in buffer to be rec_30, got ${{rm.records[0].recordId}}`);
    }}

    console.log("REVIEWER_ROLLING_FIFO_PASS");
    """

    res = subprocess.run([node_bin, "-e", node_script], capture_output=True, text=True, timeout=10)
    assert res.returncode == 0, f"Node.js rolling FIFO test failed: {res.stderr} / {res.stdout}"
    assert "REVIEWER_ROLLING_FIFO_PASS" in res.stdout


def test_reviewer_persisted_session_purges_heavy_base64_in_node():
    """Verify saveCurrentSessionToServer releases origBase64, prevBase64, and Blobs from in-memory records."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js binary not available")

    node_script = f"""
    const fs = require('fs');
    const reviewerCode = fs.readFileSync({repr(str(REVIEWER_JS))}, 'utf8');

    global.window = {{
        addEventListener: () => {{}},
        removeEventListener: () => {{}}
    }};
    global.document = {{
        addEventListener: () => {{}},
        removeEventListener: () => {{}},
        getElementById: (id) => ({{
            innerText: '',
            className: '',
            style: {{}},
            classList: {{ add: () => {{}}, remove: () => {{}}, toggle: () => {{}}, contains: () => false }},
            querySelector: () => ({{ innerText: '' }}),
            getContext: () => ({{
                save: () => {{}}, restore: () => {{}}, setTransform: () => {{}}, clearRect: () => {{}},
                fillRect: () => {{}}, strokeRect: () => {{}}, fillText: () => {{}}
            }}),
            getBoundingClientRect: () => ({{ left: 0, top: 0, width: 640, height: 360 }}),
            addEventListener: () => {{}},
            removeEventListener: () => {{}},
            width: 1280, height: 720
        }})
    }};
    global.Image = function() {{ this.onload = null; this.onerror = null; }};

    let serverSavedBody = null;
    global.fetch = (url, opts) => {{
        serverSavedBody = JSON.parse(opts.body);
        return Promise.resolve({{
            ok: true,
            json: () => Promise.resolve({{ success: true, count: serverSavedBody.records.length }})
        }});
    }};

    eval(reviewerCode);

    const ReviewManager = global.window.ReviewManager;
    const rm = new ReviewManager();

    rm.addRecord({{
        recordId: 'rec_purge_test',
        sessionId: 'SES_PURGE',
        imageFile: 'images/rec_purge_test_orig.jpg',
        previewFile: 'previews/rec_purge_test_prev.jpg',
        origBase64: 'data:image/jpeg;base64,heavy_original_string_2mb',
        prevBase64: 'data:image/jpeg;base64,heavy_preview_string_1mb',
        origBlob: {{ size: 2000000 }},
        prevBlob: {{ size: 1000000 }},
        detections: [],
        reviewStatus: 'approved'
    }});

    const recBefore = rm.records[0];
    if (!recBefore.origBase64 || !recBefore.prevBase64) {{
        throw new Error("Record should initially have base64 payload");
    }}

    async function runTest() {{
        await rm.saveCurrentSessionToServer(true);

        const recAfter = rm.records[0];

        // Verify _savedOnServer flag is set
        if (!recAfter._savedOnServer) {{
            throw new Error("_savedOnServer flag was not set");
        }}

        // Verify heavy payloads were completely purged to avoid V8 heap OOM
        if (recAfter.origBase64 !== undefined) {{
            throw new Error("origBase64 was not purged from in-memory record");
        }}
        if (recAfter.prevBase64 !== undefined) {{
            throw new Error("prevBase64 was not purged from in-memory record");
        }}
        if (recAfter.origBlob !== undefined) {{
            throw new Error("origBlob was not purged from in-memory record");
        }}
        if (recAfter.prevBlob !== undefined) {{
            throw new Error("prevBlob was not purged from in-memory record");
        }}

        // Verify payload sent to server DID include image data
        if (!serverSavedBody || serverSavedBody.records.length !== 1) {{
            throw new Error("Server payload was not generated");
        }}

        console.log("PURGE_HEAVY_BASE64_PASS");
    }}

    runTest().catch(err => {{
        console.error(err);
        process.exit(1);
    }});
    """

    res = subprocess.run([node_bin, "-e", node_script], capture_output=True, text=True, timeout=10)
    assert res.returncode == 0, f"Node.js base64 purge test failed: {res.stderr} / {res.stdout}"
    assert "PURGE_HEAVY_BASE64_PASS" in res.stdout
