"""Tests for Q5 per-file isolated parallel test runner and flaky detection."""
from __future__ import annotations

import sys

from scripts.run_tests_isolated import run_isolated


def test_isolated_runner_prevents_cross_file_env_pollution_and_reports_failures(tmp_path):
    t1 = tmp_path / "test_one.py"
    t2 = tmp_path / "test_two.py"
    t3 = tmp_path / "test_fail.py"
    t1.write_text("import os\ndef test_set_env():\n    os.environ['LEAK_KEY'] = '1'\n", encoding="utf-8")
    t2.write_text("import os\ndef test_clean_env():\n    assert 'LEAK_KEY' not in os.environ\n", encoding="utf-8")
    t3.write_text("def test_boom():\n    assert 1 == 2\n", encoding="utf-8")

    ok_res = run_isolated([t1, t2], cwd=tmp_path, python=sys.executable, workers=2)
    assert len(ok_res) == 2 and all(r.returncode == 0 for r in ok_res)

    bad_res = run_isolated([t1, t3], cwd=tmp_path, python=sys.executable, workers=2)
    by_name = {r.file: r for r in bad_res}
    assert by_name["test_one.py"].returncode == 0
    assert by_name["test_fail.py"].returncode != 0


def test_isolated_runner_flags_flaky_retry_instead_of_hiding(tmp_path):
    marker = tmp_path / "flaky.flag"
    tf = tmp_path / "test_flaky.py"
    tf.write_text(
        "from pathlib import Path\n"
        f"FLAG = Path({str(marker)!r})\n"
        "def test_once_red_then_green():\n"
        "    if not FLAG.exists():\n"
        "        FLAG.write_text('1', encoding='utf-8')\n"
        "        assert False\n"
        "    assert True\n",
        encoding="utf-8",
    )
    res = run_isolated([tf], cwd=tmp_path, python=sys.executable, workers=1, retry_flaky=True)
    assert len(res) == 1 and res[0].returncode == 0 and res[0].flaky is True
