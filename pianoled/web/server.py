"""Webserver: REST-API, WebSocket-Liveupdates, Handy-App und WLAN-Einrichtung.

Captive Portal
--------------
Im Hotspot-Betrieb beantwortet der Pi jede DNS-Anfrage mit seiner eigenen Adresse
(dnsmasq-Regel aus install.sh). Handys prüfen nach dem Verbinden eine bekannte URL
(z. B. connectivitycheck.gstatic.com/generate_204 oder captive.apple.com). Diese
Anfragen landen hier und werden auf /setup umgeleitet. Das Handy zeigt daraufhin
von selbst „Im Netzwerk anmelden“ mit der WLAN-Einrichtung.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import threading

from aiohttp import WSMsgType, web

from .. import __version__
from ..config import DEFAULTS
from ..effects import ANIMATION_NAMES_DE
from ..logbuffer import buffer as log_buffer

log = logging.getLogger(__name__)
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
NO_CACHE = {"Cache-Control": "no-cache"}


def build_id() -> str:
    """Kennung der ausgelieferten Oberfläche: ändert sich bei jedem Update der Dateien."""
    newest = max((os.path.getmtime(os.path.join(STATIC_DIR, f)) for f in os.listdir(STATIC_DIR)), default=0)
    return f"{__version__}-{int(newest)}"


def _dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def ok(data=None, status: int = 200) -> web.Response:
    return web.json_response(data if data is not None else {"ok": True}, status=status, dumps=_dumps)


def fail(message: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status, dumps=_dumps)


class WebServer(threading.Thread):
    def __init__(self, app, host: str, port: int):
        super().__init__(name="web", daemon=True)
        self.app = app
        self.host = host
        self.port = port
        self.loop: asyncio.AbstractEventLoop | None = None
        self.sockets: set[web.WebSocketResponse] = set()
        self.web = web.Application(middlewares=[self._captive_portal, self._errors],
                                   client_max_size=20 * 1024 * 1024)
        self._routes()
        self._runner: web.AppRunner | None = None
        self.build = build_id()
        app.bus.subscribe(self._on_event)

    # =================================================================
    # Routing
    # =================================================================
    def _routes(self) -> None:
        r = self.web.router
        r.add_get("/", self.page("index.html"))
        r.add_get("/setup", self.page("setup.html"))
        r.add_get("/ws", self.websocket)
        # Status & Konfiguration
        r.add_get("/api/status", self.status)
        r.add_get("/api/health", self.health)
        r.add_get("/api/meta", self.meta)
        r.add_get("/api/config", self.get_config)
        r.add_post("/api/config", self.set_config)
        r.add_post("/api/config/reset", self.reset_config)
        # Licht & Transpose
        r.add_post("/api/panic", self.panic)
        r.add_post("/api/test", self.test)
        r.add_post("/api/transpose", self.transpose)
        r.add_post("/api/transpose/calibrate", self.calibrate)
        r.add_post("/api/transpose/learn/{step}", self.learn)
        r.add_post("/api/presets", self.save_preset)
        r.add_post("/api/presets/{id}/apply", self.apply_preset)
        r.add_delete("/api/presets/{id}", self.delete_preset)
        # WLAN
        r.add_get("/api/wifi", self.wifi)
        r.add_get("/api/wifi/scan", self.wifi_scan)
        r.add_post("/api/wifi/connect", self.wifi_connect)
        r.add_post("/api/wifi/forget", self.wifi_forget)
        r.add_post("/api/wifi/hotspot", self.wifi_hotspot)
        # Extras
        r.add_get("/api/songs", self.songs)
        r.add_post("/api/songs", self.upload_song)
        r.add_delete("/api/songs/{name}", self.delete_song)
        r.add_post("/api/player/play", self.player_play)
        r.add_post("/api/player/stop", self.player_stop)
        r.add_post("/api/recorder/{action}", self.recorder)
        r.add_get("/api/monitor", self.monitor)
        r.add_post("/api/monitor", self.monitor_toggle)
        # System
        r.add_get("/api/log", self.log_text)
        r.add_post("/api/system/{action}", self.system_action)
        r.add_get("/api/lcd.png", self.lcd_png)
        r.add_post("/api/lcd/{button}", self.lcd_press)
        r.add_get("/api/sim/frame", self.sim_frame)
        r.add_static("/static/", STATIC_DIR, show_index=False)
        for name in ("manifest.json", "sw.js", "icon.svg"):
            r.add_get("/" + name, self.page(name))

    def page(self, filename: str):
        path = os.path.join(STATIC_DIR, filename)

        async def handler(request):
            return web.FileResponse(path, headers=NO_CACHE)
        return handler

    # =================================================================
    # Middleware
    # =================================================================
    @web.middleware
    async def _captive_portal(self, request, handler):
        state = self.app.network_state()
        if state.get("mode") == "hotspot":
            host = request.host.rsplit(":", 1)[0].lower().strip("[]")
            ip = state.get("ip") or "10.42.0.1"
            own = {ip, "localhost", "127.0.0.1", self.app.hostname().lower(), self.app.hostname().lower() + ".local"}
            if host not in own and not request.path.startswith(("/api/", "/ws", "/static/")):
                raise web.HTTPFound(f"http://{ip}{'' if self.port == 80 else f':{self.port}'}/setup")
        return await handler(request)

    @web.middleware
    async def _errors(self, request, handler):
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except Exception as exc:
            log.exception("Fehler bei %s %s", request.method, request.path)
            return fail(f"Interner Fehler: {exc}", 500)

    # =================================================================
    # Hilfen
    # =================================================================
    @staticmethod
    async def body(request) -> dict:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    async def blocking(fn, *args):
        """Blockierende Aufrufe (nmcli, Dateien, git) im Threadpool ausführen."""
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    # =================================================================
    # Status & Konfiguration
    # =================================================================
    async def status(self, request):
        return ok(await self.blocking(self.app.status))

    async def health(self, request):
        healthy, reason = self.app.healthy()
        return ok({"ok": healthy, "reason": reason, "components": self.app.supervisor.status()},
                  200 if healthy else 503)

    async def meta(self, request):
        return ok({"version": __version__, "build": self.build, "animations": ANIMATION_NAMES_DE})

    async def get_config(self, request):
        return ok(self.app.config.snapshot())

    async def set_config(self, request):
        data = await self.body(request)
        changes = data.get("changes", data)
        if not isinstance(changes, dict) or not changes:
            return fail("Keine Änderungen übergeben")
        return ok(await self.blocking(self.app.config.update, changes))

    async def reset_config(self, request):
        keep = {"network": self.app.config.get("network")}   # WLAN-Einstellungen behalten
        return ok(await self.blocking(self.app.config.replace, dict(DEFAULTS, **keep)))

    # =================================================================
    # Licht & Transpose
    # =================================================================
    async def panic(self, request):
        self.app.panic()
        return ok()

    async def test(self, request):
        self.app.test_pattern(await self.body(request))
        return ok()

    async def transpose(self, request):
        data = await self.body(request)
        try:
            value = int(self.app.config.get("transpose.semitones")) + int(data["delta"]) if "delta" in data \
                else int(data.get("semitones", 0))
        except (TypeError, ValueError):
            return fail("Ungültiger Wert")
        return ok({"semitones": await self.blocking(self.app.set_transpose, value)})

    async def calibrate(self, request):
        self.app.start_calibration()
        return ok()

    async def learn(self, request):
        step = request.match_info["step"]
        if step == "start":
            self.app.learn_start()
            return ok({"ok": True, "steps": 0})
        if step == "next":
            return ok({"ok": True, "steps": self.app.learn_step()})
        if step == "finish":
            return ok(await self.blocking(self.app.learn_finish))
        return fail("Unbekannter Schritt")

    async def save_preset(self, request):
        data = await self.body(request)
        return ok(await self.blocking(self.app.save_preset, str(data.get("name", ""))))

    async def apply_preset(self, request):
        found = await self.blocking(self.app.apply_preset, request.match_info["id"])
        return ok() if found else fail("Preset nicht gefunden", 404)

    async def delete_preset(self, request):
        await self.blocking(self.app.delete_preset, request.match_info["id"])
        return ok()

    # =================================================================
    # WLAN
    # =================================================================
    async def wifi(self, request):
        net = self.app.network
        if net is None:
            return ok({"state": {"mode": "offline"}, "known": [], "attempt": None})
        known = await self.blocking(net.known_networks)
        return ok({"state": dict(net.state), "known": known, "attempt": net.last_attempt})

    async def wifi_scan(self, request):
        net = self.app.network
        if net is None:
            return ok({"networks": [], "cached": False, "age_s": None})
        return ok(await self.blocking(net.scan))

    async def wifi_connect(self, request):
        data = await self.body(request)
        ssid = str(data.get("ssid", "")).strip()
        password = str(data.get("password") or "")
        if not ssid:
            return fail("Bitte ein Netzwerk wählen")
        if password and not 8 <= len(password) <= 63:
            return fail("WLAN-Passwörter haben 8 bis 63 Zeichen")
        net = self.app.network
        if net is None or not net.available:
            return fail("WLAN-Verwaltung nicht verfügbar", 503)
        net.request_connect(ssid, password or None, bool(data.get("hidden")))
        return ok({"ok": True, "ssid": ssid, "hostname": self.app.hostname()})

    async def wifi_forget(self, request):
        data = await self.body(request)
        net = self.app.network
        done = net is not None and await self.blocking(net.forget, str(data.get("ssid", "")))
        return ok({"ok": bool(done)})

    async def wifi_hotspot(self, request):
        data = await self.body(request)
        if data.get("enabled"):
            done = await self.blocking(self.app.start_hotspot)
        else:
            await self.blocking(self.app.stop_hotspot)
            done = True
        return ok({"ok": bool(done)})

    # =================================================================
    # Extras
    # =================================================================
    async def songs(self, request):
        return ok({"songs": await self.blocking(self.app.player.list_songs)})

    async def upload_song(self, request):
        reader = await request.multipart()
        saved = []
        os.makedirs(self.app.player.songs_dir, exist_ok=True)
        while (part := await reader.next()) is not None:
            if not part.filename or not part.filename.lower().endswith((".mid", ".midi")):
                continue
            name = os.path.basename(part.filename)
            data = bytearray()
            while chunk := await part.read_chunk():
                data += chunk
            await self.blocking(_write_file, os.path.join(self.app.player.songs_dir, name), bytes(data))
            saved.append(name)
        return ok({"saved": saved})

    async def delete_song(self, request):
        path = os.path.join(self.app.player.songs_dir, os.path.basename(request.match_info["name"]))
        if not os.path.isfile(path):
            return fail("Datei nicht gefunden", 404)
        await self.blocking(os.unlink, path)
        return ok()

    async def player_play(self, request):
        if not self.app.config.get("features.player.enabled"):
            return fail("MIDI-Wiedergabe ist unter Extras ausgeschaltet")
        data = await self.body(request)
        self.app.player.light_keys = bool(self.app.config.get("features.player.light_keys"))
        self.app.player.send_to_piano = bool(data.get("send_to_piano", True))
        return ok({"ok": self.app.player.play(str(data.get("name", "")))})

    async def player_stop(self, request):
        await self.blocking(self.app.player.stop)
        return ok()

    async def recorder(self, request):
        if not self.app.config.get("features.recorder.enabled"):
            return fail("Aufnahme ist unter Extras ausgeschaltet")
        action = request.match_info["action"]
        rec = self.app.recorder
        if action == "start":
            rec.start()
        elif action == "cancel":
            rec.cancel()
        elif action == "stop":
            data = await self.body(request)
            name = await self.blocking(rec.stop_and_save, data.get("name"))
            return ok({"ok": name is not None, "file": name})
        else:
            return fail("Unbekannte Aktion")
        return ok(rec.status())

    async def monitor(self, request):
        return ok({"enabled": self.app.monitor_enabled, "events": self.app.monitor, "sysex": self.app.last_sysex})

    async def monitor_toggle(self, request):
        data = await self.body(request)
        self.app.monitor_enabled = bool(data.get("enabled"))
        if not self.app.monitor_enabled:
            self.app.monitor = []
        return ok({"enabled": self.app.monitor_enabled})

    # =================================================================
    # System
    # =================================================================
    async def log_text(self, request):
        return web.Response(text=log_buffer.text(), content_type="text/plain", charset="utf-8")

    async def system_action(self, request):
        from ..system import sysinfo
        action = request.match_info["action"]
        actions = {
            "restart": self.app.restart_service,
            "reboot": self.app.reboot,
            "shutdown": self.app.shutdown,
            "update": lambda: sysinfo.update(self.app.base_dir),
        }
        if action not in actions:
            return fail("Unbekannte Aktion")
        if action in ("restart", "reboot", "shutdown"):
            # erst antworten, dann ausführen – sonst reißt die Verbindung vorher ab
            asyncio.get_running_loop().call_later(0.5, lambda: threading.Thread(target=actions[action], daemon=True).start())
            return ok({"ok": True})
        done, output = await self.blocking(actions[action])
        if done:
            asyncio.get_running_loop().call_later(1.0, lambda: threading.Thread(target=self.app.restart_service, daemon=True).start())
        return ok({"ok": done, "detail": output})

    async def lcd_png(self, request):
        lcd = self.app.lcd
        if lcd is None:
            return fail("Display nicht aktiv", 404)
        image = await self.blocking(lcd.render)
        buf = io.BytesIO()
        image.resize((256, 256), 0).save(buf, format="PNG")
        return web.Response(body=buf.getvalue(), content_type="image/png", headers=NO_CACHE)

    async def lcd_press(self, request):
        lcd = self.app.lcd
        button = request.match_info["button"]
        if lcd is None:
            return fail("Display nicht aktiv", 404)
        await self.blocking(lcd.press, button)
        return ok()

    async def sim_frame(self, request):
        frame = self.app.sim_frame
        if frame is None:
            return ok({"frame": None})
        return ok({"frame": frame.reshape(-1).tolist()})

    # =================================================================
    # WebSocket
    # =================================================================
    async def websocket(self, request):
        ws = web.WebSocketResponse(heartbeat=15)
        await ws.prepare(request)
        self.sockets.add(ws)
        try:
            await ws.send_str(_dumps({"topic": "hello", "data": {
                "version": __version__, "build": self.build, "status": await self.blocking(self.app.status),
                "config": self.app.config.snapshot()}}))
            async for msg in ws:
                if msg.type == WSMsgType.TEXT and msg.data == "status":
                    await ws.send_str(_dumps({"topic": "status", "data": await self.blocking(self.app.status)}))
        finally:
            self.sockets.discard(ws)
        return ws

    def _on_event(self, topic: str, payload) -> None:
        """Vom Event-Bus (beliebiger Thread) → an alle verbundenen Handys."""
        loop = self.loop
        if loop is None or not self.sockets or loop.is_closed():
            return
        text = _dumps({"topic": topic, "data": payload})
        try:
            loop.call_soon_threadsafe(self._schedule_broadcast, text)
        except RuntimeError:
            pass

    def _schedule_broadcast(self, text: str) -> None:
        asyncio.ensure_future(self._broadcast(text))

    async def _broadcast(self, text: str) -> None:
        for ws in list(self.sockets):
            if ws.closed:
                self.sockets.discard(ws)
                continue
            try:
                await ws.send_str(text)
            except Exception:
                self.sockets.discard(ws)

    async def _status_ticker(self) -> None:
        while True:
            await asyncio.sleep(3.0)
            if self.sockets:
                await self._broadcast(_dumps({"topic": "status", "data": await self.blocking(self.app.status)}))

    # =================================================================
    # Thread
    # =================================================================
    def run(self) -> None:
        self.loop = loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            self._runner = web.AppRunner(self.web, access_log=None)
            loop.run_until_complete(self._runner.setup())
            site = web.TCPSite(self._runner, self.host, self.port, reuse_address=True)
            loop.run_until_complete(site.start())
            log.info("Weboberfläche bereit auf Port %s", self.port)
            loop.create_task(self._status_ticker())
            loop.run_forever()
        except OSError as exc:
            log.error("Webserver kann Port %s nicht öffnen: %s", self.port, exc)
        finally:
            try:
                if self._runner is not None:
                    loop.run_until_complete(self._runner.cleanup())
            except Exception:
                pass
            loop.close()
            self.loop = None
            self.app.bus.unsubscribe(self._on_event)

    def stop(self) -> None:
        self.app.bus.unsubscribe(self._on_event)
        loop = self.loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)


def _write_file(path: str, data: bytes) -> None:
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
