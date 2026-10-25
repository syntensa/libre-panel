"""What the video mode found out, kept for the next run: ``video-state.json`` beside config.toml.

- **The bit rate** "auto" got to, per panel (its model, its place on the bus and the picture
  size, as :data:`~libre_panel.devices.turzx_video.RATES`): a new run starts there instead of
  climbing the steps again.
- **Whether ffmpeg can start an encoder** of the graphics card, per ffmpeg program and
  encoder, when that was asked and the last line ffmpeg said (why "auto" passed it over, which
  Settings → Panel → Check the colours shows): the answer holds for :data:`PROBE_S`, then it
  is asked again (a new driver may change it).
- **An encoder of the card that gave nothing converting the frames itself** (it ended before
  its first picture so), per ffmpeg program and encoder, and when: for :data:`PROBE_S` every
  connection has the processor convert for it, then the card's way is tried again.
- **Whether the card converts the colours as the processor does** (card_choice.py), per
  encoder, range and ffmpeg program, with that program's version and when it was checked: the
  colour check's verdict, by which ``video.pixels = "auto"`` lets the card convert or not; asked
  again after :data:`PROBE_S`, or at once for another version of ffmpeg.

The file is written only where config.toml's folder is there; one that cannot be read counts
as empty, and what is not in it is found out as before.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from libre_panel.config import config_dir

log = logging.getLogger(__name__)

STATE_FILENAME = "video-state.json"
PROBE_S = 7 * 24 * 3600.0  # a probe's answer holds a week
# The way of asking: answers of an earlier way are asked again (the probe of 0.7.1rc5 and
# before fed 64x64 pictures, which a card's encoder may refuse although it works).
PROBE_WAY = 2
SAID_CHARS = 300  # of what ffmpeg said last in a probe, kept

_lock = threading.Lock()  # two panels in one run write the same file


def _path() -> Path:
    return config_dir() / STATE_FILENAME


def _read() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _part(name: str) -> dict[str, Any]:
    part = _read().get(name)
    return part if isinstance(part, dict) else {}


def _write(name: str, key: str, value: Any) -> None:
    """``value`` under ``key`` in the part ``name``, the rest of the file as it was."""
    path = _path()
    if not path.parent.is_dir():
        return  # nothing is kept yet (no config.toml's folder): found out again next time
    with _lock:
        data = _read()
        part = data.get(name)
        data[name] = {**(part if isinstance(part, dict) else {}), key: value}
        # in whole: a run that starts while it is written never finds half a file
        tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            log.debug("%s not written: %s", STATE_FILENAME, exc)
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)


def _rate_key(key: tuple[str, str, tuple[int, int]]) -> str:
    model, place, (width, height) = key
    return f"{model} {place} {width}x{height}"


def rate(key: tuple[str, str, tuple[int, int]]) -> float:
    """The bit rate "auto" got to on this panel last time, in Mbit/s; 0 when none is known."""
    value = _part("rates").get(_rate_key(key))
    return float(value) if isinstance(value, int | float) and value > 0 else 0.0


def keep_rate(key: tuple[str, str, tuple[int, int]], mbit: float) -> None:
    _write("rates", _rate_key(key), mbit)


def probed(ffmpeg: str, encoder: str, now: float | None = None) -> bool | None:
    """Whether ``ffmpeg`` could encode with ``encoder`` when it was asked within the last
    week; None when it was not (or the clock went back since)."""
    entry = _part("encoders").get(f"{encoder} {ffmpeg}")
    if not isinstance(entry, dict):
        return None
    works, asked = entry.get("works"), entry.get("asked")
    if entry.get("way") != PROBE_WAY:
        return None
    if not isinstance(works, bool) or not isinstance(asked, int | float):
        return None
    now = time.time() if now is None else now
    return works if 0 <= now - asked < PROBE_S else None


def keep_probe(
    ffmpeg: str, encoder: str, works: bool, said: str = "", now: float | None = None
) -> None:
    asked = int(time.time() if now is None else now)  # (down: never later than now)
    entry: dict[str, Any] = {"works": works, "asked": asked, "way": PROBE_WAY}
    if said:
        entry["said"] = said[:SAID_CHARS]
    _write("encoders", f"{encoder} {ffmpeg}", entry)


def probe_said(ffmpeg: str, encoder: str, now: float | None = None) -> str:
    """The last line ffmpeg said when ``encoder`` was probed within the week ("": nothing)."""
    if probed(ffmpeg, encoder, now) is None:
        return ""
    said = _part("encoders").get(f"{encoder} {ffmpeg}", {}).get("said")
    return said if isinstance(said, str) else ""


def refused(ffmpeg: str, encoder: str, now: float | None = None) -> bool:
    """Whether ``encoder`` of ``ffmpeg`` took no four bytes a pixel within the last week (or
    the clock went back since: no)."""
    entry = _part("feeds").get(f"{encoder} {ffmpeg}")
    if not isinstance(entry, dict) or entry.get("refused") != "rgb0":
        return False
    when = entry.get("when")
    if not isinstance(when, int | float):
        return False
    now = time.time() if now is None else now
    return 0 <= now - when < PROBE_S


def keep_refusal(ffmpeg: str, encoder: str, now: float | None = None) -> None:
    """``encoder`` of ``ffmpeg`` ended before its first picture fed four bytes a pixel."""
    when = int(time.time() if now is None else now)
    _write("feeds", f"{encoder} {ffmpeg}", {"refused": "rgb0", "when": when})


def _card_key(ffmpeg: str, encoder: str, full: bool) -> str:
    return f"{encoder} {'full' if full else 'limited'} {ffmpeg}"


def card_choice(
    ffmpeg: str, encoder: str, full: bool, version: str, now: float | None = None
) -> dict[str, Any] | None:
    """The colour check's verdict on the card's conversion of ``encoder`` (in full or limited
    range) kept for this ffmpeg program in this ``version`` within the week; None when there is
    none (or the clock went back since)."""
    entry = _part("cards").get(_card_key(ffmpeg, encoder, full))
    if not isinstance(entry, dict) or entry.get("version") != version:
        return None
    when = entry.get("when")
    if not isinstance(when, int | float) or not isinstance(entry.get("card"), bool):
        return None
    now = time.time() if now is None else now
    return entry if 0 <= now - when < PROBE_S else None


def keep_card_choice(ffmpeg: str, encoder: str, full: bool, entry: dict[str, Any]) -> None:
    _write("cards", _card_key(ffmpeg, encoder, full), entry)
