"""The games of other launchers than Steam and of the folders you name (services/launchers.py):
each launcher's own list, made up in a temp dir or a made-up registry, and game mode finding a
game of theirs that runs."""

import json
import sqlite3
from pathlib import Path

from fake_system import FakeMachine, make_steam

from libre_panel.services import launchers
from libre_panel.services.game import Detector, Found
from libre_panel.services.launchers import Catalog, Entry, InstalledGame
from libre_panel.services.steam import Library


class Node:
    """A key of the made-up registry: its subkeys (in the case they were written) and values."""

    def __init__(self):
        self.keys: dict[str, Node] = {}
        self.values: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Registry:
    """``winreg`` over keys added by hand: the machine's 64- and 32-bit views, the user's."""

    HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER = "machine", "user"
    KEY_READ, KEY_WOW64_64KEY, KEY_WOW64_32KEY = 0x20019, 0x100, 0x200

    def __init__(self):
        self.hives = {view: Node() for view in ("64", "32", "user")}

    def add(self, view, path, **values):
        node = self.hives[view]
        for part in path.split("\\"):
            found = next((key for name, key in node.keys.items() if name.lower() == part.lower()),
                         None)  # fmt: skip
            node = found if found is not None else node.keys.setdefault(part, Node())
        node.values.update(values)

    def OpenKey(self, key, sub, reserved=0, access=0x20019):  # noqa: N802 - winreg's names
        if key == self.HKEY_LOCAL_MACHINE:
            node = self.hives["32" if access & self.KEY_WOW64_32KEY else "64"]
        elif key == self.HKEY_CURRENT_USER:
            node = self.hives["user"]
        else:
            node = key
        for part in sub.split("\\"):
            node = next((n for name, n in node.keys.items() if name.lower() == part.lower()), None)
            if node is None:
                raise FileNotFoundError(sub)
        return node

    def EnumKey(self, node, index):  # noqa: N802
        names = list(node.keys)
        if index >= len(names):
            raise OSError("no more keys")
        return names[index]

    def QueryValueEx(self, node, name):  # noqa: N802
        if name not in node.values:
            raise FileNotFoundError(name)
        return node.values[name], 1


def program(registry, view, key, **values):
    registry.add(view, f"{launchers.UNINSTALL}\\{key}", **values)


def folder(path, *programs):
    path.mkdir(parents=True, exist_ok=True)
    for name in programs:
        (path / name).write_bytes(b"MZ")
    return path


def names(games):
    return sorted((game.source, game.key, game.name) for game in games)


# -- Windows: the list of installed programs --------------------------------------------------


def test_every_installed_program_is_read_once_from_each_view(tmp_path):
    registry = Registry()
    program(registry, "64", "1207658924_is1", DisplayName="Game", InstallLocation="C:\\G")
    program(registry, "32", "1207658924_is1", DisplayName="Game", InstallLocation="C:\\G")
    program(registry, "user", "Other", DisplayName="Other")
    entries = launchers.uninstall_entries(registry)
    assert sorted(entry.key for entry in entries) == ["1207658924_is1", "Other"]
    assert entries[0] == Entry("1207658924_is1", "Game", "", "C:\\G", "")


def test_gog_games_without_their_dlc_nor_galaxy_itself(tmp_path):
    witcher = folder(tmp_path / "GOG Games" / "The Witcher 3")
    (witcher / "goggame-1207664663.info").write_text(json.dumps({"rootGameId": "1207664663"}))
    (witcher / "goggame-1207664643.info").write_text(json.dumps({"rootGameId": "1207664663"}))
    entries = [
        Entry("1207664663_is1", "The Witcher® 3: Wild Hunt", "GOG.com", str(witcher), ""),
        Entry("1207664643_is1", "The Witcher 3 - Hearts of Stone", "GOG.com", str(witcher), ""),
        Entry("GOGPACKGALAXY_is1", "GOG GALAXY", "GOG.com", "C:\\GOG Galaxy", ""),
        Entry("1234_is1", "Not by GOG", "Someone", "C:\\x", ""),
    ]
    assert names(launchers.gog(entries)) == [("gog", "1207664663", "The Witcher 3: Wild Hunt")]


