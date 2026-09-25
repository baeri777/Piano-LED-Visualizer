"""Systeminformationen und Systemaktionen (Neustart, Herunterfahren, Update)."""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import time

log = logging.getLogger(__name__)
_start = time.monotonic()


def cpu_temp() -> float | None:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as fh:
            return round(int(fh.read().strip()) / 1000, 1)
    except Exception:
        return None


def load_avg() -> list[float]:
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except Exception:
        return []


def mem_free_mb() -> int | None:
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        return None
    return None


_throttled_cache: tuple[float, str | None] = (0.0, None)


def throttled() -> str | None:
    """vcgencmd ist ein Subprozess, deshalb höchstens alle 10 s abfragen."""
    global _throttled_cache
    now = time.monotonic()
    if now - _throttled_cache[0] < 10:
        return _throttled_cache[1]
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3).stdout
        value = out.strip().split("=")[-1] or None
    except Exception:
        value = None
    _throttled_cache = (now, value)
    return value


def pi_model() -> str | None:
    try:
        with open("/proc/device-tree/model") as fh:
            return fh.read().strip("\x00\n")
    except Exception:
        return None


def info(version: str) -> dict:
    return {
        "version": version,
        "hostname": socket.gethostname(),
        "model": pi_model(),
        "cpu_temp": cpu_temp(),
        "load": load_avg(),
        "mem_free_mb": mem_free_mb(),
        "uptime_s": int(time.monotonic() - _start),
        "throttled": throttled(),
    }


def _systemctl(*args) -> tuple[bool, str]:
    try:
        p = subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=30)
        return p.returncode == 0, (p.stdout + p.stderr).strip()
    except Exception as exc:
        return False, str(exc)


def reboot() -> tuple[bool, str]:
    return _systemctl("reboot")


def shutdown() -> tuple[bool, str]:
    return _systemctl("poweroff")


def restart_service() -> tuple[bool, str]:
    return _systemctl("restart", "pianoled.service")


def update(repo_dir: str) -> tuple[bool, str]:
    """git pull + Abhängigkeiten, danach Dienst neu starten."""
    try:
        p = subprocess.run(["git", "-C", repo_dir, "pull", "--ff-only"], capture_output=True, text=True, timeout=120)
        out = p.stdout + p.stderr
        if p.returncode != 0:
            return False, out
        req = os.path.join(repo_dir, "requirements.txt")
        pip = os.path.join(repo_dir, "venv", "bin", "pip")
        if os.path.exists(pip) and os.path.exists(req):
            p2 = subprocess.run([pip, "install", "-q", "-r", req], capture_output=True, text=True, timeout=600)
            out += p2.stdout + p2.stderr
        python = os.path.join(repo_dir, "venv", "bin", "python")
        if os.path.exists(python):
            subprocess.run([python, "-m", "compileall", "-q", os.path.join(repo_dir, "pianoled")], timeout=300)
        return True, out
    except Exception as exc:
        return False, str(exc)


def journal(lines: int = 200) -> str:
    try:
        p = subprocess.run(["journalctl", "-u", "pianoled.service", "-n", str(lines), "--no-pager", "-o", "short"],
                           capture_output=True, text=True, timeout=10)
        return p.stdout or p.stderr
    except Exception as exc:
        return str(exc)
