"""Tests for unified scripts/check.py runner (QUALITY-PLAN-2 Q1)."""
from __future__ import annotations

import pytest

from scripts import check


def test_select_steps_modes_and_validation():
    full = [s.name for s in check.select_steps(None, fast=False, staged=False)]
    fast = [s.name for s in check.select_steps(None, fast=True, staged=False)]
    staged = [s.name for s in check.select_steps(None, fast=False, staged=True)]
    assert "pytest" in full
    assert "pytest" not in fast and fast == staged
    assert [s.name for s in check.select_steps("ruff,health", fast=False, staged=False)] == ["ruff", "health"]
    with pytest.raises(ValueError, match="Unknown step"):
        check.select_steps("nonexistent_step", fast=False, staged=False)


def test_list_and_only_size_health_and_empty_staged(capsys, monkeypatch):
    assert check.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "ruff" in out and "pytest" in out

    assert check.main(["--only", "nonexistent"]) == 2
    assert check.main(["--only", "size,health"]) == 0

    # Empty staged list should skip ruff/js/node cleanly and still pass
    monkeypatch.setattr(check, "staged_files", lambda root=check.ROOT: [])
    assert check.main(["--staged"]) == 0
    staged_out = capsys.readouterr().out
    assert "no staged Python files" in staged_out
    assert "no staged UI JS files" in staged_out


def test_step_t_default_check_uses_isolated_fast_tier_and_ci_keeps_full_tier():
    local_ctx = check.RunContext(root=check.ROOT, python="python", staged=None, ci=False)
    local_cmd = check._build_pytest_cmd(local_ctx)
    assert "scripts/run_tests_isolated.py" in local_cmd
    assert "--tier" in local_cmd and "fast" in local_cmd

    ci_ctx = check.RunContext(root=check.ROOT, python="python", staged=None, ci=True)
    ci_cmd = check._build_pytest_cmd(ci_ctx)
    assert "scripts/run_tests_isolated.py" in ci_cmd
    assert "--tier" in ci_cmd and "full" in ci_cmd
    assert "--ci" in ci_cmd

    full_local_ctx = check.RunContext(root=check.ROOT, python="python", staged=None, ci=False, include_slow=True)
    full_local_cmd = check._build_pytest_cmd(full_local_ctx)
    assert "--tier" in full_local_cmd and "full" in full_local_cmd

    pytest_ini = (check.ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "slow:" in pytest_ini
