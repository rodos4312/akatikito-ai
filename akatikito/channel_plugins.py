"""Reviewed, immutable channel packages from the bundled catalog or pinned GitHub source."""
from __future__ import annotations
import ast,hashlib,importlib.util,json,re,shutil,time,uuid
from pathlib import Path,PurePosixPath
from urllib.parse import urlparse,unquote
import httpx
from .local_store import checked,load_json,save_json
from .process_lock import FileLock

BUNDLES=Path(__file__).with_name('channel_bundles')
ID=re.compile(r'[a-z][a-z0-9-]{1,39}')
class PluginError(ValueError):pass

def manifest(raw):
    try:value=json.loads(raw)
    except (ValueError,UnicodeError):raise PluginError('Μη έγκυρο manifest πρόσθετου.') from None
    if (not isinstance(value,dict) or not ID.fullmatch(str(value.get('id','')))
            or value.get('kind')!='channel' or value.get('api_version')!=1
            or not all(isinstance(value.get(k),str) and 0<len(value[k])<=200 for k in ('name','version','entry'))
            or not isinstance(value.get('files'),list) or not 1<=len(value['files'])<=16
            or not isinstance(value.get('permissions'),list) or not 1<=len(value['permissions'])<=20
            or not all(isinstance(x,str) and 0<len(x)<=300 for x in value['permissions'])):
        raise PluginError('Χρειάζεται πρόσθετο καναλιού συμβατό με Akatikito Channel API v1.')
    for name in value['files']:
        if (not isinstance(name,str) or len(name)>160 or '\\' in name or ':' in name
                or PurePosixPath(name).is_absolute() or any(p in ('','..','.') or p.startswith('.') for p in name.split('/'))
                or PurePosixPath(name).suffix not in ('.py','.json','.md')):
            raise PluginError('Μη ασφαλής διαδρομή πακέτου.')
    if len(set(value['files']))!=len(value['files']) or value['entry'] not in value['files'] or not value['entry'].endswith('.py'):
        raise PluginError('Μη έγκυρο αρχείο εκκίνησης πρόσθετου.')
    return value

