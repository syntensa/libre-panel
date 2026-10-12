"""What is playing: title, artist, album, position and the cover.

- Windows: the system's media controls (what the volume pop-up shows), through
  the ``winrt`` packages (``pip install "libre-panel[media]"``; the installer
  brings them along). Windows tells when the session, the track or the
  playback changes, so a new track shows at once; a slow poll is the safety
  net. The cover is read when the track changes, not on every look.
- Linux: any MPRIS player (Spotify, browsers, VLC, ...) through ``playerctl``.

Only read while a theme shows ``media.*`` readings; a cover is fetched only
when a theme shows it, and the icon of the player (``media.app_icon``, see
``appicon``) only when a theme or a service reads it. Nothing is sent anywhere.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PIL import Image

from libre_panel.sensors.base import Reading, SensorProvider, wanted

log = logging.getLogger(__name__)

POLL_SECONDS = 1.0  # playerctl cannot tell what changes: it is asked this often
SAFETY_SECONDS = 5.0  # Windows tells what changes; this only catches a notice that went missing
SETTLE_SECONDS = 1.0  # after a notice, once more: players fill in the cover and the position later
MIN_GAP_SECONDS = 0.05  # two looks are at least this far apart, whatever the notices
SEEK_SECONDS = 1.0  # a position further than this from the expected one is a seek
IDLE_SECONDS = 60.0  # nobody looked for this long: the thread ends, and starts on demand
IDLE_CHECK_SECONDS = 1.0  # while nobody looks: how often to see whether somebody does
COVER_RETRY_SECONDS = 10.0  # a cover that could not be read is asked for again after this long
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
    app_id: str = ""  # what finds the player's icon: the AppUserModelID or the MPRIS name

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
                out["media.remaining"] = (self.duration - position, "s", "Remaining")
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


def _winrt() -> Any:
    """The WinRT pieces the media controls need, in one object. Raises ImportError
    without the packages (``pip install "libre-panel[media]"``)."""
    from winrt.runtime import ApartmentType, init_apartment
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager,
    )
    from winrt.windows.storage.streams import Buffer, InputStreamOptions

    return SimpleNamespace(
        Manager=Manager,
        Buffer=Buffer,
        InputStreamOptions=InputStreamOptions,
        ApartmentType=ApartmentType,
        init_apartment=init_apartment,
    )


class _WindowsBackend:
    """The current session of the system media transport controls, by its events.

    Windows tells when the session, the track or the playback changes, and the provider
    looks then, instead of every second. A timeline event (the position: a seek, or one of
    those players send every few seconds) brings a look at the timeline alone, a few
    milliseconds, and changes the track only where the position is not where it was expected
    (:data:`SEEK_SECONDS`). ``interval`` is the safety poll for a notice that went missing.
    """

    MANAGER_EVENTS = ("current_session_changed",)
    SESSION_EVENTS = ("media_properties_changed", "playback_info_changed",
                      "timeline_properties_changed")  # fmt: skip
    TIMELINE = "timeline_properties_changed"

    def __init__(self) -> None:
        self._w = _winrt()  # fails without the packages
        try:  # this thread talks to Windows: join the multi-threaded apartment
            self._w.init_apartment(self._w.ApartmentType.MULTI_THREADED)
        except Exception:  # noqa: BLE001, S110 - already joined
            pass
        self._wake: Callable[[], None] = lambda: None
        self._manager: Any = None
        self._manager_events: list[tuple[Any, str, Any, Any]] = []
        self._manager_listens = self._session_listens = True  # every event could be subscribed
        self._session_id: str | None = None  # the AppUserModelID of the session listened to
        self._session_events: list[tuple[Any, str, Any, Any]] = []
        self._new_session = False  # the current session changed: listen to it again
        self._new_properties = False  # the track's properties changed since the cover was read
        self._news: set[str] = set()  # the events since the last look
        self._last: Track | None = None  # what the last full look found
        self.timeline_only = False  # the last look read the timeline alone (no settle look)
        self._noticed_at: float | None = None  # when the first event since the last look came
        self.trigger: tuple[tuple[str, ...], float | None] = ((), None)  # of the last look
        self._cover_key: tuple[str, str] | None = None
        self._cover: bytes | None = None
        self._cover_asked = -math.inf  # when a cover was last read (monotonic clock)

    @property
    def interval(self) -> float:
        """Seconds until the next look when nothing is reported."""
        listens = self._manager_listens and self._session_listens
        return SAFETY_SECONDS if listens else POLL_SECONDS

    def bind(self, wake: Callable[[], None]) -> None:
        """``wake`` is called, from any thread, when Windows has news."""
        self._wake = wake

    def close(self) -> None:
        self._unsubscribe(self._session_events)
        self._unsubscribe(self._manager_events)
        self._manager = None
        self._session_id = None

    async def poll(self, want_cover: bool) -> Track | None:
        try:
            return await self._look(want_cover)
        except Exception:
            self.close()  # the manager and the subscriptions may be unusable now: start anew
            raise

    async def _look(self, want_cover: bool) -> Track | None:
        # Taken before looking: news that comes while we look stays for the next look.
        news, self._new_properties = self._new_properties, False
        kinds, self._news = self._news, set()
        self.trigger, self._noticed_at = (tuple(sorted(kinds)), self._noticed_at), None
        self.timeline_only = kinds == {self.TIMELINE} and self._last is not None
        if self.timeline_only and self._manager is not None and not self._new_session:
            return self._moved(self._last)  # type: ignore[arg-type]
        self.timeline_only = False
        self._last = track = await self._full_look(want_cover, news)
        return track

    def _moved(self, track: Track) -> Track:
        """The timeline alone: the track as it was, unless the position is not where it was
        expected (a seek): then with the position the player says."""
        session = self._manager.get_current_session()
        if session is None:
            return track
        position, duration, at = self._timeline(session, track.state)
        if position is None or track.position is None:
            return track
        expected = track.position + (max(0.0, at - track.at) if track.state == "playing" else 0)
        if abs(position - expected) <= SEEK_SECONDS:
            return track  # the same object: nothing downstream changes
        self._last = moved = replace(track, position=position, duration=duration, at=at)
        return moved

    async def _full_look(self, want_cover: bool, news: bool) -> Track | None:
        if self._manager is None:
            self._manager = await self._w.Manager.request_async()
            self._manager_listens = self._subscribe(
                self._manager, self.MANAGER_EVENTS, self._manager_events
            )
        session = self._manager.get_current_session()
        app_id = (session.source_app_user_model_id or "") if session is not None else None
        self._follow(session, app_id)
        if session is None:
            return None
        props = await session.try_get_media_properties_async()
        if props is None or not props.title:
            return None
        status = int(session.get_playback_info().playback_status)
        state = {4: "playing", 5: "paused"}.get(status, "stopped")
        position, duration, at = self._timeline(session, state)
        cover = None
        key = (props.title, props.artist or "")
        if want_cover and props.thumbnail is not None:
            now = time.monotonic()
            due = (
                key != self._cover_key  # a new track
                or news  # or the player says the picture changed
                or (self._cover is None and now - self._cover_asked >= COVER_RETRY_SECONDS)
            )
            if due:
                self._cover_key, self._cover_asked = key, now
                self._cover = await self._read_cover(props.thumbnail)
            cover = self._cover
        elif not want_cover:
            self._cover_key = None  # nobody shows it: read it again when somebody does
        return Track(
            state, props.title, props.artist or "", props.album_title or "",
            position, duration, cover, _app_name(app_id or ""), at, app_id or "",
        )  # fmt: skip

    @staticmethod
    def _timeline(session: Any, state: str) -> tuple[float | None, float | None, float]:
        """(position, length, the time of the position) of the session's timeline."""
        at = time.monotonic()
        try:
            timeline = session.get_timeline_properties()
            start = timeline.start_time.total_seconds()
            duration = timeline.end_time.total_seconds() - start
            position = max(0.0, timeline.position.total_seconds() - start)
        except Exception:  # noqa: BLE001 - a player without a timeline (a live stream)
            return None, None, at
        if duration <= 0:
            return None, None, at
        try:  # the position is as of its last update
            from datetime import UTC, datetime

            updated = timeline.last_updated_time
            if updated.year > 2000 and state == "playing":
                at -= max(0.0, (datetime.now(UTC) - updated).total_seconds())
        except (AttributeError, TypeError, ValueError, OverflowError):
            pass
        return min(position, duration), duration, at

    # -- listening ------------------------------------------------------------

    def _follow(self, session: Any, app_id: str | None) -> None:
        """Listen to the session that is current now, and to no other. Another player
        is another session; the same player after a restart is a new session object, which
        ``current_session_changed`` tells."""
        if app_id == self._session_id and not self._new_session:
            return
        self._new_session = False
        self._unsubscribe(self._session_events)
        self._session_id = app_id
        self._session_listens = session is None or self._subscribe(
            session, self.SESSION_EVENTS, self._session_events
        )

    def _subscribe(self, target: Any, names: tuple[str, ...], into: list) -> bool:
        """Listen to the events ``names`` of ``target``; False if one cannot be had (the
        safety poll then looks more often)."""
        everything = True
        for name in names:

            def handler(*_: Any, name: str = name) -> None:
                self._noticed(name)

            try:
                token = getattr(target, "add_" + name)(handler)
            except Exception:  # noqa: BLE001 - an older Windows
                everything = False
                continue
            # the handler stays here: Windows calls it from threads of its own
            into.append((target, name, token, handler))
        return everything

    @staticmethod
    def _unsubscribe(listening: list) -> None:
        while listening:
            target, name, token, _handler = listening.pop()
            try:
                getattr(target, "remove_" + name)(token)
            except Exception:  # noqa: BLE001, S110 - the session may be gone already
                pass

    def _noticed(self, name: str) -> None:
        """News from Windows, in a thread of Windows: note what it is, wake the reader."""
        self._news.add(name)
        if self._noticed_at is None:
            self._noticed_at = time.monotonic()
        if name == "media_properties_changed":
            self._new_properties = True
        elif name == "current_session_changed":
            self._new_session = True
        self._wake()

    async def _read_cover(self, reference: Any) -> bytes | None:
        try:
            stream = await reference.open_read_async()
            size = min(int(stream.size), MAX_COVER)
            buffer = self._w.Buffer(size)
            options = self._w.InputStreamOptions.READ_AHEAD
            filled = await stream.read_async(buffer, size, options)
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
        player.capitalize(), at, player.lower(),
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


