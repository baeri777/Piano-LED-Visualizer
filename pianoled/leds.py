"""LED-Treiber: einziger Besitzer des ws281x-Objekts.

`show(frame)` bekommt ein numpy-Array (led_count × 3, uint8, RGB) und schreibt nur
geänderte Pixel in den Treiber. Helligkeit, Leistungsbegrenzung und Gamma werden
vorher in `FramePipeline` in Software berechnet, damit das Treiberobjekt nie neu
erzeugt werden muss (das war im alten Code eine Absturzquelle).
"""
from __future__ import annotations

import logging
import threading

import numpy as np

log = logging.getLogger(__name__)


class LedDriver:
    def __init__(self, led_count: int):
        self.led_count = led_count
        self.last = np.zeros((led_count, 3), dtype=np.uint8)

    def show(self, frame: np.ndarray, force: bool = False) -> bool:
        raise NotImplementedError

    def close(self) -> None:
        pass


class NullDriver(LedDriver):
    """Ohne Hardware (Entwicklung, Tests, Simulation)."""

    def __init__(self, led_count: int, on_show=None):
        super().__init__(led_count)
        self.on_show = on_show
        self.show_count = 0

    def show(self, frame: np.ndarray, force: bool = False) -> bool:
        if not force and self.show_count and np.array_equal(frame, self.last):
            return False
        self.last = frame.copy()
        self.show_count += 1
        if self.on_show:
            self.on_show(self.last)
        return True


class Ws281xDriver(LedDriver):
    def __init__(self, strip_cfg: dict):
        super().__init__(int(strip_cfg["led_count"]))
        from rpi_ws281x import PixelStrip, ws  # erst hier importieren: nur auf dem Pi vorhanden

        order = strip_cfg.get("color_order", "GRB")
        strip_type = getattr(ws, f"WS2811_STRIP_{order}", ws.WS2811_STRIP_GRB)
        self._strip = PixelStrip(
            self.led_count,
            int(strip_cfg["gpio_pin"]),
            int(strip_cfg["freq_hz"]),
            int(strip_cfg["dma"]),
            bool(strip_cfg["invert"]),
            255,
            int(strip_cfg["channel"]),
            strip_type,
        )
        self._strip.begin()
        self._lock = threading.Lock()
        self._packed_last = np.full(self.led_count, -1, dtype=np.int64)
        log.info("ws281x initialisiert: %d LEDs an GPIO %s", self.led_count, strip_cfg["gpio_pin"])

    def show(self, frame: np.ndarray, force: bool = False) -> bool:
        packed = (frame[:, 0].astype(np.int64) << 16) | (frame[:, 1].astype(np.int64) << 8) | frame[:, 2].astype(np.int64)
        if force:
            changed = np.arange(self.led_count)
        else:
            changed = np.nonzero(packed != self._packed_last)[0]
            if changed.size == 0:
                return False
        with self._lock:
            set_pixel = self._strip.setPixelColor
            for i in changed.tolist():
                set_pixel(i, int(packed[i]))
            self._strip.show()
        self._packed_last = packed
        self.last = frame
        return True

    def close(self) -> None:
        try:
            with self._lock:
                for i in range(self.led_count):
                    self._strip.setPixelColor(i, 0)
                self._strip.show()
        except Exception:
            pass


class FramePipeline:
    """Helligkeit → Leistungsbegrenzung → Gamma. Rechnet auf float32-Frames (0..1)."""

    def __init__(self, strip_cfg: dict, brightness_percent: int):
        self.configure(strip_cfg, brightness_percent)

    def configure(self, strip_cfg: dict, brightness_percent: int) -> None:
        self.led_count = int(strip_cfg["led_count"])
        self.brightness = max(0.0, min(1.0, brightness_percent / 100.0))
        self.max_ma = float(strip_cfg.get("max_current_ma", 0) or 0)
        self.ma_per_channel = float(strip_cfg.get("ma_per_channel", 20))
        self.ma_idle = float(strip_cfg.get("ma_idle_per_led", 1)) * self.led_count
        gamma = float(strip_cfg.get("gamma", 2.2))
        self._lut = (np.power(np.linspace(0, 1, 256, dtype=np.float32), gamma) * 255 + 0.5).astype(np.uint8)
        self.last_estimated_ma = 0.0
        self.last_power_scale = 1.0

    def estimate_ma(self, frame01: np.ndarray) -> float:
        return float(frame01.sum()) * self.ma_per_channel + self.ma_idle

    def process(self, frame01: np.ndarray) -> np.ndarray:
        f = np.clip(frame01, 0.0, 1.0) * self.brightness
        est = self.estimate_ma(f)
        scale = 1.0
        if self.max_ma > 0 and est > self.max_ma:
            budget = max(0.0, self.max_ma - self.ma_idle)
            draw = max(1e-6, est - self.ma_idle)
            scale = budget / draw
            f *= scale
            est = self.estimate_ma(f)
        self.last_estimated_ma = est
        self.last_power_scale = scale
        idx = (f * 255 + 0.5).astype(np.uint8)
        return self._lut[idx]


def make_driver(strip_cfg: dict, simulate: bool = False, on_show=None) -> LedDriver:
    """Echten Treiber erzeugen; ohne Hardware (oder im Simulationsmodus) den Null-Treiber."""
    if not simulate:
        try:
            return Ws281xDriver(strip_cfg)
        except Exception as exc:
            log.error("LED-Treiber nicht verfügbar (%s), Simulation aktiv", exc)
    return NullDriver(int(strip_cfg["led_count"]), on_show=on_show)
