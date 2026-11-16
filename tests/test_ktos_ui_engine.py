"""tests/test_ktos_ui_engine.py
Automated tests for KTOS (Kivotos Tactical Operations System) UI Engine:
- Phase 1: Day/Night Ops Dual-Theme Engine & Variables
- Phase 2: Tactical Viewport HUD Reticles & Optical L-Brackets & Chamfered Bounding Box Telemetry
- Phase 3: Review Center Transform Matrix, Inverse Coordinate Mapping & Keyboard Triage Engine
"""

from pathlib import Path
import re
import shutil
import subprocess
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "static"


def test_phase1_dual_theme_engine_structure():
    """Verify KTOS Day/Night CSS variables and toggle elements in index.html."""
    html_path = STATIC_DIR / "index.html"
    assert html_path.exists(), "static/index.html must exist"
    content = html_path.read_text(encoding="utf-8")

    # CSS Variables verification
    assert ":root" in content
    assert ".dark" in content
    assert "--hud-bracket" in content
    assert "--p: #1274c4;" in content
    assert "--p: #38bdf8;" in content

    # Theme toggle element & persistent storage key
    assert 'id="themeToggleBtn"' in content
    assert "schale_theme" in content

    # Google Fonts integration
    assert "Space+Grotesk" in content
    assert "JetBrains+Mono" in content
    assert "Noto+Sans+KR" in content


def test_phase2_hud_brackets_and_telemetry_format():
    """Verify 4-corner optical reticles and chamfered box telemetry formatting."""
    html_path = STATIC_DIR / "index.html"
    content = html_path.read_text(encoding="utf-8")

    assert "hud-bracket hud-tl" in content
    assert "hud-bracket hud-tr" in content
    assert "hud-bracket hud-bl" in content
    assert "hud-bracket hud-br" in content

    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "drawTacticalBox" in app_js
    assert "${className} [${confVal}%] ${width}x${height}" in app_js
    assert "hexToRgba" in app_js


