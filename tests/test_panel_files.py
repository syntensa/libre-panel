"""The panel's own screens on a TURZX USB panel: the boot logo and the standby clip in Libre
Panel's look for every USB panel's size, and what is sent to put them on it (a fake panel
keeps the files and every command)."""

import io
import struct

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


def test_the_light_runs_out_from_the_middle():
    count = 31
    middle = [bootscreen._wave(15, count, p / 44) for p in range(44)]
    end = [bootscreen._wave(0, count, p / 44) for p in range(44)]
    assert middle.index(max(middle)) < end.index(max(end))  # the middle is lit first
    assert min(middle + end) >= 0.28  # the light never goes out


def test_the_logo_is_a_jpeg_in_the_panels_framebuffer():
    data = F.logo_jpeg(NINE)
    picture = Image.open(io.BytesIO(data))
    assert picture.format == "JPEG" and picture.size == (480, 1920)  # turned as the frames are
    assert len(data) < F.MAX_FILE


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
    assert answer == {"model": NINE.id, "boot_logo": len(jpeg)}
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
    answer = jobs(panel, small).write_clip(60, small)
    clip = panel.files[F.STANDBY_CLIP]
    assert answer == {"model": small.id, "standby_clip": len(clip), "name": F.STANDBY_CLIP}
    after = [entry for entry in panel.sent if entry[0] != 99]
    assert after == [
        (40, F.STANDBY_CLIP, len(clip)), (98, F.STANDBY_CLIP, 0),
        (15, bytes([30]), 0),  # the rate of a cold start
        (110, F.STANDBY_CLIP, 0),  # played, its name (never an empty one) in the settings
        (125, bytes([61, 2, 0, 0, 0, 0]), 0),  # saved: brightness, start with the clip
    ]  # fmt: skip


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
