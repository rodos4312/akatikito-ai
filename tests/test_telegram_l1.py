"""L1 Telegram hardening contract tests: host guard, pairing, approvals, files, loopback E2E."""
from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from akatikito import memory, settings
from akatikito.agent import Akatikito
from akatikito.channel_config import ChannelConfig
from akatikito.channel_plugins import BUNDLES
from akatikito.channel_sdk import ChannelError, Inbound
from akatikito.gateway import Gateway
from akatikito.model import ScriptedModel
from akatikito.telegram_pairing import PAIR_MAX_WRONG, TelegramPairing

VALID_TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijk"


def _load_telegram_mod():
    spec = importlib.util.spec_from_file_location("test_tg_l1", BUNDLES / "telegram" / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "bad_base",
    [
        "https://evil.example",
        "https://api.telegram.org.evil.example",
        "http://api.telegram.org",
        "https://user:pass@api.telegram.org",
        "https://api.telegram.org?x=1",
        "https://api.telegram.org#frag",
        "ftp://127.0.0.1",
        "not-a-url",
    ],
)
def test_telegram_rejects_foreign_or_unsafe_api_base(bad_base):
    mod = _load_telegram_mod()
    assert not mod.valid_api_base(bad_base)
    with pytest.raises(ChannelError, match="host"):
        mod.Telegram({"token": VALID_TOKEN, "api_base": bad_base})


def test_telegram_accepts_official_and_loopback_hosts_and_scrubs_tokens():
    mod = _load_telegram_mod()
    assert mod.valid_api_base("https://api.telegram.org")
    assert mod.valid_api_base("http://127.0.0.1:8081")
    assert mod.valid_api_base("http://localhost:8081")
    assert mod.valid_token(VALID_TOKEN)
    assert not mod.valid_token("123:short")
    with pytest.raises(ChannelError):
        mod.Telegram({"token": "123:abc/../../evil"})
    scrubbed = mod.scrub_token(f"err https://api.telegram.org/bot{VALID_TOKEN}/getMe", VALID_TOKEN)
    assert VALID_TOKEN not in scrubbed


def test_pairing_code_works_once_expires_and_burns_on_brute_force():
    now = {"t": 100.0}
    pairing = TelegramPairing(ttl=600, max_wrong=PAIR_MAX_WRONG, clock=lambda: now["t"])
    issued = pairing.issue()
    code = str(issued["code"])
    assert len(code) == 6 and code.isdigit()
    assert pairing.verify(f"/start {code}") is True
    assert pairing.verify(code) is False  # burned after single use

    code2 = str(pairing.issue()["code"])
    wrong = "000000" if code2 != "000000" else "111111"
    for _ in range(PAIR_MAX_WRONG):
        assert pairing.verify(wrong) is False
    assert pairing.verify(code2) is False  # burned after max wrong guesses

    code3 = str(pairing.issue()["code"])
    now["t"] += 601.0
    assert pairing.verify(code3) is False  # expired


def test_loopback_telegram_message_reaches_agent_and_stranger_is_ignored(tmp_path, monkeypatch):
    mod = _load_telegram_mod()
    directory = tmp_path / "data"
    project = tmp_path / "project"
    directory.mkdir()
    project.mkdir()
    monkeypatch.setattr(settings, "DEFAULT_PATH", directory / "settings.json")
    monkeypatch.setattr(memory, "DIR", directory / "memory")
    owner_settings = settings.Settings(
        provider="Custom",
        base_url="http://127.0.0.1:9/v1",
        model="scripted",
        project=str(project),
        checkpoints=False,
    )
    owner_settings.save()

    sent_messages: list[dict] = []
    updates = [
        {"update_id": 10, "message": {"from": {"id": 9999}, "chat": {"id": 9999, "type": "private"}, "text": "stranger"}},
        {"update_id": 11, "message": {"from": {"id": 4242}, "chat": {"id": 4242, "type": "private"}, "text": "hello bot"}},
    ]

    class FakeTGHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8")) if raw.startswith(b"{") else {}
            method = self.path.rsplit("/", 1)[-1]
            if method == "getMe":
                resp = {"ok": True, "result": {"id": 500, "is_bot": True}}
            elif method == "getUpdates":
                resp = {"ok": True, "result": list(updates)}
                updates.clear()
            elif method in ("sendMessage", "sendDocument"):
                sent_messages.append({"method": method, "body": body})
                resp = {"ok": True, "result": {"message_id": 1}}
            else:
                resp = {"ok": True, "result": True}
            encoded = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeTGHandler)
    port = server.server_address[1]
    srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
    srv_thread.start()
    try:
        cfg = {
            "token": VALID_TOKEN,
            "api_base": f"http://127.0.0.1:{port}",
            "allowed_users": ["4242"],
            "allowed_chats": [],
            "project": str(project),
            "mode": "ask",
            "enabled": True,
        }
        ChannelConfig(directory / "channels.json").save("telegram", cfg, {"telegram": {}})
        adapter = mod.Telegram(cfg)
        bot_id = adapter.connect()
        assert bot_id == "500"

        class EchoModel(ScriptedModel):
            def chat(self, messages, tools=None, **kwargs):
                return {"content": "Απάντηση από Akatikito.", "tool_calls": []}, {"prompt_tokens": 5, "completion_tokens": 3}

        gw = Gateway(directory, owner_settings, agent_factory=lambda root, config: Akatikito(root, config, model=EchoModel([])))
        gw.adapters["telegram"] = adapter
        cursor, events = adapter.poll("0", lambda: False)
        assert cursor == "12" and len(events) == 2
        accepted = gw.receive("telegram", bot_id, cursor, events)
        assert len(accepted) == 1 and accepted[0].user == "4242"  # stranger silently dropped
        gw.process(gw.store.next())
        gw.deliver(gw.store.outbox()[0])
        assert len(sent_messages) == 1
        assert sent_messages[0]["body"]["chat_id"] == "4242"
        assert "Απάντηση από Akatikito." in sent_messages[0]["body"]["text"]

        # File upload (send_file) works over loopback and rejects >10 MiB
        sample = project / "report.txt"
        sample.write_text("report body", encoding="utf-8")
        adapter.send_file(accepted[0], sample, caption="Εδώ το αρχείο")
        assert sent_messages[-1]["method"] == "sendDocument"
        adapter.close()
    finally:
        server.shutdown()
        server.server_close()


