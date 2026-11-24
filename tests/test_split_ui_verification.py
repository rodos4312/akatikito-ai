"""Self-upgrade checks must cover the split UI (akatikito/ui/*.js), not only inline scripts."""
from __future__ import annotations

from pathlib import Path

from akatikito import verification


def _candidate(root: Path, chat_js: str) -> Path:
    (root / "akatikito" / "ui").mkdir(parents=True)
    (root / "tests").mkdir()
    for name in ("app.py", "akatikito/__init__.py"):
        (root / name).write_text("# fixture\n", encoding="utf-8")
    (root / "akatikito/ui.html").write_text('<script src="/ui/chat.js"></script>', encoding="utf-8")
    (root / "akatikito/ui/chat.js").write_text(chat_js, encoding="utf-8")
    (root / "tests/test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    return root


def test_split_ui_without_inline_script_passes(tmp_path):
    root = _candidate(tmp_path, "const ok = true;\n")
    ok, report = verification.run_checks(root, ["tests/test_ok.py"])
    assert ok, report
    assert "UI JavaScript syntax (chat.js): exit=0" in report


def test_syntax_error_in_split_ui_file_rejects_candidate(tmp_path):
    root = _candidate(tmp_path, "function broken( {\n")
    ok, report = verification.run_checks(root, ["tests/test_ok.py"])
    assert not ok
    assert "UI JavaScript syntax (chat.js)" in report
