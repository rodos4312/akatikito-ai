"""Public behavior regressions. ENGINE_TEST_ROOT can audit an unchanged reference."""
import copy
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0,os.getenv("ENGINE_TEST_ROOT",str(Path(__file__).resolve().parents[1])))
from akatikito import memory,editing,tools
from akatikito.agent import Akatikito
from akatikito.app_api import AppState
from akatikito.config import Config
from akatikito.context import ContextWindow
from akatikito.model import ScriptedModel,tool_call
from akatikito.repo_map import RepoMap
from akatikito.sessions import SessionStore
from akatikito.workspace import Workspace


@pytest.fixture(autouse=True)
def isolate(tmp_path,monkeypatch):
    monkeypatch.setattr(memory,"DIR",tmp_path/"private-memory")


def files(root):
    (root/"a.py").write_text("X = 1\n")
    (root/"b.py").write_text("Y = 2\n")
    return [{"path":"a.py","edits":[{"search":"X = 1","replace":"X = 10"}]},
            {"path":"b.py","edits":[{"search":"Y = 2","replace":"Y = 20"}]}]


def fail_second_and_rollback(monkeypatch,root,concurrent=False):
    real=editing.os.replace
    counts={}
    def broken(source,target):
        target=Path(target);counts[target]=counts.get(target,0)+1
        if target==root/"b.py":
            if concurrent:(root/"a.py").write_text("OWNER CHANGE\n")
            raise OSError("synthetic second write failure")
        if target==root/"a.py" and counts[target]>1:
            raise OSError("synthetic rollback failure")
        return real(source,target)
    monkeypatch.setattr(editing.os,"replace",broken)


def row(text,path):
    return next((line for line in text.splitlines() if line.startswith((path+":",path+" ←")) or line==path),"")


def test_batch_agent_tracks_each_file_and_really_runs_local_pytest(tmp_path):
    batch=files(tmp_path)
    (tmp_path/"test_values.py").write_text("from a import X\nfrom b import Y\ndef test_values(): assert (X,Y)==(10,20)\n")
    verify='"'+sys.executable+'" -B -m pytest -q -p no:cacheprovider'
    model=ScriptedModel([
        {"tool_calls":[tool_call("read_file",path="a.py"),tool_call("read_file",path="b.py")]},
        {"tool_calls":[tool_call("edit_file",files=batch)]},
        {"content":"Ολοκληρώθηκε."},
    ])
    a=Akatikito(str(tmp_path),Config(auto_mode=False,max_steps=5,verify_command=verify,checkpoints=True),model)
    a.set_mode("code")
    state=AppState();state.agent=a;state.store=SessionStore(tmp_path)
    a.events.subscribe(state.emit_event)
    answer=a.chat("Διόρθωσε τα a.py και b.py και έλεγξέ τα.")
    assert "KeyError" not in json.dumps([e.data for e in a.events.log],ensure_ascii=False)
    assert a._task_writes=={"a.py","b.py"} and a._task_checks>=1
    assert a.task.status=="complete" and model.calls==3
    assert {f["path"] for f in state.files}=={"a.py","b.py"}
    state._save_session()
    assert {f["path"] for f in state.store.load(state.session_id)["files"]}=={"a.py","b.py"}
    assert (tmp_path/"a.py").read_text()=="X = 10\n"
    assert (tmp_path/"b.py").read_text()=="Y = 20\n"
    restored=state.checkpoint_action(a.last_checkpoint,True)
    assert not restored.get("error")
    assert (tmp_path/"a.py").read_text()=="X = 1\n"
    assert (tmp_path/"b.py").read_text()=="Y = 2\n"


