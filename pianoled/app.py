"""Die App verbindet alle Teile und ist die einzige Schnittstelle für Web und Display.

    MIDI-Thread ──► App._on_midi ──► Renderer-Queue ──► LED-Streifen
                          │
                          └──► Recorder, Transpose-Kalibrierung, Pedal-Aktionen

    Web / Display ──► App-Methoden (set_transpose, panic, …) ──► Config / Renderer
    Alle Teile ──► EventBus ──► Web (WebSocket) und Display

Hintergrund-Komponenten (MIDI, WLAN, Display, Web) laufen unter einem Supervisor,
der sie bei einem Absturz neu startet. `healthy()` sagt dem Hauptprozess, ob er sich
beim systemd-Watchdog melden darf.
"""
from __future__ import annotations

import concurrent.futures
import logging
import os
import socket
import threading
import time
import uuid

from . import __version__
from .config import Config
from .events import EventBus
from .leds import make_driver
from .midi_input import MidiInput
from .player import Player
from .recorder import Recorder
from .renderer import Renderer
from .supervisor import Supervisor
from .system import sysinfo
from .system.network import NetworkManager
from .transpose import PedalTrigger, RolandTransposeLearner, transpose_from_dt1

log = logging.getLogger(__name__)

PRESET_KEYS = ("brightness", "color_mode", "light_mode", "color", "multicolor", "rainbow",
               "fade_ms", "velocity_min", "backlight", "adjacent")
RENDERER_STALE_S = 5.0
CALIBRATION_TIMEOUT_S = 20.0


