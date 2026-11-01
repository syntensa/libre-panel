"""The pipe to ffmpeg has room for half a frame (devices/h264.py PIPE_BYTES): on Linux it is
widened, on Windows made so; what goes in comes out, and its end is seen."""

import subprocess
import sys

import pytest

from libre_panel.devices import h264

COUNT = [sys.executable, "-c", "import sys; print(len(sys.stdin.buffer.read()))"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux's pipes")
def test_linux_widens_ffmpegs_pipe():
    import fcntl

    encoder = h264.Encoder(COUNT, (16, 16), lambda picture: None)
    try:
        assert fcntl.fcntl(encoder.proc.stdin.fileno(), 1032) >= h264.PIPE_BYTES  # F_GETPIPE_SZ
    finally:
        encoder.proc.kill()
        encoder.proc.wait()


@pytest.mark.skipif(sys.platform == "win32", reason="elsewhere than Windows")
def test_elsewhere_popen_makes_the_pipe():
    assert h264._wide_pipe() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows's pipes")
def test_windows_makes_a_wide_pipe_whose_end_is_seen():
    import os

    theirs, ours = h264._wide_pipe()
    child = subprocess.Popen(COUNT, stdin=theirs, stdout=subprocess.PIPE)
    os.close(theirs)
    frame = memoryview(bytes(range(256)) * 8100)  # (two megabytes, as a 12.3" frame in YUV)
    for _ in range(3):
        view = frame
        while view:
            view = view[ours.write(view) :]
    ours.close()  # ffmpeg sees the end of its input: not inherited, no other end stays open
    out, _ = child.communicate(timeout=30)
    assert int(out) == 3 * len(frame)


def test_the_encoder_falls_back_to_popens_pipe(monkeypatch):
    monkeypatch.setattr(h264, "_wide_pipe", lambda: None)
    encoder = h264.Encoder(COUNT, (16, 16), lambda picture: None)
    try:
        assert encoder.proc.stdin is not None
        encoder.write(bytes(1000))
    finally:
        encoder.proc.kill()
        encoder.proc.wait()
