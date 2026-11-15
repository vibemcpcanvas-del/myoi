r"""tests/test_governance_invariants.py
Automated mechanical audit tests for governance artifacts in workspace.
"""

from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = REPO_ROOT / ".agents" / "rules"

def test_required_files_exist():
    required_files = [
        RULES_DIR / "01-wbs-quota-governance.md",
        RULES_DIR / "02-preflight-audit-gate.md",
        RULES_DIR / "03-anti-fabrication-visual-integrity.md",
        RULES_DIR / "04-policy-traceability-matrix.md",
        REPO_ROOT / ".agents" / "hooks.json",
        REPO_ROOT / ".gitignore",
        REPO_ROOT / "PROJECT.md",
        REPO_ROOT / "WBS.md",
        REPO_ROOT / "pyproject.toml",
    ]
    for rf in required_files:
        assert rf.exists(), f"Missing required governance file: {rf}"

def test_quota_governance_invariants():
    content = (RULES_DIR / "01-wbs-quota-governance.md").read_text(encoding="utf-8")
    assert "5분 이하의 고빈도 자동 크론" in content
    assert "캐시 토큰 비대화" in content
    assert "무인자 `pytest` 명령을 금지" in content or "무인자 `pytest`" in content
    for field in ["WBS-ID", "목표 및 범위", "선행 의존성", "담당자", "산출물", "인수 기준", "감사 판정", "롤백 전략"]:
        assert field in content, f"Missing packaging contract field: {field}"

def test_audit_gate_invariants():
    content = (RULES_DIR / "02-preflight-audit-gate.md").read_text(encoding="utf-8")
    assert "자기 승인 금지" in content
    assert "`[통과]` (PASS)" in content or "[통과]" in content
    assert "`[반려]` (REJECT)" in content or "[반려]" in content
    assert "조건부 승인" in content and "무효" in content
    assert "비면제 불변식" in content
    assert "Fail-Closed" in content

def test_anti_fabrication_invariants():
    content = (RULES_DIR / "03-anti-fabrication-visual-integrity.md").read_text(encoding="utf-8")
    assert "눈먼 장인" in content
    assert "Describe-THEN-Label" in content
    assert "CRITICAL" in content
    assert "MAJOR" in content
    assert "MINOR" in content
    assert "모순 자가 검증" in content or "Contradiction Self-Audit" in content

def test_traceability_matrix_completeness():
    content = (RULES_DIR / "04-policy-traceability-matrix.md").read_text(encoding="utf-8")
    required_req_ids = [
        "REQ-QG-01", "REQ-QG-02", "REQ-QG-03", "REQ-QG-04",
        "REQ-PG-01", "REQ-PG-02", "REQ-PG-03", "REQ-PG-04",
        "REQ-AI-01", "REQ-AI-02", "REQ-AI-03", "REQ-AI-04",
        "REQ-DOM-01", "REQ-SEC-01", "REQ-ENV-01",
    ]
    for req_id in required_req_ids:
        assert req_id in content, f"Missing traceability requirement ID: {req_id}"

def test_ssot_separation_and_state_ledger():
    project_content = (REPO_ROOT / "PROJECT.md").read_text(encoding="utf-8")
    wbs_content = (REPO_ROOT / "WBS.md").read_text(encoding="utf-8")
    assert "진실 공급원 분리 원칙" in project_content
    assert "WBS.md" in project_content
    assert "WBS 상태 전이 라이프사이클" in wbs_content
    assert "[PLANNED]" in wbs_content
    assert "[IN_PROGRESS]" in wbs_content
    assert "[AUDIT_PENDING]" in wbs_content
    assert "[PASS]" in wbs_content
    assert "[REJECT]" in wbs_content
