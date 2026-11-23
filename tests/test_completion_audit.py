"""Completion is exercised through the production model adapter with a fake transport."""
import copy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import OpenAIModel, tool_call, ModelError
from akatikito.tools import run_command
from akatikito.workspace import Workspace
from akatikito import memory, tools


@pytest.fixture(autouse=True)
def isolate(tmp_path,monkeypatch):
    monkeypatch.setattr(memory,'DIR',tmp_path/'memory')


def finish(outcome='completed',summary='Έτοιμο',evidence='Υλοποίηση και έλεγχος του ζητούμενου'):
    return tool_call('complete_task',outcome=outcome,summary=summary,evidence=evidence)


def fixture(tmp_path,script,verify='',max_steps=12):
    sent=[];replies=iter(script)
    def create(**kwargs):
        sent.append(copy.deepcopy(kwargs))
        item=next(replies)
        if isinstance(item,Exception):raise item
        calls=[NS(id=f'c{len(sent)}-{i}',function=NS(name=c['name'],arguments=json.dumps(c['arguments'],ensure_ascii=False))) for i,c in enumerate(item.get('tool_calls',[]))]
        return NS(choices=[NS(message=NS(content=item.get('content',''),tool_calls=calls,reasoning_content=''))],usage=NS(prompt_tokens=10,completion_tokens=5))
    model=OpenAIModel.__new__(OpenAIModel)
    model.config=Config(model='production-adapter-synthetic',base_url='https://unit.invalid',api_key='synthetic',auto_mode=False,stream_responses=False,verify_command=verify,max_steps=max_steps)
    model._client=NS(chat=NS(completions=NS(create=create)))
    agent=Akatikito(str(tmp_path),model.config,model)
    return agent,sent


def test_plain_success_without_implementation_is_not_completed(tmp_path):
    agent,sent=fixture(tmp_path,[{'content':'Έτοιμο, όλα λειτουργούν.'}])
    result=agent.chat('Πρόσθεσε streaming')
    assert 'Δεν ολοκληρώθηκε' in result and 'Δεν έγινε αλλαγή' in result
    assert agent.task.status=='blocked' and len(sent)==1
    assert agent.messages[-1]['content']==result


def test_ordinary_question_still_needs_no_completion_tool(tmp_path):
    agent,sent=fixture(tmp_path,[{'content':'Μια απάντηση.'}]);agent.set_mode('ask')
    assert agent.chat('Τι κάνει αυτό;')=='Μια απάντηση.' and len(sent)==1


def test_completed_without_changes_is_rejected_even_after_baseline_check(tmp_path):
    (tmp_path/'check.py').write_text('print("BASELINE")\n',encoding='utf-8')
    command=f'"{sys.executable}" -B check.py'
    script=[{'tool_calls':[tool_call('run_command',command=command)]},{'tool_calls':[finish()]},{'content':'done'}]
    agent,sent=fixture(tmp_path,script,verify=command)
    result=agent.chat('Πρόσθεσε streaming')
    assert 'Δεν ολοκληρώθηκε' in result and len(agent._task_writes)==0
    assert any('δεν έγινε αλλαγή' in m.get('content','').casefold() for m in agent.messages if m['role']=='tool')


def test_actual_write_and_check_can_complete_without_an_extra_model_call(tmp_path):
    (tmp_path/'check.py').write_text('from calc import add\nassert add(2,3)==5\n',encoding='utf-8')
    command=f'"{sys.executable}" -B check.py'
    script=[{'tool_calls':[tool_call('write_file',path='calc.py',content='def add(a,b):\n    return a+b\n')]},{'tool_calls':[finish(evidence='calc.py και assert add(2,3)==5')]}]
    agent,sent=fixture(tmp_path,script,verify=command)
    result=agent.chat('Υλοποίησε άθροισμα')
    assert 'Αλλαγμένα αρχεία: 1. Επιτυχείς έλεγχοι: 1.' in result
    assert len(sent)==2 and agent.task.status=='complete'
    assert [e.data['ok'] for e in agent.events.log if e.kind=='verify']==[True]


