"""A computer and a Steam folder by hand, for the tests of the services: the
programs that run, the window in front, the microphone, the uptime."""

import os
from collections import Counter
from pathlib import Path

from PIL import Image

from libre_panel.services.machine import Program


class FakeMachine:
    """Has the methods of ``services.machine.Machine``; the test sets what is true."""

    def __init__(self, has_foreground=True):
        self.has_foreground = has_foreground
        self.running: list[Program] = []
        self.front = 0
        self.microphone = False
        self.uptime = 100_000.0
        self.looked = 0
        self.asked = Counter()  # how often each question was asked

    def start(self, name, pid, path=""):
        self.running.append(Program(name.lower(), pid, str(path)))
        return pid

    def end(self, pid):
        self.running = [p for p in self.running if p.pid != pid]

    def programs(self):
        self.looked += 1
        self.asked["programs"] += 1
        return list(self.running)

    def foreground_pid(self):
        return self.front if self.has_foreground else None

    def foreground_name(self):
        self.asked["foreground"] += 1
        return next((p.name for p in self.running if p.pid == self.front), None)

    def microphone_in_use(self):
        self.asked["microphone"] += 1
        return self.microphone

    def uptime_s(self):
        self.asked["uptime"] += 1
        return self.uptime


MANIFEST = """"AppState"
{
\t"appid"\t\t"%(appid)s"
\t"Universe"\t\t"1"
\t"name"\t\t"%(name)s"
\t"StateFlags"\t\t"4"
\t"installdir"\t\t"%(installdir)s"
}
"""


def make_library(path, *games):
    """A Steam library with the games installed: (appid, name, installdir) each."""
    steamapps = Path(path) / "steamapps"
    (steamapps / "common").mkdir(parents=True, exist_ok=True)
    for appid, name, installdir in games:
        manifest = MANIFEST % {"appid": appid, "name": name, "installdir": installdir}
        (steamapps / f"appmanifest_{appid}.acf").write_text(manifest, encoding="utf-8")
        (steamapps / "common" / installdir).mkdir(parents=True, exist_ok=True)
    return Path(path)


def make_steam(root, *games, libraries=()):
    """Steam's folder with ``games`` in it; ``libraries`` are other folders it lists."""
    root = make_library(root, *games)
    entries = [str(root), *map(str, libraries)]
    body = ""
    for n, path in enumerate(entries):  # backslashes are doubled in Steam's files
        escaped = path.replace("\\", "\\\\")
        body += f'\t"{n}"\n\t{{\n\t\t"path"\t\t"{escaped}"\n\t\t"label"\t\t""\n\t}}\n'
    vdf = root / "steamapps" / "libraryfolders.vdf"
    vdf.write_text(f'"libraryfolders"\n{{\n{body}}}\n', encoding="utf-8")
    return root


def game_path(library, installdir, exe="game.exe"):
    return str(Path(library) / "steamapps" / "common" / installdir / exe)


def make_logo(
    root, appid, size=(640, 360), box=(100, 120, 540, 240), name="logo.png", sub="", at=None
):
    """A Steam logo: transparent all round, with ``box`` opaque. ``sub`` is the folder Steam
    keeps one version of a game's pictures in (none: the older layout, ``<appid>_logo.png``)."""
    cache = Path(root) / "appcache" / "librarycache"
    if sub:
        path = cache / str(appid) / sub / name
    else:
        path = cache / f"{appid}_{name}"
    path.parent.mkdir(parents=True, exist_ok=True)
    picture = Image.new("RGBA", size, (0, 0, 0, 0))
    picture.paste(
        Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (200, 40, 40, 255)), box[:2]
    )
    if name.endswith(".jpg"):
        picture = picture.convert("RGB")
    picture.save(path)
    if at is not None:
        os.utime(path, (at, at))
    return path
