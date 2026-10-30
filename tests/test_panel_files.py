"""The panel's own screens on a TURZX USB panel: the boot logo and the standby clip in each of
their looks for every USB panel's size, as finely as the panel shows them, and what is sent to
put them on it (a fake panel keeps the files and every command)."""

import io
import struct
from types import SimpleNamespace

import numpy
import pytest
from PIL import Image
from test_editor_server import request, server  # noqa: F401 (a fixture)

from libre_panel.devices import panel_files as F
from libre_panel.devices.base import DeviceError
from libre_panel.devices.h264 import find_ffmpeg, nal_units, slice_count
from libre_panel.devices.models import get_model
from libre_panel.render import bootscreen

NINE = get_model("turing-9.2-usb")


class FakePanel:
    """A TURZX USB panel's files and settings, and the commands it was sent."""

    def __init__(self, files=None, listing="header"):
        self.files = dict(files or {})
        self.listing = listing  # "header": [99, ack] in front; "bare": the piece alone (the 9.2")
        self.sent = []  # (command, name or args, payload size)
        self.listed = 0
        self.closed = False

    @staticmethod
    def name_of(args):
        size = struct.unpack(">I", args[:4])[0]
        return args[8 : 8 + size].decode("ascii")

    def command(self, cmd, args=b"", payload=b"", timeout_ms=2000):
        assert cmd not in (11, 12, 13), cmd
        if cmd in (40, 42, 110, 113):
            name = self.name_of(args)
            assert name, "a file command with an empty name"
            self.sent.append((cmd, name, len(payload)))
            if cmd == 40:
                assert struct.unpack(">I", args[4:8])[0] == len(payload)
                self.files[name] = payload
            if cmd == 42:
                self.files.pop(name, None)
        else:
            self.sent.append((cmd, bytes(args), len(payload)))
        return bytes([cmd, 0xC8]) + bytes(510)

    def ask(self, cmd, args=b"", timeout_ms=2000, echo=True):
        name = self.name_of(args)
        if cmd == 98:
            self.sent.append((98, name, 0))
            return bytes([98, 0xC8]) + bytes(6) + struct.pack("<I", len(self.files.get(name, b"")))
        assert cmd == 99 and name == "usr/data"
        self.sent.append((99, name, 0))
        text = "".join(f"{n.rsplit('/', 1)[1]}\0" for n in sorted(self.files)).encode()
        piece = text.ljust(F.LIST_PIECES * F.PIECE, b"\0")[
            self.listed * F.PIECE : (self.listed + 1) * F.PIECE
        ]
        self.listed = (self.listed + 1) % F.LIST_PIECES
        if self.listing == "bare":
            assert not echo  # (a check for the command's number would refuse it)
            return piece
        if self.listing == "none":
            return bytes(16)  # a panel that does not list (the reply of the error)
        return bytes([99, 0xC8]) + bytes(6) + piece

    def drain(self):
        self.drained = True

    def close(self):
        self.closed = True


def jobs(panel, model=NINE):
    return F.StartScreen(opener=lambda asked: (panel, asked or model), ffmpeg=find_ffmpeg)


# -- the pictures ------------------------------------------------------------------------------


@pytest.mark.parametrize("model", F.models(), ids=lambda m: m.id)
def test_every_usb_panel_gets_its_boot_logo_and_clip_in_its_size(model):
    logo = bootscreen.boot_logo(model)
    assert logo.size == model.size("landscape") and logo.mode == "RGB"
    pixels = numpy.asarray(logo, float)
    long_side = max(logo.size)
    quiet = round(bootscreen.QUIET * long_side)
    ends = numpy.concatenate([pixels[:, :quiet], pixels[:, -quiet:]], axis=1)
    assert ends.max() < 24  # the short ends are dark: the boot JPEG leaves them as they were
    middle = pixels[:, quiet:-quiet]
    assert middle.max() > 200  # the name and the mark are lit
    lay = bootscreen.layout(model)
    left, top, right, bottom = model.hidden
    assert lay.mark[1] >= top and lay.line[2] < logo.size[1] - bottom  # out of the hidden strip
    frames = bootscreen.standby_frames(model, 8)
    assert len({frame.tobytes() for frame in frames}) == 8  # it moves ...
    first, other = (numpy.asarray(f, float) for f in frames[:2])
    moved = numpy.argwhere(numpy.abs(first - other).max(axis=2) > 3)
    assert moved[:, 0].min() >= lay.mark[1] - lay.mark[2]  # ... where the mark and the line are


