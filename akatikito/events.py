"""Ζωντανά γεγονότα: ό,τι συμβαίνει στέλνεται σε όποιον ακούει (το παράθυρο-chat)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Event:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)


class Events:
    """Απλός πομπός: γράφεσαι με subscribe και δέχεσαι κάθε γεγονός."""

    def __init__(self) -> None:
        self._listeners: list[Callable[[Event], None]] = []
        self.log: list[Event] = []

    def subscribe(self, listener: Callable[[Event], None]) -> Callable[[Event], None]:
        self._listeners.append(listener)
        return listener

    def emit(self, kind: str, **data: Any) -> Event:
        event = Event(kind=kind, data=data)
        self.log.append(event)
        # Αντιγράφουμε τη λίστα γιατί ένας listener μπορεί να αλλάξει τη λίστα.
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                # Ένας χαλασμένος ακροατής δεν σταματάει τον βοηθό.
                pass
        return event

    def unsubscribe(self, listener: Callable[[Event], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)
