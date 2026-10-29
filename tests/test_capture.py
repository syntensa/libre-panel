"""What of the screen a panel can show as a monitor, and how ffmpeg captures it (capture.py):
the monitors xrandr names, ffmpeg's input on Windows (Windows.Graphics.Capture, else GDI) and
on X11, a part of a monitor cropped, a window; and on X11 the real thing, on Xvfb."""

import os
import shutil
import subprocess
import time

import pytest

from libre_panel import capture, mirror

XRANDR = """Monitors: 3
 0: +*DP-1 2560/597x1440/336+0+0  DP-1
 1: +HDMI-1 1920/527x1080/296+2560+0  HDMI-1
 2: LibrePanel 1920/508x720/191+4480+0  none
"""


def test_the_monitors_xrandr_names():
    found = capture.parse_xrandr(XRANDR)
    assert [(m.id, m.x, m.y, m.width, m.height, m.primary, m.virtual) for m in found] == [
        ("DP-1", 0, 0, 2560, 1440, True, False),
        ("HDMI-1", 2560, 0, 1920, 1080, False, False),
        ("LibrePanel", 4480, 0, 1920, 720, False, True),  # (extend.py's: no output of its own)
    ]
    assert capture.parse_xrandr("nonsense\n") == []


def test_windows_captures_a_monitor_through_windows_graphics_capture_else_gdi(monkeypatch):
    monitor = capture.Monitor(r"\\.\DISPLAY2", "Display 2", -1920, 0, 1920, 1080)
    monkeypatch.setattr(capture, "ffmpeg_can", lambda ffmpeg, name: name == "gfxcapture")
    found = capture.monitor_capture("ffmpeg", monitor, 50, where="windows")
    source = found.inputs[-1]
    assert found.inputs[:3] == ("-f", "lavfi", "-i") and source.startswith("gfxcapture=")
    # (a handle where Windows gives one; else the monitor's index)
    assert ("monitor_idx=1" in source or "hmonitor=" in source) and "max_framerate=50" in source
    assert found.prefilter == "hwdownload,format=bgra"  # (the card's frames to the processor)
    part = capture.monitor_capture("ffmpeg", monitor, 50, cursor=False, region=(100, 50, 801, 400),
                                   where="windows")  # fmt: skip
    assert "capture_cursor=0" in part.inputs[-1]
    assert "crop_left=100:crop_top=50:crop_right=1020:crop_bottom=630" in part.inputs[-1]
    monkeypatch.setattr(capture, "ffmpeg_can", lambda ffmpeg, name: False)  # an older ffmpeg
    gdi = capture.monitor_capture("ffmpeg", monitor, 50, region=(100, 50, 801, 400),
                                  where="windows")  # fmt: skip
    assert gdi.inputs == ("-f", "gdigrab", "-framerate", "50", "-draw_mouse", "1",
                          "-offset_x", "-1820", "-offset_y", "50", "-video_size", "800x400",
                          "-i", "desktop") and gdi.prefilter == ""  # fmt: skip


def test_a_window_by_its_handle(monkeypatch):
    window = capture.Window("0x1a2b", "Discord", "Discord.exe")
    monkeypatch.setattr(capture, "ffmpeg_can", lambda ffmpeg, name: True)
    found = capture.window_capture("ffmpeg", window, 30, where="windows")
    assert found.inputs[-1] == "gfxcapture=hwnd=0x1a2b:max_framerate=30:capture_cursor=1"
    monkeypatch.setattr(capture, "ffmpeg_can", lambda ffmpeg, name: False)
    assert capture.window_capture("ffmpeg", window, 30, where="windows").inputs[-2:] == (
        "-i", "hwnd=0x1a2b")  # fmt: skip
    monkeypatch.setenv("DISPLAY", ":3")
    x11 = capture.window_capture("ffmpeg", capture.Window("0x400007", "xterm"), 30, where="x11")
    assert x11.inputs == ("-f", "x11grab", "-framerate", "30", "-draw_mouse", "1",
                          "-window_id", "0x400007", "-i", ":3")  # fmt: skip


def test_x11_captures_a_part_of_the_screen(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":3")
    monitor = capture.Monitor("HDMI-1", "HDMI-1", 2560, 0, 1920, 1080)
    found = capture.monitor_capture("ffmpeg", monitor, 50, region=(10, 20, 641, 361), where="x11")
    assert found.inputs == ("-f", "x11grab", "-framerate", "50", "-draw_mouse", "1",
                            "-video_size", "640x360", "-i", ":3+2570,20")  # fmt: skip
    with pytest.raises(capture.CaptureError):
        capture.monitor_capture("ffmpeg", monitor, 50, where="wayland")


def test_where_it_runs(monkeypatch):
    monkeypatch.setattr(capture.sys, "platform", "linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    assert capture.platform() == "x11"
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert capture.platform() == "wayland"  # (XWayland's DISPLAY sees no Wayland windows)
    monkeypatch.setattr(capture.sys, "platform", "win32")
    assert capture.platform() == "windows"


needs_xvfb = pytest.mark.skipif(
    not (shutil.which("Xvfb") and shutil.which("xrandr") and shutil.which("ffmpeg")),
    reason="Xvfb, xrandr and ffmpeg",
)


@pytest.fixture
def xvfb(monkeypatch):
    display = f":{90 + os.getpid() % 9}"
    server = subprocess.Popen(["Xvfb", display, "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
    monkeypatch.setenv("DISPLAY", display)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    end = time.monotonic() + 10
    while time.monotonic() < end:
        if subprocess.run(["xrandr", "-q"], capture_output=True).returncode == 0:
            break
        time.sleep(0.1)
    yield display
    server.kill()
    server.wait()


@needs_xvfb
def test_x11_lists_the_monitor_and_ffmpeg_captures_it(xvfb):
    (screen,) = capture.monitors()
    assert (screen.width, screen.height, screen.primary) == (1280, 720, False) or screen.primary
    found = capture.monitor_capture(shutil.which("ffmpeg"), screen, 10, region=(0, 0, 640, 360))
    command = mirror.frames_command(shutil.which("ffmpeg"), found, (320, 180), "contain", 10)
    feed = mirror.CaptureFeed(command, (320, 180))
    try:
        end = time.monotonic() + 20
        count, picture = feed.frame()
        while picture is None and time.monotonic() < end:
            time.sleep(0.1)
            count, picture = feed.frame()
        assert picture is not None and picture.size == (320, 180) and count >= 1
    finally:
        feed.close()
