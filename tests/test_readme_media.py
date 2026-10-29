"""tools/readme_media.py: the clips for the README, the website and forum posts.

The encoders and, further down, the panel and app recipes, with tiny pictures and short clips so
that they stay fast. What a clip looks like is judged by eye when it is made; here are the
things that must hold on every run: the same frames twice, the files that come out, the
fallbacks and the size check.
"""

import io
import json
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
    # ffprobe beside ffmpeg, else on the path (the ffmpeg Libre Panel ships has none beside it)
    beside = Path(FFMPEG).with_name(Path(FFMPEG).name.replace("ffmpeg", "ffprobe"))
    ffprobe = str(beside) if beside.is_file() else shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("ffprobe is not installed")
    shown = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=pix_fmt,width,height,color_space,codec_name", "-of",
         "default=nw=1", str(mp4)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
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
    """A recipe cut to two and a half seconds, for what needs no more than to see it run (the
    rings of a close-up stand still between two readings, a second apart: a clip of a second and a
    half had them move in seven or eight frames of fifteen, by the steps of their arcs)."""

    def record(name, themes=None, scale=0.15):
        recipe = replace(panel.CLIPS[name], seconds=2.5, warmup=0.5)
        settings = encode.Settings(fps=10, scale=scale)
        return panel.record(name, recipe, settings, tmp_path_factory.mktemp(name), themes)

    return record


def pixels(recorded, n):
    return np.asarray(recorded.frames.image(n), np.int16)


def differ(recorded, a, b):
    """How much frames ``a`` and ``b`` differ, on average per pixel (of 255)."""
    return float(np.abs(pixels(recorded, a) - pixels(recorded, b)).mean())


# what the tests below use
LONG = ("card-ignition", "card-turns", "volume-band", "volume-looks", "theme-tube",
        "game-entrance", "themes-tour")  # fmt: skip


