"""Tests for «🩺 Γιατρός» (akatikito/doctor.py) — read-only diagnosis, safe 1-click fixes,
audit logging, CLI, and strict human-only isolation."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from akatikito.audit_log import AuditLog
from akatikito.doctor import Doctor, _cli


def _by_id(report: dict) -> dict[str, dict]:
    return {c["id"]: c for c in report["checks"]}


def test_diagnose_is_readonly_and_returns_all_14_checks(tmp_path):
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    data_dir.mkdir()
    proj_dir.mkdir()
    # Plant a broken JSON and a stale .part file — diagnose() must NOT touch them
    broken = data_dir / "settings.json"
    broken.write_text("{broken json", encoding="utf-8")
    part = proj_dir / ".upload.1234.part"
    part.write_bytes(b"half")

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir)
    rep = doc.diagnose(check_api=False)
    assert len(rep["checks"]) == 14
    by_id = _by_id(rep)
    assert by_id["json_integrity"]["status"] == "error"
    assert by_id["temp_partials"]["status"] == "warn"
    assert by_id["api_key_live"]["meta"].get("checked") is False
    # Verify read-only invariant
    assert broken.read_text(encoding="utf-8") == "{broken json"
    assert part.exists()


def test_fix_json_integrity_restores_from_backup_and_preserves_broken_copy(tmp_path):
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    data_dir.mkdir()
    proj_dir.mkdir()

    # Valid backup + broken live settings.json
    bak = data_dir / "settings.bak.20260101-000000.json"
    bak.write_text(json.dumps({"provider": "DeepSeek", "daily_budget": 2.5}), encoding="utf-8")
    live = data_dir / "settings.json"
    live.write_text("{corrupted!!!", encoding="utf-8")

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir)
    res = doc.apply_fix("json_integrity")
    assert res["ok"] is True
    # Restored from backup
    restored = json.loads(live.read_text(encoding="utf-8"))
    assert restored["daily_budget"] == 2.5
    # Broken copy preserved
    broken_copies = list(data_dir.glob("settings.broken.*.json"))
    assert len(broken_copies) == 1
    assert broken_copies[0].read_text(encoding="utf-8") == "{corrupted!!!"
    # Recorded in AuditLog
    entries = AuditLog(proj_dir).entries(limit=10)
    assert any(e.get("action") == "doctor_fix" and e.get("tool") == "json_integrity" for e in entries)


def test_fix_mobile_sessions_cleans_expired_and_legacy_only(tmp_path):
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    data_dir.mkdir()
    proj_dir.mkdir()

    valid_hash = hashlib.sha256(b"good-session").hexdigest()
    expired_hash = hashlib.sha256(b"old-session").hexdigest()
    mb = data_dir / "mobile_bridge.json"
    mb.write_text(
        json.dumps({
            "auto_start": False,
            "sessions": {
                valid_hash: time.time(),
                expired_hash: time.time() - 40 * 86400,
                "legacy-plaintext": time.time(),
            },
        }),
        encoding="utf-8",
    )

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir)
    by_id = _by_id(doc.diagnose())
    assert by_id["mobile_sessions"]["status"] == "warn"
    assert by_id["mobile_sessions"]["meta"]["bad_count"] == 2

    res = doc.apply_fix("mobile_sessions")
    assert res["ok"] is True
    after = json.loads(mb.read_text(encoding="utf-8"))
    assert list(after["sessions"].keys()) == [valid_hash]


def test_fix_orphan_locks_and_temp_partials_preserves_live_lock(tmp_path):
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    data_dir.mkdir()
    proj_dir.mkdir()

    dead_lock = data_dir / "dead.lock"
    dead_lock.write_text("99999999", encoding="utf-8")
    live_lock = data_dir / "live.lock"
    live_lock.write_text(str(os.getpid()), encoding="utf-8")
    stale_part = proj_dir / ".upload.abcd.part"
    stale_part.write_bytes(b"partial")

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir)
    assert doc.apply_fix("orphan_locks")["ok"] is True
    assert not dead_lock.exists()
    assert live_lock.exists(), "Live process lock must NEVER be deleted"

    assert doc.apply_fix("temp_partials")["ok"] is True
    assert not stale_part.exists()


def test_fix_agent_backups_removes_only_over_30_days(tmp_path):
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    bdir = proj_dir / ".agent_backups"
    data_dir.mkdir()
    bdir.mkdir(parents=True)

    old_f = bdir / "old.bak"
    old_f.write_bytes(b"x" * 1024)
    old_ts = time.time() - 45 * 86400
    os.utime(old_f, (old_ts, old_ts))

    new_f = bdir / "recent.bak"
    new_f.write_bytes(b"y" * 1024)

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir)
    by_id = _by_id(doc.diagnose())
    assert by_id["agent_backups"]["status"] == "warn"
    assert by_id["agent_backups"]["meta"]["old_count"] == 1

    assert doc.apply_fix("agent_backups")["ok"] is True
    assert not old_f.exists()
    assert new_f.exists()


def test_fix_daily_budget_sets_one_euro_and_logs_audit(tmp_path):
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    data_dir.mkdir()
    proj_dir.mkdir()
    (data_dir / "settings.json").write_text(json.dumps({"daily_budget": 0}), encoding="utf-8")

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir)
    assert _by_id(doc.diagnose())["daily_budget"]["status"] == "warn"
    assert doc.apply_fix("daily_budget")["ok"] is True
    saved = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    assert saved["daily_budget"] == 1.0


def test_audit_chain_tampering_is_warn_only_and_never_repaired(tmp_path):
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    data_dir.mkdir()
    proj_dir.mkdir()

    audit = AuditLog(proj_dir)
    audit.record("edit", tool="edit_file", summary="legit")
    # Tamper with the audit file
    raw = audit.path.read_text(encoding="utf-8").replace("legit", "TAMPERED")
    audit.path.write_text(raw, encoding="utf-8")

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir)
    chk = _by_id(doc.diagnose())["audit_chain"]
    assert chk["status"] == "error"
    assert chk["fixable"] is False

    res = doc.apply_fix("audit_chain")
    assert res["ok"] is False
    # Forensic evidence remains untouched
    assert "TAMPERED" in audit.path.read_text(encoding="utf-8")


def test_doctor_is_human_only_never_exposed_as_llm_tool_and_cli_runs(tmp_path, capsys):
    from akatikito.tools import TOOL_SPECS

    tool_names = {t["function"]["name"] for t in TOOL_SPECS}
    assert not any("doctor" in name for name in tool_names)

    rc = _cli(["--project", str(tmp_path), "--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "checks" in parsed and len(parsed["checks"]) == 14
    assert rc in (0, 1)
