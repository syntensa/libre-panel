"""Steam's folders, read only: the libraries, the installed games, the logos."""

import os
import sys
from types import SimpleNamespace

import pytest
from fake_system import game_path, make_library, make_logo, make_steam

from libre_panel.services import steam

NEW_STYLE = r"""
"libraryfolders"
{
	"0"
	{
		"path"		"C:\\Program Files (x86)\\Steam"
		"label"		""
		"apps"
		{
			"228980"		"1234"
		}
	}
	// a comment
	"1"
	{
		"path"		"D:\\Games \"Library\""
	}
}
"""


def test_vdf_files_are_read_into_nested_dicts():
    found = steam.parse_vdf(NEW_STYLE)
    folders = found["libraryfolders"]
    assert folders["0"]["path"] == r"C:\Program Files (x86)\Steam"
    assert folders["0"]["apps"] == {"228980": "1234"}
    assert folders["1"]["path"] == r'D:\Games "Library"'  # escapes undone
    assert steam.parse_vdf('"Key" "Value"\n"AppState" { "Name" "x\\ty" }') == {
        "key": "Value",  # keys have no case
        "appstate": {"name": "x\ty"},
    }


@pytest.mark.parametrize("text", ["", "{", "}}", '"a" { "b"', '"a" "b" "c" {', "no quotes {}"])
def test_text_that_is_not_quite_vdf_does_not_raise(text):
    assert isinstance(steam.parse_vdf(text), dict)


def test_libraries_are_the_root_and_what_the_file_names(tmp_path):
    other = make_library(tmp_path / "second")
    root = make_steam(tmp_path / "Steam", libraries=[other, tmp_path / "gone"])
    assert steam.libraries(root) == [root, other]  # a disconnected drive is left out


def test_libraries_in_the_old_format_and_without_a_file(tmp_path):
    other = make_library(tmp_path / "second")
    root = make_library(tmp_path / "Steam")
    assert steam.libraries(root) == [root]  # no libraryfolders.vdf
    entries = f'"TimeNextStatsReport" "1700000000"\n"1" "{other}"\n"2" "relative"\n'
    old = '"LibraryFolders"\n{\n' + entries + "}"
    (root / "steamapps" / "libraryfolders.vdf").write_text(old, encoding="utf-8")
    assert steam.libraries(root) == [
        root,
        other,
    ]  # numbered paths; not the statistics, not "relative"


def test_installed_games_come_from_their_manifests(tmp_path):
    root = make_steam(
        tmp_path / "Steam",
        ("10", "Space Game\u2122", "SpaceGame"),
        ("20", "  Other   Game \u00ae ", "other"),
    )
    (root / "steamapps" / "appmanifest_30.acf").write_text('"AppState" { "appid" "30" }')  # no dir
    (root / "steamapps" / "appmanifest_40.acf").write_text("garbage")
    games = steam.installed_games(root)
    assert [(g.appid, g.name, g.folder.name) for g in games] == [
        ("10", "Space Game", "SpaceGame"),
        ("20", "Other Game", "other"),
    ]
    assert games[0].folder == root / "steamapps" / "common" / "SpaceGame"


def games_of(root, **options):
    return steam.Library(str(root), **options)


def test_a_program_belongs_to_the_game_whose_folder_it_lies_in(tmp_path):
    other = make_library(tmp_path / "second", ("20", "Other Game", "Other"))
    root = make_steam(tmp_path / "Steam", ("10", "Space Game", "SpaceGame"), libraries=[other])
    library = games_of(root)
    assert [g.name for g in library.games()] == ["Space Game", "Other Game"]
    assert library.match(game_path(root, "SpaceGame")).appid == "10"
    assert library.match(game_path(root, "SpaceGame", "bin/x64/game.exe")).appid == "10"
    assert library.match(game_path(root, "SpaceGame", "")).appid == "10"  # its folder (Wine)
    assert library.match(game_path(other, "Other", "o.exe")).name == "Other Game"
    assert library.match(game_path(root, "Unknown")) is None  # in no manifest
    assert library.match(str(root / "steam.exe")) is None
    assert library.match(str(tmp_path / "elsewhere" / "game.exe")) is None
    assert library.match("") is None
    assert library.match(game_path(root, "SpaceGame2")) is None  # not the same folder


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths do not care about case")
def test_windows_paths_are_compared_without_case(tmp_path):
    root = make_steam(tmp_path / "Steam", ("10", "Space Game", "SpaceGame"))
    assert games_of(root).match(game_path(root, "spacegame").upper()).appid == "10"


def test_tools_in_a_library_are_no_games(tmp_path):
    root = make_steam(
        tmp_path / "Steam",
        ("1", "Steamworks Common Redistributables", "Steamworks Shared"),
        ("2", "Proton 9.0", "Proton 9.0"),
        ("3", "Steam Linux Runtime 3.0", "SteamLinuxRuntime_sniper"),
        ("4", "Wallpaper Engine", "wallpaper_engine"),
        ("5", "SteamVR", "SteamVR"),
        ("6", "Space Game", "SpaceGame"),
    )
    assert [g.name for g in games_of(root).games()] == ["Space Game"]


def test_the_user_can_name_more_that_are_no_games(tmp_path):
    root = make_steam(tmp_path / "Steam", ("6", "Space Game", "SpaceGame"), ("7", "Chess", "chess"))
    library = games_of(root, ignore=["space game", " CHESS "])
    assert library.games() == []
    assert library.match(game_path(root, "chess")) is None


