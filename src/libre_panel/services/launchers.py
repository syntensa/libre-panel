"""The games of other launchers than Steam, and the games in folders you name. Only reads.

Every launcher keeps a list of what it has installed. On Windows: Epic its manifests (in
ProgramData), GOG, EA app, Battle.net and Rockstar the list of installed programs (Settings →
Apps), Ubisoft Connect a key of its own in the registry, Amazon Games a database, and the Xbox
app the folder XboxGames on its drives. On Linux (and Windows): Heroic (Epic, GOG and Amazon)
its lists, and on Linux Lutris its database. A program that runs from such a game's folder is
that game, as with Steam (services/steam.py). Nothing here starts a launcher or talks to the
network.

The formats are those that Playnite's library extensions (MIT), Heroic and Lutris read.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from libre_panel.services.steam import tidy

log = logging.getLogger(__name__)

REFRESH_S = 120.0  # how often the installed games are read again: a new one is known by itself
UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
UBISOFT = r"SOFTWARE\Ubisoft\Launcher\Installs"
GOG_KEY = re.compile(r"^(\d+)_is1$")
BATTLE_NET_UID = re.compile(r"Battle\.net.*--uid=(.*?)\s", re.IGNORECASE)
ROCKSTAR_TITLE = re.compile(r"(?:Launcher|uninstall)\.exe.+uninstall=(.+)$", re.IGNORECASE)
# The games of the Rockstar Games Launcher (its title ids): its own entries are no games
ROCKSTAR_TITLES = frozenset(("gta5", "gta5_gen9", "rdr", "rdr2", "lanoire", "mp3", "lanoirevr",
                             "gtasa", "gta3", "gtavc", "bully", "gta4", "gta3unreal",
                             "gtavcunreal", "gtasaunreal"))  # fmt: skip
# What the EA app installs beside the games, by its name (in lower case)
EA_TOOLS = ("ea app", "ea desktop", "origin", "anticheat", "anti-cheat")
# Folders too wide to be a game's: a program anywhere in them would be "the game"
TOO_WIDE = frozenset(("program files", "program files (x86)", "programdata", "windows", "users",
                      "home", "usr", "opt", "games"))  # fmt: skip
# Files that make a folder a game's own (else its folders are the games)
PROGRAMS = (".exe", ".x86_64", ".x86", ".appimage", ".sh")


@dataclass(frozen=True)
class InstalledGame:
    source: str  # "epic", "gog", "ubisoft", "ea", "battlenet", "rockstar", "amazon", "xbox",
    #              "lutris" or "folder" (one you named)
    key: str  # the launcher's id for it (or its folder): one key, one game
    name: str
    folder: Path


@dataclass(frozen=True)
class Entry:
    """A program in Windows' list of installed programs."""

    key: str  # the name of its key in the registry
    name: str  # DisplayName
    publisher: str
    location: str  # InstallLocation
    uninstall: str  # UninstallString


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _rows(database: Path, query: str) -> list[tuple]:
    """A query on a launcher's database, opened to read only (it may be in use)."""
    if not database.is_file():
        return []
    try:
        connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=1.0)
        try:
            return connection.execute(query).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        log.debug("could not read %s: %s", database, exc)
        return []


def _game(source: str, key: Any, name: Any, folder: Any) -> InstalledGame | None:
    if not folder:
        return None
    where = Path(str(folder).strip().strip('"'))
    title = tidy(str(name or "")) or where.name
    return InstalledGame(source, str(key or where).lower(), title, where)


# -- Windows: the list of installed programs and the registry ------------------------------------


def _value(winreg: Any, key: Any, name: str) -> str:
    try:
        return str(winreg.QueryValueEx(key, name)[0] or "")
    except OSError:
        return ""


def _subkeys(winreg: Any, key: Any) -> Iterable[str]:
    index = 0
    while True:
        try:
            yield winreg.EnumKey(key, index)
        except OSError:
            return
        index += 1


def _views(winreg: Any) -> tuple[tuple[Any, int], ...]:
    """The places the lists are in: the machine's in its 64- and 32-bit views, the user's."""
    read = getattr(winreg, "KEY_READ", 0x20019)
    machine = winreg.HKEY_LOCAL_MACHINE
    return (
        (machine, read | getattr(winreg, "KEY_WOW64_64KEY", 0x100)),
        (machine, read | getattr(winreg, "KEY_WOW64_32KEY", 0x200)),
        (winreg.HKEY_CURRENT_USER, read),
    )


