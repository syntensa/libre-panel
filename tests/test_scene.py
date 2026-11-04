"""Frames as their layers (render/scene.py, stage 2 of the engine): where the engine composes the
frames itself, the renderer hands a frame out as its pieces, each sent to the engine once
(devices/engine.py's SceneSender); the engine composes it pixel for pixel as the renderer
would (engine/src/scene.rs). Here: the same pixels both ways, theme by theme, a picture of
such a frame made only when asked for, the records the engine is sent, the main loop's choice,
and, where the engine is built, its frames against the renderer's and a panel through it."""

import struct
import subprocess
import threading
import time
from dataclasses import replace

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat
from test_motion import Clock, Stream

from libre_panel.app import MainLoop, turned
from libre_panel.config import Config, DeviceConfig, SensorsConfig
from libre_panel.devices import engine as engines
from libre_panel.devices import h264
from libre_panel.plugins import PluginHost
from libre_panel.plugins.host import Toast
from libre_panel.plugins.render import RenderContext, ToastStyle
from libre_panel.render import moving
from libre_panel.render import scene as scenes
from libre_panel.render.overlays import ToastLayer
from libre_panel.render.renderer import Piece
from libre_panel.render.volume_looks import VOLUME_LOOKS
from libre_panel.sensors.base import SensorHub
from libre_panel.sensors.demo import DemoProvider


class Layered(Stream):
    """A streaming display whose engine composes frames of layers: each frame it gets is kept,
    and a frame of layers is checked against the renderer's own composition of its pieces."""

    takes_scenes = True

    def __init__(self):
        super().__init__()
        self.loop = None
        self.checked = 0

    def show(self, frame, region=None):
        super().show(frame, region)
        if isinstance(frame, scenes.SceneFrame) and self.loop is not None:
            renderer = self.loop.renderer
            own = renderer._as_rgb(renderer._compose(list(frame.scene.pieces)))
            if frame.scene.half:
                own = own.transpose(Image.Transpose.ROTATE_180)
            assert scenes.compose(frame.scene).tobytes() == own.tobytes(), "not the same pixels"
            self.checked += 1


def loop_with(theme, display, clock, host=None, rotate=0):
    config = Config(theme=theme, sensors=SensorsConfig(providers=["demo"]),
                    device=DeviceConfig(rotate=rotate))  # fmt: skip
    hub = SensorHub([DemoProvider({"fixed_time": 1_700_000_000})])
    loop = MainLoop(config, host=host, hub=hub, display=display, clock=clock)
    display.loop = loop
    return loop


def run(loop, clock, frames, step=0.04):
    for _ in range(frames):
        clock.now += step
        loop.step(clock.now)


@pytest.mark.parametrize(
    "theme", ["lumen-1920x720", "arena-1920x720", "almanac", "terra", "contrast"]
)
def test_a_frame_of_layers_is_the_renderer_s_own_frame_pixel_for_pixel(theme):
    display, clock = Layered(), Clock(1000.0)
    loop = loop_with(theme, display, clock)
    try:
        run(loop, clock, 30)
    finally:
        loop.close()
    assert display.checked >= 25  # (all but frames a switch or a warm-up composes here)


def test_upside_down_a_frame_of_layers_is_turned_too():
    display, clock = Layered(), Clock(1000.0)
    loop = loop_with("lumen-1920x720", display, clock, rotate=180)
    try:
        run(loop, clock, 6)
    finally:
        loop.close()
    shown = [f for f in display.frames if isinstance(f, scenes.SceneFrame)]
    assert shown and all(f.scene.half for f in shown)
    assert display.checked == len(shown)


def test_a_message_goes_as_one_more_layer_where_its_style_lays_it_as_a_picture():
    display, clock, host = Layered(), Clock(1000.0), PluginHost()
    loop = loop_with("lumen-1920x720", display, clock, host)
    try:
        run(loop, clock, 4)
        host.notify(Toast("Hello", seconds=1.0))  # (the theme's style: the built-in card)
        run(loop, clock, 10)
        shown = display.frames[-10:]
        assert all(isinstance(f, scenes.SceneFrame) for f in shown)  # (none composed here)
        assert all(f.scene.pieces[-1][0] == "\0toast" for f in shown[1:])
        whole = [f.scene.pieces[-1][1] for f in shown[-3:]]  # (shown whole: the same piece)
        assert whole[0] is whole[1] is whole[2]
        run(loop, clock, 60)  # (the message goes)
        assert all(name != "\0toast" for name, _ in display.frames[-1].scene.pieces)
        assert display.checked >= 70
    finally:
        loop.close()
        host.close()


class Drawn(ToastStyle):
    """A style of a plugin that draws on the frame itself (it has no ``layer``)."""

    name = "drawn"

    def draw(self, frame, toast, age, previous=None):
        out = frame.copy()
        ImageDraw.Draw(out).rectangle((40, 40, 300, 120), fill=(200, 30, 30))
        return out


def test_where_a_message_is_drawn_over_the_frame_it_is_composed_here_and_sent_whole():
    display, clock, host = Layered(), Clock(1000.0), PluginHost()
    loop = loop_with("lumen-1920x720", display, clock, host)
    try:
        run(loop, clock, 4)
        assert isinstance(display.frames[-1], scenes.SceneFrame)
        assert not display.frames[-1].composed  # (nobody asked for its pixels)
        loop.toasts.style = Drawn(RenderContext(loop.renderer, Drawn), {})
        host.notify(Toast("Hello", seconds=1.0))
        run(loop, clock, 10)
        plain = [f for f in display.frames[-10:] if not isinstance(f, scenes.SceneFrame)]
        assert plain and plain[0].info["damage"] is None  # (the panel's frame was the engine's)
        run(loop, clock, 60)  # (the message goes)
        assert isinstance(display.frames[-1], scenes.SceneFrame)
    finally:
        loop.close()
        host.close()


@pytest.mark.parametrize("look", ["", *sorted(VOLUME_LOOKS)])  # (""): the theme's card
def test_a_message_laid_over_a_frame_of_layers_shows_as_its_style_draws_it(look):
    display, clock = Layered(), Clock(1000.0)
    loop = loop_with("lumen-1920x720", display, clock)
    try:
        run(loop, clock, 4)
        frame = display.frames[-1]
        assert isinstance(frame, scenes.SceneFrame)
        toasts = ToastLayer(loop.renderer)
        payload = {"label": "Volume", "progress": 0.42, "muted": False}
        if look:
            payload["look"] = look
        toasts.add([Toast("42", kind="volume", payload=payload)])
        toasts.advance(0.0)
        assert toasts.layered
        toast = toasts.current[0]
        going = toast.seconds + toasts.style_of(toast).leave_s / 2
        for age in (0.05, 1.0, going):  # coming, whole, going
            laid = toasts.draw(frame, age)
            assert isinstance(laid, scenes.SceneFrame) and not frame.composed
            assert laid.scene.pieces[-1][0] == "\0toast" and toasts.box is not None
            drawn = toasts.draw(scenes.compose(frame.scene), age)
            assert toasts.box is not None  # (where it drew: a frame made anew there alone)
            assert scenes.compose(laid.scene).tobytes() == drawn.tobytes(), (look, age)
        toasts.close()
    finally:
        loop.close()


