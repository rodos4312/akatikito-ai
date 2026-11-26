"""D3 Editor ACP stdio server tests: Advanced gate, open-file + selection context, read-only diff preview."""
from __future__ import annotations

import io
import json

from akatikito.acp_server import AcpServer
from akatikito.settings import Settings


def test_acp_server_enforces_advanced_gate_and_streams_context_and_diff_preview(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

    # Closed by default -> refused with -32003
    closed_srv = AcpServer(Settings(advanced_features=[]))
    denied = closed_srv.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert denied["error"]["code"] == -32003 and "Advanced" in denied["error"]["message"]

    # Open ACP -> initialize, session/new, session/prompt (with file+selection), diff/preview (read-only)
    forwarded: list[dict] = []

    def fake_sender(path: str, token: str, payload: dict) -> dict:
        forwarded.append({"path": path, "token": token, "payload": payload})
        return {"ok": True, "reply": "Άλλαξε το - σε +."}

    srv = AcpServer(Settings(advanced_features=["acp"], project=str(tmp_path)), api_token="tok-acp", sender=fake_sender)
    r_init = srv.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r_init["result"]["capabilities"]["diffPreview"] is True

    r_new = srv.handle_request({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"workspace": str(tmp_path)}})
    sid = r_new["result"]["sessionId"]

    stream_in = io.StringIO(
        "\n".join(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "session/prompt",
                        "params": {
                            "sessionId": sid,
                            "prompt": "Διόρθωσε το bug",
                            "activeFile": "calc.py",
                            "selection": "return a - b",
                        },
                    }
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "diff/preview",
                        "params": {
                            "sessionId": sid,
                            "path": "calc.py",
                            "proposedContent": "def add(a, b):\n    return a + b\n",
                        },
                    }
                ),
            ]
        )
        + "\n"
    )
    stream_out = io.StringIO()
    count = srv.serve_stream(stream_in, stream_out)
    assert count == 2
    lines = [json.loads(x) for x in stream_out.getvalue().splitlines() if x.strip()]
    assert lines[0]["result"]["response"]["reply"] == "Άλλαξε το - σε +."
    assert "calc.py" in forwarded[0]["payload"]["text"]
    assert "return a - b" in forwarded[0]["payload"]["text"]

    diff_res = lines[1]["result"]
    assert diff_res["applied"] is False and diff_res["changed"] is True
    assert "-    return a - b" in diff_res["diff"]
    assert "+    return a + b" in diff_res["diff"]
    # File on disk remains untouched until editor user applies the diff
    assert "return a - b" in (tmp_path / "calc.py").read_text(encoding="utf-8")