def test_every_panel_recipe_runs_headless_and_makes_a_clip(panel, clip, short):
    assert list(panel.CLIPS) == [
        "video-mode", "card-ignition", "card-turns", "ai-card", "agents", "small-panel",
        "volume-band", "volume-looks", "theme-tube", "game-entrance", "close-up-rings",
        "close-up-curves", "encore", "nimbus", "almanac", "music-page", "round-report",
        "screens-in-turn", "themes-tour", "where-it-started",
    ]  # fmt: skip
    widths = {}
    for name, recipe in panel.CLIPS.items():
        # (a 3.5" panel at 0.15 is 72 pixels wide: too small for the card in its corner)
        recorded, seconds = (
            (clip(name), recipe.seconds)
            if name in LONG
            else (short(name, scale=0.3 if name == "small-panel" else 0.15), 2.5)
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


def test_the_volume_shows_in_each_of_its_looks_and_is_gone_between_them(panel, clip):
    looks = clip("volume-looks")
    every = len(panel.VOLUME_LOOKS)
    shown = [panel.LOOK_EVERY_S * n + 1.1 for n in range(every)]  # each look at its height
    gaps = [panel.LOOK_EVERY_S * (n + 1) + 0.4 for n in range(every - 1)]  # and gone between
    rate = looks.frames.count / panel.CLIPS["volume-looks"].seconds
    frame = lambda at: min(round(at * rate), looks.frames.count - 1)  # noqa: E731
    pictures = [pixels(looks, frame(at)) for at in shown]
    for i in range(every):
        for j in range(i + 1, every):  # four looks, four different pictures
            assert float(np.abs(pictures[i] - pictures[j]).mean()) > 1, (i, j)
    for at in shown:
        assert differ(looks, 0, frame(at)) > 1
    for at in gaps:
        assert differ(looks, 0, frame(at)) < differ(looks, 0, frame(at - 1.6)), at


def test_the_tube_goes_to_the_other_screen_and_back(clip):
    tube = clip("theme-tube")
    assert differ(tube, 5, 28) > 12  # another screen from 2.5 s on
    assert differ(tube, 5, 12) > 5  # a line of light opens between the two at 1.2 s
    assert differ(tube, 5, tube.frames.count - 1) < 3.5  # back where it began (graphs moved on)


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


def test_the_time_of_day_runs_in_a_clip_of_a_clock_and_stands_still_in_the_others(panel, short):
    clock = panel.Clock()
    clock.now = panel.START + 2.5
    assert panel._RunningTime(clock).time() == panel.images.WHEN + 2.5
    assert panel._FrozenTime().time() == panel.images.WHEN
    assert panel.CLIPS["almanac"].clock_runs and not panel.CLIPS["video-mode"].clock_runs
    # Almanac Analog's second hand glides: a face a second apart is not the same
    analog = short("nimbus", themes=("almanac-analog",), scale=0.3)
    assert differ(analog, 0, 10) > 0.1


def test_the_screens_in_turn_come_one_after_another_and_round_again(panel, clip):
    turn = panel.CLIPS["screens-in-turn"]
    assert turn.loop == "cut" and turn.seconds == pytest.approx(3 * 2.6)  # the clip is one round
    recorded = clip("screens-in-turn")
    # (each in the middle of its time, clear of the transitions: they begin near 1, 3.6, 6.2 s)
    assert differ(recorded, 4, 28) > 8 and differ(recorded, 28, 52) > 8  # three screens
    assert differ(recorded, 4, 52) > 8
    assert differ(recorded, 4, 76) < 1  # and round again to the first


def test_the_cards_take_turns_in_the_slot_and_come_round_again(panel, clip):
    turns = panel.CLIPS["card-turns"]
    assert turns.seconds == pytest.approx(len(panel.CARD_WAYS) * panel.CARD_TURN_S + 1.2)
    recorded = clip("card-turns")
    slot = slice(0, recorded.frames.size[0] * 2 // 5)  # (the card slot is at the left of the bar)

    def card(at):
        return pixels(recorded, round(at * 10))[:, slot]

    # each card in the middle of its time (it changes at the reading after its time is up): what
    # plays, the weather, a clock face, the date
    shown = [card((n + 0.6) * panel.CARD_TURN_S) for n in range(len(panel.CARD_WAYS))]
    for i in range(len(shown)):
        for j in range(i + 1, len(shown)):
            assert float(np.abs(shown[i] - shown[j]).mean()) > 4, (i, j)
    # and the fourth goes back to what plays, before the clip ends (its progress moved on)
    assert float(np.abs(shown[0] - card(turns.seconds - 0.1)).mean()) < 3


def test_the_cards_go_from_one_to_the_next_another_way_each_time(panel):
    """The recipe's moments, on a stand-in for the stage: the cards' time is cut short at once,
    and before each change the slot is given the next way."""
    from types import SimpleNamespace

    box = SimpleNamespace(turns=[300.0] * 4, turn_at=0.0, transition="light")

    class Stage:
        clock = SimpleNamespace(now=12.0)
        loop = SimpleNamespace(renderer=SimpleNamespace(_slots={"cards": box}))
        moments: list = []

        def theme(self, name, widgets=None):
            self.widgets = widgets
            return name

        def show(self, name, **how):
            pass

        def at(self, at, do):
            self.moments.append((at, do))

    stage = Stage()
    panel.card_turns(stage, ("lumen",))
    slots = [w for w in stage.widgets if w.get("module") == "cards"]
    assert slots and all(w["order"] == "turns" for w in slots)
    assert slots[0]["items"].split() == ["playback", "forecast", "analog", "date"]
    ways = []
    for at, do in sorted(stage.moments, key=lambda moment: moment[0]):
        do(stage)
        ways.append((round(at, 2), box.transition))
    assert ways == [
        (0.0, "fade"), (round(panel.CARD_TURN_S + 0.3, 2), "slide"),
        (round(2 * panel.CARD_TURN_S + 0.3, 2), "rise"),
        (round(3 * panel.CARD_TURN_S + 0.3, 2), "wipe"),
    ]  # fmt: skip
    assert box.turns == [panel.CARD_TURN_S] * 4 and box.turn_at == 12.0


def test_the_tour_shows_each_of_the_themes_and_comes_round_to_the_first(panel, clip):
    tour = panel.CLIPS["themes-tour"]
    assert tour.loop == "fade" and tour.clock_runs  # (Lumen at both ends: blended)
    assert tour.seconds == pytest.approx(len(tour.themes) * panel.TOUR_S)  # the clip is one round
    recorded = clip("themes-tour")
    # it begins in the first one's time; each of the others clear of the beam between two
    others = range(1, len(tour.themes))
    middles = [0] + [round((k * panel.TOUR_S + 0.4) * 10) for k in others]
    for i in range(len(middles)):
        for j in range(i + 1, len(middles)):
            assert differ(recorded, middles[i], middles[j]) > 6, (i, j)
    # and comes round to it at the end: Lumen, as the clip begins
    assert differ(recorded, 0, recorded.frames.count - 1) < differ(recorded, 0, middles[1]) / 3


def test_the_start_clip_is_the_boot_logo_then_the_standby_clip_round_and_round(
    panel, encode, tmp_path
):
    from libre_panel.render import bootscreen

    start = panel.START_CLIPS["start-screen"]
    assert start.kind == "panel" and start.about
    fps = bootscreen.CLIP_FPS  # (the clip's own rate: a round is its frames)
    settings = encode.Settings(fps=fps, scale=0.1)
    recorded = panel.record_start("start-screen", start, settings, tmp_path)
    frames = recorded.frames
    assert frames.count == round(start.seconds * fps) and 0 <= recorded.poster < frames.count
    logo = round(start.logo_s * fps)
    assert differ(recorded, 0, logo - 1) == 0  # the logo stands
    assert differ(recorded, logo - 1, logo) > 0  # the clip begins
    after = logo + bootscreen.CLIP_FRAMES  # and comes round
    assert differ(recorded, logo, after) == 0 < differ(recorded, logo, logo + 20)


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
        "app-hardware-names", "player",
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


@pytest.fixture(scope="module")
def samples():
    """tools/media_samples.py: the made-up library of the Player's clip."""
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(TOOLS))
        import media_samples

        yield media_samples


def test_the_made_up_library_draws_each_file_at_any_size_and_time(samples):
    from libre_panel import player

    assert len(samples.SAMPLES) >= 5
    for name, sample in samples.SAMPLES.items():
        assert sample.kind in player.KINDS, name
        first, again = samples.frame(name, 3.0, (160, 90)), samples.frame(name, 3.0, (160, 90))
        assert first.size == (160, 90) and first.mode == "RGB", name
        assert first.tobytes() == again.tobytes(), name  # the same on every run
        assert np.asarray(first).std() > 8, name  # a picture, not a plain colour
        later = samples.frame(name, 3.4, (160, 90))
        moved = np.abs(np.asarray(first, np.int16) - np.asarray(later, np.int16)).mean()
        # a video and an animation move; a picture does not
        assert (moved > 0.5) == (sample.kind != "picture"), name
        if sample.seconds:  # round and round
            assert samples.frame(name, 3.0 + sample.seconds, (160, 90)).tobytes() == first.tobytes()
    assert samples.frame("Night drive.mp4", 1.0, (320, 120)).size == (320, 120)  # the panel's


class _Route:
    """What Playwright hands a route's handler to answer with."""

    def fulfill(self, status=200, content_type=None, body=""):
        self.status, self.content_type, self.body = status, content_type, body


class _Page:
    """What the Player's stage asks of the page: scripts before the app, and routes."""

    def __init__(self):
        self.scripts, self.routes = [], {}

    def add_init_script(self, script):
        self.scripts.append(script)

    def route(self, pattern, handler):
        self.routes[pattern] = handler

    def ask(self, pattern, url, method="GET", body=None):
        from types import SimpleNamespace

        answer = _Route()
        request = SimpleNamespace(url=url, method=method, post_data=body and json.dumps(body))
        self.routes[pattern](answer, request)
        return answer


def test_the_player_of_the_clip_plays_the_made_up_library_on_its_own_time(app_clips, samples):
    from types import SimpleNamespace

    page = _Page()
    stage = app_clips.PlayerStage(page)
    assert app_clips.LIVE_GATE in page.scripts  # the panel's picture waits for the clip's
    assert "__liveGate" in app_clips.INIT_SCRIPT and "__liveNext" in app_clips.INIT_SCRIPT
    stage.camera = SimpleNamespace(seconds=2.0)
    player, live = "**/api/player", "**/api/plugins/control/live?**"

    def ask(pattern, query="", body=None):
        address = pattern.strip("*?").replace("**", "") + query
        return page.ask(pattern, "http://127.0.0.1" + address, "POST" if body else "GET", body)

    state = json.loads(ask(player).body)
    assert state["available"] and state["playing"] is None
    assert [item["name"] for item in state["library"]] == list(samples.SAMPLES)
    assert ask(live, "?w=480").status == 404  # nothing plays: the theme is on the panel
    played = ask(player, body={"action": "play", "start": "Northern lights.mp4"})
    playing = json.loads(played.body)["playing"]
    # (well into it, a fifth of it at most: 3:07 long, at 0:37.4)
    assert playing["name"] == "Northern lights.mp4" and playing["position"] == 37.4
    stage.camera.seconds = 3.5  # the clip goes on, and the video with it
    assert stage.status()["position"] == pytest.approx(38.9)
    picture = ask(live, "?w=480")
    assert picture.content_type == "image/jpeg"
    assert Image.open(io.BytesIO(picture.body)).size == (480, 180)  # the 12.3"'s shape
    ask(player, body={"action": "next"})  # an animated picture of 12 s: a little way into it
    assert stage.status()["name"] == "Lava lamp.gif" and stage.status()["position"] == 2.4
    stage.camera.seconds = 15.5  # (and round again at its end)
    assert stage.status()["position"] == pytest.approx(2.4)
    ask(player, body={"action": "previous"})
    assert stage.status()["name"] == "Northern lights.mp4"
    ask(player, body={"action": "stop"})
    assert stage.status() is None
    tile = ask("**/api/player/picture?**", "?name=Dusk%20at%20sea.jpg&w=320&v=900000")
    assert Image.open(io.BytesIO(tile.body)).size == (320, 180)


# -- the scenes and the README's own pictures --------------------------------------------------


@pytest.fixture(scope="module")
def scene(panel):
    """tools/media_scene.py: the scenes drawn around the panel and the app."""
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(TOOLS))
        import media_scene

        yield media_scene


