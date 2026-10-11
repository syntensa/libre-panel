"""Smoke test for a release build: start the background app, talk to it, quit it.

A plugin folder with a service that uses sqlite3 checks that folder plugins
load on the bundled Python. The built-in service "notices" checks that Libre
Panel's own services start from a build that has no package metadata.

python packaging/smoke_test.py [--tools] dist/libre-panel/libre-panel tray --background

--tools: the download carries ffmpeg (packaging/fetch_tools.py), so the app's own account of
the video mode (the panel page of its API) must miss nothing, not even ffmpeg.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
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


# The app's server answers only requests with its access token (editor/access.py). The app
# writes it for the programs of its user into a file in the settings folder, as `libre-panel
# quit` reads it.
TOKEN = {"value": ""}


def read_token(home):
    return json.loads((home / "private" / "editor.json").read_text(encoding="utf-8"))["token"]


def api(url, action=None):
    request = urllib.request.Request(url + "api/app")
    request.add_header("X-Libre-Panel", TOKEN["value"])
    if action:
        request.data = json.dumps({"action": action}).encode()
        request.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local, no proxy
    with opener.open(request, timeout=10) as response:
        return json.loads(response.read())


def fetch(url):
    request = urllib.request.Request(url, headers={"X-Libre-Panel": TOKEN["value"]})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local, no proxy
    with opener.open(request, timeout=10) as response:
        return response.read()


def refused(url):
    """Whether the server refuses a request without the token (403)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local, no proxy
    try:
        opener.open(url, timeout=10).read()
    except urllib.error.HTTPError as error:
        return error.code == 403
    return False


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
    tools = command[:1] == ["--tools"]
    command = command[1:] if tools else command
    with tempfile.TemporaryDirectory() as folder:
        home = Path(folder)
        frame = home / "frame.png"
        (home / "config.toml").write_text(
            f'theme = "spur-ii"\n\n[device]\ndriver = "virtual"\noutput = "{frame.as_posix()}"\n'
            '\n[video]\nmode = "on"\nlocal_clip = "smoke.h264"\n'
            '\n[services]\nenabled = ["smoke", "notices"]\n\n[services.notices]\nmusic = false\n',
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
            TOKEN["value"] = wait(lambda: read_token(home), 10, "access token file")
            if not refused(url + "api/app"):
                raise SystemExit("FAIL: the server answered a request without its access token")
            state = wait(lambda: api(url)["panel"]["state"] == "showing" and api(url), 60, "frames")
            print("panel:", state["panel"])
            wait(frame.exists, 10, "frame file")
            wait((home / "plugins-data" / "smoke" / "started").exists, 10, "folder plugin")
            # the editor's own pages (Control, Autopilot): their files and code are in the build
            pages = {page["id"] for page in json.loads(fetch(url + "api/plugins/pages"))}
            if not {"control", "autopilot"} <= pages:
                raise SystemExit(f"FAIL: the editor's built-in pages are missing: {sorted(pages)}")
            if b"console.js" not in fetch(url + "plugins/control/"):
                raise SystemExit("FAIL: the Control page's files are not in the build")
            control = json.loads(fetch(url + "api/plugins/control/status"))
            if not control["background"] or not any(
                s["name"] == "notices" for s in control["services"]
            ):
                raise SystemExit(f"FAIL: the Control page does not see the app: {control}")
            if tools:  # the video mode lacks nothing: the download's ffmpeg is found
                gaps = json.loads(fetch(url + "api/panel"))["video"]["gaps"]
                if gaps:
                    raise SystemExit(f"FAIL: the video mode of the build lacks {gaps}")
                print("video mode: nothing missing")
            # the live picture (Pillow's JPEG and PNG writers are in the build)
            live = url + "api/plugins/control/live"
            wait(lambda: fetch(live).startswith(b"\xff\xd8"), 20, "live picture (JPEG)")
            png = live + "?format=png"
            wait(lambda: fetch(png).startswith(b"\x89PNG"), 20, "live picture (PNG)")
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
        if "service 'notices' started" not in text:
            raise SystemExit("FAIL: the built-in service notices did not start")
    print("OK")


if __name__ == "__main__":
    main(sys.argv[1:])
