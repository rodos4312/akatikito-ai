"""24/7 headless background service for scheduled jobs and channels (FEATURES-PLAN L2).

Closed by default (`settings.background_mode = False`).
Safety invariants:
- Daily cost budget is ALWAYS enforced before any model call.
- Only owner-authorized scheduled tasks (cryptographic schedule_hash) can run.
- One-click `stop()` halts polling and tray cleanly.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .local_store import load_json, save_json
from .notify import toast as default_toast
from .scheduler import ScheduledQueue
from .settings import DEFAULT_PATH, Settings
from .usage_ledger import UsageLedger

DEFAULT_BACKGROUND_DAILY_BUDGET = 1.0
RUN_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_REG_VALUE = "AkatikitoBackground"


def default_autostart_command(python_exe: str | None = None, app_root: Path | None = None) -> str:
    """Build the headless startup command line for Windows HKCU Run."""
    exe = python_exe or sys.executable
    if exe.lower().endswith("python.exe"):
        pythonw = Path(exe).with_name("pythonw.exe")
        if pythonw.is_file():
            exe = str(pythonw)
    root = Path(app_root) if app_root else Path(__file__).resolve().parents[1]
    return f'"{exe}" -m akatikito.background_service --root "{root}"'


class _WinRegistryBackend:
    """Thin wrapper around winreg HKCU\\...\\Run so tests can inject an in-memory backend."""

    def read(self, name: str) -> str | None:
        if os.name != "nt":
            return None
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_PATH, 0, winreg.KEY_READ) as key:
                val, _ = winreg.QueryValueEx(key, name)
                return str(val)
        except OSError:
            return None

    def write(self, name: str, command: str) -> None:
        if os.name != "nt":
            raise RuntimeError("Η αυτόματη εκκίνηση Windows διατίθεται μόνο στα Windows.")
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_REG_PATH) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)

    def delete(self, name: str) -> None:
        if os.name != "nt":
            return
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except OSError:  # quality: allow S110 — value already absent in HKCU Run
            pass


def set_windows_autostart(enabled: bool, command: str | None = None, *, backend=None) -> bool:
    """Enable or disable Windows user-level Startup entry for the 24/7 service."""
    reg = backend or _WinRegistryBackend()
    if enabled:
        reg.write(RUN_REG_VALUE, command or default_autostart_command())
        return True
    reg.delete(RUN_REG_VALUE)
    return False


def is_windows_autostart_enabled(*, backend=None) -> bool:
    reg = backend or _WinRegistryBackend()
    return bool(reg.read(RUN_REG_VALUE))


def _default_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _default_spawn_service(cmd: list[str]) -> subprocess.Popen:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


class BackgroundServiceController:
    """Single-instance process controller for the 24/7 background service using a PID lock file."""

    def __init__(
        self,
        data_dir: Path,
        *,
        app_root: Path | None = None,
        spawner: Callable[[list[str]], object] | None = None,
        pid_alive: Callable[[int], bool] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.app_root = Path(app_root) if app_root else Path(__file__).resolve().parents[1]
        self.pid_path = self.data_dir / "background_service.pid"
        self._spawner = spawner or _default_spawn_service
        self._pid_alive = pid_alive or _default_pid_alive
        self._proc = None
        self._last_error: str = ""

    def _read_pid(self) -> int | None:
        raw = load_json(self.pid_path, dict)
        pid = raw.get("pid") if isinstance(raw, dict) else None
        return int(pid) if isinstance(pid, int) and pid > 0 else None

    def current_pid(self) -> int | None:
        if self._proc is not None and self._proc.poll() is None:
            return int(getattr(self._proc, "pid", 0) or 0) or None
        pid = self._read_pid()
        if pid and self._pid_alive(pid):
            return pid
        return None

    def is_running(self) -> bool:
        return self.current_pid() is not None

    def start(self) -> dict:
        existing = self.current_pid()
        if existing is not None:
            return {"running": True, "already_running": True, "pid": existing}
        cmd = [sys.executable, "-m", "akatikito.background_service", "--root", str(self.app_root)]
        proc = self._spawner(cmd)
        self._proc = proc
        pid = int(getattr(proc, "pid", 0) or 0)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        save_json(self.pid_path, {"pid": pid, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return {"running": True, "already_running": False, "pid": pid}

    def stop(self) -> dict:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired) as exc:
                self._last_error = str(exc)
        self._proc = None
        try:
            self.pid_path.unlink(missing_ok=True)
        except OSError as exc:
            self._last_error = str(exc)
        return {"running": False, "pid": None}

    def status(self, *, enabled: bool = False, autostart: bool = False, daily_budget: float = 0.0) -> dict:
        pid = self.current_pid()
        return {
            "enabled": bool(enabled),
            "autostart": bool(autostart),
            "running": pid is not None,
            "pid": pid,
            "daily_budget": float(daily_budget or 0.0),
        }


def _prices_for_settings(cfg: Settings) -> dict | None:
    prices = cfg.model_prices.get(cfg.model) if isinstance(cfg.model_prices, dict) else None
    if isinstance(prices, dict):
        return prices
    return cfg.model_prices.get(f"{cfg.provider}:{cfg.model}") if isinstance(cfg.model_prices, dict) else None


class BackgroundService:
    """Headless 24/7 runner for owner-authorized scheduled jobs with mandatory budget cap."""

    def __init__(
        self,
        *,
        data_dir: Path | None = None,
        settings: Settings | None = None,
        runner: Callable[[dict, Settings], dict] | None = None,
        notify: Callable[[str, str], object] | None = None,
        poll_interval: float = 30.0,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_PATH.parent
        self.settings = settings or Settings.load(self.data_dir / "settings.json")
        self.queue = ScheduledQueue(self.data_dir / "scheduled.json")
        self.ledger = UsageLedger(self.data_dir / "usage.json")
        self.runner = runner
        self.notify = notify or default_toast
        self.poll_interval = max(0.05, float(poll_interval))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_event: str = ""
        self._runs: int = 0
        self._budget_halted: bool = False

    def effective_budget(self) -> float:
        configured = float(getattr(self.settings, "daily_budget", 0.0) or 0.0)
        return configured if configured > 0 else DEFAULT_BACKGROUND_DAILY_BUDGET

    def check_budget(self, session_id: str) -> str:
        """Return empty string if within daily budget, else a Greek refusal reason."""
        prices = _prices_for_settings(self.settings)
        return self.ledger.check(self.effective_budget(), session_id, prices)

    def tick_once(self, *, moment: datetime | None = None) -> dict:
        """Collect due owner-authorized tasks and run at most one job if within daily budget."""
        if not self.settings.background_mode:
            return {"ran": False, "reason": "disabled"}
        roots = list(self.queue.load().get("projects") or [])
        if self.settings.project and self.settings.project not in roots:
            roots.append(self.settings.project)
        errors: list[str] = []
        for root in roots:
            try:
                self.queue.collect(root, moment=moment)
            except (OSError, ValueError) as exc:
                errors.append(str(exc))

        budget_err = self.check_budget("background")
        if budget_err:
            self._budget_halted = True
            self._last_event = budget_err
            self._safe_notify("Akatikito — Όριο κόστους", budget_err)
            return {"ran": False, "reason": "budget", "error": budget_err, "collect_errors": errors}

        job = self.queue.claim()
        if not job:
            return {"ran": False, "reason": "idle", "collect_errors": errors}
        return self._execute_job(job, errors)

    def _execute_job(self, job: dict, collect_errors: list[str]) -> dict:
        try:
            outcome = self._run_isolated(job)
            status = "completed" if outcome.get("ok", True) else "failed"
            answer = str(outcome.get("answer") or "")
            error = str(outcome.get("error") or "")
            self.queue.finish(job["id"], status, answer=answer, error=error)
            self._runs += 1
            self._budget_halted = False
            self._last_event = f"{job['title']}: {status}"
            summary = answer[:180] if status == "completed" else (error or "Αποτυχία εργασίας.")
            self._safe_notify(f"Akatikito — {job['title']}", summary)
            return {"ran": True, "job_id": job["id"], "status": status, "answer": answer, "collect_errors": collect_errors}
        except Exception as exc:  # quality: allow BLE001 — isolate background job failure so daemon stays alive
            err = f"{type(exc).__name__}: {exc}"
            self.queue.finish(job["id"], "failed", error=err)
            self._last_event = err
            self._safe_notify(f"Akatikito — Σφάλμα ({job['title']})", err[:180])
            return {"ran": True, "job_id": job["id"], "status": "failed", "error": err, "collect_errors": collect_errors}

    def _run_isolated(self, job: dict) -> dict:
        if self.runner is not None:
            return self.runner(job, self.settings)
        from .agent import Akatikito
        from .config import Config
        from .model import PROVIDERS

        cfg = Config(
            model=self.settings.model,
            base_url=self.settings.base_url or PROVIDERS.get(self.settings.provider, ""),
            api_key=self.settings.key_for(self.settings.provider),
            max_steps=self.settings.max_steps,
            checkpoints=self.settings.checkpoints,
        )
        agent = Akatikito(root=job["project"], config=cfg)
        agent.set_mode(job.get("mode", "ask"))
        answer = agent.chat(job["prompt"])
        return {"ok": True, "answer": answer}

    def _safe_notify(self, title: str, message: str) -> None:
        try:
            self.notify(title, message)
        except (OSError, RuntimeError, ValueError) as exc:
            self._last_event = f"notify-error: {exc}"

    def start(self) -> bool:
        """Start the headless background loop; refuses when `background_mode` is False."""
        if not self.settings.background_mode:
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()

        def _loop() -> None:
            while not self._stop.is_set():
                self.tick_once()
                self._stop.wait(self.poll_interval)

        self._thread = threading.Thread(target=_loop, daemon=True, name="akatikito-bg-24x7")
        self._thread.start()
        return True

    def stop(self, timeout: float = 2.0) -> None:
        """One-click immediate shutdown of the background service."""
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def status(self) -> dict:
        return {
            "enabled": bool(self.settings.background_mode),
            "autostart": bool(self.settings.background_autostart),
            "running": self.running(),
            "daily_budget": self.effective_budget(),
            "budget_halted": self._budget_halted,
            "runs": self._runs,
            "last_event": self._last_event,
        }


def main(argv: list[str] | None = None) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Akatikito 24/7 headless background service")
    parser.add_argument("--root", default="", help="Workspace/app root")
    parser.add_argument("--once", action="store_true", help="Run one tick and exit")
    args = parser.parse_args(argv)
    svc = BackgroundService()
    if args.once:
        res = svc.tick_once()
        print(res)
        return 0
    if not svc.start():
        print("Η λειτουργία παρασκηνίου (background_mode) είναι απενεργοποιημένη.")
        return 0
    try:
        while svc.running():
            time.sleep(1.0)
    except KeyboardInterrupt:
        svc.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