def uninstall_entries(winreg: Any) -> list[Entry]:
    """Every program in Windows' list of installed programs, once."""
    entries: dict[tuple[str, str], Entry] = {}
    for hive, access in _views(winreg):
        try:
            programs = winreg.OpenKey(hive, UNINSTALL, 0, access)
        except OSError:
            continue
        with programs:
            for name in list(_subkeys(winreg, programs)):
                try:
                    with winreg.OpenKey(programs, name) as item:
                        found = Entry(
                            name,
                            *(_value(winreg, item, value) for value in (
                                "DisplayName", "Publisher", "InstallLocation", "UninstallString"
                            )),
                        )  # fmt: skip
                except OSError:
                    continue
                entries.setdefault((found.key, found.location.lower()), found)
    return list(entries.values())


def gog(entries: Iterable[Entry]) -> list[InstalledGame]:
    """GOG's games (installed with GOG Galaxy or GOG's own installers), without their DLC."""
    games = []
    for entry in entries:
        number = GOG_KEY.match(entry.key)
        if not number or entry.publisher != "GOG.com" or not entry.location:
            continue
        info = _json(Path(entry.location) / f"goggame-{number[1]}.info")
        if isinstance(info, dict) and info.get("rootGameId") not in (None, "", number[1]):
            continue  # a DLC: it lies in its game's folder
        games.append(_game("gog", number[1], entry.name, entry.location))
    return [game for game in games if game]


def ea(entries: Iterable[Entry]) -> list[InstalledGame]:
    """The EA app's games: what Electronic Arts installed, without the app and its tools."""
    games = []
    for entry in entries:
        if entry.publisher != "Electronic Arts" or not entry.location:
            continue
        if any(tool in entry.name.lower() for tool in EA_TOOLS):
            continue
        games.append(_game("ea", entry.key, entry.name, entry.location))
    return [game for game in games if game]


def battle_net(entries: Iterable[Entry]) -> list[InstalledGame]:
    """Battle.net's games: the programs it uninstalls by their id, without its own."""
    games = []
    for entry in entries:
        uid = BATTLE_NET_UID.search(entry.uninstall + " ")
        if not uid or not entry.location or uid[1].lower().startswith("battle.net"):
            continue
        if entry.name.endswith(("Test", "Beta")):
            continue
        games.append(_game("battlenet", uid[1], entry.name, entry.location))
    return [game for game in games if game]


def rockstar(entries: Iterable[Entry]) -> list[InstalledGame]:
    """The Rockstar Games Launcher's games, by their title ids."""
    games = []
    for entry in entries:
        title = ROCKSTAR_TITLE.search(entry.uninstall)
        if not title or not entry.location:
            continue
        title_id = title[1].strip().strip('"').lower()
        if title_id in ROCKSTAR_TITLES:
            games.append(_game("rockstar", title_id, entry.name, entry.location))
    return [game for game in games if game]


def ubisoft(winreg: Any, entries: Iterable[Entry]) -> list[InstalledGame]:
    """Ubisoft Connect's games: its own key, the names from the list of installed programs."""
    names = {entry.key: entry.name for entry in entries if entry.key.startswith("Uplay Install ")}
    games: dict[str, InstalledGame] = {}
    for hive, access in _views(winreg)[:2]:
        try:
            installs = winreg.OpenKey(hive, UBISOFT, 0, access)
        except OSError:
            continue
        with installs:
            for number in list(_subkeys(winreg, installs)):
                try:
                    with winreg.OpenKey(installs, number) as item:
                        folder = _value(winreg, item, "InstallDir").replace("/", os.sep)
                except OSError:
                    continue
                name = names.get(f"Uplay Install {number}") or Path(folder.rstrip(os.sep)).name
                game = _game("ubisoft", number, name, folder.rstrip(os.sep))
                if game:
                    games.setdefault(game.key, game)
    return list(games.values())