def _backend() -> Any:
    if sys.platform == "win32":
        return _WindowsBackend()
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
    """media.* readings, the ``media.cover`` picture and the player's ``media.app_icon``,
    while a theme or a service reads them."""

    name = "media"
    # news shows in the next frame (a new song, play or pause), and the position moves
    # on at each frame's time: reading is a copy of the track the thread keeps
    every_frame = True

    def __init__(self, options: dict[str, Any] | None = None, *, icons: Any = None) -> None:
        super().__init__(options)
        self._track: Track | None = None
        self._cover: tuple[Any, Image.Image | None] = (None, None)
        self._icons = icons  # an appicon.AppIcons; made when first needed
        self._icon: Image.Image | None = None
        self._thread: threading.Thread | None = None
        self._poke: Callable[[], None] | None = None  # wakes the thread, from any thread
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._warned = False

    def _start(self) -> None:
        with self._lock:
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
        """Look when the player has news (a backend that has events), and every
        ``interval`` otherwise. After news, once more a moment later (``SETTLE_SECONDS``):
        the cover and the position come in after the title."""
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()

        def poke() -> None:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:  # the loop is closed: nothing to wake
                pass

        self._poke = poke
        if hasattr(backend, "bind"):
            backend.bind(poke)
        idle_since: float | None = None
        news = False
        try:
            while not self._stop.is_set():
                wake.clear()  # before looking: news that comes meanwhile brings the next look
                now = time.monotonic()
                if wanted("media."):
                    idle_since = None
                    failed = await self._look(backend)
                    await asyncio.sleep(MIN_GAP_SECONDS)
                    settle = news and not getattr(backend, "timeline_only", False)
                    pause = SETTLE_SECONDS if settle else getattr(backend, "interval", POLL_SECONDS)
                    if failed:  # the backend starts anew and has no events yet: look soon
                        pause = min(pause, POLL_SECONDS)
                else:
                    idle_since = now if idle_since is None else idle_since
                    if now - idle_since > IDLE_SECONDS:
                        # nobody looked for a minute: end, start again on demand
                        with self._lock:
                            self._track, self._icon = None, None
                        return
                    pause = IDLE_CHECK_SECONDS
                try:
                    await asyncio.wait_for(wake.wait(), pause)
                    news = True
                except TimeoutError:
                    news = False
        finally:
            self._poke = None
            if hasattr(backend, "close"):
                try:
                    backend.close()
                except Exception:  # noqa: BLE001 - nothing left to do about it
                    log.debug("media: closing failed", exc_info=True)

    async def _look(self, backend: Any) -> bool:
        """Ask the backend and keep the answer; True if the backend failed."""
        failed = False
        try:
            track = await backend.poll(wanted("media.cover"))
        except Exception as exc:  # noqa: BLE001 - a player must not stop the panel
            log.debug("media: %s", exc)
            track, failed = None, True
        cover = track.cover if track else None
        if cover is not None and cover != self._cover[0]:
            self._cover = (cover, load_cover(cover))
        icon = self._icon_of(track)
        with self._lock:
            before, self._track, self._icon = self._track, track, icon
        if log.isEnabledFor(logging.DEBUG) and track is not before:
            self._tell_change(backend, before, track)
        return failed

    @staticmethod
    def _tell_change(backend: Any, before: Track | None, track: Track | None) -> None:
        """One line a change (with --verbose), as SPUR II logged them: what it is, what
        brought it (the event, or the safety look) and how long after the event it was seen."""
        names, at = getattr(backend, "trigger", ((), None))
        if before is None or track is None:
            what = "a player" if track is not None else "nothing plays"
        elif (track.title, track.artist) != (before.title, before.artist):
            what = "a new track"
        elif track.state != before.state:
            what = track.state
        else:
            what = "a seek" if track.position != before.position else "the track's details"
        after = f", {(time.monotonic() - at) * 1000:.0f} ms after it" if at is not None else ""
        log.debug("media: %s, by %s%s", what, " + ".join(names) or "the safety look", after)

    def _icon_of(self, track: Track | None) -> Image.Image | None:
        """The icon of the player, if somebody reads it; found once per player."""
        if track is None or not track.app_id or not wanted("media.app_icon"):
            return None
        if self._icons is None:
            from libre_panel.sensors import appicon

            self._icons = appicon.AppIcons(appicon.finder())
        return self._icons.get(track.app_id)

    def read(self) -> dict[str, Reading]:
        if self._stop.is_set() or not wanted("media."):
            return {}
        self._start()
        with self._lock:
            track = self._track
        return track.readings(time.monotonic()) if track else {}

    def images(self) -> dict[str, Any]:
        with self._lock:
            track, icon = self._track, self._icon
        found: dict[str, Any] = {}
        cover = self._cover
        if track is not None and track.cover is not None and track.cover == cover[0]:
            if cover[1] is not None:
                found["media.cover"] = cover[1]
        if icon is not None:
            found["media.app_icon"] = icon
        return found

    def close(self) -> None:
        self._stop.set()
        poke = self._poke
        if poke is not None:
            poke()  # the thread ends at once, and lets go of Windows' events
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(1.0)
