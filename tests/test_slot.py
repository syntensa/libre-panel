"""The card slot: which card is on show (rules, a paused track's hold time), how a change moves
(the old card rolls up into a line of light, the new one rolls down out of it, on the frame's
planned time), the frame's colour and the helpers that cut a piece to rows."""

import copy
import json
import re

import numpy as np
import pytest
from PIL import Image

from libre_panel.render import cover, light, slot
from libre_panel.render.renderer import Piece, Renderer
from libre_panel.sensors.base import Reading
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme import modules as M
from libre_panel.theme.model import parse_theme

MB = 1024 * 1024
RUNNING = {
    "id": "steam:1", "source": "steam", "name": "Space Game", "phase": "downloading",
    "bytes": 3 * 1024 * MB, "total": 20 * 1024 * MB, "rate": 30 * MB, "remaining_s": 300.0,
    "elapsed_s": 60.0, "estimated": False, "history": [30 * MB] * 60, "more": 0,
}  # fmt: skip


def theme_of(items="", kind="cards", cols=3, rows=2, **more):
    module = {"type": "module", "id": "m", "module": kind, "cols": cols, "rows": rows, **more}
    if items:
        module["items"] = items
    return parse_theme(
        {
            "format": "libre-panel-theme/1",
            "display": {"model": "turing-9.2-usb", "orientation": "landscape"},
            "widgets": [module],
        }
    )


def demo(playing=1, media=True, weather=True):
    snap = demo_snapshot(fixed_time=1_700_000_000)
    if not media:
        for key in [k for k in snap.readings if k.startswith("media.")]:
            del snap.readings[key]
        snap.images.pop("media.cover", None)
    else:
        snap.readings["media.playing"] = Reading("media.playing", playing)
    if not weather:
        for key in [k for k in snap.readings if k.startswith("weather.")]:
            del snap.readings[key]
    return snap


def downloading(snap, state=RUNNING):
    snap = copy.deepcopy(snap)
    text = "null" if state is None else json.dumps(state)
    snap.readings["downloads"] = Reading("downloads", text, "", "", "", 0.0)
    return snap


def parts(renderer):
    """The ids of the parts of the last frame, without the module's name."""
    names = [wid.split("/", 1)[1] for wid, _ in renderer._last[0] if "/" in wid]
    return [name for name in names if not name.startswith("\0")]


def region(image, box, top, bottom):
    x, y, w, h = box
    return np.asarray(image.convert("RGB").crop((x + 50, round(top), x + w - 50, round(bottom))))


def empty(pixels):
    """Nothing is drawn there: the card's surface only (a flat colour)."""
    return pixels.size == 0 or int(np.ptp(pixels.reshape(-1, 3), axis=0).max()) <= 4


# -- which card ------------------------------------------------------------------------------


def test_the_cards_of_a_slot_are_its_items_and_by_default_playback_and_weather():
    assert M.slot_items({"module": "cards", "items": ""}) == [("playback", ""), ("forecast", "")]
    assert M.slot_items({"module": "netcard", "items": "ignored"}) == [
        ("download", ""),
        ("net", ""),
    ]
    lines = (
        "download # when one runs\n\nnonsense\nforecast if weather.temperature,!media.title\n net"
    )
    assert M.slot_items({"module": "cards", "items": lines}) == [
        ("download", ""), ("forecast", "weather.temperature,!media.title"), ("net", ""),
    ]  # fmt: skip
    assert M.slot_items({"module": "cards", "items": "nonsense\ncards"}) == [
        ("playback", ""), ("forecast", ""),
    ]  # fmt: skip


def test_a_slot_is_made_of_cards_that_are_parts_of_it():
    renderer = Renderer(theme_of(), preview=True)
    cards = {w["_card"] for w in renderer.widgets if w.get("_module") == "m" and "_card" in w}
    assert cards == {0, 1}
    ids = [w["id"] for w in renderer.widgets]
    assert len(ids) == len(set(ids))
    assert all(w["id"].startswith(f"m/c{w['_card']}-") for w in renderer.widgets if "_card" in w)
    assert "m/card" in ids  # the slot's own surface


