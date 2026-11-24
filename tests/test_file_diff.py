"""Tests for Visual Diff (akatikito/file_diff.py & AppState.file_diff)."""
from __future__ import annotations

import pytest


def test_compute_file_diff_against_latest_backup_and_new_file(tmp_path):
    from akatikito.file_diff import compute_file_diff
    from akatikito.workspace import Workspace

    ws = Workspace(str(tmp_path))
    f = tmp_path / "hello.py"
    f.write_text("a = 1\nb = 2\n", encoding="utf-8")

    # No backup yet -> treated as new file (all '+' lines)
    d_new = compute_file_diff(tmp_path, "hello.py")
    assert d_new["ok"] is True
    assert d_new["is_new"] is True
    assert d_new["added"] == 2
    assert d_new["removed"] == 0

    # Create backup then edit -> unified diff shows -b = 2 and +b = 3
    bak_id = ws.backup(f)
    f.write_text("a = 1\nb = 3\nc = 4\n", encoding="utf-8")
    d_edit = compute_file_diff(tmp_path, "hello.py")
    assert d_edit["ok"] is True
    assert d_edit["is_new"] is False
    assert d_edit["backup_id"] == bak_id
    assert d_edit["added"] == 2
    assert d_edit["removed"] == 1
    assert any(row["kind"] == "del" and "-b = 2" in row["text"] for row in d_edit["lines"])
    assert any(row["kind"] == "add" and "+b = 3" in row["text"] for row in d_edit["lines"])


def test_compute_file_diff_rejects_traversal_and_binary(tmp_path):
    from akatikito.file_diff import compute_file_diff

    with pytest.raises(PermissionError):
        compute_file_diff(tmp_path, "../../etc/passwd")

    bin_f = tmp_path / "blob.bin"
    bin_f.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    with pytest.raises(ValueError):
        compute_file_diff(tmp_path, "blob.bin")
