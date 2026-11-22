"""Read-only Git overview scoped to the project, with repo execution hooks and filters disabled."""
from __future__ import annotations

import os
import re
import shutil

from .local_process import run_local


def git_status_diff(ws, path: str = "", staged: bool = False) -> str:
    from .tools import _clip_with_archive

    git = shutil.which("git")
    if not git:
        raise ValueError("Δεν βρέθηκε Git.")
    relative = ws.relative(ws.resolve(path or "."))
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("GIT_") and not any(x in k.upper() for x in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))
    }
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_OPTIONAL_LOCKS="0",
        GIT_TERMINAL_PROMPT="0",
    )
    prefix = [
        git,
        "--no-optional-locks",
        "--no-pager",
        "-c", "core.fsmonitor=false",
        "-c", "core.hooksPath=" + os.devnull,
        "-c", "core.attributesFile=" + os.devnull,
    ]
    stop = getattr(ws, "search_stopped", lambda: False)
    config_code, configured = run_local(
        prefix + ["config", "--name-only", "--get-regexp", r"^filter\..*\.(clean|smudge|process|required)$"],
        str(ws.root),
        env,
        stopped=stop,
        limit=64000,
    )
    if config_code not in (0, 1):
        return "Το project δεν είναι ενεργό αποθετήριο Git (δεν ελέγχθηκαν οι ρυθμίσεις φίλτρων Git)."
    drivers = set()
    for key in configured.decode("utf-8", errors="strict").splitlines():
        if not re.fullmatch(r"filter\.[A-Za-z0-9_.-]{1,100}\.(clean|smudge|process|required)", key):
            raise ValueError("Μη υποστηριζόμενο όνομα φίλτρου Git. Η ανάγνωση δεν εκτελέστηκε.")
        drivers.add(key.rsplit(".", 1)[0])
    if len(drivers) > 100:
        raise ValueError("Πάρα πολλά φίλτρα Git.")
    for driver in sorted(drivers):
        for option in ("clean", "smudge", "process"):
            prefix += ["-c", driver + "." + option + "="]
        prefix += ["-c", driver + ".required=false"]

    code, status = run_local(
        prefix + ["status", "--short", "--untracked-files=normal", "--", relative],
        str(ws.root),
        env,
        stopped=stop,
    )
    if code:
        return "Το project δεν είναι ενεργό αποθετήριο Git."
    args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames"]
    if staged:
        args.append("--staged")
    args += ["--", relative]
    code, diff = run_local(prefix + args, str(ws.root), env, stopped=stop)
    if code:
        raise ValueError("Δεν αναγνώστηκε το Git diff.")
    st_out = status.decode("utf-8", errors="replace").strip() or "(Καμία αλλαγή — καθαρό working tree)"
    df_out = diff.decode("utf-8", errors="replace").strip() or "(Κενό diff)"
    combined = f'[Git Status]\n{st_out}\n\n[Git Diff{" (staged)" if staged else ""}]\n{df_out}'
    return _clip_with_archive(ws, combined)
