"""Einstiegspunkt: python -m pianoled [--config PFAD] [--simulate] [--port N]"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading

from . import __version__
from .app import App
from .web.server import WebServer


def main(argv=None) -> int:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(prog="pianoled", description="Piano LED Visualizer")
    parser.add_argument("--config", default=os.path.join(base_dir, "config.json"))
    parser.add_argument("--simulate", action="store_true", help="ohne LED-Hardware laufen")
    parser.add_argument("--port", type=int, default=None, help="Web-Port überschreiben")
    parser.add_argument("--log-level", default=None)
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    if args.log_level:
        logging.getLogger().setLevel(args.log_level.upper())

    app = App(args.config, base_dir, simulate=args.simulate)
    port = args.port or int(app.config.get("web.port"))
    server = WebServer(app, app.config.get("web.host"), port)

    stop = threading.Event()

    def handle_signal(signum, frame):
        logging.getLogger("pianoled").info("Signal %s, beende", signum)
        stop.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    app.start()
    server.start()
    _notify_systemd("READY=1")
    try:
        while not stop.wait(10):
            _notify_systemd("WATCHDOG=1")
    finally:
        app.stop()
    return 0


def _notify_systemd(msg: str) -> None:
    """sd_notify ohne zusätzliche Abhängigkeit."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    try:
        import socket
        if addr.startswith("@"):
            addr = "\0" + addr[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(msg.encode())
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
