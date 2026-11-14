"""The app's own themes where words are long: a Monday in Russian, a Monday of February in
Portuguese, the memory of a big PC. A text keeps to the card it stands in and off the texts
beside it, and no text is allowed more room than its card has."""

import dataclasses
import gc
import itertools
import json
from datetime import datetime

import numpy as np
import pytest

from libre_panel import i18n
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import THEME_FILENAME, builtin_themes_dir, load_theme

TEXTS = ("text", "metric", "clock", "countdown")
MONDAY = datetime(2026, 9, 28, 20, 8, 8)  # "понедельник", the longest weekday of Russian
FEBRUARY = datetime(2026, 2, 23, 20, 8, 8)  # "segunda-feira · 23 de fevereiro"
OCTOBER = datetime(2026, 10, 26, 20, 8, 8)  # "poniedziałek · 26 października"
SIZES = ("", "-1280x720", "-1920x720")  # the landscape screens of Nimbus and of Terra


@pytest.fixture(scope="module", autouse=True)
def renderers_let_go():
    yield
    i18n.set_language("en")
    gc.collect()  # a renderer tells the sensors what it reads until it is collected


def cards_of(widgets, width, height):
    """The cards a theme draws by itself: rectangles of some size that are not its ground."""
    return [
        (w["id"], w["x"], w["y"], w["x"] + w["w"], w["y"] + w["h"])
        for w in widgets
        if w["type"] == "rect" and w.get("w", 0) >= 60 and w.get("h", 0) >= 40
        and not (w["w"] >= width - 8 and w["h"] >= height - 8)
    ]  # fmt: skip


def inks_of(name, language, moment, change=None):
    """A built-in theme drawn on a day in a language: its renderer, its frame and, of each of
    its texts, the box of what is inked, where its layer lies and which of its pixels are ink."""
    i18n.set_language(language)
    try:
        theme = load_theme(builtin_themes_dir() / name)
        demo = demo_snapshot(fixed_time=1_760_000_000.0)
        readings = dict(demo.readings)
        for key, value in (change or {}).items():
            if value is None:
                for gone in [k for k in readings if k.startswith(key)]:
                    del readings[gone]
            else:
                readings[key] = dataclasses.replace(readings[key], value=value)
        renderer = Renderer(theme, preview=True)
        frame, _ = renderer.render(dataclasses.replace(demo, readings=readings, now=moment), 1000.0)
    finally:
        i18n.set_language("en")
    kinds = {w["id"]: w["type"] for w in renderer.widgets}
    inks = []
    for wid, piece in renderer._last[0]:
        layer = getattr(piece, "layer", None)
        if kinds.get(wid) not in TEXTS or layer is None or layer.mode != "RGBA":
            continue
        mask = np.asarray(layer.getchannel("A")) > 110
        if not mask.any():
            continue
        ys, xs = np.where(mask)
        box = (
            piece.x + xs.min(),
            piece.y + ys.min(),
            piece.x + xs.max() + 1,
            piece.y + ys.max() + 1,
        )
        inks.append((wid, box, piece.x, piece.y, mask))
    return renderer, frame, inks


def out_of_place(name, language, moment, change=None):
    """What a built-in theme's texts do wrong on a day: over a card's edge, into each other."""
    renderer, frame, inks = inks_of(name, language, moment, change)
    wrong = []
    for wid, box, *_ in inks:
        if box[0] < 0 or box[1] < 0 or box[2] > frame.width or box[3] > frame.height:
            wrong.append(f"{wid} leaves the panel")
        for card, x0, y0, x1, y1 in cards_of(renderer.widgets, frame.width, frame.height):
            inside = box[0] >= x0 and box[1] >= y0 and box[2] <= x1 and box[3] <= y1
            apart = box[2] <= x0 or box[0] >= x1 or box[3] <= y0 or box[1] >= y1
            if not inside and not apart:
                wrong.append(f"{wid} over the edge of {card}")
    for (a, abox, ax, ay, amask), (b, bbox, bx, by, bmask) in itertools.combinations(inks, 2):
        x0, y0 = max(abox[0], bbox[0]), max(abox[1], bbox[1])
        x1, y1 = min(abox[2], bbox[2]), min(abox[3], bbox[3])
        if x1 > x0 and y1 > y0:
            both = (
                amask[y0 - ay : y1 - ay, x0 - ax : x1 - ax]
                & bmask[y0 - by : y1 - by, x0 - bx : x1 - bx]
            )
            if both.sum() >= 4:
                wrong.append(f"{a} runs into {b}")
    return wrong


