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


def theme_of(items="playback\nforecast", kind="cards", cols=3, rows=2, **more):
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


def demo(playing=1, media=True, weather=True, agents=True):
    snap = demo_snapshot(fixed_time=1_700_000_000)
    if not agents:  # (the demo's Claude Code works, its Codex waits for you)
        for key in [k for k in snap.readings if k.startswith("ai.")]:
            del snap.readings[key]
    snap.readings.pop("downloads", None)  # (these tests start a download, with ``downloading``)
    if not media:
        for key in [k for k in snap.readings if k.startswith("media.")]:
            del snap.readings[key]
        snap.images.pop("media.cover", None)
        snap.images.pop("media.app_icon", None)
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


def test_the_cards_of_a_slot_are_its_items_and_by_default_the_persons_first_choice():
    default = [("playback", ""), ("ai", ""), ("forecast", ""), ("date", "")]
    assert M.slot_items({"module": "cards", "items": ""}) == default  # (config.DEFAULT_CARDS)
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
    assert M.slot_items({"module": "cards", "items": "nonsense\ncards"}) == default


def test_a_card_may_name_the_time_of_its_turn():
    assert M.card_line("forecast for 5m if weather.temperature # the weather") == M.SlotCard(
        "forecast", "weather.temperature", 300.0
    )
    times = {"5m": 300, "90s": 90, "1h": 3600, "2.5 min": 150, "10": 600, "30 sec": 30}
    for text, seconds in times.items():
        assert M.card_line(f"date for {text}").seconds == seconds, text
        assert M.card_time(M.time_text(seconds)) == seconds
    for wrong in ("soon", "2s", "25h", "-5m", "inf", "5 days"):
        assert M.card_line(f"date for {wrong}") == M.SlotCard("date"), wrong
    assert M.card_line("nonsense for 5m") is None
    assert M.slot_cards({"module": "cards", "items": "clock for 1m\nnet"}) == [
        M.SlotCard("clock", "", 60.0), M.SlotCard("net")
    ]  # fmt: skip
    assert M.slot_items({"module": "cards", "items": "clock for 1m if a"}) == [("clock", "a")]


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


# -- other ways from one card to the next ----------------------------------------------------


def card_pieces(renderer, card):
    """The pieces of card ``card`` in the last frame."""
    return [p for wid, p in renderer._last[0] if wid.startswith(f"m/c{card}-") and "\0" not in wid]


def test_a_fade_takes_the_old_card_out_and_brings_the_new_one_in():
    renderer, weather = started(transition="fade")
    box = renderer._slots["m"]
    renderer.render(weather, 1.0)
    assert (box.leaving, box.shown, box.effect) == (0, 1, "fade")
    for p in (0.1, 0.3, 0.5, 0.7, 0.9):
        renderer.render(weather, 1.0 + p * box.change)
        old = 1 - light.smooth(min(1.0, p / slot.FADE_OUT))
        new = light.smooth(max(0.0, (p - slot.FADE_IN) / (1 - slot.FADE_IN)))
        assert box.alpha == pytest.approx((old, new)), p
        assert box.rows is None and box.line == 0.0  # no rolling, no line of light
        for piece in card_pieces(renderer, 0):  # the old card, at its share
            alpha = piece.layer.getchannel("A") if not piece.add else piece.layer.convert("L")
            assert alpha.getextrema()[1] <= round(255 * old) + 1, p
        if new <= 0.004:
            assert card_pieces(renderer, 1) == [], p
    assert "m/\0line" not in [wid for wid, _ in renderer._last[0]]
    renderer.render(weather, 1.0 + box.change)
    assert box.leaving is None and box.alpha is None


