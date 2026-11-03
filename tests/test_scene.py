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
from libre_panel.render import moving
from libre_panel.render import scene as scenes
from libre_panel.render.renderer import Piece
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


def test_where_a_message_is_drawn_over_the_frame_it_is_composed_here_and_sent_whole():
    display, clock, host = Layered(), Clock(1000.0), PluginHost()
    loop = loop_with("lumen-1920x720", display, clock, host)
    try:
        run(loop, clock, 4)
        assert isinstance(display.frames[-1], scenes.SceneFrame)
        assert not display.frames[-1].composed  # (nobody asked for its pixels)
        host.notify(Toast("Hello", seconds=1.0))
        run(loop, clock, 10)
        plain = [f for f in display.frames[-10:] if not isinstance(f, scenes.SceneFrame)]
        assert plain and plain[0].info["damage"] is None  # (the panel's frame was the engine's)
        run(loop, clock, 60)  # (the message goes)
        assert isinstance(display.frames[-1], scenes.SceneFrame)
    finally:
        loop.close()
        host.close()


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

    def __init__(self, engine, size, out, half, rings=False):
        super().__init__()
        self.half, self.makes_rings = half, rings
        self.proc = subprocess.Popen(
            [engine, "encode", "--size", f"{size[0]}x{size[1]}", "--turn", "0", "--fps", "25",
             "--crf", "20", "--kbit", "4000", "--keyint", "250", "--dump-rgb", str(out)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        )  # fmt: skip
        self.sender, self.own, self.n = engines.SceneSender(rings), [], 0

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
    ("theme", "half", "rings"),
    [("lumen-1920x720", False, False), ("arena-1920x720", True, False),
     ("lumen-1920x720", False, True)],
)  # fmt: skip
def test_the_engine_composes_a_frame_of_layers_as_the_renderer_does(
    engine, tmp_path, theme, half, rings
):
    """Pixel for pixel; where the engine makes the rings too, to a level, at few pixels."""
    if rings and not engines.makes_rings(engine):
        pytest.skip("needs libre-panel-engine that makes rings (cargo build --release)")
    clock = Clock(1000.0)
    display = Fed(engine, (1920, 720), tmp_path / "frames.rgb", half, rings)
    loop = loop_with(theme, display, clock)
    try:
        run(loop, clock, 30)
    finally:
        loop.close()
        display.proc.stdin.close()
        display.proc.wait(30)
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


def test_a_video_panel_shows_frames_of_layers_the_engine_composed(engine, monkeypatch):
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
