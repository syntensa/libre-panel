"""The icon of the program that plays: the cache, Windows (the logo of a package, the icon of a
program file, with the system calls faked) and desktop entries on Linux."""

import ctypes
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from libre_panel.sensors import appicon


def solid(size=(64, 64), color="red", mode="RGBA"):
    return Image.new(mode, size, color)


def save(path, image):
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


# -- one look per app ---------------------------------------------------------------------------


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def test_an_icon_is_found_once_per_app_and_is_the_same_picture():
    asked = []
    icon = solid()
    icons = appicon.AppIcons(lambda app: asked.append(app) or icon)
    assert icons.get("a.exe") is icon and icons.get("a.exe") is icon
    assert icons.get("b.exe") is icon
    assert asked == ["a.exe", "b.exe"]
    assert icons.get("") is None and asked == ["a.exe", "b.exe"]


def test_an_app_without_an_icon_is_looked_for_again_after_a_while():
    clock, asked = Clock(), []
    found = [None, solid()]
    icons = appicon.AppIcons(lambda app: asked.append(app) or found.pop(0), 60.0, clock)
    assert icons.get("a.exe") is None
    clock.now = 59
    assert icons.get("a.exe") is None and len(asked) == 1  # not yet
    clock.now = 61  # its program may be running by now
    assert icons.get("a.exe") is not None and len(asked) == 2
    clock.now = 10_000
    assert icons.get("a.exe") is not None and len(asked) == 2  # found: for good


def test_a_finder_that_fails_gives_no_icon_and_no_error():
    def broken(app):
        raise OSError("no access")

    assert appicon.AppIcons(broken).get("a.exe") is None


def test_every_system_has_a_finder():
    assert appicon.finder("win32") is appicon.windows_icon
    assert appicon.finder("linux") is appicon.linux_icon
    assert appicon.finder("freebsd14") is appicon.linux_icon


def test_a_badge_is_square_and_a_wide_logo_is_not_stretched():
    wide = solid((200, 100), (255, 0, 0, 255))
    out = appicon.badge(wide)
    assert out.size == (appicon.ICON_SIZE,) * 2 and out.mode == "RGBA"
    middle = appicon.ICON_SIZE // 2
    assert out.getpixel((middle, middle))[3] == 255  # the logo
    assert out.getpixel((middle, 2))[3] == 0  # and see-through bars above and below
    assert appicon.badge(solid((300, 300), "blue", "RGB")).getpixel((5, 5)) == (0, 0, 255, 255)


# -- Windows: which program ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("app_id", "expected"),
    [
        ("Spotify.exe", {"spotify.exe", "spotify"}),
        ("MSEdge", {"msedge", "msedge.exe"}),
        ("Chrome.UJQ5JSVF", {"chrome.ujq5jsvf", "chrome.ujq5jsvf.exe", "chrome", "chrome.exe"}),
        ("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", {"spotify", "spotify.exe",
                                                          "spotifyab.spotifymusic",
                                                          "spotifyab.spotifymusic.exe",
                                                          "spotifyab.spotifymusic_zpdnekdrzrea0!spotify",
                                                          "spotifyab.spotifymusic_zpdnekdrzrea0!spotify.exe"}),
    ],
)  # fmt: skip
def test_the_program_of_a_player_goes_by_the_name_in_its_id(app_id, expected):
    assert appicon.program_names(app_id) == expected


def test_a_running_program_is_found_by_the_names_of_the_id():
    running = [("explorer.exe", "C:/Windows/explorer.exe"), ("spotify.exe", "C:/Apps/Spotify.exe")]
    assert appicon.running_program("Spotify.exe", lambda: running) == "C:/Apps/Spotify.exe"
    assert appicon.running_program("MSEdge", lambda: running) is None


def test_a_packaged_app_shows_its_logo_and_a_program_its_icon(monkeypatch):
    logo, exe = solid(color="green"), solid(color="blue")
    calls = []
    monkeypatch.setattr(
        appicon, "package_logo", lambda app_id, api=None: calls.append("logo") or logo
    )
    monkeypatch.setattr(
        appicon, "running_program", lambda app_id: calls.append("program") or "x.exe"
    )
    monkeypatch.setattr(appicon, "exe_icon", lambda path, api=None: calls.append("exe") or exe)
    assert appicon.windows_icon("Pkg_hash!App") is logo
    assert calls == ["logo"]  # the program is not even looked for
    assert appicon.windows_icon("Spotify.exe") is exe
    assert calls == ["logo", "program", "exe"]  # a program has no package logo to ask for