def test_the_first_card_that_has_something_to_show_is_on_show():
    renderer = Renderer(theme_of(), preview=True)
    box = renderer._slots["m"]
    renderer.render(demo(), 0.0)
    assert box.shown == 0 and all(p.startswith("c0-") or p == "card" for p in parts(renderer))
    renderer.render(demo(playing=0), 1.0)
    assert box.shown == 0  # paused a moment ago: it stays
    renderer.render(demo(media=False), 2.0)
    assert box.shown == 1 and all(p.startswith("c1-") or p == "card" for p in parts(renderer))
    renderer.render(demo(), 3.0)
    assert box.shown == 0  # a track plays again


def test_a_paused_track_stays_on_show_for_the_hold_time():
    renderer = Renderer(theme_of(hold=1), preview=True)  # a minute
    box = renderer._slots["m"]
    assert box.hold == 60
    renderer.render(demo(), 0.0)
    renderer.render(demo(playing=0), 10.0)  # paused at 10
    renderer.render(demo(playing=0), 69.0)
    assert box.shown == 0
    renderer.render(demo(playing=0), 71.0)
    assert box.shown == 1  # a minute and a second: the weather
    renderer.render(demo(playing=1), 80.0)
    assert box.shown == 0
    renderer.render(demo(playing=0), 85.0)  # paused again: the time counts anew
    renderer.render(demo(playing=0), 140.0)
    assert box.shown == 0
    renderer.render(demo(playing=0), 146.0)
    assert box.shown == 1


def test_a_new_track_that_is_paused_has_its_own_hold_time():
    renderer = Renderer(theme_of(hold=1), preview=True)
    box = renderer._slots["m"]
    renderer.render(demo(playing=0), 0.0)
    renderer.render(demo(playing=0), 59.0)
    assert box.shown == 0
    other = demo(playing=0)
    other.readings["media.title"] = Reading("media.title", "Another Song")
    renderer.render(other, 100.0)
    assert box.shown == 0  # a different track: its pause began just now
    renderer.render(other, 130.0)
    assert box.shown == 0
    renderer.render(other, 161.0)
    assert box.shown == 1


def test_without_anything_to_show_the_first_card_shows_in_its_empty_look():
    renderer = Renderer(theme_of(), preview=True)
    renderer.render(demo(media=False, weather=False), 0.0)
    assert renderer._slots["m"].shown == 0
    names = parts(renderer)
    assert "c0-idle" in names and "c0-title" not in names


def test_a_slot_may_name_a_rule_of_its_own():
    items = "forecast if cpu.load>90\nplayback if media.playing=1\nnet"
    renderer = Renderer(theme_of(items), preview=True)
    box = renderer._slots["m"]
    renderer.render(demo(), 0.0)
    assert box.shown == 1  # the cpu is not above 90: playback
    busy = demo()
    busy.readings["cpu.load"] = Reading("cpu.load", 95.0)
    renderer.render(busy, 1.0)
    assert box.shown == 0
    renderer.render(demo(media=False), 2.0)
    assert box.shown == 2  # nothing else holds: the last card here, that holds always


def test_a_network_card_shows_a_download_while_one_runs():
    renderer = Renderer(theme_of(kind="netcard", cols=2, rows=1), preview=True)
    box = renderer._slots["m"]
    renderer.render(demo(), 0.0)
    assert box.kinds == ["download", "net"] and box.shown == 1
    renderer.render(downloading(demo()), 1.0)
    assert box.shown == 0
    renderer.render(downloading(demo(), None), 2.0)  # the service says: none (a null state)
    assert box.shown == 1
    renderer.render(downloading(demo(), {**RUNNING, "phase": "done"}), 3.0)
    assert box.shown == 0  # a finished one shows for a while


