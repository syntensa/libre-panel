"""The download card: its texts for every phase, its layout, a state that is not what the
service sends, and how it is kept (a state drawn once, a stretch of light that runs)."""

import json

import numpy as np
import pytest
from PIL import Image, ImageChops

from libre_panel.render import download, finish
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.theme.model import parse_theme

MB = 1024 * 1024
GB = 1024 * MB

STATE = {
    "id": "steam:777", "source": "steam", "name": "Space Game", "phase": "downloading",
    "bytes": 9 * GB, "total": 24 * GB, "rate": 40 * MB, "remaining_s": 404.0, "elapsed_s": 252.0,
    "estimated": False, "history": [30 * MB + i * 2000 for i in range(120)], "more": 0,
}  # fmt: skip


def snapshot(state=STATE, text=None):
    value = text if text is not None else json.dumps(state)
    return Snapshot(readings={"downloads": Reading("downloads", value, "", "", "", 0.0)})


def widget(**more):
    return {"type": "downloads", "id": "d", "x": 20, "y": 10, "w": 596, "h": 130, **more}


def theme_of(*widgets, size=(640, 150)):
    return parse_theme(
        {
            "format": "libre-panel-theme/1",
            "display": {"width": size[0], "height": size[1]},
            "background": {"color": "#0a1119"},
            "widgets": list(widgets),
        }
    )


def drawn(w, snap=None, now=10.0, animate=False, size=(640, 150)):
    renderer = Renderer(theme_of(w, size=size), animate=animate)
    image, _ = renderer.render(snap or snapshot(), now)
    return renderer, image


def differs(a, b):
    return ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is not None


def lit_pixels(image, box):
    """How many pixels in ``box`` are not the background."""
    x, y, w, h = box
    part = np.asarray(image.convert("RGB").crop((x, y, x + w, y + h)))
    return int((part != (10, 17, 25)).any(axis=2).sum())


# -- the words -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "expected"),
    [(0, ("0", "MB")), (700 * MB, ("700", "MB")), (GB - 1, ("1024", "MB")),
     (GB, ("1.0", "GB")), (24.1 * GB, ("24.1", "GB")), (-5, ("0", "MB"))],
)  # fmt: skip
def test_sizes_are_in_mb_below_a_gb(size, expected):
    assert download.amount(size) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, ("0", "s")), (59.9, ("59", "s")), (60, ("1:00", "min")), (404, ("6:44", "min")),
     (3599, ("59:59", "min")), (3600, ("1:00", "h")), (7384, ("2:03", "h")), (-4, ("0", "s"))],
)  # fmt: skip
def test_time_is_seconds_minutes_or_hours(seconds, expected):
    assert download.time_left(seconds) == expected


def test_loaded_and_whole_are_in_the_unit_of_the_whole():
    assert download.of_total(9.3 * GB, 24.1 * GB) == "9.3 / 24.1 GB"
    assert download.of_total(994 * MB, 1.9 * GB) == "1.0 / 1.9 GB"  # not "994 / 1.9 GB"
    assert download.of_total(300 * MB, 700 * MB) == "300 / 700 MB"


def test_each_phase_has_its_heading_and_readings():
    running = download.read(STATE)
    assert running.phase == "downloading" and running.source == "STEAM"
    assert [r[0] for r in running.rows] == ["Rate", "Remaining"]
    assert running.rows[0][1:] == ("40.0", "MB/s") and running.rows[1][1:] == ("6:44", "min")
    assert running.amount == "9.0 / 24.0 GB" and running.share == pytest.approx(0.375)
    no_time = download.read({**STATE, "remaining_s": None})
    assert [r[0] for r in no_time.rows] == ["Rate", "Loaded"]
    assert no_time.rows[1][1:] == ("9.0", "GB")
    done = download.read({**STATE, "phase": "done", "bytes": 24 * GB, "rate": 0})
    assert done.share == 1.0 and [r[0] for r in done.rows] == ["Total", "Duration"]
    assert done.rows[1][1:] == ("4:12", "min") and done.amount == ""
    installing = download.read({**STATE, "phase": "installing"})
    assert [r[0] for r in installing.rows] == ["Loaded", "Duration"] and installing.amount == ""
    paused = download.read({**STATE, "phase": "paused", "rate": 0, "remaining_s": None})
    assert paused.rows[0][1:] == ("0", "KB/s") and paused.share == pytest.approx(0.375)
    assert download.heading("downloading") == download.heading("preparing") == "Download"
    assert len({download.heading(p) for p in download.PHASES}) == 4


