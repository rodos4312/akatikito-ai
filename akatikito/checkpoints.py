"""Local Git object snapshots, separate from the owner's repository and index."""
from __future__ import annotations
import difflib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from pathlib import Path
from .workspace import Workspace

SKIP = {'.git','.agent','.agent_backups','.venv','venv','node_modules','__pycache__','.pytest_cache','dist','build'}
MAX_FILE = 32*1024*1024
MAX_TOTAL = 256*1024*1024


class Checkpoints:
    def __init__(self,root):
        self.ws=Workspace(root);self.folder=self.ws.resolve('.agent/checkpoints');self.repo=self.folder/'objects.git'
        self.git=shutil.which('git')

    def _git(self,*args,data=None):
        if not self.git:raise ValueError('Χρειάζεται Git για checkpoints. Μπορείς να τα απενεργοποιήσεις από τις Ρυθμίσεις.')
        env=os.environ.copy()
        for key in list(env):
            if key.startswith('GIT_'):env.pop(key)
        env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_AUTHOR_NAME='Akatikito',GIT_AUTHOR_EMAIL='local@akatikito.invalid',
                   GIT_COMMITTER_NAME='Akatikito',GIT_COMMITTER_EMAIL='local@akatikito.invalid')
        result=subprocess.run([self.git,'--git-dir='+str(self.repo),*args],input=data,capture_output=True,
                              env=env,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise ValueError('Απέτυχε το τοπικό checkpoint: '+result.stderr.decode('utf-8',errors='replace')[:400])
        return result.stdout

    def _scan(self):
        self.folder.mkdir(parents=True,exist_ok=True)
        if not self.repo.exists():self._git('init','--bare',str(self.repo))
        files={};total=0;pending=[]
        for directory,dirs,names in os.walk(self.ws.root,followlinks=False):
            dirs[:]=sorted(d for d in dirs if d not in SKIP and not getattr(Path(directory,d).lstat(),'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT and not Path(directory,d).is_symlink())
            for name in sorted(names):
                p=Path(directory,name)
                if p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT or not p.is_file():continue
                st_before = p.stat()
                size=st_before.st_size
                if size>MAX_FILE or len(pending)>=10000:
                    raise ValueError('Το project ξεπερνά το όριο checkpoint (32 MiB ανά αρχείο / 256 MiB συνολικά).')
                with p.open("rb") as stream:data=stream.read(MAX_FILE+1)
                total+=len(data)
                if total>MAX_TOTAL:raise ValueError("Το project ξεπερνά το συνολικό όριο checkpoint.")
                if len(data)>MAX_FILE:raise ValueError('Το αρχείο μεγάλωσε κατά τη δημιουργία checkpoint.')
                key=p.relative_to(self.ws.root).as_posix()
                hdr=f'blob {len(data)}\0'.encode('ascii')
                pending.append((
                    key,p,hashlib.sha256(data).hexdigest(),len(data),data,
                    hashlib.sha1(hdr+data).hexdigest(),hashlib.sha256(hdr+data).hexdigest(),
                ))
        if not pending:return files
        out=[]
        paths_safe=all('\n' not in str(p) and '\r' not in str(p) and not str(p).startswith('"') for _,p,*_ in pending)
        if paths_safe:
            stdin_paths=('\n'.join(str(p) for _,p,*_ in pending)+'\n').encode('utf-8')
            try:
                out=self._git('hash-object','-w','--stdin-paths',data=stdin_paths).decode('utf-8',errors='replace').splitlines()
            except ValueError:
                out=[]
        if len(out)!=len(pending):
            out=[self._git('hash-object','-w','--stdin',data=raw).decode().strip() for _,_,_,_,raw,_,_ in pending]
        for (key,p,sha,sz,raw,exp1,exp256),blob in zip(pending,out):
            blob_id=blob.strip()
            expected=exp1 if len(blob_id)==40 else exp256
            if blob_id!=expected:
                blob_id=self._git('hash-object','-w','--stdin',data=raw).decode().strip()
            files[key]={'blob':blob_id,'sha256':sha,'size':sz}
        return files

    def _save(self,record):
        target=self.folder/(record['id']+'.json');temp=target.with_suffix('.tmp')
        temp.write_text(json.dumps(record,ensure_ascii=False,sort_keys=True),encoding='utf-8');os.replace(temp,target)

    def begin(self):
        record={'id':uuid.uuid4().hex,'root':str(self.ws.root),'before':self._scan(),'after':None}
        self._save(record);return record['id']

    def finish(self,key):
        record=self.load(key);record['after']=self._scan();self._save(record)

    def load(self,key):
        if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{32}',key):raise ValueError('Μη έγκυρο checkpoint.')
        value=json.loads((self.folder/(key+'.json')).read_text(encoding='utf-8'))
        if value.get('root')!=str(self.ws.root) or value.get('id')!=key:raise ValueError('Το checkpoint ανήκει σε άλλο project.')
        for stage in ('before','after'):
            for name,row in (value.get(stage) or {}).items():
                self.ws.resolve(name)
                if Path(name).is_absolute() or any(part in ('..','.') for part in Path(name).parts):raise ValueError('Μη έγκυρη σχετική διαδρομή checkpoint.')
                if any(part in SKIP for part in Path(name).parts) or not re.fullmatch('[a-f0-9]{40,64}',row.get('blob','')):
                    raise ValueError('Μη έγκυρο περιεχόμενο checkpoint.')
        return value

    def _blob(self,row):
        data=self._git('cat-file','blob',row['blob'])
        if hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Αλλοιωμένο checkpoint.')
        return data

    def diff(self,key):
        record=self.load(key);before=record['before'];after=record['after']
        if after is None:raise ValueError('Η εργασία του checkpoint δεν έχει ολοκληρωθεί.')
        rows=[]
        for name in sorted(set(before)|set(after)):
            a,b=before.get(name),after.get(name)
            if a==b:continue
            old=self._blob(a) if a else b'';new=self._blob(b) if b else b''
            try:
                text=''.join(difflib.unified_diff(old.decode('utf-8-sig').splitlines(True),new.decode('utf-8-sig').splitlines(True),
                                                fromfile='πριν/'+name,tofile='μετά/'+name))
            except UnicodeError:text='Δυαδικό αρχείο: '+name
            rows.append({'path':name,'diff':text[:80000],'truncated':len(text)>80000})
        return rows

    def undo(self,key):
        record=self.load(key);before=record['before'];after=record['after']
        if after is None:raise ValueError('Το checkpoint δεν έχει ολοκληρωθεί.')
        changed=[n for n in set(before)|set(after) if before.get(n)!=after.get(n)]
        # Validate every target and blob before changing even one file.
        plan=[]
        for name in changed:
            lexical=self.ws.root/name
            for component in (lexical,*lexical.parents):
                if component==self.ws.root:break
                if component.exists() and (component.is_symlink() or getattr(component.lstat(),'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT):raise ValueError('Σύγκρουση: η διαδρομή έγινε σύνδεσμος. Δεν έγινε επαναφορά.')
            target=self.ws.resolve(name);expected=after.get(name)
            if target.exists() and (not target.is_file() or target.stat().st_size>MAX_FILE):raise ValueError('Σύγκρουση: η διαδρομή δεν είναι το αναμενόμενο αρχείο.')
            current=target.read_bytes() if target.exists() else None
            digest=hashlib.sha256(current).hexdigest() if current is not None else None
            if digest!=(expected or {}).get('sha256'):raise ValueError('Σύγκρουση: το '+name+' άλλαξε μετά το checkpoint. Δεν έγινε επαναφορά.')
            original=self._blob(before[name]) if name in before else None
            plan.append((target,current,original))
        done=[]
        try:
            for target,current,original in plan:
                if original is None:target.unlink()
                else:
                    target.parent.mkdir(parents=True,exist_ok=True)
                    temp=target.with_name('.'+target.name+'.restore-'+uuid.uuid4().hex)
                    try:temp.write_bytes(original);os.replace(temp,target)
                    finally:temp.unlink(missing_ok=True)
                done.append((target,current))
        except Exception:
            for target,data in reversed(done):
                if data is None:target.unlink(missing_ok=True)
                else:target.write_bytes(data)
            raise
        return sorted(changed)
