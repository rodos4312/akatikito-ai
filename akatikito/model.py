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
        from urllib.parse import urlsplit

        host = (urlsplit(config.base_url or "").hostname or "").lower()
        effective_key = config.api_key or ("local" if host in ("127.0.0.1", "localhost", "::1") else "")
        if not effective_key:
            raise ModelError(
                "Λείπει το API key. Βάλε AKATIKITO_API_KEY ή διάλεξε κλειδί στο παράθυρο."
            )
        from openai import OpenAI  # τοπικό import: δεν φορτώνεται άδικα

        from .model_guards import EmptyResponseTracker, RateLimitCooldown

        self.config = config
        self._active_stream = None
        self._client = OpenAI(base_url=config.base_url, api_key=effective_key, max_retries=0)
        self._cooldown = RateLimitCooldown()
        self._empty_guard = EmptyResponseTracker()

    @property
    def preserves_tool_reasoning(self) -> bool:
        # GLM requires its exact interleaved reasoning with tool results.
        return "glm" in self.config.model.lower()

    def _load_model_limits(self) -> tuple[int, int]:
        from .model_limits import resolve_cached_limits

        identity = (self.config.base_url, self.config.model)
        if getattr(self, "_limits_identity", None) != identity:
            self._limits_identity = identity
            base = self.config.base_url or ""
            key = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
            cache: dict = {}
            should_fetch = "nano-gpt.com" in base or "openrouter.ai" in base
            if should_fetch:
                try:
                    list_models(base, self.config.api_key, timeout=8)
                except Exception:
                    pass  # An unavailable catalogue never prevents use of a configured model.
            try:
                cp = _models_cache_path()
                if cp.is_file():
                    cache = json.loads(cp.read_text(encoding="utf-8") or "{}")
            except (OSError, json.JSONDecodeError):
                cache = {}
            limits = resolve_cached_limits(cache, key, self.config.model)
            self._context_window_tokens = limits.context_tokens
            self._max_output_tokens = limits.max_output_tokens
        return self._context_window_tokens, getattr(self, "_max_output_tokens", 0)

    @property
    def context_window_tokens(self) -> int:
        return self._load_model_limits()[0]

    @property
    def max_output_tokens(self) -> int:
        return self._load_model_limits()[1]

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

    def _get_fallback_client(self, verdict):
        if not verdict.fallback:
            return None
        from .model_retry import build_fallback_config

        fb_cfg = build_fallback_config(self.config)
        if fb_cfg is None:
            return None
        identity = (fb_cfg.base_url, fb_cfg.model, fb_cfg.api_key)
        fb_client = getattr(self, "_fallback_model", None)
        cached_id = getattr(self, "_fallback_identity", None)
        if fb_client is None or (cached_id is not None and cached_id != identity):
            fb_client = OpenAIModel(fb_cfg)
            self._fallback_model = fb_client
            self._fallback_identity = identity
        on_fb = getattr(self, "on_fallback", None)
        if callable(on_fb):
            try:
                on_fb(fb_cfg.model, verdict.message)
            except Exception:
                pass
        return fb_client

    def _try_fallback_chat(self, messages, tools, cache_key, verdict):
        fb_client = self._get_fallback_client(verdict)
        if fb_client is None:
            return None
        return fb_client.chat(messages, tools=tools, cache_key=cache_key)

    _supports_reasoning_stream = True

    def _try_fallback_stream(self, messages, tools, cache_key, on_delta, should_stop, verdict, on_reasoning=None):
        fb_client = self._get_fallback_client(verdict)
        if fb_client is None:
            return None
        kw = {"tools": tools, "cache_key": cache_key, "on_delta": on_delta, "should_stop": should_stop}
        if on_reasoning is not None and getattr(fb_client, "_supports_reasoning_stream", False):
            kw["on_reasoning"] = on_reasoning
        return fb_client.chat_stream(messages, **kw)

    def _maybe_arm_cooldown(self, exc: Exception | None) -> None:
        from .model_retry import extract_retry_after, extract_status

        cd = getattr(self, "_cooldown", None)
        if cd is not None and extract_status(exc) == 429:
            cd.arm(extract_retry_after(exc))

    def _cooldown_verdict(self):
        from .model_retry import Verdict

        cd = getattr(self, "_cooldown", None)
        if cd is None or not cd.active():
            return None
        rem = int(cd.remaining())
        return Verdict(False, True, message=f"Ο κύριος πάροχος είναι σε cooldown ({rem}s μετά από 429)· χρήση fallback.")

    def _check_empty_and_fallback(self, reply: dict, usage: dict, fallback_fn):
        cd = getattr(self, "_cooldown", None)
        if cd is not None:
            cd.reset()
        eg = getattr(self, "_empty_guard", None)
        identity = (str(self.config.base_url or ""), str(self.config.model or ""))
        if eg is not None and eg.record(identity, reply, usage):
            from .model_retry import Verdict

            fb = fallback_fn(Verdict(False, True, message="Συνεχόμενες κενές απαντήσεις από τον κύριο πάροχο· χρήση fallback."))
            if fb is not None:
                eg.reset()
                return fb
        return reply, usage

    def chat(
        self, messages: list[dict], tools: list[dict] | None = None, cache_key: str | None = None
    ) -> tuple[dict, dict]:
        from .model_retry import Verdict, classify, extract_retry_after, extract_status

        cd_verdict = self._cooldown_verdict()
        if cd_verdict is not None:
            fb_cd = self._try_fallback_chat(messages, tools, cache_key, cd_verdict)
            if fb_cd is not None:
                return fb_cd

        kwargs = self._kwargs(messages, tools, cache_key)
        last_exc: Exception | None = None
        last_verdict = Verdict(False, False)
        max_attempts = max(1, int(self.config.model_retry_attempts))
        for attempt in range(1, max_attempts + 1):
            try:
                response = self._client.chat.completions.create(**kwargs)
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                last_verdict = classify(
                    extract_status(exc),
                    exc,
                    attempt,
                    max_attempts=max_attempts,
                    base_delay=self.config.model_retry_delay,
                    backoff=self.config.model_retry_backoff,
                    retry_after=extract_retry_after(exc),
                )
                if not last_verdict.retry:
                    break
                if last_verdict.wait > 0:
                    time.sleep(last_verdict.wait)

        if last_exc is not None:
            fb_result = self._try_fallback_chat(messages, tools, cache_key, last_verdict)
            if fb_result is not None:
                self._maybe_arm_cooldown(last_exc)
                return fb_result
            prefix = f"{last_verdict.message} " if last_verdict.message else ""
            raise ModelError(prefix + self._format_error(last_exc)) from last_exc
        choice = response.choices[0].message
        reasoning = self._reasoning_of(choice)
        calls = [
            {"id": call.id, "name": call.function.name, "arguments": call.function.arguments}
            for call in (choice.tool_calls or [])
        ]
        # Ενιαίο token accounting με το streaming (usage_of)· None → μηδενικά.
        reply = {"content": choice.content or "", "tool_calls": calls, "reasoning": reasoning, **self._protocol_payload(choice)}
        usage = usage_of(response.usage)
        return self._check_empty_and_fallback(reply, usage, lambda v: self._try_fallback_chat(messages, tools, cache_key, v))

    def cancel_stream(self):
        stream = getattr(self, "_active_stream", None)
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass  # The reader reports cancellation; never retry a partially read stream.

    def _handle_stream_failure(
        self, exc, received, attempt, attempts, messages, tools, cache_key, on_delta, should_stop, on_reasoning=None
    ):
        from .model_retry import classify, extract_retry_after, extract_status

        if received:
            detail = describe_error(exc)
            cause = exc.__cause__
            if cause is not None:
                detail += f" ({type(cause).__name__}: {cause})"
            raise ModelError("Η ροή διακόπηκε πριν ολοκληρωθεί: " + detail) from exc
        if isinstance(exc, StreamAssemblyError):
            raise ModelError(self._format_error(exc)) from exc
        verdict = classify(
            extract_status(exc),
            exc,
            attempt + 1,
            max_attempts=attempts,
            base_delay=self.config.model_retry_delay,
            backoff=self.config.model_retry_backoff,
            retry_after=extract_retry_after(exc),
        )
        if verdict.retry:
            if verdict.wait > 0:
                time.sleep(verdict.wait)
            return None
        fb_result = self._try_fallback_stream(
            messages, tools, cache_key, on_delta, should_stop, verdict, on_reasoning=on_reasoning
        )
        if fb_result is not None:
            self._maybe_arm_cooldown(exc)
            return fb_result
        prefix = f"{verdict.message} " if verdict.message else ""
        raise ModelError(prefix + self._format_error(exc)) from exc

    @staticmethod
    def _consume_stream(stream, assembled, on_delta, on_reasoning, should_stop, received_flag):
        for chunk in stream:
            received_flag[0] = True
            if should_stop():
                raise ModelStopped("Η ροή σταμάτησε από τον χρήστη.")
            text = assembled.feed(chunk)
            if assembled.last_reasoning and on_reasoning:
                on_reasoning(assembled.last_reasoning)
            if text and on_delta:
                on_delta(text)

    def chat_stream(self, messages, tools=None, cache_key=None, on_delta=None, should_stop=None, on_reasoning=None):
        """One streamed completion. Retry only before any response chunk was received."""
        should_stop = should_stop or (lambda: False)
        cd_verdict = self._cooldown_verdict()
        if cd_verdict is not None:
            fb_cd = self._try_fallback_stream(
                messages, tools, cache_key, on_delta, should_stop, cd_verdict, on_reasoning=on_reasoning
            )
            if fb_cd is not None:
                return fb_cd
        kwargs = self._kwargs(messages, tools, cache_key)
        kwargs.update(stream=True, stream_options={"include_usage": True},
                      timeout=max(1.0, float(self.config.model_stream_timeout)))
        attempts = max(1, self.config.model_retry_attempts)
        for attempt in range(attempts):
            stream = None
            received_flag = [False]
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
                self._consume_stream(stream, assembled, on_delta, on_reasoning, should_stop, received_flag)
                if should_stop():
                    raise ModelStopped("Η ροή σταμάτησε από τον χρήστη.")
                return self._check_empty_and_fallback(
                    assembled.reply(),
                    assembled.usage,
                    lambda v: self._try_fallback_stream(
                        messages, tools, cache_key, on_delta, should_stop, v, on_reasoning=on_reasoning
                    ),
                )
            except ModelStopped:
                raise
            except Exception as exc:
                received = received_flag[0]
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
                fb_res = self._handle_stream_failure(
                    exc, received, attempt, attempts, messages, tools, cache_key, on_delta, should_stop, on_reasoning=on_reasoning
                )
                if fb_res is not None:
                    return fb_res
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


def _fetch_provider_models(base_url: str, api_key: str, timeout: float, nano: bool) -> list[dict]:
    from .model_limits import extract_item_limits

    if nano:
        raw = _fetch_detailed(base_url, api_key, timeout)
        return [
            {
                "id": str(item.get("id") or ""),
                "included": bool((item.get("subscription") or {}).get("included")),
                "available": item.get("available") is not False,
                "context_length": extract_item_limits(item)[0],
                "max_output_tokens": extract_item_limits(item)[1],
            }
            for item in raw
            if item.get("id")
        ]
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key or "not-needed", timeout=timeout)
    response = client.models.list()
    models = []
    for item in response.data:
        mid = getattr(item, "id", "")
        if not mid:
            continue
        ctx_len, max_out = extract_item_limits(item)
        row = {"id": mid, "included": True, "available": True}
        if ctx_len:
            row["context_length"] = ctx_len
        if max_out:
            row["max_output_tokens"] = max_out
        models.append(row)
    return models


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
        and entry.get("schema") == 3
    )
    if force or not fresh:
        models = _fetch_provider_models(base_url, api_key, timeout, nano)
        if isinstance(cache, dict):
            cache[key] = {"at": time.time(), "models": models, "detailed": nano, "schema": 3}
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
