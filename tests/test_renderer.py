import copy
import time

import numpy as np

from pianoled.config import DEFAULTS
from pianoled.leds import FramePipeline, NullDriver
from pianoled.renderer import Renderer


def make(cfg, **look):
    cfg = copy.deepcopy(cfg)
    cfg["look"].update(look)
    published = []
    r = Renderer(cfg, lambda c: NullDriver(c["strip"]["led_count"]), lambda t, p: published.append((t, p)))
    r.published = published
    return r


def tick(r, dt=0.02):
    """Einen Frame synchron rendern (ohne Thread)."""
    now = time.monotonic()
    r._drain(now)
    frame, _ = r._compose(now, dt)
    return r.pipeline.process(frame)


def lit_leds(out):
    return set(np.nonzero(out.sum(axis=1))[0].tolist())


def test_note_on_lights_exactly_its_leds(cfg):
    r = make(cfg, brightness=100)
    r.post("note_on", 60, 100, 0, "piano")
    assert lit_leds(tick(r)) == set(r.keymap.leds_for_key(60))
    r.post("note_off", 60, 0, "piano")
    assert lit_leds(tick(r)) == set()


def test_sustain_holds_and_releases(cfg):
    r = make(cfg)
    r.post("note_on", 60, 100, 0, "piano")
    r.post("cc", 64, 127, 0)
    r.post("note_off", 60, 0, "piano")
    assert lit_leds(tick(r))
    r.post("cc", 64, 0, 0)
    assert not lit_leds(tick(r))


def test_fading_decays_and_reports_animation(cfg):
    r = make(cfg, light_mode="fading", fade_ms=200)
    r.post("note_on", 60, 100, 0, "piano")
    tick(r)
    r.post("note_off", 60, 0, "piano")
    a = tick(r, 0.05).sum()
    now = time.monotonic()
    _, animating = r._compose(now, 0.05)
    b = r.pipeline.process(r._compose(now, 0.0)[0]).sum()
    assert animating and 0 < b < a
    for _ in range(10):
        out = tick(r, 0.05)
    assert out.sum() == 0


def test_transpose_via_config(cfg):
    r = make(cfg)
    c = copy.deepcopy(r.cfg)
    c["transpose"]["semitones"] = 2
    r.post("config", c)
    r.post("note_on", 62, 100, 0, "piano")
    assert lit_leds(tick(r)) == set(r.keymap.leds_for_key(60))


def test_config_change_keeps_pressed_keys(cfg):
    r = make(cfg)
    r.post("note_on", 60, 100, 0, "piano")
    tick(r)
    c = copy.deepcopy(r.cfg)
    c["look"]["color"] = [255, 0, 0]
    r.post("config", c)
    out = tick(r)
    led = r.keymap.leds_for_key(60)[0]
    assert out[led][0] > 0 and out[led][1] == 0


def test_panic_and_all_notes_off(cfg):
    r = make(cfg)
    for n in (40, 50, 60):
        r.post("note_on", n, 100, 0, "piano")
    assert lit_leds(tick(r))
    r.post("cc", 123, 0, 0)
    assert not lit_leds(tick(r))
    r.post("note_on", 60, 100, 0, "piano")
    r.post("panic")
    assert not lit_leds(tick(r))


def test_stuck_note_timeout(cfg):
    r = make(cfg, stuck_note_timeout_s=1)
    r.post("note_on", 60, 100, 0, "piano")
    tick(r)
    r.state.keys[60].on_time -= 5
    assert r.state.expire_stuck() == 1
    assert not lit_leds(tick(r))


def test_synthesia_hand_colors(cfg):
    cfg = copy.deepcopy(cfg)
    cfg["features"]["synthesia"]["enabled"] = True
    r = make(cfg, brightness=100)
    r.post("note_on", 60, 100, 12, "piano")    # Kanal 12 = linke Hand = blau
    out = tick(r)
    led = r.keymap.leds_for_key(60)[0]
    assert out[led][2] > 0 and out[led][0] == 0


def test_adjacent_leds(cfg):
    r = make(cfg)
    r.cfg["look"]["adjacent"] = {"mode": "same", "color": [0, 0, 0]}
    r.post("note_on", 60, 100, 0, "piano")
    leds = r.keymap.leds_for_key(60)
    assert lit_leds(tick(r)) == set(leds) | {min(leds) - 1, max(leds) + 1}


def test_key_colors_published(cfg):
    r = make(cfg)
    r.post("note_on", 60, 100, 0, "piano")
    tick(r)
    assert r.snapshot()["colors"] == {"60": "#ffffff"}


def test_power_limit_scales_frame():
    strip = dict(DEFAULTS["strip"], max_current_ma=1000, led_count=176)
    p = FramePipeline(strip, 100)
    out = p.process(np.ones((176, 3), dtype=np.float32))
    assert p.last_power_scale < 1
    assert p.last_estimated_ma <= 1001
    assert out.max() < 255


def test_driver_only_shows_changes():
    drv = NullDriver(10)
    f = np.zeros((10, 3), dtype=np.uint8)
    assert drv.show(f) is True
    assert drv.show(f) is False
    assert drv.show(f, force=True) is True


def test_thread_latency_and_idle_sleep(cfg):
    """Note → LED in wenigen ms; im Leerlauf werden kaum Frames erzeugt."""
    shown = []
    r = Renderer(cfg, lambda c: NullDriver(c["strip"]["led_count"], on_show=lambda f: shown.append(time.monotonic())))
    r.start()
    try:
        time.sleep(0.3)
        idle_before = len(shown)
        time.sleep(0.5)
        assert len(shown) - idle_before <= 1          # Leerlauf: höchstens ein Refresh
        t0 = time.monotonic()
        r.post("note_on", 60, 100, 0, "piano")
        deadline = t0 + 0.5
        while time.monotonic() < deadline and not (shown and shown[-1] > t0):
            time.sleep(0.001)
        assert shown[-1] > t0
        assert shown[-1] - t0 < 0.03
        assert time.monotonic() - r.heartbeat < 1.5
    finally:
        r.stop()
        r.join(2)


def test_driver_reset_after_errors(cfg):
    created = []

    class Flaky(NullDriver):
        def show(self, frame, force=False):
            raise RuntimeError("DMA kaputt")

    def factory(c):
        d = Flaky(c["strip"]["led_count"]) if not created else NullDriver(c["strip"]["led_count"])
        created.append(d)
        return d

    r = Renderer(cfg, factory)
    frame = np.zeros((r.led_count, 3), dtype=np.float32)
    for _ in range(3):
        r._output(frame, force=True)
    assert r.driver_resets == 1 and isinstance(r.driver, NullDriver) and not isinstance(r.driver, Flaky)