def test_the_first_frame_draws_every_card_once_and_shows_one():
    renderer = Renderer(theme_of(), animate=True)
    renderer.render(demo(), 0.0)
    box = renderer._slots["m"]
    drawn = {wid.split("/")[1][:2] for wid in renderer._cache if re.match(r"m/c\d-", wid)}
    assert drawn == {"c0", "c1"}  # the weather card is made, ready for the first change
    assert box.shown == 0 and box.leaving is None and not box.frozen and box.pinned is None
    assert all(p.startswith("c0-") or p == "card" for p in parts(renderer))
    renderer.render(demo(media=False), 1.0)
    assert box.leaving == 0  # and the first change is a change: the first frame was no change
    still = Renderer(theme_of(), preview=True)  # a picture draws what it shows, no more
    still.render(demo(), 0.0)
    assert not any(wid.startswith("m/c1-") for wid in still._cache)


def test_a_slot_with_one_card_never_changes():
    renderer = Renderer(theme_of("forecast"), animate=True)
    renderer.render(demo(), 0.0)
    renderer.render(demo(media=False, weather=False), 1.0)
    box = renderer._slots["m"]
    assert box.shown == 0 and box.leaving is None and not renderer.moving


# -- a change --------------------------------------------------------------------------------


def started(items="", **more):
    """A renderer that has shown the playback card, and the weather's snapshot for the change."""
    renderer = Renderer(theme_of(items, **more), animate=True)
    renderer.render(demo(), 0.0)
    renderer.render(demo(), 0.5)
    return renderer, demo(media=False)


def test_a_change_rolls_the_old_card_up_and_the_new_one_down():
    renderer, weather = started()
    box = renderer._slots["m"]
    assert box.change == 0.6
    height = box._inner()[3]
    for p in (0.0, 0.1, 0.2, 0.3, 0.4, 0.45, 0.6, 0.8, 0.99):
        renderer.render(weather, 1.0 + p * box.change)
        up, down = box.rows
        want_up = height * (1 - light.ease_in(p / slot.ROLL_UP)) if p < slot.ROLL_UP else 0.0
        want_down = (
            height * light.ease_out((p - slot.ROLL_DOWN) / (1 - slot.ROLL_DOWN))
            if p > slot.ROLL_DOWN else 0.0
        )  # fmt: skip
        assert up == pytest.approx(want_up) and down == pytest.approx(want_down), p
        assert box.leaving == 0 and box.shown == 1 and renderer.moving
    renderer.render(weather, 1.0 + box.change)  # over
    assert box.leaving is None and box.rows is None and box.frozen == {}


def test_the_rolling_shows_in_the_pixels():
    renderer, weather = started()
    box = renderer._slots["m"]
    cell = renderer.module_boxes["m"]
    top, height = box._inner()[1], box._inner()[3]
    lower = (top + 0.78 * height, top + 0.93 * height)  # the progress and the times
    steady = renderer.render(demo(), 0.9)[0]
    assert not empty(region(steady, cell, *lower))
    first = renderer.render(weather, 1.0)[0]  # p = 0: all of the old card is still there
    assert not empty(region(first, cell, *lower))
    early = renderer.render(weather, 1.0 + 0.3 * 0.6)[0]  # p = 0.3
    rolled = height * (1 - light.ease_in(0.3 / slot.ROLL_UP))
    assert empty(region(early, cell, top + rolled + 4, top + height - 2))
    assert not empty(region(early, cell, top + 4, top + rolled - 4))
    just = 1.0 + 0.6 * (slot.ROLL_UP + 0.002)  # the old card has rolled up, the new one has begun
    gone = renderer.render(weather, just)[0]
    reached = height * light.ease_out(
        (slot.ROLL_UP + 0.002 - slot.ROLL_DOWN) / (1 - slot.ROLL_DOWN)
    )
    assert box.rows[0] == 0.0 and box.rows[1] == pytest.approx(reached)
    assert empty(region(gone, cell, top + reached + 6, top + height - 2))
    down = renderer.render(weather, 1.0 + 0.7 * 0.6)[0]  # p = 0.7: the new one comes down
    reached = height * light.ease_out((0.7 - slot.ROLL_DOWN) / (1 - slot.ROLL_DOWN))
    assert not empty(region(down, cell, top + 4, top + 0.3 * height))
    assert empty(region(down, cell, top + reached + 6, top + height - 2))


