"""L7 Wave 4 channel plugins tests: Discord, Slack, and WhatsApp bundles + host guards."""
from __future__ import annotations

import importlib.util

import pytest

from akatikito.channel_plugins import BUNDLES, Plugins
from akatikito.channel_sdk import ChannelError, Inbound


def test_wave4_catalog_contains_discord_slack_and_whatsapp_disabled_by_default(tmp_path):
    plugins = Plugins(tmp_path / "plugins")
    cat = plugins.catalog()
    avail = {row["id"]: row for row in cat["available"]}
    assert {"telegram", "email", "discord", "slack", "whatsapp"} <= set(avail.keys())
    for ident in ("discord", "slack", "whatsapp"):
        assert avail[ident]["installed"] is False

    # Review & install WhatsApp from bundled catalog
    draft = plugins.preview("catalog:whatsapp")
    installed = plugins.install(draft["id"], draft["review"])
    assert installed["id"] == "whatsapp"
    adapter = plugins.load("whatsapp", {"token": "1234567890|EAAGFakeAccessToken12345"})
    adapter.close()


def test_whatsapp_adapter_host_guard_webhook_normalization_and_send():
    spec = importlib.util.spec_from_file_location("test_wa", BUNDLES / "whatsapp" / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)

    # Foreign API host rejected
    with pytest.raises(ChannelError, match="host"):
        mod.WhatsAppChannel({"token": "12345|secret_tok", "api_base": "https://evil.example"})

    sent: list[tuple[str, dict | None]] = []

    class FakeHTTP:
        def request(self, method, path, data=None):
            sent.append((path, data))
            if method == "GET":
                return {"id": "12345"}
            return {"messages": [{"id": "wamid.out1"}]}

        def close(self):
            pass

    ch = mod.WhatsAppChannel({"token": "12345|secret_tok_999"}, http=FakeHTTP())
    assert ch.connect() == "12345"

    webhook = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"id": "wamid.1", "from": "306900000001", "type": "text", "text": {"body": "Καλημέρα"}},
                                {"id": "wamid.1", "from": "306900000001", "type": "text", "text": {"body": "duplicate"}},
                                {"id": "wamid.2", "from": "306900000001", "type": "audio"},
                            ]
                        }
                    }
                ]
            }
        ]
    }
    ingested = ch.ingest_webhook_payload(webhook)
    assert len(ingested) == 2  # duplicate dropped
    cursor, polled = ch.poll("0", lambda: False)
    assert cursor == "2" and len(polled) == 2
    assert polled[0].text == "Καλημέρα" and "Φωνητικό" in polled[1].text

    ch.send(polled[0], "Απάντηση με secret_tok_999")
    assert sent[-1][1]["text"]["body"] == "Απάντηση με [hidden]"
    ch.close()


def test_slack_adapter_connect_poll_and_send():
    spec = importlib.util.spec_from_file_location("test_sl", BUNDLES / "slack" / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)

    sent: list[tuple[str, dict | None]] = []

    class FakeHTTP:
        def request(self, method, path, data=None):
            sent.append((path, data))
            if path == "/auth.test":
                return {"ok": True, "user_id": "U_BOT"}
            if path.startswith("/conversations.history"):
                return {
                    "ok": True,
                    "messages": [{"ts": "200.000000", "user": "U_OWNER", "text": "status?"}],
                    "has_more": False,
                }
            return {"ok": True}

        def close(self):
            pass

    ch = mod.SlackChannel({"token": "xoxb-fake", "allowed_chats": ["C123"]}, http=FakeHTTP())
    assert ch.connect() == "U_BOT"
    cursor, events = ch.poll("100.0", lambda: False)
    assert len(events) == 1 and events[0].platform == "slack" and events[0].text == "status?"
    ch.send(Inbound("slack", "U_BOT", "C123", "U_OWNER", "200.0", "status?", "200.0", True), "Όλα πράσινα!")
    assert sent[-1][0] == "/chat.postMessage" and sent[-1][1]["thread_ts"] == "200.0"
    ch.close()
