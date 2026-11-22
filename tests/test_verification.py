"""Reproduce candidate checks with a wrong host interpreter and explicit self-upgrade requests."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from akatikito import app_api, verification
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel
from akatikito.sessions import SessionStore
from akatikito.settings import Settings


def main():
    root = Path(tempfile.mkdtemp(prefix="akatikito-verification-regression-"))
    (root/"akatikito").mkdir(); (root/"tests").mkdir()
    for name in ["app.py","akatikito/__init__.py"]:
        (root/name).write_text("# fixture\n",encoding="utf-8")
    (root/"akatikito/ui.html").write_text("<script>const ok = true;</script>",encoding="utf-8")
    expected = verification.interpreter_for_checks()
    probe = "import sys\nfrom pathlib import Path\nimport webview\nimport faster_whisper\nassert Path(sys.executable).resolve() == Path(" + repr(str(expected)) + ").resolve()\nprint('DESKTOP CHECK REALLY EXECUTED')\n"
    (root/"tests/test_probe.py").write_text(probe,encoding="utf-8")
    wrong_host = Path(sys.base_prefix)/"python.exe"
    with patch.object(sys, "executable", str(wrong_host)), patch.dict(os.environ, {"PYTHONHOME":"invalid-parent-home", "PYTHONPATH":"invalid-parent-path"}):
        ok, report = verification.run_checks(root, ["tests/test_probe.py"])
    assert ok, report
    assert "DESKTOP CHECK REALLY EXECUTED" in report
    assert f"Test Python: {expected}" in report
    print("OK candidate checks use application Python despite wrong host/environment; desktop dependencies really load")
    with patch("akatikito.verification.subprocess.run", return_value=subprocess.CompletedProcess([],1,'{"missing":["webview"]}','')) as runner:
        ok, report = verification.run_checks(root,["tests/test_probe.py"])
    assert not ok and "webview" in report and runner.call_count == 1
    print("OK missing dependencies fail visibly instead of silently skipping checks")
    positive = ["θέλω να αναβαθμίσεις τον εαυτό σου", "αναβάθμισε τον εαυτό σου", "thelo na anabathmiseis ton eauto sou", "improve yourself"]
    negative = ["πώς θα αναβάθμιζες τον εαυτό σου;", "τι προτείνεις για τον εαυτό σου", "μην αναβαθμίσεις τον εαυτό σου", "pos tha anabathmizes ton eauto sou", "θέλω αλλαγή στο άλλο project"]
    assert all(app_api.requests_self_upgrade(t) for t in positive)
    assert all(not app_api.requests_self_upgrade(t) for t in negative)
    print("OK explicit self-upgrade requests distinguished from discussion and refusal")
    project = root/"personal-backup"; project.mkdir()
    before = root/"personal-backup/keep.txt"; before.write_text("personal backup",encoding="utf-8")
    state = app_api.AppState()
    state.settings = Settings(provider="DeepSeek", model="test-model", project=str(project))
    state.agent = Akatikito(str(project), Config(api_key="test",model="test-model"), ScriptedModel([{"content":"old"}]))
    state.store = SessionStore(project)
    state.transcript = [{"role":"user","text":"previous conversation"}]
    with patch.object(app_api,"APP_ROOT",root), patch("akatikito.agent.OpenAIModel",return_value=ScriptedModel([{"content":"working in candidate"}])):
        result = state.send("thelo na anabathmiseis ton eauto sou")
        for _ in range(200):
            if not state.busy: break
            time.sleep(.01)
        assert not state.busy and not state.error, state.error
        assert result.get("conversation_changed") and state.self_update.root == root
        assert state.agent.ws.root == state.self_update.stage
        assert state.agent.config.model == "test-model"
        assert "ΜΠΟΡΕΙΣ" in state.agent.system_prompt() and "ΑΥΤΟΑΝΑΒΑΘΜΙΣΗ ΕΝΕΡΓΗ" in state.agent.system_prompt()
        assert before.read_text() == "personal backup"
        assert state.cancel_upgrade()["project"] == str(project)
        assert state.transcript[0]["text"] == "previous conversation"
    print("OK request routes to current app candidate, preserves model, personal backup and previous chat")
    state.agent.set_mode("ask")
    assert state.send("αναβάθμισε τον εαυτό σου").get("error") and state.self_update is None
    print("OK explicit manual read-only selection remains respected")
    print("VERIFICATION REGRESSIONS OK")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
