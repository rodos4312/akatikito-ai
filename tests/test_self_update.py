"""Failure-path tests for candidate validation and promotion; never update the installed app."""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from akatikito.self_update import SelfUpdate
from akatikito.tools import dispatch
from akatikito.workspace import Workspace


def fixture():
    root = Path(tempfile.mkdtemp(prefix="akatikito-update-test-"))
    (root/"akatikito").mkdir(); (root/"tests").mkdir()
    (root/"akatikito/__init__.py").write_text("",encoding="utf-8")
    (root/"app.py").write_text("VALUE = 1\n",encoding="utf-8")
    (root/"tests/test_base.py").write_text("def main():\n    assert True\n",encoding="utf-8")
    update = SelfUpdate(root)
    (update.stage/"app.py").write_text("VALUE = 2\n",encoding="utf-8")
    return root, update


def expect_error(fn):
    try: fn()
    except (ValueError, OSError): return
    raise AssertionError("Expected rejection")


def main():
    root, update = fixture()
    expect_error(update.apply)
    assert (root/"app.py").read_text() == "VALUE = 1\n"
    with patch("akatikito.self_update.run_checks",return_value=(False,"test failed")):
        assert not update.validate()[0]
    expect_error(update.apply)
    print("OK no promotion before checks or after failures")
    root, update = fixture()
    (update.stage/"tests/test_base.py").write_text("pass\n")
    with patch("akatikito.self_update.run_checks",return_value=(True,"ok")) as checks:
        assert not update.validate()[0] and not checks.called
    print("OK baseline tests cannot be weakened")
    root, update = fixture()
    with patch("akatikito.self_update.run_checks",return_value=(True,"checks passed")):
        assert update.validate()[0]
    (update.stage/"app.py").write_text("VALUE = 3\n")
    expect_error(update.apply)
    print("OK candidate edits invalidate previous checks")
    root, update = fixture()
    with patch("akatikito.self_update.run_checks",return_value=(True,"ok")):
        assert update.validate()[0]
    (root/"app.py").write_text("VALUE = 9\n")
    expect_error(update.apply)
    assert (root/"app.py").read_text() == "VALUE = 9\n"
    print("OK concurrent source changes block promotion")
    root, update = fixture()
    def mutating_check(*args):
        (update.stage/"app.py").write_text("VALUE = 4\n")
        return True,"ok"
    with patch("akatikito.self_update.run_checks",side_effect=mutating_check):
        assert not update.validate()[0]
    print("OK edits during tests invalidate result")
    root, update = fixture()
    with patch("akatikito.self_update.run_checks",return_value=(True,"ok")):
        assert update.validate()[0]
    result = update.apply()
    assert result["ok"] and (root/"app.py").read_text() == "VALUE = 2\n"
    assert (Path(result["backup"])/"app.py").read_text() == "VALUE = 1\n"
    print("OK successful promotion keeps original backup")
    root, update = fixture()
    (update.stage/"akatikito/new.py").write_text("VALUE = 8\n")
    with patch("akatikito.self_update.run_checks",return_value=(True,"ok")):
        assert update.validate()[0]
    import os
    real_replace = os.replace
    calls = 0
    def fail_second(src,dest):
        nonlocal calls
        calls += 1
        if calls == 2: raise OSError("simulated disk failure")
        return real_replace(src,dest)
    with patch("akatikito.self_update.os.replace",side_effect=fail_second):
        expect_error(update.apply)
    assert (root/"app.py").read_text() == "VALUE = 1\n"
    assert not (root/"akatikito/new.py").exists()
    print("OK partial write failure rolls back original files")
    root, update = fixture()
    ws = Workspace(update.stage)
    ws.update_validator = lambda:(False,"blocked check")
    assert dispatch(ws,"run_command",{"command":"echo unsafe"}).startswith("ΑΠΟΡΡΙΨΗ")
    assert dispatch(ws,"run_command",{"command":"verify"}).startswith("returncode=1")
    live = Workspace(Path(__file__).resolve().parents[1])
    assert dispatch(live,"write_file",{"path":"never-created.tmp","content":"x"}).startswith("ΑΠΟΡΡΙΨΗ")
    print("OK candidate commands restricted and live tool writes blocked")
    print("SELF UPDATE SAFETY OK")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