def test_the_window_goes_onto_the_panel_is_made_as_large_and_comes_back(scene):
    """The panel as a monitor of its own: the moments of the scene, frame by frame."""
    assert scene.CLIPS["screen-monitor"].kind == "scene"
    assert scene.CLIPS["screen-monitor"].seconds == scene.SCENE_S
    frames = round(scene.SCENE_S * scene.FPS)
    moments = [scene.moment(n / scene.FPS, 800.0) for n in range(frames + 1)]
    start, end = moments[0], moments[-1]
    assert start == (scene.BROWSER, 0.0, scene.REST, False)  # on the monitor, the pointer aside
    assert end[0] == scene.BROWSER and end[1] == 0.0 and not end[3]  # back, where it began
    assert np.hypot(end[2][0] - scene.REST[0], end[2][1] - scene.REST[1]) < 1  # (it loops)
    middle = scene.moment(4.6, 800.0)  # (the poster)
    assert middle[0] == scene.ON_PANEL and middle[1] == pytest.approx(800.0) and not middle[3]
    jumps = []
    pairs = zip(moments, moments[1:], strict=False)
    for n, ((box, scroll, pointer, pressed), (after, _, then, held)) in enumerate(pairs):
        if pressed:  # held by its title bar
            assert 0 <= pointer[1] - box.y <= scene.TITLE
            assert box.x <= pointer[0] <= box.x + box.w
        # the pointer never jumps; the window only as it is taken off the panel, back to its
        # own size under the pointer (as a desktop does with a window as large as its monitor)
        assert np.hypot(then[0] - pointer[0], then[1] - pointer[1]) < 160
        if max(abs(a - b) for a, b in zip(box.astuple(), after.astuple(), strict=True)) >= 160:
            jumps.append(n)
            assert box == scene.ON_PANEL and after.w == scene.BROWSER.w and held
        assert 0.0 <= scroll <= 800.0
    assert len(jumps) == 1


