r"""tests/test_negative_enforcement.py
Negative test suite to prove that governance constraints actually fail closed.
"""

import subprocess
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import scripts.wbs_gate_validator as validator

def test_naked_pytest_is_blocked():
    """Negative Test: Naked pytest invocation MUST exit with non-zero and blocking message."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True
    )
    assert proc.returncode != 0
    assert "[BLOCKED by REQ-QG-03 Invariant]" in proc.stderr or "[BLOCKED by REQ-QG-03 Invariant]" in proc.stdout

def test_broad_directory_scan_is_blocked():
    """Negative Test: 'pytest tests' or 'pytest .' without .py target MUST be blocked."""
    for broad_target in ["tests", "."]:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", broad_target],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True
        )
        assert proc.returncode != 0
        assert "[BLOCKED by REQ-QG-03 Invariant]" in proc.stderr or "[BLOCKED by REQ-QG-03 Invariant]" in proc.stdout

def test_option_only_pytest_is_blocked():
    """Negative Test: 'pytest -k foo' or 'pytest -v' without explicit .py file MUST be blocked."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-k", "foo"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True
    )
    assert proc.returncode != 0
    assert "[BLOCKED by REQ-QG-03 Invariant]" in proc.stderr or "[BLOCKED by REQ-QG-03 Invariant]" in proc.stdout

def test_wbs_gate_blocks_premature_transition(tmp_path):
    invalid_wbs_content = """
### [WBS-00] Core
* **목표 및 범위**: Core
* **선행 의존성**: 없음
* **담당자**: Dev
* **산출물**: None
* **인수 기준**: None
* **감사 판정**: `[AUDIT_PENDING]`
* **롤백 전략**: None

### [WBS-01] Premature
* **목표 및 범위**: Sub
* **선행 의존성**: WBS-00
* **담당자**: Dev
* **산출물**: None
* **인수 기준**: None
* **감사 판정**: `[IN_PROGRESS]`
* **롤백 전략**: None
"""
    test_file = tmp_path / "WBS_INVALID_TRANSITION.md"
    test_file.write_text(invalid_wbs_content, encoding="utf-8")
    orig_file = validator.WBS_FILE
    validator.WBS_FILE = test_file
    try:
        with pytest.raises(PermissionError) as exc_info:
            validator.validate_wbs_ledger()
        assert "[GATE VIOLATION]" in str(exc_info.value)
    finally:
        validator.WBS_FILE = orig_file

def test_wbs_gate_blocks_missing_mandatory_field(tmp_path):
    missing_field_content = """
### [WBS-00] MissingField
* **목표 및 범위**: Core
* **선행 의존성**: 없음
* **담당자**: Dev
* **산출물**: None
* **인수 기준**: None
* **감사 판정**: `[PLANNED]`
"""
    test_file = tmp_path / "WBS_MISSING_FIELD.md"
    test_file.write_text(missing_field_content, encoding="utf-8")
    orig_file = validator.WBS_FILE
    validator.WBS_FILE = test_file
    try:
        with pytest.raises(ValueError) as exc_info:
            validator.validate_wbs_ledger()
        assert "[WBS CONTRACT VIOLATION]" in str(exc_info.value)
    finally:
        validator.WBS_FILE = orig_file

def test_wbs_gate_blocks_forbidden_verdict_states(tmp_path):
    forbidden_verdict_content = """
### [WBS-00] Compromised
* **목표 및 범위**: Core
* **선행 의존성**: 없음
* **담당자**: Dev
* **산출물**: None
* **인수 기준**: None
* **감사 판정**: `[CONDITIONAL_PASS]`
* **롤백 전략**: None
"""
    test_file = tmp_path / "WBS_FORBIDDEN_VERDICT.md"
    test_file.write_text(forbidden_verdict_content, encoding="utf-8")
    orig_file = validator.WBS_FILE
    validator.WBS_FILE = test_file
    try:
        with pytest.raises(PermissionError) as exc_info:
            validator.validate_wbs_ledger()
        assert "[FORBIDDEN VERDICT]" in str(exc_info.value)
    finally:
        validator.WBS_FILE = orig_file

def test_wbs_gate_blocks_unmet_multiple_prerequisites(tmp_path):
    multi_prereq_content = """
### [WBS-00] First
* **목표 및 범위**: First
* **선행 의존성**: 없음
* **담당자**: Dev
* **산출물**: None
* **인수 기준**: None
* **감사 판정**: `[PASS]`
* **롤백 전략**: None

### [WBS-01] Second
* **목표 및 범위**: Second
* **선행 의존성**: 없음
* **담당자**: Dev
* **산출물**: None
* **인수 기준**: None
* **감사 판정**: `[AUDIT_PENDING]`
* **롤백 전략**: None

### [WBS-02] Dependent
* **목표 및 범위**: Third
* **선행 의존성**: WBS-00, WBS-01
* **담당자**: Dev
* **산출물**: None
* **인수 기준**: None
* **감사 판정**: `[IN_PROGRESS]`
* **롤백 전략**: None
"""
    test_file = tmp_path / "WBS_MULTI_PREREQ.md"
    test_file.write_text(multi_prereq_content, encoding="utf-8")
    orig_file = validator.WBS_FILE
    validator.WBS_FILE = test_file
    try:
        with pytest.raises(PermissionError) as exc_info:
            validator.validate_wbs_ledger()
        assert "[GATE VIOLATION]" in str(exc_info.value)
        assert "WBS-01" in str(exc_info.value)
    finally:
        validator.WBS_FILE = orig_file
