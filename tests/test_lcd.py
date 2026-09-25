from pianoled.app import App
from pianoled.lcd import screens
from pianoled.lcd.screens import ScreenState
from pianoled.lcd.ui import LcdUI


def test_screens_render_all_modes():
    for wifi in ({"mode": "wifi", "ssid": "Home", "ip": "192.168.1.5", "signal": 70},
                 {"mode": "hotspot", "hotspot_ssid": "PianoLED", "hotspot_password": "pianoled123", "ip": "10.42.0.1"},
                 {"mode": "connecting", "connecting_to": "Home"}, {"mode": "offline"}, {"mode": "unavailable"}):
        s = ScreenState(wifi=wifi, transpose=2, toast="Hallo")
        assert screens.status(s).size == (128, 128)
        assert screens.qr(s).size == (128, 128)
    assert screens.menu("Menü", ["a", "b"], 1).size == (128, 128)
    assert screens.boot().size == (128, 128)


def test_qr_payloads():
    hs = ScreenState(wifi={"mode": "hotspot", "hotspot_ssid": "Piano;LED", "hotspot_password": "pw:12345678"})
    assert screens.qr_payload(hs)[0] == r"WIFI:T:WPA;S:Piano\;LED;P:pw\:12345678;;"
    wifi = ScreenState(wifi={"mode": "wifi", "ip": "192.168.1.5"})
    assert screens.qr_payload(wifi)[0] == "http://192.168.1.5"


def test_fit_shrinks_long_text():
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    text, f = screens.fit(d, "192.168.178.123", 120, 17, bold=True)
    assert screens.text_width(d, text, f) <= 120


def test_buttons_drive_app(tmp_path):
    app = App(str(tmp_path / "c.json"), str(tmp_path), simulate=True)
    ui = LcdUI(app, use_hardware=False)
    ui.press("right")
    assert app.config.get("look.brightness") == 55
    ui.press("up")
    assert app.config.get("transpose.semitones") == 1
    ui.press("press")
    assert ui.page == "qr"
    ui.press("key2")
    assert ui.page == "status"
    ui.press("key1")
    assert ui.page == "menu"
    ui.press("down")
    ui.press("left")                       # Transpose-Zeile: ◀ verringert
    assert app.config.get("transpose.semitones") == 0
    assert ui.render().size == (128, 128)
    # Display aus: erster Druck weckt nur
    ui.screen_on = False
    ui.press("right")
    assert app.config.get("look.brightness") == 55 and ui.screen_on
