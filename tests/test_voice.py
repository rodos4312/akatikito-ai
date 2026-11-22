"""Voice tests use generated WAVs and deterministic engines, without microphones or providers."""
import base64
import hashlib
import io
import json
import struct
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from akatikito.voice import VoiceError, VoiceService
from akatikito.voice_worker import MAX_SECONDS, decode_audio, model_ready, transcribe
from akatikito import settings
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import ScriptedModel
from akatikito.workspace import Workspace


def ready(folder, model='base'):
    folder.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in ('config.json', 'model.bin', 'tokenizer.json', 'vocabulary.json'):
        raw = (name+' synthetic').encode()
        (folder/name).write_bytes(raw)
        files[name] = {'size':len(raw), 'sha256':hashlib.sha256(raw).hexdigest()}
    (folder/'ready.json').write_text(json.dumps({'version':1,'model':model,'files':files}))


def wav(seconds=.2, rate=16000, channels=1, silence=False):
    data = io.BytesIO()
    with wave.open(data, 'wb') as file:
        file.setnchannels(channels);file.setsampwidth(2);file.setframerate(rate)
        file.writeframes(struct.pack('<h', 0 if silence else 1000)*int(seconds*rate)*channels)
    return data.getvalue()


def wait(service):
    service.thread.join(timeout=5)
    assert not service.thread.is_alive()
    return service.status()['job']


def test_real_audio_decoder_normalizes_channels_and_rate_and_bounds():
    samples = decode_audio(wav(rate=44100, channels=2), 'audio/wav;codecs=pcm')
    assert abs(len(samples)-3200)<10 and samples.dtype.name=='float32'
    assert 0 < float(samples.max()) < 1
    with pytest.raises(ValueError, match='65'):decode_audio(wav(MAX_SECONDS+1), 'audio/wav')
    with pytest.raises(ValueError):decode_audio(b'not audio', 'audio/wav')
    with pytest.raises(ValueError):decode_audio(wav(), 'application/octet-stream')


def test_transcription_is_local_and_keeps_greek_output(tmp_path):
    folder=tmp_path/'base';ready(folder);seen=[]
    def engine(path, **options):
        seen.append((path,options))
        class Local:
            def transcribe(self,samples,**kwargs):
                assert kwargs['language']=='el' and kwargs['vad_filter']
                return iter([SimpleNamespace(text=' Γεια σου '),SimpleNamespace(text='κόσμε.')]),SimpleNamespace(language='el')
        return Local()
    result=transcribe(folder,'base',wav(),'audio/wav','el',engine)
    assert result['text']=='Γεια σου κόσμε.' and result['language']=='el'
    assert seen[0][1]['local_files_only'] is True and seen[0][1]['device']=='cpu'
    assert seen[0][1]['compute_type']=='int8'
    def forbidden(*a,**k):pytest.fail('Silence must not load or call a model')
    assert transcribe(folder,'base',wav(silence=True),'audio/wav','el',forbidden)['text']==''
    (folder/'model.bin').write_bytes(b'corrupt')
    assert not model_ready(folder,'base',verify=True)
    with pytest.raises(ValueError,match='λήψη'):transcribe(folder,'base',wav(),'audio/wav','el',forbidden)


def test_job_cleans_audio_and_does_not_call_chat(tmp_path):
    seen=[]
    def runner(request,cancel):
        audio=Path(request['audio']);assert audio.is_file();seen.append(audio)
        assert request['language']=='el' and request['model']=='base'
        return {'ok':True,'text':'Τοπική υπαγόρευση'}
    service=VoiceService(tmp_path,runner);ready(service.models/'base')
    job=service.transcribe(base64.b64encode(wav()).decode(),'audio/wav','base','el',['project','session'])
    result=wait(service)
    assert result['id']==job['id'] and result['status']=='complete' and result['text']=='Τοπική υπαγόρευση'
    assert result['context']==['project','session'] and seen and not seen[0].exists()
    assert not list((tmp_path/'temporary').iterdir())
    service.close()
    with pytest.raises(VoiceError):service.prepare('base')


def test_cancel_and_second_job_do_not_overlap(tmp_path):
    entered=threading.Event();release=threading.Event();seen=[]
    def runner(request,cancel):
        seen.append(Path(request['audio']));entered.set();assert release.wait(5)
        return {'ok':True,'text':'must not appear'}
    service=VoiceService(tmp_path,runner);ready(service.models/'base')
    job=service.transcribe(base64.b64encode(wav()).decode(),'audio/wav','base','el',[])
    assert entered.wait(3)
    assert not service.cancel('wrong-id') and service.cancel(job['id'])
    with pytest.raises(VoiceError,match='ήδη'):service.prepare('base')
    release.set();result=wait(service)
    assert result['status']=='cancelled' and result['text']=='' and not seen[0].exists()


def test_validation_and_worker_failure_are_visible_without_retaining_audio(tmp_path):
    service=VoiceService(tmp_path,lambda *args: {'error':'synthetic decoder failure'})
    with pytest.raises(VoiceError,match='πρώτα'):service.transcribe(base64.b64encode(wav()).decode(),'audio/wav','base','el',[])
    ready(service.models/'base')
    for encoded,mime,model,language in [('!?','audio/wav','base','el'),('eA==','bad','base','el'),('eA==','audio/wav','../other','el'),('eA==','audio/wav','base','bad')]:
        with pytest.raises(VoiceError):service.transcribe(encoded,mime,model,language,[])
    service.transcribe(base64.b64encode(wav()).decode(),'audio/wav','base','el',[])
    result=wait(service);assert result['status']=='error' and 'synthetic' in result['error']
    assert not list((tmp_path/'temporary').iterdir())


