"""Streaming protocol, SDK SSE decoding, cancellation and app polling with synthetic data."""
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace as NS
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.model import OpenAIModel, ModelError, ModelStopped
from akatikito.streaming import StreamReply, StreamAssemblyError
from akatikito import memory


def chunk(text=None, *, calls=None, finish=None, reason=None, details=None, usage=None):
    delta = {}
    if text is not None:
        delta['content'] = text
    if calls is not None:
        delta['tool_calls'] = calls
    if reason is not None:
        delta['reasoning_content'] = reason
    if details is not None:
        delta['reasoning_details'] = details
    return {'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}], 'usage': usage}


def part(index, name=None, args=None, id=None):
    value = {'index': index, 'function': {}}
    if id is not None:
        value['id'] = id
    if name is not None:
        value['function']['name'] = name
    if args is not None:
        value['function']['arguments'] = args
    return value


class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
    def __iter__(self):
        for value in self.chunks:
            if isinstance(value, Exception):
                raise value
            yield value
    def close(self):
        self.closed = True


class StatusError(Exception):
    def __init__(self, text, status):
        super().__init__(text)
        self.status_code = status


def model_for(sequence, **config):
    sent = []
    items = iter(sequence)
    def create(**kwargs):
        sent.append(kwargs)
        result = next(items)
        if isinstance(result, Exception):
            raise result
        return result
    model = OpenAIModel.__new__(OpenAIModel)
    model.config = Config(api_key='synthetic', base_url='https://unit.invalid', model_retry_delay=0, **config)
    model._client = NS(chat=NS(completions=NS(create=create)))
    model._active_stream = None
    return model, sent


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'DIR', tmp_path / 'memory')
    monkeypatch.setenv('AKATIKITO_MEMORY_DIR', str(tmp_path / 'memory'))
    monkeypatch.setenv('AKATIKITO_SETTINGS_FILE', str(tmp_path / 'settings.json'))


def test_content_reasoning_and_final_usage_chunk_are_exact():
    reply = StreamReply()
    reply.feed(chunk('Γεια ', reason='  reason\n', details=[{'type': 'encrypted', 'data': 'opaque==', 'signature': 'signed'}]))
    reply.feed(chunk('κόσμε', reason='next\t', finish='stop'))
    reply.feed({'choices': [], 'usage': {'prompt_tokens': 11, 'completion_tokens': 4, 'prompt_cache_hit_tokens': 3}})
    result = reply.reply()
    assert result['content'] == 'Γεια κόσμε' and result['reasoning_content'] == '  reason\nnext\t'
    assert result['reasoning_details'] == [{'type': 'encrypted', 'data': 'opaque==', 'signature': 'signed'}]
    assert reply.usage['prompt_tokens'] == 11 and reply.usage['cache_hit_tokens'] == 3


def test_interleaved_function_fragments_assemble_by_index():
    reply = StreamReply()
    reply.feed(chunk(calls=[part(1, 'github_', '{"owner":', 'call-b'), part(0, 'read_', '{"path":"a', 'call-a')]))
    reply.feed(chunk(calls=[part(0, 'file', '.py"}'), part(1, 'read', '"chromium","repo":"chromium"}')], finish='tool_calls'))
    calls = reply.reply()['tool_calls']
    assert [c['id'] for c in calls] == ['call-a', 'call-b']
    assert calls[0]['name'] == 'read_file' and json.loads(calls[0]['arguments']) == {'path': 'a.py'}
    assert calls[1]['name'] == 'github_read'


@pytest.mark.parametrize('finish', [None, 'length', 'content_filter'])
def test_unfinished_truncated_or_filtered_reply_never_returns_calls(finish):
    reply = StreamReply()
    reply.feed(chunk(calls=[part(0, 'write_file', '{"path":', 'call-1')], finish=finish))
    with pytest.raises(StreamAssemblyError):
        reply.reply()


@pytest.mark.parametrize('args', ['{"path":', '[]', 'null'])
def test_incomplete_or_nonobject_tool_json_is_rejected(args):
    reply = StreamReply()
    reply.feed(chunk(calls=[part(0, 'write_file', args, 'call-1')], finish='tool_calls'))
    with pytest.raises(StreamAssemblyError):
        reply.reply()


def test_opaque_indexed_fragments_keep_exact_strings():
    reply = StreamReply()
    reply.feed(chunk(details=[{'index': 0, 'type': 'reasoning.text', 'text': '  original\n', 'signature': 'sig-'}]))
    reply.feed(chunk(details=[{'index': 0, 'type': 'reasoning.text', 'text': '\tend', 'signature': 'tail'}], finish='stop'))
    assert reply.reply()['reasoning_details'] == [{'index': 0, 'type': 'reasoning.text', 'text': '  original\n\tend', 'signature': 'sig-tail'}]


