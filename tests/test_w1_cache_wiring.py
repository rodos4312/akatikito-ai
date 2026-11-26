"""W1 real-entry-point wiring test for B2 CacheGuard inside Akatikito.chat."""
from __future__ import annotations

from akatikito import memory
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel


def test_w1_agent_chat_tracks_session_frame_and_defers_mid_session_setting_change(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DIR", tmp_path / "memory")
    notices: list[dict] = []
    seen_system: list[str] = []

    class RecordingModel(ScriptedModel):
        def chat(self, messages, tools=None, **kwargs):
            seen_system.append(messages[0]["content"] if messages else "")
            return {"content": f"ok-{len(seen_system)}", "tool_calls": []}, {
                "prompt_tokens": 500,
                "completion_tokens": 10,
                "cache_hit_tokens": 450,
            }

    cfg = Config(model="scripted-w1", base_url="http://127.0.0.1:9/v1", api_key="synthetic", checkpoints=False)
    agent = Akatikito(str(tmp_path), cfg, model=RecordingModel([]))
    agent.events.subscribe(lambda ev: notices.append(ev.data) if ev.kind == "cache_notice" else None)

    fingerprints: list[str] = []
    for turn in range(1, 11):
        if turn == 5:
            # Mid-session setting change without apply_now must emit notice and NOT mutate fingerprint
            res = agent.defer_setting_change("ui_language", apply_now=False)
            assert res["applied_now"] is False
            assert "επόμενη συζήτηση" in res["notice"]
            memory.append("user", "Μνήμη στον 5ο γύρο")
        reply = agent.chat(f"Γύρος {turn}")
        assert reply == f"ok-{turn}"
        assert agent.cache_guard.frame is not None
        fingerprints.append(agent.cache_frame_fingerprint)

    # All 10 turns share the exact same SessionFrame fingerprint
    assert len(fingerprints) == 10
    assert len(set(fingerprints)) == 1 and fingerprints[0] != ""
    assert len(set(seen_system)) == 1
    assert notices and any("επόμενη συζήτηση" in n.get("notice", "") for n in notices)

    # New session (reset) clears frame & pending changes and freezes fresh frame on next turn
    old_fp = fingerprints[0]
    agent.reset()
    assert agent.cache_guard.frame is None
    assert agent.cache_guard.pending_changes() == []
    agent.runtime_instructions = "Νέα οδηγία επόμενης συζήτησης"
    agent.chat("Νέα συνεδρία")
    assert agent.cache_frame_fingerprint != "" and agent.cache_frame_fingerprint != old_fp