def test_a_bar_on_end_gets_them_upright():
    logo = bootscreen.boot_logo(NINE, "portrait")
    assert logo.size == (480, 1920)
    pixels = numpy.asarray(logo, float)
    quiet = round(bootscreen.QUIET * 1920)
    ends = numpy.concatenate([pixels[:quiet], pixels[-quiet:]], axis=0)
    assert ends.max() < 24  # the short ends, now at the top and the bottom, stay dark
    lay = bootscreen.layout(NINE, "portrait")
    assert min(lay.mark[0], lay.name[0], lay.line[0]) >= NINE.hidden_edges("portrait")["left"]
    jpeg = Image.open(io.BytesIO(F.logo_jpeg(NINE, orientation="portrait")))
    turned = numpy.asarray(logo.rotate(180).convert("L"), float)  # as a bar on end's frames
    assert numpy.abs(numpy.asarray(jpeg.convert("L"), float) - turned).mean() < 3
    frames = bootscreen.standby_frames(NINE, 4, orientation="portrait")
    assert {frame.size for frame in frames} == {(480, 1920)}


LOOKS = list(bootscreen.designs())
SMALL = [get_model("turing-2.1-usb"), get_model("turing-4.6-usb")]  # round, a bar: quick


@pytest.mark.parametrize("model", SMALL, ids=lambda m: m.id)
@pytest.mark.parametrize("design", LOOKS)
def test_every_look_is_drawn_for_every_panel_and_its_clip_loops(design, model):
    look = bootscreen.design_of(design)
    assert look.frames % bootscreen.SEQUENCE == 0  # whole sequences for the panel's decoder
    logo = bootscreen.boot_logo(model, design=design)
    assert logo.size == model.size("landscape") and logo.mode == "RGB"
    pixels = numpy.asarray(logo, float)
    assert pixels.max() > 40  # something is lit ...
    w, h = logo.size
    if model.shape == "round":  # ... and the corners the glass leaves out are dark
        assert pixels[:4, :4].max() < 8 and pixels[-4:, -4:].max() < 8
    else:  # ... and the short ends fade out
        edge = round(bootscreen.QUIET * w) // 4
        assert pixels[:, :edge].max() < 24 and pixels[:, -edge:].max() < 24
    frames = bootscreen.standby_frames(model, 4, design=design)
    assert len({frame.tobytes() for frame in frames}) >= 3  # it moves
    if design != bootscreen.DEFAULT:  # (Libre Panel's own light runs out anew each loop)
        drawn, _where = bootscreen._look(model, "landscape", design)
        seam = numpy.abs(drawn.frame(0.0) - drawn.frame(1.0)).max()
        assert seam < 0.5  # the loop ends where it begins


@pytest.mark.parametrize("model", ["turing-4.6-usb", "turing-9.2-usb", "turing-12.3-usb"])
def test_a_bar_on_end_gets_every_look_upright(model):
    model = get_model(model)
    for design in LOOKS:
        logo = bootscreen.boot_logo(model, "portrait", design, scale=0.25)
        w, h = logo.size
        room = bootscreen.room(model, "portrait", 0.25)
        assert h > w and (w, h) == (room.w, room.h), design
        pixels = numpy.asarray(logo, float)
        assert pixels.max() > 40, design  # something is lit
        edge = round(bootscreen.QUIET * h) // 4  # the short ends, top and bottom, fade out
        assert pixels[:edge].max() < 24 and pixels[-edge:].max() < 24, design


