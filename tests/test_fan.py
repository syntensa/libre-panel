"""The fan widget: how far it turns with its speed, what it shows without one, and its pictures."""

import numpy as np
import pytest
from PIL import Image

from libre_panel.render import fan
from libre_panel.render.fan import POSITIONS, Rotor
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.adapt import adapt_theme
from libre_panel.theme.model import THEME_FORMAT, ThemeError, parse_theme

CYAN = (34, 211, 238)


def run(rotor, rpm, seconds, fps=50, start=0.0, max_rpm=3000.0):
    """The rotor at ``rpm`` for ``seconds`` of planned time, a frame every 1 / ``fps``."""
    for n in range(round(seconds * fps) + 1):
        rotor.advance(rpm, max_rpm, start + n / fps)
    return rotor


def pitches(rotor, rpm, seconds, fps=50):
    """How many blade pitches the rotor turns in ``seconds`` once it runs at ``rpm``."""
    run(rotor, rpm, 1.0, fps)  # run up
    before, now = rotor.angle, 1.0 + 1 / fps
    turned = 0.0
    for n in range(round(seconds * fps)):
        rotor.advance(rpm, 3000.0, now + n / fps)
        turned += (rotor.angle - before) % 1.0
        before = rotor.angle
    return turned


def snapshot(**readings):
    return Snapshot(readings={k: Reading(k, v) for k, v in readings.items()})


def theme(*widgets, size=(300, 100)):
    return parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": size[0], "height": size[1]},
            "background": {"color": "#0a1119"},
            "widgets": list(widgets),
        }
    )


def widget(**kw):
    return {"type": "fan", "id": "f", "x": 20, "y": 20, "sensor": "rpm", **kw}


# -- how it turns ------------------------------------------------------------------------


def test_the_rotor_turns_faster_with_the_speed():
    slow = pitches(Rotor("a"), 600, 2)
    medium = pitches(Rotor("a"), 1200, 2)
    fast = pitches(Rotor("a"), 2400, 2)
    assert 0 < slow < medium < fast
    # the turn is the shown speed (turns a second times the blades) over the time
    shown = fan.turns(1200, 3000) * fan.BLADES * 2
    assert medium == pytest.approx(shown, rel=0.02)


def test_a_fan_stands_at_zero_rpm_and_without_a_reading():
    for rpm in (0, 12, None):
        rotor = Rotor("a")
        run(rotor, rpm, 3)
        angle = rotor.angle
        run(rotor, rpm, 3, start=10)
        assert rotor.angle == angle and rotor.speed == 0 and not rotor.turning


def test_it_runs_out_and_stops_when_the_speed_drops_to_zero():
    rotor = run(Rotor("a"), 1500, 2)
    assert rotor.turning
    run(rotor, 0, 4, start=2.02)
    stopped = rotor.angle
    assert rotor.speed == 0
    run(rotor, 0, 2, start=7)
    assert rotor.angle == stopped


def test_a_new_speed_is_followed_with_inertia_not_at_once():
    rotor = run(Rotor("a"), 600, 2)
    slow = rotor.speed
    rotor.advance(2400, 3000.0, 2.04)
    assert slow < rotor.speed < fan.turns(2400, 3000)
    run(rotor, 2400, 4, start=2.06)
    assert rotor.speed == pytest.approx(fan.turns(2400, 3000), rel=0.01)


@pytest.mark.parametrize("fps", [25, 30, 50])
def test_the_turn_is_the_same_at_every_frame_rate(fps):
    rotor = run(Rotor("a"), 1000, 5, fps)
    reference = run(Rotor("a"), 1000, 5, 50)
    assert rotor.angle == pytest.approx(reference.angle, abs=0.02) or abs(
        rotor.angle - reference.angle
    ) == pytest.approx(1.0, abs=0.02)


def test_it_never_turns_further_than_a_frame_can_show():
    """At a high speed and a low frame rate the step is capped: no turn backwards."""
    rotor = Rotor("a")
    rotor.advance(3000, 3000.0, 0.0)
    before = rotor.angle
    rotor.advance(3000, 3000.0, 0.2)  # a long frame
    assert (rotor.angle - before) % 1.0 <= fan.MAX_STEP + 1e-9


def test_a_time_that_goes_back_or_a_long_pause_does_not_throw_it_about():
    rotor = run(Rotor("a"), 1500, 2)
    angle = rotor.angle
    rotor.advance(1500, 3000.0, 0.5)  # earlier than the last frame
    assert rotor.angle == angle
    rotor.advance(1500, 3000.0, 500.0)  # a pause of minutes: one step at most
    assert (rotor.angle - angle) % 1.0 <= fan.MAX_STEP + 1e-9