@pytest.mark.parametrize("transition", ["slide", "rise"])
def test_the_new_card_pushes_the_old_one_out_and_both_stay_in_the_card(transition):
    renderer, weather = started(transition=transition)
    box = renderer._slots["m"]
    x, y, w, h = box._inner()
    reach = round(w if transition == "slide" else h) + round(slot.GAP * renderer.theme.height / 480)
    renderer.render(weather, 1.0)
    assert box.push == (0, reach)  # the old card where it was, the new one a reach away
    steady = {id(p): p for p in box.frozen.values()}
    for p in (0.25, 0.5, 0.75):
        renderer.render(weather, 1.0 + p * box.change)
        away = round(reach * light.smooth(p))
        assert box.push == (-away, reach - away)
        for card in (0, 1):
            for piece in card_pieces(renderer, card):
                if transition == "slide":
                    assert x - 1 <= piece.x and piece.x + piece.layer.width <= x + w + 1
                else:
                    assert y - 1 <= piece.y and piece.y + piece.layer.height <= y + h + 1
    assert steady  # (what the old card showed is what moves)
    renderer.render(weather, 1.0 + box.change)
    assert box.leaving is None and box.push is None


def test_a_wipe_shows_the_new_card_left_of_its_edge_and_the_old_one_right_of_it():
    renderer, weather = started(transition="wipe")
    box = renderer._slots["m"]
    x, _, w, _ = box._inner()
    renderer.render(weather, 1.0)
    renderer.render(weather, 1.0 + 0.5 * box.change)
    edge = round(x + w * light.smooth(0.5))
    assert round(box.edge) == edge
    assert all(p.x + p.layer.width <= edge for p in card_pieces(renderer, 1))
    assert all(p.x >= edge for p in card_pieces(renderer, 0))
    assert card_pieces(renderer, 0) and card_pieces(renderer, 1)


def test_a_cut_changes_the_card_at_once():
    renderer, weather = started(transition="cut")
    box = renderer._slots["m"]
    renderer.render(weather, 1.0)
    assert (box.leaving, box.shown) == (None, 1) and not card_pieces(renderer, 0)


def test_random_takes_one_of_the_moving_ways_never_the_same_twice_running():
    renderer = Renderer(theme_of(transition="random"), animate=True)
    box = renderer._slots["m"]
    renderer.render(demo(), 0.0)
    effects = []
    for i in range(12):
        renderer.render(demo(media=bool(i % 2)), 1.0 + i)  # a change every second
        effects.append(box.effect)
    assert set(effects) <= set(slot.MOVING) and len(set(effects)) > 1
    assert all(a != b for a, b in zip(effects, effects[1:], strict=False))


def test_an_unknown_transition_or_order_is_the_first_one():
    renderer = Renderer(theme_of(), animate=True)
    box = renderer._slots["m"]
    module = {**box.module, "transition": "spin", "order": "shuffle", "turn": "soon"}
    other = slot.Slot(renderer, module, M.slot_cards(module), box.box)
    assert (other.transition, other.order, other.turns) == ("light", "first", [300.0, 300.0])


def test_a_faded_piece_keeps_its_colour_or_its_light_dims_and_its_glow_with_it():
    glow = solid(x=0, y=96, w=40, h=20, colour=(100, 80, 60, 255), add=True)
    piece = Piece(Image.new("RGBA", (20, 10), (200, 100, 50, 255)), 10, 100, light=glow)
    half = slot.faded(piece, 0.5)
    assert half.layer.getpixel((0, 0)) == pytest.approx((200, 100, 50, 128), abs=1)
    assert half.light.layer.getpixel((0, 0))[:3] == pytest.approx((50, 40, 30), abs=1)
    assert slot.faded(piece, 0.0) is None and slot.faded(piece, 1.0) is piece
    assert piece.layer.getpixel((0, 0))[3] == 255  # (the piece stays as it was)
    moved = slot.moved(piece, 5, -3)
    assert (moved.x, moved.y, moved.light.x, moved.light.y) == (15, 97, 5, 93)


# -- cards in turn ----------------------------------------------------------------------------


def turns(items, animate=True, **more):
    return Renderer(theme_of(items, order="turns", **more), animate=animate)