def test_phase3_reviewer_transform_and_triage_engine_in_node():
    """Verify ReviewManager view matrix, inverse coordinates, and shortcuts in Node.js."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js not installed")

    reviewer_js = STATIC_DIR / "reviewer.js"
    assert reviewer_js.exists()

    node_script = f"""
    const fs = require('fs');
    const code = fs.readFileSync({repr(str(reviewer_js))}, 'utf8');

    // Create a mock DOM environment
    const elements = {{
        'reviewCanvas': {{
            width: 1280,
            height: 720,
            getContext: () => ({{
                save: () => {{}},
                restore: () => {{}},
                setTransform: () => {{}},
                clearRect: () => {{}},
                translate: () => {{}},
                scale: () => {{}},
                drawImage: () => {{}},
                fillRect: () => {{}},
                strokeRect: () => {{}},
                beginPath: () => {{}},
                moveTo: () => {{}},
                lineTo: () => {{}},
                stroke: () => {{}},
                measureText: (txt) => ({{ width: txt.length * 8 }}),
                fillText: () => {{}}
            }}),
            getBoundingClientRect: () => ({{ left: 0, top: 0, width: 640, height: 360 }}),
            addEventListener: () => {{}}
        }},
        'tabAll': {{ onclick: null }},
        'tabPending': {{ onclick: null }},
        'tabApproved': {{ onclick: null }},
        'tabRejected': {{ onclick: null }},
        'btnModeOrig': {{ onclick: null }},
        'btnModePrev': {{ onclick: null }},
        'btnModeEdit': {{ onclick: null }},
        'btnResetView': {{ onclick: null, innerText: '' }},
        'btnApprove': {{ onclick: null }},
        'btnReject': {{ onclick: null }},
        'btnPending': {{ onclick: null }},
        'btnDelBox': {{ onclick: null }},
        'btnAddBox': {{ onclick: null }},
        'selBoxClass': {{ onchange: null, value: '0', options: [{{ value: '0', text: 'Class 0' }}, {{ value: '1', text: 'Class 1' }}] }},
        'btnCloseReview': {{ onclick: null }},
        'btnOpenReview': {{ onclick: null }},
        'reviewModal': {{ classList: {{ contains: (c) => false, add: () => {{}}, remove: () => {{}} }} }},
        'reviewList': {{ innerHTML: '' }},
        'inspectorMeta': {{ innerText: '' }},
        'cntTotal': {{ innerText: '' }},
        'cntPending': {{ innerText: '' }},
        'cntApproved': {{ innerText: '' }},
        'cntRejected': {{ innerText: '' }},
        'tabCountAll': {{ innerText: '' }},
        'tabCountPending': {{ innerText: '' }},
        'tabCountApproved': {{ innerText: '' }},
        'tabCountRejected': {{ innerText: '' }}
    }};

    const mockWindow = {{
        document: {{
            getElementById: (id) => elements[id] || null,
            createElement: (tag) => ({{}}),
            activeElement: {{ tagName: 'BODY' }}
        }},
        Image: function() {{
            this.onload = null;
            this.complete = true;
        }},
        addEventListener: () => {{}},
        ReviewManager: null
    }};

    global.document = mockWindow.document;
    global.window = mockWindow;
    global.Image = mockWindow.Image;

    eval('(function(window){{ ' + code + ' }})(mockWindow)');

    const ReviewManager = mockWindow.ReviewManager;
    if (!ReviewManager) throw new Error('ReviewManager not defined');

    const rm = new ReviewManager();
    if (!rm.view || rm.view.scale !== 1.0 || rm.view.panX !== 0 || rm.view.panY !== 0) {{
        throw new Error('Initial view matrix invalid');
    }}

    // Test 1: Inverse Coordinate Mapping
    // With scale = 2.0, panX = 100, panY = 50
    rm.view = {{ scale: 2.0, panX: 100, panY: 50 }};
    // If canvas rect is 640x360 and internal is 1280x720, clientX = 100 -> cx = 200
    // clientY = 75 -> cy = 150
    const mapped = rm.getCanvasCoordinates({{ clientX: 100, clientY: 75 }});
    // Expected x = (200 - 100) / 2 = 50
    // Expected y = (150 - 50) / 2 = 50
    if (Math.abs(mapped.x - 50) > 0.001 || Math.abs(mapped.y - 50) > 0.001) {{
        throw new Error(`Inverse coordinate mapping failed: got ${{mapped.x}}, ${{mapped.y}}`);
    }}

    // Test 2: Reset View
    rm.resetView();
    if (rm.view.scale !== 1.0 || rm.view.panX !== 0 || rm.view.panY !== 0) {{
        throw new Error('Reset view did not restore identity transform');
    }}

    // Test 3: Wheel Zoom Invariant
    // Cursor at (100, 100) on client -> cx = 200, cy = 200
    // Before zoom: imgX = (200 - 0) / 1 = 200
    rm.handleCanvasWheel({{ clientX: 100, clientY: 100, deltaY: -100, preventDefault: () => {{}} }});
    const expectedScale = 1.15;
    if (Math.abs(rm.view.scale - expectedScale) > 0.01) {{
        throw new Error(`Scale did not zoom by 1.15: ${{rm.view.scale}}`);
    }}
    // Point under mouse after zoom: imgX * newScale + panX must equal cx = 200
    const pointAfter = 200 * rm.view.scale + rm.view.panX;
    if (Math.abs(pointAfter - 200) > 0.001) {{
        throw new Error(`Wheel zoom invariant violated: pointAfter = ${{pointAfter}}`);
    }}

    // Test 4: Shortcut Class Selection
    rm.handleClassShortcut(1);
    if (elements['selBoxClass'].value !== '1') {{
        throw new Error('handleClassShortcut did not select class 1');
    }}

    // Test 5: Zero Rect Safe Guard
    elements['reviewCanvas'].getBoundingClientRect = () => ({{ left: 0, top: 0, width: 0, height: 0 }});
    const safeCoords = rm.getCanvasCoordinates({{ clientX: 10, clientY: 10 }});
    if (isNaN(safeCoords.x) || !isFinite(safeCoords.x)) {{
        throw new Error('Zero rect produced NaN/Infinity coordinates');
    }}
    elements['reviewCanvas'].getBoundingClientRect = () => ({{ left: 0, top: 0, width: 640, height: 360 }});

    // Test 6: Filter-Aware Frame Navigation & Auto-Advance
    rm.records = [
        {{ reviewStatus: 'pending', reviewedAnnotations: [{{ classId: 0, box: {{ x1: 10, y1: 10, x2: 50, y2: 50 }} }}] }},
        {{ reviewStatus: 'approved', reviewedAnnotations: [] }},
        {{ reviewStatus: 'pending', reviewedAnnotations: [{{ classId: 1, box: {{ x1: 20, y1: 20, x2: 60, y2: 60 }} }}] }}
    ];
    rm.setFilter('pending');
    rm.selectRecord(0);
    if (rm.currentRecordIndex !== 0) throw new Error('Initial selection should be 0');

    // Press ']' (next frame in pending filter): must skip index 1 and jump to index 2!
    rm.navigateFrame(1);
    if (rm.currentRecordIndex !== 2) {{
        throw new Error(`Filter navigation failed: expected 2, got ${{rm.currentRecordIndex}}`);
    }}

    // Press '[' (prev frame in pending filter): must jump back to index 0!
    rm.navigateFrame(-1);
    if (rm.currentRecordIndex !== 0) {{
        throw new Error(`Filter navigation prev failed: expected 0, got ${{rm.currentRecordIndex}}`);
    }}

    // Auto-advance upon approval: record 0 approved -> next pending frame should be record 2!
    rm.setDecision('approved');
    if (rm.currentRecordIndex !== 2) {{
        throw new Error(`Auto-advance on decision failed: expected 2, got ${{rm.currentRecordIndex}}`);
    }}

    console.log('ALL_NODE_TESTS_PASS');
    """

    res = subprocess.run([node_bin, "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Node script error: {res.stderr}\n{res.stdout}"
    assert "ALL_NODE_TESTS_PASS" in res.stdout
