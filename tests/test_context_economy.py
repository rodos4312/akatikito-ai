"""Test exact retrieval, protected instructions, valid tool protocol and actual request reduction."""
from __future__ import annotations
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.context import ContextWindow, encoded
from akatikito.model import ScriptedModel, tool_call
from akatikito.repair import RepairState
from akatikito.sessions import SessionStore
from akatikito.tools import run_command
from akatikito.workspace import Workspace


def exchange(n,name,args,result):
    key=f'call-{n}'
    return [{'role':'assistant','content':'','tool_calls':[{'id':key,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}, {'role':'tool','tool_call_id':key,'content':result}]


def history():
    messages=[{'role':'system','content':'system rules'}, {'role':'user','content':'Μόνιμος περιορισμός: διατήρησε το port 8877 και τα δεδομένα.', '_origin':'owner'}]
    for n in range(12):
        text='returncode=0\n'+'\n'.join(f'log {n}-{i}: completed operation and unchanged fixture data' for i in range(650))
        if n==2:text+='\nRARE_EXACT_VALUE=κλειδί-01928\n'
        messages+=exchange(n,'run_command',{'command':f'check-{n}'},text)
        messages.append({'role':'user','content':'[ΑΥΤΟΜΑΤΟΣ ΕΛΕΓΧΟΣ] πέρασε=True\n'+text,'_origin':'internal'})
    messages += [{'role':'assistant','content':'Συμφωνημένο σχέδιο: διόρθωση και πλήρης έλεγχος.'}, {'role':'user','content':'Προχώρα χωρίς να αλλάξεις τα tests.','_origin':'owner'}]
    return messages


def ids(view):
    return set(re.findall(r'id=([a-f0-9]{20})',encoded(view)))


def test_large_request_reduced_with_exact_archive_and_no_history_mutation(tmp_path):
    context=ContextWindow(Workspace(tmp_path),max_chars=18000)
    original=history();before=copy.deepcopy(original)
    view=context.prepare(original)
    assert original==before
    assert len(encoded(view)) < len(encoded(original))*.25
    assert any(m.get('content')==before[1]['content'] for m in view)
    assert any(m.get('content')==before[-1]['content'] for m in view)
    assert any(m.get('content')==before[-2]['content'] for m in view)
    found=False
    for key in ids(view):
        recalled=context.recall(key,query='RARE_EXACT_VALUE')
        if recalled.get('found') is not False:
            assert 'RARE_EXACT_VALUE=κλειδί-01928' in recalled['text'];found=True;break
    assert found
    ContextWindow.groups(view)  # Completed tool exchanges remain valid.
    assert all(not any(k.startswith('_') for k in m) for m in view)


def test_exact_paging_survives_new_session_and_integrity_checks(tmp_path):
    context=ContextWindow(Workspace(tmp_path))
    original=('ß Ελληνικά\n'*3000)+'END: 42'
    key=context.store(original)
    again=ContextWindow(Workspace(tmp_path));start=0;chunks=[]
    while True:
        page=again.recall(key,start=start,length=777)
        chunks.append(page['text'])
        if page['next'] is None:break
        start=page['next']
    assert ''.join(chunks)==original
    assert 'END: 42' in again.recall(key,query='END: 42')['text']
    with pytest.raises(ValueError):again.recall('../settings.json')
    p=context.folder/(key+'.json');data=json.loads(p.read_text(encoding='utf-8'));data['value']='corrupt';p.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError):again.recall(key)


def test_active_failure_and_diagnosis_remain_exact(tmp_path):
    ws=Workspace(tmp_path);path=tmp_path/'calc.py';code='def total(a, b):\n    return a - b\n';path.write_text(code,encoding='utf-8')
    repair=RepairState(ws)
    feedback=repair.failed(f'tests/test_sum.py: exit=1\n  File "{path}", line 2, in total\nAssertionError: expected 5, actual -1')
    repair.failure['command']='verify'
    repair.diagnose('η αφαίρεση παράγει -1 αντί για 5','return a - b',['calc.py'])
    messages=history()+[{'role':'user','content':feedback,'_origin':'internal'}]
    view=ContextWindow(ws,max_chars=12000).prepare(messages,repair)
    all_text='\n'.join(str(m.get('content','')) for m in view)
    assert feedback in all_text and 'return a - b' in all_text
    assert 'expected 5, actual -1' in all_text
    assert 'η αφαίρεση παράγει -1 αντί για 5' in all_text and 'verify' in all_text
    assert repair.pending and repair.diagnosis is not None


def test_obsolete_reads_do_not_masquerade_as_current_source(tmp_path):
    messages=[{'role':'system','content':'rules'},{'role':'user','content':'fix source','_origin':'owner'}]
    messages+=exchange('old','read_file',{'path':'./x.py'},'old content\n'*400)
    messages+=exchange('write','edit_file',{'path':'x.py','diff_text':'SEARCH old / REPLACE new'},'ΕΓΙΝΕ: αλλαγή')
    messages+=exchange('new','read_file',{'path':'x.py'},'CURRENT_SOURCE = 42')
    view=ContextWindow(Workspace(tmp_path)).prepare(messages)
    assert not any(m.get('tool_call_id')=='call-old' for m in view)
    assert any(m.get('content')=='CURRENT_SOURCE = 42' for m in view)
    assert any('Παλιά έκδοση' in m.get('content','') for m in view)


def test_rules_runtime_preferences_never_truncated_and_memory_recoverable(tmp_path,monkeypatch):
    rules='CRITICAL_RULE KEEP_THIS\n'*700
    (tmp_path/'RULES.md').write_text(rules,encoding='utf-8')
    memory='\n'.join(f'παλιά πληροφορία {i}: πλήρες περιεχόμενο' for i in range(300))
    agent=Akatikito(str(tmp_path),Config(),ScriptedModel([{'content':'ok'}]))
    agent.ws.memory_path().write_text(memory,encoding='utf-8')
    agent.runtime_instructions='UPGRADE PERMISSIONS MUST REMAIN'
    monkeypatch.setattr('akatikito.agent.global_memory.snapshot',lambda:('general\n'*500,'USER PREFERENCE KEEP'))
    prompt=agent.system_prompt()
    assert rules.strip() in prompt
    assert agent.runtime_instructions in prompt and 'USER PREFERENCE KEEP' in prompt and 'Λειτουργία' in prompt
    recovered=False
    for key in ids([{'content':prompt}]):
        if 'παλιά πληροφορία 17:' in agent.context.recall(key,query='παλιά πληροφορία 17:').get('text',''):recovered=True
    assert recovered
    # Owner constraints take precedence over a soft economy target.
    view=agent.context.prepare([{'role':'system','content':prompt}])
    assert prompt==view[0]['content']


def test_small_request_is_not_enlarged(tmp_path):
    context=ContextWindow(Workspace(tmp_path))
    messages=[{'role':'system','content':'rules'},{'role':'user','content':'hello','_origin':'owner'}]
    view=context.prepare(messages)
    assert len(encoded(view))<=len(encoded(messages))
    assert context.metrics['reduction_percent']==0


def test_raw_history_persisted_not_compacted_view(tmp_path):
    agent=Akatikito(str(tmp_path),Config(),ScriptedModel([{'content':'answer'}]))
    agent.messages=history()
    agent.chat('ερώτηση για παλιά στοιχεία')
    store=SessionStore(tmp_path);key=store.create();store.save(key,messages=agent.messages)
    saved=store.load(key)['messages']
    assert saved==agent.messages
    assert len(encoded(agent.model.seen[-1]))<len(encoded(saved))*.4
    assert 'RARE_EXACT_VALUE' in encoded(saved)


def test_full_failed_command_log_archived_before_clipping(tmp_path):
    ws=Workspace(tmp_path)
    command=f'"{sys.executable}" -B -c "print(\'x\'*22000);print(\'TAIL_FAILURE_SENTINEL\');raise SystemExit(1)"'
    result=run_command(ws,command)
    assert result.startswith('returncode=1') and 'TAIL_FAILURE_SENTINEL' in result
    key=re.search(r'id=([a-f0-9]{20})',result).group(1)
    recalled=ContextWindow(ws).recall(key,query='TAIL_FAILURE_SENTINEL')
    assert 'TAIL_FAILURE_SENTINEL' in recalled['text']


def test_incomplete_tool_protocol_is_rejected(tmp_path):
    context=ContextWindow(Workspace(tmp_path))
    messages=exchange('broken','read_file',{'path':'x'},'text')[:-1]
    with pytest.raises(ValueError):context.prepare(messages)


def test_current_rules_refreshed_between_requests(tmp_path):
    rules=tmp_path/'RULES.md';rules.write_text('CURRENT RULE A',encoding='utf-8')
    model=ScriptedModel([{'content':'ok'},{'content':'ok'}])
    agent=Akatikito(str(tmp_path),Config(auto_mode=False),model)
    agent.chat('first request')
    rules.write_text('CURRENT RULE B',encoding='utf-8')
    agent.chat('second request')
    assert 'CURRENT RULE A' in model.seen[0][0]['content']
    assert 'CURRENT RULE B' in model.seen[1][0]['content']
    assert 'CURRENT RULE A' not in model.seen[1][0]['content']


def test_invalid_historical_tool_arguments_do_not_break_compaction(tmp_path):
    messages=[{'role':'system','content':'rules'}]
    messages+=exchange('invalid','read_file',{},'error\n'*3000)
    messages[1]['tool_calls'][0]['function']['arguments']='[]'
    messages.append({'role':'user','content':'continue','_origin':'owner'})
    view=ContextWindow(Workspace(tmp_path),max_chars=4000).prepare(messages)
    ContextWindow.groups(view)
    assert any(m.get('content')=='continue' for m in view)


def test_defined_main_is_really_executed_without_script_guard(tmp_path):
    from akatikito.verification import run_checks
    (tmp_path/'akatikito').mkdir();(tmp_path/'tests').mkdir()
    (tmp_path/'app.py').write_text('# fixture\n',encoding='utf-8')
    (tmp_path/'akatikito/ui.html').write_text('<script>const ok=true;</script>',encoding='utf-8')
    (tmp_path/'tests/test_uncalled.py').write_text('def main():\n    raise AssertionError("MAIN MUST EXECUTE")\n',encoding='utf-8')
    ok,report=run_checks(tmp_path,['tests/test_uncalled.py'])
    assert not ok and 'MAIN MUST EXECUTE' in report
    # Preserve Python's failure semantics for a main() returning an error string.
    (tmp_path/'tests/test_uncalled.py').write_text('def main():\n    return "STRING FAILURE MUST NOT PASS"\n',encoding='utf-8')
    ok,report=run_checks(tmp_path,['tests/test_uncalled.py'])
    assert not ok and 'STRING FAILURE MUST NOT PASS' in report
    (tmp_path/'tests/test_uncalled.py').write_text('def main():\n    return None\n',encoding='utf-8')
    ok,report=run_checks(tmp_path,['tests/test_uncalled.py'])
    assert ok,report
