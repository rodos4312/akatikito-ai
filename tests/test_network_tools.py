"""Public network tools and real agent routing, with no external/model traffic."""
import base64
import gzip
import json
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from akatikito import network_tools as net, modes
from akatikito.activity import ActivityGuard
from akatikito.agent import Akatikito
from akatikito.config import Config
from akatikito.context import ContextWindow
from akatikito.model import OpenAIModel, ScriptedModel, tool_call
from akatikito.tools import TOOL_SPECS, _HANDLERS, dispatch
from akatikito.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv('AKATIKITO_MEMORY_DIR', str(tmp_path / 'memory'))
    monkeypatch.setenv('AKATIKITO_SETTINGS_FILE', str(tmp_path / 'settings.json'))
    monkeypatch.setattr(net.socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('8.8.8.8', 443))])
    def forbidden(*a, **kw):
        raise AssertionError('Unexpected real network/model request')
    monkeypatch.setattr(net.socket, 'create_connection', forbidden)
    monkeypatch.setattr(OpenAIModel, 'chat', forbidden)


@pytest.fixture
def ws(tmp_path):
    return Workspace(tmp_path)


def json_response(data, url='https://api.github.com/repos/chromium/chromium/contents/README.md'):
    return net.Response(url, json.dumps(data).encode(), {'content-type': 'application/json'})


def file_data(text, **overrides):
    result = dict(type='file', size=len(text.encode()), encoding='base64',
                  content=base64.b64encode(text.encode()).decode(), sha='abc123',
                  html_url='https://github.com/chromium/chromium/blob/main/README.md')
    result.update(overrides)
    return result


class FakeResponse:
    def __init__(self, body=b'hello', status=200, **headers):
        self.body = body
        self.status = status
        self.headers = headers
    def getheaders(self):
        return list(self.headers.items())
    def read1(self, size):
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk


@pytest.fixture
def transport(monkeypatch):
    state = SimpleNamespace(queue=[], calls=[], connections=[])
    class Connection:
        def __init__(self, host, port, address, secure, timeout):
            self.sock = None
            self.closed = False
            state.connections.append(self)
            state.calls.append(dict(host=host, port=port, address=address, secure=secure, timeout=timeout))
        def request(self, method, path, headers):
            state.calls[-1].update(method=method, path=path, headers=headers)
        def getresponse(self):
            result = state.queue.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        def close(self):
            self.closed = True
    monkeypatch.setattr(net, 'PinnedConnection', Connection)
    return state


@pytest.mark.parametrize('url', [
    'file:///D:/secret', 'ftp://example.com/a', 'http://localhost/a',
    'http://x.local/a', 'http://x.internal/a', 'http://127.0.0.1/a',
    'http://10.1.2.3', 'http://169.254.169.254/latest', 'http://[::1]/',
    'http://[::ffff:127.0.0.1]/', 'http://user:secret@example.com/',
    'https://example.com:0/', 'https://example.com:99999/',
    'https://example.com/\r\nHeader:x', 'https://example.com\\@localhost/a',
])
def test_private_credentials_and_unsupported_urls_rejected(url):
    with pytest.raises(PermissionError):
        net._public_url(url)


def test_mixed_public_private_dns_rejected(monkeypatch):
    monkeypatch.setattr(net.socket, 'getaddrinfo', lambda *a, **kw: [
        (2, 1, 6, '', ('8.8.8.8', 443)), (2, 1, 6, '', ('10.0.0.4', 443))])
    with pytest.raises(PermissionError):
        net._public_url('https://public.example')


def test_public_unicode_path_and_query_encoded_without_double_encoding():
    scheme, host, port, path, addresses = net._public_url('https://EXAMPLE.com/α%20b?q=hello world#ignored')
    assert (scheme, host, port, addresses) == ('https', 'example.com', 443, ['8.8.8.8'])
    assert path == '/%CE%B1%20b?q=hello%20world'