def test_model_emits_before_completion_and_closes_stream():
    stream = FakeStream([chunk('first '), chunk('second', finish='stop'), {'choices': [], 'usage': {'prompt_tokens': 9, 'completion_tokens': 2}}])
    model, sent = model_for([stream])
    deltas = []
    def delta(text):
        assert not stream.closed
        deltas.append(text)
    reply, usage = model.chat_stream([{'role': 'user', 'content': 'synthetic'}], on_delta=delta)
    assert deltas == ['first ', 'second'] and reply['content'] == 'first second'
    assert usage['prompt_tokens'] == 9 and stream.closed
    assert sent[0]['stream'] is True and sent[0]['stream_options']['include_usage'] is True


def test_retry_before_first_chunk_and_no_retry_after_partial_output():
    successful = FakeStream([chunk('done', finish='stop')])
    model, sent = model_for([StatusError('temporary', 503), successful])
    assert model.chat_stream([])[0]['content'] == 'done' and len(sent) == 2
    partial = FakeStream([chunk('partial'), StatusError('connection lost', 503)])
    model, sent = model_for([partial, successful])
    with pytest.raises(ModelError, match='connection lost'):
        model.chat_stream([])
    assert len(sent) == 1 and partial.closed


def test_role_only_first_chunk_still_prevents_paid_replay():
    stream = FakeStream([chunk(), StatusError('lost after response began', 503)])
    model, sent = model_for([stream])
    with pytest.raises(ModelError):
        model.chat_stream([])
    assert len(sent) == 1 and stream.closed


def test_unsupported_usage_option_retries_without_it_within_same_budget():
    model, sent = model_for([StatusError('unsupported stream_options', 400), FakeStream([chunk('ok', finish='stop')])])
    assert model.chat_stream([])[0]['content'] == 'ok'
    assert len(sent) == 2 and 'stream_options' not in sent[1] and sent[1]['stream']


def test_explicitly_unsupported_stream_uses_normal_response_once():
    response = NS(choices=[NS(message=NS(content='fallback', tool_calls=[], reasoning_content=''))], usage=None)
    model, sent = model_for([StatusError('stream is not supported', 400), response])
    assert model.chat_stream([])[0]['content'] == 'fallback'
    assert len(sent) == 2 and 'stream' not in sent[1] and 'stream_options' not in sent[1]


def test_hard_quota_is_not_retried_even_before_chunks():
    model, sent = model_for([StatusError('weekly included input token limit exceeded', 429)])
    with pytest.raises(ModelError):
        model.chat_stream([])
    assert len(sent) == 1


def test_stop_closes_stream_and_rejects_partial_tool_call():
    stopped = False
    stream = FakeStream([chunk('preview'), chunk(calls=[part(0, 'write_file', '{"path":', 'c')])])
    model, sent = model_for([stream])
    def delta(text):
        nonlocal stopped
        stopped = True
        model.cancel_stream()
    with pytest.raises(ModelStopped):
        model.chat_stream([], on_delta=delta, should_stop=lambda: stopped)
    assert stream.closed and len(sent) == 1 and model._active_stream is None


def test_glm_stream_keeps_protocol_in_the_next_tool_request(tmp_path):
    (tmp_path / 'a.py').write_text('x = 1\n', encoding='utf-8')
    first = FakeStream([chunk(reason='  EXACT\n', calls=[part(0, 'read_file', '{"path":"a.py"}', 'c')], finish='tool_calls')])
    second = FakeStream([chunk('done', finish='stop')])
    model, sent = model_for([first, second], model='glm-synthetic')
    agent = Akatikito(str(tmp_path), Config(model='glm-synthetic', auto_mode=False), model)
    assert agent.chat('inspect') == 'done'
    previous = next(m for m in sent[1]['messages'] if m.get('tool_calls'))
    assert previous['reasoning_content'] == '  EXACT\n'
    assert sent[0]['extra_body']['thinking']['clear_thinking'] is False
    assert any(e.kind == 'stream_delta' and e.data['text'] == 'done' for e in agent.events.log)


def test_broken_tool_stream_never_executes_a_write(tmp_path):
    stream = FakeStream([chunk(calls=[part(0, 'write_file', '{"path":"bad.txt","content":', 'c')]), StatusError('lost', 503)])
    model, sent = model_for([stream])
    agent = Akatikito(str(tmp_path), Config(auto_mode=False), model)
    with pytest.raises(ModelError):
        agent.chat('write a file')
    assert not (tmp_path / 'bad.txt').exists() and len(sent) == 1
    assert not any(e.kind == 'tool_call' for e in agent.events.log)
    assert agent.events.log[-1].kind == 'stream_end' and agent.events.log[-1].data['status'] == 'error'


