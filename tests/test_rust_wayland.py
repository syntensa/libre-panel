"""The screen on Wayland in Rust (rust/app/src/wayland.rs, wayland_feed.rs) against the stand-ins
of test_wayland.py on a session bus of the test's own, as wayland.py and wayland_feed.py do it:
the capture program with GNOME's own monitor and a monitor by its connector, the session
stopped as GStreamer ends; through the portal with the PipeWire connection handed to
GStreamer and the dialog's choice kept for the next time; a cancelled dialog; GNOME's
monitors as the app lists them."""

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
from test_rust_app import DATA, _libraries, _program
from test_wayland import DisplayConfig, Mutter, Portal, bus  # noqa: F401 - the fixture

from libre_panel import wayland_feed

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("dbus-daemon"), reason="a session bus of its own"
)


def _gst(folder: Path) -> tuple[dict[str, str], Path]:
    """A stand-in of gst-launch-1.0 on the PATH: it writes its pipeline down, and as its frames
    the portal's PipeWire connection (two bytes from the descriptor its pipeline names) or
    "BGRxBGRx"."""
    fake = folder / "bin" / "gst-launch-1.0"
    fake.parent.mkdir(parents=True, exist_ok=True)
    said = folder / "pipeline.txt"
    fake.write_text(
        "#!/bin/sh\n"
        f'echo "$@" > {said}\n'
        'fd=$(echo "$@" | tr " " "\\n" | grep "^fd=" | head -1 | cut -d= -f2)\n'
        'if [ "$fd" != "1" ]; then head -c 2 <&"$fd"; else printf "BGRxBGRx"; fi\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake.parent}{os.pathsep}{os.environ['PATH']}"}
    return env, said


def _feed(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([str(_program()), "wayland-feed", *args], capture_output=True,
                          env=env, timeout=30)  # fmt: skip


def test_gnome_gives_its_own_monitor_and_a_monitor_by_its_connector(bus, tmp_path):  # noqa: F811
    env, said = _gst(tmp_path)
    mutter = Mutter()
    try:
        own = _feed(env, "--kind", "virtual", "--size", "1920x720", "--fps", "30")
        pipeline = said.read_text(encoding="utf-8").split()
        by_name = _feed(env, "--kind", "monitor", "--id", "DP-1", "--size", "480x480",
                        "--fit", "cover", "--no-cursor")  # fmt: skip
    finally:
        mutter.close()
    assert own.returncode == 0, own.stderr
    assert own.stdout == b"BGRxBGRx"  # its output is the frames alone
    assert b"wayland-feed: stream 42\n" in own.stderr
    assert pipeline == wayland_feed.gst_command("gst", 42, None, None, (1920, 720), 30,
                                                "contain", True)[1:]  # fmt: skip
    assert by_name.returncode == 0, by_name.stderr
    assert [call[0] for call in mutter.calls] == ["CreateSession", "RecordVirtual", "Start",
                                                 "Stop", "CreateSession", "RecordMonitor",
                                                 "Start", "Stop"]  # fmt: skip
    assert mutter.calls[1][1] == ({"cursor-mode": ("u", 1)},)  # (the pointer drawn in)
    assert mutter.calls[5][1] == ("DP-1", {"cursor-mode": ("u", 0)})


def test_the_portal_hands_over_the_pipewire_connection_and_asks_once(bus, tmp_path):  # noqa: F811
    env, said = _gst(tmp_path)
    home = tmp_path / "home"
    env["LIBRE_PANEL_HOME"] = str(home)
    portal = Portal()
    try:
        os.write(portal.write, b"pw")
        first = _feed(env, "--kind", "virtual", "--size", "1920x720", "--fit", "cover")
        pipeline = said.read_text(encoding="utf-8")
        os.write(portal.write, b"pw")
        again = _feed(env, "--kind", "virtual", "--size", "1920x720", "--no-cursor")
    finally:
        portal.close()
    assert first.returncode == 0, first.stderr
    assert first.stdout == b"pw"  # the portal's descriptor, read by GStreamer
    assert b"wayland-feed: stream 77 (1920x1080)\n" in first.stderr
    fd = int(pipeline.split(" fd=")[1].split()[0])
    expected = wayland_feed.gst_command("gst", 77, fd, (1920, 1080), (1920, 720), 30, "cover",
                                        False)  # fmt: skip
    assert pipeline.split() == expected[1:]
    chosen = [body[1] for member, body in portal.calls if member == "SelectSources"]
    assert chosen[0]["types"] == ("u", 4) and chosen[0]["persist_mode"] == ("u", 2)
    assert chosen[0]["cursor_mode"] == ("u", 2) and "restore_token" not in chosen[0]
    assert json.loads((home / "wayland-screencast.json").read_text()) == {"virtual": "kept-1"}
    assert again.returncode == 0, again.stderr
    assert chosen[1]["restore_token"] == ("s", "kept-1") and chosen[1]["cursor_mode"] == ("u", 1)
    assert [member for member, _ in portal.calls].count("Close") == 2


def test_a_cancelled_dialog_gives_no_screen(bus, tmp_path):  # noqa: F811
    env, _ = _gst(tmp_path)
    env["LIBRE_PANEL_HOME"] = str(tmp_path / "home")
    portal = Portal(answer_code=1)
    try:
        done = _feed(env, "--kind", "monitor", "--size", "800x480")
    finally:
        portal.close()
    assert done.returncode == 3
    assert done.stderr == b"wayland-feed: the desktop gave no screen: cancelled\n"
    assert _feed(env, "--kind", "monitor").returncode == 2  # (no size)


def test_gnomes_monitors_are_listed_as_wayland_py_lists_them(bus, tmp_path):  # noqa: F811
    (tmp_path / "config.toml").write_text(
        'theme = "slate"\nfps = 2\n[device]\ndriver = "virtual"\noutput = "panel.png"\n'
        "[sensors]\nproviders = []\n",
        encoding="utf-8",
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           "WAYLAND_DISPLAY": "wayland-test", **_libraries()}  # fmt: skip
    display = DisplayConfig()
    with subprocess.Popen([str(_program()), "start", "--port", "0", "--no-browser"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            assert program.stdout is not None
            address = program.stdout.readline().split(" running at ")[1].split()[0]
            base, token = address.split("#token=")
            request = urllib.request.Request(base + "api/mirror", headers={"X-Libre-Panel": token})
            with urllib.request.urlopen(request, timeout=10) as response:
                told = json.loads(response.read())
        finally:
            display.close()
            program.terminate()
            program.communicate(timeout=30)
    assert told["platform"] == "wayland"
    found = [(m["id"], m["name"], m["x"], m["width"], m["height"], m["primary"])
             for m in told["monitors"]]  # fmt: skip
    assert found == [("DP-1", 'Dell 27"', 0, 3840, 2160, True),
                     ("HDMI-1", "HDMI-1", 3840, 1920, 1080, False)]  # fmt: skip
    assert told["extend"]["how"] == "wayland"


@pytest.fixture(autouse=True)
def _program_built():
    _program()
