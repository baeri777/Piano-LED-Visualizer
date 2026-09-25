"""Zentrale Verdrahtung: Konfiguration, MIDI, Renderer, Zusatzfunktionen, Netzwerk.

Web-Oberfläche und LCD sprechen ausschließlich mit dieser Klasse.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Callable

from . import __version__
from .config import Config
from .leds import make_driver
from .midi_input import MidiInput
from .player import Player
from .recorder import Recorder
from .renderer import Renderer
from .transpose import PedalTrigger, RolandTransposeLearner, transpose_from_dt1
from .system import sysinfo
from .system.network import NetworkManager

log = logging.getLogger(__name__)

LOOK_PRESET_KEYS = ("brightness", "color_mode", "light_mode", "color", "multicolor", "rainbow",
                    "fade_ms", "velocity_min", "backlight", "adjacent")


class App:
    def __init__(self, config_path: str, base_dir: str, simulate: bool = False):
        self.base_dir = base_dir
        self.simulate = simulate
        self.config = Config(config_path)
        cfg = self.config.snapshot()
        logging.getLogger().setLevel(getattr(logging, cfg.get("log_level", "INFO"), logging.INFO))

        self._listeners: list[Callable[[str, dict], None]] = []
        self.sim_frame = None
        self.driver = make_driver(cfg["strip"], simulate=simulate, on_show=self._on_sim_frame)
        self.renderer = Renderer(cfg, self.driver, on_keys_changed=lambda s: self._emit("keys", s))
        self.midi = MidiInput(cfg["midi"], self._on_midi_event, on_status=lambda s: self._emit("midi", s))

        songs_dir = os.path.join(base_dir, cfg["features"]["player"]["songs_dir"])
        self.recorder = Recorder(songs_dir)
        self.player = Player(songs_dir, self.midi.send, self.renderer.post, self._on_player_state)
        self.network = NetworkManager(cfg["network"])

        # Transpose-Hilfen
        self.calibrating = False
        self.calibrate_deadline = 0.0
        self.pedal_trigger = self._make_pedal_trigger(cfg)
        self.learner: RolandTransposeLearner | None = None
        self.last_sysex: list[str] = []
        self.monitor: list[dict] = []
        self.monitor_enabled = False

        # Pedal-Presets
        self._preset_pedal_down = False
        self.active_preset: str | None = None

        self.config.on_change(self._on_config_changed)
        self.lcd = None

    # -- Lebenszyklus ---------------------------------------------------
    def start(self) -> None:
        self.renderer.start()
        self.midi.start()
        self.network.start()
        if self.config.get("features.lcd.enabled"):
            try:
                from .lcd.ui import LcdUI
                self.lcd = LcdUI(self)
                self.lcd.start()
            except Exception as exc:
                log.warning("LCD nicht verfügbar: %s", exc)
                self.lcd = None
        log.info("PianoLED %s gestartet", __version__)

    def stop(self) -> None:
        self.player.stop()
        if self.lcd:
            self.lcd.stop()
        self.midi.stop()
        self.network.stop()
        self.renderer.stop()
        self.renderer.join(timeout=3)

    # -- Ereignisse -----------------------------------------------------
    def on(self, callback: Callable[[str, dict], None]) -> None:
        self._listeners.append(callback)

    def _emit(self, topic: str, payload: dict) -> None:
        for cb in list(self._listeners):
            try:
                cb(topic, payload)
            except Exception:
                log.exception("Listener für %s fehlgeschlagen", topic)

    def _on_sim_frame(self, frame) -> None:
        self.sim_frame = frame

    def _on_player_state(self, playing: bool) -> None:
        self.renderer.post("player", playing)
        self._emit("player", self.player.status())

    def _on_config_changed(self, snapshot: dict) -> None:
        self.renderer.post("config", snapshot)
        self.pedal_trigger = self._make_pedal_trigger(snapshot)
        old_filter = self.midi.cfg.get("port_filter", "")
        self.midi.cfg.update(snapshot["midi"])
        if snapshot["midi"].get("port_filter", "") != old_filter:
            self.midi.set_port_filter(snapshot["midi"].get("port_filter", ""))
        self.network.cfg.update(snapshot["network"])
        self._emit("config", snapshot)

    def _make_pedal_trigger(self, cfg: dict) -> PedalTrigger | None:
        pc = cfg["transpose"]["pedal_calibration"]
        if not pc["enabled"]:
            return None
        return PedalTrigger(control=int(pc["control"]), presses=int(pc["presses"]), window_s=float(pc["window_s"]))

    def _on_midi_event(self, ev: tuple) -> None:
        """Wird im rtmidi-Thread aufgerufen: schnell bleiben, nur weiterreichen."""
        kind = ev[0]
        if self.monitor_enabled:
            self._monitor_add(ev)
        if kind == "note_on":
            if self.calibrating and ev[2] > 0:
                self._finish_calibration(ev[1])
                return
            self.recorder.feed(ev)
            self.renderer.post("note_on", ev[1], ev[2], ev[3], "piano")
        elif kind == "note_off":
            self.recorder.feed(ev)
            self.renderer.post("note_off", ev[1], ev[2], "piano")
        elif kind == "cc":
            self.recorder.feed(ev)
            self.renderer.post("cc", ev[1], ev[2], ev[3])
            self._handle_pedals(ev[1], ev[2])
        elif kind == "sysex":
            self._handle_sysex(ev[1])
        else:
            self.renderer.post("activity")

    def _handle_pedals(self, control: int, value: int) -> None:
        if self.pedal_trigger and self.pedal_trigger.feed(control, value):
            self.start_calibration()
        pp = self.config.get("features.pedal_presets")
        if pp and pp["enabled"] and control == int(pp["control"]):
            down = value >= int(pp["threshold"])
            if down and not self._preset_pedal_down:
                self.next_preset()
            self._preset_pedal_down = down

    def _handle_sysex(self, data: bytes) -> None:
        hexstr = " ".join(f"{b:02X}" for b in data)
        self.last_sysex = (self.last_sysex + [hexstr])[-20:]
        if self.learner is not None:
            self.learner.feed(data)
        rs = self.config.get("transpose.roland_sysex")
        if rs and rs["enabled"] and rs["address"]:
            value = transpose_from_dt1(data, rs["address"], rs["base_value"])
            if value is not None and value != self.config.get("transpose.semitones"):
                log.info("Transpose vom Piano übernommen: %+d", value)
                self.set_transpose(value)

    def _monitor_add(self, ev: tuple) -> None:
        entry = {"t": round(time.time(), 3), "type": ev[0]}
        if ev[0] == "note_on":
            entry.update(note=ev[1], velocity=ev[2], channel=ev[3])
        elif ev[0] == "note_off":
            entry.update(note=ev[1], channel=ev[2])
        elif ev[0] == "cc":
            entry.update(control=ev[1], value=ev[2], channel=ev[3])
        elif ev[0] == "sysex":
            entry.update(data=" ".join(f"{b:02X}" for b in ev[1]))
        else:
            entry.update(detail=str(ev[1]))
        self.monitor = (self.monitor + [entry])[-200:]
        self._emit("monitor", entry)

    # -- Transpose ------------------------------------------------------
    def set_transpose(self, semitones: int) -> int:
        semitones = max(-48, min(48, int(semitones)))
        self.config.set("transpose.semitones", semitones)
        self.renderer.post("panic")
        self._emit("transpose", {"semitones": semitones})
        return semitones

    def start_calibration(self, timeout_s: float = 20.0) -> None:
        self.calibrating = True
        self.calibrate_deadline = time.monotonic() + timeout_s
        self.renderer.post("panic")
        lowest = int(self.config.get("strip.lowest_note"))
        self.renderer.post("test", {"kind": "keys", "keys": [lowest], "color": [255, 120, 0], "seconds": timeout_s})
        self._emit("calibration", {"active": True, "timeout_s": timeout_s})
        threading.Timer(timeout_s, self._calibration_timeout).start()

    def _calibration_timeout(self) -> None:
        if self.calibrating and time.monotonic() >= self.calibrate_deadline - 0.1:
            self.calibrating = False
            self._emit("calibration", {"active": False, "result": None})

    def _finish_calibration(self, received_note: int) -> None:
        self.calibrating = False
        value = self.renderer.keymap.calibration_from_lowest_key(received_note)
        self.set_transpose(value)
        self.renderer.post("test", {"kind": "keys", "keys": [int(self.config.get("strip.lowest_note"))],
                                    "color": [0, 255, 0], "seconds": 1.5})
        self._emit("calibration", {"active": False, "result": value})

    def learn_start(self) -> None:
        self.learner = RolandTransposeLearner()

    def learn_step(self) -> int:
        if self.learner is None:
            self.learn_start()
        self.learner.next_step()
        return len(self.learner.steps)

    def learn_finish(self) -> dict:
        if self.learner is None:
            return {"ok": False, "reason": "Lernen nicht gestartet"}
        result = self.learner.result()
        self.learner = None
        if not result:
            return {"ok": False, "reason": "Keine passende SysEx-Adresse gefunden. Ist 'Tx Edit Data' am Piano aktiv?"}
        address, base = result
        self.config.update({"transpose.roland_sysex.address": address,
                            "transpose.roland_sysex.base_value": base,
                            "transpose.roland_sysex.enabled": True})
        return {"ok": True, "address": address, "base_value": base}

    # -- Presets --------------------------------------------------------
    def save_preset(self, name: str) -> dict:
        look = self.config.get("look")
        preset = {"id": uuid.uuid4().hex[:8], "name": name.strip() or "Preset",
                  "look": {k: look[k] for k in LOOK_PRESET_KEYS}}
        presets = self.config.get("presets") or []
        presets.append(preset)
        self.config.set("presets", presets)
        self.active_preset = preset["id"]
        return preset

    def apply_preset(self, preset_id: str) -> bool:
        for p in self.config.get("presets") or []:
            if p["id"] == preset_id:
                self.config.update({f"look.{k}": v for k, v in p["look"].items()})
                self.active_preset = preset_id
                self._emit("preset", {"active": preset_id})
                return True
        return False

    def delete_preset(self, preset_id: str) -> None:
        presets = [p for p in (self.config.get("presets") or []) if p["id"] != preset_id]
        self.config.set("presets", presets)
        if self.active_preset == preset_id:
            self.active_preset = None

    def next_preset(self) -> str | None:
        presets = self.config.get("presets") or []
        if not presets:
            return None
        ids = [p["id"] for p in presets]
        idx = (ids.index(self.active_preset) + 1) % len(ids) if self.active_preset in ids else 0
        self.apply_preset(ids[idx])
        return ids[idx]

    # -- Befehle --------------------------------------------------------
    def panic(self) -> None:
        self.renderer.post("panic")

    def test_pattern(self, pattern: dict) -> None:
        self.renderer.post("test", pattern)

    def set_brightness(self, value: int) -> int:
        value = max(1, min(100, int(value)))
        self.config.set("look.brightness", value)
        return value

    def status(self) -> dict:
        cfg = self.config.snapshot()
        return {
            "version": __version__,
            "simulate": self.simulate,
            "midi": self.midi.status(),
            "keys": self.renderer.snapshot(),
            "transpose": cfg["transpose"]["semitones"],
            "calibrating": self.calibrating,
            "wifi": self.network.last_state or {"available": False},
            "player": self.player.status(),
            "recorder": self.recorder.status(),
            "active_preset": self.active_preset,
            "renderer": {"frames": self.renderer.frame_count,
                         "tick_ms": round(self.renderer.last_tick_ms, 2),
                         "max_tick_ms": round(self.renderer.max_tick_ms, 2)},
            "system": sysinfo.info(__version__),
        }
