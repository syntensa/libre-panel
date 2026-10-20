"""One file with the person's settings and themes, to take to another computer or to keep
(Settings → System → Backup).

A backup is a zip: ``config.toml`` and the folder of the person's own themes
(``themes/<name>/...``), with a small ``libre-panel-backup.json`` that says what it is.
Restoring it checks everything before it writes anything: only those paths, nothing outside
them, a config.toml that Libre Panel reads, and sizes within bounds. The config.toml it
replaces is kept beside it (``config.toml.before-restore``); the files of the backup's themes
are written over those of the same name, and the other themes stay.

config.toml holds what the person set there, keys of services among it (Home Assistant's): a
backup is theirs to keep, not to share.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from libre_panel import __version__
from libre_panel.config import (
    CONFIG_FILENAME,
    ConfigError,
    _write,
    config_dir,
    parse_config,
    user_themes_dir,
)
from libre_panel.i18n import t

MANIFEST = "libre-panel-backup.json"
KIND = "libre-panel-backup"
MAX_TOTAL = 200 * 1024 * 1024  # the files of a backup together, unpacked
MAX_FILES = 5000
KEPT = CONFIG_FILENAME + ".before-restore"
_LEFT_OUT = (".npz", ".tmp", ".pyc")  # made again by themselves (drawn layers), or half-written


class BackupError(ValueError):
    pass


def _theme_files(themes: Path) -> list[Path]:
    if not themes.is_dir():
        return []
    files = []
    for path in sorted(themes.rglob("*")):
        relative = path.relative_to(themes)
        if not path.is_file() or path.is_symlink() or path.suffix in _LEFT_OUT:
            continue
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        files.append(path)
    return files


def make_backup(config_path: Path | None = None, themes: Path | None = None) -> bytes:
    """The backup of this computer's settings and own themes, as the bytes of a zip."""
    config_path = config_path or config_dir() / CONFIG_FILENAME
    themes = themes or user_themes_dir()
    buffer = io.BytesIO()
    names = sorted({path.relative_to(themes).parts[0] for path in _theme_files(themes)})
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        manifest = {"kind": KIND, "version": __version__, "made": time.strftime("%Y-%m-%d %H:%M"),
                    "themes": names}  # fmt: skip
        archive.writestr(MANIFEST, json.dumps(manifest, indent=2))
        if config_path.is_file():
            archive.write(config_path, CONFIG_FILENAME)
        for path in _theme_files(themes):
            archive.write(path, "themes/" + path.relative_to(themes).as_posix())
    return buffer.getvalue()


def _checked(archive: zipfile.ZipFile) -> tuple[bytes | None, dict[str, list[tuple[str, bytes]]]]:
    """What a backup holds, read and checked: config.toml, and the files of each theme by its
    name (paths inside the theme's folder). Nothing is written yet."""
    from libre_panel.theme.model import valid_theme_name

    entries = archive.infolist()
    if len(entries) > MAX_FILES:
        raise BackupError(t("this is not a backup of Libre Panel"))  # (that many files)
    if sum(entry.file_size for entry in entries) > MAX_TOTAL:
        raise BackupError(t("the backup is too large"))
    try:
        manifest = json.loads(archive.read(MANIFEST))
    except (KeyError, ValueError) as exc:
        raise BackupError(t("this is not a backup of Libre Panel")) from exc
    if not isinstance(manifest, dict) or manifest.get("kind") != KIND:
        raise BackupError(t("this is not a backup of Libre Panel"))
    config: bytes | None = None
    themes: dict[str, list[tuple[str, bytes]]] = {}
    for entry in entries:
        name = entry.filename
        if entry.is_dir() or name == MANIFEST:
            continue
        if name == CONFIG_FILENAME:
            config = archive.read(entry)
            continue
        parts = PurePosixPath(name).parts
        if (
            len(parts) < 3
            or parts[0] != "themes"
            or "\\" in name
            or name.startswith("/")
            or any(part in ("", ".", "..") or ":" in part for part in parts)
            or not valid_theme_name(parts[1])
        ):
            raise BackupError(t("{name}: not a file a backup of Libre Panel holds", name=name))
        themes.setdefault(parts[1], []).append(("/".join(parts[2:]), archive.read(entry)))
    if config is not None:
        try:
            text = config.decode("utf-8")
            import tomllib

            parse_config(tomllib.loads(text))
        except (UnicodeDecodeError, ValueError, ConfigError) as exc:
            raise BackupError(t("its config.toml cannot be read: {error}", error=exc)) from exc
    return config, themes


def restore_backup(
    data: bytes, config_path: Path | None = None, themes: Path | None = None
) -> dict[str, Any]:
    """Put a backup in place: its config.toml (the one before kept beside it) and its themes.
    ``{"config": whether it had one, "themes": the names}``; :class:`BackupError` before
    anything is written when it is not a backup of Libre Panel, or not a sound one."""
    config_path = config_path or config_dir() / CONFIG_FILENAME
    themes = themes or user_themes_dir()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            config, found = _checked(archive)
    except zipfile.BadZipFile as exc:
        raise BackupError(t("this is not a backup of Libre Panel")) from exc
    for name, files in found.items():
        folder = themes / name
        for relative, content in files:
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    if config is not None:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if config_path.is_file():
            (config_path.with_name(KEPT)).write_bytes(config_path.read_bytes())
        _write(config_path, config.decode("utf-8"))
    return {"config": config is not None, "themes": sorted(found)}
