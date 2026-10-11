"""tools/readme_media.py: the clips for the README, the website and forum posts.

The encoders and, further down, the panel and app recipes, with tiny pictures and short clips so
that they stay fast. What a clip looks like is judged by eye when it is made; here are the
things that must hold on every run: the same frames twice, the files that come out, the
fallbacks and the size check.
"""

import shutil
from dataclasses import replace
from functools import partial
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

TOOLS = Path(__file__).resolve().parent.parent / "tools"
FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


@pytest.fixture(scope="module")
def encode():
    """tools/media_encode.py, imported by name as the tools do among themselves."""
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(TOOLS))
        import media_encode

        yield media_encode


def picture(n: int, size=(96, 64)) -> Image.Image:
    """A frame of a made-up clip: a gradient, and a bright block that moves right."""
    w, h = size
    ramp = np.linspace(10, 60, w, dtype=np.uint8)
    pixels = np.zeros((h, w, 3), np.uint8)
    pixels[:, :] = ramp[None, :, None]
    x = 4 + n * 5
    pixels[h // 4 : h // 2, x : x + 14] = (19, 229, 215)
    return Image.fromarray(pixels)


@pytest.fixture
def frames(encode, tmp_path):
    sink = encode.FrameSink(tmp_path / "frames", fps=20)
    for n in range(16):
        sink.add(picture(n))
    return sink.frames()


def test_frames_are_kept_as_numbered_pngs(encode, frames):
    assert frames.count == 16 and frames.fps == 20 and frames.size == (96, 64)
    assert frames.path(3).name == "00003.png" and frames.pattern.endswith("%05d.png")
    assert frames.image(5).tobytes() == picture(5).tobytes()  # nothing lost on the way


def test_an_empty_clip_is_an_error(encode, tmp_path):
    with pytest.raises(encode.EncodeError):
        encode.FrameSink(tmp_path / "none", fps=25).frames()


@needs_ffmpeg
def test_one_clip_becomes_a_gif_a_video_a_webm_and_a_poster(encode, frames, tmp_path):
    settings = encode.Settings(gif_width=48, gif_fps=10)
    gif = encode.write_gif(frames, tmp_path / "out" / "clip.gif", settings)
    with Image.open(gif.path) as image:
        assert image.size == (48, 32)  # as wide as asked, in proportion
        assert image.n_frames == 8  # 16 frames at 20 a second, 10 of them a second
        assert image.info["loop"] == 0 and image.info["duration"] == 100  # forever, 10 a second
    assert gif.size == gif.path.stat().st_size and gif.within_target

    mp4 = encode.write_mp4(frames, tmp_path / "out" / "clip.mp4", settings)
    webm = encode.write_webm(frames, tmp_path / "out" / "clip.webm", settings)
    assert mp4.read_bytes()[4:8] == b"ftyp"  # (and the index comes first: faststart)
    assert mp4.read_bytes().find(b"moov") < mp4.read_bytes().find(b"mdat")
    assert webm.read_bytes()[:4] == bytes.fromhex("1a45dfa3")  # Matroska's header

    poster = encode.write_poster(frames, 7, tmp_path / "out" / "clip.png")
    with Image.open(poster) as image:
        assert image.size == (96, 64)
        assert image.tobytes() == picture(7).tobytes()


@needs_ffmpeg
def test_a_video_is_yuv420p_in_bt709_with_even_sides(encode, tmp_path):
    import subprocess

    sink = encode.FrameSink(tmp_path / "odd", fps=20)
    for n in range(6):
        sink.add(picture(n, (97, 65)))  # odd sizes: yuv420p needs even ones
    mp4 = encode.write_mp4(sink.frames(), tmp_path / "odd.mp4")
    shown = subprocess.run(
        [FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=pix_fmt,width,height,color_space,codec_name", "-of",
         "default=nw=1", str(mp4)],
        capture_output=True, text=True,
    )  # fmt: skip
    if shown.returncode:
        pytest.skip("ffprobe is not installed")
    found = dict(line.split("=") for line in shown.stdout.split())
    assert found == {"codec_name": "h264", "width": "96", "height": "64", "pix_fmt": "yuv420p",
                     "color_space": "bt709"}  # fmt: skip


def test_without_ffmpeg_pillow_writes_the_gif(encode, frames, tmp_path, monkeypatch):
    monkeypatch.setattr(encode.shutil, "which", lambda name: None)
    assert encode.find_ffmpeg() is None
    settings = encode.Settings(gif_width=48, gif_fps=10)
    gif = encode.write_gif(frames, tmp_path / "pillow.gif", settings)
    with Image.open(gif.path) as image:
        assert image.size == (48, 32) and image.n_frames == 8
        assert image.info["loop"] == 0 and image.info["duration"] == 100
        image.seek(7)
        last = np.asarray(image.convert("RGB"), np.int16)
    reference = np.asarray(frames.image(14).resize((48, 32), Image.Resampling.LANCZOS), np.int16)
    assert np.abs(last - reference).mean() < 6  # the same picture, in at most 256 colours
    for what in (encode.write_mp4, encode.write_webm):  # video needs ffmpeg, and says so
        with pytest.raises(encode.EncodeError, match="needs ffmpeg"):
            what(frames, tmp_path / "clip.out", settings)


@needs_ffmpeg
def test_both_encoders_make_the_same_picture(encode, frames, tmp_path, monkeypatch):
    settings = encode.Settings(gif_width=96, gif_fps=20)
    with_ffmpeg = encode.write_gif(frames, tmp_path / "a.gif", settings)
    monkeypatch.setattr(encode.shutil, "which", lambda name: None)
    with_pillow = encode.write_gif(frames, tmp_path / "b.gif", settings)
    for n in (0, 9):
        shots = []
        for gif in (with_ffmpeg, with_pillow):
            with Image.open(gif.path) as image:
                image.seek(n)
                shots.append(np.asarray(image.convert("RGB"), np.int16))
        assert np.abs(shots[0] - shots[1]).mean() < 6


def test_a_gif_goes_down_the_ladder_until_it_is_small_enough(encode, tmp_path):
    rng = np.random.default_rng(3)  # noise: a GIF cannot make it small, only leaner
    sink = encode.FrameSink(tmp_path / "noise", fps=20)
    for _ in range(12):
        sink.add(Image.fromarray(rng.integers(0, 255, (60, 80, 3), dtype=np.uint8)))
    frames = sink.frames()
    ladder = (encode.GifLook(256, 3), encode.GifLook(64, 5, 10), encode.GifLook(16, 5, 5))
    best = encode.write_gif(frames, tmp_path / "a.gif", encode.Settings(target=1), ladder)
    sizes = [size for _look, size in best.tried]
    assert len(sizes) == 3 and sizes[0] > sizes[1] > sizes[2]  # leaner at every step
    assert best.look == ladder[2] and best.size == sizes[2] and not best.within_target
    assert best.fps == 5  # the last look takes fewer frames
    roomy = encode.write_gif(frames, tmp_path / "b.gif", encode.Settings(target=10**9), ladder)
    assert roomy.look == ladder[0] and len(roomy.tried) == 1 and roomy.within_target


def test_a_gif_over_the_limit_is_an_error_and_leaves_nothing(encode, frames, tmp_path):
    out, ladder = tmp_path / "out" / "big.gif", encode.LADDER[:2]
    with pytest.raises(encode.GifTooLarge, match=r"big\.gif .* a forum takes"):
        encode.write_gif(frames, out, encode.Settings(target=100, limit=200), ladder)
    assert not out.exists()
    fine = encode.write_gif(frames, out, encode.Settings(target=100, limit=10**7), ladder)
    assert out.exists() and fine.size < 10**7 and not fine.within_target  # over the target only


def test_sizes_are_told_in_the_units_a_person_reads(encode):
    assert encode.human(3_500_000) == "3.34 MB" and encode.human(2048) == "2 kB"
    assert encode.GIF_TARGET == 4 * 2**20 and encode.GIF_LIMIT == 8 * 2**20


def test_a_clip_that_loops_fades_its_end_into_its_start(encode, tmp_path):
    sink = encode.FrameSink(tmp_path / "loop", fps=10)
    for n in range(14):  # 10 frames of the clip, then 4 of what comes after its end
        sink.add(Image.new("RGB", (8, 8), (n * 10, 0, 0)))
    frames = encode.fade_loop(sink.frames(), 4)
    assert frames.count == 10 and not frames.path(10).exists()  # the continuation is gone
    reds = [frames.image(n).getpixel((0, 0))[0] for n in range(frames.count)]
    assert reds[0] == 100  # the clip now starts where its end would have gone on
    assert reds[1:4] == sorted(reds[1:4], reverse=True) and reds[3] < 100  # ... and gives way
    assert reds[4:] == [40, 50, 60, 70, 80, 90]  # to the real start, which is as it was
    with pytest.raises(encode.EncodeError):
        encode.fade_loop(frames, 8)  # a clip cannot fade more than it has


# -- the panel ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def panel():
    """tools/media_panel.py, with a short history to start the graphs from."""
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(TOOLS))
        import media_panel

        patch.setattr(media_panel, "PREFILL_S", 10.0)
        yield media_panel


