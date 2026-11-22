"""Durable owner-authorized scheduled jobs, isolated sessions, and crash-safe uncertain recovery."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

from .local_store import checked, load_json, save_json
from .process_lock import FileLock


def alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def load_agenda(path):
    from .agenda import Agenda, Task

    data = load_json(path, lambda: {"tasks": []})
    rows = data.get("tasks", []) if isinstance(data, dict) else data
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("Μη έγκυρη ατζέντα. Δεν αντικαταστάθηκε.")
    try:
        for row in rows:
            Task.from_dict(row)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Η ατζέντα έχει μη έγκυρη εργασία. Διατηρήθηκε το αρχικό αρχείο.") from exc
    return Agenda(path)


def schedule_hash(task) -> str:
    payload = {k: getattr(task, k) for k in ("id", "title", "note", "date", "time", "repeat", "auto_mode")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class ScheduledQueue:
    """Owner-authorized scheduled job queue stored outside the project workspace."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = FileLock(self.path.with_suffix(".lock"))

    def load(self) -> dict:
        data = load_json(self.path, lambda: {"projects": [], "jobs": [], "authorized": {}})
        if not isinstance(data, dict) or not isinstance(data.get("projects"), list) or not isinstance(data.get("jobs"), list):
            raise ValueError("Κατεστραμμένη ουρά εργασιών. Δεν αντικαταστάθηκε.")
        if not all(isinstance(p, str) and Path(p).is_absolute() for p in data["projects"]) or len(data["projects"]) > 20 or len(data["jobs"]) > 1000:
            raise ValueError("Μη έγκυρη ουρά εργασιών.")
        required = {"id", "key", "project", "task", "title", "prompt", "mode", "status"}
        for j in data["jobs"]:
            if (
                not isinstance(j, dict)
                or not required <= j.keys()
                or not all(isinstance(j[k], str) for k in required)
                or j["status"] not in ("queued", "running", "uncertain", "failed", "completed", "cancelled")
                or j["mode"] not in ("ask", "assistant", "code", "debug")
                or not Path(j["project"]).is_absolute()
            ):
                raise ValueError("Μη έγκυρη εγγραφή ουράς. Δεν αντικαταστάθηκε.")
        authorized = data.setdefault("authorized", {})
        if not isinstance(authorized, dict) or not all(
            isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("expected"), str)
            for k, v in authorized.items()
        ):
            raise ValueError("Μη έγκυρες εγκρίσεις προγραμματισμού.")
        return data

    def register(self, root) -> None:
        with self.lock:
            data = self.load()
            root_str = str(Path(root).resolve())
            if root_str not in data["projects"]:
                if len(data["projects"]) >= 20:
                    raise ValueError("Όριο 20 projects με προγραμματισμό.")
                data["projects"].append(root_str)
                save_json(self.path, data)

    def authorize_task(self, root, task) -> None:
        root_str = str(Path(root).resolve())
        with self.lock:
            data = self.load()
            if root_str not in data["projects"]:
                if len(data["projects"]) >= 20:
                    raise ValueError("Όριο 20 projects.")
                data["projects"].append(root_str)
            data["authorized"][root_str + "|" + task.id] = {"expected": schedule_hash(task)}
            save_json(self.path, data)

    def collect(self, root, moment=None) -> None:
        from .agenda import now
        from .features import AGENDA_LOCK

        root_path = Path(root).resolve()
        path = checked(root_path / ".agent" / "agenda.json")
        if not path.exists():
            return
        with AGENDA_LOCK, self.lock:
            agenda = load_agenda(path)
            data = self.load()
            changed = False
            for task in agenda.due(moment):
                if not task.auto_run:
                    continue
                authorization = data["authorized"].get(str(root_path) + "|" + task.id, {})
                current = schedule_hash(task)
                key = str(root_path) + "|" + task.id + "|" + task.when().isoformat()
                existing = any(j["key"] == key for j in data["jobs"])
                if current != authorization.get("expected") and not (existing and current == authorization.get("previous")):
                    raise ValueError("Η αυτόματη εργασία δεν έχει τρέχουσα έγκριση ιδιοκτήτη: " + task.title)
                if not existing:
                    if len(data["jobs"]) >= 1000:
                        raise ValueError("Η ουρά έχει 1000 εγγραφές. Χρειάζεται διαχείριση πριν προστεθούν άλλες.")
                    data["jobs"].append(
                        {
                            "id": uuid.uuid4().hex,
                            "key": key,
                            "project": str(root_path),
                            "task": task.id,
                            "title": task.title,
                            "prompt": task.note.strip() or task.title,
                            "mode": task.auto_mode,
                            "status": "queued",
                            "created": time.time(),
                        }
                    )
                    # Commit queue entry before marking task notified so crash/busy cannot lose it.
                    save_json(self.path, data)
                agenda._mark_notified(task, moment or now())
                authorization.update(previous=current, expected=schedule_hash(task))
                save_json(self.path, data)
                changed = True
            if changed:
                agenda.save()

    def claim(self) -> dict | None:
        with self.lock:
            data = self.load()
            changed = False
            for job in data["jobs"]:
                if job["status"] == "running" and not alive(job.get("pid", 0)):
                    job.update(
                        status="uncertain",
                        error="Η προηγούμενη εκτέλεση διακόπηκε. Έλεγξε το ιστορικό πριν ζητήσεις νέα εκτέλεση.",
                    )
                    changed = True
            job = next((j for j in data["jobs"] if j["status"] == "queued"), None)
            if job:
                job.update(status="running", pid=os.getpid(), session=uuid.uuid4().hex[:12])
                changed = True
            if changed:
                save_json(self.path, data)
            return copy.deepcopy(job)

    def finish(self, job_id: str, status: str, **fields) -> None:
        with self.lock:
            data = self.load()
            job = next(j for j in data["jobs"] if j["id"] == job_id)
            if job["status"] != "running":
                raise ValueError("Η εργασία δεν είναι ενεργή.")
            job.update(status=status, finished=time.time(), **fields)
            save_json(self.path, data)

    def cancel_task(self, root, task_id: str) -> None:
        root_str = str(Path(root).resolve())
        with self.lock:
            data = self.load()
            data["authorized"].pop(root_str + "|" + task_id, None)
            for j in data["jobs"]:
                if j["project"] == root_str and j["task"] == task_id and j["status"] == "queued":
                    j["status"] = "cancelled"
            save_json(self.path, data)

    def action(self, job_id: str, action_name: str) -> None:
        with self.lock:
            data = self.load()
            job = next((j for j in data["jobs"] if j["id"] == job_id), None)
            if not job:
                raise ValueError("Δεν βρέθηκε η εργασία.")
            if action_name == "cancel" and job["status"] == "queued":
                job["status"] = "cancelled"
            elif action_name == "retry" and job["status"] in ("uncertain", "failed"):
                if len(data["jobs"]) >= 1000:
                    raise ValueError("Όριο 1000 εκτελέσεων.")
                new_job = {k: v for k, v in job.items() if k in ("project", "task", "title", "prompt", "mode")}
                new_job.update(id=uuid.uuid4().hex, key="retry:" + uuid.uuid4().hex, status="queued", created=time.time())
                data["jobs"].append(new_job)
            else:
                raise ValueError("Η κατάσταση της εργασίας άλλαξε· ανανέωσε την προβολή.")
            save_json(self.path, data)

    def view(self) -> list[dict]:
        return self.load()["jobs"][-100:]


