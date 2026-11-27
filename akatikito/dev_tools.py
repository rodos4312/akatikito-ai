"""Precision programmer tools: find_references (AST + JS/TS), smart run_tests, and lint_check.

All tools stay strictly inside workspace boundaries:
- find_references: read-only Python AST + word/import-aware JS/TS/HTML reference finder.
- run_tests: auto-detects project test runner, supports failed_only (--lf) & keyword filtering,
  and routes exclusively through dispatch(..., 'run_command') so owner approval & profiles apply.
- lint_check: runs local ruff / tsc / eslint (shell=False, argv list) with clean fallback.
"""
from __future__ import annotations

import ast
from contextlib import suppress
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .workspace import Workspace

_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    "dist", "build", ".agent", ".agent_backups", ".akatikito", ".mypy_cache", ".ruff_cache",
})
_PY_EXTS = frozenset({".py"})
_CODE_EXTS = frozenset({
    ".js", ".cjs", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".htm",
    ".go", ".rs", ".java", ".dart", ".cs", ".cpp", ".c", ".h", ".css",
})
_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$-]{0,120}$")
_SAFE_KEYWORD_RE = re.compile(r"^[A-Za-z0-9_ .\-]+$")
_MAX_SCAN_FILES = 600
_MAX_FILE_BYTES = 512 * 1024


def _iter_code_files(ws: Workspace, rel_path: str) -> list[Path]:
    root = ws.resolve(rel_path) if str(rel_path or "").strip() else ws.root
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    out: list[Path] = []
    for cur_root, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        for fname in sorted(files):
            p = Path(cur_root) / fname
            if p.suffix.lower() not in _PY_EXTS and p.suffix.lower() not in _CODE_EXTS:
                continue
            try:
                if p.is_symlink() or p.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            out.append(p)
            if len(out) >= _MAX_SCAN_FILES:
                return out
    return out


def _scan_python_ast(text: str, symbol: str) -> list[tuple[str, int]]:
    """Return [(kind, lineno), ...] via Python AST, completely ignoring comments & string literals."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    hits: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    def _add(kind: str, lineno: int) -> None:
        if lineno > 0 and (kind, lineno) not in seen:
            seen.add((kind, lineno))
            hits.append((kind, lineno))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
            _add("def", node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == symbol or alias.asname == symbol for alias in node.names):
                _add("import", node.lineno)
        elif isinstance(node, ast.Import):
            if any(alias.name.split(".")[-1] == symbol or alias.asname == symbol for alias in node.names):
                _add("import", node.lineno)
        elif isinstance(node, ast.Name) and node.id == symbol:
            _add("ref", node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == symbol:
            _add("ref", node.lineno)
    return hits


def _scan_generic_code(lines: list[str], symbol: str) -> list[tuple[str, int]]:
    word_re = re.compile(rf"\b{re.escape(symbol)}\b")
    def_re = re.compile(
        rf"\b(?:function|class|const|let|var|def|fn|func|interface|type)\s+{re.escape(symbol)}\b"
    )
    imp_re = re.compile(rf"\b(?:import|require|from)\b.*\b{re.escape(symbol)}\b")
    hits: list[tuple[str, int]] = []
    for lineno, raw in enumerate(lines, 1):
        code_part = raw.split("//", 1)[0]
        if not word_re.search(code_part):
            continue
        if def_re.search(code_part):
            hits.append(("def", lineno))
        elif imp_re.search(code_part):
            hits.append(("import", lineno))
        else:
            hits.append(("ref", lineno))
    return hits


def find_references(ws: Workspace, symbol: str, path: str = "", max_results: int = 60) -> str:
    """Find definitions, imports, and usages of a symbol across the workspace."""
    sym = str(symbol or "").strip()
    if not _SAFE_IDENT_RE.match(sym):
        return "ΣΦΑΛΜΑ: δώσε έγκυρο όνομα συμβόλου (π.χ. parse_config, MyClass)."
    cap = max(10, min(150, int(max_results or 60)))
    buckets: dict[str, list[str]] = {"def": [], "import": [], "ref": []}
    total = 0
    for fpath in _iter_code_files(ws, path):
        try:
            text = fpath.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        if sym not in text:
            continue
        lines = text.splitlines()
        rel = ws.relative(fpath).replace("\\", "/")
        hits = _scan_python_ast(text, sym) if fpath.suffix.lower() in _PY_EXTS else _scan_generic_code(lines, sym)
        for kind, lineno in hits:
            snippet = lines[lineno - 1].strip()[:140] if 1 <= lineno <= len(lines) else ""
            buckets[kind].append(f"  {rel}:{lineno} | {snippet}")
            total += 1
            if total >= cap:
                break
        if total >= cap:
            break
    if total == 0:
        return f"Δεν βρέθηκαν αναφορές για το σύμβολο «{sym}»."
    d_n, i_n, r_n = len(buckets["def"]), len(buckets["import"]), len(buckets["ref"])
    out = [f"Αναφορές συμβόλου «{sym}» ({d_n} ορισμοί, {i_n} imports, {r_n} χρήσεις):"]
    for key, label in (("def", "[Ορισμοί]"), ("import", "[Imports]"), ("ref", "[Χρήσεις]")):
        if buckets[key]:
            out.append(label)
            out.extend(buckets[key])
    if total >= cap:
        out.append(f"… (εμφανίστηκαν τα πρώτα {cap} αποτελέσματα· περιόρισε με path=...)")
    return "\n".join(out)


def _detect_test_runner(ws: Workspace) -> tuple[str, str]:
    """Return (kind, base_command) for the workspace's test suite."""
    cfg_cmd = str(getattr(getattr(ws, "config", None), "verify_command", "") or "").strip()
    if cfg_cmd:
        kind = "pytest" if "pytest" in cfg_cmd.lower() else ("npm" if "npm " in cfg_cmd.lower() else "custom")
        return kind, cfg_cmd
    root = ws.root
    venv_py = root / ".venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    py_exe = f'"{venv_py}"' if venv_py.is_file() else f'"{sys.executable}"'
    if (root / "pytest.ini").is_file() or (root / "pyproject.toml").is_file() or (root / "tests").is_dir():
        return "pytest", f"{py_exe} -m pytest -q"
    pkg_json = root / "package.json"
    if pkg_json.is_file():
        with suppress(OSError, ValueError):
            scripts = (json.loads(pkg_json.read_text(encoding="utf-8")) or {}).get("scripts") or {}
            test_script = str(scripts.get("test") or "")
            if test_script and "no test specified" not in test_script.lower():
                return "npm", "npm test"
    if (root / "Cargo.toml").is_file():
        return "cargo", "cargo test"
    if (root / "go.mod").is_file():
        return "go", "go test ./..."
    return "", ""