class App:
    def __init__(self, config_path: str, base_dir: str, simulate: bool = False):
        self.base_dir = base_dir
        self.simulate = simulate
        self.started = time.monotonic()
        self.bus = EventBus()
        self.config = Config(config_path)
        self.config.on_change(self._on_config_changed)
        cfg = self.config.snapshot()
        logging.getLogger().setLevel(getattr(logging, cfg.get("log_level", "INFO"), logging.INFO))

        # Langsame Dinge (Datei schreiben, nmcli, git) nie im MIDI- oder Render-Thread
        self._jobs = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")

        self.sim_frame = None
        self.renderer = Renderer(cfg, self._make_driver, self.bus.publish)
        songs_dir = os.path.join(base_dir, cfg["features"]["player"]["songs_dir"])
        self.recorder = Recorder(songs_dir)
        self.player = Player(songs_dir, self._midi_send, lambda ev: self.renderer.post(*ev), self._on_player_state)
        self.supervisor = Supervisor(on_restart=lambda name, n: self.bus.publish("restart", {"component": name, "count": n}))

        # Transpose-Hilfen
        self.calibrating = False
        self._calibration_deadline = 0.0
        self.pedal_trigger = self._make_pedal_trigger(cfg)
        self.learner: RolandTransposeLearner | None = None
        self.last_sysex: list[str] = []
        self.monitor_enabled = False
        self.monitor: list[dict] = []

        self._preset_pedal_down = False
        self.active_preset: str | None = None

    # =================================================================
    # Lebenszyklus
    # =================================================================
    def start(self) -> None:
        self.renderer.start()
        sup = self.supervisor
        sup.add("midi", lambda: MidiInput(self.config.get("midi"), self._on_midi,
                                          on_status=lambda s: self.bus.publish("midi", s),
                                          on_disconnect=self.panic))
        sup.add("network", lambda: NetworkManager(self.config.get("network"), self.bus.publish))
        sup.add("lcd", self._make_lcd)
        sup.start()
        log.info("Piano LED %s gestartet%s", __version__, " (Simulation)" if self.simulate else "")

    def add_component(self, name: str, factory) -> None:
        """Weitere überwachte Komponente, z. B. der Webserver."""
        self.supervisor.add(name, factory)

    def stop(self) -> None:
        self.player.stop()
        self.supervisor.stop()
        self.renderer.stop()
        self.renderer.join(timeout=3)
        self.config.flush()
        self._jobs.shutdown(wait=False)

    def _make_driver(self, cfg: dict):
        return make_driver(cfg["strip"], simulate=self.simulate, on_show=self._on_sim_frame)

    def _make_lcd(self):
        from .lcd.ui import LcdUI
        use_hw = bool(self.config.get("features.lcd.enabled")) and not self.simulate
        try:
            return LcdUI(self, use_hardware=use_hw)
        except Exception as exc:
            # Kein Hat aufgesteckt o. Ä.: ohne Hardware weiterlaufen, Vorschau bleibt verfügbar
            log.warning("Display nicht verfügbar (%s), nur Vorschau", exc)
            return LcdUI(self, use_hardware=False)

    # =================================================================
    # Zugriff auf überwachte Komponenten (können nach Neustart neue Objekte sein)
    # =================================================================
    @property
    def midi(self) -> MidiInput | None:
        return self.supervisor.get("midi")

    @property
    def network(self) -> NetworkManager | None:
        return self.supervisor.get("network")

    @property
    def lcd(self):
        return self.supervisor.get("lcd")

    def midi_status(self) -> dict:
        m = self.midi
        return m.status() if m else {"connected": False, "available": [], "error": "MIDI startet …"}

    def network_state(self) -> dict:
        n = self.network
        return dict(n.state) if n else {"mode": "offline"}

    def hostname(self) -> str:
        return self.config.get("network.hostname") or socket.gethostname()

    def _midi_send(self, msg) -> bool:
        m = self.midi
        return m.send(msg) if m else False

    # =================================================================
    # Gesundheit
    # =================================================================
    def healthy(self) -> tuple[bool, str]:
        age = time.monotonic() - self.renderer.heartbeat
        if not self.renderer.is_alive():
            return False, "Renderer beendet"
        if age > RENDERER_STALE_S:
            return False, f"Renderer hängt seit {age:.0f} s"
        return True, "ok"

    # =================================================================
    # Ereignisse
    # =================================================================
    def _on_sim_frame(self, frame) -> None:
        self.sim_frame = frame

    def _on_player_state(self, playing: bool) -> None:
        self.renderer.post("player", playing)
        self.bus.publish("player", self.player.status())

    def _on_config_changed(self, snapshot: dict) -> None:
        self.renderer.post("config", snapshot)
        self.pedal_trigger = self._make_pedal_trigger(snapshot)
        if self.midi:
            self.midi.update_config(snapshot["midi"])
        if self.network:
            self.network.cfg = snapshot["network"]
        self.bus.publish("config", snapshot)

    @staticmethod
    def _make_pedal_trigger(cfg: dict) -> PedalTrigger | None:
        pc = cfg["transpose"]["pedal_calibration"]
        if not pc["enabled"]:
            return None
        return PedalTrigger(control=int(pc["control"]), presses=int(pc["presses"]), window_s=float(pc["window_s"]))

    def _on_midi(self, ev: tuple) -> None:
        """Läuft im rtmidi-Thread: nur weiterreichen, nichts Langsames."""
        kind = ev[0]
        if self.monitor_enabled:
            self._monitor_add(ev)
        if kind == "note_on":
            if self.calibrating and ev[2] > 0:
                self._jobs.submit(self._finish_calibration, ev[1])
                return
            self.recorder.feed(ev)
            self.renderer.post("note_on", ev[1], ev[2], ev[3], "piano")
        elif kind == "note_off":
            self.recorder.feed(ev)
            self.renderer.post("note_off", ev[1], ev[2], "piano")
        elif kind == "cc":
            self.recorder.feed(ev)
            self.renderer.post("cc", ev[1], ev[2], ev[3])
            self._handle_pedal(ev[1], ev[2])
        elif kind == "sysex":
            self._handle_sysex(ev[1])
        else:
            self.renderer.post("activity")

    def _handle_pedal(self, control: int, value: int) -> None:
        trigger = self.pedal_trigger
        if trigger and trigger.feed(control, value):
            self._jobs.submit(self.start_calibration)
        pp = self.config.get("features.pedal_presets")
        if pp["enabled"] and control == int(pp["control"]):
            down = value >= int(pp["threshold"])
            if down and not self._preset_pedal_down:
                self._jobs.submit(self.next_preset)
            self._preset_pedal_down = down

    def _handle_sysex(self, data: bytes) -> None:
        self.last_sysex = (self.last_sysex + [" ".join(f"{b:02X}" for b in data)])[-20:]
        if self.learner is not None:
            self.learner.feed(data)
        rs = self.config.get("transpose.roland_sysex")
        if rs["enabled"] and rs["address"]:
            value = transpose_from_dt1(data, rs["address"], rs["base_value"])
            if value is not None and value != self.config.get("transpose.semitones"):
                log.info("Transpose vom Piano übernommen: %+d", value)
                self._jobs.submit(self.set_transpose, value)

    def _monitor_add(self, ev: tuple) -> None:
        entry: dict = {"t": round(time.time(), 3), "type": ev[0]}
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
        self.bus.publish("monitor", entry)

    # =================================================================
    # Transpose
    # =================================================================
    def set_transpose(self, semitones: int) -> int:
        semitones = max(-24, min(24, int(semitones)))
        self.config.set("transpose.semitones", semitones)
        self.renderer.post("panic")   # Tasten mit altem Versatz nicht stehen lassen
        self.bus.publish("transpose", {"semitones": semitones})
        return semitones

    def start_calibration(self) -> None:
        self.calibrating = True
        self._calibration_deadline = time.monotonic() + CALIBRATION_TIMEOUT_S
        self.renderer.post("panic")
        lowest = int(self.config.get("strip.lowest_note"))
        self.renderer.post("test", {"kind": "keys", "keys": [lowest], "color": [255, 120, 0],
                                    "seconds": CALIBRATION_TIMEOUT_S})
        self.bus.publish("calibration", {"active": True, "timeout_s": CALIBRATION_TIMEOUT_S})
        timer = threading.Timer(CALIBRATION_TIMEOUT_S + 0.2, self._calibration_timeout)
        timer.daemon = True
        timer.start()

    def _calibration_timeout(self) -> None:
        if self.calibrating and time.monotonic() >= self._calibration_deadline:
            self.calibrating = False
            self.renderer.post("panic")
            self.bus.publish("calibration", {"active": False, "result": None})

    def _finish_calibration(self, received_note: int) -> None:
        if not self.calibrating:
            return
        self.calibrating = False
        # Das Piano sendet für die tiefste Taste "tiefste Note + eingestellter Transpose"
        value = self.set_transpose(self.renderer.keymap.calibration_from_lowest_key(received_note))
        self.renderer.post("test", {"kind": "keys", "keys": [int(self.config.get("strip.lowest_note"))],
                                    "color": [0, 255, 0], "seconds": 1.2})
        self.bus.publish("calibration", {"active": False, "result": value})
        if self.lcd:
            self.lcd.show_toast(f"Transpose {value:+d}" if value else "Transpose 0")

    def learn_start(self) -> None:
        self.learner = RolandTransposeLearner()

    def learn_step(self) -> int:
        if self.learner is None:
            self.learn_start()
        self.learner.next_step()
        return len(self.learner.steps)

    def learn_finish(self) -> dict:
        learner, self.learner = self.learner, None
        if learner is None:
            return {"ok": False, "reason": "Lernen wurde nicht gestartet."}
        result = learner.result()
        if not result:
            return {"ok": False, "reason": "Keine passende Nachricht gefunden. Ist am Piano „Tx Edit Data“ eingeschaltet?"}
        address, base = result
        self.config.update({"transpose.roland_sysex.address": address,
                            "transpose.roland_sysex.base_value": base,
                            "transpose.roland_sysex.enabled": True})
        return {"ok": True, "address": address, "base_value": base}

    # =================================================================
    # Presets
    # =================================================================
    def save_preset(self, name: str) -> dict:
        look = self.config.get("look")
        preset = {"id": uuid.uuid4().hex[:8], "name": (name or "").strip()[:40] or "Preset",
                  "look": {k: look[k] for k in PRESET_KEYS}}
        self.config.set("presets", (self.config.get("presets") or []) + [preset])
        self.active_preset = preset["id"]
        return preset

    def apply_preset(self, preset_id: str) -> bool:
        for p in self.config.get("presets") or []:
            if p["id"] == preset_id:
                self.config.update({f"look.{k}": v for k, v in p["look"].items()})
                self.active_preset = preset_id
                self.bus.publish("preset", {"active": preset_id, "name": p["name"]})
                return True
        return False

    def delete_preset(self, preset_id: str) -> None:
        self.config.set("presets", [p for p in self.config.get("presets") or [] if p["id"] != preset_id])
        if self.active_preset == preset_id:
            self.active_preset = None

    def next_preset(self) -> str | None:
        ids = [p["id"] for p in self.config.get("presets") or []]
        if not ids:
            return None
        nxt = ids[(ids.index(self.active_preset) + 1) % len(ids)] if self.active_preset in ids else ids[0]
        self.apply_preset(nxt)
        return nxt

    # =================================================================
    # Befehle
    # =================================================================
    def panic(self) -> None:
        self.renderer.post("panic")

    def test_pattern(self, pattern: dict) -> None:
        self.renderer.post("test", pattern)

    def set_brightness(self, value: int) -> int:
        value = max(1, min(100, int(value)))
        self.config.set("look.brightness", value)
        return value

    def start_hotspot(self) -> bool:
        return bool(self.network and self.network.start_hotspot())

    def stop_hotspot(self) -> None:
        if self.network:
            self.network.stop_hotspot()

    def restart_service(self):
        self.panic()
        return sysinfo.restart_service()

    def reboot(self):
        self.panic()
        self.config.flush()
        return sysinfo.reboot()

    def shutdown(self):
        self.panic()
        self.config.flush()
        return sysinfo.shutdown()

    # =================================================================
    # Status für App und Display
    # =================================================================
    def status(self) -> dict:
        return {
            "version": __version__,
            "simulate": self.simulate,
            "midi": self.midi_status(),
            "wifi": self.network_state(),
            "keys": self.renderer.snapshot(),
            "transpose": self.config.get("transpose.semitones"),
            "calibrating": self.calibrating,
            "player": self.player.status(),
            "recorder": self.recorder.status(),
            "active_preset": self.active_preset,
            "renderer": self.renderer.stats(),
            "components": self.supervisor.status(),
            "system": sysinfo.info(__version__),
            "hostname": self.hostname(),
        }
