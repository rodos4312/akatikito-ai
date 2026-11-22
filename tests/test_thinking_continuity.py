"""Exact provider protocol and stable active-turn request continuity."""
from pathlib import Path
from types import SimpleNamespace
import copy,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.context import ContextWindow,encoded
from akatikito.model import OpenAIModel,tool_call
from akatikito.sessions import SessionStore
from akatikito.workspace import Workspace


def exchange(n,text='source'):
    return [{'role':'assistant','content':'','reasoning_content':f'  ORIGINAL REASON {n}\n','_reasoning_identity':['glm-test','https://unit.invalid'],'tool_calls':[{'id':f'call-{n}','type':'function','function':{'name':'read_file','arguments':json.dumps({'path':f'f{n}.py'})}}]},{'role':'tool','tool_call_id':f'call-{n}','content':text}]


def test_payload_roundtrip_is_exact_and_not_just_ui_reasoning(tmp_path):
    raw='  unmodified protocol payload\n\t'
    details=[{'type':'reasoning.encrypted','data':'opaque==','signature':'sig','index':0}]
    choice=SimpleNamespace(model_extra={'reasoning':raw,'reasoning_details':details})
    payload=OpenAIModel._protocol_payload(choice)
    assert payload['reasoning_content']==raw and payload['reasoning_details']==details
    details[0]['data']='changed'
    assert payload['reasoning_details'][0]['data']=='opaque=='


def test_wire_request_preserves_glm_fields_and_uses_required_options():
    sent=[]
    choice=SimpleNamespace(content='',tool_calls=[],reasoning_content='  exact\n',model_extra={})
    response=SimpleNamespace(choices=[SimpleNamespace(message=choice)],usage=SimpleNamespace(prompt_tokens=5,completion_tokens=2))
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: sent.append(kw) or response)))
    model=OpenAIModel.__new__(OpenAIModel)
    model.config=Config(model='z-ai/glm-5.3:thinking',base_url='https://api.nano-gpt.com/api/subscription/v1',api_key='synthetic')
    model._client=client
    messages=[{'role':'assistant','content':'','reasoning_content':'  prior\n'}]
    answer,_=model.chat(messages,[])
    assert sent[0]['messages']==messages and answer['reasoning_content']=='  exact\n'
    assert sent[0]['extra_body']['thinking']['clear_thinking'] is False
    assert sent[0]['extra_body']['reasoning_content_compat'] is True
    model.config.model='plain-chat'
    model.chat(messages,[])
    assert 'reasoning_content' not in sent[1]['messages'][0]
    assert messages[0]['reasoning_content']=='  prior\n'


def test_agent_keeps_full_protocol_in_next_request_and_saved_session(tmp_path):
    (tmp_path/'app.py').write_text('value=7\n',encoding='utf-8')
    class ThinkingModel:
        preserves_tool_reasoning=True
        def __init__(self):self.seen=[]
        def chat(self,messages,tools,cache_key=None):
            self.seen.append(copy.deepcopy(messages))
            if len(self.seen)==1:
                return {'content':'','reasoning':'display','reasoning_content':'  EXACT ACTIVE PAYLOAD\n','reasoning_details':[{'data':'opaque','signature':'sig'}],'tool_calls':[{'id':'call-read','name':'read_file','arguments':'{"path":"app.py"}'}]},{}
            return {'content':'done'},{}
    model=ThinkingModel();cfg=Config(model='glm-test',base_url='https://unit.invalid',auto_mode=False)
    agent=Akatikito(str(tmp_path),cfg,model)
    assert agent.chat('inspect')=='done'
    message=next(m for m in model.seen[1] if m.get('tool_calls'))
    assert message['reasoning_content']=='  EXACT ACTIVE PAYLOAD\n'
    assert message['reasoning_details']==[{'data':'opaque','signature':'sig'}]
    assert sum(m['role']=='user' for m in model.seen[1])==1
    store=SessionStore(tmp_path);sid=store.create();store.save(sid,messages=agent.messages)
    saved=store.load(sid)['messages']
    assert any(m.get('reasoning_content')=='  EXACT ACTIVE PAYLOAD\n' for m in saved)


def test_internal_test_observation_does_not_create_fake_user_turn(tmp_path):
    context=ContextWindow(Workspace(tmp_path));context.reasoning_identity=('glm-test','https://unit.invalid')
    messages=[{'role':'system','content':'rules'},{'role':'user','content':'fix it','_origin':'owner'}]+exchange(1)
    messages.append({'role':'user','content':'[ΑΥΤΟΜΑΤΟΣ ΕΛΕΓΧΟΣ] passed','_origin':'internal'})
    before=copy.deepcopy(messages)
    view=context.prepare(messages,continuous=True)
    assert sum(m['role']=='user' for m in view)==1
    assert view[-1]['role']=='tool' and 'passed' in view[-1]['content']
    assert view[-2]['reasoning_content']=='  ORIGINAL REASON 1\n'
    assert messages==before
    ContextWindow.groups(view)


def test_active_exchanges_are_not_incrementally_replaced_by_archives(tmp_path):
    context=ContextWindow(Workspace(tmp_path),max_chars=36000);context.reasoning_identity=('glm-test','https://unit.invalid')
    messages=[{'role':'system','content':'rules'},{'role':'user','content':'implement','_origin':'owner'}]
    previous=[]
    for n in range(8):
        messages+=exchange(n,'actual source '+str(n))
        view=context.prepare(messages,continuous=True)
        active=[m for m in view if m.get('role')!='system']
        assert active[:len(previous)]==previous
        assert len([m for m in active if m.get('tool_calls')])==n+1
        assert not any('ΑΡΧΕΙΟ ΙΣΤΟΡΙΚΟΥ' in str(m.get('content')) for m in active)
        previous=copy.deepcopy(active)
    assert not context.folder.exists()


def test_checkpoint_resets_whole_old_reasoning_and_preserves_new_segment(tmp_path):
    context=ContextWindow(Workspace(tmp_path),max_chars=8000);context.reasoning_identity=('glm-test','https://unit.invalid')
    messages=[{'role':'system','content':'DO NOT CHANGE PORT 8877'},{'role':'user','content':'implement','_origin':'owner'}]+exchange(1,'source '*2200)
    view=context.prepare(messages,continuous=True)
    assert context.metrics['protocol_checkpoints']==1
    assert not any('reasoning_content' in m for m in view)
    assert '8877' in encoded(view) and 'ΣΥΝΕΧΕΙΑ ΙΔΙΟΥ ΑΙΤΗΜΑΤΟΣ' in view[-1]['content']
    frozen=copy.deepcopy(view)
    messages+=exchange(2,'small new source')
    second=context.prepare(messages,continuous=True)
    assert context.metrics['protocol_checkpoints']==1
    assert second[:len(frozen)]==frozen
    assert second[-2]['reasoning_content']=='  ORIGINAL REASON 2\n'
    ContextWindow.groups(second)


def test_old_reasoning_is_not_forwarded_to_another_model_or_provider(tmp_path):
    context=ContextWindow(Workspace(tmp_path));context.reasoning_identity=('other-glm','https://other.invalid')
    messages=[{'role':'system','content':'rules'},{'role':'user','content':'inspect','_origin':'owner'}]+exchange(1)
    view=context.prepare(messages,continuous=True)
    assert not any('reasoning_content' in m for m in view)
    assert messages[2]['reasoning_content']=='  ORIGINAL REASON 1\n'