def _summarize_test_output(raw: str) -> str:
    lines = raw.splitlines()
    status = lines[0] if lines else ""
    highlights = [
        ln for ln in lines[1:]
        if re.search(r"^(?:FAILED|ERROR|E\s+|AssertionError|TypeError|SyntaxError|\d+\s+(?:passed|failed))", ln.strip())
    ]
    if not highlights or len(raw) <= 2500:
        return raw
    summary = "\n".join(highlights[:30])
    tail = "\n".join(lines[-25:])
    return f"{status}\n[Σύνοψη αποτυχιών]\n{summary}\n\n[Τέλος εξόδου]\n{tail}"


def run_tests(
    ws: Workspace,
    target: str = "",
    failed_only: bool = False,
    keyword: str = "",
    timeout: int = 120,
) -> str:
    """Auto-detect & run project tests with optional target, failed_only (--lf), and keyword (-k)."""
    from .tools import dispatch

    kind, base_cmd = _detect_test_runner(ws)
    if not base_cmd:
        return "ΣΦΑΛΜΑ: δεν εντοπίστηκε test runner (pytest / npm test / cargo / go) στο project."
    cmd_parts = [base_cmd]
    if kind == "pytest":
        if failed_only:
            cmd_parts.append("--lf")
        kw = str(keyword or "").strip()
        if kw:
            if not _SAFE_KEYWORD_RE.match(kw):
                return "ΣΦΑΛΜΑ: το keyword επιτρέπεται να περιέχει μόνο γράμματα, αριθμούς, κενά και _.-"
            cmd_parts.append(f'-k "{kw}"')
    tgt = str(target or "").strip()
    if tgt:
        resolved = ws.resolve(tgt)
        if not resolved.exists():
            return f"ΣΦΑΛΜΑ: ο στόχος test '{tgt}' δεν βρέθηκε."
        rel = ws.relative(resolved).replace("\\", "/")
        cmd_parts.append(f'"{rel}"' if " " in rel else rel)
    full_cmd = " ".join(cmd_parts)
    raw = dispatch(ws, "run_command", {"command": full_cmd, "timeout": max(10, min(300, int(timeout or 120)))})
    if raw.startswith(("ΑΠΟΡΡΙΨΗ", "ΣΦΑΛΜΑ")):
        return raw
    return _summarize_test_output(raw)


