"""Who turns the frames' colours into YUV when ``video.pixels`` is "auto": the graphics card,
where a colour check found its conversion identical to the processor's (every patch within
±2 code values, :func:`~colour_check.judge`), else the processor.

The verdict is kept in video-state.json for each combination it holds for: the encoder, the
panel's range (limited or full), the ffmpeg program, its version and the graphics driver's
version where it is cheaply known (:func:`version`). The first time the display starts one of
the card's encoders in a combination without a verdict, the check runs once in the background
(:func:`ensure`): a few frames of the test picture through the very command the display builds,
the card's way and the processor's, a few seconds, never on the panel and never while another
check runs (:func:`~colour_check.exclusive`). Until it has spoken the processor converts; once
the card is found right, the display goes over to it at its next frame. The person's own check
(Settings → Panel → Check the colours, *Check again*) keeps its verdict here too.

A card's encoder that gives no picture converting itself is a verdict as well ("refused"): the
processor converts for it. A check that failed or was stopped says nothing, and the processor
goes on converting; it is tried again at the next start of the app.
"""

from __future__ import annotations

import logging
import platform
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from libre_panel.devices import colour_check, h264, video_state
from libre_panel.devices.h264 import NO_WINDOW, EncoderSettings

log = logging.getLogger(__name__)

FRAMES = 5  # of the test picture, each way
TIMEOUT_S = 10.0  # the longest an ffmpeg of the background check may take
VERSION_S = 10.0  # the longest ``ffmpeg -version`` may take
REREAD_S = 5.0  # a verdict is read from video-state.json again at most this often (another
# process of the app, the person's check, may have found one meanwhile)

_lock = threading.Lock()
_versions: dict[str, str] = {}
_driver: str | None = None
_known: dict[tuple[str, str, bool], tuple[float, dict[str, Any] | None]] = {}
_running: set[tuple[str, str, bool]] = set()
_tried: set[tuple[str, str, bool]] = set()  # began in this run, whatever came of it
_threads: list[threading.Thread] = []


def can(encoder: str, full: bool) -> bool:
    """Whether the card can convert for ``encoder`` at all: one of its encoders, and for a
    panel in ``full`` range one that makes full range (h264.FULL_RANGE_CARDS)."""
    return encoder in h264.CARD_ENCODERS and (not full or encoder in h264.FULL_RANGE_CARDS)


def driver_version() -> str:
    """The graphics driver's version, where it is cheaply known (Windows: the display
    adapters' in the registry; Linux: NVIDIA's module and the kernel's); "" else."""
    global _driver
    if _driver is None:
        try:
            _driver = _read_driver()
        except Exception:  # (nothing known is no fault)
            _driver = ""
    return _driver


def _read_driver() -> str:
    if sys.platform == "win32":
        import winreg

        found = []
        name = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, name) as adapters:
            for i in range(16):
                try:
                    sub = winreg.EnumKey(adapters, i)
                except OSError:
                    break
                try:
                    with winreg.OpenKey(adapters, sub) as adapter:
                        found.append(str(winreg.QueryValueEx(adapter, "DriverVersion")[0]))
                except OSError:  # ("Properties", one without a driver)
                    continue
        return ",".join(found)
    if sys.platform.startswith("linux"):
        parts = []
        try:
            with open("/sys/module/nvidia/version", encoding="utf-8") as file:
                parts.append("nvidia " + file.read().strip())
        except OSError:
            pass
        parts.append(platform.release())
        return ",".join(parts)
    return ""