@pytest.fixture(scope="module")
def clip(panel, encode, tmp_path_factory):
    """The frames of a panel recipe at a tiny size, recorded once however many tests look."""
    settings = encode.Settings(fps=10, scale=0.15)
    done = {}

    def record(name):
        if name not in done:
            folder = tmp_path_factory.mktemp(name)
            done[name] = panel.record(name, panel.CLIPS[name], settings, folder)
        return done[name]

    return record


@pytest.fixture(scope="module")
def short(panel, encode, tmp_path_factory):
    """A recipe cut to a second and a half, for what needs no more than to see it run."""

    def record(name, themes=None, scale=0.15):
        recipe = replace(panel.CLIPS[name], seconds=1.5, warmup=0.5)
        settings = encode.Settings(fps=10, scale=scale)
        return panel.record(name, recipe, settings, tmp_path_factory.mktemp(name), themes)

    return record


def pixels(recorded, n):
    return np.asarray(recorded.frames.image(n), np.int16)


def differ(recorded, a, b):
    """How much frames ``a`` and ``b`` differ, on average per pixel (of 255)."""
    return float(np.abs(pixels(recorded, a) - pixels(recorded, b)).mean())


LONG = ("card-ignition", "volume-band", "theme-tube", "game-entrance")  # what the tests below use


