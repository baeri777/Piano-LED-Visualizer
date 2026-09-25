"""MIDI-Eingang mit automatischer Wiederverbindung (Hot-Plug).

Ein Thread schaut alle `rescan_s` Sekunden nach, welche MIDI-Ports existieren.
* Verschwindet der offene Port (Piano aus, USB gezogen), wird er geschlossen und
  `on_disconnect` aufgerufen. Die App schaltet dann alle LEDs aus, denn die
  Note-Off-Nachrichten der gerade gedrückten Tasten kommen nie mehr an.
* Taucht ein passender Port auf, wird er geöffnet.

Nachrichten kommen per Callback im rtmidi-Thread (kein Polling) und werden als
kleine Tupel weitergereicht:

    ("note_on", note, velocity, channel)
    ("note_off", note, channel)
    ("cc", control, value, channel)
    ("sysex", bytes)
    ("other", typ)

Active Sensing (das Roland sendet es alle ~300 ms) wird nicht weitergereicht,
dient aber als Lebenszeichen: `alive` zeigt, ob das Piano gerade sendet.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

SENSING_TIMEOUT_S = 1.5


class MidiInput(threading.Thread):
    def __init__(self, midi_cfg: dict, on_event: Callable[[tuple], None],
                 on_status: Callable[[dict], None] | None = None,
                 on_disconnect: Callable[[], None] | None = None):
        super().__init__(name="midi", daemon=True)
        self.cfg = dict(midi_cfg)
        self.on_event = on_event
        self.on_status = on_status
        self.on_disconnect = on_disconnect
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._inport = None
        self._outport = None
        self.port_name: str | None = None
        self.available: list[str] = []
        self.message_count = 0
        self.last_message = 0.0
        self.last_sensing = 0.0
        self.error: str | None = None

    # -- Ports ----------------------------------------------------------
    def _choose(self, names: list[str]) -> str | None:
        ignore = [s.lower() for s in self.cfg.get("ignore_ports", [])]
        wanted = (self.cfg.get("port_filter") or "").strip().lower()
        candidates = [n for n in names if not any(i in n.lower() for i in ignore)]
        if wanted:
            return next((n for n in candidates if wanted in n.lower()), None)
        return candidates[0] if candidates else None

    def _open(self, name: str) -> None:
        import mido
        self._inport = mido.open_input(name, callback=self._callback)
        self._outport = None
        try:
            device = name.split(":")[0]
            outs = [o for o in mido.get_output_names() if o.split(":")[0] == device]
            if outs:
                self._outport = mido.open_output(outs[0])
        except Exception as exc:
            log.debug("Kein MIDI-Ausgang: %s", exc)
        self.port_name = name
        self.error = None
        log.info("Piano verbunden: %s", name)
        self._notify()

    def _close(self, reason: str = "") -> None:
        was_open = self.port_name is not None
        for attr in ("_inport", "_outport"):
            port = getattr(self, attr)
            setattr(self, attr, None)
            if port is not None:
                try:
                    port.close()
                except Exception:
                    pass
        if was_open:
            log.info("Piano getrennt: %s %s", self.port_name, reason)
            self.port_name = None
            if self.on_disconnect:
                try:
                    self.on_disconnect()
                except Exception:
                    log.exception("on_disconnect fehlgeschlagen")
            self._notify()

    def _notify(self) -> None:
        if self.on_status:
            try:
                self.on_status(self.status())
            except Exception:
                log.exception("MIDI-Status-Callback fehlgeschlagen")

    def status(self) -> dict:
        now = time.monotonic()
        return {
            "connected": self.port_name is not None,
            "port": self.port_name,
            "alive": bool(self.last_sensing and now - self.last_sensing < SENSING_TIMEOUT_S)
                     or bool(self.last_message and now - self.last_message < SENSING_TIMEOUT_S),
            "available": list(self.available),
            "messages": self.message_count,
            "error": self.error,
        }

    # -- Callback (rtmidi-Thread): kurz halten! -------------------------
    def _callback(self, msg) -> None:
        now = time.monotonic()
        t = msg.type
        if t == "active_sensing":
            self.last_sensing = now
            return
        if t == "clock":
            return
        self.message_count += 1
        self.last_message = now
        try:
            if t == "note_on":
                if msg.channel not in self.cfg.get("ignore_channels", ()):
                    self.on_event(("note_on", msg.note, msg.velocity, msg.channel))
            elif t == "note_off":
                if msg.channel not in self.cfg.get("ignore_channels", ()):
                    self.on_event(("note_off", msg.note, msg.channel))
            elif t == "control_change":
                self.on_event(("cc", msg.control, msg.value, msg.channel))
            elif t == "sysex":
                self.on_event(("sysex", bytes(msg.data)))
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

    def update_config(self, midi_cfg: dict) -> None:
        """Neue Einstellungen; bei geändertem Port-Filter neu verbinden."""
        changed = midi_cfg.get("port_filter", "") != self.cfg.get("port_filter", "")
        self.cfg = dict(midi_cfg)
        if changed:
            with self._lock:
                self._close("(Port-Filter geändert)")
            self._wake.set()

    # -- Thread ---------------------------------------------------------
    def run(self) -> None:
        try:
            import mido
            mido.set_backend("mido.backends.rtmidi")
            mido.get_input_names()
        except Exception as exc:
            self.error = f"MIDI nicht verfügbar: {exc}"
            log.error(self.error)
            self._notify()
            self._stop_event.wait()   # nicht sterben, sonst startet der Supervisor im Sekundentakt neu
            return
        while not self._stop_event.is_set():
            try:
                names = mido.get_input_names()
                if names != self.available:
                    self.available = names
                    self._notify()
                with self._lock:
                    if self.port_name is not None and self.port_name not in names:
                        self._close("(Port verschwunden)")
                    if self.port_name is None:
                        wanted = self._choose(names)
                        if wanted:
                            try:
                                self._open(wanted)
                            except Exception as exc:
                                self.error = str(exc)
                                log.warning("Port %s lässt sich nicht öffnen: %s", wanted, exc)
            except Exception as exc:
                self.error = str(exc)
                log.debug("MIDI-Scan fehlgeschlagen: %s", exc)
            self._wake.wait(float(self.cfg.get("rescan_s", 2.0)))
            self._wake.clear()
        with self._lock:
            self._close("(Programmende)")

    def stop(self) -> None:
        self._stop_event.set()
        self._wake.set()
