"""Bounded MCP stdio transport (2026 metadata and legacy initialization).
Only the owner connects servers. Server content never grants permissions.
"""
from __future__ import annotations
import collections,hashlib,json,os,queue,re,shutil,subprocess,threading,time
from pathlib import Path
from .settings import mask_secret,unmask_secret
MODERN='2026-07-28'
LEGACY=('2025-11-25','2025-06-18','2025-03-26','2024-11-05')
MAX_LINE=2*1024*1024
_ANSI_RE=re.compile(r'\x1b\[[0-9;]*[A-Za-z]')


class MCPError(RuntimeError):pass
class RPCError(MCPError):
    def __init__(self,error):self.error=error;super().__init__(str(error.get('message','MCP error'))[:1000])


def _check_command_available(command: str) -> None:
    p = Path(command)
    if p.is_file() or shutil.which(command):
        return
    low = p.name.lower()
    if low in ('npx', 'npx.cmd', 'npm', 'npm.cmd', 'node', 'node.exe'):
        raise MCPError(f'Δεν βρέθηκε το «{command}». Χρειάζεται εγκατάσταση Node.js (nodejs.org) για αυτόν τον MCP server.')
    if low in ('uvx', 'uvx.exe', 'uv', 'uv.exe'):
        raise MCPError(f'Δεν βρέθηκε το «{command}». Χρειάζεται εγκατάσταση του uv (docs.astral.sh/uv) για αυτόν τον MCP server.')
    raise MCPError(f'Δεν βρέθηκε η εντολή «{command}» στο σύστημα (PATH). Έλεγξε τη διαδρομή ή εγκατάστησε το πρόγραμμα.')


def _config_digest(entry: dict) -> str:
    core = {
        k: entry.get(k)
        for k in ('transport', 'command', 'args', 'cwd', 'url', 'env', 'headers', 'headersEnv', 'modes', 'readOnlyTools')
        if k in entry
    }
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


