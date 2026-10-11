"""Downloads in progress, Steam's and the browser's, as one state for a download card.

Steam: ``logs/content_log.txt`` (what Steam does with an app: its phases and size) and the
app's ``appmanifest_<id>.acf`` (its name and how much is loaded), both only read. Steam's log
names a rate once a minute at best, so the speed is the network's download (``net.down``); when
the manifest stands still although data flows, the loaded amount is extrapolated (``estimated``).

Browsers: the part files (``.crdownload``, ``.part``, ``.partial``) in the user's Downloads
folder; how a file grows gives the amount and the speed. The size of the whole would be in the
browser's history, which is never read (it holds everything one has looked at): a browser
download has no remaining time.

Small is no download: nothing below ``min_mb`` is shown, and a download of unknown size only
after 5 s at more than 1 MB/s. A finished download stays for a few seconds as ``done``. Nothing
is written and nothing is kept: the state lives on the panel only.
"""

from __future__ import annotations

import collections
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from libre_panel.i18n import t
from libre_panel.plugins import Service
from libre_panel.services import steam

log = logging.getLogger(__name__)

KEY = "downloads"  # the state this service publishes
STEP_S = 1.0  # how often it looks
MB = 1024 * 1024
HISTORY_S = 180  # seconds of speed a state carries
DONE_S = 6.0  # a finished download stays this long
PAUSED_AFTER_S = 20.0  # a part file that did not grow this long is paused
STEAM_QUIET_S = 60.0  # Steam downloads, but no data flows and the log says nothing: hide it
STEAM_STALE_S = 30.0  # the log says "running", the manifest "installed": a left-over
TAIL_BYTES = 256 * 1024  # at the start, so much of the end of Steam's log is read
PLACES_S = 300.0  # Steam's libraries and the folder are looked up again after this
UNKNOWN_AFTER_S = 5.0  # a download of unknown size counts after this long ...
UNKNOWN_RATE = 1 * MB  # ... at more than this speed
REMAINING_RATE = 0.2 * MB  # no remaining time at a speed below this
SUFFIXES = (".crdownload", ".part", ".partial")

DOWNLOADING, PREPARING, PAUSED, INSTALLING, DONE = (
    "downloading", "preparing", "paused", "installing", "done",
)  # fmt: skip

_START = re.compile(r"AppID (\d+) update started : download (\d+)/(\d+).*?stage (\d+)/(\d+)")
_CHANGED = re.compile(r"AppID (\d+) App update changed : (.*)$")
_FINISHED = re.compile(r"AppID (\d+) finished update")
# StateFlags of a manifest: 256 an update is running, 512 it is paused, 1024 it was started
STEAM_RUNNING = 256 | 512 | 1024
_MANIFEST_KEYS = ("stateflags", "bytestodownload", "bytesdownloaded")


def _phase(state: str) -> str | None:
    """The phase from Steam's "App update changed : ..."; None: over, "": a transition."""
    state = state.strip()
    if "Downloading" in state:
        return DOWNLOADING
    if "Verifying Staged" in state or "Committing" in state or "Staging" in state:
        return INSTALLING
    if "Preallocating" in state or "Reconfiguring" in state or "Verifying Installed" in state:
        return PREPARING
    if "Paused" in state or "Suspended" in state:
        return PAUSED
    if state in ("None", ""):
        return None
    return ""


# -- where the browser puts what it loads -------------------------------------------------------

# FOLDERID_Downloads: {374DE290-123F-4565-9164-39C4925E467B}
FOLDERID_DOWNLOADS = (0x374DE290, 0x123F, 0x4565, (0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))


