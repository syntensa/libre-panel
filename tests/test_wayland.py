"""The screen on Wayland (wayland.py, wayland_feed.py), against stand-ins of GNOME's screen cast
and the screen-sharing portal on a session bus of the test's own: Mutter's session with a
monitor or a virtual one and its stream's node; the portal's requests answered by their
Response signals, the dialog's choice kept, the PipeWire connection handed over as a file
descriptor; GNOME's monitors; GStreamer's pipeline; and the program as the app starts it, its
frames on its output, the session stopped as it ends."""

import itertools
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

jeepney = pytest.importorskip("jeepney")

from jeepney import (  # noqa: E402
    DBusAddress,
    HeaderFields,
    MessageType,
    new_error,
    new_method_return,
    new_signal,
)
from jeepney.bus_messages import message_bus  # noqa: E402
from jeepney.io.blocking import open_dbus_connection  # noqa: E402

from libre_panel import capture, mirror, wayland, wayland_feed  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("dbus-daemon"), reason="a session bus of its own"
)
SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture
def bus(monkeypatch):
    daemon = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address"],
                              stdout=subprocess.PIPE, text=True)  # fmt: skip
    address = daemon.stdout.readline().strip()
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", address)
    yield address
    daemon.kill()
    daemon.wait()


class Service(threading.Thread):
    """A stand-in on the bus: ``name``, answering method calls by (interface, member)."""

    def __init__(self, name):
        super().__init__(daemon=True)
        self.connection = open_dbus_connection(bus="SESSION", enable_fds=True)
        self.connection.send_and_get_reply(message_bus.RequestName(name))
        self.name, self.calls, self.stop = name, [], threading.Event()
        self.start()

    def answer(self, message):  # -> (signature, body, signals to send after the reply)
        raise NotImplementedError

    def run(self):
        while not self.stop.is_set():
            try:
                message = self.connection.receive(timeout=0.1)
            except TimeoutError:
                continue
            header = message.header
            if header.message_type != MessageType.method_call:
                continue
            member = header.fields[HeaderFields.member]
            self.calls.append((member, message.body))
            try:
                signature, body, signals = self.answer(message)
            except LookupError:
                self.connection.send(new_error(message, "org.freedesktop.DBus.Error.UnknownMethod"))
                continue
            self.connection.send(new_method_return(message, signature, body))
            for signal in signals:
                self.connection.send(signal)

    def close(self):
        self.stop.set()
        self.join(2)
        self.connection.close()


class Mutter(Service):
    SESSION = "/org/gnome/Mutter/ScreenCast/Session/u1"
    STREAM = "/org/gnome/Mutter/ScreenCast/Stream/u1"

    def __init__(self):
        super().__init__("org.gnome.Mutter.ScreenCast")

    def answer(self, message):
        member = message.header.fields[HeaderFields.member]
        if member == "CreateSession":
            return "o", (self.SESSION,), []
        if member in ("RecordVirtual", "RecordMonitor"):
            return "o", (self.STREAM,), []
        if member == "Start":
            stream = DBusAddress(self.STREAM, interface="org.gnome.Mutter.ScreenCast.Stream")
            return "", (), [new_signal(stream, "PipeWireStreamAdded", "u", (42,))]
        if member == "Stop":
            session = DBusAddress(self.SESSION, interface="org.gnome.Mutter.ScreenCast.Session")
            return "", (), [new_signal(session, "Closed")]
        raise LookupError(member)


class Portal(Service):
    def __init__(self, answer_code=0):
        super().__init__("org.freedesktop.portal.Desktop")
        self.code = answer_code
        self.read, self.write = os.pipe()
        self.sessions = itertools.count(1)

    def respond(self, message, options, results):
        sender = message.header.fields[HeaderFields.sender].lstrip(":").replace(".", "_")
        token = options["handle_token"][1]
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        request = DBusAddress(path, interface="org.freedesktop.portal.Request")
        return "o", (path,), [new_signal(request, "Response", "ua{sv}", (self.code, results))]

    def answer(self, message):
        member = message.header.fields[HeaderFields.member]
        body = message.body
        if member == "CreateSession":
            session = f"/org/freedesktop/portal/desktop/session/1_1/s{next(self.sessions)}"
            return self.respond(message, body[0], {"session_handle": ("s", session)})
        if member == "SelectSources":
            return self.respond(message, body[1], {})
        if member == "Start":
            stream = (77, {"size": ("(ii)", (1920, 1080)), "source_type": ("u", 4)})
            return self.respond(message, body[2], {"streams": ("a(ua{sv})", [stream]),
                                                   "restore_token": ("s", "kept-1")})  # fmt: skip
        if member == "OpenPipeWireRemote":
            return "h", (self.read,), []
        if member == "Close":
            return "", (), []
        raise LookupError(member)


