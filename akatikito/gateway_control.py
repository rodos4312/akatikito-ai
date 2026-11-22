"""Desktop owner interface to a separately running, authenticated local gateway."""
from __future__ import annotations
import json,os,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.parse import urlparse
import httpx
from .channel_plugins import Plugins,PluginError
from .channel_config import ChannelConfig
from .local_store import load_json
from .settings import Settings

class GatewayControl:
    def __init__(self,directory):
        self.directory=Path(directory);self.plugins=Plugins(self.directory/'plugins');self.config=ChannelConfig(self.directory/'channels.json')
        self.cached=None;self.cached_at=0
    def call(self,path,data=None):
        desc=load_json(self.directory/'gateway'/'control.json',dict)
        if not desc:raise ValueError('Το gateway δεν εκτελείται.')
        url=urlparse(desc.get('url',''))
        if url.scheme!='http' or url.hostname!='127.0.0.1' or not url.port or url.path or url.query or url.username:raise ValueError('Μη έγκυρο τοπικό gateway.')
        with httpx.Client(timeout=1.5,trust_env=False,follow_redirects=False) as client:
            response=client.request('POST' if data is not None else 'GET',desc['url']+path,
                headers={'X-Akatikito-Gateway':desc['token']},json=data if data is not None else None)
            response.raise_for_status();return response.json()
    def status(self,fresh=False):
        if not fresh and self.cached is not None and time.monotonic()-self.cached_at<.75:return self.cached
        try:result=self.call('/status')
        except (OSError,ValueError,httpx.HTTPError,KeyError):result={'running':False,'channels':{},'approvals':[]}
        self.cached=result;self.cached_at=time.monotonic();return result
    def view(self):
        return {**self.plugins.catalog(),'config':self.config.load(),'gateway':self.status()}
    def action(self,data):
        action=data.get('action');running=self.status(fresh=True).get('running')
        if action=='start':
            if running:return {'ok':True,'gateway':self.status()}
            settings=Settings.load()
            if not settings.model:raise ValueError('Διάλεξε πρώτα πάροχο και μοντέλο.')
            configs=self.config.load()
            installed=self.plugins.installed()
            if any(r.get('enabled') and ident not in installed for ident,r in configs.items()):
                raise ValueError('Υπάρχει ενεργό κανάλι χωρίς εγκατεστημένο πρόσθετο. Εγκατάστησέ το ξανά ή απενεργοποίησέ το.')
            from .api_security import endpoint_identity
            from .model import PROVIDERS
            destination=endpoint_identity(settings.base_url or PROVIDERS.get(settings.provider,''))
            origin=settings.key_origins.get(settings.provider)
            if settings.key_for(settings.provider) and origin and endpoint_identity(origin)!=destination:
                raise ValueError('Το κλειδί μοντέλου ανήκει σε διαφορετική διεύθυνση. Σύνδεσε πρώτα τον πάροχο ξανά.')
            if not any(r.get('enabled') for r in configs.values()):raise ValueError('Ενεργοποίησε πρώτα ένα εγκατεστημένο κανάλι.')
            app_root=Path(__file__).resolve().parents[1]
            env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1'
            from .process_lock import writable_dir
            env_temp=os.environ.get('AKATIKITO_TEMP_DIR','').strip()
            candidates=[]
            if env_temp:candidates.append(Path(env_temp)/'akatikito-gateway-runtime')
            if Path('D:/').exists():candidates.append(Path('D:/Meu-Temp/akatikito-gateway-runtime'))
            candidates.append(Path(tempfile.gettempdir())/'akatikito-gateway-runtime')
            temp=writable_dir(*candidates)
            for name in ('TEMP','TMP','TMPDIR'):env[name]=str(temp)
            flags=(subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS) if os.name=='nt' else 0
            subprocess.Popen([sys.executable,'-B','-m','akatikito.gateway'],cwd=app_root,env=env,
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=flags,start_new_session=os.name!='nt',close_fds=True)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                status=self.status(fresh=True)
                if status.get('running'):return {'ok':True,'gateway':status}
                time.sleep(.1)
            raise ValueError('Το gateway δεν ξεκίνησε. Έλεγξε την εγκατάσταση και τις ρυθμίσεις.')
        if action=='stop':
            if running:self.call('/stop',{})
            self.cached=None;return {'ok':True}
        if action=='approval':
            self.cached=None;return self.call('/approval',{'id':data.get('id'),'allow':data.get('allow') is True})
        if running:raise ValueError('Σταμάτησε πρώτα το gateway για αλλαγή προσθέτου ή σύνδεσης.')
        if action=='preview':return self.plugins.preview(str(data.get('source','')))
        if action=='install':return {'ok':True,'plugin':self.plugins.install(data.get('id'),data.get('review'))}
        if action=='remove':
            ident=str(data.get('id',''));self.plugins.remove(ident)
            from .local_store import save_json
            rows=load_json(self.config.path,dict)
            if ident in rows:rows[ident]['enabled']=False;save_json(self.config.path,rows)
            return {'ok':True}
        if action=='configure':return {'ok':True,'config':self.config.save(data.get('id'),data.get('config'),self.plugins.installed())}
        raise ValueError('Άγνωστη ενέργεια πρόσθετου.')
