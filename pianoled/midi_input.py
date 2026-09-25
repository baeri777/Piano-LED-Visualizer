"""MIDI-Eingang mit Hot-Plug.

Ein Hintergrund-Thread prüft alle `rescan_s` Sekunden die verfügbaren Ports.
Verschwindet der offene Port (Piano aus, USB gezogen), wird er geschlossen und
beim Wiederauftauchen automatisch neu geöffnet. Nachrichten kommen per Callback
(kein Polling) und werden als einfache Tupel an `on_event` übergeben:

  ("note_on", note, velocity, channel)
  ("note_off", note, channel)
  ("cc", control, value, channel)
  ("sysex", bytes)
  ("other", type)
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)


class MidiInput(threading.Thread):
    def __init__(self, midi_cfg: dict, on_event: Callable[[tuple], None], on_status: Callable[[dict], None] | None = None):
        super().__init__(name="midi-input", daemon=True)
        self.cfg = midi_cfg
        self.on_event = on_event
        self.on_status = on_status
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._inport = None
        self._outport = None
        self.port_name: str | None = None
        self.available: list[str] = []
        self.message_count = 0
        self.last_message_time = 0.0
        self.error: str | None = None

    # -- Ports ----------------------------------------------------------
    def _wanted(self, names: list[str]) -> str | None:
        ignore = [s.lower() for s in self.cfg.get("ignore_ports", [])]
        pref = (self.cfg.get("port_filter") or "").strip().lower()
        candidates = [n for n in names if not any(i in n.lower() for i in ignore)]
        if pref:
            for n in candidates:
                if pref in n.lower():
                    return n
            return None
        return candidates[0] if candidates else None

    def _open(self, name: str) -> None:
        import mido
        self._inport = mido.open_input(name, callback=self._callback)
        try:
            outs = mido.get_output_names()
            same = [o for o in outs if o.split(":")[0] == name.split(":")[0]] or [o for o in outs if o == name]
            self._outport = mido.open_output(same[0]) if same else None
        except Exception as exc:
            log.debug("Kein MIDI-Ausgang: %s", exc)
            self._outport = None
        self.port_name = name
        self.error = None
        log.info("MIDI-Eingang geöffnet: %s", name)
        self._status()

    def _close(self) -> None:
        for attr in ("_inport", "_outport"):
            port = getattr(self, attr)
            if port is not None:
                try:
                    port.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        if self.port_name:
            log.info("MIDI-Eingang geschlossen: %s", self.port_name)
        self.port_name = None
        self._status()

    def _status(self) -> None:
        if self.on_status:
            try:
                self.on_status(self.status())
            except Exception:
                log.exception("MIDI-Status-Callback fehlgeschlagen")

    def status(self) -> dict:
        return {
            "connected": self.port_name is not None,
            "port": self.port_name,
            "available": list(self.available),
            "messages": self.message_count,
            "last_message_age_s": round(time.monotonic() - self.last_message_time, 1) if self.last_message_time else None,
            "error": self.error,
        }

    # -- Callback (rtmidi-Thread) --------------------------------------
    def _callback(self, msg) -> None:
        self.message_count += 1
        self.last_message_time = time.monotonic()
        t = msg.type
        try:
            if t == "note_on":
                if msg.channel in self.cfg.get("ignore_channels", []):
                    return
                self.on_event(("note_on", msg.note, msg.velocity, msg.channel))
            elif t == "note_off":
                if msg.channel in self.cfg.get("ignore_channels", []):
                    return
                self.on_event(("note_off", msg.note, msg.channel))
            elif t == "control_change":
                self.on_event(("cc", msg.control, msg.value, msg.channel))
            elif t == "sysex":
                self.on_event(("sysex", bytes(msg.data)))
            elif t in ("clock", "active_sensing"):
                return
            else:
                self.on_event(("other", t))
        except Exception:
            log.exception("Fehler im MIDI-Callback")

    # -- Ausgang --------------------------------------------------------
    def send(self, msg) -> bool:
        port = self._outport
        if port is None:
            return False
        try:
            port.send(msg)
            return True
        except Exception as exc:
            log.warning("MIDI senden fehlgeschlagen: %s", exc)
            return False

    def set_port_filter(self, value: str) -> None:
        self.cfg["port_filter"] = value or ""
        with self._lock:
            self._close()

    # -- Thread ---------------------------------------------------------
    def run(self) -> None:
        try:
            import mido
            mido.set_backend("mido.backends.rtmidi")
        except Exception as exc:
            self.error = f"mido/rtmidi nicht verfügbar: {exc}"
            log.error(self.error)
            return
        interval = float(self.cfg.get("rescan_s", 2.0))
        while not self._stop.is_set():
            try:
                names = mido.get_input_names()
                self.available = names
                with self._lock:
                    if self.port_name is not None and self.port_name not in names:
                        log.warning("MIDI-Port verschwunden: %s", self.port_name)
                        self._close()
                    if self.port_name is None:
                        wanted = self._wanted(names)
                        if wanted:
                            try:
                                self._open(wanted)
                            except Exception as exc:
                                self.error = str(exc)
                                log.warning("MIDI-Port %s konnte nicht geöffnet werden: %s", wanted, exc)
            except Exception as exc:
                self.error = str(exc)
                log.debug("MIDI-Scan fehlgeschlagen: %s", exc)
            self._stop.wait(interval)
        with self._lock:
            self._close()

    def stop(self) -> None:
        self._stop.set()
