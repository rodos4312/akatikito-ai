"""Dynamic LLM context-window & max-output-token detection across providers.

Resolves real model limits from:
1. Provider catalogue metadata cached in ``data/cache/models.json``
   (OpenRouter, NanoGPT, and any OpenAI-compatible ``/models`` response).
2. A built-in table of well-known model families (DeepSeek, OpenAI, Claude,
   Gemini, Qwen, Grok, Llama, GLM, Mistral) when offline or unreported.

Provides adaptive coding chunk sizing (lines per step) so large-output models
write big cohesive blocks without unnecessary micro-steps while small-output
models stay safely below ``finish_reason=length``.
"""
from __future__ import annotations

from dataclasses import dataclass

# Ordered from most specific to most general substring matches.
_KNOWN_LIMITS: tuple[tuple[str, int, int], ...] = (
    ("gemini-2.5", 1_048_576, 65_536),
    ("gemini-2.0", 1_048_576, 8_192),
    ("gemini-1.5", 1_000_000, 8_192),
    ("gpt-4.1", 1_047_576, 32_768),
    ("gpt-5", 272_000, 32_768),
    ("o3", 200_000, 32_768),
    ("o4-mini", 200_000, 32_768),
    ("gpt-4o", 128_000, 16_384),
    ("claude-opus-4", 200_000, 32_000),
    ("claude-sonnet-4", 200_000, 32_000),
    ("claude-3-7", 200_000, 16_384),
    ("claude-3.7", 200_000, 16_384),
    ("claude-3-5", 200_000, 8_192),
    ("claude-3.5", 200_000, 8_192),
    ("deepseek-reasoner", 128_000, 16_384),
    ("deepseek-r1", 128_000, 16_384),
    ("deepseek-chat", 128_000, 8_192),
    ("deepseek-v3", 128_000, 8_192),
    ("grok-4", 256_000, 16_384),
    ("grok-3", 131_072, 16_384),
    ("qwen3", 131_072, 16_384),
    ("qwen-2.5", 128_000, 8_192),
    ("qwen2.5", 128_000, 8_192),
    ("glm-4", 128_000, 8_192),
    ("llama-3.3", 128_000, 8_192),
    ("llama-3.1", 128_000, 8_192),
    ("codestral", 128_000, 8_192),
    ("mistral", 128_000, 8_192),
)


@dataclass(frozen=True)
class ModelLimits:
    context_tokens: int = 0
    max_output_tokens: int = 0
    source: str = "unknown"


def _positive_int(value) -> int:
    if isinstance(value, bool):
        return 0
    try:
        num = int(value)
        return num if 0 < num <= 10_000_000 else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def extract_item_limits(item) -> tuple[int, int]:
    """Extract (context_length, max_output_tokens) from a dict or OpenAI SDK Model object."""
    if isinstance(item, dict):
        data = item
    else:
        extra = getattr(item, "model_extra", None)
        data = dict(extra) if isinstance(extra, dict) else {}
        for attr in ("context_length", "context_window", "max_output_tokens", "max_completion_tokens", "top_provider"):
            if attr not in data and hasattr(item, attr):
                data[attr] = getattr(item, attr)

    top = data.get("top_provider") if isinstance(data.get("top_provider"), dict) else {}
    ctx = (
        _positive_int(data.get("context_length"))
        or _positive_int(data.get("context_window"))
        or _positive_int(top.get("context_length"))
    )
    out = (
        _positive_int(data.get("max_output_tokens"))
        or _positive_int(data.get("max_completion_tokens"))
        or _positive_int(top.get("max_completion_tokens"))
        or _positive_int(top.get("max_output_tokens"))
    )
    return ctx, out


def lookup_known_limits(model_id: str) -> ModelLimits:
    """Return known (context_tokens, max_output_tokens) for well-known model families."""
    low = (model_id or "").strip().lower()
    if not low:
        return ModelLimits()
    for needle, ctx, out in _KNOWN_LIMITS:
        if needle in low:
            return ModelLimits(context_tokens=ctx, max_output_tokens=out, source="known_family")
    return ModelLimits()


def resolve_cached_limits(cache: dict, key: str, model_id: str) -> ModelLimits:
    """Look up limits from a parsed models.json cache dict, falling back to known families."""
    models = (cache.get(key) or {}).get("models") if isinstance(cache, dict) else None
    ctx, out = 0, 0
    if isinstance(models, list):
        item = next((m for m in models if isinstance(m, dict) and m.get("id") == model_id), None)
        if item is not None:
            ctx, out = extract_item_limits(item)
    known = lookup_known_limits(model_id)
    final_ctx = ctx or known.context_tokens
    final_out = out or known.max_output_tokens
    if ctx or out:
        return ModelLimits(final_ctx, final_out, source="catalogue")
    if final_ctx or final_out:
        return ModelLimits(final_ctx, final_out, source=known.source)
    return ModelLimits()


def recommended_chunk_lines(max_output_tokens: int) -> tuple[int, int]:
    """Return (min_lines, max_lines) ideal per code-write step for a model's output window."""
    out = _positive_int(max_output_tokens)
    if out <= 0:
        return (350, 450)
    if out <= 4_096:
        return (150, 220)
    if out <= 8_192:
        return (350, 480)
    if out <= 16_384:
        return (550, 800)
    return (800, 1_200)


def format_window_hint(context_tokens: int, max_output_tokens: int) -> str:
    """Build a short runtime prompt line telling the coding model its real window budget.

    Only advises chunking when the model actually has a small output limit (<= 8k tokens);
    large-output models are encouraged to complete edits in as few steps as possible.
    """
    ctx = _positive_int(context_tokens)
    out = _positive_int(max_output_tokens)
    if not ctx and not out:
        return ""
    parts = []
    if ctx:
        parts.append(f"context ~{ctx // 1000}k tokens")
    if out:
        parts.append(f"έξοδος ~{out // 1000}k tokens/βήμα")
    if out and out <= 8_192:
        lo, hi = recommended_chunk_lines(out)
        advice = f"Επειδή η έξοδος ανά βήμα είναι περιορισμένη, γράφε ~{lo}–{hi} γραμμές ανά βήμα."
    else:
        advice = "Έχεις μεγάλο περιθώριο εξόδου: γράφε ολόκληρα αρχεία/ενότητες και ομαδοποίησε τις αλλαγές σε ελάχιστα βήματα."
    return f"[ΠΑΡΑΘΥΡΟ ΜΟΝΤΕΛΟΥ: {', '.join(parts)}] {advice}"
