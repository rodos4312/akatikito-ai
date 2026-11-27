"""Coding Engine Pro: symbol-level reading/editing & fast smoke-test verification.

Supports Python (via stdlib ast) and JS/TS/HTML inline <script> blocks (via a
string- and comment-aware brace matcher), reusing editing.py's syntax checks and
atomic commit_batch backups.
"""
from __future__ import annotations

import ast
import difflib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .editing import EditError, EditResult, check_syntax, commit_batch
from .workspace import Workspace

_JS_EXTS = frozenset({".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx"})
_HTML_EXTS = frozenset({".html", ".htm"})

_SCRIPT_BLOCK_RE = re.compile(
    r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_JS_DECL_RE = re.compile(
    r"^[ \t]*(?:export[ \t]+(?:default[ \t]+)?)?"
    r"(?:(?:async[ \t]+)?function[ \t]+(?P<fn>[A-Za-z_$][\w$]*)[ \t]*\("
    r"|class[ \t]+(?P<cls>[A-Za-z_$][\w$]*)\b"
    r"|(?:const|let|var)[ \t]+(?P<var>[A-Za-z_$][\w$]*)[ \t]*=[ \t]*(?:async[ \t]+)?(?:function\b|\([^)]*\)[ \t]*=>|[A-Za-z_$][\w$]*[ \t]*=>))",
    re.MULTILINE,
)
_TOP_CONST_RE = re.compile(r"^[ \t]*(?:const|let|class)[ \t]+([A-Za-z_$][\w$]*)\b", re.MULTILINE)
_LOCAL_ASSET_RE = re.compile(r"""(?:src|href)\s*=\s*["']([^"'#?]+)["']""", re.IGNORECASE)


@dataclass(frozen=True)
class SymbolSpan:
    name: str
    kind: str
    start_line: int  # 1-based inclusive
    end_line: int    # 1-based inclusive


def _extract_python_symbols(text: str) -> list[SymbolSpan]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    spans: list[SymbolSpan] = []

    def _visit(node: ast.AST, prefix: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(child, ast.ClassDef) else "function"
                full_name = f"{prefix}.{child.name}" if prefix else child.name
                decs = [d.lineno for d in getattr(child, "decorator_list", ()) if hasattr(d, "lineno")]
                start = min([child.lineno, *decs])
                end = getattr(child, "end_lineno", child.lineno) or child.lineno
                spans.append(SymbolSpan(full_name, kind, start, end))
                if isinstance(child, ast.ClassDef):
                    _visit(child, full_name)

    _visit(tree)
    return spans


def _skip_js_non_code(src: str, pos: int) -> int | None:
    """If src[pos:] starts a string or comment, return index after it; else None."""
    n = len(src)
    ch = src[pos]
    nxt = src[pos + 1] if pos + 1 < n else ""
    if ch == "/" and nxt == "/":
        nl = src.find("\n", pos + 2)
        return n if nl < 0 else nl + 1
    if ch == "/" and nxt == "*":
        end = src.find("*/", pos + 2)
        return n if end < 0 else end + 2
    if ch in ("'", '"', "`"):
        i = pos + 1
        while i < n:
            if src[i] == "\\":
                i += 2
                continue
            if src[i] == ch:
                return i + 1
            i += 1
        return n
    return None


def _find_matching_brace(src: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    n = len(src)
    while i < n:
        skipped = _skip_js_non_code(src, i)
        if skipped is not None:
            i = skipped
            continue
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _extract_js_symbols(src: str, base_line: int = 1) -> list[SymbolSpan]:
    spans: list[SymbolSpan] = []
    for match in _JS_DECL_RE.finditer(src):
        name = match.group("fn") or match.group("cls") or match.group("var")
        kind = "class" if match.group("cls") else "function"
        brace = src.find("{", match.end() - 1)
        if brace < 0:
            continue
        # Ensure we didn't jump over a newline+statement into another block.
        between = src[match.end() : brace]
        if ";" in between:
            continue
        close = _find_matching_brace(src, brace)
        if close < 0:
            continue
        start_line = base_line + src.count("\n", 0, match.start())
        end_line = base_line + src.count("\n", 0, close)
        spans.append(SymbolSpan(name, kind, start_line, end_line))
    return spans


def extract_symbols(path: Path, text: str) -> list[SymbolSpan]:
    """Extract top-level symbols with 1-based line ranges from Python, JS/TS, or HTML."""
    ext = path.suffix.lower()
    if ext == ".py":
        return _extract_python_symbols(text)
    if ext in _JS_EXTS:
        return _extract_js_symbols(text, base_line=1)
    if ext in _HTML_EXTS:
        found: list[SymbolSpan] = []
        for match in _SCRIPT_BLOCK_RE.finditer(text):
            if re.search(r"\bsrc\s*=", match.group("attrs") or "", re.IGNORECASE):
                continue
            body_start = match.start("body")
            base_line = 1 + text.count("\n", 0, body_start)
            found.extend(_extract_js_symbols(match.group("body"), base_line=base_line))
        return found
    return []


def _find_symbol(spans: list[SymbolSpan], query: str) -> SymbolSpan | None:
    q = query.strip()
    for sp in spans:
        if sp.name == q:
            return sp
    # Allow short method lookup (e.g., "method" matching "Class.method") if unique.
    suffix_hits = [sp for sp in spans if sp.name.endswith("." + q)]
    if len(suffix_hits) == 1:
        return suffix_hits[0]
    return None


def read_symbol(ws: Workspace, path: str, symbol: str = "", context_lines: int = 2) -> str:
    """List symbols in a file or read a single function/class cleanly by name."""
    target = ws.resolve(path)
    if not target.is_file():
        return f"ΣΦΑΛΜΑ: το αρχείο «{path}» δεν βρέθηκε."
    text = target.read_text(encoding="utf-8", errors="replace")
    spans = extract_symbols(target, text)
    if not symbol.strip():
        if not spans:
            return f"Δεν βρέθηκαν δομικά σύμβολα στο {ws.relative(target)}."
        rows = [f"- {sp.name} ({sp.kind}) L{sp.start_line}-L{sp.end_line}" for sp in spans]
        return f"Σύμβολα στο {ws.relative(target)} ({len(spans)}):\n" + "\n".join(rows)
    hit = _find_symbol(spans, symbol)
    if hit is None:
        avail = ", ".join(sp.name for sp in spans[:25]) or "κανένα"
        return f"ΣΦΑΛΜΑ: δεν βρέθηκε το σύμβολο «{symbol}» στο {ws.relative(target)}. Διαθέσιμα: {avail}."
    lines = text.splitlines()
    ctx = max(0, min(20, int(context_lines)))
    lo = max(1, hit.start_line - ctx)
    hi = min(len(lines), hit.end_line + ctx)
    numbered = [f"{i:4d} | {lines[i - 1]}" for i in range(lo, hi + 1)]
    header = f"{ws.relative(target)} :: {hit.name} ({hit.kind}, L{hit.start_line}-L{hit.end_line})"
    return header + "\n" + "\n".join(numbered)


def replace_symbol(ws: Workspace, path: str, symbol: str, new_code: str) -> str:
    """Replace a single named function/class atomically with syntax gate and backup."""
    target = ws.resolve(path)
    if not target.is_file():
        return f"ΣΦΑΛΜΑ: το αρχείο «{path}» δεν βρέθηκε."
    original_bytes = target.read_bytes()
    text = original_bytes.decode("utf-8")
    spans = extract_symbols(target, text)
    hit = _find_symbol(spans, symbol)
    if hit is None:
        avail = ", ".join(sp.name for sp in spans[:25]) or "κανένα"
        return f"ΣΦΑΛΜΑ: δεν βρέθηκε το σύμβολο «{symbol}» στο {ws.relative(target)}. Διαθέσιμα: {avail}."
    eol = "\r\n" if "\r\n" in text else "\n"
    trailing_nl = text.endswith("\n")
    lines = text.splitlines()
    replacement_lines = str(new_code).rstrip("\r\n").splitlines()
    new_lines = lines[: hit.start_line - 1] + replacement_lines + lines[hit.end_line :]
    updated = eol.join(new_lines) + (eol if trailing_nl else "")
    try:
        check_syntax(target, updated)
    except EditError as exc:
        return f"ΣΦΑΛΜΑ σύνταξης πριν την εγγραφή ({ws.relative(target)}): {exc}"
    try:
        commit_batch(ws, [(target, original_bytes, updated.encode("utf-8"))])
    except EditError as exc:
        return f"ΣΦΑΛΜΑ: {exc}"
    diff = "".join(
        difflib.unified_diff(
            text.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{ws.relative(target)}",
            tofile=f"b/{ws.relative(target)}",
            n=2,
        )
    )
    smoke = _smoke_check_single(ws, target, updated)
    rel = ws.relative(target)
    msg = (
        f"OK: ενημερώθηκε το σύμβολο {hit.name} στο {rel} "
        f"(L{hit.start_line}-L{hit.end_line} → {len(replacement_lines)} γραμμές, με backup).\n"
        f"{smoke}\n{diff[:1800]}"
    ).strip()
    return EditResult(msg, [rel])


def _check_html_deep(ws: Workspace, target: Path, text: str) -> list[str]:
    issues: list[str] = []
    seen_top: dict[str, int] = {}
    for idx, match in enumerate(_SCRIPT_BLOCK_RE.finditer(text), start=1):
        if re.search(r"\bsrc\s*=", match.group("attrs") or "", re.IGNORECASE):
            continue
        body = match.group("body")
        for decl in _TOP_CONST_RE.finditer(body):
            name = decl.group(1)
            prev = seen_top.get(name)
            if prev is not None and prev != idx:
                issues.append(f"διπλή top-level δήλωση «{name}» στα <script> #{prev} και #{idx}")
            else:
                seen_top[name] = idx
    parent = target.parent
    for ref in _LOCAL_ASSET_RE.findall(text):
        cleaned = ref.strip()
        if not cleaned or cleaned.startswith(("http://", "https://", "//", "data:", "mailto:", "javascript:", "/")):
            continue
        cand = (parent / cleaned).resolve()
        if ws.root in cand.parents and not cand.exists():
            issues.append(f"λείπει τοπικό asset: {cleaned}")
    return issues


def _run_related_pytest(ws: Workspace, target: Path) -> str:
    stem = target.stem
    candidates = [
        ws.root / "tests" / f"test_{stem}.py",
        target.with_name(f"test_{stem}.py"),
    ]
    test_file = next((p for p in candidates if p.is_file()), None)
    if test_file is None:
        return "pytest: δεν βρέθηκε αντίστοιχο test file"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(test_file)],
        cwd=str(ws.root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()
    summary = tail[-1] if tail else f"exit={proc.returncode}"
    mark = "✅" if proc.returncode == 0 else "❌"
    return f"{mark} pytest ({ws.relative(test_file)}): {summary}"


def _smoke_check_single(ws: Workspace, target: Path, text: str | None = None, run_tests: bool = False) -> str:
    rel = ws.relative(target)
    if text is None:
        text = target.read_text(encoding="utf-8", errors="replace")
    ext = target.suffix.lower()
    try:
        check_syntax(target, text)
    except EditError as exc:
        return f"❌ SMOKE CHECK FAILED ({rel}): {exc}"
    notes: list[str] = []
    if ext == ".py":
        try:
            compile(text, rel, "exec")
        except SyntaxError as exc:
            return f"❌ SMOKE CHECK FAILED ({rel}): {exc}"
        notes.append("AST/compile OK")
        if run_tests:
            notes.append(_run_related_pytest(ws, target))
    elif ext in _HTML_EXTS:
        deep = _check_html_deep(ws, target, text)
        if deep:
            return f"❌ SMOKE CHECK FAILED ({rel}): " + "; ".join(deep)
        notes.append("HTML scripts + assets OK")
    elif ext in _JS_EXTS:
        notes.append("JS syntax OK")
    elif ext == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return f"❌ SMOKE CHECK FAILED ({rel}): {exc}"
        notes.append("JSON OK")
    else:
        notes.append("file readable")
    return f"✅ SMOKE CHECK OK ({rel}): " + " · ".join(notes)


def smoke_check(ws: Workspace, path: str, run_tests: bool = False) -> str:
    """Run a fast, deterministic syntax & runtime-hazard smoke check on a workspace file."""
    target = ws.resolve(path)
    if not target.is_file():
        return f"ΣΦΑΛΜΑ: το αρχείο «{path}» δεν βρέθηκε."
    return _smoke_check_single(ws, target, run_tests=bool(run_tests))


CODE_ENGINE_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "read_symbol",
            "description": (
                "List functions/classes in a Python/JS/TS/HTML file (when symbol is omitted) "
                "or read a single function/class cleanly by name without loading the whole file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative file path"},
                    "symbol": {"type": "string", "description": "Function or class name (empty = list outline)"},
                    "context_lines": {"type": "integer", "description": "Extra lines around the symbol (default 2)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_symbol",
            "description": (
                "Atomically replace a single named function or class in Python/JS/TS/HTML "
                "with pre-write syntax checking, automatic backup, and immediate smoke check."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative file path"},
                    "symbol": {"type": "string", "description": "Exact symbol name (e.g. buildCity or Class.method)"},
                    "new_code": {"type": "string", "description": "Complete replacement code for the symbol"},
                },
                "required": ["path", "symbol", "new_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "smoke_check",
            "description": (
                "Run a fast deterministic smoke check on a file: Python AST/compile (+ optional pytest), "
                "HTML inline <script> syntax + duplicate top-level const/let + missing local assets, JS, or JSON."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative file path to verify"},
                    "run_tests": {"type": "boolean", "description": "For Python files, also run matching pytest file"},
                },
                "required": ["path"],
            },
        },
    },
]

CODE_ENGINE_HANDLERS = {
    "read_symbol": read_symbol,
    "replace_symbol": replace_symbol,
    "smoke_check": smoke_check,
}
