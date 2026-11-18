"""src/capture/__init__.py
Windows Graphics Capture (WGC) and Native Desktop Window Capture Package.
Anti-cheat safe (zero DLL injection, out-of-process capture).
"""

from src.capture.window_finder import WindowFinder, WindowInfo
from src.capture.wgc_capture import NativeCaptureEngine, CaptureState
from src.capture.service import CaptureService, CaptureTelemetry, get_capture_service

__all__ = [
    "WindowFinder",
    "WindowInfo",
    "NativeCaptureEngine",
    "CaptureState",
    "CaptureService",
    "CaptureTelemetry",
    "get_capture_service",
]
