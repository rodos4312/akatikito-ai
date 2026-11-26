# ADR 0003 — Downward-Only Quality Ratchets Anchored to `main`

- **Status:** Accepted
- **Context:** Legacy codebases accumulate complexity, broad `except Exception`, and large files over time. Enforcing strict limits globally on day one blocks delivery; storing mutable baselines only in the working branch lets a branch silently relax its own limits.
- **Decision:**
  1. Every quality gate (`scripts/health_check.py`, `scripts/quality_rules.py`, `scripts/coverage_check.py`, `scripts/size_check.py`) compares the current branch's baseline against `git merge-base HEAD main` and rejects any relaxation.
  2. Improvements automatically tighten baselines via `--tighten` so debt monotonically decreases.
  3. Any justified exception requires an explicit inline reason (`# quality: allow <RULE_ID> — <reason>` or `# age-ok: <reason>`).
- **Consequences:** Zero new technical debt can enter `main`, while legacy hotspots are refactored safely one function per commit with characterization tests first.