def _find_tool_bin(ws: Workspace, name: str) -> str | None:
    venv_dir = ws.root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    cand = venv_dir / (f"{name}.exe" if os.name == "nt" else name)
    if cand.is_file():
        return str(cand)
    return shutil.which(name)


def _run_linter_argv(argv: list[str], cwd: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        return proc.returncode, (proc.stdout + "\n" + proc.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def _lint_python_target(ws: Workspace, target: Path) -> str:
    ruff_bin = _find_tool_bin(ws, "ruff")
    if ruff_bin:
        rc, out = _run_linter_argv([ruff_bin, "check", "--output-format=concise", str(target)], ws.root)
        if rc == 0:
            return "✅ Ruff: 0 σφάλματα lint."
        return f"⚠️ Ruff ({ws.relative(target) if target != ws.root else '.'}):\n" + out[:4000]
    # Pure-Python AST fallback when ruff is not installed
    py_files = [target] if target.is_file() else [p for p in _iter_code_files(ws, "") if p.suffix.lower() == ".py"]
    errs: list[str] = []
    for p in py_files[:200]:
        try:
            ast.parse(p.read_text(encoding="utf-8-sig"), filename=str(p))
        except (SyntaxError, UnicodeError, OSError) as exc:
            errs.append(f"{ws.relative(p)}: {exc}")
    return "✅ Python AST: 0 συντακτικά σφάλματα." if not errs else "⚠️ Python AST:\n" + "\n".join(errs[:20])


def lint_check(ws: Workspace, path: str = "") -> str:
    """Run installed project linters/typecheckers (ruff, tsc, node --check) safely without shell."""
    target = ws.resolve(path) if str(path or "").strip() else ws.root
    if not target.exists():
        return f"ΣΦΑΛΜΑ: η διαδρομή '{path}' δεν βρέθηκε."
    reports: list[str] = []
    ext = target.suffix.lower() if target.is_file() else ""
    if ext == ".py" or (target.is_dir() and any(p.suffix == ".py" for p in _iter_code_files(ws, path)[:50])):
        reports.append(_lint_python_target(ws, target))
    if (ws.root / "tsconfig.json").is_file() and (not ext or ext in (".ts", ".tsx")):
        tsc_bin = _find_tool_bin(ws, "tsc")
        if tsc_bin:
            rc, out = _run_linter_argv([tsc_bin, "--noEmit", "--pretty", "false"], ws.root)
            reports.append("✅ TypeScript (tsc): 0 σφάλματα." if rc == 0 else "⚠️ TypeScript (tsc):\n" + out[:4000])
    if ext in (".js", ".cjs", ".mjs"):
        node_bin = shutil.which("node")
        if node_bin:
            rc, out = _run_linter_argv([node_bin, "--check", str(target)], ws.root)
            reports.append(f"✅ Node JS syntax ({ws.relative(target)}): OK." if rc == 0 else f"⚠️ JS syntax:\n{out[:2000]}")
    if not reports:
        return "ℹ️ Δεν εντοπίστηκε συμβατός linter (ruff / tsc / node) για αυτή τη διαδρομή."
    return "\n\n".join(reports)


DEV_TOOLS_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "find_references",
            "description": (
                "Find all definitions, imports, and usages of a function/class/variable across the project "
                "(Python AST + JS/TS/HTML). Ignores comments/strings in Python. Use before renaming or refactoring."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Exact symbol name to locate"},
                    "path": {"type": "string", "description": "Optional subfolder or file to scope search"},
                    "max_results": {"type": "integer"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": (
                "Auto-detect and run project tests (pytest / npm test / cargo / go) with structured failure summary. "
                "Supports failed_only=true (--lf) and keyword (-k) for fast edit-fix loops."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Optional test file or directory inside project"},
                    "failed_only": {"type": "boolean", "description": "Re-run only last failed tests (pytest --lf)"},
                    "keyword": {"type": "string", "description": "Optional pytest -k filter expression"},
                    "timeout": {"type": "integer"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lint_check",
            "description": "Run local static linters/typecheckers (ruff, tsc --noEmit, node --check) on a file or the project without shell execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Optional file or directory to lint (defaults to project root)"},
                },
                "required": [],
            },
        },
    },
]

DEV_TOOLS_HANDLERS = {
    "find_references": find_references,
    "run_tests": run_tests,
    "lint_check": lint_check,
}
