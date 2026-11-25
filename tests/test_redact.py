"""Tests for centralized secret redaction (PRO-PLAN B1): sessions, audit log, exports, and plain text safety."""
from __future__ import annotations

import json

from akatikito.audit_log import AuditLog
from akatikito.redact import redact, redact_all, redact_obj
from akatikito.sessions import SessionStore
from akatikito.settings import Settings


def test_redact_masks_all_key_formats_and_preserves_normal_greek_prose():
    normal = "Το κλειδί του σπιτιού είναι στο συρτάρι και το cache_key = 'abc123456789'."
    assert redact(normal) == normal

    samples = [
        "sk-proj-AbCdEfGhIjKlMnOpQrStUv1234",
        "AIzaSyD1234567890abcdefghijkLMNOPQRST",
        "ghp_1234567890abcdefghijklmnop",
        "github_pat_11ABCDEFG_0123456789abcdef",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig12345",
        "OPENAI_API_KEY=super_secret_value_12345",
        "DB_PASSWORD='my_db_password_99'",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA12345\n-----END RSA PRIVATE KEY-----",
    ]
    for s in samples:
        out = redact(s)
        assert out != s
        for forbidden in ("AbCdEfGhIjKlMnOp", "AIzaSyD1234567890", "1234567890abcdef", "eyJhbGciOiJIUzI1NiJ9", "super_secret_value_12345", "my_db_password_99", "MIIEpAIBAAKCAQEA12345"):
            assert forbidden not in out


def test_redact_obj_preserves_base64_images_and_masks_known_user_key():
    img_uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    payload = {
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": img_uri}}, {"type": "text", "text": "my key is custom_provider_tok_987654"}]}
        ]
    }
    red, changed = redact_obj(payload, ("custom_provider_tok_987654",))
    assert changed is True
    assert red["messages"][0]["content"][0]["image_url"]["url"] == img_uri
    assert "custom_provider_tok_987654" not in json.dumps(red)


def test_session_store_and_audit_log_never_persist_secrets_on_disk(tmp_path, monkeypatch):
    # Save a custom user key in isolated settings
    spath = tmp_path / "settings.json"
    monkeypatch.setattr("akatikito.settings.DEFAULT_PATH", spath)
    st = Settings()
    st.set_key("DeepSeek", "custom_user_secret_xyz987654")
    st.save(spath)

    store = SessionStore(tmp_path)
    sid = store.create()
    store.save(
        sid,
        messages=[
            {"role": "user", "content": "Το κλειδί μου είναι sk-proj-SecretTestKey1234567890 και custom_user_secret_xyz987654"},
        ],
        transcript=[
            {"role": "user", "text": "OPENAI_API_KEY=leaked_env_token_77777"},
        ],
        request_cache={"key": "a" * 32, "context": {"version": 1}},
    )

    raw_session = (tmp_path / ".agent" / "sessions" / f"{sid}.json").read_text(encoding="utf-8")
    for secret in ("SecretTestKey1234567890", "custom_user_secret_xyz987654", "leaked_env_token_77777"):
        assert secret not in raw_session
    loaded = json.loads(raw_session)
    assert loaded["request_cache"] == {}

    audit = AuditLog(tmp_path)
    audit.record("tool_call", tool="run_command", summary="printed GITHUB_TOKEN=ghp_SecretToken1234567890123456")
    raw_audit = audit.path.read_text(encoding="utf-8")
    assert "ghp_SecretToken1234567890123456" not in raw_audit
    assert redact_all("plain", ()) == "plain"
