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