def version(ffmpeg: str) -> str:
    """What a verdict holds for besides the encoder and the range: the first line ffmpeg's
    ``-version`` says and the graphics driver's version. Asked once a run."""
    with _lock:
        found = _versions.get(ffmpeg)
    if found is not None:
        return found
    try:
        done = subprocess.run(
            [ffmpeg, "-hide_banner", "-version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=VERSION_S,
            creationflags=NO_WINDOW,
        )
        lines = done.stdout.decode("utf-8", "replace").splitlines()
        said = lines[0].strip() if lines else ""
    except (OSError, subprocess.SubprocessError):
        said = ""
    found = f"{said} | driver {driver_version() or '-'}"
    with _lock:
        _versions[ffmpeg] = found
    return found


def verdict(ffmpeg: str, encoder: str, full: bool) -> dict[str, Any] | None:
    """The verdict kept for this combination (:mod:`video_state` ``cards``): ``card`` (True: the
    card converts), how the check ended (``state``), its ``verdict`` and ``worst`` difference,
    ``when`` (seconds since 1970) and whether the person's check found it (``by``); None for
    none within the week."""
    key = (ffmpeg, encoder, full)
    now = time.monotonic()
    with _lock:
        cached = _known.get(key)
    if cached is not None and now - cached[0] < REREAD_S:
        return cached[1]
    found = video_state.card_choice(ffmpeg, encoder, full, version(ffmpeg))
    with _lock:
        _known[key] = (now, found)
    return found


def chosen(ffmpeg: str | None, encoder: str, full: bool) -> bool:
    """Whether the card converts for ``encoder`` under "auto": only where it can and a check
    found it identical."""
    if ffmpeg is None or not can(encoder, full):
        return False
    found = verdict(ffmpeg, encoder, full)
    return bool(found and found.get("card"))


def entry_of(result: dict[str, Any], ffmpeg: str, by: str, now: float | None = None):
    """What a check's ``result`` for one encoder says for the choice, as kept; None when it says
    nothing (failed, stopped, or not the card against the processor)."""
    if (result.get("compared") or [None])[0] != "card":
        return None
    state = result.get("state")
    if state == "done":
        card = result.get("verdict") == "identical"
    elif state == "refused":
        card = False
    else:
        return None
    return {
        "card": card,
        "state": state,
        "verdict": result.get("verdict"),
        "worst": result.get("worst"),
        "when": int(time.time() if now is None else now),
        "version": version(ffmpeg),
        "by": by,
    }


def record(
    ffmpeg: str, result: dict[str, Any], full: bool, by: str = "check"
) -> dict[str, Any] | None:
    """Keep what a check found for one encoder (``by``: "check", the person's; "background");
    the display follows at its next frame. The entry kept, or None when it says nothing."""
    encoder = result.get("encoder", "")
    if not can(encoder, full):
        return None
    entry = entry_of(result, ffmpeg, by)
    if entry is None:
        return None
    video_state.keep_card_choice(ffmpeg, encoder, full, entry)
    with _lock:
        _known[(ffmpeg, encoder, full)] = (time.monotonic(), entry)
    return entry


def checking(ffmpeg: str, encoder: str, full: bool) -> bool:
    """Whether the background check of this combination runs now."""
    with _lock:
        return (ffmpeg, encoder, full) in _running


def ensure(
    ffmpeg: str,
    encoder: str,
    full: bool,
    settings: Callable[[str, str], EncoderSettings],
    size: tuple[int, int],
    rotate: int | None,
    command: colour_check.Command | None = None,
) -> threading.Thread | None:
    """The background check for this combination, begun unless its verdict is known, it runs
    or ran in this run already, or the card cannot convert for ``encoder`` (:func:`can`).
    ``settings(encoder, pixels)`` and ``command`` are the display's (:func:`colour_check.
    check_encoder`). The thread, or None when none began."""
    key = (ffmpeg, encoder, full)
    if not can(encoder, full) or verdict(ffmpeg, encoder, full) is not None:
        return None
    with _lock:
        if key in _running or key in _tried:
            return None
        _running.add(key)
        _tried.add(key)
        thread = threading.Thread(
            target=_check,
            args=(key, settings, size, rotate, command or h264.ffmpeg_command),
            name="colour-check-background",
            daemon=True,
        )
        _threads[:] = [*(t for t in _threads if t.is_alive()), thread]
    thread.start()
    return thread


def _check(
    key: tuple[str, str, bool],
    settings: Callable[[str, str], EncoderSettings],
    size: tuple[int, int],
    rotate: int | None,
    command: colour_check.Command,
) -> None:
    ffmpeg, encoder, full = key
    try:
        with colour_check.exclusive() as free:
            if not free:  # another check runs: this one waits for the next start of the app
                with _lock:
                    _tried.discard(key)  # (or for the next encoder start in this run)
                log.info("video: the colour check of %s waits: another check runs", encoder)
                return
            result = colour_check.check_encoder(
                ffmpeg,
                encoder,
                settings,
                size,
                rotate,
                command,
                full=full,
                frames=FRAMES,
                timeout=TIMEOUT_S,
            )
        entry = record(ffmpeg, result, full, by="background")
        if entry is None:
            log.info(
                "video: the colour check of %s said nothing (%s: %s); the processor converts",
                encoder,
                result.get("state"),
                result.get("error") or "-",
            )
        elif entry["card"]:
            log.info("video: %s converts the colours as the processor does; the card converts",
                     encoder)  # fmt: skip
        else:
            log.info(
                "video: %s converts the colours otherwise (%s); the processor converts",
                encoder,
                entry["verdict"] or entry["state"],
            )
    except Exception:  # the processor goes on converting
        log.exception("video: the colour check of %s stopped at a fault", encoder)
    finally:
        with _lock:
            _running.discard(key)


def forget(wait: float = 0.0) -> None:
    """What this run knows and tried, forgotten, after the checks under way have ended (within
    ``wait`` seconds each; tests)."""
    with _lock:
        threads = list(_threads)
    for thread in threads:
        thread.join(wait)
    with _lock:
        _known.clear()
        _tried.clear()
        _versions.clear()
        _threads[:] = [t for t in _threads if t.is_alive()]