def test_transport_uses_pinned_ip_and_sends_no_ambient_credentials(transport, monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'NEVER-SEND')
    monkeypatch.setenv('HTTPS_PROXY', 'https://secret@localhost:8080')
    transport.queue = [FakeResponse(b'data', **{'Content-Type': 'text/plain'})]
    response = net._get('https://example.com/file')
    assert response.data == b'data' and response.headers['content-type'] == 'text/plain'
    call = transport.calls[0]
    assert call['address'] == '8.8.8.8' and call['secure'] and call['path'] == '/file'
    assert 'Authorization' not in call['headers'] and 'Cookie' not in call['headers']
    assert all(c.closed for c in transport.connections)


def test_tls_still_verifies_original_hostname(monkeypatch):
    sock = SimpleNamespace(close=lambda: None)
    observed = []
    monkeypatch.setattr(net.socket, 'create_connection', lambda address, timeout: observed.append(address) or sock)
    monkeypatch.setattr(net.ssl, 'create_default_context', lambda: SimpleNamespace(
        wrap_socket=lambda value, server_hostname: observed.append(server_hostname) or value))
    connection = net.PinnedConnection('example.com', 443, '8.8.8.8', True, 5)
    connection.connect()
    assert observed == [('8.8.8.8', 443), 'example.com'] and connection.sock is sock


@pytest.mark.parametrize('destination', ['http://127.0.0.1/secret', 'https://10.0.0.1/secret', 'http://example.com/downgrade'])
def test_redirect_revalidated_and_https_downgrade_rejected(transport, destination):
    transport.queue = [FakeResponse(status=302, Location=destination)]
    with pytest.raises(PermissionError):
        net._get('https://example.com/start')
    assert len(transport.calls) == 1 and transport.connections[0].closed


def test_relative_redirect_and_github_api_boundary(transport):
    transport.queue = [FakeResponse(status=302, Location='/next'), FakeResponse(b'done')]
    assert net._get('https://example.com/start').data == b'done'
    assert [c['path'] for c in transport.calls] == ['/start', '/next']
    transport.queue = [FakeResponse(status=302, Location='https://example.com/untrusted')]
    with pytest.raises(PermissionError):
        net._get('https://api.github.com/repos/a/b', api_only=True)
    assert len(transport.calls) == 3


def test_redirect_loop_and_limit(transport):
    transport.queue = [FakeResponse(status=302, Location='/start')]
    with pytest.raises(net.NetworkError, match='Βρόχος'):
        net._get('https://example.com/start')
    transport.queue = [FakeResponse(status=302, Location=f'/{i}') for i in range(5)]
    with pytest.raises(net.NetworkError, match='ανακατευθύνσεις'):
        net._get('https://example.com/a')


@pytest.mark.parametrize('response', [
    FakeResponse(**{'Content-Length': str(net.MAX_BYTES + 1)}),
    FakeResponse(b'a' * (net.MAX_BYTES + 1)),
    FakeResponse(gzip.compress(b'a' * (net.MAX_BYTES + 1)), **{'Content-Encoding': 'gzip'}),
])
def test_bounded_download_and_decompression(transport, response):
    transport.queue = [response]
    with pytest.raises(net.NetworkError, match='2 MiB'):
        net._get('https://example.com/big')
    assert all(c.closed for c in transport.connections)


@pytest.mark.parametrize('status,headers,message', [
    (404, {}, '404'), (429, {'Retry-After': '30'}, 'όριο αιτημάτων'),
    (403, {'X-RateLimit-Remaining': '0'}, 'όριο αιτημάτων'),
])
def test_http_errors_are_actionable_and_not_retried(transport, status, headers, message):
    transport.queue = [FakeResponse(status=status, **headers)]
    with pytest.raises(net.NetworkError, match=message):
        net._get('https://example.com/a')
    assert len(transport.calls) == 1


