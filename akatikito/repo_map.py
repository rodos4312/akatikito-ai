"""Stable, bounded code navigation. Exact source stays in read_file/search_project."""
from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path
import re
import time

from .local_store import checked, load_json, save_json
from .reading import SUPPORTED_STRUCT_SUFFIXES, scan_definitions
from .syntax_index import LANGUAGES, source_suffix, symbols as ts_symbols

MAX_FILES = 2000
MAX_ENTRIES = 20000
MAX_BYTES = 256000
CACHE_NAME = "repo-map-v6.json"
SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", ".agent",
        ".agent_backups", "data", "dist", "build", ".mypy_cache", ".pytest_cache"}
SUFFIXES = {".py", ".pyw", ".md"} | set(SUPPORTED_STRUCT_SUFFIXES) | set(LANGUAGES)
SCRIPT_IMPORTS = re.compile(r"(?:from\s+|require\s*\(\s*|import\s*)['\"]([^'\"]+)['\"]")


def _short(names, count=12):
    names = sorted(set(names))
    return ", ".join(names[:count]) + (f", … +{len(names)-count}" if len(names) > count else "")


def _describe(raw, suffix, is_test):
    text = raw.decode("utf-8-sig", errors="replace")
    if suffix in (".py", ".pyw"):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return ["[SyntaxError: χρησιμοποίησε read_file]"], []
        names = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not is_test or not node.name.startswith("test_"):
                    names.append(node.name)
            elif isinstance(node, ast.ClassDef):
                methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                names.append(f"class {node.name}({_short(methods, 8)})")
        if is_test:
            tests = sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_") for n in ast.walk(tree))
            if tests:
                names.append(f"{tests} tests")
        refs = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                refs.extend("py-import:"+n.name for n in node.names)
            elif isinstance(node, ast.ImportFrom):
                refs.append("py-from:"+str(node.level)+":"+(node.module or "")+":"+
                            ",".join(sorted(n.name for n in node.names)))
        return sorted(set(names)), sorted(set(refs))
    if suffix == ".md":
        return [], []
    if suffix in LANGUAGES:
        analysis = ts_symbols(text, suffix)
        if not analysis.get("missing_parser"):
            if not analysis["cacheable"]:
                raise ValueError(analysis["notice"])
            names = [row["name"] for row in analysis["symbols"]]
            if analysis["notice"]:
                names.append("[μερική δομή: read_file outline=true]")
            return sorted(set(names)), analysis["refs"]
    if suffix in SUPPORTED_STRUCT_SUFFIXES:
        blocks = scan_definitions(text.splitlines(), suffix)
        names = [name for _, _, name, _ in blocks if not name.startswith("impl ")]
        refs = ["js:" + m.group(1) for m in SCRIPT_IMPORTS.finditer(text)]
        return sorted(set(names)), sorted(set(refs))
    return [], []


from .dependencies import dependants as _dependants


