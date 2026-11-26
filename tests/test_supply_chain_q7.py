"""Tests for Q7 supply-chain checks: Actions SHA pins, requirement bounds, and 7-day release age."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.supply_chain_check import (
    check_release_age,
    check_requirement_bounds,
    check_workflow_sha_pins,
    run_offline_checks,
)


def test_workflow_sha_pin_rejects_floating_tags_and_accepts_40char_sha(tmp_path):
    wf = tmp_path / ".github" / "workflows" / "test.yml"
    wf.parent.mkdir(parents=True)
    wf.write_text(
        "steps:\n"
        "  - uses: actions/checkout@v4\n"
        "  - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065  # v5\n",
        encoding="utf-8",
    )
    errs = check_workflow_sha_pins(tmp_path)
    assert len(errs) == 1 and "[SC-SHA]" in errs[0] and "actions/checkout@v4" in errs[0]


def test_requirement_bounds_and_release_age_policy(tmp_path):
    (tmp_path / "requirements.txt").write_text("httpx>=0.28,<1\nunbounded_pkg\n", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text("ruff==0.16.1\npytest>=9\n", encoding="utf-8")
    (tmp_path / "requirements-features.txt").write_text(
        "freshpkg==1.0.0\n"
        "urgentpkg==2.0.0  # age-ok: critical CVE fix\n"
        "maturepkg==3.0.0\n",
        encoding="utf-8",
    )
    b_errs = check_requirement_bounds(tmp_path)
    assert len(b_errs) == 2 and all("[SC-PIN]" in e for e in b_errs)

    now = datetime(2026, 11, 26, tzinfo=timezone.utc)
    ages = {
        ("freshpkg", "1.0.0"): now - timedelta(days=2),
        ("urgentpkg", "2.0.0"): now - timedelta(days=1),
        ("maturepkg", "3.0.0"): now - timedelta(days=30),
        ("ruff", "0.16.1"): now - timedelta(days=20),
    }
    age_errs = check_release_age(tmp_path, min_days=7, now=now, fetcher=lambda p, v: ages.get((p, v)))
    assert len(age_errs) == 1 and "freshpkg==1.0.0" in age_errs[0]


def test_real_repo_passes_offline_supply_chain_checks():
    ok, errs = run_offline_checks()
    assert ok, f"Supply chain issues: {errs}"
