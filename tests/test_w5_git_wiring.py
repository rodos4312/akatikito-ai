"""W5 real-entry-point wiring test for D4 Git commit/draft PR tools gated behind Advanced `git`."""
from __future__ import annotations

import subprocess

import pytest

from akatikito import advanced
from akatikito.agent import Akatikito
from akatikito.app_api import AppState
from akatikito.config import Config
from akatikito.git_workflow import validate_git_args
from akatikito.model import ScriptedModel, tool_call
from akatikito.settings import Settings


def _init_repo(path):
    subprocess.run(["git", "init", "-b", "main"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "w5@akatikito.local"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "W5 Test"], cwd=str(path), check=True, capture_output=True)
    (path / "mod.py").write_text("VAL = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "mod.py"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), check=True, capture_output=True)


def test_w5_git_tools_closed_by_default_and_wired_when_advanced_git_enabled(tmp_path):
    _init_repo(tmp_path)
    new_tools = {"git_prepare_commit", "git_commit", "prepare_draft_pr"}
    assert new_tools <= set(advanced.FEATURES["git"].tools)

    # Door check: Advanced closed -> tools absent from allowed_tools & tools[]
    state = AppState()
    state.settings = Settings(project=str(tmp_path), advanced_features=[])
    state.agent = Akatikito(str(tmp_path), Config(model="m", api_key="k", checkpoints=False), model=ScriptedModel([]))
    state.agent._blocked_advanced_tools = advanced.blocked_tools(state.settings)
    state.agent.set_mode("code")
    assert not (new_tools & state.agent.allowed_tools("code"))

    # Enable Advanced `git` via AppState.set_preferences -> tools unlocked
    pref = state.set_preferences({"advanced_features": ["git"], "apply_now": True})
    assert not pref.get("error")
    assert new_tools <= state.agent.allowed_tools("code")
    state.close()

    # Secret file is refused even when Advanced `git` is enabled
    (tmp_path / ".env").write_text("SECRET_KEY=topsecret\n", encoding="utf-8")
    # Valid code change -> prepare, approve commit with diff preview, prepare draft PR
    (tmp_path / "mod.py").write_text("VAL = 2\n", encoding="utf-8")
    approvals: list[dict] = []
    script = [
        {"tool_calls": [tool_call("git_prepare_commit", files=[".env"])]},
        {"tool_calls": [tool_call("git_prepare_commit", files=["mod.py"])]},
        {"tool_calls": [tool_call("git_commit", message="fix: set VAL=2", files=["mod.py"])]},
        {"tool_calls": [tool_call("prepare_draft_pr", title="Set VAL=2", body="Small fix")]},
        {"content": "Έγινε commit και draft PR."},
    ]
    ag = Akatikito(str(tmp_path), Config(model="m", api_key="k", checkpoints=False), model=ScriptedModel(script))
    ag.set_mode("code")
    ag.ws.advanced_settings = Settings(advanced_features=["git"])
    ag.ws.confirm_write = lambda rel, diff: approvals.append({"rel": rel, "diff": diff}) or True
    ag._blocked_advanced_tools = advanced.blocked_tools(ag.ws.advanced_settings)

    reply = ag.chat("Κάνε commit το mod.py και ετοίμασε draft PR.")
    assert "commit" in reply
    assert approvals and "git_commit" in approvals[0]["rel"]
    log = subprocess.run(["git", "log", "-1", "--pretty=%s"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    assert log.stdout.strip() == "fix: set VAL=2"
    # .env was NEVER committed
    tracked = subprocess.run(["git", "ls-files"], cwd=str(tmp_path), capture_output=True, text=True, check=True).stdout
    assert ".env" not in tracked
    # push still strictly forbidden
    with pytest.raises(PermissionError):
        validate_git_args(["push", "origin", "main"])
