"""Renderer: der einzige Thread, der den LED-Streifen anfasst.

Grundidee
---------
Alle Eingaben (Noten, Pedale, Konfiguration, Befehle) kommen als Tupel über eine
Queue herein. Der Renderer schläft, bis etwas passiert oder der nächste Frame fällig
ist:

* Neue Note → Frame so schnell wie möglich (frühestens `MIN_EVENT_INTERVAL_S`
  nach dem letzten), damit die Latenz Taste → LED nur wenige Millisekunden beträgt.
* Etwas bewegt sich (Ausblenden, Regenbogen, Animation) → Frames im `fps`-Takt.
* Nichts passiert → nur alle `full_refresh_s` ein kompletter Frame. Das korrigiert
  gestörte Pixel ("hängende LEDs") und hält die CPU-Last nahe null.

Selbstheilung
-------------
* Jeder Schleifendurchlauf aktualisiert `heartbeat`; der Hauptprozess meldet sich nur
  dann beim systemd-Watchdog, wenn der Herzschlag frisch ist.
* Scheitert der LED-Treiber mehrfach hintereinander, wird er neu erzeugt.
* Tasten, die unplausibel lange leuchten, werden abgeschaltet (Stuck-Timeout).
"""
from __future__ import annotations

import logging
import queue
import random
import threading
import time
from typing import Callable

import numpy as np

from . import effects
from .keymap import KeyMap
from .leds import FramePipeline, LedDriver
from .state import KeyboardState

log = logging.getLogger(__name__)

MIN_EVENT_INTERVAL_S = 0.004   # ws281x braucht für 176 LEDs ~5 ms pro Übertragung
PUBLISH_INTERVAL_S = 0.033     # Live-Tastatur in der App: max. ~30 Updates/s
STUCK_CHECK_INTERVAL_S = 1.0
DRIVER_ERRORS_BEFORE_RESET = 3


