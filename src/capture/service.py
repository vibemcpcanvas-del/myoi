"""src/capture/service.py
Capture Service coordinator for SCHALE Tactical Tracking.
Manages asynchronous background capture workers, thread-safe frame buffers,
FPS regulation, and real-time streaming to WebSocket/HTTP consumers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from PIL import Image

from src.capture.window_finder import WindowFinder, WindowInfo
from src.capture.wgc_capture import NativeCaptureEngine, CaptureState

logger = logging.getLogger("schale.capture")


@dataclass
class CaptureTelemetry:
    fps: float = 0.0
    total_frames: int = 0
    dropped_frames: int = 0
    avg_latency_ms: float = 0.0
    last_frame_timestamp: float = 0.0
    status: str = "idle"
    state: str = CaptureState.IDLE.value
    target_window: Optional[Dict[str, Any]] = None
    backend: str = "none"
    seq_id: int = 0
    stalled: bool = False


class CaptureService:
    """Manages continuous background frame capture and distribution."""

    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode
        self.finder = WindowFinder(mock_mode=mock_mode)
        self.engine = NativeCaptureEngine(mock_mode=mock_mode)

        self._lock = threading.RLock()
        self._is_running = False
        self._current_state = CaptureState.IDLE
        self._last_frame_perf = 0.0
        self._target_hwnd: int = 0
        self._target_info: Optional[WindowInfo] = None
        self._target_fps: int = 20
        self._quality: int = 80

        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Thread-safe latest frame cache
        self._latest_image: Optional[Image.Image] = None
        self._latest_jpeg: Optional[bytes] = None
        self._latest_meta: Dict[str, Any] = {}

        # Telemetry
        self._telemetry = CaptureTelemetry()
        self._frame_times: List[float] = []

        # Listeners / async queues for frame broadcast
        self._async_queues: Set[asyncio.Queue] = set()

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def current_state(self) -> CaptureState:
        with self._lock:
            return self._current_state

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self._is_running,
                "status": self._telemetry.status,
                "state": self._telemetry.state,
                "targetHwnd": self._target_hwnd,
                "targetWindow": self._target_info.to_dict() if self._target_info else None,
                "targetFps": self._target_fps,
                "actualFps": round(self._telemetry.fps, 1),
                "totalFrames": self._telemetry.total_frames,
                "droppedFrames": self._telemetry.dropped_frames,
                "avgLatencyMs": round(self._telemetry.avg_latency_ms, 2),
                "backend": self._telemetry.backend,
                "lastFrameTimestamp": self._telemetry.last_frame_timestamp,
                "seqId": self._telemetry.seq_id,
                "stalled": self._telemetry.stalled,
            }

    def list_windows(self) -> List[Dict[str, Any]]:
        windows = self.finder.list_windows()
        return [w.to_dict() for w in windows]

    def start_capture(
        self,
        target: int | str = 0,
        fps: int = 20,
        quality: int = 80,
    ) -> Dict[str, Any]:
        """Starts background window or desktop capture."""
        with self._lock:
            self._target_fps = max(1, min(fps, 60))
            self._quality = max(10, min(quality, 100))
            return self._switch_target_locked(target)

    def switch_target(self, target: int | str = 0) -> Dict[str, Any]:
        """Dynamically switches active capture target window without tearing down streams."""
        with self._lock:
            return self._switch_target_locked(target)

    def _switch_target_locked(self, target: int | str = 0) -> Dict[str, Any]:
        """Internal lock-held target resolution and dynamic switching logic."""
        target_hwnd = 0
        target_info = None

        if target != 0 and target != "0" and target != "desktop":
            found = self.finder.find_window(target)
            if found:
                target_hwnd = found.hwnd
                target_info = found
            elif isinstance(target, int):
                target_hwnd = target
            elif str(target).lstrip("-").isdigit():
                target_hwnd = int(target)
            else:
                return {
                    "success": False,
                    "error": "target_window_not_found",
                    "message": f"Target window matching '{target}' was not found.",
                }

        self._target_hwnd = target_hwnd
        self._target_info = target_info

        # Purge stale frame cache to prevent leaking old window frames
        self._latest_image = None
        self._latest_jpeg = None
        self._latest_meta = {}
        self._last_frame_perf = time.perf_counter()
        self._telemetry.target_window = target_info.to_dict() if target_info else None
        self._telemetry.last_frame_timestamp = 0.0
        self._telemetry.seq_id += 1
        self._telemetry.stalled = False

        if not self._is_running or self._worker_thread is None or not self._worker_thread.is_alive():
            self._stop_event.clear()
            self._is_running = True
            self._current_state = CaptureState.CAPTURING
            self._telemetry.status = "capturing"
            self._telemetry.state = CaptureState.CAPTURING.value
            self._worker_thread = threading.Thread(
                target=self._capture_worker_loop,
                name="schale-wgc-worker",
                daemon=True,
            )
            self._worker_thread.start()
        else:
            self._current_state = CaptureState.CAPTURING
            self._telemetry.status = "capturing"
            self._telemetry.state = CaptureState.CAPTURING.value
            self._dispatch_control_to_queues({
                "type": "target_switched",
                "targetHwnd": target_hwnd,
                "targetTitle": target_info.title if target_info else "전체 데스크톱",
                "seq_id": self._telemetry.seq_id,
                "timestamp": time.time(),
            })

        return {
            "success": True,
            "targetHwnd": self._target_hwnd,
            "targetTitle": target_info.title if target_info else "전체 데스크톱",
            "fps": self._target_fps,
            "state": CaptureState.CAPTURING.value,
        }

    def stop_capture(self) -> Dict[str, Any]:
        with self._lock:
            self._stop_worker_locked()
            return {"success": True, "status": "stopped", "state": CaptureState.STOPPED.value}

    def _stop_worker_locked(self) -> None:
        self._is_running = False
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)
        self._worker_thread = None
        self._current_state = CaptureState.STOPPED
        self._telemetry.status = "stopped"
        self._telemetry.state = CaptureState.STOPPED.value
        self._telemetry.stalled = False

    def get_latest_frame(self) -> Tuple[Optional[bytes], Dict[str, Any]]:
        with self._lock:
            if self._current_state in (CaptureState.PAUSED_MINIMIZED, CaptureState.FAILED):
                return None, {
                    "status": self._current_state.value,
                    "state": self._current_state.value,
                    "error": "capture_not_active",
                }
            if self._telemetry.last_frame_timestamp > 0 and (time.time() - self._telemetry.last_frame_timestamp > 2.0):
                return None, {
                    "status": "stale_frame",
                    "state": CaptureState.DEGRADED.value,
                    "error": "frame_timeout",
                }
            return self._latest_jpeg, dict(self._latest_meta)

    def get_latest_image(self) -> Optional[Image.Image]:
        with self._lock:
            if self._current_state in (CaptureState.PAUSED_MINIMIZED, CaptureState.FAILED):
                return None
            return self._latest_image.copy() if self._latest_image else None

    def register_async_queue(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._async_queues.add(queue)

    def unregister_async_queue(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._async_queues.discard(queue)

    def _capture_worker_loop(self) -> None:
        """Continuous capture loop running on dedicated worker thread with fail-closed watchdog."""
        interval = 1.0 / float(self._target_fps)
        latencies: List[float] = []

        while not self._stop_event.is_set():
            t0 = time.perf_counter()

            try:
                with self._lock:
                    current_target = self._target_hwnd
                img, meta = self.engine.capture_frame(current_target)

                if img is not None:
                    jpeg_bytes = self.engine.encode_jpeg(img, quality=self._quality)
                    now = time.time()
                    p_now = time.perf_counter()

                    with self._lock:
                        self._latest_image = img
                        self._latest_jpeg = jpeg_bytes
                        self._latest_meta = meta
                        self._telemetry.total_frames += 1
                        self._telemetry.seq_id += 1
                        meta["seq_id"] = self._telemetry.seq_id
                        meta["state"] = CaptureState.CAPTURING.value
                        self._telemetry.last_frame_timestamp = now
                        self._telemetry.backend = meta.get("backend", "unknown")
                        self._telemetry.status = "capturing"
                        self._telemetry.state = CaptureState.CAPTURING.value
                        self._telemetry.stalled = False
                        self._current_state = CaptureState.CAPTURING
                        self._last_frame_perf = p_now

                        # Track FPS
                        self._frame_times.append(now)
                        cutoff = now - 2.0
                        self._frame_times = [t for t in self._frame_times if t >= cutoff]
                        if len(self._frame_times) > 1:
                            duration = self._frame_times[-1] - self._frame_times[0]
                            self._telemetry.fps = (len(self._frame_times) - 1) / max(0.001, duration)

                        # Track Latency
                        elapsed = meta.get("elapsed_ms", 0.0)
                        latencies.append(elapsed)
                        if len(latencies) > 30:
                            latencies.pop(0)
                        self._telemetry.avg_latency_ms = sum(latencies) / max(1, len(latencies))

                    # Broadcast frame to async WebSocket queues
                    self._dispatch_frame_to_queues(jpeg_bytes, meta)
                else:
                    status_str = meta.get("status", "capture_error")
                    if status_str == "window_minimized":
                        new_state = CaptureState.PAUSED_MINIMIZED
                    elif status_str in ("window_not_found", "window_zero_bounds"):
                        new_state = CaptureState.FAILED
                    else:
                        new_state = CaptureState.DEGRADED

                    with self._lock:
                        self._current_state = new_state
                        self._telemetry.status = status_str
                        self._telemetry.state = new_state.value
                        self._telemetry.dropped_frames += 1
                        self._telemetry.stalled = True

                    # Broadcast state transition immediately to WebSocket consumers
                    self._dispatch_control_to_queues({
                        "type": "capture_state",
                        "state": new_state.value,
                        "status": status_str,
                        "error": meta.get("error", "Capture degraded"),
                        "hwnd": current_target,
                        "timestamp": time.time(),
                    })
            except Exception as e:
                logger.warning(f"Error in capture worker loop: {e}")
                with self._lock:
                    self._current_state = CaptureState.FAILED
                    self._telemetry.status = "error"
                    self._telemetry.state = CaptureState.FAILED.value
                    self._telemetry.dropped_frames += 1
                    self._telemetry.stalled = True

                self._dispatch_control_to_queues({
                    "type": "capture_state",
                    "state": CaptureState.FAILED.value,
                    "status": "error",
                    "error": str(e),
                    "hwnd": current_target,
                    "timestamp": time.time(),
                })

            # Fail-closed stale watchdog: flag DEGRADED if no frame captured for > 1.0s
            watchdog_alert = False
            with self._lock:
                if (
                    self._current_state == CaptureState.CAPTURING
                    and self._last_frame_perf > 0
                    and (time.perf_counter() - self._last_frame_perf > 1.0)
                ):
                    self._current_state = CaptureState.DEGRADED
                    self._telemetry.state = CaptureState.DEGRADED.value
                    self._telemetry.status = "stalled"
                    self._telemetry.stalled = True
                    watchdog_alert = True

            if watchdog_alert:
                self._dispatch_control_to_queues({
                    "type": "capture_state",
                    "state": CaptureState.DEGRADED.value,
                    "status": "stalled",
                    "error": "Frame capture stalled (>1.0s without fresh frame)",
                    "hwnd": current_target,
                    "timestamp": time.time(),
                })

            # Regulate FPS sleep
            calc_time = time.perf_counter() - t0
            sleep_time = interval - calc_time
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _dispatch_frame_to_queues(self, jpeg_bytes: bytes, meta: Dict[str, Any]) -> None:
        """Dispatches captured frame payload to registered asyncio queues non-blockingly."""
        with self._lock:
            queues = list(self._async_queues)

        for q in queues:
            try:
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait((jpeg_bytes, meta))
            except Exception:
                pass

    def _dispatch_control_to_queues(self, payload: Dict[str, Any]) -> None:
        """Dispatches control/state change message to registered asyncio queues non-blockingly."""
        with self._lock:
            queues = list(self._async_queues)

        for q in queues:
            try:
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(payload)
            except Exception:
                pass


# Global Singleton Service
_GLOBAL_CAPTURE_SERVICE: Optional[CaptureService] = None
_CAPTURE_LOCK = threading.Lock()


def get_capture_service(mock_mode: bool = False) -> CaptureService:
    global _GLOBAL_CAPTURE_SERVICE
    with _CAPTURE_LOCK:
        if _GLOBAL_CAPTURE_SERVICE is None:
            _GLOBAL_CAPTURE_SERVICE = CaptureService(mock_mode=mock_mode)
        return _GLOBAL_CAPTURE_SERVICE
