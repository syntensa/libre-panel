"""Smoke test for a release build: start the background app, talk to it, quit it.

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


def main(command):
    with tempfile.TemporaryDirectory() as folder:
        home = Path(folder)
        frame = home / "frame.png"
        (home / "config.toml").write_text(
            f'theme = "spur-ii"\n\n[device]\ndriver = "virtual"\noutput = "{frame.as_posix()}"\n',
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
            if log.exists():
                print(log.read_text(encoding="utf-8"))
    print("OK")


if __name__ == "__main__":
    main(sys.argv[1:])