def test_failed_rollback_never_claims_all_files_were_restored(tmp_path,monkeypatch):
    batch=files(tmp_path)
    original=(tmp_path/"a.py").read_bytes()
    fail_second_and_rollback(monkeypatch,tmp_path)
    result=tools.edit_file(Workspace(tmp_path),files=batch)
    assert "ΑΤΕΛΗΣ ΕΠΑΝΑΦΟΡΑ" in result
    assert "οι προηγούμενες αλλαγές αναιρέθηκαν" not in result
    assert set(getattr(result,"changed_paths",()))=={"a.py"}
    assert getattr(result,"recovery_required",False)
    assert (tmp_path/"a.py").read_text()=="X = 10\n"
    assert (tmp_path/"b.py").read_text()=="Y = 2\n"
    assert any(p.is_file() and p.read_bytes()==original for p in tmp_path.rglob("*") if p.name!="a.py")


def test_agent_stops_after_partial_rollback_and_keeps_actual_paths(tmp_path,monkeypatch):
    batch=files(tmp_path);fail_second_and_rollback(monkeypatch,tmp_path)
    model=ScriptedModel([
        {"tool_calls":[tool_call("read_file",path="a.py"),tool_call("read_file",path="b.py")]},
        {"tool_calls":[tool_call("edit_file",files=batch),tool_call("write_file",path="never.txt",content="no")]},
        {"content":"MUST NOT BE USED"},
    ])
    a=Akatikito(str(tmp_path),Config(auto_mode=False,max_steps=6,verify_command="verify"),model)
    a.set_mode("code")
    monkeypatch.setattr(a,"_verify",lambda:pytest.fail("A broken rollback must stop before verification"))
    answer=a.chat("Διόρθωσε τα δύο αρχεία.")
    assert "ΑΤΕΛΗΣ ΕΠΑΝΑΦΟΡΑ" in answer
    assert "Δεν έγινε αλλαγή αρχείου" not in answer
    assert "Αλλαγμένα ή μη επαληθευμένα αρχεία μετά την αποτυχία: a.py" in answer
    assert not (tmp_path/"never.txt").exists() and model.calls==2
    assert a._task_writes=={"a.py"}


def test_rollback_never_overwrites_a_concurrent_owner_edit(tmp_path,monkeypatch):
    batch=files(tmp_path);fail_second_and_rollback(monkeypatch,tmp_path,concurrent=True)
    result=tools.edit_file(Workspace(tmp_path),files=batch)
    assert "ΑΤΕΛΗΣ ΕΠΑΝΑΦΟΡΑ" in result
    assert (tmp_path/"a.py").read_text()=="OWNER CHANGE\n"


def test_dependency_map_does_not_confuse_stdlib_with_unrelated_local_file(tmp_path):
    (tmp_path/"tools").mkdir()
    (tmp_path/"tools"/"parse.py").write_text("def local_parse(): pass\n")
    (tmp_path/"main.py").write_text("from urllib import parse\n")
    text=RepoMap(tmp_path).render(time_limit=10)
    assert "←" not in row(text,"tools/parse.py")


def test_qualified_relative_imports_and_package_init_are_resolved(tmp_path):
    for pkg in ["pkg","other"]:(tmp_path/pkg).mkdir()
    (tmp_path/"pkg"/"__init__.py").write_text("")
    (tmp_path/"pkg"/"core.py").write_text("def work(): pass\n")
    (tmp_path/"other"/"core.py").write_text("def unrelated(): pass\n")
    (tmp_path/"pkg"/"client.py").write_text("from .core import work\n")
    (tmp_path/"main.py").write_text("from pkg import core\n")
    text=RepoMap(tmp_path).render(budget=6000,time_limit=10)
    target=row(text,"pkg/core.py")
    assert "main.py" in target and "pkg/client.py" in target
    assert "← main.py" in row(text,"pkg/__init__.py")
    assert "←" not in row(text,"other/core.py")


def test_imported_attribute_is_not_a_same_named_module(tmp_path):
    (tmp_path/"pkg").mkdir();(tmp_path/"pkg"/"__init__.py").write_text("")
    (tmp_path/"pkg"/"holder.py").write_text("core=1\n")
    (tmp_path/"core.py").write_text("def unrelated(): pass\n")
    (tmp_path/"main.py").write_text("from pkg.holder import core\n")
    text=RepoMap(tmp_path).render(time_limit=10)
    assert "← main.py" in row(text,"pkg/holder.py")
    assert "←" not in row(text,"core.py")


