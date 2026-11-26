"""F4 real-entry-point tests: Issue Report preview scrubbing (keys/paths/emails/memory), safe GitHub URL, zero network."""
from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request

from akatikito import memory
from akatikito.app_api import AppState, start_server
from akatikito.memory_store import MemoryStore


def test_f4_diagnostics_preview_scrubs_secrets_paths_emails_memory_and_builds_safe_issue_url(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    mem_dir = data_dir / "memory"
    mem_dir.mkdir(parents=True)
    monkeypatch.setattr("akatikito.settings.DEFAULT_PATH", data_dir / "settings.json")
    monkeypatch.setattr(memory, "DIR", mem_dir)

    # Plant secrets, OS user path, email address, and private memory content
    secret_key = "sk-planted-super-secret-key-987654321"
    planted_email = "owner.private@example-family.gr"
    planted_path = r"C:\Users\SecretUsername\Desktop\PrivateTax\doc.txt"
    planted_memory = "Μυστικό ΑΦΜ ιδιοκτήτη 098765432"

    (data_dir / "errors.log").write_text(
        f"Failed calling API with key {secret_key} for {planted_email} at {planted_path}\n",
        encoding="utf-8",
    )
    MemoryStore(mem_dir / "memory.db").add(planted_memory)

    state = AppState()
    state.settings.set_key("OpenAI", secret_key)
    state.settings.save(data_dir / "settings.json")
    state.error = f"Crash reading {planted_path} for {planted_email} ({secret_key})"

    url, server = start_server(state)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    try:
        req = urllib.request.Request(
            url + "/api/diagnostics/preview",
            headers={"X-Akatikito-Token": server.auth_token},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        assert payload["auto_upload"] is False
        assert payload["files"], "Preview must list sanitized files"

        # Preview text must NEVER contain keys, OS usernames, emails, or memory contents
        all_preview = json.dumps(payload["files"], ensure_ascii=False)
        assert secret_key not in all_preview
        assert "SecretUsername" not in all_preview
        assert planted_email not in all_preview
        assert planted_memory not in all_preview

        # GitHub issue URL must contain ONLY version and OS — zero personal/log data
        issue_url = payload.get("issue_url", "")
        assert issue_url.startswith("https://github.com/rodos4312/akatikito-ai/issues/new?")
        decoded_url = urllib.parse.unquote(issue_url)
        assert secret_key not in decoded_url
        assert "SecretUsername" not in decoded_url
        assert planted_email not in decoded_url
        assert planted_memory not in decoded_url
        assert "v" in decoded_url
    finally:
        server.shutdown()
        server.server_close()
        state.close()
