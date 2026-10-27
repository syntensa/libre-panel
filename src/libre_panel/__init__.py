"""Libre Panel: free software for USB system-monitor displays."""

import time

__version__ = "0.7.2"


def clock() -> float:
    """The main loop's clock, in seconds: ``now`` in screens, widget types and
    the renderer is a time on it (the time a frame is planned for). It is
    ``time.perf_counter``, fine-grained on every system; compare with it, not
    with ``time.monotonic``, which is coarse on Windows before Python 3.13."""
    return time.perf_counter()
