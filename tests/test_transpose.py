from pianoled.transpose import PedalTrigger, RolandTransposeLearner, parse_roland_dt1, transpose_from_dt1


def dt1(addr, value):
    body = [0x41, 0x10, 0x00, 0x00, 0x3B, 0x12] + list(addr) + [value]
    checksum = (128 - (sum(addr) + value) % 128) % 128
    return bytes(body + [checksum])


def test_parse_dt1():
    parsed = parse_roland_dt1(dt1([0x01, 0x00, 0x00, 0x05], 66))
    assert parsed == ((1, 0, 0, 5), b"\x42")
    assert parse_roland_dt1(b"\x43\x00") is None


def test_learner_finds_incrementing_address():
    l = RolandTransposeLearner()
    for value in (64, 65, 66):
        l.feed(dt1([1, 0, 0, 5], value))
        l.feed(dt1([1, 0, 0, 9], 12))   # konstanter Störwert
        l.next_step()
    assert l.result() == ([1, 0, 0, 5], 64)
    assert transpose_from_dt1(dt1([1, 0, 0, 5], 62), [1, 0, 0, 5], 64) == -2
    assert transpose_from_dt1(dt1([1, 0, 0, 9], 62), [1, 0, 0, 5], 64) is None


def test_pedal_trigger():
    t = PedalTrigger(control=67, presses=3, window_s=2)
    now = 100.0
    fired = []
    for i in range(3):
        fired.append(t.feed(67, 127, now + i * 0.3))
        t.feed(67, 0, now + i * 0.3 + 0.1)
    assert fired == [False, False, True]
    assert t.feed(64, 127, now) is False
    # zu langsam: kein Auslösen
    t2 = PedalTrigger(control=67, presses=3, window_s=1)
    assert not any(t2.feed(67, 127 if j % 2 == 0 else 0, now + j) for j in range(6))
