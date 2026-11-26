"""B2 Prompt-cache discipline guard: 10-turn stable SessionFrame fingerprint + explicit apply_now."""
from __future__ import annotations

import copy

from akatikito import memory
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel
from akatikito.session_frame import CacheGuard, SessionFrame, cache_hit_summary


def test_ten_turn_session_keeps_identical_session_frame_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DIR", tmp_path / "memory")
    frames: list[str] = []

    class RecordingModel(ScriptedModel):
        def chat(self, messages, tools=None, **kwargs):
            system = messages[0]["content"] if messages and messages[0].get("role") == "system" else ""
            fp = SessionFrame.build(system, tools or [], "scripted-b2").fingerprint()
            frames.append(fp)
            return {"content": f"Απάντηση #{len(frames)}", "tool_calls": []}, {
                "prompt_tokens": 1000,
                "completion_tokens": 20,
                "cache_hit_tokens": 920,
            }

    cfg = Config(model="scripted-b2", base_url="http://127.0.0.1:9/v1", api_key="synthetic", checkpoints=False)
    agent = Akatikito(str(tmp_path), cfg, model=RecordingModel([]))

    for idx in range(10):
        if idx == 4:
            # Mid-session memory update must be appended at tail, never mutating system prompt
            memory.append("user", "Νέα προτίμηση στη μέση της συζήτησης")
        reply = agent.chat(f"Ερώτηση #{idx + 1}")
        assert reply == f"Απάντηση #{idx + 1}"

    assert len(frames) == 10
    assert len(set(frames)) == 1, f"SessionFrame fingerprint drifted mid-session: {frames}"


def test_cache_guard_defers_changes_until_apply_now_and_reports_hit_ratio():
    guard = CacheGuard()
    tools_a = [{"function": {"name": "read_file"}}, {"function": {"name": "list_files"}}]
    tools_b = copy.deepcopy(list(reversed(tools_a)))
    f1 = guard.freeze("SYSTEM v1", tools_a, "deepseek-chat")
    # Tool order differences must yield identical fingerprint
    assert f1.fingerprint() == SessionFrame.build("SYSTEM v1", tools_b, "deepseek-chat").fingerprint()

    # Mid-session change without apply_now preserves fingerprint
    deferred = guard.on_setting_change("skills", apply_now=False, new_system="SYSTEM v2")
    assert deferred["applied_now"] is False
    assert deferred["cache_broken"] is False
    assert deferred["fingerprint"] == f1.fingerprint()
    assert "επόμενη συζήτηση" in deferred["notice"]
    assert guard.pending_changes() == ["skills"]

    # Explicit apply_now updates frame and signals new cache
    applied = guard.on_setting_change("skills", apply_now=True, new_system="SYSTEM v2")
    assert applied["applied_now"] is True
    assert applied["cache_broken"] is True
    assert applied["fingerprint"] != f1.fingerprint()
    assert guard.pending_changes() == []

    summary = cache_hit_summary(10_000, 9_200)
    assert summary["percent"] == 92.0
    assert summary["label"] == "cache: 92% των tokens εισόδου"
