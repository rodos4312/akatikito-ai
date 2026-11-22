"""Offline voice recognition worker. Never imports the chat/provider model."""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from pathlib import Path

MODELS = ('base', 'small')
LANGUAGES = ('el', 'en', 'auto')
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_SECONDS = 65
RATE = 16000
FORMATS = {'audio/wav': 'wav', 'audio/webm': 'matroska', 'audio/ogg': 'ogg', 'audio/mp4': 'mov'}
REQUIRED = ('config.json', 'model.bin', 'tokenizer.json')


def model_ready(folder, model, verify=False):
    folder = Path(folder)
    try:
        marker = json.loads((folder / 'ready.json').read_text(encoding='utf-8'))
        files = marker['files']
        if marker.get('version') != 1 or marker.get('model') != model or not set(REQUIRED) <= set(files):
            return False
        if not any(name.startswith('vocabulary.') for name in files):
            return False
        for name, row in files.items():
            if Path(name).name != name or name == 'ready.json':
                return False
            file = folder / name
            if file.is_symlink() or not file.is_file() or file.stat().st_size != row['size'] or row['size'] <= 0:
                return False
            if verify and hashlib.sha256(file.read_bytes()).hexdigest() != row['sha256']:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def prepare_model(folder, model):
    if model not in MODELS:
        raise ValueError('Μη έγκυρο τοπικό μοντέλο φωνής.')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    from faster_whisper.utils import download_model
    download_model(model, output_dir=str(folder), cache_dir=str(folder.parent / 'download-cache'), use_auth_token=False)
    files = {}
    for file in folder.iterdir():
        if file.is_file() and (file.name in REQUIRED or file.name.startswith('vocabulary.') or file.name=='preprocessor_config.json'):
            files[file.name] = {'size': file.stat().st_size, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}
    marker = folder / 'ready.json'
    temp = marker.with_suffix('.tmp')
    temp.write_text(json.dumps({'version': 1, 'model': model, 'files': files}, sort_keys=True), encoding='utf-8')
    os.replace(temp, marker)
    if not model_ready(folder, model, verify=True):
        marker.unlink(missing_ok=True)
        raise ValueError('Το τοπικό μοντέλο δεν κατέβηκε ολόκληρο.')
    return {'ok': True, 'model': model}


def decode_audio(raw, mime):
    import av
    import numpy as np
    mime = mime.split(';', 1)[0].strip().lower()
    if mime not in FORMATS or not raw or len(raw) > MAX_AUDIO_BYTES:
        raise ValueError('Χρειάζεται ήχος WAV/WebM/OGG/MP4 έως 8 MiB.')
    chunks = []
    count = 0
    try:
        with av.open(io.BytesIO(raw), mode='r', format=FORMATS[mime]) as container:
            if not container.streams.audio:
                raise ValueError('Το αρχείο δεν περιέχει ήχο.')
            resampler = av.AudioResampler(format='s16', layout='mono', rate=RATE)
            for frame in container.decode(audio=0):
                for output in resampler.resample(frame):
                    samples = output.to_ndarray().flatten()
                    count += samples.size
                    if count > MAX_SECONDS * RATE:
                        raise ValueError('Ο ήχος ξεπερνά τα 65 δευτερόλεπτα. Κάνε μικρότερη υπαγόρευση.')
                    chunks.append(samples)
            for output in resampler.resample(None):
                samples = output.to_ndarray().flatten()
                count += samples.size
                if count > MAX_SECONDS * RATE:
                    raise ValueError('Ο ήχος ξεπερνά τα 65 δευτερόλεπτα.')
                chunks.append(samples)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Δεν διαβάστηκε το ηχητικό αρχείο.') from exc
    if not chunks:
        raise ValueError('Το ηχητικό αρχείο είναι κενό.')
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def transcribe(folder, model, raw, mime, language, model_factory=None):
    if model not in MODELS or language not in LANGUAGES:
        raise ValueError('Μη έγκυρη ρύθμιση φωνής.')
    if not model_ready(folder, model, verify=True):
        raise ValueError('Χρειάζεται πρώτα λήψη του τοπικού μοντέλου από τις Ρυθμίσεις φωνής.')
    samples = decode_audio(raw, mime)
    if not (abs(samples) > .00001).any():
        return {'ok': True, 'text': '', 'language': language, 'duration': len(samples) / RATE}
    if model_factory is None:
        from faster_whisper import WhisperModel
        model_factory = WhisperModel
    engine = model_factory(str(folder), device='cpu', compute_type='int8', cpu_threads=min(4, os.cpu_count() or 2),
                           num_workers=1, local_files_only=True)
    segments, info = engine.transcribe(samples, language=None if language == 'auto' else language,
                                      beam_size=3, vad_filter=True, condition_on_previous_text=False)
    parts = []
    for segment in segments:
        parts.append(segment.text.strip())
        if sum(map(len, parts)) > 12000:
            raise ValueError('Πολύ μεγάλο αποτέλεσμα υπαγόρευσης.')
    return {'ok': True, 'text': ' '.join(filter(None, parts)), 'language': info.language, 'duration': len(samples) / RATE}


def main():
    try:
        request = json.loads(sys.stdin.buffer.read(16385).decode('utf-8'))
        folder = Path(request['folder'])
        model = request['model']
        if request['action'] == 'prepare':
            result = prepare_model(folder, model)
        elif request['action'] == 'transcribe':
            file = Path(request['audio'])
            with file.open('rb') as stream:
                raw = stream.read(MAX_AUDIO_BYTES + 1)
            result = transcribe(folder, model, raw, request['mime'], request['language'])
        else:
            raise ValueError('Μη έγκυρη ενέργεια φωνής.')
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({'error': str(exc)[:1200]}, ensure_ascii=False), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
