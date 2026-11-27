"""Managed background processes (dev servers, watchers) with owner approval & guaranteed cleanup.

Security & lifecycle guarantees:
- Enforces the exact same safety gates as run_command (blocks live app dir, self-update mode,
  '..', dangerous commands without explicit approval, and respects ws.command_guard / profiles).
- Bounded concurrency (max 3 running per workspace) and bounded redacted output ring buffer.
- Kills the entire process tree on stop, agent.stop(), agent.reset(), and interpreter exit (atexit).
"""
from __future__ import annotations

import atexit
from contextlib import suppress
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import redact
from .workspace import Workspace

MAX_RUNNING_PROCESSES = 3
MAX_OUTPUT_BUFFER_CHARS = 64_000
_LIVE_APP_ROOT = Path(__file__).resolve().parents[1]
_ALL_REGISTRIES_LOCK = threading.Lock()
_ALL_REGISTRIES: list[BackgroundProcessRegistry] = []


@dataclass
class _ManagedProcess:
    proc_id: str
    label: str
    command: str
    cwd: str
    proc: subprocess.Popen
    started_at: float
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _chunks: list[str] = field(default_factory=list)
    _total_chars: int = 0

    def append(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._chunks.append(text)
            self._total_chars += len(text)
            while self._total_chars > MAX_OUTPUT_BUFFER_CHARS and len(self._chunks) > 1:
                dropped = self._chunks.pop(0)
                self._total_chars -= len(dropped)

    def tail(self, limit: int = 4000) -> str:
        with self._lock:
            joined = "".join(self._chunks)
        capped = max(200, min(16_000, int(limit or 4000)))
        text = joined[-capped:] if len(joined) > capped else joined
        return redact.redact_all(text).strip()

    def status_str(self) -> str:
        rc = self.proc.poll()
        return "running" if rc is None else f"exited({rc})"


def _kill_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except (OSError, subprocess.SubprocessError):
            with suppress(OSError, subprocess.SubprocessError):
                proc.kill()
                proc.wait(timeout=2)


def _reader_loop(entry: _ManagedProcess) -> None:
    stream = entry.proc.stdout
    if stream is None:
        return
    try:
        read_fn = stream.read1 if hasattr(stream, "read1") else stream.readline
        while True:
            chunk = read_fn(4096)
            if not chunk:
                break
            entry.append(chunk.decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return


class BackgroundProcessRegistry:
    """Tracks workspace-scoped background processes with safe teardown."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, _ManagedProcess] = {}
        with _ALL_REGISTRIES_LOCK:
            _ALL_REGISTRIES.append(self)

    def running_count(self) -> int:
        with self._lock:
            return sum(1 for item in self._items.values() if item.proc.poll() is None)

    def start(self, command: str, cwd: Path, label: str = "") -> _ManagedProcess:
        with self._lock:
            if sum(1 for item in self._items.values() if item.proc.poll() is None) >= MAX_RUNNING_PROCESSES:
                raise RuntimeError(f"Όριο {MAX_RUNNING_PROCESSES} ενεργών background διεργασιών. Σταμάτησε πρώτα μία.")
        kwargs: dict[str, Any] = {
            "shell": True,
            "cwd": str(cwd),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(command, **kwargs)  # noqa: S602 — command_risk & owner command_guard enforced before start()
        pid_key = "bg-" + uuid.uuid4().hex[:8]
        entry = _ManagedProcess(
            proc_id=pid_key,
            label=(label or command)[:80],
            command=command,
            cwd=str(cwd),
            proc=proc,
            started_at=time.monotonic(),
        )
        t = threading.Thread(target=_reader_loop, args=(entry,), name=f"akatikito-{pid_key}", daemon=True)
        t.start()
        with self._lock:
            self._items[pid_key] = entry
        return entry

    def get(self, proc_id: str) -> _ManagedProcess | None:
        with self._lock:
            return self._items.get(str(proc_id or "").strip())

    def stop(self, proc_id: str) -> _ManagedProcess | None:
        entry = self.get(proc_id)
        if entry is None:
            return None
        _kill_process_tree(entry.proc)
        return entry

    def stop_all(self) -> int:
        with self._lock:
            entries = list(self._items.values())
        stopped = 0
        for entry in entries:
            if entry.proc.poll() is None:
                _kill_process_tree(entry.proc)
                stopped += 1
        return stopped


def _cleanup_all_registries() -> None:
    with _ALL_REGISTRIES_LOCK:
        regs = list(_ALL_REGISTRIES)
    for reg in regs:
        reg.stop_all()


atexit.register(_cleanup_all_registries)


def get_registry(ws: Workspace) -> BackgroundProcessRegistry:
    reg = getattr(ws, "_bg_processes", None)
    if reg is None:
        reg = BackgroundProcessRegistry()
        ws._bg_processes = reg
    return reg


def stop_all_background_processes(ws: Workspace) -> int:
    reg = getattr(ws, "_bg_processes", None)
    return reg.stop_all() if reg is not None else 0


def _authorize_bg_command(ws: Workspace, command: str) -> str | None:
    if getattr(ws, "update_validator", None) is not None:
        return "ΑΠΟΡΡΙΨΗ: στην αυτοαναβάθμιση δεν επιτρέπονται background διεργασίες."
    if ws.root == _LIVE_APP_ROOT or _LIVE_APP_ROOT in ws.root.parents:
        return "ΑΠΟΡΡΙΨΗ: για αλλαγές/ελέγχους της εφαρμογής χρησιμοποίησε Αυτοαναβάθμιση."
    if not command:
        return "ΣΦΑΛΜΑ: κενή εντολή."
    if ".." in command.replace("\\", "/").split():
        return "ΑΠΟΡΡΙΨΗ: μην προσπαθείς να βγεις έξω από το project με '..'."
    from .approvals import command_risk

    guard = getattr(ws, "command_guard", None)
    risk = command_risk(command, ws.root)
    if guard is not None:
        if not guard(command, risk):
            return "ΑΠΟΡΡΙΨΗ [OWNER_DENIED]: Ο χρήστης δεν ενέκρινε την εντολή ή σταμάτησε την εργασία."
    elif risk == "dangerous":
        return "ΑΠΟΡΡΙΨΗ [OWNER_DENIED]: Η επικίνδυνη εντολή χρειάζεται ρητή έγκριση στο παράθυρο."
    return None


def start_background_process(ws: Workspace, command: str, label: str = "", cwd: str = "") -> str:
    """Start an owner-approved long-running dev server/watcher in the workspace."""
    cmd = str(command or "").strip()
    denied = _authorize_bg_command(ws, cmd)
    if denied:
        return denied
    target_dir = ws.resolve(cwd) if str(cwd or "").strip() else ws.root
    if not target_dir.is_dir():
        return f"ΣΦΑΛΜΑ: ο φάκελος εργασίας '{cwd}' δεν υπάρχει."
    try:
        entry = get_registry(ws).start(cmd, target_dir, label=label)
    except (OSError, RuntimeError) as exc:
        return f"ΣΦΑΛΜΑ: δεν ξεκίνησε η background διεργασία ({exc})."
    # Brief non-blocking settle so instant crash output is visible immediately.
    with suppress(subprocess.TimeoutExpired):
        entry.proc.wait(timeout=0.15)
    out = entry.tail(2000) or "(καμία έξοδος ακόμα)"
    return f"ΕΓΙΝΕ: proc_id={entry.proc_id} · pid={entry.proc.pid} · status={entry.status_str()}\n{out}"


def read_background_process(ws: Workspace, proc_id: str, tail_chars: int = 4000) -> str:
    """Read live status and redacted output tail of a managed background process."""
    entry = get_registry(ws).get(proc_id)
    if entry is None:
        return f"ΣΦΑΛΜΑ: δεν βρέθηκε background διεργασία '{proc_id}'."
    elapsed = round(time.monotonic() - entry.started_at, 1)
    out = entry.tail(tail_chars) or "(καμία έξοδος ακόμα)"
    return f"proc_id={entry.proc_id} · status={entry.status_str()} · uptime={elapsed}s\n{out}"


def stop_background_process(ws: Workspace, proc_id: str) -> str:
    """Terminate a managed background process and its child tree."""
    entry = get_registry(ws).stop(proc_id)
    if entry is None:
        return f"ΣΦΑΛΜΑ: δεν βρέθηκε background διεργασία '{proc_id}'."
    out = entry.tail(1500)
    suffix = f"\nΤελευταία έξοδος:\n{out}" if out else ""
    return f"ΕΓΙΝΕ: σταμάτησε η {entry.proc_id} (status={entry.status_str()}).{suffix}"


BG_PROCESS_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "start_background_process",
            "description": (
                "Start a long-running local process (e.g. dev server) inside the project without blocking. "
                "Returns proc_id for read_background_process / stop_background_process. "
                "Automatically stopped on Stop/exit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run in background"},
                    "label": {"type": "string", "description": "Short human label"},
                    "cwd": {"type": "string", "description": "Optional workspace-relative working directory"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_background_process",
            "description": "Check status and latest stdout/stderr output of a running or exited background process.",
            "parameters": {
                "type": "object",
                "properties": {
                    "proc_id": {"type": "string"},
                    "tail_chars": {"type": "integer"},
                },
                "required": ["proc_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_background_process",
            "description": "Stop a background process and its child tree by proc_id.",
            "parameters": {
                "type": "object",
                "properties": {"proc_id": {"type": "string"}},
                "required": ["proc_id"],
            },
        },
    },
]

BG_PROCESS_HANDLERS = {
    "start_background_process": start_background_process,
    "read_background_process": read_background_process,
    "stop_background_process": stop_background_process,
}
