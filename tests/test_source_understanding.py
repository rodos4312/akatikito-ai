"""Source understanding and replay regressions for overlapping reads."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akatikito.activity import ActivityGuard
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.context import ContextWindow,encoded
from akatikito.model import ScriptedModel,tool_call
from akatikito.tools import read_file
from akatikito.workspace import Workspace


def read(guard,ws,**args):
    blocked=guard.before('read_file',args)
    result=blocked or read_file(ws,**args)
    progress=guard.record('read_file',args,result)
    return result,progress


def test_anchor_reads_complete_decorated_function_not_tiny_slice(tmp_path):
    source='import os\n\ndef decorator(fn):\n    return fn\n\n@decorator\ndef work(value):\n    if value:\n        return value + 1\n    return 0\n\ndef unrelated():\n    return 99\n'
    (tmp_path/'app.py').write_text(source,encoding='utf-8');ws=Workspace(tmp_path)
    result=read_file(ws,'app.py',anchor_line=8)
    assert '@decorator' in result and 'def work(value)' in result and 'return 0' in result
    assert 'def unrelated' not in result and 'γραμμές 6-10' in result
    assert 'Πλήρες τμήμα work' in result


def test_outline_has_qualified_names_and_class_anchor(tmp_path):
    (tmp_path/'app.py').write_text('class Worker:\n    def run(self):\n        return 7\n\ndef outside():\n    return 9\n',encoding='utf-8')
    ws=Workspace(tmp_path)
    outline=read_file(ws,'app.py',outline=True)
    assert 'Worker.run' in outline and 'outside' in outline and 'return 7' not in outline
    method=read_file(ws,'app.py',anchor_line=3)
    assert 'def run' in method and 'return 7' in method and 'def outside' not in method


def test_overlapping_ranges_and_alias_paths_do_not_bypass_coverage(tmp_path):
    (tmp_path/'app.py').write_text('\n'.join(f'LINE_{n}=1' for n in range(1,81)),encoding='utf-8')
    ws=Workspace(tmp_path);guard=ActivityGuard(ws)
    for first,last in [(1,40),(41,80),(1,40),(41,80)]:
        result,_=read(guard,ws,path='app.py',start=first,end=last)
        assert not result.startswith('ΑΠΟΤΡΟΠΗ')
    result,_=read(guard,ws,path='./app.py',start=25,end=65)
    assert result.startswith('ΑΠΟΤΡΟΠΗ') and 'LINE_25' in result and 'LINE_65' in result
    assert 'ίδιας έκδοσης' in result


def test_clipped_middle_is_still_unread(tmp_path):
    (tmp_path/'huge.txt').write_text('\n'.join(f'{n} '+('x'*500) for n in range(1,61)),encoding='utf-8')
    ws=Workspace(tmp_path);guard=ActivityGuard(ws)
    for _ in range(2):read(guard,ws,path='huge.txt')
    assert not guard.before('read_file',{'path':'huge.txt','start':25,'end':35})
    result,progress=read(guard,ws,path='huge.txt',start=25,end=35)
    assert progress and not result.startswith('ΑΠΟΤΡΟΠΗ')


def test_rereading_known_text_does_not_reset_nonprogress(tmp_path):
    (tmp_path/'app.py').write_text('value=7\n',encoding='utf-8')
    ws=Workspace(tmp_path);guard=ActivityGuard(ws)
    _,progress=read(guard,ws,path='app.py');assert progress
    guard.block('prior no progress')
    _,progress=read(guard,ws,path='app.py')
    assert not progress and guard.blocked==1


def test_only_changed_file_invalidates_coverage(tmp_path):
    file=tmp_path/'app.py';file.write_text('value=7\n',encoding='utf-8')
    ws=Workspace(tmp_path);guard=ActivityGuard(ws)
    for _ in range(2):read(guard,ws,path='app.py')
    guard.record('write_file',{'path':'other.py'},'ΕΓΙΝΕ: γράφτηκε νέο αρχείο other.py')
    assert guard.before('read_file',{'path':'app.py'}).startswith('ΑΠΟΤΡΟΠΗ')
    file.write_text('value=8877\n',encoding='utf-8')
    assert not guard.before('read_file',{'path':'app.py'})
    result,progress=read(guard,ws,path='app.py')
    assert progress and '8877' in result


def test_source_map_survives_history_condensation_and_reflects_external_change(tmp_path):
    path=tmp_path/'app.py';path.write_text('def work():\n    return 42\n',encoding='utf-8')
    ws=Workspace(tmp_path);guard=ActivityGuard(ws);read(guard,ws,path='app.py')
    context=ContextWindow(ws,max_chars=4000)
    messages=[{'role':'system','content':'rules'},{'role':'user','content':'implement','_origin':'owner'}]
    for n in range(20):messages.append({'role':'assistant','content':'old output '+str(n)*1000})
    view=context.prepare(messages,activity=guard)
    assert 'ΧΑΡΤΗΣ ΤΡΕΧΟΝΤΟΣ ΚΩΔΙΚΑ' in encoded(view) and '1-2 | work' in encoded(view)
    path.write_text('def renamed():\n    return 8877\n',encoding='utf-8')
    state=guard.source_state()
    assert 'renamed' in state and 'Έχουν διαβαστεί 0 γραμμές' in state


def test_repeated_read_pattern_stops_in_five_model_calls_with_useful_report(tmp_path):
    (tmp_path/'app.py').write_text('\n'.join(f'line_{n}=1' for n in range(1,81)),encoding='utf-8')
    ranges=[(1,80),(1,80),(2,79),(3,78),(4,77)]+[(n,80) for n in range(5,65)]
    script=[{'tool_calls':[tool_call('read_file',path='app.py',start=a,end=b)]} for a,b in ranges]
    model=ScriptedModel(script);agent=Akatikito(str(tmp_path),Config(auto_mode=False,max_steps=100),model)
    answer=agent.chat('implement the feature')
    assert model.calls==5 and agent.activity.stalled
    assert 'Δεν ολοκληρώθηκε' in answer and 'Δεν έγινε αλλαγή αρχείου' in answer
    assert 'Δεν εκτελέστηκε έλεγχος' in answer


def test_stop_reports_actual_changes_and_check_results(tmp_path):
    (tmp_path/'app.py').write_text('value=7\n',encoding='utf-8')
    script=[{'tool_calls':[tool_call('write_file',path='new.py',content='print(42)\n')]}]
    script += [{'tool_calls':[tool_call('read_file',path='app.py')]} for _ in range(5)]
    command=f'"{sys.executable}" -B new.py'
    agent=Akatikito(str(tmp_path),Config(auto_mode=False,max_steps=100,verify_command=command),ScriptedModel(script))
    answer=agent.chat('implement')
    assert 'Δεν ολοκληρώθηκε' in answer and 'new.py' in answer and 'Ο τελευταίος έλεγχος πέρασε' in answer


def test_independent_reads_can_share_one_model_round(tmp_path):
    for name in ('a.py','b.py','c.py'):(tmp_path/name).write_text('value=7\n',encoding='utf-8')
    model=ScriptedModel([{'tool_calls':[tool_call('read_file',path=name) for name in ('a.py','b.py','c.py')]},{'content':'done'}])
    agent=Akatikito(str(tmp_path),Config(auto_mode=False),model)
    assert agent.chat('inspect')=='done' and model.calls==2
    assert len([e for e in agent.events.log if e.kind=='tool_result'])==3


def test_long_default_read_exposes_continuation_and_source_map(tmp_path):
    source='def first():\n    return 1\n'+'\n'*450+'def last():\n    return 2\n'
    (tmp_path/'app.py').write_text(source,encoding='utf-8')
    result=read_file(Workspace(tmp_path),'app.py')
    assert 'start=401' in result and 'last' in result and 'χάρτης ορισμών' in result


def test_outline_is_not_accepted_as_source_evidence_for_repair(tmp_path):
    (tmp_path/'app.py').write_text('def broken():\n    return 0\n',encoding='utf-8')
    agent=Akatikito(str(tmp_path),Config(auto_mode=False),ScriptedModel([]))
    agent.repair.pending=True
    def execute(name,**args):
        import json
        return agent._execute_call({'name':name,'arguments':json.dumps(args)},set())[0]
    result=execute('read_file',path='app.py',outline=True)
    assert 'χάρτης' in result and not agent.repair.read_paths
    rejected=execute('record_diagnosis',cause='wrong value',evidence='expected 1',paths=['app.py'])
    assert rejected.startswith('ΑΠΟΡΡΙΨΗ')
    execute('read_file',path='app.py',anchor_line=2)
    assert 'app.py' in agent.repair.read_paths
    accepted=execute('record_diagnosis',cause='wrong return',evidence='return 0 while expected 1',paths=['app.py'])
    assert accepted.startswith('ΔΙΑΓΝΩΣΗ')
