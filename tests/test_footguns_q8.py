"""Fault-injection tests proving each of the 5 Q8 footgun checks catches real bugs."""
from __future__ import annotations

from scripts.footgun_check import (
    check_case_collisions,
    check_cli_utf8_guards,
    check_detached_spawns,
    check_hardcoded_paths,
    check_large_blobs,
    run_all,
)


def test_case_collision_fails_on_windows_conflicting_names():
    errs = check_case_collisions(["akatikito/Utils.py", "akatikito/utils.py", "README.md"])
    assert len(errs) == 1 and "[FG-CASE]" in errs[0] and "Fix:" in errs[0]
    assert check_case_collisions(["akatikito/a.py", "akatikito/b.py"]) == []


def test_large_blob_fails_outside_allowlist_and_passes_inside(tmp_path):
    bad = tmp_path / "dump.bin"
    bad.write_bytes(b"x" * 2048)
    ok_asset = tmp_path / "assets" / "logo.bin"
    ok_asset.parent.mkdir()
    ok_asset.write_bytes(b"x" * 2048)

    errs = check_large_blobs(tmp_path, [bad, ok_asset], max_bytes=1024)
    assert len(errs) == 1 and "[FG-BLOB]" in errs[0] and "dump.bin" in errs[0]


def test_hardcoded_path_fails_unless_justified(tmp_path):
    pkg = tmp_path / "akatikito"
    pkg.mkdir()
    bad = pkg / "leak.py"
    bad.write_text('OUT = "/tmp/cache.txt"\nWIN = "C:\\\\Users\\\\Alice\\\\doc.txt"\n', encoding="utf-8")
    errs = check_hardcoded_paths(tmp_path, [bad])
    assert len(errs) == 2 and all("[FG-PATH]" in e for e in errs)

    bad.write_text('OUT = "/tmp/cache.txt"  # quality: allow FG-PATH — synthetic doc example\n', encoding="utf-8")
    assert check_hardcoded_paths(tmp_path, [bad]) == []


def test_detached_popen_fails_when_handle_discarded_and_passes_when_assigned(tmp_path):
    pkg = tmp_path / "akatikito"
    pkg.mkdir()
    mod = pkg / "proc.py"
    mod.write_text("import subprocess\ndef bad():\n    subprocess.Popen(['echo', 'hi'])\n", encoding="utf-8")
    errs = check_detached_spawns(tmp_path, [mod])
    assert len(errs) == 1 and "[FG-DETACHED]" in errs[0]

    mod.write_text("import subprocess\ndef good():\n    proc = subprocess.Popen(['echo', 'hi'])\n    return proc\n", encoding="utf-8")
    assert check_detached_spawns(tmp_path, [mod]) == []


def test_cli_utf8_guard_fails_when_missing_reconfigure(tmp_path):
    cli = tmp_path / "my_cli.py"
    cli.write_text("print('Ελληνικά')\n", encoding="utf-8")
    errs = check_cli_utf8_guards(tmp_path, ("my_cli.py",))
    assert len(errs) == 1 and "[FG-UTF8]" in errs[0]

    cli.write_text("import sys\nsys.stdout.reconfigure(encoding='utf-8', errors='replace')\n", encoding="utf-8")
    assert check_cli_utf8_guards(tmp_path, ("my_cli.py",)) == []


def test_real_repo_passes_all_five_footgun_checks():
    ok, errs = run_all()
    assert ok, f"Unexpected footguns in repo: {errs}"
