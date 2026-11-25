"""Tests for «🩺 Γιατρός» (akatikito/doctor.py) — read-only diagnosis, safe 1-click fixes,
Safe Trash & user-file protection, scheduled.json uncertain jobs, audit logging, and CLI."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from akatikito.audit_log import AuditLog
from akatikito.doctor import Doctor, _cli


def _by_id(report: dict) -> dict[str, dict]:
    return {c["id"]: c for c in report["checks"]}


def test_diagnose_is_readonly_and_returns_all_18_checks(tmp_path):
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    data_dir.mkdir()
    proj_dir.mkdir()
    broken = data_dir / "settings.json"
    broken.write_text("{broken json", encoding="utf-8")
    part = proj_dir / ".upload.a1b2c3d4.part"
    part.write_bytes(b"half")
    old_ts = time.time() - 7200  # 2 hours old
    os.utime(part, (old_ts, old_ts))

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir, lock_dir=tmp_path / "locks")
    rep = doc.diagnose(check_api=False)
    assert len(rep["checks"]) == 18
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

    bak = data_dir / "settings.bak.20260101-000000.json"
    bak.write_text(json.dumps({"provider": "DeepSeek", "daily_budget": 2.5}), encoding="utf-8")
    live = data_dir / "settings.json"
    live.write_text("{corrupted!!!", encoding="utf-8")
    # Also plant a broken data/agenda.json
    (data_dir / "agenda.json").write_text("[broken", encoding="utf-8")

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir, lock_dir=tmp_path / "locks")
    by_id = _by_id(doc.diagnose())
    assert "data/agenda.json" in by_id["json_integrity"]["meta"]["broken"]

    res = doc.apply_fix("json_integrity")
    assert res["ok"] is True
    assert res["audit_logged"] is True
    restored = json.loads(live.read_text(encoding="utf-8"))
    assert restored["daily_budget"] == 2.5
    assert json.loads((data_dir / "agenda.json").read_text(encoding="utf-8")) == []
    broken_copies = list(data_dir.glob("settings.broken.*.json"))
    assert len(broken_copies) == 1
    assert broken_copies[0].read_text(encoding="utf-8") == "{corrupted!!!"
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

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir, lock_dir=tmp_path / "locks")
    by_id = _by_id(doc.diagnose())
    assert by_id["mobile_sessions"]["status"] == "warn"
    assert by_id["mobile_sessions"]["meta"]["bad_count"] == 2

    res = doc.apply_fix("mobile_sessions")
    assert res["ok"] is True
    after = json.loads(mb.read_text(encoding="utf-8"))
    assert list(after["sessions"].keys()) == [valid_hash]


def test_fix_orphan_locks_preserves_live_lock(tmp_path):
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    data_dir.mkdir()
    proj_dir.mkdir()

    dead_lock = data_dir / "dead.lock"
    dead_lock.write_text("99999999", encoding="utf-8")
    live_lock = data_dir / "live.lock"
    live_lock.write_text(str(os.getpid()), encoding="utf-8")

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir, lock_dir=tmp_path / "locks")
    assert doc.apply_fix("orphan_locks")["ok"] is True
    assert not dead_lock.exists()
    assert live_lock.exists(), "Live process lock must NEVER be deleted"


def test_temp_partials_preserves_user_tmp_and_active_upload_part(tmp_path):
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    data_dir.mkdir()
    proj_dir.mkdir()

    old_ts = time.time() - 7200  # 2 hours ago
    # User's own .tmp file (old!) -> MUST SURVIVE!
    user_tmp = proj_dir / "my_report.tmp"
    user_tmp.write_text("user draft", encoding="utf-8")
    os.utime(user_tmp, (old_ts, old_ts))

    # Active in-flight mobile upload (.part < 1 hour old) -> MUST SURVIVE!
    active_part = proj_dir / ".video.deadbeef.part"
    active_part.write_bytes(b"uploading...")

    # Stale mobile upload (> 1 hour old) & stale data/*.json.tmp -> MUST BE CLEANED!
    stale_part = proj_dir / ".old.1234abcd.part"
    stale_part.write_bytes(b"stale")
    os.utime(stale_part, (old_ts, old_ts))
    stale_data_tmp = data_dir / "settings.json.tmp"
    stale_data_tmp.write_text("{}", encoding="utf-8")
    os.utime(stale_data_tmp, (old_ts, old_ts))

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir, lock_dir=tmp_path / "locks")
    assert doc.apply_fix("temp_partials")["ok"] is True
    assert user_tmp.exists(), "User *.tmp file in project must NEVER be deleted"
    assert active_part.exists(), "Active <1h mobile upload .part must NEVER be deleted"
    assert not stale_part.exists()
    assert not stale_data_tmp.exists()


def test_agent_backups_cleanup_preserves_organizer_safe_trash_and_prunes_index(tmp_path):
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    bdir = proj_dir / ".agent_backups"
    trash_dir = bdir / "organizer" / "op1" / "trash"
    data_dir.mkdir()
    trash_dir.mkdir(parents=True)

    old_ts = time.time() - 45 * 86400

    # Safe Trash file >30 days old -> MUST SURVIVE!
    trash_file = trash_dir / "0"
    trash_file.write_text("user trashed file", encoding="utf-8")
    os.utime(trash_file, (old_ts, old_ts))

    # Regular edit backup >30 days old -> deleted & pruned from index.jsonl
    old_bak = bdir / "old.bak"
    old_bak.write_bytes(b"x" * 1024)
    os.utime(old_bak, (old_ts, old_ts))

    # Recent edit backup -> MUST SURVIVE!
    new_bak = bdir / "recent.bak"
    new_bak.write_bytes(b"y" * 1024)

    idx = bdir / "index.jsonl"
    idx.write_text(
        json.dumps({"backup": "old.bak", "path": "a.py"}) + "\n"
        + json.dumps({"backup": "recent.bak", "path": "b.py"}) + "\n",
        encoding="utf-8",
    )

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir, lock_dir=tmp_path / "locks")
    by_id = _by_id(doc.diagnose())
    assert by_id["agent_backups"]["status"] == "warn"
    assert by_id["agent_backups"]["meta"]["old_count"] == 1

    assert doc.apply_fix("agent_backups")["ok"] is True
    assert trash_file.exists(), "Safe Trash in .agent_backups/organizer/ must NEVER be deleted"
    assert not old_bak.exists()
    assert new_bak.exists()
    idx_text = idx.read_text(encoding="utf-8")
    assert "old.bak" not in idx_text
    assert "recent.bak" in idx_text


def test_fix_uncertain_jobs_reads_scheduled_json_and_cancels(tmp_path):
    from akatikito.scheduler import ScheduledQueue

    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    data_dir.mkdir()
    proj_dir.mkdir()

    sched_path = data_dir / "scheduled.json"
    sched_path.write_text(
        json.dumps({
            "projects": [str(proj_dir)],
            "authorized": {},
            "jobs": [{
                "id": "job1",
                "key": "k1",
                "project": str(proj_dir),
                "title": "Stuck job",
                "prompt": "do thing",
                "task": "do thing",
                "mode": "ask",
                "status": "uncertain",
                "created_at": time.time() - 60,
            }],
        }),
        encoding="utf-8",
    )
    q = ScheduledQueue(sched_path)

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir, lock_dir=tmp_path / "locks")
    by_id = _by_id(doc.diagnose())
    assert by_id["uncertain_jobs"]["status"] == "warn"
    assert by_id["uncertain_jobs"]["meta"]["ids"] == ["job1"]

    res = doc.apply_fix("uncertain_jobs", action="cancel")
    assert res["ok"] is True
    jobs_after = q.view()
    assert jobs_after[0]["status"] == "cancelled"


def test_windows_shortcuts_fix_invokes_create_shortcuts_script(tmp_path, monkeypatch):
    if os.name != "nt":
        return
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    scripts_dir = tmp_path / "scripts"
    data_dir.mkdir()
    proj_dir.mkdir()
    scripts_dir.mkdir()
    (scripts_dir / "create_shortcuts.py").write_text("# stub\n", encoding="utf-8")

    called: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        called.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir, lock_dir=tmp_path / "locks")
    res = doc.apply_fix("windows_shortcuts")
    assert res["ok"] is True
    assert called and str(scripts_dir / "create_shortcuts.py") in called[0]


def test_fix_daily_budget_sets_one_euro_and_logs_audit(tmp_path):
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    data_dir.mkdir()
    proj_dir.mkdir()
    (data_dir / "settings.json").write_text(json.dumps({"daily_budget": 0}), encoding="utf-8")

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir, lock_dir=tmp_path / "locks")
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
    raw = audit.path.read_text(encoding="utf-8").replace("legit", "TAMPERED")
    audit.path.write_text(raw, encoding="utf-8")

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir, lock_dir=tmp_path / "locks")
    chk = _by_id(doc.diagnose())["audit_chain"]
    assert chk["status"] == "error"
    assert chk["fixable"] is False

    res = doc.apply_fix("audit_chain")
    assert res["ok"] is False
    assert "TAMPERED" in audit.path.read_text(encoding="utf-8")


def test_doctor_is_human_only_never_exposed_as_llm_tool_and_cli_runs(tmp_path, capsys):
    from akatikito.tools import TOOL_SPECS

    tool_names = {t["function"]["name"] for t in TOOL_SPECS}
    assert not any("doctor" in name for name in tool_names)

    rc = _cli(["--project", str(tmp_path), "--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "checks" in parsed and len(parsed["checks"]) == 18
    assert rc in (0, 1)


def test_doctor_repairs_corrupted_doc_index_and_verifies_healthy_index(tmp_path):
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    data_dir.mkdir()
    (proj_dir / ".agent").mkdir(parents=True)
    db = proj_dir / ".agent" / "doc-index.sqlite"
    db.write_bytes(b"NOT A VALID SQLITE DATABASE!!!")

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir, lock_dir=tmp_path / "locks")
    chk = _by_id(doc.diagnose())["doc_index"]
    assert chk["status"] == "error" and chk["fixable"] is True

    res = doc.apply_fix("doc_index")
    assert res["ok"] is True and res["audit_logged"] is True
    assert list((proj_dir / ".agent").glob("doc-index.broken.*.sqlite"))
    assert _by_id(doc.diagnose())["doc_index"]["status"] == "ok"


def test_doctor_cleans_only_old_staged_attachments_and_keeps_recent(tmp_path):
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    att_dir = proj_dir / ".agent" / "attachments"
    data_dir.mkdir()
    att_dir.mkdir(parents=True)

    old_file = att_dir / "old_shot.png"
    recent_file = att_dir / "recent_shot.png"
    old_file.write_bytes(b"x" * 2048)
    recent_file.write_bytes(b"y" * 1024)
    old_ts = time.time() - 40 * 86400
    os.utime(old_file, (old_ts, old_ts))

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir, lock_dir=tmp_path / "locks")
    chk = _by_id(doc.diagnose())["staged_attachments"]
    assert chk["status"] == "warn" and chk["fixable"] is True
    assert chk["meta"]["old_count"] == 1

    res = doc.apply_fix("staged_attachments")
    assert res["ok"] is True
    assert not old_file.exists()
    assert recent_file.exists()
    assert _by_id(doc.diagnose())["staged_attachments"]["status"] == "ok"


def test_doctor_validates_and_repairs_invalid_global_hotkey_and_broken_usage_json(tmp_path):
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    assets_dir = tmp_path / "assets"
    data_dir.mkdir()
    proj_dir.mkdir()
    assets_dir.mkdir()
    (assets_dir / "akatikito.ico").write_bytes(b"\x00\x00\x01\x00")
    (data_dir / "settings.json").write_text(json.dumps({"global_hotkey": "BadKey+???+"}), encoding="utf-8")
    (data_dir / "usage.json").write_text("{corrupt usage", encoding="utf-8")

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir, lock_dir=tmp_path / "locks")
    rep = _by_id(doc.diagnose())
    assert rep["desktop_hotkey_tray"]["status"] == "warn" and rep["desktop_hotkey_tray"]["fixable"] is True
    assert "usage.json" in rep["json_integrity"]["meta"]["broken"]

    assert doc.apply_fix("desktop_hotkey_tray")["ok"] is True
    assert doc.apply_fix("json_integrity")["ok"] is True
    saved = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    assert saved["global_hotkey"] == "Alt+Space"
    assert json.loads((data_dir / "usage.json").read_text(encoding="utf-8")) == {"days": {}, "sessions": {}}
    assert _by_id(doc.diagnose())["desktop_hotkey_tray"]["status"] == "ok"


def test_doctor_source_syntax_catches_broken_app_python_readonly(tmp_path):
    pkg = tmp_path / "akatikito"
    pkg.mkdir()
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (pkg / "bad_mod.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=tmp_path, lock_dir=tmp_path / "locks")
    chk = _by_id(doc.diagnose())["source_syntax"]
    assert chk["status"] == "error" and chk["fixable"] is False
    assert "akatikito/bad_mod.py" in chk["meta"]["broken"]


def test_doctor_blocks_repairs_and_snapshots_while_busy_or_gateway_alive(tmp_path):
    from types import SimpleNamespace

    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    data_dir.mkdir()
    proj_dir.mkdir()
    (data_dir / "settings.json").write_text(json.dumps({"daily_budget": 0}), encoding="utf-8")

    # 1. Busy AppState blocks fix & snapshot
    busy_state = SimpleNamespace(busy=True, self_update=None, mobile_bridge=None)
    doc_busy = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir, state=busy_state, lock_dir=tmp_path / "locks")
    assert doc_busy.is_busy() is True
    assert doc_busy.apply_fix("daily_budget")["ok"] is False
    assert doc_busy.snapshot_configs()["ok"] is False

    # 2. Live gateway PID in data/gateway/control.json blocks fix
    gw = data_dir / "gateway"
    gw.mkdir()
    (gw / "control.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    doc_gw = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir, lock_dir=tmp_path / "locks")
    assert doc_gw.is_busy() is True
    assert doc_gw.apply_fix("daily_budget")["ok"] is False


def test_doctor_snapshot_configs_saves_only_valid_json_and_logs_audit(tmp_path):
    data_dir = tmp_path / "data"
    proj_dir = tmp_path / "proj"
    data_dir.mkdir()
    proj_dir.mkdir()
    (data_dir / "settings.json").write_text(json.dumps({"provider": "DeepSeek", "daily_budget": 2.0}), encoding="utf-8")
    (data_dir / "usage.json").write_text("{corrupted json", encoding="utf-8")

    doc = Doctor(app_root=tmp_path, data_dir=data_dir, project_root=proj_dir, lock_dir=tmp_path / "locks")
    res = doc.snapshot_configs()
    assert res["ok"] is True and res["audit_logged"] is True
    assert "settings.json" in res["saved"]
    assert "usage.json" in res["skipped"]
    assert list(data_dir.glob("settings.bak.*.json"))
    assert not list(data_dir.glob("usage.bak.*.json"))
    entries = AuditLog(proj_dir).entries(limit=10)
    assert any(e.get("action") == "doctor_snapshot" for e in entries)
