"""What the video mode found out, kept for the next run: ``video-state.json`` beside config.toml.

- **The bit rate** "auto" got to, per panel (its model, its place on the bus and the picture
  size, as :data:`~libre_panel.devices.turzx_video.RATES`): a new run starts there instead of
  climbing the steps again.
- **Whether ffmpeg can start an encoder** of the graphics card, per ffmpeg program and
  encoder, and when that was asked: the answer holds for :data:`PROBE_S`, then it is asked
  again (a new driver may change it).

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
    if not isinstance(works, bool) or not isinstance(asked, int | float):
        return None
    now = time.time() if now is None else now
    return works if 0 <= now - asked < PROBE_S else None


def keep_probe(ffmpeg: str, encoder: str, works: bool, now: float | None = None) -> None:
    asked = int(time.time() if now is None else now)  # (down: never later than now)
    _write("encoders", f"{encoder} {ffmpeg}", {"works": works, "asked": asked})
