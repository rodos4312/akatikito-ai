"""Observed non-coding procedures, owner review and real scripted agent integration."""
from pathlib import Path
import copy
import json
import sys
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akatikito import memory
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel,tool_call
from akatikito.skills import Skills


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):monkeypatch.setattr(memory,'DIR',tmp_path/'private-memory')


def operation(name,args,result):
    return [SimpleNamespace(kind='tool_call',data={'name':name,'arguments':json.dumps(args)}),
            SimpleNamespace(kind='tool_result',data={'name':name,'path':args.get('path',''),'result':result})]


def research():
    return sum((operation('read_file',{'path':name},'private-source-body') for name in ('a.txt','b.txt','c.txt')),[])


def twice(store,events,**kwargs):
    for _ in range(2):store.capture(events,completed=True,**kwargs)
    return store.hints()


def test_readonly_research_is_captured_without_copying_source_or_prompts(tmp_path):
    s=Skills(tmp_path);rows=twice(s,research(),task='synthetic-secret-user-prompt')
    assert len(rows)==1 and rows[0]['category']=='research' and rows[0]['source_count']==3
    raw=s.path.read_text(encoding='utf-8')
    assert 'private-source-body' not in raw and 'synthetic-secret-user-prompt' not in raw
    assert all(r['source']=='builtin' for r in s.catalog()) and not s.view()['proposals']


def test_completed_evidence_is_required_and_repeated_paging_does_not_qualify(tmp_path):
    s=Skills(tmp_path);s.capture(research(),completed=False);assert not s.path.exists()
    pages=sum((operation('read_file',{'path':p,'start':n},'text') for n,p in enumerate(['a.txt','./a.txt','a.txt'])),[])
    assert not twice(s,pages)


def test_checklist_planning_is_distinct_from_research(tmp_path):
    s=Skills(tmp_path)
    plan=research()[:4]+operation('update_todos',{'todos':[{'id':'1','text':'Plan','status':'pending'}]},'ΕΓΙΝΕ: checklist')
    rows=twice(s,plan)
    assert rows[0]['category']=='planning' and rows[0]['evidence']=='checklist-and-report'
    rows=twice(s,research());assert {r['category'] for r in rows}=={'research','planning'}


def test_analysis_can_finish_with_a_successful_check_without_modifying_files(tmp_path):
    events=research()[:4]+operation('run_command',{'command':'python -m pytest -q'},'returncode=0\npassed')
    rows=twice(Skills(tmp_path),events)
    assert rows[0]['category']=='analysis' and rows[0]['evidence']=='successful-command'


def test_reminder_organization_requires_actual_readback_of_created_ids(tmp_path):
    before=operation('list_reminders',{},'[]')
    created=operation('add_reminder',{'title':'owner request'},'ΕΓΙΝΕ: '+json.dumps({'id':'task-1'}))
    wrong=before+created+operation('list_reminders',{},json.dumps([{'id':'other'}]))
    s=Skills(tmp_path);assert not twice(s,wrong)
    right=before+created+operation('list_reminders',{},json.dumps([{'id':'task-1'}]))
    rows=twice(s,right);assert rows[0]['category']=='organization'


def test_readback_must_match_the_final_state_not_only_the_id(tmp_path):
    events=operation('list_reminders',{},'[]')+operation('done_reminder',{'id':'1'},'ΕΓΙΝΕ: '+json.dumps({'id':'1','done':True}))
    events+=operation('list_reminders',{},json.dumps([{'id':'1','done':False}]))
    assert not twice(Skills(tmp_path),events)


def test_mcp_workflows_use_owner_reviewed_readonly_traits(tmp_path):
    events=sum((operation('mcp_s_read',{'path':p},'external data') for p in ('a','b','c')),[])
    s=Skills(tmp_path);assert not twice(s,events)
    rows=twice(s,events,traits={'mcp_s_read':{'readonly':True,'server':'s'}})
    assert rows[0]['category']=='research'


