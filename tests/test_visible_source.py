"""Prevent compression and repetition guards from disagreeing about visible source."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from akatikito.activity import ActivityGuard
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import OpenAIModel, ScriptedModel, tool_call
from akatikito.tools import read_file
from akatikito.workspace import Workspace
from akatikito import memory


def view(path, text):
    return [{'role':'assistant','content':'','tool_calls':[{'id':'c','type':'function','function':{'name':'read_file','arguments':json.dumps({'path':path})}}]},
            {'role':'tool','tool_call_id':'c','content':text}]


@pytest.fixture
def fixture(tmp_path):
    path=tmp_path/'source.py';path.write_text('def work():\n    return 42\n',encoding='utf-8')
    ws=Workspace(tmp_path);guard=ActivityGuard(ws);args={'path':'source.py'}
    text=read_file(ws,**args)
    guard.record('read_file',args,text);guard.record('read_file',args,text)
    return ws,guard,args,text


def test_hidden_source_is_restored_once_instead_of_a_false_repetition_block(fixture):
    ws,guard,args,text=fixture
    guard.update_visible([{'role':'user','content':'old source summarized without its body'}])
    restored=guard.before('read_file',args)
    assert restored.startswith('source.py') and 'return 42' in restored
    assert 'ΑΠΟΤΡΟΠΗ' not in restored and guard.blocked==0
    guard.record('read_file',args,restored)
    guard.update_visible(view(args['path'],restored))
    assert guard.before('read_file',args).startswith('ΑΠΟΤΡΟΠΗ')


def test_repeated_eviction_and_range_alias_cannot_create_restore_loop(fixture):
    ws,guard,args,text=fixture
    guard.update_visible([])
    assert 'return 42' in guard.before('read_file',args)
    guard.update_visible([])
    for _ in range(3):
        assert guard.before('read_file',{'path':'./source.py','start':1,'end':2}).startswith('ΑΠΟΤΡΟΠΗ')
    assert guard.stalled


def test_changed_file_is_not_replaced_with_an_old_cached_read(fixture):
    ws,guard,args,text=fixture
    guard.update_visible([])
    (ws.root/'source.py').write_text('def work():\n    return 987654321\n',encoding='utf-8')
    assert guard.before('read_file',args)==''


def test_visible_source_still_blocks_redundant_reads(fixture):
    ws,guard,args,text=fixture
    guard.update_visible(view(args['path'],text))
    assert guard.before('read_file',args).startswith('ΑΠΟΤΡΟΠΗ')


def test_outline_and_different_file_are_not_full_source_visibility(fixture):
    ws,guard,args,text=fixture
    guard.update_visible(view('different.py',text))
    assert guard.before('read_file',args).startswith('source.py')


def test_non_glm_provider_keeps_active_multifile_source_until_budget(tmp_path,monkeypatch):
    monkeypatch.setattr(memory,'DIR',tmp_path/'memory')
    assert OpenAIModel.keeps_active_history is True
    source=''.join(f'value_{n} = '+repr('payload'*8)+'\n' for n in range(100))
    for n in range(4):(tmp_path/f'f{n}.py').write_text(source,encoding='utf-8')
    class PlainModel(ScriptedModel):
        keeps_active_history=True
        preserves_tool_reasoning=False
        context_window_tokens=200000
    script=[{'tool_calls':[tool_call('read_file',path=f'f{n}.py')]} for n in range(4)]+[{'content':'done'}]
    model=PlainModel(script)
    agent=Akatikito(str(tmp_path),Config(model='deepseek-synthetic',auto_mode=False,context_max_chars=4000),model)
    assert agent.chat('inspect four files')=='done'
    last=model.seen[-1]
    assert sum(m.get('role')=='tool' for m in last)==4
    assert sum('value_99' in m.get('content','') for m in last if m['role']=='tool')==4
    assert agent.context.metrics['protocol_checkpoints']==0
    assert agent.context.metrics['active_budget_bytes']==120000


def test_guard_receives_the_actual_request_view(tmp_path,monkeypatch):
    monkeypatch.setattr(memory,'DIR',tmp_path/'memory')
    agent=Akatikito(str(tmp_path),Config(auto_mode=False),ScriptedModel([{'content':'done'}]))
    observed=[]
    original=agent.activity.update_visible
    def record(messages):
        observed.append(messages)
        return original(messages)
    agent.activity.update_visible=record
    agent.chat('question')
    assert observed==agent.model.seen