def test_a_packaged_app_without_a_logo_falls_back_to_its_program(monkeypatch):
    exe = solid(color="blue")
    monkeypatch.setattr(appicon, "package_logo", lambda app_id, api=None: None)
    monkeypatch.setattr(appicon, "running_program", lambda app_id: "C:/Apps/spotify.exe")
    monkeypatch.setattr(appicon, "exe_icon", lambda path, api=None: exe)
    assert appicon.windows_icon("Pkg_hash!Spotify") is exe
    monkeypatch.setattr(appicon, "running_program", lambda app_id: None)
    assert appicon.windows_icon("Pkg_hash!Spotify") is None


# -- Windows: the icon of a program file ---------------------------------------------------------


class FakeIcons:
    """user32 and gdi32 with one icon in them: handle 7, its colour bitmap 11, its mask 12.
    Pixels are BGRA, top row first; the calls check what they are given."""

    ICON, COLOR, MASK, DEVICE = 7, 11, 12, 99

    def __init__(self, color=None, mask=None, size=(8, 8), found=1, has_color=True):
        self.color, self.mask, self.size, self.found = color, mask, size, found
        self.has_color = has_color
        self.extracted, self.destroyed, self.deleted, self.released = [], [], [], []
        self.user32 = SimpleNamespace(
            PrivateExtractIconsW=self.extract,
            GetIconInfo=self.icon_info,
            DestroyIcon=self.destroy,
            GetDC=lambda window: self.DEVICE,
            ReleaseDC=lambda window, device: self.released.append(device) or 1,
        )
        self.gdi32 = SimpleNamespace(
            GetObjectW=self.get_object, GetDIBits=self.get_bits, DeleteObject=self.delete
        )

    def extract(self, path, index, cx, cy, icon, ident, count, flags):
        self.extracted.append((path, index, cx, cy, count, flags))
        if self.found == 1:
            icon._obj.value = self.ICON  # byref(c_void_p): the handle goes in
        return self.found

    def icon_info(self, icon, info):
        assert icon.value == self.ICON
        info._obj.hbmColor = self.COLOR if self.has_color else None
        info._obj.hbmMask = self.MASK
        return 1

    def destroy(self, icon):
        self.destroyed.append(icon.value)
        return 1

    def get_object(self, bitmap, size, out):
        assert bitmap == self.COLOR and size == ctypes.sizeof(appicon.Bitmap)
        out._obj.bmWidth, out._obj.bmHeight = self.size
        return size

    def get_bits(self, device, bitmap, start, lines, buffer, header, usage):
        assert device == self.DEVICE and start == 0 and usage == appicon.DIB_RGB_COLORS
        head = header._obj
        width, height = self.size
        assert (head.biWidth, head.biHeight, head.biBitCount) == (width, -height, 32)  # top-down
        data = self.color if bitmap == self.COLOR else self.mask
        assert len(data) == width * height * 4 and lines == height
        ctypes.memmove(buffer, data, len(data))
        return lines

    def delete(self, handle):
        self.deleted.append(handle)
        return 1


def pixels(rgba, size=(8, 8)):
    """BGRA bytes of one colour for every pixel."""
    r, g, b, a = rgba
    return bytes((b, g, r, a)) * (size[0] * size[1])


def test_the_icon_of_a_program_is_taken_at_a_large_size_and_cleaned_up():
    api = FakeIcons(color=pixels((255, 0, 0, 255)))
    icon = appicon.exe_icon("C:/Apps/Player.exe", api)
    assert api.extracted == [("C:/Apps/Player.exe", 0, 256, 256, 1, 0)]
    assert icon.size == (appicon.ICON_SIZE,) * 2 and icon.mode == "RGBA"
    assert icon.getpixel((40, 40)) == (255, 0, 0, 255)  # blue-green-red-alpha became RGBA
    assert api.destroyed == [FakeIcons.ICON]
    assert sorted(api.deleted) == [FakeIcons.COLOR, FakeIcons.MASK]  # nothing left behind
    assert api.released == [FakeIcons.DEVICE] * 1