class RepoMap:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.cache = {}
        self.loaded = False
        self.last_text = ""
        self.last_signature = None

    def _paths(self):
        found, pending, scanned = [], [self.root], 0
        limited = False
        while pending:
            directory = pending.pop()
            try:
                entries = []
                with os.scandir(directory) as scan:
                    for entry in scan:
                        scanned += 1
                        if scanned > MAX_ENTRIES:
                            limited = True
                            break
                        entries.append(entry)
                folders = []
                for entry in sorted(entries, key=lambda e: e.name):
                    if entry.name.startswith(".") or entry.name in SKIP:
                        continue
                    path = Path(entry.path)
                    try:
                        checked(path)
                        if entry.is_dir(follow_symlinks=False):
                            folders.append(path)
                        elif entry.is_file(follow_symlinks=False) and path.suffix.lower() in SUFFIXES:
                            found.append(path)
                            if len(found) >= MAX_FILES:
                                limited = True
                                break
                    except (OSError, ValueError):
                        continue
                pending.extend(reversed(folders))
                if limited:
                    break
            except OSError:
                continue
        return found, limited

    def _load(self):
        if self.loaded:
            return
        self.loaded = True
        try:
            saved = load_json(self.root / ".agent" / CACHE_NAME, dict)
            if saved.get("version") == 6 and saved.get("root") == str(self.root):
                for name, entry in saved.get("files", {}).items():
                    if (isinstance(entry, dict) and isinstance(entry.get("stamp"), list)
                            and len(entry["stamp"]) == 3
                            and all(isinstance(x, str) for x in entry.get("defs", []))
                            and all(isinstance(x, str) for x in entry.get("refs", []))):
                        self.cache[name] = entry
        except (OSError, ValueError, AttributeError, TypeError):
            self.cache = {}  # disposable navigation cache, never source or memory

    def render(self, budget=6000, time_limit=1.0):
        budget = max(128, min(60000, int(budget)))
        self._load()
        paths, limited = self._paths()
        rows, signature = [], []
        for path in paths:
            try:
                info = path.stat()
            except OSError:
                continue
            name = path.relative_to(self.root).as_posix()
            stamp = [info.st_mtime_ns, info.st_size, info.st_ctime_ns]
            signature.append((name, *stamp))
            rows.append((path, name, stamp))
        full_signature = (budget, limited, tuple(signature))
        if self.last_signature == full_signature:
            return self.last_text
        started, complete, changed = time.monotonic(), True, False
        entries = []
        for path, name, stamp in rows:
            entry = self.cache.get(name)
            if not entry or entry["stamp"] != stamp:
                if time.monotonic() - started > time_limit:
                    complete = False
                    entries.append((name, ["[ανάλυση εκκρεμεί]"], []))
                    continue
                try:
                    with path.open("rb") as stream:
                        raw = stream.read(MAX_BYTES + 1)
                    if path.suffix.lower() == ".md":
                        defs, refs = [], []
                    elif len(raw) > MAX_BYTES:
                        defs, refs = ["[μεγάλο αρχείο: read_file outline=true]"], []
                    else:
                        defs, refs = _describe(raw, source_suffix(path),
                                               path.name.startswith("test_") or "tests" in path.relative_to(self.root).parts)
                    # A concurrent edit must not install stale definitions in the cache.
                    current = path.stat()
                    if [current.st_mtime_ns, current.st_size, current.st_ctime_ns] != stamp:
                        complete = False
                        entries.append((name, ["[αρχείο υπό αλλαγή]"], []))
                        continue
                    entry = {"stamp": stamp, "defs": defs, "refs": refs,
                             "digest": hashlib.sha256(raw).hexdigest()}
                    self.cache[name] = entry
                    changed = True
                except (OSError, ValueError):
                    complete = False
                    entries.append((name, ["[προσωρινά μη διαθέσιμο]"], []))
                    continue
            entries.append((name, entry["defs"], entry["refs"]))
        present = {name for _, name, _ in rows}
        if set(self.cache) - present:
            self.cache = {k: v for k, v in self.cache.items() if k in present}
            changed = True
        if changed:
            try:
                save_json(self.root / ".agent" / CACHE_NAME,
                          {"version": 6, "root": str(self.root), "files": self.cache})
            except (OSError, ValueError):
                pass  # a read-only workspace still gets an in-memory map
        from .dependencies import edges
        used_by = {}
        for source,target in edges(entries):
            used_by[target] = used_by.get(target,0)+1
        entries.sort(key=lambda row: (not bool(row[1]), -used_by.get(row[0], 0), row[0]))
        header = f"{len(entries)} αρχεία{' και άλλα' if limited else ''}. Χάρτης πλοήγησης· ακριβής κώδικας: read_file outline=true.\n"
        footer = "\nΠλήρης δομή/πηγαίος κώδικας: list_files / search_project."
        dependants = _dependants(entries)
        lines, used, omitted = [], len(header) + len(footer) + 65, 0
        for name, defs, _ in entries:
            line = name + (": " + _short(defs, 20) if defs else "")
            if used + len(line) + 1 <= budget:
                lines.append((name, line))
                used += len(line) + 1
            elif used + len(name) + 1 <= budget:
                lines.append((name, name))
                used += len(name) + 1
            else:
                omitted += 1
        # Only after every name is placed: dependants are the first thing the budget drops.
        placed = []
        for name, line in lines:
            extra = dependants.get(name, "")
            if extra and used + len(extra) + 1 <= budget:
                line += " " + extra
                used += len(extra) + 1
            placed.append(line)
        lines = placed
        tail = f"\n+{omitted} αρχεία εκτός προβολής." if omitted else ""
        text = header + "\n".join(lines) + tail + footer if entries else ""
        if len(text) > budget:
            text = (f"Χάρτης: {len(entries)} αρχεία. Χρησιμοποίησε list_files / search_project.")[:budget]
        if complete:
            self.last_signature, self.last_text = full_signature, text
        return text
