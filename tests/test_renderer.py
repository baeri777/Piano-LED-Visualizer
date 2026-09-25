import copy
import time

import numpy as np

from pianoled.config import DEFAULTS, validate
from pianoled.leds import FramePipeline, NullDriver
from pianoled.renderer import Renderer


def make(**look):
    cfg = validate(copy.deepcopy(DEFAULTS))
    cfg["look"].update(look)
    cfg["strip"]["max_current_ma"] = 0
    drv = NullDriver(cfg["strip"]["led_count"])
    r = Renderer(cfg, drv)
    return r, drv


def tick(r, dt=0.02):
    now = time.monotonic()
    try:
        while True:
            r._handle(r.events.get_nowait(), now)
    except Exception:
        pass
    r._advance(dt, now)
    frame = r._render_keys(now)
    out = r.pipeline.process(frame)
    r.driver.show(out, force=False)
    return out


def test_note_on_lights_leds_and_note_off_clears():
    r, drv = make(brightness=100)
    r.post("note_on", 60, 100, 0, "piano")
    out = tick(r)
    leds = r.keymap.leds_for_key(60)
    assert all(out[i].sum() > 0 for i in leds)
    assert out.sum() == out[list(leds)].sum()
    r.post("note_off", 60, 0, "piano")
    out = tick(r)
    assert out.sum() == 0


def test_sustain_holds_and_releases():
    r, drv = make()
    r.post("note_on", 60, 100, 0, "piano")
    r.post("cc", 64, 127, 0)
    r.post("note_off", 60, 0, "piano")
    out = tick(r)
    assert out.sum() > 0
    r.post("cc", 64, 0, 0)
    out = tick(r)
    assert out.sum() == 0


def test_fading_decays():
    r, drv = make(light_mode="fading", fade_ms=200)
    r.post("note_on", 60, 100, 0, "piano")
    tick(r)
    r.post("note_off", 60, 0, "piano")
    a = tick(r, dt=0.05).sum()
    b = tick(r, dt=0.05).sum()
    assert 0 < b < a
    for _ in range(10):
        out = tick(r, dt=0.05)
    assert out.sum() == 0


def test_transpose_via_config_event():
    r, drv = make()
    cfg = copy.deepcopy(r.cfg)
    cfg["transpose"]["semitones"] = 2
    r.post("config", cfg)
    r.post("note_on", 62, 100, 0, "piano")
    out = tick(r)
    assert all(out[i].sum() > 0 for i in r.keymap.leds_for_key(60))


def test_panic_and_all_notes_off():
    r, drv = make()
    for n in (40, 50, 60):
        r.post("note_on", n, 100, 0, "piano")
    assert tick(r).sum() > 0
    r.post("cc", 123, 0, 0)
    assert tick(r).sum() == 0
    r.post("note_on", 60, 100, 0, "piano")
    r.post("panic")
    assert tick(r).sum() == 0


def test_stuck_note_timeout():
    r, drv = make(stuck_note_timeout_s=1)
    r.post("note_on", 60, 100, 0, "piano")
    tick(r)
    r.state.keys[60].on_time -= 5
    assert r.state.expire_stuck() == 1
    assert tick(r).sum() == 0


def test_synthesia_channels_use_hand_colors():
    r, drv = make(brightness=100)
    r.synthesia = dict(r.synthesia, enabled=True)
    r.post("note_on", 60, 100, 12, "piano")   # links = blau
    out = tick(r)
    led = r.keymap.leds_for_key(60)[0]
    assert out[led][2] > 0 and out[led][0] == 0


def test_power_limit_scales_frame():
    strip = dict(DEFAULTS["strip"], max_current_ma=1000, led_count=176)
    p = FramePipeline(strip, 100)
    frame = np.ones((176, 3), dtype=np.float32)
    out = p.process(frame)
    assert p.last_power_scale < 1
    assert p.last_estimated_ma <= 1000 + 1
    assert out.max() < 255


def test_driver_only_shows_changes():
    drv = NullDriver(10)
    f = np.zeros((10, 3), dtype=np.uint8)
    assert drv.show(f) is True
    assert drv.show(f) is False
    assert drv.show(f, force=True) is True
