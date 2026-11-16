"""scripts/verify_visual_output.py
Captures representative visual screenshots of SCHALE KTOS UI for verification.
Uses a lightweight HTTP server to serve the workspace so all /static/ assets load cleanly.
"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time

from playwright.sync_api import sync_playwright

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
SCREENSHOTS_DIR = WORKSPACE_DIR / "docs" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

def run_server(port, stop_event):
    handler = partial(SimpleHTTPRequestHandler, directory=str(WORKSPACE_DIR))
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        httpd.timeout = 0.5
        while not stop_event.is_set():
            httpd.handle_request()

def capture_visuals():
    port = 8765
    stop_event = threading.Event()
    server_thread = threading.Thread(target=run_server, args=(port, stop_event), daemon=True)
    server_thread.start()
    time.sleep(1)

    url = f"http://127.0.0.1:{port}/static/index.html"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        # 1. Load Day Ops theme
        page.goto(url)
        page.wait_for_timeout(1000)

        # Ensure light theme
        page.evaluate("() => { document.documentElement.classList.remove('dark'); localStorage.setItem('schale_theme', 'light'); }")
        page.wait_for_timeout(300)

        # Screenshot 1: Day Ops Clean Light Dashboard
        shot1 = SCREENSHOTS_DIR / "01_day_ops_dashboard.png"
        page.screenshot(path=str(shot1), full_page=False)
        print(f"Captured: {shot1}")

        # 2. Toggle to Night Ops
        page.click("#themeToggleBtn")
        page.wait_for_timeout(500)

        # Screenshot 2: Night Ops OLED Tactical Dark Dashboard
        shot2 = SCREENSHOTS_DIR / "02_night_ops_dashboard.png"
        page.screenshot(path=str(shot2), full_page=False)
        print(f"Captured: {shot2}")

        # 3. Inject synthetic test record into Reviewer and open Review Center
        page.evaluate("""() => {
            const testCanvas = document.createElement('canvas');
            testCanvas.width = 1280;
            testCanvas.height = 720;
            const tCtx = testCanvas.getContext('2d');

            // Draw dark tactical background grid
            tCtx.fillStyle = '#0a0f1d';
            tCtx.fillRect(0, 0, 1280, 720);
            tCtx.strokeStyle = 'rgba(56, 189, 248, 0.15)';
            tCtx.lineWidth = 1;
            for (let x = 0; x < 1280; x += 64) {
                tCtx.beginPath(); tCtx.moveTo(x, 0); tCtx.lineTo(x, 720); tCtx.stroke();
            }
            for (let y = 0; y < 720; y += 64) {
                tCtx.beginPath(); tCtx.moveTo(0, y); tCtx.lineTo(1280, y); tCtx.stroke();
            }

            // Draw mock targets
            tCtx.fillStyle = '#1e293b';
            tCtx.fillRect(200, 150, 240, 320);
            tCtx.fillStyle = '#334155';
            tCtx.fillRect(600, 200, 300, 220);

            const testDataUrl = testCanvas.toDataURL('image/jpeg', 0.9);

            const mockRecord = {
                schemaVersion: '1.0.0',
                recordId: 'rec_test_visual_01',
                sessionId: 'SES_VERIFY_KTOS',
                capturedAtUtc: new Date().toISOString(),
                originalWidth: 1280,
                originalHeight: 720,
                origBase64: testDataUrl,
                prevBase64: testDataUrl,
                coordinateSpace: 'letterbox_640',
                reviewStatus: 'pending',
                reviewedAnnotations: [
                    {
                        classId: 0,
                        className: 'Drone_Scout',
                        confidence: 0.94,
                        box: { x1: 200, y1: 150, x2: 440, y2: 470, width: 240, height: 320 }
                    },
                    {
                        classId: 1,
                        className: 'Decagrammaton',
                        confidence: 0.88,
                        box: { x1: 600, y1: 200, x2: 900, y2: 420, width: 300, height: 220 }
                    }
                ]
            };

            window.Reviewer.addRecord(mockRecord);
            document.getElementById('btnOpenReview').click();
            window.Reviewer.selectRecord(0);
        }""")
        page.wait_for_timeout(600)

        # Screenshot 3: Review Center Modal with KTOS Reticles & Triage UI
        shot3 = SCREENSHOTS_DIR / "03_review_center_triage.png"
        page.screenshot(path=str(shot3), full_page=False)
        print(f"Captured: {shot3}")

        browser.close()

    stop_event.set()
    server_thread.join(timeout=2)
    print("Verification screenshots captured successfully.")

if __name__ == "__main__":
    capture_visuals()