def test_in_turn_each_card_is_on_show_for_its_time_then_the_next():
    renderer = turns("forecast for 10s\ndate for 20s\nanalog", turn=0.5)  # (analog: 30 s)
    box = renderer._slots["m"]
    assert box.turns == [10.0, 20.0, 30.0]
    snap = demo()
    shown = []
    for t in (0.0, 9.9, 10.0, 10.7, 29.9, 30.0, 30.7, 59.9, 60.0, 60.7):
        renderer.render(snap, t)
        shown.append(box.shown)
    assert shown == [0, 0, 1, 1, 1, 2, 2, 2, 0, 0]
    renderer.render(snap, 60.1)
    assert box.leaving is None  # (the change at 60 s is over)


def test_in_turn_a_card_with_nothing_to_show_is_passed_over_and_one_that_runs_out_gives_way():
    renderer = turns("playback for 10s\nforecast for 10s\ndate for 10s", animate=False)
    box = renderer._slots["m"]
    quiet = demo(media=False)
    renderer.render(quiet, 0.0)
    assert box.shown == 1  # no music: the weather first
    renderer.render(quiet, 10.0)
    assert box.shown == 2
    renderer.render(quiet, 20.0)
    assert box.shown == 1  # not the music card
    renderer.render(demo(), 25.0)  # music now: it waits for its turn
    assert box.shown == 1
    renderer.render(demo(), 30.0)
    assert box.shown == 2
    renderer.render(demo(), 40.0)
    assert box.shown == 0
    renderer.render(quiet, 41.0)  # the music stops (no track at all): the next at once
    assert box.shown == 1


def test_in_turn_a_card_alone_with_something_to_show_stays_without_a_change():
    renderer = turns("playback for 10s\nforecast for 10s")
    box = renderer._slots["m"]
    weather_only = demo(media=False)
    for t in (0.0, 10.0, 20.0, 35.0):
        renderer.render(weather_only, t)
        assert box.shown == 1 and box.leaving is None, t
    renderer.render(demo(media=False, weather=False), 40.0)  # nothing at all: the first
    assert box.shown == 0


def test_in_turn_the_time_is_the_planned_time_and_the_warm_up_starts_again():
    a = turns("forecast for 10s\ndate for 10s", animate=False)
    b = turns("forecast for 10s\ndate for 10s", animate=False)
    for t in (0.0, 3.0, 6.0, 9.0, 10.0):
        a.render(demo(), t)
    b.render(demo(), 0.0)
    b.render(demo(), 10.0)
    assert a._slots["m"].shown == b._slots["m"].shown == 1
    box = a._slots["m"]
    box.settle()
    assert box.turn_at is None and box.shown == 0
    a.render(demo(), 100.0)
    assert box.shown == 0 and box.turn_at == 100.0


# -- a new track ------------------------------------------------------------------------------

RED, BLUE = (230, 20, 20), (20, 20, 230)


def song(title, colour, artist="Artist"):
    """The demo's snapshot with a track of ``title`` and a cover of one ``colour``."""
    snap = demo()
    snap.readings["media.title"] = Reading("media.title", title)
    snap.readings["media.artist"] = Reading("media.artist", artist)
    snap.images["media.cover"] = Image.new("RGBA", (200, 200), (*colour, 255))
    return snap


def cover_columns(frame, box, colour):
    """The first and last column inside the card where ``colour`` is drawn (the cover, which is
    the one thing of that colour at the card's left), or None."""
    x, y, w, h = (round(v) for v in box._inner())
    inside = np.asarray(frame.convert("RGB").crop((x, y, x + w, y + h))).astype(int)
    near = np.abs(inside - np.array(colour)).max(axis=2) <= 14
    found = np.flatnonzero(near.any(axis=0))
    return (int(found.min()) + x, int(found.max()) + x) if found.size else None


def sliding(items="playback", **more):
    """A renderer that has shown the first track (a red cover) for a second, and the box."""
    renderer = Renderer(theme_of(items, **{"slide": 0.5, **more}), animate=True)
    for t in (0.0, 0.3, 0.6):
        frame = renderer.render(song("First Song", RED), t)[0]
    box = renderer._slots["m"]
    return renderer, box, cover_columns(frame, box, RED)


