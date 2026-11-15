"""run.py
Single entrypoint runner for myoi (SCHALE Tactical Tracking Workspace).
Provides CLI commands for server launch, health checks, and targeted verification.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent


def cmd_status(args):
    """Check system health, environment configuration, and WBS state."""
    from src.config import get_config
    import scripts.wbs_gate_validator as validator

    print("=" * 60)
    print("SCHALE Tactical Tracking System Status Check")
    print("=" * 60)

    config = get_config()
    print(f"Python Executable:    {sys.executable}")
    print(f"Server Host / Port:   {config.host}:{config.port}")
    print(f"Upstream API URL:     {config.inference_api_url}")
    print(f"API Key Configured:   {'YES [PROTECTED]' if config.is_api_key_configured else 'NO [SET INFERENCE_API_KEY]'}")
    print(f"Mock Mode:            {'ENABLED' if config.mock_inference else 'DISABLED'}")
    print(f"Request Timeout:      {config.request_timeout_sec}s")
    print(f"Max Upload Size:      {config.max_upload_size_bytes / (1024 * 1024):.1f} MB")
    print("-" * 60)

    try:
        packages = validator.validate_wbs_ledger()
        print(f"WBS Ledger Status:    VALID ({len(packages)} packages tracked)")
        for pkg_id, info in packages.items():
            print(f"  - [{pkg_id}] status: {info['verdict']}")
    except Exception as e:
        print(f"WBS Ledger Status:    INVALID ({e})")

    print("=" * 60)


def cmd_serve(args):
    """Start the FastAPI application via uvicorn."""
    import uvicorn
    from src.config import get_config

    config = get_config()
    host = args.host or config.host
    port = args.port or config.port
    reload = args.reload

    print(f"[*] Starting SCHALE Tactical Tracking Server on http://{host}:{port}")
    uvicorn.run("src.server:app", host=host, port=port, reload=reload)


def cmd_test(args):
    """Run targeted test suites."""
    target = args.target or "tests/test_governance_invariants.py"
    cmd = [sys.executable, "-m", "pytest", target]
    print(f"[*] Executing targeted test: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(ROOT_DIR))
    sys.exit(result.returncode)


def cmd_validate_wbs(args):
    """Validate WBS state ledger invariants."""
    import scripts.wbs_gate_validator as validator

    try:
        validator.validate_wbs_ledger()
        print("[*] WBS validation succeeded.")
    except Exception as e:
        print(f"[!] WBS validation failed: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="myoi master entrypoint")
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # status
    p_status = subparsers.add_parser("status", help="Show system configuration and WBS state")
    p_status.set_defaults(func=cmd_status)

    # serve
    p_serve = subparsers.add_parser("serve", help="Launch the web application server")
    p_serve.add_argument("--host", default=None, help="Host address to bind")
    p_serve.add_argument("--port", type=int, default=None, help="Port to listen on")
    p_serve.add_argument("--reload", action="store_true", help="Enable auto-reload for dev")
    p_serve.set_defaults(func=cmd_serve)

    # test
    p_test = subparsers.add_parser("test", help="Run targeted test file")
    p_test.add_argument("target", nargs="?", default="tests/test_governance_invariants.py", help="Test file path")
    p_test.set_defaults(func=cmd_test)

    # validate-wbs
    p_val = subparsers.add_parser("validate-wbs", help="Run WBS gate invariant verification")
    p_val.set_defaults(func=cmd_validate_wbs)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
