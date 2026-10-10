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


class Scripted:
    """A sampler of the load source that gives one value per read, the last one for ever.
    An exception is raised, ``None`` means "no value this time"."""

    def __init__(self, *values):
        self.values = list(values)
        self.reads = 0
        self.closed = threading.Event()
        self.gate = None  # an Event: reads wait for it

    def read(self):
        if self.gate is not None:
            assert self.gate.wait(10)
        self.reads += 1
        value = self.values.pop(0) if len(self.values) > 1 else self.values[0]
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.closed.set()


class FakeSource:
    """A way to read the volume (the audio source), made by the provider's thread."""

    events = False

    def __init__(self, *states, interval=0.005):
        self.states = list(states)
        self.interval = interval
        self.device_checks = 0
        self.changed = False  # the next check finds another device
        self.closed_in = None
        self.on_change = None

    def sample(self):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if isinstance(state, Exception):
            raise state
        return state

    def check_device(self):
        self.device_checks += 1
        changed, self.changed = self.changed, False
        return changed

    def close(self):
        self.closed_in = threading.current_thread()
