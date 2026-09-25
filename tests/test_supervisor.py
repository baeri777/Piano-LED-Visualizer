import threading
import time

import pytest

from pianoled.supervisor import Supervisor

pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")


class Crashy(threading.Thread):
    runs = 0

    def __init__(self, crash):
        super().__init__(daemon=True)
        self.crash = crash
        self._stop_event = threading.Event()

    def run(self):
        Crashy.runs += 1
        if self.crash:
            raise RuntimeError("Absturz")
        self._stop_event.wait()

    def stop(self):
        self._stop_event.set()


def test_restarts_dead_component():
    restarts = []
    sup = Supervisor(on_restart=lambda name, n: restarts.append((name, n)))
    crash = [True]
    sup.add("x", lambda: Crashy(crash.pop() if crash else False))
    time.sleep(0.1)
    assert not sup.get("x").is_alive()
    sup.check_once()
    time.sleep(0.05)
    assert sup.get("x").is_alive()
    assert restarts == [("x", 1)]
    assert sup.status()["x"]["alive"]
    sup.stop()


def test_backoff_limits_restart_rate():
    sup = Supervisor()
    sup.add("y", lambda: Crashy(True))
    time.sleep(0.05)
    sup.check_once()
    time.sleep(0.05)
    before = Crashy.runs
    sup.check_once()          # innerhalb der Wartezeit: kein neuer Versuch
    assert Crashy.runs == before
    sup.stop()