def test_an_old_icon_without_alpha_is_cut_out_by_its_mask():
    size = (8, 8)
    color = pixels((10, 200, 30, 0), size)  # colours, but no alpha anywhere
    left_see_through = (bytes((255, 255, 255, 0)) * 4 + bytes((0, 0, 0, 0)) * 4) * 8
    api = FakeIcons(color=color, mask=left_see_through, size=size)
    icon = appicon.exe_icon("old.exe", api)
    assert icon.getpixel((4, 48))[3] == 0  # the half the mask marks white
    assert icon.getpixel((90, 48)) == (10, 200, 30, 255)  # the other half shows


@pytest.mark.parametrize("found", [0, 0xFFFFFFFF])
def test_a_program_without_an_icon_gives_none(found):
    api = FakeIcons(found=found)
    assert appicon.exe_icon("none.exe", api) is None
    assert api.destroyed == []  # there was no handle to give back


def test_a_black_and_white_icon_is_given_back_and_not_shown():
    api = FakeIcons(has_color=False)
    assert appicon.exe_icon("mono.exe", api) is None
    assert api.destroyed == [FakeIcons.ICON] and api.deleted == [FakeIcons.MASK]


def test_an_icon_whose_pixels_cannot_be_had_is_given_back():
    api = FakeIcons(color=pixels((1, 2, 3, 255)))
    api.gdi32.GetDIBits = lambda *args: 0
    assert appicon.exe_icon("x.exe", api) is None
    assert api.destroyed == [FakeIcons.ICON] and sorted(api.deleted) == [11, 12]
    assert api.released == [FakeIcons.DEVICE]


# -- Windows: the logo of a package ---------------------------------------------------------------

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10">
  <Applications>
    <Application Id="Other" Executable="other.exe">
      <uap:VisualElements Square44x44Logo="Assets\\Other44.png"
                          Square150x150Logo="Assets\\Other150.png"/>
    </Application>
    <Application Id="Player" Executable="player.exe">
      <uap:VisualElements DisplayName="Player" Square150x150Logo="Assets\\Square150x150Logo.png"
                          Square44x44Logo="Assets\\Square44x44Logo.png"/>
    </Application>
  </Applications>
