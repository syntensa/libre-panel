"""What the renderer keeps from frame to frame (render/kept.py): held to a number of bytes, the
least recently used first to go, hits and misses counted; and in a long run of the themes of
the 12.3" and the 9.2" every cache stays within its budget, as often hit as it was when it
was held by a count."""

import threading
from dataclasses import dataclass

import numpy as np
import pytest
from PIL import Image

from libre_panel.render import kept as kept_module
from libre_panel.render.kept import Kept, size_of, stats


def made_by(made, n, size=1000):
    def build():
        made.append(n)
        return np.zeros(size, np.uint8)

    return build


def test_the_least_recently_used_go_once_more_bytes_than_the_budget_are_kept():
    kept = Kept("test.budget", 3 * size_of(np.zeros(1000, np.uint8)))
    made = []
    for n in (1, 2, 3, 1):
        kept.make(n, made_by(made, n))
    assert made == [1, 2, 3] and len(kept) == 3
    kept.make(4, made_by(made, 4))  # over the budget: the least recently used (2) goes
    kept.make(1, made_by(made, 1))
    kept.make(2, made_by(made, 2))
    assert made == [1, 2, 3, 4, 2]
    assert kept.size <= kept.budget and kept.dropped == 2
    assert (kept.hits, kept.misses) == (2, 5)


def test_the_newest_stays_even_when_it_alone_is_over_the_budget():
    kept = Kept("test.big", 100)
    kept.put("small", np.zeros(10, np.uint8))
    big = kept.put("big", np.zeros(10_000, np.uint8))
    assert list(kept) == ["big"] and kept["big"] is big and kept.size > kept.budget
    kept.resize(1 << 20)
    kept.put("small", np.zeros(10, np.uint8))
    assert list(kept) == ["big", "small"]


def test_a_value_that_does_not_serve_is_a_miss_and_none_is_a_value():
    kept = Kept("test.valid", 1 << 20)
    kept.put("a", (1, "x"))
    assert kept.get("a", valid=lambda v: v[0] == 2) is None
    assert kept.get("a", valid=lambda v: v[0] == 1) == (1, "x")
    assert (kept.hits, kept.misses) == (1, 1)
    kept.put("unreadable", None)  # (a picture that could not be read: not read again)
    marker = object()
    assert kept.get("unreadable", marker) is None and kept.get("nothing", marker) is marker
    assert "unreadable" in kept and kept.make("unreadable", lambda: 1 / 0) is None


def test_reading_it_as_a_mapping_counts_nothing_and_moves_nothing():
    kept = Kept("test.mapping", 1 << 20)
    for key in "abc":
        kept.put(key, key.upper())
    assert "a" in kept and kept["a"] == "A" and list(kept) == ["a", "b", "c"]
    assert dict(kept) == {"a": "A", "b": "B", "c": "C"} == kept.copy()
    assert kept.keys() == ["a", "b", "c"] and kept.values() == ["A", "B", "C"]
    assert (kept.hits, kept.misses) == (0, 0)
    kept.get("a")
    assert list(kept) == ["b", "c", "a"]
    assert kept.pop("b") == "B" and kept.pop("b", 7) == 7 and len(kept) == 2
    kept.clear()
    assert len(kept) == 0 and kept.size == 0


@dataclass
class _Piece:
    layer: Image.Image
    light: object = None
    backdrop: Image.Image | None = None


def test_sizes_are_the_pixels_of_pictures_arrays_and_pieces():
    rgb = Image.new("RGB", (100, 10))
    assert size_of(rgb) - size_of(Image.new("L", (100, 10))) == 3000  # RGB: four bytes a pixel
    assert size_of(Image.new("RGBA", (100, 10))) == size_of(rgb)
    assert size_of(np.zeros((10, 10), np.float32)) > 400
    piece = _Piece(rgb, light=_Piece(Image.new("RGBA", (10, 10))))
    assert size_of(piece) > size_of(rgb) + 400
    assert size_of((rgb, rgb)) > 2 * size_of(rgb) and size_of(None) == 0


def test_threads_make_each_value_once_kept_and_counted():
    kept = Kept("test.threads", 1 << 30)
    start = threading.Barrier(8)

    def work():
        start.wait()
        for n in range(200):
            assert kept.make(n % 50, lambda n=n: n % 50) == n % 50

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(kept) == 50 and kept.hits + kept.misses == 1600 and kept.hits >= 1600 - 8 * 50


def test_stats_sum_up_by_name():
    one, other = Kept("test.stats", 1000), Kept("test.stats", 2000)
    one.put(1, b"x")
    other.get(1)
    row = stats()["test.stats"]
    assert row["count"] >= 2 and row["budget"] == 2000 and row["items"] >= 1
    assert row["misses"] >= 1 and row["bytes"] == one.size + other.size
    kept_module.reset_counts()
    assert stats()["test.stats"]["misses"] == 0


# -- in a long run of the themes ------------------------------------------------------------------

