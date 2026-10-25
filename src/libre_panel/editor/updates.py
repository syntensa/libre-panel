"""Whether a newer Libre Panel is out, for Start to say so (views/home/update.js), and the
update itself on a click (:class:`Install`).

Only where config.toml's ``check_updates`` is on (Start asks once; Settings → System switches
it): at most once a day Libre Panel asks GitHub for its latest release. That request carries
what any does (the computer's address, "libre-panel/<version>"), nothing of the person or the
panel. ``updates.json`` beside config.toml keeps the answer and when it came, so that the
window and a browser ask once a day between them, and the version whose card was closed
("later"): its card does not come again, the next version's does. Only releases are offered;
a pre-release that runs hears of the pre-releases of its own minor version too (0.7.1rc2 after
0.7.1rc1), never of a draft.

"Update to 0.7.1" fetches the download for this kind of install from the release's own files
(github.com/syntensa/libre-panel/releases/download/, over HTTPS only) and checks it against the
release's SHA256SUMS (release.yml writes it; a release from before has only its size checked).
Nothing is installed until the second click, "Install and restart": the Windows setup then runs
without questions, the portable zip is unpacked into a new folder beside this one with the
settings, the AppImage takes the running one's place; each starts once this one has quit. The
Linux tarball is only fetched and its folder shown, to unpack by hand as the first time; a pip
install gets the command instead.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import logging
import ntpath
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePath
from typing import Any

from libre_panel import __version__
from libre_panel.autostart import WINDOWED_EXE
from libre_panel.config import CONFIG_FILENAME, PORTABLE_MARK, config_dir, portable_dir
from libre_panel.i18n import t

log = logging.getLogger(__name__)

UPDATES_FILENAME = "updates.json"
LATEST_URL = "https://api.github.com/repos/syntensa/libre-panel/releases/latest"
# a pre-release that runs looks at the latest releases, pre-releases among them
RELEASES_URL = "https://api.github.com/repos/syntensa/libre-panel/releases?per_page=30"
# where the files of the releases are: the only place an update is fetched from
RELEASE_FILES = "https://github.com/syntensa/libre-panel/releases/download/"
CHECKSUMS = "SHA256SUMS"  # among a release's files, as sha256sum writes it (release.yml)
DAY_S = 24 * 3600
RETRY_S = 3600  # after no answer: the next try an hour later, not at each look at Start
NOTES_CHARS = 600  # of the release notes, the start (their first paragraph says what is new)
VERSION = re.compile(
    r"v?(\d{1,4})\.(\d{1,4})(?:\.(\d{1,4}))?(?:\.(\d{1,4}))?(?:(a|b|rc)(\d{1,4}))?"
)
# where a pre-release stands among the releases of its version: before the release itself
STAGES = {"a": 0, "b": 1, "rc": 2, None: 3}


def version_key(text: str) -> tuple[int, int, int, int, int, int] | None:
    """``"v0.6.1"``, ``"0.6"``, a hotfix's ``"0.6.8.1"`` or a pre-release's ``"0.7.0rc1"``
    as numbers to compare (a pre-release before its release); None for what is no version."""
    found = VERSION.fullmatch(text.strip())
    if not found:
        return None
    major, minor, patch, fix, stage, number = found.groups()
    return (int(major), int(minor), int(patch or 0), int(fix or 0), STAGES[stage],
            int(number or 0))  # fmt: skip


def newer(latest: str, current: str = __version__) -> bool:
    """Whether ``latest`` is a later release than ``current`` (an unknown one is not)."""
    a, b = version_key(latest), version_key(current)
    return a is not None and b is not None and a > b


def prerelease(version: str) -> bool:
    """Whether ``version`` is a pre-release (``0.7.0rc1``)."""
    key = version_key(version)
    return key is not None and key[4] < STAGES[None]


def _offered(data: Any, current: str) -> bool:
    """Whether a release of GitHub's answer is one to offer: never a draft, a pre-release only
    to a pre-release that runs and only of its own minor version (0.7.1rc2 to 0.7.1rc1, not
    0.8.0rc1)."""
    if not isinstance(data, dict) or data.get("draft") or not isinstance(data.get("tag_name"), str):
        return False
    key, mine = version_key(data["tag_name"]), version_key(current)
    if key is None:
        return False
    if not (data.get("prerelease") or key[4] < STAGES[None]):
        return True
    return mine is not None and prerelease(current) and key[:2] == mine[:2]


def _pick(data: Any, current: str) -> Any:
    """The release of GitHub's answer to offer: the latest release itself, or of the list a
    pre-release asks for the highest version that is offered; None where there is none."""
    if isinstance(data, list):
        offered = [item for item in data if _offered(item, current)]
        return max(offered, key=lambda item: version_key(item["tag_name"]), default=None)
    return data if _offered(data, current) else None


def install_kind() -> str:
    """How this Libre Panel came: ``"setup"`` (the Windows setup), ``"portable"`` (the Windows
    .zip), ``"appimage"``, ``"linux"`` (the Linux .tar.gz) or ``"python"`` (pip and the like),
    which says the download that replaces it."""
    if not getattr(sys, "frozen", False):
        return "python"
    if sys.platform == "win32":
        # Inno Setup leaves its uninstaller beside the program it installed
        return "setup" if Path(sys.executable).with_name("unins000.exe").is_file() else "portable"
    if os.environ.get("APPIMAGE"):
        return "appimage"
    return "linux"


# the end of each download's name on a release, by how Libre Panel came
ASSET_ENDINGS = {
    "setup": "-windows-x64-setup.exe",
    "portable": "-windows-x64-portable.zip",
    "appimage": "-x86_64.AppImage",
    "linux": "-linux-x64.tar.gz",
}


# a name of a release's file, which an update keeps as the name of its download
FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _files(data: dict[str, Any], ending: str | None) -> tuple[dict[str, Any] | None, str]:
    """Of a release's files the one for this kind of install (``{"name", "url", "size"}``)
    and the address of its SHA256SUMS (""): only files of the release itself on GitHub."""
    own = f"{RELEASE_FILES}{data['tag_name'].strip()}/"
    asset, sums = None, ""
    for item in data.get("assets") or []:
        name = item.get("name") if isinstance(item, dict) else None
        url = item.get("browser_download_url") if isinstance(item, dict) else None
        if not (isinstance(name, str) and isinstance(url, str) and url.startswith(own)):
            continue
        if name == CHECKSUMS:
            sums = url
        elif ending and name.endswith(ending) and FILE_NAME.fullmatch(name):
            size = item.get("size")
            size = size if isinstance(size, int) and not isinstance(size, bool) else 0
            asset = {"name": name, "url": url, "size": max(size, 0)}
    return asset, sums


def _release(data: Any, kind: str, current: str = __version__) -> dict[str, Any] | None:
    """What Start shows of GitHub's answer: the version, its page, the start of its notes, the
    download for this kind of install ("" for pip: its command is shown instead) and, for
    "Update to", that file of the release with the address of its checksums."""
    data = _pick(data, current)
    if data is None:
        return None
    version = data["tag_name"].strip().removeprefix("v")
    if version_key(version) is None:
        return None
    page = data.get("html_url")
    if not isinstance(page, str) or not page.startswith("https://github.com/"):
        return None
    ending = ASSET_ENDINGS.get(kind)
    download = ""
    for asset in data.get("assets") or []:
        name = asset.get("name") if isinstance(asset, dict) else None
        url = asset.get("browser_download_url") if isinstance(asset, dict) else None
        if ending and isinstance(name, str) and name.endswith(ending) and isinstance(url, str):
            if url.startswith("https://github.com/"):
                download = url
    body = data.get("body") if isinstance(data.get("body"), str) else ""
    notes = body.strip().split("\n\n", 1)[0].replace("\n", " ").strip()[:NOTES_CHARS]
    published = data.get("published_at") if isinstance(data.get("published_at"), str) else ""
    asset, sums = _files(data, ending)
    return {
        "version": version,
        "page": page,
        "download": download,
        "notes": notes,
        "published": published,
        "asset": asset,
        "sums": sums,
    }


def fetch_latest(timeout: float = 10) -> Any:
    """GitHub's answer on the latest release (drafts and pre-releases are never it); where a
    pre-release runs, the list of the latest releases, of which :func:`_pick` takes one."""
    request = urllib.request.Request(
        RELEASES_URL if prerelease(__version__) else LATEST_URL,
        headers={
            "User-Agent": f"libre-panel/{__version__}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


class Updates:
    def __init__(
        self,
        config_path: Path | None = None,
        fetch: Callable[[], Any] = fetch_latest,
        clock: Callable[[], float] = time.time,
        kind: Callable[[], str] = install_kind,
    ) -> None:
        config = config_path or config_dir() / CONFIG_FILENAME
        self.path = config.parent / UPDATES_FILENAME
        self.fetch = fetch
        self.clock = clock
        self.kind = kind
        self.lock = threading.Lock()  # one ask at a time: the window and a browser look alike

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        if not self.path.parent.is_dir():
            return  # a first start: nothing is kept yet
        try:
            self.path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        except OSError as exc:
            log.debug("updates.json not written: %s", exc)

    def _due(self, data: dict[str, Any]) -> bool:
        asked = data.get("asked")
        if not isinstance(asked, int | float):
            return True
        wait = RETRY_S if data.get("error") else DAY_S
        return not 0 <= self.clock() - asked < wait

    def _ask(self, data: dict[str, Any]) -> dict[str, Any]:
        kind = self.kind()
        try:
            latest = _release(self.fetch(), kind)
            error = "" if latest else "GitHub's answer named no release"
        except (OSError, ValueError, http.client.HTTPException) as exc:
            latest, error = None, str(getattr(exc, "reason", exc)) or type(exc).__name__
            log.info("no word on a newer Libre Panel: %s", error)
        data = {**data, "asked": self.clock(), "error": error}
        if latest:
            data["latest"] = latest
        self._write(data)
        return data

    def state(self, check: bool | None, now: bool = False) -> dict[str, Any]:
        """``{"check", "current", "kind", "latest", "newer", "asked", "error", "later"}``.
        GitHub is asked only where ``check`` is on, and only once a day (``now``: at once, as
        "Look now" in Settings asks)."""
        with self.lock:
            data = self._read()
            if check and (now or self._due(data)):
                data = self._ask(data)
        latest = data.get("latest") if isinstance(data.get("latest"), dict) else None
        later = data.get("later") if isinstance(data.get("later"), str) else ""
        return {
            "check": check,
            "current": __version__,
            "kind": self.kind(),
            "latest": latest if check else None,
            "newer": bool(check and latest and newer(str(latest.get("version", "")))),
            "asked": data.get("asked") if isinstance(data.get("asked"), int | float) else None,
            "error": data.get("error") if check and isinstance(data.get("error"), str) else "",
            "later": later,
        }

    def later(self, version: str, check: bool | None) -> dict[str, Any]:
        """The card of ``version`` was closed: Start shows it no more (a later one it does)."""
        if version_key(version) is None:
            raise ValueError("later is a version such as 0.7.0")
        with self.lock:
            self._write({**self._read(), "later": version})
        return self.state(check)


# -- "Update to 0.7.1": the download, its check, and putting it in place ---------------------

# what "Install and restart" does with the download, by how Libre Panel came (pip: no button)
HOW = {"setup": "setup", "portable": "portable", "appimage": "appimage", "linux": "open"}
# Inno Setup without questions, as packaging/windows/test_installer.py runs it; it keeps the
# folder, the tasks and the way (for all users or for me only) of the version before
SETUP_FLAGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
CHUNK = 256 * 1024
SUMS_LIMIT = 1 << 20  # a checksum file is a few lines
SUM_LINE = re.compile(r"([0-9a-fA-F]{64}) [ *](.+)")
BUSY = ("downloading", "verifying", "installing")
IDLE: dict[str, Any] = {
    "state": "idle",
    "version": "",
    "how": "",
    "file": "",
    "where": "",
    "done": 0,
    "total": 0,
    "checked": False,
    "error": "",
}
# the shell waits for this Libre Panel to have quit (a minute at most), then starts the new one
WAIT_THEN_START = (
    'i=0; while kill -0 "$1" 2>/dev/null && [ "$i" -lt 120 ]; do sleep 0.5; i=$((i+1)); done; '
    'exec "$2"'
)


class UpdateError(ValueError):
    pass


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    """GitHub sends a download on to where it keeps the files: over HTTPS only."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise urllib.error.URLError(f"sent on to {newurl}, which is not HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_https(url: str, timeout: float = 30) -> Any:
    """The answer to a GET of ``url``, an HTTPS address, and of every place it is sent on to."""
    if urllib.parse.urlsplit(url).scheme != "https":
        raise urllib.error.URLError(f"{url} is not HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": f"libre-panel/{__version__}"})
    return urllib.request.build_opener(_HttpsOnly).open(request, timeout=timeout)