def shell_downloads(shell: Any = None, ole: Any = None) -> str | None:
    """The Downloads folder as the Windows shell knows it (the user may have moved it), or
    None. ``shell`` and ``ole`` are shell32 and ole32 (tests bring fakes)."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("a", wintypes.DWORD),
            ("b", wintypes.WORD),
            ("c", wintypes.WORD),
            ("d", ctypes.c_ubyte * 8),
        ]

    if shell is None:
        if not hasattr(ctypes, "WinDLL"):  # not Windows
            return None
        shell = ctypes.WinDLL("shell32")  # type: ignore[attr-defined]
        shell.SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p),
        ]  # fmt: skip
        shell.SHGetKnownFolderPath.restype = ctypes.c_long
    if ole is None:
        ole = ctypes.WinDLL("ole32")  # type: ignore[attr-defined]
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole.CoTaskMemFree.restype = None
    a, b, c, d = FOLDERID_DOWNLOADS
    guid = GUID(a, b, c, (ctypes.c_ubyte * 8)(*d))
    out = ctypes.c_void_p()
    if shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) != 0:
        return None
    if not out.value:
        return None
    try:
        return ctypes.wstring_at(out.value)
    finally:
        ole.CoTaskMemFree(out)


def _normal(path: Path) -> str:
    return os.path.normcase(os.path.normpath(path))


def downloads_folder(
    platform: str | None = None,
    home: Path | None = None,
    run: Callable[..., Any] = subprocess.run,
    known_folder: Callable[[], str | None] = shell_downloads,
) -> Path:
    """The user's Downloads folder: on Windows the shell's known folder, on Linux what
    ``xdg-user-dir DOWNLOAD`` says; failing that ``~/Downloads``. Nothing is created."""
    platform = platform or sys.platform
    home = home or Path.home()
    found: str | None = None
    try:
        if platform == "win32":
            found = known_folder()
        elif platform.startswith("linux"):
            done = run(
                ["xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True, timeout=3, check=False
            )
            found = done.stdout.strip() if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        log.debug("downloads: the Downloads folder is not known to the system: %s", exc)
    if found:
        folder = Path(found)
        # without a setting xdg-user-dir answers with the home folder itself: no folder of downloads
        if folder.is_absolute() and _normal(folder) != _normal(home):
            return folder
    return home / "Downloads"


# -- what is going on ---------------------------------------------------------------------------


@dataclass
class Item:
    """One download as a card shows it."""

    id: str
    source: str  # "steam" or "browser"
    name: str
    phase: str
    loaded: float
    total: float | None
    rate: float
    remaining: float | None
    started: float
    estimated: bool = False
    until: float = 0.0  # a finished one is shown until then


@dataclass
class _App:
    """An app Steam is working on."""

    appid: str
    name: str
    started: float
    phase: str = PREPARING
    total: int = 0
    loaded: float = 0.0  # the manifest's amount, extrapolated with the speed in between
    manifest_loaded: int = 0
    manifest_at: float = -1e9
    log_at: float = 0.0  # when the log last said something about it
    moving_at: float = 0.0  # when data last flowed
    shown: bool = False
    done_until: float = 0.0


@dataclass
class _Part:
    """A part file of a browser."""

    id: str
    name: str
    started: float
    size: int
    at: float  # when the size was read
    moving_at: float
    rate: float = 0.0
    shown: bool = False


class Tracker:
    """Follows Steam's log and manifests and the browsers' part files. ``step(now, rate)``
    is called once a second with the network's download in bytes per second and gives the
    state to publish: the download that matters most, or None."""

    def __init__(
        self,
        *,
        steam_on: bool = True,
        browser: bool = True,
        min_mb: float = 50.0,
        steam_path: str = "",
        folder: str = "",
        find_folder: Callable[[], Path] | None = None,
        report: Callable[[str], None] = lambda text: None,
    ) -> None:
        self.steam_on, self.browser_on = steam_on, browser
        self.min_bytes = max(0.0, float(min_mb)) * MB
        self.steam_path, self.folder_given = steam_path, folder
        self.find_folder = find_folder or downloads_folder
        self.report = report
        self.root: Path | None = None
        self.libraries: list[Path] = []
        self.folder: Path | None = None
        self._places_at = -PLACES_S
        self._warned = False
        self._log_pos: int | None = None
        self._apps: dict[str, _App] = {}
        self._parts: dict[str, _Part] = {}
        self._done: list[Item] = []
        self._serial = 0  # browser downloads are numbered, so that no path is in the state
        self._history: dict[str, collections.deque[float]] = {}
        self._rate = 0.0
        self._at: float | None = None

    # -- one look ----------------------------------------------------------------------------

    def step(self, now: float, rate: float | None) -> dict[str, Any] | None:
        dt = 1.0 if self._at is None else max(0.0, min(5.0, now - self._at))
        self._at = now
        raw = max(0.0, float(rate)) if isinstance(rate, int | float) else 0.0
        self._rate += (raw - self._rate) * min(1.0, dt / 2.0)  # smoothed over about two seconds
        self._look_up(now)
        items: list[Item] = []
        if self.steam_on:
            items += self._steam_step(now, dt)
        if self.browser_on:
            items += self._browser_step(now)
        self._done = [item for item in self._done if item.until > now]
        items += self._done
        for item in items:
            if item.phase != DONE:
                history = self._history.setdefault(item.id, collections.deque(maxlen=HISTORY_S))
                history.append(item.rate)
        active = {item.id for item in items}
        for key in [key for key in self._history if key not in active]:
            del self._history[key]
        if not items:
            return None
        main = max(items, key=lambda i: (i.phase != DONE, i.rate, i.loaded))
        return self._state(main, len(items) - 1, now)

    def _state(self, item: Item, more: int, now: float) -> dict[str, Any]:
        return {
            "id": item.id,
            "source": item.source,
            "name": item.name,
            "phase": item.phase,
            "bytes": round(item.loaded),
            "total": round(item.total) if item.total else None,
            "rate": round(item.rate),
            "remaining_s": None if item.remaining is None else round(item.remaining, 1),
            "elapsed_s": round(now - item.started, 1),
            "estimated": item.estimated,
            "history": [round(v) for v in self._history.get(item.id, ())],
            "more": more,
        }

    def _look_up(self, now: float) -> None:
        """Steam's libraries and the Downloads folder, looked up again now and then."""
        if now - self._places_at < PLACES_S:
            return
        self._places_at = now
        if self.steam_on:
            self.root = steam.find_root(self.steam_path)
            self.libraries = steam.libraries(self.root) if self.root else []
            if self.root is None and self.steam_path and not self._warned:
                self._warned = True
                self.report(f"Steam: {self.steam_path} has no steamapps folder")
        if self.browser_on:
            given = Path(self.folder_given).expanduser() if self.folder_given else None
            self.folder = given or self.find_folder()

    # -- Steam -------------------------------------------------------------------------------

    def _tail(self) -> tuple[list[str], bool]:
        """The lines Steam's log gained since the last look, and whether this was the first
        look (what it holds then is history)."""
        if self.root is None:
            return [], False
        path = self.root / "logs" / "content_log.txt"
        try:
            size = path.stat().st_size
        except OSError:
            return [], False
        first = self._log_pos is None
        if self._log_pos is None:
            self._log_pos = max(0, size - TAIL_BYTES)
        if size < self._log_pos:  # a new log was begun
            self._log_pos = 0
        if size == self._log_pos:
            return [], first
        try:
            with open(path, "rb") as stream:
                stream.seek(self._log_pos)
                raw = stream.read(size - self._log_pos)
        except OSError:  # it went away, or is locked: the next look
            return [], first
        end = raw.rfind(b"\n")
        if end < 0:  # not a whole line yet
            return [], first
        middle = first and self._log_pos > 0  # the first line of a tail may be cut
        self._log_pos += end + 1
        lines = raw[:end].decode("utf-8", "replace").splitlines()
        return (lines[1:] if middle else lines), first

    def _manifest(self, appid: str) -> dict[str, Any]:
        for library in self.libraries:
            path = library / "steamapps" / f"appmanifest_{appid}.acf"
            if path.is_file():
                state = steam.read_vdf(path).get("appstate", {})
                return state if isinstance(state, dict) else {}
        return {}

    def _steam_step(self, now: float, dt: float) -> list[Item]:
        lines, first = self._tail()
        for line in lines:
            self._steam_line(line, now, first)
        items = []
        for appid, app in list(self._apps.items()):
            manifest = self._manifest(appid)
            if manifest.get("name"):
                app.name = steam.tidy(str(manifest["name"]))
            flags, total, loaded = (_whole(manifest.get(key)) for key in _MANIFEST_KEYS)
            if app.phase == DONE:
                if now >= app.done_until:
                    del self._apps[appid]
                    continue
            elif (
                flags and not flags & STEAM_RUNNING and (first or now - app.log_at > STEAM_STALE_S)
            ):
                # the log says "running", the manifest long "installed": what was left from before
                # the look began, or a Steam that ended
                del self._apps[appid]
                continue
            if not app.total and total:
                app.total = total
            if loaded and loaded != app.manifest_loaded:
                app.manifest_loaded, app.manifest_at, app.loaded = loaded, now, float(loaded)
                app.moving_at = now  # data flows, whatever the network reading says
            if app.phase == DOWNLOADING:
                app.loaded += self._rate * dt
                if self._rate >= MB / 2:
                    app.moving_at = now
            known = float(app.total) if app.total else None
            have = min(app.loaded, known) if known else app.loaded
            if app.phase in (INSTALLING, DONE) and known:
                have = known
            rate = self._rate if app.phase == DOWNLOADING else 0.0
            if app.phase == DOWNLOADING and now - max(app.moving_at, app.log_at) > STEAM_QUIET_S:
                continue  # it stands still: not shown
            show = (known or 0) >= self.min_bytes or (
                not known
                and app.phase == DOWNLOADING
                and now - app.started >= UNKNOWN_AFTER_S
                and rate >= UNKNOWN_RATE
            )
            if app.phase == DONE:
                show = app.shown
            if not show:
                continue
            app.shown = True
            remaining = None
            if app.phase == DOWNLOADING and known and rate > REMAINING_RATE:
                remaining = (known - have) / rate
            items.append(
                Item(
                    f"steam:{appid}", "steam", app.name, app.phase, have, known, rate, remaining,
                    app.started, estimated=app.phase == DOWNLOADING and now - app.manifest_at > 10,
                    until=app.done_until,
                )
            )  # fmt: skip
        return items

    def _new_app(self, appid: str, now: float) -> _App:
        return _App(appid, t("App {appid}", appid=appid), now, log_at=now, moving_at=now)

    def _steam_line(self, line: str, now: float, first: bool) -> None:
        found = _START.search(line)
        if found:
            app = self._apps.setdefault(found[1], self._new_app(found[1], now))
            loaded, total, stage = int(found[2]), int(found[3]), int(found[5])
            app.total = total or stage
            app.loaded = float(loaded) if total else 0.0
            app.log_at = now
            return
        found = _FINISHED.search(line)
        if found:
            app = self._apps.get(found[1])
            if app is not None:
                if first or not app.shown:  # history, or never shown: no "done" for it
                    del self._apps[found[1]]
                else:
                    app.phase, app.done_until, app.log_at = DONE, now + DONE_S, now
            return
        found = _CHANGED.search(line)
        if not found:
            return
        appid, phase = found[1], _phase(found[2])
        app = self._apps.get(appid)
        if phase is None:
            if app is not None and app.phase != DONE:
                del self._apps[appid]
            return
        if phase == "":
            if app is not None:
                app.log_at = now
            return
        if app is None:
            app = self._apps[appid] = self._new_app(appid, now)
        if app.phase != DONE:
            app.phase = phase
        app.log_at = now

    # -- the browser -------------------------------------------------------------------------

    def _part_files(self) -> dict[str, tuple[int, str]]:
        """The part files in the folder: path -> (size, name without the suffix)."""
        found: dict[str, tuple[int, str]] = {}
        if self.folder is None:
            return found
        try:
            with os.scandir(self.folder) as entries:
                for entry in entries:
                    lower = entry.name.lower()
                    suffix = next((s for s in SUFFIXES if lower.endswith(s)), None)
                    if suffix is None or not entry.is_file():
                        continue
                    try:
                        # os.stat, not entry.stat(): on Windows scandir gives the size from the
                        # directory entry, which an open file gets only when it is closed
                        size = os.stat(entry.path).st_size
                        found[entry.path] = (size, entry.name[: -len(suffix)] or entry.name)
                    except OSError:
                        continue
        except OSError:
            pass
        return found

    def _browser_step(self, now: float) -> list[Item]:
        seen = self._part_files()
        gone = [self._parts.pop(path) for path in list(self._parts) if path not in seen]
        for path, (size, name) in sorted(seen.items()):  # numbered the same on every system
            part = self._parts.get(path)
            if part is None:
                self._serial += 1
                part = _Part(f"browser:{self._serial}", name, now, size, now, now)
                # Chrome renames "Unconfirmed 123.crdownload" while it loads: the same
                # download, not a new one
                before = next((p for p in gone if p.size <= size), None)
                if before is not None:
                    gone.remove(before)
                    part.id, part.started = before.id, before.started
                    part.rate, part.shown = before.rate, before.shown
                self._parts[path] = part
                continue
            dt = now - part.at
            if dt > 0:
                part.rate += (max(0.0, (size - part.size) / dt) - part.rate) * min(1.0, dt / 2.0)
                if size != part.size:
                    part.moving_at = now
                part.size, part.at = size, now
        for part in gone:  # finished, if the file it was becoming is there now
            if self.folder is None or not part.shown:
                continue
            target = self.folder / part.name
            if target.is_file():
                try:
                    size = target.stat().st_size
                except OSError:
                    size = part.size
                self._done.append(
                    Item(part.id, "browser", part.name, DONE, size, size, 0.0, None, part.started,
                         until=now + DONE_S)
                )  # fmt: skip
        items = []
        for part in self._parts.values():
            paused = now - part.moving_at > PAUSED_AFTER_S
            rate = 0.0 if paused else part.rate
            if not (
                part.shown
                or part.size >= self.min_bytes
                or (now - part.started >= UNKNOWN_AFTER_S and rate >= UNKNOWN_RATE)
            ):
                continue
            part.shown = True
            items.append(
                Item(part.id, "browser", part.name, PAUSED if paused else DOWNLOADING, part.size,
                     None, rate, None, part.started)
            )  # fmt: skip
        return items


