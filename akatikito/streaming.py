"""Assembly of Chat Completions deltas; never execute partial tool arguments."""
from __future__ import annotations
import copy
import json
import time


def value(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def usage_of(usage):
    prompt = int(value(usage, 'prompt_tokens', 0) or 0)
    completion = int(value(usage, 'completion_tokens', 0) or 0)
    details = value(usage, 'prompt_tokens_details')
    cached = int(
        value(usage, 'prompt_cache_hit_tokens', 0)
        or (value(details, 'cached_tokens', 0) if details is not None else 0)
        or value(usage, 'cache_read_input_tokens', 0)
        or 0
    )
    miss = int(
        value(usage, 'prompt_cache_miss_tokens', 0)
        or value(usage, 'cache_creation_input_tokens', 0)
        or (max(0, prompt - cached) if cached > 0 else 0)
    )
    return {
        'prompt_tokens': prompt,
        'completion_tokens': completion,
        'cache_hit_tokens': cached,
        'cache_miss_tokens': miss,
    }


class StreamAssemblyError(ValueError):
    pass


class DeltaBatcher:
    """Συσσωρεύει τμήματα κειμένου ροής και τα παραδίδει σε δόσεις (backpressure).

    Ο στόχος είναι η ροή να φτάνει στο UI σε ορατά βήματα χωρίς να πλημμυρίζει
    το κανάλι γεγονότων: το πρώτο τμήμα εκπέμπεται αμέσως, μετά μόνο αν έχει
    περάσει το χρονικό όριο ή έχει συσσωρευτεί αρκετό κείμενο.
    """

    def __init__(self, emit, flush_interval: float = 0.05, flush_chars: int = 256):
        self._emit = emit
        self.flush_interval = flush_interval
        self.flush_chars = flush_chars
        self._pending: list[str] = []
        self._last_sent: float | None = None

    def feed(self, text: str) -> None:
        if not text:
            return
        self._pending.append(text)
        if (
            self._last_sent is None
            or time.monotonic() - self._last_sent >= self.flush_interval
            or sum(map(len, self._pending)) >= self.flush_chars
        ):
            self.flush()

    def flush(self) -> None:
        if self._pending:
            self._emit("".join(self._pending))
            self._pending.clear()
            self._last_sent = time.monotonic()


class StreamReply:
    def __init__(self):
        self.content = []
        self.reasoning = []
        self.last_reasoning = ''
        self.has_reasoning = False
        self.details = []
        self.detail_indices = {}
        self.calls = {}
        self.finish = None
        self.usage = usage_of(None)
        self.usage_received = False

    def feed(self, chunk):
        usage = value(chunk, 'usage')
        if usage is not None:
            self.usage = usage_of(usage)
            self.usage_received = True
        content = ''
        reasoning_chunk = ''
        for choice in value(chunk, 'choices', []) or []:
            if value(choice, 'index', 0) != 0:
                continue
            delta = value(choice, 'delta') or {}
            part = value(delta, 'content')
            if isinstance(part, str):
                self.content.append(part)
                content += part
            extra = value(delta, 'model_extra', {}) or {}
            reason = value(delta, 'reasoning_content')
            if reason is None:
                reason = extra.get('reasoning_content')
            if reason is None:
                reason = value(delta, 'reasoning', extra.get('reasoning'))
            if isinstance(reason, str):
                self.has_reasoning = True
                self.reasoning.append(reason)
                reasoning_chunk += reason
            details = value(delta, 'reasoning_details', extra.get('reasoning_details'))
            if isinstance(details, list):
                self._details(details)
            for part_call in value(delta, 'tool_calls', []) or []:
                index = value(part_call, 'index')
                if type(index) is not int or index < 0:
                    raise StreamAssemblyError('Λείπει έγκυρο index στην εντολή εργαλείου.')
                call = self.calls.setdefault(index, {'id': '', 'name': '', 'arguments': ''})
                kind = value(part_call, 'type')
                if kind not in (None, 'function'):
                    raise StreamAssemblyError('Μη υποστηριζόμενος τύπος εργαλείου.')
                fragment = value(part_call, 'id')
                if fragment:
                    call['id'] += fragment
                function = value(part_call, 'function') or {}
                for field, target in (('name', 'name'), ('arguments', 'arguments')):
                    fragment = value(function, field)
                    if fragment is not None:
                        if not isinstance(fragment, str):
                            raise StreamAssemblyError('Μη έγκυρο τμήμα εντολής εργαλείου.')
                        call[target] += fragment
            finish = value(choice, 'finish_reason')
            if finish is not None:
                self.finish = finish
        self.last_reasoning = reasoning_chunk
        return content

    def _details(self, fragments):
        # Assemble indexed string fragments verbatim; preserve all other opaque fields.
        for raw in fragments:
            if not isinstance(raw, dict):
                raise StreamAssemblyError('Μη έγκυρα reasoning_details.')
            item = copy.deepcopy(raw)
            index = item.get('index')
            if index is None:
                self.details.append(item)
                continue
            if type(index) is not int:
                raise StreamAssemblyError('Μη έγκυρο index reasoning_details.')
            if index not in self.detail_indices:
                self.detail_indices[index] = len(self.details)
                self.details.append(item)
                continue
            previous = self.details[self.detail_indices[index]]
            for key, part in item.items():
                if key in ('text', 'data', 'signature') and isinstance(part, str):
                    previous[key] = previous.get(key, '') + part
                elif key not in previous or previous[key] is None:
                    previous[key] = part
                elif previous[key] != part:
                    raise StreamAssemblyError('Ασυνεπή opaque metadata στη ροή reasoning.')

    def reply(self):
        pure_text_truncated = (
            self.finish == 'length'
            and not self.calls
            and bool(''.join(self.content).strip())
        )
        if self.finish not in ('stop', 'tool_calls') and not pure_text_truncated:
            raise StreamAssemblyError('Η ροή δεν ολοκληρώθηκε κανονικά' + (f' ({self.finish})' if self.finish else '') + '.')
        calls = [self.calls[index] for index in sorted(self.calls)]
        ids = set()
        for call in calls:
            if not call['id'] or not call['name'] or call['id'] in ids:
                raise StreamAssemblyError('Ελλιπής ή διπλή ταυτότητα εργαλείου στη ροή.')
            ids.add(call['id'])
            try:
                arguments = json.loads(call['arguments'])
            except (TypeError, ValueError) as exc:
                raise StreamAssemblyError('Η ροή έστειλε ελλιπή JSON ορίσματα εργαλείου.') from exc
            if not isinstance(arguments, dict):
                raise StreamAssemblyError('Τα ορίσματα εργαλείου πρέπει να είναι JSON object.')
        if self.finish == 'tool_calls' and not calls:
            raise StreamAssemblyError('Η ροή τελείωσε χωρίς τις δηλωμένες εντολές εργαλείων.')
        reply = {'content': ''.join(self.content), 'reasoning': ''.join(self.reasoning).strip(), 'tool_calls': calls}
        if pure_text_truncated:
            reply['truncated_text'] = True
            reply['finish_reason'] = 'length'
        if self.has_reasoning:
            reply['reasoning_content'] = ''.join(self.reasoning)
        if self.details:
            reply['reasoning_details'] = copy.deepcopy(self.details)
        return reply