def test_fans_start_in_different_positions():
    assert Rotor("one").angle != Rotor("two").angle
    assert 0 <= Rotor("one").angle < 1


def test_settled_it_shows_the_look_of_the_speed_but_does_not_turn():
    rotor = Rotor("a")
    angle = rotor.angle
    rotor.settle(2400, 3000.0)
    assert rotor.angle == angle and rotor.turning and rotor.blur > 0 and rotor.ring == 0.8


def test_the_position_is_one_of_the_48_over_a_blade_pitch():
    rotor = Rotor("a")
    rotor.angle = 0.0
    assert rotor.phase == 0
    rotor.angle = 1 / POSITIONS * 1.01
    assert rotor.phase == 1
    rotor.angle = 0.9999
    assert rotor.phase == POSITIONS - 1


# -- what it looks like ----------------------------------------------------------------------


def lit(image):
    """Pixels of the ring that are bright (the arc, not the groove or the rotor inside)."""
    a = np.asarray(image, np.int16)
    yy, xx = np.mgrid[: a.shape[0], : a.shape[1]]
    ring = np.hypot(yy - a.shape[0] / 2 + 0.5, xx - a.shape[1] / 2 + 0.5) > 20.5
    return int((ring & (a[..., 3] > 200) & (a[..., :3].max(-1) > 200)).sum())


def test_the_ring_shows_the_share_of_the_top_speed():
    shares = []
    for rpm in (0, 750, 1500, 2250, 3000):
        rotor = Rotor("a")
        rotor.advance(rpm, 3000.0, 0.0)
        shares.append(lit(fan.fan_image(CYAN, 48, 48, rotor)))
    assert shares[0] == 0 and shares == sorted(shares) and shares[-1] > 3 * shares[1]


def test_a_stopped_fan_shows_dimmed_and_one_without_a_reading_more_so():
    def weight(rpm):
        rotor = Rotor("a")
        rotor.advance(rpm, 3000.0, 0.0)
        a = np.asarray(fan.fan_image(CYAN, 48, 48, rotor, "none"), np.float32)
        return (a[..., 3] / 255).sum()

    assert weight(None) < weight(0) < weight(1400)


def test_the_rotor_is_coloured_and_kept():
    one = fan.rotor_image(CYAN, 38, 5)
    assert fan.rotor_image(CYAN, 38, 5) is one  # coloured once, kept
    assert fan.rotor_image(CYAN, 38, 5 + POSITIONS) is one  # the pitch repeats
    a = np.asarray(one, np.float32)
    solid = a[..., 3] > 200
    mean = a[..., :3][solid].mean(axis=0)
    assert mean[0] < mean[1] and mean[0] < mean[2]  # cyan stays cyan
    other = np.asarray(fan.rotor_image((250, 80, 80), 38, 5), np.float32)
    assert other[..., 0][solid].mean() > other[..., 2][solid].mean()


def test_the_positions_differ_and_the_blur_is_the_mean_of_them():
    a = np.asarray(fan.rotor_image(CYAN, 38, 0), np.float32)
    b = np.asarray(fan.rotor_image(CYAN, 38, 24), np.float32)
    assert np.abs(a - b).sum() > 0
    disc = np.asarray(fan.disc_image(CYAN, 38), np.float32)
    inside = (a[..., 3] > 250) & (b[..., 3] > 250)
    # blurred, the blades' light and dark meet: less contrast than one position has
    assert disc[..., :3][inside].std() < a[..., :3][inside].std()


def test_the_picture_follows_the_size_of_the_fan():
    for w, h in ((48, 48), (96, 96), (30, 60), (24, 24)):
        rotor = Rotor("a")
        rotor.advance(1400, 3000.0, 0.0)
        image = fan.fan_image(CYAN, w, h, rotor)
        size, margin, canvas = fan.geometry(w, h)
        assert size == min(w, h) and image.size == (canvas, canvas) and canvas > size > 0
        assert np.asarray(image)[..., 3].max() > 200


def test_the_three_frames():
    rotor = Rotor("a")
    rotor.advance(1400, 3000.0, 0.0)
    ring, square, none = (fan.fan_image(CYAN, 48, 48, rotor, f) for f in ("ring", "square", "none"))
    assert not np.array_equal(np.asarray(ring), np.asarray(square))
    assert not np.array_equal(np.asarray(ring), np.asarray(none))
    # without a frame only the rotor is there: its box is the middle of the picture
    box = Image.fromarray(np.asarray(none)[..., 3]).getbbox()
    assert box is not None and box[0] > 5 and box[2] < ring.width - 5


# -- in a theme ----------------------------------------------------------------------------------


