"""Supervisor: hält Hintergrund-Komponenten am Leben.

Jede Komponente ist ein Thread mit `start()`, `stop()` und `is_alive()`. Stirbt ein
Thread (unerwartete Ausnahme), erzeugt der Supervisor über die registrierte Fabrik
eine neue Instanz und startet sie – mit wachsender Wartezeit, damit ein dauerhaft
defektes Teil (z. B. Display abgezogen) nicht in einer Endlosschleife hängt.

Hängt dagegen der ganze Prozess, greift der systemd-Watchdog (siehe __main__.py).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)


@dataclass
class _Component:
    name: str
    factory: Callable[[], threading.Thread]
    instance: threading.Thread | None = None
    restarts: int = 0
    next_attempt: float = 0.0
    last_error: str | None = None
    started_at: float = field(default_factory=time.monotonic)


class Supervisor(threading.Thread):
    CHECK_INTERVAL_S = 2.0
    MAX_BACKOFF_S = 120.0
    STABLE_AFTER_S = 300.0   # nach so langer fehlerfreier Laufzeit wird der Zähler zurückgesetzt

    def __init__(self, on_restart: Callable[[str, int], None] | None = None) -> None:
        super().__init__(name="supervisor", daemon=True)
        self._components: dict[str, _Component] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._on_restart = on_restart

    # -- Verwaltung -----------------------------------------------------
    def add(self, name: str, factory: Callable[[], threading.Thread]) -> threading.Thread | None:
        comp = _Component(name, factory)
        with self._lock:
            self._components[name] = comp
        self._launch(comp)
        return comp.instance

    def get(self, name: str):
        comp = self._components.get(name)
        return comp.instance if comp else None

    def status(self) -> dict:
        with self._lock:
            return {
                name: {
                    "alive": bool(c.instance and c.instance.is_alive()),
                    "restarts": c.restarts,
                    "error": c.last_error,
                }
                for name, c in self._components.items()
            }

    def _launch(self, comp: _Component) -> None:
        try:
            comp.instance = comp.factory()
            comp.instance.start()
            comp.started_at = time.monotonic()
        except Exception as exc:
            comp.instance = None
            comp.last_error = str(exc)
            log.error("Komponente '%s' konnte nicht gestartet werden: %s", comp.name, exc)

    # -- Überwachung ------------------------------------------------------
    def check_once(self) -> None:
        now = time.monotonic()
        with self._lock:
            components = list(self._components.values())
        for comp in components:
            alive = comp.instance is not None and comp.instance.is_alive()
            if alive:
                if comp.restarts and now - comp.started_at > self.STABLE_AFTER_S:
                    comp.restarts = 0
                continue
            if now < comp.next_attempt:
                continue
            comp.restarts += 1
            backoff = min(self.MAX_BACKOFF_S, 2.0 ** min(comp.restarts, 7))
            comp.next_attempt = now + backoff
            log.error("Komponente '%s' ist ausgefallen, Neustart Nr. %d", comp.name, comp.restarts)
            self._launch(comp)
            if self._on_restart:
                try:
                    self._on_restart(comp.name, comp.restarts)
                except Exception:
                    log.exception("Restart-Callback fehlgeschlagen")

    def run(self) -> None:
        while not self._stop.wait(self.CHECK_INTERVAL_S):
            try:
                self.check_once()
            except Exception:
                log.exception("Supervisor-Fehler")

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            components = list(self._components.values())
        for comp in components:
            inst = comp.instance
            if inst is not None and hasattr(inst, "stop"):
                try:
                    inst.stop()
                except Exception:
                    log.exception("Stoppen von '%s' fehlgeschlagen", comp.name)