def test_a_new_track_moves_in_from_the_right_as_the_old_one_moves_out_to_the_left():
    renderer, box, home = sliding()
    assert box.slide == 0.5 and home is not None and box.shift is None
    x, _, width, _ = box._inner()
    reach = width + slot.GAP  # the gap closes as the new track arrives
    new = song("Second Song", BLUE)
    for p in (0.0, 0.1, 0.3, 0.6, 0.9, 0.99):
        frame = renderer.render(new, 1.0 + p * 0.5)[0]
        away = round(reach * light.ease_out(p))
        assert box.shift == (-away, reach - away) and renderer.moving, p
        old, coming = cover_columns(frame, box, RED), cover_columns(frame, box, BLUE)
        if home[1] - away > x:  # (a part of the old cover is still inside the card)
            assert old is not None and old[0] == max(round(x), home[0] - away), p
        else:
            assert old is None, p
        arrives = home[0] + reach - away  # the new cover's left edge, where it comes in from
        if arrives < x + width - 8:
            assert coming is not None and coming[0] == pytest.approx(arrives, abs=2), p
        else:
            assert coming is None or coming[0] >= x + width - 12, p  # not in yet
    frame = renderer.render(new, 1.5)[0]  # over: the new track at rest, the old one gone
    assert box.shift is None and box.sliding is None
    assert cover_columns(frame, box, RED) is None
    assert cover_columns(frame, box, BLUE) == pytest.approx(home, abs=1)
    ids = [wid for wid, _ in renderer._last[0]]
    assert not any(wid.endswith("\0old") for wid in ids)


def test_the_first_frame_of_a_slide_still_shows_the_old_track_whole():
    renderer, box, home = sliding()
    frame = renderer.render(song("Second Song", BLUE), 1.0)[0]  # p = 0: nothing has moved
    assert cover_columns(frame, box, RED) == home and cover_columns(frame, box, BLUE) is None
    assert box.shift == (0, box._inner()[2] + slot.GAP)


def test_the_old_track_is_what_the_card_showed_last_moving_with_its_light_under_the_frame():
    renderer, box, _ = sliding()
    shown = {wid: piece for wid, piece in renderer._last[0] if wid.startswith("m/c0-")}
    renderer.render(song("Second Song", BLUE), 1.0)  # the slide begins ...
    renderer.render(song("Second Song", BLUE), 1.15)  # ... and 0.3 of it later: the old track's
    pieces = dict(renderer._last[0])  # right end is still in, the new track's left end comes in
    ids = [wid for wid, _ in renderer._last[0]]
    old = [wid for wid in ids if wid.endswith("\0old")]
    assert {wid[: -len("\0old")] for wid in old} <= set(shown)  # parts of what was on show
    assert "m/c0-length\0old" in old and "m/c0-cover\0old" not in old  # (the cover is out)
    assert "m/c0-title" in ids and "m/c0-length" not in ids  # the new track's title is coming in
    assert box.sliding is not None and set(box.sliding[1]) >= {"m/c0-title", "m/c0-cover"}
    assert ids.index("m/\0frame") > max(ids.index(wid) for wid in old)  # the frame on top
    assert min(ids.index(wid) for wid in old) > ids.index("m/c0-title")  # over the card's parts
    x, _, w, _ = box._inner()
    for wid in [*old, "m/c0-title"]:
        piece = pieces[wid]  # nothing of it lies outside the card's inside
        assert piece.x >= round(x) and piece.x + piece.layer.width <= round(x + w) + 1, wid


def test_a_slide_takes_the_planned_time_not_the_number_of_frames():
    a, box_a, _ = sliding()
    b, box_b, _ = sliding()
    new = song("Second Song", BLUE)
    for t in (1.0, 1.1, 1.2, 1.3):  # a frame now and then ...
        a.render(new, t)
    b.render(new, 1.0)
    b.render(new, 1.3)  # ... or two: the same place at the same time
    assert box_a.shift == box_b.shift
    a.render(new, 1.5)
    b.render(new, 1.5)
    assert box_a.shift is None and box_b.shift is None


