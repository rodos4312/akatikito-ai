"""Extensions tested with local subprocesses and scripted replies, never providers."""
import base64,io,json,sys,threading,time
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akatikito.attachments import Attachments
from akatikito.context import ContextWindow
from akatikito.workspace import Workspace
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel,tool_call
from akatikito.usage_ledger import UsageLedger
from akatikito.mcp_client import StdioClient,MCPHub,MCPError
from akatikito import modes


def test_attachments_are_explicit_and_vision_checked(tmp_path):
    ws=Workspace(tmp_path);store=Attachments(ws);archive={};ws.context_store=lambda text:archive.setdefault('raw',text) and 'raw'
    raw='χρήσιμο κείμενο'.encode();item=store.upload('original.md',base64.b64encode(raw).decode())
    assert archive=={} # staging alone must never extract/send
    blocks,names=store.content([item]);assert names==['original.md'] and 'χρήσιμο' in blocks[0]['text']
    from PIL import Image
    image=io.BytesIO();Image.new('RGB',(2,2),'red').save(image,format='PNG')
    img=store.upload('a.png',base64.b64encode(image.getvalue()).decode())
    with pytest.raises(ValueError,match='vision'):store.content([img])
    blocks,_=store.content([img],True);assert blocks[0]['image_url']['url'].startswith('data:image/png;base64,')
    captured=[]
    class Model:
        keeps_active_history=True
        def chat(self,messages,tools=None,**kwargs):captured.append(messages);return {'content':'Είδα την εικόνα'},{'prompt_tokens':1}
    agent=Akatikito(str(tmp_path),Config(verify_command=' ',stream_responses=False),Model());agent.chat('Εξήγησε',attachments=blocks)
    user=next(m for m in captured[0] if m['role']=='user')
    assert isinstance(user['content'],list) and any(x.get('type')=='image_url' for x in user['content'])
    assert any(x.get('type')=='text' and 'ΕΞΩΤΕΡΙΚΗ' in x.get('text','') for x in user['content'])
    with pytest.raises(ValueError):store.content([{'id':'../secret'}],True)
    with pytest.raises(ValueError):store.upload('bad.exe',base64.b64encode(b'x').decode())


def test_ledger_exact_cached_cost_session_day_and_budget(tmp_path):
    ledger=UsageLedger(tmp_path/'usage.json');prices={'input':2,'cached':.2,'output':4}
    assert ledger.record('one',{'prompt_tokens':1000,'cache_hit_tokens':800,'completion_tokens':100},prices)==pytest.approx(.00096)
    ledger.record('two',{'prompt_tokens':500,'completion_tokens':50},prices)
    view=ledger.view('one');assert view['session']['calls']==1 and view['day']['calls']==2 and view['day']['prompt_tokens']==1500
    assert ledger.check(.0001,'one',prices) and not ledger.check(2,'one',prices)
    ledger.record('unknown',{'prompt_tokens':20});assert ledger.view('unknown')['session']['unknown']==1 and ledger.check(2,'one',prices)
    assert ledger.check(2,'one',None) and not ledger.check(0,'one',None)


def test_budget_stops_before_another_model_call(tmp_path):
    model=ScriptedModel([{'content':'should not run'}]);agent=Akatikito(str(tmp_path),Config(verify_command=' '),model)
    agent.before_model=lambda:'Έφτασες το ημερήσιο όριο κόστους'
    assert 'όριο' in agent.chat('Εξήγησε') and agent.turn_usage['model_calls']==0


SERVER = r"""
import sys,json,time
modern=sys.argv[1]=='modern'
for line in sys.stdin:
    msg=json.loads(line);method=msg.get('method');key=msg.get('id')
    if key is None:continue
    if method=='server/discover':
        if modern:result={'supportedVersions':['2026-07-28'],'capabilities':{'tools':{}}}
        else:
            print(json.dumps({'jsonrpc':'2.0','id':key,'error':{'code':-32602,'message':'initialize first'}}),flush=True);continue
    elif method=='initialize':result={'protocolVersion':'2025-11-25','capabilities':{'tools':{}}}
    elif method=='tools/list':result={'tools':[{'name':'read','description':'Read fixture','inputSchema':{'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False},'annotations':{'readOnlyHint':True}},{'name':'write','description':'Write fixture','inputSchema':{'type':'object'}}]}
    elif method=='tools/call':
        if msg['params']['arguments'].get('sleep'):time.sleep(30)
        result={'content':[{'type':'text','text':msg['params']['name']}],'meta_seen':msg['params'].get('_meta',{}).get('io.modelcontextprotocol/protocolVersion')}
    else:result={}
    print(json.dumps({'jsonrpc':'2.0','id':key,'result':result}),flush=True)
"""