def fingerprint(parts):
    return hashlib.sha256(json.dumps(parts,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

class Plugins:
    def __init__(self,directory):
        self.directory=Path(directory);self.registry=self.directory/'installed.json'
        self.lock=FileLock(self.directory/'registry.lock')
    def installed(self):
        value=load_json(self.registry,dict)
        if not isinstance(value,dict):raise PluginError('Μη έγκυρος κατάλογος προσθέτων.')
        return value
    def catalog(self, *, settings=None, dev_dir: Path | None = None):
        installed=self.installed();available=[]
        for p in sorted(BUNDLES.glob('*/akatikito-plugin.json')):
            row=manifest(p.read_text(encoding='utf-8'));available.append({**row,'installed':row['id'] in installed,'source':'catalog:'+row['id']})
        dev_rows = []
        if settings is not None:
            from . import advanced
            if advanced.enabled(settings, 'plugin_sdk'):
                from .plugin_sdk import discover_dev_plugins
                folder = Path(dev_dir) if dev_dir is not None else (self.directory / 'dev')
                dev_rows = discover_dev_plugins(folder, settings)
        return {'available':available,'installed':list(installed.values()),'dev':dev_rows}

    def load_dev(self, ident: str, config: dict, *, dev_dir: Path | None = None, settings=None):
        """Load an unsigned/dev channel plugin ONLY from the explicit dev folder when Advanced `plugin_sdk` is enabled."""
        from . import advanced
        if not advanced.enabled(settings, 'plugin_sdk'):
            raise PermissionError('Η λειτουργία «Δικά μου plugins και skills (SDK)» είναι κλειστή στο Advanced.')
        from .plugin_sdk import discover_dev_plugins
        folder = Path(dev_dir) if dev_dir is not None else (self.directory / 'dev')
        found = {row['id']: row for row in discover_dev_plugins(folder, settings)}
        row = found.get(ident)
        if not row:
            raise PluginError(f'Δεν βρέθηκε dev πρόσθετο: {ident}')
        base = checked(Path(row['path']))
        info = manifest(checked(base / 'akatikito-plugin.json').read_text(encoding='utf-8'))
        code = checked(base / info['entry']).read_text(encoding='utf-8')
        spec = importlib.util.spec_from_file_location('akatikito_dev_channel_' + ident, base / info['entry'])
        module = importlib.util.module_from_spec(spec)
        exec(compile(code, str(base / info['entry']), 'exec'), module.__dict__)
        adapter = module.create(config)
        if not all(callable(getattr(adapter, name, None)) for name in ('connect', 'poll', 'send', 'close')):
            raise PluginError('Το πρόσθετο δεν υλοποιεί Channel API v1.')
        return adapter
    def _github(self,url):
        parsed=urlparse(str(url))
        parts=[unquote(x) for x in parsed.path.strip('/').split('/')]
        if (parsed.scheme!='https' or parsed.netloc!='github.com' or parsed.query or parsed.fragment
                or len(parts)<2 or not all(re.fullmatch(r'[A-Za-z0-9_.-]+',x) and x not in ('.','..') for x in parts)):
            raise PluginError('Δώσε διεύθυνση αποθετηρίου https://github.com/owner/repo ή φακέλου /tree/ref/path.')
        owner,repo=parts[:2];ref='HEAD';folder=''
        if len(parts)>2:
            if len(parts)<4 or parts[2]!='tree':raise PluginError('Χρησιμοποίησε σύνδεσμο φακέλου GitHub /tree/ref/path.')
            ref=parts[3];folder='/'.join(parts[4:])
        api='https://api.github.com/repos/'+owner+'/'+repo.removesuffix('.git')
        def fetch(path):
            try:
                with httpx.Client(timeout=20,follow_redirects=False,trust_env=False,headers={'Accept':'application/vnd.github+json','User-Agent':'Akatikito-plugin-review'}) as client:
                    with client.stream('GET',api+path) as response:
                        response.raise_for_status();raw=bytearray()
                        for chunk in response.iter_bytes():
                            raw.extend(chunk)
                            if len(raw)>1_500_000:raise PluginError('Το αρχείο πρόσθετου είναι πολύ μεγάλο.')
                return json.loads(raw)
            except (httpx.HTTPError,ValueError):raise PluginError('Δεν διαβάστηκε το δημόσιο πακέτο GitHub.') from None
        sha=fetch('/commits/'+ref).get('sha','')
        if not re.fullmatch('[0-9a-f]{40}',sha):raise PluginError('Δεν προσδιορίστηκε σταθερό commit.')
        def read(name):
            from urllib.parse import quote
            path='/'.join(x for x in (folder,name) if x)
            row=fetch('/contents/'+quote(path,safe='/')+'?ref='+sha)
            if row.get('type')!='file' or row.get('encoding')!='base64' or int(row.get('size',0))>200000:
                raise PluginError('Χρειάζεται κανονικό αρχείο έως 200 KB.')
            import base64
            try:return base64.b64decode(row['content'],validate=False).decode('utf-8')
            except (ValueError,UnicodeError):raise PluginError('Μη έγκυρο κείμενο πακέτου.') from None
        return read,'https://github.com/'+owner+'/'+repo+'/tree/'+sha+('/'+folder if folder else ''),sha
    def preview(self,source):
        if source.startswith('catalog:'):
            ident=source.split(':',1)[1]
            if not ID.fullmatch(ident):raise PluginError('Άγνωστο πρόσθετο καταλόγου.')
            base=checked(BUNDLES/ident)
            read=lambda name:checked(base/name).read_text(encoding='utf-8')
            origin=source;commit='bundled'
        else:read,origin,commit=self._github(source)
        raw=read('akatikito-plugin.json');info=manifest(raw)
        parts={'akatikito-plugin.json':raw}
        for name in info['files']:
            body=read(name)
            if len(body.encode())>200000:raise PluginError('Πολύ μεγάλο αρχείο πρόσθετου.')
            if name.endswith('.py'):ast.parse(body,filename=name)
            parts[name]=body
        if sum(len(x.encode()) for x in parts.values())>1_000_000:raise PluginError('Πολύ μεγάλο πακέτο πρόσθετου.')
        key=uuid.uuid4().hex;review=fingerprint(parts)
        draft={'id':key,'manifest':info,'source':origin,'commit':commit,'parts':parts,'review':review,'created':time.time()}
        with self.lock:save_json(self.directory/'drafts'/(key+'.json'),draft)
        return {k:draft[k] for k in ('id','manifest','source','commit','review','parts')}
    def install(self,key,review):
        if not re.fullmatch('[0-9a-f]{32}',str(key)):raise PluginError('Μη έγκυρη προεπισκόπηση.')
        with self.lock:
            draft=load_json(self.directory/'drafts'/(key+'.json'),dict)
            if (not draft or time.time()-draft.get('created',0)>3600 or draft.get('review')!=review
                    or fingerprint(draft['parts'])!=review):raise PluginError('Η προεπισκόπηση άλλαξε ή έληξε. Διάβασέ την ξανά.')
            info=manifest(draft['parts']['akatikito-plugin.json']);ident=info['id']
            target=checked(self.directory/'packages'/ident/review)
            if target.exists():
                for name,body in draft['parts'].items():
                    if checked(target/name).read_text(encoding='utf-8')!=body:raise PluginError('Η εγκατεστημένη έκδοση άλλαξε.')
            else:
                target.mkdir(parents=True)
                for name,body in draft['parts'].items():
                    p=checked(target/name);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(body,encoding='utf-8')
            row={**info,'source':draft['source'],'commit':draft['commit'],'review':review,'installed_at':time.time()}
            installed=self.installed();installed[ident]=row;save_json(self.registry,installed)
            return row
    def remove(self,ident):
        with self.lock:
            rows=self.installed();rows.pop(ident,None);save_json(self.registry,rows)
    def load(self,ident,config):
        rows=self.installed();row=rows.get(ident)
        if not row:raise PluginError('Το πρόσθετο δεν είναι εγκατεστημένο.')
        review=row.get('review','')
        if not ID.fullmatch(ident) or not re.fullmatch('[0-9a-f]{64}',review):raise PluginError('Μη έγκυρη εγκατάσταση.')
        base=checked(self.directory/'packages'/ident/review)
        parts={'akatikito-plugin.json':checked(base/'akatikito-plugin.json').read_text(encoding='utf-8')}
        info=manifest(parts['akatikito-plugin.json'])
        for name in info['files']:parts[name]=checked(base/name).read_text(encoding='utf-8')
        if fingerprint(parts)!=review:raise PluginError('Το πρόσθετο άλλαξε μετά την έγκριση· χρειάζεται νέα εγκατάσταση.')
        spec=importlib.util.spec_from_file_location('akatikito_channel_'+ident+'_'+review,base/info['entry'])
        module=importlib.util.module_from_spec(spec);exec(compile(parts[info['entry']],str(base/info['entry']),'exec'),module.__dict__)
        adapter=module.create(config)
        if not all(callable(getattr(adapter,name,None)) for name in ('connect','poll','send','close')):
            raise PluginError('Το πρόσθετο δεν υλοποιεί Channel API v1.')
        return adapter
