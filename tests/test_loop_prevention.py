"""Regressions for recall-of-recall, cyclic transport and repeated memory writes."""
from __future__ import annotations
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from akatikito.context import ContextWindow,encoded
from akatikito.workspace import Workspace
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel,tool_call
from akatikito import memory,modes


def exchange(n,name,args,result):
    key=f'call-{n}'
    return [{'role':'assistant','content':'','tool_calls':[{'id':key,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}, {'role':'tool','tool_call_id':key,'content':result}]


def legacy_chain(context,leaf,depth=8):
    key=leaf
    for n in range(depth):
        # Exactly the old format: JSON text containing JSON tool-call/result transport.
        result=json.dumps({'id':key,'start':0,'end':6000,'total':12000,'next':6000,'text':json.dumps(exchange(n,'recall_context',{'id':key},'old duplicated transport'))})
        key=context.store(exchange(n,'recall_context',{'id':key},result))
    return key


def test_legacy_recall_wrappers_resolve_to_primary_text(tmp_path):
    context=ContextWindow(Workspace(tmp_path));original='PRIMARY EVIDENCE\nRARE_VALUE=4321\n'
    leaf=context.store(original);outer=legacy_chain(context,leaf)
    recalled=context.recall(outer)
    assert recalled['text']==original and recalled['source_ids']==[leaf]
    assert recalled['total']==len(original) and 'tool_calls' not in recalled['text']
    first=context.request_recall(id=outer)
    second=context.request_recall(id=leaf)
    assert first['text']==original and second['blocked']
    assert not context.recall_available


def test_recalled_transport_is_not_archived_again_or_reintroduced(tmp_path):
    context=ContextWindow(Workspace(tmp_path),max_chars=8000)
    original='REAL SOURCE TEXT\n'*900;leaf=context.store(original)
    messages=[{'role':'system','content':'rules'},{'role':'user','content':'implement the current task','_origin':'owner'}]
    messages+=exchange('real','read_file',{'path':'app.py'},'def work(): return 42')
    initial=context.prepare(messages);before=len(list(context.folder.glob('*.json')))
    for n in range(30):
        page=context.request_recall(id=leaf) if n==0 else {'blocked':True,'text':'already retrieved'}
        messages+=exchange(n,'recall_context',{'id':leaf},context.recall_text(page))
        view=context.prepare(messages)
    assert len(list(context.folder.glob('*.json')))==before
    assert 'def work(): return 42' in encoded(view)
    assert len(encoded(view))<len(encoded(initial))+10000
    assert not any(c['function']['name']=='recall_context' for m in view for c in m.get('tool_calls',[]))
    assert original[:100] in '\n'.join(str(m.get('content','')) for m in view)


def test_forward_pages_allowed_but_repeat_aliases_are_blocked(tmp_path):
    context=ContextWindow(Workspace(tmp_path));text='0123456789'*5000;key=context.store(text)
    start=0;chunks=[]
    for n in range(7):
        page=context.request_recall(id=key,start=start,length=8000)
        assert not page.get('blocked'),page
        chunks.append(page['text'])
        if page['next'] is None:break
        start=page['next']
    assert ''.join(chunks)==text
    assert context.request_recall(id=key,start=0,length=8000)['blocked']
    context.note_progress()
    assert not context.request_recall(id=key,start=0,length=8000).get('blocked')


def test_memory_loop_writes_once_and_stops_boundedly(tmp_path,monkeypatch):
    monkeypatch.setattr(memory,'DIR',tmp_path/'private-memory')
    script=[{'tool_calls':[tool_call('remember',scope='user',note=f'Ο χρήστης θέλει σύντομες απαντήσεις, παραλλαγή {n}')]} for n in range(10)]
    agent=Akatikito(str(tmp_path),Config(auto_mode=False,max_steps=100),ScriptedModel(script));agent.set_mode('code')
    answer=agent.chat('Κατάγραψε την προτίμηση μία φορά και απάντησε.')
    from akatikito.profile import Profile
    data=Profile().load()
    assert len(data['proposals'])==1 and not data['entries']
    assert agent.model.calls<=4 and agent.activity.stalled
    assert 'επαναλαμβανόμενες' in answer
    assert agent.turn_usage['model_calls']==agent.model.calls


def test_conversational_preference_needs_no_tools(tmp_path,monkeypatch):
    text='ok aples erotisis ekana , isos p repei na eimaste poio periektiki na min keme tzapa tokens'
    assert modes.classify(text)=='ask'
    assert modes.classify('Φτιάξε τον agent να κάνει σύντομες απαντήσεις')=='code'
    agent=Akatikito(str(tmp_path),Config(),ScriptedModel([{'content':'Θα απαντώ πιο σύντομα.'}]))
    assert agent.chat(text)=='Θα απαντώ πιο σύντομα.'
    assert agent.mode=='ask' and agent.model.calls==1
    assert not any(e.kind=='tool_call' for e in agent.events.log)


def test_current_state_keeps_last_real_work_when_recall_dominates(tmp_path):
    context=ContextWindow(Workspace(tmp_path),max_chars=9000)
    messages=[{'role':'system','content':'rules'},{'role':'user','content':'continue implementing the parser','_origin':'owner'}]
    messages+=exchange('write','edit_file',{'path':'parser.py'},'ΕΓΙΝΕ: εφαρμόστηκε η αλλαγή στο parser.py')
    messages+=exchange('check','run_command',{'command':'verify'},'returncode=0\nOK tests')
    for n in range(80):messages+=exchange(n,'recall_context',{'id':'a'*20},json.dumps({'id':'a'*20,'start':0,'end':10,'total':10,'text':'transport'}))
    view=context.prepare(messages);text=encoded(view)
    assert 'ΤΡΕΧΟΥΣΑ ΚΑΤΑΣΤΑΣΗ' in text and 'parser.py' in text and 'OK tests' in text
    assert 'transport' not in text
    assert context.metrics['original_chars']<2000


def test_source_version_change_allows_fresh_read(tmp_path):
    path=tmp_path/'data.txt';path.write_text('old',encoding='utf-8')
    replies=[{'tool_calls':[tool_call('read_file',path='data.txt')]}]*3+[{'content':'done'}]
    agent=Akatikito(str(tmp_path),Config(auto_mode=False),ScriptedModel(replies));agent.set_mode('code')
    agent.chat('inspect data')
    results=[e.data['result'] for e in agent.events.log if e.kind=='tool_result']
    assert sum(r.startswith('ΑΠΟΤΡΟΠΗ') for r in results)==1
    path.write_text('new version',encoding='utf-8')
    assert not agent.activity.before('read_file',{'path':'data.txt'})


def test_exact_persistent_duplicate_does_not_rewrite_profile(tmp_path,monkeypatch):
    monkeypatch.setattr(memory,'DIR',tmp_path/'profile')
    memory.append('user','Σύντομες απαντήσεις.')
    p=memory.DIR/'USER.md';before=p.read_bytes();stamp=p.stat().st_mtime_ns
    result=memory.append('user','συντομες   απαντησεις!')
    assert result.startswith('Υπάρχει ήδη')
    assert p.read_bytes()==before and p.stat().st_mtime_ns==stamp


def test_cancelled_response_is_counted_but_tools_not_executed(tmp_path):
    class StopModel:
        def chat(self,*args,**kwargs):
            agent.stop()
            return {'content':'','tool_calls':[{'id':'cancel','name':'write_file','arguments':json.dumps({'path':'should-not-exist','content':'x'})}]},{'prompt_tokens':100,'completion_tokens':20}
    agent=Akatikito(str(tmp_path),Config(),StopModel())
    assert agent.chat('implement')=='Σταμάτησα όπως ζήτησες.'
    assert not (tmp_path/'should-not-exist').exists()
    assert agent.tokens['prompt_tokens']==100 and agent.turn_usage['completion_tokens']==20


def test_primary_tool_decision_is_preserved_when_unwrapping(tmp_path):
    context=ContextWindow(Workspace(tmp_path))
    group=exchange('source','read_file',{'path':'calc.py'},'return a + b')
    group[0]['content']='Agreed decision: preserve port 8877.'
    leaf=context.store(group);outer=legacy_chain(context,leaf)
    text=context.recall(outer)['text']
    assert 'Agreed decision: preserve port 8877.' in text
    assert 'return a + b' in text
