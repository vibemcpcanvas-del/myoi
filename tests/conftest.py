r"""c:/mey/tests/conftest.py
Mechanical enforcement hook for pytest execution in workspace.
Strictly blocks naked/un-targeted pytest runs to conserve tokens and prevent legacy test pollution.
Enforces REQ-QG-03.
"""
import sys
from pathlib import Path
import pytest

def pytest_cmdline_main(config):
    """Enforce targeted test execution invariant REQ-QG-03.
    Rejects bare 'pytest' runs, option-only runs (-k, -m, -v), and broad directory scans ('pytest tests', 'pytest .').
    Requires at least one explicit Python test file target (ending with '.py' or '.py::*').
    """
    args = config.invocation_params.args

    has_concrete_test_file = False
    for arg in args:
        if arg.startswith("-"):
            continue
        file_part = arg.split("::")[0]
        if file_part.endswith(".py"):
            has_concrete_test_file = True
            break

    if not has_concrete_test_file:
        raise pytest.UsageError(
            "[BLOCKED by REQ-QG-03 Invariant] Naked or broad-directory 'pytest' execution is strictly prohibited!\n"
            "You MUST specify an explicit target test file (e.g., 'pytest tests/test_governance_invariants.py').\n"
            "Broad directory scans ('pytest', 'pytest .', 'pytest tests/') or flag-only calls are blocked to prevent token waste."
        )
