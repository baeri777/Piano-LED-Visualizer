"""Farbfunktionen und Leerlauf-Animationen (reine Funktionen auf numpy-Frames)."""
from __future__ import annotations

import math

import numpy as np


def wheel(pos: np.ndarray | int) -> np.ndarray:
    """Farbrad 0..255 → RGB (0..1). Vektorisiert."""
    p = np.asarray(pos, dtype=np.float32) % 256
    r = np.where(p < 85, 255 - p * 3, np.where(p < 170, 0, (p - 170) * 3))
    g = np.where(p < 85, p * 3, np.where(p < 170, 255 - (p - 85) * 3, 0))
    b = np.where(p < 85, 0, np.where(p < 170, (p - 85) * 3, 255 - (p - 170) * 3))
    return np.stack([r, g, b], axis=-1).astype(np.float32) / 255.0


def rainbow_colors(led_indices: np.ndarray, offset: int, scale: int, speed: int, t: float) -> np.ndarray:
    pos = (led_indices.astype(np.float32) + offset + t * speed) * (scale / 100.0)
    return wheel(pos)


# -- Leerlauf-Animationen: (t, led_count) → Frame (led_count × 3, float 0..1) ----

def anim_rainbow_cycle(t: float, n: int) -> np.ndarray:
    idx = np.arange(n, dtype=np.float32)
    return wheel(idx * 256.0 / max(1, n) + t * 40.0)


def anim_rainbow(t: float, n: int) -> np.ndarray:
    idx = np.arange(n, dtype=np.float32)
    return wheel(idx + t * 40.0)


def anim_breathing(t: float, n: int) -> np.ndarray:
    level = (math.sin(t * 1.2) + 1) / 2
    return np.full((n, 3), level, dtype=np.float32) * np.array([0.6, 0.7, 1.0], dtype=np.float32)


def anim_scanner(t: float, n: int) -> np.ndarray:
    period = 3.0
    phase = (t % period) / period
    pos = (1 - abs(phase * 2 - 1)) * (n - 1)
    idx = np.arange(n, dtype=np.float32)
    level = np.clip(1 - np.abs(idx - pos) / 6.0, 0, 1) ** 2
    frame = np.zeros((n, 3), dtype=np.float32)
    frame[:, 0] = level
    frame[:, 1] = level * 0.15
    return frame


def anim_theater_chase(t: float, n: int) -> np.ndarray:
    step = int(t * 8) % 5
    frame = np.zeros((n, 3), dtype=np.float32)
    frame[step::5] = 0.6
    return frame


def anim_police(t: float, n: int) -> np.ndarray:
    frame = np.zeros((n, 3), dtype=np.float32)
    half = n // 2
    if int(t * 4) % 2 == 0:
        frame[:half, 0] = 1.0
    else:
        frame[half:, 2] = 1.0
    return frame


ANIMATIONS = {
    "rainbow_cycle": anim_rainbow_cycle,
    "rainbow": anim_rainbow,
    "breathing": anim_breathing,
    "scanner": anim_scanner,
    "theater_chase": anim_theater_chase,
    "police": anim_police,
}

ANIMATION_NAMES_DE = {
    "rainbow_cycle": "Regenbogen (Zyklus)",
    "rainbow": "Regenbogen",
    "breathing": "Atmen",
    "scanner": "Scanner",
    "theater_chase": "Lauflicht",
    "police": "Polizei",
}
