"""Phase 3 hardening tests: backup-before-overwrite (mobile upload & tool plugins)
and session-sticky Smart Routing (no mid-conversation cache busting)."""
from __future__ import annotations

import io
from pathlib import Path


def test_mobile_upload_overwrite_creates_backup_first(tmp_path):
    from akatikito.mobile_bridge import save_upload_stream

    target = tmp_path / "doc.txt"
    target.write_bytes(b"OLD_VERSION")

    res = save_upload_stream(tmp_path, "doc.txt", io.BytesIO(b"NEW_VERSION"), len(b"NEW_VERSION"))
    assert res["ok"] is True
    assert target.read_bytes() == b"NEW_VERSION"

    backups = list((tmp_path / ".agent_backups").glob("*__doc.txt"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"OLD_VERSION"


def test_plugin_overwrite_true_creates_backup_first(tmp_path):
    from akatikito.tool_plugins import _guard_output_write
    from akatikito.workspace import Workspace

    ws = Workspace(str(tmp_path))
    existing = tmp_path / "report.pdf"
    existing.write_bytes(b"%PDF-OLD")

    _guard_output_write(ws, existing, overwrite=True)
    backups = list((tmp_path / ".agent_backups").glob("*__report.pdf"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"%PDF-OLD"


def test_smart_routing_is_session_sticky_until_reset(tmp_path):
    from akatikito.agent import Akatikito
    from akatikito.config import Config

    class TrackModel:
        def __init__(self):
            self.model = "base-m"
            self.seen_models: list[str] = []

        def chat(self, messages, tools=None, cache_key=""):
            self.seen_models.append(self.model)
            return {"content": "OK", "tool_calls": []}, {"prompt_tokens": 5, "completion_tokens": 5}

    cfg = Config(
        model="base-m",
        base_url="https://api.deepseek.com/v1",
        api_key="k",
        fast_model="fast-m",
        smart_model="smart-m",
        auto_mode=True,
        checkpoints=False,
        stream_responses=False,
    )
    stub = TrackModel()
    ag = Akatikito(str(tmp_path), cfg, stub)

    # Turn 1 (fresh session): simple question -> auto-mode picks 'ask' & locks session to 'fast-m'
    ag.chat("τι είναι το json;")
    # Turn 2 (same session): complex prompt -> stays on 'fast-m' so prompt cache is NOT busted!
    ag.chat("φτιάξε python script για refactor και security audit")
    assert stub.seen_models == ["fast-m", "fast-m"]

    # New session via reset(): complex prompt now picks 'smart-m'
    ag.reset()
    ag.chat("φτιάξε python script για refactor και security audit")
    assert stub.seen_models[-1] == "smart-m"


def test_sync_core_copies_python_core_and_prunes_stale_files(tmp_path):
    from scripts.sync_core import sync_core

    src = tmp_path / "src_core"
    dst = tmp_path / "dst_core"
    (src / "__pycache__").mkdir(parents=True)
    (src / "agent.py").write_text("# core\n", encoding="utf-8")
    (src / "__pycache__" / "agent.cpython-311.pyc").write_bytes(b"pyc")
    dst.mkdir()
    (dst / "obsolete.py").write_text("# old\n", encoding="utf-8")

    res = sync_core(src, dst)
    assert res["copied"] == 1
    assert res["removed"] == 1
    assert (dst / "agent.py").is_file()
    assert not (dst / "obsolete.py").exists()
    assert not (dst / "__pycache__").exists()
