"""Whether a built plugin host (libre-panel-plugins of the downloads) runs plugins: started on
a plugin folder made here, it says hello with the plugin's parts, loads them and reads its
sensor source, as the Rust app asks it (src/libre_panel/plugin_host.py).

    python packaging/check_plugin_host.py <the program>
    python packaging/check_plugin_host.py --app <libre-panel>   (the app finds its host and
                                                                 lists the plugin's parts)
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = """
from libre_panel.plugins import Service
from libre_panel.sensors.base import Reading, SensorProvider
from PIL import Image


class Fans(SensorProvider):
    name = "check.fans"
    api = 1

    def read(self):
        picture = Image.new("RGB", (4, 4), "red")  # (Pillow is there)
        return {"fan.check": Reading("fan.check", float(picture.width * 300), "RPM")}


class Quiet(Service):
    name = "check.quiet"
    api = 1
"""


def ask(program: subprocess.Popen, ident: int, op: str, **fields: object) -> dict:
    body = json.dumps({"id": ident, "op": op, **fields}).encode()
    program.stdin.write(struct.pack("<I", len(body)) + body)
    program.stdin.flush()
    while True:
        (size,) = struct.unpack("<I", program.stdout.read(4))
        message = json.loads(program.stdout.read(size))
        if message.get("blob"):
            program.stdout.read(message["blob"])
        if message.get("id") == ident:
            if message.get("error"):
                raise SystemExit(f"FAIL: {op}: {message['error']}")
            return message


def _home() -> tempfile.TemporaryDirectory[str]:
    """A settings folder for the check. With administrator rights (Windows runners have them)
    plugin folders load only from where nobody without them can change them
    (libre_panel.rights): one in Program Files then."""
    if sys.platform == "win32":
        import ctypes

        if ctypes.windll.shell32.IsUserAnAdmin():
            return tempfile.TemporaryDirectory(dir=os.environ["ProgramFiles"])
    return tempfile.TemporaryDirectory()


def plugin_in(home: str) -> Path:
    """The plugin folder, installed in ``home``."""
    folder = Path(home) / "plugins" / "check"
    (folder / "check_plugin").mkdir(parents=True)
    (folder / "check_plugin" / "__init__.py").write_text(PLUGIN, encoding="utf-8")
    (folder / "plugin.toml").write_text(
        'name = "check"\napi = 1\n[entry-points."libre_panel.sensors"]\n'
        '"check.fans" = "check_plugin:Fans"\n'
        '[entry-points."libre_panel.services"]\n"check.quiet" = "check_plugin:Quiet"\n',
        encoding="utf-8",
    )
    return folder


def app(path: str) -> int:
    """The app finds its plugin host beside it and lists the plugin's parts as loading."""
    with _home() as home:
        plugin_in(home)
        env = {**os.environ, "LIBRE_PANEL_HOME": home}
        env.pop("LIBRE_PANEL_PLUGIN_HOST", None)
        done = subprocess.run([path, "plugins"], env=env, capture_output=True, text=True,
                              timeout=120, check=False)  # fmt: skip
    rows = [line.split() for line in done.stdout.splitlines()]
    ours = [row[:3] for row in rows if len(row) >= 3 and row[1].startswith("check.")]
    if done.returncode != 0 or ours != [["sensors", "check.fans", "ok"],
                                         ["services", "check.quiet", "ok"]]:  # fmt: skip
        raise SystemExit(f"FAIL: {path} plugins:\n{done.stdout}{done.stderr}")
    print(f"{path}: its plugin host runs the plugins")
    return 0


def main() -> int:
    if sys.argv[1] == "--app":
        return app(sys.argv[2])
    program_path = sys.argv[1]
    with _home() as home:
        folder = plugin_in(home)
        program = subprocess.Popen(
            [program_path, "--folder", str(folder.parent)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env={**os.environ, "LIBRE_PANEL_HOME": home},
        )
        try:
            hello = ask(program, 1, "hello")
            names = sorted(part["name"] for part in hello["parts"])
            if names != ["check.fans", "check.quiet"] or hello["errors"]:
                raise SystemExit(f"FAIL: the parts found: {hello}")
            checked = ask(program, 2, "check")
            if any(part["error"] for part in checked["parts"]):
                raise SystemExit(f"FAIL: a part does not load: {checked}")
            ask(program, 3, "sensors.open", name="check.fans", options={})
            read = ask(program, 4, "sensors.read", name="check.fans", wanted=[])
            if read["readings"] != [
                {
                    "key": "fan.check",
                    "value": 1200.0,
                    "unit": "RPM",
                    "label": "",
                    "origin": "",
                    "device": "",
                }
            ]:
                raise SystemExit(f"FAIL: the source's readings: {read}")
        finally:
            program.stdin.close()
            program.wait(30)
    print(f"plugin host {program_path}: plugins found, loaded and read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
