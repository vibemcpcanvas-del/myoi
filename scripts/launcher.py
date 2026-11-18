"""scripts/launcher.py
Non-developer one-click launch controller for SCHALE Tactical Tracking.
Provides environment preflight verification, PID command-line and process name identity validation,
fail-closed safe targeted shutdown, distinct exit codes, desktop shortcut creation, and automatic browser launch.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

PID_FILE = WORKSPACE_ROOT / ".server.pid"
LOGS_DIR = WORKSPACE_ROOT / "logs"
LOG_FILE = LOGS_DIR / "server.log"

REQUIRED_PACKAGES = [
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("websockets", "websockets>=12.0"),
    ("httpx", "httpx"),
    ("pydantic", "pydantic"),
    ("PIL", "pillow"),
]

# Distinct Non-Zero Exit Codes for Discrete Fail-Closed Diagnostics
EXIT_ENV_ERROR = 2
EXIT_PORT_CONFLICT = 3
EXIT_EARLY_CRASH = 4
EXIT_HEALTH_TIMEOUT = 5
EXIT_PID_MISMATCH = 6


def check_python_environment(auto_heal: bool = True) -> tuple[bool, str]:
    """Verify Python version and required dependencies with automatic self-healing."""
    if sys.version_info < (3, 10):
        return False, (
            f"Python 버전이 낮습니다 ({sys.version.split()[0]}). "
            "Python 3.10 이상을 권장합니다."
        )

    missing = []
    for import_name, pkg_name in REQUIRED_PACKAGES:
        if importlib.util.find_spec(import_name) is None:
            missing.append((import_name, pkg_name))

    if missing and auto_heal:
        print("[*] 필수 패키지 누락 감지. 자동 자가 치유(Self-Healing) 설치를 진행합니다...")
        for import_name, pkg_name in missing:
            print(f"  -> 패키지 자동 설치 시도 중: {pkg_name} ...")
            try:
                res = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pkg_name],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=60,
                )
                if res.returncode == 0:
                    print(f"  [성공] {pkg_name} 자동 설치 완료!")
                else:
                    print(f"  [경고] {pkg_name} 자동 설치 실패: {res.stderr.strip() or res.stdout.strip()}")
            except Exception as e:
                print(f"  [경고] {pkg_name} 설치 중 예외 발생: {e}")

        # Re-check after auto-heal attempt
        importlib.invalidate_caches()
        remaining_missing = []
        for import_name, pkg_name in REQUIRED_PACKAGES:
            if importlib.util.find_spec(import_name) is None:
                remaining_missing.append(pkg_name)

        if not remaining_missing:
            return True, "정상"
        missing_names = remaining_missing
    else:
        missing_names = [pkg_name for _, pkg_name in missing]

    if missing_names:
        return False, (
            f"필수 패키지가 설치되어 있지 않습니다: {', '.join(missing_names)}\n"
            f"해결 방법: 터미널에서 다음 명령어를 실행해주세요.\n"
            f"  pip install -r requirements.txt"
        )

    return True, "정상"


def is_port_in_use(host: str, port: int) -> bool:
    """Check if a network port is already in use."""
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        result = sock.connect_ex((connect_host, port))
        return result == 0


def get_process_name(pid: int) -> str:
    """Retrieve process executable name (e.g. python.exe)."""
    if pid <= 0:
        return ""
    if os.name == "nt":
        try:
            cmd = ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=5)
            output = res.stdout.strip()
            if output and not output.startswith("INFO:"):
                parts = output.split(",")
                if parts:
                    return parts[0].strip('"').lower()
            return ""
        except Exception:
            return ""
    else:
        try:
            comm_path = Path(f"/proc/{pid}/comm")
            if comm_path.exists():
                return comm_path.read_text(encoding="utf-8").strip().lower()
        except Exception:
            pass
        return ""


def get_process_cmdline(pid: int) -> str:
    """Retrieve command line of process on Windows or POSIX for identity verification."""
    if pid <= 0:
        return ""
    if os.name == "nt":
        try:
            # Use PowerShell to retrieve process CommandLine securely without external binaries
            ps_cmd = (
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').CommandLine"
            )
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            return res.stdout.strip()
        except Exception:
            return ""
    else:
        try:
            cmd_path = Path(f"/proc/{pid}/cmdline")
            if cmd_path.exists():
                return cmd_path.read_text(encoding="utf-8", errors="replace").replace("\0", " ")
        except Exception:
            pass
        return ""


def is_pid_listening_on_port(pid: int, port: int) -> bool:
    """Check if the given PID is actively listening on port."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            res = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            for line in res.stdout.splitlines():
                line = line.strip()
                if "LISTENING" in line:
                    parts = line.split()
                    if len(parts) >= 5 and parts[-1] == str(pid):
                        local_addr = parts[1]
                        local_port = local_addr.rsplit(":", 1)[-1]
                        if local_port == str(port):
                            return True
            return False
        except Exception:
            return False
    return False


