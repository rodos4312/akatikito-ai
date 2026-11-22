"""Cancellable local speech jobs; audio exists only in a private temporary folder."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .voice_worker import LANGUAGES, MODELS, FORMATS, MAX_AUDIO_BYTES, model_ready


class VoiceError(ValueError):
    pass


class VoiceService:
    def __init__(self, storage, runner=None):
        self.storage = Path(storage).resolve()
        self.models = self.storage / 'models'
        self.lock = threading.RLock()
        self.job = None
        self.thread = None
        self.process = None
        self.closed = False
        self.runner = runner or self._run_worker

    def status(self):
        with self.lock:
            public = dict(self.job['view']) if self.job else None
        return {'local': True, 'models': {name: model_ready(self.models / name, name) for name in MODELS}, 'job': public}

    def busy(self):
        with self.lock:
            return bool(self.thread and self.thread.is_alive())

    def prepare(self, model):
        return self._start('prepare', model, 'el', (), None, None)

    def transcribe(self, encoded, mime, model, language, context):
        if not isinstance(mime, str) or mime.split(';', 1)[0].strip().lower() not in FORMATS:
            raise VoiceError('Υποστηρίζονται WAV, WebM, OGG και MP4.')
        if not isinstance(encoded, str) or len(encoded) > MAX_AUDIO_BYTES * 4 // 3 + 8:
            raise VoiceError('Όριο ήχου 8 MiB.')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise VoiceError('Μη έγκυρος ήχος.') from exc
        if not raw or len(raw) > MAX_AUDIO_BYTES:
            raise VoiceError('Κενός ή πολύ μεγάλος ήχος.')
        if model not in MODELS or language not in LANGUAGES:
            raise VoiceError('Μη έγκυρη ρύθμιση φωνής.')
        if not model_ready(self.models / model, model):
            raise VoiceError('Κατέβασε πρώτα το τοπικό μοντέλο από τις Ρυθμίσεις φωνής.')
        return self._start('transcribe', model, language, context, raw, mime)

    def _start(self, action, model, language, context, raw, mime):
        if model not in MODELS or language not in LANGUAGES:
            raise VoiceError('Μη έγκυρη ρύθμιση φωνής.')
        with self.lock:
            if self.closed:
                raise VoiceError('Η εφαρμογή κλείνει.')
            if self.busy():
                raise VoiceError('Εκτελείται ήδη μία εργασία φωνής.')
            ticket = uuid.uuid4().hex
            view = {'id': ticket, 'kind': action, 'status': 'preparing' if action == 'prepare' else 'transcribing',
                    'model': model, 'text': '', 'error': '', 'context': list(context)}
            job = {'view': view, 'cancel': threading.Event()}
            self.job = job
            self.thread = threading.Thread(target=self._perform, args=(job, language, raw, mime), daemon=True,
                                           name='akatikito-voice')
            self.thread.start()
            return dict(view)

    def _perform(self, job, language, raw, mime):
        try:
            view = job['view']
            request = {'action': view['kind'], 'folder': str(self.models / view['model']), 'model': view['model'],
                       'language': language, 'mime': mime}
            temp_root = self.storage / 'temporary'
            temp_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='voice-', dir=temp_root) as directory:
                if raw is not None:
                    file = Path(directory) / 'recording.audio'
                    file.write_bytes(raw)
                    request['audio'] = str(file)
                result = self.runner(request, job['cancel'])
                if result.get('error'):
                    raise VoiceError(result['error'])
                if view['kind'] == 'prepare' and not model_ready(self.models / view['model'], view['model']):
                    raise VoiceError('Η λήψη δεν ολοκληρώθηκε. Δοκίμασε ξανά από τις Ρυθμίσεις φωνής.')
                text = result.get('text', '')
                if not isinstance(text, str) or len(text) > 12000:
                    raise VoiceError('Μη έγκυρο αποτέλεσμα υπαγόρευσης.')
                with self.lock:
                    if not job['cancel'].is_set():
                        view.update(status='complete', text=text.strip())
        except Exception as exc:
            with self.lock:
                if not job['cancel'].is_set():
                    job['view'].update(status='error', error=str(exc)[:1200], text='')
        finally:
            with self.lock:
                if job['cancel'].is_set():
                    job['view'].update(status='cancelled', text='', error='')

    def _run_worker(self, request, cancel):
        from .verification import interpreter_for_checks
        interpreter = interpreter_for_checks()
        env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in ('TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL', 'API_KEY'))}
        cache = self.storage / 'cache'
        cache.mkdir(parents=True, exist_ok=True)
        for name in ('PYTHONHOME', 'PYTHONPATH', '__PYVENV_LAUNCHER__'):
            env.pop(name, None)
        env.update(TEMP=str(cache), TMP=str(cache), TMPDIR=str(cache), HF_HOME=str(cache / 'huggingface'),
                   XDG_CACHE_HOME=str(cache), HF_HUB_DISABLE_IMPLICIT_TOKEN='1', PYTHONDONTWRITEBYTECODE='1',
                   PYTHONIOENCODING='utf-8', HF_HUB_DISABLE_PROGRESS_BARS='1')
        if request['action'] == 'transcribe':
            env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        else:
            env.pop('HF_HUB_OFFLINE', None)
            env.pop('TRANSFORMERS_OFFLINE', None)
        if cancel.is_set():
            raise VoiceError('Η εργασία ακυρώθηκε.')
        process = subprocess.Popen([str(interpreter), '-B', str(Path(__file__).with_name('voice_worker.py'))],
                                   cwd=Path(__file__).resolve().parents[1], env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        with self.lock:
            self.process = process
        timeout = 900 if request['action'] == 'prepare' else 180
        deadline = time.monotonic() + timeout
        payload = json.dumps(request).encode('utf-8')
        try:
            while True:
                if cancel.is_set() or time.monotonic() >= deadline:
                    process.terminate()
                    try:
                        process.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate(timeout=2)
                    raise VoiceError('Η εργασία ακυρώθηκε.' if cancel.is_set() else 'Η τοπική αναγνώριση άργησε υπερβολικά. Δοκίμασε μικρότερο ηχητικό.')
                try:
                    stdout, stderr = process.communicate(input=payload, timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    payload = None
            if len(stdout) > 100000:
                raise VoiceError('Μη έγκυρο αποτέλεσμα της τοπικής αναγνώρισης.')
            try:
                result = json.loads(stdout.decode('utf-8'))
            except (ValueError, UnicodeError) as exc:
                raise VoiceError('Δεν ξεκίνησε η τοπική αναγνώριση. Έλεγξε την εγκατάσταση φωνής.') from exc
            if not isinstance(result, dict) or process.returncode and not result.get('error'):
                raise VoiceError('Απέτυχε η τοπική αναγνώριση.')
            return result
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=2)
            with self.lock:
                if self.process is process:
                    self.process = None

    def cancel(self, ticket=None):
        with self.lock:
            if not self.job or ticket and ticket != self.job['view']['id']:
                return False
            self.job['cancel'].set()
            self.job['view'].update(status='cancelled', text='', error='')
        return True

    def close(self):
        with self.lock:
            self.closed = True
        self.cancel()
        thread = self.thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=4)
