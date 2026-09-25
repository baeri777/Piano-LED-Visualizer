"""Transpose-Hilfen.

1. Kalibrierung: Der Spieler drückt die tiefste Taste; aus der empfangenen Note
   ergibt sich der Versatz. Auslösbar per App oder per Pedal-Hack (Soft-Pedal
   mehrfach kurz treten).
2. Roland-SysEx-Follower: Viele Roland-Instrumente senden bei eingeschaltetem
   "Tx Edit Data" einen DT1-SysEx (F0 41 dev 00 .. 12 aa aa aa aa vv cs F7),
   wenn ein Parameter wie Transpose geändert wird. Die Adresse ist modellabhängig
   und wird per Lernfunktion ermittelt: Der Spieler stellt am Piano nacheinander
   Transpose 0, +1, +2 ein; die Adresse, deren Wert dabei jeweils um 1 steigt,
   ist die Transpose-Adresse.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class PedalTrigger:
    """Erkennt n kurze Pedaldrücke innerhalb eines Zeitfensters."""
    control: int = 67
    presses: int = 3
    window_s: float = 2.0
    _times: list[float] = field(default_factory=list)
    _down: bool = False

    def feed(self, control: int, value: int, now: float | None = None) -> bool:
        if control != self.control:
            return False
        now = now or time.monotonic()
        down = value >= 64
        fired = False
        if down and not self._down:
            self._times = [t for t in self._times if now - t <= self.window_s] + [now]
            if len(self._times) >= self.presses:
                self._times.clear()
                fired = True
        self._down = down
        return fired


def parse_roland_dt1(data: bytes) -> tuple[tuple[int, ...], bytes] | None:
    """Zerlegt den Datenteil (ohne F0/F7) eines Roland-DT1-SysEx in (Adresse, Nutzdaten)."""
    # 41 dev 00 mm mm 12 a a a a d... cs   (Modell-ID 2 oder 3 Bytes)
    if len(data) < 9 or data[0] != 0x41:
        return None
    try:
        cmd_index = data.index(0x12, 2)
    except ValueError:
        return None
    if cmd_index > 6:
        return None
    body = data[cmd_index + 1:]
    if len(body) < 6:
        return None
    address = tuple(body[:4])
    payload = bytes(body[4:-1])  # letzte Byte = Prüfsumme
    return address, payload


@dataclass
class RolandTransposeLearner:
    """Sammelt DT1-Nachrichten während der Lernphase und findet die Transpose-Adresse."""
    steps: list[dict[tuple[int, ...], int]] = field(default_factory=list)
    current: dict[tuple[int, ...], int] = field(default_factory=dict)

    def feed(self, data: bytes) -> None:
        parsed = parse_roland_dt1(data)
        if parsed and parsed[1]:
            self.current[parsed[0]] = parsed[1][0]

    def next_step(self) -> None:
        self.steps.append(dict(self.current))
        self.current = {}

    def result(self) -> tuple[list[int], int] | None:
        """(Adresse, Basiswert bei Transpose 0) oder None."""
        if len(self.steps) < 2:
            return None
        common = set(self.steps[0])
        for s in self.steps[1:]:
            common &= set(s)
        for addr in sorted(common):
            values = [s[addr] for s in self.steps]
            if all(values[i + 1] - values[i] == 1 for i in range(len(values) - 1)):
                return list(addr), values[0]
        return None


def transpose_from_dt1(data: bytes, address: list[int] | None, base_value: int) -> int | None:
    if not address:
        return None
    parsed = parse_roland_dt1(data)
    if not parsed or list(parsed[0]) != list(address) or not parsed[1]:
        return None
    return int(parsed[1][0]) - int(base_value)
