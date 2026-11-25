"""Tests for Phase 7.4 (size_check ratchet) and Phase 7.5 (sync_core --check)."""
from __future__ import annotations

import json


def test_size_check_blocks_new_oversized_and_growing_grandfathered_file(tmp_path):
    from scripts.size_check import check_sizes, write_baseline

    (tmp_path / "akatikito").mkdir()
    big = tmp_path / "akatikito" / "legacy.py"
    big.write_text("\n".join(f"x = {i}" for i in range(850)) + "\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    write_baseline(tmp_path, baseline, limit=800)
    assert json.loads(baseline.read_text(encoding="utf-8"))["akatikito/legacy.py"] == 850

    # At baseline -> OK
    ok, errs = check_sizes(tmp_path, baseline, limit=800)
    assert ok and not errs

    # Growing legacy.py by 1 line -> REJECTED
    big.write_text("\n".join(f"x = {i}" for i in range(851)) + "\n", encoding="utf-8")
    ok2, errs2 = check_sizes(tmp_path, baseline, limit=800)
    assert not ok2 and any("legacy.py" in e for e in errs2)

    # Shrinking legacy.py below baseline -> OK
    big.write_text("\n".join(f"x = {i}" for i in range(840)) + "\n", encoding="utf-8")
    # Adding a brand-new 805-line file -> REJECTED
    new_big = tmp_path / "akatikito" / "new_module.py"
    new_big.write_text("\n".join(f"y = {i}" for i in range(805)) + "\n", encoding="utf-8")
    ok3, errs3 = check_sizes(tmp_path, baseline, limit=800)
    assert not ok3 and any("new_module.py" in e for e in errs3)


def test_sync_core_check_detects_drift_and_passes_when_synced(tmp_path):
    from scripts.sync_core import check_sync, main, sync_core

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "agent.py").write_text("v = 1\n", encoding="utf-8")
    (dst / "agent.py").write_text("v = 0\n", encoding="utf-8")

    res = check_sync(src, dst)
    assert res["in_sync"] is False
    assert res["modified"] == ["agent.py"]
    assert main(["--src", str(src), "--dst", str(dst), "--check"]) == 1

    sync_core(src, dst)
    res2 = check_sync(src, dst)
    assert res2["in_sync"] is True
    assert main(["--src", str(src), "--dst", str(dst), "--check"]) == 0


def test_repo_has_zero_grandfathered_files_over_800_lines_and_verification_enforces_limit(tmp_path):
    from pathlib import Path
    from akatikito.verification import _size_violations
    from scripts.size_check import BASELINE_PATH, check_sizes

    assert json.loads(BASELINE_PATH.read_text(encoding="utf-8")) == {}
    ok, errs = check_sizes()
    assert ok and not errs

    (tmp_path / "akatikito").mkdir()
    (tmp_path / "akatikito" / "too_big.py").write_text("\n".join(f"x={i}" for i in range(810)) + "\n", encoding="utf-8")
    (tmp_path / "akatikito" / "style.css").write_text("\n".join(f".c{i}{{}}" for i in range(805)) + "\n", encoding="utf-8")
    bad = _size_violations(Path(tmp_path))
    assert any("too_big.py" in item for item in bad)
    assert any("style.css" in item for item in bad)