def test_the_line_of_light_ignites_and_dies_away():
    renderer, weather = started()
    box = renderer._slots["m"]
    strengths = []
    for p in (0.0, 0.1, 0.2, 0.3, 0.42, 0.6, 0.8, 1.0):
        renderer.render(weather, 1.0 + p * box.change)
        strengths.append(box.line)
    assert strengths[0] == 0.0 and strengths[-1] == pytest.approx(0.0, abs=1e-9)
    peak = max(range(len(strengths)), key=strengths.__getitem__)
    assert strengths[peak] == pytest.approx(slot.GLOW * light.ease_out(1.0)) and peak == 4
    assert all(a <= b for a, b in zip(strengths[:5], strengths[1:5], strict=False))
    assert all(a >= b for a, b in zip(strengths[4:], strengths[5:], strict=False))
    renderer, weather = started()  # (again: time does not run back)
    x, y, w, h = renderer.module_boxes["m"]
    renderer.render(weather, 1.0)  # it begins
    renderer.render(weather, 1.0 + 0.42 * box.change)
    ids = [wid for wid, _ in renderer._last[0]]
    assert "m/\0line" in ids and ids.index("m/\0line") > ids.index("m/\0frame")
    line = dict(renderer._last[0])["m/\0line"]
    assert line.add and line.layer.width == w
    renderer.render(weather, 2.0)
    assert "m/\0line" not in [wid for wid, _ in renderer._last[0]]


def test_a_change_takes_the_planned_time_not_the_number_of_frames():
    a, weather = started()
    b, _ = started()
    for t in (1.0, 1.1, 1.2, 1.3):  # a frame now and then ...
        a.render(weather, t)
    b.render(weather, 1.0)
    b.render(weather, 1.3)  # ... or two: the same place at the same time
    sa, sb = a._slots["m"], b._slots["m"]
    assert sa.rows == pytest.approx(sb.rows) and sa.line == pytest.approx(sb.line)
    a.render(weather, 1.0 + 0.6)
    b.render(weather, 1.0 + 0.6)
    assert sa.leaving is None and sb.leaving is None


def test_a_change_on_its_way_is_finished_before_the_next_begins():
    renderer, weather = started()
    box = renderer._slots["m"]
    renderer.render(weather, 1.0)
    assert (box.leaving, box.shown) == (0, 1)
    renderer.render(demo(), 1.2)  # the track plays again, mid-change
    assert (box.leaving, box.shown) == (0, 1)
    renderer.render(demo(), 1.7)  # the change is over: the next begins at once
    assert (box.leaving, box.shown) == (1, 0) and box.since == 1.7
    renderer.render(demo(), 2.4)
    assert box.leaving is None and box.shown == 0


def test_a_change_needs_a_panel_that_animates_and_a_card_that_was_shown():
    cut = Renderer(theme_of(), animate=False)
    cut.render(demo(), 0.0)
    cut.render(demo(media=False), 1.0)
    box = cut._slots["m"]
    assert box.shown == 1 and box.leaving is None and not cut.moving  # the PNG path: a cut
    quick = Renderer(theme_of(change=0), animate=True)
    quick.render(demo(), 0.0)
    quick.render(demo(media=False), 1.0)
    assert quick._slots["m"].shown == 1 and quick._slots["m"].leaving is None
    fresh = Renderer(theme_of(), animate=True)
    fresh.render(demo(media=False), 0.0)  # the first frame: no card has been on show before
    assert fresh._slots["m"].shown == 1 and fresh._slots["m"].leaving is None


def test_the_slot_moves_only_while_it_changes():
    renderer = Renderer(theme_of(moving=False), animate=True)  # (symbols that stand)
    renderer.render(demo(), 0.0)
    weather = demo(media=False)
    renderer.render(weather, 1.0)
    assert renderer.moving  # a change is under way
    renderer.render(weather, 1.0 + 0.3)
    assert renderer.moving
    renderer.render(weather, 3.0)  # the change and the frame's colour are over
    assert not renderer.moving


