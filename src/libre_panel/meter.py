"""How the main loop is doing: frame rate, drawing time, data rate, the video link, faults.

The loop tells the meter about every frame it draws (:meth:`Meter.drew`) and every
step it takes (:meth:`Meter.tick`); once a second the numbers are made and handed to
the run's status (``RunStatus.stats``, a new dict each time), where the tray, the
editor's panel menu (``/api/app``) and its Control page read them.

Measuring costs next to nothing and holds no lock: drawing times go into a ring of the
last frames, rates come from a ring of per-second samples of counts, and what the
drivers count (:class:`~libre_panel.devices.base.Counters`) is read, never reset.
"""

from __future__ import annotations

import statistics
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from libre_panel.app import RunStatus

FRAMES = 120  # drawing times kept: what counts is the last few seconds
EVERY_S = 1.0  # how often the numbers are made
SAMPLES = 6  # rates are measured over the samples kept: the last five seconds


class Meter:
    """Counts frames and times their drawing; makes ``status.stats`` once a second.

    ``now`` is the time on the main loop's clock when it begins.
    """

    def __init__(self, status: RunStatus, now: float) -> None:
        self.status = status
        self.counters = status.counters
        self.began = now
        self.next_at = now + EVERY_S
        self.drawn = 0  # frames drawn
        self.times: deque[float] = deque(maxlen=FRAMES)  # seconds each took to draw
        self.samples: deque[tuple[float, int, int, int]] = deque(maxlen=SAMPLES)
        self.samples.append(self._sample(now))

    def _sample(self, now: float) -> tuple[float, int, int, int]:
        """(time, frames drawn, bytes sent, blocks sent) as of ``now``."""
        return now, self.drawn, self.counters.bytes, self.counters.blocks

    def drew(self, seconds: float) -> None:
        """A frame is drawn (render, fit, transition and message); it took ``seconds``."""
        self.times.append(seconds)
        self.drawn += 1

    def tick(self, now: float, target_fps: int, streaming: bool) -> None:
        """After a step: once a second has passed since the last numbers, make new ones.
        ``target_fps`` is the rate the loop plans for, ``streaming`` whether the panel
        wants every frame (video mode)."""
        if now < self.next_at:
            return
        self.next_at = now + EVERY_S  # not ``+= EVERY_S``: a stalled loop makes no burst
        self.samples.append(self._sample(now))
        self.status.stats = self.stats(now, target_fps, streaming)

    def stats(self, now: float, target_fps: int, streaming: bool) -> dict[str, Any]:
        """The numbers. ``None`` is "not known" (nothing counted it): the data rate for a
        driver that does not count, the video link outside video mode."""
        first, last = self.samples[0], self.samples[-1]
        span = last[0] - first[0]

        def rate(column: int, scale: float = 1.0) -> float | None:
            return round((last[column] - first[column]) / span / scale, 1) if span > 0 else None

        counters, times = self.counters, self.times
        video = counters.blocks > 0  # the video link has been used in this run
        return {
            "fps": rate(1),  # frames drawn per second, over the last few seconds
            "target_fps": target_fps,
            "streaming": streaming,
            "render_ms": round(statistics.median(times) * 1000, 1) if times else None,
            "render_peak_ms": round(max(times) * 1000, 1) if times else None,
            "kbps": rate(2, 1000.0) if counters.bytes else None,  # KB (1000 bytes) sent per second
            "blocks_per_s": rate(3) if video else None,
            "max_depth": counters.max_depth if video else None,
            "bit_rate": counters.bit_rate if video else None,
            "encoder": (counters.encoder or None) if video else None,  # "h264_amf, rgb0 turned"
            "waits": counters.waits if video else None,
            "drops": counters.drops if video else None,
            "usb_errors": counters.usb_errors,
            "panel_restarts": counters.panel_restarts,
            "ffmpeg_restarts": counters.ffmpeg_restarts,
            "uptime_s": round(now - self.began),
        }
