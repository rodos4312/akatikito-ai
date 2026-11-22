"""Μοντέλο: μιλάει με κάθε OpenAI-compatible υπηρεσία (DeepSeek, OpenRouter, Ollama)."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .config import Config
from .streaming import StreamReply, StreamAssemblyError, usage_of


# Status codes που αξίζουν retry (προσωρινό πρόβλημα)
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
_RETRYABLE_ERRORS = ("Connection", "Timeout", "ServiceUnavailable", "InternalServer")


class ModelError(RuntimeError):
    pass


class ModelStopped(ModelError):
    pass


def _provider_detail(exc: Exception) -> str:
    """Το πραγματικό μήνυμα του παρόχου (π.χ. 'Insufficient balance') αν το στέλνει."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        for key in ("error", "message", "detail"):
            value = body.get(key)
            if isinstance(value, dict):
                value = value.get("message") or value.get("type") or value
            if value and isinstance(value, str):
                return value
    return ""


def describe_error(exc: Exception) -> str:
    name = type(exc).__name__
    detail = _provider_detail(exc)
    return f"{name}: {exc}" + (f" — {detail}" if detail else "")


class OpenAIModel:
    # Active source continuity is independent of whether a model emits reasoning.
    keeps_active_history = True
    requires_completion_evidence = True

    def __init__(self, config: Config) -> None:
        if not config.api_key:
            raise ModelError(
                "Λείπει το API key. Βάλε AKATIKITO_API_KEY ή διάλεξε κλειδί στο παράθυρο."
            )
        from openai import OpenAI  # τοπικό import: δεν φορτώνεται άδικα

        self.config = config
        self._active_stream = None
        self._client = OpenAI(base_url=config.base_url, api_key=config.api_key, max_retries=0)

    @property
    def preserves_tool_reasoning(self) -> bool:
        # GLM requires its exact interleaved reasoning with tool results.
        return "glm" in self.config.model.lower()

    @property
    def context_window_tokens(self) -> int:
        identity = (self.config.base_url, self.config.model)
        if getattr(self,"_limits_identity",None) != identity:
            self._limits_identity = identity
            self._context_window_tokens = 0
            if "nano-gpt.com" in (self.config.base_url or ""):
                try:
                    list_models(self.config.base_url,self.config.api_key,timeout=8)
                    cache=json.loads(_models_cache_path().read_text(encoding="utf-8"))
                    key=hashlib.sha1(self.config.base_url.encode("utf-8")).hexdigest()[:12]
                    item=next((m for m in cache.get(key,{}).get("models",[]) if m.get("id")==self.config.model),{})
                    self._context_window_tokens=_positive_limit(item.get("context_length"))
                except Exception:
                    pass  # An unavailable catalogue never prevents use of a configured model.
        return self._context_window_tokens

    def _request_messages(self, messages):
        projected = copy.deepcopy(messages)
        for message in projected:
            if message.get("role") != "assistant" or not self.preserves_tool_reasoning:
                for key in ("reasoning_content", "reasoning", "reasoning_details"):
                    message.pop(key, None)
        return projected

    @staticmethod
    def _protocol_payload(choice):
        # Do not strip, rewrite, summarize or join signed/opaque protocol fields.
        extra = getattr(choice, "model_extra", None) or {}
        if not isinstance(extra, dict):
            extra = {}
        raw = getattr(choice, "reasoning_content", None)
        if raw is None:
            raw = extra.get("reasoning_content")
        if raw is None:
            raw = getattr(choice, "reasoning", None)
        if raw is None:
            raw = extra.get("reasoning")
        payload = {"reasoning_content":raw} if isinstance(raw,str) else {}
        details = getattr(choice, "reasoning_details", None)
        if details is None:
            details = extra.get("reasoning_details")
        if isinstance(details,list):
            payload["reasoning_details"] = copy.deepcopy(details)
        return payload

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Κρίνει αν το σφάλμα αξίζει να ξαναπροσπαθήσουμε."""
        status = getattr(exc, "status_code", None)
        if status is None:
            # Το OpenAI SDK βάζει το status σε nested response.
            body = getattr(exc, "response", None)
            if body is not None:
                status = getattr(body, "status_code", None)
        if status == 429:
            detail = (_provider_detail(exc) + " " + str(exc)).casefold()
            hard_limits = ("weekly included input token limit exceeded", "daily included input token limit exceeded",
                           "monthly included input token limit exceeded", "insufficient_quota", "quota_exceeded",
                           "insufficient balance")
            if any(marker in detail for marker in hard_limits):
                return False
        if isinstance(status, int) and status in _RETRYABLE_STATUS:
            return True
        name = type(exc).__name__
        if any(word in name for word in _RETRYABLE_ERRORS):
            return True
        return False

    def _format_error(self, exc: Exception) -> str:
        """Φτιάχνει το μήνυμα σφάλματος με συμβουλές ανάλογα με την περίπτωση."""
        message = describe_error(exc)
        status = getattr(exc, "status_code", None)
        if "Connection" in type(exc).__name__:
            message += (
                f" — Δεν έφτασα στη διεύθυνση {self.config.base_url}. "
                "Έλεγξε internet/πληκτρολόγησε σωστή διεύθυνση και ξαναδοκίμασε."
            )
        if status == 402 and "nano-gpt.com" in (self.config.base_url or ""):
            message += (
                " — Το κλειδί σου χρεώνει στο υπόλοιπο, όχι στη συνδρομή. "
                "Άνοιξε nano-gpt.com/api → το κλειδί → Billing → «Subscription first»."
            )
        return message

    def _kwargs(self, messages, tools, cache_key):
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": self._request_messages(messages),
            "tools": tools or None,
            "tool_choice": "auto" if tools else None,
            "temperature": self.config.temperature,
        }
        # Το prompt_cache_key το καταλαβαίνει μόνο το OpenAI API· αλλού (DeepSeek κ.ά.)
        # το caching είναι αυτόματο και θα απέρριπτε το άγνωστο πεδίο.
        if cache_key and "openai.com" in (self.config.base_url or ""):
            kwargs["extra_body"] = {"prompt_cache_key": cache_key}

        if self.preserves_tool_reasoning:
            body = kwargs.setdefault("extra_body", {})
            body["thinking"] = {"type":"enabled", "clear_thinking":False}
            if "nano-gpt.com" in (self.config.base_url or ""):
                body["reasoning_content_compat"] = True

        return kwargs

    def chat(
        self, messages: list[dict], tools: list[dict] | None = None, cache_key: str | None = None
    ) -> tuple[dict, dict]:
        kwargs = self._kwargs(messages, tools, cache_key)

        last_exc: Exception | None = None
        delay = self.config.model_retry_delay
        for attempt in range(1, self.config.model_retry_attempts + 1):
            try:
                response = self._client.chat.completions.create(**kwargs)
                last_exc = None  # Η επιτυχία ακυρώνει το προηγούμενο προσωρινό σφάλμα.
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt >= self.config.model_retry_attempts or not self._is_retryable(exc):
                    break
                time.sleep(delay)
                delay = min(delay * self.config.model_retry_backoff, 30.0)
        else:
            # Αν δεν έγινε break ποτέ (δεν περιμένουμε να φτάσουμε εδώ, αλλά για ασφάλεια).
            exc = last_exc or RuntimeError("άγνωστο σφάλμα μοντέλου")
            raise ModelError(self._format_error(exc)) from exc

        if last_exc is not None:
            exc = last_exc
            raise ModelError(self._format_error(exc)) from exc
        choice = response.choices[0].message
        reasoning = self._reasoning_of(choice)
        calls = []
        for call in (choice.tool_calls or []):
            calls.append(
                {
                    "id": call.id,
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                }
            )
        # Ενιαίο token accounting με το streaming (usage_of)· None → μηδενικά.
        usage = usage_of(response.usage)
        return {"content": choice.content or "", "tool_calls": calls, "reasoning": reasoning, **self._protocol_payload(choice)}, usage

    def cancel_stream(self):
        stream = getattr(self, "_active_stream", None)
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass  # The reader reports cancellation; never retry a partially read stream.

    def chat_stream(self, messages, tools=None, cache_key=None, on_delta=None, should_stop=None):
        """One streamed completion. Retry only before any response chunk was received."""
        should_stop = should_stop or (lambda: False)
        kwargs = self._kwargs(messages, tools, cache_key)
        kwargs.update(stream=True, stream_options={"include_usage": True},
                      timeout=max(1.0, float(self.config.model_stream_timeout)))
        delay = self.config.model_retry_delay
        attempts = max(1, self.config.model_retry_attempts)
        for attempt in range(attempts):
            stream = None
            received = False
            try:
                if should_stop():
                    raise ModelStopped("Η ροή σταμάτησε από τον χρήστη.")
                stream = self._client.chat.completions.create(**kwargs)
                self._active_stream = stream
                if should_stop():
                    raise ModelStopped("Η ροή σταμάτησε από τον χρήστη.")
                # Explicitly rejected streaming may fall back within the SAME retry budget.
                if not kwargs.get("stream"):
                    choice = stream.choices[0].message
                    calls = [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
                             for c in (choice.tool_calls or [])]
                    return {"content": choice.content or "", "tool_calls": calls,
                            "reasoning": self._reasoning_of(choice), **self._protocol_payload(choice)}, usage_of(stream.usage)
                assembled = StreamReply()
                for chunk in stream:
                    received = True
                    if should_stop():
                        raise ModelStopped("Η ροή σταμάτησε από τον χρήστη.")
                    text = assembled.feed(chunk)
                    if text and on_delta:
                        on_delta(text)
                if should_stop():
                    raise ModelStopped("Η ροή σταμάτησε από τον χρήστη.")
                return assembled.reply(), assembled.usage
            except ModelStopped:
                raise
            except Exception as exc:
                if should_stop():
                    raise ModelStopped("Η ροή σταμάτησε από τον χρήστη.") from exc
                status = getattr(exc, "status_code", None)
                detail = (_provider_detail(exc) + " " + str(exc)).casefold()
                rejected_option = not received and status in (400, 422) and any(
                    word in detail for word in ("unsupported", "not supported", "unknown", "unrecognized", "not permitted"))
                if rejected_option and attempt + 1 < attempts:
                    if "stream_options" in detail and "stream_options" in kwargs:
                        kwargs.pop("stream_options")
                        continue
                    if "stream" in detail and kwargs.get("stream"):
                        kwargs.pop("stream"); kwargs.pop("stream_options", None)
                        continue
                if received or isinstance(exc, StreamAssemblyError) or attempt + 1 >= attempts or not self._is_retryable(exc):
                    if received:
                        detail = describe_error(exc)
                        cause = exc.__cause__
                        if cause is not None:
                            detail += f" ({type(cause).__name__}: {cause})"
                        raise ModelError("Η ροή διακόπηκε πριν ολοκληρωθεί: " + detail) from exc
                    raise ModelError(self._format_error(exc)) from exc
                time.sleep(delay)
                delay = min(delay * self.config.model_retry_backoff, 30.0)
            finally:
                self._active_stream = None
                if stream is not None and callable(getattr(stream, "close", None)):
                    try:
                        stream.close()
                    except Exception:
                        pass
        raise ModelError("Δεν ολοκληρώθηκε η κλήση μοντέλου.")

    @staticmethod
    def _reasoning_of(choice: Any) -> str:
        """Η «σκέψη» του μοντέλου, όταν τη δίνει (π.χ. deepseek-reasoner, GLM, nano-gpt)."""
        raw = getattr(choice, "reasoning_content", None)
        if raw is None:
            raw = getattr(choice, "reasoning", None)
        if raw is None:
            extra = getattr(choice, "model_extra", None) or {}
            if isinstance(extra, dict):
                raw = extra.get("reasoning_content") or extra.get("reasoning")
        return str(raw or "").strip()


class ScriptedModel:
    """Για δοκιμές χωρίς δίκτυο: απαντάει με προκαθορισμένη σειρά."""

    def __init__(self, script: list[dict]) -> None:
        self.script = list(script)
        self.calls = 0
        self.seen: list[list[dict]] = []

    def chat(
        self, messages: list[dict], tools: list[dict] | None = None, cache_key: str | None = None
    ) -> tuple[dict, dict]:
        self.seen.append(list(messages))
        step = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        reply = {
            "content": step.get("content", ""),
            "reasoning": step.get("reasoning", ""),
            "tool_calls": [
                {"id": f"call_{i}", "name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}
                for i, c in enumerate(step.get("tool_calls", []))
            ],
        }
        return reply, {"prompt_tokens": 20, "completion_tokens": 10, "cache_hit_tokens": 0, "cache_miss_tokens": 0}


def tool_call(name: str, **arguments: Any) -> dict:
    """Βοηθητικό για τα scripted σενάρια."""
    return {"name": name, "arguments": arguments}


# --------------------------------------------------------------------------- πάροχοι

PROVIDERS: dict[str, str] = {
    "DeepSeek": "https://api.deepseek.com/v1",
    "Google Gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "Anthropic (Claude)": "https://api.anthropic.com/v1",
    "OpenAI": "https://api.openai.com/v1",
    "OpenRouter": "https://openrouter.ai/api/v1",
    "NanoGPT (συνδρομή)": "https://api.nano-gpt.com/api/subscription/v1",
    "NanoGPT (credits)": "https://api.nano-gpt.com/api/v1",
    "Z.ai (GLM)": "https://api.z.ai/api/paas/v4",
    "Ollama (τοπικό)": "http://localhost:11434/v1",
    "Custom": "",
}

# Έτοιμες προτάσεις μοντέλων ώστε να μη χρειάζεται πληκτρολόγηση.
# Όπου δεν είμαστε σίγουροι για ονόματα, το αφήνουμε κενό: το «Φόρτωση μοντέλων»
# φέρνει την αληθινή λίστα από τον πάροχο.
PROVIDER_MODELS: dict[str, list[str]] = {
    "DeepSeek": ["deepseek-chat", "deepseek-reasoner"],
    "Google Gemini": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
    "Anthropic (Claude)": ["claude-sonnet-4-20250514", "claude-3-7-sonnet-20250219", "claude-3-5-haiku-20241022"],
    "OpenAI": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
    "OpenRouter": [],
    "NanoGPT (συνδρομή)": ["deepseek-ai/DeepSeek-V3.1", "qwen/qwen3.5-32b"],
    "NanoGPT (credits)": [],
    "Z.ai (GLM)": ["glm-4.6", "glm-4.5", "glm-4.5-air"],
    "Ollama (τοπικό)": [],
    "Custom": [],
}

PROVIDER_HINTS: dict[str, str] = {
    "DeepSeek": "deepseek-reasoner δείχνει και τη σκέψη του βήμα-βήμα.",
    "Google Gemini": "Κλειδί από aistudio.google.com (Google AI Studio).",
    "Anthropic (Claude)": "Κλειδί από console.anthropic.com (OpenAI-compatible API).",
    "OpenAI": "Θέλει κλειδί από platform.openai.com.",
    "OpenRouter": "Πάτα «Φόρτωση μοντέλων» για τη λίστα (π.χ. deepseek/deepseek-chat).",
    "NanoGPT (συνδρομή)": "Χρεώνει στη μηνιαία συνδρομή σου. Δείχνω μόνο όσα καλύπτει.",
    "NanoGPT (credits)": "Πληρώνεις με υπόλοιπο (pay-as-you-go) — δείχνει όλα τα μοντέλα.",
    "Z.ai (GLM)": "Κλειδί από z.ai. Αν έχεις πακέτο coding, άλλαξε τη διεύθυνση σε "
    "https://api.z.ai/api/coding/paas/v4 με «Custom».",
    "Ollama (τοπικό)": "Δεν θέλει κλειδί — άφησέ το κενό. Θέλει ανοιχτό ollama serve.",
    "Custom": "Οποιαδήποτε υπηρεσία μιλάει τη γλώσσα του OpenAI (…/v1).",
}


def _positive_limit(value) -> int:
    try:
        result=int(value)
        return result if 0 < result <= 10000000 else 0
    except (ValueError,TypeError,OverflowError):
        return 0


def _models_cache_path() -> Path:
    path = Path(__file__).resolve().parents[1] / "data" / "cache" / "models.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


_MODELS_TTL_SECONDS = 24 * 3600


def _fetch_detailed(base_url: str, api_key: str, timeout: float) -> list[dict]:
    """Λίστα μοντέλων με λεπτομέρειες (χωρίς το openai SDK, για να περάσουμε ?detailed=true)."""
    import urllib.request

    # Ο κατάλογος είναι ο ίδιος για συνδρομή και credits — δείχνει πάντα το api/v1.
    root = "https://api.nano-gpt.com/api/v1" if "nano-gpt.com" in (base_url or "") else base_url.rstrip("/")
    url = root.rstrip("/") + "/models?detailed=true"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    items = payload.get("data") if isinstance(payload, dict) else None
    return items if isinstance(items, list) else []


def list_models(
    base_url: str,
    api_key: str = "",
    timeout: float = 15.0,
    force: bool = False,
    only_subscription: bool = True,
) -> list[str]:
    """Λίστα μοντέλων με τοπική cache 24 ωρών.

    Για το NanoGPT κρατά μόνο όσα καλύπτει η μηνιαία συνδρομή (αυτά που έχουν
    ``subscription.included = true``), γιατί τα υπόλοιπα θέλουν pay-as-you-go
    υπόλοιπο και γυρίζουν 402.
    """
    nano = "nano-gpt.com" in (base_url or "")
    cache_path = _models_cache_path()
    cache: dict = {}
    if cache_path.is_file():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            cache = {}

    key = hashlib.sha1(base_url.encode("utf-8")).hexdigest()[:12]
    entry = cache.get(key) if isinstance(cache, dict) else None
    fresh = (
        isinstance(entry, dict)
        and time.time() - float(entry.get("at") or 0) < _MODELS_TTL_SECONDS
        and bool(entry.get("detailed")) == nano
        and entry.get("schema") == 2
    )
    if force or not fresh:
        if nano:
            raw = _fetch_detailed(base_url, api_key, timeout)
            models = [
                {
                    "id": str(item.get("id") or ""),
                    "included": bool((item.get("subscription") or {}).get("included")),
                    "available": item.get("available") is not False,
                    "context_length": _positive_limit(item.get("context_length")),
                    "max_output_tokens": _positive_limit(item.get("max_output_tokens")),
                }
                for item in raw
                if item.get("id")
            ]
        else:
            from openai import OpenAI

            client = OpenAI(base_url=base_url, api_key=api_key or "not-needed", timeout=timeout)
            response = client.models.list()
            models = [
                {"id": getattr(item, "id", ""), "included": True, "available": True}
                for item in response.data
                if getattr(item, "id", "")
            ]
        if isinstance(cache, dict):
            cache[key] = {"at": time.time(), "models": models, "detailed": nano, "schema":2}
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        entry = cache[key]

    items = [item for item in ((entry or {}).get("models") or []) if isinstance(item, dict)]
    usable = [item for item in items if item.get("available", True)]
    # Φιλτράρουμε μόνο στη διεύθυνση της συνδρομής· στο «credits» θέλεις να βλέπεις όλα.
    if "nano-gpt.com" in (base_url or "") and "/subscription/" in (base_url or "") and only_subscription:
        usable = [item for item in usable if item.get("included")]
    ids = {str(item.get("id")) for item in usable if item.get("id")}
    return sorted(ids, key=lambda name: (_priority(name), name.lower()))


# Τα γνωστά/δυνατά μοντέλα πρώτα στη λίστα — δεν κρύβεται τίποτα, απλώς δεν ψάχνεις
# μέσα σε 280 ονόματα για το deepseek.
_PREFERRED = (
    "deepseek",
    "qwen",
    "glm",
    "claude",
    "gpt-",
    "gemini",
    "kimi",
    "minimax",
    "mistral",
    "llama",
    "grok",
)


def _priority(model_id: str) -> int:
    lower = model_id.lower()
    return 0 if any(word in lower for word in _PREFERRED) else 1
