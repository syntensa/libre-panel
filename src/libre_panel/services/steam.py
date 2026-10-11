"""Where Steam is, which games it has installed, and their logos. Only reads.

Steam keeps its libraries in ``steamapps/libraryfolders.vdf`` and one
``appmanifest_<id>.acf`` per installed game, and the pictures of every game it
knows in ``appcache/librarycache``. Libre Panel needs the games to tell that a
program that runs is a game (whatever its name), and the logo to show at the
start of game mode. Nothing here talks to Steam or the network.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

log = logging.getLogger(__name__)

REFRESH_S = 120.0  # how often the installed games are read again: a new one is known by itself
# Tools that lie in a library like games (compared with the start of the folder's or the
# game's name).
NOT_GAMES = (
    "steamworks shared",
    "steamworks common redistributables",
    "steamvr",
    "proton",
    "steam linux runtime",
    "steamlinuxruntime",
    "wallpaper_engine",
)
WINDOWS_STEAM_KEYS = (
    ("HKEY_CURRENT_USER", r"Software\Valve\Steam", "SteamPath"),
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\Valve\Steam", "InstallPath"),
)
WINDOWS_STEAM_FOLDERS = ("ProgramFiles(x86)", "ProgramFiles")
# Under the home folder: the usual place, the link Steam makes, the Flatpak and the Snap
LINUX_STEAM_FOLDERS = (
    ".local/share/Steam",
    ".steam/steam",
    ".steam/root",
    ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    "snap/steam/common/.local/share/Steam",
)

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|//[^\n]*')
_ESCAPE = re.compile(r"\\(.)")


def parse_vdf(text: str) -> dict[str, Any]:
    """Steam's text format as nested dicts, keys in lower case (Steam does not care
    about their case). Text that is not quite right gives what could be read."""
    root: dict[str, Any] = {}
    stack = [root]
    key: str | None = None
    for match in _TOKEN.finditer(text):
        string, brace = match.groups()
        if string is not None:
            value = _ESCAPE.sub(lambda m: {"n": "\n", "t": "\t"}.get(m[1], m[1]), string)
            if key is None:
                key = value.lower()
            else:
                stack[-1][key] = value
                key = None
        elif brace == "{" and key is not None:
            child: dict[str, Any] = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}" and len(stack) > 1:
            stack.pop()
            key = None
    return root


def read_vdf(path: Path) -> dict[str, Any]:
    try:
        return parse_vdf(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}


def find_root(
    given: str = "", platform: str | None = None, home: Path | None = None, winreg: Any = None
) -> Path | None:
    """Steam's folder: the one in ``given``, else where the system or the usual
    places say it is."""
    platform = platform or sys.platform
    home = home or Path.home()
    if given:
        return Path(given) if (Path(given) / "steamapps").is_dir() else None
    candidates: list[Path] = []
    if platform == "win32":
        candidates += _windows_registry_paths(winreg)
        for name in WINDOWS_STEAM_FOLDERS:
            if os.environ.get(name):
                candidates.append(Path(os.environ[name]) / "Steam")
    elif platform == "darwin":
        candidates.append(home / "Library" / "Application Support" / "Steam")
    else:
        candidates += [home / folder for folder in LINUX_STEAM_FOLDERS]
    return next((path for path in candidates if (path / "steamapps").is_dir()), None)


def _windows_registry_paths(winreg: Any = None) -> list[Path]:
    if winreg is None:
        try:
            import winreg
        except ImportError:
            return []
    paths = []
    for hive, subkey, name in WINDOWS_STEAM_KEYS:
        try:
            with winreg.OpenKey(getattr(winreg, hive), subkey) as key:
                paths.append(Path(winreg.QueryValueEx(key, name)[0]))
        except OSError:
            continue
    return paths


def libraries(root: Path) -> list[Path]:
    """Every library folder: Steam's own and those ``libraryfolders.vdf`` names."""
    found = [root]
    listed = read_vdf(root / "steamapps" / "libraryfolders.vdf").get("libraryfolders", {})
    for number, entry in listed.items() if isinstance(listed, dict) else ():
        # newer Steam: a table with a "path"; older: the path itself, numbered
        if isinstance(entry, dict):
            path = entry.get("path")
        else:
            path = entry if number.isdigit() else None
        library = Path(path) if isinstance(path, str) and path else None
        if library and library.is_absolute() and library.is_dir():
            if all(not _same(library, other) for other in found):
                found.append(library)
    return found


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def tidy(name: str) -> str:
    """A game's name without trademark signs and double spaces: on a panel it stands large."""
    for sign in ("™", "®", "©"):
        name = name.replace(sign, "")
    return " ".join(name.split())


