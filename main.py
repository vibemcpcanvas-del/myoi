"""
myoi - SCHALE Tactical Tracking Standalone Entry Point
Enables one-click binary execution and PyInstaller freeze compatibility.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

_IS_FROZEN = getattr(sys, "frozen", False)
_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.launcher import main

if __name__ == "__main__":
    # If launched with no arguments in frozen GUI mode, default to 'run'
    if len(sys.argv) == 1:
        sys.argv.append("run")
    sys.exit(main())
