"""Install the Windows setup silently, check it, run it, uninstall it.

python packaging/windows/test_installer.py dist/libre-panel-X-windows-x64-setup.exe
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import winreg
from pathlib import Path

APP = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Libre Panel"
MENU = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Libre Panel.lnk"
SMOKE = Path(__file__).resolve().parent.parent / "smoke_test.py"


def run_value():
    run = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run) as key:
            return winreg.QueryValueEx(key, "Libre Panel")[0]
    except FileNotFoundError:
        return None


def check(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print("ok:", message)


def wait(predicate, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.5)
    return False


def main(setup):
    log = Path(tempfile.gettempdir()) / "libre-panel-setup.log"
    subprocess.run(
        [
            setup,
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/TASKS=autostart",
            f"/LOG={log}",
        ],
        check=True,
        timeout=600,
    )
    check((APP / "LibrePanel.exe").is_file(), "LibrePanel.exe installed")
    check((APP / "libre-panel.exe").is_file(), "libre-panel.exe installed")
    check(MENU.is_file(), "start menu entry")
    value = run_value() or ""
    check("LibrePanel.exe" in value and "tray --background" in value, f"autostart entry: {value}")

    # The installed program works as autostart would start it.
    subprocess.run(
        [sys.executable, str(SMOKE), str(APP / "LibrePanel.exe"), "--background"], check=True
    )

    # Uninstalling quits a running Libre Panel and removes everything it added.
    with tempfile.TemporaryDirectory() as home:
        os.environ["LIBRE_PANEL_HOME"] = home
        Path(home, "config.toml").write_text(
            '[device]\ndriver = "virtual"\noutput = "frame.png"\n', encoding="utf-8"
        )
        running = subprocess.Popen([str(APP / "LibrePanel.exe"), "--background"])
        info = Path(home, "instance.json")
        check(wait(info.exists, 60), "background app started")
        url = json.loads(info.read_text())["editor"]
        with urllib.request.urlopen(url + "api/app", timeout=10) as response:
            check(json.loads(response.read())["available"], "editor answers")

        subprocess.run(
            [str(APP / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
        )
        check(wait(lambda: running.poll() is not None, 60), "uninstaller quit the running app")
        check(wait(lambda: not (APP / "LibrePanel.exe").exists(), 120), "program files removed")
    check(not MENU.exists(), "start menu entry removed")
    check(run_value() is None, "autostart entry removed")
    print("OK")


if __name__ == "__main__":
    main(sys.argv[1])
