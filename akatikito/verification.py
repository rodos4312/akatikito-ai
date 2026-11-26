"""Trusted local checks: run actual test scripts, with all outputs isolated on D:."""
from __future__ import annotations
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def interpreter_for_checks() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    candidates = (
        repo_root / ".venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "bin" / "python",
        Path(sys.prefix) / "Scripts" / "python.exe",
        Path(sys.prefix) / "bin" / "python",
        Path(sys.executable).with_name("python.exe"),
        Path(sys.executable),
    )
    for candidate in candidates:
        if candidate.is_file():
            return Path(os.path.abspath(candidate))
    raise RuntimeError("Δεν βρέθηκε Python ελέγχων. Άνοιξε το AkatikitoApp.cmd από τον φάκελο της εφαρμογής.")


def _size_violations(root: Path, limit: int = 800) -> list[str]:
    skip = {"__pycache__", ".pytest_cache", "node_modules", "template_parsers", ".venv", "venv"}
    suffixes = (".py", ".pyw", ".js", ".cjs", ".mjs", ".html", ".css", ".cmd", ".ps1", ".sh")
    excluded = {"akatikito/i18n.py"}
    bad: list[str] = []
    targets = [p for p in sorted(root.iterdir()) if p.is_file() and p.suffix.lower() in suffixes]
    for folder in (root / "akatikito", root / "scripts"):
        if folder.is_dir():
            targets.extend(
                p for p in sorted(folder.rglob("*"))
                if p.is_file() and p.suffix.lower() in suffixes and not (set(p.relative_to(root).parts) & skip)
            )
    for p in targets:
        rel = p.relative_to(root).as_posix()
        if rel in excluded:
            continue
        try:
            n = len(p.read_text(encoding="utf-8-sig", errors="replace").splitlines())
        except OSError:
            continue
        if n > limit:
            bad.append(f"{rel} ({n} γραμμές)")
    return bad


def run_checks(root: Path, scripts: list[str] | None = None) -> tuple[bool, str]:
    root = Path(root).resolve()
    from .process_lock import writable_dir
    env_temp = os.environ.get("AKATIKITO_TEMP_DIR", "").strip()
    candidates: list[Path] = []
    if env_temp:
        candidates.append(Path(env_temp))
    if Path("D:/").exists():
        candidates.append(Path("D:/Meu-Temp/akatikito-checks"))
    candidates.append(Path(tempfile.gettempdir()) / "akatikito-checks")
    temp_base = writable_dir(*candidates)
    work = Path(tempfile.mkdtemp(prefix="akatikito-check-", dir=temp_base))
    env = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "__PYVENV_LAUNCHER__"):
        env.pop(name, None)
    env.update(TEMP=str(work), TMP=str(work), TMPDIR=str(work), PYTHONDONTWRITEBYTECODE="1",
               PYTHONIOENCODING="utf-8", AKATIKITO_SETTINGS_FILE=str(work / "settings.json"),
               AKATIKITO_MEMORY_DIR=str(work / "memory"), AKATIKITO_AGENDA_FILE=str(work / "agenda.json"),
               AKATIKITO_TESTING="1")
    logs = []
    def finish(ok: bool, message: str = ""):
        if message:
            logs.append(message)
        report = "\n".join(logs)
        (work / "checks.log").write_text(report, encoding="utf-8")
        return ok, report + f"\nΑρχείο ελέγχων: {work / 'checks.log'}"
    try:
        python = interpreter_for_checks()
        env["VIRTUAL_ENV"] = str(python.parent.parent)
        env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", "")
        logs.extend([f"Candidate: {root}", f"Test Python: {python}"])
        probe = ("import sys,json,importlib.util; "
                 "required=['openai','pyperclip','webview','pytest','pypdf','docx','openpyxl','jsonschema','PIL','faster_whisper','av']; "
                 "missing=[n for n in required if importlib.util.find_spec(n) is None]; "
                 "print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'missing':missing})); "
                 "sys.exit(bool(missing))")
        preflight = subprocess.run([str(python), "-B", "-c", probe], cwd=root, env=env,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        logs.append("Environment: " + preflight.stdout.strip() + preflight.stderr)
        if preflight.returncode:
            return finish(False, "Λείπουν εξαρτήσεις από την Python ελέγχων. Ο έλεγχος απέτυχε, δεν παραλείφθηκε. Άνοιξε το AkatikitoApp.cmd από τον κύριο φάκελο.")
        oversized = _size_violations(root)
        if oversized:
            return finish(False, "Όριο μεγέθους κώδικα (<=800 γραμμές): " + ", ".join(oversized[:10]))
        for p in [root / "app.py", *sorted((root / "akatikito").rglob("*.py")), *sorted((root / "tests").glob("*.py"))]:
            ast.parse(p.read_text(encoding="utf-8-sig"), filename=str(p))
        logs.append("OK Python syntax")
        html = (root / "akatikito/ui.html").read_text(encoding="utf-8")
        node = shutil.which("node")
        if not node:
            return finish(False, "Δεν βρέθηκε node για έλεγχο του UI.")
        commands = []
        # Inline <script> blocks (older single-file UI) and every split UI file.
        for index, part in enumerate(html.split("<script>")[1:]):
            js = work / f"ui-inline-{index}.js"
            js.write_text(part.split("</script>", 1)[0], encoding="utf-8")
            commands.append((f"UI JavaScript syntax (inline {index})", [node, "--check", str(js)]))
        ui_scripts = sorted((root / "akatikito/ui").glob("*.js"))
        for js in ui_scripts:
            commands.append((f"UI JavaScript syntax ({js.name})", [node, "--check", str(js)]))
        if not commands:
            return finish(False, "Δεν βρέθηκε JavaScript του UI για έλεγχο.")
        selected = scripts if scripts is not None else [str(p.relative_to(root)).replace("\\", "/") for p in sorted((root / "tests").glob("test_*.py"))] + ["tests/smoke.py"]
        if not selected:
            return finish(False, "Δεν υπάρχουν δοκιμές: η αναβάθμιση απορρίπτεται.")
        for name in selected:
            tree = ast.parse((root / name).read_text(encoding="utf-8-sig"))
            has_main = any(isinstance(n, ast.FunctionDef) and n.name == "main" for n in tree.body)
            collected = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_") or isinstance(n, ast.ClassDef) and n.name.startswith("Test") for n in tree.body)
            if has_main:
                invoke = ("import runpy,sys; path=sys.argv[1]; sys.argv=[path]; "
                          "namespace=runpy.run_path(path,run_name='__akatikito_check__'); "
                          "result=namespace['main'](); sys.exit(result)")
                commands.append((name, [str(python), "-B", "-c", invoke, str(root / name)]))
            elif not collected:
                commands.append((name, [str(python), "-B", str(root / name)]))
            if collected:
                commands.append((name + " [pytest]", [str(python), "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", str(root / name)]))
        frontend_test = root / "tests/test_ui.cjs"
        if frontend_test.is_file():
            commands.append(("UI behavior", [node, str(frontend_test)]))
        for label, command in commands:
            result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=180)
            logs.append(f"{label}: exit={result.returncode}\n{result.stdout}{result.stderr}")
            if result.returncode:
                return finish(False)
        return finish(True, f"OK: εκτελέστηκαν {len(commands)} έλεγχοι.")
    except Exception as exc:
        return finish(False, f"Αποτυχία ελέγχου: {type(exc).__name__}: {exc}")
