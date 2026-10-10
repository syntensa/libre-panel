"""Helpers for the tests of sensor sources that run in a thread of their own."""

import threading

from libre_panel.sensors.base import want


def wait_for(predicate, timeout=10.0):
    """True as soon as ``predicate()`` is; False after ``timeout`` seconds."""
    pause = threading.Event()
    for _ in range(int(timeout / 0.005)):
        if predicate():
            return True
        pause.wait(0.005)
    return False


def show(*keys):
    """A theme on show that reads ``keys``; the test keeps it for as long as it is shown."""
    theme = type("Theme", (), {})()
    want(theme, set(keys))
    return theme


class Rounds(threading.Event):
    """A source's stop event that counts the waits of its thread (one per round) and
    writes each into ``log``, to see what happens between two rounds."""

    def __init__(self, log=None):
        super().__init__()
        self.count = 0
        self.log = log if log is not None else []

    def wait(self, timeout=None):
        self.count += 1
        self.log.append("wait")
        return super().wait(timeout)
