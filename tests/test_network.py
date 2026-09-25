import json
import os
import stat
import sys
import textwrap
import time

import pytest

from pianoled.config import DEFAULTS
from pianoled.system import network
from pianoled.system.network import NetworkManager, friendly_error, split_terse

FAKE = textwrap.dedent('''\
    #!{py}
    import json, os, sys
    p = os.environ["FAKE_NM_STATE"]
    st = json.load(open(p))
    a = sys.argv[1:]; j = " ".join(a)
    def save(): json.dump(st, open(p, "w"))
    if "device show" in j:
        if st["mode"] == "hotspot": print("GENERAL.STATE:100 (connected)\\nGENERAL.CONNECTION:PianoLED-Hotspot\\nIP4.ADDRESS[1]:10.42.0.1/24")
        elif st["mode"] == "wifi": print("GENERAL.STATE:100 (connected)\\nGENERAL.CONNECTION:Home\\nIP4.ADDRESS[1]:192.168.1.5/24")
        else: print("GENERAL.STATE:30 (disconnected)\\nGENERAL.CONNECTION:")
    elif "ACTIVE,SSID,SIGNAL" in j: print("yes:Home:70")
    elif "SSID,SIGNAL" in j: print("Home:70:WPA2:2412 MHz\\nCafe:30::2412 MHz")
    elif "NAME,TYPE" in j:
        for k in st["known"]: print(k + ":802-11-wireless")
    elif a[:3] == ["device", "wifi", "connect"]:
        if "password" in a and a[a.index("password") + 1] == "gutespasswort":
            st.update(mode="wifi"); st["known"].append(a[3]); save()
        else:
            print("Error: Connection activation failed: Secrets were required, but not provided."); save(); sys.exit(4)
    elif a[:3] == ["device", "wifi", "hotspot"]: st.update(mode="hotspot"); save()
    elif a[:2] == ["connection", "down"]: st.update(mode="offline"); save()
''')


@pytest.fixture
def fake_nm(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("nmcli", FAKE.format(py=sys.executable)), ("iw", "#!/bin/sh\necho 'Station aa (on wlan0)'\n")):
        f = bindir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"mode": "offline", "known": []}))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_NM_STATE", str(state))
    monkeypatch.setattr(network.shutil, "which", lambda name: str(bindir / name))
    monkeypatch.setattr(network.time, "sleep", lambda s: None)
    return state


def make_nm(events):
    cfg = json.loads(json.dumps(DEFAULTS["network"]))
    return NetworkManager(cfg, lambda t, p: events.append((t, p)))


def test_helpers():
    assert split_terse(r"My\:Net:80:WPA2") == ["My:Net", "80", "WPA2"]
    assert friendly_error("Secrets were required, but not provided") == "Passwort falsch?"
    assert "nicht gefunden" in friendly_error("Error: No network with SSID 'x' found.")


def test_hotspot_quickly_when_no_known_network(fake_nm):
    events = []
    nm = make_nm(events)
    nm._disconnected_since = time.monotonic() - 60
    nm._tick()
    assert nm.state["mode"] == "hotspot"
    assert nm.state["hotspot_password"] == "pianoled123"
    assert nm.scan_cache and nm.scan_cache[0]["ssid"] == "Home"      # vor dem Hotspot gescannt
    assert any(t == "wifi" and p["mode"] == "hotspot" for t, p in events)


def test_waits_before_hotspot_when_known_network(fake_nm):
    fake_nm.write_text(json.dumps({"mode": "offline", "known": ["Home"]}))
    nm = make_nm([])
    nm._disconnected_since = time.monotonic() - 20      # < fallback_after_s (45)
    nm._tick()
    assert nm.state["mode"] == "offline"


def test_connect_wrong_then_right_password(fake_nm):
    events = []
    nm = make_nm(events)
    nm._disconnected_since = time.monotonic() - 60
    nm._tick()
    assert nm.hotspot_active
    nm.request_connect("Home", "falsch123")
    nm._tick()
    assert nm.last_attempt["status"] == "failed"
    assert nm.last_attempt["message"] == "Passwort falsch?"
    assert nm.state["mode"] == "hotspot"                  # Hotspot ist zurück
    nm.request_connect("Home", "gutespasswort")
    nm._tick()
    assert nm.last_attempt["status"] == "ok"
    assert nm.state["mode"] == "wifi" and nm.state["ip"] == "192.168.1.5" and nm.state["ssid"] == "Home"