def test_what_the_old_card_showed_is_what_rolls_up():
    renderer, weather = started()
    box = renderer._slots["m"]
    before = {k: v for k, v in box.last.items()}
    assert any(k.startswith("m/c0-title") for k in before)
    renderer.render(weather, 1.0)  # the title is gone from the readings, yet it rolls up
    assert box.frozen.keys() == before.keys()
    ids = [wid for wid, _ in renderer._last[0]]
    assert "m/c0-title" in ids and "m/c0-progress" in ids
    assert not any(wid.startswith("m/c1-") for wid in ids)  # nothing of the new one yet


# -- the frame -------------------------------------------------------------------------------


def frame_colour(renderer):
    piece = dict(renderer._last[0])["m/\0frame"]
    x, y, w, h = piece.box
    return tuple(piece.layer.getpixel((w // 2, 0))[:3])


def test_the_frame_takes_the_colour_of_the_card_on_show():
    renderer = Renderer(theme_of(), animate=True)
    box = renderer._slots["m"]
    snap = demo()
    renderer.render(snap, 0.0)
    carried = cover.dominant(snap.images["media.cover"])
    assert carried is not None
    ground = M._rgb(M.colors_of(renderer.theme.palette)["bg"])
    assert frame_colour(renderer) == light.mix(carried, ground, slot.MIXED)
    renderer.render(demo(media=False), 1.0)
    weather = box._weather(demo(media=False))
    assert weather == M._rgb(M.colors_of(renderer.theme.palette)["text3"])  # overcast
    renderer.render(demo(media=False), 1.0 + slot.FADE + 0.1)
    assert frame_colour(renderer) == light.mix(weather, ground, slot.MIXED)


def test_the_frame_changes_its_colour_over_a_while_and_never_jumps():
    renderer = Renderer(theme_of(), animate=True)
    renderer.render(demo(), 0.0)
    start = frame_colour(renderer)
    weather = demo(media=False)
    renderer.render(weather, 1.0)
    first = frame_colour(renderer)
    renderer.render(weather, 1.0 + slot.FADE / 2)
    middle = frame_colour(renderer)
    renderer.render(weather, 1.0 + slot.FADE + 0.05)
    end = frame_colour(renderer)
    assert first == start  # it begins where it was
    assert len({start, middle, end}) == 3
    for c in range(3):  # and goes there in one direction
        low, high = sorted((start[c], end[c]))
        assert low <= middle[c] <= high


def test_the_weather_has_its_colour_and_a_module_may_set_its_own():
    renderer = Renderer(theme_of(), animate=False)
    box = renderer._slots["m"]
    colors = M.colors_of(renderer.theme.palette)
    day, night = demo(media=False), demo(media=False)
    for snap, code in ((day, 0.0), (night, 0.0)):
        snap.readings["weather.code"] = Reading("weather.code", code)
    day.readings["weather.is_day"] = Reading("weather.is_day", 1)
    night.readings["weather.is_day"] = Reading("weather.is_day", 0)
    assert box._weather(day) == M._rgb(colors["warn"])
    assert box._weather(night) == M._rgb(colors["text2"])
    for code, role in ((61.0, "gpu"), (73.0, "text"), (95.0, "crit"), (45.0, "text3")):
        day.readings["weather.code"] = Reading("weather.code", code)
        assert box._weather(day) == M._rgb(colors[role]), code
    named = parse_theme(
        {
            "format": "libre-panel-theme/1",
            "display": {"model": "turing-9.2-usb"},
            "palette": {"hot": "#ff4400"},
            "widgets": [{"type": "module", "id": "m", "module": "cards", "color": "@hot"}],
        }
    )
    pal = Renderer(named, animate=False)
    pal.render(demo(), 0.0)
    ground = M._rgb(M.colors_of(pal.theme.palette)["bg"])
    assert frame_colour(pal) == light.mix((255, 68, 0), ground, slot.MIXED)  # (a palette entry)
    own = Renderer(theme_of(color="#ff8800"), animate=False)
    own.render(demo(), 0.0)
    ground = M._rgb(M.colors_of(own.theme.palette)["bg"])
    assert frame_colour(own) == light.mix((255, 136, 0), ground, slot.MIXED)


def test_the_frame_is_a_flat_thin_line_and_follows_the_style():
    renderer = Renderer(theme_of(), animate=False)
    renderer.render(demo(), 0.0)
    box = renderer._slots["m"]
    piece = dict(renderer._last[0])["m/\0frame"]
    alpha = np.asarray(piece.layer.getchannel("A"))
    h, w = alpha.shape
    assert (piece.x, piece.y, w, h) == tuple(renderer.module_boxes["m"])
    assert box.width == max(1, round(min(w, h) / 140))
    column = alpha[:, w // 2]
    assert int((column > 127).sum()) == 2 * box.width  # one line at the top, one at the bottom
    assert column[: box.width].min() >= 200 and column[box.width + 2 : -box.width - 2].max() <= 3
    assert int(alpha[60:-60, 60:-60].max()) <= 3  # nothing inside: no fill, no bevel, no trough
    colours = {tuple(px[:3]) for px in np.asarray(piece.layer).reshape(-1, 4) if px[3] > 250}
    assert len(colours) == 1  # one flat colour
    flat = Renderer(
        parse_theme({
            "format": "libre-panel-theme/1", "display": {"model": "turing-9.2-usb"},
            "style": {"card": "flat"},
            "widgets": [{"type": "module", "id": "m", "module": "cards", "cols": 3, "rows": 2}],
        }),
        animate=False,
    )  # fmt: skip
    flat.render(demo(), 0.0)
    assert "m/\0frame" not in [wid for wid, _ in flat._last[0]]  # no outline in this style


# -- cutting a piece to rows -----------------------------------------------------------------


def solid(x=10, y=100, w=20, h=10, colour=(200, 100, 50, 255), add=False):
    return Piece(Image.new("RGBA", (w, h), colour), x, y, [x, y, w, h], add=add)


def test_a_window_keeps_the_rows_between_top_and_bottom():
    cut = slot.window(solid(), 103, 107)
    assert (cut.y, cut.layer.height, cut.x, cut.layer.width) == (103, 4, 10, 20)
    assert cut.layer.getpixel((0, 0))[3] == 255 and cut.layer.getpixel((0, 3))[3] == 255
    assert slot.window(solid(), 90, 100) is None  # above it
    assert slot.window(solid(), 110, 120) is None  # below it
    assert slot.window(solid(), 107, 103) is None
    assert slot.window(None, 0, 10) is None
    whole = slot.window(solid(), 0, 1000)
    assert (whole.y, whole.layer.height) == (100, 10)


def test_a_row_that_is_cut_shows_its_share():
    cut = slot.window(solid(), 100, 103.25)
    alphas = [cut.layer.getpixel((0, row))[3] for row in range(cut.layer.height)]
    assert alphas[:3] == [255, 255, 255] and alphas[3] == pytest.approx(64, abs=2)
    cut = slot.window(solid(), 100.5, 109)
    assert cut.y == 100 and cut.layer.getpixel((0, 0))[3] == pytest.approx(128, abs=2)
    assert cut.layer.getpixel((0, cut.layer.height - 1))[3] == 255
    thin = slot.window(solid(), 104.25, 104.75)  # inside one row
    assert thin.layer.height == 1 and thin.layer.getpixel((0, 0))[3] == pytest.approx(128, abs=2)


def test_light_that_is_cut_is_dimmed_in_its_colour_and_its_glow_is_cut_too():
    glow = solid(x=0, y=96, w=40, h=20, colour=(100, 80, 60, 255), add=True)
    piece = Piece(Image.new("RGBA", (20, 10), (200, 100, 50, 255)), 10, 100, light=glow)
    cut = slot.window(piece, 100, 102.5)
    assert cut.light is not None and cut.light.y == 100 and cut.light.layer.height == 3
    assert cut.light.layer.getpixel((0, 2))[:3] == pytest.approx((50, 40, 30), abs=2)
    added = slot.window(solid(add=True), 100, 100.5)
    assert added.layer.getpixel((0, 0))[:3] == pytest.approx((100, 50, 25), abs=2)
    only_glow = slot.window(piece, 90, 99)  # the piece is out of the window, its glow is in
    assert only_glow is not None and only_glow.light.y == 96 and only_glow.layer.size == (1, 1)


def test_a_cut_piece_is_a_new_one_and_the_old_stays_as_it_was():
    piece = solid()
    slot.window(piece, 100, 104.5)
    assert piece.layer.height == 10 and piece.layer.getpixel((0, 4))[3] == 255


# -- the colour of a cover -------------------------------------------------------------------


def picture(colour, size=(64, 64)):
    return Image.new("RGB", size, colour)


def test_a_cover_carries_its_strongest_large_colour():
    red = cover.dominant(picture((200, 30, 40)))
    assert red is not None and red[0] > red[1] and red[0] > red[2] and max(red) >= cover.BRIGHT
    two = Image.new("RGB", (64, 64), (90, 90, 95))  # a grey ground ...
    two.paste((20, 200, 90), (0, 0, 24, 64))  # ... and a green stripe that is a third of it
    green = cover.dominant(two)
    assert green is not None and green[1] > green[0] and green[1] > green[2]


def test_a_cover_of_greys_or_darkness_has_no_colour_and_a_dark_one_is_brightened():
    assert cover.dominant(picture((128, 128, 128))) is None
    assert cover.dominant(picture((20, 10, 10))) is None
    assert cover.dominant(None) is None
    dim = cover.dominant(picture((70, 20, 20)))
    assert dim is not None and max(dim) >= cover.BRIGHT
    assert dim[0] > dim[1]  # (still red)


def test_a_cover_is_looked_at_once():
    image = picture((10, 160, 200))
    first = cover.dominant(image)
    assert cover.dominant(image) is first
    for i in range(cover.KEPT + 2):
        cover.dominant(picture((200, 10 + i, 10)))
    assert cover.dominant(image) == first  # (looked at again, the same answer)
    assert len(cover._kept) <= cover.KEPT


# -- the slot in a theme ---------------------------------------------------------------------


@pytest.mark.parametrize("size", [(1, 1), (2, 1), (1, 2), (2, 2), (3, 1), (4, 2)])
@pytest.mark.parametrize("kind", ["cards", "netcard"])
def test_a_slot_draws_with_and_without_readings(kind, size):
    renderer = Renderer(theme_of(kind=kind, cols=size[0], rows=size[1]), animate=True)
    odd = {"phase": "x", "history": "no"}
    nothing = demo()
    nothing.readings.clear()
    for snap in (demo(), demo(media=False, weather=False), downloading(demo()), nothing,
                 downloading(demo(), odd)):  # fmt: skip
        frame, boxes = renderer.render(snap, 1.0)
        assert renderer.warnings == [] and "m" in boxes
        assert frame.size == (1920, 480)


def test_a_slot_stays_in_its_cells():
    renderer = Renderer(theme_of(), animate=True)
    x, y, w, h = renderer.module_boxes["m"]
    for widget in renderer.widgets:
        if widget.get("_module") != "m" or "w" not in widget or widget["type"] == "rect":
            continue
        assert x <= widget["x"] and widget["x"] + widget["w"] <= x + w + 1, widget["id"]
        assert y <= widget["y"] and widget["y"] + widget["h"] <= y + h + 1, widget["id"]


def test_a_theme_with_a_slot_survives_a_round_trip_and_detaches_its_first_card():
    t = theme_of("download\nforecast")
    again = parse_theme(json.loads(json.dumps(t.to_dict())))
    assert [w["id"] for w in again.widgets] == [w["id"] for w in t.widgets]
    plain = M.detach(t, "m")
    assert plain and all("_card" not in w and "_module" not in w for w in plain)
    assert any(w["type"] == "downloads" for w in plain) and not any(
        w["type"] == "symbol" for w in plain
    )
