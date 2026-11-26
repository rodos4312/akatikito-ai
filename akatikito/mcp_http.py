"""Request-scoped Streamable HTTP MCP; no redirects, retries or model calls."""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import socket
import threading
import time
from urllib.parse import urlsplit

from .mcp_client import MCPError,RPCError,StdioClient,MODERN,LEGACY,MAX_LINE


class LegacyEndpoint(MCPError):pass


def endpoint(config):
    value=config.get('url','')
    if not isinstance(value,str) or len(value)>4096 or any(ord(c)<=32 for c in value):raise ValueError('Μη έγκυρη διεύθυνση HTTP MCP.')
    try:
        parts=urlsplit(value)
        if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.fragment or parts.query:
            raise ValueError()
        port=parts.port
        local=parts.hostname.casefold()=='localhost'
        try:local=local or ipaddress.ip_address(parts.hostname).is_loopback
        except ValueError:pass
        if parts.scheme=='http' and not local:raise ValueError()
    except ValueError as exc:
        raise ValueError('HTTP MCP: χρειάζεται HTTPS ή τοπικό loopback HTTP, χωρίς credentials/query/fragment στο URL.') from exc
    return value,parts


def auth_headers(config):
    result={}
    for field,from_env in (('headers',False),('headersEnv',True)):
        mapping=config.get(field,{})
        if not isinstance(mapping,dict) or len(mapping)>20:raise ValueError('Μη έγκυρα headers HTTP MCP.')
        for name,value in mapping.items():
            if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,79}',name):raise ValueError('Μη έγκυρο όνομα header MCP.')
            if name.casefold().startswith('mcp-param-') or name.casefold() in {'host','content-length','content-type','accept','connection','transfer-encoding','mcp-session-id','mcp-protocol-version','mcp-method','mcp-name'}:
                raise ValueError('Το header MCP δεσμεύεται από τη μεταφορά.')
            if not isinstance(value,str):raise ValueError('Μη έγκυρη τιμή header MCP.')
            if from_env:
                value=os.environ.get(value,'')
                if not value:raise ValueError('Λείπει μεταβλητή περιβάλλοντος για HTTP MCP authentication.')
            if len(value)>8192 or any(ord(c)<32 or ord(c)>126 for c in value):raise ValueError('Μη έγκυρη τιμή header MCP.')
            if name.casefold() in {n.casefold() for n in result}:raise ValueError('Διπλό header MCP.')
            result[name]=value
    return result


