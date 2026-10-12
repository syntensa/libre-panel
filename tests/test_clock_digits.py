"""A clock's digits that roll or fade on the second, as SPUR II's do, and Studio's seconds run.

The clock stands at 14:30:00.00 and goes on in steps of 0.02 s (50 frames a second): the
minute's last digit and both seconds digits change at once (14:29:59 to 14:30:00)."""

import json
from datetime import datetime, timedelta

import numpy
import pytest
from PIL import ImageChops

from libre_panel.render.renderer import DIGITS_S, Renderer
from libre_panel.sensors.base import Snapshot
from libre_panel.theme.model import builtin_themes_dir, parse_theme

START = datetime(2026, 9, 28, 14, 30, 0)
# where the letters of "14:29:59" stand at 60 px (x 10 on): the hours, the minutes' last
# digit and the seconds
HOURS, MINUTE, SECONDS = (10, 75), (128, 165), (182, 255)


def clock(**kw):
    widget = {"type": "clock", "id": "c", "x": 10, "y": 10, "format": "%H:%M:%S",
              "font_size": 60, "color": "#ffffff", **kw}  # fmt: skip
    theme = parse_theme({"format": "libre-panel-theme/1", "display": {"width": 280, "height": 100},
                         "background": {"color": "#000000"}, "widgets": [widget]})  # fmt: skip
    renderer = Renderer(theme, animate=True)
    renderer.fps = 50
    return renderer


def frame(renderer, step, start=START):
    return renderer.render(Snapshot(now=start + timedelta(seconds=0.02 * step)), 0.02 * step)[0]


def jumping(when):
    """The clock as it was before: the text, jumping on the second."""
    return clock().render(Snapshot(now=when), 0.0)[0]


def apart(one, other, columns=(0, 280)):
    """The largest difference of two frames in a range of columns."""
    box = (columns[0], 0, columns[1], 100)
    difference = ImageChops.difference(one.crop(box), other.crop(box))
    return max(band[1] for band in difference.getextrema())


def test_what_changes_on_the_second_rolls_for_0_42_s_then_it_is_the_text_as_it_stands():
    renderer = clock(digits="roll")
    old, new = jumping(START - timedelta(seconds=1)), jumping(START + timedelta(seconds=0.5))
    assert apart(frame(renderer, 0), old) <= 2  # the roll starts with the old time
    for step in range(1, 16):
        shown = frame(renderer, step)
        assert renderer.moving
        for where in (MINUTE, SECONDS):  # all that changes rolls at once
            assert apart(shown, old, where) > 40 and apart(shown, new, where) > 40, (step, where)
        assert apart(shown, new, HOURS) == 0  # what stays stands where it is
    assert apart(frame(renderer, 20), new) <= 2  # landed softly
    for step in (21, 25, 49):  # done: the same pixels as a clock that jumps
        assert apart(frame(renderer, step), new) == 0, step
        assert not renderer.moving


def test_a_digit_ends_its_roll_at_full_strength_not_dimmed_and_lit_up_after():
    renderer = clock(digits="roll")
    new = jumping(START + timedelta(seconds=0.5))
    box = (SECONDS[0], 0, SECONDS[1], 100)
    light = numpy.asarray(new.convert("L").crop(box), float).sum()
    landing = numpy.asarray(frame(renderer, 18).convert("L").crop(box), float).sum()
    assert landing >= 0.98 * light


def test_the_digits_may_fade_instead_and_jump_by_default():
    fading = clock(digits="fade")
    old, new = jumping(START - timedelta(seconds=1)), jumping(START + timedelta(seconds=0.5))
    halfway = frame(fading, 4)  # 0.08 s: about half way
    assert apart(halfway, old, SECONDS) > 40 and apart(halfway, new, SECONDS) > 40
    assert apart(frame(fading, 25), new) == 0
    plain = clock()
    for step in (0, 5, 25):
        assert apart(frame(plain, step), jumping(START + timedelta(seconds=0.02 * step))) == 0
        assert not plain.moving
    still = clock(digits="roll")
    still.animate = False  # a panel drawn at 1 fps: nothing to roll with
    assert apart(frame(still, 5), new) == 0


