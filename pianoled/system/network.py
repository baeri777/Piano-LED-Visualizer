"""WLAN über NetworkManager (nmcli) mit Hotspot-Fallback.

Zustände (`mode`)
-----------------
    "wifi"        mit einem WLAN verbunden
    "connecting"  Verbindungsversuch läuft (aus der App angestoßen oder automatisch)
    "hotspot"     eigener Access Point aktiv, Einrichtung über http://10.42.0.1
    "offline"     weder noch (z. B. kurz nach dem Start)
    "unavailable" kein NetworkManager (Entwicklung am PC)

Regeln
------
* Ist gar kein WLAN gespeichert, startet der Hotspot nach wenigen Sekunden.
* Sonst erst, wenn `fallback_after_s` lang keine Verbindung besteht. Ein kurzer
  Aussetzer schaltet also nicht gleich in den Hotspot.
* Im Hotspot wird alle `retry_known_every_s` Sekunden geprüft, ob ein bekanntes
  WLAN wieder da ist, aber nur, wenn gerade niemand mit dem Hotspot verbunden ist.
* Vor dem Start des Hotspots wird die Umgebung gescannt. Im Hotspot selbst kann der
  WLAN-Chip nicht scannen, die Einrichtungsseite zeigt deshalb diese Liste.
* Ein Verbindungsauftrag aus der App läuft im Hintergrund: Die HTTP-Antwort geht
  noch raus, danach wird der Hotspot abgebaut. Scheitert die Verbindung, kommt der
  Hotspot zurück und `last_attempt` enthält eine verständliche Fehlermeldung.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

HOTSPOT_CON = "PianoLED-Hotspot"
IFACE = "wlan0"
HOTSPOT_IP = "10.42.0.1"
NO_KNOWN_NETWORK_FALLBACK_S = 8
POLL_S = 5.0


def run_cmd(args: list[str], timeout: float = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, f"{args[0]} nicht gefunden"
    except subprocess.TimeoutExpired:
        return 124, "Zeitüberschreitung"


def split_terse(line: str) -> list[str]:
    """nmcli -t trennt mit ':' und maskiert ':' in Werten als '\\:'."""
    parts, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def friendly_error(output: str) -> str:
    o = output.lower()
    if "secrets were required" in o or "802-1x" in o or "psk" in o and "invalid" in o:
        return "Passwort falsch?"
    if "no network with ssid" in o or "not found" in o:
        return "Netzwerk nicht gefunden (außer Reichweite oder 5-GHz-Netz? Der Pi Zero 2 W kann nur 2,4 GHz)."
    if "timeout" in o or "zeitüberschreitung" in o:
        return "Zeitüberschreitung beim Verbinden."
    if "ip configuration could not be reserved" in o or "dhcp" in o:
        return "Keine IP-Adresse vom Router erhalten."
    return output.strip().splitlines()[-1] if output.strip() else "Unbekannter Fehler"


class NetworkManager(threading.Thread):
    def __init__(self, net_cfg: dict, publish: Callable[[str, object], None] | None = None):
        super().__init__(name="network", daemon=True)
        self.cfg = net_cfg
        self._publish = publish or (lambda t, p: None)
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()
        self.available = shutil.which("nmcli") is not None
        self.state: dict = self._initial_state()
        self.scan_cache: list[dict] = []
        self.scan_time: float | None = None
        self.last_attempt: dict | None = None
        self._pending: tuple[str, str | None, bool] | None = None
        self._disconnected_since: float | None = time.monotonic()
        self._hotspot_since: float | None = None
        self._last_retry = 0.0

    def _initial_state(self) -> dict:
        return {"mode": "offline" if self.available else "unavailable", "ssid": None, "ip": None,
                "signal": None, "hotspot_ssid": self.cfg["hotspot"]["ssid"],
                "hotspot_password": self.cfg["hotspot"]["password"], "clients": 0, "connecting_to": None}

    @property
    def hotspot_active(self) -> bool:
        return self.state.get("mode") == "hotspot"

    # =================================================================
    # Abfragen
    # =================================================================
    def _query(self) -> dict:
        """Aktuellen Zustand von NetworkManager holen (2 schnelle nmcli-Aufrufe)."""
        rc, out = run_cmd(["nmcli", "-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS",
                           "device", "show", IFACE], 10)
        con, ip, dev_state = None, None, ""
        for line in out.splitlines():
            key, _, value = line.partition(":")
            if key == "GENERAL.CONNECTION":
                con = value or None
            elif key == "GENERAL.STATE":
                dev_state = value
            elif key.startswith("IP4.ADDRESS") and value and not ip:
                ip = value.split("/")[0]
        state = dict(self.state)
        state.update(hotspot_ssid=self.cfg["hotspot"]["ssid"], hotspot_password=self.cfg["hotspot"]["password"])
        if con == HOTSPOT_CON:
            state.update(mode="hotspot", ssid=None, ip=ip or HOTSPOT_IP, signal=None, clients=self._clients())
        elif con and dev_state.startswith("100"):
            ssid, signal = self._active_ssid()
            state.update(mode="wifi", ssid=ssid or con, ip=ip, signal=signal, clients=0)
        elif self._pending or dev_state.startswith(("40", "50", "60", "70", "80", "90")):
            state.update(mode="connecting", ip=None, signal=None)
        else:
            state.update(mode="offline", ssid=None, ip=None, signal=None, clients=0)
        return state

    def _active_ssid(self) -> tuple[str | None, int | None]:
        rc, out = run_cmd(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "device", "wifi", "list", "--rescan", "no"], 10)
        for line in out.splitlines():
            parts = split_terse(line)
            if len(parts) >= 3 and parts[0] in ("yes", "ja"):
                try:
                    return parts[1], int(parts[2])
                except ValueError:
                    return parts[1], None
        return None, None

    def _clients(self) -> int:
        rc, out = run_cmd(["iw", "dev", IFACE, "station", "dump"], 5)
        return out.count("Station ") if rc == 0 else 0

    def known_networks(self) -> list[str]:
        if not self.available:
            return []
        rc, out = run_cmd(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], 10)
        names = []
        for line in out.splitlines():
            parts = split_terse(line)
            if len(parts) >= 2 and parts[1] == "802-11-wireless" and parts[0] != HOTSPOT_CON:
                names.append(parts[0])
        return names

    def scan(self, force: bool = False) -> dict:
        """Netzwerke in der Umgebung. Im Hotspot-Betrieb die letzte gespeicherte Liste."""
        if not self.available:
            return {"networks": [], "cached": False, "age_s": None}
        if self.hotspot_active and not force:
            age = round(time.monotonic() - self.scan_time) if self.scan_time else None
            return {"networks": self.scan_cache, "cached": True, "age_s": age}
        rc, out = run_cmd(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,FREQ", "device", "wifi", "list",
                           "--rescan", "yes"], 30)
        best: dict[str, dict] = {}
        for line in out.splitlines():
            parts = split_terse(line)
            if len(parts) < 3 or not parts[0]:
                continue
            ssid, signal, security = parts[0], parts[1], parts[2]
            try:
                signal_i = int(signal)
            except ValueError:
                signal_i = 0
            if ssid not in best or best[ssid]["signal"] < signal_i:
                best[ssid] = {"ssid": ssid, "signal": signal_i, "secure": bool(security and security != "--")}
        nets = sorted(best.values(), key=lambda n: -n["signal"])
        if nets:
            self.scan_cache, self.scan_time = nets, time.monotonic()
        return {"networks": nets, "cached": False, "age_s": 0}

    # =================================================================
    # Aufträge (von Web/Display)
    # =================================================================
    def request_connect(self, ssid: str, password: str | None, hidden: bool = False) -> None:
        """Verbindung im Hintergrund aufbauen; Ergebnis landet in `last_attempt`."""
        with self._lock:
            self._pending = (ssid, password or None, hidden)
            self.last_attempt = {"ssid": ssid, "status": "pending", "message": None, "time": time.time()}
        self._set_state(dict(self.state, mode="connecting", connecting_to=ssid))
        self._wake.set()

    def forget(self, ssid: str) -> bool:
        rc, _ = run_cmd(["nmcli", "connection", "delete", "id", ssid], 10)
        return rc == 0

    def start_hotspot(self) -> bool:
        with self._lock:
            ok = self._start_hotspot()
        self._refresh()
        return ok

    def stop_hotspot(self) -> None:
        with self._lock:
            self._stop_hotspot()
            self._disconnected_since = time.monotonic()
            self._last_retry = time.monotonic()
        run_cmd(["nmcli", "device", "connect", IFACE], 45)
        self._refresh()

    # =================================================================
    # Umsetzung
    # =================================================================
    def _do_connect(self, ssid: str, password: str | None, hidden: bool) -> None:
        time.sleep(1.5)   # HTTP-Antwort an das Handy soll noch durchgehen
        was_hotspot = self.hotspot_active
        self._stop_hotspot()
        run_cmd(["nmcli", "device", "wifi", "rescan"], 15)
        time.sleep(3)
        run_cmd(["nmcli", "connection", "delete", "id", ssid], 10)
        args = ["nmcli", "device", "wifi", "connect", ssid, "ifname", IFACE]
        if password:
            args += ["password", password]
        if hidden:
            args += ["hidden", "yes"]
        rc, out = run_cmd(args, 60)
        if rc == 0:
            run_cmd(["nmcli", "connection", "modify", "id", ssid, "connection.autoconnect", "yes",
                     "connection.autoconnect-priority", "10"], 10)
            log.info("WLAN verbunden: %s", ssid)
            self.last_attempt = {"ssid": ssid, "status": "ok", "message": None, "time": time.time()}
            self._disconnected_since = None
        else:
            msg = friendly_error(out)
            log.warning("Verbindung zu %s fehlgeschlagen: %s", ssid, out)
            run_cmd(["nmcli", "connection", "delete", "id", ssid], 10)   # kaputten Eintrag nicht behalten
            self.last_attempt = {"ssid": ssid, "status": "failed", "message": msg, "time": time.time()}
            if was_hotspot or self.cfg["hotspot"]["enabled"]:
                self._start_hotspot()
        self._publish("wifi_attempt", self.last_attempt)

    def _start_hotspot(self) -> bool:
        if not self.available:
            return False
        try:
            self.scan(force=True)   # Liste für die Einrichtungsseite merken
        except Exception:
            log.debug("Scan vor Hotspot fehlgeschlagen", exc_info=True)
        hs = self.cfg["hotspot"]
        run_cmd(["nmcli", "connection", "delete", "id", HOTSPOT_CON], 10)
        rc, out = run_cmd(["nmcli", "device", "wifi", "hotspot", "ifname", IFACE, "con-name", HOTSPOT_CON,
                           "ssid", hs["ssid"], "band", "bg", "password", hs["password"]], 30)
        if rc != 0:
            log.error("Hotspot konnte nicht gestartet werden: %s", out)
            return False
        # iPhones verbinden sich zuverlässiger ohne PMF; nicht automatisch starten (das macht dieser Dienst)
        run_cmd(["nmcli", "connection", "modify", "id", HOTSPOT_CON, "connection.autoconnect", "no",
                 "802-11-wireless-security.pmf", "disable"], 10)
        run_cmd(["nmcli", "connection", "up", "id", HOTSPOT_CON], 20)
        self._hotspot_since = time.monotonic()
        log.info("Hotspot aktiv: %s", hs["ssid"])
        return True

    def _stop_hotspot(self) -> None:
        if self.hotspot_active or self._hotspot_since:
            run_cmd(["nmcli", "connection", "down", "id", HOTSPOT_CON], 15)
            self._hotspot_since = None
            log.info("Hotspot beendet")

    def _set_state(self, state: dict) -> None:
        old = self.state
        self.state = state
        if any(old.get(k) != state.get(k) for k in ("mode", "ssid", "ip", "clients", "connecting_to")):
            self._publish("wifi", dict(state))
        elif old.get("signal") != state.get("signal"):
            self._publish("wifi", dict(state))

    def _refresh(self) -> dict:
        state = self._query()
        if state["mode"] != "connecting":
            state["connecting_to"] = None
        self._set_state(state)
        return state

    # =================================================================
    # Überwachung
    # =================================================================
    def _tick(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, None
        if pending:
            self._do_connect(*pending)
        state = self._refresh()
        now = time.monotonic()
        hs = self.cfg["hotspot"]
        mode = state["mode"]
        if mode == "wifi":
            self._disconnected_since = None
            return
        if self._disconnected_since is None:
            self._disconnected_since = now
        if mode in ("offline", "connecting") and hs["enabled"]:
            offline_for = now - self._disconnected_since
            limit = hs["fallback_after_s"] if self.known_networks() else NO_KNOWN_NETWORK_FALLBACK_S
            if offline_for >= limit:
                log.info("Seit %d s kein WLAN, starte Hotspot", offline_for)
                with self._lock:
                    self._start_hotspot()
                self._refresh()
        elif mode == "hotspot":
            idle_hotspot = state.get("clients", 0) == 0
            due = now - max(self._last_retry, self._hotspot_since or 0) >= hs["retry_known_every_s"]
            if idle_hotspot and due and self.known_networks():
                self._last_retry = now
                log.info("Hotspot pausiert, suche bekannte WLANs")
                with self._lock:
                    self._stop_hotspot()
                    run_cmd(["nmcli", "device", "connect", IFACE], 45)
                if self._refresh()["mode"] != "wifi" and hs["enabled"]:
                    with self._lock:
                        self._start_hotspot()
                    self._refresh()

    def run(self) -> None:
        if not self.available:
            log.warning("nmcli nicht gefunden, WLAN-Verwaltung deaktiviert")
            self._set_state(dict(self.state, mode="unavailable"))
            self._stop_event.wait()
            return
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("Fehler in der WLAN-Überwachung")
            self._wake.wait(POLL_S)
            self._wake.clear()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake.set()
