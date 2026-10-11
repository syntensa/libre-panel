"""packaging/check_tools.py, which CI runs on the program folder, on what the setup installed and
on the AppImage's content: it must pass a program that does the work, and say what is wrong with
one that does not. The programs are stand-ins here; one test runs the ffmpeg of this system."""

import hashlib
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from libre_panel import bundled
from libre_panel.devices.h264 import EncoderSettings, ffmpeg_command

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_tools", ROOT / "packaging" / "check_tools.py")
check_tools = importlib.util.module_from_spec(spec)
sys.modules["check_tools"] = check_tools
spec.loader.exec_module(check_tools)

START = b"\x00\x00\x00\x01"
SPS, PPS, IDR, SLICE = (
    START + b"\x67\x42\x00",
    START + b"\x68\xce\x3c",
    START + b"\x65\x88\x84",
    START + b"\x41\x9a\x24",
)
ENCODERS = (
    " V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC\n V....D libx264rgb  RGB\n"
)


def stream(pictures=check_tools.FRAMES):
    """What ffmpeg writes: parameter sets and a keyframe, then one slice per picture."""
    return SPS + PPS + IDR + SLICE * (pictures - 1)


def stand_in(encoders=ENCODERS, output=None, code=0, version="ffmpeg version 8.1.3"):
    """A ``run`` that answers like ffmpeg does to the three questions the check asks."""

    def run(command, **options):
        if "-encoders" in command:
            out = encoders
        elif "-version" in command:
            out = f"{version} Copyright (c) the FFmpeg developers\nbuilt with gcc\n"
        else:
            assert options["input"], "frames should be fed in"
            return subprocess.CompletedProcess(
                command, code, stream() if output is None else output, b"error text"
            )
        return subprocess.CompletedProcess(command, 0, out, "")

    return run


def program_folder(tmp_path, files, executable="libre-panel"):
    """tmp_path/program with the program of Libre Panel and its tools folder holding the
    files, and a SOURCES.txt that lists them."""
    tools = tmp_path / "program" / "tools"
    tools.mkdir(parents=True)
    (tmp_path / "program" / executable).write_bytes(b"program")
    for name, content in files.items():
        (tools / name).write_bytes(content)
    sums = "\n".join(f"{hashlib.sha256(c).hexdigest()}  {n}" for n, c in files.items())
    (tools / "SOURCES.txt").write_text(
        f"Programs.\n\nSHA-256 of the files in this folder:\n{sums}\n"
    )
    return tmp_path / "program"


def test_the_folder_name_is_the_one_the_app_looks_in():
    assert check_tools.TOOLS_FOLDER == bundled.TOOLS_FOLDER


def test_a_good_ffmpeg_passes(tmp_path, capsys):
    program = program_folder(tmp_path, {"ffmpeg": b"x"})
    check_tools.check(program, stand_in())
    out = capsys.readouterr().out
    assert "ffmpeg is the file that was fetched" in out
    assert "ffmpeg version 8.1.3 Copyright (c) the FFmpeg developers lists libx264" in out
    assert "encodes 8 frames" in out


def test_the_encode_is_the_one_the_video_mode_asks_for(tmp_path):
    asked = []

    def run(command, **options):
        asked.append(command)
        return stand_in()(command, **options)

    check_tools.check_ffmpeg(tmp_path / "ffmpeg", run)
    encode = asked[-1]
    expected = ffmpeg_command(
        str(tmp_path / "ffmpeg"), check_tools.SIZE, 270, EncoderSettings(fps=25, keyframe_s=1)
    )
    assert encode == expected and "libx264" in encode and "transpose=1" in encode


@pytest.mark.parametrize(
    "encoders",
    ["", " V....D libx264rgb  RGB\n", " A....D aac AAC\n"],
    ids=["none", "rgb only", "audio"],
)
def test_an_ffmpeg_without_libx264_fails(tmp_path, encoders):
    with pytest.raises(check_tools.CheckError, match="does not list libx264"):
        check_tools.check_ffmpeg(tmp_path / "ffmpeg", stand_in(encoders=encoders))


@pytest.mark.parametrize(
    "output, code, wanted",
    [
        (b"", 1, "did not encode: exit code 1 error text"),  # ffmpeg failed
        (b"", 0, "did not encode"),  # and one that says nothing
        (stream(3), 0, "encoded 3 pictures of 8 frames"),  # some frames missing
        (SPS + PPS + IDR * 8, 0, "8 of them keyframes"),  # the keyframe interval is not kept
        (IDR + SLICE * 7, 0, "NAL units"),  # no parameter sets
        (START + b"\x65\x08\x00" + START + b"\x61\x08\x00" + stream(), 0, "several slices"),
    ],
)
def test_an_ffmpeg_that_does_not_encode_the_stream_fails(tmp_path, output, code, wanted):
    with pytest.raises(check_tools.CheckError, match=wanted):
        check_tools.check_ffmpeg(tmp_path / "ffmpeg", stand_in(output=output, code=code))


