"""Real local documents and state, no provider calls."""
import io,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akatikito.features import read_document,RepoMap,Skills,add_reminder,list_reminders,done_reminder
from akatikito.workspace import Workspace
from akatikito.task_state import TaskState
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel,tool_call
from akatikito import modes,intent


def test_docs_real_formats_and_exact_archive(tmp_path):
    ws=Workspace(tmp_path);archive={};ws.context_store=lambda text: archive.setdefault('full',text) and 'full'
    (tmp_path/'a.csv').write_text('όνομα,τιμή\nΆννα,42',encoding='utf-8')
    from docx import Document
    doc=Document();doc.add_paragraph('Σημαντική παράγραφος');doc.add_table(1,1).cell(0,0).text='Κελί';doc.save(tmp_path/'a.docx')
    from openpyxl import Workbook
    book=Workbook();book.active['A1']='Στοιχείο';book.active['B1']='=1+2';book.save(tmp_path/'a.xlsx');book.close()
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject,NameObject,DictionaryObject
    writer=PdfWriter();page=writer.add_blank_page(300,300)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 20 200 Td (Real PDF content) Tj ET');page[NameObject('/Contents')]=writer._add_object(stream)
    with (tmp_path/'a.pdf').open('wb') as f:writer.write(f)
    for file,expected in [('a.csv','Άννα'),('a.docx','Κελί'),('a.xlsx','=1+2'),('a.pdf','Real PDF content')]:
        before=(tmp_path/file).read_bytes();result=json.loads(read_document(ws,file))
        assert expected in result['text'] and (tmp_path/file).read_bytes()==before
    page=json.loads(read_document(ws,'a.csv',0,3));assert len(page['text'])==3 and page['next']==3
    assert 'full' in archive
    with pytest.raises(PermissionError):read_document(ws,'../outside.csv')


def test_document_limits_and_zip_bomb(tmp_path):
    import zipfile
    ws=Workspace(tmp_path)
    with zipfile.ZipFile(tmp_path/'bomb.docx','w',compression=zipfile.ZIP_DEFLATED) as z:z.writestr('bomb','0'*(65*1024*1024))
    with pytest.raises(ValueError,match='64 MiB'):read_document(ws,'bomb.docx')
    with pytest.raises(ValueError):read_document(ws,'missing.csv')


def test_todos_validation_roundtrip_and_mode(tmp_path):
    task=TaskState();rows=[{'id':'x','text':'Δοκιμή','status':'in_progress'}];task.set_todos(rows)
    restored=TaskState();restored.restore(task.snapshot());assert restored.todos==rows
    with pytest.raises(ValueError):task.set_todos(rows+rows)
    assert task.todos==rows
    a=Akatikito(str(tmp_path),Config(verify_command=' '),ScriptedModel([{'content':'Έγινε','tool_calls':[tool_call('update_todos',todos=rows)]},{'content':'Τέλος'}]))
    a.chat('Διάβασε και εξήγησε');assert a.task.todos==rows
    assert 'run_command' not in modes.allowed_tools('assistant')
    assert intent.classify('Θύμισέ μου αύριο στις 10 το ραντεβού')=='assistant'
    a.mode='assistant';a._turn_tools=set(modes.allowed_tools('assistant'))
    result,_=a._execute_call({'name':'write_file','arguments':{'path':'bad.py','content':'x=1'}},a._turn_tools)
    assert result.startswith('ΑΠΟΡΡΙΨΗ') and not (tmp_path/'bad.py').exists()


def test_rules_map_and_skill_metadata(tmp_path):
    ws=Workspace(tmp_path);(tmp_path/'AGENTS.md').write_text('Γενικός κανόνας',encoding='utf-8')
    rules=tmp_path/'.akatikito/rules';rules.mkdir(parents=True)
    (rules/'all.md').write_text('Πάντα',encoding='utf-8');(rules/'rules-code.md').write_text('Κώδικας',encoding='utf-8');(rules/'rules-debug.md').write_text('Διόρθωση',encoding='utf-8')
    assert 'Κώδικας' in ws.rules('code') and 'Διόρθωση' not in ws.rules('code') and 'Πάντα' in ws.rules('ask')
    (tmp_path/'source.py').write_text('class One:\n def work(self): pass\n')
    ignored=tmp_path/'.venv';ignored.mkdir();(ignored/'secret.py').write_text('def secret(): pass')
    mapper=RepoMap(tmp_path);assert 'class One(work)' in mapper.render() and 'secret' not in mapper.render()
    (tmp_path/'source.py').write_text('def changed(): pass');assert 'changed' in mapper.render()
    folder=tmp_path/'skills';folder.mkdir();(folder/'check.md').write_text('---\nname: Έλεγχος\ndescription: Ελέγχει το project\n---\nSECRET FULL GUIDANCE',encoding='utf-8')
    skills=Skills(tmp_path);assert 'SECRET' not in json.dumps(skills.catalog()) and 'SECRET' in skills.read('check')
    with pytest.raises(ValueError):skills.read('../other')


