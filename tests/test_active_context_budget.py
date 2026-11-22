"""Regressions from the real self-upgrade run: premature reset and missing inventory."""
from pathlib import Path
import hashlib,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.context import ContextWindow
from akatikito.workspace import Workspace
from akatikito.tools import list_files,dispatch
from akatikito import modes,model as provider


def test_active_chain_uses_its_budget_not_old_history_target(tmp_path):
    for n in range(4):(tmp_path/f'f{n}.py').write_text('\n'.join(f'value_{i} = '+repr('x'*50) for i in range(500)),encoding='utf-8')
    class ThinkingModel:
        preserves_tool_reasoning=True
        context_window_tokens=1048576
        def __init__(self):self.calls=0;self.seen=[]
        def chat(self,messages,tools,cache_key=None):
            self.seen.append(messages);n=self.calls;self.calls+=1
            if n==4:return {'content':'done'},{}
            return {'content':'','reasoning_content':f'exact reason {n}','tool_calls':[{'id':f'c{n}','name':'read_file','arguments':json.dumps({'path':f'f{n}.py'})}]},{}
    model=ThinkingModel();agent=Akatikito(str(tmp_path),Config(auto_mode=False,context_max_chars=8000),model)
    assert agent.chat('inspect')=='done'
    assert agent.context.metrics['protocol_checkpoints']==0
    assert agent.context.metrics['active_budget_bytes']>160000
    assert agent.context.metrics['active_budget_bytes']<=int(model.context_window_tokens*0.6)
    assert sum(m.get('role')=='tool' for m in model.seen[-1])==4
    assert sum('reasoning_content' in m for m in model.seen[-1])==4


def test_active_budget_is_constrained_by_provider_window(tmp_path):
    class Model:
        preserves_tool_reasoning=True
        context_window_tokens=50000
        def chat(self,*a,**kw):return {'content':'done'},{}
    agent=Akatikito(str(tmp_path),Config(auto_mode=False),Model())
    agent.chat('inspect')
    assert agent.context.active_max_bytes==30000


def test_model_catalog_keeps_native_limits_and_cache_is_reused(tmp_path,monkeypatch):
    cache=tmp_path/'models.json';fetches=[]
    monkeypatch.setattr(provider,'_models_cache_path',lambda:cache)
    def fetch(*args):
        fetches.append(1)
        return [{'id':'z-ai/glm-unit:thinking','context_length':1048576,'max_output_tokens':131072,'subscription':{'included':True}}]
    monkeypatch.setattr(provider,'_fetch_detailed',fetch)
    model=provider.OpenAIModel.__new__(provider.OpenAIModel)
    model.config=Config(model='z-ai/glm-unit:thinking',base_url='https://api.nano-gpt.com/api/subscription/v1',api_key='synthetic')
    assert model.context_window_tokens==1048576
    assert model.context_window_tokens==1048576 and len(fetches)==1
    row=next(iter(json.loads(cache.read_text()).values()))['models'][0]
    assert row['max_output_tokens']==131072
    again=provider.OpenAIModel.__new__(provider.OpenAIModel);again.config=model.config
    assert again.context_window_tokens==1048576 and len(fetches)==1


def test_file_inventory_is_read_only_bounded_and_available_in_all_modes(tmp_path):
    (tmp_path/'src').mkdir();(tmp_path/'src/app.py').write_text('value=1')
    (tmp_path/'.agent').mkdir();(tmp_path/'.agent/private').write_text('private')
    (tmp_path/'.venv').mkdir();(tmp_path/'.venv/python').write_text('binary')
    ws=Workspace(tmp_path)
    result=list_files(ws,recursive=True)
    assert 'src/app.py' in result and '.agent' not in result and '.venv' not in result
    assert 'Όριο 1' in list_files(ws,max_entries=1)
    assert dispatch(ws,'list_files',{'path':'..'}).startswith('ΑΠΟΡΡΙΨΗ')
    for mode in modes.MODES:assert 'list_files' in modes.allowed_tools(mode)


def test_known_large_window_keeps_realistic_multifile_work_in_one_segment(tmp_path):
    context=ContextWindow(Workspace(tmp_path),max_chars=36000)
    context.active_max_bytes=int(1048576*0.6)
    context.reasoning_identity=('glm-test','https://unit.invalid')
    messages=[{'role':'system','content':'rules'},{'role':'user','content':'implement web and github','_origin':'owner'}]
    for n in range(17):
        messages += [{'role':'assistant','content':'','reasoning_content':f'original-{n}', '_reasoning_identity':['glm-test','https://unit.invalid'],'tool_calls':[{'id':f'c{n}','type':'function','function':{'name':'read_file','arguments':json.dumps({'path':f'source{n}.py'})}}]}, {'role':'tool','tool_call_id':f'c{n}','content':('current source line\n'*650)+str(n)}]
        view=context.prepare(messages,continuous=True)
        assert context.metrics['protocol_checkpoints']==0
        assert sum('reasoning_content' in m for m in view)==n+1
    assert context.metrics['sent_chars']>160000


def test_explicit_user_active_cap_is_respected(tmp_path):
    class Model:
        preserves_tool_reasoning=True
        context_window_tokens=1048576
        def chat(self,*args,**kw):return {'content':'done'},{}
    agent=Akatikito(str(tmp_path),Config(auto_mode=False,context_active_max_bytes=20000),Model())
    agent.chat('inspect')
    assert agent.context.active_max_bytes==20000
