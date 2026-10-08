"""What is playing: title, artist, album, position and the cover.

- Windows: the system's media controls (what the volume pop-up shows), through
  the ``winrt`` packages (``pip install "libre-panel[media]"``; the installer
  brings them along).
- Linux: any MPRIS player (Spotify, browsers, VLC, ...) through ``playerctl``.
- macOS: Spotify or Music, asked through ``osascript``.

Only read while a theme shows ``media.*`` readings; a cover is fetched only
when a theme shows it. Nothing is sent anywhere.
"""

from __future__ import annotations

import asyncio
import io
import logging
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from libre_panel.sensors.base import Reading, SensorProvider, wanted

log = logging.getLogger(__name__)

POLL_SECONDS = 1.0
MAX_COVER = 4 * 1024 * 1024
# media.state: shown in the user's language (the renderer translates it)
MEDIA_STATES = ("Playing", "Paused", "Stopped")


@dataclass
class Track:
    state: str  # "playing", "paused" or "stopped"
    title: str
    artist: str = ""
    album: str = ""
    position: float | None = None  # seconds, at ``at``
    duration: float | None = None
    cover: str | bytes | None = None  # a file, a web address, or the picture itself
    source: str = ""
    at: float = 0.0  # time.monotonic() of the position

    def readings(self, now: float) -> dict[str, Reading]:
        out = {
            "media.title": (self.title, "", "Title"),
            "media.artist": (self.artist, "", "Artist"),
            "media.album": (self.album, "", "Album"),
            "media.source": (self.source, "", "Player"),
            "media.state": (self.state.capitalize(), "", "Media"),
            "media.playing": (1 if self.state == "playing" else 0, "", "Playing"),
        }
        position = self.position
        if position is not None and self.state == "playing":
            position += max(0.0, now - self.at)  # it moved on since it was asked
        if self.duration:
            position = None if position is None else min(position, self.duration)
            out["media.duration"] = (self.duration, "s", "Length")
        if position is not None:
            out["media.position"] = (position, "s", "Position")
            if self.duration:
                out["media.progress"] = (position / self.duration, "", "Progress")
        return {
            key: Reading(key, value, unit, label)
            for key, (value, unit, label) in out.items()
            if value not in ("", None)
        }


def _app_name(model_id: str) -> str:
    """ "Spotify.exe" -> "Spotify"; "Microsoft.ZuneMusic_8wekyb3d8bbwe!App" -> "ZuneMusic"."""
    name = model_id.split("!", 1)[0].split("_", 1)[0]
    name = name.rsplit("\\", 1)[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.rsplit(".", 1)[-1] if "." in name else name


# -- Windows -------------------------------------------------------------------


class _WindowsBackend:
    """The current session of the system media transport controls."""

    def __init__(self) -> None:
        from winrt.windows.media.control import (  # noqa: F401 - fails without the packages
            GlobalSystemMediaTransportControlsSessionManager,
        )

        self._cover_key: tuple[str, str] | None = None
        self._cover: bytes | None = None
        try:  # this thread talks to Windows: join the multi-threaded apartment
            from winrt.runtime import ApartmentType, init_apartment

            init_apartment(ApartmentType.MULTI_THREADED)
        except Exception:  # noqa: BLE001, S110 - already joined
            pass

    async def poll(self, want_cover: bool) -> Track | None:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Manager,
        )

        manager = await Manager.request_async()
        session = manager.get_current_session()
        if session is None:
            return None
        props = await session.try_get_media_properties_async()
        if props is None or not props.title:
            return None
        status = int(session.get_playback_info().playback_status)
        state = {4: "playing", 5: "paused"}.get(status, "stopped")
        timeline = session.get_timeline_properties()
        duration = timeline.end_time.total_seconds() - timeline.start_time.total_seconds()
        position = timeline.position.total_seconds()
        at = time.monotonic()
        try:  # the position is as of its last update
            from datetime import UTC, datetime

            updated = timeline.last_updated_time
            if updated.year > 2000 and state == "playing":
                at -= max(0.0, (datetime.now(UTC) - updated).total_seconds())
        except (AttributeError, TypeError, ValueError, OverflowError):
            pass
        cover = None
        key = (props.title, props.artist)
        if want_cover and props.thumbnail is not None:
            if key != self._cover_key:
                self._cover_key, self._cover = key, await self._read(props.thumbnail)
            cover = self._cover
        return Track(
            state, props.title, props.artist or "", props.album_title or "",
            position if duration > 0 else None, duration if duration > 0 else None, cover,
            _app_name(session.source_app_user_model_id or ""), at,
        )  # fmt: skip

    @staticmethod
    async def _read(reference: Any) -> bytes | None:
        from winrt.windows.storage.streams import Buffer, InputStreamOptions

        try:
            stream = await reference.open_read_async()
            size = min(int(stream.size), MAX_COVER)
            buffer = Buffer(size)
            filled = await stream.read_async(buffer, size, InputStreamOptions.READ_AHEAD)
            filled = filled if filled is not None else buffer
            return bytes(memoryview(filled))[: filled.length]
        except Exception as exc:  # noqa: BLE001 - a missing cover is no error
            log.debug("media: no cover (%s)", exc)
            return None


# -- Linux ---------------------------------------------------------------------

_SEP = "\x1f"
_FORMAT = _SEP.join(
    ["{{status}}", "{{artist}}", "{{title}}", "{{album}}", "{{position}}",
     "{{mpris:length}}", "{{mpris:artUrl}}", "{{playerName}}"]
)  # fmt: skip


