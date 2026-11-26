"""D1 Advanced menu + D2 akatikito CLI tests: 3-door gate, piped stdin, --yes safety, doctor & usage."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from akatikito import advanced
from akatikito.cli import call_local_api, evaluate_terminal_approval, run_cli
from akatikito.settings import Settings
from akatikito.usage_ledger import UsageLedger


def test_d1_advanced_catalog_includes_all_developer_features_closed_by_default():
    cfg = Settings()
    keys = {row["key"] for row in advanced.catalog(cfg)}
    assert {"self_update", "subtasks", "git", "cli", "acp", "plugin_sdk"} <= keys
    assert all(row["enabled"] is False for row in advanced.catalog(cfg))
    assert all(row["warning"] for row in advanced.catalog(cfg))


def test_d2_cli_respects_advanced_gate_pipes_stdin_and_calls_local_api(tmp_path):
    spath = tmp_path / "settings.json"
    Settings(advanced_features=[]).save(spath)

    # Closed by default -> ask/code refused
    rc, res = run_cli(["--settings", str(spath), "τι κάνει αυτό;"])
    assert rc == 1 and "Advanced" in res["error"]

    # Doctor & usage work read-only anytime
    UsageLedger(tmp_path / "usage.json").record(
        "s1",
        {"prompt_tokens": 500, "completion_tokens": 100, "cache_hit_tokens": 250},
        {"input": 1.0, "cached": 0.2, "output": 2.0},
    )
    rc_u, usage = run_cli(["--settings", str(spath), "usage"])
    assert rc_u == 0 and usage["calls"] == 1

    # Enable `cli` Advanced feature and hit a loopback fake local API with piped stdin
    Settings(advanced_features=["cli"]).save(spath)
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            received.append({"path": self.path, "token": self.headers.get("X-Akatikito-Token"), "body": body})
            out = json.dumps({"ok": True, "reply": "Εξήγηση σφάλματος."}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        rc_ok, out = run_cli(
            ["--settings", str(spath), "--api-url", f"http://127.0.0.1:{port}", "--token", "tok-123", "τι σημαίνει αυτό;"],
            stdin_text="Traceback: ValueError: bad input",
        )
        assert rc_ok == 0 and out["ok"] is True
        assert received[0]["token"] == "tok-123"
        assert "Traceback: ValueError" in received[0]["body"]["text"]
    finally:
        server.shutdown()
        server.server_close()


def test_d2_cli_yes_never_approves_dangerous_commands_and_rejects_non_loopback():
    # Non-loopback API URL rejected
    with pytest.raises(ValueError, match="127.0.0.1"):
        call_local_api("https://evil.example", "tok", "/api/send", {})

    # --yes approves ordinary command but NEVER dangerous commands
    ok_norm, _ = evaluate_terminal_approval("pytest -q", auto_yes=True)
    assert ok_norm is True
    ok_danger, reason = evaluate_terminal_approval("rm -rf /", auto_yes=True)
    assert ok_danger is False and "--yes" in reason
