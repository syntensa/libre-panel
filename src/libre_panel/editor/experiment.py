"""Settings → Panel → Video experiment, run by itself (``/api/experiment``).

The steps try what a panel takes beyond what was measured on the 9.2": the bit rate's cap left
to "auto" for minutes (its ladder climbs), a lower crf, x264's fastest preset, the profiles
main and high, short keyframe spacing, the graphics card's encoder where the PC has one, 60
frames a second, and a flood (a high cap at a low crf). Each step writes its settings (every
panel's loop opens its panel again with them, the one beside the first too), lets the panel
settle, then samples the numbers the meter makes (``RunStatus.stats``) for every panel for a
while, and judges them: pictures must have reached the panel, the frame rate must have held,
the queue must not have kept filling. The settings from before the run come back at the end,
or when it is stopped. What only the eye can judge (whether the picture was there) the
results cannot say; the log has a line per step.
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# The video mode's defaults (config.py VideoConfig): what every step starts from.
MEASURED: dict[str, Any] = {
    "video.maxrate": "auto",
    "video.crf": 20,
    "video.preset": "superfast",
    "video.profile": "auto",
    "video.encoder": "auto",
    "video.keyframe_s": 10.0,
    "video.fps": 50,
    "video.device_fps": 60,
}
HARDWARE_ENCODERS = {"amd": "h264_amf", "nvidia": "h264_nvenc", "intel": "h264_qsv"}


@dataclass(frozen=True)
class Step:
    id: str
    changes: dict[str, Any]  # what differs from MEASURED
    seconds: float  # how long the numbers are sampled


def gpu_vendor(name: str) -> str:
    """ "amd", "nvidia", "intel" or "" from the graphics card's name."""
    lower = name.lower()
    if any(word in lower for word in ("nvidia", "geforce", "rtx", "gtx", "quadro")):
        return "nvidia"
    if any(word in lower for word in ("amd", "radeon", "ati ")):
        return "amd"
    if any(word in lower for word in ("intel", "arc ", "iris")):
        return "intel"
    return ""


def steps(vendor: str = "") -> list[Step]:
    """The steps in their order; the graphics card's encoder only where ``vendor`` has one."""
    found = [
        Step("auto", {}, 240.0),  # "auto" climbs a step a minute while the panel keeps up
        Step("crf-18", {"video.crf": 18}, 30.0),
        Step("ultrafast", {"video.preset": "ultrafast"}, 20.0),
        Step("main", {"video.profile": "main"}, 20.0),
        Step("high", {"video.profile": "high"}, 20.0),
        Step("keyframe-2", {"video.keyframe_s": 2.0}, 20.0),
    ]
    encoder = HARDWARE_ENCODERS.get(vendor)
    if encoder:
        found.append(Step(encoder, {"video.encoder": encoder}, 30.0))
    found.append(Step("fps-60", {"video.fps": 60, "video.device_fps": 70}, 30.0))
    found.append(Step("flood", {"video.maxrate": "24M", "video.crf": 16}, 30.0))
    return found


def _mean(samples: list[dict[str, Any]], key: str) -> float | None:
    values = [s[key] for s in samples if isinstance(s.get(key), int | float)]
    return round(statistics.fmean(values), 1) if values else None


def _delta(first: dict[str, Any], last: dict[str, Any], *keys: str) -> int:
    return sum(int(last.get(k) or 0) - int(first.get(k) or 0) for k in keys)


def judge(samples: list[dict[str, Any]], first: dict[str, Any], last: dict[str, Any]) -> dict:
    """One panel's numbers over a step, and the verdict: "failed" when no pictures reached
    the panel, the panel had to be restarted or ffmpeg kept stopping; "stutter" when the
    frame rate fell under nine tenths of the target or the sender kept waiting for room;
    "pc" when the frame rate fell but the drawing took most of a frame's time (the PC, not
    the panel: a game was running); "no video" when the panel was not in video mode; else
    "ok"."""
    fps, kbps, blocks = (
        _mean(samples, "fps"),
        _mean(samples, "kbps"),
        _mean(samples, "blocks_per_s"),
    )
    render = _mean(samples, "render_ms")
    target = last.get("target_fps") or first.get("target_fps")
    waits, drops = _delta(first, last, "waits"), _delta(first, last, "drops")
    faults = _delta(first, last, "usb_errors", "panel_restarts", "ffmpeg_restarts")
    slow = fps is not None and target and fps < 0.9 * target
    if not samples or last.get("max_depth") is None and blocks is None:
        verdict = "no video"
    elif (
        (blocks or 0) < 1
        or _delta(first, last, "panel_restarts")
        or _delta(first, last, "ffmpeg_restarts") >= 2
    ):
        verdict = "failed"
    elif waits > 10 or drops > 5:
        verdict = "stutter"
    elif slow and render is not None and target and render > 0.8 * 1000 / target:
        verdict = "pc"
    elif slow:
        verdict = "stutter"
    else:
        verdict = "ok"
    return {
        "fps": fps,
        "target_fps": target,
        "render_ms": render,
        "kbps": kbps,
        "blocks_per_s": blocks,
        "bit_rate": last.get("bit_rate"),
        "waits": waits,
        "drops": drops,
        "faults": faults,
        "verdict": verdict,
    }


