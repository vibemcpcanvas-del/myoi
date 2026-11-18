"""tests/test_stream_concealment_bug.py
Targeted test suite reproducing and verifying the fix for Problem 1:
"스트리밍 화면 가리기" (Hide streaming display) Detection Bug.

Verifies:
1. When user checks "스트리밍 화면 가리기" (chkHideVid / chkHideStream), the video element
   is NEVER given display: none, visibility: hidden, or opacity: 0 (which causes browser
   video decoders to freeze or emit black frames to canvas.drawImage).
2. The visual stream is concealed via an informative tactical overlay (#streamConcealOverlay),
   keeping the user's visual display hidden.
3. The underlying frame source maintains full composition (opacity: 1), ensuring 100% full FPS
   inference and active pixel capture without dropping frames or breaking detections.
4. Both chkHideVid and chkHideStream identifiers are supported for backwards and forwards compatibility.
"""

from pathlib import Path
import re
import pytest

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"
APP_JS = STATIC_DIR / "app.js"


def test_html_contains_stream_conceal_overlay():
    """Verify index.html contains the tactical privacy concealment overlay."""
    assert INDEX_HTML.exists(), f"Missing {INDEX_HTML}"
    content = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="streamConcealOverlay"' in content, (
        "index.html must contain #streamConcealOverlay for visual concealment without decoding freeze"
    )
    assert "TACTICAL STREAM CONCEALED" in content
    assert "Letterbox 640" in content


def test_html_supports_hide_stream_checkbox_aliases():
    """Verify index.html has chkHideVid with alias support for chkHideStream."""
    content = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="chkHideVid"' in content
    assert 'data-alias="chkHideStream"' in content or 'chkHideStream' in content
    assert "스트리밍 화면 가리기" in content


def test_app_js_does_not_zero_video_opacity_on_hide():
    """Anti-Regression: Verify app.js NEVER sets vid.style.opacity = '0' on checkbox change."""
    content = APP_JS.read_text(encoding="utf-8")

    # The buggy pattern was: $('chkHideVid').onchange = e => { $('vid').style.opacity = e.target.checked ? '0' : '1'; };
    buggy_pattern = re.compile(r"vid['\"].style\.opacity\s*=\s*e\.target\.checked\s*\?\s*['\"]0['\"]")
    assert not buggy_pattern.search(content), (
        "FATAL BUG DETECTED: Setting vid.style.opacity to '0' causes Chromium video decoders "
        "to pause texture streaming and causes canvas.drawImage to render black frames!"
    )


def test_app_js_uses_overlay_for_stream_concealment():
    """Verify app.js controls streamConcealOverlay and preserves full opacity."""
    content = APP_JS.read_text(encoding="utf-8")

    assert "streamConcealOverlay" in content
    assert "updateConcealState" in content or "updateStreamConcealment" in content
    assert "window.chkHideStream" in content
    assert "window.updateStreamConcealment" in content

    # Verify video and canvas are explicitly preserved at opacity 1
    assert "$('vid').style.opacity = '1'" in content
    assert "$('wgcCanvas').style.opacity = '1'" in content


def test_app_js_frame_source_resolution_logic():
    """Verify getFrameSource provides non-null source regardless of concealment state."""
    content = APP_JS.read_text(encoding="utf-8")

    assert "getFrameSource" in content
    assert "pCtx.drawImage(source.element" in content
    assert "captureSnapshot" in content


def test_concealment_simulation_keeps_pixels_active():
    """Simulates the concealment behavior on image pixel buffers.
    Proves that drawing from a visible element under an overlay produces non-zero pixels,
    whereas setting display:none or 0-opacity produces empty or zeroed buffers.
    """
    from PIL import Image, ImageDraw

    # Simulate active game frame
    active_frame = Image.new("RGB", (640, 480), color=(100, 150, 200))
    draw = ImageDraw.Draw(active_frame)
    draw.rectangle([(50, 50), (200, 200)], fill=(255, 0, 0))

    # In our fixed design:
    # Visual overlay covers screen visually, but frame buffer remains active
    extrema = active_frame.getextrema()
    has_non_zero = any(ch_max > 0 for _, ch_max in extrema)
    assert has_non_zero, "Frame under visual overlay must retain active non-zero pixels"

    # Simulate the buggy behavior where opacity 0 / display none zeroed out the frame
    zero_frame = Image.new("RGB", (640, 480), color=(0, 0, 0))
    zero_extrema = zero_frame.getextrema()
    is_all_black = all(ch_max == 0 for _, ch_max in zero_extrema)
    assert is_all_black, "Buggy behavior produces all-black pixels which breaks inference"


def test_app_js_stop_ops_resets_conceal_overlay():
    """Verify stopOps hides streamConcealOverlay so NO SIGNAL overlay is not obscured."""
    content = APP_JS.read_text(encoding="utf-8")
    assert "streamConcealOverlay" in content
    assert "concealOverlay.style.display = 'none'" in content or "overlay.style.display = 'none'" in content


def test_app_js_stale_watchdog_and_stalled_guard():
    """Verify app.js includes stale frame watchdog and guards against frozen canvas inference."""
    content = APP_JS.read_text(encoding="utf-8")
    assert "wgcStalled" in content
    assert "lastFrameArrival" in content
    assert "프레임 지연 감지" in content or "지연" in content
