"""W7 real-entry-point subprocess stdio test for D3 ACP server + live loopback AppState HTTP API."""
from __future__ import annotations

import json
import subprocess
import sys
import threading

from akatikito.agent import Akatikito
from akatikito.app_api import AppState
from akatikito.app_api_server import start_server
from akatikito.config import Config
from akatikito.model import ScriptedModel
from akatikito.settings import Settings


def test_w7_acp_subprocess_refuses_when_closed_and_answers_prompt_over_stdio_when_open(tmp_path):
    spath = tmp_path / "settings.json"

    # 1. Closed by default -> `python -m akatikito.acp_server` refuses immediately with non-zero exit & clear message
    Settings(advanced_features=[], project=str(tmp_path)).save(spath)
    closed_proc = subprocess.run(
        [sys.executable, "-m", "akatikito.acp_server", "--settings", str(spath)],
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert closed_proc.returncode != 0
    assert "Advanced" in (closed_proc.stderr + closed_proc.stdout)

    # 2. Open Advanced `acp` -> launch live loopback AppState API + real stdio subprocess
    cfg = Settings(advanced_features=["acp"], project=str(tmp_path))
    cfg.save(spath)
    state = AppState()
    state.settings = cfg
    state.agent = Akatikito(
        str(tmp_path),
        Config(model="scripted-acp", api_key="k", checkpoints=False, stream_responses=False),
        model=ScriptedModel([{"content": "Απάντηση ACP από live server."}]),
    )
    url, server = start_server(state)
    srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
    srv_thread.start()
    try:
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"workspace": str(tmp_path)}},
        ]
        # Start persistent stdio subprocess to do initialize -> session/new -> session/prompt
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "akatikito.acp_server",
                "--settings",
                str(spath),
                "--api-url",
                url,
                "--token",
                server.auth_token,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        assert proc.stdin and proc.stdout
        proc.stdin.write(json.dumps(requests[0]) + "\n")
        proc.stdin.flush()
        r1 = json.loads(proc.stdout.readline())
        assert r1["result"]["serverInfo"]["name"] == "akatikito-acp"

        proc.stdin.write(json.dumps(requests[1]) + "\n")
        proc.stdin.flush()
        r2 = json.loads(proc.stdout.readline())
        sid = r2["result"]["sessionId"]

        prompt_req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {"sessionId": sid, "prompt": "Εξήγησε τη συνάρτηση", "activeFile": "app.py"},
        }
        proc.stdin.write(json.dumps(prompt_req) + "\n")
        proc.stdin.flush()
        r3 = json.loads(proc.stdout.readline())
        proc.stdin.close()
        proc.wait(timeout=10)
        assert r3["result"]["response"].get("ok") is True
    finally:
        server.shutdown()
        server.server_close()
        state.close()
