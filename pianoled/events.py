"""Einfacher, thread-sicherer Event-Bus.

Komponenten veröffentlichen Ereignisse unter einem Thema ("keys", "wifi", "midi" …),
Web-Oberfläche und Display abonnieren sie. Ein fehlerhafter Abonnent kann weder
den Absender noch andere Abonnenten stören.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

log = logging.getLogger(__name__)

Listener = Callable[[str, Any], None]


class EventBus:
    def __init__(self) -> None:
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()

    def subscribe(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def publish(self, topic: str, payload: Any = None) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(topic, payload)
            except Exception:
                log.exception("Abonnent für '%s' fehlgeschlagen", topic)