def test_total_read_deadline_prevents_slow_body(transport, monkeypatch):
    transport.queue = [FakeResponse(b'data')]
    clock = iter([0, 0, net.DEADLINE + 1])
    monkeypatch.setattr(net.time, 'monotonic', lambda: next(clock))
    with pytest.raises(net.NetworkError, match='χρόνος'):
        net._get('https://example.com/slow')
    assert transport.connections[0].closed


def test_timeout_reports_failure_without_unbounded_retry(transport):
    transport.queue = [TimeoutError('synthetic')]
    with pytest.raises(net.NetworkError, match='timeout'):
        net._get('https://example.com/slow')
    assert len(transport.calls) == 1


def test_html_extraction_and_exact_archive_paging(ws, monkeypatch):
    html = '<h1>Hello &amp; κόσμε</h1><script>secret()</script><style>hidden</style><p>' + 'abc ' * 100 + '</p>'
    monkeypatch.setattr(net, '_get', lambda *a, **kw: net.Response('https://example.com', html.encode(), {'content-type': 'text/html'}))
    result = dispatch(ws, 'web_fetch', {'url': 'https://example.com', 'max_chars': 100})
    assert 'Hello & κόσμε' in result and 'secret()' not in result and 'hidden' not in result
    assert 'next_start_char=100' in result
    key = re.search(r'id=([a-f0-9]{20})', result).group(1)
    exact = ContextWindow(ws)._load(key)
    result2 = net.web_fetch(ws, 'https://example.com', start_char=100, max_chars=100)
    assert exact[100:200] in result2 and len(exact) > 200


@pytest.mark.parametrize('body,kind', [(b'%PDF-example', 'application/pdf'), (b'\0PNG', 'image/png'), (b'abc\0def', 'text/plain')])
def test_web_binary_formats_rejected(ws, monkeypatch, body, kind):
    monkeypatch.setattr(net, '_get', lambda *a, **kw: net.Response('https://example.com', body, {'content-type': kind}))
    assert dispatch(ws, 'web_fetch', {'url': 'https://example.com'}).startswith('ΣΦΑΛΜΑ')


def test_github_file_ref_is_encoded_and_pagination_keeps_exact_source(ws, monkeypatch):
    text = ''.join(f'line {n} α\n' for n in range(1, 31))
    requests = []
    def get(url, **kw):
        requests.append((url, kw))
        return json_response(file_data(text))
    monkeypatch.setattr(net, '_get', get)
    result = net.github_read(ws, 'chromium', 'chromium', 'base/a b.h', ref='branch/test', max_lines=12)
    assert '/contents/base/a%20b.h?ref=branch%2Ftest' in requests[0][0]
    assert requests[0][1]['api_only'] and 'SHA: abc123' in result
    assert '   12 | line 12 α' in result and 'next_start_line=13' in result
    key = re.search(r'id=([a-f0-9]{20})', result).group(1)
    assert ContextWindow(ws)._load(key) == text
    assert '   13 | line 13 α' in net.github_read(ws, 'chromium', 'chromium', 'a', start_line=13)


def test_github_root_directory_without_cloning(ws, monkeypatch):
    routes = []
    monkeypatch.setattr(net, '_api', lambda route, params: routes.append(route) or [
        {'type': 'dir', 'path': 'base'}, {'type': 'file', 'path': 'README.md'}])
    result = dispatch(ws, 'github_read', {'owner': 'chromium', 'repo': 'chromium'})
    assert routes == ['/repos/chromium/chromium/contents']
    assert 'dir base' in result and 'file README.md' in result
    assert not (ws.root / 'chromium').exists()


