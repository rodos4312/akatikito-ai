"""Tests for precision programmer tools: find_references, run_tests, and lint_check."""
from __future__ import annotations

import shutil

from akatikito import modes
from akatikito.dev_tools import find_references, lint_check, run_tests
from akatikito.tools import dispatch
from akatikito.workspace import Workspace


def test_find_references_python_ast_ignores_comments_and_strings_and_scans_js(tmp_path):
    (tmp_path / "calc.py").write_text(
        "# compute_tax is mentioned in a comment\n"
        'DOC = "compute_tax inside a string"\n'
        "def compute_tax(amount):\n"
        "    return amount * 0.24\n",
        encoding="utf-8",
    )
    (tmp_path / "invoice.py").write_text(
        "from calc import compute_tax\n"
        "def total(x):\n"
        "    return x + compute_tax(x)\n",
        encoding="utf-8",
    )
    (tmp_path / "ui.js").write_text(
        "// renderCard ignored in comment\n"
        "function renderCard(item) { return item; }\n"
        "const out = renderCard(42);\n",
        encoding="utf-8",
    )
    ws = Workspace(tmp_path)

    py_res = find_references(ws, "compute_tax")
    assert "[Ορισμοί]" in py_res and "calc.py:3" in py_res
    assert "[Imports]" in py_res and "invoice.py:1" in py_res
    assert "[Χρήσεις]" in py_res and "invoice.py:3" in py_res
    # Must NOT match comment (line 1) or string literal (line 2)!
    assert "calc.py:1" not in py_res
    assert "calc.py:2" not in py_res

    js_res = find_references(ws, "renderCard")
    assert "ui.js:2" in js_res and "ui.js:3" in js_res
    assert "ui.js:1" not in js_res

    # Invalid symbol name rejected cleanly
    assert find_references(ws, "bad; rm -rf").startswith("ΣΦΑΛΜΑ")
    assert "Δεν βρέθηκαν" in find_references(ws, "nonexistent_symbol_xyz")


def test_run_tests_auto_detects_pytest_supports_filters_and_enforces_guard(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_math_demo.py").write_text(
        "def test_add():\n    assert 1 + 1 == 2\n\n"
        "def test_sub():\n    assert 5 - 3 == 2\n",
        encoding="utf-8",
    )
    ws = Workspace(tmp_path)

    # Auto-detects pytest & runs cleanly
    out = run_tests(ws, target="tests/test_math_demo.py", keyword="test_add")
    assert out.startswith("returncode=0") and "1 passed" in out

    # Rejects unsafe keyword injection & path traversal
    assert run_tests(ws, keyword="add; echo pwn").startswith("ΣΦΑΛΜΑ")
    assert "ΑΠΟΡΡΙΨΗ" in dispatch(ws, "run_tests", {"target": "../outside.py"})

    # Enforces owner command_guard denial
    ws.command_guard = lambda cmd, risk: False
    denied = run_tests(ws, target="tests/test_math_demo.py")
    assert "OWNER_DENIED" in denied


def test_lint_check_reports_clean_and_catches_errors(tmp_path):
    good = tmp_path / "good.py"
    good.write_text("def ok(x: int) -> int:\n    return x + 1\n", encoding="utf-8")
    ws = Workspace(tmp_path)

    res_ok = lint_check(ws, "good.py")
    assert res_ok.startswith("✅")

    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n    pass\n", encoding="utf-8")
    res_bad = lint_check(ws, "bad.py")
    assert res_bad.startswith("⚠️")

    if shutil.which("node"):
        bad_js = tmp_path / "bad.js"
        bad_js.write_text("function oops( { return 1; }\n", encoding="utf-8")
        assert lint_check(ws, "bad.js").startswith("⚠️")


def test_dev_tools_mode_permissions_and_slim_ask_preservation():
    assert {"find_references", "lint_check", "run_tests"} <= set(modes.allowed_tools("code"))
    assert {"find_references", "lint_check", "run_tests"} <= set(modes.allowed_tools("debug"))
    assert {"find_references", "lint_check"} <= set(modes.allowed_tools("architect"))
    assert "run_tests" not in modes.allowed_tools("ask")
    assert "run_tests" not in modes.allowed_tools("architect")
    assert "find_references" not in modes.ASK_CORE_TOOLS
    assert "lint_check" not in modes.ASK_CORE_TOOLS
    unlocked = modes.select_ask_tools("Κάνε lint στον κώδικα", set(modes.allowed_tools("ask")), set())
    assert {"find_references", "lint_check"} <= unlocked


def test_new_v26_tools_integrated_with_profiles_mutations_and_dispatch(tmp_path):
    from akatikito.agent import Akatikito
    from akatikito.config import Config
    from akatikito.execution import PARALLEL_READS, mutation_paths
    from akatikito.model import ScriptedModel, tool_call
    from akatikito.project_profiles import ProjectProfiles

    # 1. Project profile enforcement: readonly & office block replace_symbol, run_tests, bg processes
    for tool in ("replace_symbol", "run_tests", "start_background_process", "stop_background_process"):
        assert ProjectProfiles.check_tool("readonly", tool) is not None
        assert ProjectProfiles.check_tool("office", tool) is not None
        assert ProjectProfiles.check_tool("developer", tool) is None

    # 2. mutation_paths recognizes replace_symbol & PARALLEL_READS includes new read tools
    assert mutation_paths("replace_symbol", {"path": "a.py"}, "ΕΓΙΝΕ: αντικατάσταση") == ("a.py",)
    assert {"read_symbol", "find_relevant_files", "find_references", "lint_check", "smoke_check"} <= PARALLEL_READS

    # 3. run_tests counts as verification check (no redundant double-verify!) & read_symbol records repair read
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text("from m import f\n\ndef test_f():\n    assert f() == 2\n", encoding="utf-8")
    model = ScriptedModel([
        {"tool_calls": [tool_call("read_symbol", path="m.py", symbol="f")]},
        {"tool_calls": [tool_call("replace_symbol", path="m.py", symbol="f", new_code="def f():\n    return 2")]},
        {"tool_calls": [tool_call("run_tests", target="tests/test_m.py")]},
        {"content": "Done!"},
    ])
    ag = Akatikito(str(tmp_path), Config(auto_mode=False, verify_command=" "), model)
    ag.set_mode("code")
    ans = ag.chat("Fix f()")
    assert "Done!" in ans
    assert "m.py" in ag._task_writes
    assert ag._task_checks >= 1
    assert ag._verified_generation == ag._mutation_generation
