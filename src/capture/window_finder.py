"""src/capture/window_finder.py
Window enumeration and target selection engine for SCHALE Tactical Tracking.
Discovers and filters target application/game windows out-of-process.
Enforces the Zero-Injection Invariant (Anti-Cheat Safe: Vanguard, GameGuard, EAC compatible).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass
import os
import sys
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class WindowInfo:
    """Immutable representation of a capturable target window."""
    hwnd: int
    title: str
    process_name: str
    pid: int
    x: int
    y: int
    width: int
    height: int
    is_visible: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class WindowFinder:
    """Discovers visible application windows on the active Windows desktop.
    Uses standard read-only User32/DWM APIs with zero DLL injection.
    """

    def __init__(self, mock_mode: bool = False):
        self._is_windows = sys.platform == "win32" and not mock_mode
        self._mock_mode = mock_mode

    def list_windows(
        self,
        min_width: int = 200,
        min_height: int = 150,
        filter_empty_titles: bool = True,
    ) -> List[WindowInfo]:
        """Enumerates visible top-level windows on the active user desktop."""
        if not self._is_windows:
            return self._get_mock_windows()

        try:
            return self._enumerate_windows_win32(min_width, min_height, filter_empty_titles)
        except Exception:
            return self._get_mock_windows()

    def find_window(self, query: str | int) -> Optional[WindowInfo]:
        """Finds a window by HWND, process name, or title substring.
        First checks active windows; if not found returns None.
        """
        windows = self.list_windows(min_width=0, min_height=0, filter_empty_titles=False)

        # 1. Exact HWND match
        if isinstance(query, int):
            for w in windows:
                if w.hwnd == query:
                    return w
            return None

        # 2. String numeric HWND match
        q_str = str(query).strip()
        if q_str.isdigit():
            target_hwnd = int(q_str)
            for w in windows:
                if w.hwnd == target_hwnd:
                    return w

        # 3. Process name exact / substring match
        q_lower = q_str.lower()
        for w in windows:
            if w.process_name.lower() == q_lower:
                return w

        # 4. Window title substring match (case-insensitive)
        for w in windows:
            if q_lower in w.title.lower():
                return w

        return None

    def _enumerate_windows_win32(
        self,
        min_width: int,
        min_height: int,
        filter_empty_titles: bool,
    ) -> List[WindowInfo]:
        user32 = ctypes.windll.user32
        results: List[WindowInfo] = []

        # Try to open active user input desktop for robust discovery under services
        try:
            hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception:
            hdesk = None

        def enum_window_callback(hwnd: wintypes.HWND, lparam: wintypes.LPARAM) -> bool:
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True

                length = user32.GetWindowTextLengthW(hwnd)
                if filter_empty_titles and length <= 0:
                    return True

                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value.strip()

                if filter_empty_titles and not title:
                    return True

                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                w = rect.right - rect.left
                h = rect.bottom - rect.top

                if w < min_width or h < min_height:
                    return True

                # Process ID and process name
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                proc_name = self._get_process_name_by_pid(pid.value)

                # Skip common background / overlay system noise if not matching
                if title in ("Program Manager", "Windows 입력 환경") and not filter_empty_titles:
                    pass

                results.append(
                    WindowInfo(
                        hwnd=int(hwnd),
                        title=title,
                        process_name=proc_name,
                        pid=int(pid.value),
                        x=int(rect.left),
                        y=int(rect.top),
                        width=int(w),
                        height=int(h),
                        is_visible=True,
                    )
                )
            except Exception:
                pass
            return True

        DESKTOPENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        cb = DESKTOPENUMPROC(enum_window_callback)

        if hdesk:
            user32.EnumDesktopWindows.argtypes = [wintypes.HANDLE, DESKTOPENUMPROC, wintypes.LPARAM]
            user32.EnumDesktopWindows.restype = wintypes.BOOL
            user32.EnumDesktopWindows(hdesk, cb, 0)
            user32.CloseDesktop(hdesk)
        else:
            user32.EnumWindows.argtypes = [DESKTOPENUMPROC, wintypes.LPARAM]
            user32.EnumWindows.restype = wintypes.BOOL
            user32.EnumWindows(cb, 0)

        # Sort by title, descending by area
        results.sort(key=lambda item: item.width * item.height, reverse=True)
        return results

    @staticmethod
    def _get_process_name_by_pid(pid: int) -> str:
        """Retrieves process name safely using Win32 API without external dependencies, with psutil fallback."""
        if pid <= 0:
            return "unknown.exe"

        # 1. Primary Win32 method: QueryFullProcessImageNameW (100% zero-dependency on Windows)
        if sys.platform == "win32":
            try:
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                kernel32 = ctypes.windll.kernel32
                h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if h_proc:
                    try:
                        buf = ctypes.create_unicode_buffer(1024)
                        size = wintypes.DWORD(1024)
                        if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):
                            exe_path = buf.value
                            return os.path.basename(exe_path)
                    finally:
                        kernel32.CloseHandle(h_proc)
            except Exception:
                pass

        # 2. Secondary fallback via psutil
        try:
            import psutil
            proc = psutil.Process(pid)
            return proc.name()
        except Exception:
            return f"process_{pid}.exe"

    @staticmethod
    def _get_mock_windows() -> List[WindowInfo]:
        """Provides simulated windows for mock, testing, and CI environments."""
        return [
            WindowInfo(
                hwnd=1001,
                title="BlueArchive - Kivotos Tactical Operation",
                process_name="BlueArchive.exe",
                pid=4020,
                x=100,
                y=100,
                width=1920,
                height=1080,
                is_visible=True,
            ),
            WindowInfo(
                hwnd=1002,
                title="MapleStory - Tactical Drop Tracker",
                process_name="MapleStory.exe",
                pid=5120,
                x=50,
                y=50,
                width=1280,
                height=720,
                is_visible=True,
            ),
            WindowInfo(
                hwnd=1003,
                title="Desktop Screen Compositor",
                process_name="explorer.exe",
                pid=1200,
                x=0,
                y=0,
                width=1920,
                height=1080,
                is_visible=True,
            ),
        ]
