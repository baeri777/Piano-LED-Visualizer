"""WLAN-Verwaltung über NetworkManager (nmcli) mit Hotspot-Fallback.

Logik des Überwachungs-Threads:
  - Nach dem Start `fallback_after_s` Sekunden auf eine WLAN-Verbindung warten.
  - Keine Verbindung → Hotspot (AP) starten. Die Weboberfläche ist dann unter
    http://10.42.0.1 erreichbar.
  - Im Hotspot-Betrieb alle `retry_known_every_s` Sekunden, sofern kein Client
    verbunden ist, den Hotspot kurz beenden und bekannte Netze probieren.
  - Wird über die App ein Netz eingetragen, sofort verbinden; schlägt das fehl,
    Hotspot wieder starten.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time

log = logging.getLogger(__name__)

HOTSPOT_CON = "PianoLED-Hotspot"
IFACE = "wlan0"


def _run(args: list[str], timeout: float = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, f"{args[0]} nicht gefunden"
    except subprocess.TimeoutExpired:
        return 124, "Zeitüberschreitung"


class NetworkManager(threading.Thread):
    def __init__(self, net_cfg: dict):
        super().__init__(name="network", daemon=True)
        self.cfg = net_cfg
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.available = shutil.which("nmcli") is not None
        self.hotspot_active = False
        self.last_state: dict = {}
        self._wake = threading.Event()

    # -- Abfragen -------------------------------------------------------
    def wifi_state(self) -> dict:
        if not self.available:
            return {"available": False, "connected": False, "ssid": None, "ip": None, "hotspot": False}
        rc, out = _run(["nmcli", "-t", "-f", "GENERAL.CONNECTION,IP4.ADDRESS", "device", "show", IFACE], 10)
        con, ip = None, None
        for line in out.splitlines():
            if line.startswith("GENERAL.CONNECTION:"):
                con = line.split(":", 1)[1] or None
            elif line.startswith("IP4.ADDRESS"):
                ip = line.split(":", 1)[1].split("/")[0] or ip
        hotspot = con == HOTSPOT_CON
        ssid = None
        if con and not hotspot:
            rc2, out2 = _run(["nmcli", "-t", "-f", "802-11-wireless.ssid", "connection", "show", con], 10)
            m = re.search(r"ssid:(.*)", out2)
            ssid = m.group(1).strip() if m else con
        self.hotspot_active = hotspot
        state = {"available": True, "connected": bool(con) and not hotspot, "ssid": ssid, "ip": ip,
                 "hotspot": hotspot, "hotspot_ssid": self.cfg["hotspot"]["ssid"],
                 "hotspot_clients": self._hotspot_clients() if hotspot else 0}
        self.last_state = state
        return state

    def _hotspot_clients(self) -> int:
        rc, out = _run(["iw", "dev", IFACE, "station", "dump"], 5)
        return out.count("Station ") if rc == 0 else 0

    def scan(self) -> list[dict]:
        if not self.available or self.hotspot_active:
            return []
        rc, out = _run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes"], 30)
        seen: dict[str, dict] = {}
        for line in out.splitlines():
            parts = line.rsplit(":", 2)
            if len(parts) != 3 or not parts[0]:
                continue
            ssid, signal, sec = parts
            try:
                signal_i = int(signal)
            except ValueError:
                signal_i = 0
            if ssid not in seen or seen[ssid]["signal"] < signal_i:
                seen[ssid] = {"ssid": ssid, "signal": signal_i, "security": sec or "offen"}
        return sorted(seen.values(), key=lambda e: -e["signal"])

    def known_networks(self) -> list[str]:
        rc, out = _run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], 10)
        return [l.split(":")[0] for l in out.splitlines() if l.endswith(":802-11-wireless") and l.split(":")[0] != HOTSPOT_CON]

    # -- Aktionen -------------------------------------------------------
    def connect(self, ssid: str, password: str | None) -> tuple[bool, str]:
        if not self.available:
            return False, "NetworkManager nicht verfügbar"
        with self._lock:
            self._stop_hotspot()
            _run(["nmcli", "connection", "delete", ssid], 10)
            args = ["nmcli", "device", "wifi", "connect", ssid, "ifname", IFACE]
            if password:
                args += ["password", password]
            rc, out = _run(args, 60)
            if rc == 0:
                _run(["nmcli", "connection", "modify", ssid, "connection.autoconnect", "yes",
                      "connection.autoconnect-priority", "10"], 10)
                log.info("WLAN verbunden: %s", ssid)
                return True, out
            log.warning("WLAN-Verbindung zu %s fehlgeschlagen: %s", ssid, out)
            self._maybe_start_hotspot()
            return False, out

    def forget(self, ssid: str) -> bool:
        rc, _ = _run(["nmcli", "connection", "delete", ssid], 10)
        return rc == 0

    def start_hotspot(self) -> bool:
        with self._lock:
            return self._start_hotspot()

    def stop_hotspot(self) -> None:
        with self._lock:
            self._stop_hotspot()
        self._wake.set()

    def _start_hotspot(self) -> bool:
        hs = self.cfg["hotspot"]
        _run(["nmcli", "connection", "delete", HOTSPOT_CON], 10)
        rc, out = _run(["nmcli", "device", "wifi", "hotspot", "ifname", IFACE, "con-name", HOTSPOT_CON,
                        "ssid", hs["ssid"], "password", hs["password"]], 30)
        if rc == 0:
            _run(["nmcli", "connection", "modify", HOTSPOT_CON, "connection.autoconnect", "no"], 10)
            self.hotspot_active = True
            log.info("Hotspot gestartet: %s", hs["ssid"])
            return True
        log.error("Hotspot konnte nicht gestartet werden: %s", out)
        return False

    def _stop_hotspot(self) -> None:
        if self.hotspot_active:
            _run(["nmcli", "connection", "down", HOTSPOT_CON], 15)
            self.hotspot_active = False
            log.info("Hotspot beendet")

    def _maybe_start_hotspot(self) -> None:
        if self.cfg["hotspot"]["enabled"] and not self.hotspot_active:
            self._start_hotspot()

    # -- Überwachung ----------------------------------------------------
    def run(self) -> None:
        if not self.available:
            log.warning("nmcli nicht gefunden, WLAN-Verwaltung deaktiviert")
            return
        hs = self.cfg["hotspot"]
        boot = time.monotonic()
        last_retry = time.monotonic()
        while not self._stop.is_set():
            try:
                state = self.wifi_state()
                if not state["connected"] and not state["hotspot"]:
                    if hs["enabled"] and time.monotonic() - boot > hs["fallback_after_s"]:
                        with self._lock:
                            self._maybe_start_hotspot()
                elif state["hotspot"]:
                    if (time.monotonic() - last_retry) > hs["retry_known_every_s"] and state["hotspot_clients"] == 0 \
                            and self.known_networks():
                        last_retry = time.monotonic()
                        log.info("Hotspot pausiert, versuche bekannte WLANs")
                        with self._lock:
                            self._stop_hotspot()
                            _run(["nmcli", "device", "connect", IFACE], 45)
                        self._wake.wait(20)
                        if not self.wifi_state()["connected"]:
                            with self._lock:
                                self._maybe_start_hotspot()
            except Exception:
                log.exception("Fehler in der WLAN-Überwachung")
            self._wake.clear()
            self._wake.wait(10)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