def epic(programdata: Path) -> list[InstalledGame]:
    """The Epic Games Launcher's games, from its manifests, without DLC and Unreal plugins."""
    epic = programdata / "Epic"
    listed = _json(epic / "UnrealEngineLauncher" / "LauncherInstalled.dat")
    where = {
        str(app.get("AppName", "")).lower(): app.get("InstallLocation") or ""
        for app in (listed or {}).get("InstallationList", []) if isinstance(app, dict)
    }  # fmt: skip
    try:
        manifests = sorted((epic / "EpicGamesLauncher" / "Data" / "Manifests").glob("*.item"))
    except OSError:
        return []
    games = []
    for path in manifests:
        manifest = _json(path)
        if not isinstance(manifest, dict):
            continue
        kinds = manifest.get("AppCategories") or []
        if "addons" in kinds and "addons/launchable" not in kinds:
            continue  # a DLC
        if ({"plugins", "plugins/engine"} & set(kinds)
                or any(str(app).startswith("UE_") for app in manifest.get("CompatibleApps") or ())
                or "plugins/engine" in str(manifest.get("TechnicalType") or "")):  # fmt: skip
            continue  # a plugin for Unreal Engine
        app = str(manifest.get("AppName") or "")
        folder = manifest.get("InstallLocation") or ""
        if not folder or not Path(folder).is_dir():
            folder = where.get(app.lower()) or folder
        games.append(_game("epic", app or folder, manifest.get("DisplayName"), folder))
    return [game for game in games if game]


def amazon(local_appdata: Path) -> list[InstalledGame]:
    """Amazon Games' games, from its database of what it installed."""
    database = local_appdata / "Amazon Games" / "Data" / "Games" / "Sql" / "GameInstallInfo.sqlite"
    rows = _rows(
        database, "SELECT Id, ProductTitle, InstallDirectory FROM DbSet WHERE Installed = 1"
    )
    return [game for game in (_game("amazon", *row) for row in rows) if game]


def xbox(drives: Iterable[Path]) -> list[InstalledGame]:
    """The Xbox app's games: a folder each in XboxGames, where it installs them on a drive."""
    games = []
    for drive in drives:
        root = drive / "XboxGames"
        try:
            folders = sorted(path for path in root.iterdir() if path.is_dir())
        except OSError:
            continue
        games += [_game("xbox", folder.name, folder.name, folder) for folder in folders
                  if not folder.name.startswith(".")]  # fmt: skip
    return [game for game in games if game]


# -- Heroic and Lutris -------------------------------------------------------------------------


def heroic(roots: Iterable[Path]) -> list[InstalledGame]:
    """Heroic's games: Epic's (through legendary), GOG's and Amazon's (through nile)."""
    games: list[InstalledGame | None] = []
    for root in roots:
        legendary = _json(root / "legendaryConfig" / "legendary" / "installed.json")
        for app, info in legendary.items() if isinstance(legendary, dict) else ():
            if isinstance(info, dict) and not info.get("is_dlc"):
                games.append(_game("epic", app, info.get("title"), info.get("install_path")))
        installed = _json(root / "gog_store" / "installed.json")
        for info in (installed or {}).get("installed", []) if isinstance(installed, dict) else ():
            if isinstance(info, dict) and not info.get("is_dlc"):
                folder = info.get("install_path")
                games.append(_game("gog", info.get("appName") or folder, "", folder))
        nile = _json(root / "nile_config" / "nile" / "installed.json")
        for info in nile if isinstance(nile, list) else ():
            if isinstance(info, dict):
                games.append(
                    _game("amazon", info.get("id") or info.get("path"), "", info.get("path"))
                )
    return [game for game in games if game]


def lutris(roots: Iterable[Path]) -> list[InstalledGame]:
    """Lutris's installed games (its Steam games are Steam's)."""
    games = []
    for root in roots:
        query = "SELECT slug, name, directory, runner FROM games WHERE installed = 1"
        for slug, name, directory, runner in _rows(root / "pga.db", query):
            if directory and runner != "steam":
                games.append(_game("lutris", slug or directory, name, directory))
    return [game for game in games if game]


# -- folders you name ----------------------------------------------------------------------------


def _holds_programs(folder: Path) -> bool:
    try:
        return any(path.is_file() and path.suffix.lower() in PROGRAMS for path in folder.iterdir())
    except OSError:
        return False


def folders(paths: Iterable[Any]) -> list[InstalledGame]:
    """The games in the folders you name: a folder with programs in it is a game of its own, any
    other holds a game in each of its folders."""
    games = []
    for raw in paths:
        text = str(raw).strip().strip('"')
        if not text:
            continue
        folder = Path(os.path.expandvars(os.path.expanduser(text)))
        if not folder.is_dir():
            log.info("game folder %s is not there", folder)
            continue
        if _holds_programs(folder):
            games.append(_game("folder", folder, folder.name, folder))
            continue
        try:
            inside = sorted(path for path in folder.iterdir() if path.is_dir())
        except OSError:
            continue
        games += [_game("folder", path, path.name, path) for path in inside
                  if not path.name.startswith(".")]  # fmt: skip
    return [game for game in games if game]


