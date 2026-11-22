"""Behavior regressions for sessions, models, modes and source search."""
from __future__ import annotations
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from akatikito import app_api
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel, tool_call
from akatikito.sessions import SessionStore
from akatikito.settings import Settings
from akatikito.tools import dispatch
from akatikito.workspace import Workspace, detect_verify


def wait(state):
    for _ in range(200):
        if not state.busy:
            return
        time.sleep(.01)
    raise AssertionError("Worker did not finish")


def scripted(root, replies):
    return Akatikito(str(root), Config(api_key="test", max_steps=8, verify_command=""), ScriptedModel(replies))


def main():
    root = Path(tempfile.mkdtemp(prefix="akatikito-regression-"))
    catalog, project = root / "app", root / "project"
    catalog.mkdir(); project.mkdir()
    app_api.APP_ROOT = catalog
    state = app_api.AppState()
    state.settings = Settings(provider="DeepSeek", model="custom-last-model", project=str(project))
    state.settings.set_key("DeepSeek", "test")
    sid = SessionStore(catalog).create()
    SessionStore(catalog).save(sid, transcript=[{"role":"user", "text":"παλιά συνομιλία"}])
    rows = state.sessions()
    assert len(rows) == 1 and rows[0]["project"] == str(catalog)
    assert not state.open_session(sid, str(catalog)).get("error")
    assert state.transcript[0]["text"] == "παλιά συνομιλία"
    assert state.agent.config.model == "custom-last-model"
    assert state.change_model("moonshotai/kimi-k2.7-code")["ok"]
    saved = Settings.load()
    assert saved.model == "moonshotai/kimi-k2.7-code" and saved.models["DeepSeek"] == saved.model
    state.busy = True
    assert state.change_model("wrong").get("error")
    assert state.new_session().get("error") and state.set_mode("ask").get("error")
    state.busy = False
    state.new_session()
    state.agent.model = ScriptedModel([{"content":"απάντηση", "tool_calls":[]}])
    state.send("γεια"); wait(state)
    assert state.transcript[-1]["model"] == "moonshotai/kimi-k2.7-code"
    assert SessionStore(catalog).load(state.session_id)["transcript"][-1]["model"] == saved.model
    print("OK session discovery/open, model persistence, answer attribution and busy guards")
    state.new_session()
    class Broken:
        def chat(self, *args, **kwargs):
            raise RuntimeError("simulated failure")
    state.agent.model = Broken()
    state.send("διατήρησε και αυτή την ερώτηση"); wait(state)
    assert state.error and SessionStore(catalog).load(state.session_id)["transcript"][0]["role"] == "user"
    print("OK failed requests retain their session")
    clipboard = {"text":"δοκιμή\nδεύτερη γραμμή"}
    original = sys.modules.get("pyperclip")
    sys.modules["pyperclip"] = SimpleNamespace(copy=lambda text: clipboard.update(text=text), paste=lambda:clipboard["text"])
    try:
        assert state.copy_text("ελληνικά")["ok"]
        assert state.paste_text()["text"] == "ελληνικά"
    finally:
        if original is None: sys.modules.pop("pyperclip", None)
        else: sys.modules["pyperclip"] = original
    print("OK clipboard backend without touching real clipboard")
    agent = scripted(project, [{"tool_calls":[tool_call("write_file", path="blocked.py", content="x")]} , {"content":"done"}])
    agent.set_mode("ask"); agent.chat("γράψε κώδικα")
    assert agent.mode == "ask" and not (project/"blocked.py").exists()
    agent.reset(); assert agent.mode == "ask"
    agent = scripted(project, [{"tool_calls":[tool_call("set_mode", mode="code"),tool_call("write_file",path="bypass.py",content="x")]}, {"content":"done"}])
    agent.set_mode("ask"); agent.chat("τι κάνει;")
    assert agent.mode == "ask" and not (project/"bypass.py").exists()
    agent = scripted(project, [{"tool_calls":[tool_call("write_file",path="impl.py",content="bad"), tool_call("write_file",path="PLAN.md",content="σχέδιο")]}, {"content":"done"}])
    agent.set_mode("architect"); agent.chat("φτιάξε το πλάνο")
    assert not (project/"impl.py").exists() and (project/"PLAN.md").is_file()
    print("OK manual mode preserved, no model privilege escalation, architect restricted to PLAN.md")
    agent = scripted(project, [
        {"tool_calls":[tool_call("set_mode",mode="architect")]},
        {"tool_calls":[tool_call("set_mode",mode="code")]},
        {"tool_calls":[tool_call("write_file",path="done.py",content="print(1)")]},
        {"content":"done"}, {"content":"explanation"},
    ])
    agent.chat("υλοποίησε τη λειτουργία")
    assert (project/"done.py").is_file() and agent.mode == "code"
    agent.chat("τι κάνει αυτό;"); assert agent.mode == "ask"
    agent.set_mode("code"); agent.model = ScriptedModel([{"content":"ok"}]); agent.chat("τι κάνει;")
    assert agent.mode == "code"
    agent.set_mode("auto"); agent.model = ScriptedModel([{"content":"ok"}]); agent.chat("σχεδίασε πλάνο")
    assert agent.mode == "architect"
    print("OK automatic plan/code transitions, next-turn classification and manual override")
    (project/"visible.py").write_text("unique_token",encoding="utf-8")
    for name in [".venv", ".agent_backups", "__pycache__", "data"]:
        (project/name).mkdir(exist_ok=True)
        (project/name/"hidden.txt").write_text("unique_token",encoding="utf-8")
    result = dispatch(Workspace(project), "search_project", {"keyword":"unique_token"})
    assert "visible.py" in result and "hidden.txt" not in result
    tests = project/"tests"; tests.mkdir()
    (tests/"test_script.py").write_text("def main():\n    return 0\n",encoding="utf-8")
    command = detect_verify(project)
    assert "test_script.py" in command and "pytest" not in command
    print("OK search ignores internal data, standalone tests actually execute")
    print("REGRESSIONS OK")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