DISPLAY_STATE = (
    7,
    [(("DP-1", "DEL", "U2720Q", "1"),
      [("3840x2160@60", 3840, 2160, 60.0, 1.0, [1.0, 2.0], {"is-current": ("b", True)}),
       ("1920x1080@60", 1920, 1080, 60.0, 1.0, [1.0], {})],
      {"display-name": ("s", "Dell 27\"")}),
     (("HDMI-1", "ACR", "X", "2"),
      [("1920x1080@60", 1920, 1080, 60.0, 1.0, [1.0], {"is-current": ("b", True)})],
      {})],
    [(0, 0, 1.0, 0, True, [("DP-1", "DEL", "U2720Q", "1")], {}),
     (3840, 0, 1.0, 0, False, [("HDMI-1", "ACR", "X", "2")], {})],
    {},
)  # fmt: skip


class DisplayConfig(Service):
    def __init__(self):
        super().__init__("org.gnome.Mutter.DisplayConfig")

    def answer(self, message):
        signature = "ua((ssss)a(siiddada{sv})a{sv})a(iiduba(ssss)a{sv})a{sv}"
        return signature, DISPLAY_STATE, []


def test_gnome_makes_the_panels_own_monitor_and_says_its_stream(bus):
    mutter = Mutter()
    connection = open_dbus_connection(bus="SESSION", enable_fds=True)
    try:
        assert wayland.has_name(connection, wayland.MUTTER_CAST)
        node, fd, size, session = wayland_feed.mutter_stream(connection, "virtual", "", True)
        assert (node, fd, size) == (42, None, None)
        assert [call[0] for call in mutter.calls] == ["CreateSession", "RecordVirtual", "Start"]
        assert mutter.calls[1][1] == ({"cursor-mode": ("u", 1)},)  # (the pointer drawn in)
        wayland_feed.mutter_stream(connection, "monitor", "DP-1", False)
        assert mutter.calls[-2] == ("RecordMonitor", ("DP-1", {"cursor-mode": ("u", 0)}))
    finally:
        connection.close()
        mutter.close()


def test_the_portal_asks_once_and_hands_over_the_pipewire_connection(bus, isolated_home):
    portal = Portal()
    connection = open_dbus_connection(bus="SESSION", enable_fds=True)
    try:
        node, fd, size, session = wayland_feed.portal_stream(connection, "virtual", True)
        assert (node, size) == (77, (1920, 1080))
        os.write(portal.write, b"pw")
        assert os.read(fd, 2) == b"pw"  # the portal's descriptor, here
        os.close(fd)
        chosen = next(body for member, body in portal.calls if member == "SelectSources")[1]
        assert chosen["types"] == ("u", 4) and chosen["persist_mode"] == ("u", 2)
        assert chosen["cursor_mode"] == ("u", 2) and "restore_token" not in chosen
        assert session.object_path.startswith("/org/freedesktop/portal/desktop/session/")
        # the next time the kept choice answers for the person
        _, fd, _, _ = wayland_feed.portal_stream(connection, "virtual", False)
        os.close(fd)
        again = [body for member, body in portal.calls if member == "SelectSources"][-1][1]
        assert again["restore_token"] == ("s", "kept-1") and again["cursor_mode"] == ("u", 1)
    finally:
        connection.close()
        portal.close()


def test_a_cancelled_dialog_gives_no_screen(bus, isolated_home):
    portal = Portal(answer_code=1)
    connection = open_dbus_connection(bus="SESSION", enable_fds=True)
    try:
        with pytest.raises(wayland_feed.Refused, match="cancelled"):
            wayland_feed.portal_stream(connection, "monitor", True)
    finally:
        connection.close()
        portal.close()


def test_gnomes_monitors(bus):
    display = DisplayConfig()
    try:
        found = wayland.monitors()
    finally:
        display.close()
    assert [(m.id, m.name, m.x, m.width, m.height, m.primary) for m in found] == [
        ("DP-1", 'Dell 27"', 0, 3840, 2160, True),
        ("HDMI-1", "HDMI-1", 3840, 1920, 1080, False),
    ]
    assert wayland.monitors() == []  # (no GNOME: the dialog lists them)