@pytest.mark.parametrize(
    ("name", "language", "moment"),
    [
        # the weekday and the date beside the clock's seconds
        ("nimbus", "ru", MONDAY),
        ("nimbus-1280x720", "pt", FEBRUARY),
        ("nimbus-1920x720", "ru", MONDAY),
        ("terra", "pt", FEBRUARY),
        ("terra-1280x720", "ru", MONDAY),
        ("terra-1920x720", "es", datetime(2026, 9, 23, 20, 8, 8)),
        # one line under the clock, the panel's whole width
        ("nimbus-vertical", "pt", FEBRUARY),
        ("terra-vertical", "pl", OCTOBER),
        # the date under the clock, in the clock's card
        ("lumen-1920x720", "pt", FEBRUARY),
        ("lumen-pair-1920x720", "pl", OCTOBER),
        ("studio-vertical", "ru", MONDAY),
        ("studio-vertical", "pt", datetime(2026, 11, 23, 20, 8, 8)),
        # the shares' "%" at the card's right edge
        ("round-report-1280x720", "en", MONDAY),
    ],
)
def test_a_long_day_keeps_to_its_place(name, language, moment):
    assert out_of_place(name, language, moment) == []


@pytest.mark.parametrize(
    "name", [f"{theme}{size}" for theme in ("nimbus", "terra") for size in SIZES]
)
@pytest.mark.parametrize(("language", "moment"), [("ru", MONDAY), ("pt", FEBRUARY)])
def test_the_weekday_keeps_its_distance_from_the_seconds(name, language, moment):
    """The weekday is set from the right edge toward the clock's seconds: "ПОНЕДЕЛЬНИК" and
    "SEGUNDA-FEIRA" reached them, or stood 2 px beside them."""
    boxes = {wid: box for wid, box, *_ in inks_of(name, language, moment)[2]}
    assert boxes["weekday"][0] - boxes["seconds"][2] >= 12
    assert boxes["date"][0] - boxes["seconds"][2] >= 12 or boxes["date"][1] >= boxes["seconds"][3]


def test_what_stands_for_the_music_keeps_to_its_card():
    """ "Ничего не играет" in the size of a title is wider than a bar on its end."""
    assert out_of_place("encore-vertical", "ru", MONDAY, {"media.": None}) == []
    assert out_of_place("encore-1920x720", "ja", MONDAY, {"media.": None}) == []
    assert out_of_place("studio-vertical", "pt", FEBRUARY, {"weather.": None}) == []


def test_the_memory_of_a_big_pc_keeps_off_its_total():
    """100.4 of 128 GB: a digit more than the 16 or 32 the place was made for."""
    assert out_of_place("slate", "en", MONDAY, {"mem.used": 100.4, "mem.total": 128.0}) == []


def test_no_text_is_allowed_more_room_than_its_card_has():
    """A text's ``max_width`` is the room it may take before it is set smaller or cut: one that
    reaches past the card's edge lets a long text out of the card."""
    wrong = []
    for file in sorted(builtin_themes_dir().glob(f"*/{THEME_FILENAME}")):
        data = json.loads(file.read_text(encoding="utf-8"))
        display = data.get("display", {})
        cards = cards_of(data["widgets"], display.get("width", 10**6), display.get("height", 10**6))
        for w in data["widgets"]:
            room = w.get("max_width", 0)
            if w["type"] not in TEXTS or not room:
                continue
            x, y = w.get("x", 0), w.get("y", 0) + w.get("font_size", 20) / 2
            around = [c for c in cards if c[1] <= x <= c[3] and c[2] <= y <= c[4]]
            if not around:
                continue
            _, left, _, right, _ = min(around, key=lambda c: (c[3] - c[1]) * (c[4] - c[2]))
            align = w.get("align", "left")
            a, b = {"left": (x, x + room), "right": (x - room, x)}.get(
                align, (x - room / 2, x + room / 2)
            )
            if a < left or b > right:
                wrong.append(
                    f"{file.parent.name}: {w['id']} may take {a:.0f}..{b:.0f} of {left}..{right}"
                )
    assert wrong == []