def test_javascript_relative_import_is_not_matched_by_basename(tmp_path):
    for name in ["web","other"]:(tmp_path/name).mkdir()
    (tmp_path/"web"/"core.js").write_text("export const value=1;\n")
    (tmp_path/"other"/"core.js").write_text("export const other=2;\n")
    (tmp_path/"web"/"app.js").write_text("import {value} from './core.js';\n")
    text=RepoMap(tmp_path).render(time_limit=10)
    assert "← web/app.js" in row(text,"web/core.js")
    assert "←" not in row(text,"other/core.js")


def test_runtime_search_limit_is_not_the_dataclass_default(tmp_path):
    (tmp_path/"a.py").write_text("VALUE=1\n")
    a=Akatikito(str(tmp_path),Config(auto_mode=False),ScriptedModel([]))
    a.config.search_time_limit=-1
    assert "ΜΕΡΙΚΟ" in tools.dispatch(a.ws,"search_project",{"keyword":"VALUE"})
    a.config.search_time_limit=2
    assert "a.py" in tools.dispatch(a.ws,"search_project",{"keyword":"VALUE"})


def test_adversarial_regex_has_a_hard_deadline(tmp_path):
    (tmp_path/"a.txt").write_text("a"*60000+"!")
    started=time.monotonic()
    result=tools.search_project(Workspace(tmp_path),"(a+)+$",regex=True,time_limit=.4)
    assert time.monotonic()-started<4
    assert "ΜΕΡΙΚΟ" in result


def test_batch_syntax_is_checked_once_per_file(tmp_path,monkeypatch):
    for name in ["a.json","b.json"]:(tmp_path/name).write_text("{}")
    actual=editing.check_syntax;seen=[]
    def check(path,text):seen.append(Path(path).name);return actual(path,text)
    monkeypatch.setattr(editing,"check_syntax",check)
    out=tools.edit_file(Workspace(tmp_path),files=[
        {"path":name,"edits":[{"search":"{}","replace":'{"valid":1}'}]} for name in ["a.json","b.json"]])
    assert out.startswith("ΕΓΙΝΕ") and sorted(seen)==["a.json","b.json"]


def test_batch_respects_assistant_and_architect_file_permissions(tmp_path):
    (tmp_path/"PLAN.md").write_text("old")
    (tmp_path/"a.py").write_text("X=1")
    a=Akatikito(str(tmp_path),Config(auto_mode=False),ScriptedModel([]));a.set_mode("architect")
    request={"files":[{"path":"PLAN.md","edits":[{"search":"old","replace":"new"}]},
                      {"path":"a.py","edits":[{"search":"X=1","replace":"X=2"}]}]}
    result,_=a._execute_call({"name":"edit_file","arguments":request},a.allowed_tools())
    assert result.startswith("ΑΠΟΡΡΙΨΗ") and (tmp_path/"PLAN.md").read_text()=="old"
    a.set_mode("assistant")
    result,_=a._execute_call({"name":"edit_file","arguments":request},a.allowed_tools())
    assert result.startswith("ΑΠΟΡΡΙΨΗ") and (tmp_path/"a.py").read_text()=="X=1"


