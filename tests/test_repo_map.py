"""Stable navigation under code edits, cache reloads and strict size budgets."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akatikito.repo_map import RepoMap
from akatikito import repo_map, memory
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel
from akatikito.workspace import Workspace
from akatikito.tools import read_file


def project(root):
    (root/"core.py").write_text("class Core:\n def process(self): return 1\n",encoding="utf-8")
    (root/"app.py").write_text("from core import Core\ndef main(): return Core()\n",encoding="utf-8")
    (root/"web.js").write_text("export const render = (x) => x;\n",encoding="utf-8")
    (root/"README.md").write_text("# project",encoding="utf-8")
    (root/".venv").mkdir()
    (root/".venv"/"hidden.py").write_text("def never_read(): pass")


def test_map_stays_identical_after_line_body_or_comment_changes(tmp_path):
    project(tmp_path)
    mapper=RepoMap(tmp_path)
    first=mapper.render(time_limit=10)
    assert "class Core(process)" in first and "render" in first and "never_read" not in first
    assert first.index("core.py:") < first.index("app.py:")
    (tmp_path/"core.py").write_text("\n# changed comment\nclass Core:\n def process(self): return 999\n")
    assert mapper.render(time_limit=10)==first
    (tmp_path/"core.py").write_text("class Core:\n def changed(self): return 999\n")
    assert "changed" in mapper.render(time_limit=10) and mapper.render(time_limit=10)!=first


def test_small_map_does_not_remove_or_make_omitted_source_unreadable(tmp_path):
    for number in range(150):
        (tmp_path/f"module_{number:03}.py").write_text(f"def long_function_{number}():\n return {number}\n")
    before={p.name:p.read_bytes() for p in tmp_path.glob("*.py")}
    text=RepoMap(tmp_path).render(budget=800,time_limit=10)
    assert len(text)<=800 and "εκτός προβολής" in text
    assert "return 149" in read_file(Workspace(tmp_path),"module_149.py")
    assert before=={p.name:p.read_bytes() for p in tmp_path.glob("*.py")}


def test_persistent_cache_reuses_parses_and_recovers_from_bad_cache(tmp_path,monkeypatch):
    project(tmp_path)
    first=RepoMap(tmp_path).render(time_limit=10)
    original=repo_map._describe
    monkeypatch.setattr(repo_map,"_describe",lambda *a:pytest.fail("Unchanged file was parsed again"))
    assert RepoMap(tmp_path).render(time_limit=10)==first
    monkeypatch.setattr(repo_map,"_describe",original)
    (tmp_path/".agent"/repo_map.CACHE_NAME).write_text("broken json")
    assert RepoMap(tmp_path).render(time_limit=10)==first


def test_deferred_analysis_is_completed_on_next_call_and_deleted_files_disappear(tmp_path):
    project(tmp_path);mapper=RepoMap(tmp_path)
    first=mapper.render(time_limit=-1)
    assert "εκκρεμεί" in first
    assert "class Core" in mapper.render(time_limit=10)
    (tmp_path/"core.py").unlink()
    assert "core.py" not in mapper.render(time_limit=10)


def test_agent_sends_only_changed_maps_and_no_map_for_general_questions(tmp_path,monkeypatch):
    project(tmp_path);monkeypatch.setattr(memory,"DIR",tmp_path/"memory")
    agent=Akatikito(str(tmp_path),Config(auto_mode=False),ScriptedModel([{"content":"ok"}]*4))
    agent.set_mode("code")
    agent.chat("Εξήγησε");agent.chat("Συνέχισε")
    maps=lambda:[m for m in agent.messages if m.get("_context_key")=="χάρτης κώδικα"]
    assert len(maps())==1
    (tmp_path/"app.py").write_text("\n\nfrom core import Core\ndef main(): return 20\n")
    agent.chat("Εξήγησε ξανά")
    assert len(maps())==1
    agent.set_mode("assistant");agent.chat("Καλημέρα")
    assert "Δεν υπάρχει ενεργό στοιχείο" in maps()[-1]["content"]
    ask=Akatikito(str(tmp_path),Config(auto_mode=False),ScriptedModel([{"content":"Γεια"}]))
    ask.set_mode("ask");ask.chat("Γεια")
    assert not [m for m in ask.messages if m.get("_context_key")=="χάρτης κώδικα"]