def test_large_github_file_uses_official_raw_endpoint_not_download_url(ws, monkeypatch):
    requests = []
    def get(url, **kw):
        requests.append((url, kw['accept']))
        if len(requests) == 1:
            return json_response(file_data('', encoding='none', content='', size=1100000, download_url='http://localhost/secret'))
        return net.Response(url, b'// raw source\n', {'content-type': 'text/plain'})
    monkeypatch.setattr(net, '_get', get)
    result = net.github_read(ws, 'chromium', 'chromium', 'source.cc', ref='commit')
    assert '// raw source' in result and requests[0][0] == requests[1][0]
    assert requests[1][1] == 'application/vnd.github.raw+json'


@pytest.mark.parametrize('owner,repo,path', [('a/b', 'c', ''), ('a', '..', ''), ('a', 'b', '../secret'), ('a', 'b', 'a\\b'), ('a', 'b', 'a/./b')])
def test_github_invalid_coordinates_rejected_before_request(ws, monkeypatch, owner, repo, path):
    def forbidden(*a, **kw):
        raise AssertionError('Invalid route reached network')
    monkeypatch.setattr(net, '_api', forbidden)
    assert dispatch(ws, 'github_read', dict(owner=owner, repo=repo, path=path)).startswith('ΣΦΑΛΜΑ')


@pytest.mark.parametrize('data', [dict(type='symlink'), file_data('', size=net.MAX_BYTES + 1), file_data('', content='!bad!'), file_data('nul\0value')])
def test_github_unsupported_invalid_and_binary_files(ws, monkeypatch, data):
    monkeypatch.setattr(net, '_api', lambda *a, **kw: data)
    assert dispatch(ws, 'github_read', {'owner': 'a', 'repo': 'b', 'path': 'x'}).startswith('ΣΦΑΛΜΑ')


def test_search_and_issue_routes_and_filters(ws, monkeypatch):
    requests = []
    def api(route, params):
        requests.append((route, params))
        if route.startswith('/search'):
            return dict(total_count=2, items=[dict(full_name='chromium/chromium', html_url='https://github.com/chromium/chromium', description='source')])
        return [dict(number=1, title='PR hidden', pull_request={}), dict(number=2, title='Real bug', state='open', body='Evidence', html_url='https://github.com/a/b/issues/2')]
    monkeypatch.setattr(net, '_api', api)
    assert 'chromium/chromium' in net.github_search(ws, 'chromium', per_page=1, page=2)
    result = net.github_issues(ws, 'a', 'b', state='closed', per_page=2)
    assert 'Real bug' in result and 'PR hidden' not in result
    assert requests[0][1]['page'] == 2 and requests[1][1]['state'] == 'closed'
    assert 'page=2' in result


def test_github_json_error_and_dispatch_errors(ws, monkeypatch):
    monkeypatch.setattr(net, '_get', lambda *a, **kw: net.Response('https://api.github.com', b'<error>', {}))
    assert 'JSON' in dispatch(ws, 'github_search', {'query': 'chromium'})
    def denied(*a, **kw):
        raise PermissionError('private target')
    monkeypatch.setattr(net, '_get', denied)
    assert dispatch(ws, 'web_fetch', {'url': 'https://example.com'}).startswith('ΑΠΟΡΡΙΨΗ')


def test_clone_is_public_bounded_and_does_not_overwrite(ws, monkeypatch):
    monkeypatch.setattr(net, '_api', lambda *a: {'private': False})
    calls = []
    monkeypatch.setattr(net.subprocess, 'run', lambda argv, **kw: calls.append((argv, kw)) or SimpleNamespace(returncode=0))
    assert net.github_clone(ws, 'https://github.com/a/b.git').startswith('OK')
    argv, kw = calls[0]
    assert '--depth' in argv and '--no-recurse-submodules' in argv and 'credential.helper=' in argv
    assert kw['timeout'] == 60 and kw['env']['GIT_TERMINAL_PROMPT'] == '0'
    (ws.root / 'existing').mkdir()
    assert dispatch(ws, 'github_clone', {'url': 'https://github.com/a/b', 'dest': 'existing'}).startswith('ΣΦΑΛΜΑ')
    assert dispatch(ws, 'github_clone', {'url': 'https://github.com/a/b', 'dest': '../outside'}).startswith('ΑΠΟΡΡΙΨΗ')
    assert len(calls) == 1


