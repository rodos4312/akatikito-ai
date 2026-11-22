"""Real loopback MCP HTTP/JSON/SSE, no model or external service."""
from pathlib import Path
import http.server
import json
import base64
import sys
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akatikito.mcp_client import MCPHub,MCPError,MODERN,LEGACY
from akatikito.mcp_http import HTTPClient


@contextmanager
def server(legacy=False,sse=False,behavior='normal',tool_schema=None,extra_tools=()):
    calls=[];started=threading.Event();release=threading.Event()
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def send(self,status,obj,headers=None):
            raw=json.dumps(obj).encode();self.send_response(status)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
            for k,v in (headers or {}).items():self.send_header(k,v)
            self.end_headers()
            try:self.wfile.write(raw)
            except OSError:pass
        def do_DELETE(self):
            calls.append({'method':'DELETE','headers':dict(self.headers)})
            self.send(200,{})
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            method=body['method'];key=body.get('id');headers=dict(self.headers)
            calls.append({'method':method,'body':body,'headers':headers})
            if method=='server/discover' and legacy:
                if legacy=='plain':
                    self.send_response(400);self.send_header('Content-Length','0');self.end_headers();return
                if legacy=='version':self.send(400,{'jsonrpc':'2.0','id':key,'error':{'code':-32022,'message':'unsupported','data':{'supported':[LEGACY[0]]}}});return
                self.send(404,{'jsonrpc':'2.0','id':key,'error':{'code':-32601,'message':'unknown'}});return
            if method=='server/discover':result={'supportedVersions':[MODERN],'capabilities':{}}
            elif method=='initialize':result={'protocolVersion':LEGACY[0],'capabilities':{}}
            elif method=='notifications/initialized':self.send(202,{});return
            elif method=='tools/list':result={'tools':[*extra_tools,{'name':'echo','description':'Local echo','inputSchema':tool_schema or {'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False},'annotations':{'readOnlyHint':True}}]}
            elif method=='tools/call':
                started.set()
                if behavior=='redirect':self.send(302,{}, {'Location':'http://127.0.0.1:1/leak'});return
                if behavior=='expired':self.send(404,{});return
                if behavior=='auth':self.send(401,{'secret':'synthetic-secret'});return
                if behavior=='oversized':self.send(200,{'jsonrpc':'2.0','id':key,'result':{'text':'x'*(2*1024*1024+1)}});return
                if behavior=='rpc-error':self.send(400,{'jsonrpc':'2.0','id':key,'error':{'code':-32602,'message':'synthetic-secret'}});return
                if behavior=='input':result={'inputRequests':[{'method':'sampling/createMessage'}]}
                else:result={'content':[{'type':'text','text':body['params']['arguments']['text']} ]}
                if behavior=='wrong-id':key+=100
                if sse or behavior=='hang':
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                    try:
                        self.wfile.write(b': keepalive\n\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n');self.wfile.flush()
                        if behavior=='hang':
                            self.connection.settimeout(2)
                            if not self.connection.recv(1):calls.append({'method':'stream-closed'})
                            return
                        raw=json.dumps({'jsonrpc':'2.0','id':key,'result':result})
                        self.wfile.write(('event: message\ndata: '+raw+'\n\n').encode());self.wfile.flush()
                    except OSError:pass
                    return
            else:self.send(404,{});return
            self.send(200,{'jsonrpc':'2.0','id':key,'result':result}, {'Mcp-Session-Id':'local-session'} if method=='initialize' else {})
    listener=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=listener.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{listener.server_port}/mcp',calls,started
    finally:release.set();listener.shutdown();listener.server_close();thread.join(3)


def hub(tmp_path,url,**extra):
    path=tmp_path/'mcp.json'
    path.write_text(json.dumps({'servers':{'remote':{'transport':'http','url':url,'readOnlyTools':['echo'],**extra}}}))
    return MCPHub(path)


def agent():
    return SimpleNamespace(mode='ask',_stop=False,repair=SimpleNamespace(pending=False),ws=SimpleNamespace(),context=SimpleNamespace(store=lambda text:'archive'))


@pytest.mark.parametrize('sse',[False,True])
def test_modern_http_tools_and_required_headers(tmp_path,sse,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','do-not-send')
    with server(sse=sse) as (url,calls,_):
        h=hub(tmp_path,url);h.connect('remote',lambda details:True)
        try:
            assert 'hello' in h.call('mcp_remote_echo',{'text':'hello'},agent())
            for row in calls:
                assert row['headers']['MCP-Protocol-Version']==MODERN
                assert row['headers']['Mcp-Method']==row['method']
                assert row['body']['params']['_meta']['io.modelcontextprotocol/protocolVersion']==MODERN
                assert not any('do-not-send' in v for v in row['headers'].values())
            assert calls[-1]['headers']['Mcp-Name']=='echo'
            assert not any(r['method']=='initialize' for r in calls)
        finally:h.close()


@pytest.mark.parametrize('legacy',[True,'version','plain'])
def test_legacy_session_sse_and_delete(tmp_path,legacy):
    with server(legacy=legacy,sse=True) as (url,calls,_):
        h=hub(tmp_path,url);h.connect('remote',lambda d:True)
        assert 'Greek Ελληνικά' in h.call('mcp_remote_echo',{'text':'Greek Ελληνικά'},agent())
        h.close()
        assert [r['method'] for r in calls][:4]==['server/discover','initialize','notifications/initialized','tools/list']
        assert all(r['headers'].get('Mcp-Session-Id')=='local-session' for r in calls[2:])
        assert calls[-1]['method']=='DELETE'


def test_declined_connection_never_contacts_endpoint(tmp_path):
    with server() as (url,calls,_):
        h=hub(tmp_path,url)
        with pytest.raises(PermissionError):h.connect('remote',lambda d:False)
        assert not calls


def test_auth_is_explicit_and_approval_hides_values(tmp_path,monkeypatch):
    monkeypatch.setenv('SYNTHETIC_MCP_AUTH','Bearer synthetic-secret')
    with server() as (url,calls,_):
        details=[];h=hub(tmp_path,url,headersEnv={'Authorization':'SYNTHETIC_MCP_AUTH'})
        h.connect('remote',lambda d:details.append(d) or True)
        try:
            assert 'synthetic-secret' not in json.dumps(details)
            assert details[0]['url']==url and details[0]['header_names']==['Authorization']
            assert all(r['headers']['Authorization']=='Bearer synthetic-secret' for r in calls)
        finally:h.close()


@pytest.mark.parametrize('behavior',['redirect','wrong-id','oversized','auth','input'])
def test_invalid_responses_fail_without_replaying_the_tool(tmp_path,behavior):
    with server(behavior=behavior) as (url,calls,_):
        h=hub(tmp_path,url);h.connect('remote',lambda d:True)
        try:
            with pytest.raises(MCPError) as error:h.call('mcp_remote_echo',{'text':'hello'},agent())
            assert 'synthetic-secret' not in str(error.value)
            assert sum(r['method']=='tools/call' for r in calls)==1
            assert not h.names('ask')
        finally:h.close()


def test_rpc_errors_do_not_echo_secrets(tmp_path):
    with server(behavior='rpc-error') as (url,calls,_):
        h=hub(tmp_path,url);h.connect('remote',lambda d:True)
        try:
            with pytest.raises(MCPError) as error:h.call('mcp_remote_echo',{'text':'hello'},agent())
            assert 'synthetic-secret' not in str(error.value)
        finally:h.close()


def test_expired_legacy_session_does_not_repeat_side_effects(tmp_path):
    with server(legacy=True,behavior='expired') as (url,calls,_):
        h=hub(tmp_path,url);h.connect('remote',lambda d:True)
        try:
            with pytest.raises(MCPError,match='επανασύνδεση'):h.call('mcp_remote_echo',{'text':'hello'},agent())
            assert sum(r['method']=='initialize' for r in calls)==1
            assert sum(r['method']=='tools/call' for r in calls)==1
        finally:h.close()


def test_stop_closes_a_live_sse_request_without_another_model_call(tmp_path):
    with server(behavior='hang') as (url,calls,started):
        h=hub(tmp_path,url);h.connect('remote',lambda d:True);a=agent();errors=[]
        def run():
            try:h.call('mcp_remote_echo',{'text':'hello'},a)
            except MCPError as exc:errors.append(str(exc))
        thread=threading.Thread(target=run);thread.start();assert started.wait(2)
        begin=time.monotonic();a._stop=True;thread.join(2)
        assert not thread.is_alive() and errors and time.monotonic()-begin<2
        deadline=time.monotonic()+1
        while not any(r['method']=='stream-closed' for r in calls) and time.monotonic()<deadline:time.sleep(.01)
        assert any(r['method']=='stream-closed' for r in calls)
        assert sum(r['method']=='tools/call' for r in calls)==1
        assert not h.names('ask');h.close()


def test_timeout_is_bounded_and_not_retried(tmp_path):
    with server(behavior='hang') as (url,calls,_):
        h=hub(tmp_path,url,timeout=.25);h.connect('remote',lambda d:True)
        begin=time.monotonic()
        with pytest.raises(TimeoutError):h.call('mcp_remote_echo',{'text':'hello'},agent())
        assert time.monotonic()-begin<2 and sum(r['method']=='tools/call' for r in calls)==1
        h.close()


def test_stop_during_connect_never_sends_the_tool_afterwards(tmp_path,monkeypatch):
    with server() as (url,calls,_):
        h=hub(tmp_path,url);h.connect('remote',lambda d:True);client=h.servers['remote'];a=agent();factory=client._connection
        def connection():
            conn=factory();original=conn.connect
            def connect_then_stop():original();a._stop=True
            conn.connect=connect_then_stop;return conn
        monkeypatch.setattr(client,'_connection',connection)
        with pytest.raises(MCPError):h.call('mcp_remote_echo',{'text':'hello'},a)
        assert not any(r['method']=='tools/call' for r in calls);h.close()


def test_closed_connection_is_never_automatically_reopened_before_send(tmp_path,monkeypatch):
    with server() as (url,calls,_):
        h=hub(tmp_path,url);h.connect('remote',lambda d:True);client=h.servers['remote'];factory=client._connection
        def connection():
            conn=factory();original=conn.request
            def close_before_send(*args,**kwargs):client.close();return original(*args,**kwargs)
            conn.request=close_before_send;return conn
        monkeypatch.setattr(client,'_connection',connection)
        with pytest.raises(MCPError):h.call('mcp_remote_echo',{'text':'hello'},agent())
        assert not any(r['method']=='tools/call' for r in calls);h.close()


@pytest.mark.parametrize('url',['http://example.com/mcp','https://u:p@example.com/mcp','https://example.com/mcp?key=x','https://example.com/mcp#secret','file:///D:/x'])
def test_unsafe_endpoint_configuration_is_rejected_without_network(url):
    with pytest.raises(ValueError):HTTPClient({'url':url})


def test_mutating_http_tool_still_requires_owner_approval(tmp_path):
    with server() as (url,calls,_):
        h=hub(tmp_path,url,readOnlyTools=[]);h.connect('remote',lambda d:True)
        a=agent();a.mode='code';a.ws.external_guard=lambda *a:False
        assert '[OWNER_DENIED]' in h.call('mcp_remote_echo',{'text':'hello'},a)
        assert not any(r['method']=='tools/call' for r in calls);h.close()


def test_nested_annotated_headers_use_exact_values_and_safe_encoding(tmp_path):
    schema={'type':'object','properties':{'text':{'type':'string'},
        'tenant':{'type':'object','properties':{'region':{'type':'string','x-mcp-header':'Region'}}},
        'count':{'type':'integer','x-mcp-header':'Count'},'enabled':{'type':'boolean','x-mcp-header':'Enabled'},
        'optional':{'type':['string','null'],'x-mcp-header':'Optional'}}}
    with server(tool_schema=schema) as (url,calls,_):
        h=hub(tmp_path,url);h.connect('remote',lambda d:True)
        try:
            args={'text':'ok','tenant':{'region':'Ελλάδα\nμε νέα γραμμή'},'count':3,'enabled':True,'optional':None}
            assert 'ok' in h.call('mcp_remote_echo',args,agent())
            headers=calls[-1]['headers'];value=headers['Mcp-Param-Region']
            assert value.startswith('=?base64?') and value.endswith('?=')
            assert base64.b64decode(value[9:-2]).decode()==args['tenant']['region']
            assert headers['Mcp-Param-Count']=='3' and headers['Mcp-Param-Enabled']=='true'
            assert 'Mcp-Param-Optional' not in headers
            assert 'x-mcp-header' not in json.dumps(h.specs())
        finally:h.close()


@pytest.mark.parametrize('schema',[
    {'type':'object','properties':{'a':{'type':'string','x-mcp-header':'bad\r\nheader'}}},
    {'type':'object','properties':{'a':{'type':'number','x-mcp-header':'Count'}}},
    {'type':'object','properties':{'a':{'type':'string','x-mcp-header':'X'},'b':{'type':'string','x-mcp-header':'x'}}},
    {'type':'object','properties':{'rows':{'type':'array','items':{'type':'string','x-mcp-header':'X'}}}},
])
def test_invalid_header_tool_is_excluded_without_hiding_valid_tools(tmp_path,schema):
    bad={'name':'bad','inputSchema':schema}
    with server(extra_tools=[bad]) as (url,calls,_):
        h=hub(tmp_path,url);h.connect('remote',lambda d:True)
        assert set(h.entries)=={'mcp_remote_echo'} and h.servers['remote'].warnings
        h.close()


def test_unsafe_integer_header_is_rejected_before_sending_tool(tmp_path):
    schema={'type':'object','properties':{'text':{'type':'string'},'count':{'type':'integer','x-mcp-header':'Count'}}}
    with server(tool_schema=schema) as (url,calls,_):
        h=hub(tmp_path,url);h.connect('remote',lambda d:True)
        with pytest.raises(MCPError):h.call('mcp_remote_echo',{'text':'hello','count':2**60},agent())
        assert not any(r['method']=='tools/call' for r in calls);h.close()
