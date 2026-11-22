"""Bounded local helper processes. No shell, hidden windows, cancellation and output limits."""
from __future__ import annotations

import os
import subprocess
import threading
import time


def run_local(argv, cwd, env=None, timeout=15, stopped=lambda: False, limit=2 * 1024 * 1024):
    from .job_sandbox import managed_job

    with managed_job() as job:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        job.assign(proc)
        chunks = []
        size = 0
        overflow = threading.Event()

        def consume():
            nonlocal size
            while True:
                data = proc.stdout.read1(65536)
                if not data:
                    return
                size += len(data)
                if size > limit:
                    overflow.set()
                    return
                chunks.append(data)

        reader = threading.Thread(target=consume, daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                if stopped() or overflow.is_set() or time.monotonic() > deadline:
                    raise ValueError("Η τοπική εντολή ακυρώθηκε ή ξεπέρασε το όριο χρόνου/εξόδου.")
                time.sleep(0.03)
            reader.join(timeout=2)
            if reader.is_alive() or overflow.is_set():
                raise ValueError("Η έξοδος της τοπικής εντολής ξεπέρασε το όριο.")
            return proc.returncode, b"".join(chunks)
        finally:
            if proc.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        timeout=5,
                    )
                else:
                    proc.kill()
                proc.wait(timeout=5)
            proc.stdout.close()
            reader.join(timeout=1)
