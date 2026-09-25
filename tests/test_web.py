import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from pianoled.app import App
from pianoled.web.server import WebServer


@pytest.fixture
def app(tmp_path):
    a = App(str(tmp_path / "config.json"), str(tmp_path), simulate=True)
    a.renderer.start()
    yield a
    a.renderer.stop()
    a._jobs.shutdown(wait=True)


def wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
async def client(app):
    server = WebServer(app, "127.0.0.1", 0)
    async with TestClient(TestServer(server.web)) as c:
        yield c


async def test_status_and_config_roundtrip(client):
    r = await client.get("/api/status")
    assert r.status == 200
    data = await r.json()
    assert data["simulate"] is True
    r = await client.post("/api/config", json={"changes": {"look.brightness": 77}})
    assert (await r.json())["look"]["brightness"] == 77


async def test_transpose_and_calibration(client, app):
    r = await client.post("/api/transpose", json={"delta": 2})
    assert (await r.json())["semitones"] == 2
    await client.post("/api/transpose/calibrate")
    assert app.calibrating
    app._on_midi(("note_on", 24, 90, 0))   # Piano steht auf +3 → A0 kommt als 24
    assert wait_for(lambda: app.config.get("transpose.semitones") == 3)
    assert not app.calibrating


async def test_presets(client, app):
    r = await client.post("/api/presets", json={"name": "Blau"})
    pid = (await r.json())["id"]
    await client.post("/api/config", json={"changes": {"look.color": [1, 2, 3]}})
    r = await client.post(f"/api/presets/{pid}/apply")
    assert r.status == 200
    assert app.config.get("look.color") == [255, 255, 255]
    assert app.next_preset() == pid


async def test_sim_frame_after_note(client, app):
    app._on_midi(("note_on", 60, 100, 0))
    assert wait_for(lambda: app.sim_frame is not None and app.sim_frame.sum() > 0)
    r = await client.get("/api/sim/frame")
    frame = (await r.json())["frame"]
    assert frame is not None and sum(frame) > 0
    assert "Piano LED" in await (await client.get("/")).text()


async def test_disabled_features_reject(client):
    r = await client.post("/api/recorder/start")
    assert r.status == 400
    r = await client.post("/api/player/play", json={"name": "x.mid"})
    assert r.status == 400


async def test_captive_portal_redirects_only_in_hotspot(client, app, monkeypatch):
    r = await client.get("/generate_204", headers={"Host": "connectivitycheck.gstatic.com"}, allow_redirects=False)
    assert r.status == 404                                  # kein Hotspot: nichts umleiten
    monkeypatch.setattr(app, "network_state", lambda: {"mode": "hotspot", "ip": "10.42.0.1"})
    r = await client.get("/generate_204", headers={"Host": "connectivitycheck.gstatic.com"}, allow_redirects=False)
    assert r.status == 302 and r.headers["Location"].endswith("/setup")
    r = await client.get("/", headers={"Host": "10.42.0.1"}, allow_redirects=False)
    assert r.status == 200
    r = await client.get("/api/status", headers={"Host": "example.com"}, allow_redirects=False)
    assert r.status == 200                                  # API nie umleiten


async def test_wifi_connect_validation(client):
    r = await client.post("/api/wifi/connect", json={"ssid": ""})
    assert r.status == 400
    r = await client.post("/api/wifi/connect", json={"ssid": "x", "password": "kurz"})
    assert r.status == 400


async def test_setup_page_and_lcd_preview(client, app):
    from pianoled.lcd.ui import LcdUI
    assert "einrichten" in await (await client.get("/setup")).text()
    assert (await client.get("/api/lcd.png")).status == 404   # Display noch nicht gestartet
    app.supervisor.add("lcd", lambda: LcdUI(app, use_hardware=False))
    r = await client.get("/api/lcd.png")
    assert r.status == 200 and r.content_type == "image/png"
    assert (await client.post("/api/lcd/right")).status == 200
    assert app.config.get("look.brightness") == 55
    app.supervisor.stop()


async def test_health_and_log(client):
    r = await client.get("/api/health")
    assert r.status == 200 and (await r.json())["ok"]
    assert (await client.get("/api/log")).status == 200


async def test_midi_disconnect_turns_leds_off(app):
    from pianoled.midi_input import MidiInput
    midi = MidiInput(app.config.get("midi"), app._on_midi, on_disconnect=app.panic)
    app._on_midi(("note_on", 60, 100, 0))
    assert wait_for(lambda: app.sim_frame is not None and app.sim_frame.sum() > 0)
    midi.port_name = "Roland RD-700NX"          # so tun, als wäre das Piano verbunden
    midi._close("(Piano ausgeschaltet)")
    assert wait_for(lambda: app.sim_frame.sum() == 0)
