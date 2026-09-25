"""Note → LED-Mapping.

Physikalisches Modell: Jede Taste hat eine Position in Millimetern entlang der
Klaviatur (weiße Tasten im Raster `key_pitch_mm`, schwarze dazwischen). Die LEDs
sitzen im Raster `led_pitch_mm` (144/m → 6,94 mm). Daraus ergibt sich für jede
Note die LED unter der Tastenmitte. Transpose wird vor dem Mapping abgezogen:
Sendet das Piano bei "+2" die Note 62 statt 60, leuchtet trotzdem die LED über
dem C, wenn `semitones = 2`.
"""
from __future__ import annotations

# Halbtonklasse → (Index der weißen Taste in der Oktave, ist schwarz)
_PITCH_CLASS = {
    0: (0, False),  # C
    1: (0, True),   # C#
    2: (1, False),  # D
    3: (1, True),   # D#
    4: (2, False),  # E
    5: (3, False),  # F
    6: (3, True),   # F#
    7: (4, False),  # G
    8: (4, True),   # G#
    9: (5, False),  # A
    10: (5, True),  # A#
    11: (6, False),  # B
}


def white_key_index(note: int) -> float:
    """Position der Note in Einheiten weißer Tasten (0 = C-1). Schwarze Tasten liegen bei x.5."""
    octave, pc = divmod(int(note), 12)
    idx, black = _PITCH_CLASS[pc]
    return octave * 7 + idx + (0.5 if black else 0.0)


class KeyMap:
    def __init__(self, strip_cfg: dict, transpose: int = 0):
        self.led_count = int(strip_cfg["led_count"])
        self.led_pitch = float(strip_cfg["led_pitch_mm"])
        self.key_pitch = float(strip_cfg["key_pitch_mm"])
        self.offset = int(strip_cfg["led_offset"])
        self.leds_per_key = int(strip_cfg["leds_per_key"])
        self.reverse = bool(strip_cfg["reverse"])
        self.lowest = int(strip_cfg["lowest_note"])
        self.highest = int(strip_cfg["highest_note"])
        self.transpose = int(transpose)
        self._table: dict[int, tuple[int, ...]] = {}
        self._center: dict[int, int] = {}
        self._build()

    def _build(self) -> None:
        base = white_key_index(self.lowest)
        self._table.clear()
        self._center.clear()
        for note in range(self.lowest, self.highest + 1):
            x_mm = (white_key_index(note) - base) * self.key_pitch + self.key_pitch / 2
            center = int(round(x_mm / self.led_pitch - 0.5)) + self.offset
            if self.reverse:
                center = self.led_count - 1 - center
            self._center[note] = center
            leds = self._spread(center)
            self._table[note] = tuple(i for i in leds if 0 <= i < self.led_count)

    def _spread(self, center: int) -> list[int]:
        n = self.leds_per_key
        if n == 1:
            return [center]
        if n == 2:
            return [center, center + (-1 if self.reverse else 1)]
        return [center - 1, center, center + 1]

    # -- API -------------------------------------------------------------
    def set_transpose(self, semitones: int) -> None:
        self.transpose = int(semitones)

    def note_to_key(self, received_note: int) -> int | None:
        """Empfangene MIDI-Note → physische Taste (Transpose herausgerechnet)."""
        key = int(received_note) - self.transpose
        return key if self.lowest <= key <= self.highest else None

    def leds_for_key(self, key: int) -> tuple[int, ...]:
        return self._table.get(int(key), ())

    def leds_for_note(self, received_note: int) -> tuple[int, ...]:
        key = self.note_to_key(received_note)
        return self._table[key] if key is not None else ()

    def center_led(self, key: int) -> int | None:
        return self._center.get(int(key))

    def key_count(self) -> int:
        return self.highest - self.lowest + 1

    def keys(self):
        return range(self.lowest, self.highest + 1)

    def calibration_from_lowest_key(self, received_note: int) -> int:
        """Transpose-Wert, wenn der Spieler die tiefste Taste drückt und `received_note` ankommt."""
        return int(received_note) - self.lowest
