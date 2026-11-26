"""Characterization tests for tool_plugins.py and tool_plugins_media.py (Q4 batch 1)."""
from __future__ import annotations

import hashlib
import json
import zipfile

import pytest

from akatikito.tool_plugins import _safe_eval_decimal, tool_convert_data_file
from akatikito.tool_plugins_media import tool_pdf_toolkit, tool_zip_manager
from akatikito.workspace import Workspace


def test_safe_eval_decimal_ops_functions_and_guards():
    assert str(_safe_eval_decimal("2 + 3 * 4 - 5 / 2")) == "11.5"
    assert _safe_eval_decimal("17 // 5") == 3
    assert _safe_eval_decimal("17 % 5") == 2
    assert _safe_eval_decimal("2 ** 10") == 1024
    assert _safe_eval_decimal("pow(3, 4)") == 81
    assert _safe_eval_decimal("sum([1, 2, 3, 4])") == 10
    assert _safe_eval_decimal("min(9, 2, 7)") == 2
    assert _safe_eval_decimal("max([1, 8, 3])") == 8
    assert _safe_eval_decimal("round(3.14159, 2)").compare(_safe_eval_decimal("3.14")) == 0
    assert _safe_eval_decimal("abs(-42)") == 42
    assert _safe_eval_decimal("sqrt(144)") == 12
    assert _safe_eval_decimal("ceil(2.1)") == 3
    assert _safe_eval_decimal("floor(2.9)") == 2
    assert _safe_eval_decimal("log10(1000)") == 3
    with pytest.raises(ValueError, match="εκθέτης"):
        _safe_eval_decimal("2 ** 2000")
    with pytest.raises(ValueError):
        _safe_eval_decimal("__import__('os').system('echo hi')")


def test_convert_data_file_csv_json_markdown_and_sha_guard(tmp_path):
    ws = Workspace(tmp_path)
    (tmp_path / "items.csv").write_text("name,price\nAlpha,10\nBeta,20\n", encoding="utf-8")

    md = tool_convert_data_file(ws, "items.csv", target_format="markdown")
    assert "| name | price |" in md and "| Alpha | 10 |" in md

    out_json = tool_convert_data_file(ws, "items.csv", target_format="json", output_path="items.json")
    assert out_json.startswith("ΕΓΙΝΕ:")
    loaded = json.loads((tmp_path / "items.json").read_text(encoding="utf-8"))
    assert len(loaded) == 2 and loaded[0]["name"] == "Alpha"

    # Expected SHA mismatch rejects overwrite
    with pytest.raises(ValueError, match="SHA256"):
        tool_convert_data_file(ws, "items.csv", target_format="json", output_path="items.json", expected_sha256="0" * 64)

    # Matching SHA succeeds
    good_sha = hashlib.sha256((tmp_path / "items.json").read_bytes()).hexdigest()
    ok2 = tool_convert_data_file(ws, "items.csv", target_format="json", output_path="items.json", expected_sha256=good_sha)
    assert ok2.startswith("ΕΓΙΝΕ:")

    # JSON back to CSV
    csv_out = tool_convert_data_file(ws, "items.json", target_format="csv", output_path="roundtrip.csv")
    assert csv_out.startswith("ΕΓΙΝΕ:") and "Alpha,10" in (tmp_path / "roundtrip.csv").read_text(encoding="utf-8")


def test_zip_manager_create_list_extract_and_zipslip(tmp_path):
    ws = Workspace(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.txt").write_text("hello zip", encoding="utf-8")
    (tmp_path / "docs" / ".env").write_text("SECRET=1", encoding="utf-8")

    created = tool_zip_manager(ws, action="create", archive_path="bundle.zip", paths=["docs"])
    assert created.startswith("ΕΓΙΝΕ:") and "1 αρχεία" in created

    listed = tool_zip_manager(ws, action="list", archive_path="bundle.zip")
    assert "docs/a.txt" in listed and ".env" not in listed

    extracted = tool_zip_manager(ws, action="extract", archive_path="bundle.zip", destination="out")
    assert extracted.startswith("ΕΓΙΝΕ:")
    assert (tmp_path / "out" / "docs" / "a.txt").read_text(encoding="utf-8") == "hello zip"

    # Zip-Slip traversal must be blocked
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escape.txt", "pwned")
    with pytest.raises((PermissionError, ValueError)):
        tool_zip_manager(ws, action="extract", archive_path="evil.zip", destination="out")


def test_pdf_toolkit_info_merge_and_split(tmp_path):
    from pypdf import PdfWriter

    ws = Workspace(tmp_path)
    for fname in ("p1.pdf", "p2.pdf"):
        w = PdfWriter()
        w.add_blank_page(width=200, height=200)
        with (tmp_path / fname).open("wb") as fh:
            w.write(fh)

    info = tool_pdf_toolkit(ws, action="info", paths=["p1.pdf"])
    assert "1 σελίδες" in info

    merged = tool_pdf_toolkit(ws, action="merge", paths=["p1.pdf", "p2.pdf"], output_path="both.pdf")
    assert merged.startswith("ΕΓΙΝΕ:") and "2 σελίδες" in merged

    split = tool_pdf_toolkit(ws, action="split", paths=["both.pdf"], output_path="only2.pdf", pages="2")
    assert split.startswith("ΕΓΙΝΕ:") and "1 σελίδες" in split
