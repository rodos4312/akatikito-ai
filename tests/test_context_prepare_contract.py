"""Characterization tests for ContextWindow.prepare (Q4.8 safety net)."""
from __future__ import annotations

import json

from akatikito.context import ContextWindow
from akatikito.repair import RepairState
from akatikito.workspace import Workspace


def _tool_exchange(cid, name, args, result, written=None):
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}],
    }
    tool = {"role": "tool", "tool_call_id": cid, "content": result, "_written_paths": list(written or [])}
    return [assistant, tool]


def test_prepare_disabled_and_small_conversation_preserved(tmp_path):
    ws = Workspace(tmp_path)
    msgs = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "hello", "_origin": "owner"},
        {"role": "assistant", "content": "hi there"},
    ]
    cw_off = ContextWindow(ws, enabled=False)
    out_off = cw_off.prepare(msgs)
    assert len(out_off) == 3 and cw_off.metrics["reduction_percent"] == 0

    cw_on = ContextWindow(ws, enabled=True)
    out_on = cw_on.prepare(msgs)
    assert len(out_on) == 3 and out_on[-1]["content"] == "hi there"


def test_prepare_summarizes_stale_read_trims_command_and_enforces_budget(tmp_path):
    ws = Workspace(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    cw = ContextWindow(ws, max_chars=2400, recent_groups=2, enabled=True)
    msgs = [{"role": "system", "content": "policy"}, {"role": "user", "content": "fix a.py", "_origin": "owner"}]
    # Stale read followed by write to same file
    msgs.extend(_tool_exchange("c1", "read_file", {"path": "a.py"}, "old content " * 80))
    msgs.extend(_tool_exchange("c2", "write_file", {"path": "a.py", "content": "x = 2\n"}, "ΕΓΙΝΕ: a.py", written=["a.py"]))
    # Large successful run_command output
    msgs.extend(_tool_exchange("c3", "run_command", {"command": "pytest"}, "returncode=0\n" + ("PASS line\n" * 300)))
    # Extra old noisy tool exchanges to push over budget
    for idx in range(4, 10):
        msgs.extend(_tool_exchange(f"c{idx}", "search_project", {"query": f"q{idx}"}, ("match " * 120)))

    repair = RepairState(ws)
    repair.failed("AssertionError in test_a")

    class _Act:
        def source_state(self):
            return "[ΠΗΓΕΣ] a.py"

    view = cw.prepare(msgs, repair=repair, activity=_Act())
    joined = "\n".join(str(m.get("content") or "") for m in view)
    assert "recall_context id=" in joined
    assert "[ΕΝΕΡΓΗ ΚΑΤΑΣΤΑΣΗ ΔΙΟΡΘΩΣΗΣ" in joined
    assert "[ΤΡΕΧΟΥΣΑ ΚΑΤΑΣΤΑΣΗ ΕΡΓΑΣΙΑΣ" in joined
    assert "[ΠΗΓΕΣ] a.py" in joined
    assert cw.metrics["retrieval_available"] is True
    assert cw.metrics["sent_chars"] < cw.metrics["original_chars"]