def test_every_panel_recipe_runs_headless_and_makes_a_clip(panel, clip, short):
    assert list(panel.CLIPS) == [
        "video-mode", "card-ignition", "small-panel", "volume-band", "theme-tube",
        "game-entrance", "close-up-rings", "close-up-curves", "where-it-started",
    ]  # fmt: skip
    widths = {}
    for name, recipe in panel.CLIPS.items():
        # (a 3.5" panel at 0.15 is 72 pixels wide: too small for the card in its corner)
        recorded, seconds = (
            (clip(name), recipe.seconds)
            if name in LONG
            else (short(name, scale=0.3 if name == "small-panel" else 0.15), 1.5)
        )
        frames = recorded.frames
        widths[name] = frames.size[0]
        assert frames.count == round(seconds * 10) and frames.fps == 10, name
        assert 0 <= recorded.poster < frames.count, name
        assert (
            len({pixels(recorded, n).tobytes() for n in range(frames.count)}) > frames.count // 2
        ), name
        assert recipe.about and recipe.kind == "panel" and recipe.themes, name
        for theme in recipe.themes:  # every theme a recipe names is there
            assert panel.theme_data(theme)[0]["widgets"], name
    # the small panel is drawn at twice its size (480 pixels is small in a README), in its frame
    assert widths["small-panel"] > 480 * 0.3 * 2


