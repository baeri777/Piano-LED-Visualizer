"""MIDI-Wiedergabe (optional): spielt eine Datei an das Piano und beleuchtet die Tasten."""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)


class Player:
    def __init__(self, songs_dir: str, send: Callable, on_event: Callable[[tuple], None], on_state: Callable[[bool], None]):
        self.songs_dir = songs_dir
        self.send = send
        self.on_event = on_event
        self.on_state = on_state
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.current: str | None = None
        self.position = 0.0
        self.length = 0.0
        self.light_keys = True
        self.send_to_piano = True

    def list_songs(self) -> list[dict]:
        if not os.path.isdir(self.songs_dir):
            return []
        out = []
        for name in sorted(os.listdir(self.songs_dir)):
            if name.lower().endswith((".mid", ".midi")):
                out.append({"name": name, "size": os.path.getsize(os.path.join(self.songs_dir, name))})
        return out

    def play(self, name: str) -> bool:
        path = os.path.join(self.songs_dir, os.path.basename(name))
        if not os.path.isfile(path):
            return False
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(path,), name="midi-player", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2)
        self._thread = None

    def _run(self, path: str) -> None:
        import mido
        try:
            mid = mido.MidiFile(path)
        except Exception as exc:
            log.error("MIDI-Datei %s unlesbar: %s", path, exc)
            return
        self.current = os.path.basename(path)
        self.length = mid.length
        self.position = 0.0
        self.on_state(True)
        start = time.monotonic()
        try:
            for msg in mid:  # liefert Nachrichten mit Zeit in Sekunden
                if self._stop.is_set():
                    break
                self.position += msg.time
                target = start + self.position
                while True:
                    remaining = target - time.monotonic()
                    if remaining <= 0 or self._stop.is_set():
                        break
                    time.sleep(min(remaining, 0.05))
                if self._stop.is_set():
                    break
                if msg.is_meta:
                    continue
                if self.send_to_piano:
                    self.send(msg)
                if self.light_keys:
                    if msg.type == "note_on":
                        self.on_event(("note_on", msg.note, msg.velocity, msg.channel, "player"))
                    elif msg.type == "note_off":
                        self.on_event(("note_off", msg.note, msg.channel, "player"))
                    elif msg.type == "control_change":
                        self.on_event(("cc", msg.control, msg.value, msg.channel))
        finally:
            if self.send_to_piano:
                for ch in range(16):
                    self.send(mido.Message("control_change", control=123, value=0, channel=ch))
            self.on_event(("cc", 123, 0, 0))
            self.current = None
            self.position = 0.0
            self.on_state(False)

    def status(self) -> dict:
        return {"playing": self.current is not None, "song": self.current,
                "position": round(self.position, 1), "length": round(self.length, 1)}
