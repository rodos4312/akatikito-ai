"""Ζωντανά γεγονότα: ό,τι συμβαίνει στέλνεται σε όποιον ακούει (το παράθυρο-chat)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Event:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)


class Events:
    """Απλός πομπός: γράφεσαι με subscribe και δέχεσαι κάθε γεγονός (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._listeners: list[Callable[[Event], None]] = []
        self.log: list[Event] = []

    def subscribe(self, listener: Callable[[Event], None]) -> Callable[[Event], None]:
        with self._lock:
            self._listeners.append(listener)
        return listener

    def emit(self, kind: str, **data: Any) -> Event:
        event = Event(kind=kind, data=data)
        with self._lock:
            self.log.append(event)
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                # Ένας χαλασμένος ακροατής δεν σταματάει τον βοηθό.
                pass
        return event

    def unsubscribe(self, listener: Callable[[Event], None]) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)
