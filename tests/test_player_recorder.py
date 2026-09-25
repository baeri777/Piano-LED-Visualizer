import time

import mido

from pianoled.app import App


def wait_for(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_record_then_play_lights_keys(tmp_path):
    app = App(str(tmp_path / "c.json"), str(tmp_path), simulate=True)
    app.config.update({"features.recorder.enabled": True, "features.player.enabled": True,
                       "look.startup_animation": False})
    app.renderer.start()
    try:
        rec = app.recorder
        rec.start()
        app._on_midi(("note_on", 60, 90, 0))
        time.sleep(0.05)
        app._on_midi(("note_off", 60, 0))
        name = rec.stop_and_save("Test")
        assert name == "Test.mid"
        notes = [m for m in mido.MidiFile(str(tmp_path / "Songs" / name)) if m.type in ("note_on", "note_off")]
        assert [m.type for m in notes] == ["note_on", "note_off"]

        sent = []
        app.player.send = sent.append
        app._on_midi(("cc", 123, 0, 0))
        assert app.player.play(name)
        led = app.renderer.keymap.leds_for_key(60)[0]
        assert wait_for(lambda: app.sim_frame is not None and app.sim_frame[led].sum() > 0)
        assert wait_for(lambda: not app.player.status()["playing"])
        assert any(m.type == "note_on" for m in sent)
    finally:
        app.player.stop()
        app.renderer.stop()