@dataclass(frozen=True)
class SteamGame:
    appid: str
    name: str
    folder: Path  # <library>/steamapps/common/<installdir>


def installed_games(library: Path) -> list[SteamGame]:
    """The games installed in one library, from their manifests."""
    steamapps = library / "steamapps"
    try:
        manifests = sorted(steamapps.glob("appmanifest_*.acf"))
    except OSError:
        return []
    games = []
    for manifest in manifests:
        state = read_vdf(manifest).get("appstate", {})
        if not isinstance(state, dict):
            continue
        appid, name, installdir = (state.get(k) for k in ("appid", "name", "installdir"))
        if appid and installdir:
            folder = steamapps / "common" / installdir
            games.append(SteamGame(str(appid), tidy(name or installdir), folder))
    return games


class Library:
    """The games installed through Steam, read again every ``REFRESH_S``.

    ``given`` is Steam's folder when it is not where Libre Panel looks; ``ignore``
    names (case does not matter) games and tools that are never "the game".
    """

    def __init__(
        self,
        given: str = "",
        ignore: list[str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.given = given
        self.ignore = {str(name).strip().lower() for name in ignore or () if str(name).strip()}
        self.clock = clock
        self.root: Path | None = None
        self._read_at = -REFRESH_S
        self._warned = False
        self._games: dict[str, SteamGame] = {}  # by folder
        self._commons: list[str] = []

    def games(self) -> list[SteamGame]:
        self._refresh()
        return list(self._games.values())

    def match(self, path: str) -> SteamGame | None:
        """The game a program at ``path`` (or working in that folder) belongs to."""
        self._refresh()
        if not path:
            return None
        where = os.path.normcase(os.path.normpath(path))
        for common in self._commons:
            if where.startswith(common + os.sep):
                installdir = where[len(common) + 1 :].split(os.sep, 1)[0]
                return self._games.get(common + os.sep + installdir)
        return None

    def _refresh(self) -> None:
        now = self.clock()
        if now - self._read_at < REFRESH_S:
            return
        self._read_at = now
        self.root = find_root(self.given)
        if self.root is None and self.given and not self._warned:
            self._warned = True
            log.warning("Steam: %s has no steamapps folder", self.given)
        games, commons = {}, []
        for library in libraries(self.root) if self.root else ():
            commons.append(os.path.normcase(os.path.normpath(library / "steamapps" / "common")))
            for game in installed_games(library):
                if not self._is_game(game):
                    continue
                games[os.path.normcase(os.path.normpath(game.folder))] = game
        self._games, self._commons = games, commons

    def _is_game(self, game: SteamGame) -> bool:
        names = {game.folder.name.lower(), game.name.lower()}
        return not (self.ignore & names or any(name.startswith(NOT_GAMES) for name in names))

    def logo(self, appid: str) -> Image.Image | None:
        """The logo of a game from Steam's cache, cut out and not scaled (the header
        picture when there is no logo), or None."""
        self._refresh()
        if self.root is None:
            return None
        try:
            return load_logo(self.root, appid)
        except OSError as exc:  # a picture that goes while we look: no logo this time
            log.debug("logo of %s not read: %s", appid, exc)
            return None


def cache_files(root: Path, appid: str, names: tuple[str, ...]) -> list[Path]:
    """Cached pictures of a game, newest first. Steam keeps them in a folder per
    game and version (a new season's logo is a new file) or, older, as
    ``<appid>_<name>``."""
    if not appid.isdigit():
        return []
    cache = root / "appcache" / "librarycache"
    found: list[Path] = []
    for name in names:
        found += cache.glob(f"{appid}/*/{name}")
        found += cache.glob(f"{appid}/{name}")
        found += cache.glob(f"{appid}_{name}")
    found = [path for path in found if path.is_file()]
    return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)


def load_logo(root: Path, appid: str) -> Image.Image | None:
    """The newest logo (a PNG with a transparent surrounding) cut to what shows, or
    the newest header picture as it is. Not scaled: a screen scales it where it needs it."""
    for names, cut in ((("logo.png",), True), (("header.jpg", "header.png"), False)):
        for path in cache_files(root, appid, names)[:1]:
            try:
                with Image.open(path) as picture:
                    logo = picture.convert("RGBA")
            except (OSError, ValueError) as exc:
                log.debug("logo %s not read: %s", path, exc)
                continue
            box = logo.getchannel("A").getbbox() if cut else None
            return logo.crop(box) if box else logo
    return None