# -- where each list lies ---------------------------------------------------------------------


def _drives() -> list[Path]:
    """The computer's own drives (not CD drives nor network shares)."""
    try:
        import psutil

        return [Path(part.mountpoint) for part in psutil.disk_partitions(all=False)
                if "cdrom" not in part.opts and "remote" not in part.opts]  # fmt: skip
    except Exception:  # noqa: BLE001 - without them, no Xbox games
        return []


def _heroic_roots(platform: str, home: Path) -> list[Path]:
    if platform == "win32":
        return [Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "heroic"]
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return [config / "heroic", home / ".var/app/com.heroicgameslauncher.hgl/config/heroic"]


def _lutris_roots(home: Path) -> list[Path]:
    data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return [data / "lutris", home / ".var/app/net.lutris.Lutris/data/lutris"]


def launcher_games(platform: str | None = None, home: Path | None = None,
                   winreg: Any = None) -> list[InstalledGame]:  # fmt: skip
    """The games of every launcher found on this computer."""
    platform = platform or sys.platform
    home = home or Path.home()
    games: list[InstalledGame] = []
    if platform == "win32":
        if winreg is None:
            try:
                import winreg
            except ImportError:
                winreg = None
        programdata = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        games += epic(programdata)
        if winreg is not None:
            entries = uninstall_entries(winreg)
            games += gog(entries) + ubisoft(winreg, entries) + ea(entries)
            games += battle_net(entries) + rockstar(entries)
        games += amazon(local) + xbox(_drives())
    games += heroic(_heroic_roots(platform, home))
    if platform != "win32":
        games += lutris(_lutris_roots(home))
    return games


def _too_wide(folder: Path, home: Path) -> bool:
    """A folder in which "a program of the game" would be half the computer's programs."""
    parts = [part for part in folder.parts if part not in (folder.anchor, os.sep)]
    if not parts or folder == home:
        return True
    return len(parts) == 1 and parts[0].lower() in TOO_WIDE


class Catalog:
    """The games of the other launchers and of the folders you name, read again every
    ``REFRESH_S``. ``ignore`` names (case does not matter) games and tools that are never "the
    game", as for Steam."""

    def __init__(
        self,
        named: Iterable[Any] = (),
        ignore: Iterable[Any] = (),
        launchers: bool = True,
        clock: Callable[[], float] = time.monotonic,
        read: Callable[[], list[InstalledGame]] | None = None,
        home: Path | None = None,
    ) -> None:
        self.named = [str(path) for path in named if str(path).strip()]
        self.ignore = {str(name).strip().lower() for name in ignore if str(name).strip()}
        self.launchers = launchers
        self.clock = clock
        self.read = read or launcher_games
        self.home = home or Path.home()
        self._read_at = -REFRESH_S
        self._games: dict[str, InstalledGame] = {}  # by folder, normalised

    @property
    def wanted(self) -> bool:
        return self.launchers or bool(self.named)

    def games(self) -> list[InstalledGame]:
        self._refresh()
        return list(self._games.values())

    def match(self, path: str) -> InstalledGame | None:
        """The game a program at ``path`` (or working in that folder) belongs to: the one with
        the innermost folder."""
        self._refresh()
        if not path:
            return None
        where = os.path.normcase(os.path.normpath(path))
        best: tuple[int, InstalledGame] | None = None
        for folder, game in self._games.items():
            if (where == folder or where.startswith(folder + os.sep)) and (
                best is None or len(folder) > best[0]
            ):
                best = (len(folder), game)
        return best[1] if best else None

    def _refresh(self) -> None:
        now = self.clock()
        if now - self._read_at < REFRESH_S:
            return
        self._read_at = now
        found: list[InstalledGame] = []
        if self.launchers:
            try:
                found += self.read()
            except Exception:  # noqa: BLE001 - a launcher's odd list must not stop game mode
                log.exception("could not read the launchers' games")
        found += folders(self.named)  # (after the launchers': theirs have the better names)
        games: dict[str, InstalledGame] = {}
        for game in found:
            names = {game.name.lower(), game.folder.name.lower()}
            if self.ignore & names or _too_wide(game.folder, self.home):
                continue
            games.setdefault(os.path.normcase(os.path.normpath(game.folder)), game)
        self._games = games
