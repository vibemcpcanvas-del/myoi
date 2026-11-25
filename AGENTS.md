# AGENTS.md

## Agent Role & Governance Map
- **Primary Agent**: `gpt-6.2-astra` (System Architect & Verifier)
- **High-Reasoning Alternate**: `gpt-6.2-sol` (Audit, Fail-Closed Decision Engine)
- **Harness & Verification**: Strictly enforced by `scripts/verify_workspace.py` and pre-commit hooks.

## Invariant Protocols
1. **Zero Fluff & Strict Contrast**: Every status report must present a 1:1 contrast of [Proven] vs [Unproven / Fail-Closed].
2. **Deterministic Reproducibility**: All unit and regression tests must execute without flake.
3. **No Unapproved Side Effects**: Network boundaries, credentials, and local state are strictly guarded.