@pytest.mark.parametrize(
    "shown, wanted",
    [("PresentMon 2.6.0\nPresentMon [options]\n", "PresentMon 2.6.0 starts"), ("usage", None)],
)
def test_presentmon_has_to_start_and_say_its_version(tmp_path, capsys, shown, wanted):
    def run(command, **options):
        assert command[1] == "--help"
        return subprocess.CompletedProcess(command, 1, "", shown)  # exit code 1 is how it ends

    if wanted:
        check_tools.check_presentmon(tmp_path / "PresentMon.exe", run)
        assert wanted in capsys.readouterr().out
    else:
        with pytest.raises(check_tools.CheckError, match="said no version"):
            check_tools.check_presentmon(tmp_path / "PresentMon.exe", run)


def test_a_windows_folder_checks_each_program_it_lists(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")  # the names the app looks for there
    program = program_folder(
        tmp_path,
        {"ffmpeg.exe": b"MZ1", "PresentMon.exe": b"MZ2", "PawnIO_setup.exe": b"MZ3"},
        executable="LibrePanel.exe",
    )
    seen = []

    def run(command, **options):
        seen.append(Path(command[0]).name)
        if Path(command[0]).name == "PresentMon.exe":
            return subprocess.CompletedProcess(command, 1, "", "PresentMon 2.6.0")
        return stand_in()(command, **options)

    check_tools.check(program, run)
    assert set(seen) == {"ffmpeg.exe", "PresentMon.exe"}  # the installer is there, and not run
    out = capsys.readouterr().out
    assert "PawnIO_setup.exe is the file that was fetched" in out
    assert "started from LibrePanel.exe, the app finds ffmpeg.exe in tools" in out
    assert "the app finds PresentMon.exe in tools" in out


def test_the_app_has_to_find_the_programs_in_the_folder_and_not_somewhere_else(
    tmp_path, monkeypatch
):
    """A build whose tools folder is not where the app looks is a failure, even where a
    system ffmpeg would make the video mode work."""
    program = program_folder(tmp_path, {"ffmpeg": b"x"})
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")
    check_tools.check_discovery(program, program / "tools", ["ffmpeg"])  # in place: found first
    (program / "tools").rename(program / "kit")  # not under the name the app looks for
    with pytest.raises(check_tools.CheckError, match="finds /usr/bin/ffmpeg and not ffmpeg"):
        check_tools.check_discovery(program, program / "kit", ["ffmpeg"])
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(check_tools.CheckError, match="the app finds no ffmpeg"):
        check_tools.check_discovery(program, program / "kit", ["ffmpeg"])
    (program / "libre-panel").unlink()  # and without a program of Libre Panel there is no asking
    with pytest.raises(check_tools.CheckError, match="has no program of Libre Panel"):
        check_tools.check_discovery(program, program / "kit", ["ffmpeg"])


def test_a_file_that_is_not_the_fetched_one_or_is_gone_fails(tmp_path):
    program = program_folder(tmp_path, {"ffmpeg": b"x", "PresentMon.exe": b"MZ"})
    (program / "tools" / "ffmpeg").write_bytes(b"replaced")
    with pytest.raises(check_tools.CheckError, match="is not the file that was fetched"):
        check_tools.check(program, stand_in())
    (program / "tools" / "ffmpeg").unlink()
    with pytest.raises(check_tools.CheckError, match="cannot be read"):
        check_tools.check(program, stand_in())


def test_a_folder_without_tools_or_sources_or_ffmpeg_fails(tmp_path):
    with pytest.raises(check_tools.CheckError, match="has no tools folder"):
        check_tools.check(tmp_path, stand_in())
    (tmp_path / "tools").mkdir()
    with pytest.raises(check_tools.CheckError, match="SOURCES.txt cannot be read"):
        check_tools.check(tmp_path, stand_in())
    (tmp_path / "tools" / "SOURCES.txt").write_text("nothing listed\n")
    with pytest.raises(check_tools.CheckError, match="lists no file"):
        check_tools.check(tmp_path, stand_in())
    program = program_folder(tmp_path / "other", {"PresentMon.exe": b"MZ"})
    with pytest.raises(check_tools.CheckError, match="has no ffmpeg"):
        check_tools.check(program, stand_in())


def test_the_command_says_ok_or_what_failed(tmp_path, capsys):
    assert check_tools.main([str(tmp_path)]) == 1
    assert "FAIL: " in capsys.readouterr().err
    program = program_folder(tmp_path / "good", {"ffmpeg": b"x"})
    assert check_tools.main([str(program)], stand_in()) == 0
    assert capsys.readouterr().out.endswith("OK\n")
    assert check_tools.main([str(program)], stand_in(encoders="")) == 1
    assert "does not list libx264" in capsys.readouterr().err


def test_a_program_that_cannot_be_started_is_a_failure(tmp_path):
    with pytest.raises(check_tools.CheckError, match="did not run"):
        check_tools.run([str(tmp_path / "missing-program")])


FFMPEG = shutil.which("ffmpeg")


@pytest.mark.skipif(FFMPEG is None, reason="needs ffmpeg with libx264")
def test_the_ffmpeg_of_this_system_passes_when_it_has_libx264(capsys):
    encoders = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True)
    if "libx264 " not in encoders.stdout:
        pytest.skip("this ffmpeg has no libx264")
    check_tools.check_ffmpeg(Path(FFMPEG))
    assert "encodes 8 frames" in capsys.readouterr().out