def test_a_browser_download_has_no_share_and_an_estimate_is_marked():
    browser = download.read({**STATE, "source": "browser", "total": None, "more": 2})
    assert browser.share is None and browser.amount == "" and browser.name.endswith("+2")
    assert browser.source == "BROWSER"
    assert download.read({**STATE, "estimated": True}).amount.startswith("≈ ")


@pytest.mark.parametrize(
    "state",
    [
        {},
        {"phase": 7, "bytes": "many", "total": "?", "rate": [], "history": "fast"},
        {"phase": ["x"], "history": [None, "a", float("nan"), -5, 3], "name": None, "more": "x"},
        {"bytes": float("inf"), "total": 0, "rate": float("nan"), "remaining_s": True},
        {"history": [1e300, 2e300], "name": "x" * 500, "source": 5},
    ],
)
def test_a_state_that_is_not_what_the_service_sends_still_reads(state):
    r = download.read(state)
    assert r.phase in download.PHASES and len(r.rows) == 2
    assert all(len(row) == 3 for row in r.rows)


# -- the geometry ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "size", [(596, 130), (456, 198), (219, 198), (691, 414), (60, 30), (30, 90)]
)
def test_everything_stands_inside_the_box(size):
    w, h = size
    lay = download.layout(w, h)
    assert 0 <= lay.left < lay.right <= w and lay.g > 0
    x0, y0, x1, y1 = download.rail(lay)
    assert lay.left <= x0 < x1 <= lay.right and lay.top <= y0 < y1 <= h + 1
    if lay.graph is not None:
        gx0, gy0, gx1, gy1 = lay.graph
        assert 0 <= gx0 < gx1 <= w and lay.top <= gy0 < gy1 <= h + 0.5, lay
    for line in (*lay.ref["rows"], lay.ref["head"]):
        assert 0 <= lay.y(line) <= h + 1


def test_a_box_that_is_wide_has_its_graph_beside_the_readings_and_a_tall_one_below():
    wide, tall = download.layout(596, 130), download.layout(200, 200)
    assert not wide.stacked and tall.stacked
    assert wide.graph[0] > wide.left * 20 and wide.graph[1] < wide.y(wide.ref["rows"][1])
    assert tall.graph[0] == tall.left and tall.graph[1] > tall.y(tall.ref["rows"][1])
    assert download.layout(300, 100).graph is not None  # smaller: the same card, smaller
    assert download.layout(300, 40).graph is not None  # lower: a wider graph


def test_the_reference_box_is_the_studios_numbers():
    lay = download.layout(596, 130)
    assert lay.g == 1.0 and lay.gy == 1.0 and lay.top == 0
    assert (lay.left, lay.right) == (13, 580)
    assert download.rail(lay)[1] == 44


# -- the widget ------------------------------------------------------------------------------


@pytest.mark.parametrize("finish_name", ["flat", "lit"])
def test_it_draws_a_download_and_nothing_where_none_runs(finish_name):
    box = (20, 10, 596, 130)
    renderer, image = drawn(widget(finish=finish_name))
    assert renderer.warnings == [] and lit_pixels(image, box) > 3000
    _, none = drawn(widget(finish=finish_name), snapshot(text="null"))
    assert lit_pixels(none, box) == 0
    _, absent = drawn(widget(finish=finish_name), Snapshot())
    assert lit_pixels(absent, box) == 0
    _, broken = drawn(widget(finish=finish_name), snapshot(text="{not json"))
    assert lit_pixels(broken, box) == 0


def test_the_empty_text_stands_where_nothing_downloads():
    box = (20, 10, 596, 130)
    _, image = drawn(widget(empty="No download running"), Snapshot())
    assert lit_pixels(image, box) > 200
    _, with_state = drawn(widget(empty="No download running"))
    assert differs(image, with_state)
    _, nothing = drawn(widget(empty=""), snapshot(text="null"))
    assert lit_pixels(nothing, box) == 0


