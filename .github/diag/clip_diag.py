"""One-off: the standby clip of every look for the 9.2" on this machine, as Python draws each
picture and encodes the clip with the given ffmpeg, and as the given app makes it (the files
to keep, upright and turned round): digests and sizes, to set beside another machine's."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from PIL import Image

from libre_panel.devices import panel_files
from libre_panel.render import bootscreen

MODEL = "turing-9.2-usb"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def drawn(ffmpeg: str) -> dict:
    model = panel_files.model_of(MODEL)
    made = {}
    for look in bootscreen.designs():
        frames = bootscreen.standby_frames(model, None, "landscape", look, 1.0)
        each = [sha(frame.tobytes()) for frame in frames]
        entry = {"pictures": len(frames), "all": sha("".join(each).encode()), "each": each}
        for name, pictures in (
            ("upright", frames),
            ("turned", [frame.transpose(Image.Transpose.ROTATE_180) for frame in frames]),
        ):
            data = panel_files.clip_h264(model, ffmpeg, frames=pictures, full=True)
            entry[name] = {"size": len(data), "sha": sha(data)}
        made[look] = entry
        print(f"{look}: drawn and encoded", file=sys.stderr, flush=True)
    return made


def from_app(app: str) -> dict:
    home = Path(tempfile.mkdtemp())
    made: dict = {}
    for name, rotate in (("upright", 0), ("turned", 180)):
        (home / "config.toml").write_text(
            f'theme = "slate"\nlanguage = "en"\n[device]\ndriver = "turzx"\n'
            f'model = "{MODEL}"\nrotate = {rotate}\n[sensors]\nproviders = []\n',
            encoding="utf-8",
        )
        env = {**os.environ, "LIBRE_PANEL_HOME": str(home)}
        with subprocess.Popen([app, "editor", "--port", "0"], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, encoding="utf-8") as program:  # fmt: skip
            try:
                address = program.stdout.readline().split(" running at ")[1].split()[0]
                port = int(address.split(":")[2].split("/")[0])
                token = address.split("#token=")[1]
                for look in bootscreen.designs():
                    url = (f"http://127.0.0.1:{port}/api/start-screen/file?model={MODEL}"
                           f"&what=clip&design={look}")  # fmt: skip
                    asked = urllib.request.Request(url, headers={"X-Libre-Panel": token})
                    with urllib.request.urlopen(asked, timeout=600) as answer:
                        data = answer.read()
                    made.setdefault(look, {})[name] = {"size": len(data), "sha": sha(data)}
                    print(f"{look} {name}: from the app", file=sys.stderr, flush=True)
            finally:
                program.terminate()
    return made


def main() -> None:
    ffmpeg, app = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""
    out = {"python": drawn(ffmpeg)}
    if app:
        out["app"] = from_app(app)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