class HTTPClient(StdioClient):
    """Reuse tool pagination/legacy initialization, with an independent HTTP channel."""
    def __init__(self,config,timeout=20,stopped=lambda:False):
        self.url,self.parts=endpoint(config)
        self.headers=auth_headers(config)
        self.timeout=max(.1,min(60,float(config.get('timeout',timeout))))
        self.stopped=stopped
        self.tool_headers={};self.warnings=[]
        self.lock=threading.RLock();self.state_lock=threading.RLock()
        self.counter=0;self.version=MODERN;self.session='';self.closed=False;self.active=None;self.active_socket=None
        try:
            try:
                discovery=self.request('server/discover',{},probe=True)
                if MODERN not in discovery.get('supportedVersions',[]):self._legacy()
            except RPCError as exc:
                supported=exc.error.get('data',{}).get('supported',[])
                if exc.error.get('code')==-32601 or exc.error.get('code')==-32022 and any(v in LEGACY for v in supported):
                    self._legacy()
                else:raise
            except LegacyEndpoint:self._legacy()
        except BaseException:
            self.close();raise

    def _legacy(self):
        self.version=LEGACY[0]
        result=self.request('initialize',{'protocolVersion':self.version,'capabilities':{},
            'clientInfo':{'name':'Akatikito','version':'0.1.0'}})
        if result.get('protocolVersion') not in LEGACY[:-1]:raise MCPError('Το HTTP MCP απαιτεί έκδοση 2025-03-26 ή νεότερη.')
        self.version=result['protocolVersion'];self.notify('notifications/initialized',{})

    def tools(self):
        rows=super().tools();valid=[]
        from .mcp_headers import definition
        for row in rows:
            try:
                schema=row.get('inputSchema',{'type':'object'})
                if not isinstance(schema,dict) or schema.get('type')!='object':raise ValueError('MCP inputSchema requires object')
                clean,annotations=definition(schema)
            except (ValueError,TypeError,RecursionError):
                self.warnings.append(str(row.get('name',''))[:80]+': απορρίφθηκε μη έγκυρο x-mcp-header.')
                continue
            self.tool_headers[row.get('name')]=annotations
            valid.append({**row,'inputSchema':clean})
        return valid

    def _connection(self,timeout=None):
        cls=http.client.HTTPSConnection if self.parts.scheme=='https' else http.client.HTTPConnection
        return cls(self.parts.hostname,self.parts.port,timeout=self.timeout if timeout is None else timeout)

    def _response(self,obj,key,status):
        if not isinstance(obj,dict) or obj.get('jsonrpc')!='2.0':raise MCPError('Μη έγκυρο HTTP JSON-RPC.')
        if 'method' in obj:
            if 'id' in obj:raise MCPError('Ο MCP server ζητά μη ενεργοποιημένη δυνατότητα client.')
            return None
        if obj.get('id')!=key and not (status>=400 and obj.get('id') is None):raise MCPError('Λανθασμένο id απόκρισης HTTP MCP.')
        if 'error' in obj:
            error=obj['error']
            if not isinstance(error,dict):raise MCPError('Μη έγκυρο σφάλμα HTTP MCP.')
            data=error.get('data',{})
            data=data if isinstance(data,dict) else {}
            # A remote error may echo credentials; return only structured negotiation data.
            raise RPCError({'code':error.get('code'),'message':'Σφάλμα JSON-RPC από HTTP MCP server.',
                            'data':{'supported':data.get('supported',data.get('supportedVersions',[]))}})
        if status>=400:raise MCPError(f'HTTP MCP: HTTP {status}.')
        result=obj.get('result')
        if not isinstance(result,dict):raise MCPError('Μη έγκυρο αποτέλεσμα HTTP MCP.')
        if result.get('inputRequests'):raise MCPError('Ο MCP server ζητά μη ενεργοποιημένη δυνατότητα client.')
        return result

    def _exchange_headers(self, payload: dict, method: str) -> dict:
        headers = dict(self.headers)
        headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.version,
            "Mcp-Method": method,
        })
        if method in ("tools/call", "resources/read", "prompts/get"):
            from .mcp_headers import encoded, values

            headers["Mcp-Name"] = encoded(payload["params"].get("name") or payload["params"].get("uri", ""))
            if method == "tools/call" and self.version == MODERN:
                name = payload["params"].get("name")
                if name not in self.tool_headers:
                    raise MCPError("Χρειάζεται tools/list πριν από HTTP tools/call.")
                headers.update(values(self.tool_headers[name], payload["params"].get("arguments", {})))
        if self.version != MODERN and self.session:
            headers["Mcp-Session-Id"] = self.session
        return headers

    def _read_json_response(self, response, key, status: int, legacy_probe: bool):
        raw = response.read(MAX_LINE + 1)
        if len(raw) > MAX_LINE:
            raise MCPError("Πολύ μεγάλο αποτέλεσμα HTTP MCP.")
        try:
            obj = json.loads(raw)
        except ValueError:
            if legacy_probe:
                raise LegacyEndpoint("Χρειάζεται αρχικοποίηση legacy HTTP MCP.") from None
            raise
        if legacy_probe and (not isinstance(obj, dict) or obj.get("jsonrpc") != "2.0"):
            raise LegacyEndpoint("Χρειάζεται αρχικοποίηση legacy HTTP MCP.")
        return self._response(obj, key, status)

    def _read_sse_response(self, response, key, status: int):
        total = 0
        data: list[str] = []
        result = None
        while result is None:
            line = response.readline(MAX_LINE + 1)
            total += len(line)
            if total > MAX_LINE:
                raise MCPError("Πολύ μεγάλη ροή HTTP MCP.")
            if not line:
                raise MCPError("Η ροή HTTP MCP έκλεισε πριν από τελική απόκριση.")
            text = line.decode("utf-8-sig").rstrip("\r\n")
            if not text:
                if data:
                    result = self._response(json.loads("\n".join(data)), key, status)
                data = []
            elif text.startswith("data:"):
                data.append(text[5:].removeprefix(" "))
        return result

    def _parse_http_response(self, response, method: str, key):
        status = response.status
        if 300 <= status < 400:
            raise MCPError("Απορρίφθηκε ανακατεύθυνση HTTP MCP· δεν προωθήθηκαν credentials.")
        if status in (401, 403):
            raise MCPError(f"HTTP MCP: HTTP {status}· έλεγξε τα credentials του server.")
        if key is None:
            if status != 202:
                raise MCPError(f"HTTP MCP notification: HTTP {status}.")
            return {}
        if status == 404 and self.session:
            raise MCPError("Η συνεδρία HTTP MCP έληξε. Χρειάζεται επανασύνδεση· η κλήση δεν επαναλήφθηκε.")
        content = response.getheader("Content-Type", "").split(";")[0].strip().casefold()
        legacy_probe = method == "server/discover" and status in (400, 404, 405)
        if legacy_probe and content != "application/json":
            raise LegacyEndpoint("Χρειάζεται αρχικοποίηση legacy HTTP MCP.")
        if content == "application/json":
            result = self._read_json_response(response, key, status, legacy_probe)
        elif content == "text/event-stream" and status < 400:
            result = self._read_sse_response(response, key, status)
        else:
            raise MCPError(f"Μη υποστηριζόμενη απόκριση HTTP MCP (HTTP {status}).")
        if result is None:
            raise MCPError("Απουσιάζει τελική απόκριση HTTP MCP.")
        if method == "initialize":
            session = response.getheader("Mcp-Session-Id", "")
            if session and (len(session) > 1024 or any(ord(c) < 33 or ord(c) > 126 for c in session)):
                raise MCPError("Μη έγκυρη συνεδρία HTTP MCP.")
            self.session = session
        return result

    def _exchange(self, payload, key, cancelled, stopped):
        method = payload["method"]
        headers = self._exchange_headers(payload, method)
        conn = self._connection()
        conn.auto_open = 0
        response = None
        with self.state_lock:
            if self.closed or cancelled.is_set() or stopped():
                raise MCPError("Η σύνδεση HTTP MCP έκλεισε.")
            self.active = conn
        try:
            # Resolve/connect before sending, so Stop during DNS cannot later send a tool call.
            conn.connect()
            with self.state_lock:
                if self.closed or cancelled.is_set() or stopped():
                    raise MCPError("Η σύνδεση HTTP MCP έκλεισε.")
                self.active_socket = conn.sock
            conn.request("POST", self.parts.path or "/", json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers)
            response = conn.getresponse()
            return self._parse_http_response(response, method, key)
        finally:
            if response is not None:
                response.close()
            conn.close()
            with self.state_lock:
                if self.active is conn:
                    self.active = None
                    self.active_socket = None

    def _request(self,method,params,stopped,timeout,notification=False,probe=False):
        with self.lock:
            if self.closed:raise MCPError('Η σύνδεση HTTP MCP έκλεισε· χρειάζεται επανασύνδεση.')
            if stopped():self.close();raise MCPError('Ο χρήστης σταμάτησε το HTTP MCP εργαλείο.')
            self.counter+=1;key=None if notification else self.counter;params=dict(params)
            if self.version==MODERN:
                params['_meta']={'io.modelcontextprotocol/protocolVersion':MODERN,
                    'io.modelcontextprotocol/clientCapabilities':{},
                    'io.modelcontextprotocol/clientInfo':{'name':'Akatikito','version':'0.1.0'}}
            payload={'jsonrpc':'2.0','method':method,'params':params}
            if key is not None:payload['id']=key
            if len(json.dumps(payload).encode('utf-8'))>MAX_LINE:raise MCPError('Πολύ μεγάλο αίτημα HTTP MCP.')
            done=threading.Event();cancelled=threading.Event();box=[]
            def work():
                try:box.append((True,self._exchange(payload,key,cancelled,stopped)))
                except Exception as exc:box.append((False,exc))
                finally:done.set()
            threading.Thread(target=work,daemon=True,name='akatikito-mcp-http').start()
            deadline=time.monotonic()+(self.timeout if timeout is None else timeout)
            while not done.wait(.03):
                if stopped() or time.monotonic()>deadline:
                    cancelled.set();self.close()
                    if stopped():raise MCPError('Ο χρήστης σταμάτησε το HTTP MCP εργαλείο.')
                    raise TimeoutError('Ο HTTP MCP server δεν απάντησε εγκαίρως· η κλήση δεν επαναλήφθηκε.')
            if not box:self.close();raise MCPError('Η μεταφορά HTTP MCP απέτυχε.')
            ok,result=box[0]
            if not ok:
                if isinstance(result,RPCError):raise result
                if isinstance(result,LegacyEndpoint) and probe:raise result
                self.close()
                if isinstance(result,TimeoutError):raise TimeoutError('Ο HTTP MCP server δεν απάντησε εγκαίρως· η κλήση δεν επαναλήφθηκε.') from None
                if isinstance(result,MCPError):raise result
                raise MCPError('Αποτυχία μεταφοράς HTTP MCP· δεν έγινε αυτόματη επανάληψη.') from None
            return result

    def request(self,method,params,stopped=None,timeout=None,probe=False):
        return self._request(method,params,self.stopped if stopped is None else stopped,timeout,probe=probe)

    def notify(self,method,params):
        return self._request(method,params,self.stopped,None,notification=True)

    def close(self):
        with self.state_lock:
            if self.closed:return
            self.closed=True;conn=self.active;sock=self.active_socket;session=self.session;self.session=''
            if conn:
                try:
                    if sock:sock.shutdown(socket.SHUT_RDWR)
                    elif conn.sock:conn.sock.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                conn.close()
        if session:
            cleanup=self._connection(.3)
            try:
                cleanup.request('DELETE',self.parts.path or '/',headers={**self.headers,
                    'Mcp-Session-Id':session,'MCP-Protocol-Version':self.version})
                cleanup.getresponse().close()
            except (OSError,http.client.HTTPException):pass
            finally:cleanup.close()
