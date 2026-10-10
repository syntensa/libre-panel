"""Motion kept by the main loop: gliding values, fading colours, graph histories."""

from libre_panel.render.motion import PAUSE_S, Motion
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.theme.model import THEME_FORMAT, parse_theme


def theme(*widgets, size=(200, 100), smoothing_ms=400):
    return parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": size[0], "height": size[1]},
            "background": {"color": "#000000"},
            "animation": {"smoothing_ms": smoothing_ms},
            "widgets": list(widgets),
        }
    )


def bar(wid="b", sensor="v", **kw):
    return {"type": "bar", "id": wid, "x": 0, "y": 0, "w": 200, "h": 20, "sensor": sensor,
            "radius": 0, "background": "#202020", "color": "#ff0000", "smooth": True,
            **kw}  # fmt: skip


def snap(**values):
    return Snapshot(readings={k: Reading(k, v) for k, v in values.items()})


def test_widgets_of_one_sensor_glide_together_and_none_stays_none():
    renderer = Renderer(theme(bar("a"), bar("b", y=40, max=200)), animate=True)
    renderer.render(snap(v=0.0), 0.0)
    renderer.render(snap(v=100.0), 0.1)
    value = renderer.motion.value("v", 0.1)
    assert 0 < value < 100 and renderer.moving  # one spring for both bars
    assert renderer._eased(bar("a"), 100.0, 0.1) == renderer._eased(bar("b"), 100.0, 0.1)
    renderer.render(snap(v=None), 0.2)
    assert renderer.motion.value("v", 0.2) is None  # no reading: shown as missing, not as 0
    assert renderer._eased(bar("a"), None, 0.2) is None


def test_a_value_off_the_panel_starts_again_where_it_is():
    """A value no frame drew for a while (another theme did not show it) does
    not catch up a glide nobody saw: it starts at its reading."""
    motion = Motion()
    motion.begin({}, 0.0, 0.5, 0.02)
    motion.glide("v", 0.0, 0.0, 0.5, 0.02)
    for k in range(1, 30):  # frames without "v", 20 ms apart
        motion.begin({}, k / 50, 0.5, 0.02)
    now = 30 / 50
    motion.begin({}, now, 0.5, 0.02)
    assert now > PAUSE_S
    assert motion.glide("v", 100.0, now, 0.5, 0.02) == (100.0, 0.0)
    # drawn on every frame, it glides
    motion.glide("w", 0.0, now, 0.5, 0.02)
    motion.begin({}, now + 0.02, 0.5, 0.02)
    assert motion.glide("w", 100.0, now + 0.02, 0.5, 0.02)[0] < 50


def test_the_png_paths_pauses_do_not_restart_a_glide():
    """Without video the loop sleeps up to half a second while nothing moves;
    the value stood on the panel all the while, so a new reading glides."""
    motion = Motion()
    motion.begin({}, 0.0, 0.45, 0.1)
    motion.glide("v", 0.0, 0.0, 0.45, 0.1)
    motion.begin({}, 0.5, 0.45, 0.1)  # the next frame, half a second later
    value, speed = motion.glide("v", 100.0, 0.5, 0.45, 0.1)
    assert 0 < value < 50 and speed > 0


def test_values_glide_the_same_at_25_30_and_50_fps():
    halfway = {}
    for fps in (25, 30, 50):
        renderer = Renderer(theme(bar()), animate=True)
        renderer.fps = fps
        for n in range(round(1.5 * fps)):
            now = 10.0 + n / fps
            renderer.render(snap(v=0.0 if now < 10.5 else 100.0), now)
        halfway[fps] = renderer.motion.value("v", 10.7)  # 0.2 s into a 0.4 s glide
    assert all(60 < value < 85 for value in halfway.values()), halfway
    assert max(halfway.values()) - min(halfway.values()) < 5, halfway


