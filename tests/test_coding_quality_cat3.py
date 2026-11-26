"""Category 3 tests: HTML inline <script> syntax guard & explainable file relevance ranking."""
from __future__ import annotations

import shutil
import pytest

from akatikito.editing import EditError, check_syntax
from akatikito.relevance import rank_project_files
from akatikito.tools import dispatch, edit_file
from akatikito.workspace import Workspace


def test_html_syntax_check_blocks_unbalanced_scripts_and_broken_inline_js(tmp_path):
    html_file = tmp_path / "game.html"
    valid_html = (
        "<!doctype html><html><body>"
        "<script type='application/json'>{\"a\": 1}</script>"
        "<script src='external.js'></script>"
        "<script>function buildCity(n) { return n * 2; }</script>"
        "</body></html>"
    )
    html_file.write_text(valid_html, encoding="utf-8")
    # Valid HTML passes cleanly.
    check_syntax(html_file, valid_html)

    # Unbalanced <script> tag is rejected BEFORE write.
    unbalanced = "<html><body><script>const x = 1;</body></html>"
    with pytest.raises(EditError, match="Μη ισορροπημένα"):
        check_syntax(html_file, unbalanced)

    # Broken inline JS inside <script> is rejected BEFORE writing via edit_file!
    if shutil.which("node"):
        ws = Workspace(tmp_path)
        res = edit_file(
            ws,
            path="game.html",
            edits=[{"search": "function buildCity(n) { return n * 2; }", "replace": "function buildCity(n) { return (n * 2; }"}],
        )
        assert res.startswith("ΣΦΑΛΜΑ:")
        assert "SyntaxError" in res
        # Original file stayed completely untouched on disk!
        assert html_file.read_text(encoding="utf-8") == valid_html


def test_find_relevant_files_ranks_explicit_symbols_imports_and_tests(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "billing.py").write_text(
        "from src.tax_rules import apply_vat\n\ndef compute_invoice(amount):\n    return apply_vat(amount)\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "tax_rules.py").write_text(
        "def apply_vat(amount, rate=0.24):\n    return round(amount * (1 + rate), 2)\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_billing.py").write_text(
        "from src.billing import compute_invoice\n\ndef test_inv():\n    assert compute_invoice(100) == 124\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Unrelated docs\nJust some notes.\n", encoding="utf-8")

    ws = Workspace(tmp_path)
    ranked = rank_project_files(ws, "Fix bug in compute_invoice inside billing.py")
    assert ranked
    paths = [r.path for r in ranked]
    assert paths[0] == "src/billing.py"
    assert "explicit_file" in ranked[0].reasons
    assert any("exact_symbol(compute_invoice)" in r for r in ranked[0].reasons)
    # Related test and imported dependency are automatically boosted into the results!
    assert "tests/test_billing.py" in paths
    assert "src/tax_rules.py" in paths

    # End-to-end tool dispatch returns readable ranked list with scores & reasons.
    out = dispatch(ws, "find_relevant_files", {"query": "compute_invoice billing.py", "max_results": 5})
    assert "src/billing.py" in out and "score=" in out
