# PROMPTS.md

## Standard Execution Prompts & Workflows
- **Strict Verification Gate**:
  `python scripts/verify_workspace.py --strict --json`
- **Full Test Suite Execution**:
  `pytest tests/ -q --tb=short`
- **Standalone PyInstaller Build**:
  `python -m PyInstaller --noconfirm myoi.spec`
- **Subagent Delegation Command**:
  `python C:\Users\ROCmAdmin\tes\scripts\codex_subagent.py --worker-id 1 --prompt "<PROMPT>"`
