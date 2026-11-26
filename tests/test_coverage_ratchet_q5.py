"""Tests for Q5 area coverage ratchet and AK-SOURCE-READ-TEST rule."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import coverage_check as cc
import quality_rules as qr


def test_compute_area_coverage_buckets_files_accurately():
    fake_cov = {
        "files": {
            "akatikito/agent.py": {"summary": {"covered_lines": 80, "num_statements": 100}},
            "akatikito/agent_loop.py": {"summary": {"covered_lines": 90, "num_statements": 100}},
            "akatikito/memory_store.py": {"summary": {"covered_lines": 75, "num_statements": 100}},
            "akatikito/tools.py": {"summary": {"covered_lines": 60, "num_statements": 100}},
            "akatikito/app_api.py": {"summary": {"covered_lines": 50, "num_statements": 100}},
        }
    }
    areas = cc.compute_area_coverage(fake_cov, ROOT)
    assert areas["agent"]["percent"] == 85.0
    assert areas["memory"]["percent"] == 75.0
    assert areas["tools"]["percent"] == 60.0
    assert areas["app_api"]["percent"] == 50.0
    assert areas["overall"]["percent"] == 71.0


def test_check_floors_rejects_drop_and_anti_relaxation():
    areas = {"agent": {"percent": 40.0}, "memory": {"percent": 60.0}}
    # Within tolerance passes
    assert cc.check_floors(areas, {"agent": 40.2, "memory": 59.0}, tolerance=0.5) == []
    # Drop beyond tolerance fails
    errs = cc.check_floors(areas, {"agent": 50.0, "memory": 59.0}, tolerance=0.5)
    assert any("agent" in e and "floor" in e for e in errs)
    # Lowering branch baseline below main fails even if actual passes branch baseline
    anti = cc.check_floors(areas, {"agent": 35.0}, main_baseline={"agent": 45.0}, tolerance=0.5)
    assert any("lowered" in e and "main" in e for e in anti)


def test_source_read_test_rule_flags_and_allows_justified(tmp_path):
    bad = tmp_path / "tests" / "test_bad_read.py"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        "import inspect\n"
        "def test_x():\n"
        "    s = inspect.getsource(object)\n"
        "    t = (ROOT / 'akatikito' / 'agent.py').read_text()\n",
        encoding="utf-8",
    )
    findings = qr.scan_all(tmp_path, include_ruff=False)
    sr = [f for f in findings if f.rule_id == "AK-SOURCE-READ-TEST"]
    assert len(sr) == 2

    # Justified inline allow suppresses cleanly
    bad.write_text(
        "import inspect\n"
        "def test_x():\n"
        "    s = inspect.getsource(object)  # quality: allow AK-SOURCE-READ-TEST — meta reflection check\n",
        encoding="utf-8",
    )
    findings2 = qr.scan_all(tmp_path, include_ruff=False)
    assert [f for f in findings2 if f.rule_id == "AK-SOURCE-READ-TEST"] == []


def test_coverage_baseline_file_is_valid_json():
    data = json.loads((ROOT / "scripts" / "coverage_baseline.json").read_text(encoding="utf-8"))
    assert set(data["areas"]) == {"agent", "memory", "tools", "app_api", "overall"}
    assert all(isinstance(v, (int, float)) and v >= 0 for v in data["areas"].values())