def test_ea_games_without_the_app_and_its_tools():
    entries = [
        Entry("{1}", "Battlefield™ 2042", "Electronic Arts", "C:\\EA Games\\Battlefield 2042", ""),
        Entry(
            "{2}", "EA app", "Electronic Arts", "C:\\Program Files\\Electronic Arts\\EA Desktop", ""
        ),
        Entry("{3}", "EA AntiCheat", "Electronic Arts", "C:\\Program Files\\EA\\AC", ""),
        Entry("{4}", "Some Game", "Electronic Arts", "", ""),
    ]
    assert names(launchers.ea(entries)) == [("ea", "{1}", "Battlefield 2042")]


def test_battle_net_games_by_their_id_without_the_app_itself():
    app = '"C:\\Program Files (x86)\\Battle.net\\Battle.net.exe" --uid={} --lang=enUS'
    entries = [
        Entry(
            "Overwatch",
            "Overwatch",
            "Blizzard Entertainment",
            "C:\\Games\\Overwatch",
            app.format("prometheus"),
        ),  # fmt: skip
        Entry(
            "Battle.net",
            "Battle.net",
            "Blizzard Entertainment",
            "C:\\Battle.net",
            app.format("battle.net"),
        ),  # fmt: skip
        Entry(
            "PTR",
            "Overwatch Test",
            "Blizzard Entertainment",
            "C:\\Games\\PTR",
            app.format("prometheus_test"),
        ),  # fmt: skip
    ]
    assert names(launchers.battle_net(entries)) == [("battlenet", "prometheus", "Overwatch")]


def test_rockstar_games_by_their_title_ids():
    def entry(name, uninstall, location="C:\\Games\\X"):
        return Entry(name, name, "Rockstar Games", location, uninstall)

    launcher = '"C:\\Program Files\\Rockstar Games\\Launcher\\Launcher.exe"'
    entries = [
        entry("Grand Theft Auto V", f"{launcher} -enableFullMode -uninstall=gta5"),
        entry(
            "Rockstar Games Launcher",
            '"C:\\Program Files\\Rockstar Games\\Launcher\\uninstall.exe"',
        ),
        entry("Something", f"{launcher} -uninstall=unknown"),
    ]
    assert names(launchers.rockstar(entries)) == [("rockstar", "gta5", "Grand Theft Auto V")]


def test_ubisoft_connect_games_from_its_own_key_with_their_names(tmp_path):
    registry = Registry()
    registry.add("32", f"{launchers.UBISOFT}\\635", InstallDir="C:/Ubisoft Games/Far Cry 5/")
    registry.add("32", f"{launchers.UBISOFT}\\4311", InstallDir="C:/Ubisoft Games/Anno 1800/")
    entries = [Entry("Uplay Install 635", "Far Cry® 5", "Ubisoft", "", "")]
    found = launchers.ubisoft(registry, entries)
    assert names(found) == [("ubisoft", "4311", "Anno 1800"), ("ubisoft", "635", "Far Cry 5")]


# -- Windows: Epic, Amazon, the Xbox app -------------------------------------------------------


def manifest(programdata, name, **fields):
    manifests = programdata / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    (manifests / f"{name}.item").write_text(json.dumps(fields), encoding="utf-8")