def test_without_an_engine_that_composes_them_frames_are_composed_here():
    display, clock = Stream(), Clock(1000.0)
    loop = MainLoop(Config(theme="lumen-1920x720", sensors=SensorsConfig(providers=["demo"])),
                    hub=SensorHub([DemoProvider({})]), display=display, clock=clock)  # fmt: skip
    try:
        run(loop, clock, 4)
    finally:
        loop.close()
    assert not any(isinstance(f, scenes.SceneFrame) for f in display.frames)
    assert loop.renderer.scenes == 0


def piece(colour, x=0, y=0, size=(8, 6), **kwargs):
    return Piece(Image.new("RGBA", size, colour), x, y, **kwargs)


def scene_of(*pieces, size=(32, 16), ground=(1, 2, 3)):
    return scenes.Scene(size, ground, tuple((f"w{n}", p) for n, p in enumerate(pieces)))


def test_a_frame_of_layers_makes_its_pixels_once_when_they_are_asked_for(monkeypatch):
    scene = scene_of(piece((255, 0, 0, 128)), piece((0, 0, 255, 255), 6, 4),
                     size=(16, 12), ground=(10, 20, 30))  # fmt: skip
    made = []
    compose = scenes.compose
    monkeypatch.setattr(scenes, "compose", lambda s: made.append(s) or compose(s))
    frame = scenes.SceneFrame(scene)
    assert (frame.size, frame.mode, frame.composed) == ((16, 12), "RGB", False)
    turned_frame = turned(frame, 180)
    assert isinstance(turned_frame, scenes.SceneFrame) and turned_frame.scene.half
    assert not made
    threads = [threading.Thread(target=frame.tobytes) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(made) == 1 and frame.composed
    expected = Image.new("RGB", (16, 12), (10, 20, 30))
    expected.paste(scene.pieces[0][1].layer, (0, 0), scene.pieces[0][1].layer)
    expected.paste(scene.pieces[1][1].layer, (6, 4), scene.pieces[1][1].layer)
    assert frame.tobytes() == expected.tobytes()
    copy = frame.copy()  # (a copy is a picture like any other: one drawn on is not the scene's)
    assert not isinstance(copy, scenes.SceneFrame) and copy.tobytes() == expected.tobytes()
    upside = turned_frame.tobytes()
    assert upside == expected.transpose(Image.Transpose.ROTATE_180).tobytes()


def test_pieces_the_engine_cannot_compose_as_the_renderer_does_keep_the_frame_here():
    assert scenes.layerable(piece((1, 2, 3, 4)))
    frosted = piece((1, 2, 3, 4), backdrop=Image.new("L", (8, 6), 255), box=[0, 0, 8, 6])
    assert not scenes.layerable(frosted)
    grey = Piece(Image.new("L", (4, 4), 9), 0, 0)
    assert not scenes.layerable(grey)  # (pasted through itself, as Pillow does an L)
    assert scenes.layerable(Piece(Image.new("L", (4, 4), 9), 0, 0, add=True))
    glow = piece((1, 2, 3, 4), light=Piece(Image.new("RGB", (4, 4), 5), 1, 1))
    assert scenes.layerable(glow)


def heads(parts):
    """The records in ``parts``: (kind, a, b, what follows the head)."""
    data, out = b"".join(parts), []
    while data:
        assert data[:4] == b"LPE1"
        kind, a, b = data[4], *struct.unpack("<II", data[8:16])
        length = 12 + 4 * b if kind == engines.SCENE else b  # (the others: b bytes follow)
        out.append((kind, a, b, data[16 : 16 + length]))
        data = data[16 + length :]
    return out


def test_each_layer_goes_to_the_engine_once_while_it_is_in_the_frames():
    sender = engines.SceneSender()
    card, value = piece((9, 9, 9, 255), size=(20, 10)), piece((200, 0, 0, 77), 3, 2)
    glow = Piece(Image.new("RGBA", (6, 6), (40, 50, 60, 0)), 2, 2)
    lit = piece((5, 5, 5, 255), 4, 4, light=glow)
    first = heads(sender.records(scene_of(card, value, lit), 0, 1.5))
    kinds = [k for k, *_ in first]
    assert kinds == [engines.GROUND, engines.LAYER, engines.LAYER, engines.LAYER, engines.LAYER,
                     engines.SCENE]  # fmt: skip
    assert first[0][1:] == (0, 3, b"\x01\x02\x03")
    layer = first[2]
    x, y, w, h, how = struct.unpack("<iiIII", layer[3][:20])
    assert (x, y, w, h, how) == (3, 2, 8, 6, engines.OVER)
    assert layer[3][20:] == value.layer.tobytes()
    light = first[4]
    assert struct.unpack("<iiIII", light[3][:20]) == (2, 2, 6, 6, engines.ADD)
    assert light[3][20:] == bytes([40, 50, 60]) * 36  # (RGB, as Pillow adds it: alpha gone)
    frame = first[-1]
    planned, turn = struct.unpack("<dI", frame[3][:12])
    order = struct.unpack(f"<{frame[2]}I", frame[3][12:])
    assert (frame[1], planned, turn) == (0, 1.5, 0) and len(order) == 4
    # the same pieces again: the frame alone; one new: its layer alone, the one gone left out
    again = heads(sender.records(scene_of(card, value, lit), 1, None))
    assert [k for k, *_ in again] == [engines.SCENE]
    assert struct.unpack(f"<{again[0][2]}I", again[0][3][12:]) == order
    moved = piece((200, 0, 0, 77), 4, 2)
    third = heads(sender.records(scene_of(card, moved), 2, None))
    assert [k for k, *_ in third] == [engines.LAYER, engines.SCENE]
    new_order = struct.unpack(f"<{third[-1][2]}I", third[-1][3][12:])
    assert new_order[0] == order[0] and new_order[1] == third[0][1] not in order
    # a picture for a ground, once while it is the same picture; a video's frame as it is
    ground = Image.new("RGB", (32, 16), (7, 7, 7))
    video = Image.new("RGB", (4, 4), (1, 1, 1))
    moving._SOLID[id(video)] = video
    try:
        fourth = heads(sender.records(scene_of(Piece(video, 0, 0), ground=ground), 3, None))
    finally:
        moving._SOLID.pop(id(video), None)
    assert fourth[0][:3] == (engines.GROUND, 1, 32 * 16 * 3)
    assert struct.unpack("<iiIII", fourth[1][3][:20])[4] == engines.SOLID
    fifth = heads(sender.records(scene_of(ground=ground), 4, None))
    assert [k for k, *_ in fifth] == [engines.SCENE]


def test_a_layer_goes_to_the_engine_without_what_of_it_shows_nothing():
    """What of a layer is see-through, or of the light it adds black, changes no pixel (Pillow
    leaves the frame as it is there, and so does the engine): the engine gets the rest, at
    the place it lies, and nothing of a layer that shows nothing. A graph's newest part came
    in its graph's whole box: Lumen's 12.3" sends a third of the bytes less so."""
    sender = engines.SceneSender()
    head = Image.new("RGBA", (40, 30), (0, 0, 0, 0))
    head.paste((90, 200, 255, 255), (30, 6, 38, 26))
    head.putpixel((12, 3), (255, 255, 255, 1))  # (a breath of alpha shows: it stays)
    nothing = Image.new("RGBA", (16, 16), (255, 255, 255, 0))  # (its colour, but see-through)
    light = Image.new("RGB", (20, 20))
    light.paste((7, 0, 0), (4, 5, 9, 11))
    lit = piece((40, 50, 60, 255), 1, 1, size=(6, 6), light=Piece(light, -3, -2))
    added = Image.new("RGBA", (10, 10), (0, 0, 0, 255))  # (light it adds: its alpha goes)
    added.putpixel((9, 0), (0, 0, 3, 0))
    scene = scene_of(Piece(head, 100, 50), Piece(nothing, 5, 5), lit,
                     Piece(added, 20, 30, add=True), size=(320, 160))  # fmt: skip
    records = heads(sender.records(scene, 0, None))
    layers = [
        (*struct.unpack("<iiIII", data[:20]), data[20:])
        for kind, _, _, data in records
        if kind == engines.LAYER
    ]
    assert layers == [
        (112, 53, 26, 23, engines.OVER, head.crop((12, 3, 38, 26)).tobytes()),
        (1, 1, 6, 6, engines.OVER, lit.layer.tobytes()),
        (1, 3, 5, 6, engines.ADD, light.crop((4, 5, 9, 11)).tobytes()),
        (29, 30, 1, 1, engines.ADD, bytes([0, 0, 3])),
    ]
    frame = records[-1]
    assert frame[0] == engines.SCENE and frame[2] == 4  # (the see-through piece: no layer)
    # the piece that shows nothing is in the frame still: next time the frame alone
    again = heads(sender.records(scene, 1, None))
    assert [k for k, *_ in again] == [engines.SCENE] and again[0][2] == 4


def test_a_card_whose_inside_is_one_colour_goes_as_its_edges_and_that_colour():
    """Where the engine lays a layer of one colour, a card whose inside is one colour is made as
    the strips along its edges and that colour: its pixels made here only when asked for, and
    then the card's pixels; the frame of them the renderer's own; a third of the bytes kept at
    most (of a module's card, a sixth)."""
    from test_motion import video

    from libre_panel.render.renderer import Renderer
    from libre_panel.sensors.demo import demo_snapshot
    from libre_panel.theme.model import find_theme, load_theme

    theme = load_theme(find_theme("lumen-1920x720"))
    now = 1_700_000_000.0
    snap = demo_snapshot(fixed_time=now)
    plain, cut = (video(Renderer(theme, dither=True), fps=25) for _ in range(2))
    cut.fills = True
    own, _ = plain.render(snap, now)
    frame, _ = cut.render(snap, now, scene=True)
    assert isinstance(frame, scenes.SceneFrame)
    made = {wid: p for wid, p in frame.scene.pieces if isinstance(p.native, scenes.Filled)}
    assert set(made) >= {"frame-mid", "frame-rings", "frame-net"}
    assert all(not p.layer.composed for p in made.values())  # (for the engine: none made)
    pieces = dict(plain._last[0])
    for wid, p in made.items():
        kept = sum(part.width * part.height for part, _, _ in p.native.parts)
        assert kept * 3 < p.layer.width * p.layer.height, wid
        assert p.layer.tobytes() == pieces[wid].layer.tobytes(), wid
    assert scenes.compose(frame.scene).tobytes() == own.tobytes()
    before = dict(plain._cache)
    plain.fills = True  # (switched on: its rects made again, the rest kept as they are)
    assert "frame-mid" not in plain._cache and len(plain._cache) > len(before) // 2
    assert all(plain._cache[k] is v for k, v in before.items() if k in plain._cache)
    gradient = Image.linear_gradient("L").resize((200, 200)).convert("RGBA")
    assert scenes.filled(gradient, 20) is None  # (an inside of many colours: as its pixels)


def test_a_filled_piece_goes_to_the_engine_as_its_colour_and_the_strips_round_it():
    card = Image.new("RGBA", (200, 180), (12, 18, 24, 178))
    card.paste((90, 100, 110, 255), (0, 0, 200, 3))  # (an outline along the top)
    made = scenes.filled(card, 12)
    assert made is not None and made.fill == (12, 12, 176, 156, (12, 18, 24, 178))
    filled = Piece(made.layer, 5, 7, native=made)
    first = heads(engines.SceneSender(fills=True).records(scene_of(filled, size=(240, 200)), 0,
                                                          None))  # fmt: skip
    kinds = [k for k, *_ in first]
    assert kinds == [engines.GROUND, engines.FILL, *[engines.LAYER] * 4, engines.SCENE]
    assert struct.unpack("<iiII4B", first[1][3]) == (17, 19, 176, 156, 12, 18, 24, 178)
    sent = sum(b - 20 for k, _, b, _ in first if k == engines.LAYER)
    assert sent == 4 * (2 * 200 * 12 + 2 * 12 * 156) and not made.layer.composed
    whole = heads(engines.SceneSender().records(scene_of(filled, size=(240, 200)), 0, None))
    assert [k for k, *_ in whole] == [engines.GROUND, engines.LAYER, engines.SCENE]
    assert whole[1][3][20:] == card.tobytes()  # (an engine that lays none: its pixels, made)


def test_a_filled_card_s_pixels_go_again_when_nothing_asked_for_them_a_while(monkeypatch):
    """Made here (a frame composed here, the app's picture of the panel), a card's pixels stay
    while they are asked for, and go once nothing asked for them for FILLS_IDLE_S: the engine
    lays it without them. Asked for again, they are made again, the same."""
    from test_motion import video

    from libre_panel.render import renderer as renderers
    from libre_panel.sensors.demo import demo_snapshot
    from libre_panel.theme.model import find_theme, load_theme

    clock = [1000.0]
    monkeypatch.setattr(scenes.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(renderers.time, "monotonic", lambda: clock[0])
    renderer = video(renderers.Renderer(load_theme(find_theme("lumen-1920x720")), dither=True),
                     fps=25)  # fmt: skip
    renderer.fills = True
    now = 1_700_000_000.0
    snap = demo_snapshot(fixed_time=now)
    frame, _ = renderer.render(snap, now, scene=True)
    card = dict(frame.scene.pieces)["frame-mid"].layer
    assert isinstance(card, scenes.Again) and not card.composed
    frame.tobytes()  # (the app's picture of the panel: its cards made here)
    pixels = card.tobytes()
    assert card.composed
    for k in range(1, 9):  # (frames the engine composes, 5 s apart, then 30: looked at)
        clock[0] += 5.0 if k < 6 else 30.0
        frame, _ = renderer.render(snap, now + k / 25, scene=True)
        assert card.composed == (k < 6), k  # (kept 25 s after the last ask, gone after 55)
    assert card.tobytes() == pixels and card.composed  # (asked again: made again, the same)
    assert card.forget() == card.width * card.height * 4 and card.forget() == 0


def test_a_picture_that_goes_and_comes_again_is_the_same_whoever_asks():
    """Its pixels go while other threads ask for them: each gets them whole, the same."""
    image = Image.new("RGBA", (64, 48))
    image.putdata([(x, y, x ^ y, 200) for y in range(48) for x in range(64)])
    made = []
    again = scenes.Again(image.size, "RGBA", lambda: made.append(1) or image.copy())
    want, bad, stop = image.tobytes(), [], threading.Event()

    def ask():
        while not stop.is_set():
            if again.tobytes() != want or again.copy().tobytes() != want:
                bad.append(1)

    threads = [threading.Thread(target=ask) for _ in range(4)]
    for t in threads:
        t.start()
    for _ in range(300):
        again.forget()
        time.sleep(0.0005)
    stop.set()
    for t in threads:
        t.join()
    assert not bad and len(made) > 10


def strip(w=40, h=6):
    """A picture of ``w`` x ``h``, every pixel its own (x, y, x + y, alpha)."""
    image = Image.new("RGBA", (w, h))
    image.putdata([(x, y, x + y, 128 + x) for y in range(h) for x in range(w)])
    return image


def test_a_view_is_the_crops_pasted_and_cut_as_its_columns_are():
    phase, halo = strip(), strip(30, 6).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    cuts = [(phase, 34, 0, 0, 0, 12, 6), (halo, 2, -1, 0, 0, 3, 6), (halo, 20, 0, 9, 0, 3, 6)]
    view = scenes.viewed((12, 6), cuts)
    assert not view.layer.composed
    want = phase.crop((34, 0, 46, 6))  # (beyond the strip's right edge: nothing there)
    want.paste(halo.crop((2, -1, 5, 5)), (0, 0))
    want.paste(halo.crop((20, 0, 23, 6)), (9, 0))
    assert view.layer.tobytes() == want.tobytes()
    cut = scenes.cut_columns(scenes.viewed((12, 6), cuts), 3, 9)
    assert cut.layer.size == (6, 6) and all(part[0] is phase for part in cut.parts)
    assert cut.layer.tobytes() == want.crop((3, 0, 9, 6)).tobytes()


def test_a_graph_s_window_is_cut_by_the_engine_from_its_strip_sent_once():
    sender = engines.SceneSender(views=True)
    phase = strip()
    window = scenes.viewed((10, 6), [(phase, 5, 0, 0, 0, 10, 6)])
    card = piece((9, 9, 9, 255), size=(20, 10))
    first = heads(sender.records(scene_of(card, Piece(window.layer, 2, 3, native=window)), 0, None))
    assert [k for k, *_ in first] == [engines.GROUND, engines.LAYER, engines.SOURCE, engines.VIEW,
                                      engines.SCENE]  # fmt: skip
    source, cut = first[2], first[3]
    assert struct.unpack("<II", source[3][:8]) == (40, 6) and source[3][8:] == phase.tobytes()
    assert struct.unpack("<iiIII", cut[3][:20]) == (2, 3, 10, 6, 1)
    assert struct.unpack("<IiiiiII", cut[3][20:]) == (source[1], 5, 0, 0, 0, 10, 6)
    assert not window.layer.composed  # (its pixels were not made here)
    # the next window of the strip: the view alone; the same piece again: nothing new
    later = scenes.viewed((10, 6), [(phase, 6, 0, 0, 0, 10, 6)])
    moved = Piece(later.layer, 2, 3, native=later)
    second = heads(sender.records(scene_of(card, moved), 1, None))
    assert [k for k, *_ in second] == [engines.VIEW, engines.SCENE]
    assert struct.unpack("<I", second[0][3][20:24])[0] == source[1]
    assert [k for k, *_ in heads(sender.records(scene_of(card, moved), 2, None))] == [engines.SCENE]
    # a frame without it: the engine lets the strip go, and so does this; then it comes anew
    sender.records(scene_of(card), 3, None)
    again = scenes.viewed((10, 6), [(phase, 7, 0, 0, 0, 10, 6)])
    fourth = heads(sender.records(scene_of(Piece(again.layer, 0, 0, native=again)), 4, None))
    assert [k for k, *_ in fourth] == [engines.SOURCE, engines.VIEW, engines.SCENE]
    # an engine that does not cut views gets the window's pixels, made then
    plain = engines.SceneSender()
    other = scenes.viewed((10, 6), [(phase, 8, 0, 0, 0, 10, 6)])
    fifth = heads(plain.records(scene_of(Piece(other.layer, 0, 0, native=other)), 0, None))
    assert [k for k, *_ in fifth] == [engines.GROUND, engines.LAYER, engines.SCENE]
    assert fifth[1][3][20:] == phase.crop((8, 0, 18, 6)).tobytes()


def test_a_strip_grown_from_one_the_engine_keeps_goes_as_its_new_columns():
    """A graph's next strip, its first columns the one before's (Renderer._grown_strip), goes
    to an engine that grows pictures as the rest of its columns, while the engine keeps the
    one before (a layer of the frame before is cut from it); else as all of it."""
    sender = engines.SceneSender(views=True, grown=True)
    old = strip(40, 6)
    new = Image.new("RGBA", (36, 6), (0, 0, 0, 0))
    new.paste(old.crop((10, 0, 35, 6)), (0, 0))  # (its first 25 columns: the old one's from 10)
    new.paste(strip(11, 6).transpose(Image.Transpose.FLIP_TOP_BOTTOM), (25, 0))
    scenes.grew(new, old, 10, 25)

    def window(picture, x):
        cut = scenes.viewed((10, 6), [(picture, x, 0, 0, 0, 10, 6)])
        return Piece(cut.layer, 2, 3, native=cut)

    first = heads(sender.records(scene_of(window(old, 20)), 0, None))
    assert [k for k, *_ in first] == [engines.GROUND, engines.SOURCE, engines.VIEW, engines.SCENE]
    second = heads(sender.records(scene_of(window(new, 12)), 1, None))
    assert [k for k, *_ in second] == [engines.GROWN, engines.VIEW, engines.SCENE]
    kind, number, length, data = second[0]
    assert struct.unpack("<5I", data[:20]) == (36, 6, first[1][1], 10, 25)
    assert data[20:] == new.crop((25, 0, 36, 6)).tobytes() and length == 20 + 11 * 6 * 4
    assert sender.grown_sources == 1 and struct.unpack("<I", second[1][3][20:24])[0] == number
    # the one before gone from the engine (no layer of the frame before is cut from it), or
    # gone here, or an engine that grows none: all of it
    again = Image.new("RGBA", (36, 6), (0, 0, 0, 0))
    scenes.grew(again, old, 10, 25)
    sender.records(scene_of(window(new, 13)), 2, None)  # (the old one let go)
    third = heads(sender.records(scene_of(window(again, 12)), 3, None))
    assert [k for k, *_ in third] == [engines.SOURCE, engines.VIEW, engines.SCENE]
    plain = engines.SceneSender(views=True)
    plain.records(scene_of(window(old, 20)), 0, None)
    assert heads(plain.records(scene_of(window(new, 12)), 1, None))[0][0] == engines.SOURCE
    lost = Image.new("RGBA", (36, 6), (0, 0, 0, 0))
    scenes.grew(lost, strip(40, 6), 10, 25)  # (its base gone at once: nothing holds it)
    assert scenes.growth(lost) is None and scenes.growth(new)[1:] == (10, 25)


def test_a_laid_picture_is_its_parts_laid_over_each_other_its_pixels_made_when_asked():
    ring, rotor = strip(12, 12), strip(4, 4).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    made = scenes.laid((12, 12), [(ring, 0, 0), (rotor, 4, 4), (rotor, 10, -1)])
    assert not made.layer.composed and made.layer.size == (12, 12)
    want = Image.new("RGBA", (12, 12), (0, 0, 0, 0))
    for image, x, y in made.parts:
        want.alpha_composite(image, (x, y))
    assert made.layer.tobytes() == want.tobytes()


def test_a_laid_fan_goes_to_the_engine_as_its_parts_each_sent_once_and_kept():
    """Its ring and its rotor go once each, as pictures the engine keeps, and every frame the
    layer laid from them; the rotor's other positions are kept with it too, so one it comes
    back to goes no more. An engine that lays none gets the fan's pixels, made then."""
    sender = engines.SceneSender(laid=True)
    ring, positions = strip(12, 12), [strip(4, 4).rotate(90 * k) for k in range(3)]
    card = piece((9, 9, 9, 255), size=(20, 10))

    def fan(k, kept=()):
        made = scenes.laid((12, 12), [(ring, 0, 0), (positions[k], 4, 4)], kept)
        return Piece(made.layer, 2, 3, native=made), made

    first, made = fan(0)
    records = heads(sender.records(scene_of(card, first), 0, None))
    assert [k for k, *_ in records] == [engines.GROUND, engines.LAYER, engines.SOURCE,
                                        engines.SOURCE, engines.LAID, engines.SCENE]  # fmt: skip
    laid = records[4][3]
    assert struct.unpack("<iiIIII", laid[:24]) == (2, 3, 12, 12, 2, 0)
    assert struct.unpack("<IiiIii", laid[24:]) == (records[2][1], 0, 0, records[3][1], 4, 4)
    assert records[3][3][8:] == positions[0].tobytes()
    assert not made.layer.composed  # (its pixels were not made here)
    second, _ = fan(1, kept=(positions[0],))  # (the rotor on: the position before kept)
    records = heads(sender.records(scene_of(card, second), 1, None))
    assert [k for k, *_ in records] == [engines.SOURCE, engines.LAID, engines.SCENE]
    assert struct.unpack("<6I", records[1][3][:24])[4:] == (2, 1)  # (two parts, one kept)
    back, _ = fan(0, kept=(positions[1],))  # (back to the first position: kept, not sent)
    records = heads(sender.records(scene_of(card, back), 2, None))
    assert [k for k, *_ in records] == [engines.LAID, engines.SCENE]
    plain = engines.SceneSender()
    other, made = fan(2)
    records = heads(plain.records(scene_of(other), 0, None))
    assert [k for k, *_ in records] == [engines.GROUND, engines.LAYER, engines.SCENE]
    assert made.layer.composed  # (made for an engine that lays none)


def test_a_scrolling_graph_cut_by_the_engine_shows_what_it_showed():
    """Its window's pixels, made only when asked for, the window's as cropped here before, its
    sides cut away too where it is clipped; one drawn again at its end (a reading the strip
    did not know) as its pixels."""
    from test_motion import graph, theme, video

    from libre_panel.render.renderer import Renderer

    widgets = (graph(), graph("clipped", y=50, clip=True, glow=0.6))
    cut, cropped = (video(Renderer(theme(*widgets, size=(200, 100))), fps=25) for _ in range(2))
    cut.native_views = True
    viewed = 0
    for k in range(60):
        snapshot, now = snap_at(k), 1000 + k / 25
        frame, _ = cut.render(snapshot, now, scene=True)
        assert isinstance(frame, scenes.SceneFrame)
        for wid in ("g", "clipped"):
            mine, theirs = cut._cache[wid][1], None
            if isinstance(mine.native, scenes.View) and not mine.layer.composed:
                viewed += 1  # (its pixels not made for the frame)
        own, _ = cropped.render(snapshot, now)
        assert scenes.compose(frame.scene).tobytes() == own.tobytes(), k
        for wid in ("g", "clipped"):
            mine, theirs = cut._cache[wid][1], cropped._cache[wid][1]
            assert (mine.x, mine.y, mine.box) == (theirs.x, theirs.y, theirs.box)
            assert mine.layer.tobytes() == theirs.layer.tobytes(), (k, wid)
    assert viewed > 60  # (most windows: cut, their pixels made only here, for the test)


def snap_at(k):
    from libre_panel.sensors.base import Reading, Snapshot

    value = 50 + 40 * ((k // 10) % 2)  # a new reading every 0.4 s
    return Snapshot(readings={"v": Reading("v", float(value))})


def test_what_lay_under_the_pieces_composed_here_goes_once_the_engine_composes_them():
    """The frames composed here before the engine takes them (a panel's first few) keep what
    lies under their changing pieces: it goes with the first frame of layers (Lumen on the
    12.3": 7 MB that no frame found after)."""
    from test_motion import graph, theme, video

    from libre_panel.render.renderer import Renderer

    renderer = video(Renderer(theme(graph(), size=(200, 100))), fps=25)
    for k in range(6):
        frame, _ = renderer.render(snap_at(k), 1000 + k / 25)
        assert not isinstance(frame, scenes.SceneFrame)
    assert renderer._underlays.size > 0
    frame, _ = renderer.render(snap_at(6), 1000 + 6 / 25, scene=True)
    assert isinstance(frame, scenes.SceneFrame)
    assert renderer._underlays.size == 0
    own, _ = renderer.render(snap_at(7), 1000 + 7 / 25)  # (composed here again: as before)
    assert not isinstance(own, scenes.SceneFrame) and own.size == (200, 100)


def test_a_turning_globe_goes_to_the_engine_once_and_each_step_as_its_number():
    """Its tables once, its map once a light and its air every two degrees (as sources), each
    step a layer with its number and your place's mark; its pixels, made only when asked for,
    are Turning.frame's."""
    from libre_panel.render import earth

    turning = earth.Turning(200, 20.0)
    turning.light(1_700_000_000.0)
    sender = engines.SceneSender(globes=True)
    place = (52.5, 13.4)
    first = scenes.turned_globe(turning, 3, place)
    assert not first.layer.composed
    one = heads(sender.records(scene_of(Piece(first.layer, 4, 5, native=first),
                                        size=(220, 220)), 0, None))  # fmt: skip
    assert [k for k, *_ in one] == [engines.GROUND, engines.GLOBE, engines.SOURCE,
                                    engines.SOURCE, engines.GLOBE_LAYER, engines.SCENE]  # fmt: skip
    tables, map_, air, layer = one[1], one[2], one[3], one[4]
    size, width, disc, rim = struct.unpack("<IIII", tables[3][:16])
    assert (size, width, disc) == (200, turning.width, len(turning.inside))
    assert map_[3][8:] == turning.map_image.tobytes() and air[3][8:] == first.air.tobytes()
    globe, map_n, air_n, step, x, y, mw, mh, mx, my = struct.unpack("<IIIIiiIIii",
                                                                    layer[3][:40])  # fmt: skip
    assert (globe, map_n, air_n, step, x, y) == (tables[1], map_[1], air[1], 3, 4, 5)
    assert (mw, mh, (mx, my)) == (*first.mark[0].size, first.mark[1])
    assert layer[3][40:] == first.mark[0].tobytes()
    assert not first.layer.composed  # (its pixels were not made here)
    # a later step with the same air (it is laid anew every two degrees): its layer alone
    air_of = lambda step: round(turning.longitude(step) / turning.AIR_STEP)  # noqa: E731
    step = next(n for n in range(4, 40) if air_of(n) == air_of(3))
    later = scenes.turned_globe(turning, step, place)
    two = heads(sender.records(scene_of(Piece(later.layer, 4, 5, native=later),
                                        size=(220, 220)), 1, None))  # fmt: skip
    assert [k for k, *_ in two] == [engines.GLOBE_LAYER, engines.SCENE]
    assert struct.unpack("<III", two[0][3][:12]) == (tables[1], map_[1], air[1])
    # its pixels where a frame is composed here: Turning.frame's
    assert later.layer.tobytes() == turning.frame(step, place).tobytes()
    # an engine that does not make globes gets the pixels
    plain = heads(engines.SceneSender().records(
        scene_of(Piece(later.layer, 4, 5, native=later), size=(220, 220)), 0, None))  # fmt: skip
    assert [k for k, *_ in plain] == [engines.GROUND, engines.LAYER, engines.SCENE]


@pytest.fixture
def engine():
    found = h264.find_engine()
    if not found or not engines.takes_scenes(found):
        pytest.skip("needs libre-panel-engine that composes scenes (cargo build --release)")
    return found


class Fed(Layered):
    """A display whose frames go to the engine's ``encode`` as the stream would send them: as
    their layers, every seventh as the frame's own pixels (a message over it), all turned with
    ``half``; the renderer's own frame of each is kept to compare."""

    def __init__(self, engine, size, out, half, rings=False, views=False, globes=False,
                 fills=False, laid=False, grown=False):  # fmt: skip
        super().__init__()
        self.half, self.makes_rings = half, rings
        self.proc = subprocess.Popen(
            [engine, "encode", "--size", f"{size[0]}x{size[1]}", "--turn", "0", "--fps", "25",
             "--crf", "20", "--kbit", "4000", "--keyint", "250", "--dump-rgb", str(out)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        )  # fmt: skip
        self.sender = engines.SceneSender(rings, views, globes, fills, laid, grown)
        self.own, self.n = [], 0

    def show(self, frame, region=None):
        Stream.show(self, frame, region)
        if not isinstance(frame, scenes.SceneFrame):
            return
        renderer = self.loop.renderer
        own = renderer._as_rgb(renderer._compose(list(frame.scene.pieces)))
        if self.half:
            own, frame = own.transpose(Image.Transpose.ROTATE_180), frame.turned()
        if self.n % 7 == 3:
            h264.engine_frame(own, None, self.n)(self.proc.stdin)
        else:
            for part in self.sender.records(frame.scene, self.n, None):
                self.proc.stdin.write(part)
        self.own.append(own.tobytes())
        self.n += 1


@pytest.mark.parametrize(
    ("theme", "half", "rings", "toast", "views"),
    [("lumen-1920x720", False, False, False, False), ("arena-1920x720", True, False, False, False),
     ("lumen-1920x720", False, True, False, False), ("arena-1920x720", True, False, True, False),
     ("lumen-1920x720", False, False, False, True), ("arena-1920x720", True, False, True, True),
     ("terra-1920x720", False, False, False, "globes"), ("terra-1920x720", True, False, True,
                                                         "globes"),
     ("lumen-1920x720", False, False, False, "fills"), ("almanac-1920x720", True, False, True,
                                                        "fills"),
     ("lumen-1920x720", False, False, False, "laid"), ("lumen-1920x720", True, False, True,
                                                       "laid"),
     ("arena-1920x720", False, False, False, "laid"), ("lumen-1920x720", False, False, False,
                                                       "grown"),
     ("arena-1920x720", True, False, True, "grown")],
)  # fmt: skip
def test_the_engine_composes_a_frame_of_layers_as_the_renderer_does(
    engine, tmp_path, theme, half, rings, toast, views
):
    """Pixel for pixel, a message over it too (one more layer, as it comes, shows and goes),
    the graphs' windows cut by the engine from their strips too; where the engine makes the
    rings too, to a level, at few pixels."""
    if rings and not engines.makes_rings(engine):
        pytest.skip("needs libre-panel-engine that makes rings (cargo build --release)")
    if views and not engines.cuts_views(engine):
        pytest.skip("needs libre-panel-engine that cuts views (cargo build --release)")
    globes = views == "globes"  # (and the turning globes made by the engine, Terra's)
    if globes and not engines.makes_globes(engine):
        pytest.skip("needs libre-panel-engine that makes globes (cargo build --release)")
    fills = views == "fills"  # (and the cards' insides laid by the engine as their colour)
    if fills and not engines.makes_fills(engine):
        pytest.skip("needs libre-panel-engine that lays fills (cargo build --release)")
    laid = views in ("laid", "grown")  # (and the fans and texts laid by the engine)
    if laid and not engines.lays_laid(engine):
        pytest.skip("needs libre-panel-engine that lays layers (cargo build --release)")
    grown = views == "grown"  # (and the graphs' strips grown by the engine from those before)
    if grown and not engines.grows_sources(engine):
        pytest.skip("needs libre-panel-engine that grows sources (cargo build --release)")
    clock, host = Clock(1000.0), PluginHost()
    display = Fed(engine, (1920, 720), tmp_path / "frames.rgb", half, rings, bool(views), globes,
                  fills, laid, grown)  # fmt: skip
    loop = loop_with(theme, display, clock, host)
    try:
        run(loop, clock, 4)
        if toast:  # (it comes, it shows, it goes: 0.5 s and the card's 0.25)
            host.notify(Toast("Volume 42", icon="volume", seconds=0.5))
        run(loop, clock, 26)
    finally:
        loop.close()
        host.close()
        display.proc.stdin.close()
        display.proc.wait(30)
    if toast:
        tops = [f.scene.pieces[-1][0] for f in display.frames if isinstance(f, scenes.SceneFrame)]
        assert tops.count("\0toast") >= 15  # (its frames went as their layers too)
    made = (tmp_path / "frames.rgb").read_bytes()
    size = 1920 * 720 * 3
    assert display.n >= 25 and len(made) == size * display.n
    for n, own in enumerate(display.own):
        if not rings:
            assert made[n * size : (n + 1) * size] == own, f"frame {n} is not the renderer's"
            continue
        got = Image.frombytes("RGB", (1920, 720), made[n * size : (n + 1) * size])
        diff = ImageChops.difference(got, Image.frombytes("RGB", (1920, 720), own))
        assert max(high for _, high in diff.getextrema()) <= 1, f"frame {n}"
        assert diff.convert("L").point(lambda v: v > 0 and 255).histogram()[255] <= 200
    if rings:
        assert display.sender._rings and loop.renderer.native_rings  # (made by the engine)
    if views and not fills:  # (the graphs' strips went to it, their windows cut there)
        assert display.sender._sources and loop.renderer.native_views
    if globes:  # (the globe's tables, its map and air went to it, its steps made there)
        assert display.sender._globes and loop.renderer.native_globes
        steps = {f.scene.pieces[i][1].native.step for f in display.frames
                 if isinstance(f, scenes.SceneFrame) for i in range(len(f.scene.pieces))
                 if isinstance(f.scene.pieces[i][1].native, scenes.Globe)}  # fmt: skip
        assert len(steps) >= 5  # (it turned)
    if fills:  # (the cards' insides went to it as their colour, their edges as their pixels)
        assert display.sender.filled >= 3 and loop.renderer.fills
    if laid:  # (the fans went as their parts, laid by the engine, their pixels not made here)
        lain = [p for f in display.frames if isinstance(f, scenes.SceneFrame)
                for _wid, p in f.scene.pieces if isinstance(p.native, scenes.Laid)]  # fmt: skip
        assert len(lain) >= 25 and loop.renderer.native_laid
        assert display.sender.laid_layers >= 25  # (they went as LAID records)


def test_the_engine_grows_a_strip_from_the_one_before_as_it_was_drawn(engine, tmp_path):
    """Strips each grown from the one before (their first columns the old one's from a column
    on, the rest new), their windows cut by the engine as they scroll: the frames are those
    composed here, pixel for pixel, and each strip after the first went as its new columns."""
    if not engines.grows_sources(engine):
        pytest.skip("needs libre-panel-engine that grows sources (cargo build --release)")
    out = tmp_path / "frames.rgb"
    proc = subprocess.Popen(
        [engine, "encode", "--size", "64x32", "--turn", "0", "--fps", "25", "--crf", "20",
         "--kbit", "4000", "--keyint", "250", "--dump-rgb", str(out)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
    )  # fmt: skip
    sender, numbers = engines.SceneSender(views=True, grown=True), iter(range(1, 1 << 30))

    def noise(w, h):  # (pixels of every kind, see-through ones among them)
        seed = next(numbers)
        return Image.frombytes("RGBA", (w, h), bytes((seed * 7919 + i * i * 31) % 251 for i in
                                                     range(w * h * 4)))  # fmt: skip

    strip, own = noise(80, 20), []
    card = piece((9, 9, 9, 255), size=(64, 32))
    for n in range(8):
        if n:  # (grown from the one before, as Renderer._grown_strip makes it)
            shift, keep = 3 + n % 2, 60 - n
            grown = Image.new("RGBA", (80, 20), (0, 0, 0, 0))
            grown.paste(strip.crop((shift, 0, shift + keep, 20)), (0, 0))
            grown.paste(noise(80 - keep, 20), (keep, 0))
            scenes.grew(grown, strip, shift, keep)
            strip = grown
        cut = scenes.viewed((40, 20), [(strip, 5 + n, 0, 0, 0, 40, 20)])
        scene = scene_of(card, Piece(cut.layer, 10, 6, native=cut), size=(64, 32))
        for part in sender.records(scene, n, None):
            proc.stdin.write(part)
        own.append(scenes.compose(scene).tobytes())
    proc.stdin.close()
    assert proc.wait(30) == 0
    made, size = out.read_bytes(), 64 * 32 * 3
    assert len(made) == size * 8
    for n, frame in enumerate(own):
        assert made[n * size : (n + 1) * size] == frame, f"frame {n} is not the renderer's"
    assert sender.grown_sources == 7


def test_a_video_panel_shows_frames_of_layers_the_engine_composed(engine, monkeypatch, caplog):
    from fake_panel import FakePanel
    from panel_socket import PanelSocket
    from test_video import decode, settle, video_config

    from libre_panel.devices import turzx_usb, turzx_video
    from libre_panel.devices.engine import EngineLink, EngineStream, EngineTransport
    from libre_panel.devices.turzx_video import TurzxVideoDisplay

    if not h264.find_ffmpeg():
        pytest.skip("needs ffmpeg to decode what the panel got")
    fake = FakePanel()
    sock = PanelSocket(fake)

    def open_by_engine(path, pid=None, beside=(), skip=0, popen=subprocess.Popen):
        command = [path, "panel", "--pid", f"{fake.pid:04x}", "--link", sock.link]
        return EngineTransport(EngineLink(command), fake.pid)

    monkeypatch.setattr(EngineTransport, "open", staticmethod(open_by_engine))
    monkeypatch.setattr(turzx_usb.UsbTransport, "present", staticmethod(lambda pid: True))
    monkeypatch.setattr(turzx_video, "RESTART", turzx_video._Restart())
    monkeypatch.setattr(turzx_video, "RATES", {})
    caplog.set_level("INFO", logger="libre_panel.devices.turzx_video")
    display = TurzxVideoDisplay(video_config(encoder="native"))
    display.open()
    sources = []
    try:
        for n in range(30):
            square = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
            ImageDraw.Draw(square).ellipse((10, 10, 110, 110), fill=(250, 200, 40, 255))
            pieces = (("square", Piece(square, 40 + n * 50, 100)),)
            frame = scenes.SceneFrame(scenes.Scene((1920, 480), (20, 40, 80), pieces))
            display.show(frame)
            assert isinstance(display.encoder, EngineStream) and display.takes_scenes
            assert not frame.composed  # (its layers went: its pixels were not made here)
            sources.append(scenes.compose(frame.scene))
            time.sleep(0.02)
        settle(fake, timeout=15, at_least=25)
    finally:
        display.close()
        sock.close()
    assert sock.errors == []
    said = [r.getMessage() for r in caplog.records if "from their layers" in r.getMessage()]
    assert len(said) == 1 and ("rings" in said[0]) == engines.makes_rings(engine)
    shown = decode(fake.stream())
    assert len(shown) >= 25
    for source, picture in zip(sources, shown, strict=False):
        native = source.transpose(Image.Transpose.ROTATE_270)
        assert max(ImageStat.Stat(ImageChops.difference(native, picture)).mean) < 6


# -- a lit gauge's ring, made by the engine (render/lit.py's Ring, engine/src/ring.rs) ---------


def lumen_ring():
    """A ring of Lumen's on the 12.3" (its renderer, widget and colours), as it draws it."""
    from libre_panel.render import lit

    seen = {}
    real = lit.gauge_piece

    def capture(renderer, widget, frac, paint, native=None):
        seen.setdefault("ring", (renderer, widget, paint))
        return real(renderer, widget, frac, paint, native)

    display, clock = Layered(), Clock(1000.0)
    lit.gauge_piece = capture
    try:
        loop = loop_with("lumen-1920x720", display, clock)
        try:
            run(loop, clock, 3)
        finally:
            loop.close()
    finally:
        lit.gauge_piece = real
    return seen["ring"]


RINGS = [  # (what, the widget's changes, the colour: None for the theme's)
    ("closed", {}, None),
    ("faded", {}, (46, 230, 214, 150)),
    ("open", {"start_angle": 135, "end_angle": 405}, None),
    ("small", {"w": 120, "h": 120, "thickness": 8}, None),
    ("open and faded", {"start_angle": -210, "end_angle": 30, "w": 140, "h": 140},
     (250, 120, 30, 200)),
]  # fmt: skip
FRACS = (0.0005, 0.05, 0.2, 0.5, 0.77, 0.95, 0.9995, 1.0)


def test_a_ring_the_engine_makes_is_made_here_only_when_asked_for_and_then_as_before():
    from libre_panel.render import lit

    renderer, widget, paint = lumen_ring()
    for frac in (0.03, 0.6, 1.0):
        renderer.native_rings = True
        native = lit.gauge_piece(renderer, widget, frac, paint)
        renderer.native_rings = False
        plain = lit.gauge_piece(renderer, widget, frac, paint)
        assert isinstance(native.native, lit.Ring) and native.native.frac == frac
        assert not native.layer.composed and plain.native is None
        assert (native.x, native.y, native.box) == (plain.x, plain.y, plain.box)
        assert native.layer.tobytes() == plain.layer.tobytes()  # (made here: the same pixels)
    gradient = lit.gauge_piece(renderer, widget, 0.5, (paint[0], (255, 0, 0, 255)))
    renderer.native_rings = True
    assert lit.gauge_piece(renderer, {**widget, "glow": 0.5}, 0.5, paint).native is None
    assert lit.gauge_piece(renderer, widget, 0.5, (paint[0], (255, 0, 0, 255))).native is None
    assert lit.gauge_piece(renderer, widget, 0.0, paint).native is None  # (the groove alone)
    renderer.native_rings = False
    assert gradient.native is None


def test_a_ring_goes_to_the_engine_once_and_each_value_as_its_share_lit():
    from libre_panel.render import lit

    renderer, widget, paint = lumen_ring()
    renderer.native_rings = True
    try:
        pieces = [lit.gauge_piece(renderer, widget, f, paint) for f in (0.2, 0.3)]
    finally:
        renderer.native_rings = False
    sender = engines.SceneSender(rings=True)
    first = heads(sender.records(scene_of(pieces[0], size=(1920, 720)), 0, None))
    assert [k for k, *_ in first] == [engines.GROUND, engines.RING, engines.RING_LAYER,
                                      engines.SCENE]  # fmt: skip
    side = pieces[0].layer.width
    assert first[1][2] == 56 + 2 * side * side * 4
    ring = struct.unpack("<II5d4B", first[1][3][:52])
    assert ring[:2] == (side, pieces[0].native.shape[4]) and ring[7:] == tuple(paint[0])
    base, x, y, _, frac = struct.unpack("<IiiId", first[2][3])
    assert (base, x, y, frac) == (first[1][1], pieces[0].x, pieces[0].y, 0.2)
    second = heads(sender.records(scene_of(pieces[1], size=(1920, 720)), 1, None))
    assert [k for k, *_ in second] == [engines.RING_LAYER, engines.SCENE]  # (the ring it has)
    assert struct.unpack("<IiiId", second[0][3])[0] == base
    # a piece whose picture is not the ring's any more (faded, turned) goes as its pixels
    faded = replace(pieces[1], layer=pieces[1].layer.copy())
    third = heads(sender.records(scene_of(faded, size=(1920, 720)), 2, None))
    assert [k for k, *_ in third] == [engines.LAYER, engines.SCENE]
    # an engine that makes no rings gets every ring as its pixels
    plain = heads(engines.SceneSender().records(scene_of(pieces[0], size=(1920, 720)), 0, None))
    assert [k for k, *_ in plain] == [engines.GROUND, engines.LAYER, engines.SCENE]


@pytest.fixture
def ring_engine(engine):
    if not engines.makes_rings(engine):
        pytest.skip("needs libre-panel-engine that makes rings (cargo build --release)")
    return engine


def test_the_engine_makes_a_lit_ring_as_the_renderer_does_to_a_level(ring_engine, tmp_path):
    from libre_panel.render import lit

    renderer, widget, paint = lumen_ring()
    size, ground = (1920, 720), (40, 44, 52)
    out = tmp_path / "rings.rgb"
    proc = subprocess.Popen(
        [ring_engine, "encode", "--size", "1920x720", "--turn", "0", "--fps", "25", "--crf",
         "20", "--kbit", "4000", "--keyint", "250", "--dump-rgb", str(out)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
    )  # fmt: skip
    sender, own = engines.SceneSender(rings=True), []
    try:
        n = 0
        for _what, change, colour in RINGS:
            ring = {**widget, **change}
            colours = ((colour or paint[0]), None)
            for frac in FRACS:
                renderer.native_rings = True
                native = lit.gauge_piece(renderer, ring, frac, colours)
                renderer.native_rings = False
                plain = lit.gauge_piece(renderer, ring, frac, colours)
                assert native.native is not None
                for part in sender.records(scene_of(native, size=size, ground=ground), n, None):
                    proc.stdin.write(part)
                frame = Image.new("RGB", size, ground)
                frame.paste(plain.layer, (plain.x, plain.y), plain.layer)
                own.append((plain, frame.tobytes()))
                n += 1
    finally:
        renderer.native_rings = False
        proc.stdin.close()
        proc.wait(30)
    made = out.read_bytes()
    whole = size[0] * size[1] * 3
    assert len(made) == whole * len(own)
    for k, (plain, frame) in enumerate(own):
        got = Image.frombytes("RGB", size, made[k * whole : (k + 1) * whole])
        diff = ImageChops.difference(got, Image.frombytes("RGB", size, frame))
        assert max(high for _, high in diff.getextrema()) <= 1  # (to a level)
        # (and at few pixels: the light worked out in floating point as there)
        assert sum(diff.convert("L").point(lambda v: v > 0 and 255).histogram()[1:]) <= 0.002 * (
            plain.layer.width * plain.layer.height
        )
