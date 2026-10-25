"""A theme as one file to give to someone, and such a file put in place (Start → Themes:
Export…, Import…, and the community's themes).

The file is a zip of the theme's folder: ``<name>/theme.json`` and the pictures and fonts the
theme names, at the paths it names them by (``<name>/assets/back.png``), nothing else. The
theme.json in it is the theme as the editor saves it, without a line that names a whole path
or one of the person's own folders (a slideshow's ``C:/Users/…/*.jpg``, which no other
computer has).

Importing trusts nothing in the file. Before anything is written: at most 20 MB, 50 MB
unpacked, no path that leaves the folder or names a whole path, no links, only theme.json,
pictures (PNG, JPEG, GIF, WebP) and fonts (TTF, OTF), each what its name says, and a
theme.json Libre Panel loads. Of the pictures and fonts only those the theme names are kept.
The theme goes into the person's themes folder under its folder's name, with a number after
it where that name is taken (``lumen-2`` beside the built-in Lumen).
"""

from __future__ import annotations

import io
import json
import os
import posixpath
import re
import secrets
import shutil
import stat
import tempfile
import threading
import zipfile
import zlib
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from libre_panel.config import config_dir, user_themes_dir
from libre_panel.i18n import t
from libre_panel.theme.model import (
    THEME_FILENAME,
    ThemeError,
    find_theme,
    load_theme,
    parse_theme,
    resolve_asset,
    valid_theme_name,
)

MAX_ZIP = 20 * 1024 * 1024  # a shared theme's file
MAX_UNPACKED = 50 * 1024 * 1024  # its files together
MAX_FILES = 1000
MAX_THEME_JSON = 2 * 1024 * 1024
MAX_GLOBS = 64  # "photos/*.jpg": a folder of pictures; a theme has a few of them at most
# What a theme folder may carry besides its theme.json, by the first bytes of each kind
MAGIC: dict[str, tuple[bytes, ...]] = {
    ".png": (b"\x89PNG",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),  # (and "WEBP" at 8)
    ".ttf": (b"\x00\x01\x00\x00", b"true", b"OTTO"),
    ".otf": (b"OTTO", b"\x00\x01\x00\x00", b"true"),
}
FILE_TYPES = frozenset(MAGIC)
# what the file managers of macOS and Windows put into a zip by themselves: left out
_LITTER = {".DS_Store", "Thumbs.db", "desktop.ini"}
_GLOB = re.compile(r"[*?\[]")
_lock = threading.Lock()  # one import at a time picks a free name and takes it


class ShareError(ThemeError):
    """A theme that cannot be shared, or a file that is not a sound shared theme."""


def _mb(size: int) -> int:
    return size // (1024 * 1024)


def _lines(data: Any) -> set[str]:
    """Every line of every text in theme.json's content, once."""
    found: set[str] = set()
    stack = [data]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, str):
            found.update(line.strip() for line in value.splitlines() if line.strip())
    return found


def _own_file(folder: Path, base: Path, name: str) -> str | None:
    """``name`` as a file of the theme folder: a picture or font inside it, reached without a
    link, by the path it has there ("assets/back.png"); None for anything else."""
    if len(name) > 255 or Path(name).suffix.lower() not in FILE_TYPES:
        return None
    try:
        target = resolve_asset(folder, name)
        if not target.is_file():
            return None
        found = target.relative_to(base).as_posix()
    except (ThemeError, OSError, ValueError):
        return None
    named = posixpath.normpath(name.replace("\\", "/"))
    # a link inside the folder resolves to another path than the one named: left out
    return named if os.path.normcase(found) == os.path.normcase(named) else None


def referenced_files(folder: Path, data: Any) -> list[str]:
    """The files in ``folder`` that the theme ``data`` (theme.json's content) names: the
    pictures and fonts named in any of its texts (an image's ``src``, a font, a line of a
    slideshow, ``photos/*.jpg`` for a folder of them), as paths inside the folder. Nothing
    outside it, nothing reached through a link, nothing of another kind."""
    base = folder.resolve()
    found: set[str] = set()
    globs = 0
    for line in sorted(_lines(data)):
        names = [line]
        if _GLOB.search(line):
            globs += 1
            if globs > MAX_GLOBS:
                continue
            try:  # (a whole path is no pattern pathlib takes: nothing)
                names = [path.relative_to(folder).as_posix() for path in folder.glob(line)]
            except (ValueError, NotImplementedError, OSError):
                continue
        for name in names:
            own = _own_file(folder, base, name)
            if own is not None:
                found.add(own)
    return sorted(found)


def _private_folders() -> list[str]:
    """The person's own folders as a text may name them: their home and the settings folder."""
    folders = [config_dir()]
    try:
        folders.append(Path.home())
    except RuntimeError:  # no home known
        pass
    names = set()
    for folder in folders:
        for text in (str(folder), folder.as_posix()):
            if len(text) > 3:  # not "/" or "C:\"
                names.add(text.casefold())
    return sorted(names)


