"""The icon of the program that plays, for the badge on a "now playing" message.

The media controls name the player only by an id, so each system has its own way
from that id to a picture. Everything here only reads; no program is touched.

- Windows: the id is the AppUserModelID. A packaged (Store) app is
  ``PackageFamilyName!ApplicationId``; its logo is the one its manifest names.
  Any other program is named after its file (``Spotify.exe``, ``MSEdge``): the
  icon comes from the file of the running program of that name.
- Linux: the MPRIS player name (``spotify``) finds ``spotify.desktop`` and the
  PNG its ``Icon=`` line names. Icons that only exist as SVG are left out.

An icon is looked for once per app. The Windows calls are loaded on first use.
"""

from __future__ import annotations

import ctypes
import functools
import glob
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from PIL import Image

log = logging.getLogger(__name__)

ICON_SIZE = 96  # pixels: enough for the badge of the widest panel
EXTRACT_SIZE = 256  # asked of Windows, then scaled down here (it scales less well)
RETRY_SECONDS = 60.0  # an app whose icon was not found is looked for again after this long

PACKAGE_FILTER_HEAD = 0x10  # the main package, not its resources
ERROR_INSUFFICIENT_BUFFER = 122
DIB_RGB_COLORS = 0
# manifest entries that name a logo, best first (the first is what the taskbar shows)
LOGO_ATTRIBUTES = ("Square44x44Logo", "Square150x150Logo", "Logo", "SmallLogo")