@pytest.mark.parametrize("ending", ["complete", "stop", "error"])
def test_real_sdk_sse_parser_and_app_poll_deliver_text_before_done(tmp_path, ending):
    import httpx
    from openai import OpenAI
    from akatikito.app_api import AppState, start_server
    from akatikito.sessions import SessionStore
    import urllib.request
    first_seen = threading.Event()
    release = threading.Event()
    class Bytes(httpx.SyncByteStream):
        closed = False
        def __iter__(self):
            # Deliberately split UTF-8 and SSE frames across arbitrary byte boundaries.
            event = {'id': 's', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'synthetic', **chunk('Ελληνικά ')}
            raw = ('data: ' + json.dumps(event, ensure_ascii=False) + '\n\n').encode()
            for i in range(0, len(raw), 7):
                yield raw[i:i+7]
            assert release.wait(5), 'Test did not release the synthetic stream'
            if ending == 'error':
                raise httpx.ReadError('synthetic interrupted SSE')
            event.update(chunk('τέλος', finish='stop'))
            yield ('data: ' + json.dumps(event, ensure_ascii=False) + '\n\ndata: [DONE]\n\n').encode()
        def close(self):
            self.closed = True
    body = Bytes()
    requests = []
    def respond(request):
        assert request.url.host == 'unit.invalid'
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=body)
    client = OpenAI(base_url='https://unit.invalid/v1', api_key='synthetic', max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(respond), trust_env=False))
    model, _ = model_for([])
    model._client = client
    agent = Akatikito(str(tmp_path), Config(auto_mode=False), model)
    state = AppState()
    done = threading.Event()
    original_push = state.push
    def push(kind, **data):
        original_push(kind, **data)
        if kind == 'done':
            done.set()
    state.push = push
    state.agent = agent
    state.store = SessionStore(tmp_path)
    agent.events.subscribe(state.emit_event)
    agent.events.subscribe(lambda e: first_seen.set() if e.kind == 'stream_delta' else None)
    url, server = start_server(state)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    def post(route, data):
        req = urllib.request.Request(url + route, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json', 'X-Akatikito-Token':server.auth_token})
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)
    try:
        assert post('/api/send', {'text': 'synthetic local request'})['ok']
        assert first_seen.wait(3), 'No partial text before completion'
        partial = post('/api/poll', {'since': 0})
        assert partial['busy'] and partial['stream']['text'] == 'Ελληνικά '
        assert not any(m['role'] == 'assistant' for m in partial['transcript'])
        if ending == 'stop':
            assert post('/api/stop', {})['ok']
            assert body.closed
        release.set()
        assert done.wait(3), 'App did not complete the local synthetic request' 
        final = post('/api/poll', {'since': partial['last_seq']})
        assert not final['busy']
        answers = [m for m in final['transcript'] if m['role'] == 'assistant']
        expected = 'Ελληνικά τέλος' if ending == 'complete' else 'Σταμάτησα όπως ζήτησες.'
        if ending == 'error':
            assert not answers and 'synthetic interrupted SSE' in final['error']
            assert final['stream']['status'] == 'error'
        else:
            assert len(answers) == 1 and answers[0]['text'] == expected
            assert final['stream']['status'] == ('complete' if ending == 'complete' else 'stopped')
        saved = state.store.load(state.session_id)
        assert len([m for m in saved['transcript'] if m['role'] == 'assistant']) == (0 if ending == 'error' else 1)
        if ending == 'error':
            assert saved['messages'][-1]['role'] == 'assistant'
            assert 'Δεν ολοκληρώθηκε' in saved['messages'][-1]['content']
            assert 'synthetic interrupted SSE' in saved['messages'][-1]['content']
        elif ending == 'stop':
            assert saved['messages'][-1]['content'] == 'Σταμάτησα όπως ζήτησες.'
        assert len(requests) == 1 and requests[0]['stream'] is True and body.closed
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        client.close()


def test_stream_ui_behavior_runs_in_node():
    result = subprocess.run(['node', str(Path(__file__).with_name('test_ui_streaming.cjs'))],
                            capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_stop_before_request_opens_no_connection():
    model, sent = model_for([])
    with pytest.raises(ModelStopped):
        model.chat_stream([], should_stop=lambda: True)
    assert not sent


def test_duplicate_tool_ids_are_rejected_before_execution():
    reply = StreamReply()
    reply.feed(chunk(calls=[part(0, 'read_file', '{"path":"a.py"}', 'same'), part(1, 'read_file', '{"path":"b.py"}', 'same')], finish='tool_calls'))
    with pytest.raises(StreamAssemblyError):
        reply.reply()