def test_real_child_process_cancels_and_receives_no_credentials(tmp_path,monkeypatch):
    from akatikito import voice
    started=tmp_path/'started';script=tmp_path/'slow.py'
    script.write_text('import sys,time\nfrom pathlib import Path\nsys.stdin.read()\nPath('+repr(str(started))+').touch()\ntime.sleep(60)\n')
    actual=subprocess.Popen;seen=[]
    def process(args,**options):
        seen.append((args,options));return actual([sys.executable,'-B',str(script)],**options)
    monkeypatch.setattr(voice.subprocess,'Popen',process)
    monkeypatch.setenv('AKATIKITO_API_KEY','synthetic-do-not-pass')
    monkeypatch.setenv('HF_TOKEN','synthetic-do-not-pass')
    monkeypatch.setenv('HUGGINGFACE_HUB_TOKEN','synthetic-do-not-pass')
    service=VoiceService(tmp_path/'voice');ready(service.models/'base')
    job=service.transcribe(base64.b64encode(wav()).decode(),'audio/wav','base','el',[])
    deadline=time.monotonic()+5
    while not started.exists() and time.monotonic()<deadline:time.sleep(.03)
    assert started.exists()
    child=service.process;service.cancel(job['id']);result=wait(service)
    assert result['status']=='cancelled' and child.poll() is not None
    env=seen[0][1]['env'];assert 'AKATIKITO_API_KEY' not in env and 'HF_TOKEN' not in env and 'HUGGINGFACE_HUB_TOKEN' not in env
    assert env['HF_HUB_OFFLINE']=='1' and Path(env['TEMP']).is_relative_to(tmp_path)
    assert not list((service.storage/'temporary').iterdir())


def test_preparation_and_persistent_voice_preferences(tmp_path):
    def runner(request,cancel):ready(Path(request['folder']));return {'ok':True}
    service=VoiceService(tmp_path/'voice',runner)
    assert not service.status()['models']['base']
    service.prepare('base');assert wait(service)['status']=='complete' and service.status()['models']['base']
    value=settings.Settings(model='last-chat',project='last-project',voice_model='small',voice_language='auto')
    value.set_key('provider','synthetic-key');value.save(tmp_path/'settings.json')
    loaded=settings.Settings.load(tmp_path/'settings.json')
    assert (loaded.model,loaded.project,loaded.voice_model,loaded.voice_language)==('last-chat','last-project','small','auto')
    assert loaded.key_for('provider')=='synthetic-key'


def test_authenticated_routes_and_navigation_context(tmp_path,monkeypatch):
    import http.client
    from akatikito.app_api import AppState,start_server
    monkeypatch.setattr(settings,'DEFAULT_PATH',tmp_path/'settings.json')
    state=AppState();state.agent=Akatikito(str(tmp_path),Config(verify_command=''),ScriptedModel([]))
    entered=threading.Event();release=threading.Event()
    def runner(request,cancel):entered.set();release.wait(3);return {'ok':True,'text':'κείμενο'}
    state.voice=VoiceService(tmp_path/'voice',runner);ready(state.voice.models/'base')
    url,server=start_server(state);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def post(path,data,token=True):
        connection=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=4)
        headers={'Content-Type':'application/json'}
        if token:headers['X-Akatikito-Token']=server.auth_token
        connection.request('POST',path,json.dumps(data),headers);response=connection.getresponse();result=json.loads(response.read());connection.close();return response.status,result
    try:
        assert post('/api/voice/config',{'model':'small'},False)[0]==403
        assert post('/api/voice/config',{'model':'base','language':'el'})[1]['model']=='base'
        payload={'project':'wrong','session_id':'','base64':base64.b64encode(wav()).decode(),'mime':'audio/wav'}
        assert 'Άλλαξε' in post('/api/voice/transcribe',payload)[1]['error'] and not entered.is_set()
        payload['project']=str(tmp_path);job=post('/api/voice/transcribe',payload)[1]
        assert entered.wait(2) and not state.busy
        assert state.send('Πρόχειρο')['error'] and state.agent.model.calls==0
        assert post('/api/voice/cancel',{'id':job['id']})[1]['ok'];release.set();wait(state.voice)
        assert state.transcript==[] and state.agent.messages==[]
    finally:
        release.set();state.close();server.shutdown();server.server_close();thread.join(2)


def test_worker_can_load_without_the_agent_or_provider_sdk():
    worker=Path(__file__).resolve().parents[1]/'akatikito'/'voice_worker.py'
    code="import sys,runpy;runpy.run_path(sys.argv[1],run_name='isolated_voice_test');assert 'akatikito.model' not in sys.modules;assert 'openai' not in sys.modules;print('voice worker independent')"
    result=subprocess.run([sys.executable,'-B','-c',code,str(worker)],capture_output=True,text=True,encoding='utf-8',timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
