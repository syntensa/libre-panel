"""Who may talk to the editor's server.

The server listens on 127.0.0.1, which every program of every user of this PC can reach, and it
can do a lot: it writes config.toml, and the running Libre Panel starts the programs and loads
the libraries that file names, with the highest rights when it was set up so. So each run makes a
secret token, and a request must carry it in the ``X-Libre-Panel`` header (``server.py``) but for
the app's own files.

Only the person who started Libre Panel gets the token:

- A window, the tray, ``libre-panel editor`` and the browser fallback hand it to the app in the
  address, after a ``#``: a browser never sends that part to a server, and the app takes it out
  of the address at once (``static/token.js``).
- A program that starts later (the window of the tray, ``libre-panel quit``) reads it from a file
  in the settings folder that only this user can open: mode 600 in a folder of mode 700 on Linux,
  and on Windows the user's profile, whose ACLs keep the others out.

A window process is not given the token on its command line, which the other users of a Linux PC
can read: it gets it in its environment, or finds it in the file. (A browser has no other way
than the address it is asked to open.)
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from libre_panel.config import CONFIG_FILENAME, config_dir
from libre_panel.i18n import t

log = logging.getLogger(__name__)

HEADER = "X-Libre-Panel"
NAME = "token"  # in the address' fragment (``#token=...``) and in a picture's query (``?token=``)
ENV = "LIBRE_PANEL_TOKEN"  # how a program that starts a window tells it the token
PRIVATE_DIR = "private"  # in the settings folder: opened by this user only
TOKEN_FILE = "editor.json"  # in it: the pid, the port and the token of the running editor
_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,200}")
# this run's token, and the key of the notices from other programs (notify.py)
_IN_TEXT = re.compile(r"([?&#](?:" + NAME + r"|key)=)[A-Za-z0-9_-]+")


def new_token() -> str:
    """A token for one run of the server: 256 bits, in the characters of an address."""
    return secrets.token_urlsafe(32)


def matches(given: str | None, expected: str) -> bool:
    """Whether a request's token is the right one. The time this takes does not tell how much
    of it was right."""
    if not given or not expected:
        return False
    # as bytes: compare_digest refuses text that is not ASCII, and anyone may send some
    return secrets.compare_digest(given.encode("utf-8", "replace"), expected.encode("utf-8"))


# -- the token in an address ---------------------------------------------------------------------


def split_token(url: str) -> tuple[str, str | None]:
    """``url`` without the token in its fragment, and the token (None: there is none).

    The fragment is the app's route and the token, ``#/design&token=...`` or ``#token=...``."""
    head, _sep, fragment = url.partition("#")
    token = None
    kept = []
    for part in fragment.split("&") if fragment else []:
        key, _, value = part.partition("=")
        if key == NAME:
            token = token or (value if _TOKEN.fullmatch(value) else None)
        else:
            kept.append(part)
    rest = "&".join(kept)
    return (f"{head}#{rest}" if rest else head), token


def with_token(url: str, token: str | None) -> str:
    """``url`` with ``token`` in its fragment, after the route if it has one. Without a token it
    is ``url`` as it is."""
    if not token:
        return url
    page, _old = split_token(url)
    return f"{page}{'&' if '#' in page else '#'}{NAME}={token}"


def hide_token(text: str) -> str:
    """``text`` (a line of the log) with the tokens in it blanked."""
    return _IN_TEXT.sub(r"\1…", text)


# -- the file for the programs that start later ----------------------------------------------------


def token_file() -> Path:
    return config_dir() / PRIVATE_DIR / TOKEN_FILE


def _private_dir(folder: Path) -> None:
    """Make ``folder`` (and nothing above it), for this user only. Raises OSError when it cannot
    be made so, e.g. it belongs to somebody else."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "posix":
        return  # Windows: what the folder inherits from the user's profile
    info = folder.stat()
    if info.st_uid != os.geteuid():
        raise PermissionError(f"{folder} belongs to another user")
    if info.st_mode & 0o077:
        folder.chmod(0o700)


def publish(token: str, port: int) -> Path | None:
    """Write the token of the editor on ``port`` where the programs of this user find it. None
    (and a line in the log) when that cannot be done: the app works, but `libre-panel quit` and
    a window started later cannot sign in."""
    path = token_file()
    try:
        _private_dir(path.parent)
        # a temporary file that only this user can open, then a rename: the token is never
        # readable by others, not even for a moment, and a reader never meets half a file
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".editor-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump({"pid": os.getpid(), "port": port, "token": token}, file)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as exc:
        log.warning("the editor's token could not be written to %s: %s", path, exc)
        return None
    return path


def _read() -> dict | None:
    try:
        info = json.loads(token_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def forget(token: str) -> None:
    """Remove the file if it still holds ``token`` (a later run may have written its own)."""
    info = _read()
    if info and info.get("token") == token:
        token_file().unlink(missing_ok=True)


def inherited_token() -> str | None:
    """The token the program that started this one passed on in its environment (``ENV``). It
    is taken out of the environment, so that what this program starts does not inherit it."""
    token = os.environ.pop(ENV, "")
    return token if _TOKEN.fullmatch(token) else None


def read_token(url: str | None = None) -> str | None:
    """The token of the running editor, from the file it wrote. With ``url``, only when the file
    is about the server at that address, and when its program still runs."""
    info = _read()
    token = info.get("token") if info else None
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        return None
    if url is not None:
        try:
            port = urlsplit(url).port
        except ValueError:
            return None
        if port is None or info.get("port") != port:
            return None
        import psutil

        pid = info.get("pid")
        if not isinstance(pid, int) or not psutil.pid_exists(pid):
            return None  # a run that ended: its token opens nothing
    return token


# -- rights --------------------------------------------------------------------------------------


def elevated() -> bool:
    """Whether this program runs with administrator rights (Windows) or as root (elsewhere)."""
    if sys.platform == "win32":
        from libre_panel.autostart import is_admin

        return is_admin()
    geteuid = getattr(os, "geteuid", None)
    return geteuid is not None and geteuid() == 0


def folder_risk(folder: Path, home: Path | None = None, posix: bool | None = None) -> str:
    """Why a Libre Panel with the highest rights should not read its settings from ``folder``,
    in a few words; "" when nothing is wrong or nothing can be told. It runs with those rights
    what config.toml and the plugins in it name, so whoever else can write there can run a
    program as administrator. On Windows the profile of the user (``home``) keeps the others
    out; elsewhere (``posix``) the owner and the modes of the folder and of what is in it tell."""
    try:
        if os.name == "posix" if posix is None else posix:
            for path in (folder, folder / CONFIG_FILENAME, folder / "plugins"):
                if not path.exists():
                    continue
                info = path.stat()
                if info.st_uid != os.geteuid():
                    return t("it belongs to another user")
                if info.st_mode & 0o022:
                    return t("other users may write to it")
        elif not folder.resolve().is_relative_to((home or Path.home()).resolve()):
            return t("it is outside your user folder, where other users may be allowed to write")
    except OSError:
        pass
    return ""
