"""Hardware-Treiber für den Waveshare 1.44" LCD-Hat (ST7735S, 128×128) über spidev + gpiozero.

Portiert aus dem Waveshare-Beispielcode (LCD_1in44.py), reduziert auf das Nötige:
Init-Sequenz, Fensteradressierung, Bild anzeigen (RGB565), Hintergrundbeleuchtung.
"""
from __future__ import annotations

import time

import numpy as np

WIDTH = 128
HEIGHT = 128
X_ADJ = 1   # entspricht LCD_X_Adjust = LCD_Y im Waveshare-Code
Y_ADJ = 2

PIN_RST = 27
PIN_DC = 25
PIN_BL = 24


class ST7735:
    def __init__(self, rotation: int = 0):
        import spidev
        from gpiozero import OutputDevice

        self.spi = spidev.SpiDev(0, 0)
        self.spi.max_speed_hz = 16_000_000
        self.spi.mode = 0
        self.rst = OutputDevice(PIN_RST, initial_value=True)
        self.dc = OutputDevice(PIN_DC, initial_value=False)
        self.bl = OutputDevice(PIN_BL, initial_value=True)
        self.rotation = rotation
        self._init()

    # -- Low level --------------------------------------------------------
    def _cmd(self, reg: int, *data: int) -> None:
        self.dc.off()
        self.spi.writebytes([reg])
        if data:
            self.dc.on()
            self.spi.writebytes(list(data))

    def _reset(self) -> None:
        self.rst.on(); time.sleep(0.1)
        self.rst.off(); time.sleep(0.1)
        self.rst.on(); time.sleep(0.1)

    def _init(self) -> None:
        self._reset()
        c = self._cmd
        c(0xB1, 0x01, 0x2C, 0x2D)
        c(0xB2, 0x01, 0x2C, 0x2D)
        c(0xB3, 0x01, 0x2C, 0x2D, 0x01, 0x2C, 0x2D)
        c(0xB4, 0x07)
        c(0xC0, 0xA2, 0x02, 0x84)
        c(0xC1, 0xC5)
        c(0xC2, 0x0A, 0x00)
        c(0xC3, 0x8A, 0x2A)
        c(0xC4, 0x8A, 0xEE)
        c(0xC5, 0x0E)
        c(0xE0, 0x0F, 0x1A, 0x0F, 0x18, 0x2F, 0x28, 0x20, 0x22, 0x1F, 0x1B, 0x23, 0x37, 0x00, 0x07, 0x02, 0x10)
        c(0xE1, 0x0F, 0x1B, 0x0F, 0x17, 0x33, 0x2C, 0x29, 0x2E, 0x30, 0x30, 0x39, 0x3F, 0x00, 0x07, 0x03, 0x10)
        c(0xF0, 0x01)
        c(0xF6, 0x00)
        c(0x3A, 0x05)  # 16 Bit Farbe
        # Scanrichtung U2D_R2L (wie Waveshare-Default) + RGB-Bit
        madctl = {0: 0x60, 90: 0x00, 180: 0xA0, 270: 0xC0}.get(self.rotation, 0x60)
        c(0x36, madctl | 0x08)
        time.sleep(0.2)
        c(0x11)
        time.sleep(0.12)
        c(0x29)

    def _window(self, x0: int, y0: int, x1: int, y1: int) -> None:
        self._cmd(0x2A, 0x00, (x0 & 0xFF) + X_ADJ, 0x00, ((x1 - 1) & 0xFF) + X_ADJ)
        self._cmd(0x2B, 0x00, (y0 & 0xFF) + Y_ADJ, 0x00, ((y1 - 1) & 0xFF) + Y_ADJ)
        self._cmd(0x2C)

    # -- API ------------------------------------------------------------
    def backlight(self, on: bool) -> None:
        (self.bl.on if on else self.bl.off)()

    def show(self, image) -> None:
        """PIL-Bild (128×128, RGB) anzeigen."""
        img = np.asarray(image.convert("RGB"), dtype=np.uint16)
        r = (img[..., 0] & 0xF8) << 8
        g = (img[..., 1] & 0xFC) << 3
        b = img[..., 2] >> 3
        pix = (r | g | b).astype(">u2").tobytes()
        self._window(0, 0, WIDTH, HEIGHT)
        self.dc.on()
        for i in range(0, len(pix), 4096):
            self.spi.writebytes2(pix[i:i + 4096])

    def clear(self) -> None:
        from PIL import Image
        self.show(Image.new("RGB", (WIDTH, HEIGHT), "black"))

    def close(self) -> None:
        try:
            self.clear()
            self.backlight(False)
            self.spi.close()
        except Exception:
            pass
