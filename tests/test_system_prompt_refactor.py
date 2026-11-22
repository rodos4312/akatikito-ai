"""Tests για τον διαχωρισμό σύνθεσης system prompt (μη-περικοπτόμενα τμήματα + budget μνημών)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel
from akatikito.workspace import Workspace


def test_system_prompt_budget_is_configurable():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(tmp)
        Path(tmp, "RULES.md").write_text("KEEP RULE", encoding="utf-8")
        ws.memory_path().write_text("\n".join(f"memory {i}" for i in range(500)), encoding="utf-8")
        agent = Akatikito(
            tmp,
            config=Config(system_prompt_budget=2000, memory_excerpt_overhead=0),
            model=ScriptedModel([{"content": "ok"}]),
        )
        agent.runtime_instructions = "RUNTIME KEEP"
        prompt = agent.system_prompt()
        # Μη-περικοπτόμενα τμήματα παραμένουν ολόκληρα...
        assert "KEEP RULE" in prompt
        assert "RUNTIME KEEP" in prompt
        # ...ενώ η μνήμη κόβεται εντός του budget (παραμένει ανακτήσιμη με recall_context).
        assert "memory 499" not in prompt


def test_default_budget_keeps_prompt_under_cap():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(tmp)
        huge = "\n".join(f"σημείωση {i}: κείμενο." * 20 for i in range(200))
        ws.memory_path().write_text(huge, encoding="utf-8")
        agent = Akatikito(tmp, config=Config(), model=ScriptedModel([{"content": "ok"}]))
        prompt = agent.system_prompt()
        assert len(prompt) <= 12000
        assert "Λειτουργία" in prompt
        assert "Project:" in prompt
