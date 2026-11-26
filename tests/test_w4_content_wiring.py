"""W4 real-entry-point wiring test for L6 content-workflow skill & build_content_draft inside Akatikito.chat."""
from __future__ import annotations

from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel, tool_call


def test_w4_content_workflow_skill_runs_via_agent_chat_and_saves_sourced_draft(tmp_path):
    script = [
        {"tool_calls": [{"name": "use_skill", "arguments": {"name": "content-workflow"}}]},
        {
            "tool_calls": [
                tool_call(
                    "build_content_draft",
                    topic="Τοπική Ιδιωτικότητα AI",
                    outline=["Εισαγωγή", "Τοπική αποθήκευση", "Συμπέρασμα"],
                    body="Το Akatikito διατηρεί όλα τα δεδομένα και τη μνήμη τοπικά στον υπολογιστή του χρήστη χωρίς cloud.",
                    sources=[{"title": "Τεκμηρίωση", "reference": "docs/SETTINGS.md"}],
                    fmt="md",
                    output_subdir="drafts",
                )
            ]
        },
        {"content": "Το πρόχειρο αποθηκεύτηκε με πηγές χωρίς δημοσίευση."},
    ]
    agent = Akatikito(str(tmp_path), Config(model="m", api_key="k", checkpoints=False), model=ScriptedModel(script))
    agent.set_mode("assistant")
    assert "build_content_draft" in agent.allowed_tools("assistant")
    assert "build_content_draft" not in agent.allowed_tools("ask")

    reply = agent.chat("Χρησιμοποίησε το skill content-workflow για άρθρο περί τοπικής ιδιωτικότητας.")
    assert "πρόχειρο" in reply
    saved = list((tmp_path / "drafts").glob("*.md"))
    assert len(saved) == 1
    text = saved[0].read_text(encoding="utf-8")
    assert "Τεκμηρίωση" in text and "docs/SETTINGS.md" in text
    assert "χωρίς δημοσίευση" in text