def test_the_desk_has_the_panel_below_the_monitor_as_the_desktop_has_it(scene):
    desk = scene.Desk()
    assert desk.to_picture(0, 0) == (desk.mx, desk.my)
    assert desk.to_picture(*scene.PANEL_AT) == (desk.px, desk.py)
    assert desk.py > desk.my + desk.screen_m[1]  # (the panel stands in front, under it)
    assert desk.base.size == (desk.width, desk.height)
    page = Image.new("RGB", (200, 100), (255, 0, 0))
    drawn = scene.window(page, (300, 200), "Libre Panel", "syntensa.github.io/libre-panel")
    assert drawn.size == (300, 200)
    assert drawn.getpixel((150, 150)) == (255, 0, 0)  # the page fills it under the title bar
    assert drawn.getpixel((150, scene.TITLE // 2))[0] < 100


def test_the_top_of_the_readme_is_the_headline_over_lumen_in_its_frame(
    scene, encode, tmp_path, monkeypatch
):
    made = []

    def lumen(settings):  # (Lumen, made up: a colour a frame)
        sink = encode.FrameSink(tmp_path / f"lumen-{len(made)}", fps=50)
        for n in range(4):
            sink.add(Image.new("RGB", (384, 96), (40 * n, 90, 200)))
        made.append(sink.frames().folder)
        return sink.frames(), 1

    monkeypatch.setattr(scene, "_bare_lumen", lumen)
    pictures = {}
    for language in ("en", "de"):
        out = encode.FrameSink(tmp_path / language, fps=scene.FPS)
        poster = scene.hero(language)(encode.Settings(), out)
        assert not made[-1].exists()  # (Lumen's frames are gone once they are in it)
        frames = out.frames()
        assert frames.count == 2 and poster == 0  # every second frame: 50 a second to 25
        first, second = (np.asarray(frames.image(n), np.int16) for n in range(2))
        w, h = frames.size
        assert w > 384 and h > 96 * 1.5  # the frame round the screen, the headline above it
        top = scene._headline(w, language, 96 / 480).height
        head = first[:top]
        lit = (head[..., 1] > 150) & (head[..., 2] > 150) & (head[..., 0] < 120)
        assert lit.sum() > 30  # the lit words, in the accent
        assert np.abs(first - second)[top:].mean() > 1  # Lumen moves below
        assert np.abs(first - second)[:top].mean() == 0  # the headline stands
        pictures[language] = first
    assert np.abs(pictures["en"] - pictures["de"]).mean() > 0.5  # in each README's language


def test_a_scene_draws_the_website_and_the_app_in_their_windows(scene, encode, tmp_path):
    pytest.importorskip("playwright", reason="Playwright is not installed")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scene, "SCENE_S", 0.4)  # (ten frames: the first moments of the scene)
        sink = encode.FrameSink(tmp_path / "scene", fps=scene.FPS)
        try:
            scene.screen_monitor(encode.Settings(scale=0.5), sink)
        except scene.media_app.Unavailable as error:
            pytest.skip(str(error))
    frames = sink.frames()
    desk = scene.Desk()
    assert frames.count == 10 and frames.size == (round(desk.width / 2), round(desk.height / 2))
    picture = np.asarray(frames.image(0), np.int16)
    monitor = picture[desk.my // 2 : (desk.my + desk.screen_m[1]) // 2,
                      desk.mx // 2 : (desk.mx + desk.screen_m[0]) // 2]  # fmt: skip
    assert monitor.std() > 20  # the app and the website, not a bare wallpaper


@pytest.fixture(scope="module")
def images(panel):
    """tools/readme_images.py, the README's and the website's pictures."""
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(TOOLS))
        import readme_images

        yield readme_images


def test_the_overview_shows_the_apps_own_icons_lit_on_a_tile(images):
    from xml.etree import ElementTree

    assert set(images.ICONS) <= set(images.pictures())
    for name, icon in images.ICONS.items():
        drawing = images.app_icon(icon)
        assert "<path" in drawing or "<circle" in drawing, name
        svg = ElementTree.fromstring(images.icon_tile(icon))  # (well formed)
        assert svg.get("width") == svg.get("height") == "56", name
        assert drawing in images.icon_tile(icon)
    assert images.app_icon("palette").count("<circle") == 3  # its dots, as the app draws them
    with pytest.raises(KeyError):
        images.app_icon("nothing-of-the-sort")


def test_the_download_buttons_are_the_websites_on_a_clear_background(images, tmp_path):
    pytest.importorskip("playwright", reason="Playwright is not installed")
    try:
        images.buttons(tmp_path)
    except Exception as error:  # no Chromium
        if "Executable doesn't exist" in str(error):
            pytest.skip(str(error).splitlines()[0])
        raise
    for name in images.BUTTONS:
        picture = np.asarray(Image.open(tmp_path / name).convert("RGBA"))
        h, w = picture.shape[:2]
        assert w > 2 * h, name  # a button, with room for its glow
        assert picture[0, 0, 3] == 0 and picture[-1, -1, 3] == 0, name  # clear round it
        assert picture[h // 2, w // 2, 3] == 255, name  # and solid in it
    windows = np.asarray(Image.open(tmp_path / "button-windows.png").convert("RGB"), np.int16)
    assert windows[..., 1].mean() > windows[..., 0].mean() + 20  # the primary one, in the accent


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
    for name in ("video-mode", "volume-band", "volume-looks", "theme-tube", "game-entrance",
                 "card-ignition",
                 "small-panel", "close-up-rings", "close-up-curves", "where-it-started",
                 "app-look", "app-drag-module", "app-ring-style", "app-command-palette",
                 "app-hardware-names", "encore", "nimbus", "almanac", "music-page",
                 "round-report", "screens-in-turn", "start-screen", "card-turns",
                 "themes-tour", "player", "hero", "hero-de", "screen-monitor"):  # fmt: skip
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
