"""Regression checks use synthetic commands and scripted models, never providers."""
import concurrent.futures
import sys
from pathlib import Path
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from akatikito.approvals import ApprovalBroker, command_risk
from akatikito.tools import run_command
from akatikito.workspace import Workspace
from akatikito.app_api import AppState
from akatikito.model import ScriptedModel
from akatikito import memory
from akatikito.settings import Settings


@pytest.mark.parametrize("command", [
    "git clean -fd", "git reset --hard", "git push origin main", "git rebase main",
    "git checkout -- file.py", "git branch -D old", "git restore .",
    "git --literal-pathspecs reset --hard", "git --no-pager clean -fd",
    'git -c alias.x=bad status', "git -cfoo=bar status",
    '"C:\\Program Files\\Git\\bin\\git.exe" reset --hard',
    "taskkill /F /IM synthetic.exe", "Stop-Process -Name synthetic",
    "powershell -EncodedCommand synthetic", "cmd /c echo hello",
    "git status && taskkill /F /IM synthetic.exe", "g^it reset --hard",
    "pip uninstall package", "robocopy a b /MIR", "setx NAME value",
    "python -m pip uninstall package",
])
def test_destructive_actions_are_always_classified_dangerous(command):
    assert command_risk(command) == "dangerous"


@pytest.mark.parametrize("command", [
    'python -c "print(\'pytest\')"', "git diff --output=example.patch",
    "git diff --ext-diff", "git show --textconv HEAD", "git.cmd status",
    "git status && echo hello", "python -m pytest > output.txt",
])
def test_safe_names_cannot_disguise_other_actions(command):
    assert command_risk(command) == "normal"


def test_external_paths_are_dangerous_but_project_test_paths_are_safe(tmp_path):
    inside=tmp_path/"tests"
    assert command_risk(f'python -m pytest "{inside}"', tmp_path) == "safe"
    assert command_risk(f'git diff --output="{tmp_path.parent / "outside.patch"}"', tmp_path) != "safe"
    assert command_risk(f'git diff --output="{tmp_path.parent / "outside.patch"}"', tmp_path) == "dangerous"
    assert command_risk("git diff ../outside.py", tmp_path) == "dangerous"
    assert command_risk(f'git -C "{tmp_path.parent}" status', tmp_path) == "dangerous"
    assert command_risk(f'git -C "{tmp_path}" status', tmp_path) == "safe"


def test_previously_remembered_permission_and_autonomy_do_not_cover_git_reset(tmp_path):
    command="git reset --hard"
    key=ApprovalBroker.key(tmp_path,"command",{"command":command})
    broker=ApprovalBroker(allowed=[key],timeout=0)
    assert not broker.request(tmp_path,"command",{"command":command},command_risk(command),True)
    ws=Workspace(tmp_path)
    observed=[]
    ws.command_guard=lambda c,r: observed.append((c,r)) or False
    assert "[OWNER_DENIED]" in run_command(ws,command)
    assert observed == [(command,"dangerous")]


def test_changing_provider_never_inherits_an_unrelated_model(tmp_path,monkeypatch):
    monkeypatch.setattr(memory,"DIR",tmp_path/"memory")
    settings=Settings()
    settings.provider="DeepSeek";settings.model="deepseek/saved"
    settings.models={"DeepSeek":"deepseek/saved","Custom":"custom/saved"}
    monkeypatch.setattr(Settings,"load",lambda:settings)
    monkeypatch.setattr(Settings,"save",lambda self:None)
    created=[]
    monkeypatch.setattr("akatikito.agent.OpenAIModel",
                        lambda config:created.append(config) or ScriptedModel([]))
    state=AppState()
    result=state.connect("Custom","https://synthetic.invalid/v1","","",str(tmp_path),5,"")
    assert result["connected"] and created[-1].model=="custom/saved"
    state=AppState()
    settings.models.pop("Custom")
    settings.provider="DeepSeek";settings.model="deepseek/saved"
    result=state.connect("Custom","https://synthetic.invalid/v1","","",str(tmp_path),5,"")
    assert "error" in result and len(created)==1
    state=AppState()
    result=state.connect("OpenAI","","","synthetic",str(tmp_path),5,"")
    assert result["connected"] and created[-1].model != "deepseek/saved"