def test_a_new_game_is_known_without_a_restart(tmp_path):
    root = make_steam(tmp_path / "Steam", ("10", "Space Game", "SpaceGame"))
    now = [0.0]
    library = steam.Library(str(root), clock=lambda: now[0])
    assert [g.name for g in library.games()] == ["Space Game"]
    make_library(root, ("20", "New Game", "New"))
    now[0] = steam.REFRESH_S - 1
    assert len(library.games()) == 1  # read again only now and then
    now[0] = steam.REFRESH_S + 1
    assert [g.name for g in library.games()] == ["Space Game", "New Game"]


def test_steam_is_found_where_it_usually_is(tmp_path, monkeypatch):
    home = tmp_path / "home"
    assert steam.find_root(platform="linux", home=home) is None
    nested = make_library(home / ".local" / "share" / "Steam")
    assert steam.find_root(platform="linux", home=home) == nested
    flatpak = make_library(
        tmp_path / "other" / ".var/app/com.valvesoftware.Steam/.local/share/Steam"
    )
    assert steam.find_root(platform="linux", home=tmp_path / "other") == flatpak
    mac = make_library(tmp_path / "mac" / "Library" / "Application Support" / "Steam")
    assert steam.find_root(platform="darwin", home=tmp_path / "mac") == mac
    given = make_library(tmp_path / "mine")
    assert steam.find_root(str(given), platform="linux", home=home) == given
    assert steam.find_root(str(tmp_path / "nowhere"), platform="linux", home=home) is None
    files = make_library(tmp_path / "Program Files (x86)" / "Steam")
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "Program Files (x86)"))
    registry = SimpleNamespace(HKEY_CURRENT_USER=1, HKEY_LOCAL_MACHINE=2, OpenKey=_no_key)
    assert steam.find_root(platform="win32", home=home, winreg=registry) == files


def _no_key(*_):
    raise OSError("no such key")


class FakeRegistry:
    """The Windows registry with one value: Steam's folder."""

    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = "user", "machine"

    def __init__(self, path):
        self.path = path
        self.opened = []

    def OpenKey(self, hive, subkey):  # noqa: N802 - the name winreg has
        self.opened.append((hive, subkey))
        if (hive, subkey) != ("user", r"Software\Valve\Steam"):
            raise OSError("no such key")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def QueryValueEx(self, key, name):  # noqa: N802
        assert name == "SteamPath"
        return self.path, 1


def test_windows_says_where_steam_is_in_its_registry(tmp_path):
    root = make_library(tmp_path / "D" / "Steam")
    registry = FakeRegistry(str(root).replace(os.sep, "/"))  # Steam writes forward slashes
    assert steam.find_root(platform="win32", home=tmp_path, winreg=registry) == root
    assert registry.opened[0] == ("user", r"Software\Valve\Steam")


def test_a_wrong_folder_is_said_once(tmp_path, caplog):
    library = steam.Library(str(tmp_path / "nowhere"))
    with caplog.at_level("WARNING", logger="libre_panel.services.steam"):
        assert library.games() == [] and library.match("x") is None
        library._read_at = -steam.REFRESH_S
        assert library.games() == []
    assert caplog.text.count("has no steamapps folder") == 1


# -- logos ----------------------------------------------------------------------------------------


def test_the_logo_is_cut_out_and_not_scaled(tmp_path):
    root = make_steam(tmp_path / "Steam", ("10", "Space Game", "SpaceGame"))
    make_logo(root, 10, size=(640, 360), box=(100, 120, 540, 240), sub="abc123")
    logo = steam.Library(str(root)).logo("10")
    assert logo.mode == "RGBA" and logo.size == (440, 120)  # what shows, at its size
    assert logo.getchannel("A").getbbox() == (0, 0, 440, 120)


def test_the_newest_logo_is_the_one_of_the_season(tmp_path):
    root = make_steam(tmp_path / "Steam", ("10", "Space Game", "SpaceGame"))
    make_logo(root, 10, box=(0, 0, 100, 50), sub="old", at=1_000_000)
    make_logo(root, 10, box=(0, 0, 200, 80), sub="season4", at=2_000_000)
    make_logo(root, 10, box=(0, 0, 300, 90), at=500_000)  # the older layout, older still
    assert steam.Library(str(root)).logo("10").size == (200, 80)


def test_older_steam_keeps_the_logo_beside_the_others(tmp_path):
    root = make_steam(tmp_path / "Steam", ("10", "Space Game", "SpaceGame"))
    make_logo(root, 10, box=(10, 10, 110, 60))
    assert steam.Library(str(root)).logo("10").size == (100, 50)


def test_without_a_logo_the_header_picture_is_used_as_it_is(tmp_path):
    root = make_steam(tmp_path / "Steam", ("10", "Space Game", "SpaceGame"))
    assert steam.Library(str(root)).logo("10") is None
    make_logo(root, 10, size=(460, 215), box=(0, 0, 460, 215), name="header.jpg", sub="abc")
    header = steam.Library(str(root)).logo("10")
    assert header.size == (460, 215)  # no transparency to cut at


def test_a_picture_that_cannot_be_read_is_not_a_logo(tmp_path):
    root = make_steam(tmp_path / "Steam", ("10", "Space Game", "SpaceGame"))
    broken = make_logo(root, 10, sub="x")
    broken.write_bytes(b"not a picture")
    assert steam.Library(str(root)).logo("10") is None
    make_logo(root, 10, size=(460, 215), box=(0, 0, 460, 215), name="header.jpg", sub="y")
    assert steam.Library(str(root)).logo("10").size == (460, 215)  # the next best


def test_only_a_number_names_a_game_in_the_cache(tmp_path):
    root = make_steam(tmp_path / "Steam", ("10", "Space Game", "SpaceGame"))
    make_logo(root, 10, sub="x")
    assert steam.cache_files(root, "*", ("logo.png",)) == []
    assert steam.cache_files(root, "10/../10", ("logo.png",)) == []
    assert len(steam.cache_files(root, "10", ("logo.png",))) == 1
