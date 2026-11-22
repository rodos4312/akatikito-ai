"""Δοκιμές: συνεδρίες (σώζω/φορτώνω) και μνήμη (project / global / user)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from akatikito import memory  # noqa: E402
from akatikito.agent import Akatikito  # noqa: E402
from akatikito.config import Config  # noqa: E402
from akatikito.model import ScriptedModel, tool_call  # noqa: E402
from akatikito.sessions import SessionStore  # noqa: E402
from akatikito.tools import dispatch  # noqa: E402
from akatikito.workspace import Workspace  # noqa: E402
from akatikito.profile import Profile

PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ✔ {name}")
    else:
        print(f"  ✘ {name} — {detail}")
        raise SystemExit(1)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="akatikito-mem-"))
    memory.DIR = root / "data" / "memory"  # απομονωμένη μνήμη για το τεστ
    ws = Workspace(root)

    # ---------- μνήμη ----------
    print("μνήμη:")
    check("project", "Αποθηκεύτηκε" in dispatch(ws, "remember", {"note": "τα tests θέλουν pytest"}), "")
    check(
        "global",
        "ΠΡΟΤΑΣΗ" in dispatch(ws, "remember", {"note": "το venv είναι 3.11", "scope": "global"}),
        "",
    )
    check(
        "user",
        "ΠΡΟΤΑΣΗ ΜΝΗΜΗΣ"
        in dispatch(ws, "remember", {"note": "θέλει απλή γλώσσα", "scope": "user"}),
        "",
    )

    profile=Profile()
    check("πρόταση δεν ενεργοποιείται χωρίς έγκριση", "θέλει απλή γλώσσα" not in profile.prompt())
    check("γενική πρόταση δεν ενεργοποιείται χωρίς έγκριση", "το venv είναι 3.11" not in memory.read())
    for proposal in profile.listing()["proposals"]:
        profile.answer(proposal["id"],True,expected=proposal["review_hash"])
    agent = Akatikito(
        root=str(root),
        config=Config(api_key="test", max_steps=4),
        model=ScriptedModel([{"content": "ok", "tool_calls": []}]),
    )
    prompt = agent.system_prompt()
    check("project μπαίνει στο prompt", "τα tests θέλουν pytest" in prompt)
    check("global μπαίνει στο prompt", "το venv είναι 3.11" in prompt)
    check("user μπαίνει στο prompt", "θέλει απλή γλώσσα" in prompt)
    check("project μνήμη στο δίσκο", (root / ".agent" / "MEMORY.md").is_file())
    check("global μνήμη στο δίσκο", (memory.DIR / "MEMORY.md").is_file())
    check("user μνήμη στο δίσκο", (memory.DIR / "profile.json").is_file())

    # ---------- συνεδρίες ----------
    print("συνεδρίες:")
    store = SessionStore(root)
    sid = store.create()
    check("δημιουργία", (root / ".agent" / "sessions" / f"{sid}.json").is_file())

    messages = [
        {"role": "system", "content": "κανόνες"},
        {"role": "user", "content": "βρες τα bugs"},
        {"role": "assistant", "content": "τα βρήκα"},
    ]
    store.save(
        sid,
        messages=messages,
        tokens={"prompt_tokens": 12, "completion_tokens": 7},
        transcript=[
            {"role": "user", "text": "βρες τα bugs"},
            {"role": "assistant", "text": "τα βρήκα"},
        ],
    )
    loaded = store.load(sid)
    check("φορτώνει μηνύματα", loaded["messages"] == messages, str(loaded["messages"]))
    check("φορτώνει tokens", loaded["tokens"]["prompt_tokens"] == 12)
    check("τίτλος από το πρώτο μήνυμα", loaded["title"] == "βρες τα bugs", loaded["title"])

    rows = store.list()
    check("εμφανίζεται στη λίστα", any(r["id"] == sid for r in rows))
    check("μετράει 3 μηνύματα", next(r for r in rows if r["id"] == sid)["messages"] == 3)

    # συνέχεια σε νέο agent (σαν άνοιγμα αύριο)
    agent2 = Akatikito(
        root=str(root),
        config=Config(api_key="test", max_steps=4),
        model=ScriptedModel([{"content": "ok", "tool_calls": []}]),
    )
    agent2.messages = list(store.load(sid)["messages"])
    check("συνεχίζει από εκεί που έμεινε", agent2.messages[-1]["content"] == "τα βρήκα")

    check("διαγραφή", store.delete(sid) and store.load(sid) is None)

    # ---------- ροή με εργαλείο ----------
    print("ροή με εργαλείο:")
    model = ScriptedModel(
        [
            {"content": "", "tool_calls": [tool_call("remember", note="νέα σημείωση", scope="global")]},
            {"content": "τέλος", "tool_calls": []},
        ]
    )
    agent3 = Akatikito(root=str(root), config=Config(api_key="test", max_steps=4), model=model)
    answer = agent3.chat("θυμήσου κάτι")
    check("απάντησε", answer == "τέλος", answer)
    check("το εργαλείο δεν αυτοεγκρίνει γενική μνήμη", "νέα σημείωση" not in memory.read("memory"))
    pending=profile.listing()['proposals']
    check("αποθηκεύτηκε πρόταση μέσω εργαλείου",len(pending)==1 and pending[0]['target']=='global')
    profile.answer(pending[0]['id'],True,expected=pending[0]['review_hash'])
    check("η εγκεκριμένη σημείωση ενεργοποιήθηκε", "νέα σημείωση" in memory.read("memory"))

    print(f"\n{len(PASSED)} έλεγχοι πέρασαν.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