def test_a_slide_needs_the_option_a_panel_that_animates_and_a_card_that_was_on_show():
    off, box, _ = sliding(slide=0)  # (the theme's own option is 0: no slide)
    frame = off.render(song("Second Song", BLUE), 1.0)[0]
    assert box.sliding is None and box.shift is None  # (the frame's colour still fades)
    assert cover_columns(frame, box, RED) is None and cover_columns(frame, box, BLUE) is not None
    cut = Renderer(theme_of("playback", slide=0.5), animate=False)  # the PNG path: a cut
    for t in (0.0, 0.3):
        cut.render(song("First Song", RED), t)
    cut.render(song("Second Song", BLUE), 1.0)
    assert cut._slots["m"].sliding is None and not cut.moving
    first = Renderer(theme_of("playback", slide=0.5), animate=True)
    first.render(song("First Song", RED), 0.0)  # the first frame is no change of track
    assert first._slots["m"].sliding is None and first._slots["m"].track is not None
    assert Renderer(theme_of(), animate=True)._slots["m"].slide == 0  # unless a theme asks


def test_the_same_track_does_not_slide_when_it_is_paused_or_its_cover_comes_late():
    renderer, box, _ = sliding()
    renderer.render(song("First Song", RED), 1.0)
    paused = song("First Song", RED)
    paused.readings["media.playing"] = Reading("media.playing", 0)
    renderer.render(paused, 1.1)
    late = song("First Song", BLUE)  # (the same track, its cover read a moment later)
    renderer.render(late, 1.2)
    assert box.sliding is None and box.shift is None
    other = song("First Song", RED, artist="Somebody Else")  # a new artist is a new track
    renderer.render(other, 1.3)
    assert box.sliding is not None


def test_a_slide_ends_where_the_card_changes_and_does_not_begin_in_a_change():
    renderer, box, _ = sliding("playback\nforecast")
    renderer.render(song("Second Song", BLUE), 1.0)
    assert box.sliding is not None
    renderer.render(demo(media=False), 1.1)  # the weather takes over mid-slide: the card rolls up
    assert box.leaving == 0 and box.shown == 1 and box.sliding is None and box.shift is None
    assert not any(wid.endswith("\0old") for wid, _ in renderer._last[0])
    renderer.render(song("Third Song", RED), 1.3)  # the track comes back in the change: no slide
    assert box.sliding is None
    renderer.render(song("Third Song", RED), 1.8)  # the change is over: the playback card rolls in
    renderer.render(song("Fourth Song", BLUE), 2.0)  # (a track in a change of cards: no slide)
    assert box.leaving == 1 and box.sliding is None
    renderer.render(song("Fourth Song", BLUE), 2.5)  # over: the card is on show with its track
    assert box.leaving is None and box.shown == 0 and box.sliding is None
    renderer.render(song("Fifth Song", RED), 2.6)  # a new track on the card on show: it slides
    assert box.sliding is not None


def test_a_slide_restarts_at_a_track_that_comes_before_it_ends():
    renderer, box, _ = sliding()
    renderer.render(song("Second Song", BLUE), 1.0)
    renderer.render(song("Second Song", BLUE), 1.2)
    assert box.sliding is not None and box.sliding[0] == 1.0
    renderer.render(song("Third Song", RED), 1.3)  # skipped on: the second track is the old one
    assert box.sliding[0] == 1.3 and set(box.sliding[1]) >= {"m/c0-title"}
    renderer.render(song("Third Song", RED), 1.8)  # 0.5 s after: over
    assert box.sliding is None