def test_reminders_are_project_local_and_done(tmp_path):
    (tmp_path/'one').mkdir();(tmp_path/'two').mkdir()
    ws=Workspace(tmp_path/'one');other=Workspace(tmp_path/'two')
    result=add_reminder(ws,'Ραντεβού','2030-01-02','10:00');key=json.loads(result.split(': ',1)[1])['id']
    assert len(json.loads(list_reminders(ws)))==1 and json.loads(list_reminders(other))==[]
    done_reminder(ws,key);assert json.loads(list_reminders(ws))[0]['done']
    with pytest.raises(ValueError):add_reminder(ws,'','bad')


def test_word_order_and_utf16_csv(tmp_path):
    from docx import Document
    ws=Workspace(tmp_path);doc=Document();doc.add_paragraph('before');doc.add_table(1,1).cell(0,0).text='middle';doc.add_paragraph('after');doc.save(tmp_path/'ordered.docx')
    text=json.loads(read_document(ws,'ordered.docx'))['text'];assert text.index('before')<text.index('middle')<text.index('after')
    (tmp_path/'greek.csv').write_bytes('όνομα,τιμή\nΆννα,42'.encode('utf-16'))
    assert 'Άννα' in json.loads(read_document(ws,'greek.csv'))['text']


def test_whitespace_verify_is_normalized_without_false_failure(tmp_path):
    a=Akatikito(str(tmp_path),Config(verify_command='   '),ScriptedModel([{'tool_calls':[tool_call('write_file',path='a.txt',content='data')]},{'content':'Έγινε'}]))
    assert a.config.verify_command=='' and a.chat('Γράψε το αρχείο')=='Έγινε' and not a.repair.pending


def test_notification_tick_fires_once_with_local_clock(tmp_path,monkeypatch):
    from akatikito import settings
    from akatikito.app_api import AppState
    monkeypatch.setattr(settings,'DEFAULT_PATH',tmp_path/'settings.json')
    state=AppState();state.agent=Akatikito(str(tmp_path),Config(verify_command=' '),ScriptedModel([]))
    add_reminder(state.agent.ws,'Συνθετική ειδοποίηση','2000-01-01','10:00')
    fired=[];monkeypatch.setattr('akatikito.notify.notify',lambda *args:fired.append(args))
    class InlineThread:
        def __init__(self,target,args,daemon):self.target,self.args=target,args
        def start(self):self.target(*self.args)
    monkeypatch.setattr('akatikito.app_api.threading.Thread',InlineThread)
    state.reminder_tick();state._reminder_tick_at=0;state.reminder_tick()
    assert len(fired)==1 and 'Συνθετική' in fired[0][0]


def test_general_questions_and_negations_keep_read_only_permissions(tmp_path):
    from akatikito.tools import validate_arguments
    for text in ('Γιατί δεν εμφανίζεται το PDF;','Τι περιέχει το CSV;','Μην μου θυμίσεις το ραντεβού','Δεν θέλω υπενθύμιση'):
        assert intent.classify(text)=='ask',text
    for text in ('Θύμισέ μου να τρέξω τα tests αύριο','Γράψε επιστολή','Δημιούργησε CSV με ονόματα'):
        assert intent.classify(text)=='assistant',text
    assert intent.classify('Γράψε κώδικα για να διαβάζει PDF')=='code'
    task=TaskState();task.begin('Θύμισέ μου το ραντεβού',[]);assert task.kind=='assistant' and task.status=='active'
    assert validate_arguments('set_mode',{'mode':'assistant'}) is None
    a=Akatikito(str(tmp_path),Config(verify_command=' '),ScriptedModel([]));a.mode='code';a._turn_tools=a.allowed_tools()
    result,_=a._execute_call({'name':'set_mode','arguments':{'mode':'assistant'}},a._turn_tools)
    assert a.mode=='assistant' and 'Βοηθός' in result
    a.mode='ask';a._turn_tools=a.allowed_tools()
    denied,_=a._execute_call({'name':'add_reminder','arguments':{'title':'forbidden','date':'2030-01-01'}},a._turn_tools)
    assert denied.startswith('ΑΠΟΤΡΟΠΗ') or denied.startswith('ΑΠΟΡΡΙΨΗ')
    assert json.loads(list_reminders(a.ws))==[]