def test_a_close_up_is_a_crop_without_the_device_frame(panel, clip, short):
    for name in ("close-up-rings", "close-up-curves"):
        recipe = panel.CLIPS[name]
        x, y, w, h = recipe.crop.box(panel.theme_data(recipe.themes[0])[0])
        assert short(name).frames.size == (round(w * 0.3), round(h * 0.3)), name  # 2 x 0.15
        assert w < 1920 / 2 and h < 480 and (x, y) != (0, 0), name  # a part of the bar
    assert clip("volume-band").frames.size[0] > 288  # a panel clip has the frame round it


def test_a_focus_is_the_box_round_the_widgets_of_some_kinds(panel):
    data, _folder = panel.theme_data("lumen")
    fans = panel.Focus(("fan",), 10, 20, 30, 10).box(data)
    boxes = [(w["x"], w["y"], w["w"], w["h"]) for w in data["widgets"] if w["type"] == "fan"]
    left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
    right, bottom = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
    assert fans == (left - 10, top - 20, right - left + 40, bottom - top + 30)
    rings = panel.Focus(("instrument",)).box(data)  # modules: by the module's kind, on the grid
    assert rings is not None and rings[0] > 1280 and rings[0] + rings[2] <= 1920
    assert panel.Focus(("fan",), 5000, 5000, 5000, 5000).box(data) == (0, 0, 1920, 480)  # kept in
    assert panel.Focus(("fan", "instrument")).box(panel.theme_data("pico")[0]) is None


def test_a_close_up_of_a_theme_without_what_it_looks_for_shows_the_panel(short):
    whole = short("close-up-rings", themes=("pico",), scale=0.5)
    assert whole.frames.size == (80, 40)  # the panel (160 x 80, at half) as it is, not enlarged


def test_the_volume_band_rolls_down_and_the_clip_ends_as_it_began(clip):
    volume = clip("volume-band")
    assert differ(volume, 0, 20) > 8  # the band is on show at two seconds
    assert differ(volume, 0, 30) > 8
    assert differ(volume, 0, volume.frames.count - 1) < 1  # and gone, the rings as they were


def test_the_tube_goes_to_the_other_screen_and_back(clip):
    tube = clip("theme-tube")
    assert differ(tube, 5, 28) > 12  # another screen from 2.5 s on
    assert differ(tube, 5, 12) > 5  # a line of light opens between the two at 1.2 s
    assert differ(tube, 5, tube.frames.count - 1) < 2.5  # back where it began


def test_the_entrance_darkens_the_panel_for_the_game_and_the_end(clip):
    game = clip("game-entrance")
    means = [float(pixels(game, n).mean()) for n in range(game.frames.count)]
    assert means[20] < 0.6 * means[0] and means[60] < 0.6 * means[0]  # a band on black
    assert differ(game, 0, 42) > 8  # the screen of the game at 4.2 s
    assert differ(game, 0, game.frames.count - 1) < 1.5  # the clip ends on the first screen