def test_the_progress_follows_the_bytes_and_each_phase_looks_its_own():
    quarter = drawn(widget(), snapshot({**STATE, "bytes": 6 * GB}))[1]
    half = drawn(widget(), snapshot({**STATE, "bytes": 12 * GB}))[1]
    assert differs(quarter, half)
    again = drawn(widget(), snapshot({**STATE, "bytes": 6 * GB}))[1]
    assert not differs(quarter, again)
    paused = drawn(widget(), snapshot({**STATE, "phase": "paused"}))[1]
    done = drawn(widget(), snapshot({**STATE, "phase": "done", "bytes": 24 * GB}))[1]
    assert differs(paused, quarter) and differs(done, quarter) and differs(done, paused)


def test_the_graph_is_the_history():
    with_graph = drawn(widget(), snapshot(STATE))[1]
    without = drawn(widget(), snapshot({**STATE, "history": []}))[1]
    assert differs(with_graph, without)
    narrow = drawn(widget(w=300, h=100), snapshot(STATE))[1]
    assert lit_pixels(narrow, (20, 10, 300, 100)) > 1000  # no room for it: the rest remains


@pytest.mark.parametrize("finish_name", ["flat", "lit"])
def test_a_download_of_unknown_size_runs_a_stretch_of_light(finish_name):
    state = {**STATE, "total": None, "remaining_s": None, "source": "browser"}
    w = widget(finish=finish_name)
    renderer = Renderer(theme_of(w), animate=True)
    first, _ = renderer.render(snapshot(state), 0.0), None
    assert renderer.moving
    a = renderer.render(snapshot(state), 0.3)[0].copy()
    b = renderer.render(snapshot(state), 1.2)[0].copy()
    c = renderer.render(snapshot(state), 0.3 + download.RUN)[0].copy()
    assert differs(a, b) and not differs(a, c)  # it comes round: the time is the frame's
    assert first is not None
    still = Renderer(theme_of(w), animate=False)
    still.render(snapshot(state), 0.3)
    assert not still.moving
    paused = Renderer(theme_of(w), animate=True)
    paused.render(snapshot({**state, "phase": "paused"}), 0.3)
    assert not paused.moving  # a paused download stands


def test_a_download_of_known_size_is_drawn_once_per_state():
    w = widget(finish="lit")
    renderer = Renderer(theme_of(w), animate=True)
    renderer.render(snapshot(), 0.0)
    first = renderer._cache["d"][1]
    renderer.render(snapshot(), 0.5)
    assert renderer._cache["d"][1] is first and not renderer.moving
    renderer.render(snapshot({**STATE, "rate": 41 * MB}), 1.0)
    assert renderer._cache["d"][1] is not first


def test_the_lit_finish_is_registered_for_it_and_adapts_to_any_size():
    assert finish.get("lit", "downloads") is not None
    assert finish.supports({"type": "downloads", "finish": "lit"})
    for size in ((60, 30), (30, 90), (1000, 100), (200, 400)):
        w, h = size
        renderer, image = drawn(widget(w=w, h=h, finish="lit"), size=(max(w, 640) + 40, h + 40))
        assert renderer.warnings == [] and image.size == (max(w, 640) + 40, h + 40)


def test_a_theme_can_name_another_state():
    w = widget(state="mine")
    snap = Snapshot(readings={"mine": Reading("mine", json.dumps(STATE), "", "", "", 0.0)})
    assert lit_pixels(drawn(w, snap)[1], (20, 10, 596, 130)) > 3000
    assert lit_pixels(drawn(w, snapshot())[1], (20, 10, 596, 130)) == 0


def test_pictures_do_not_leak_between_widgets():
    a = widget(id="a", x=0, y=0, w=300, h=140)
    b = widget(id="b", x=320, y=0, w=300, h=140, color2="#f87171")
    renderer = Renderer(theme_of(a, b, size=(640, 150)), animate=False)
    image, _ = renderer.render(snapshot(), 1.0)
    left = image.crop((0, 0, 310, 150))
    right = image.crop((320, 0, 630, 150))
    assert differs(left, right) and isinstance(left, Image.Image)