</Package>
"""


@pytest.fixture
def package(tmp_path):
    folder = tmp_path / "Pkg_1.0.0.0_x64__hash"
    folder.mkdir()
    (folder / "AppxManifest.xml").write_text(MANIFEST, encoding="utf-8")
    return folder


def test_the_manifest_names_the_logo_of_the_application(package):
    assert appicon.manifest_logo(package, "Player") == "Assets\\Square44x44Logo.png"
    assert appicon.manifest_logo(package, "Other") == "Assets\\Other44.png"
    assert appicon.manifest_logo(package, "") == "Assets\\Other44.png"  # the first
    assert appicon.manifest_logo(package, "Missing") is None
    assert appicon.manifest_logo(package / "nowhere", "Player") is None
    (package / "AppxManifest.xml").write_text("<broken", encoding="utf-8")
    assert appicon.manifest_logo(package, "Player") is None


def test_the_logo_file_is_the_one_without_a_plate_nearest_above_the_size_wanted(package):
    assets = package / "Assets"
    for name in (
        "Square44x44Logo.scale-100.png",
        "Square44x44Logo.scale-200.png",
        "Square44x44Logo.targetsize-24.png",
        "Square44x44Logo.targetsize-48.png",
        "Square44x44Logo.targetsize-256.png",
        "Square44x44Logo.targetsize-48_altform-unplated.png",
        "Square44x44Logo.targetsize-128_altform-unplated.png",
        "Square44x44Logo.targetsize-256_altform-lightunplated.png",
        "Square150x150Logo.png",
    ):
        save(assets / name, solid((8, 8)))
    found = appicon.logo_file(package / "Assets" / "Square44x44Logo.png")
    assert found.name == "Square44x44Logo.targetsize-128_altform-unplated.png"


def test_without_unplated_pictures_the_nearest_size_above_is_taken(package):
    assets = package / "Assets"
    for name in (
        "Logo.scale-100.png",
        "Logo.scale-200.png",
        "Logo.scale-400.png",
        "Logo.targetsize-16.png",
    ):
        save(assets / name, solid((8, 8)))
    # scale-200 of a 44-pixel logo is 88 pixels (under 96), scale-400 is 176: nearest above
    assert appicon.logo_file(package / "Assets" / "Logo.png").name == "Logo.scale-400.png"
    only_small = package / "Small"
    for name in ("Logo.scale-100.png", "Logo.targetsize-24.png"):
        save(only_small / name, solid((8, 8)))
    assert appicon.logo_file(only_small / "Logo.png").name == "Logo.scale-100.png"  # 44 beats 24


def test_a_logo_that_ships_as_it_is_named_is_taken_and_a_missing_one_is_none(package):
    save(package / "Assets" / "Square44x44Logo.png", solid((8, 8)))
    assert (
        appicon.logo_file(package / "Assets" / "Square44x44Logo.png").name == "Square44x44Logo.png"
    )
    assert appicon.logo_file(package / "Assets" / "Nothing.png") is None
    assert appicon.logo_file(package / "NoFolder" / "Nothing.png") is None


def test_the_logo_of_a_packaged_app_is_a_badge(package):
    save(
        package / "Assets" / "Square44x44Logo.targetsize-256_altform-unplated.png",
        solid((256, 256), (0, 128, 0, 255)),
    )
    other = package.parent / "Pkg_0.9.0.0_x64__hash"
    other.mkdir()
    logo = appicon.package_logo("Pkg_hash!Player", folders=lambda family: [other, package])
    assert logo.size == (appicon.ICON_SIZE,) * 2 and logo.getpixel((40, 40)) == (0, 128, 0, 255)
    assert appicon.package_logo("Pkg_hash!Missing", folders=lambda family: [package]) is None
    assert appicon.package_logo("Pkg_hash!Player", folders=lambda family: []) is None


def test_the_family_name_is_what_comes_before_the_exclamation_mark(package):
    asked = []

    def folders(family):
        asked.append(family)
        return []

    appicon.package_logo("Publisher.Name_8wekyb3d8bbwe!App", folders=folders)
    assert asked == ["Publisher.Name_8wekyb3d8bbwe"]


class FakeKernel:
    """The package calls of Windows for some installed packages, with their way of asking twice:
    first how much room there must be (ERROR_INSUFFICIENT_BUFFER), then for the answer."""

    def __init__(self, packages):
        self.packages = packages  # full name -> install folder
        self.families = []

    def FindPackagesByPackageFamily(self, family, filters, count, names, length, buffer, props):  # noqa: N802
        self.families.append((family, filters))
        full = list(self.packages)
        room = sum(len(name) + 1 for name in full)
        if names is None:
            count._obj.value, length._obj.value = len(full), room
            return 122 if full else 0
        assert (count._obj.value, length._obj.value) == (len(full), room)
        assert len(buffer) >= room and len(props) == len(full)
        for i, name in enumerate(full):
            names[i] = name
        return 0

    def GetPackagePathByFullName(self, name, size, path):  # noqa: N802
        folder = self.packages[name]
        if path is None:
            size._obj.value = len(folder) + 1
            return 122
        assert size._obj.value == len(folder) + 1
        path.value = folder
        return 0


def test_the_install_folders_of_a_package_family():
    kernel = SimpleNamespace(
        kernel32=FakeKernel(
            {"Pkg_1.0_x64__hash": "C:/Apps/Pkg_1.0", "Pkg_2.0_x64__hash": "C:/Apps/Pkg_2.0"}
        )
    )
    assert appicon.package_folders("Pkg_hash", kernel) == [
        Path("C:/Apps/Pkg_1.0"),
        Path("C:/Apps/Pkg_2.0"),
    ]
    assert kernel.kernel32.families[0] == ("Pkg_hash", appicon.PACKAGE_FILTER_HEAD)
    assert appicon.package_folders("None_hash", SimpleNamespace(kernel32=FakeKernel({}))) == []


# -- Linux --------------------------------------------------------------------------------------


@pytest.fixture
def share(tmp_path):
    root = tmp_path / "share"
    (root / "applications").mkdir(parents=True)
    return root


def entry(share, name, icon, extra=""):
    path = share / "applications" / name
    path.write_text(
        f"[Desktop Entry]\nName=Player\nIcon[de]=nope\nIcon={icon}\n{extra}\n"
        "[Desktop Action Next]\nIcon=not-this\n",
        encoding="utf-8",
    )
    return path


def test_the_data_folders_follow_the_xdg_settings(tmp_path):
    env = {"XDG_DATA_HOME": str(tmp_path / "mine"), "XDG_DATA_DIRS": "/a:/b:"}
    found = appicon.data_dirs(env, tmp_path)
    assert found[:3] == [tmp_path / "mine", Path("/a"), Path("/b")]
    assert Path("/var/lib/flatpak/exports/share") in found  # and where Flatpak and Snap export
    defaults = appicon.data_dirs({}, tmp_path)
    assert defaults[:3] == [
        tmp_path / ".local" / "share",
        Path("/usr/local/share"),
        Path("/usr/share"),
    ]


def test_a_player_finds_its_desktop_entry_by_name(share):
    exact = entry(share, "spotify.desktop", "spotify-client")
    entry(share, "org.mozilla.firefox.desktop", "firefox")
    entry(share, "chromium-browser.desktop", "chromium")
    entry(share, "chromium.snap.desktop", "other")
    dirs = [share]
    assert appicon.desktop_entry("Spotify", dirs) == exact
    assert appicon.desktop_entry("firefox", dirs).name == "org.mozilla.firefox.desktop"
    assert appicon.desktop_entry("chromium", dirs).name == "chromium-browser.desktop"
    assert appicon.desktop_entry("vlc", dirs) is None
    assert appicon.desktop_entry("vlc", [share / "nowhere"]) is None


def test_the_icon_of_an_entry_is_the_one_of_its_main_section(share):
    path = entry(share, "spotify.desktop", "spotify-client")
    assert appicon.desktop_icon_name(path) == "spotify-client"
    path.write_text("[Desktop Entry]\nName=No icon\n", encoding="utf-8")
    assert appicon.desktop_icon_name(path) is None


def test_the_biggest_picture_of_the_theme_or_a_pixmap_is_the_file(share, tmp_path):
    for size in ("48x48", "256x256", "128x128"):
        save(share / "icons" / "hicolor" / size / "apps" / "spotify-client.png", solid((8, 8)))
    assert appicon.icon_file("spotify-client", [share]).parent.parent.name == "256x256"
    save(share / "pixmaps" / "old-player.png", solid((8, 8)))
    assert appicon.icon_file("old-player", [share]).name == "old-player.png"
    absolute = save(tmp_path / "somewhere" / "logo.png", solid((8, 8)))
    assert appicon.icon_file(str(absolute), [share]) == absolute
    assert appicon.icon_file(str(tmp_path / "gone.png"), [share]) is None
    assert appicon.icon_file("unknown", [share]) is None


def test_a_player_has_the_icon_its_desktop_entry_names(share):
    entry(share, "spotify.desktop", "spotify-client")
    save(
        share / "icons" / "hicolor" / "256x256" / "apps" / "spotify-client.png",
        solid((256, 256), "red"),
    )
    icon = appicon.linux_icon("spotify", [share])
    assert icon.size == (appicon.ICON_SIZE,) * 2 and icon.getpixel((40, 40)) == (255, 0, 0, 255)
    assert appicon.linux_icon("vlc", [share]) is None


def test_an_icon_that_only_exists_as_a_vector_is_left_out(share):
    entry(share, "vlc.desktop", "vlc")
    svg = share / "icons" / "hicolor" / "scalable" / "apps" / "vlc.svg"
    svg.parent.mkdir(parents=True)
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    assert appicon.linux_icon("vlc", [share]) is None
    entry(share, "mpv.desktop", str(svg))  # named by path: Pillow cannot read it either
    assert appicon.linux_icon("mpv", [share]) is None