def parse_playerctl(text: str, at: float) -> Track | None:
    parts = text.rstrip("\n").split(_SEP)
    if len(parts) != 8 or not parts[2]:
        return None
    status, artist, title, album, position, length, art, player = parts

    def seconds(micro: str) -> float | None:
        try:
            return int(micro) / 1_000_000
        except ValueError:
            return None

    return Track(
        status.lower() if status.lower() in ("playing", "paused") else "stopped", title,
        artist, album, seconds(position), seconds(length) or None, art or None,
        player.capitalize(), at,
    )  # fmt: skip


class _PlayerctlBackend:
    def __init__(self) -> None:
        self.command = shutil.which("playerctl")
        if self.command is None:
            raise RuntimeError("playerctl is not installed")

    async def poll(self, want_cover: bool) -> Track | None:
        result = subprocess.run(
            [self.command, "metadata", "--format", _FORMAT],
            capture_output=True, text=True, timeout=3, check=False,
        )  # fmt: skip
        if result.returncode != 0:
            return None  # no player
        return parse_playerctl(result.stdout, time.monotonic())


# -- macOS ---------------------------------------------------------------------

_APPLESCRIPT = """
set sep to ASCII character 31
if application "Spotify" is running then
  tell application "Spotify"
    if player state is not stopped then
      set t to current track
      return (player state as text) & sep & (artist of t) & sep & (name of t) & sep & \
(album of t) & sep & (player position as text) & sep & ((duration of t) / 1000 as text) & \
sep & (artwork url of t) & sep & "Spotify"
    end if
  end tell
end if
if application "Music" is running then
  tell application "Music"
    if player state is not stopped then
      set t to current track
      return (player state as text) & sep & (artist of t) & sep & (name of t) & sep & \
(album of t) & sep & (player position as text) & sep & (duration of t as text) & sep & "" & \
sep & "Music"
    end if
  end tell
end if
return ""
"""


def parse_osascript(text: str, at: float) -> Track | None:
    parts = text.strip().split(_SEP)
    if len(parts) != 8 or not parts[2]:
        return None
    status, artist, title, album, position, length, art, player = parts

    def number(value: str) -> float | None:
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None

    return Track(
        status if status in ("playing", "paused") else "stopped", title, artist, album,
        number(position), number(length), art or None, player, at,
    )  # fmt: skip


class _AppleScriptBackend:
    async def poll(self, want_cover: bool) -> Track | None:
        result = subprocess.run(
            ["osascript", "-e", _APPLESCRIPT], capture_output=True, text=True, timeout=3,
            check=False,
        )  # fmt: skip
        if result.returncode != 0:
            return None
        return parse_osascript(result.stdout, time.monotonic())


def _backend() -> Any:
    if sys.platform == "win32":
        return _WindowsBackend()
    if sys.platform == "darwin":
        return _AppleScriptBackend()
    return _PlayerctlBackend()


# -- the provider --------------------------------------------------------------


def load_cover(cover: str | bytes | None) -> Image.Image | None:
    """A cover from its bytes, a file (path or file://) or the web."""
    if not cover:
        return None
    try:
        if isinstance(cover, bytes):
            data = cover
        elif cover.startswith(("http://", "https://")):
            request = urllib.request.Request(cover, headers={"User-Agent": "libre-panel"})
            with urllib.request.urlopen(request, timeout=5) as response:
                data = response.read(MAX_COVER + 1)
            if len(data) > MAX_COVER:
                return None
        else:  # a path, or a file:// address (file:///C:/... on Windows)
            path = cover
            if cover.startswith("file://"):
                path = urllib.request.url2pathname(urllib.parse.urlparse(cover).path)
            file = Path(path)
            if not file.is_file() or file.stat().st_size > MAX_COVER:
                return None
            data = file.read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            image.thumbnail((512, 512))
            return image.convert("RGBA")
    except Exception as exc:  # noqa: BLE001 - a broken cover is no error
        log.debug("media: cover not loaded (%s)", exc)
        return None


class MediaProvider(SensorProvider):
    """media.* readings and the ``media.cover`` picture, while a theme shows them."""

    name = "media"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._track: Track | None = None
        self._cover: tuple[Any, Image.Image | None] = (None, None)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._warned = False

    def _start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="sensors-media", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            backend = _backend()
        except Exception as exc:  # noqa: BLE001
            if not self._warned:
                hint = ' (pip install "libre-panel[media]")' if sys.platform == "win32" else ""
                log.warning("media: what is playing cannot be read: %s%s", exc, hint)
                self._warned = True
            return
        asyncio.run(self._loop(backend))

    async def _loop(self, backend: Any) -> None:
        idle = 0.0
        while not self._stop.is_set():
            if not wanted("media."):
                idle += POLL_SECONDS
                if idle > 60:  # nobody looked for a minute: end, start again on demand
                    with self._lock:
                        self._track = None
                    return
            else:
                idle = 0.0
                try:
                    track = await backend.poll(wanted("media.cover"))
                except Exception as exc:  # noqa: BLE001 - a player must not stop the panel
                    log.debug("media: %s", exc)
                    track = None
                cover = track.cover if track else None
                if cover is not None and cover != self._cover[0]:
                    self._cover = (cover, load_cover(cover))
                with self._lock:
                    self._track = track
            await asyncio.sleep(POLL_SECONDS)

    def read(self) -> dict[str, Reading]:
        if not wanted("media."):
            return {}
        self._start()
        with self._lock:
            track = self._track
        return track.readings(time.monotonic()) if track else {}

    def images(self) -> dict[str, Any]:
        with self._lock:
            track = self._track
        if track is None or track.cover is None or track.cover != self._cover[0]:
            return {}
        return {"media.cover": self._cover[1]} if self._cover[1] is not None else {}

    def close(self) -> None:
        self._stop.set()
