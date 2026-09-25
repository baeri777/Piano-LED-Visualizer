"""Display-Steuerung: Seiten, Tasten, Hinweise, Energiesparen.

Bedienung (Waveshare 1,44"-Hat)
-------------------------------
Statusseite   Joystick ◀ ▶ Helligkeit, ▲ ▼ Transpose, ● QR-Code, KEY1 Menü, KEY3 alle LEDs aus
QR-Seite      ● oder KEY2 zurück
Menü          ▲ ▼ wählen, ● bzw. ▶ ausführen, ◀ ▶ bei Helligkeit/Transpose ändern, KEY2 zurück

Das Display läuft auch ohne Hardware (`device=None`): Dann wird nur gezeichnet, und
die Web-App kann das aktuelle Bild unter /api/lcd.png als Vorschau abrufen.
"""
from __future__ import annotations

import logging
import threading
import time

from . import screens
from .screens import ScreenState

log = logging.getLogger(__name__)

PINS = {"up": 6, "down": 19, "left": 5, "right": 26, "press": 13, "key1": 21, "key2": 20, "key3": 16}
TOAST_S = 2.5
MIN_REDRAW_S = 0.15
IDLE_REDRAW_S = 5.0


class LcdUI(threading.Thread):
    def __init__(self, app, use_hardware: bool = True):
        super().__init__(name="lcd", daemon=True)
        self.app = app
        cfg = app.config.get("features.lcd")
        self.off_after_s = float(cfg.get("off_after_min", 5)) * 60
        self.device = None
        if use_hardware:
            from .device import ST7735
            self.device = ST7735(rotation=int(cfg.get("rotation", 0)))
            self.device.show(screens.boot())
        self._stop_event = threading.Event()
        self._dirty = threading.Event()
        self._buttons: list = []
        self.page = "status"
        self.menu_index = 0
        self.toast: tuple[str, tuple, float] | None = None
        self.last_input = time.monotonic()
        self.screen_on = True
        self.image = screens.boot()
        self._last_wifi_mode: str | None = None
        self._last_piano: bool | None = None
        app.bus.subscribe(self._on_event)

    # =================================================================
    # Menü
    # =================================================================
    def _menu_items(self) -> list[tuple[str, callable, bool]]:
        """(Beschriftung, Aktion, mit ◀ ▶ einstellbar)."""
        a = self.app
        brightness = a.config.get("look.brightness")
        transpose = a.config.get("transpose.semitones")
        hotspot = a.network_state().get("mode") == "hotspot"
        return [
            (f"Helligkeit  ◀ {brightness}% ▶", lambda d: self._change_brightness(d * 5), True),
            (f"Transpose  ◀ {transpose:+d} ▶" if transpose else "Transpose  ◀ 0 ▶",
             lambda d: self._change_transpose(d), True),
            ("Kalibrieren", lambda d: self._do(a.start_calibration, None), False),
            ("Nächstes Preset", lambda d: self._do(a.next_preset, "Preset gewechselt"), False),
            ("Alle LEDs aus", lambda d: self._do(a.panic, "LEDs aus"), False),
            ("QR-Code zeigen", lambda d: self._goto("qr"), False),
            ("Hotspot beenden" if hotspot else "Hotspot starten",
             lambda d: self._do_async(a.stop_hotspot if hotspot else a.start_hotspot,
                                      "Hotspot wird beendet" if hotspot else "Hotspot startet"), False),
            ("Dienst neu starten", lambda d: self._do_async(a.restart_service, "Starte neu …"), False),
            ("Pi neu starten", lambda d: self._do_async(a.reboot, "Pi startet neu …"), False),
            ("Ausschalten", lambda d: self._do_async(a.shutdown, "Fährt herunter …"), False),
        ]

    def _change_brightness(self, delta: int) -> None:
        if delta:
            v = self.app.set_brightness(int(self.app.config.get("look.brightness")) + delta)
            self.show_toast(f"Helligkeit {v} %")

    def _change_transpose(self, delta: int) -> None:
        if delta:
            v = self.app.set_transpose(int(self.app.config.get("transpose.semitones")) + delta)
            self.show_toast(f"Transpose {v:+d}" if v else "Transpose 0")

    def _do(self, fn, message: str | None) -> None:
        fn()
        self.page = "status"
        if message:
            self.show_toast(message)

    def _do_async(self, fn, message: str) -> None:
        self.show_toast(message, screens.WARN)
        threading.Thread(target=fn, daemon=True).start()
        self.page = "status"

    def _goto(self, page: str) -> None:
        self.page = page

    # =================================================================
    # Ereignisse
    # =================================================================
    def show_toast(self, text: str, color=screens.ACCENT) -> None:
        self.toast = (text, color, time.monotonic() + TOAST_S)
        self._wake_screen()
        self._dirty.set()

    def _on_event(self, topic: str, payload) -> None:
        if topic == "wifi":
            mode = payload.get("mode")
            if mode != self._last_wifi_mode and self._last_wifi_mode is not None:
                if mode == "wifi":
                    self.show_toast("WLAN verbunden", screens.OK)
                elif mode == "hotspot":
                    self.show_toast("Hotspot gestartet", screens.WARN)
                elif mode == "offline" and self._last_wifi_mode == "wifi":
                    self.show_toast("WLAN getrennt", screens.ERR)
            self._last_wifi_mode = mode
            self._dirty.set()
        elif topic == "wifi_attempt" and payload.get("status") == "failed":
            self.show_toast("WLAN-Fehler: " + (payload.get("message") or ""), screens.ERR)
        elif topic == "midi":
            connected = bool(payload.get("connected"))
            if self._last_piano is not None and connected != self._last_piano:
                self.show_toast("Piano verbunden" if connected else "Piano getrennt",
                                screens.OK if connected else screens.ERR)
            self._last_piano = connected
            self._dirty.set()
        elif topic in ("config", "transpose", "calibration", "preset"):
            self._dirty.set()

    def _setup_buttons(self) -> None:
        if self.device is None:
            return
        from gpiozero import Button
        for name, pin in PINS.items():
            try:
                button = Button(pin, pull_up=True, bounce_time=0.03, hold_time=0.4, hold_repeat=True)
                button.when_pressed = (lambda n: (lambda: self.press(n)))(name)
                if name in ("left", "right", "up", "down"):
                    button.when_held = (lambda n: (lambda: self.press(n, repeat=True)))(name)
                self._buttons.append(button)
            except Exception as exc:
                log.warning("Taste %s (GPIO %s) nicht verfügbar: %s", name, pin, exc)

    def _wake_screen(self) -> bool:
        """Display einschalten. Gibt True zurück, wenn es aus war (Tastendruck nur zum Wecken)."""
        self.last_input = time.monotonic()
        if self.screen_on:
            return False
        self.screen_on = True
        if self.device:
            self.device.backlight(True)
        return True

    def press(self, name: str, repeat: bool = False) -> None:
        """Tastendruck verarbeiten (auch für Tests und die Web-Vorschau nutzbar)."""
        if self._wake_screen() and not repeat:
            self._dirty.set()
            return
        try:
            self._press(name, repeat)
        except Exception as exc:
            log.exception("Tastenaktion fehlgeschlagen")
            self.show_toast(f"Fehler: {exc}", screens.ERR)
        self._dirty.set()

    def _press(self, name: str, repeat: bool) -> None:
        if name == "key3":
            self.app.panic()
            self.show_toast("Alle LEDs aus")
            return
        if name == "key2":
            self.page = "status"
            return
        if self.page == "status":
            if name == "key1":
                self.page, self.menu_index = "menu", 0
            elif name == "press":
                self.page = "qr"
            elif name in ("left", "right"):
                self._change_brightness(-5 if name == "left" else 5)
            elif name in ("up", "down") and not repeat:
                self._change_transpose(1 if name == "up" else -1)
        elif self.page == "qr":
            if name in ("press", "key1"):
                self.page = "status" if name == "press" else "menu"
        elif self.page == "menu":
            items = self._menu_items()
            if name == "key1":
                self.page = "status"
            elif name == "up":
                self.menu_index = (self.menu_index - 1) % len(items)
            elif name == "down":
                self.menu_index = (self.menu_index + 1) % len(items)
            else:
                _label, action, adjustable = items[self.menu_index]
                if adjustable and name in ("left", "right"):
                    action(-1 if name == "left" else 1)
                elif not adjustable and name in ("press", "right") and not repeat:
                    action(0)

    # =================================================================
    # Zeichnen
    # =================================================================
    def screen_state(self) -> ScreenState:
        a = self.app
        midi = a.midi_status()
        toast = None
        color = screens.ACCENT
        if self.toast and time.monotonic() < self.toast[2]:
            toast, color = self.toast[0], self.toast[1]
        return ScreenState(
            wifi=a.network_state(),
            piano_connected=bool(midi.get("connected")),
            transpose=int(a.config.get("transpose.semitones")),
            brightness=int(a.config.get("look.brightness")),
            hostname=a.hostname(),
            calibrating=a.calibrating,
            toast=toast,
            toast_color=color,
            web_port=int(a.config.get("web.port")),
        )

    def render(self):
        s = self.screen_state()
        if self.page == "qr":
            return screens.qr(s)
        if self.page == "menu":
            items = [item[0] for item in self._menu_items()]
            self.menu_index = min(self.menu_index, len(items) - 1)
            return screens.menu("Menü", items, self.menu_index, s)
        return screens.status(s)

    def run(self) -> None:
        self._setup_buttons()
        last_draw = 0.0
        while not self._stop_event.is_set():
            now = time.monotonic()
            if self.screen_on and self.off_after_s > 0 and now - self.last_input > self.off_after_s \
                    and not self.app.calibrating and self.page == "status":
                self.screen_on = False
                if self.device:
                    self.device.backlight(False)
            toast_expired = self.toast is not None and now >= self.toast[2]
            if toast_expired:
                self.toast = None
            if not self.screen_on:   # Display aus: nichts zeichnen, auf Tastendruck warten
                self._dirty.clear()
                self._dirty.wait(1.0)
                continue
            want = self._dirty.is_set() or toast_expired or now - last_draw >= IDLE_REDRAW_S
            if want:
                wait = MIN_REDRAW_S - (now - last_draw)
                if wait > 0:   # Zeichenrate begrenzen, ohne Busy-Loop
                    self._stop_event.wait(wait)
                    continue
                self._dirty.clear()
                try:
                    self.image = self.render()
                    if self.device:
                        self.device.show(self.image)
                except Exception:
                    log.exception("Display-Zeichenfehler")
                last_draw = time.monotonic()
            self._dirty.wait(1.0)
        if self.device:
            try:
                self.device.show(screens.boot("Beendet"))
                time.sleep(0.05)
            except Exception:
                pass
        for b in self._buttons:
            try:
                b.close()
            except Exception:
                pass

    def stop(self) -> None:
        self.app.bus.unsubscribe(self._on_event)
        self._stop_event.set()
        self._dirty.set()
