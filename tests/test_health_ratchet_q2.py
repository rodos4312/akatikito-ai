"""Tests for Q2 full health ratchet: length <= 300, nesting depth <= 6, JS CC, anti-relaxation & tighten."""
from __future__ import annotations

import json

from scripts.health_check import (
    check_health,
    detect_baseline_relaxation,
    function_depth,
    write_baseline,
)
from scripts.js_metrics import measure_js_source


def test_nesting_depth_counts_nested_blocks_but_not_elif_siblings():
    import ast

    elif_src = (
        "def flat(x):\n"
        "    if x == 1: return 1\n"
        "    elif x == 2: return 2\n"
        "    elif x == 3: return 3\n"
        "    elif x == 4: return 4\n"
        "    elif x == 5: return 5\n"
        "    elif x == 6: return 6\n"
        "    elif x == 7: return 7\n"
        "    else: return 0\n"
    )
    deep_src = (
        "def nested(x):\n"
        "    if x:\n"
        "        for i in x:\n"
        "            while i:\n"
        "                try:\n"
        "                    if i > 1:\n"
        "                        if i > 2:\n"
        "                            if i > 3:\n"
        "                                return i\n"
        "                except ValueError:\n"
        "                    pass\n"
    )
    fn_flat = ast.parse(elif_src).body[0]
    fn_deep = ast.parse(deep_src).body[0]
    assert function_depth(fn_flat) == 1
    assert function_depth(fn_deep) == 7


def test_health_ratchet_enforces_length_depth_js_and_auto_tightens(tmp_path):
    (tmp_path / "akatikito" / "ui").mkdir(parents=True)
    py_file = tmp_path / "akatikito" / "mod.py"
    js_file = tmp_path / "akatikito" / "ui" / "view.js"
    base = tmp_path / "health_baseline.json"

    # Initial clean file + one grandfathered deep function
    py_file.write_text(
        "def ok():\n    return 1\n",
        encoding="utf-8",
    )
    js_file.write_text("function simple(a) { return a ? 1 : 2; }\n", encoding="utf-8")
    write_baseline(tmp_path, base)
    ok, errs = check_health(tmp_path, base, use_git_base=False)
    assert ok and not errs

    # 1. Function > 300 lines -> rejected with actionable Fix hint
    body_lines = "\n".join(f"    x = {i}" for i in range(305))
    py_file.write_text(f"def huge():\n{body_lines}\n    return x\n", encoding="utf-8")
    ok_len, errs_len = check_health(tmp_path, base, use_git_base=False)
    assert not ok_len and any("length" in e and "Fix:" in e for e in errs_len)

    # 2. Nesting depth > 6 -> rejected
    py_file.write_text(
        "def deep(x):\n"
        "    if x:\n"
        "        if x:\n"
        "            if x:\n"
        "                if x:\n"
        "                    if x:\n"
        "                        if x:\n"
        "                            if x:\n"
        "                                return 1\n",
        encoding="utf-8",
    )
    ok_dep, errs_dep = check_health(tmp_path, base, use_git_base=False)
    assert not ok_dep and any("depth" in e and "Fix:" in e for e in errs_dep)

    # 3. UI JS function with CC > 20 -> rejected
    py_file.write_text("def ok():\n    return 1\n", encoding="utf-8")
    js_ifs = "\n".join(f"  if (x === {i}) return {i};" for i in range(22))
    js_file.write_text(f"function complexJs(x) {{\n{js_ifs}\n  return -1;\n}}\n", encoding="utf-8")
    ok_js, errs_js = check_health(tmp_path, base, use_git_base=False)
    assert not ok_js and any("complexJs" in e and "js_complexity" in e for e in errs_js)
    assert measure_js_source(js_file.read_text(encoding="utf-8"))["complexJs"] == 23

    # 4. Auto-tighten lowers baseline when function improves
    write_baseline(tmp_path, base)
    assert json.loads(base.read_text(encoding="utf-8"))["js_complexity"]["akatikito/ui/view.js::complexJs"] == 23
    js_file.write_text("function complexJs(x) { return x ? 1 : 0; }\n", encoding="utf-8")
    write_baseline(tmp_path, base)
    assert "akatikito/ui/view.js::complexJs" not in json.loads(base.read_text(encoding="utf-8"))["js_complexity"]


def test_detect_baseline_relaxation_blocks_raised_or_added_entries():
    ref_main = {
        "complexity": {"akatikito/agent.py::Akatikito._chat": 137},
        "length": {},
        "depth": {},
        "js_complexity": {},
        "long_lines": {},
    }
    raised = {
        **ref_main,
        "complexity": {
            "akatikito/agent.py::Akatikito._chat": 140,
            "akatikito/new.py::sneaky": 25,
        },
    }
    errs = detect_baseline_relaxation(raised, ref_main)
    assert len(errs) == 2
    assert any("137 -> 140" in e for e in errs)
    assert any("sneaky" in e for e in errs)
