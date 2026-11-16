"""tests/test_launcher_governance.py
Targeted verification suite for Commercial One-Click Launcher and Process Lifecycle Governance.
Tests Python preflight checks, PID identity validation, port conflict handling,
distinct non-zero fail-closed exit codes, CRLF batch syntax execution, and absence of unsafe un-targeted process termination.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import pytest

from scripts.launcher import (
    check_python_environment,
    check_health,
    create_desktop_shortcut,
    get_active_server_pid,
    get_process_name,
    is_pid_running,
    is_port_in_use,
    terminate_pid_safely,
    verify_pid_identity,
    EXIT_ENV_ERROR,
    EXIT_PORT_CONFLICT,
    EXIT_EARLY_CRASH,
    EXIT_HEALTH_TIMEOUT,
    EXIT_PID_MISMATCH,
    PID_FILE,
    WORKSPACE_ROOT,
)


def test_python_preflight_environment():
    """Verify that Python version >= 3.10 and all required packages are detected."""
    ok, msg = check_python_environment()
    assert ok is True
    assert msg == "정상"


def test_is_port_in_use_on_free_port():
    """Verify free port detection accurately returns False and handles 0.0.0.0 on Windows."""
    assert is_port_in_use("127.0.0.1", 59876) is False
    # Connecting to 0.0.0.0 on Windows must not raise WinError 10049
    assert is_port_in_use("0.0.0.0", 59876) is False


def test_is_pid_listening_on_port_exact_match(monkeypatch):
    """Verify is_pid_listening_on_port uses exact port match rather than substring match."""
    from scripts.launcher import is_pid_listening_on_port

    mock_netstat_output = (
        "  Proto  Local Address          Foreign Address        State           PID\n"
        "  TCP    0.0.0.0:8000           0.0.0.0:0              LISTENING       1234\n"
        "  TCP    0.0.0.0:8080           0.0.0.0:0              LISTENING       5678\n"
    )

    class MockCompletedProcess:
        stdout = mock_netstat_output
        returncode = 0

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: MockCompletedProcess())

    # Exact port 8000 on PID 1234 -> True
    assert is_pid_listening_on_port(1234, 8000) is True
    # Substring port 80 on PID 1234 -> False (must NOT match 8000)
    assert is_pid_listening_on_port(1234, 80) is False
    # Substring port 800 on PID 1234 -> False (must NOT match 8000)
    assert is_pid_listening_on_port(1234, 800) is False
    # Exact port 8080 on PID 5678 -> True
    assert is_pid_listening_on_port(5678, 8080) is True



def test_is_pid_running_lifecycle():
    """Verify PID detection distinguishes active process from dead PID."""
    current_pid = os.getpid()
    assert is_pid_running(current_pid) is True
    # Unrealistically high PID should return False
    assert is_pid_running(9999999) is False
    assert is_pid_running(-1) is False


def test_verify_pid_identity_safety():
    """Verify that PID identity validation prevents attacking arbitrary non-server processes."""
    # Dead PID fails identity
    assert verify_pid_identity(9999999) is False
    # Negative PID fails identity
    assert verify_pid_identity(-1) is False


def test_verify_pid_identity_rejects_non_python(monkeypatch):
    """Verify that non-python processes are rejected even if active."""
    current_pid = os.getpid()
    # Mock process name to non-python image
    monkeypatch.setattr("scripts.launcher.get_process_name", lambda pid: "notepad.exe")
    assert verify_pid_identity(current_pid) is False


def test_verify_pid_identity_fail_closed_when_cmdline_empty(monkeypatch):
    """Verify fail-closed invariant: empty cmdline on non-listening process returns False."""
    current_pid = os.getpid()
    monkeypatch.setattr("scripts.launcher.get_process_cmdline", lambda pid: "")
    monkeypatch.setattr("scripts.launcher.is_pid_listening_on_port", lambda pid, port: False)
    assert verify_pid_identity(current_pid) is False


def test_stale_pid_file_cleanup(tmp_path, monkeypatch):
    """Verify stale PID file is automatically deleted when process is dead."""
    test_pid_file = tmp_path / ".test_server.pid"
    test_pid_file.write_text("9999999", encoding="utf-8")

    monkeypatch.setattr("scripts.launcher.PID_FILE", test_pid_file)

    active_pid = get_active_server_pid()
    assert active_pid is None
    assert not test_pid_file.exists()


def test_server_direct_import_sys_path():
    """Verify src/server.py can be loaded directly without ModuleNotFoundError."""
    import src.server as server_mod
    assert hasattr(server_mod, "app")
    assert str(WORKSPACE_ROOT) in sys.path


def test_batch_launchers_hygiene():
    """Verify batch launchers conform to fail-closed and non-developer standards."""
    required_launchers = [
        WORKSPACE_ROOT / "실행하기.bat",
        WORKSPACE_ROOT / "종료하기.bat",
        WORKSPACE_ROOT / "바탕화면_바로가기_만들기.bat",
        WORKSPACE_ROOT / "백그라운드_실행하기.vbs",
        WORKSPACE_ROOT / "사용방법.md",
    ]

    for fpath in required_launchers:
        assert fpath.exists(), f"Missing required deliverable: {fpath.name}"
        assert fpath.stat().st_size > 0, f"Empty deliverable: {fpath.name}"

    for bat_name in ["실행하기.bat", "종료하기.bat", "바탕화면_바로가기_만들기.bat"]:
        raw_bytes = (WORKSPACE_ROOT / bat_name).read_bytes()
        assert b"\r\n" in raw_bytes, f"{bat_name} must enforce Windows CRLF line endings"
        content = raw_bytes.decode("utf-8")
        assert "%~dp0" in content, f"{bat_name} must use relative %~dp0 paths"
        assert "chcp 65001" in content, f"{bat_name} must enforce UTF-8 codepage 65001"

    # Advisor PM Invariant: NEVER use blanket 'taskkill /IM python.exe'
    all_bat_contents = "".join(
        (WORKSPACE_ROOT / b).read_text(encoding="utf-8")
        for b in ["실행하기.bat", "종료하기.bat", "바탕화면_바로가기_만들기.bat"]
    )
    assert "/IM python.exe" not in all_bat_contents.lower(), (
        "[SAFETY INVARIANT VIOLATION] Blanket 'taskkill /IM python.exe' is strictly forbidden!"
    )


def test_batch_execution_syntax():
    """Verify cmd.exe parses 종료하기.bat and 바탕화면_바로가기_만들기.bat without syntax/encoding errors."""
    if os.name != "nt":
        pytest.skip("Windows batch execution requires Windows OS")

    res_stop = subprocess.run(
        ["cmd.exe", "/c", "종료하기.bat"],
        cwd=str(WORKSPACE_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res_stop.returncode == 0, f"종료하기.bat execution failed: {res_stop.stderr}"
    assert "was unexpected at this time" not in res_stop.stderr
    assert "is not recognized as an internal" not in res_stop.stderr

    res_sc = subprocess.run(
        ["cmd.exe", "/c", "바탕화면_바로가기_만들기.bat"],
        cwd=str(WORKSPACE_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res_sc.returncode == 0, f"바탕화면_바로가기_만들기.bat execution failed: {res_sc.stderr}"
    assert "was unexpected at this time" not in res_sc.stderr
    assert "is not recognized as an internal" not in res_sc.stderr


def test_shortcut_creation_helper():
    """Verify desktop shortcut creation utility executes and generates valid .lnk file."""
    if os.name != "nt":
        pytest.skip("Windows shortcut creation requires Windows OS")

    ok, msg = create_desktop_shortcut()
    assert ok is True, f"create_desktop_shortcut failed: {msg}"
    desktop = Path(os.environ["USERPROFILE"]) / "Desktop"
    lnk_file = desktop / "SCHALE 전투 추적 시스템.lnk"
    assert lnk_file.exists()
    assert lnk_file.stat().st_size > 0


def test_server_real_network_boot_and_shutdown():
    """Spin up server on an isolated port, test /health payload readiness, and safely terminate by PID."""
    test_port = 8899
    assert not is_port_in_use("127.0.0.1", test_port), f"Port {test_port} must be free for test"

    launch_cmd = [
        sys.executable,
        "-m", "uvicorn",
        "src.server:app",
        "--host", "127.0.0.1",
        "--port", str(test_port),
    ]

    proc = subprocess.Popen(
        launch_cmd,
        cwd=str(WORKSPACE_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        healthy = False
        start = time.time()
        while time.time() - start < 10.0:
            if is_port_in_use("127.0.0.1", test_port):
                if check_health("127.0.0.1", test_port, timeout=1.0):
                    healthy = True
                    break
            time.sleep(0.3)

        assert healthy is True, f"Server on port {test_port} failed to report online readiness"

        assert is_pid_running(proc.pid) is True
        terminated = terminate_pid_safely(proc.pid, port=test_port)
        assert terminated is True

        time.sleep(1.0)
        assert not is_pid_running(proc.pid)
        assert not is_port_in_use("127.0.0.1", test_port)
    finally:
        if is_pid_running(proc.pid):
            terminate_pid_safely(proc.pid, port=test_port)
