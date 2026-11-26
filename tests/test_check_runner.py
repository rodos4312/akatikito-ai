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