def test_compaction_marks_reads_of_every_batch_file_as_stale(tmp_path,monkeypatch):
    ws=Workspace(tmp_path);ctx=ContextWindow(ws,max_chars=1000,recent_groups=0)
    calls=[{"id":"ra","type":"function","function":{"name":"read_file","arguments":json.dumps({"path":"a.py"})}},
           {"id":"rb","type":"function","function":{"name":"read_file","arguments":json.dumps({"path":"b.py"})}}]
    write={"id":"w","type":"function","function":{"name":"edit_file","arguments":json.dumps({"files":[{"path":"a.py"},{"path":"b.py"}]})}}
    messages=[{"role":"user","content":"goal"},{"role":"assistant","content":"","tool_calls":calls},
              {"role":"tool","tool_call_id":"ra","content":"OLD_A"*300},
              {"role":"tool","tool_call_id":"rb","content":"OLD_B"*300},
              {"role":"assistant","content":"","tool_calls":[write]},
              {"role":"tool","tool_call_id":"w","content":"ΕΓΙΝΕ: changed","_written_paths":["a.py","b.py"]}]
    original=copy.deepcopy(messages);seen=[]
    real=ctx.summarize
    def summarize(group,stale=()):
        seen.extend(stale)
        return real(group,stale)
    monkeypatch.setattr(ctx,"summarize",summarize)
    ctx.prepare(messages)
    assert {"a.py","b.py"}<=set(seen) and messages==original


def test_staging_failure_leaves_no_source_changes_or_temporary_files(tmp_path,monkeypatch):
    batch=files(tmp_path)
    def fail(*a,**k):raise OSError("synthetic fsync failure")
    monkeypatch.setattr(editing.os,"fsync",fail)
    out=tools.edit_file(Workspace(tmp_path),files=batch)
    assert out.startswith("ΣΦΑΛΜΑ")
    assert (tmp_path/"a.py").read_text()=="X = 1\n" and (tmp_path/"b.py").read_text()=="Y = 2\n"
    assert not list(tmp_path.glob(".*.tmp"))


def test_successful_rollback_reports_no_remaining_changes(tmp_path,monkeypatch):
    batch=files(tmp_path);real=editing.os.replace
    def fail(source,target):
        if Path(target)==tmp_path/"b.py":raise OSError("synthetic second failure")
        return real(source,target)
    monkeypatch.setattr(editing.os,"replace",fail)
    result=tools.edit_file(Workspace(tmp_path),files=batch)
    assert result.startswith("ΣΦΑΛΜΑ") and "Δεν εφαρμόστηκε τίποτα" in result
    assert not getattr(result,"changed_paths",()) and not getattr(result,"recovery_required",False)
    assert (tmp_path/"a.py").read_text()=="X = 1\n" and (tmp_path/"b.py").read_text()=="Y = 2\n"


def test_assistant_batch_document_readback_still_counts_as_verified_workflow(tmp_path):
    (tmp_path/"input.txt").write_text("source")
    for name in ["one.md","two.md"]:(tmp_path/name).write_text("old")
    request=[{"path":p,"edits":[{"search":"old","replace":"new"}]} for p in ["one.md","two.md"]]
    model=ScriptedModel([
        {"tool_calls":[tool_call("read_file",path="input.txt")]},
        {"tool_calls":[tool_call("edit_file",files=request)]},
        {"tool_calls":[tool_call("read_file",path="one.md"),tool_call("read_file",path="two.md")]},
        {"content":"Έτοιμο."},
    ])
    a=Akatikito(str(tmp_path),Config(auto_mode=False,max_steps=6),model);a.set_mode("assistant")
    a.chat("Ενημέρωσε τα δύο σημειώματα.")
    from akatikito.skills import Skills
    workflows=Skills(tmp_path)._load()["workflows"]
    assert workflows and workflows[0]["count"]==1
    assert a._task_writes=={"one.md","two.md"}