class Renderer(threading.Thread):
    def __init__(self, cfg: dict, driver_factory: Callable[[dict], LedDriver],
                 publish: Callable[[str, object], None] | None = None):
        super().__init__(name="renderer", daemon=True)
        self._driver_factory = driver_factory
        self._publish = publish or (lambda topic, payload: None)
        self.events: queue.Queue = queue.Queue(maxsize=4096)
        self._stop_event = threading.Event()

        self.driver = driver_factory(cfg)
        self.heartbeat = time.monotonic()
        self.frame_count = 0
        self.last_frame_ms = 0.0
        self.max_frame_ms = 0.0
        self.driver_resets = 0

        self.test_pattern: dict | None = None
        self.test_until = 0.0
        self.player_active = False
        self.idle_animation_active = False
        self.last_colors: dict[int, str] = {}

        self._dirty = True
        self._animating = False
        self._force_full = True
        self._driver_errors = 0
        self._last_frame = 0.0
        self._last_full = 0.0
        self._last_publish = 0.0
        self._last_stuck_check = 0.0
        self._apply_config(cfg)

    # =====================================================================
    # Konfiguration
    # =====================================================================
    def _apply_config(self, cfg: dict) -> None:
        self.cfg = cfg
        strip, look = cfg["strip"], cfg["look"]
        self.keymap = KeyMap(strip, cfg["transpose"]["semitones"])
        self.led_count = int(strip["led_count"])
        self.frame_interval = 1.0 / int(strip["fps"])
        self.full_refresh_s = float(strip["full_refresh_s"])
        self.pipeline = FramePipeline(strip, int(look["brightness"]))
        self.idle_cfg = cfg["features"]["idle_animation"]
        self.synthesia = cfg["features"]["synthesia"]

        old = getattr(self, "state", None)
        self.state = KeyboardState(self.keymap.lowest, self.keymap.highest,
                                   sustain_holds_light=bool(look["sustain_holds_light"]),
                                   stuck_timeout_s=float(look["stuck_note_timeout_s"]))
        nkeys = self.keymap.key_count()
        self.intensity = np.zeros(nkeys, dtype=np.float32)
        self.key_color = np.ones((nkeys, 3), dtype=np.float32)
        self.key_fixed = np.zeros(nkeys, dtype=bool)   # Farbe kommt von außen (Synthesia)
        self.key_vel = np.ones(nkeys, dtype=np.float32)
        self._build_led_tables()
        self.backlight = self._backlight_frame()

        if old is not None:  # gedrückte Tasten über Konfigurationswechsel retten
            self.state.sustain = old.sustain
            for note, key in old.keys.items():
                if note in self.state.keys:
                    self.state.keys[note] = key
                    if key.lit:
                        self._activate(note, key)
        self._force_full = True
        self._dirty = True

    def _build_led_tables(self) -> None:
        """Flache Index-Tabellen, damit das Rendern ohne Python-Schleifen auskommt."""
        led_idx, led_key, adj_idx, adj_key, centers = [], [], [], [], []
        for i, note in enumerate(self.keymap.keys()):
            leds = self.keymap.leds_for_key(note)
            led_idx += leds
            led_key += [i] * len(leds)
            if leds:
                for n in (min(leds) - 1, max(leds) + 1):
                    if 0 <= n < self.led_count:
                        adj_idx.append(n)
                        adj_key.append(i)
            c = self.keymap.center_led(note)
            centers.append(c if c is not None else 0)
        self._led_idx = np.array(led_idx, dtype=np.intp)
        self._led_key = np.array(led_key, dtype=np.intp)
        self._adj_idx = np.array(adj_idx, dtype=np.intp)
        self._adj_key = np.array(adj_key, dtype=np.intp)
        self._centers = np.array(centers, dtype=np.float32)

    def _backlight_frame(self) -> np.ndarray:
        bl = self.cfg["look"]["backlight"]
        frame = np.zeros((self.led_count, 3), dtype=np.float32)
        if bl["enabled"] and bl["brightness"] > 0:
            frame[:] = np.asarray(bl["color"], dtype=np.float32) / 255.0 * (bl["brightness"] / 100.0)
        return frame

    # =====================================================================
    # Farben
    # =====================================================================
    def _velocity_factor(self, velocity: int) -> float:
        look = self.cfg["look"]
        if look["light_mode"] != "velocity":
            return 1.0
        vmin = look["velocity_min"] / 100.0
        return vmin + (1.0 - vmin) * (max(1, min(127, velocity)) / 127.0)

    def _base_color(self, note: int) -> np.ndarray:
        look = self.cfg["look"]
        if look["color_mode"] == "multicolor":
            options = [e["color"] for e in look["multicolor"] if e["range"][0] <= note <= e["range"][1]]
            return np.asarray(random.choice(options) if options else (0, 0, 0), dtype=np.float32) / 255.0
        if look["color_mode"] == "rainbow":
            return np.ones(3, dtype=np.float32)   # wird beim Rendern pro Taste ersetzt
        return np.asarray(look["color"], dtype=np.float32) / 255.0

    def _activate(self, note: int, key) -> None:
        i = note - self.keymap.lowest
        self.intensity[i] = 1.0
        self.key_fixed[i] = key.color is not None
        self.key_color[i] = (np.asarray(key.color, dtype=np.float32) / 255.0) if key.color is not None \
            else self._base_color(note)
        self.key_vel[i] = self._velocity_factor(key.velocity)

    # =====================================================================
    # Ereignisse
    # =====================================================================
    def post(self, *event) -> None:
        """Von jedem Thread aus aufrufbar."""
        try:
            self.events.put_nowait(event)
        except queue.Full:
            log.warning("Event-Queue voll, '%s' verworfen", event[0])

    def _handle(self, ev: tuple, now: float) -> None:
        kind = ev[0]
        if kind == "note_on":
            _, note, velocity, channel, source = ev
            if velocity <= 0:
                self._handle(("note_off", note, channel, source), now)
                return
            key_no = self.keymap.note_to_key(note)
            if key_no is None:
                return
            color = None
            syn = self.synthesia
            if syn["enabled"]:
                if channel == syn["left_channel"]:
                    color = tuple(syn["left_color"])
                elif channel == syn["right_channel"]:
                    color = tuple(syn["right_color"])
                elif syn["hide_normal_notes"]:
                    return
            key = self.state.note_on(key_no, velocity, channel, color=color, source=source, now=now)
            if key is not None:
                self._activate(key_no, key)
        elif kind == "note_off":
            key_no = self.keymap.note_to_key(ev[1])
            if key_no is not None:
                self.state.note_off(key_no, now=now)
        elif kind == "cc":
            _, control, value, _channel = ev
            if control == 64:
                self.state.set_sustain(value, now=now)
            elif control in (120, 123):
                self.state.all_off(now=now)
            else:
                self.state.last_activity = now
        elif kind == "activity":
            self.state.last_activity = now
        elif kind == "config":
            self._apply_config(ev[1])
        elif kind == "panic":
            self.state.all_off(now=now)
            self.intensity[:] = 0
            self.test_pattern = None
            self._force_full = True
        elif kind == "test":
            self.test_pattern = ev[1]
            self.test_until = now + float(ev[1].get("seconds", 5))
        elif kind == "player":
            self.player_active = bool(ev[1])
        self._dirty = True

    def _drain(self, now: float) -> None:
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                return
            try:
                self._handle(ev, now)
            except Exception:
                log.exception("Fehler bei Ereignis %s", ev[0])

    # =====================================================================
    # Frame berechnen
    # =====================================================================
    def _advance(self, dt: float) -> bool:
        """Intensitäten fortschreiben. Gibt True zurück, solange noch etwas ausblendet."""
        look = self.cfg["look"]
        lit = np.fromiter((k.lit for k in self.state.keys.values()), dtype=bool, count=len(self.state.keys))
        if look["light_mode"] == "normal":
            self.intensity = lit.astype(np.float32)
            return False
        step = dt * 1000.0 / float(look["fade_ms"])
        self.intensity = np.where(lit, 1.0, np.maximum(0.0, self.intensity - step)).astype(np.float32)
        return bool(np.any((~lit) & (self.intensity > 0)))

    def _key_colors(self, now: float) -> tuple[np.ndarray, np.ndarray]:
        """Farbe je Taste (inkl. Helligkeit durch Ausblenden/Anschlag) und Maske aktiver Tasten."""
        level = self.intensity * self.key_vel
        active = level > 0.002
        colors = self.key_color.copy()
        look = self.cfg["look"]
        if look["color_mode"] == "rainbow" and active.any():
            rb = look["rainbow"]
            rainbow = effects.rainbow_colors(self._centers, rb["offset"], rb["scale"], rb["speed"], now)
            colors = np.where(self.key_fixed[:, None], colors, rainbow)
        return colors * level[:, None], active

    def _render_keys(self, now: float) -> np.ndarray:
        frame = self.backlight.copy()
        colors, active = self._key_colors(now)
        self._remember_colors(colors, active)
        if not active.any():
            return frame
        key_frame = np.zeros_like(frame)
        sel = active[self._led_key]
        np.maximum.at(key_frame, self._led_idx[sel], colors[self._led_key[sel]])
        touched = np.zeros(self.led_count, dtype=bool)
        touched[self._led_idx[sel]] = True

        adj = self.cfg["look"]["adjacent"]
        if adj["mode"] != "off" and self._adj_idx.size:
            asel = active[self._adj_key]
            if asel.any():
                if adj["mode"] == "same":
                    acol = colors[self._adj_key[asel]]
                else:
                    level = (self.intensity * self.key_vel)[self._adj_key[asel]]
                    acol = (np.asarray(adj["color"], dtype=np.float32) / 255.0)[None, :] * level[:, None]
                np.maximum.at(frame, self._adj_idx[asel], acol)
        frame[touched] = key_frame[touched]
        return frame

    def _remember_colors(self, colors: np.ndarray, active: np.ndarray) -> None:
        """Farben der leuchtenden Tasten für die Live-Tastatur in der App (Hex)."""
        idx = np.nonzero(active)[0]
        if idx.size == 0:
            if self.last_colors:
                self.last_colors = {}
                self.state.changed = True
            return
        rgb = np.clip(colors[idx] * 255.0, 0, 255).astype(np.uint8)
        low = self.keymap.lowest
        new = {int(i) + low: "#%02x%02x%02x" % tuple(int(v) for v in c) for i, c in zip(idx, rgb)}
        if new != self.last_colors:
            self.last_colors = new
            self.state.changed = True

    def _render_test(self, now: float) -> np.ndarray:
        frame = np.zeros((self.led_count, 3), dtype=np.float32)
        tp = self.test_pattern or {}
        color = np.asarray(tp.get("color", [255, 255, 255]), dtype=np.float32) / 255.0
        kind = tp.get("kind", "keys")
        if kind == "all":
            frame[:] = color
        elif kind == "leds":
            for led in tp.get("leds", []):
                if 0 <= int(led) < self.led_count:
                    frame[int(led)] = color
        elif kind == "keys":
            for note in tp.get("keys", []):
                for led in self.keymap.leds_for_key(int(note)):
                    frame[led] = color
        elif kind == "chase":
            frame[int((now * 60) % self.led_count)] = color
        elif kind == "rgb":      # Farbreihenfolge prüfen: Rot, Grün, Blau je 1 s
            frame[:, int(now) % 3] = 1.0
        return frame

    def _idle_frame(self, now: float) -> np.ndarray | None:
        idle = self.idle_cfg
        active = (idle["enabled"] and not self.player_active
                  and (now - self.state.last_activity) >= idle["after_min"] * 60)
        if active != self.idle_animation_active:
            self.idle_animation_active = active
            self.state.changed = True
        if not active:
            return None
        fn = effects.ANIMATIONS.get(idle["animation"], effects.anim_rainbow_cycle)
        return fn(now, self.led_count) * (idle["brightness"] / 100.0)

    def _compose(self, now: float, dt: float) -> tuple[np.ndarray, bool]:
        """Frame + Info, ob im nächsten Takt wieder gerendert werden muss."""
        fading = self._advance(dt)
        if self.test_pattern is not None:
            if now <= self.test_until:
                return self._render_test(now), True
            self.test_pattern = None
            self._force_full = True
        idle = self._idle_frame(now)
        if idle is not None:
            return idle, True
        frame = self._render_keys(now)
        look = self.cfg["look"]
        rainbow_moving = look["color_mode"] == "rainbow" and look["rainbow"]["speed"] != 0 and bool(self.last_colors)
        return frame, fading or rainbow_moving

    # =====================================================================
    # Ausgabe
    # =====================================================================
    def _output(self, frame01: np.ndarray, force: bool) -> bool:
        out = self.pipeline.process(frame01)
        if out.shape[0] != self.driver.led_count:   # LED-Anzahl geändert, Treiber erst nach Neustart
            fixed = np.zeros((self.driver.led_count, 3), dtype=np.uint8)
            n = min(out.shape[0], self.driver.led_count)
            fixed[:n] = out[:n]
            out = fixed
        try:
            shown = self.driver.show(out, force=force)
            self._driver_errors = 0
            return shown
        except Exception as exc:
            self._driver_errors += 1
            log.error("LED-Ausgabe fehlgeschlagen (%d×): %s", self._driver_errors, exc)
            if self._driver_errors >= DRIVER_ERRORS_BEFORE_RESET:
                self._reset_driver()
            return False

    def _reset_driver(self) -> None:
        log.warning("LED-Treiber wird neu initialisiert")
        try:
            self.driver.close()
        except Exception:
            pass
        try:
            self.driver = self._driver_factory(self.cfg)
            self.driver_resets += 1
            self._driver_errors = 0
            self._force_full = True
        except Exception:
            log.exception("LED-Treiber konnte nicht neu erzeugt werden")

    def _frame(self, now: float) -> None:
        t0 = time.monotonic()
        dt = min(0.25, now - self._last_frame) if self._last_frame else 0.0
        self._last_frame = now
        frame, self._animating = self._compose(now, dt)
        force = self._force_full or (now - self._last_full) >= self.full_refresh_s
        if self._output(frame, force):
            self.frame_count += 1
        if force:
            self._last_full = now
            self._force_full = False
        self._dirty = False
        self.last_frame_ms = (time.monotonic() - t0) * 1000
        self.max_frame_ms = max(self.max_frame_ms, self.last_frame_ms)

    def _maybe_publish(self, now: float) -> None:
        if self.state.changed and now - self._last_publish >= PUBLISH_INTERVAL_S:
            self.state.changed = False
            self._last_publish = now
            self._publish("keys", self.snapshot())

    def _next_due(self) -> float:
        if self._dirty:
            due = self._last_frame + MIN_EVENT_INTERVAL_S
        elif self._animating:
            due = self._last_frame + self.frame_interval
        else:
            due = min(self._last_full + self.full_refresh_s, self._last_stuck_check + STUCK_CHECK_INTERVAL_S)
        if self.state.changed:
            due = min(due, self._last_publish + PUBLISH_INTERVAL_S)
        return due

    def _housekeeping(self, now: float) -> None:
        if now - self._last_stuck_check >= STUCK_CHECK_INTERVAL_S:
            self._last_stuck_check = now
            expired = self.state.expire_stuck(now)
            if expired:
                log.warning("%d hängende Taste(n) nach Timeout ausgeschaltet", expired)
                self._dirty = True
            if self.idle_cfg["enabled"]:
                self._dirty = True   # prüft Start/Ende der Leerlauf-Animation

    # =====================================================================
    # Thread
    # =====================================================================
    def _startup_animation(self) -> None:
        """Kurzer Lauf über den Streifen: sofort sichtbar, dass das System lebt."""
        if not self.cfg["look"].get("startup_animation", True):
            return
        n = self.led_count
        idx = np.arange(n, dtype=np.float32)
        start = time.monotonic()
        duration = 0.9
        while not self._stop_event.is_set():
            t = (time.monotonic() - start) / duration
            if t >= 1.0:
                break
            head = t * (n + 20)
            tail = np.clip(1 - (head - idx) / 20.0, 0, 1) * (idx <= head)
            frame = np.zeros((n, 3), dtype=np.float32)
            frame[:, 1] = tail * 0.6
            frame[:, 2] = tail
            self._output(frame, force=False)
            self._stop_event.wait(0.016)
        self._output(np.zeros((n, 3), dtype=np.float32), force=True)

    def run(self) -> None:
        log.info("Renderer läuft (%d LEDs, max. %d fps)", self.led_count, round(1 / self.frame_interval))
        try:
            self._startup_animation()
        except Exception:
            log.exception("Startanimation fehlgeschlagen")
        while not self._stop_event.is_set():
            self.heartbeat = now = time.monotonic()
            try:
                timeout = self._next_due() - now
                if timeout > 0:
                    try:
                        ev = self.events.get(timeout=min(timeout, 1.0))
                        self._handle(ev, time.monotonic())
                    except queue.Empty:
                        pass
                now = time.monotonic()
                self._drain(now)
                self._housekeeping(now)
                if now >= self._next_due() or self._force_full:
                    self._frame(now)
                self._maybe_publish(now)
            except Exception:
                log.exception("Renderfehler")
                self._stop_event.wait(0.1)   # kein Busy-Loop bei Dauerfehlern
        try:
            self.driver.close()
        except Exception:
            pass

    def stop(self) -> None:
        self._stop_event.set()

    # =====================================================================
    # Status
    # =====================================================================
    def snapshot(self) -> dict:
        return {
            "colors": {str(k): v for k, v in self.last_colors.items()},
            "pressed": self.state.pressed_keys(),
            "sustain": self.state.sustain,
            "idle_animation": self.idle_animation_active,
            "estimated_ma": round(self.pipeline.last_estimated_ma),
            "power_scale": round(self.pipeline.last_power_scale, 3),
        }

    def stats(self) -> dict:
        return {
            "frames": self.frame_count,
            "frame_ms": round(self.last_frame_ms, 2),
            "max_frame_ms": round(self.max_frame_ms, 2),
            "queue": self.events.qsize(),
            "driver_resets": self.driver_resets,
            "heartbeat_age_s": round(time.monotonic() - self.heartbeat, 2),
        }