def _whole_path(line: str) -> bool:
    """Whether a line names a whole path (or one that climbs out), not one in the folder."""
    if line.startswith(("/", "\\", "~")) or PureWindowsPath(line).drive:
        return True
    return ".." in re.split(r"[/\\]", line)


def _scrubbed(value: Any, private: list[str], pictures: bool = False) -> Any:
    """theme.json's content without a line that names one of the person's folders, and in the
    lists of pictures (``pictures``) without one that names a whole path: no other computer
    has them, and they say who made the theme and where."""
    if isinstance(value, dict):
        kind = (value.get("type"), value.get("module"))
        lists = {"image": {"slides"}, "module": {"items"} if kind[1] == "image" else set()}
        names = lists.get(kind[0], set())
        return {key: _scrubbed(item, private, key in names) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrubbed(item, private) for item in value]
    if not isinstance(value, str):
        return value

    def kept(line: str) -> bool:
        if any(name in line.casefold() for name in private):
            return False
        return not pictures or line.strip().startswith("@") or not _whole_path(line.strip())

    return "\n".join(line for line in value.split("\n") if kept(line))


def export_theme(name: str) -> tuple[bytes, str]:
    """The theme ``name`` (yours, a plugin's or a built-in one) as the bytes of a zip to share,
    and the zip's file name; :class:`ThemeError` for a theme that is not there or does not
    load, :class:`ShareError` for one whose files are too much for one file."""
    folder = find_theme(name)
    data = _scrubbed(load_theme(folder).to_dict(), _private_folders())
    files = referenced_files(folder, data)
    if sum((folder / rel).stat().st_size for rel in files) > MAX_UNPACKED:
        raise ShareError(
            t("The files of “{name}” come to more than {size} MB: too much to share as one file.",
              name=name, size=_mb(MAX_UNPACKED))
        )  # fmt: skip
    buffer = io.BytesIO()
    # (a file dated before 1980, as some packages date theirs, is dated 1980)
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as archive:
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        archive.writestr(f"{name}/{THEME_FILENAME}", text.encode("utf-8"))
        for rel in files:
            archive.write(folder / rel, f"{name}/{rel}")
    return buffer.getvalue(), f"{name}.zip"


def _not_a_zip() -> str:
    return t("This file is not a .zip: a theme is shared as a .zip of its folder.")


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-.")[:64].strip("-.")
    return slug if valid_theme_name(slug) else ""


def _checked(archive: zipfile.ZipFile) -> tuple[str, dict[str, zipfile.ZipInfo]]:
    """What a shared theme's zip holds, checked: the folder its files are in ("" for none), and
    its files by their path inside the theme. Nothing is written."""
    entries = archive.infolist()
    if len(entries) > MAX_FILES:
        raise ShareError(t("This .zip holds more than {count} files: too many for a theme.",
                           count=MAX_FILES))  # fmt: skip
    if sum(entry.file_size for entry in entries) > MAX_UNPACKED:
        raise ShareError(
            t("The theme's files come to more than {size} MB unpacked: a shared theme may be "
              "at most that.", size=_mb(MAX_UNPACKED))
        )  # fmt: skip
    files: dict[str, zipfile.ZipInfo] = {}
    for entry in entries:
        name = entry.filename
        parts = name.rstrip("/").split("/")
        if "\\" in name or any(part in ("", ".", "..") or ":" in part for part in parts):
            raise ShareError(t("“{name}” in the .zip leaves the theme's folder or names a whole "
                               "path: nothing was imported.", name=name))  # fmt: skip
        if stat.S_ISLNK(entry.external_attr >> 16):
            raise ShareError(t("“{name}” in the .zip is a link to another place: nothing was "
                               "imported.", name=name))  # fmt: skip
        if entry.flag_bits & 0x1:
            raise ShareError(t("“{name}” in the .zip is locked with a password: nothing was "
                               "imported.", name=name))  # fmt: skip
        if entry.is_dir() or parts[0] == "__MACOSX" or parts[-1] in _LITTER:
            continue
        files[name] = entry
    if THEME_FILENAME in files:
        top = ""
    else:
        tops = {name.split("/", 1)[0] for name in files}
        top = tops.pop() if len(tops) == 1 else ""
        if not top or f"{top}/{THEME_FILENAME}" not in files:
            raise ShareError(t("There is no theme.json in this .zip: a theme is shared as a .zip "
                               "of its folder, with its theme.json."))  # fmt: skip
    inside = {name[len(top) + 1 :] if top else name: entry for name, entry in files.items()}
    for rel, entry in inside.items():
        if rel != THEME_FILENAME and PurePosixPath(rel).suffix.lower() not in FILE_TYPES:
            raise ShareError(
                t("“{name}” is not a file a theme carries (theme.json, pictures: PNG, JPEG, "
                  "GIF, WebP, fonts: TTF, OTF): nothing was imported.", name=entry.filename)
            )  # fmt: skip
    return top, inside