def test_inline_approval_allows_normal_and_blocks_dangerous_remotely(tmp_path, monkeypatch):
    mod = _load_telegram_mod()
    directory = tmp_path / "data"
    project = tmp_path / "project"
    directory.mkdir()
    project.mkdir()
    monkeypatch.setattr(settings, "DEFAULT_PATH", directory / "settings.json")
    monkeypatch.setattr(memory, "DIR", directory / "memory")
    owner_settings = settings.Settings(
        provider="Custom",
        base_url="http://127.0.0.1:9/v1",
        model="scripted",
        project=str(project),
        checkpoints=False,
    )
    owner_settings.save()
    cfg = {"token": VALID_TOKEN, "allowed_users": ["4242"], "allowed_chats": [], "project": str(project), "mode": "ask", "enabled": True}
    ChannelConfig(directory / "channels.json").save("telegram", cfg, {"telegram": {}})

    calls: list[tuple[str, dict]] = []

    class FakeHTTP:
        def request(self, method, path, data=None):
            calls.append((path, data or {}))
            if path == "/getMe":
                return {"ok": True, "result": {"id": 500, "is_bot": True}}
            return {"ok": True, "result": True}

        def close(self):
            pass

    adapter = mod.Telegram(cfg, http=FakeHTTP())
    adapter.connect()
    ev = Inbound("telegram", "500", "4242", "4242", "1", "run")

    gw = Gateway(directory, owner_settings, agent_factory=lambda root, config: Akatikito(root, config, model=ScriptedModel([])))
    gw.adapters["telegram"] = adapter
    state = gw._state(ev, cfg, gw.store.route(ev))
    gw.active[ev.key()] = {"state": state, "event": ev}

    ready = threading.Event()
    orig_notify = state.approvals.notify

    def _on_notify():
        orig_notify()
        if state.approvals.pending():
            ready.set()

    state.approvals.notify = _on_notify

    # Normal risk -> inline buttons sent, callback approves
    answers: list[bool] = []
    ready.clear()
    t1 = threading.Thread(target=lambda: answers.append(state.approvals.request(project, "command", {"command": "pytest"}, risk="normal")))
    t1.start()
    assert ready.wait(timeout=3)
    ticket = state.approvals.pending()[0]["id"]
    gw._notify_pending_approval(ev, state.approvals.pending()[0])
    assert "reply_markup" in calls[-1][1]
    reply = gw._resolve_inline_approval(ev, f"/approve {ticket} 1")
    t1.join(timeout=3)
    assert answers == [True] and "καταχωρήθηκε" in reply

    # Dangerous risk -> NO inline buttons, remote approve refused
    answers2: list[bool] = []
    ready.clear()
    t2 = threading.Thread(target=lambda: answers2.append(state.approvals.request(project, "command", {"command": "rm -rf /"}, risk="dangerous")))
    t2.start()
    assert ready.wait(timeout=3)
    ticket2 = state.approvals.pending()[0]["id"]
    gw._notify_pending_approval(ev, state.approvals.pending()[0])
    assert "reply_markup" not in calls[-1][1]
    refusal = gw._resolve_inline_approval(ev, f"/approve {ticket2} 1")
    assert "υπολογιστή" in refusal
    state.approvals.respond(ticket2, False)
    t2.join(timeout=3)
    assert answers2 == [False]
    state.close()
    gw.active.clear()