def test_completion_in_same_batch_waits_for_actual_checks(tmp_path):
    (tmp_path/'check.py').write_text('assert open("new.txt").read()=="ok"\n',encoding='utf-8')
    command=f'"{sys.executable}" -B check.py'
    script=[{'tool_calls':[tool_call('write_file',path='new.txt',content='ok'),finish()]},{'tool_calls':[finish()]}]
    agent,sent=fixture(tmp_path,script,verify=command)
    assert 'Αλλαγμένα αρχεία: 1' in agent.chat('Υλοποίησε το αρχείο')
    assert any('χρειάζονται επιτυχή έλεγχο' in m.get('content','') for m in agent.messages if m['role']=='tool')
    assert len(sent)==2


def test_already_satisfied_requires_read_and_check(tmp_path):
    (tmp_path/'feature.py').write_text('READY=True\n',encoding='utf-8')
    (tmp_path/'test_feature.py').write_text('from feature import READY\nassert READY\n',encoding='utf-8')
    command=f'"{sys.executable}" -B test_feature.py'
    script=[{'tool_calls':[finish('already_satisfied')]},
            {'tool_calls':[tool_call('read_file',path='feature.py'),tool_call('run_command',command=command)]},
            {'tool_calls':[finish('already_satisfied',summary='Υπάρχει ήδη',evidence='feature.py READY και test_feature.py')]}]
    agent,sent=fixture(tmp_path,script,verify=command)
    assert 'Αλλαγμένα αρχεία: 0. Επιτυχείς έλεγχοι: 1.' in agent.chat('Πρόσθεσε τη λειτουργία')
    assert len(sent)==3 and agent.task.status=='complete'


def test_completion_must_be_last_and_tool_failure_prevents_same_batch_success(tmp_path):
    script=[{'tool_calls':[finish(),tool_call('write_file',path='new.txt',content='ok')]},
            {'tool_calls':[tool_call('read_file',path='missing.txt'),finish()]},
            {'tool_calls':[finish('blocked',summary='Λείπει το ζητούμενο αρχείο',evidence='Το missing.txt δεν υπάρχει')]}]
    agent,sent=fixture(tmp_path,script)
    result=agent.chat('Υλοποίησε την αλλαγή')
    assert 'Δεν ολοκληρώθηκε' in result and agent.task.status=='blocked'
    results=[m['content'] for m in agent.messages if m['role']=='tool']
    assert any('τελευταία κλήση' in r for r in results) and any('εργαλείο απέτυχε' in r for r in results)


def test_reasking_for_start_permission_is_rejected(tmp_path):
    script=[{'tool_calls':[finish('needs_input',summary='Θέλεις να προχωρήσω;',evidence='Νέα έγκριση υλοποίησης')]},
            {'tool_calls':[tool_call('write_file',path='ok.txt',content='done')]},{'tool_calls':[finish()]}]
    agent,sent=fixture(tmp_path,script)
    assert 'Αλλαγμένα αρχεία: 1' in agent.chat('Υλοποίησε το επιλεγμένο χαρακτηριστικό')
    assert len(sent)==3 and any('έχει ήδη ζητηθεί' in m.get('content','') for m in agent.messages)


def test_genuine_missing_input_is_reported_without_false_completion(tmp_path):
    agent,sent=fixture(tmp_path,[{'tool_calls':[finish('needs_input',summary='Ποιο CSV θέλεις να μετατρέψω;',evidence='Δεν υπάρχει CSV στο project')]}])
    assert 'Ποιο CSV' in agent.chat('Φτιάξε μετατροπή CSV')
    assert agent.task.status=='blocked' and len(sent)==1


def test_existing_read_only_memory_is_not_current_task_permission(tmp_path):
    agent,sent=fixture(tmp_path,[{'content':'answer'}])
    agent.ws.append_memory('Η παλιά εργασία περίμενε έγκριση')
    agent.set_mode('ask')
    assert agent.chat('τι είναι αυτό;')=='answer'
    assert 'ιστορικά δεδομένα' in sent[0]['messages'][0]['content']


