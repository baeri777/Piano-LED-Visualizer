"""Bedienoberfläche auf dem LCD-Hat: Statusseite und kleines Menü.

Tasten (BCM):  Joystick hoch 6 / runter 19 / links 5 / rechts 26 / Druck 13,
               KEY1 21 (Menü), KEY2 20 (Zurück), KEY3 16 (Alle LEDs aus).
Das Display wird nur neu gezeichnet, wenn sich etwas geändert hat (max. 2 Hz),
und nach `off_after_min` Minuten ohne Tastendruck abgeschaltet.
"""
from __future__ import annotations

import logging
import threading
import time

from PIL import Image, ImageDraw, ImageFont

from .st7735 import ST7735, WIDTH, HEIGHT

log = logging.getLogger(__name__)

PINS = {"up": 6, "down": 19, "left": 5, "right": 26, "press": 13, "key1": 21, "key2": 20, "key3": 16}


class LcdUI:
    def __init__(self, app):
        self.app = app
        cfg = app.config.get("features.lcd")
        self.off_after_s = float(cfg.get("off_after_min", 5)) * 60
        self.lcd = ST7735(rotation=int(cfg.get("rotation", 0)))
        self.font = self._font(13)
        self.font_small = self._font(11)
        self.font_big = self._font(22)
        self._stop = threading.Event()
        self._dirty = threading.Event()
        self.last_input = time.monotonic()
        self.screen_on = True
        self.menu_open = False
        self.menu_index = 0
        self.message: tuple[str, float] | None = None
        self.items = [
            ("Helligkeit", self._adjust_brightness),
            ("Transpose", self._adjust_transpose),
            ("Preset weiter", lambda d: self._action(self.app.next_preset, "Preset gewechselt")),
            ("Kalibrieren", lambda d: self._action(self.app.start_calibration, "Tiefste Taste drücken")),
            ("LEDs aus", lambda d: self._action(self.app.panic, "LEDs aus")),
            ("Hotspot an", lambda d: self._action(self.app.network.start_hotspot, "Hotspot startet")),
            ("Hotspot aus", lambda d: self._action(self.app.network.stop_hotspot, "Hotspot beendet")),
            ("Neustart", lambda d: self._sys("reboot")),
            ("Ausschalten", lambda d: self._sys("shutdown")),
        ]
        self._buttons = []
        self.thread = threading.Thread(target=self._run, name="lcd", daemon=True)
        app.on(lambda topic, payload: self._dirty.set())

    @staticmethod
    def _font(size: int):
        for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    # -- Tasten ----------------------------------------------------------
    def _setup_buttons(self) -> None:
        from gpiozero import Button
        for name, pin in PINS.items():
            try:
                b = Button(pin, pull_up=True, bounce_time=0.03)
                b.when_pressed = (lambda n: (lambda: self._on_button(n)))(name)
                self._buttons.append(b)
            except Exception as exc:
                log.warning("Taste %s (GPIO %s) nicht verfügbar: %s", name, pin, exc)

    def _on_button(self, name: str) -> None:
        self.last_input = time.monotonic()
        if not self.screen_on:
            self.screen_on = True
            self.lcd.backlight(True)
            self._dirty.set()
            return
        if name == "key3":
            self.app.panic()
            self._flash("LEDs aus")
        elif name == "key1":
            self.menu_open = not self.menu_open
        elif name == "key2":
            self.menu_open = False
        elif self.menu_open:
            if name == "up":
                self.menu_index = (self.menu_index - 1) % len(self.items)
            elif name == "down":
                self.menu_index = (self.menu_index + 1) % len(self.items)
            elif name in ("left", "right", "press"):
                delta = {"left": -1, "right": 1, "press": 0}[name]
                try:
                    self.items[self.menu_index][1](delta)
                except Exception as exc:
                    self._flash(f"Fehler: {exc}")
        else:
            if name in ("left", "right"):
                self._adjust_brightness(-5 if name == "left" else 5)
            elif name in ("up", "down"):
                self._adjust_transpose(1 if name == "up" else -1)
        self._dirty.set()

    def _adjust_brightness(self, delta: int) -> None:
        if delta:
            v = self.app.set_brightness(int(self.app.config.get("look.brightness")) + delta)
            self._flash(f"Helligkeit {v} %")

    def _adjust_transpose(self, delta: int) -> None:
        if delta:
            v = self.app.set_transpose(int(self.app.config.get("transpose.semitones")) + delta)
            self._flash(f"Transpose {v:+d}")

    def _action(self, fn, msg: str) -> None:
        fn()
        self._flash(msg)

    def _sys(self, action: str) -> None:
        from ..system import sysinfo
        self._flash("Neustart …" if action == "reboot" else "Ausschalten …")
        self.app.panic()
        threading.Timer(0.5, sysinfo.reboot if action == "reboot" else sysinfo.shutdown).start()

    def _flash(self, text: str) -> None:
        self.message = (text, time.monotonic() + 2.0)
        self._dirty.set()

    # -- Zeichnen ---------------------------------------------------------
    def _draw(self) -> Image.Image:
        img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
        d = ImageDraw.Draw(img)
        st = self.app.status()
        if self.menu_open:
            d.text((4, 2), "Menü", font=self.font, fill=(140, 150, 170))
            start = max(0, min(self.menu_index - 3, len(self.items) - 7))
            for row, idx in enumerate(range(start, min(len(self.items), start + 7))):
                y = 20 + row * 15
                sel = idx == self.menu_index
                if sel:
                    d.rectangle((0, y - 1, WIDTH, y + 13), fill=(60, 110, 255))
                d.text((6, y), self.items[idx][0], font=self.font, fill=(255, 255, 255) if sel else (200, 205, 215))
        else:
            midi = st["midi"]
            wifi = st.get("wifi") or {}
            d.text((4, 2), "Piano LED", font=self.font, fill=(140, 150, 170))
            d.ellipse((108, 4, 118, 14), fill=(60, 220, 130) if midi["connected"] else (255, 80, 80))
            if wifi.get("hotspot"):
                net = f"Hotspot {wifi.get('hotspot_ssid', '')}"
                ip = wifi.get("ip") or "10.42.0.1"
            elif wifi.get("connected"):
                net = wifi.get("ssid") or "WLAN"
                ip = wifi.get("ip") or ""
            else:
                net, ip = "Kein WLAN", ""
            d.text((4, 20), net[:18], font=self.font_small, fill=(220, 220, 230))
            d.text((4, 33), ip, font=self.font_small, fill=(160, 170, 190))
            tr = st["transpose"]
            d.text((4, 52), "Transpose", font=self.font_small, fill=(140, 150, 170))
            d.text((4, 64), f"{tr:+d}", font=self.font_big, fill=(255, 255, 255))
            d.text((70, 52), "Hell.", font=self.font_small, fill=(140, 150, 170))
            d.text((70, 64), f"{self.app.config.get('look.brightness')}%", font=self.font_big, fill=(255, 255, 255))
            temp = st["system"].get("cpu_temp")
            d.text((4, 96), f"{temp} °C" if temp is not None else "", font=self.font_small, fill=(160, 170, 190))
            d.text((4, 110), "K1 Menü  K3 LEDs aus", font=self.font_small, fill=(100, 110, 130))
            if st.get("calibrating"):
                d.rectangle((0, 86, WIDTH, 100), fill=(255, 140, 0))
                d.text((4, 87), "Tiefste Taste drücken", font=self.font_small, fill=(0, 0, 0))
        if self.message and time.monotonic() < self.message[1]:
            d.rectangle((0, HEIGHT - 18, WIDTH, HEIGHT), fill=(60, 110, 255))
            d.text((4, HEIGHT - 16), self.message[0][:20], font=self.font_small, fill=(255, 255, 255))
        return img

    def _run(self) -> None:
        self._setup_buttons()
        last_draw = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if self.screen_on and self.off_after_s > 0 and now - self.last_input > self.off_after_s:
                self.screen_on = False
                self.lcd.backlight(False)
            if self.screen_on and ((self._dirty.is_set() and now - last_draw > 0.25) or now - last_draw > 2.0):
                self._dirty.clear()
                try:
                    self.lcd.show(self._draw())
                except Exception:
                    log.exception("LCD-Zeichenfehler")
                last_draw = now
            self._dirty.wait(0.25)
        self.lcd.close()

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._dirty.set()