@pytest.mark.parametrize('era',['modern','legacy'])
def test_real_mcp_stdio_negotiation_schema_and_permissions(tmp_path,era):
    script=tmp_path/'server.py';script.write_text(SERVER)
    config={'command':sys.executable,'args':['-B','-u',str(script),era],'readOnlyTools':['read']}
    path=tmp_path/'mcp.json';path.write_text(json.dumps({'servers':{'local':config}}))
    hub=MCPHub(path)
    with pytest.raises(PermissionError):hub.connect('local',lambda details:False)
    assert not hub.servers
    hub.connect('local',lambda details:True)
    try:
        assert 'mcp_local_read' in hub.names('ask') and 'mcp_local_write' not in hub.names('ask')
        ws=Workspace(tmp_path);a=Akatikito(str(tmp_path),Config(verify_command=' '),ScriptedModel([]));a.mcp=hub;a.mode='ask';a._turn_tools=a.allowed_tools()
        out,_=a._execute_call({'name':'mcp_local_read','arguments':{'value':'ok'}},a._turn_tools)
        assert 'read' in out and 'ΣΦΑΛΜΑ' not in out
        if era=='modern':assert '2026-07-28' in out
        bad,_=a._execute_call({'name':'mcp_local_read','arguments':{'value':3}},a._turn_tools);assert bad.startswith('ΣΦΑΛΜΑ')
        a.mode='code';a._turn_tools=a.allowed_tools();ws=a.ws;ws.external_guard=lambda *args:False
        denied,_=a._execute_call({'name':'mcp_local_write','arguments':{}},a._turn_tools);assert 'OWNER_DENIED' in denied
        a._owner_denied=False # a new explicit owner attempt after denial
        ws.external_guard=lambda *args:True
        allowed,_=a._execute_call({'name':'mcp_local_write','arguments':{}},a._turn_tools);assert 'write' in allowed
        ws.update_validator=object()
        denied,_=a._execute_call({'name':'mcp_local_write','arguments':{}},a._turn_tools);assert 'ΑΠΟΡΡΙΨΗ' in denied
    finally:hub.close()
    assert not hub.servers


def test_mcp_stop_terminates_unresponsive_process(tmp_path):
    script=tmp_path/'server.py';script.write_text(SERVER)
    client=StdioClient({'command':sys.executable,'args':['-B','-u',str(script),'modern']})
    stop=threading.Event();threading.Timer(.15,stop.set).start()
    with pytest.raises(MCPError,match='σταμάτησε'):client.request('tools/call',{'name':'write','arguments':{'sleep':True}},stopped=stop.is_set)
    assert client.process.poll() is not None


def test_subtask_isolated_history_aggregates_usage_once(tmp_path):
    class Model:
        def __init__(self):self.requests=[]
        def chat(self,messages,tools=None,**kwargs):
            self.requests.append(messages)
            if len(self.requests)==1:return {'tool_calls':[{'id':'delegate','name':'run_subtask','arguments':json.dumps({'task':'Διάβασε το a.txt και δώσε περίληψη','mode':'ask'},ensure_ascii=False)}]},{'prompt_tokens':10,'completion_tokens':2}
            if len(self.requests)==2:
                assert not any(m.get('content')=='parent private old context' for m in messages)
                return {'content':'Περίληψη υποεργασίας'},{'prompt_tokens':20,'completion_tokens':3}
            return {'content':'Ολοκληρώθηκε η ανάλυση'},{'prompt_tokens':30,'completion_tokens':4}
    model=Model();a=Akatikito(str(tmp_path),Config(verify_command=' ',stream_responses=False),model)
    a.messages=[{'role':'system','content':'old policy'},{'role':'user','content':'parent private old context'}]
    a.chat('Διάβασε και εξήγησε το project')
    assert len(model.requests)==3 and a.tokens['prompt_tokens']==60 and a.turn_usage['model_calls']==3
    assert a.subtasks[0]['status']=='complete' and a.subtasks[0]['archive']
    a._child_depth=1;assert 'βάθος' in a._run_subtask('x','ask')
    a._child_depth=0;a.mode='ask';a._turn_tools=a.allowed_tools();assert 'δικαιώματα' in a._run_subtask('x','code')


def test_subtask_inherits_command_denial_and_stop(tmp_path):
    model=ScriptedModel([{'tool_calls':[tool_call('run_command',command='echo side-effect')]},{'content':'Η εντολή απορρίφθηκε'}])
    a=Akatikito(str(tmp_path),Config(verify_command=' '),model);a.mode='code';a._turn_tools=a.allowed_tools();a._task_writes=set();a._task_reads=set();a._task_checks=0;a._mutation_generation=0;a._verified_generation=-1
    a.ws.command_guard=lambda *args:False
    result=json.loads(a._run_subtask('Τρέξε echo για δοκιμή','code'))
    archive=a.context._load(result['archive'])
    assert 'OWNER_DENIED' in json.dumps(archive,ensure_ascii=False)
    child=SimpleNamespace(stop=lambda:setattr(a,'child_stopped',True));a._active_child=child;a.stop();assert a.child_stopped


def test_owner_denial_stops_remaining_writes_and_model_calls(tmp_path):
    model=ScriptedModel([{'tool_calls':[tool_call('write_file',path='one.txt',content='one'),tool_call('write_file',path='two.txt',content='two')]},{'content':'must not run'}])
    a=Akatikito(str(tmp_path),Config(verify_command=' '),model);reviewed=[]
    def review(*args):reviewed.append(args);return False
    a.ws.review_write=review
    result=a.chat('Γράψε δύο αρχεία')
    assert len(reviewed)==1 and model.calls==1 and 'δεν ενέκρινε' in result
    assert not (tmp_path/'one.txt').exists() and not (tmp_path/'two.txt').exists()
