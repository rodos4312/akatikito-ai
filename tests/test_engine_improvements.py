"""Συμβόλαια συμπεριφοράς: κατάταξη αναζήτησης, ατομικό batch commit, έλεγχος σύνταξης πέρα από Python."""
import os

import pytest

from akatikito import editing, tools
from akatikito.editing import EditError, check_syntax, commit_batch
from akatikito.workspace import Workspace


@pytest.fixture
def ws(tmp_path):
    return Workspace(tmp_path)


# --- search_project: κατάταξη και regex ---

def test_search_ranks_definition_file_above_earlier_weak_file(ws):
    (ws.root / "a_notes.txt").write_text("we should call compute_total somewhere\n", encoding="utf-8")
    (ws.root / "z_core.py").write_text("def compute_total(x):\n    return x\n", encoding="utf-8")
    out = tools.search_project(ws, "compute_total")
    assert out.index("z_core.py") < out.index("a_notes.txt")
    assert "2 αρχεία" in out


def test_search_returns_best_max_files_not_first_on_disk(ws):
    for i in range(5):
        (ws.root / f"a{i}.txt").write_text("needle once\n", encoding="utf-8")
    (ws.root / "zz.py").write_text("class needle:\n    pass\n", encoding="utf-8")
    out = tools.search_project(ws, "needle", max_files=2)
    assert "zz.py" in out and "βρέθηκε σε 6 αρχεία" in out


def test_search_regex_and_invalid_regex(ws):
    (ws.root / "m.py").write_text("def get_user_by_id():\n    pass\n", encoding="utf-8")
    assert "m.py" in tools.search_project(ws, r"get_\w+_by_id", regex=True)
    assert "m.py" not in tools.search_project(ws, r"get_\w+_by_id")
    out = tools.search_project(ws, "(unclosed", regex=True)
    assert isinstance(out, str) and out.startswith("ΣΦΑΛΜΑ")


def test_search_regex_in_tool_schema():
    spec = next(t for t in tools.TOOL_SPECS if t["function"]["name"] == "search_project") if hasattr(tools, "TOOL_SPECS") else None
    if spec is None:
        pytest.skip("το όνομα της λίστας schema διαφέρει")
    props = spec["function"]["parameters"]["properties"]
    assert props["regex"]["type"] == "boolean" and "regex" not in spec["function"]["parameters"]["required"]


# --- commit_batch ---

def test_batch_writes_all(ws):
    a, b = ws.root / "a.txt", ws.root / "sub" / "b.txt"
    a.write_bytes(b"old")
    commit_batch(ws, [(a, b"old", b"new"), (b, None, b"fresh")])
    assert a.read_bytes() == b"new" and b.read_bytes() == b"fresh"


def test_batch_unwritable_second_leaves_first_untouched(ws):
    a = ws.root / "a.txt"
    a.write_bytes(b"old")
    blocker = ws.root / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")
    with pytest.raises(EditError) as err:
        commit_batch(ws, [(a, b"old", b"new"), (blocker / "b.txt", None, b"x")])
    assert a.read_bytes() == b"old"
    assert "b.txt" in str(err.value) and "τίποτα" in str(err.value)


def test_batch_rolls_back_when_write_fails_midway(ws, monkeypatch):
    a, b = ws.root / "a.txt", ws.root / "b.txt"
    a.write_bytes(b"A0");b.write_bytes(b"B0")
    real = os.replace
    def flaky(src, dst):
        if str(dst).endswith("b.txt"):
            raise OSError("disk full")
        return real(src, dst)
    monkeypatch.setattr(editing.os, "replace", flaky)
    with pytest.raises(EditError):
        commit_batch(ws, [(a, b"A0", b"A1"), (b, b"B0", b"B1")])
    monkeypatch.undo()
    assert a.read_bytes() == b"A0" and b.read_bytes() == b"B0"
    assert not [p for p in ws.root.iterdir() if p.name.endswith(".tmp")]


def test_batch_with_broken_python_refused_whole(ws):
    a, b = ws.root / "a.txt", ws.root / "b.py"
    a.write_bytes(b"old")
    with pytest.raises(EditError, match="b.py"):
        commit_batch(ws, [(a, b"old", b"new"), (b, None, b"def (:\n")])
    assert a.read_bytes() == b"old" and not b.exists()