def queue_for(state) -> ScheduledQueue:
    if not hasattr(state, "_schedule_queue"):
        from .settings import DEFAULT_PATH

        state._schedule_queue = ScheduledQueue(DEFAULT_PATH.parent / "scheduled.json")
    return state._schedule_queue


def tick(state, force: bool = False) -> bool:
    """Collect due authorized tasks and start one isolated scheduled worker if idle. Returns True if a worker started."""
    if getattr(state, "_gateway_worker", False) or not state.agent or state.self_update or state.restart_required:
        return False
    if not force and time.monotonic() - getattr(state, "_schedule_tick_at", 0) < 30:
        return False
    state._schedule_tick_at = time.monotonic()
    queue = queue_for(state)
    try:
        roots = list(dict.fromkeys(queue.load()["projects"] + [str(state.agent.ws.root)]))
        for root in roots:
            try:
                queue.collect(root)
            except (OSError, ValueError) as exc:
                state.notice = str(exc)
        with state._lock:
            if state.busy or state.voice.busy():
                return False
            job = queue.claim()
            if not job:
                return False
            state.busy = True
            state.stop_flag = False

        def run():
            worker = None
            try:
                from .agent import Akatikito
                from .app_api import APP_ROOT, AppState
                from .sessions import SessionStore
                from .workspace import detect_verify

                worker = AppState()
                worker._gateway_worker = True
                worker.settings = copy.deepcopy(state.settings)
                worker.settings.project = job["project"]
                worker.approvals = state.approvals
                cfg = copy.deepcopy(state.agent.config)
                cfg.verify_command = detect_verify(job["project"])
                worker.agent = getattr(state, "_schedule_agent_factory", Akatikito)(root=job["project"], config=cfg)
                worker.agent.set_mode(job["mode"])
                worker.agent.events.subscribe(worker.emit_event)
                worker._bind_permissions(worker.agent)
                original = worker.agent.ws.write_guard

                def guard(path, *args, **kwargs):
                    try:
                        Path(path).resolve().relative_to(APP_ROOT.resolve())
                    except ValueError:
                        return original(path, *args, **kwargs) if original else None
                    raise PermissionError("Η ίδια η εφαρμογή αλλάζει μόνο από ελεγχόμενη αυτοαναβάθμιση.")

                worker.agent.ws.write_guard = guard
                worker.store = SessionStore(job["project"])
                worker.session_id = worker.store.create(
                    source={"platform": "scheduled", "job": job["id"]},
                    session_id=job["session"],
                )
                worker.agent.ws.session_source = {"platform": "scheduled", "job": job["id"]}
                state._scheduled_active = worker
                if state.stop_flag:
                    raise ValueError("Η προγραμματισμένη εργασία ακυρώθηκε πριν ξεκινήσει.")
                sent = worker.send(job["prompt"])
                if sent.get("error"):
                    raise ValueError(sent["error"])
                while worker.busy:
                    time.sleep(0.05)
                status = (
                    "failed"
                    if worker.error or worker.agent._stop or worker.agent.task.status == "blocked"
                    else "completed"
                )
                queue.finish(
                    job["id"],
                    status,
                    answer=(worker.transcript[-1].get("text", "") if worker.transcript else ""),
                    error=worker.error,
                )
            except Exception as exc:
                try:
                    queue.finish(job["id"], "failed", error=type(exc).__name__ + ": " + str(exc))
                except Exception:
                    pass
            finally:
                if worker:
                    worker.close()
                state._scheduled_active = None
                state.busy = False
                state.push("scheduled_changed")

        state._schedule_thread = threading.Thread(target=run, daemon=True)
        state._schedule_thread.start()
        return True
    except (OSError, ValueError) as exc:
        state.notice = str(exc)
        return False