def _quoted(text: str) -> str:
    """``text`` as a string of PowerShell, which takes the typographic quotes for ' too."""
    return "'" + re.sub("(['\u2018\u2019\u201a\u201b])", r"\1\1", text) + "'"


def _powershell(script: str) -> list[str]:
    """Windows PowerShell running ``script``, given encoded: no path is quoted on a command
    line."""
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    program = ntpath.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [program, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
            "-EncodedCommand", encoded]  # fmt: skip


def restart_command(
    how: str, pid: int, program: PurePath, setup: PurePath | None = None, sha256: str = ""
) -> list[str]:
    """What starts ``program`` once the process ``pid`` (this Libre Panel) has quit, run on its
    own: on Windows PowerShell, which first runs the ``setup`` without questions where there is
    one and it is still the file that was checked (``sha256``: Windows may give it administrator
    rights), then starts the program in its folder (the new one, or the one there was where the
    setup was cancelled) and removes the setup's folder; on Linux the shell."""
    if how not in ("setup", "portable"):
        return ["/bin/sh", "-c", WAIT_THEN_START, "libre-panel-update", str(pid), str(program)]
    lines = [
        "$ErrorActionPreference = 'SilentlyContinue'",
        f"Wait-Process -Id {int(pid)} -Timeout 60",
    ]
    if setup is not None:
        flags = ",".join(_quoted(flag) for flag in SETUP_FLAGS)
        file, digest = _quoted(str(setup)), _quoted(sha256)
        run = f"Start-Process -FilePath {file} -ArgumentList {flags} -Wait"
        lines.append(f"if ((Get-FileHash -Algorithm SHA256 -LiteralPath {file}).Hash -eq {digest}) "
                     f"{{ {run} }}")  # fmt: skip
    folder = _quoted(str(program.parent))
    lines.append(f"Start-Process -FilePath {_quoted(str(program))} -WorkingDirectory {folder}")
    if setup is not None:
        lines.append(f"Remove-Item -LiteralPath {_quoted(str(setup.parent))} -Recurse -Force")
    return _powershell("\n".join(lines))