def test_gstreamers_pipeline_fits_the_stream():
    assert wayland_feed.cover_crop((1920, 1080), (1920, 720)) == (0, 0, 180, 180)
    assert wayland_feed.cover_crop((1280, 720), (480, 480)) == (280, 280, 0, 0)
    command = wayland_feed.gst_command("gst", 77, 5, (1920, 1080), (1920, 720), 50, "cover", False)
    pipeline = " ".join(command)
    assert pipeline.startswith("gst -q pipewiresrc path=77 ") and " fd=5 " in pipeline
    assert "videocrop left=0 right=0 top=180 bottom=180 ! videoscale add-borders=false" in pipeline
    assert "video/x-raw,format=BGRx,width=1920,height=720,pixel-aspect-ratio=1/1" in pipeline
    assert pipeline.endswith("video/x-raw,framerate=50/1 ! fdsink fd=1 sync=false")
    own = " ".join(wayland_feed.gst_command("gst", 42, None, None, (1920, 720), 30, "contain",
                                            True))  # fmt: skip
    assert "pipewiresrc path=42 do-timestamp=true keepalive-time=1000 always-copy=true ! " in own
    assert "! video/x-raw,width=1920,height=720 ! videoconvert" in own  # (Mutter's size asked)
    assert "fd=" not in own.split("fdsink")[0] and "add-borders=true" in own


def test_the_app_reads_the_programs_raw_frames(monkeypatch):
    monkeypatch.setattr(wayland, "jeepney_found", lambda: True)
    monkeypatch.setattr(wayland, "gstreamer", lambda: "/usr/bin/gst-launch-1.0")
    found = wayland.capture_of(mirror.Source("virtual", fit="cover", cursor=False), 50,
                               (1920, 720))  # fmt: skip
    assert found.inputs == ("-f", "rawvideo", "-pix_fmt", "bgr0", "-video_size", "1920x720",
                            "-framerate", "50", "-i", "pipe:0")  # fmt: skip
    assert found.feeder[-12:] == ("wayland-feed", "--kind", "virtual", "--id", "ask", "--size",
                                  "1920x720", "--fps", "50", "--fit", "cover",
                                  "--no-cursor")  # fmt: skip
    monkeypatch.setattr(wayland, "gstreamer", lambda: None)
    with pytest.raises(mirror.MirrorError, match="GStreamer"):
        wayland.capture_of(mirror.Source("virtual"), 50, (1920, 720))
    monkeypatch.setattr(capture, "platform", lambda: "wayland")
    monkeypatch.setattr(mirror, "capture_of", mirror.capture_of)  # (the real one: Wayland's)
    with pytest.raises(mirror.MirrorError, match="GStreamer"):
        mirror.capture_of("ffmpeg", mirror.Source("monitor", "ask"), 50, (1920, 720))


def test_the_program_writes_the_frames_and_stops_the_session_as_they_end(bus, tmp_path):
    """As the app starts it: GNOME's own monitor; GStreamer (a stand-in) writes the frames to
    the program's output; as it ends, the session is stopped."""
    mutter = Mutter()
    fake = tmp_path / "bin" / "gst-launch-1.0"
    fake.parent.mkdir()
    said = tmp_path / "pipeline.txt"
    fake.write_text(f'#!/bin/sh\necho "$@" > {said}\nprintf "BGRxBGRx"\n', encoding="utf-8")
    fake.chmod(0o755)
    environment = {**os.environ, "PATH": f"{fake.parent}{os.pathsep}{os.environ['PATH']}",
                   "PYTHONPATH": str(SRC)}  # fmt: skip
    try:
        done = subprocess.run([sys.executable, "-m", "libre_panel", "wayland-feed", "--kind",
                               "virtual", "--size", "1920x720", "--fps", "30"],
                              capture_output=True, env=environment, timeout=30)  # fmt: skip
    finally:
        mutter.close()
    assert done.returncode == 0, done.stderr
    assert done.stdout == b"BGRxBGRx"  # its output is the frames alone
    assert b"stream 42" in done.stderr
    assert "pipewiresrc path=42" in said.read_text(encoding="utf-8")
    assert [call[0] for call in mutter.calls] == ["CreateSession", "RecordVirtual", "Start",
                                                 "Stop"]  # fmt: skip
