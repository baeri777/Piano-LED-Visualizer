from pianoled.config import DEFAULTS
from pianoled.keymap import KeyMap, white_key_index


def strip(**over):
    s = dict(DEFAULTS["strip"])
    s.update(over)
    return s


def test_white_key_index_black_keys_between():
    assert white_key_index(60) == 35.0        # C4
    assert white_key_index(61) == 35.5        # C#4
    assert white_key_index(62) == 36.0        # D4
    assert white_key_index(21) == 12.0   # A0 = Oktave 1 (7*1) + A (5)


def test_88_keys_fit_176_leds():
    km = KeyMap(strip())
    assert km.leds_for_key(21) == (1, 2)          # Tastenmitte von A0 liegt bei 11,75 mm → LED 1
    assert km.leds_for_key(108) == (174, 175)
    # jede Taste bekommt LEDs, und die Zentren sind monoton steigend
    centers = [km.center_led(n) for n in km.keys()]
    assert all(c is not None for c in centers)
    assert centers == sorted(centers)
    assert 170 <= centers[-1] <= 175


def test_reverse_direction():
    km = KeyMap(strip(reverse=True))
    assert km.leds_for_key(21) == (174, 173)
    assert km.center_led(108) < 6


def test_offset_and_leds_per_key():
    km = KeyMap(strip(led_offset=3, leds_per_key=3))
    assert km.leds_for_key(21) == (3, 4, 5)
    km1 = KeyMap(strip(leds_per_key=1))
    assert km1.leds_for_key(21) == (1,)


def test_transpose_shifts_received_notes():
    km = KeyMap(strip(), transpose=2)
    # Piano steht auf +2 und sendet 62, wenn C4 gedrückt wird → LED von C4
    assert km.note_to_key(62) == 60
    assert km.leds_for_note(62) == KeyMap(strip()).leds_for_key(60)
    assert km.note_to_key(21) is None            # unter A0 nach Rücktransposition
    assert km.calibration_from_lowest_key(23) == 2
    assert km.calibration_from_lowest_key(19) == -2


def test_out_of_range_notes_ignored():
    km = KeyMap(strip())
    assert km.leds_for_note(5) == ()
    assert km.leds_for_note(120) == ()