def _spawn(command: list[str], cwd: Path) -> None:
    """Start ``command`` on its own, without what this program's bundle put in the environment
    (PyInstaller's, the AppImage's): what it starts is another program."""
    from libre_panel.linux_rights import clean_environment
    from libre_panel.window import _detached

    env = {**clean_environment(os.environ), "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    subprocess.Popen(command, cwd=cwd, env=env, **_detached())  # noqa: S603 - built here


def _show(folder: Path) -> None:
    from libre_panel.tray import open_path

    open_path(folder)


def unpack_portable(archive: Path, program: Path, version: str) -> Path:
    """The portable zip unpacked into a new folder beside the folder of ``program`` (the running
    copy's), ``libre-panel-0.7.1`` (``-2`` where that is taken), with the settings of its
    ``data`` folder: Windows keeps the files of a running program, so they are not replaced.
    The running copy stays as it is, to delete once the new one runs. The new folder."""
    from libre_panel.instance import LOCK_FILENAME
    from libre_panel.service import INSTANCE_FILENAME

    running = program.resolve().parent
    target, number = running.parent / f"libre-panel-{version}", 2
    while target.exists():
        target, number = running.parent / f"libre-panel-{version}-{number}", number + 1
    part = target.with_name(target.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    try:
        with zipfile.ZipFile(archive) as zipped:
            for name in zipped.namelist():
                if not (part / name).resolve().is_relative_to(part.resolve()):
                    raise UpdateError(
                        t("The zip is not a portable Libre Panel: it is not installed.")
                    )
            zipped.extractall(part)
        if not (part / WINDOWED_EXE).is_file() or not (part / PORTABLE_MARK).is_file():
            raise UpdateError(t("The zip is not a portable Libre Panel: it is not installed."))
        data = portable_dir(str(program))
        if data is None:  # this copy keeps its settings in the user's folder: the new one too
            (part / PORTABLE_MARK).unlink()
        elif data.is_dir():  # (the running app's lock and address are its own)
            ignore = shutil.ignore_patterns(LOCK_FILENAME, INSTANCE_FILENAME)
            shutil.copytree(data, part / data.name, ignore=ignore)
        part.rename(target)
    except BaseException:
        shutil.rmtree(part, ignore_errors=True)
        raise
    return target


def move_autostart(old: Path, new: Path, autostart: Any = None) -> None:
    """A portable copy that starts at login: Windows' Run value names the new folder's program
    from now on. (A start with the highest rights needs an administrator to change: the log
    says so.)"""
    from libre_panel.autostart import APP_NAME, Autostart, same_program

    autostart = autostart or Autostart()
    if autostart.kind != "windows":
        return
    try:
        value = (autostart.registry.get(APP_NAME) or "").strip()
        if value.startswith('"'):
            named, _, rest = value[1:].partition('"')
        else:
            named, _, rest = value.partition(" ")
            rest = f" {rest}" if rest else ""
        if value and same_program(named, str(old / WINDOWED_EXE)):
            command = subprocess.list2cmdline([str(new / WINDOWED_EXE)]) + rest
            autostart.registry.set(APP_NAME, command)
        elif autostart.elevated():
            log.warning("the start with the highest rights still names %s: set it up again", old)
    except OSError as exc:  # (the update goes on: autostart is set again in Settings)
        log.warning("autostart still names %s: %s", old, exc)


def _words(exc: BaseException) -> str:
    """What went wrong, as the person is told."""
    if isinstance(exc, UpdateError):
        return str(exc)
    reason = str(getattr(exc, "reason", "") or exc) or type(exc).__name__
    return t("The download did not work: {error}", error=reason)


class Install:
    """The button "Update to 0.7.1": the download of a newer version for this kind of install,
    checked, and on a second click put in place. ``state()``: idle, downloading (``done`` of
    ``total`` bytes), verifying, ready (``checked``: against the release's SHA256SUMS),
    installing or error (``error``, in words)."""

    def __init__(
        self,
        kind: Callable[[], str] = install_kind,
        open_url: Callable[[str], Any] = open_https,
        spawn: Callable[[list[str], Path], None] = _spawn,
        show: Callable[[Path], None] = _show,
        temp: Path | None = None,
    ) -> None:
        self.kind = kind
        self.open_url = open_url
        self.spawn = spawn
        self.show = show
        self.temp = temp  # where a download goes that is not beside the AppImage or in Downloads
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self._state = dict(IDLE)
        self._file: Path | None = None  # the download, once it is checked
        self._sha256 = ""  # its SHA-256

    def state(self) -> dict[str, Any]:
        with self.lock:
            return dict(self._state)

    def _set(self, **changes: Any) -> None:
        with self.lock:
            self._state.update(changes)

    def _folder(self, how: str) -> Path:
        """Where the download goes: beside the running AppImage (it is renamed into its place),
        the tarball into Downloads (to unpack from), the rest into a folder of its own."""
        if how == "appimage":
            image = os.environ.get("APPIMAGE", "")
            folder = Path(image).parent if image else None
            if folder is None or not os.access(folder, os.W_OK):
                raise UpdateError(t("The AppImage's folder {folder} cannot be written to: download "
                                    "the new one from its page and put it there yourself.",
                                    folder=folder or "?"))  # fmt: skip
            return folder
        downloads = Path.home() / "Downloads"
        if how == "open" and downloads.is_dir() and os.access(downloads, os.W_OK):
            return downloads
        try:
            return Path(tempfile.mkdtemp(prefix="libre-panel-update-", dir=self.temp))
        except OSError as exc:
            raise UpdateError(_words(exc)) from exc

    def download(self, latest: dict[str, Any] | None) -> dict[str, Any]:
        """Fetch the file of ``latest`` (Updates.state's, None where nothing is newer) for this
        kind of install, in the background; ``state()`` says how far it is."""
        how = HOW.get(self.kind(), "")
        version = str((latest or {}).get("version", ""))
        asset = (latest or {}).get("asset")
        if not latest or not newer(version):
            raise UpdateError(t("There is no newer version to update to."))
        if not how or not isinstance(asset, dict):
            raise UpdateError(t("There is no download of Libre Panel {version} for this kind of "
                                "install: get it from its page.", version=version))  # fmt: skip
        own = (f"{RELEASE_FILES}v{version}/", f"{RELEASE_FILES}{version}/")
        url, name, size = asset.get("url"), asset.get("name"), asset.get("size")
        if not (isinstance(url, str) and url.startswith(own) and isinstance(name, str)
                and FILE_NAME.fullmatch(name)):  # fmt: skip
            raise UpdateError(t("That download is not one of Libre Panel's releases on GitHub: "
                                "it is not fetched."))  # fmt: skip
        sums = latest.get("sums") if isinstance(latest.get("sums"), str) else ""
        sums = sums if sums.startswith(own) else ""
        total = size if isinstance(size, int) and size > 0 else 0
        with self.lock:  # (one download at a time: a second click is told the first one's)
            if self._state["state"] in BUSY:
                return dict(self._state)
            folder = self._folder(how)
            self._forget()
            self._state = {**IDLE, "state": "downloading", "version": version, "how": how,
                           "file": name, "where": str(folder), "total": total}  # fmt: skip
            started = dict(self._state)
        self.thread = threading.Thread(
            target=self._fetch, args=(url, sums, folder, name, total), name="update", daemon=True
        )
        self.thread.start()
        return started

    def _forget(self) -> None:
        """An earlier download that was not installed goes (not the tarball in Downloads)."""
        earlier, self._file = self._file, None
        if earlier is not None and self._state["how"] in ("setup", "portable"):
            shutil.rmtree(earlier.parent, ignore_errors=True)
        elif earlier is not None and self._state["how"] == "appimage":
            earlier.unlink(missing_ok=True)

    def _fetch(self, url: str, sums: str, folder: Path, name: str, size: int) -> None:
        part = folder / f".{name}.part"
        digest, done = hashlib.sha256(), 0
        try:
            with self.open_url(url) as response, part.open("wb") as out:
                length = str(response.headers.get("Content-Length") or "")
                total = size or (int(length) if length.isdigit() else 0)
                self._set(total=total)
                while chunk := response.read(CHUNK):
                    done += len(chunk)
                    if total and done > total:
                        raise UpdateError(t("The download is longer than GitHub says: it is not "
                                            "installed."))  # fmt: skip
                    out.write(chunk)
                    digest.update(chunk)
                    self._set(done=done)
            self._set(state="verifying")
            if total and done != total:
                raise UpdateError(t("The download broke off ({done} of {total} bytes): try again.",
                                    done=done, total=total))  # fmt: skip
            if sums and self._sum(sums, name) != digest.hexdigest():
                raise UpdateError(t("The download is not the file the release names: its SHA-256 "
                                    "differs. It is not installed; try again."))  # fmt: skip
            final = folder / name
            if self._state["how"] == "appimage":
                if final == Path(os.environ.get("APPIMAGE", "")):
                    final = folder / f"{name}.new"  # (the running one is replaced only on a click)
                part.chmod(0o755)
            os.replace(part, final)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            log.warning("the update was not downloaded: %s", exc)
            part.unlink(missing_ok=True)
            return self._set(state="error", error=_words(exc))
        with self.lock:
            self._file, self._sha256 = final, digest.hexdigest()
            self._state.update(state="ready", checked=bool(sums))

    def _sum(self, url: str, name: str) -> str:
        """The SHA-256 the release's checksums give ``name``."""
        with self.open_url(url) as response:
            text = response.read(SUMS_LIMIT + 1)
        for line in text[:SUMS_LIMIT].decode("utf-8", "replace").splitlines():
            found = SUM_LINE.fullmatch(line.strip())
            if found and found[2] == name:
                return found[1].lower()
        raise UpdateError(t("The release's checksums do not name {name}: it is not installed.",
                            name=name))  # fmt: skip

    def install(self, quit: Callable[[], Any] | None) -> dict[str, Any]:
        """Put the checked download in place and start it, after ``quit`` (the running app's):
        the setup, the portable zip and the AppImage; the tarball's folder is only shown."""
        with self.lock:  # (a second click finds it installing)
            state, file = dict(self._state), self._file
            if state["state"] != "ready" or file is None:
                raise UpdateError(t("Nothing is downloaded to install yet."))
            if state["how"] != "open" and quit is None:
                raise UpdateError(t("Only the running app installs an update: start Libre "
                                    "Panel and update from there."))  # fmt: skip
            if state["how"] != "open":
                self._state["state"] = "installing"
        if state["how"] == "open":
            try:
                self.show(file.parent)
            except OSError as exc:
                raise UpdateError(str(exc)) from exc
            return state
        self.thread = threading.Thread(
            target=self._put_in_place, args=(state, file, quit), name="update", daemon=True
        )
        self.thread.start()
        return {**state, "state": "installing"}

    def _put_in_place(self, state: dict[str, Any], file: Path, quit: Callable[[], Any]) -> None:
        program, pid = Path(sys.executable), os.getpid()
        try:
            if state["how"] == "setup":  # (PowerShell runs outside the folder it removes)
                app = program.with_name(WINDOWED_EXE)
                command = restart_command("setup", pid, app, file, self._sha256)
                cwd = file.parent.parent
            elif state["how"] == "portable":
                cwd = unpack_portable(file, program, state["version"])
                shutil.rmtree(file.parent, ignore_errors=True)
                move_autostart(program.resolve().parent, cwd)
                command = restart_command("portable", pid, cwd / WINDOWED_EXE)
                self._set(where=str(cwd))
            else:
                image = Path(os.environ.get("APPIMAGE", ""))
                if not image.name:
                    raise UpdateError(t("Nothing is downloaded to install yet."))
                os.replace(file, image)  # the running one keeps its own until it quits
                command, cwd = restart_command("appimage", pid, image), image.parent
            self.spawn(command, cwd)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            log.warning("the update was not put in place: %s", exc)
            error = str(exc) or type(exc).__name__
            if not isinstance(exc, UpdateError):
                error = t("The update could not be put in place: {error}", error=error)
            return self._set(state="error", error=error)
        log.info("updating to %s: %s", state["version"], command[0])
        quit()