def test_failed_or_blocked_research_is_not_recorded(tmp_path):
    events=research()+operation('web_fetch',{'url':'https://not-contacted.invalid'},'ΣΦΑΛΜΑ: stopped')
    assert not twice(Skills(tmp_path),events)


def test_research_skill_draft_approval_reuse_and_refinement_keep_history(tmp_path):
    s=Skills(tmp_path);row=twice(s,research())[0]
    steps=['Διάβασε τρεις πηγές.','Σύγκρινε τα ευρήματα.','Γράψε τεκμηριωμένη αναφορά.']
    s.propose('compare-sources','Σύγκριση πηγών',steps,row['id'])
    assert 'compare-sources' not in {r['name'] for r in s.catalog()};proposal=s.view()['proposals'][0]
    s.answer(proposal['id'],True,expected=proposal['signature'])
    old=s.read('compare-sources')
    s.propose('compare-sources','Σύγκριση με έλεγχο αντιφάσεων',steps+['Επισήμανε τις αντιφάσεις.'])
    proposal=s.view()['proposals'][0];s.answer(proposal['id'],True,expected=proposal['signature'])
    assert 'Επισήμανε' in s.read('compare-sources') and s._load()['history'][-1]['before'] in old
    assert len(list(s.user_root.glob('compare-sources*.md')))==1


def test_existing_records_are_retained_when_new_categories_are_added(tmp_path):
    s=Skills(tmp_path);data=s._load()
    original={'id':'old','count':5,'tools':['read_file','edit_file','run_command'],'examples':['old note']}
    data['workflows'].append(copy.deepcopy(original));data['history'].append({'action':'legacy','text':'KEEP'})
    s._save(data);twice(s,research())
    assert original in s._load()['workflows'] and s._load()['history']==data['history']


def test_real_ask_agent_learns_repeated_research_without_additional_calls(tmp_path):
    for name in ('a.txt','b.txt','c.txt'):(tmp_path/name).write_text(name)
    replies=[]
    for _ in range(2):
        replies.extend([{'tool_calls':[tool_call('read_file',path=p) for p in ('a.txt','b.txt','c.txt')]},
                        {'content':'Τεκμηριωμένη σύγκριση των τριών αρχείων.'}])
    model=ScriptedModel(replies);a=Akatikito(str(tmp_path),Config(auto_mode=False,max_steps=3),model);a.set_mode('ask')
    for _ in range(2):a.chat('Σύγκρινε τα τρία αρχεία, χωρίς αλλαγές.')
    assert model.calls==4
    assert Skills(tmp_path).hints()[0]['category']=='research'
    for name in ('a.txt','b.txt','c.txt'):assert (tmp_path/name).read_text()==name


def test_step_limit_does_not_turn_partial_research_into_learning(tmp_path):
    for p in ('a.txt','b.txt','c.txt'):(tmp_path/p).write_text(p)
    model=ScriptedModel([{'tool_calls':[tool_call('read_file',path=p) for p in ('a.txt','b.txt','c.txt')]}])
    a=Akatikito(str(tmp_path),Config(auto_mode=False,max_steps=1),model);a.set_mode('ask')
    a.chat('Σύγκρινε τα αρχεία.')
    assert model.calls==1 and not Skills(tmp_path).hints()


def test_real_assistant_records_verified_reminder_organization(tmp_path):
    replies=[]
    for _ in range(2):replies.extend([
        {'tool_calls':[tool_call('list_reminders')]},
        {'tool_calls':[tool_call('add_reminder',title='Synthetic reminder',date='2026-12-01')]},
        {'tool_calls':[tool_call('list_reminders')]},{'content':'Η υπενθύμιση καταγράφηκε και ελέγχθηκε.'}])
    model=ScriptedModel(replies);a=Akatikito(str(tmp_path),Config(auto_mode=False,max_steps=5),model);a.set_mode('assistant')
    for _ in range(2):a.chat('Πρόσθεσε και έλεγξε μια υπενθύμιση.')
    assert model.calls==8 and Skills(tmp_path).hints()[0]['category']=='organization'
