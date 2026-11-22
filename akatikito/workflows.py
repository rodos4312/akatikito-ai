"""Evidence for non-coding procedures, without retaining source bodies or secrets."""
from __future__ import annotations
from collections import defaultdict,deque
import hashlib
import json
from pathlib import Path

READS={'list_files','read_file','search_in_file','search_project','read_document',
       'web_search','web_fetch','browser_fetch','web_news','youtube_transcript',
       'github_search','github_read','github_issues','list_reminders','list_memory','list_skills','search_past_sessions','git_status_diff'}
ACTIONS={'add_reminder','done_reminder','create_word_document','create_excel_sheet','create_pdf_document'}


def observed(events,traits=None,root=None):
    traits=traits or {};pending=defaultdict(deque);rows=[];seen=set();failed=False
    for event in events:
        data=event.data;name=data.get('name','')
        if event.kind=='tool_call':
            try:args=json.loads(data.get('arguments') or '{}') if isinstance(data.get('arguments'),str) else data.get('arguments',{})
            except (ValueError,TypeError):args={}
            pending[name].append(args if isinstance(args,dict) else {})
            continue
        if event.kind!='tool_result':continue
        args=pending[name].popleft() if pending[name] else {'path':data.get('path','')}
        result=str(data.get('result') or '')
        trait=traits.get(name,{})
        role=('inspect' if name in READS or trait.get('readonly') is True or (name == 'convert_data_file' and not str(args.get('output_path') or '').strip()) else
              'action' if name in ACTIONS or trait.get('readonly') is False or (name == 'convert_data_file' and bool(str(args.get('output_path') or '').strip())) else
              'plan' if name=='update_todos' else 'verify' if name=='run_command' else '')
        if not role:continue
        if result.startswith(('ΣΦΑΛΜΑ','ΑΠΟΡΡΙΨΗ','ΑΠΟΤΡΟΠΗ')) or role=='verify' and not result.startswith('returncode=0'):
            failed=True;continue
        if role in ('action','plan') and not (result.startswith('ΕΓΙΝΕ') or name in traits):
            failed=True;continue
        # Paging the same source repeatedly is not a procedure with three steps.
        identity={k:v for k,v in args.items() if k not in ('start','end','start_line','max_lines','start_char','max_chars','length','limit','max_entries','max_files')}
        if root is not None and identity.get('path') and name in READS and not name.startswith('github_'):
            identity['path']=str((Path(root)/str(identity['path'])).resolve()).casefold()
        fingerprint=hashlib.sha256(json.dumps([name,identity],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if fingerprint in seen and not (name=='list_reminders' and any(r['role']=='action' for r in rows)):
            continue
        seen.add(fingerprint)
        source=([args.get('owner'),args.get('repo'),args.get('path','')] if name.startswith('github_') else identity.get('path') or args.get('url'))
        source=source or (name+':'+fingerprint)
        rows.append({'name':name,'role':role,'source':hashlib.sha256(str(source).encode()).hexdigest(),
                     'server':trait.get('server'),'result':result[:5000] if name in ACTIONS or name=='list_reminders' else ''})
    if failed or len(rows)<3:return None
    reads=[r for r in rows if r['role']=='inspect'];actions=[r for r in rows if r['role']=='action']
    if actions:
        if any(a['name'] in ('create_word_document','create_excel_sheet','create_pdf_document','convert_data_file') for a in actions) and reads:
            category='documents';phases=['inspect','generate','verify'];evidence='document-created'
        else:
            last=max(i for i,r in enumerate(rows) if r['role']=='action');checked=False
            for read in rows[last+1:]:
                if read['role']!='inspect':continue
                if all(a['name'] in ACTIONS for a in actions) and read['name']=='list_reminders':
                    try:
                        returned={str(r['id']):r for r in json.loads(read['result'])}
                        wanted={}
                        for action in actions:
                            item=json.loads(action['result'].split(': ',1)[1]);wanted[str(item['id'])]=item
                        checked=all(returned.get(key)==value for key,value in wanted.items())
                    except (ValueError,TypeError,KeyError):pass
                elif read['server'] and all(a['server']==read['server'] for a in actions):checked=True
            if not checked or not reads:return None
            category='organization';phases=['inspect','act','readback'];evidence='readback'
    elif any(r['role']=='plan' for r in rows) and len(reads)>=2:
        category='planning';phases=['inspect','structure','report'];evidence='checklist-and-report'
    elif rows[-1]['role']=='verify' and reads:
        category='analysis';phases=['inspect','evaluate','verify'];evidence='successful-command'
    elif len({r['source'] for r in reads})>=2 and len(reads)>=3:
        category='research';phases=['inspect','compare','report'];evidence='sources-and-report'
    else:return None
    names=list(dict.fromkeys(r['name'] for r in rows))[:30]
    signature=json.dumps([category,phases,names],ensure_ascii=False)
    return {'id':hashlib.sha256(signature.encode()).hexdigest()[:16],'category':category,
            'tools':names,'phases':phases,'evidence':evidence,'source_count':len({r['source'] for r in reads}),
            'extensions':[],'examples':[category+': '+' → '.join(names[:8])]}
