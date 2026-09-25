"""Weboberfläche: REST-API + WebSocket (aiohttp) und statische PWA-Dateien."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading

from aiohttp import web

from .. import __version__
from ..effects import ANIMATION_NAMES_DE
from ..system import sysinfo

log = logging.getLogger(__name__)
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def json_response(data, status=200):
    return web.json_response(data, status=status, dumps=lambda d: json.dumps(d, ensure_ascii=False))


class WebServer:
    def __init__(self, app, host: str, port: int):
        self.app = app
        self.host = host
        self.port = port
        self.loop: asyncio.AbstractEventLoop | None = None
        self.sockets: set[web.WebSocketResponse] = set()
        self.thread: threading.Thread | None = None
        self.web = web.Application(client_max_size=20 * 1024 * 1024)
        self._routes()
        app.on(self._on_app_event)

    # -- Routing -------------------------------------------------------
    def _routes(self) -> None:
        r = self.web.router
        r.add_get("/", self.index)
        r.add_get("/ws", self.websocket)
        r.add_get("/api/status", self.status)
        r.add_get("/api/config", self.get_config)
        r.add_post("/api/config", self.set_config)
        r.add_post("/api/config/reset", self.reset_config)
        r.add_get("/api/meta", self.meta)
        r.add_post("/api/transpose", self.transpose)
        r.add_post("/api/transpose/calibrate", self.calibrate)
        r.add_post("/api/transpose/learn/{step}", self.learn)
        r.add_post("/api/panic", self.panic)
        r.add_post("/api/test", self.test)
        r.add_get("/api/presets", self.presets)
        r.add_post("/api/presets", self.save_preset)
        r.add_post("/api/presets/{id}/apply", self.apply_preset)
        r.add_delete("/api/presets/{id}", self.delete_preset)
        r.add_get("/api/wifi", self.wifi)
        r.add_get("/api/wifi/scan", self.wifi_scan)
        r.add_post("/api/wifi/connect", self.wifi_connect)
        r.add_post("/api/wifi/forget", self.wifi_forget)
        r.add_post("/api/wifi/hotspot", self.wifi_hotspot)
        r.add_get("/api/songs", self.songs)
        r.add_post("/api/songs/upload", self.upload_song)
        r.add_delete("/api/songs/{name}", self.delete_song)
        r.add_post("/api/player/play", self.player_play)
        r.add_post("/api/player/stop", self.player_stop)
        r.add_post("/api/recorder/{action}", self.recorder)
        r.add_get("/api/monitor", self.monitor)
        r.add_post("/api/monitor", self.monitor_toggle)
        r.add_get("/api/log", self.log_tail)
        r.add_post("/api/system/{action}", self.system_action)
        r.add_get("/api/sim/frame", self.sim_frame)
        r.add_static("/", STATIC_DIR, show_index=False)

    async def index(self, request):
        return web.FileResponse(os.path.join(STATIC_DIR, "index.html"),
                                headers={"Cache-Control": "no-cache"})

    async def _json(self, request) -> dict:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    # -- Status / Konfiguration --------------------------------------
    async def status(self, request):
        return json_response(self.app.status())

    async def meta(self, request):
        return json_response({"version": __version__, "animations": ANIMATION_NAMES_DE,
                              "features": list(self.app.config.get("features").keys())})

    async def get_config(self, request):
        return json_response(self.app.config.snapshot())

    async def set_config(self, request):
        data = await self._json(request)
        changes = data.get("changes") if "changes" in data else data
        if not isinstance(changes, dict):
            return json_response({"error": "changes fehlt"}, 400)
        # Passwörter nicht über Presets/Änderungen leaken
        snap = await asyncio.get_event_loop().run_in_executor(None, self.app.config.update, changes)
        return json_response(snap)

    async def reset_config(self, request):
        from ..config import DEFAULTS
        snap = self.app.config.replace(DEFAULTS)
        return json_response(snap)

    # -- Transpose ----------------------------------------------------
    async def transpose(self, request):
        data = await self._json(request)
        if "delta" in data:
            value = int(self.app.config.get("transpose.semitones")) + int(data["delta"])
        else:
            value = int(data.get("semitones", 0))
        return json_response({"semitones": self.app.set_transpose(value)})

    async def calibrate(self, request):
        self.app.start_calibration()
        return json_response({"ok": True})

    async def learn(self, request):
        step = request.match_info["step"]
        if step == "start":
            self.app.learn_start()
            return json_response({"ok": True, "steps": 0})
        if step == "next":
            return json_response({"ok": True, "steps": self.app.learn_step()})
        if step == "finish":
            return json_response(self.app.learn_finish())
        return json_response({"error": "unbekannter Schritt"}, 400)

    # -- Befehle ------------------------------------------------------
    async def panic(self, request):
        self.app.panic()
        return json_response({"ok": True})

    async def test(self, request):
        data = await self._json(request)
        self.app.test_pattern(data)
        return json_response({"ok": True})

    # -- Presets ------------------------------------------------------
    async def presets(self, request):
        return json_response({"presets": self.app.config.get("presets") or [], "active": self.app.active_preset})

    async def save_preset(self, request):
        data = await self._json(request)
        return json_response(self.app.save_preset(str(data.get("name", ""))))

    async def apply_preset(self, request):
        ok = self.app.apply_preset(request.match_info["id"])
        return json_response({"ok": ok}, 200 if ok else 404)

    async def delete_preset(self, request):
        self.app.delete_preset(request.match_info["id"])
        return json_response({"ok": True})

    # -- WLAN ---------------------------------------------------------
    async def wifi(self, request):
        loop = asyncio.get_event_loop()
        state = await loop.run_in_executor(None, self.app.network.wifi_state)
        known = await loop.run_in_executor(None, self.app.network.known_networks) if state.get("available") else []
        return json_response({"state": state, "known": known})

    async def wifi_scan(self, request):
        nets = await asyncio.get_event_loop().run_in_executor(None, self.app.network.scan)
        return json_response({"networks": nets})

    async def wifi_connect(self, request):
        data = await self._json(request)
        ssid = str(data.get("ssid", "")).strip()
        if not ssid:
            return json_response({"error": "SSID fehlt"}, 400)
        ok, out = await asyncio.get_event_loop().run_in_executor(
            None, self.app.network.connect, ssid, data.get("password") or None)
        return json_response({"ok": ok, "detail": out})

    async def wifi_forget(self, request):
        data = await self._json(request)
        ok = await asyncio.get_event_loop().run_in_executor(None, self.app.network.forget, str(data.get("ssid", "")))
        return json_response({"ok": ok})

    async def wifi_hotspot(self, request):
        data = await self._json(request)
        loop = asyncio.get_event_loop()
        if data.get("enabled"):
            ok = await loop.run_in_executor(None, self.app.network.start_hotspot)
        else:
            await loop.run_in_executor(None, self.app.network.stop_hotspot)
            ok = True
        return json_response({"ok": ok})

    # -- Songs / Player / Recorder -----------------------------------
    async def songs(self, request):
        return json_response({"songs": self.app.player.list_songs()})

    async def upload_song(self, request):
        reader = await request.multipart()
        saved = []
        os.makedirs(self.app.player.songs_dir, exist_ok=True)
        while True:
            part = await reader.next()
            if part is None:
                break
            if not part.filename or not part.filename.lower().endswith((".mid", ".midi")):
                continue
            name = os.path.basename(part.filename)
            path = os.path.join(self.app.player.songs_dir, name)
            with open(path, "wb") as fh:
                while True:
                    chunk = await part.read_chunk()
                    if not chunk:
                        break
                    fh.write(chunk)
            saved.append(name)
        return json_response({"saved": saved})

    async def delete_song(self, request):
        name = os.path.basename(request.match_info["name"])
        path = os.path.join(self.app.player.songs_dir, name)
        if os.path.isfile(path):
            os.unlink(path)
            return json_response({"ok": True})
        return json_response({"ok": False}, 404)

    async def player_play(self, request):
        if not self.app.config.get("features.player.enabled"):
            return json_response({"error": "Wiedergabe ist deaktiviert"}, 400)
        data = await self._json(request)
        self.app.player.light_keys = bool(self.app.config.get("features.player.light_keys"))
        self.app.player.send_to_piano = bool(data.get("send_to_piano", True))
        ok = self.app.player.play(str(data.get("name", "")))
        return json_response({"ok": ok})

    async def player_stop(self, request):
        await asyncio.get_event_loop().run_in_executor(None, self.app.player.stop)
        return json_response({"ok": True})

    async def recorder(self, request):
        if not self.app.config.get("features.recorder.enabled"):
            return json_response({"error": "Aufnahme ist deaktiviert"}, 400)
        action = request.match_info["action"]
        if action == "start":
            self.app.recorder.start()
        elif action == "cancel":
            self.app.recorder.cancel()
        elif action == "stop":
            data = await self._json(request)
            name = self.app.recorder.stop_and_save(data.get("name"))
            return json_response({"ok": name is not None, "file": name})
        return json_response(self.app.recorder.status())

    # -- Monitor / Log / System --------------------------------------
    async def monitor(self, request):
        return json_response({"enabled": self.app.monitor_enabled, "events": self.app.monitor,
                              "sysex": self.app.last_sysex})

    async def monitor_toggle(self, request):
        data = await self._json(request)
        self.app.monitor_enabled = bool(data.get("enabled"))
        if not self.app.monitor_enabled:
            self.app.monitor = []
        return json_response({"enabled": self.app.monitor_enabled})

    async def log_tail(self, request):
        text = await asyncio.get_event_loop().run_in_executor(None, sysinfo.journal, 300)
        return web.Response(text=text, content_type="text/plain")

    async def system_action(self, request):
        action = request.match_info["action"]
        loop = asyncio.get_event_loop()
        if action == "reboot":
            self.app.panic()
            ok, out = await loop.run_in_executor(None, sysinfo.reboot)
        elif action == "shutdown":
            self.app.panic()
            ok, out = await loop.run_in_executor(None, sysinfo.shutdown)
        elif action == "restart":
            ok, out = await loop.run_in_executor(None, sysinfo.restart_service)
        elif action == "update":
            ok, out = await loop.run_in_executor(None, sysinfo.update, self.app.base_dir)
            if ok:
                loop.call_later(1.0, lambda: sysinfo.restart_service())
        else:
            return json_response({"error": "unbekannte Aktion"}, 400)
        return json_response({"ok": ok, "detail": out})

    async def sim_frame(self, request):
        frame = self.app.sim_frame
        return json_response({"frame": frame.tolist() if frame is not None else None})

    # -- WebSocket ----------------------------------------------------
    async def websocket(self, request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.sockets.add(ws)
        try:
            await ws.send_str(json.dumps({"topic": "status", "data": self.app.status()}, ensure_ascii=False))
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT and msg.data == "status":
                    await ws.send_str(json.dumps({"topic": "status", "data": self.app.status()}, ensure_ascii=False))
        finally:
            self.sockets.discard(ws)
        return ws

    def _on_app_event(self, topic: str, payload: dict) -> None:
        if self.loop is None or not self.sockets:
            return
        text = json.dumps({"topic": topic, "data": payload}, ensure_ascii=False)
        self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._broadcast(text)))

    async def _broadcast(self, text: str) -> None:
        for ws in list(self.sockets):
            try:
                await ws.send_str(text)
            except Exception:
                self.sockets.discard(ws)

    async def _status_ticker(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            if self.sockets:
                await self._broadcast(json.dumps({"topic": "status", "data": self.app.status()}, ensure_ascii=False))

    # -- Start ----------------------------------------------------------
    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="web", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        runner = web.AppRunner(self.web, access_log=None)
        self.loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, self.host, self.port, reuse_address=True)
        try:
            self.loop.run_until_complete(site.start())
        except OSError as exc:
            log.error("Webserver konnte Port %s nicht öffnen: %s", self.port, exc)
            return
        log.info("Weboberfläche: http://%s:%s", self.host, self.port)
        self.loop.create_task(self._status_ticker())
        self.loop.run_forever()
