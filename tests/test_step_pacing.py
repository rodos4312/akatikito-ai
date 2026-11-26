"""Tests for step-budget pacing nudges (65% pace, 85% wrap-up) in code/debug turns."""
from __future__ import annotations

from akatikito.agent import Akatikito
from akatikito.config import Config


def test_step_budget_nudges_fire_once_at_65_and_85_percent(tmp_path):
    for i in range(1, 10):
        (tmp_path / f"f{i}.txt").write_text(f"hello {i}\n", encoding="utf-8")
    seen_pace = []
    seen_wrap = []

    class TenStepModel:
        def __init__(self):
            self.step = 0

        def chat(self, messages, tools=None, cache_key=None):
            self.step += 1
            joined = "\n".join(str(m.get("content", "")) for m in messages)
            if "ΡΥΘΜΟΣ ΒΗΜΑΤΩΝ" in joined:
                seen_pace.append(self.step)
            if "ΤΕΛΙΚΗ ΕΥΘΕΙΑ" in joined:
                seen_wrap.append(self.step)
            if self.step < 9:
                return {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"c{self.step}",
                            "name": "read_file",
                            "arguments": f'{{"path": "f{self.step}.txt"}}',
                        }
                    ],
                }, {"prompt_tokens": 10, "completion_tokens": 5}
            return {"content": "Ολοκληρώθηκε εγκαίρως.", "tool_calls": []}, {"prompt_tokens": 10, "completion_tokens": 5}

    agent = Akatikito(str(tmp_path), Config(auto_mode=False, max_steps=10), TenStepModel())
    ans = agent.chat("Κάνε έλεγχο")
    assert "Ολοκληρώθηκε εγκαίρως." in ans
    # With max_steps=10: warn_at=6, wrap_at=8
    assert seen_pace and seen_pace[0] == 6
    assert seen_wrap and seen_wrap[0] == 8
    # Each nudge message was injected at most once
    pace_msgs = [m for m in agent.messages if "ΡΥΘΜΟΣ ΒΗΜΑΤΩΝ" in str(m.get("content", ""))]
    wrap_msgs = [m for m in agent.messages if "ΤΕΛΙΚΗ ΕΥΘΕΙΑ" in str(m.get("content", ""))]
    assert len(pace_msgs) == 1
    assert len(wrap_msgs) == 1
