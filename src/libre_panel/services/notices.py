"""Notices: short messages for what happens around the panel.

The volume turning, a new track, a sensor that crosses a limit, the panel that
came back from a restart. Each one is a toast with a kind, a key, a rank and
a payload, so the theme's toast style draws it (the band or the card) and the
theme's ``toast.off`` and the screen's ``suppresses`` decide whether it
shows. The service only decides *when* something is worth a message.

It looks at every new sample (``host.on("sample")``) and asks for the readings
it needs (``host.want``), so the volume and what is playing are read although
no theme on the panel shows them. The volume and what is playing are also
looked at whenever they change between two samples (``host.on("fresh")``), so
their message comes within a frame or two of the key, not with the next reading
(up to a second later).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NamedTuple

from libre_panel.i18n import t
from libre_panel.plugins import Service
from libre_panel.sensors.base import Snapshot
from libre_panel.services.readings import is_playing, number, readable

VOLUME_STEP = 1.0  # percent: a smaller change is no news
VOLUME_KEYS = frozenset({"audio.volume", "audio.muted"})
HINT_AFTER_S = 30.0  # without a volume reading for this long, say what is missing
COVER = "media.cover"
APP_ICON = "media.app_icon"  # the icon of the program that plays, if a source brings it
COVER_WAIT_S = 3.0  # a cover that comes after the title still joins the message
ICONS = {"cpu": "cpu", "gpu": "gpu", "ram": "ram", "mem": "ram", "disk": "disk", "fan": "fan"}


class Kind(NamedTuple):
    """What a theme can switch off (``toast.off``), how it ranks, and how long it
    stays (None: the theme's hold time)."""

    name: str
    rank: int
    seconds: float | None


VOLUME = Kind("volume", 0, None)
MUSIC = Kind("music", 1, 5.0)
WARNING = Kind("warning", 2, 5.0)
PANEL = Kind("panel", 2, 8.0)  # long enough for the first full frame after a restart


@dataclass
class Limit:
    """A sensor key and the values at which it is worth a message."""

    key: str
    warn: float | None = None
    crit: float | None = None
    name: str = ""
    icon: str | None = None
    scale: float | None = None  # the value a full bar stands for
    shown: int = 0  # the highest level told since the value was last below ``rearm``
    told: float = -math.inf  # when the last message was sent

    @property
    def rearm(self) -> float:
        """Below this the key is armed again."""
        return min(limit for limit in (self.warn, self.crit) if limit is not None)

    def level(self, value: float) -> int:
        if self.crit is not None and value >= self.crit:
            return 2
        return 1 if self.warn is not None and value >= self.warn else 0


def parse_limit(entry: Any, where: str, report: Callable[[str], None]) -> Limit | None:
    """One entry of the ``warnings`` option; a wrong one is reported and left out."""
    if not isinstance(entry, dict) or not isinstance(entry.get("key"), str) or not entry["key"]:
        report(f"{where}: needs a sensor key, e.g. {{ key = 'cpu.temp', crit = 90 }}")
        return None
    found: dict[str, float | None] = {}
    for field in ("warn", "crit", "max"):
        given = entry.get(field)
        if given is not None and number(given) is None:
            report(f"{where}.{field}: expected a number, got {given!r}")
            return None
        found[field] = number(given)
    warn, crit = found["warn"], found["crit"]
    if warn is None and crit is None:
        report(f"{where}: needs a warn or a crit limit")
        return None
    if warn is not None and crit is not None and warn >= crit:
        report(f"{where}: warn must be below crit")
        return None
    icon = entry.get("icon")
    return Limit(
        entry["key"].strip(), warn, crit, str(entry.get("name") or ""),
        icon if isinstance(icon, str) else None, found["max"],
    )  # fmt: skip


class Notices(Service):
    """Messages for the volume, a new track, sensors past a limit and a panel restart."""

    name = "notices"
    api = 1
    options = {
        "volume": ("bool", True),
        "music": ("bool", True),
        "restarted": ("bool", True),
        "warnings": ("list", []),
        "quiet_s": ("number", 120.0),
    }

    def __init__(
        self,
        host: Any,
        options: dict[str, Any],
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = datetime.now,
    ) -> None:
        super().__init__(host, options)
        self.clock, self.wall = clock, wall

        def report(text: str) -> None:
            host.log.warning("%s", text)

        entries = [
            parse_limit(e, f"warnings[{n}]", report) for n, e in enumerate(options["warnings"], 1)
        ]
        self.limits = [limit for limit in entries if limit is not None]
        self._volume: tuple[float, bool] | None = None  # at the last message, or the first value
        self._volume_seen = False
        self._hinted = False
        self._track: tuple[str, str] | None = None  # (title, artist) of what plays
        self._late: tuple[tuple[str, str], float] | None = None  # told without a cover, until
        self._began = clock()
        self._stopped = False

    def start(self) -> None:
        keys = [limit.key for limit in self.limits]
        if self.options["volume"]:
            keys += sorted(VOLUME_KEYS)
        if self.options["music"]:
            keys += ["media.*"]
        self.host.want(*keys)
        self.host.on("sample", self.on_sample)
        if self.options["volume"] or self.options["music"]:
            self.host.on("fresh", self.on_fresh)
        if self.options["restarted"]:
            self.host.on("panel-restarted", self.on_restarted)

    def stop(self) -> None:
        self._stopped = True  # a sample that is under way then says nothing

    # -- every sample ------------------------------------------------------------

    def on_sample(self, snapshot: Snapshot) -> None:
        if self._stopped:
            return
        now = self.clock()
        if self.options["volume"]:
            self._check_volume(snapshot, now)
        if self.options["music"]:
            self._check_music(snapshot, now, "the sample")
        for limit in self.limits:
            self._check_limit(limit, snapshot, now)

    def on_fresh(self, snapshot: Snapshot, keys: frozenset[str]) -> None:
        """The volume moved, or the track or its state changed, between two samples: tell it
        now, not with the next reading. (A track already told is not told again.)"""
        if self._stopped:
            return
        if self.options["volume"] and not VOLUME_KEYS.isdisjoint(keys):
            self._check_volume(snapshot, self.clock())
        if self.options["music"] and any(key.startswith("media.") for key in keys):
            self._check_music(snapshot, self.clock(), "a frame")

    def _check_volume(self, snapshot: Snapshot, now: float) -> None:
        volume = number(snapshot.value("audio.volume"))
        if volume is None:
            if not self._volume_seen and not self._hinted and now - self._began > HINT_AFTER_S:
                self._hinted = True
                self.host.log.info(
                    'no volume readings: add "audio" to [sensors] providers in config.toml'
                )
            return
        self._volume_seen = True
        muted = bool(snapshot.value("audio.muted"))
        before = self._volume
        if before is not None and muted == before[1] and abs(volume - before[0]) < VOLUME_STEP:
            return  # the mark stays what was told, so a slow turn still adds up to a step
        self._volume = (volume, muted)
        if before is None:
            return  # the first value is no change
        text, share = (t("Muted"), 0.0) if muted else (f"{volume:.0f}", volume / 100)
        self._tell(VOLUME, text, {"label": t("Volume"), "progress": share})

    def _check_music(self, snapshot: Snapshot, now: float, via: str = "the sample") -> None:
        title = snapshot.value("media.title")
        if not is_playing(snapshot) or not isinstance(title, str):
            return  # paused: the track stays what it was, so resuming says nothing
        artist = snapshot.value("media.artist")
        track = (title, artist if isinstance(artist, str) else "")
        cover = snapshot.images.get(COVER)
        if track == self._track:
            if self._late is not None:  # told without a cover; has it come since?
                if now > self._late[1]:
                    self._late = None
                elif cover is not None:
                    self._late = None
                    self._tell_track(snapshot, track, cover)
            return
        first, self._track, self._late = self._track is None, track, None
        if first:
            return  # what plays when the service starts is no news
        self.host.log.debug("notices: the new track is told on %s", via)
        if cover is None:
            self._late = (track, now + COVER_WAIT_S)
        self._tell_track(snapshot, track, cover)

    def _tell_track(self, snapshot: Snapshot, track: tuple[str, str], cover: Any) -> None:
        title, artist = track
        payload: dict[str, Any] = {"label": t("Now playing")}
        if cover is not None:
            payload["image"] = cover
        badge = snapshot.images.get(APP_ICON)
        if badge is not None:
            payload["badge"] = badge
        progress = number(snapshot.value("media.progress"))
        if progress is not None:
            payload["progress"] = progress
        self._tell(MUSIC, f"{title} – {artist}" if artist else title, payload, icon="music")

    def _check_limit(self, limit: Limit, snapshot: Snapshot, now: float) -> None:
        value = number(snapshot.value(limit.key))
        if value is None:
            return
        if value < limit.rearm:
            limit.shown = 0
            return
        level = limit.level(value)
        if level <= limit.shown:
            return
        escalation = limit.shown > 0  # worse than what was told: news also in the quiet time
        limit.shown = level
        if not escalation and now - limit.told < self.options["quiet_s"]:
            return
        limit.told = now
        reading = snapshot.readings.get(limit.key)
        unit = reading.unit if reading else ""
        name = limit.name or (reading.label if reading else "") or limit.key
        payload: dict[str, Any] = {"label": t("Warning")}
        full = limit.scale or (100.0 if unit == "%" else None)
        if full:
            payload["progress"] = max(0.0, min(1.0, value / full))
        icon = limit.icon or ICONS.get(limit.key.split(".")[0])
        if icon is None and unit == "°C":
            icon = "temperature"
        self._tell(
            WARNING, f"{name} {readable(value)} {unit}".rstrip(), payload,
            key=f"warning:{limit.key}", level="error" if level == 2 else "warning", icon=icon,
        )  # fmt: skip

    # -- the panel -----------------------------------------------------------------

    def on_restarted(self) -> None:
        """Back from a restart that cleared a hung video decoder (not from a replug)."""
        if self._stopped:
            return
        text = t("Panel restarted {time}", time=self.wall().strftime("%H:%M"))
        self._tell(PANEL, text, {"label": t("Panel")})

    def _tell(
        self,
        kind: Kind,
        text: str,
        payload: dict[str, Any],
        key: str = "",
        level: str = "info",
        icon: str | None = None,
    ) -> None:
        self.host.notify(
            text, icon=icon, level=level, seconds=kind.seconds, kind=kind.name, rank=kind.rank,
            payload=payload, key=key or kind.name,
        )  # fmt: skip
