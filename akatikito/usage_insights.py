"""Formatted usage & cost insights report (/usage, 0 tokens) backed by UsageLedger."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .settings import DEFAULT_PATH
from .usage_ledger import UsageLedger


def _fmt_usd(cost: float) -> str:
    val = float(cost or 0.0)
    if val <= 0:
        return "$0.0000"
    return f"${val:.4f}" if val < 0.1 else f"${val:.3f}"


def _window_totals(days_map: dict[str, Any], ref: date, span_days: int) -> dict[str, Any]:
    prompt = comp = hit = calls = unknown = 0
    cost = 0.0
    for offset in range(span_days):
        key = (ref - timedelta(days=offset)).isoformat()
        row = days_map.get(key)
        if not isinstance(row, dict):
            continue
        prompt += int(row.get("prompt_tokens", 0) or 0)
        comp += int(row.get("completion_tokens", 0) or 0)
        hit += int(row.get("cache_hit_tokens", 0) or 0)
        calls += int(row.get("calls", 0) or 0)
        unknown += int(row.get("unknown", 0) or 0)
        cost += float(row.get("cost", 0.0) or 0.0)
    cache_pct = round(100.0 * min(prompt, hit) / prompt, 1) if prompt > 0 else 0.0
    return {
        "prompt_tokens": prompt,
        "completion_tokens": comp,
        "total_tokens": prompt + comp,
        "cache_hit_pct": cache_pct,
        "calls": calls,
        "unknown": unknown,
        "cost": round(cost, 6),
    }


def _ledger_path(ledger_path: Path | None = None) -> Path:
    if ledger_path is not None:
        return Path(ledger_path)
    return DEFAULT_PATH.parent / "usage.json"


def build_usage_insights(
    *,
    ledger_path: Path | None = None,
    session_tokens: dict[str, Any] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Compute Today / 7-day / 30-day usage & cost breakdown from usage.json."""
    ref = today or date.today()
    ledger = UsageLedger(_ledger_path(ledger_path))
    try:
        with ledger.lock:
            data = ledger.load()
        monthly = ledger.monthly_insights(today=ref)
    except (OSError, ValueError):
        data = {"days": {}, "sessions": {}}
        monthly = {"top_models": [], "top_projects": [], "cache_saved": 0.0}
    days_map = data.get("days") if isinstance(data.get("days"), dict) else {}
    return {
        "today": _window_totals(days_map, ref, 1),
        "days_7": _window_totals(days_map, ref, 7),
        "days_30": _window_totals(days_map, ref, 30),
        "top_models": list(monthly.get("by_model") or [])[:4],
        "top_projects": list(monthly.get("by_project") or [])[:4],
        "session": dict(session_tokens or {}),
    }


def format_usage_insights(
    *,
    ledger_path: Path | None = None,
    session_tokens: dict[str, Any] | None = None,
    today: date | None = None,
) -> str:
    """Render a clean Greek /usage summary (0 LLM tokens)."""
    rep = build_usage_insights(ledger_path=ledger_path, session_tokens=session_tokens, today=today)
    lines = ["📈 **Αναφορά Κόστους & Χρήσης** *(0 tokens)*"]
    sess = rep["session"]
    s_in = int(sess.get("prompt_tokens", 0) or 0)
    s_out = int(sess.get("completion_tokens", 0) or 0)
    if s_in or s_out:
        s_cost = float(sess.get("provider_cost", 0.0) or 0.0)
        s_hit = int(sess.get("cache_hit_tokens", 0) or 0)
        s_pct = round(100.0 * min(s_in, s_hit) / s_in, 1) if s_in > 0 else 0.0
        cost_part = f" · **{_fmt_usd(s_cost)}**" if s_cost > 0 else ""
        lines.append(f"- Τρέχουσα συνεδρία: **{s_in + s_out:,}** tok (cache {s_pct}%){cost_part}")
    for label, key in (("Σήμερα", "today"), ("Τελευταίες 7 μέρες", "days_7"), ("Τελευταίες 30 μέρες", "days_30")):
        w = rep[key]
        lines.append(
            f"- {label}: **{_fmt_usd(w['cost'])}** · {w['total_tokens']:,} tok "
            f"({w['calls']} κλήσεις · cache {w['cache_hit_pct']}%)"
        )
    if rep["top_models"]:
        lines.append("\n**Top Μοντέλα (μήνας):**")
        for m in rep["top_models"]:
            lines.append(f"- `{m['name']}`: {_fmt_usd(m['cost'])} · {m['tokens']:,} tok ({m['calls']} κλήσεις)")
    if rep["top_projects"]:
        lines.append("\n**Top Projects (μήνας):**")
        for p in rep["top_projects"]:
            lines.append(f"- `{p['name']}`: {_fmt_usd(p['cost'])} · {p['tokens']:,} tok")
    return "\n".join(lines)