def test_a_slide_makes_nothing_again_for_every_frame(monkeypatch):
    """The parts are made once with the new track; the frames of the slide move and cut them."""
    renderer, box, _ = sliding()
    built = []
    real = renderer._text_piece
    monkeypatch.setattr(
        renderer, "_text_piece", lambda *a, **k: built.append(a[1]) or real(*a, **k)
    )
    new = song("Second Song", BLUE)
    renderer.render(new, 1.0)
    made = len(built)
    assert made > 0  # (the new track's words)
    for n in range(1, 25):
        renderer.render(new, 1.0 + n * 0.02)  # 0.48 s: a slide at 50 frames a second
    assert box.sliding is not None and len(built) == made


def test_a_piece_moved_and_cut_to_the_columns_of_the_card():
    red = Image.new("RGBA", (10, 4), (255, 0, 0, 255))
    piece = Piece(red, 20, 5)
    moved = slot.slid(piece, 5, 0, 100)
    assert (moved.x, moved.y, moved.layer.size) == (25, 5, (10, 4)) and moved is not piece
    cut = slot.slid(piece, -18, 4, 100)  # x 2 to 12: the first two columns are outside
    assert (cut.x, cut.layer.size) == (4, (8, 4))
    assert slot.slid(piece, 100, 0, 100) is None and slot.slid(piece, -40, 0, 100) is None
    right = slot.slid(piece, 75, 0, 100)  # x 95 to 105: cut at 100
    assert (right.x, right.layer.size) == (95, (5, 4))
    assert slot.slid(None, 3, 0, 10) is None
    lit = replace_light(piece, Piece(Image.new("RGBA", (30, 4), (0, 0, 255, 255)), 10, 5, add=True))
    out = slot.slid(lit, -35, 0, 100)  # the layer has left, the light that reaches further stays
    assert out.layer.size == (1, 1) and out.light.x == 0 and out.light.layer.width == 5
    assert slot.slid(lit, 50, 0, 100).light.x == 60


def replace_light(piece, light):
    from dataclasses import replace

    return replace(piece, light=light)


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
    unit = min(w, h, min(renderer.theme.width, renderer.theme.height) / 2)
    assert box.width == max(1, round(unit / 160))  # as thick as a module card's outline
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


def test_the_covers_of_tracks_gone_are_not_kept_alive_for_their_colour():
    """A cover is up to a megabyte: the colour it carries is kept, the picture is not."""
    import gc
    import weakref

    image = picture((10, 160, 200))
    found = cover.dominant(image)
    gone = weakref.ref(image)
    del image
    gc.collect()
    assert gone() is None  # (nothing here held it)
    other = picture((10, 160, 200))
    assert cover.dominant(other) == found  # (an id that comes back is looked at anew)


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


def test_on_a_big_panel_a_lit_card_and_a_slot_are_framed_as_the_cards_beside_them():
    """Lumen 12.3's weather card had rounder corners and a heavier line than the cards beside
    it (a lit card was framed as its own size): every card now has the same, as the
    companions have it."""
    t = parse_theme(
        {
            "format": "libre-panel-theme/1",
            "display": {"model": "turing-12.3-usb", "orientation": "landscape"},
            "widgets": [
                {"type": "module", "id": "m", "module": "cards", "cols": 3, "rows": 3},
                {"type": "module", "id": "c", "module": "cores", "col": 3, "cols": 3, "rows": 3},
            ],
        }
    )
    style = {**M.STYLE_DEFAULTS, **(t.style or {}), M.FRAME_U: min(t.width, t.height) / 2}
    colors = M.colors_of(t.palette)
    box = [0, 0, 600, 680]
    frames = {}
    for kind in ("forecast", "playback", "cores"):
        built = M.build_module({**t.widgets[1], "module": kind}, box, colors, style)
        card = next(w for w in built if w["id"] == "card")
        frames[kind] = card["radius"]
    assert len(set(frames.values())) == 1, frames
    renderer = Renderer(t, animate=False)
    renderer.render(demo(), 0.0)
    there = M.build_module({**t.widgets[1], "module": "cores"}, renderer.module_boxes["m"],
                           colors, style)  # fmt: skip
    card = next(w for w in there if w["id"] == "card")
    assert renderer._slots["m"].width == card["outline_width"]  # the slot's line as thick
