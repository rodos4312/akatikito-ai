"""W3 real-entry-point wiring test for L5 NotesVault inside Settings, i18n, AppState, and Akatikito.chat."""
from __future__ import annotations

from akatikito import i18n
from akatikito.agent import Akatikito
from akatikito.app_api import AppState
from akatikito.config import Config
from akatikito.model import ScriptedModel, tool_call
from akatikito.settings import Settings


def test_w3_notes_closed_by_default_and_wired_into_chat_and_i18n(tmp_path):
    # Closed by default -> notes_folder empty, tools absent from advertised tools[]
    cfg_default = Settings()
    assert cfg_default.notes_folder == ""
    seen_closed: list[list[str]] = []

    class CaptureModel(ScriptedModel):
        def chat(self, messages, tools=None, **kwargs):
            seen_closed.append([t["function"]["name"] for t in (tools or [])])
            return {"content": "ok", "tool_calls": []}, {"prompt_tokens": 10, "completion_tokens": 2}

    ag_closed = Akatikito(str(tmp_path), Config(model="m", api_key="k", checkpoints=False), model=CaptureModel([]))
    ag_closed.set_mode("code")
    ag_closed.chat("έλεγξε σημειώσεις")
    assert "search_notes" not in seen_closed[0]
    assert "propose_note_edit" not in seen_closed[0]

    # All 6 UI languages document the notes folder setting label
    for lang in i18n.SUPPORTED_LANGUAGES:
        bundle = i18n.TRANSLATIONS[lang]
        assert "lbl_notes_folder" in bundle and bundle["lbl_notes_folder"].strip()

    # Configure notes_folder via AppState.set_preferences -> persists and enables tools
    vault = tmp_path / "obsidian"
    vault.mkdir()
    (vault / "recipe.md").write_text("# Συνταγή\nΜυστικό υλικό: θυμάρι και ρίγανη.\n", encoding="utf-8")

    state = AppState()
    state.settings.project = str(tmp_path)
    pref_res = state.set_preferences({"notes_folder": str(vault)})
    assert not pref_res.get("error"), pref_res
    assert state.settings.notes_folder == str(vault.resolve())
    state.close()

    # Open notes_folder -> Akatikito.chat calls search_notes and finds the note
    seen_tools_ask: list[list[str]] = []
    script_ask = [
        {"tool_calls": [tool_call("search_notes", query="θυμάρι")]},
        {"content": "Βρήκα τη συνταγή με θυμάρι."},
    ]

    class AskModel(ScriptedModel):
        def chat(self, messages, tools=None, **kwargs):
            seen_tools_ask.append([t["function"]["name"] for t in (tools or [])])
            return super().chat(messages, tools=tools, **kwargs)

    ag_ask = Akatikito(
        str(tmp_path),
        Config(model="m", api_key="k", checkpoints=False, notes_folder=str(vault)),
        model=AskModel(script_ask),
    )
    ag_ask.set_mode("ask")
    ans = ag_ask.chat("τι είχα γράψει για θυμάρι στις σημειώσεις;")
    assert "θυμάρι" in ans
    assert "search_notes" in seen_tools_ask[0]
    assert "propose_note_edit" not in seen_tools_ask[0]  # ask mode is strictly read-only

    # Code mode: propose_note_edit rejects path escape and requires approval for existing edits
    approvals: list[dict] = []
    script_edit = [
        {"tool_calls": [tool_call("propose_note_edit", path="../escape.md", content="bad")]},
        {"tool_calls": [tool_call("propose_note_edit", path="recipe.md", content="# Συνταγή\nΝέο υλικό: βασιλικός.\n")]},
        {"content": "Έγινε."},
    ]
    ag_code = Akatikito(
        str(tmp_path),
        Config(model="m", api_key="k", checkpoints=False, notes_folder=str(vault)),
        model=ScriptedModel(script_edit),
    )
    ag_code.set_mode("code")
    ag_code.ws.confirm_write = lambda rel, diff: approvals.append({"rel": rel, "diff": diff}) or True
    ag_code.chat("ενημέρωσε τη σημείωση")
    assert not (tmp_path / "escape.md").exists()
    assert approvals and "βασιλικός" in approvals[0]["diff"]
    assert "βασιλικός" in (vault / "recipe.md").read_text(encoding="utf-8")
