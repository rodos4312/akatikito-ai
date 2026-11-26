"""Tests for parallel subtasks (run_subtasks), FileClaims, thread-safety, 80% warning & stall detection."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

from akatikito.agent import Akatikito
from akatikito.config import Config


class SleepFakeModel:
    """Thread-safe fake model that sleeps on each call and returns a deterministic reply."""

    def __init__(self, delay: float = 0.0, prompt_toks: int = 100, comp_toks: int = 50, responder=None):
        self.delay = delay
        self.prompt_toks = prompt_toks
        self.comp_toks = comp_toks
        self.responder = responder
        self.lock = threading.Lock()
        self.calls: list[dict] = []
        self.model = "fake-parallel"

    def chat(self, messages, tools=None, cache_key=""):
        if self.delay > 0:
            time.sleep(self.delay)
        user_txt = ""
        for m in messages:
            if m.get("role") == "user":
                txt = str(m.get("content", ""))
                if not txt.startswith("[ΚΑΤΑΣΤΑΣΗ ΕΦΑΡΜΟΓΗΣ:"):
                    user_txt = txt
                    break
        with self.lock:
            self.calls.append({"user": user_txt, "at": time.monotonic(), "tools": [t["function"]["name"] for t in (tools or [])]})
            idx = len(self.calls)
        if self.responder is not None:
            raw = self.responder(messages, idx, user_txt)
            usage = raw.pop("usage", {"prompt_tokens": self.prompt_toks, "completion_tokens": self.comp_toks})
            return raw, usage
        return (
            {"content": f"DONE:{user_txt}", "tool_calls": []},
            {"prompt_tokens": self.prompt_toks, "completion_tokens": self.comp_toks},
        )


def _make_agent(tmp_path, model, **cfg_kw):
    cfg = Config(model="fake-parallel", base_url="http://127.0.0.1:9/v1", api_key="k", auto_mode=False, checkpoints=False, **cfg_kw)
    ag = Akatikito(str(tmp_path), cfg, model)
    ag.set_mode("code")
    ag._turn_tools = set(ag.allowed_tools())
    return ag


def test_two_subtasks_different_files_run_in_parallel(tmp_path):
    barrier = threading.Barrier(2, timeout=3.0)
    both_entered: list[str] = []

    def responder(messages, idx, user_txt):
        barrier.wait()  # Only succeeds if both subtasks run concurrently; serial execution deadlocks/times out
        both_entered.append(user_txt)
        return {"content": f"DONE:{user_txt}", "tool_calls": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    ag = _make_agent(tmp_path, SleepFakeModel(responder=responder), max_parallel_subtasks=2)
    raw = ag._run_subtasks([
        {"task": "Task A", "mode": "code", "files": ["a.py"]},
        {"task": "Task B", "mode": "code", "files": ["b.py"]},
    ])
    rows = json.loads(raw)
    assert len(rows) == 2
    assert len(both_entered) == 2
    assert "DONE:Task A" in rows[0]["summary"]
    assert "DONE:Task B" in rows[1]["summary"]


def test_shared_file_serializes_second_subtask(tmp_path):
    events_log: list[tuple[str, str, float]] = []
    log_lock = threading.Lock()

    def responder(messages, idx, user_txt):
        tag = "A" if "Task A" in user_txt else "B"
        with log_lock:
            events_log.append((tag, "start", time.monotonic()))
        time.sleep(0.25)
        with log_lock:
            events_log.append((tag, "end", time.monotonic()))
        return {"content": f"OK:{tag}", "tool_calls": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    ag = _make_agent(tmp_path, SleepFakeModel(responder=responder), max_parallel_subtasks=2)
    raw = ag._run_subtasks([
        {"task": "Task A", "mode": "code", "files": ["shared.py"]},
        {"task": "Task B", "mode": "code", "files": ["shared.py"]},
    ])
    rows = json.loads(raw)
    assert len(rows) == 2
    # The first task must END before the second task STARTS
    assert events_log[1][1] == "end" and events_log[2][1] == "start"
    assert events_log[1][0] != events_log[2][0]


def test_undeclared_file_write_is_rejected(tmp_path):
    step_map: dict[str, int] = {}

    def responder(messages, idx, user_txt):
        if not any(m.get("role") == "tool" for m in messages):
            return {
                "content": "",
                "tool_calls": [{
                    "id": f"tc_{idx}",
                    "name": "write_file",
                    "arguments": json.dumps({"path": "secret.py", "content": "x = 1\n"}),
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            }
        tool_msg = [m for m in messages if m.get("role") == "tool"][-1]["content"]
        return {"content": f"SAW:{tool_msg}", "tool_calls": [], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}

    ag = _make_agent(tmp_path, SleepFakeModel(responder=responder), max_parallel_subtasks=2)
    raw = ag._run_subtasks([
        {"task": "Try undeclared write", "mode": "code", "files": ["allowed.py"]},
    ])
    rows = json.loads(raw)
    assert not (tmp_path / "secret.py").exists()
    assert "ΑΠΟΡΡΙΨΗ" in rows[0]["summary"] and "secret.py" in rows[0]["summary"]


def test_parent_stop_cancels_all_active_children(tmp_path):
    started = threading.Event()
    count_lock = threading.Lock()
    running = [0]

    def responder(messages, idx, user_txt):
        with count_lock:
            running[0] += 1
            if running[0] >= 2:
                started.set()
        time.sleep(0.15)
        return {
            "content": "",
            "tool_calls": [{"id": f"tc_{idx}", "name": "list_files", "arguments": "{}"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    ag = _make_agent(tmp_path, SleepFakeModel(responder=responder), max_parallel_subtasks=2)

    def stopper():
        started.wait(timeout=2.0)
        ag.stop()

    t = threading.Thread(target=stopper, daemon=True)
    t.start()
    raw = ag._run_subtasks([
        {"task": "Loop 1", "mode": "ask"},
        {"task": "Loop 2", "mode": "ask"},
    ])
    t.join(timeout=2.0)
    rows = json.loads(raw)
    assert len(rows) == 2
    assert ag._stop is True
    assert len(ag._active_children) == 0


def test_token_aggregation_thread_safety_50_runs(tmp_path):
    model = SleepFakeModel(delay=0.0, prompt_toks=10, comp_toks=5)
    ag = _make_agent(tmp_path, model, max_parallel_subtasks=2, subtask_limit=4, subtask_total_token_limit=500000)
    for _ in range(50):
        ag.subtasks = []
        ag._subtask_tokens_total = 0
        ag._run_subtasks([
            {"task": "T1", "mode": "ask"},
            {"task": "T2", "mode": "ask"},
        ])
    # 50 runs * 2 subtasks * (10 prompt + 5 completion)
    assert ag.tokens["prompt_tokens"] == 50 * 2 * 10
    assert ag.tokens["completion_tokens"] == 50 * 2 * 5


def test_total_token_limit_stops_subtasks(tmp_path):
    def responder(messages, idx, user_txt):
        return {
            "content": "",
            "tool_calls": [{"id": f"tc_{idx}", "name": "list_files", "arguments": "{}"}],
            "usage": {"prompt_tokens": 60, "completion_tokens": 40},
        }

    ag = _make_agent(
        tmp_path,
        SleepFakeModel(responder=responder),
        max_parallel_subtasks=2,
        subtask_token_limit=500,
        subtask_total_token_limit=150,
    )
    raw = ag._run_subtasks([
        {"task": "Heavy 1", "mode": "ask", "max_steps": 6},
        {"task": "Heavy 2", "mode": "ask", "max_steps": 6},
    ])
    rows = json.loads(raw)
    assert any("Όριο tokens" in r.get("summary", "") for r in rows)


def test_idle_subtask_stops_after_3_steps_without_progress(tmp_path):
    calls_seen = [0]

    def responder(messages, idx, user_txt):
        calls_seen[0] += 1
        # list_files does NOT increment _task_reads / _task_writes / _task_checks -> idle stall!
        return {
            "content": "",
            "tool_calls": [{"id": f"tc_{idx}", "name": "list_files", "arguments": "{}"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    ag = _make_agent(tmp_path, SleepFakeModel(responder=responder), max_parallel_subtasks=2)
    raw = ag._run_subtasks([{"task": "Stall forever", "mode": "ask", "max_steps": 10}])
    rows = json.loads(raw)
    assert "δεν προχωρούσε" in rows[0]["summary"]
    assert calls_seen[0] <= 4


def test_child_cannot_call_run_subtasks_or_propose_memory(tmp_path):
    def responder(messages, idx, user_txt):
        if not any(m.get("role") == "tool" for m in messages):
            return {
                "content": "",
                "tool_calls": [
                    {"id": f"tc_a_{idx}", "name": "run_subtasks", "arguments": json.dumps({"tasks": [{"task": "Nested", "mode": "ask"}]})},
                    {"id": f"tc_b_{idx}", "name": "propose_memory", "arguments": json.dumps({"topic": "X", "text": "Y", "kind": "fact", "status": "confirmed"})},
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        tool_msgs = [m["content"] for m in messages if m.get("role") == "tool"]
        return {"content": " | ".join(tool_msgs), "tool_calls": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    ag = _make_agent(tmp_path, SleepFakeModel(responder=responder), max_parallel_subtasks=2)
    raw = ag._run_subtasks([{"task": "Try forbidden tools", "mode": "code", "files": ["a.py"]}])
    rows = json.loads(raw)
    summary = rows[0]["summary"]
    assert "δεν επιτρέπεται" in summary
    # Verify neither run_subtasks nor propose_memory was advertised in child tool schemas
    assert all("run_subtasks" not in c["tools"] and "propose_memory" not in c["tools"] for c in ag.model.calls)


def test_budget_warning_injected_at_80_percent(tmp_path):
    seen_warning = [False]
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")

    def responder(messages, idx, user_txt):
        sys_txt = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
        if "ΠΛΗΣΙΑΖΕΙΣ ΤΟ ΟΡΙΟ" in sys_txt:
            seen_warning[0] = True
            return {"content": "WRAPPED_UP", "tool_calls": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        # First call consumes 85 of 100 token budget (85% >= 80%) and reads a.txt so it's not stalled
        return {
            "content": "",
            "tool_calls": [{"id": f"tc_{idx}", "name": "read_file", "arguments": json.dumps({"path": "a.txt"})}],
            "usage": {"prompt_tokens": 60, "completion_tokens": 25},
        }

    ag = _make_agent(
        tmp_path,
        SleepFakeModel(responder=responder),
        max_parallel_subtasks=2,
        subtask_token_limit=100,
        subtask_total_token_limit=500,
    )
    raw = ag._run_subtasks([{"task": "Near budget", "mode": "ask", "max_steps": 4}])
    rows = json.loads(raw)
    assert seen_warning[0] is True
    assert "WRAPPED_UP" in rows[0]["summary"]


def test_plain_run_subtask_preserves_parent_write_guard(tmp_path):
    guard_hits: list[str] = []

    def parent_guard(path, old_text="", new_text=""):
        guard_hits.append(str(path))
        raise PermissionError("ΑΠΟΡΡΙΨΗ ΓΟΝΕΑ: απαιτείται έγκριση.")

    def responder(messages, idx, user_txt):
        if not any(m.get("role") == "tool" for m in messages):
            return {
                "content": "",
                "tool_calls": [{
                    "id": f"tc_{idx}",
                    "name": "write_file",
                    "arguments": json.dumps({"path": "out.txt", "content": "hi"}),
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        tool_msg = [m["content"] for m in messages if m.get("role") == "tool"][-1]
        return {"content": f"SAW:{tool_msg}", "tool_calls": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    ag = _make_agent(tmp_path, SleepFakeModel(responder=responder))
    ag.ws.write_guard = parent_guard
    raw = ag._run_subtask("Write out.txt", "code")
    row = json.loads(raw)
    assert guard_hits, "Parent write_guard must be invoked for plain run_subtask"
    assert not (tmp_path / "out.txt").exists()
    assert "ΑΠΟΡΡΙΨΗ ΓΟΝΕΑ" in row["summary"]


def test_child_never_leaks_primary_key_to_routed_provider(tmp_path, monkeypatch):
    from akatikito import agent as agent_mod
    from akatikito.model import OpenAIModel

    seen_cfgs: list[Config] = []

    class DummyOpenAI(OpenAIModel):
        def __init__(self, cfg):
            seen_cfgs.append(cfg)
            self.model = cfg.model

        def chat(self, messages, tools=None, cache_key=""):
            return {"content": "OK", "tool_calls": []}, {"prompt_tokens": 5, "completion_tokens": 5}

    cfg = Config(
        model="primary-model",
        base_url="https://provider-a.example/v1",
        api_key="PRIMARY_SECRET_KEY",
        auto_mode=False,
        checkpoints=False,
        stream_responses=False,
    )
    ag = Akatikito(str(tmp_path), cfg, DummyOpenAI(cfg))
    ag.set_mode("code")
    ag._turn_tools = set(ag.allowed_tools())
    # Simulate smart routing having mutated config.base_url / model to Provider B
    ag.config.base_url = "https://provider-b.example/v1"
    ag.config.model = "smart-b-model"
    # Patch OpenAIModel check so child constructs a new client with child_cfg
    monkeypatch.setattr(agent_mod, "OpenAIModel", DummyOpenAI)
    seen_cfgs.clear()

    ag._run_subtask("Check key isolation", "ask")
    assert len(seen_cfgs) == 1
    assert seen_cfgs[0].base_url == "https://provider-a.example/v1"
    assert seen_cfgs[0].model == "primary-model"
    assert seen_cfgs[0].api_key == "PRIMARY_SECRET_KEY"


def test_tool_plugin_guard_output_write_respects_declared_files(tmp_path):
    from akatikito.subtasks import make_declared_write_guard
    from akatikito.tool_plugins import _guard_output_write
    from akatikito.workspace import Workspace

    ws = Workspace(str(tmp_path))
    ws.write_guard = make_declared_write_guard(ws, {"allowed.pdf"})
    # Allowed target passes
    _guard_output_write(ws, ws.resolve("allowed.pdf"), overwrite=True)
    # Undeclared target is rejected by write_guard
    try:
        _guard_output_write(ws, ws.resolve("sneaky.pdf"), overwrite=True)
        assert False, "Expected PermissionError for undeclared plugin output path"
    except PermissionError as exc:
        assert "sneaky.pdf" in str(exc)


def test_bad_path_in_one_task_does_not_abort_batch(tmp_path):
    ag = _make_agent(tmp_path, SleepFakeModel(delay=0.05), max_parallel_subtasks=2)
    raw = ag._run_subtasks([
        {"task": "Bad path", "mode": "code", "files": ["../../outside.py"]},
        {"task": "Good task", "mode": "code", "files": ["ok.py"]},
    ])
    rows = json.loads(raw)
    assert len(rows) == 2
    assert rows[0]["status"] == "failed"
    assert "DONE:Good task" in rows[1]["summary"]


def test_independent_third_task_runs_while_second_waits_for_shared_file(tmp_path):
    events_log: list[tuple[str, str]] = []
    lock = threading.Lock()

    def responder(messages, idx, user_txt):
        tag = "A" if "Task A" in user_txt else ("B" if "Task B" in user_txt else "C")
        with lock:
            events_log.append((tag, "start"))
        time.sleep(0.3 if tag == "A" else 0.05)
        with lock:
            events_log.append((tag, "end"))
        return {"content": f"OK:{tag}", "tool_calls": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    ag = _make_agent(tmp_path, SleepFakeModel(responder=responder), max_parallel_subtasks=2)
    raw = ag._run_subtasks([
        {"task": "Task A", "mode": "code", "files": ["shared.py"]},
        {"task": "Task B", "mode": "code", "files": ["shared.py"]},
        {"task": "Task C", "mode": "code", "files": ["independent.py"]},
    ])
    rows = json.loads(raw)
    assert len(rows) == 3
    # C must start BEFORE A finishes (not blocked behind B's conflict on shared.py)
    c_start = events_log.index(("C", "start"))
    a_end = events_log.index(("A", "end"))
    b_start = events_log.index(("B", "start"))
    assert c_start < a_end, f"Expected C to run in parallel with A, log={events_log}"
    assert a_end < b_start, f"Expected B to wait for A to finish, log={events_log}"


def test_stale_child_from_previous_turn_does_not_pollute_new_turn(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def responder(messages, idx, user_txt):
        entered.set()
        release.wait(timeout=3.0)
        return {"content": "LATE_DONE", "tool_calls": [], "usage": {"prompt_tokens": 77, "completion_tokens": 33}}

    ag = _make_agent(tmp_path, SleepFakeModel(responder=responder), max_parallel_subtasks=2, subtask_grace_seconds=0.05)

    def stop_and_advance_turn():
        entered.wait(timeout=2.0)
        ag.stop()
        time.sleep(0.12)  # past 0.05s grace period
        # Simulate user starting a brand-new turn before the slow child exits
        with ag._sub_lock:
            ag._turn_id += 1
        ag._task_writes.clear()
        ag.tokens["prompt_tokens"] = 0
        ag.tokens["completion_tokens"] = 0
        release.set()

    t = threading.Thread(target=stop_and_advance_turn, daemon=True)
    t.start()
    ag._run_subtasks([{"task": "Slow child", "mode": "ask"}])
    t.join(timeout=3.0)
    time.sleep(0.15)  # allow background child thread to reach finally:
    assert ag.tokens["prompt_tokens"] == 0
    assert ag.tokens["completion_tokens"] == 0
    assert len(ag._task_writes) == 0
