"""Programmstart:  python -m pianoled [--simulate] [--port N] [--config PFAD]

Reihenfolge ist auf schnellen Start optimiert: Erst laufen LEDs, MIDI, WLAN und
Display, danach wird der (schwerere) Webserver geladen. Der Prozess meldet sich alle
paar Sekunden beim systemd-Watchdog, aber nur, solange der Renderer lebt. Hängt er,
bleibt die Meldung aus und systemd startet den Dienst neu.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import threading

from . import __version__
from .logbuffer import buffer as log_buffer

log = logging.getLogger("pianoled")
WATCHDOG_PING_S = 5.0


def sd_notify(message: str) -> None:
    """systemd benachrichtigen (ohne zusätzliche Bibliothek)."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(message.encode())
    except OSError:
        pass


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    # unter systemd liefert journald den Zeitstempel selbst
    fmt = "%(levelname)s %(name)s: %(message)s" if os.environ.get("INVOCATION_ID") \
        else "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handler.setFormatter(logging.Formatter(fmt, "%H:%M:%S"))
    root = logging.getLogger()
    root.handlers[:] = [handler, log_buffer]
    root.setLevel(level.upper())

    def thread_excepthook(args):
        log.error("Unbehandelter Fehler im Thread %s", args.thread.name if args.thread else "?",
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = thread_excepthook


def main(argv=None) -> int:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(prog="pianoled", description="Piano LED Visualizer")
    parser.add_argument("--config", default=os.path.join(base_dir, "config.json"), help="Pfad zur Konfiguration")
    parser.add_argument("--simulate", action="store_true", help="ohne LED-/Display-Hardware laufen")
    parser.add_argument("--port", type=int, help="Web-Port überschreiben")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    setup_logging(args.log_level)

    from .app import App
    app = App(args.config, base_dir, simulate=args.simulate)
    app.start()

    from .web.server import WebServer   # erst jetzt laden: LEDs reagieren schon
    port = args.port or int(app.config.get("web.port"))
    app.add_component("web", lambda: WebServer(app, app.config.get("web.host"), port))

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda signum, frame: stop.set())

    sd_notify("READY=1")
    unhealthy_logged = False
    try:
        while not stop.wait(WATCHDOG_PING_S):
            healthy, reason = app.healthy()
            if healthy:
                sd_notify("WATCHDOG=1")
                unhealthy_logged = False
            elif not unhealthy_logged:
                log.error("Ungesund (%s) – systemd startet den Dienst gleich neu", reason)
                unhealthy_logged = True
    finally:
        log.info("Beende …")
        sd_notify("STOPPING=1")
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