def test_clone_respects_write_guard_and_private_repo(ws, monkeypatch):
    monkeypatch.setattr(net, '_api', lambda *a: {'private': True})
    assert dispatch(ws, 'github_clone', {'url': 'https://github.com/a/b'}).startswith('ΑΠΟΡΡΙΨΗ')
    def guard(*a):
        raise PermissionError('pending failed test')
    ws.write_guard = guard
    assert 'pending failed test' in dispatch(ws, 'github_clone', {'url': 'https://github.com/a/b'})


def test_mode_tool_schemas_and_handlers_are_connected():
    names = [item['function']['name'] for item in TOOL_SPECS]
    assert len(names) == len(set(names))
    for mode in modes.MODES:
        assert set(net.READ_TOOLS) <= set(modes.allowed_tools(mode))
        assert ('github_clone' in modes.allowed_tools(mode)) == (mode in ('code', 'debug'))
    assert set(net.NETWORK_HANDLERS) <= set(_HANDLERS)
    assert set(net.NETWORK_HANDLERS) <= set(names)


def test_actual_agent_turn_routes_remote_tools_and_returns_conclusion(ws, monkeypatch):
    monkeypatch.setattr(net, '_get', lambda *a, **kw: json_response(file_data('Chromium public source\n')))
    class CaptureModel(ScriptedModel):
        def chat(self, messages, tools=None, cache_key=None):
            self.offered = [item['function']['name'] for item in tools]
            return super().chat(messages, tools, cache_key)
    model = CaptureModel([
        {'tool_calls': [tool_call('github_read', owner='chromium', repo='chromium', path='README.md')]},
        {'content': 'Διαβάστηκε το πραγματικό αποτέλεσμα του εργαλείου.'}])
    agent = Akatikito(str(ws.root), Config(api_key='unused', auto_mode=False, max_steps=4), model)
    agent.set_mode('ask')
    assert agent.chat('Διάβασε το README του Chromium') == 'Διαβάστηκε το πραγματικό αποτέλεσμα του εργαλείου.'
    assert 'github_read' in model.offered and 'github_clone' not in model.offered
    assert model.calls == 2
    assert any('Chromium public source' in m.get('content', '') for m in model.seen[-1] if m['role'] == 'tool')
    assert not any(e.kind == 'verify' for e in agent.events.log)


def test_remote_repeat_guard_allows_continuation_but_blocks_identical_loop(ws):
    guard = ActivityGuard(ws)
    args = dict(owner='chromium', repo='chromium', path='README.md')
    for _ in range(2):
        assert not guard.before('github_read', args)
        guard.record('github_read', args, 'source')
    assert guard.before('github_read', args).startswith('ΑΠΟΤΡΟΠΗ')
    assert not guard.before('github_read', dict(args, start_line=30))
    assert not guard.before('github_read', dict(args, ref='different-commit'))


def test_search_does_not_offer_an_empty_next_page_for_known_repository(ws, monkeypatch):
    monkeypatch.setattr(net, '_api', lambda *a: {'total_count': 1, 'items': [{'full_name': 'chromium/chromium'}]})
    result = net.github_search(ws, 'repo:chromium/chromium', per_page=1)
    assert 'chromium/chromium' in result and 'Επόμενη' not in result


def test_empty_file_is_readable_but_invalid_continuation_is_rejected(ws, monkeypatch):
    monkeypatch.setattr(net, '_api', lambda *a: file_data(''))
    assert 'από 0' in net.github_read(ws, 'a', 'b', 'empty')
    with pytest.raises(net.NetworkError):
        net.github_read(ws, 'a', 'b', 'empty', start_line=2)
