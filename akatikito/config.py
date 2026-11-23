"""Ρυθμίσεις του Akatikito."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    # Μοντέλο (OpenAI-compatible: DeepSeek, OpenRouter, Ollama, ...)
    model: str = os.getenv("AKATIKITO_MODEL", "deepseek-chat")
    fast_provider: str = ""
    fast_model: str = ""   # Προαιρετικό οικονομικό/γρήγορο μοντέλο για απλές εργασίες (Auto routing)
    fast_base_url: str = ""
    fast_api_key: str = ""
    smart_provider: str = ""
    smart_model: str = ""  # Προαιρετικό ισχυρό μοντέλο για δύσκολες/reasoning εργασίες
    smart_base_url: str = ""
    smart_api_key: str = ""
    base_url: str = os.getenv("AKATIKITO_BASE_URL", "https://api.deepseek.com/v1")
    api_key: str = os.getenv("AKATIKITO_API_KEY", "")
    temperature: float = 0.0

    # Όρια ασφαλείας
    max_steps: int = 50
    max_retries: int = 5
    command_timeout: int = 120
    parallel_tool_workers: int = 4  # independent reads only; writes/commands are barriers
    stream_responses: bool = True
    model_stream_timeout: float = 30.0
    max_output_chars: int = 12000

    # Σύνθεση system prompt: μη-περικοπτόμενα τμήματα (κανόνες, προτιμήσεις,
    # mode, runtime) μπαίνουν πάντα ολόκληρα· οι μνήμες κόβονται στο υπόλοιπο budget.
    system_prompt_budget: int = 11500
    memory_excerpt_overhead: int = 220

    # Περιορισμένη προβολή προς το μοντέλο, με πλήρες τοπικό ιστορικό.
    context_economy: bool = True
    context_active_max_bytes: int = 0  # 0: use provider window; unknown providers fall back to 160k bytes
    context_max_chars: int = 36000
    context_recent_groups: int = 4
    repo_map: bool = True
    repo_map_max_chars: int = 6000  # 12000 was measured worse: 1/5 fixes vs 5/5, twice the calls; a fuller map distracted the model
    repo_map_time_limit: float = 1.0
    search_time_limit: float = 2.0  # wall-clock budget for one search_project scan

    # Retry ρυθμίσεις για κλήσεις μοντέλου
    model_retry_attempts: int = 3
    model_retry_delay: float = 1.0
    model_retry_backoff: float = 2.0

    # Αυτόματη επιλογή λειτουργίας (ask / architect / code / debug) από την εργασία
    auto_mode: bool = True

    # Αυτόματος έλεγχος μετά από αλλαγή (π.χ. "python -m pytest -q")
    verify_command: str = ""

    subtask_limit: int = 4
    subtask_max_steps: int = 12
    subtask_token_limit: int = 40000
    supports_vision: bool = False
    checkpoints: bool = False  # Desktop enables it; library callers can opt in.

    # Γλώσσα απαντήσεων & διεπαφής (el, en, ru, de, es, fr)
    language: str = "greek"
    ui_language: str = "el"
