"""Tastenzustand: welche Tasten sind gedrückt, seit wann, mit welcher Velocity.

Wird ausschließlich vom Renderer-Thread verändert (Events kommen über eine Queue),
deshalb ohne Locks.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Key:
    pressed: bool = False
    lit: bool = False            # leuchtet (gedrückt oder per Pedal gehalten)
    velocity: int = 0
    channel: int = 0
    on_time: float = 0.0
    off_time: float = 0.0
    color: tuple[int, int, int] | None = None   # feste Farbe für diese Aktivierung (Multicolor/Synthesia)
    source: str = "piano"        # piano | player


@dataclass
class KeyboardState:
    lowest: int
    highest: int
    sustain: bool = False
    sustain_value: int = 0
    sustain_holds_light: bool = True
    stuck_timeout_s: float = 90.0
    keys: dict[int, Key] = field(default_factory=dict)
    last_activity: float = field(default_factory=time.monotonic)
    changed: bool = True

    def __post_init__(self):
        self.keys = {k: Key() for k in range(self.lowest, self.highest + 1)}

    # -- Ereignisse ----------------------------------------------------
    def note_on(self, key: int, velocity: int, channel: int = 0, color=None, source="piano", now=None) -> Key | None:
        k = self.keys.get(key)
        if k is None:
            return None
        now = now or time.monotonic()
        k.pressed = True
        k.lit = True
        k.velocity = velocity
        k.channel = channel
        k.on_time = now
        k.color = color
        k.source = source
        self.last_activity = now
        self.changed = True
        return k

    def note_off(self, key: int, now=None) -> Key | None:
        k = self.keys.get(key)
        if k is None:
            return None
        now = now or time.monotonic()
        k.pressed = False
        k.off_time = now
        if not (self.sustain and self.sustain_holds_light):
            k.lit = False
        self.last_activity = now
        self.changed = True
        return k

    def set_sustain(self, value: int, now=None) -> None:
        now = now or time.monotonic()
        down = value >= 64
        self.sustain_value = value
        if self.sustain and not down:
            for k in self.keys.values():
                if k.lit and not k.pressed:
                    k.lit = False
                    k.off_time = now
            self.changed = True
        self.sustain = down
        self.last_activity = now

    def all_off(self, now=None) -> None:
        now = now or time.monotonic()
        for k in self.keys.values():
            if k.lit or k.pressed:
                k.pressed = False
                k.lit = False
                k.off_time = now
        self.sustain = False
        self.changed = True

    def expire_stuck(self, now=None) -> int:
        """Tasten, die unplausibel lange leuchten, ausschalten. Gibt Anzahl zurück."""
        if self.stuck_timeout_s <= 0:
            return 0
        now = now or time.monotonic()
        n = 0
        for k in self.keys.values():
            if k.lit and (now - k.on_time) > self.stuck_timeout_s:
                k.lit = False
                k.pressed = False
                k.off_time = now
                n += 1
        if n:
            self.changed = True
        return n

    def lit_keys(self) -> list[int]:
        return [n for n, k in self.keys.items() if k.lit]

    def pressed_keys(self) -> list[int]:
        return [n for n, k in self.keys.items() if k.pressed]
