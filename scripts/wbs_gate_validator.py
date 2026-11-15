r"""scripts/wbs_gate_validator.py
Mechanical enforcement validator for WBS Sequential Gate Invariant (REQ-QG-02)
and Binary Verdict Gate Invariant (REQ-PG-02).
"""

from pathlib import Path
import re
import sys

WBS_FILE = Path("WBS.md")

MANDATORY_FIELDS = [
    "목표 및 범위",
    "선행 의존성",
    "담당자",
    "산출물",
    "인수 기준",
    "감사 판정",
    "롤백 전략"
]

VALID_VERDICTS = {"[PASS]", "[REJECT]", "[AUDIT_PENDING]", "[PLANNED]", "[IN_PROGRESS]"}

def validate_wbs_ledger():
    if not WBS_FILE.exists():
        raise FileNotFoundError(f"WBS file not found: {WBS_FILE}")

    content = WBS_FILE.read_text(encoding="utf-8")
    sections = re.split(r"###\s+\[(WBS-\d+)\]", content)

    if len(sections) < 3:
        raise ValueError("Invalid WBS.md structure: No WBS packages found.")

    packages = {}
    for i in range(1, len(sections), 2):
        wbs_id = sections[i]
        body = sections[i + 1]

        # 1. Structural Markdown Field Validation
        for field in MANDATORY_FIELDS:
            pattern = rf"\*\s+\*\*{re.escape(field)}(?:\s*\([^)]+\))?\*\*:"
            if not re.search(pattern, body):
                raise ValueError(
                    f"[WBS CONTRACT VIOLATION] Package {wbs_id} is missing mandatory contract field: '* **{field}**:'"
                )

        # 2. Extract Prerequisite and Verdict
        prereq_match = re.search(r"\*\s+\*\*선행 의존성(?:\s*\([^)]+\))?\*\*:\s*([^\n\r]+)", body)
        verdict_match = re.search(r"\*\s+\*\*감사 판정(?:\s*\([^)]+\))?\*\*:\s*([^\n\r]+)", body)

        prereq_str = prereq_match.group(1).strip() if prereq_match else ""
        verdict_raw = verdict_match.group(1).strip() if verdict_match else ""

        verdict_token_match = re.search(r"(\[[A-Z_]+\])", verdict_raw)
        if not verdict_token_match:
            raise ValueError(
                f"[INVALID VERDICT SYNTAX] Package {wbs_id} has invalid verdict format: '{verdict_raw}'"
            )
        verdict_token = verdict_token_match.group(1)

        # 3. Whitelist Enforcement
        if verdict_token not in VALID_VERDICTS:
            raise PermissionError(
                f"[FORBIDDEN VERDICT] Package {wbs_id} has illegal or compromised verdict '{verdict_token}'. "
                f"Allowed states are: {sorted(list(VALID_VERDICTS))}"
            )

        prereq_ids = re.findall(r"(WBS-\d+)", prereq_str)

        packages[wbs_id] = {
            "prereq_str": prereq_str,
            "prereq_ids": prereq_ids,
            "verdict": verdict_token
        }

    # 4. Multi-Prerequisite Sequential Gate Enforcement Check
    for wbs_id, data in packages.items():
        prereq_ids = data["prereq_ids"]
        verdict = data["verdict"]

        for pid in prereq_ids:
            prereq_pkg = packages.get(pid)
            if not prereq_pkg:
                raise ValueError(f"[DEPENDENCY ERROR] {wbs_id} references nonexistent prerequisite '{pid}'.")

            if prereq_pkg["verdict"] != "[PASS]":
                if verdict in {"[IN_PROGRESS]", "[PASS]"}:
                    raise PermissionError(
                        f"[GATE VIOLATION] Package {wbs_id} cannot have status '{verdict}' because prerequisite "
                        f"'{pid}' is not [PASS] (current status: '{prereq_pkg['verdict']}')."
                    )

    print(f"[GATE PASS] WBS state ledger successfully verified ({len(packages)} packages checked).")
    return packages

if __name__ == "__main__":
    try:
        validate_wbs_ledger()
    except Exception as e:
        print(f"[GATE ERROR] {e}", file=sys.stderr)
        sys.exit(1)