def test_a_new_theme_goes_on_from_where_the_last_one_was():
    """The main loop hands its motion to the next renderer: a value mid-glide
    goes on gliding in the new theme instead of starting over."""
    motion = Motion()
    first = Renderer(theme(bar()), animate=True, motion=motion)
    first.fps = 50
    first.render(snap(v=0.0), 0.0)
    for k in range(1, 6):
        first.render(snap(v=100.0), k / 50)
    before = motion.value("v", 5 / 50)
    second = Renderer(theme(bar("other", y=40)), animate=True, motion=motion)
    second.fps = 50
    second.render(snap(v=100.0), 6 / 50)
    after = motion.value("v", 6 / 50)
    assert 0 < before < after < 100  # one frame further on, not back at 0, not jumped to 100


def test_previews_keep_their_own_motion():
    loop = Motion()
    panel = Renderer(theme(bar()), animate=True, motion=loop)
    panel.render(snap(v=40.0), 0.0)
    preview = Renderer(theme(bar()), animate=True, preview=True)
    preview.render(snap(v=90.0), 0.05)
    assert preview.motion is not loop and loop.value("v", 0.05) == 40.0


def metric(wid="m", **kw):
    return {"type": "metric", "id": wid, "x": 0, "y": 40, "sensor": "v", "format": "{value:.0f}",
            "font_size": 30, **kw}  # fmt: skip


def shown_text(renderer, wid):
    return renderer._cache[wid][0][0]


def test_numbers_glide_with_smooth_and_read_the_reading_without():
    renderer = Renderer(theme(bar(), metric(smooth=True), metric("raw", y=70)), animate=True)
    renderer.fps = 50
    renderer.render(snap(v=0.0), 0.0)
    texts = []
    for k in range(1, 26):  # half a second after a jump to 100
        renderer.render(snap(v=100.0), k / 50)
        texts.append(int(shown_text(renderer, "m")))
    assert shown_text(renderer, "raw") == "100"
    assert texts[0] < 20 and texts == sorted(texts) and len(set(texts)) > 15  # counts up
    assert abs(texts[4] - renderer.motion.value("v", 5 / 50)) <= 0.5  # the bar's value
    for k in range(26, 100):  # settled: the reading itself, nothing moves
        renderer.render(snap(v=100.0), k / 50)
    assert shown_text(renderer, "m") == "100" and not renderer.moving


def test_gliding_numbers_draw_each_text_once():
    renderer = Renderer(theme(metric(smooth=True)), animate=True)
    renderer.fps = 50
    drawn = []
    real = renderer._text_piece

    def counted(widget, text, color):
        drawn.append(text)
        return real(widget, text, color)

    renderer._text_piece = counted
    for k in range(200):  # 0 -> 30 -> 0 -> 30, two seconds each way
        value = 30.0 if (k // 50) % 2 else 0.0
        renderer.render(snap(v=value), k / 50)
    assert len(drawn) == len(set(drawn)) <= 31  # 0..30, each once


def rules():
    return [{"above": 80, "color": "#ffff00"}, {"above": 90, "color": "#ff0000"}]


def test_threshold_colours_fade_and_a_bar_and_its_number_change_together():
    widgets = (
        bar(color="#00ff00", color_rules=rules()),
        metric(color="#00ff00", color_rules=rules()),
    )
    renderer = Renderer(theme(*widgets, smoothing_ms=0), animate=True)
    renderer.fps = 50
    renderer.render(snap(v=50.0), 0.0)
    assert renderer._cache["m"][0][1] == "#00ff00"
    bars, texts = [], []
    for k in range(1, 15):  # 50 -> 85 at once: past the first threshold
        renderer.render(snap(v=85.0), k / 50)
        bars.append(renderer._cache["b"][0][1][0])
        texts.append(renderer._cache["m"][0][1])
    assert any(b not in ((0, 255, 0, 255), (255, 255, 0, 255)) for b in bars)  # on its way
    assert bars[-1] == (255, 255, 0, 255) and texts[-1] == "#ffff00"  # there within 0.3 s
    for colour, text in zip(bars, texts, strict=True):  # the same colour at every step
        assert text.lower() in ("#ffff00", "#00ff00") or text == "#{:02x}{:02x}{:02x}{:02x}".format(
            *colour
        )
    still = Renderer(theme(*widgets, smoothing_ms=0))  # no animation: at once
    still.render(snap(v=50.0), 0.0)
    still.render(snap(v=85.0), 0.02)
    assert still._cache["m"][0][1] == "#ffff00"
