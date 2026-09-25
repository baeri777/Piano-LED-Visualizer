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
    app._on_midi_event(("note_on", 24, 90, 0))   # Piano steht auf +3 → A0 kommt als 24
    assert app.config.get("transpose.semitones") == 3
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
    app._on_midi_event(("note_on", 60, 100, 0))
    time.sleep(0.1)
    r = await client.get("/api/sim/frame")
    frame = (await r.json())["frame"]
    assert frame is not None and sum(map(sum, frame)) > 0
    assert await (await client.get("/")).text()


async def test_disabled_features_reject(client):
    r = await client.post("/api/recorder/start")
    assert r.status == 400
    r = await client.post("/api/player/play", json={"name": "x.mid"})
    assert r.status == 400