def test_subtask_batch_inherits_guards_and_reports_files_to_ui(tmp_path):
    batch=files(tmp_path)
    model=ScriptedModel([
        {"tool_calls":[tool_call("run_subtask",task="Διόρθωσε τα a.py και b.py.",mode="code")]},
        {"tool_calls":[tool_call("read_file",path="a.py"),tool_call("read_file",path="b.py")]},
        {"tool_calls":[tool_call("edit_file",files=batch)]},
        {"content":"Έτοιμα."},{"content":"Ολοκληρώθηκε."},
    ])
    a=Akatikito(str(tmp_path),Config(auto_mode=False,max_steps=6),model);a.set_mode("code")
    state=AppState();state.agent=a;a.events.subscribe(state.emit_event)
    calls=[];a.ws.write_guard=lambda path,old,new:calls.append(path.name)
    a.chat("Υλοποίησε τη διόρθωση με υποεργασία.")
    assert calls==["a.py","b.py"] and a._task_writes=={"a.py","b.py"}
    assert {f["path"] for f in state.files}=={"a.py","b.py"}


def test_write_that_completes_before_reporting_failure_is_also_recovered(tmp_path,monkeypatch):
    batch=files(tmp_path);actual=editing.os.replace
    def uncertain(source,target):
        result=actual(source,target)
        if Path(target)==tmp_path/"b.py":
            raise OSError("synthetic uncertain return after rename")
        return result
    monkeypatch.setattr(editing.os,"replace",uncertain)
    result=tools.edit_file(Workspace(tmp_path),files=batch)
    assert result.startswith("ΣΦΑΛΜΑ") and "Δεν εφαρμόστηκε τίποτα" in result
    assert (tmp_path/"a.py").read_text()=="X = 1\n" and (tmp_path/"b.py").read_text()=="Y = 2\n"
    assert not getattr(result,"recovery_required",False)


def test_postwrite_tracking_failure_preserves_actual_mutation_receipt(tmp_path,monkeypatch):
    batch=files(tmp_path)
    a=Akatikito(str(tmp_path),Config(auto_mode=False),ScriptedModel([]));a.set_mode("code")
    def fail(path):raise OSError("synthetic tracking failure")
    monkeypatch.setattr(a.repair,"note_change",fail)
    out,_=a._execute_call({"name":"edit_file","arguments":{"files":batch}},a.allowed_tools())
    assert "ΜΕΤΑ ΤΗΝ ΕΓΓΡΑΦΗ" in out
    assert set(getattr(out,"changed_paths",()))=={"a.py","b.py"}
    assert getattr(out,"recovery_required",False)
    assert (tmp_path/"a.py").read_text()=="X = 10\n" and (tmp_path/"b.py").read_text()=="Y = 20\n"


def test_rejected_http_request_with_delayed_body_still_delivers_403():
    import http.client,socket,threading
    from akatikito.app_api import start_server
    state=AppState();_,server=start_server(state)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    body=b'{"text":"must never execute"}'
    try:
        for _ in range(5):
            with socket.create_connection(server.server_address,timeout=3) as sock:
                headers=(f"POST /api/send HTTP/1.0\r\nHost: 127.0.0.1:{server.server_address[1]}\r\n"
                         f"X-Akatikito-Token: {server.auth_token}\r\nContent-Type: text/plain\r\n"
                         f"Content-Length: {len(body)}\r\n\r\n").encode("ascii")
                sock.sendall(headers);time.sleep(.05);sock.sendall(body)
                response=http.client.HTTPResponse(sock);response.begin()
                assert response.status==403;response.read();response.close()
        assert not state.transcript and state.agent is None
    finally:
        server.shutdown();server.server_close();thread.join(5)


def test_rejected_http_body_never_waits_unboundedly():
    import http.client,socket,threading
    from akatikito.app_api import start_server
    state=AppState();_,server=start_server(state)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with socket.create_connection(server.server_address,timeout=3) as sock:
            headers=(f"POST /api/send HTTP/1.0\r\nHost: 127.0.0.1:{server.server_address[1]}\r\n"
                     "Content-Type: text/plain\r\nContent-Length: 10000\r\n\r\n").encode("ascii")
            started=time.monotonic();sock.sendall(headers)
            response=http.client.HTTPResponse(sock);response.begin()
            assert response.status==403 and time.monotonic()-started<2
            response.read();response.close()
    finally:
        server.shutdown();server.server_close();thread.join(5)
