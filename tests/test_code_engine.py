"""Tests for Coding Engine Pro: read_symbol, replace_symbol, and smoke_check."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from akatikito.code_engine import read_symbol, replace_symbol, smoke_check
from akatikito.modes import allowed_tools
from akatikito.tools import dispatch
from akatikito.workspace import Workspace


def test_python_symbol_outline_read_and_atomic_replace(tmp_path: Path):
    src = tmp_path / "calc.py"
    src.write_text(
        "def add(a, b):\n    return a + b\n\n"
        "class Engine:\n    def run(self, x):\n        return x * 2\n",
        encoding="utf-8",
    )
    ws = Workspace(tmp_path)

    outline = read_symbol(ws, "calc.py")
    assert "add (function)" in outline
    assert "Engine (class)" in outline
    assert "Engine.run (function)" in outline

    snippet = read_symbol(ws, "calc.py", symbol="Engine.run", context_lines=0)
    assert "def run(self, x):" in snippet
    assert "return x * 2" in snippet

    # Broken syntax must be rejected BEFORE writing
    bad = replace_symbol(ws, "calc.py", "add", "def add(a, b):\n    return a +")
    assert "ΣΦΑΛΜΑ" in bad
    assert "return a + b" in src.read_text(encoding="utf-8")

    # Valid symbol replacement updates only that function and leaves backup
    ok = replace_symbol(ws, "calc.py", "add", "def add(a, b):\n    return a + b + 1")
    assert ok.startswith("OK:")
    assert "SMOKE CHECK OK" in ok
    updated = src.read_text(encoding="utf-8")
    assert "return a + b + 1" in updated
    assert "class Engine:" in updated
    assert list((tmp_path / ".agent_backups").glob("*"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node required for HTML/JS syntax check")
def test_html_inline_script_symbol_edit_and_smoke_check(tmp_path: Path):
    html = tmp_path / "paroxos_v8.html"
    html.write_text(
        "<!doctype html>\n<html><body><canvas id='c'></canvas>\n<script>\n"
        "function buildCity(seed) {\n"
        "  const note = 'braces } inside { string';\n"
        "  return seed + note.length;\n"
        "}\n"
        "const newGame = () => {\n"
        "  return buildCity(42);\n"
        "};\n"
        "</script></body></html>\n",
        encoding="utf-8",
    )
    ws = Workspace(tmp_path)

    outline = read_symbol(ws, "paroxos_v8.html")
    assert "buildCity (function)" in outline
    assert "newGame (function)" in outline

    # Replace buildCity cleanly
    ok = replace_symbol(
        ws,
        "paroxos_v8.html",
        "buildCity",
        "function buildCity(seed) {\n  return seed * 3;\n}",
    )
    assert ok.startswith("OK:")
    assert "SMOKE CHECK OK" in ok
    assert "return seed * 3;" in html.read_text(encoding="utf-8")

    # Broken JS rejected before write
    bad = replace_symbol(ws, "paroxos_v8.html", "buildCity", "function buildCity( { broken")
    assert "ΣΦΑΛΜΑ" in bad
    assert "return seed * 3;" in html.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node required for HTML/JS smoke check")
def test_html_smoke_check_catches_duplicate_const_and_missing_asset(tmp_path: Path):
    dup = tmp_path / "dup.html"
    dup.write_text(
        "<html><body><script>const MAP = 1;</script><script>const MAP = 2;</script></body></html>",
        encoding="utf-8",
    )
    missing = tmp_path / "asset.html"
    missing.write_text(
        '<html><head><link rel="stylesheet" href="missing_style.css"></head><body></body></html>',
        encoding="utf-8",
    )
    ws = Workspace(tmp_path)
    res_dup = smoke_check(ws, "dup.html")
    assert "SMOKE CHECK FAILED" in res_dup
    assert "MAP" in res_dup

    res_missing = smoke_check(ws, "asset.html")
    assert "SMOKE CHECK FAILED" in res_missing
    assert "missing_style.css" in res_missing


def test_python_smoke_check_with_related_pytest_and_mode_gating(tmp_path: Path):
    (tmp_path / "math_mod.py").write_text("def double(n):\n    return n * 2\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_math_mod.py").write_text(
        "from math_mod import double\ndef test_double():\n    assert double(4) == 8\n",
        encoding="utf-8",
    )
    ws = Workspace(tmp_path)
    out = dispatch(ws, "smoke_check", {"path": "math_mod.py", "run_tests": True})
    assert "SMOKE CHECK OK" in out
    assert "1 passed" in out

    # Mode gating: read_symbol is read-only (allowed in ask); mutating/verify tools only in code/debug
    assert "read_symbol" in allowed_tools("ask")
    assert "replace_symbol" not in allowed_tools("ask")
    assert "replace_symbol" in allowed_tools("code")
    assert "smoke_check" in allowed_tools("code")
    assert "smoke_check" in allowed_tools("debug")