def test_the_card_slot_changes_and_changes_back(clip):
    cards = clip("card-ignition")
    slot = slice(0, cards.frames.size[0] * 2 // 5)  # (the card slot is at the left of the bar)

    def card(n):
        return pixels(cards, n)[:, slot]

    assert np.abs(card(5) - card(35)).mean() > 8  # the weather instead of what plays
    assert np.abs(card(5) - card(cards.frames.count - 1)).mean() < 1.5  # playing again at the end


def test_a_clip_is_the_same_on_every_run(short):
    first, again = short("video-mode"), short("video-mode")  # (curves, fans, rings: all in it)
    assert again.frames.count == first.frames.count and again.poster == first.poster
    for n in range(first.frames.count):
        assert again.frames.path(n).read_bytes() == first.frames.path(n).read_bytes(), n


def test_a_clip_shows_the_theme_it_is_given(panel, clip, encode, tmp_path):
    other = panel.record("volume-band", panel.CLIPS["volume-band"],
                         encode.Settings(fps=10, scale=0.15), tmp_path / "other",
                         themes=("libre-default",))  # fmt: skip
    assert other.frames.size != clip("volume-band").frames.size  # a 3.5" panel, not a bar
    assert differ(other, 0, 20) > 3  # and the band rolls down all the same
    with pytest.raises(ValueError, match="shows 2 theme"):
        panel.record("theme-tube", panel.CLIPS["theme-tube"], encode.Settings(fps=10),
                     tmp_path / "x", themes=("studio",))  # fmt: skip


def test_a_clip_leaves_the_machine_as_it_found_it(panel, encode, tmp_path, monkeypatch):
    import os
    import time

    from libre_panel import app, i18n
    from libre_panel.plugins import loader

    monkeypatch.setattr(loader, "_installed", lambda group: iter(["a plugin of the machine"]))
    before = os.environ["LIBRE_PANEL_HOME"]
    i18n.set_language("de")
    with panel.workshop() as home:
        assert os.environ["LIBRE_PANEL_HOME"] == str(home) != before  # a folder of its own
        assert list(loader._installed("libre_panel.themes")) == []  # no plugin of the machine
        assert app.time.time() == panel.images.WHEN == app.time.time()  # the time stands still
        assert app.time.sleep is time.sleep  # (the rest of ``time`` is the real one)
    assert os.environ["LIBRE_PANEL_HOME"] == before and app.time is time
    assert list(loader._installed("libre_panel.themes")) == ["a plugin of the machine"]
    assert i18n.language() == "de"
    i18n.set_language("en")


def test_values_surge_and_settle_again(panel):
    from libre_panel.sensors.base import Reading

    readings = {
        "cpu.load": Reading("cpu.load", 70.0, "%"),
        "cpu.power": Reading("cpu.power", 40.0, "W"),
        "gpu.load": Reading("gpu.load", 10.0, "%"),
    }
    change = panel.surge({"cpu.load": 50, "cpu.power": 40, "fan": 9}, at=2.0, rise=1.0, hold=1.0,
                         fall=2.0, every=8.0)  # fmt: skip

    def at(seconds):
        now = dict(readings)
        change(now, seconds)
        return now["cpu.load"].value, now["cpu.power"].value

    assert at(1.0) == (70.0, 40.0)  # nothing before it begins
    assert at(2.5)[1] == pytest.approx(40 + 40 * panel.bump(0.5, 1, 1, 2))  # on its way up
    assert at(3.5) == (100.0, 80.0)  # held: a percentage stops at 100
    assert 70 < at(5.0)[0] < 100 and at(7.0) == (70.0, 40.0)  # settled
    assert at(2.5) == at(10.5) == at(-5.5)  # and it comes round, before the clip too


def test_what_plays_can_be_silenced_for_a_while(panel):
    from libre_panel.sensors.base import Reading

    readings = {"media.title": Reading("media.title", "x"), "cpu.load": Reading("cpu.load", 1.0)}
    quiet = panel.silence("media.", 2.0, 5.0)
    for seconds, gone in ((1.9, False), (2.0, True), (4.9, True), (5.0, False)):
        now = dict(readings)
        quiet(now, seconds)
        assert ("media.title" not in now) is gone and "cpu.load" in now


def test_the_demo_comes_round_with_a_clip_that_loops(panel):
    clock = panel.Clock()
    looping, plain = panel.ClipDemo(clock, [], period=8.0), panel.ClipDemo(clock, [])
    at = {}
    for seconds in (0.0, 3.3, 8.0, 16.0):
        clock.now = panel.START + seconds
        at[seconds] = looping.read()
    for key in ("cpu.load", "gpu.fan", "net.down"):
        assert (
            at[0.0][key].value
            == pytest.approx(at[8.0][key].value)
            == pytest.approx(at[16.0][key].value)
        )
    assert at[0.0]["cpu.load"].value != at[3.3]["cpu.load"].value  # it moves in between
    assert at[3.3]["temp.cpu.package"].value == at[3.3]["cpu.temp"].value  # copies agree
    clock.now = panel.START
    start = plain.read()["cpu.load"].value
    clock.now = panel.START + 8.0
    assert plain.read()["cpu.load"].value != pytest.approx(start)  # (without a period it goes on)


def test_the_bezel_is_the_device_frame_of_the_images_tool(panel):
    """A frame pasted into the bezel made once is what ``device`` and ``background`` of
    tools/readme_images.py draw round that screen."""
    rng = np.random.default_rng(5)
    size = (160, 60)
    screen = Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))
    bezel = panel.Bezel(size)
    border = max(4, min(24, round(0.0375 * size[1])))
    across, down = round(1.2 * border), round(1.8 * border)
    margin = panel.images.shadow_margin(border)
    canvas = panel.images.background(*bezel.size, glow=(0.5, 1.0)).convert("RGBA")
    canvas.alpha_composite(panel.images.device(screen, border), (across - margin, down - margin))
    expected = np.asarray(canvas.convert("RGB"), np.int16)
    made = np.asarray(bezel.frame(screen), np.int16)
    assert bezel.size == (size[0] + 2 * (border + across), size[1] + 2 * (border + down))
    assert np.abs(made - expected).max() <= 1  # (a rounding in the reflection, at most)


