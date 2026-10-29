"""A monitor of the panel's own (extend.py): the Virtual Display Driver's settings with the
panel's size among its resolutions, what is missing where; on X11 the room right of the
monitors, made with xrandr and given back."""

import pytest

from libre_panel import capture, extend

SETTINGS = """<?xml version='1.0' encoding='utf-8'?>
<vdd_settings>
    <monitors>
        <count>1</count>
    </monitors>
    <resolutions>
        <resolution>
            <width>1920</width>
            <height>1080</height>
            <refresh_rate>60</refresh_rate>
        </resolution>
    </resolutions>
    <logging>
        <logging>false</logging>
    </logging>
</vdd_settings>
"""


def test_the_drivers_settings_get_the_panels_size_once():
    assert not extend.vdd_has_resolution((1920, 720), SETTINGS)
    changed = extend.with_resolution(SETTINGS, (1920, 720))
    assert extend.vdd_has_resolution((1920, 720), changed)
    assert extend.vdd_has_resolution((1920, 1080), changed)  # (its own kept)
    assert extend.with_resolution(changed, (1920, 720)) == changed  # (once)
    assert changed.index("<width>1920</width>\n            <height>720</height>") < changed.index(
        "</resolutions>"
    )
    assert changed.count("<resolution>") == 2 and "<logging>false</logging>" in changed
    bare = "<vdd_settings>\n    <monitors><count>1</count></monitors>\n</vdd_settings>\n"
    assert extend.vdd_has_resolution((480, 1920), extend.with_resolution(bare, (480, 1920)))
    with pytest.raises(extend.ExtendError):
        extend.with_resolution("<html/>", (1920, 720))


def test_what_is_missing_where(monkeypatch):
    monkeypatch.setattr(extend, "vdd_installed", lambda: False)
    assert extend.state((1920, 720), "windows") == {"how": "vdd", "ready": False,
                                                    "missing": "driver"}  # fmt: skip
    monkeypatch.setattr(extend, "vdd_installed", lambda: True)
    monkeypatch.setattr(extend, "vdd_has_resolution", lambda size, text=None: False)
    assert extend.state((1920, 720), "windows")["missing"] == "resolution"
    monkeypatch.setattr(extend, "vdd_has_resolution", lambda size, text=None: True)
    assert extend.state((1920, 720), "windows") == {"how": "vdd", "ready": True, "missing": ""}
    monkeypatch.setattr(extend.shutil, "which", lambda name: None)
    assert extend.state((1920, 720), "x11")["missing"] == "xrandr"
    assert extend.state((1920, 720), "other") == {"how": "", "ready": False, "missing": ""}
    if extend.sys.platform != "win32":  # (the driver is Windows' alone)
        with pytest.raises(extend.ExtendError):
            extend.setup((1920, 720))


def test_x11_makes_room_right_of_the_monitors_and_gives_it_back(monkeypatch):
    monitors = [capture.Monitor("DP-1", "DP-1", 0, 0, 2560, 1440, True),
                capture.Monitor("HDMI-1", "HDMI-1", 2560, 200, 1920, 1080)]  # fmt: skip
    assert extend.x11_plan((4480, 1440), monitors, (1920, 720)) == ((6400, 1440), (4480, 0))
    calls = []
    listed = ["Monitors: 2\n 0: +*DP-1 2560/597x1440/336+0+0  DP-1\n"
              " 1: +HDMI-1 1920/527x1080/296+2560+200  HDMI-1\n"]  # fmt: skip

    def xrandr(*args):
        calls.append(args)
        if args == ("--listmonitors",):
            return listed[0]
        if args == ("-q",):
            return "Screen 0: minimum 8 x 8, current 4480 x 1440, maximum 32767 x 32767\n"
        return ""

    monkeypatch.setattr(extend, "_xrandr", xrandr)
    monkeypatch.setattr(capture, "monitors", lambda where=None: monitors)
    extend.attach((1920, 720), "x11")
    assert ("--fb", "6400x1440") in calls
    assert ("--setmonitor", "LibrePanel", "1920/508x720/191+4480+0", "none") in calls
    listed[0] += " 2: LibrePanel 1920/508x720/191+4480+0  none\n"
    calls.clear()
    extend.detach("x11")
    assert calls[1:] == [("--delmonitor", "LibrePanel"), ("--fb", "4480x1440")]
    # where the screen is already large enough, it stays as it is
    calls.clear()
    listed[0] = listed[0].split(" 2:")[0]
    monkeypatch.setattr(extend, "_xrandr", lambda *a: calls.append(a) or (
        "Screen 0: current 8000 x 2000" if a == ("-q",) else listed[0]))  # fmt: skip
    extend.attach((1920, 720), "x11")
    assert not any(call[0] == "--fb" for call in calls)


def test_a_failing_x11_says_why(monkeypatch):
    def xrandr(*args):
        if args[0] == "--fb":
            raise OSError("xrandr: screen cannot be larger than 1600x900")
        return "Screen 0: current 1600 x 900" if args == ("-q",) else "Monitors: 0\n"

    monkeypatch.setattr(extend, "_xrandr", xrandr)
    monkeypatch.setattr(capture, "monitors", lambda where=None: [
        capture.Monitor("screen", "screen", 0, 0, 1600, 900, True)])  # fmt: skip
    with pytest.raises(extend.ExtendError, match="cannot be larger"):
        extend.attach((1920, 720), "x11")