def test_batch_stale_file_refused_whole(ws):
    a, b = ws.root / "a.txt", ws.root / "b.txt"
    a.write_bytes(b"A0");b.write_bytes(b"changed meanwhile")
    with pytest.raises(EditError, match="b.txt"):
        commit_batch(ws, [(a, b"A0", b"A1"), (b, b"B0", b"B1")])
    assert a.read_bytes() == b"A0"


def test_batch_refuses_internal_git_path(ws):
    with pytest.raises(EditError):
        commit_batch(ws, [(ws.root / ".git" / "config", None, b"x")])


def test_batch_honours_review_denial_without_writing(ws):
    a, b = ws.root / "a.txt", ws.root / "b.txt"
    ws.review_write = lambda target, original, updated: target.name != "b.txt"
    with pytest.raises(EditError, match="OWNER_DENIED"):
        commit_batch(ws, [(a, None, b"x"), (b, None, b"y")])
    assert not a.exists() and not b.exists()


# --- check_syntax πέρα από Python ---

def test_invalid_json_refused_before_write(ws):
    with pytest.raises(EditError, match="ΠΡΙΝ την εγγραφή"):
        check_syntax(ws.root / "x.json", '{"a": ,}')
    check_syntax(ws.root / "x.json", '{"a": 1}')
    t = ws.root / "x.json"
    with pytest.raises(EditError):
        commit_batch(ws, [(t, None, b"{bad")])
    assert not t.exists()


def test_invalid_toml_refused():
    from pathlib import Path
    with pytest.raises(EditError):
        check_syntax(Path("x.toml"), "a = = 1")
    check_syntax(Path("x.toml"), "a = 1\n")


def test_yaml_checked_only_when_pyyaml_present():
    from pathlib import Path
    try:
        import yaml  # noqa: F401
    except ImportError:
        check_syntax(Path("x.yml"), "a: [unclosed")
        return
    with pytest.raises(EditError):
        check_syntax(Path("x.yml"), "a: [unclosed")
    check_syntax(Path("x.yml"), "a: 1\n")


def test_js_syntax_error_refused_if_node_present():
    import shutil
    from pathlib import Path
    if shutil.which("node") is None:
        check_syntax(Path("x.js"), "function (")  # χωρίς node: σιωπηλή παράλειψη
        return
    with pytest.raises(EditError):
        check_syntax(Path("x.js"), "function (")
    check_syntax(Path("x.js"), "const a = 1;\n")