def test_the_widget_is_in_the_spec_and_checks_max_rpm():
    assert theme(widget()).widgets[0]["max_rpm"] == 3000
    assert theme(widget()).widgets[0]["frame"] == "ring"
    with pytest.raises(ThemeError, match="max_rpm"):
        theme(widget(max_rpm=0))
    with pytest.raises(ThemeError, match="frame"):
        theme(widget(frame="round"))


@pytest.mark.parametrize("size", [(1920, 480), (320, 240)])
def test_a_fan_renders_and_turns_at_both_sizes(size):
    w = size[1] // 5
    t = theme(widget(x=10, y=10, w=w, h=w), widget(id="g", x=w + 30, y=10, w=w, h=w), size=size)
    renderer = Renderer(t, animate=True)
    renderer.continuous = True
    snap = snapshot(rpm=1500.0)
    frames = []
    for n in range(10):
        image, boxes = renderer.render(snap, 1.0 + n / 50)
        frames.append(image.tobytes())
    assert image.size == size and renderer.warnings == []
    assert boxes["f"] == [10, 10, w, w] and renderer.moving
    assert len(set(frames)) > 5  # it turns


def test_a_fan_without_a_reading_or_at_zero_stands_still_and_does_not_keep_the_loop_busy():
    for snap in (snapshot(rpm=0.0), Snapshot()):
        renderer = Renderer(theme(widget(), widget(id="g", x=100)), animate=True)
        frames = {renderer.render(snap, 1.0 + n / 50)[0].tobytes() for n in range(40)}
        assert len(frames) == 1 and not renderer.moving


def test_hidden_when_the_sensor_has_no_value_if_asked():
    renderer = Renderer(theme(widget(hide_if_missing=True)), animate=True)
    assert renderer.render(Snapshot(), 1.0)[1] == {}
    assert "f" in renderer.render(snapshot(rpm=900.0), 1.1)[1]


def test_without_animation_a_fan_does_not_turn():
    renderer = Renderer(theme(widget()), animate=False, preview=True)
    frames = {renderer.render(snapshot(rpm=1500.0), 1.0 + n)[0].tobytes() for n in range(5)}
    assert len(frames) == 1 and not renderer.moving


def test_effects_and_the_same_picture_for_the_same_position():
    renderer = Renderer(theme(widget(glow=0.5, opacity=0.8)), animate=True)
    renderer.render(snapshot(rpm=0.0), 1.0)
    first = renderer._fans.copy()
    renderer.render(snapshot(rpm=0.0), 1.02)
    assert dict(renderer._fans) == first  # a fan that stands is one piece, kept


def test_the_demo_fan_readings_drive_the_preset_block():
    from libre_panel.editor.presets import PRESETS, resolve_tokens

    block = next(p for p in PRESETS if p["id"] == "fan")
    kinds = [w["type"] for w in block["widgets"]]
    assert kinds.count("fan") == 1 and "metric" in kinds
    t = theme(*resolve_tokens(block["widgets"], {}), size=(200, 80))
    image, boxes = Renderer(t).render(demo_snapshot(fixed_time=1_700_000_000), 1.0)
    assert "fan" in boxes and image.size == (200, 80)


def test_warming_up_loads_every_position_off_the_render_thread():
    renderer = Renderer(theme(widget(w=52, h=52)), animate=True)
    paused = []
    renderer.warm(snapshot(rpm=1200.0), 1.0, pause=lambda: paused.append(1))
    k = fan.rotor_size(52)
    assert k in fan._layers and fan._layers[k].shape[:2] == (POSITIONS, 4)
    assert fan.rotor_image((34, 211, 238), k, 47) is fan.rotor_image((34, 211, 238), k, 47)
    assert paused


def test_a_theme_with_fans_has_their_rotors_ready_before_the_first_frame():
    """The main loop warms a new theme without readings before its first frame: the
    rotor's pictures are read then, not in the frame (about 200 ms)."""
    renderer = Renderer(theme(widget(w=58, h=58, color="#123456")), animate=True)
    assert renderer.prepares  # a fan alone is something to prepare
    k = fan.rotor_size(58)
    fan._layers.pop(k, None)
    renderer.warm(None, 0.0)
    assert k in fan._layers
    assert all(("rotor", (0x12, 0x34, 0x56), k, p) in fan._images for p in range(POSITIONS))


def test_a_fan_stays_round_when_the_theme_is_adapted():
    data = theme(widget(x=100, y=40, w=48, h=48), size=(480, 320)).to_dict()
    out = adapt_theme(data, "custom", "landscape", "scale", width=1920, height=480)
    wide = next(w for w in out["widgets"] if w["type"] == "fan")
    assert wide["w"] == wide["h"] == 72  # the smaller scale (480 / 320 = 1.5), not 4 x
