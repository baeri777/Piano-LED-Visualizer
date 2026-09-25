"""Bildschirme für das 128×128-Display, als reine Funktionen (PIL).

Nichts hier spricht mit Hardware. Dadurch lassen sich alle Seiten testen und über
/api/lcd.png in der App bzw. am PC als Vorschau ansehen.

Seiten
------
status   Netzwerk groß (WLAN + IP oder Hotspot + Passwort), Piano-Status, Transpose, Helligkeit
qr       QR-Code: im Hotspot zum Beitreten des WLANs, sonst die Adresse der App
menu     Liste mit Auswahlbalken
boot     Startbildschirm
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

W = H = 128

BG = (8, 10, 14)
FG = (236, 239, 245)
MUTED = (132, 141, 160)
DIM = (70, 76, 92)
PANEL = (24, 28, 36)
ACCENT = (80, 140, 255)
OK = (60, 215, 130)
WARN = (255, 170, 40)
ERR = (255, 85, 85)

FONT_PATHS = {
    False: ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",),
    True: ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",),
}


@functools.lru_cache(maxsize=64)
def font(size: int, bold: bool = False):
    for path in FONT_PATHS[bold]:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def text_width(d: ImageDraw.ImageDraw, text: str, f) -> int:
    return int(d.textlength(text, font=f))


def fit(d: ImageDraw.ImageDraw, text: str, max_width: int, size: int, bold: bool = False, min_size: int = 8):
    """Größte Schrift ≤ size, in die der Text passt; notfalls gekürzt mit …"""
    for s in range(size, min_size - 1, -1):
        f = font(s, bold)
        if text_width(d, text, f) <= max_width:
            return text, f
    f = font(min_size, bold)
    while text and text_width(d, text + "…", f) > max_width:
        text = text[:-1]
    return text + "…", f


def draw_fit(d, xy, text, max_width, size, fill, bold=False, anchor="la"):
    t, f = fit(d, text, max_width, size, bold)
    d.text(xy, t, font=f, fill=fill, anchor=anchor)


@dataclass
class ScreenState:
    """Alles, was das Display anzeigen kann. Wird von der LCD-Steuerung befüllt."""
    wifi: dict = field(default_factory=dict)
    piano_connected: bool = False
    transpose: int = 0
    brightness: int = 50
    hostname: str = "pianoled"
    calibrating: bool = False
    toast: str | None = None
    toast_color: tuple = ACCENT
    web_port: int = 80


# ---------------------------------------------------------------------------
# Bausteine
# ---------------------------------------------------------------------------
def _header(d: ImageDraw.ImageDraw, s: ScreenState) -> None:
    d.text((4, 3), "Piano LED", font=font(11, True), fill=FG)
    # Piano-Status rechts: Punkt + Text
    color, label = (OK, "Piano") if s.piano_connected else (ERR, "kein Piano")
    f = font(9)
    tw = text_width(d, label, f)
    d.text((W - 4 - tw, 4), label, font=f, fill=MUTED)
    d.ellipse((W - 4 - tw - 9, 6, W - 4 - tw - 3, 12), fill=color)
    d.line((0, 17, W, 17), fill=PANEL)


def _wifi_icon(d: ImageDraw.ImageDraw, x: int, y: int, color, bars: int = 3) -> None:
    """Kleines WLAN-Symbol (Viertelkreise), bars = 0..3."""
    for i in range(3):
        r = 3 + i * 3
        c = color if i < bars else DIM
        d.arc((x - r, y - r, x + r, y + r), 225, 315, fill=c, width=2)
    d.ellipse((x - 1, y - 1, x + 1, y + 1), fill=color if bars else DIM)


def _signal_bars(signal) -> int:
    if signal is None:
        return 3
    return 3 if signal >= 65 else 2 if signal >= 40 else 1


def _url(s: ScreenState, host: str) -> str:
    return f"http://{host}" + ("" if s.web_port == 80 else f":{s.web_port}")


def _tiles(d: ImageDraw.ImageDraw, s: ScreenState, y: int) -> None:
    for i, (label, value) in enumerate((("Transpose", f"{s.transpose:+d}" if s.transpose else "0"),
                                        ("Helligkeit", f"{s.brightness}%"))):
        x0 = 3 + i * 63
        d.rounded_rectangle((x0, y, x0 + 59, y + 30), radius=5, fill=PANEL)
        d.text((x0 + 30, y + 3), label, font=font(8), fill=MUTED, anchor="ma")
        color = WARN if (i == 0 and s.transpose) else FG
        d.text((x0 + 30, y + 13), value, font=font(14, True), fill=color, anchor="ma")


def _toast(d: ImageDraw.ImageDraw, s: ScreenState) -> None:
    if not s.toast:
        return
    d.rectangle((0, H - 17, W, H), fill=s.toast_color)
    draw_fit(d, (W // 2, H - 9), s.toast, W - 6, 11, (255, 255, 255), bold=True, anchor="mm")


# ---------------------------------------------------------------------------
# Seiten
# ---------------------------------------------------------------------------
def status(s: ScreenState) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _header(d, s)
    w = s.wifi or {}
    mode = w.get("mode", "offline")
    y = 22
    if mode == "wifi":
        _wifi_icon(d, 9, y + 10, OK, _signal_bars(w.get("signal")))
        d.text((20, y), "WLAN verbunden", font=font(10, True), fill=OK)
        draw_fit(d, (4, y + 14), w.get("ssid") or "", W - 8, 12, FG)
        draw_fit(d, (W // 2, y + 31), w.get("ip") or "", W - 6, 17, FG, bold=True, anchor="ma")
        draw_fit(d, (W // 2, y + 51), _url(s, f"{s.hostname}.local").replace("http://", ""), W - 8, 10, MUTED, anchor="ma")
    elif mode == "hotspot":
        _wifi_icon(d, 9, y + 10, WARN, 3)
        d.text((20, y), "Hotspot aktiv", font=font(10, True), fill=WARN)
        d.text((4, y + 15), "WLAN", font=font(9), fill=MUTED)
        draw_fit(d, (34, y + 13), w.get("hotspot_ssid") or "", W - 38, 12, FG, bold=True)
        d.text((4, y + 30), "Passw.", font=font(9), fill=MUTED)
        draw_fit(d, (34, y + 28), w.get("hotspot_password") or "", W - 38, 12, FG, bold=True)
        draw_fit(d, (W // 2, y + 45), _url(s, w.get("ip") or "10.42.0.1").replace("http://", ""),
                 W - 8, 14, ACCENT, bold=True, anchor="ma")
    elif mode == "connecting":
        _wifi_icon(d, 9, y + 10, ACCENT, 1)
        d.text((20, y), "Verbinde …", font=font(10, True), fill=ACCENT)
        draw_fit(d, (4, y + 16), w.get("connecting_to") or w.get("ssid") or "WLAN", W - 8, 13, FG, bold=True)
        d.text((4, y + 36), "Einen Moment bitte", font=font(9), fill=MUTED)
    elif mode == "unavailable":
        _wifi_icon(d, 9, y + 10, DIM, 0)
        d.text((20, y), "Kein WLAN-Modul", font=font(10, True), fill=MUTED)
    else:
        _wifi_icon(d, 9, y + 10, ERR, 0)
        d.text((20, y), "Kein WLAN", font=font(10, True), fill=ERR)
        d.text((4, y + 16), "Suche bekannte Netze …", font=font(9), fill=MUTED)
        d.text((4, y + 29), "Sonst startet gleich", font=font(9), fill=MUTED)
        d.text((4, y + 40), "der Hotspot.", font=font(9), fill=MUTED)
    _tiles(d, s, 84)
    if s.calibrating:
        d.rectangle((0, H - 17, W, H), fill=WARN)
        d.text((W // 2, H - 9), "Tiefste Taste drücken!", font=font(10, True), fill=(0, 0, 0), anchor="mm")
    elif s.toast:
        _toast(d, s)
    else:
        d.text((W // 2, H - 8), "● QR   K1 Menü   K3 Aus", font=font(8), fill=DIM, anchor="mm")
    return img


def qr_payload(s: ScreenState) -> tuple[str, str, str]:
    """(Inhalt, Titel, Untertitel) für die QR-Seite."""
    w = s.wifi or {}
    if w.get("mode") == "hotspot":
        ssid = (w.get("hotspot_ssid") or "").replace("\\", "\\\\").replace(";", "\;").replace(":", "\\:")
        pw = (w.get("hotspot_password") or "").replace("\\", "\\\\").replace(";", "\;").replace(":", "\\:")
        return f"WIFI:T:WPA;S:{ssid};P:{pw};;", "Mit Hotspot verbinden", "Kamera draufhalten"
    host = w.get("ip") or f"{s.hostname}.local"
    return _url(s, host), "App öffnen", _url(s, host).replace("http://", "")


def qr(s: ScreenState) -> Image.Image:
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    payload, title, subtitle = qr_payload(s)
    matrix = qr_matrix(payload)
    n = len(matrix)
    avail = H - 26
    scale = max(1, avail // n)
    size = n * scale
    x0 = (W - size) // 2
    y0 = 13
    for r, row in enumerate(matrix):
        for c, on in enumerate(row):
            if on:
                d.rectangle((x0 + c * scale, y0 + r * scale, x0 + (c + 1) * scale - 1, y0 + (r + 1) * scale - 1),
                            fill=(0, 0, 0))
    draw_fit(d, (W // 2, 1), title, W - 4, 10, (0, 0, 0), bold=True, anchor="ma")
    draw_fit(d, (W // 2, H - 11), subtitle, W - 4, 9, (60, 60, 60), anchor="ma")
    return img


@functools.lru_cache(maxsize=8)
def qr_matrix(payload: str) -> tuple[tuple[bool, ...], ...]:
    try:
        import qrcode
        q = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_L)
        q.add_data(payload)
        q.make(fit=True)
        return tuple(tuple(bool(v) for v in row) for row in q.get_matrix())
    except Exception:
        return ((False,),)


def menu(title: str, items: list[str], index: int, s: ScreenState | None = None) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((4, 3), title, font=font(11, True), fill=FG)
    d.line((0, 17, W, 17), fill=PANEL)
    rows = 6
    start = max(0, min(index - rows // 2, len(items) - rows))
    for row, i in enumerate(range(start, min(len(items), start + rows))):
        y = 21 + row * 16
        selected = i == index
        if selected:
            d.rounded_rectangle((2, y - 1, W - 3, y + 14), radius=4, fill=ACCENT)
        draw_fit(d, (8, y), items[i], W - 16, 11, FG if selected else (200, 205, 215), bold=selected)
    if len(items) > rows:   # Scrollbalken
        bar_h = max(8, int(96 * rows / len(items)))
        bar_y = 20 + int((96 - bar_h) * start / max(1, len(items) - rows))
        d.rectangle((W - 2, bar_y, W - 1, bar_y + bar_h), fill=MUTED)
    if s is not None and s.toast:
        _toast(d, s)
    else:
        d.text((W // 2, H - 8), "▲▼ wählen  ● ok  K2 zurück", font=font(8), fill=DIM, anchor="mm")
    return img


def boot(message: str = "Starte …") -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # stilisierte Tastatur
    for i in range(7):
        x = 22 + i * 12
        d.rectangle((x, 40, x + 10, 72), fill=FG)
    for i in (0, 1, 3, 4, 5):
        x = 30 + i * 12
        d.rectangle((x, 40, x + 6, 58), fill=BG)
    for i, c in enumerate(((255, 80, 80), (255, 200, 60), (80, 220, 120), (80, 160, 255), (180, 100, 255))):
        d.rectangle((22 + i * 17, 32, 22 + i * 17 + 15, 35), fill=c)
    d.text((W // 2, 84), "Piano LED", font=font(15, True), fill=FG, anchor="ma")
    d.text((W // 2, 106), message, font=font(10), fill=MUTED, anchor="ma")
    return img
