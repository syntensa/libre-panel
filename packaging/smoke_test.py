"""Smoke test for a release build: start the background app, talk to it, quit it.

A plugin folder with a service that uses sqlite3 checks that folder plugins
load on the bundled Python.

python packaging/smoke_test.py dist/libre-panel/libre-panel tray --background
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def wait(check, seconds, what):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            result = check()
        except (OSError, ValueError):
            result = None
        if result:
            return result
        time.sleep(0.2)
    raise SystemExit(f"FAIL: {what} (after {seconds} s)")


def api(url, action=None):
    request = urllib.request.Request(url + "api/app")
    if action:
        request.data = json.dumps({"action": action}).encode()
        request.add_header("X-Libre-Panel", "1")
        request.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local, no proxy
    with opener.open(request, timeout=10) as response:
        return json.loads(response.read())


PLUGIN = """
import sqlite3

from libre_panel.plugins import Service


class Smoke(Service):
    name = "smoke"
    api = 1

    def start(self):
        with sqlite3.connect(self.host.data_dir / "smoke.db") as db:
            db.execute("create table if not exists runs (at text)")
        (self.host.data_dir / "started").write_text("ok")
"""


def main(command):
    with tempfile.TemporaryDirectory() as folder:
        home = Path(folder)
        frame = home / "frame.png"
        (home / "config.toml").write_text(
            f'theme = "spur-ii"\n\n[device]\ndriver = "virtual"\noutput = "{frame.as_posix()}"\n'
            '\n[services]\nenabled = ["smoke"]\n',
            encoding="utf-8",
        )
        plugin = home / "plugins" / "smoke"
        (plugin / "smoke_plugin").mkdir(parents=True)
        (plugin / "smoke_plugin" / "__init__.py").write_text(PLUGIN, encoding="utf-8")
        (plugin / "plugin.toml").write_text(
            'name = "smoke"\napi = 1\n\n[entry-points."libre_panel.services"]\n'
            'smoke = "smoke_plugin:Smoke"\n',
            encoding="utf-8",
        )
        env = dict(os.environ, LIBRE_PANEL_HOME=str(home))
        print("starting:", " ".join(command))
        proc = subprocess.Popen(command, env=env)
        try:
            info = wait(lambda: json.loads((home / "instance.json").read_text()), 90, "instance")
            url = info["editor"]
            print("editor:", url)
            state = wait(lambda: api(url)["panel"]["state"] == "showing" and api(url), 60, "frames")
            print("panel:", state["panel"])
            wait(frame.exists, 10, "frame file")
            wait((home / "plugins-data" / "smoke" / "started").exists, 10, "folder plugin")
            api(url, "quit")
            code = proc.wait(30)
            if code != 0:
                raise SystemExit(f"FAIL: exit code {code}")
            if (home / "instance.json").exists():
                raise SystemExit("FAIL: instance.json left behind")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(10)
            log = home / "logs" / "libre-panel.log"
            text = log.read_text(encoding="utf-8") if log.exists() else ""
            print(text)
        # Quit has to work by itself, not through the last-resort exit.
        if "exiting anyway" in text:
            raise SystemExit("FAIL: the app only ended through the last-resort exit")
    print("OK")


if __name__ == "__main__":
    main(sys.argv[1:])
