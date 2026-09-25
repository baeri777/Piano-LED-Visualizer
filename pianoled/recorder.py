"""MIDI-Aufnahme (optional). Schreibt gespielte Noten als Standard-MIDI-Datei."""
from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, songs_dir: str):
        self.songs_dir = songs_dir
        self._lock = threading.Lock()
        self._events: list[tuple[float, tuple]] = []
        self.recording = False
        self.started_at = 0.0

    def start(self) -> None:
        with self._lock:
            self._events = []
            self.recording = True
            self.started_at = time.monotonic()

    def feed(self, ev: tuple) -> None:
        if not self.recording:
            return
        if ev[0] in ("note_on", "note_off", "cc"):
            with self._lock:
                self._events.append((time.monotonic() - self.started_at, ev))

    def cancel(self) -> None:
        with self._lock:
            self.recording = False
            self._events = []

    def stop_and_save(self, name: str | None = None) -> str | None:
        import mido
        with self._lock:
            self.recording = False
            events = self._events
            self._events = []
        if not events:
            return None
        ticks_per_beat = 480
        tempo = 500000  # 120 bpm
        mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
        track = mido.MidiTrack()
        mid.tracks.append(track)
        track.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
        last = 0.0
        for t, ev in events:
            delta = int(mido.second2tick(max(0.0, t - last), ticks_per_beat, tempo))
            last = t
            if ev[0] == "note_on":
                track.append(mido.Message("note_on", note=ev[1], velocity=ev[2], channel=ev[3], time=delta))
            elif ev[0] == "note_off":
                track.append(mido.Message("note_off", note=ev[1], velocity=0, channel=ev[2], time=delta))
            elif ev[0] == "cc":
                track.append(mido.Message("control_change", control=ev[1], value=ev[2], channel=ev[3], time=delta))
        safe = "".join(c for c in (name or "") if c.isalnum() or c in " -_").strip()
        filename = (safe or time.strftime("Aufnahme %Y-%m-%d %H-%M-%S")) + ".mid"
        os.makedirs(self.songs_dir, exist_ok=True)
        path = os.path.join(self.songs_dir, filename)
        mid.save(path)
        log.info("Aufnahme gespeichert: %s (%d Ereignisse)", path, len(events))
        return filename

    def status(self) -> dict:
        return {"recording": self.recording,
                "seconds": round(time.monotonic() - self.started_at, 1) if self.recording else 0,
                "events": len(self._events)}
