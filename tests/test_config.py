import json
import os

from pianoled.config import Config, DEFAULTS, validate, _merge


def test_load_defaults_when_missing(tmp_path):
    c = Config(str(tmp_path / "config.json"))
    assert c.get("strip.led_count") == 176
    assert c.get("features.recorder.enabled") is False


def test_update_saves_and_validates(tmp_path):
    path = tmp_path / "config.json"
    c = Config(str(path))
    c.update({"look.brightness": 500, "look.color_mode": "kaputt", "transpose.semitones": 3,
              "network.hotspot.password": "kurz"})
    assert c.get("look.brightness") == 100
    assert c.get("look.color_mode") == "single"
    assert c.get("transpose.semitones") == 3
    assert c.get("network.hotspot.password") == "pianoled123"
    assert not path.exists()            # verzögertes Speichern
    c.flush()
    data = json.loads(path.read_text())
    assert data["transpose"]["semitones"] == 3


def test_broken_file_restores_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ kaputt")
    c = Config(str(path))
    assert c.get("strip.led_count") == 176
    assert os.path.exists(str(path) + ".broken")


def test_merge_ignores_unknown_and_adds_missing():
    merged = _merge(DEFAULTS, {"strip": {"led_count": 100, "foo": 1}, "bar": 2})
    assert merged["strip"]["led_count"] == 100
    assert "foo" not in merged["strip"]
    assert "bar" not in merged
    assert merged["look"]["brightness"] == 50


def test_listener_called(tmp_path):
    c = Config(str(tmp_path / "c.json"))
    seen = []
    c.on_change(lambda snap: seen.append(snap["look"]["brightness"]))
    c.set("look.brightness", 33)
    assert seen == [33]


def test_validate_multicolor_garbage():
    cfg = _merge(DEFAULTS, {"look": {"multicolor": [{"color": "x"}, 5, {"color": [1, 2, 3], "range": [10, 200]}]}})
    out = validate(cfg)
    assert out["look"]["multicolor"][-1]["range"] == [10, 127]