def test_timeout_keeps_output_and_enters_the_repair_state(tmp_path,monkeypatch):
    timeout_err=subprocess.TimeoutExpired('python -m pytest',1,output=b'before timeout\n',stderr=b'diagnostic\n')
    class FakePopen:
        pid = 99999
        returncode = 124
        def __init__(self, *a, **kw): self._timed_out = False
        def communicate(self, timeout=None):
            if not self._timed_out:
                self._timed_out = True
                raise timeout_err
            return (b'', b'')
        def kill(self): pass
    monkeypatch.setattr(tools.subprocess,'Popen',FakePopen)
    result=run_command(Workspace(tmp_path),'python -m pytest',timeout=1)
    assert result.startswith('returncode=124') and 'before timeout' in result and 'diagnostic' in result
    script=[{'tool_calls':[tool_call('run_command',command='python -m pytest')]},{'content':'done'}]
    agent,sent=fixture(tmp_path,script,verify='python -m pytest',max_steps=2)
    answer=agent.chat('Διόρθωσε τα tests')
    assert agent.repair.pending and 'TimeoutError' in answer
    assert agent.messages[-1]['content']==answer


def test_legacy_transport_failure_is_also_saved_for_followup(tmp_path):
    agent,sent=fixture(tmp_path,[ValueError('synthetic transport failure')])
    with pytest.raises(ModelError):agent.chat('Υλοποίησε το αίτημα')
    assert 'synthetic transport failure' in agent.messages[-1]['content']
    assert agent.task.status=='blocked'


def test_plain_answer_after_real_verified_implementation_remains_compatible(tmp_path):
    (tmp_path/'check.py').write_text('assert open("new.txt").read()=="ok"\n',encoding='utf-8')
    command=f'"{sys.executable}" -B check.py'
    script=[{'tool_calls':[tool_call('write_file',path='new.txt',content='ok')]},{'content':'Έγινε και ελέγχθηκε.'}]
    agent,sent=fixture(tmp_path,script,verify=command)
    assert agent.chat('Υλοποίησε το αρχείο')=='Έγινε και ελέγχθηκε.'
    assert agent.task.status=='complete' and len(sent)==2


def test_request_to_run_checks_does_not_require_unnecessary_file_changes(tmp_path):
    (tmp_path/'check.py').write_text('print("OK")\n',encoding='utf-8')
    command=f'"{sys.executable}" -B check.py'
    script=[{'tool_calls':[tool_call('run_command',command=command)]},{'content':'Οι έλεγχοι πέρασαν.'}]
    agent,sent=fixture(tmp_path,script,verify=command)
    assert agent.chat('τρέξε τους ελέγχους')=='Οι έλεγχοι πέρασαν.'
    assert not agent._task_writes and len(sent)==2


def test_plan_alone_cannot_complete_a_feature_implementation(tmp_path):
    script=[{'tool_calls':[tool_call('write_file',path='PLAN.md',content='Σχέδιο')]},{'tool_calls':[finish()]},{'content':'Έτοιμη η υλοποίηση'}]
    agent,sent=fixture(tmp_path,script)
    assert 'Δεν ολοκληρώθηκε' in agent.chat('Υλοποίησε τη λειτουργία')
    assert any('ένα πλάνο δεν αρκεί' in m.get('content','') for m in agent.messages)


def test_mode_changes_refresh_system_policy_without_losing_tool_history(tmp_path):
    script=[{'tool_calls':[tool_call('set_mode',mode='architect')]},
            {'tool_calls':[tool_call('write_file',path='PLAN.md',content='Σχέδιο'),tool_call('set_mode',mode='code')]},
            {'tool_calls':[tool_call('write_file',path='impl.py',content='value=42\n')]},
            {'tool_calls':[finish()]}]
    agent,sent=fixture(tmp_path,script)
    assert 'Αλλαγμένα αρχεία: 2' in agent.chat('Υλοποίησε το feature')
    assert 'Λειτουργία (Αρχιτέκτονας)' in sent[1]['messages'][0]['content']
    assert 'Λειτουργία (Κώδικας)' in sent[2]['messages'][0]['content']
    assert any(m.get('tool_calls') for m in sent[2]['messages'])