# What each cache hit in 300 frames of the video mode (10 s at 30 frames a second, new readings
# each second: _video_run) when they were held by a count, each theme in a process of its own,
# on the 12.3" and the 9.2": held by bytes, each hits within a few percent as often (most more
# often).
BEFORE = {
    "lumen-1920x720": {"renderer.texts": 0.944, "renderer.glyphs": 0.915,
                       "renderer.underlays": 0.712, "renderer.fans": 0.654, "fan.images": 0.685,
                       "lit.pictures": 0.969},
    "lumen-companion": {"renderer.list_bars": 0.706, "renderer.glyphs": 0.842,
                        "renderer.underlays": 0.976, "renderer.texts": 0.993, "fan.images": 0.801},
    "arena-1920x720": {"renderer.texts": 0.899, "renderer.glyphs": 0.895,
                       "renderer.list_bars": 0.828, "renderer.underlays": 0.963,
                       "fan.images": 0.801, "lit.pictures": 0.993},
    "encore-1920x720": {"renderer.texts": 0.981, "renderer.glyphs": 0.902,
                        "renderer.underlays": 0.894, "lit.pictures": 0.642},
    "spur-horizon": {"renderer.texts": 0.917, "renderer.glyphs": 0.974,
                     "renderer.underlays": 0.946, "renderer.fans": 0.655, "fan.images": 0.673},
}  # fmt: skip


def _video_run(theme_id, seconds=10, fps=30):
    """A theme in the video mode as the panel's loop draws it, every frame, with new readings
    each second; every Kept looked at every half second: how far over its budget it was with
    more than the newest in it (the most)."""
    from datetime import datetime, timedelta

    from libre_panel.render.renderer import Renderer
    from libre_panel.sensors.demo import demo_snapshot
    from libre_panel.theme.model import find_theme, load_theme

    renderer = Renderer(load_theme(find_theme(theme_id)), animate=True)
    renderer.continuous, renderer.fps = True, fps
    start = datetime(2026, 9, 27, 23, 59, 50)
    snapshot = None
    over = 0
    for i in range(seconds * fps):
        now = 1000.0 + i / fps
        if i % fps == 0:
            snapshot = demo_snapshot(fixed_time=1_700_000_000 + i // fps)
            renderer.new_sample(now, 1.0)
        snapshot.now = start + timedelta(seconds=i / fps)
        renderer.render(snapshot, now)
        if i % 15 == 0:
            for kept in list(kept_module._ALL):
                if len(kept) > kept.least:
                    over = max(over, kept.size - kept.budget)
    return renderer, over


@pytest.mark.parametrize("theme_id", sorted(BEFORE))
def test_in_a_long_run_every_cache_stays_within_its_budget_and_hits_as_often(theme_id):
    """Each cache stays within its budget, frame after frame (only the newest alone may be
    larger), and hits within a few percent as often as it did held by a count."""
    from libre_panel import light

    light.empty_caches()
    kept_module.reset_counts()
    renderer, over = _video_run(theme_id)
    try:
        assert over <= 0
        found = stats()
        for name, rate in BEFORE[theme_id].items():
            assert found[name]["rate"] >= rate - 0.03, (name, found[name]["rate"], rate)
    finally:
        renderer.close()


def test_a_graph_off_the_panel_lets_its_strips_go_and_draws_the_same_when_back():
    """A graph not on show (on a card that is not) keeps no strip: once its strip ends, it
    could not be shown but drawn anew, which it is when it comes back. Every frame is the one a
    renderer draws that keeps them."""
    from datetime import datetime, timedelta

    from libre_panel.render.renderer import Renderer
    from libre_panel.sensors.base import Reading, Snapshot
    from libre_panel.theme.model import THEME_FORMAT, parse_theme

    theme = parse_theme({
        "format": THEME_FORMAT, "display": {"width": 320, "height": 120},
        "background": {"color": "#000000"},
        "widgets": [{"type": "graph", "id": "g", "x": 10, "y": 10, "w": 300, "h": 100,
                     "sensor": "cpu.load", "glow": 0.5}],
    })  # fmt: skip
    kept, keeping = Renderer(theme, animate=True), Renderer(theme, animate=True)
    keeping._forget_strips = lambda now: None  # (as before: the strips stay)
    for renderer in (kept, keeping):
        renderer.continuous, renderer.fps = True, 25
    start = datetime(2026, 9, 27, 12, 0, 0)
    for i in range(250):  # ten seconds: on show, off for four, on again
        now = 1000.0 + i / 25
        visible = not 75 <= i < 175
        for renderer in (kept, keeping):
            renderer.widgets[0]["visible"] = visible
            if i % 25 == 0:
                renderer.new_sample(now, 1.0)
        value = 50 + 40 * ((i // 25) % 3 - 1)
        snapshot = Snapshot(readings={"cpu.load": Reading("cpu.load", float(value), "%")},
                            now=start + timedelta(seconds=i / 25))  # fmt: skip
        a, _ = kept.render(snapshot, now)
        b, _ = keeping.render(snapshot, now)
        assert a.tobytes() == b.tobytes(), f"frame {i}"
        if i == 170:  # off for more than its strip reaches
            assert "g" not in kept._strips and "g" in keeping._strips
    assert "g" in kept._strips


def test_where_none_need_stay_one_too_big_is_not_kept_and_pushes_none_out():
    from libre_panel.render.kept import cached

    made = []

    @cached("test.cached", 3000)
    def picture(w, h):
        made.append((w, h))
        return Image.new("L", (w, h))

    small = picture(10, 10)
    assert picture(10, 10) is small and made == [(10, 10)]  # kept
    big = picture(100, 100)  # (10 kB: over the budget alone)
    assert picture(100, 100) is not big and made == [(10, 10), (100, 100), (100, 100)]
    assert picture(10, 10) is small and len(picture.kept) == 1  # the small one stayed
    picture.cache_clear()
    assert len(picture.kept) == 0
