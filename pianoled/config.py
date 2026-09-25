"""Konfiguration: Defaults, Laden/Speichern (atomar), Validierung.

Die Konfiguration ist ein verschachteltes Dict. Alle Schlüssel haben Defaults,
unbekannte Schlüssel aus der Datei werden ignoriert, fehlende ergänzt. Dadurch
überlebt die Datei Versionswechsel und kann nie das Programm zum Absturz bringen.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import threading
from typing import Any, Callable

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "version": 2,
    "strip": {
        "led_count": 176,
        "gpio_pin": 18,          # 18 = PWM0, 10 = SPI, 21 = PCM
        "dma": 10,
        "freq_hz": 800000,
        "invert": False,
        "channel": 0,
        "color_order": "GRB",     # WS2812B: GRB
        "reverse": False,         # True, wenn LED 0 bei der höchsten Taste liegt
        "led_pitch_mm": 1000 / 144,
        "key_pitch_mm": 23.5,     # Breite einer weißen Taste (Standardklaviatur)
        "led_offset": 0,          # Verschiebung in LEDs (kann negativ sein)
        "leds_per_key": 2,        # 1..3 LEDs pro Taste leuchten
        "lowest_note": 21,        # A0 auf einer 88-Tasten-Klaviatur
        "highest_note": 108,      # C8
        "gamma": 2.2,
        "max_current_ma": 3000,   # Leistungsbudget des Netzteils, 0 = unbegrenzt
        "ma_per_channel": 20,     # Strom eines Farbkanals bei 255
        "ma_idle_per_led": 1,
        "fps": 60,
        "full_refresh_s": 1.0,    # kompletter Frame mindestens so oft senden
    },
    "look": {
        "brightness": 50,         # Prozent
        "color_mode": "single",   # single | multicolor | rainbow
        "light_mode": "normal",   # normal | fading | velocity
        "color": [255, 255, 255],
        "multicolor": [
            {"color": [255, 80, 0], "range": [21, 59]},
            {"color": [0, 160, 255], "range": [60, 108]},
        ],
        "rainbow": {"offset": 0, "scale": 100, "speed": 0},
        "fade_ms": 800,
        "velocity_min": 15,       # Prozent Helligkeit bei leisestem Anschlag
        "backlight": {"enabled": False, "color": [10, 10, 30], "brightness": 20},
        "adjacent": {"mode": "off", "color": [255, 255, 255]},  # off | same | rgb
        "sustain_holds_light": True,
        "stuck_note_timeout_s": 90,
    },
    "transpose": {
        "semitones": 0,
        "pedal_calibration": {"enabled": False, "control": 67, "presses": 3, "window_s": 2.0},
        "roland_sysex": {"enabled": False, "address": None, "base_value": 64},
    },
    "midi": {
        "port_filter": "",         # Teilstring des gewünschten Ports, leer = automatisch
        "ignore_ports": ["Through", "RtMidi", "RPi", "USB-USB"],
        "rescan_s": 2.0,
        "ignore_channels": [],
    },
    "features": {
        "synthesia": {"enabled": False, "left_color": [0, 0, 255], "right_color": [0, 255, 0],
                      "left_channel": 12, "right_channel": 11, "hide_normal_notes": False},
        "recorder": {"enabled": False},
        "player": {"enabled": False, "songs_dir": "Songs", "light_keys": True},
        "pedal_presets": {"enabled": False, "control": 66, "threshold": 64},
        "idle_animation": {"enabled": False, "after_min": 10, "animation": "rainbow_cycle", "brightness": 30},
        "lcd": {"enabled": True, "off_after_min": 5, "rotation": 0},
    },
    "presets": [],
    "network": {
        "hotspot": {"enabled": True, "ssid": "PianoLED", "password": "pianoled123",
                    "fallback_after_s": 45, "retry_known_every_s": 300},
        "hostname": "pianoled",
    },
    "web": {"host": "0.0.0.0", "port": 80},
    "log_level": "INFO",
}


def _merge(defaults: dict, data: Any) -> dict:
    """Ergänzt fehlende Schlüssel aus den Defaults, ignoriert Unbekanntes."""
    if not isinstance(data, dict):
        return copy.deepcopy(defaults)
    out = {}
    for key, dval in defaults.items():
        if key in data:
            val = data[key]
            if isinstance(dval, dict) and key != "presets":
                out[key] = _merge(dval, val)
            else:
                out[key] = copy.deepcopy(val)
        else:
            out[key] = copy.deepcopy(dval)
    return out


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def validate(cfg: dict) -> dict:
    """Bringt Werte in gültige Bereiche. Wirft nie, korrigiert nur."""
    s = cfg["strip"]
    s["led_count"] = int(_clamp(int(s.get("led_count", 176)), 1, 2000))
    s["leds_per_key"] = int(_clamp(int(s.get("leds_per_key", 2)), 1, 3))
    s["gamma"] = float(_clamp(float(s.get("gamma", 2.2)), 1.0, 3.5))
    s["fps"] = int(_clamp(int(s.get("fps", 60)), 10, 120))
    s["max_current_ma"] = int(max(0, int(s.get("max_current_ma", 0))))
    s["lowest_note"] = int(_clamp(int(s.get("lowest_note", 21)), 0, 127))
    s["highest_note"] = int(_clamp(int(s.get("highest_note", 108)), s["lowest_note"], 127))
    if s.get("color_order") not in ("RGB", "GRB", "BGR", "RBG", "GBR", "BRG"):
        s["color_order"] = "GRB"
    lk = cfg["look"]
    lk["brightness"] = int(_clamp(int(lk.get("brightness", 50)), 1, 100))
    if lk.get("color_mode") not in ("single", "multicolor", "rainbow"):
        lk["color_mode"] = "single"
    if lk.get("light_mode") not in ("normal", "fading", "velocity"):
        lk["light_mode"] = "normal"
    lk["color"] = _color(lk.get("color"))
    lk["fade_ms"] = int(_clamp(int(lk.get("fade_ms", 800)), 50, 10000))
    lk["velocity_min"] = int(_clamp(int(lk.get("velocity_min", 15)), 0, 100))
    lk["stuck_note_timeout_s"] = int(_clamp(int(lk.get("stuck_note_timeout_s", 90)), 0, 3600))
    mc = []
    for entry in lk.get("multicolor") or []:
        try:
            rng = entry.get("range", [21, 108])
            mc.append({"color": _color(entry.get("color")),
                       "range": [int(_clamp(int(rng[0]), 0, 127)), int(_clamp(int(rng[1]), 0, 127))]})
        except Exception:
            continue
    lk["multicolor"] = mc or copy.deepcopy(DEFAULTS["look"]["multicolor"])
    rb = lk["rainbow"]
    rb["offset"] = int(rb.get("offset", 0)) % 256
    rb["scale"] = int(_clamp(int(rb.get("scale", 100)), 1, 1000))
    rb["speed"] = int(_clamp(int(rb.get("speed", 0)), -200, 200))
    bl = lk["backlight"]
    bl["color"] = _color(bl.get("color"))
    bl["brightness"] = int(_clamp(int(bl.get("brightness", 20)), 0, 100))
    bl["enabled"] = bool(bl.get("enabled"))
    adj = lk["adjacent"]
    if adj.get("mode") not in ("off", "same", "rgb"):
        adj["mode"] = "off"
    adj["color"] = _color(adj.get("color"))
    tr = cfg["transpose"]
    tr["semitones"] = int(_clamp(int(tr.get("semitones", 0)), -48, 48))
    cfg["web"]["port"] = int(_clamp(int(cfg["web"].get("port", 80)), 1, 65535))
    return cfg


def _color(value) -> list[int]:
    try:
        r, g, b = value
        return [int(_clamp(int(r), 0, 255)), int(_clamp(int(g), 0, 255)), int(_clamp(int(b), 0, 255))]
    except Exception:
        return [255, 255, 255]


class Config:
    """Thread-sicherer Zugriff auf die Konfiguration mit Änderungs-Callbacks."""

    def __init__(self, path: str | None):
        self.path = path
        self._lock = threading.RLock()
        self._listeners: list[Callable[[dict], None]] = []
        self.data: dict = copy.deepcopy(DEFAULTS)
        self.load()

    # -- Datei ---------------------------------------------------------
    def load(self) -> None:
        data = None
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception as exc:  # defekte Datei: Defaults, Backup anlegen
                log.error("Konfiguration %s unlesbar (%s), verwende Defaults", self.path, exc)
                try:
                    os.replace(self.path, self.path + ".broken")
                except OSError:
                    pass
        with self._lock:
            self.data = validate(_merge(DEFAULTS, data))

    def save(self) -> None:
        if not self.path:
            return
        with self._lock:
            payload = json.dumps(self.data, indent=2, ensure_ascii=False)
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- Zugriff -------------------------------------------------------
    def get(self, dotted: str, default=None):
        with self._lock:
            node: Any = self.data
            for part in dotted.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.data)

    def set(self, dotted: str, value, save: bool = True) -> None:
        self.update({dotted: value}, save=save)

    def update(self, changes: dict[str, Any], save: bool = True) -> dict:
        """Setzt mehrere Werte per Punktpfad ("look.brightness") und validiert."""
        with self._lock:
            for dotted, value in changes.items():
                node = self.data
                parts = dotted.split(".")
                for part in parts[:-1]:
                    if part not in node or not isinstance(node[part], dict):
                        node[part] = {}
                    node = node[part]
                node[parts[-1]] = value
            self.data = validate(_merge(DEFAULTS, self.data))
            snap = copy.deepcopy(self.data)
        if save:
            try:
                self.save()
            except Exception as exc:
                log.error("Konfiguration konnte nicht gespeichert werden: %s", exc)
        for cb in list(self._listeners):
            try:
                cb(snap)
            except Exception:
                log.exception("Konfigurations-Listener fehlgeschlagen")
        return snap

    def replace(self, data: dict, save: bool = True) -> dict:
        with self._lock:
            self.data = validate(_merge(DEFAULTS, data))
        return self.update({}, save=save)

    def on_change(self, callback: Callable[[dict], None]) -> None:
        self._listeners.append(callback)