def _whole(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# -- the service --------------------------------------------------------------------------------


class Downloads(Service):
    """Shows what is downloading: the state ``downloads`` (see docs/PLUGINS.md) is the one
    that matters most of Steam's and the browser's downloads, or None."""

    name = "downloads"
    api = 1
    options = {
        "steam": ("bool", True),
        "steam_path": ("string", ""),
        "browser": ("bool", True),
        "folder": ("string", ""),
        "min_mb": ("number", 50.0),
    }

    def __init__(
        self,
        host: Any,
        options: dict[str, Any],
        *,
        clock: Callable[[], float] = time.monotonic,
        find_folder: Callable[[], Path] | None = None,
    ) -> None:
        super().__init__(host, options)
        self.clock = clock
        self.tracker = Tracker(
            steam_on=bool(options["steam"]),
            browser=bool(options["browser"]),
            min_mb=float(options["min_mb"]),
            steam_path=str(options["steam_path"]).strip(),
            folder=str(options["folder"]).strip(),
            find_folder=find_folder,
            report=lambda text: host.log.warning("%s", text),
        )
        self._lock = threading.Lock()
        self._ended = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] | None = None
        self._failure: str | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="downloads", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._ended.set()
        if self._thread is not None:
            self._thread.join(3.0)
            self._thread = None

    def _run(self) -> None:
        while not self._ended.wait(STEP_S):
            self.step()

    def step(self) -> None:
        """Look once and publish what is found. A failed look is said once, then it is tried
        again; it never reaches the panel."""
        try:
            state = self.tracker.step(self.clock(), self._rate())
            failure = None
        except Exception as exc:  # one failed look must not end the service
            state, failure = None, f"{type(exc).__name__}: {exc}"
        if failure != self._failure:
            if failure:
                self.host.log.warning("looking at the downloads failed: %s", failure)
            else:
                self.host.log.info("looking at the downloads works again")
            self._failure = failure
        with self._lock:
            if self._ended.is_set() or (state is None and self._state is None):
                return  # a look that was under way when the service stopped publishes nothing
            self._state = state
        self.host.publish_state(KEY, state)

    def _rate(self) -> float | None:
        """The network's download in bytes per second, as the panel last read it."""
        snapshot = self.host.snapshot()
        value = snapshot.value("net.down") if snapshot is not None else None
        return float(value) if isinstance(value, int | float) else None

    def status(self) -> dict[str, Any]:
        """What it looks at, for a console page."""
        with self._lock:
            state = self._state
        return {
            "steam": str(self.tracker.root) if self.tracker.root else None,
            "folder": str(self.tracker.folder) if self.tracker.folder else None,
            "download": state["name"] if state else None,
            "error": self._failure,
        }