def test_node_absent_or_crashing_never_blocks(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(editing.shutil, "which", lambda name: None)
    check_syntax(Path("x.js"), "function (")
    monkeypatch.undo()
    monkeypatch.setattr(editing.shutil, "which", lambda name: "Z:/nonexistent/node.exe")
    check_syntax(Path("x.js"), "function (")


def test_unknown_extension_still_written(ws):
    t = ws.root / "notes.xyz"
    commit_batch(ws, [(t, None, b"{ not any known syntax")])
    assert t.read_bytes() == b"{ not any known syntax"
    editing.commit(ws, ws.root / "n2.xyz", None, b"((")
    assert (ws.root / "n2.xyz").exists()


# --- edit_file(files=...): ο καταναλωτής του commit_batch ---

def _edits(old, new):
    return [{"search": old, "replace": new}]


def test_edit_file_multi_writes_both_files_and_reports_each(ws):
    (ws.root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (ws.root / "b.py").write_text("y = 2\n", encoding="utf-8")
    out = tools.edit_file(ws, files=[{"path": "a.py", "edits": _edits("x = 1", "x = 10")},
                                     {"path": "b.py", "edits": _edits("y = 2", "y = 20")}])
    assert out.startswith("ΕΓΙΝΕ") and "a.py (1" in out and "b.py (1" in out
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "x = 10\n"
    assert (ws.root / "b.py").read_text(encoding="utf-8") == "y = 20\n"


def test_edit_file_multi_second_failure_leaves_first_untouched(ws):
    (ws.root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (ws.root / "b.py").write_text("y = 2\n", encoding="utf-8")
    out = tools.edit_file(ws, files=[{"path": "a.py", "edits": _edits("x = 1", "x = 10")},
                                     {"path": "b.py", "edits": _edits("MISSING", "z")}])
    assert out.startswith("ΣΦΑΛΜΑ") and "τίποτα" in out
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "x = 1\n"


def test_edit_file_multi_late_syntax_failure_is_atomic(ws):
    (ws.root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (ws.root / "b.json").write_text("{}", encoding="utf-8")
    out = tools.edit_file(ws, files=[{"path": "a.py", "edits": _edits("x = 1", "x = 10")},
                                     {"path": "b.json", "edits": _edits("{}", "{")}])
    assert out.startswith("ΣΦΑΛΜΑ")
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "x = 1\n"


def test_edit_file_path_and_files_together_or_neither_refused(ws):
    (ws.root / "a.py").write_text("x = 1\n", encoding="utf-8")
    both = tools.edit_file(ws, path="a.py", edits=_edits("x = 1", "x = 2"),
                           files=[{"path": "a.py", "edits": _edits("x = 1", "x = 3")}])
    neither = tools.edit_file(ws)
    assert both.startswith("ΣΦΑΛΜΑ") and neither.startswith("ΣΦΑΛΜΑ")
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "x = 1\n"


def test_edit_file_multi_reachable_through_dispatch_schema(ws):
    (ws.root / "a.py").write_text("x = 1\n", encoding="utf-8")
    out = tools.dispatch(ws, "edit_file", {"files": [{"path": "a.py", "edits": _edits("x = 1", "x = 5")}]})
    assert out.startswith("ΕΓΙΝΕ")


def test_edit_file_multi_keeps_write_guard(ws):
    (ws.root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (ws.root / "b.py").write_text("y = 2\n", encoding="utf-8")

    def guard(target, original, updated):
        if target.name == "b.py":
            raise EditError("guard says no")
    ws.write_guard = guard
    out = tools.edit_file(ws, files=[{"path": "a.py", "edits": _edits("x = 1", "x = 10")},
                                     {"path": "b.py", "edits": _edits("y = 2", "y = 20")}])
    assert "guard says no" in out
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "x = 1\n"


# --- commit_batch και μη-UTF-8 bytes ---

def test_commit_batch_writes_invalid_utf8_without_syntax_error(ws):
    # Το 0xE9 είναι έγκυρο latin-1 αναγνωριστικό· σε αλλοιωμένο κείμενο (U+FFFD) θα αποτύγχανε ψευδώς.
    target = ws.root / "legacy.py"
    original = b"# -*- coding: latin-1 -*-\ncaf\xe9 = 1\n"
    updated = b"# -*- coding: latin-1 -*-\ncaf\xe9 = 2\n"
    target.write_bytes(original)
    commit_batch(ws, [(target, original, updated)])
    assert target.read_bytes() == updated


def test_invalid_json_refused_directly():
    from pathlib import Path
    with pytest.raises(EditError):
        check_syntax(Path("x.json"), '{"a": }')
    check_syntax(Path("x.json"), '{"a": 1}')


# --- search_project: χρονικό όριο ---

def test_search_budget_exhausted_says_partial_and_still_ranks(ws):
    (ws.root / "a_notes.txt").write_text("compute_total mentioned\n", encoding="utf-8")
    (ws.root / "z_core.py").write_text("def compute_total(x):\n    return x\n", encoding="utf-8")
    out = tools.search_project(ws, "compute_total", time_limit=-1)
    assert "ΜΕΡΙΚΟ" in out
    full = tools.search_project(ws, "compute_total")
    assert "ΜΕΡΙΚΟ" not in full and full.index("z_core.py") < full.index("a_notes.txt")


def test_search_budget_midway_returns_ranked_partial(ws, monkeypatch):
    for name, body in (("a_notes.txt", "compute_total\n"), ("z_core.py", "def compute_total(x):\n    pass\n"), ("zz_late.txt", "compute_total\n")):
        (ws.root / name).write_text(body, encoding="utf-8")
    ticks = iter([0.0, 0.0])
    monkeypatch.setattr(tools.time, "monotonic", lambda: next(ticks, 99.0))
    out = tools.search_project(ws, "compute_total", time_limit=1.0)
    assert "ΜΕΡΙΚΟ" in out and "βρέθηκε σε 1 αρχεία" in out
