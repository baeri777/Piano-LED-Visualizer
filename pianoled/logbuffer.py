"""Ringpuffer für die letzten Logzeilen, damit die App das Protokoll ohne journalctl zeigen kann."""
from __future__ import annotations

import collections
import logging
import threading


class LogBuffer(logging.Handler):
    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self._lines: collections.deque[str] = collections.deque(maxlen=capacity)
        self._lock_buf = threading.Lock()
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%d.%m. %H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            return
        with self._lock_buf:
            self._lines.append(line)

    def text(self) -> str:
        with self._lock_buf:
            return "\n".join(self._lines)


buffer = LogBuffer()
