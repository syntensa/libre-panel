"""Libre Panel: free software for USB system-monitor displays."""

import os
import time

# numpy's products of small matrices (a glow's blur, render.dither) in the thread that asks for
# them: OpenBLAS would spread each over every core, and while a game keeps them busy its threads
# wait for one another (a blur of a tenth of a millisecond took twenty). Set before numpy is
# loaded; one set already holds.
for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_name, "1")
del _name

__version__ = "0.9.0"


def clock() -> float:
    """The main loop's clock, in seconds: ``now`` in screens, widget types and
    the renderer is a time on it (the time a frame is planned for). It is
    ``time.perf_counter``, fine-grained on every system; compare with it, not
    with ``time.monotonic``, which is coarse on Windows before Python 3.13."""
    return time.perf_counter()