def test_a_heartbeat_runs_across_on_a_bar_on_end_too():
    """Pulse on a bar on end: its traces run across, one under the other, as on a monitor,
    not one trace on its side; a round panel has one."""
    tall = bootscreen._look(get_model("turing-8.8-usb"), "portrait", "pulse")[0]
    lit = numpy.argwhere(tall.cover > 0.5)
    rows = numpy.unique(numpy.round(lit[:, 0] / 40))  # (bands of 40 px the traces are lit in)
    across = numpy.ptp(lit[:, 1])
    assert across > 0.9 * tall.where.w and len(rows) >= 8
    lone = bootscreen._look(get_model("turing-2.1-usb"), "landscape", "pulse")[0]
    middle = numpy.argwhere(lone.cover[:, lone.where.w // 2 - 40 : lone.where.w // 2 + 40] > 0.5)
    assert numpy.ptp(middle[:, 0]) < lone.where.h * 0.2  # one trace through the middle


def test_an_unknown_look_is_refused():
    with pytest.raises(DeviceError, match="no look"):
        F.design_of("sunset")
    assert F.design_of("") == F.design_of(None) == bootscreen.DEFAULT == LOOKS[0]


def test_a_dark_gradient_has_no_bands_and_black_stays_black():
    """A gradient of a few levels across many rows, made 8 bits: dithered, each row comes to
    its level on average (rounded, it would stand still for a band and jump); black is 0."""
    h, w = 240, 960
    where = bootscreen.Room(w, h, 0, 0, w, h)
    ideal = numpy.linspace(6.0, 10.0, h, dtype=numpy.float32)
    canvas = numpy.repeat(ideal[:, None, None], w, axis=1).repeat(3, axis=2)
    canvas[:, 400:420] = 0  # a black stripe
    picture = numpy.asarray(bootscreen.finish(canvas, where), float)
    rows = picture[:, 100:380, 1].mean(axis=1)  # (away from the short ends, which fade)
    assert numpy.abs(rows - ideal).max() < 0.3
    rounded = numpy.floor(ideal + 0.5)
    assert numpy.abs(rounded - ideal).max() > 0.45  # (what rounding alone would have done)
    assert picture[:, 400:420].max() == 0


def test_a_glow_is_blurred_in_floating_point():
    light = numpy.zeros((60, 60), numpy.float32)
    light[30, 30] = 1000.0
    out = bootscreen.blur(light, 4.0)
    assert abs(out.sum() - 1000.0) < 1.0  # no light lost
    profile = out[30, 30:50]
    assert numpy.all(numpy.diff(profile) <= 0) and profile[9] > 0  # a smooth tail, not steps
    assert abs(out[30, 34] / out[30, 30] - numpy.exp(-0.5)) < 0.08  # as a Gaussian of 4 px


def test_the_light_runs_out_from_the_middle():
    count = 31
    middle = [bootscreen._wave(15, count, p / 44) for p in range(44)]
    end = [bootscreen._wave(0, count, p / 44) for p in range(44)]
    assert middle.index(max(middle)) < end.index(max(end))  # the middle is lit first
    assert min(middle + end) >= 0.28  # the light never goes out


def test_the_logo_is_a_jpeg_in_the_panels_framebuffer():
    from PIL import JpegImagePlugin

    data = F.logo_jpeg(NINE)
    picture = Image.open(io.BytesIO(data))
    assert picture.format == "JPEG" and picture.size == (480, 1920)  # turned as the frames are
    assert JpegImagePlugin.get_sampling(picture) == 0  # every pixel its colour
    assert len(data) < F.MAX_FILE
    drawn = numpy.asarray(bootscreen.boot_logo(NINE).rotate(-90, expand=True), float)
    assert numpy.abs(numpy.asarray(picture.convert("RGB"), float) - drawn).mean() < 1.5


def test_the_clip_is_what_the_panels_decoder_plays():
    try:
        ffmpeg = find_ffmpeg()
    except DeviceError:
        pytest.skip("no ffmpeg")
    model = get_model("turing-2.8-usb")  # small: quick
    data = F.clip_h264(model, ffmpeg)
    assert slice_count(data) == bootscreen.CLIP_FRAMES == 44  # one slice a picture
    kinds = [data[start] & 0x1F for start, _end in nal_units(data)]
    assert kinds.count(7) == 4 and kinds.count(5) == 4  # a sequence every 11 pictures
    assert len(data) < F.MAX_FILE
    assert "sliced-threads=0" in " ".join(F.clip_command("ffmpeg", (480, 480)))
    data = F.clip_h264(model, ffmpeg, design="night")  # a longer loop: more sequences
    assert slice_count(data) == bootscreen.design_of("night").frames == 88
    assert [data[start] & 0x1F for start, _end in nal_units(data)].count(7) == 8


def _first_luma(ffmpeg, data):
    import subprocess

    raw = subprocess.run([ffmpeg, "-v", "error", "-i", "-", "-frames:v", "1", "-f", "rawvideo",
                          "-"], input=data, capture_output=True, check=True).stdout  # fmt: skip
    return numpy.frombuffer(raw, numpy.uint8)[: len(raw) * 2 // 3]  # (its values as they are)


def test_a_full_range_panel_gets_its_clip_in_full_range():
    """The 9.2" and the 12.3" show every stream as full range: their clip is made so, its black
    0, not the 16 of limited range they showed as grey."""
    try:
        ffmpeg = find_ffmpeg()
    except DeviceError:
        pytest.skip("no ffmpeg")
    command = " ".join(F.clip_command("ffmpeg", (480, 480), full=True))
    assert "out_range=full" in command and "video_full_range_flag=1" in command
    assert "out_range=full" not in " ".join(F.clip_command("ffmpeg", (480, 480)))
    model = get_model("turing-2.8-usb")
    frames = bootscreen.standby_frames(model, 11, design="dark")
    limited = _first_luma(ffmpeg, F.clip_h264(model, ffmpeg, frames))
    full = _first_luma(ffmpeg, F.clip_h264(model, ffmpeg, frames, full=True))
    assert limited.min() >= 14 and full.min() <= 2


def test_a_clip_is_made_as_finely_as_it_fits(monkeypatch):
    sizes = {17: 900_000, 20: 2_000_000, 22: 1_200_000, 24: 700_000}
    asked = []

    def run(command, **kwargs):
        crf = int(command[command.index("-crf") + 1])
        asked.append(crf)
        return SimpleNamespace(returncode=0, stdout=b"\x00" * sizes[crf], stderr=b"")

    monkeypatch.setattr(F.subprocess, "run", run)
    monkeypatch.setattr("libre_panel.devices.h264.slice_count", lambda data: 1)
    frame = [Image.new("RGB", (16, 16))]
    assert len(F.clip_h264(NINE, "ffmpeg", frame)) == 700_000 and asked == [20, 22, 24]
    sizes[20], asked[:] = 300_000, []
    assert len(F.clip_h264(NINE, "ffmpeg", frame)) == 900_000 and asked == [20, 17]  # finer


# -- on the panel ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["/usr/data/boot.jpg", "usr/data/../boot.jpg", "media/logo.jpg", "usr/data/", "boot.jpg",
     "usr/data/a b.jpg", "usr/data\\boot.jpg", "usr/data/bööt.jpg", ""],
)  # fmt: skip
def test_only_plain_files_below_usr_data_are_written(name):
    with pytest.raises(DeviceError):
        F.checked_name(name)
    assert F.checked_name("usr/data/boot.jpg") == b"usr/data/boot.jpg"


def test_a_file_is_at_most_a_mebibyte_and_has_the_size_it_should():
    panel = FakePanel()
    files = F.PanelFiles(panel)
    with pytest.raises(DeviceError, match="1 MiB"):
        files.write(F.BOOT_LOGO, bytes(F.MAX_FILE + 1))
    with pytest.raises(DeviceError):
        files.write(F.BOOT_LOGO, b"")
    files.write(F.BOOT_LOGO, b"x" * 1000)
    assert files.size(F.BOOT_LOGO) == 1000

    class Short(FakePanel):  # a panel that kept one byte less
        def command(self, cmd, args=b"", payload=b"", timeout_ms=2000):
            reply = super().command(cmd, args, payload, timeout_ms)
            if cmd == 40:
                self.files[self.name_of(args)] = payload[:-1]
            return reply

    with pytest.raises(DeviceError, match="999 bytes of it, not 1000"):
        F.PanelFiles(Short()).write(F.BOOT_LOGO, b"x" * 1000)
    for never in (11, 12, 13):
        with pytest.raises(DeviceError, match="never sent"):
            files._command(never)


def test_the_boot_logo_is_written_shown_and_taken_away_again():
    panel = FakePanel({F.STANDBY_CLIP: b"clip"})
    answer = jobs(panel).write_logo()
    jpeg = panel.files[F.BOOT_LOGO]
    assert answer == {"model": NINE.id, "boot_logo": len(jpeg), "design": "libre-panel"}
    kinds = [cmd for cmd, *_ in panel.sent]
    assert kinds == [99] * 20 + [40, 98, 113]  # listed (all pieces), written, checked, shown
    assert panel.sent[20] == (40, F.BOOT_LOGO, len(jpeg)) and panel.closed
    panel.sent.clear()
    assert jobs(panel).remove_logo() == {"model": NINE.id, "boot_logo": 0}
    assert panel.sent == [(42, F.BOOT_LOGO, 0), (98, F.BOOT_LOGO, 0)]
    assert F.STANDBY_CLIP in panel.files  # nothing else is touched


def test_the_standby_clip_is_written_played_and_saved_as_what_the_panel_starts_with():
    try:
        find_ffmpeg()
    except DeviceError:
        pytest.skip("no ffmpeg")
    panel = FakePanel()
    small = get_model("turing-2.8-usb")
    answer = jobs(panel, small).write_clip(60, small, design="dark")
    clip = panel.files[F.STANDBY_CLIP]
    assert answer == {"model": small.id, "standby_clip": len(clip), "name": F.STANDBY_CLIP,
                      "design": "dark"}  # fmt: skip
    after = [entry for entry in panel.sent if entry[0] != 99]
    assert after == [
        (40, F.STANDBY_CLIP, len(clip)), (98, F.STANDBY_CLIP, 0),
        (15, bytes([30]), 0),  # the rate of a cold start
        (110, F.STANDBY_CLIP, 0),  # played, its name (never an empty one) in the settings
        (125, bytes([61, 2, 0, 0, 0, 0]), 0),  # saved: brightness, start with the clip
    ]  # fmt: skip


@pytest.mark.parametrize(
    "model, video_range, full",
    [("turing-12.3-usb", "auto", True), ("turing-12.3-usb", "limited", False),
     ("turing-8.8-usb", "auto", False), ("turing-8.8-usb", "full", True)],
)  # fmt: skip
def test_the_clip_is_made_in_the_range_the_panel_shows(monkeypatch, model, video_range, full):
    made = []
    monkeypatch.setattr(F, "clip_h264", lambda found, ffmpeg, **how: made.append(how) or b"c")
    found = get_model(model)
    screen = F.StartScreen(opener=lambda asked: (FakePanel(), found), ffmpeg=lambda: "ffmpeg")
    screen.write_clip(50, found, design="aurora", video_range=video_range)  # (no ffmpeg needed)
    assert made == [{"orientation": "landscape", "design": "aurora", "full": full}]


def test_a_listing_without_a_header_is_read_as_the_9_2_sends_it():
    """The 9.2" sends each piece of the listing as it is, beginning with zeros: no command
    number in front (\"command 99: unexpected reply 00 00 ...\" stopped every write)."""
    panel = FakePanel({F.BOOT_LOGO: b"j", F.STANDBY_CLIP: b"c"}, listing="bare")
    assert F.PanelFiles(panel).listing() == ["boot.jpg", "standby.h264"]
    silent = FakePanel({}, listing="none")
    assert F.PanelFiles(silent).listing() == []  # nothing in it: no names, and no error
    answer = jobs(silent).write_logo()  # the boot logo is written all the same
    assert answer["boot_logo"] == len(silent.files[F.BOOT_LOGO])


def test_the_listing_gives_the_names_the_panel_has():
    assert F.names_in(b"boot.jpg\0standby.h264\0\0img\0app.cfg\0boot.jpg\0") == [
        "boot.jpg", "standby.h264", "app.cfg"
    ]  # fmt: skip
    panel = FakePanel({F.BOOT_LOGO: b"j", F.STANDBY_CLIP: b"c"})
    state = jobs(panel).state()
    assert state == {"model": NINE.id, "files": ["boot.jpg", "standby.h264"], "boot_logo": 1,
                     "standby_clip": 1}  # fmt: skip


# -- in the app --------------------------------------------------------------------------------


def test_the_app_shows_the_pictures_for_each_usb_panel(server, isolated_home):  # noqa: F811
    status, data = request(server, "GET", "/api/start-screen")
    assert status == 200 and [m["id"] for m in data["models"]] == [m.id for m in F.models()]
    assert [m["id"] for m in data["models"] if m["measured"]] == ["turing-9.2-usb"]
    assert data["names"] == {"logo": F.BOOT_LOGO, "clip": F.STANDBY_CLIP}
    assert data["designs"] == LOOKS and LOOKS[0] == "libre-panel" and len(LOOKS) >= 7
    status, png = request(
        server, "GET", "/api/start-screen/picture?model=turing-4.6-usb&what=logo&w=480&design=night"
    )
    assert status == 200 and Image.open(io.BytesIO(png)).size == (480, 160)
    path = "/api/start-screen/picture?model=turing-4.6-usb&what=logo&w=480&design=sunset"
    assert request(server, "GET", path)[0] == 404
    status, png = request(
        server, "GET", "/api/start-screen/picture?model=turing-4.6-usb&what=logo&w=480"
    )
    assert status == 200 and Image.open(io.BytesIO(png)).size == (480, 160)
    status, png = request(
        server, "GET", "/api/start-screen/picture?model=turing-2.8-usb&what=clip&w=240"
    )
    picture = Image.open(io.BytesIO(png))
    assert status == 200 and getattr(picture, "n_frames", 1) == 44
    status, jpeg = request(server, "GET", "/api/start-screen/file?model=turing-9.2-usb&what=logo")
    assert status == 200 and Image.open(io.BytesIO(jpeg)).size == (480, 1920)
    assert request(server, "GET", "/api/start-screen/picture?model=turing-3.5&what=logo")[0] == 404
    from libre_panel.config import set_config_value

    set_config_value("device.orientation", "portrait")  # a bar on end: they stand upright
    status, data = request(server, "GET", "/api/start-screen")
    assert data["orientation"] == "portrait"
    assert [m["size"] for m in data["models"] if m["id"] == "turing-9.2-usb"] == [[480, 1920]]
    status, png = request(
        server, "GET", "/api/start-screen/picture?model=turing-9.2-usb&what=logo&w=240&o=portrait"
    )
    assert status == 200 and Image.open(io.BytesIO(png)).size == (240, 960)


def test_the_app_writes_only_when_asked_and_pauses_the_panel_for_it(isolated_home, monkeypatch):
    import threading

    from test_editor_server import token_of  # noqa: F401 (the token of the server)

    from libre_panel.editor.server import make_server

    class Controls:
        def __init__(self):
            self.acts = []

        def act(self, action, value=None):
            self.acts.append(action)
            return {}

        def snapshot(self):
            return {"panel": {}}

    controls = Controls()
    srv = make_server(port=0, controls=controls)
    panel = FakePanel()
    srv.editor_state.start_screen_jobs = jobs(panel)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        status, data = request(port, "POST", "/api/start-screen", {"action": "logo"})
        assert status == 200 and data["paused"] is True and data["boot_logo"] > 0
        assert controls.acts == ["pause"] and F.BOOT_LOGO in panel.files
        status, data = request(port, "POST", "/api/start-screen",
                               {"action": "logo", "design": "circuit"})  # fmt: skip
        assert status == 200 and data["design"] == "circuit"
        status, data = request(port, "POST", "/api/start-screen",
                               {"action": "logo", "design": "sunset"})  # fmt: skip
        assert status == 400 and "no look" in data["error"]
        status, data = request(port, "POST", "/api/start-screen", {"action": "flash"})
        assert status == 400
        status, data = request(port, "POST", "/api/start-screen",
                               {"action": "logo", "model": "turing-3.5"})  # fmt: skip
        assert status == 400  # no TURZX USB panel

        def broken(asked):
            raise DeviceError("the panel is busy")

        srv.editor_state.start_screen_jobs = F.StartScreen(opener=broken)
        status, data = request(port, "POST", "/api/start-screen", {"action": "state"})
        assert status == 409 and data["error"] == "the panel is busy" and data["paused"] is True
    finally:
        srv.shutdown()
        srv.server_close()
        srv.editor_state.close()
