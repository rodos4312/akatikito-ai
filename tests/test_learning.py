"""Owner-reviewed learning; all data is synthetic and lives in pytest temp dirs."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akatikito import memory
from akatikito.profile import Profile, CONSOLIDATE_SECONDS
from akatikito.skills import Skills
from akatikito import skills as skills_module, modes
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel,tool_call
from akatikito.app_api import AppState
from akatikito.settings import Settings


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(memory,"DIR",tmp_path/"private-memory")


def pending(profile):
    return profile.listing()["proposals"][-1]


def approve(profile):
    row=pending(profile)
    profile.answer(row["id"],True,expected=row["review_hash"])
    return row


def events():
    return [SimpleNamespace(kind="tool_result",data={"name":name,"result":result}) for name,result in [
        ("read_file","current code"),("edit_file","ΕΓΙΝΕ: changed"),("run_command","returncode=0\npassed")]]


def repeated(store):
    store.capture(events());store.capture(events())
    return store.hints()[0]["id"]


STEPS=["Διάβασε τον σχετικό κώδικα.","Διόρθωσε τη συγκεκριμένη αιτία.","Τρέξε τους σχετικούς ελέγχους."]


def test_facts_beliefs_and_revisions_do_not_accumulate_contradictions():
    p=Profile()
    p.propose("Ύφος","Σύντομες απαντήσεις","fact","confirmed")
    assert p.prompt()==""  # a proposal is not active memory
    first=approve(p)
    p.propose("ΥΦΟΣ","Αναλυτικές απαντήσεις","fact","confirmed")
    assert "Σύντομες" in p.prompt() and "Αναλυτικές" not in p.prompt()
    approve(p)
    assert "Αναλυτικές" in p.prompt() and "Σύντομες" not in p.prompt()
    history=p.load()["revisions"]
    assert any(r["action"]=="revision" and r["before"][0]["text"]=="Σύντομες απαντήσεις" for r in history)
    p.propose("Πιθανή αιτία","Ίσως υπάρχει race","belief","unconfirmed")
    approve(p)
    assert "ΑΝΕΠΙΒΕΒΑΙΩΤΗ ΥΠΟΘΕΣΗ" in p.prompt()
    p.propose("Πιθανή αιτία","Η αιτία αποδείχτηκε","belief","confirmed")
    approve(p)
    assert "Ίσως" not in p.prompt() and "Η αιτία αποδείχτηκε" in p.prompt()


def test_duplicate_pending_is_idempotent_and_rejection_keeps_current_value():
    p=Profile();p.propose("γλώσσα","Ελληνικά","fact","confirmed");approve(p)
    p.propose("γλώσσα","Αγγλικά","fact","confirmed")
    before=p.path.read_bytes();stamp=p.path.stat().st_mtime_ns
    assert "ήδη" in p.propose("γλώσσα","Αγγλικά","fact","confirmed")
    assert p.path.read_bytes()==before and p.path.stat().st_mtime_ns==stamp
    row=pending(p);p.answer(row["id"],False,expected=row["review_hash"])
    assert "Ελληνικά" in p.prompt() and "Αγγλικά" not in p.prompt()
    assert not p.listing()["proposals"]


def test_legacy_migration_preserves_original_and_forgotten_notes_do_not_return():
    memory.DIR.mkdir()
    legacy=memory.DIR/"USER.md"
    original="- Κράτα το πρώτο.\n- Κράτα το δεύτερο.\n- Κράτα το πρώτο.\n"
    legacy.write_text(original,encoding="utf-8")
    p=Profile();data=p.load()
    assert len(data["entries"])==2 and legacy.read_text(encoding="utf-8")==original
    assert data["revisions"][0]["original"].encode("utf-8")==legacy.read_bytes()
    p.forget(data["entries"][0]["id"])
    legacy.write_text(original+"- Μια νέα σημείωση.\n",encoding="utf-8")
    assert "Κράτα το πρώτο." not in p.prompt() and "Μια νέα σημείωση." in p.prompt()
    assert legacy.read_text(encoding="utf-8")==original+"- Μια νέα σημείωση.\n"


def test_consolidation_archives_only_exact_duplicates_and_keeps_conflicts():
    p=Profile();p.propose("editor","Editor A","fact","confirmed");approve(p)
    data=p.load();copy=dict(data["entries"][0]);copy["id"]="duplicate"
    other={**copy,"id":"conflict","text":"Editor B"}
    data["entries"].extend([copy,other]);p._save(data)
    result=p.consolidate(now=100)
    assert result["merged"]==1 and len(result["conflicts"])==1
    assert "Editor A" in p.prompt() and "Editor B" in p.prompt()
    assert p.load()["revisions"][-1]["before"][0]["id"]=="duplicate"
    before=p.path.read_bytes()
    assert not p.consolidate(due_only=True,now=101)["due"] and p.path.read_bytes()==before
    p.consolidate(due_only=True,now=100+CONSOLIDATE_SECONDS)
    assert p.load()["last_consolidated"]==100+CONSOLIDATE_SECONDS


def test_stale_preview_and_corrupt_profile_never_get_silently_overwritten():
    p=Profile();p.propose("x","first","fact","confirmed")
    row=pending(p)
    with pytest.raises(ValueError,match="προεπισκόπηση"):
        p.answer(row["id"],True,expected="stale")
    p.path.write_text("broken json",encoding="utf-8");before=p.path.read_bytes()
    with pytest.raises(ValueError):
        p.propose("y","second","fact","confirmed")
    assert p.path.read_bytes()==before


def test_global_memory_retains_old_notes_past_the_old_excerpt_limit():
    first="αρχική χρήσιμη πληροφορία"
    memory.append("memory",first)
    for n in range(20):
        memory.append("memory",str(n)+" "+("κείμενο "*70))
    assert first in memory.read("memory") and len(memory.read("memory"))>memory.LIMIT


def test_capture_draft_reuse_and_refine_keeps_one_recipe_and_full_previous_version(tmp_path):
    s=Skills(tmp_path)
    with pytest.raises(ValueError,match="επανάληψη"):
        s.propose("project-check","Ελέγχει το project",STEPS)
    workflow=repeated(s)
    s.propose("project-check","Ελέγχει το project",STEPS,workflow)
    assert "project-check" not in {r["name"] for r in s.catalog()}
    row=s.view()["proposals"][0]
    assert len(STEPS)==3 and all(step in row["body"] for step in STEPS)
    s.answer(row["id"],True,expected=row["signature"])
    original=s.read("project-check")
    revised=STEPS+["Δώσε την τεκμηριωμένη αναφορά."]
    s.propose("project-check","Ελέγχει και αναφέρει",revised)
    row=s.view()["proposals"][0];s.answer(row["id"],True)
    assert s.read("project-check").endswith("4. Δώσε την τεκμηριωμένη αναφορά.\n")
    assert len(list(s.user_root.glob("project-check*.md")))==1
    assert s._load()["history"][-1]["before"] in original


def test_skills_reject_duplicate_body_stale_target_wrong_project_and_bad_paths(tmp_path):
    s=Skills(tmp_path);workflow=repeated(s)
    s.propose("recipe-one","Περιγραφή",STEPS,workflow)
    row=s.view()["proposals"][0]
    other=tmp_path/"other";other.mkdir()
    with pytest.raises(ValueError,match="project"):
        Skills(other).answer(row["id"],True)
    s.answer(row["id"],True)
    assert "recipe-one" in s.propose("recipe-two","Άλλη περιγραφή",STEPS,workflow)
    assert not s.view()["proposals"]
    s.read("recipe-one")
    s.propose("recipe-one","Βελτίωση",STEPS+["Αναφορά."])
    row=s.view()["proposals"][0]
    target=Path(row["target"]);target.write_text("owner edit",encoding="utf-8")
    with pytest.raises(ValueError,match="άλλαξε"):
        s.answer(row["id"],True)
    assert target.read_text(encoding="utf-8")=="owner edit"
    for bad in ["../escape","x/y","UpperCase","a"]:
        with pytest.raises(ValueError):s.propose(bad,"Περιγραφή",STEPS,workflow)


def test_skill_rejection_and_failed_write_preserve_pending_data(tmp_path,monkeypatch):
    s=Skills(tmp_path);workflow=repeated(s)
    s.propose("recipe-one","Περιγραφή",STEPS,workflow)
    row=s.view()["proposals"][0];before=s.path.read_bytes()
    def fail(*a,**k):raise OSError("synthetic write failure")
    monkeypatch.setattr(skills_module,"atomic_bytes",fail)
    with pytest.raises(OSError):s.answer(row["id"],True)
    assert s.path.read_bytes()==before and not Path(row["target"]).exists()
    s.answer(row["id"],False)
    assert not s.view()["proposals"] and not Path(row["target"]).exists()


def test_large_skill_catalog_is_bounded_but_every_recipe_is_discoverable(tmp_path):
    folder=tmp_path/"skills";folder.mkdir()
    for n in range(60):
        (folder/f"recipe-{n:02}.md").write_text("---\nname: recipe\ndescription: "+("details "*50)+"\n---\nprivate instructions")
    s=Skills(tmp_path)
    prompt=s.prompt()
    assert len(prompt)<=4000 and "list_skills" in prompt and "private instructions" not in prompt
    page=s.listing(query="recipe-59")
    assert page["skills"][0]["name"]=="recipe-59"
    assert "private instructions" in s.read("recipe-59")


def test_readonly_mode_has_no_learning_write_or_approval_tools(tmp_path):
    agent=Akatikito(str(tmp_path),Config(auto_mode=False),ScriptedModel([]))
    agent.set_mode("ask");allowed=set(modes.allowed_tools("ask"))
    assert {"list_memory","list_skills"}<=allowed
    assert not {"propose_memory","propose_skill","approve_memory","approve_skill"} & allowed
    result,_=agent._execute_call({"name":"propose_memory","arguments":{"topic":"x","text":"y","kind":"fact","status":"confirmed"}},allowed)
    assert "δεν επιτρέπεται" in result and not Profile().path.exists()


def test_owner_api_requires_project_and_exact_preview_and_never_calls_a_model(tmp_path,monkeypatch):
    settings=Settings();settings.project=str(tmp_path)
    monkeypatch.setattr(Settings,"load",lambda:settings)
    p=Profile();p.propose("γλώσσα","Ελληνικά","fact","confirmed")
    state=AppState();view=state.learning_view();row=view["profile"]["proposals"][0]
    assert state.learning_action({"action":"memory_approve","id":row["id"],"project":"different","review":row["review_hash"]})["error"]
    assert state.learning_action({"action":"memory_approve","id":row["id"],"project":view["project"]})["error"]
    result=state.learning_action({"action":"memory_approve","id":row["id"],"project":view["project"],"review":row["review_hash"],"text":"Μόνο ελληνικά"})
    assert result["ok"] and "Μόνο ελληνικά" in p.prompt() and state.agent is None


def test_verified_real_local_checks_capture_repeated_workflow_with_scripted_replies(tmp_path):
    (tmp_path/"value.py").write_text("VALUE=0\n")
    (tmp_path/"test_value.py").write_text("from value import VALUE\ndef test_value(): assert VALUE>=1\n")
    verify='"'+sys.executable+'" -B -m pytest -q -p no:cacheprovider'
    replies=[]
    for number in [1,2]:
        replies.extend([
            {"tool_calls":[tool_call("read_file",path="value.py")]},
            {"tool_calls":[tool_call("edit_file",path="value.py",edits=[{"search":f"VALUE={number-1}","replace":f"VALUE={number}"}])]},
            {"tool_calls":[tool_call("run_command",command=verify)]},
            {"content":"Ολοκληρώθηκε."},
        ])
    model=ScriptedModel(replies)
    agent=Akatikito(str(tmp_path),Config(auto_mode=False,max_steps=6,verify_command=verify),model)
    agent.set_mode("code")
    agent.chat("Άλλαξε το VALUE σε 1 και έλεγξέ το.")
    assert not Skills(tmp_path).hints()
    agent.chat("Άλλαξε το VALUE σε 2 και έλεγξέ το.")
    hints=Skills(tmp_path).hints()
    assert hints and hints[0]["count"]==2 and len(hints[0]["tools"])>=3
    assert model.calls==8 and agent._task_checks>=1


def test_large_profile_retains_relevant_preference_and_exact_archive(tmp_path):
    p=Profile()
    data=p.load()
    data["entries"]=[{"id":str(i),"topic":f"στοιχείο {i}","text":"γενική πληροφορία "*20,
                     "kind":"fact","status":"confirmed"} for i in range(60)]
    data["entries"].append({"id":"important","topic":"RELEVANT_KEY","text":"KEEP_THIS_PREFERENCE",
                            "kind":"fact","status":"confirmed"})
    p._save(data)
    agent=Akatikito(str(tmp_path),Config(auto_mode=False),ScriptedModel([{"content":"ok"}]))
    agent.set_mode("code");agent.chat("Εξήγησε το RELEVANT_KEY")
    notes=[m["content"] for m in agent.messages if m.get("_context_key")=="προτιμήσεις χρήστη"]
    assert notes and "KEEP_THIS_PREFERENCE" in notes[-1]
    assert len(notes[-1])<5000 and "recall_context id=" in notes[-1]
    import re
    key=re.search(r"recall_context id=([a-f0-9]+)",notes[-1]).group(1)
    assert agent.context._load(key)==p.prompt()


def test_capture_recognizes_repetition_despite_different_read_search_batches(tmp_path):
    s=Skills(tmp_path)
    first=events()
    second=events()
    second.insert(1,SimpleNamespace(kind="tool_result",data={"name":"search_project","result":"found"}))
    s.capture(first,"Διόρθωσε το πρώτο helper")
    s.capture(second,"Διόρθωσε το δεύτερο helper")
    hints=s.hints()
    assert hints and hints[0]["count"]==2 and len(hints[0]["examples"])==2


def test_local_tests_block_external_connections_without_contacting_them():
    import socket
    sock=socket.socket()
    try:
        with pytest.raises(RuntimeError,match="External network disabled"):
            sock.connect(("203.0.113.1",443))
    finally:
        sock.close()


def test_identical_values_for_different_topics_are_not_lost():
    p=Profile()
    p.propose("Έχει αυτοκίνητο","Ναι","fact","confirmed");approve(p)
    p.propose("Έχει άδεια οδήγησης","Ναι","fact","confirmed");approve(p)
    assert len(p.load()["entries"])==2
    assert p.consolidate()["merged"]==0


def test_invalid_proposal_can_be_corrected_in_the_same_turn(tmp_path):
    a=Akatikito(str(tmp_path),Config(auto_mode=False),ScriptedModel([]))
    a.set_mode("code");allowed=set(modes.allowed_tools("code"))
    args={"topic":"αιτία","text":"Ίσως υπάρχει race","kind":"fact","status":"unconfirmed"}
    result,_=a._execute_call({"name":"propose_memory","arguments":args},allowed)
    assert result.startswith("ΣΦΑΛΜΑ")
    args["kind"]="belief"
    result,_=a._execute_call({"name":"propose_memory","arguments":args},allowed)
    assert result.startswith("ΠΡΟΤΑΣΗ ΜΝΗΜΗΣ")
    assert len(Profile().load()["proposals"])==1


def test_malformed_structured_history_is_visible_and_preserved(tmp_path):
    p=Profile();p.path.parent.mkdir()
    data={"version":1,"entries":[],"proposals":[],"revisions":["broken"],"legacy":{}}
    p.path.write_text(json.dumps(data),encoding="utf-8");before=p.path.read_bytes()
    with pytest.raises(ValueError):p.prompt()
    assert p.path.read_bytes()==before
    s=Skills(tmp_path);data={"version":1,"history":[],"proposals":[],"workflows":[{}],"uses":{}}
    s.path.write_text(json.dumps(data),encoding="utf-8");before=s.path.read_bytes()
    with pytest.raises(ValueError):s.hints()
    assert s.path.read_bytes()==before


def test_metadata_storage_never_follows_a_directory_junction(tmp_path,monkeypatch):
    import os,subprocess
    from akatikito.repo_map import RepoMap,CACHE_NAME
    def link_directory(link,target):
        assert link.absolute().is_relative_to(tmp_path.resolve())
        assert target.resolve().is_relative_to(tmp_path.resolve())
        if os.name=="nt":
            result=subprocess.run(["cmd","/c","mklink","/J",str(link),str(target)],
                                  capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
            assert result.returncode==0,result.stderr
        else:link.symlink_to(target,target_is_directory=True)
    outside=tmp_path/"synthetic-outside";outside.mkdir()
    link_directory(memory.DIR,outside)
    with pytest.raises(ValueError,match="junction"):
        Profile().propose("x","y","fact","confirmed")
    assert not (outside/"profile.json").exists()
    project=tmp_path/"project";project.mkdir()
    (project/"a.py").write_text("def safe(): pass")
    link_directory(project/".agent",outside)
    assert "safe" in RepoMap(project).render(time_limit=10)
    assert not (outside/CACHE_NAME).exists()


def test_workflow_without_inspection_is_not_a_three_stage_learning_example(tmp_path):
    s=Skills(tmp_path)
    invalid=[SimpleNamespace(kind="tool_result",data={"name":name,"result":"ΕΓΙΝΕ"})
             for name in ("edit_file","write_file","run_command")]
    s.capture(invalid);s.capture(invalid)
    assert not s.hints()


def test_document_workflow_uses_real_readback_without_any_shell_command(tmp_path):
    (tmp_path/"input.txt").write_text("Χρήσιμα δεδομένα",encoding="utf-8")
    script=[]
    for n in [1,2]:
        script.extend([
            {"tool_calls":[tool_call("read_file",path="input.txt")]},
            {"tool_calls":[tool_call("write_file",path=f"note{n}.md",content="Ένα ελεγμένο σημείωμα.")]},
            {"tool_calls":[tool_call("read_file",path=f"note{n}.md")]},
            {"content":"Έτοιμο."},
        ])
    a=Akatikito(str(tmp_path),Config(auto_mode=False,max_steps=5),ScriptedModel(script))
    a.set_mode("assistant")
    a.chat("Ετοίμασε σημείωμα από το input.txt.")
    a.chat("Ετοίμασε και δεύτερο σημείωμα από το input.txt.")
    hints=Skills(tmp_path).hints()
    assert hints and hints[0]["count"]==2
    assert not any(e.kind=="tool_call" and e.data["name"]=="run_command" for e in a.events.log)
    assert a.model.calls==8
