"""Renderer: fester Takt, ein Besitzer des LED-Strips, Events über eine Queue.

Ablauf pro Tick:
  1. alle anstehenden Events verarbeiten (Noten, Pedale, Konfiguration, Befehle)
  2. Tastenzustand pflegen (Sustain, Stuck-Timeout)
  3. Intensitäten fortschreiben (Fading)
  4. Frame berechnen (Backlight → Tasten → Nachbar-LEDs) oder Animation/Testbild
  5. Pipeline (Helligkeit, Leistung, Gamma) → Treiber (nur bei Änderung, spätestens nach full_refresh_s)
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


class Renderer(threading.Thread):
    def __init__(self, cfg: dict, driver: LedDriver, on_keys_changed: Callable[[dict], None] | None = None):
        super().__init__(name="renderer", daemon=True)
        self.driver = driver
        self.events: queue.Queue = queue.Queue(maxsize=4096)
        self.on_keys_changed = on_keys_changed
        self._stop = threading.Event()
        self.frame_count = 0
        self.last_tick_ms = 0.0
        self.max_tick_ms = 0.0
        self.test_pattern: dict | None = None
        self.test_until = 0.0
        self.idle_animation_active = False
        self.player_active = False
        self._apply_config(cfg)

    # -- Konfiguration -------------------------------------------------
    def _apply_config(self, cfg: dict) -> None:
        self.cfg = cfg
        strip = cfg["strip"]
        look = cfg["look"]
        self.keymap = KeyMap(strip, cfg["transpose"]["semitones"])
        self.led_count = int(strip["led_count"])
        self.fps = int(strip["fps"])
        self.full_refresh_s = float(strip["full_refresh_s"])
        self.pipeline = FramePipeline(strip, int(look["brightness"]))
        nkeys = self.keymap.key_count()
        old_state = getattr(self, "state", None)
        self.state = KeyboardState(self.keymap.lowest, self.keymap.highest,
                                   sustain_holds_light=bool(look["sustain_holds_light"]),
                                   stuck_timeout_s=float(look["stuck_note_timeout_s"]))
        if old_state is not None:  # gedrückte Tasten über Konfigurationswechsel erhalten
            for n, k in old_state.keys.items():
                if n in self.state.keys:
                    self.state.keys[n] = k
            self.state.sustain = old_state.sustain
        self.intensity = np.zeros(nkeys, dtype=np.float32)
        self.key_color = np.ones((nkeys, 3), dtype=np.float32)
        self.key_vel = np.ones(nkeys, dtype=np.float32)
        if old_state is not None:
            for n, k in self.state.keys.items():
                if k.lit:
                    i = n - self.keymap.lowest
                    self.intensity[i] = 1.0
                    self.key_color[i] = self._color_for_key(n, k)
                    self.key_vel[i] = self._velocity_factor(k.velocity)
        # LED-Index-Tabelle je Taste (für Rainbow und Rendering)
        self.key_leds = [self.keymap.leds_for_key(n) for n in self.keymap.keys()]
        self.key_center = np.array([self.keymap.center_led(n) if self.keymap.center_led(n) is not None else 0
                                    for n in self.keymap.keys()], dtype=np.float32)
        self.backlight = self._backlight_frame()
        self.idle_cfg = cfg["features"]["idle_animation"]
        self.synthesia = cfg["features"]["synthesia"]
        self.force_full = True

    def _backlight_frame(self) -> np.ndarray:
        bl = self.cfg["look"]["backlight"]
        frame = np.zeros((self.led_count, 3), dtype=np.float32)
        if bl["enabled"] and bl["brightness"] > 0:
            frame[:] = np.array(bl["color"], dtype=np.float32) / 255.0 * (bl["brightness"] / 100.0)
        return frame

    # -- Farben ---------------------------------------------------------
    def _velocity_factor(self, velocity: int) -> float:
        look = self.cfg["look"]
        if look["light_mode"] != "velocity":
            return 1.0
        vmin = look["velocity_min"] / 100.0
        return vmin + (1.0 - vmin) * (max(1, min(127, velocity)) / 127.0)

    def _color_for_key(self, key: int, k) -> np.ndarray:
        if k.color is not None:
            return np.array(k.color, dtype=np.float32) / 255.0
        look = self.cfg["look"]
        mode = look["color_mode"]
        if mode == "multicolor":
            candidates = [e["color"] for e in look["multicolor"] if e["range"][0] <= key <= e["range"][1]]
            if candidates:
                return np.array(random.choice(candidates), dtype=np.float32) / 255.0
            return np.zeros(3, dtype=np.float32)
        if mode == "rainbow":
            return np.ones(3, dtype=np.float32)  # wird beim Rendern pro LED ersetzt
        return np.array(look["color"], dtype=np.float32) / 255.0

    # -- Events ---------------------------------------------------------
    def post(self, *event) -> None:
        try:
            self.events.put_nowait(event)
        except queue.Full:
            log.warning("Event-Queue voll, Ereignis verworfen: %s", event[0])

    def _handle(self, ev: tuple, now: float) -> None:
        kind = ev[0]
        if kind == "note_on":
            _, note, vel, ch, source = ev
            if vel <= 0:
                return self._handle(("note_off", note, ch, source), now)
            key = self.keymap.note_to_key(note)
            if key is None:
                return
            color = None
            syn = self.synthesia
            if syn["enabled"]:
                if ch == syn["left_channel"]:
                    color = tuple(syn["left_color"])
                elif ch == syn["right_channel"]:
                    color = tuple(syn["right_color"])
                elif syn["hide_normal_notes"]:
                    return
            k = self.state.note_on(key, vel, ch, color=color, source=source, now=now)
            i = key - self.keymap.lowest
            self.intensity[i] = 1.0
            self.key_color[i] = self._color_for_key(key, k)
            self.key_vel[i] = self._velocity_factor(vel)
        elif kind == "note_off":
            _, note, ch, source = ev
            key = self.keymap.note_to_key(note)
            if key is not None:
                self.state.note_off(key, now=now)
        elif kind == "cc":
            _, control, value, ch = ev
            if control == 64:
                self.state.set_sustain(value, now=now)
            elif control in (120, 123):
                self.state.all_off(now=now)
            self.state.last_activity = now
        elif kind == "activity":
            self.state.last_activity = now
        elif kind == "config":
            self._apply_config(ev[1])
        elif kind == "panic":
            self.state.all_off(now=now)
            self.intensity[:] = 0
            self.force_full = True
        elif kind == "test":
            self.test_pattern = ev[1]
            self.test_until = now + float(ev[1].get("seconds", 5))
            self.force_full = True
        elif kind == "player":
            self.player_active = bool(ev[1])

    # -- Frame ----------------------------------------------------------
    def _advance(self, dt: float, now: float) -> None:
        look = self.cfg["look"]
        mode = look["light_mode"]
        lit = np.fromiter((k.lit for k in self.state.keys.values()), dtype=bool, count=len(self.state.keys))
        if mode == "normal":
            self.intensity = lit.astype(np.float32)
        else:
            step = dt * 1000.0 / max(50.0, float(look["fade_ms"]))
            self.intensity = np.where(lit, 1.0, np.maximum(0.0, self.intensity - step)).astype(np.float32)

    def _render_keys(self, now: float) -> np.ndarray:
        frame = self.backlight.copy()
        look = self.cfg["look"]
        active = np.nonzero(self.intensity > 0.001)[0]
        if active.size == 0:
            return frame
        level = self.intensity[active] * self.key_vel[active]
        colors = self.key_color[active]
        if look["color_mode"] == "rainbow":
            rb = look["rainbow"]
            fixed = np.array([self.state.keys[int(i) + self.keymap.lowest].color is not None for i in active])
            rbcol = effects.rainbow_colors(self.key_center[active], rb["offset"], rb["scale"], rb["speed"], now)
            colors = np.where(fixed[:, None], colors, rbcol)
        adj = look["adjacent"]
        adj_color = np.array(adj["color"], dtype=np.float32) / 255.0
        for idx, i in enumerate(active.tolist()):
            col = colors[idx] * level[idx]
            leds = self.key_leds[i]
            for led in leds:
                frame[led] = col
            if adj["mode"] != "off" and leds:
                lo, hi = min(leds) - 1, max(leds) + 1
                acol = col if adj["mode"] == "same" else adj_color * level[idx]
                if lo >= 0:
                    frame[lo] = np.maximum(frame[lo], acol)
                if hi < self.led_count:
                    frame[hi] = np.maximum(frame[hi], acol)
        return frame

    def _render_test(self, now: float) -> np.ndarray:
        frame = np.zeros((self.led_count, 3), dtype=np.float32)
        tp = self.test_pattern or {}
        kind = tp.get("kind", "keys")
        color = np.array(tp.get("color", [255, 255, 255]), dtype=np.float32) / 255.0
        if kind == "all":
            frame[:] = color
        elif kind == "leds":
            for led in tp.get("leds", []):
                if 0 <= int(led) < self.led_count:
                    frame[int(led)] = color
        elif kind == "keys":
            for key in tp.get("keys", []):
                for led in self.keymap.leds_for_key(int(key)):
                    frame[led] = color
        elif kind == "chase":
            pos = int((now * 60) % self.led_count)
            frame[pos] = color
        elif kind == "rgb":  # Farbreihenfolge prüfen: rot, grün, blau nacheinander
            phase = int(now) % 3
            frame[:, phase] = 1.0
        return frame

    def _idle_frame(self, now: float) -> np.ndarray | None:
        idle = self.idle_cfg
        if not idle["enabled"] or self.player_active:
            self.idle_animation_active = False
            return None
        if (now - self.state.last_activity) < idle["after_min"] * 60:
            self.idle_animation_active = False
            return None
        self.idle_animation_active = True
        fn = effects.ANIMATIONS.get(idle["animation"], effects.anim_rainbow_cycle)
        return fn(now, self.led_count) * (idle["brightness"] / 100.0)

    # -- Hauptschleife --------------------------------------------------
    def run(self) -> None:
        period = 1.0 / self.fps
        last = time.monotonic()
        last_show = 0.0
        last_publish = 0.0
        last_stuck_check = last
        log.info("Renderer gestartet (%d fps, %d LEDs)", self.fps, self.led_count)
        while not self._stop.is_set():
            t0 = time.monotonic()
            dt = t0 - last
            last = t0
            try:
                while True:
                    self._handle(self.events.get_nowait(), t0)
            except queue.Empty:
                pass
            except Exception:
                log.exception("Fehler bei Event-Verarbeitung")
            if t0 - last_stuck_check > 1.0:
                last_stuck_check = t0
                n = self.state.expire_stuck(t0)
                if n:
                    log.warning("%d hängende Taste(n) nach Timeout ausgeschaltet", n)
            period = 1.0 / self.fps
            try:
                self._advance(dt, t0)
                if self.test_pattern is not None:
                    if t0 > self.test_until:
                        self.test_pattern = None
                        self.force_full = True
                        frame = self._render_keys(t0)
                    else:
                        frame = self._render_test(t0)
                else:
                    frame = self._idle_frame(t0)
                    if frame is None:
                        frame = self._render_keys(t0)
                out = self.pipeline.process(frame)
                if out.shape[0] != self.driver.led_count:  # LED-Anzahl geändert: Treiber erst nach Neustart
                    fixed = np.zeros((self.driver.led_count, 3), dtype=np.uint8)
                    n = min(out.shape[0], self.driver.led_count)
                    fixed[:n] = out[:n]
                    out = fixed
                force = self.force_full or (t0 - last_show) > self.full_refresh_s
                if self.driver.show(out, force=force):
                    last_show = t0
                    self.frame_count += 1
                self.force_full = False
            except Exception:
                log.exception("Renderfehler")
            if self.state.changed and self.on_keys_changed and (t0 - last_publish) > 0.03:
                self.state.changed = False
                last_publish = t0
                try:
                    self.on_keys_changed(self.snapshot())
                except Exception:
                    log.exception("Fehler beim Veröffentlichen des Tastenzustands")
            elapsed = time.monotonic() - t0
            self.last_tick_ms = elapsed * 1000
            self.max_tick_ms = max(self.max_tick_ms, self.last_tick_ms)
            remaining = period - elapsed
            if remaining > 0:
                self._stop.wait(remaining)
        self.driver.close()

    def snapshot(self) -> dict:
        return {
            "lit": self.state.lit_keys(),
            "pressed": self.state.pressed_keys(),
            "sustain": self.state.sustain,
            "idle_animation": self.idle_animation_active,
            "estimated_ma": round(self.pipeline.last_estimated_ma),
            "power_scale": round(self.pipeline.last_power_scale, 3),
        }

    def stop(self) -> None:
        self._stop.set()