def is_pid_running(pid: int) -> bool:
    """Check if process with given PID is currently active."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            cmd = ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=5)
            output = res.stdout.strip()
            if output and not output.startswith("INFO:"):
                return str(pid) in output
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def verify_pid_identity(pid: int, port: int | None = None) -> bool:
    """Verify that PID belongs specifically to SCHALE/uvicorn/server to avoid killing innocent processes."""
    if not is_pid_running(pid):
        return False

    proc_name = get_process_name(pid)
    valid_proc_names = {"python.exe", "pythonw.exe", "py.exe", "python", "python3"}
    if proc_name and proc_name not in valid_proc_names:
        # P0 safety invariant: reject non-python processes immediately
        return False

    cmdline = get_process_cmdline(pid).lower()
    indicators = [
        "uvicorn",
        "server.py",
        "run.py",
        "myoi",
        "src.server",
        "launcher.py",
        "launcher",
        "schale",
    ]
    if cmdline:
        return any(ind in cmdline for ind in indicators)

    # Fail-closed: only accept if it is a verified python process listening on the target port
    target_port = port if port is not None else 8000
    if proc_name in valid_proc_names and is_pid_listening_on_port(pid, target_port):
        return True

    return False


def get_active_server_pid(port: int | None = None) -> int | None:
    """Read PID file and return PID if process is alive and verified; clean stale file if dead."""
    if not PID_FILE.exists():
        return None
    try:
        content = PID_FILE.read_text(encoding="utf-8").strip()
        if not content:
            PID_FILE.unlink(missing_ok=True)
            return None
        pid = int(content)
        if is_pid_running(pid) and verify_pid_identity(pid, port=port):
            return pid
        # Stale PID file
        PID_FILE.unlink(missing_ok=True)
        return None
    except Exception:
        PID_FILE.unlink(missing_ok=True)
        return None


def terminate_pid_safely(pid: int, port: int | None = None) -> bool:
    """Terminate process tree associated with PID safely, enforcing identity verification."""
    if not verify_pid_identity(pid, port=port):
        print(f"[안전 차단] PID {pid}는 SCHALE 서버 프로세스 식별자가 아닙니다. 종료를 중단합니다.")
        return False

    if os.name == "nt":
        cmd = ["taskkill", "/PID", str(pid), "/T", "/F"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return res.returncode == 0
    else:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            if is_pid_running(pid):
                os.kill(pid, signal.SIGKILL)
            return True
        except Exception:
            return False


def check_health(host: str, port: int, timeout: float = 1.5) -> bool:
    """Query /health endpoint to verify real application readiness and valid payload."""
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{connect_host}:{port}/health"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SCHALE-Launcher/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                raw_body = response.read().decode("utf-8", errors="replace")
                data = json.loads(raw_body)
                return data.get("status") == "online" and "service" in data
            return False
    except Exception:
        return False


def open_browser_when_ready(host: str, port: int, max_wait: float = 15.0) -> None:
    """Background helper that polls health check and opens browser when online."""
    start_time = time.time()
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{connect_host}:{port}"
    while time.time() - start_time < max_wait:
        if check_health(host, port, timeout=1.0):
            print(f"\n[안내] 서버 준비 완료! 웹 브라우저를 엽니다: {url}")
            webbrowser.open(url)
            return
        time.sleep(0.4)


def create_desktop_shortcut() -> tuple[bool, str]:
    """Create a Windows desktop shortcut pointing to 실행하기.bat."""
    if os.name != "nt":
        return False, "바탕화면 바로가기 생성은 Windows 환경에서만 지원됩니다."

    bat_target = WORKSPACE_ROOT / "실행하기.bat"
    if not bat_target.exists():
        return False, f"대상 배치 파일이 존재하지 않습니다: {bat_target}"

    try:
        target_str = str(bat_target.resolve())
        workdir_str = str(WORKSPACE_ROOT.resolve())
        icon_path = WORKSPACE_ROOT / "static" / "favicon.ico"
        icon_arg = f"$s.IconLocation = '{icon_path.resolve()}';" if icon_path.exists() else ""

        ps_script = (
            "$ws = New-Object -ComObject WScript.Shell; "
            "$desktop = [Environment]::GetFolderPath('Desktop'); "
            "$shortcutPath = Join-Path $desktop 'SCHALE 전투 추적 시스템.lnk'; "
            "$s = $ws.CreateShortcut($shortcutPath); "
            f"$s.TargetPath = '{target_str}'; "
            f"$s.WorkingDirectory = '{workdir_str}'; "
            "$s.Description = 'SCHALE 전투 추적 및 YOLO 데이터 수집 시스템'; "
            f"{icon_arg} "
            "$s.Save()"
        )

        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if res.returncode == 0:
            return True, "바탕화면에 'SCHALE 전투 추적 시스템' 바로가기가 성공적으로 생성되었습니다!"
        else:
            err = res.stderr.strip() or res.stdout.strip()
            return False, f"바로가기 생성 실패: {err}"
    except Exception as e:
        return False, f"바로가기 생성 중 예외 발생: {e}"


def cmd_run(args: argparse.Namespace) -> int:
    """Run SCHALE server in foreground console with automatic browser launch."""
    print("========================================================================")
    print("  SCHALE 전투 추적 시스템 (myoi) 실행 중...")
    print("========================================================================")

    # 1. Environment check and self-healing FIRST before importing application dependencies
    ok, msg = check_python_environment()
    if not ok:
        print(f"[환경 오류] {msg}")
        return EXIT_ENV_ERROR

    from src.config import get_config
    import uvicorn

    config = get_config()
    host = args.host or config.host
    port = args.port or config.port
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    server_url = f"http://{connect_host}:{port}"

    # 2. Check if already running
    active_pid = get_active_server_pid(port=port)
    if active_pid:
        print(f"[알림] SCHALE 서버가 이미 정상 실행 중입니다. (PID: {active_pid})")
        print(f"[안내] 웹 브라우저를 엽니다: {server_url}")
        if not args.no_browser:
            webbrowser.open(server_url)
        return 0

    # 3. Check port conflict
    if is_port_in_use(host, port):
        print(f"[오류] 포트 {port}가 다른 프로그램에 의해 이미 사용 중입니다!")
        print(f"[안내] 포트 {port}를 사용 중인 프로그램을 종료하거나 .env 파일에서 PORT를 변경해주세요.")
        return EXIT_PORT_CONFLICT

    # 4. Prepare logs directory
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # 5. Launch background browser poller thread
    if not args.no_browser:
        t = threading.Thread(target=open_browser_when_ready, args=(host, port), daemon=True)
        t.start()

    print(f"[*] 서버 주소: {server_url}")
    print(f"[*] 프로세스 PID: {os.getpid()}")
    try:
        from src.capture import WindowFinder
        finder = WindowFinder(mock_mode=config.mock_inference)
        wins = finder.list_windows()
        print(f"[*] Native WGC Sidecar: 준비 완료 ({len(wins)}개 대상 창 감지 가능)")
    except Exception as e:
        print(f"[!] Native WGC Sidecar: 비활성 ({e})")
    print("------------------------------------------------------------------------")
    print("  * 서버를 종료하시려면 이 창을 닫거나, '종료하기.bat'을 실행하십시오.")
    print("========================================================================")

    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    try:
        uvicorn.run("src.server:app", host=host, port=port, log_level="info")
        return 0
    except KeyboardInterrupt:
        print("\n[*] 사용자에 의해 서버가 중단되었습니다.")
        return 0
    except Exception as e:
        print(f"\n[오류] 서버 비정상 중단: {e}")
        return EXIT_EARLY_CRASH
    finally:
        PID_FILE.unlink(missing_ok=True)


def cmd_start(args: argparse.Namespace) -> int:
    """Start SCHALE server in detached background mode with strict health confirmation."""
    print("========================================================================")
    print("  [SCHALE TACTICAL TRACKING] 서버 시작 준비 중...")
    print("========================================================================")

    # 1. Environment check and self-healing FIRST before importing application dependencies
    ok, msg = check_python_environment()
    if not ok:
        print(f"[환경 오류] {msg}")
        return EXIT_ENV_ERROR

    from src.config import get_config
    config = get_config()

    host = args.host or config.host
    port = args.port or config.port
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    server_url = f"http://{connect_host}:{port}"

    # 2. Check if already running
    active_pid = get_active_server_pid(port=port)
    if active_pid:
        print(f"[알림] SCHALE 서버가 이미 정상 실행 중입니다. (PID: {active_pid})")
        print(f"[안내] 웹 브라우저를 엽니다: {server_url}")
        if not args.no_browser:
            webbrowser.open(server_url)
        return 0

    # 3. Check port conflict
    if is_port_in_use(host, port):
        print(f"[오류] 포트 {port}가 다른 프로그램에 의해 이미 사용 중입니다!")
        print(f"[안내] 포트 {port}를 사용 중인 다른 프로그램을 종료해주세요.")
        return EXIT_PORT_CONFLICT

    # 4. Prepare logs directory
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    launch_cmd = [
        sys.executable,
        "-m", "uvicorn",
        "src.server:app",
        "--host", host,
        "--port", str(port),
    ]

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{WORKSPACE_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"

    log_fp = open(LOG_FILE, "a", encoding="utf-8")
    log_fp.write(f"\n--- [SCHALE SERVER LAUNCH: {time.strftime('%Y-%m-%d %H:%M:%S')}] ---\n")
    log_fp.flush()

    creation_flags = 0
    if os.name == "nt":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    proc = subprocess.Popen(
        launch_cmd,
        cwd=str(WORKSPACE_ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        creationflags=creation_flags,
    )
    try:
        log_fp.close()
    except Exception:
        pass

    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    print(f"[*] 서버 프로세스가 시작되었습니다. (PID: {proc.pid})")
    print("[*] 서버 준비 상태를 점검 중입니다...", end="", flush=True)

    max_wait = 15.0
    start_time = time.time()
    server_ready = False

    while time.time() - start_time < max_wait:
        if proc.poll() is not None:
            print("\n[오류] 서버 프로세스가 비정상 조기 종료되었습니다.")
            print(f"로그 파일({LOG_FILE}) 내용:")
            if LOG_FILE.exists():
                lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines[-15:]:
                    print(f"  {line}")
            PID_FILE.unlink(missing_ok=True)
            return EXIT_EARLY_CRASH

        if check_health(host, port, timeout=1.0):
            server_ready = True
            break
        time.sleep(0.5)
        print(".", end="", flush=True)

    print()

    if not server_ready:
        print("========================================================================")
        print("[실패] 서버가 헬스체크 타임아웃 내에 응답하지 못했습니다.")
        print(f"로그 파일({LOG_FILE}) 내용:")
        if LOG_FILE.exists():
            lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-15:]:
                print(f"  {line}")
        print("========================================================================")
        terminate_pid_safely(proc.pid, port=port)
        PID_FILE.unlink(missing_ok=True)
        return EXIT_HEALTH_TIMEOUT

    print("========================================================================")
    print("  [SUCCESS] SCHALE 전투 추적 시스템이 성공적으로 실행되었습니다!")
    print(f"  - 웹 인터페이스 : {server_url}")
    print(f"  - 프로세스 PID  : {proc.pid}")
    print(f"  - 로그 파일     : {LOG_FILE.relative_to(WORKSPACE_ROOT)}")
    print("  - 서버를 종료하려면 '종료하기.bat'을 더블클릭하세요.")
    print("========================================================================")

    if not args.no_browser:
        print("[*] 기본 웹 브라우저를 엽니다...")
        webbrowser.open(server_url)

    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """Safely terminate only the tracked and verified server process."""
    from src.config import get_config
    config = get_config()
    host = args.host or config.host
    port = args.port or config.port

    print("========================================================================")
    print("  [SCHALE TACTICAL TRACKING] 서버 종료 작업 진행 중...")
    print("========================================================================")

    if not PID_FILE.exists():
        if is_port_in_use(host, port):
            print(f"[경고] 포트 {port}가 현재 사용 중이지만, 추적 중인 서버 PID 파일(.server.pid)이 없습니다.")
            print("[안전 원칙] 다른 프로세스를 임의로 종료하지 않습니다. 해당 프로그램을 직접 확인하십시오.")
            return EXIT_PID_MISMATCH
        print("[알림] 현재 실행 중인 SCHALE 서버가 없습니다.")
        return 0

    try:
        raw_pid = PID_FILE.read_text(encoding="utf-8").strip()
        pid = int(raw_pid)
    except Exception:
        PID_FILE.unlink(missing_ok=True)
        print("[오류] PID 파일이 손상되어 잔여 파일을 정리했습니다.")
        return 0

    if not is_pid_running(pid):
        PID_FILE.unlink(missing_ok=True)
        print(f"[알림] 저장된 PID({pid})의 프로세스가 이미 종료되어 있습니다. 잔여 기록을 정리했습니다.")
        return 0

    # Verify identity before terminating
    if not verify_pid_identity(pid, port=port):
        print(f"[안전 차단] PID {pid}는 SCHALE 서버 프로세스로 검증되지 않았습니다 (PID 재사용 가능성).")
        print("[안전 원칙] 타 프로세스 손상을 방지하기 위해 강제 종료를 거부합니다.")
        return EXIT_PID_MISMATCH

    print(f"[*] 추적된 서버 프로세스(PID: {pid})를 안전하게 종료합니다...")
    terminate_pid_safely(pid, port=port)

    # Wait for process exit verification
    for _ in range(10):
        if not is_pid_running(pid):
            break
        time.sleep(0.3)

    PID_FILE.unlink(missing_ok=True)

    if not is_pid_running(pid):
        print(f"[성공] SCHALE 서버(PID: {pid})가 안전하게 정상 종료되었습니다.")
        return 0
    else:
        print(f"[경고] PID {pid} 프로세스 종료를 시도했으나 여전히 실행 중일 수 있습니다.")
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    """Print operational status of the server."""
    from src.config import get_config
    config = get_config()
    host = args.host or config.host
    port = args.port or config.port
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host

    active_pid = get_active_server_pid(port=port)
    in_use = is_port_in_use(host, port)
    healthy = check_health(host, port) if in_use else False

    capture_info = "UNAVAILABLE"
    try:
        from src.capture import WindowFinder
        finder = WindowFinder(mock_mode=config.mock_inference)
        wins = finder.list_windows()
        capture_info = f"ACTIVE ({len(wins)} capturable windows discovered)"
    except Exception as e:
        capture_info = f"UNAVAILABLE ({e})"

    print("========================================================================")
    print("  SCHALE Tactical Tracking Server Status")
    print("========================================================================")
    print(f"  - Target URL       : http://{connect_host}:{port}")
    print(f"  - Active Server PID: {active_pid if active_pid else 'None (Not Running)'}")
    print(f"  - Port Listening   : {'YES' if in_use else 'NO'}")
    print(f"  - Health Endpoint  : {'ONLINE (200 OK)' if healthy else 'OFFLINE'}")
    print(f"  - Native WGC Capture: {capture_info}")
    print("========================================================================")
    return 0 if (active_pid and healthy) else 1


def cmd_capture(args: argparse.Namespace) -> int:
    """CLI handler for inspecting Native WGC capturable windows."""
    from src.capture import WindowFinder, NativeCaptureEngine
    print("========================================================================")
    print("  SCHALE Native WGC / Desktop Window Capture")
    print("========================================================================")
    finder = WindowFinder()
    if args.list:
        windows = finder.list_windows()
        print(f"Discovered {len(windows)} visible windows:")
        for w in windows:
            print(f"  [{w.hwnd}] {w.title} ({w.process_name}) {w.width}x{w.height}")
        return 0
    target = args.window or 0
    engine = NativeCaptureEngine()
    print(f"[*] Capturing frame for target: {target}")
    img, meta = engine.capture_frame(int(target) if str(target).isdigit() else target)
    print("Capture Result Meta:", meta)
    if img:
        print(f"Successfully captured image: size={img.size}, mode={img.mode}")
        return 0
    else:
        print("[!] Capture returned no image.")
        return 1


def cmd_shortcut(args: argparse.Namespace) -> int:
    """CLI handler for creating desktop shortcut."""
    print("========================================================================")
    print("  SCHALE 전투 추적 시스템 - 바탕화면 바로가기 생성")
    print("========================================================================")
    ok, msg = create_desktop_shortcut()
    if ok:
        print(f"[성공] {msg}")
        return 0
    else:
        print(f"[오류] {msg}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="SCHALE Server Launcher Controller")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_run = subparsers.add_parser("run", help="Run server in foreground console")
    p_run.add_argument("--host", default=None)
    p_run.add_argument("--port", type=int, default=None)
    p_run.add_argument("--no-browser", action="store_true")
    p_run.add_argument("--no-pause", action="store_true", help="Non-interactive execution flag")
    p_run.set_defaults(func=cmd_run)

    p_start = subparsers.add_parser("start", help="Start server in background")
    p_start.add_argument("--host", default=None)
    p_start.add_argument("--port", type=int, default=None)
    p_start.add_argument("--no-browser", action="store_true")
    p_start.set_defaults(func=cmd_start)

    p_stop = subparsers.add_parser("stop", help="Stop running server")
    p_stop.add_argument("--host", default=None)
    p_stop.add_argument("--port", type=int, default=None)
    p_stop.set_defaults(func=cmd_stop)

    p_status = subparsers.add_parser("status", help="Check server status")
    p_status.add_argument("--host", default=None)
    p_status.add_argument("--port", type=int, default=None)
    p_status.set_defaults(func=cmd_status)

    p_cap = subparsers.add_parser("capture", help="Enumerate or capture game windows")
    p_cap.add_argument("--list", action="store_true", help="List capturable windows")
    p_cap.add_argument("--window", default=0, help="Target HWND or window name")
    p_cap.set_defaults(func=cmd_capture)

    p_sc = subparsers.add_parser("shortcut", help="Create desktop shortcut")
    p_sc.set_defaults(func=cmd_shortcut)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