class StdioClient:
    def __init__(self,config,timeout=20):
        command=config.get('command');args=config.get('args',[])
        if not isinstance(command,str) or not command or not isinstance(args,list) or any(not isinstance(a,str) for a in args):raise ValueError('MCP command/args δεν είναι έγκυρα.')
        _check_command_available(command)
        self.lock=threading.RLock();self.queue=queue.Queue(maxsize=256);self.counter=0;self.version=MODERN;self.timeout=timeout;self.closed=False
        self._stderr_lines=collections.deque(maxlen=20);self._secrets=[]
        env={k:v for k,v in os.environ.items() if not any(term in k.upper() for term in ('API_KEY','API_TOKEN','SECRET','PASSWORD','AUTH_TOKEN'))}
        additions=config.get('env',{})
        if not isinstance(additions,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in additions.items()):raise ValueError('Μη έγκυρο MCP env.')
        for k,v in additions.items():
            real_v=unmask_secret(v)
            env[k]=real_v
            if len(real_v)>=4:self._secrets.append(real_v)
        self.process=subprocess.Popen([command,*args],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=config.get('cwd') or None,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        threading.Thread(target=self._stderr_reader,daemon=True).start()
        threading.Thread(target=self._reader,daemon=True).start()
        try:
            try:
                discovery=self.request('server/discover',{},timeout=min(2,timeout),probe=True)
                versions=discovery.get('supportedVersions',[])
                if MODERN not in versions:raise MCPError('Δεν υπάρχει κοινή σύγχρονη έκδοση MCP.')
            except RPCError as exc:
                if exc.error.get('code')==-32022:raise MCPError('Ο server δεν υποστηρίζει την έκδοση MCP 2026-07-28.') from exc
                self._legacy()
            except TimeoutError:self._legacy()
        except BaseException:self.close();raise
    def _stderr_reader(self):
        try:
            stream=self.process.stderr
            if stream is None:return
            while not self.closed:
                raw=stream.readline(4096)
                if not raw:break
                line=_ANSI_RE.sub('',raw.decode('utf-8',errors='replace')).strip()
                if line:self._stderr_lines.append(line[:240])
        except OSError:pass
    def _stderr_tail(self):
        text=' | '.join(list(self._stderr_lines)[-4:]).strip()
        for sec in self._secrets:
            if sec and sec in text:text=text.replace(sec,'***')
        return text[:450]
    def _legacy(self):
        self.version=LEGACY[0]
        result=self.request('initialize',{'protocolVersion':self.version,'capabilities':{},'clientInfo':{'name':'Akatikito','version':'0.1.0'}})
        if result.get('protocolVersion') not in LEGACY:raise MCPError('Μη υποστηριζόμενη έκδοση MCP.')
        self.version=result['protocolVersion'];self.notify('notifications/initialized',{})
    def _reader(self):
        try:
            while not self.closed:
                line=self.process.stdout.readline(MAX_LINE+1)
                if not line:
                    time.sleep(.03)
                    tail=self._stderr_tail()
                    raise MCPError('Ο MCP server έκλεισε.'+(f' Λεπτομέρειες: {tail}' if tail else ''))
                if len(line)>MAX_LINE or not line.endswith(b'\n'):raise MCPError('Πολύ μεγάλο ή μη έγκυρο MCP μήνυμα.')
                obj=json.loads(line)
                if not isinstance(obj,dict) or obj.get('jsonrpc')!='2.0':raise MCPError('Μη έγκυρο JSON-RPC.')
                self.queue.put_nowait(obj)
        except (OSError,ValueError,queue.Full,MCPError) as exc:
            try:self.queue.put_nowait(MCPError(str(exc)))
            except queue.Full: self.process.terminate()
    def _send(self,payload):
        if self.closed or self.process.poll() is not None:raise MCPError('Ο MCP server δεν τρέχει.')
        raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()+b'\n'
        if len(raw)>MAX_LINE:raise MCPError('Πολύ μεγάλο αίτημα MCP.')
        self.process.stdin.write(raw);self.process.stdin.flush()
    def notify(self,method,params):self._send({'jsonrpc':'2.0','method':method,'params':params})
    def request(self,method,params,stopped=lambda:False,timeout=None,probe=False):
        with self.lock:
            self.counter+=1;key=self.counter;params=dict(params)
            if self.version==MODERN:params['_meta']={'io.modelcontextprotocol/protocolVersion':MODERN,'io.modelcontextprotocol/clientCapabilities':{},'io.modelcontextprotocol/clientInfo':{'name':'Akatikito','version':'0.1.0'}}
            self._send({'jsonrpc':'2.0','id':key,'method':method,'params':params})
            deadline=time.monotonic()+(self.timeout if timeout is None else timeout)
            while True:
                if stopped() or time.monotonic()>deadline:
                    self.notify('notifications/cancelled',{'requestId':key,'reason':'cancelled or timed out'})
                    if not probe:self.close()
                    if stopped():raise MCPError('Ο χρήστης σταμάτησε το MCP εργαλείο.')
                    raise TimeoutError('Ο MCP server δεν απάντησε εγκαίρως.')
                try:item=self.queue.get(timeout=.05)
                except queue.Empty:continue
                if isinstance(item,Exception):raise item
                if 'method' in item:
                    if 'id' in item:
                        if self.version==MODERN:raise MCPError('Μη επιτρεπόμενο αίτημα server στη σύγχρονη μεταφορά.')
                        self._send({'jsonrpc':'2.0','id':item['id'],'error':{'code':-32601,'message':'Client capability is not enabled'}})
                    continue
                if item.get('id')!=key:continue
                if 'error' in item:raise RPCError(item['error'])
                result=item.get('result')
                if not isinstance(result,dict):raise MCPError('Μη έγκυρο αποτέλεσμα MCP.')
                if result.get('inputRequests'):raise MCPError('Αυτό το MCP εργαλείο ζητά μη ενεργοποιημένη δυνατότητα client.')
                return result
    def tools(self):
        rows=[];cursor=None;seen=set()
        for _ in range(10):
            result=self.request('tools/list',{'cursor':cursor} if cursor else {})
            tools=result.get('tools',[])
            if not isinstance(tools,list):raise MCPError('Μη έγκυρη λίστα εργαλείων.')
            rows.extend(tools)
            if len(rows)>200:raise MCPError('Όριο 200 MCP εργαλείων.')
            cursor=result.get('nextCursor')
            if not cursor:return rows
            if cursor in seen:raise MCPError('Επαναλαμβανόμενος δείκτης MCP.')
            seen.add(cursor)
        raise MCPError('Υπερβολική σελιδοποίηση MCP.')
    def close(self):
        if self.closed:return
        self.closed=True
        try:self.process.stdin.close()
        except (OSError,ValueError):pass
        try:self.process.wait(timeout=.3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=1)
        try:self.process.stdout.close()
        except OSError:pass
        try:
            if self.process.stderr is not None:self.process.stderr.close()
        except OSError:pass


class MCPHub:
    def __init__(self,path):self.path=Path(path);self.servers={};self.entries={};self.lock=threading.RLock();self.auto_warnings={}
    def configured(self):
        if not self.path.exists():return {}
        if self.path.stat().st_size>256000:raise ValueError('Πολύ μεγάλο mcp.json.')
        data=json.loads(self.path.read_text(encoding='utf-8-sig'));servers=data.get('servers',{})
        if not isinstance(servers,dict) or len(servers)>20:raise ValueError('Μη έγκυρο mcp.json.')
        return servers
    def public_configs(self):
        """Return configured servers with all env/header secret values stripped (keys only)."""
        out={}
        for name,cfg in self.configured().items():
            if not isinstance(cfg,dict):continue
            row={
                'transport':cfg.get('transport','stdio'),
                'command':cfg.get('command',''),
                'args':list(cfg.get('args') or []),
                'url':cfg.get('url',''),
                'readOnlyTools':list(cfg.get('readOnlyTools') or []),
                'modes':list(cfg.get('modes') or ['ask','assistant','code','debug','architect']),
                'autoConnect':bool(cfg.get('autoConnect',False)),
                'approved':bool(cfg.get('approvedHash') and cfg.get('approvedHash')==_config_digest(cfg)),
                'envKeys':sorted(str(k) for k in (cfg.get('env') or {}).keys()),
                'headerKeys':sorted(str(k) for k in (cfg.get('headers') or {}).keys()),
                'headersEnv':dict(cfg.get('headersEnv') or {}),
            }
            out[name]=row
        return out
    def server_tools(self):
        """Group active connected tools per server for UI display."""
        grouped={}
        for alias,row in self.entries.items():
            srv=row['server']
            if getattr(self.servers.get(srv),'closed',True):continue
            desc=str(row['spec']['function'].get('description') or '').removeprefix('[MCP εξωτερικό δεδομένο] ').strip()
            grouped.setdefault(srv,[]).append({
                'name':row['original'],
                'alias':alias,
                'readonly':bool(row['readonly']),
                'description':desc[:160],
            })
        return grouped
    def _write_servers(self,servers):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'servers':servers},ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(tmp,self.path)
    @staticmethod
    def _build_stdio_entry(config: dict, old: dict, modes: list, readonly: list, auto_connect: bool) -> dict:
        command = str(config.get("command") or "").strip()
        if not command:
            raise ValueError("Συμπλήρωσε την εντολή (command) του stdio server.")
        args = [str(a) for a in (config.get("args") or []) if str(a).strip()]
        raw_env = config.get("env") if isinstance(config.get("env"), dict) else {}
        old_env = old.get("env") if isinstance(old.get("env"), dict) else {}
        masked_env = {}
        for k, v in raw_env.items():
            key = str(k).strip()
            if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,120}", key):
                raise ValueError(f"Μη έγκυρο όνομα μεταβλητής περιβάλλοντος MCP: {key}")
            val = str(v)
            if not val and key in old_env:
                masked_env[key] = old_env[key]
            elif val:
                masked_env[key] = val if val.startswith("v1:") else mask_secret(val)
        entry = {
            "transport": "stdio", "command": command, "args": args, "env": masked_env,
            "modes": modes, "readOnlyTools": readonly, "autoConnect": auto_connect,
        }
        if config.get("cwd"):
            entry["cwd"] = str(config["cwd"])
        return entry

    @staticmethod
    def _build_http_entry(config: dict, old: dict, modes: list, readonly: list, auto_connect: bool) -> dict:
        from .mcp_http import auth_headers, endpoint

        url, _ = endpoint({"url": str(config.get("url") or "").strip()})
        headers_env = config.get("headersEnv") if isinstance(config.get("headersEnv"), dict) else {}
        raw_headers = config.get("headers") if isinstance(config.get("headers"), dict) else {}
        old_headers = old.get("headers") if isinstance(old.get("headers"), dict) else {}
        masked_headers = {}
        plain_for_check = {}
        for k, v in raw_headers.items():
            hname, hval = str(k).strip(), str(v).strip()
            if not hname:
                continue
            if not hval and hname in old_headers:
                masked_headers[hname] = old_headers[hname]
                plain_for_check[hname] = unmask_secret(old_headers[hname])
            elif hval:
                plain_for_check[hname] = unmask_secret(hval) if hval.startswith("v1:") else hval
                masked_headers[hname] = hval if hval.startswith("v1:") else mask_secret(hval)
        if plain_for_check or headers_env:
            auth_headers({"headers": plain_for_check, "headersEnv": {}})
        return {
            "transport": "http", "url": url, "headers": masked_headers, "headersEnv": headers_env,
            "modes": modes, "readOnlyTools": readonly, "autoConnect": auto_connect,
        }

    def save_server(self, name, config):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,30}", name):
            raise ValueError("Όνομα MCP server: 1–30 λατινικά γράμματα, αριθμοί, παύλες ή κάτω παύλες.")
        if not isinstance(config, dict):
            raise ValueError("Μη έγκυρες ρυθμίσεις MCP server.")
        transport = config.get("transport", "stdio")
        if transport not in ("stdio", "http", "streamable-http"):
            raise ValueError("Η μεταφορά πρέπει να είναι stdio ή http.")
        modes = config.get("modes") or ["ask", "assistant", "code", "debug", "architect"]
        if not isinstance(modes, list) or not all(m in ("ask", "assistant", "code", "debug", "architect") for m in modes):
            raise ValueError("Μη έγκυρες λειτουργίες MCP.")
        readonly = [str(x).strip() for x in (config.get("readOnlyTools") or []) if str(x).strip()]
        auto_connect = bool(config.get("autoConnect", False))
        with self.lock:
            servers = dict(self.configured())
            old = servers.get(name, {}) if isinstance(servers.get(name), dict) else {}
            entry = (
                self._build_stdio_entry(config, old, modes, readonly, auto_connect)
                if transport == "stdio"
                else self._build_http_entry(config, old, modes, readonly, auto_connect)
            )
            # Preserve approvedHash ONLY if core connection parameters did not change!
            if old.get("approvedHash") and _config_digest(entry) == old.get("approvedHash"):
                entry["approvedHash"] = old["approvedHash"]
            if name not in servers and len(servers) >= 20:
                raise ValueError("Όριο 20 MCP servers.")
            servers[name] = entry
            self._write_servers(servers)
            self.auto_warnings.pop(name, None)
        return entry
    def import_json(self,raw_text: str):
        """Import one or more MCP servers from Claude Desktop / Cursor / VS Code / Akatikito JSON."""
        if not isinstance(raw_text,str) or not raw_text.strip():
            raise ValueError('Επικόλλησε έγκυρο JSON διαμόρφωσης MCP.')
        if len(raw_text)>120000:
            raise ValueError('Το JSON είναι υπερβολικά μεγάλο (όριο 120 KB).')
        try:
            parsed=json.loads(raw_text)
        except ValueError as exc:
            raise ValueError(f'Μη έγκυρο JSON: {exc}') from exc
        if not isinstance(parsed,dict):
            raise ValueError('Το JSON πρέπει να είναι αντικείμενο (π.χ. {"mcpServers": {...}}).')
        if isinstance(parsed.get('mcpServers'),dict):
            source=parsed['mcpServers']
        elif isinstance(parsed.get('servers'),dict):
            source=parsed['servers']
        elif 'command' in parsed or 'url' in parsed:
            source={'imported-mcp':parsed}
        else:
            source=parsed
        imported=[]
        for raw_name,cfg in source.items():
            if not isinstance(cfg,dict):continue
            clean_name=re.sub(r'[^A-Za-z0-9_-]+','-',str(raw_name)).strip('-')[:30] or 'mcp-server'
            item=dict(cfg)
            if not item.get('transport'):
                item['transport']='http' if (item.get('url') and not item.get('command')) else 'stdio'
            if item['transport']=='stdio' and os.name=='nt':
                cmd=str(item.get('command') or '').strip()
                if cmd.lower()=='npx':item['command']='npx.cmd'
                elif cmd.lower()=='npm':item['command']='npm.cmd'
            self.save_server(clean_name,item)
            imported.append(clean_name)
        if not imported:
            raise ValueError('Δεν βρέθηκαν έγκυροι MCP servers στο JSON.')
        return {'ok':True,'imported':imported}
    def remove_server(self,name):
        with self.lock:
            self.disconnect(name)
            servers=dict(self.configured())
            if name not in servers:raise ValueError('Ο MCP server δεν βρέθηκε.')
            servers.pop(name,None)
            self._write_servers(servers)
            self.auto_warnings.pop(name,None)
        return {'ok':True}
    def _record_approval(self,name,config):
        with self.lock:
            servers=dict(self.configured())
            if name in servers and isinstance(servers[name],dict):
                servers[name]['approvedHash']=_config_digest(config)
                self._write_servers(servers)
    def connect(self,name,approve,stopped=lambda:False):
        config=self.configured().get(name)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,30}',name) or not isinstance(config,dict):raise ValueError('Άγνωστος MCP server.')
        transport=config.get('transport','stdio')
        if transport not in ('stdio','http','streamable-http'):raise ValueError('Μη υποστηριζόμενη μεταφορά MCP.')
        if transport=='stdio':
            details={'server':name,'transport':'stdio','command':config.get('command'),'args':config.get('args',[]),'cwd':config.get('cwd'),'environment_variables':list((config.get('env') or {}).keys())}
        else:
            from .mcp_http import endpoint
            url,_=endpoint(config)
            details={'server':name,'transport':'http','url':url,
                     'header_names':list((config.get('headers') or {}).keys())+list((config.get('headersEnv') or {}).keys())}
        if not approve(details):raise PermissionError('Ο χρήστης δεν ενέκρινε τη σύνδεση MCP.')
        # Use exactly the reviewed configuration, never reread it after approval.
        runtime_cfg=dict(config)
        if transport=='stdio':
            if isinstance(runtime_cfg.get('env'),dict):
                runtime_cfg['env']={k:unmask_secret(v) for k,v in runtime_cfg['env'].items()}
            client=StdioClient(runtime_cfg)
        else:
            from .mcp_http import HTTPClient
            if isinstance(runtime_cfg.get('headers'),dict):
                runtime_cfg['headers']={k:unmask_secret(v) for k,v in runtime_cfg['headers'].items()}
            client=HTTPClient(runtime_cfg,stopped=stopped)
        try:
            entries={}
            for tool in client.tools():
                original=tool.get('name','')
                if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',original):raise ValueError('Μη έγκυρο όνομα MCP εργαλείου.')
                alias='mcp_'+name+'_'+original.replace('.','_').replace('-','_')
                if len(alias)>64 or alias in entries:raise ValueError('Σύγκρουση ονομάτων MCP εργαλείων.')
                schema=tool.get('inputSchema',{'type':'object'})
                from jsonschema import Draft202012Validator
                Draft202012Validator.check_schema(schema)
                if '$ref' in json.dumps(schema):raise ValueError('Δεν υποστηρίζονται εξωτερικές αναφορές schema MCP.')
                readonly=original in config.get('readOnlyTools',[]) and tool.get('annotations',{}).get('readOnlyHint') is True
                entries[alias]={'server':name,'original':original,'readonly':readonly,'modes':config.get('modes',['code','debug','assistant','ask','architect']),
                    'spec':{'type':'function','function':{'name':alias,'description':'[MCP εξωτερικό δεδομένο] '+str(tool.get('description',''))[:2000], 'parameters':schema}}}
            with self.lock:
                self.disconnect(name);self.servers[name]=client;self.entries.update(entries)
                self.auto_warnings.pop(name,None)
            self._record_approval(name,config)
        except BaseException:client.close();raise
    def auto_connect(self):
        """Reconnect only servers with autoConnect=True whose exact config matches previously approvedHash."""
        connected=[]
        for name,cfg in list(self.configured().items()):
            if not isinstance(cfg,dict) or not cfg.get('autoConnect'):
                continue
            if name in self.servers and not getattr(self.servers[name],'closed',True):
                continue
            expected=_config_digest(cfg)
            if not cfg.get('approvedHash') or cfg.get('approvedHash')!=expected:
                self.auto_warnings[name]=['Οι ρυθμίσεις του MCP άλλαξαν — χρειάζεται νέα χειροκίνητη σύνδεση και έγκριση.']
                continue
            try:
                self.connect(name,lambda _details: True)
                connected.append(name)
            except Exception as exc:
                self.auto_warnings[name]=[f'Η αυτόματη επανασύνδεση απέτυχε: {exc}']
        return connected
    def names(self,mode):
        return [name for name,row in self.entries.items() if not getattr(self.servers.get(row['server']),'closed',True)
                and mode in row['modes'] and (row['readonly'] or mode in ('code','debug','assistant'))]
    def specs(self):return [row['spec'] for row in self.entries.values()]
    def call(self,name,args,agent):
        row=self.entries[name]
        if name not in self.names(agent.mode):raise PermissionError('Το MCP εργαλείο δεν επιτρέπεται στη λειτουργία.')
        if not row['readonly']:
            if agent.repair.pending or getattr(agent.ws,'update_validator',None):raise PermissionError('MCP μεταβολές δεν επιτρέπονται στη διόρθωση ή σε υποψήφιο αναβάθμισης.')
            gate=getattr(agent.ws,'external_guard',lambda *a:False)
            if not gate('mcp',{'server':row['server'],'tool':row['original'],'arguments':args}):return 'ΑΠΟΡΡΙΨΗ: [OWNER_DENIED] MCP εργαλείο.'
        from jsonschema import Draft202012Validator
        Draft202012Validator(row['spec']['function']['parameters']).validate(args)
        result=self.servers[row['server']].request('tools/call',{'name':row['original'],'arguments':args},stopped=lambda:agent._stop)
        text=json.dumps(result,ensure_ascii=False)
        if len(text)>16000:
            key=agent.context.store(text);text=text[:16000]+f'\n[πλήρες archive={key}]'
        return ('ΣΦΑΛΜΑ: ' if result.get('isError') else '')+text
    def disconnect(self,name):
        with self.lock:
            client=self.servers.pop(name,None)
            if client:client.close()
            self.entries={k:v for k,v in self.entries.items() if v['server']!=name}
    def close(self):
        for name in list(self.servers):self.disconnect(name)