def _what_it_says(rel: str, content: bytes) -> bool:
    """Whether a picture or font is the kind its name says, by its first bytes."""
    suffix = PurePosixPath(rel).suffix.lower()
    if not content.startswith(MAGIC[suffix]):
        return False
    return suffix != ".webp" or content[8:12] == b"WEBP"


def _taken(name: str, themes: Path) -> bool:
    if (themes / name).exists():
        return True
    try:
        find_theme(name)  # a built-in or a plugin's theme of that name: yours would hide it
    except ThemeError:
        return False
    return True


def _free_name(wanted: str, themes: Path) -> str:
    """``wanted``, or with the first number after it that makes it free (``lumen-2``)."""
    name, n = wanted, 1
    while _taken(name, themes):
        n += 1
        ending = f"-{n}"
        name = wanted[: 64 - len(ending)].rstrip("-.") + ending
    return name


def import_theme(
    data: bytes, themes: Path | None = None, name: str = "", folder: str = ""
) -> dict[str, Any]:
    """Put a shared theme (the bytes of its zip) into the person's themes folder (``themes``):
    ``{"id": its folder's name, "name": the theme's, "files": how many pictures and fonts}``.
    The folder is named ``folder`` (the community's list names it), else after the one in the
    zip, else ``name`` (the file's), else the theme's name, with a number where it is taken.
    :class:`ShareError` before anything is in place when the file is not a sound shared
    theme."""
    themes = themes or user_themes_dir()
    if len(data) > MAX_ZIP:
        raise ShareError(t("The file is larger than {size} MB: a shared theme may be at most that.",
                           size=_mb(MAX_ZIP)))  # fmt: skip
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise ShareError(_not_a_zip()) from exc
    staging = themes / f".import-{secrets.token_hex(6)}"  # hidden: no list shows it
    try:
        with archive:
            top, files = _checked(archive)
            entry = files[THEME_FILENAME]
            if entry.file_size > MAX_THEME_JSON:
                raise ShareError(t("Its theme.json is larger than {size} MB: more than a theme "
                                   "needs.", size=_mb(MAX_THEME_JSON)))  # fmt: skip
            try:  # read as the panel reads it (load_theme)
                raw = json.loads(archive.read(entry).decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError) as exc:
                raise ShareError(t("Its theme.json cannot be read: {error}", error=exc)) from exc
            try:
                theme = parse_theme(raw, root=staging)
            except (ThemeError, RecursionError) as exc:
                raise ShareError(
                    t("Its theme.json is not a theme Libre Panel can show: {error}", error=exc)
                ) from exc
            staging.mkdir(parents=True)
            for rel, info in files.items():
                target = staging / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
        kept = set(referenced_files(staging, raw))
        for rel in files:
            path = staging / rel
            if rel == THEME_FILENAME or rel in kept:
                if rel != THEME_FILENAME and not _what_it_says(rel, path.read_bytes()):
                    raise ShareError(t("“{name}” is not the picture or font its name says: "
                                       "nothing was imported.", name=rel))  # fmt: skip
                continue
            path.unlink()
        for place, _subs, _files in sorted(os.walk(staging), reverse=True):  # emptied folders
            if Path(place) != staging and not os.listdir(place):
                os.rmdir(place)
        given = PurePosixPath(name.replace("\\", "/")).name
        given = given[:-4] if given.lower().endswith(".zip") else given
        wanted = next(
            (
                n
                for n in (folder, top, given, _slug(given), _slug(theme.name))
                if valid_theme_name(n)
            ),
            "theme",
        )
        with _lock:
            chosen = _free_name(wanted, themes)
            os.rename(staging, themes / chosen)
    except (zipfile.BadZipFile, EOFError, zlib.error) as exc:  # a damaged entry, read
        raise ShareError(_not_a_zip()) from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return {"id": chosen, "name": theme.name, "files": len(kept)}


def inspect_shared(data: bytes) -> dict[str, Any]:
    """What a shared theme's zip holds, checked as an import checks it and kept nowhere:
    ``{"name", "size": [width, height], "author", "license"}``; :class:`ShareError` as
    :func:`import_theme` has it."""
    with tempfile.TemporaryDirectory() as place:
        done = import_theme(data, themes=Path(place))
        theme = load_theme(Path(place) / done["id"])
    return {"name": theme.name, "size": [theme.width, theme.height], "author": theme.author,
            "license": theme.license}  # fmt: skip