def test_epic_games_from_its_manifests_without_dlc_and_plugins(tmp_path):
    data = tmp_path / "ProgramData"
    fortnite = folder(tmp_path / "Epic" / "Fortnite")
    moved = folder(tmp_path / "D" / "Hades")
    manifest(data, "a", AppName="Fortnite", DisplayName="Fortnite",
             AppCategories=["public", "games"], InstallLocation=str(fortnite))  # fmt: skip
    manifest(data, "b", AppName="DLC", DisplayName="A DLC", AppCategories=["addons"],
             InstallLocation=str(fortnite))  # fmt: skip
    manifest(data, "c", AppName="Launchable", DisplayName="Launchable add-on",
             AppCategories=["addons", "addons/launchable"],
             InstallLocation=str(fortnite / "x"))  # fmt: skip
    manifest(data, "d", AppName="Plugin", DisplayName="A plugin", AppCategories=["plugins"],
             InstallLocation=str(tmp_path / "plugin"))  # fmt: skip
    manifest(
        data,
        "e",
        AppName="Min",
        DisplayName="Hades",
        AppCategories=["games"],
        InstallLocation=str(tmp_path / "gone"),
    )  # moved: the launcher's list knows where
    (data / "Epic" / "UnrealEngineLauncher").mkdir(parents=True)
    (data / "Epic" / "UnrealEngineLauncher" / "LauncherInstalled.dat").write_text(json.dumps(
        {"InstallationList": [{"AppName": "Min", "InstallLocation": str(moved)}]}))  # fmt: skip
    (data / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests" / "f.item").write_text("{broken")
    found = {game.key: game for game in launchers.epic(data)}
    assert sorted(found) == ["fortnite", "launchable", "min"]
    assert found["min"].folder == moved and found["fortnite"].name == "Fortnite"


def test_amazon_games_from_its_database(tmp_path):
    local = tmp_path / "Local"
    sql = local / "Amazon Games" / "Data" / "Games" / "Sql"
    sql.mkdir(parents=True)
    with sqlite3.connect(sql / "GameInstallInfo.sqlite") as db:
        db.execute("CREATE TABLE DbSet (Id TEXT, ProductTitle TEXT, InstallDirectory TEXT, "
                   "Installed INTEGER)")  # fmt: skip
        db.execute("INSERT INTO DbSet VALUES ('amzn1', 'Rocket League', 'C:\\Amazon\\RL', 1)")
        db.execute("INSERT INTO DbSet VALUES ('amzn2', 'Gone', 'C:\\Amazon\\Gone', 0)")
    db.close()
    assert names(launchers.amazon(local)) == [("amazon", "amzn1", "Rocket League")]
    assert launchers.amazon(tmp_path / "nowhere") == []


def test_xbox_games_in_xboxgames_on_each_drive(tmp_path):
    folder(tmp_path / "C" / "XboxGames" / "Forza Horizon 5" / "Content")
    folder(tmp_path / "D" / "XboxGames" / "Starfield")
    folder(tmp_path / "D" / "XboxGames" / ".hidden")
    found = launchers.xbox([tmp_path / "C", tmp_path / "D", tmp_path / "E"])
    assert [game.name for game in found] == ["Forza Horizon 5", "Starfield"]


# -- Heroic and Lutris -----------------------------------------------------------------------


def test_heroic_games_of_epic_gog_and_amazon(tmp_path):
    root = tmp_path / "heroic"
    (root / "legendaryConfig" / "legendary").mkdir(parents=True)
    (root / "legendaryConfig" / "legendary" / "installed.json").write_text(json.dumps({
        "Fortnite": {"title": "Fortnite", "install_path": "/games/Fortnite", "is_dlc": False},
        "DLC": {"title": "A DLC", "install_path": "/games/Fortnite", "is_dlc": True},
    }))  # fmt: skip
    (root / "gog_store").mkdir()
    (root / "gog_store" / "installed.json").write_text(json.dumps({"installed": [
        {"appName": "1207664663", "install_path": "/games/The Witcher 3", "is_dlc": False},
    ]}))  # fmt: skip
    (root / "nile_config" / "nile").mkdir(parents=True)
    (root / "nile_config" / "nile" / "installed.json").write_text(json.dumps([
        {"id": "amzn1", "path": "/games/Rocket League", "version": "1"},
    ]))  # fmt: skip
    assert names(launchers.heroic([root, tmp_path / "flatpak"])) == [
        ("amazon", "amzn1", "Rocket League"),
        ("epic", "fortnite", "Fortnite"),
        ("gog", "1207664663", "The Witcher 3"),
    ]


def test_lutris_games_from_its_database_but_its_steam_ones(tmp_path):
    root = tmp_path / "lutris"
    root.mkdir()
    with sqlite3.connect(root / "pga.db") as db:
        db.execute("CREATE TABLE games (id INTEGER, name TEXT, slug TEXT, directory TEXT, "
                   "runner TEXT, installed INTEGER)")  # fmt: skip
        db.executemany("INSERT INTO games VALUES (?, ?, ?, ?, ?, ?)", [
            (1, "Diablo II", "diablo-ii", "/home/u/Games/diablo-ii", "wine", 1),
            (2, "Portal 2", "portal-2", "/home/u/.steam/steamapps/common/Portal 2", "steam", 1),
            (3, "Gone", "gone", "/home/u/Games/gone", "wine", 0),
            (4, "No folder", "nofolder", "", "linux", 1),
        ])  # fmt: skip
    db.close()
    assert names(launchers.lutris([root])) == [("lutris", "diablo-ii", "Diablo II")]


# -- folders you name ------------------------------------------------------------------------


def test_a_named_folder_is_a_game_or_holds_one_in_each_of_its_folders(tmp_path, caplog):
    portable = folder(tmp_path / "Portable" / "Doom", "doom.exe")
    games = tmp_path / "Games"
    folder(games / "Celeste", "Celeste.exe")
    folder(games / "Hollow Knight" / "hollow_knight_Data")
    folder(games / ".cache")
    with caplog.at_level("INFO", logger="libre_panel.services.launchers"):
        found = launchers.folders([str(portable), f'"{games}"', "", str(tmp_path / "nowhere")])
    assert [(game.name, game.folder) for game in found] == [
        ("Doom", portable),
        ("Celeste", games / "Celeste"),
        ("Hollow Knight", games / "Hollow Knight"),
    ]  # fmt: skip
    assert "nowhere" in caplog.text


# -- the catalog, and game mode with it ------------------------------------------------------


def installed(*games):
    return lambda: [
        InstalledGame(source, key, name, Path(path)) for source, key, name, path in games
    ]


def test_a_program_belongs_to_the_game_of_its_innermost_folder(tmp_path):
    catalog = Catalog(read=installed(
        ("epic", "outer", "Outer", tmp_path / "Games" / "Outer"),
        ("epic", "inner", "Inner", tmp_path / "Games" / "Outer" / "Inner"),
    ))  # fmt: skip
    assert (
        catalog.match(str(tmp_path / "Games" / "Outer" / "Inner" / "bin" / "x.exe")).key == "inner"
    )
    assert catalog.match(str(tmp_path / "Games" / "Outer" / "y.exe")).key == "outer"
    assert catalog.match(str(tmp_path / "Games" / "Outer")).key == "outer"  # (Wine: its folder)
    assert catalog.match(str(tmp_path / "Games" / "OuterSpace" / "z.exe")) is None
    assert catalog.match("") is None


def test_folders_too_wide_ignored_names_and_the_launchers_turned_off(tmp_path):
    home = tmp_path / "home" / "me"
    catalog = Catalog(
        named=[str(folder(tmp_path / "Mine" / "Doom", "doom.exe"))],
        ignore=["Some Tool"],
        home=home,
        read=installed(
            ("ea", "{1}", "Everything", tmp_path.anchor),
            ("ea", "{2}", "Programs", Path(tmp_path.anchor) / "Program Files"),
            ("ea", "{3}", "Home", home),
            ("epic", "tool", "Some Tool", tmp_path / "Tool"),
            ("epic", "game", "A Game", tmp_path / "Game"),
        ),
    )
    assert [game.name for game in catalog.games()] == ["A Game", "Doom"]
    named_only = Catalog(named=[str(tmp_path / "Mine")], launchers=False, read=installed(
        ("epic", "game", "A Game", tmp_path / "Game")))  # fmt: skip
    assert [game.name for game in named_only.games()] == ["Doom"]
    assert not Catalog(launchers=False).wanted


def test_the_list_is_read_again_after_a_while_and_a_broken_one_stops_nothing(tmp_path, caplog):
    now, reads = [0.0], []

    def read():
        reads.append(now[0])
        if len(reads) == 2:
            raise ValueError("odd list")
        return [InstalledGame("epic", "a", "A", tmp_path / "A")]

    catalog = Catalog(read=read, clock=lambda: now[0])
    assert len(catalog.games()) == 1 and len(catalog.games()) == 1 and reads == [0.0]
    now[0] = launchers.REFRESH_S + 1
    with caplog.at_level("ERROR", logger="libre_panel.services.launchers"):
        assert catalog.games() == []  # (the next read brings them back)
    assert "launchers' games" in caplog.text


def test_game_mode_finds_another_launchers_game_once_its_window_has_been_in_front(tmp_path):
    steam = make_steam(tmp_path / "Steam", ("10", "Space Game", "SpaceGame"))
    hades = tmp_path / "Epic" / "Hades"
    catalog = Catalog(read=installed(("epic", "min", "Hades", hades)))
    machine = FakeMachine()
    look = Detector(machine, Library(str(steam)), {}, catalog=catalog).look
    machine.start("helper.exe", 4, str(hades / "helper.exe"))  # (in the background all day)
    assert look(0) is None
    machine.start("hades.exe", 7, str(hades / "x64" / "Hades.exe"))
    machine.front = 7
    assert look(1) == Found("epic:min", "Hades", 7, str(hades / "x64" / "Hades.exe"), "")
    machine.front = 1  # alt-tab: it is still the game
    assert look(3).key == "epic:min"


def test_without_a_window_in_front_any_program_of_such_a_game_counts(tmp_path):
    catalog = Catalog(named=[str(folder(tmp_path / "Games" / "Celeste", "Celeste.exe"))],
                      launchers=False)  # fmt: skip
    machine = FakeMachine(has_foreground=False)
    look = Detector(
        machine, Library(str(tmp_path / "nosteam")), {}, steam=False, catalog=catalog
    ).look
    machine.start("celeste.exe", 3, str(tmp_path / "Games" / "Celeste"))  # (Wine: its folder)
    assert look(0).name == "Celeste" and look(0).key.startswith("folder:")


def test_a_steam_game_stays_steams_even_where_another_list_names_its_folder(tmp_path):
    steam = make_steam(tmp_path / "Steam", ("10", "Space Game", "SpaceGame"))
    space = steam / "steamapps" / "common" / "SpaceGame"
    catalog = Catalog(read=installed(("ea", "{1}", "Space Game (EA)", space)))
    machine = FakeMachine()
    machine.start("space.exe", 7, str(space / "space.exe"))
    machine.front = 7
    found = Detector(machine, Library(str(steam)), {}, catalog=catalog).look(0)
    assert (found.key, found.appid) == ("steam:10", "10")  # (its logo is Steam's)


def test_launcher_games_reads_the_linux_lists_under_home(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    root = tmp_path / ".config" / "heroic" / "legendaryConfig" / "legendary"
    root.mkdir(parents=True)
    (root / "installed.json").write_text(json.dumps(
        {"Fortnite": {"title": "Fortnite", "install_path": "/games/Fortnite"}}))  # fmt: skip
    found = launchers.launcher_games(platform="linux", home=tmp_path)
    assert names(found) == [("epic", "fortnite", "Fortnite")]