# -- the app -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app_clips(panel):
    """tools/media_app.py: the recipes of the app, which need a browser to run (but not to list)."""
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(TOOLS))
        import media_app

        yield media_app


def test_the_app_recipes_are_listed_with_a_panel_each_to_open_them_on(app_clips, panel):
    assert list(app_clips.CLIPS) == [
        "app-look", "app-drag-module", "app-ring-style", "app-command-palette",
        "app-hardware-names",
    ]  # fmt: skip
    for name, recipe in app_clips.CLIPS.items():
        assert recipe.kind == "app" and recipe.about and recipe.seconds > 0, name
        data, folder = panel.theme_data(recipe.themes[0])  # (made without a browser)
        assert data["widgets"], name
        if recipe.themes[0].startswith("layout-"):  # the 3.5" panel Libre Panel starts with
            assert data["display"]["model"] == "turing-3.5", name
            assert data["name"] == "My panel" and folder is None  # a name: nobody's
    assert app_clips.CLIPS["app-drag-module"].room  # there is room on its grid for a module


def test_a_crop_around_an_element_keeps_its_room_inside_the_window(app_clips):
    class Page:
        def locator(self, selector):
            assert selector == ".panel-canvas"
            return self

        first = property(lambda self: self)

        def bounding_box(self):
            return {"x": 395.4, "y": 185.0, "width": 504.0, "height": 336.2}

    around = app_clips.Around(".panel-canvas", 46, 50, 46, 52)
    assert app_clips.window_box(Page(), around) == {
        "x": 349,
        "y": 135,
        "width": 597,
        "height": 439,
    }  # (rounded outwards)
    wide = app_clips.Around(".panel-canvas", 999, 999, 999, 999)  # the window is all there is
    box = app_clips.window_box(Page(), wide)
    assert box == {"x": 0, "y": 0, "width": app_clips.VIEWPORT[0], "height": app_clips.VIEWPORT[1]}
    assert app_clips.window_box(Page(), (1, 2, 3, 4)) == {"x": 1, "y": 2, "width": 3, "height": 4}
    assert app_clips.window_box(Page(), None)["width"] == app_clips.VIEWPORT[0]


