"""Lightweight zero-dependency cyclomatic complexity scanner for UI JavaScript files (QUALITY-PLAN-2 Q2)."""
from __future__ import annotations

import re
from pathlib import Path

_FN_RE = re.compile(r"\b(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\([^)]*\)\s*\{")
_DECISION_RE = re.compile(r"\b(?:if|for|while|case|catch)\b|&&|\|\||\?\?|\?")


def _strip_comments_and_strings(src: str) -> str:
    """Replace JS comments and string/template literals with spaces while preserving line structure."""
    out: list[str] = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                if src[i] == "\n":
                    out.append("\n")
                i += 1
            i += 2
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(" ")
            i += 1
            while i < n:
                c = src[i]
                if c == "\\":
                    i += 2
                    continue
                if c == "\n":
                    out.append("\n")
                if c == quote:
                    i += 1
                    break
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _extract_brace_body(cleaned: str, open_brace_idx: int) -> str:
    depth = 0
    for idx in range(open_brace_idx, len(cleaned)):
        ch = cleaned[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return cleaned[open_brace_idx + 1 : idx]
    return cleaned[open_brace_idx + 1 :]


def measure_js_source(src: str) -> dict[str, int]:
    """Return {function_name: cyclomatic_complexity} for named JS functions in source text."""
    cleaned = _strip_comments_and_strings(src)
    scores: dict[str, int] = {}
    seen: dict[str, int] = {}
    for match in _FN_RE.finditer(cleaned):
        name = match.group(1)
        idx = seen.get(name, 0)
        seen[name] = idx + 1
        key = name if idx == 0 else f"{name}#{idx}"
        body = _extract_brace_body(cleaned, match.end() - 1)
        scores[key] = 1 + len(_DECISION_RE.findall(body))
    return scores


def measure_js_files(root: Path) -> dict[str, int]:
    """Measure all UI JS files under akatikito/ui/*.js and akatikito/onboarding.js."""
    result: dict[str, int] = {}
    ui_dir = root / "akatikito" / "ui"
    targets = sorted(ui_dir.glob("*.js")) if ui_dir.is_dir() else []
    onb = root / "akatikito" / "onboarding.js"
    if onb.is_file():
        targets.append(onb)
    for path in targets:
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for fn_name, cc in measure_js_source(text).items():
            result[f"{rel}::{fn_name}"] = cc
    return result