def test_a_roll_takes_as_long_as_the_theme_says():
    renderer = clock(digits="roll", digits_seconds=0.2)
    new = jumping(START + timedelta(seconds=0.5))
    assert apart(frame(renderer, 5), new) > 40  # 0.1 s
    assert apart(frame(renderer, 10), new) == 0  # 0.2 s: done
    assert DIGITS_S == 0.42


def test_a_lettered_clock_rolls_its_own_letters():
    """Faceted lettering rolls as it is, so nothing changes its look when the roll ends."""
    widget = {"type": "clock", "id": "c", "x": 10, "y": 10, "format": "%H:%M:%S",
              "font_size": 60, "color": "#ffffff", "finish": "facet"}  # fmt: skip

    def renderer(**kw):
        theme = parse_theme({"format": "libre-panel-theme/1",
                             "display": {"width": 280, "height": 100},
                             "background": {"color": "#000000"},
                             "widgets": [{**widget, **kw}]})  # fmt: skip
        found = Renderer(theme, animate=True)
        found.fps = 50
        return found

    rolling, plain = renderer(digits="roll"), renderer()
    new = plain.render(Snapshot(now=START + timedelta(seconds=0.5)), 0.0)[0]
    assert apart(frame(rolling, 6), new, SECONDS) > 40
    assert apart(frame(rolling, 25), new) == 0
    assert rolling.warnings == []


def test_studio_runs_through_the_minute_under_its_clock_on_the_frames_time():
    snapshot = Snapshot(now=START + timedelta(seconds=30.5))
    assert snapshot.value("time.second") == 30.5  # a reading of the frame's own time
    data = json.loads((builtin_themes_dir() / "spur-studio" / "theme.json").read_text("utf-8"))
    run = next(w for w in data["widgets"] if w["id"] == "seconds-run")
    assert (run["sensor"], run["min"], run["max"], run["smooth"]) == ("time.second", 0, 60, False)
    widgets = [{**run, "color": "#00ffff", "x": 0, "y": 10}]
    theme = parse_theme({"format": "libre-panel-theme/1", "display": {"width": 600, "height": 30},
                         "background": {"color": "#000000"}, "widgets": widgets})  # fmt: skip
    renderer = Renderer(theme, animate=True)
    heads = []
    for step in range(26):  # half a second: some five pixels
        snapshot = Snapshot(now=START + timedelta(seconds=30 + 0.02 * step))
        renderer.render(snapshot, 0.02 * step)
        dot = dict(renderer._last[0])["seconds-run\0dot"]
        alpha = numpy.asarray(dot.layer.getchannel("A"), float).sum(axis=0)
        heads.append(dot.x + (alpha * (numpy.arange(alpha.size) + 0.5)).sum() / alpha.sum())
    steps = numpy.diff(heads)
    assert heads[0] == pytest.approx(568 / 2 - 0.5, abs=0.3)  # half way at half a minute
    # in quarter pixels, as SPUR II's: never a whole pixel at once, 568 px a minute
    assert steps.max() < 0.3 and steps.min() > -0.01, steps
    assert heads[-1] - heads[0] == pytest.approx(568 / 60 / 2, abs=0.3)


@pytest.mark.parametrize(
    "name",
    ["spur-classic", "spur-cockpit", "spur-focus", "spur-header", "spur-horizon",
     "spur-session", "spur-studio", "spur-ii", "round-report", "studio", "lumen", "encore",
     "nimbus", "almanac", "almanac-analog"],
)  # fmt: skip
def test_the_spur_screens_and_lumen_roll_their_clocks(name):
    data = json.loads((builtin_themes_dir() / name / "theme.json").read_text("utf-8"))
    clocks = [w for w in data["widgets"] if w["type"] == "clock" and "%" in w["format"]]
    timed = [w for w in clocks if any(f"%{c}" in w["format"] for c in "HMS")]
    assert timed and all(w["digits"] == "roll" for w in timed)
    assert all("digits" not in w for w in clocks if w not in timed)  # a date does not roll