def test_the_app_of_the_clips_is_opened_with_the_token_it_wants(app_clips, panel):
    """The editor's server answers only the token of its run (editor/access.py); the address the
    recipes open has it after the ``#``, where the app takes it from."""
    import json
    import urllib.request

    from libre_panel.editor import access

    data, folder = panel.theme_data("layout-desk")
    with app_clips.running(data, folder) as url:
        base, token = access.split_token(url)
        assert token and url == f"{base}#token={token}" and base.startswith("http://127.0.0.1:")
        request = urllib.request.Request(base + "api/themes", headers={access.HEADER: token})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request) as answer:
            assert any(theme["id"] == app_clips.THEME for theme in json.load(answer))


def test_an_app_clip_is_shot_in_a_browser_and_ends_as_it_began(app_clips, encode, tmp_path):
    pytest.importorskip("playwright", reason="Playwright is not installed")
    settings = encode.Settings(quick=True, scale=0.25)  # short holds, half the density
    try:
        recorded = app_clips.record(
            "app-command-palette", app_clips.CLIPS["app-command-palette"], settings, tmp_path
        )
    except app_clips.Unavailable as error:
        pytest.skip(str(error))
    frames = recorded.frames
    assert frames.size == (640, 360) and frames.count > 20  # the window, at 0.25 x 2
    assert 0 <= recorded.poster < frames.count
    first, last = (np.asarray(frames.image(n), np.int16) for n in (0, frames.count - 1))
    picked = np.asarray(frames.image(recorded.poster), np.int16)
    assert np.abs(first - picked).mean() > 4  # the palette is open over the panel
    h, w = first.shape[:2]
    # the look is undone, in the panel's part of the window (the top bar says "not saved" now)
    panel = (slice(h // 5, h * 9 // 10), slice(w * 3 // 10, w * 7 // 10))
    assert np.abs(first[panel] - last[panel]).mean() < 0.5
    assert len({frames.path(n).read_bytes() for n in range(frames.count)}) > 6  # it moves


# -- the program -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cli(panel):
    """tools/readme_media.py, the program."""
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(TOOLS))
        import readme_media

        yield readme_media


@pytest.fixture
def tiny(cli, panel, monkeypatch):
    """A recipe of a second on the smallest built-in panel: the whole program, without the wait."""

    def setup(stage, themes):
        stage.show(stage.theme(themes[0]))

    recipe = panel.PanelClip("a second on the smallest panel", ("pico",), 1.0, setup, poster=0.5)
    monkeypatch.setattr(cli, "clips", lambda: {"tiny": recipe})
    return recipe


def run(cli, encode, tmp_path, **options):
    """Make the clip "tiny" in tmp_path/out; the lines the program said, and its status."""
    said = []
    settings = encode.Settings(fps=10, gif_width=120, gif_fps=10, **options.pop("settings", {}))
    status = cli.make(["tiny"], tmp_path / "out", settings, say=said.append, **options)
    return said, status


def test_the_list_says_what_there_is_and_what_each_shows(cli, capsys):
    assert cli.main(["--list"]) == 0
    shown = capsys.readouterr().out
    for name in ("video-mode", "volume-band", "theme-tube", "game-entrance", "card-ignition",
                 "small-panel", "close-up-rings", "close-up-curves", "where-it-started",
                 "app-look", "app-drag-module", "app-ring-style", "app-command-palette",
                 "app-hardware-names"):  # fmt: skip
        assert name in shown
    assert "shows: lumen, studio" in shown  # (the tube goes from one theme to another)
    assert "shows: libre-default" in shown and "shows: layout-rings" in shown


@needs_ffmpeg
def test_a_clip_is_written_as_gif_mp4_webm_and_poster(cli, encode, tiny, tmp_path):
    said, status = run(cli, encode, tmp_path)
    assert status == 0
    out = tmp_path / "out"
    assert sorted(p.name for p in out.iterdir()) == [
        f"tiny.{x}" for x in ("gif", "mp4", "png", "webm")
    ]
    text = "\n".join(said)
    for name in ("tiny.gif", "tiny.mp4", "tiny.webm", "tiny.png"):
        assert name in text  # each file is told, with its size
    with Image.open(out / "tiny.gif") as gif:
        assert gif.n_frames == 10 and gif.size[0] == 120  # 10 frames a second for a second
    with Image.open(out / "tiny.png") as poster:
        assert poster.size[0] > 160  # the panel and the frame round it


def test_only_the_gif_when_asked_to(cli, encode, tiny, tmp_path):
    said, status = run(cli, encode, tmp_path, settings={"gif_only": True})
    assert status == 0 and [p.name for p in (tmp_path / "out").iterdir()] == ["tiny.gif"]


def test_without_ffmpeg_the_gif_is_made_and_the_videos_are_told_about(
    cli, encode, tiny, tmp_path, monkeypatch
):
    monkeypatch.setattr(encode.shutil, "which", lambda name: None)
    said, status = run(cli, encode, tmp_path)
    assert status == 0
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["tiny.gif", "tiny.png"]
    assert any("tiny.mp4" in line and "need ffmpeg" in line for line in said)


def test_a_gif_over_the_limit_fails_loudly_and_over_the_target_says_so(
    cli, encode, tiny, tmp_path, monkeypatch
):
    monkeypatch.setattr(encode, "write_gif", partial(encode.write_gif, ladder=encode.LADDER[:1]))
    said, status = run(cli, encode, tmp_path, settings={"limit": 100, "gif_only": True})
    assert status == 1 and any(line.strip().startswith("ERROR") for line in said)
    assert not (tmp_path / "out" / "tiny.gif").exists()
    said, status = run(cli, encode, tmp_path, settings={"target": 100, "gif_only": True})
    assert status == 0 and any("OVER" in line for line in said)  # made, but told


def test_the_frames_can_be_kept(cli, encode, tiny, tmp_path):
    run(cli, encode, tmp_path, keep=tmp_path / "frames", settings={"gif_only": True})
    assert len(list((tmp_path / "frames" / "tiny").glob("*.png"))) == 10


def test_a_theme_can_be_named_for_a_clip_from_the_command_line(cli, encode, tiny, tmp_path):
    said, status = run(cli, encode, tmp_path, themes={"tiny": ("libre-default",)},
                       settings={"gif_only": True, "scale": 0.25})  # fmt: skip
    assert status == 0
    with Image.open(tmp_path / "out" / "tiny.gif") as gif:
        assert gif.size[1] > gif.size[0] * 0.6  # a 3.5" panel (480 x 320), not a strip of 160 x 80
    known = {"tiny": tiny}
    assert cli.parse_themes(["tiny=studio"], known) == {"tiny": ("studio",)}
    for wrong in ("tiny", "tiny=", "other=studio", "tiny=studio,slate"):
        with pytest.raises(SystemExit, match="--theme"):
            cli.parse_themes([wrong], known)


def test_the_options_become_settings(cli, tiny, tmp_path, monkeypatch, capsys):
    made = []
    monkeypatch.setattr(cli, "make", lambda *args, **kwargs: made.append(args) or 0)
    assert cli.main([str(tmp_path), "--only", "tiny", "--gif-only", "--bare", "--ffmpeg", "x",
                     "--theme", "tiny=studio", "--draft"]) == 0  # fmt: skip
    names, out, settings, themes, keep = made[0]
    assert (names, out, themes, keep) == (["tiny"], tmp_path, {"tiny": ("studio",)}, None)
    assert (settings.gif_only, settings.bare, settings.ffmpeg) == (True, True, "x")
    assert (settings.scale, settings.fps, settings.gif_width) == (0.5, 25, 480)  # a draft
    with pytest.raises(SystemExit):
        cli.main(["--only", "nothing"])
    assert "no such clip: nothing" in capsys.readouterr().err
