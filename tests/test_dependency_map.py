"""The code map says who depends on a file, without costing names or stability."""
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akatikito.repo_map import RepoMap
from akatikito import repo_map


def line_of(text,name):
    return next((l for l in text.splitlines() if l.startswith(name+":") or l==name),"")


def project(root):
    (root/"core.py").write_text("class Core:\n def process(self): return 1\n",encoding="utf-8")
    (root/"app.py").write_text("import os\nfrom core import Core\ndef main(): return Core()\n",encoding="utf-8")
    (root/"lonely.py").write_text("def alone(): return 1\n",encoding="utf-8")


def test_importer_is_listed_as_dependant(tmp_path):
    project(tmp_path)
    assert "← app.py" in line_of(RepoMap(tmp_path).render(time_limit=10),"core.py")


def test_file_nobody_imports_has_no_dependants_line(tmp_path):
    project(tmp_path)
    text=RepoMap(tmp_path).render(time_limit=10)
    assert "←" not in line_of(text,"lonely.py") and "←" not in line_of(text,"app.py")


def test_stdlib_and_unknown_imports_never_create_dependants(tmp_path):
    project(tmp_path)
    (tmp_path/"extra.py").write_text("import json, requests\nfrom pathlib import Path\nfrom nowhere import thing\n")
    assert "←" not in RepoMap(tmp_path).render(time_limit=10).replace("← app.py","")


def test_exact_root_module_is_not_hidden_by_same_basename_in_other_package(tmp_path):
    project(tmp_path)
    (tmp_path/"pkg").mkdir();(tmp_path/"pkg"/"core.py").write_text("def other(): pass\n")
    text=RepoMap(tmp_path).render(time_limit=10)
    assert "← app.py" in line_of(text,"core.py")
    assert "←" not in line_of(text,"pkg/core.py")


def test_long_dependant_list_is_truncated_with_count(tmp_path):
    project(tmp_path)
    for n in range(6):(tmp_path/f"user_{n}.py").write_text("import core\n")
    row=line_of(RepoMap(tmp_path).render(time_limit=10),"core.py")
    assert "+4" in row and row.count("\n")==0


def test_dependants_survive_body_comment_and_blank_line_edits(tmp_path):
    project(tmp_path);mapper=RepoMap(tmp_path)
    first=mapper.render(time_limit=10)
    assert "←" in first
    (tmp_path/"app.py").write_text("import os\n\n# note\nfrom core import Core\ndef main(): return 99\n")
    (tmp_path/"core.py").write_text("class Core:\n def process(self): return 'changed'\n")
    assert mapper.render(time_limit=10)==first


def test_tight_budget_drops_dependants_before_names(tmp_path,monkeypatch):
    for n in range(40):(tmp_path/f"mod_{n:02}.py").write_text(f"def function_{n}(): pass\n")
    (tmp_path/"user.py").write_text("\n".join(f"import mod_{n:02}" for n in range(40))+"\n")
    full=RepoMap(tmp_path).render(budget=6000,time_limit=10)
    assert full.count("←")==40
    dropped=False
    for budget in range(len(full)-1,len(full)-400,-25):
        with_deps=RepoMap(tmp_path).render(budget=budget,time_limit=10)
        monkeypatch.setattr(repo_map,"_dependants",lambda *a,**k:{})
        names_only=RepoMap(tmp_path).render(budget=budget,time_limit=10)
        monkeypatch.undo()
        assert len(with_deps)<=budget
        assert re.sub(r" ← [^\n]*","",with_deps)==names_only
        dropped|=0<with_deps.count("←")<40
    assert dropped