WORST = ("no video", "ok", "pc", "stutter", "failed")


class ExperimentError(ValueError):
    pass


class Experiment:
    """The run: :meth:`start` in a thread of its own, :meth:`stop`, :meth:`state` for the app.

    ``write(changes)`` writes settings (the Settings' ``write``), ``read()`` gives the video
    table as the settings serve it, ``status_of()`` the first panel's ``RunStatus`` (its
    ``more`` has the panels beside it), ``gpu()`` the graphics card's name.
    """

    SETTLE_S = 6.0  # a change: the panel opens again, ffmpeg starts, the meter's first second
    SAMPLE_S = 1.0
    scale = 1.0  # tests shorten every wait

    def __init__(
        self,
        write: Callable[[dict[str, Any]], Any],
        read: Callable[[], dict[str, Any]],
        status_of: Callable[[], Any],
        gpu: Callable[[], str] = lambda: "",
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.write, self.read, self.status_of, self.gpu = write, read, status_of, gpu
        self.clock, self.sleep = clock, sleep
        self.steps: list[Step] = []
        self.results: list[dict[str, Any]] = []
        self.before: dict[str, Any] = {}
        self.running = False
        self.step = -1
        self.left_s = 0.0
        self.error = ""
        self.ended = ""  # "done", "stopped" or "" while it runs or never ran
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self.running:
                raise ExperimentError("the experiment is running")
            video = self.read()
            self.before = {
                key: video[key.split(".", 1)[1]]
                for key in MEASURED
                if key.split(".", 1)[1] in video
            }
            self.steps = steps(gpu_vendor(self.gpu() or ""))
            self.results, self.error, self.ended, self.step = [], "", "", -1
            self._stop = threading.Event()
            self.running = True
            self._thread = threading.Thread(target=self._run, name="video-experiment", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def state(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "step": self.step,
            "of": len(self.steps),
            "left_s": round(self.left_s),
            "steps": [{"id": s.id, "changes": s.changes, "seconds": s.seconds} for s in self.steps],
            "results": list(self.results),
            "error": self.error,
            "ended": self.ended,
        }

    # -- the run -------------------------------------------------------------------------------

    def _run(self) -> None:
        try:
            for i, step in enumerate(self.steps):
                if self._stop.is_set():
                    break
                self.step = i
                self.write({**MEASURED, **step.changes})
                self._wait(self.SETTLE_S)
                result = self._measure(step)
                self.results.append(result)
                log.info("experiment: %s: %s", step.id, self._summary(result))
        except Exception as exc:  # the app shows it; the settings still come back
            self.error = str(exc)
            log.exception("the video experiment stopped at a fault")
        finally:
            try:
                self.write(self.before)
            except Exception as exc:
                log.warning("the settings from before the experiment did not come back: %s", exc)
                self.error = self.error or str(exc)
            self.ended = "stopped" if self._stop.is_set() else "done"
            self.running = False

    def _wait(self, seconds: float) -> None:
        self.sleep(seconds * self.scale)

    def _snapshot(self) -> dict[int, dict[str, Any]]:
        """Every panel's stats now, by its number (1 the first), the dict the meter made."""
        status = self.status_of()
        if status is None:
            return {}
        found = {1: dict(getattr(status, "stats", None) or {})}
        for n, each in sorted((getattr(status, "more", None) or {}).items()):
            found[n + 2] = dict(getattr(each, "stats", None) or {})
        return found

    def _measure(self, step: Step) -> dict[str, Any]:
        first = self._snapshot()
        samples: dict[int, list[dict[str, Any]]] = {n: [] for n in first}
        began = self.clock()
        total = step.seconds * self.scale
        while not self._stop.is_set():
            self.left_s = max(0.0, (total - (self.clock() - began)) / self.scale)
            if self.clock() - began >= total:
                break
            self._wait(self.SAMPLE_S)
            for n, stats in self._snapshot().items():
                samples.setdefault(n, []).append(stats)
        last = self._snapshot()
        panels = [
            {"panel": n, **judge(samples.get(n, []), first.get(n, {}), last.get(n, {}))}
            for n in sorted(set(first) | set(last))
        ]
        verdict = max((p["verdict"] for p in panels), key=WORST.index, default="no video")
        return {"id": step.id, "changes": step.changes, "panels": panels, "verdict": verdict}

    @staticmethod
    def _summary(result: dict[str, Any]) -> str:
        parts = []
        for p in result["panels"]:
            fps = "-" if p["fps"] is None else f"{p['fps']:.0f}"
            kbps = "-" if p["kbps"] is None else f"{p['kbps']:.0f}"
            parts.append(
                f"panel {p['panel']} {p['verdict']} ({fps} fps, {kbps} KB/s, cap {p['bit_rate']}, "
                f"waited {p['waits']}, dropped {p['drops']}, faults {p['faults']})"
            )
        return "; ".join(parts) or "no panel"
