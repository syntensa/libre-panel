import plistlib
import sys

import pytest

from libre_panel import autostart as autostart_module
from libre_panel.autostart import (
    APP_NAME,
    MAC_LABEL,
    Autostart,
    desktop_exec,
    launch_command,
)

COMMAND = ["/opt/Libre Panel/libre-panel", "--config", "/home/u/100% cfg/config.toml", "tray"]


class FakeRegistry:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def test_xdg_desktop_entry(tmp_path):
    auto = Autostart("linux", home=tmp_path, environ={})
    assert not auto.is_enabled()
    where = auto.enable(COMMAND)
    path = tmp_path / ".config" / "autostart" / "libre-panel.desktop"
    assert where == str(path) and auto.is_enabled()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("[Desktop Entry]\n")
    assert f"Name={APP_NAME}" in text and "Terminal=false" in text
    exec_line = next(line for line in text.splitlines() if line.startswith("Exec="))
    assert exec_line == f"Exec={desktop_exec(COMMAND)}"
    assert auto.disable() is True
    assert not path.exists() and not auto.is_enabled()
    assert auto.disable() is False


def test_xdg_respects_xdg_config_home_and_hidden(tmp_path):
    auto = Autostart("freebsd", home=tmp_path, environ={"XDG_CONFIG_HOME": str(tmp_path / "x")})
    auto.enable(["libre-panel", "tray"])
    path = tmp_path / "x" / "autostart" / "libre-panel.desktop"
    assert path.is_file()
    path.write_text(path.read_text() + "Hidden=true\n")  # what desktop settings apps write
    assert not auto.is_enabled()


def unquote_desktop_exec(value):
    """Parse an Exec value as the Desktop Entry spec says: string escapes first,
    then arguments split on spaces, with "..." quoting where \\ escapes " ` $ \\."""
    value = value.replace("\\\\", "\\")
    args, current, quoted, escaped, started = [], [], False, False, False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
        elif quoted and char == "\\":
            escaped = True
        elif char == '"':
            quoted, started = not quoted, True
        elif char == " " and not quoted:
            if current or started:
                args.append("".join(current))
            current, started = [], False
        else:
            current.append(char)
    if current or started:
        args.append("".join(current))
    return [arg.replace("%%", "%") for arg in args]


@pytest.mark.parametrize(
    "args",
    [
        ["/usr/bin/python3", "-m", "libre_panel", "tray"],
        COMMAND,
        ['C:\\odd "path"\\python.exe', "$HOME", "`x`", "a;b", ""],
    ],
)
def test_desktop_exec_round_trips(args):
    assert unquote_desktop_exec(desktop_exec(args)) == args


def test_desktop_exec_quotes_only_when_needed():
    assert desktop_exec(["/usr/bin/libre-panel", "tray"]) == "/usr/bin/libre-panel tray"
    assert desktop_exec(["/a b/x"]) == '"/a b/x"'
    assert desktop_exec(["libre-panel", "50%"]) == "libre-panel 50%%"
    assert desktop_exec(["/opt/100%/libre-panel"]) == "env /opt/100%%/libre-panel"
    with pytest.raises(ValueError):
        desktop_exec(["a\nb"])


def test_macos_launch_agent(tmp_path):
    auto = Autostart("darwin", home=tmp_path)
    auto.enable(COMMAND)
    path = tmp_path / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"
    agent = plistlib.loads(path.read_bytes())
    assert agent["Label"] == MAC_LABEL
    assert agent["ProgramArguments"] == COMMAND
    assert agent["RunAtLoad"] is True
    assert agent["KeepAlive"] == {"SuccessfulExit": False}  # Quit stays quit
    assert auto.is_enabled()
    assert auto.disable() and not path.exists()


def test_windows_run_key_with_fake_registry():
    registry = FakeRegistry()
    auto = Autostart("win32", registry=registry)
    assert not auto.is_enabled()
    auto.enable([r"C:\Program Files\Libre Panel\LibrePanel.exe", "tray", "--background"])
    assert registry.values[APP_NAME] == (
        r'"C:\Program Files\Libre Panel\LibrePanel.exe" tray --background'
    )
    assert auto.is_enabled()
    assert "CurrentVersion\\Run" in auto.location()
    assert auto.disable() is True and not auto.is_enabled()


@pytest.mark.skipif(sys.platform != "win32", reason="needs the Windows registry")
def test_windows_registry_for_real():
    key = r"Software\LibrePanelTests\Run"
    registry = autostart_module.WindowsRegistry(key)
    auto = Autostart("win32", registry=registry)
    try:
        auto.enable(["C:\\x\\LibrePanel.exe", "tray"])
        assert registry.get(APP_NAME) == "C:\\x\\LibrePanel.exe tray"
        assert auto.is_enabled()
    finally:
        auto.disable()
    assert not auto.is_enabled()
    import winreg

    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\LibrePanelTests")


def test_launch_command_from_source_install(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    command = launch_command(tmp_path / "config.toml")
    assert command[1:3] == ["-m", "libre_panel"]
    assert command[3:] == [
        "--config",
        str((tmp_path / "config.toml").resolve()),
        "tray",
        "--background",
    ]
    assert launch_command()[3:] == ["tray", "--background"]


def test_launch_command_from_release_build(tmp_path, monkeypatch):
    exe = tmp_path / ("libre-panel.exe" if sys.platform == "win32" else "libre-panel")
    exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert launch_command() == [str(exe), "tray", "--background"]
    if sys.platform == "win32":  # the build without a console window is preferred
        (tmp_path / "LibrePanel.exe").write_text("")
        assert launch_command()[0] == str(tmp_path / "LibrePanel.exe")


@pytest.mark.skipif(
    not (sys.platform.startswith("linux") and __import__("shutil").which("gio")),
    reason="needs GLib's gio to launch desktop entries",
)
def test_glib_starts_the_entry_with_exact_arguments(tmp_path):
    """GLib (GNOME, Cinnamon, ...) parses our Exec line back to the same arguments."""
    import json
    import subprocess
    import time

    folder = tmp_path / 'odd $dir `x` 100% \\back "q"'
    folder.mkdir()
    out = tmp_path / "argv.json"
    script = folder / "show args.py"
    script.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        f"open({str(out)!r}, 'w').write(json.dumps(sys.argv))\n"
    )
    script.chmod(0o755)
    args = [str(script), "--config", '/h/100% "c"/config.toml', "semi;colon", "tray"]
    auto = Autostart("linux", home=tmp_path, environ={})
    auto.enable(args)
    subprocess.run(["gio", "launch", str(auto.path())], check=True, timeout=10)
    for _ in range(100):
        if out.exists() and out.stat().st_size:
            break
        time.sleep(0.05)
    assert json.loads(out.read_text()) == args