class AppIcons:
    """Icons by app id, found once per app. An app without one is tried again after
    ``retry_s``: its program may not have been running yet."""

    def __init__(
        self,
        find: Callable[[str], Image.Image | None],
        retry_s: float = RETRY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._find, self._retry_s, self._clock = find, retry_s, clock
        self._known: dict[str, tuple[Image.Image | None, float]] = {}

    def get(self, app_id: str) -> Image.Image | None:
        """The same picture object every time for an app (callers compare by identity)."""
        if not app_id:
            return None
        known = self._known.get(app_id)
        if known is not None and (known[0] is not None or self._clock() - known[1] < self._retry_s):
            return known[0]
        try:
            image = self._find(app_id)
        except Exception:  # noqa: BLE001 - a missing icon is no error
            log.debug("media: no icon for %r", app_id, exc_info=True)
            image = None
        self._known[app_id] = (image, self._clock())
        return image


def finder(platform: str | None = None) -> Callable[[str], Image.Image | None]:
    """The way to find an icon from the player's id on this system."""
    platform = platform or sys.platform
    if platform == "win32":
        return windows_icon
    return linux_icon


def badge(image: Image.Image) -> Image.Image:
    """A square RGBA picture of ``ICON_SIZE``; a wide logo is not stretched."""
    image = image.convert("RGBA")
    side = max(image.size)
    if image.size != (side, side):
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
        image = square
    return image.resize((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)


def load_file(path: Path) -> Image.Image | None:
    """An icon from a picture file Pillow can read, or None."""
    try:
        with Image.open(path) as opened:
            return badge(opened)
    except Exception as exc:  # noqa: BLE001 - an unreadable file (an SVG) is no error
        log.debug("media: icon file %s not read (%s)", path, exc)
        return None


# -- Windows -------------------------------------------------------------------------


class IconInfo(ctypes.Structure):
    _fields_ = [
        ("fIcon", ctypes.c_int),
        ("xHotspot", ctypes.c_uint32),
        ("yHotspot", ctypes.c_uint32),
        ("hbmMask", ctypes.c_void_p),
        ("hbmColor", ctypes.c_void_p),
    ]


class Bitmap(ctypes.Structure):
    _fields_ = [
        ("bmType", ctypes.c_int32),
        ("bmWidth", ctypes.c_int32),
        ("bmHeight", ctypes.c_int32),
        ("bmWidthBytes", ctypes.c_int32),
        ("bmPlanes", ctypes.c_uint16),
        ("bmBitsPixel", ctypes.c_uint16),
        ("bmBits", ctypes.c_void_p),
    ]


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


@functools.cache
def _windows_api() -> Any:
    """user32, gdi32 and kernel32 with the types of the calls set: without them ctypes
    cuts a 64-bit handle to 32 bits. Own DLL objects, so that no function pointer other
    code shares is changed."""
    c = ctypes
    user32 = c.WinDLL("user32")  # type: ignore[attr-defined]
    gdi32 = c.WinDLL("gdi32")  # type: ignore[attr-defined]
    kernel32 = c.WinDLL("kernel32")  # type: ignore[attr-defined]
    user32.PrivateExtractIconsW.argtypes = (
        c.c_wchar_p, c.c_int, c.c_int, c.c_int, c.POINTER(c.c_void_p),
        c.POINTER(c.c_uint32), c.c_uint32, c.c_uint32,
    )  # fmt: skip
    user32.PrivateExtractIconsW.restype = c.c_uint32
    user32.GetIconInfo.argtypes = (c.c_void_p, c.POINTER(IconInfo))
    user32.GetIconInfo.restype = c.c_int
    user32.DestroyIcon.argtypes = (c.c_void_p,)
    user32.DestroyIcon.restype = c.c_int
    user32.GetDC.argtypes = (c.c_void_p,)
    user32.GetDC.restype = c.c_void_p
    user32.ReleaseDC.argtypes = (c.c_void_p, c.c_void_p)
    user32.ReleaseDC.restype = c.c_int
    gdi32.GetObjectW.argtypes = (c.c_void_p, c.c_int, c.c_void_p)
    gdi32.GetObjectW.restype = c.c_int
    gdi32.GetDIBits.argtypes = (
        c.c_void_p, c.c_void_p, c.c_uint32, c.c_uint32, c.c_void_p,
        c.POINTER(BitmapInfoHeader), c.c_uint32,
    )  # fmt: skip
    gdi32.GetDIBits.restype = c.c_int
    gdi32.DeleteObject.argtypes = (c.c_void_p,)
    gdi32.DeleteObject.restype = c.c_int
    kernel32.FindPackagesByPackageFamily.argtypes = (
        c.c_wchar_p, c.c_uint32, c.POINTER(c.c_uint32), c.c_void_p, c.POINTER(c.c_uint32),
        c.c_void_p, c.c_void_p,
    )  # fmt: skip
    kernel32.FindPackagesByPackageFamily.restype = c.c_int32
    kernel32.GetPackagePathByFullName.argtypes = (
        c.c_wchar_p, c.POINTER(c.c_uint32), c.c_void_p,
    )  # fmt: skip
    kernel32.GetPackagePathByFullName.restype = c.c_int32

    class Api:
        pass

    api = Api()
    api.user32, api.gdi32, api.kernel32 = user32, gdi32, kernel32  # type: ignore[attr-defined]
    return api


def windows_icon(app_id: str, api: Any = None) -> Image.Image | None:
    """The icon of the app with this AppUserModelID: the logo of its package, else the
    icon of its running program."""
    if "!" in app_id:
        logo = package_logo(app_id, api=api)
        if logo is not None:
            return logo
    path = running_program(app_id)
    return exe_icon(path, api) if path else None


def program_names(app_id: str) -> set[str]:
    """The file names (lower case) a player's program may have, from its media-controls
    id: ``Spotify.exe``, ``MSEdge`` (msedge.exe), ``Chrome.ABCDEF`` (chrome.exe), and for a
    packaged app the program named after the application (``Pkg_hash!Spotify``)."""
    names: set[str] = set()

    def add(text: str) -> None:
        text = text.strip().lower()
        if text:
            names.update({text, text if text.endswith(".exe") else text + ".exe"})

    add(app_id)
    if "!" in app_id:
        family, _, application = app_id.partition("!")
        add(application)
        add(family.partition("_")[0])
    else:
        add(app_id.partition(".")[0])
    return names


def programs_running() -> Iterable[tuple[str, str]]:
    """(file name in lower case, full path) of every program that runs and can be read."""
    import psutil

    for process in psutil.process_iter(["exe"]):
        exe = process.info.get("exe")
        if exe:
            yield os.path.basename(exe).lower(), exe


def running_program(
    app_id: str, programs: Callable[[], Iterable[tuple[str, str]]] = programs_running
) -> str | None:
    """The path of a running program that goes with the id, or None."""
    names = program_names(app_id)
    for name, path in programs():
        if name in names:
            return path
    return None


def exe_icon(path: str, api: Any = None) -> Image.Image | None:
    """The icon of a program file. ``PrivateExtractIconsW`` takes the size wanted, so the
    large icon comes, not the 32-pixel one."""
    api = api or _windows_api()
    icon, ident = ctypes.c_void_p(), ctypes.c_uint32()
    found = api.user32.PrivateExtractIconsW(
        path, 0, EXTRACT_SIZE, EXTRACT_SIZE, ctypes.byref(icon), ctypes.byref(ident), 1, 0
    )
    if not found or not icon.value:  # nothing, or 0xFFFFFFFF for an error
        return None
    try:
        image = icon_image(icon, api)
    finally:
        api.user32.DestroyIcon(icon)
    return badge(image) if image is not None else None


def icon_image(icon: Any, api: Any) -> Image.Image | None:
    """An HICON as a Pillow picture. An old icon has no alpha channel of its own: its
    mask says what is see-through then."""
    info = IconInfo()
    if not api.user32.GetIconInfo(icon, ctypes.byref(info)):
        return None
    try:
        if not info.hbmColor:  # a black-and-white icon: not worth showing
            return None
        bitmap = Bitmap()
        api.gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(bitmap), ctypes.byref(bitmap))
        width, height = bitmap.bmWidth, bitmap.bmHeight
        if width <= 0 or height <= 0:
            return None
        colors = _pixels(api, info.hbmColor, width, height)
        if colors is None:
            return None
        image = Image.frombytes("RGBA", (width, height), colors, "raw", "BGRA")
        if not image.getchannel("A").getbbox():
            mask = _pixels(api, info.hbmMask, width, height) if info.hbmMask else None
            if mask is not None:
                see_through = Image.frombytes("RGBA", (width, height), mask, "raw", "BGRA")
                image.putalpha(see_through.convert("L").point(lambda v: 0 if v else 255))
        return image
    finally:
        for bitmap_handle in (info.hbmColor, info.hbmMask):
            if bitmap_handle:
                api.gdi32.DeleteObject(bitmap_handle)


def _pixels(api: Any, bitmap: Any, width: int, height: int) -> bytes | None:
    """The 32-bit pixels of a bitmap, top row first (BGRA)."""
    header = BitmapInfoHeader()
    header.biSize = ctypes.sizeof(BitmapInfoHeader)
    header.biWidth, header.biHeight = width, -height  # a negative height: top-down
    header.biPlanes, header.biBitCount = 1, 32
    buffer = ctypes.create_string_buffer(width * height * 4)
    device = api.user32.GetDC(None)
    try:
        done = api.gdi32.GetDIBits(
            device, bitmap, 0, height, buffer, ctypes.byref(header), DIB_RGB_COLORS
        )
    finally:
        api.user32.ReleaseDC(None, device)
    return buffer.raw if done else None


def package_folders(family: str, api: Any = None) -> list[Path]:
    """The install folders of a package family's main packages (several versions can be
    installed at once)."""
    api = api or _windows_api()
    kernel = api.kernel32
    count, length = ctypes.c_uint32(0), ctypes.c_uint32(0)
    # asked twice: first how much room it takes, then for the names
    if (
        kernel.FindPackagesByPackageFamily(
            family, PACKAGE_FILTER_HEAD, ctypes.byref(count), None, ctypes.byref(length), None, None
        )
        != ERROR_INSUFFICIENT_BUFFER
        or not count.value
    ):
        return []
    names = (ctypes.c_wchar_p * count.value)()
    buffer = ctypes.create_unicode_buffer(length.value)
    properties = (ctypes.c_uint32 * count.value)()
    if kernel.FindPackagesByPackageFamily(
        family, PACKAGE_FILTER_HEAD, ctypes.byref(count), names, ctypes.byref(length),
        buffer, properties,
    ):  # fmt: skip
        return []
    folders = []
    for full_name in list(names)[: count.value]:
        size = ctypes.c_uint32(0)
        if (
            kernel.GetPackagePathByFullName(full_name, ctypes.byref(size), None)
            != (ERROR_INSUFFICIENT_BUFFER)
            or not size.value
        ):
            continue
        path = ctypes.create_unicode_buffer(size.value)
        if kernel.GetPackagePathByFullName(full_name, ctypes.byref(size), path) == 0:
            folders.append(Path(path.value))
    return folders


def package_logo(
    app_id: str,
    api: Any = None,
    folders: Callable[[str], list[Path]] | None = None,
) -> Image.Image | None:
    """The logo of a packaged app (``PackageFamilyName!ApplicationId``), from the picture
    its manifest names. Any installed version will do; the highest folder name goes first."""
    family, _, application = app_id.partition("!")
    found = folders or (lambda name: package_folders(name, api))
    for folder in sorted(found(family), reverse=True):
        declared = manifest_logo(folder, application)
        # the manifest writes backslashes; any system reads them as a path
        file = logo_file(folder / declared.replace("\\", "/")) if declared else None
        image = load_file(file) if file else None
        if image is not None:
            return image
    return None


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def manifest_logo(folder: Path, application: str) -> str | None:
    """The logo ``AppxManifest.xml`` names for the application (the first, if none is
    named): ``Assets\\Square44x44Logo.png``, a path relative to the package."""
    try:
        root = ElementTree.parse(folder / "AppxManifest.xml").getroot()
    except (OSError, ElementTree.ParseError):
        return None
    for app in root.iter():
        if _local(app.tag) != "Application" or (application and app.get("Id") != application):
            continue
        for element in app.iter():
            if _local(element.tag) == "VisualElements":
                for name in LOGO_ATTRIBUTES:
                    if element.get(name):
                        return element.get(name)
    return None


def logo_file(declared: Path) -> Path | None:
    """The file behind a logo the manifest names. ``Logo.png`` ships as ``Logo.scale-200.png``,
    ``Logo.targetsize-48.png`` and so on; the one without a backing plate and nearest
    in size above ``ICON_SIZE`` is taken."""
    pattern = glob.escape(declared.stem) + "*" + glob.escape(declared.suffix)
    try:
        candidates = [p for p in declared.parent.glob(pattern) if p.is_file()]
    except OSError:
        candidates = []
    if declared.is_file() and declared not in candidates:
        candidates.append(declared)
    if not candidates:
        return None
    base = re.search(r"(\d+)x\d+", declared.stem)
    return max(candidates, key=lambda p: _logo_rank(p.name, int(base.group(1)) if base else 44))


def _logo_rank(name: str, base: int) -> tuple[int, int]:
    """Higher is better: no plate first (``altform-unplated``, not the light variant), then
    the size nearest above ``ICON_SIZE``, then the biggest below it."""
    name = name.lower()
    plain = "altform-unplated" in name and "lightunplated" not in name
    size = 0
    if found := re.search(r"targetsize-(\d+)", name):
        size = int(found.group(1))
    elif found := re.search(r"scale-(\d+)", name):
        size = base * int(found.group(1)) // 100
    nearness = -(size - ICON_SIZE) if size >= ICON_SIZE else -10_000 + size
    return (1 if plain else 0), nearness


# -- Linux ---------------------------------------------------------------------------

THEME_SIZES = ("512x512", "256x256", "192x192", "128x128", "96x96", "64x64", "48x48", "32x32")


def data_dirs(environment: dict[str, str] | None = None, home: Path | None = None) -> list[Path]:
    """The folders desktop entries and icons live in: the user's, the system's, and where
    Flatpak and Snap export theirs."""
    environment = os.environ if environment is None else environment
    home = home or Path.home()
    system = environment.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    found = [Path(environment.get("XDG_DATA_HOME") or home / ".local" / "share")]
    found += [Path(p) for p in system.split(":") if p]
    found += [
        Path("/var/lib/flatpak/exports/share"),
        home / ".local" / "share" / "flatpak" / "exports" / "share",
        Path("/var/lib/snapd/desktop"),
    ]
    return list(dict.fromkeys(found))


def desktop_entry(player: str, dirs: list[Path]) -> Path | None:
    """The desktop entry of an MPRIS player: ``spotify.desktop``, else one named like a
    reverse domain (``org.mozilla.firefox.desktop``) or with a suffix (``firefox-esr``)."""
    name = player.lower()
    for base in dirs:
        exact = base / "applications" / f"{name}.desktop"
        if exact.is_file():
            return exact
    for pattern in (f"*.{name}.desktop", f"{name}-*.desktop"):
        for base in dirs:
            try:
                matches = sorted((base / "applications").glob(pattern), key=lambda p: len(p.name))
            except OSError:
                continue
            if matches:
                return matches[0]
    return None


def desktop_icon_name(entry: Path) -> str | None:
    """The ``Icon=`` of a desktop entry's main section."""
    inside = False
    for line in entry.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("["):
            inside = line == "[Desktop Entry]"
        elif inside and line.startswith("Icon="):
            return line[5:].strip() or None
    return None


def icon_file(icon: str, dirs: list[Path]) -> Path | None:
    """The PNG for an icon name: the biggest in the hicolor theme, else a pixmap."""
    if os.path.isabs(icon):
        return Path(icon) if os.path.isfile(icon) else None
    for base in dirs:
        for size in THEME_SIZES:
            path = base / "icons" / "hicolor" / size / "apps" / f"{icon}.png"
            if path.is_file():
                return path
    for base in dirs:
        for suffix in (".png", ".xpm"):
            path = base / "pixmaps" / f"{icon}{suffix}"
            if path.is_file():
                return path
    return None


def linux_icon(player: str, dirs: list[Path] | None = None) -> Image.Image | None:
    """The icon of an MPRIS player, by its name."""
    dirs = dirs if dirs is not None else data_dirs()
    entry = desktop_entry(player, dirs)
    name = desktop_icon_name(entry) if entry else None
    file = icon_file(name, dirs) if name else None
    return load_file(file) if file else None
