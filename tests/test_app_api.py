"""Δοκιμή του app API με scripted μοντέλο (χωρίς δίκτυο)."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from akatikito.agent import Akatikito  # noqa: E402
from akatikito.app_api import AppState, start_server  # noqa: E402
from akatikito.config import Config  # noqa: E402
from akatikito.model import ScriptedModel, tool_call  # noqa: E402
from akatikito.sessions import SessionStore  # noqa: E402


def main() -> int:
    import akatikito.app_api as app_api
    app_api.APP_ROOT = Path(tempfile.mkdtemp(prefix="akatikito-api-catalog-"))
    state = AppState()
    url, server = start_server(state)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def get(path: str) -> dict:
        with urllib.request.urlopen(urllib.request.Request(url + path,headers={"X-Akatikito-Token":server.auth_token})) as r:
            return json.loads(r.read())

    def post(path: str, body: dict) -> dict:
        req = urllib.request.Request(
            url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-Akatikito-Token":server.auth_token}
        )
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    # 1. status / providers / sessions χωρίς σύνδεση
    assert get("/api/status")["connected"] is False
    assert len(get("/api/providers")["providers"]) == 8
    assert get("/api/sessions") == {"sessions": []}
    assert post("/api/send", {"text": "γεια"})["error"], "πρέπει να αρνείται χωρίς σύνδεση"
    print("  OK status/providers/sessions/send-χωρίς-σύνδεση")

    # 2. πλήρης βρόχος με scripted μοντέλο
    root = Path(tempfile.mkdtemp(prefix="akatikito-app-"))
    (root / "hello.py").write_text("x = 1\n", encoding="utf-8")
    cfg = Config(model="test", base_url="http://x", api_key="k")
    agent = Akatikito(root=str(root), config=cfg)
    agent.model = ScriptedModel(
        [
            {"tool_calls": [tool_call("read_file", path="hello.py")], "content": ""},
            {"content": "Τέλος: διάβασα το hello.py."},
        ]
    )
    agent.events.subscribe(state.emit_event)
    state.agent = agent
    state.store = SessionStore(str(root))

    assert post("/api/send", {"text": "διάβασε το hello.py"}) == {"ok": True}
    for _ in range(50):
        time.sleep(0.1)
        if not state.busy:
            break
    poll = post("/api/poll", {"since": 0})
    assert poll["busy"] is False, "πρέπει να τελείωσε"
    kinds = [e["kind"] for e in poll["events"]]
    assert "tool_call" in kinds and "tool_result" in kinds, kinds
    texts = [m["text"] for m in poll["transcript"]]
    assert any("hello.py" in t for t in texts), texts
    print("  OK πλήρης βρόχος: send -> events -> answer")

    # 3. η συνεδρία αποθηκεύτηκε
    sessions = get("/api/sessions")["sessions"]
    assert len(sessions) == 1, sessions
    print("  OK συνεδρία αποθηκεύτηκε:", sessions[0]["title"])

    # 4. νέα συνεδρία + ξαναάνοιγμα
    st = post("/api/session/new", {})
    assert st["transcript"] == []
    st = post("/api/session/open", {"id": sessions[0]["id"]})
    assert any("hello.py" in m.get("text", "") for m in st["transcript"]), st["transcript"]
    print("  OK νέα + ξαναάνοιγμα συνεδρίας")

    # 5. mode / model / backups / restore
    assert post("/api/mode", {"mode": "debug"})["ok"] is True
    assert post("/api/model", {"model": "other"})["ok"] is True
    backups = get("/api/backups")["backups"]
    print("  ✔ mode/model/backups (", len(backups), "backups )")

    # 6. stop
    assert post("/api/stop", {})["ok"] is True
    print("  ✔ stop")

    # 7. UI σερβίρεται
    with urllib.request.urlopen(url + "/") as r:
        assert b"Akatikito" in r.read()
    print("  ✔ UI σερβίρεται")

    server.shutdown()
    print("\nΟΛΑ ΟΚ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())