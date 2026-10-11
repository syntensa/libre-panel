"""The programs a download carries (ffmpeg, PresentMon, PawnIO's installer): found first, beaten
by the settings, and never looked for in the user's Downloads. No download is needed here; a
program folder is made, and the program is told it is a release build."""

import hashlib
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from libre_panel import bundled
from libre_panel.devices.base import DeviceError
from libre_panel.devices.h264 import find_ffmpeg
from libre_panel.sensors import presentmon

PLATFORMS = ["linux", "win32"]


def exe(platform):
    return ".exe" if platform == "win32" else ""


def release_build(tmp_path, monkeypatch, platform="linux", *files, folder="tools"):
    """A frozen build whose program folder holds ``files`` in ``folder`` (the tools folder)."""
    program = tmp_path / "program"
    (program / folder).mkdir(parents=True, exist_ok=True)
    for name in files:
        (program / folder / name).write_bytes(b"x")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(program / f"libre-panel{exe(platform)}"))
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    return program


@pytest.fixture(autouse=True)
def nothing_on_the_path(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_the_programs_of_a_download_are_found_in_its_tools_folder(tmp_path, monkeypatch, platform):
    program = release_build(
        tmp_path, monkeypatch, platform, f"ffmpeg{exe(platform)}", f"PresentMon{exe(platform)}"
    )
    assert find_ffmpeg() == str(program / "tools" / f"ffmpeg{exe(platform)}")
    assert presentmon.find_presentmon() == str(program / "tools" / f"PresentMon{exe(platform)}")
    assert bundled.find("PawnIO_setup.exe") is None  # what it does not carry is not there


def test_the_programs_of_a_download_come_before_the_ones_on_the_path(tmp_path, monkeypatch):
    program = release_build(tmp_path, monkeypatch, "linux", "ffmpeg", "PresentMon")
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    assert find_ffmpeg() == str(program / "tools" / "ffmpeg")  # the build that was tested
    assert presentmon.find_presentmon() == str(program / "tools" / "PresentMon")
    # nothing in the folder: the system's own
    (program / "tools" / "ffmpeg").unlink()
    assert find_ffmpeg() == "/usr/bin/ffmpeg"


def test_a_program_named_in_the_settings_beats_the_download(tmp_path, monkeypatch):
    release_build(tmp_path, monkeypatch, "linux", "ffmpeg", "PresentMon")
    mine = tmp_path / "mine"
    mine.write_bytes(b"x")
    assert find_ffmpeg(str(mine)) == str(mine)
    assert presentmon.find_presentmon(str(mine)) == str(mine)
    # named, but not there: an error, not a quiet fall back to another program
    with pytest.raises(DeviceError, match="ffmpeg not found at"):
        find_ffmpeg(str(tmp_path / "missing"))
    assert presentmon.find_presentmon(str(tmp_path / "missing")) is None


def test_the_folder_of_pyinstaller_is_looked_in_too(tmp_path, monkeypatch):
    release_build(tmp_path, monkeypatch, "linux")
    internal = tmp_path / "program" / "_internal"
    (internal / "tools").mkdir(parents=True)
    (internal / "tools" / "ffmpeg").write_bytes(b"x")
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    assert find_ffmpeg() == str(internal / "tools" / "ffmpeg")


def test_a_pip_install_and_a_checkout_carry_no_programs(tmp_path, monkeypatch):
    """Not a release build: a ``tools`` folder next to Python is nobody's doing, and not
    looked in. (A program right next to Python is, as ever.)"""
    program = release_build(tmp_path, monkeypatch, "linux", "ffmpeg", "PresentMon")
    monkeypatch.delattr(sys, "frozen")
    assert bundled.tools_folders() == []
    with pytest.raises(DeviceError, match="needs ffmpeg"):
        find_ffmpeg()
    assert presentmon.find_presentmon() is None
    (program / "ffmpeg").write_bytes(b"x")
    assert find_ffmpeg() == str(program / "ffmpeg")


def test_the_downloads_folder_is_never_looked_in(tmp_path, monkeypatch):
    """Libre Panel may run as administrator: a program any other program can put among the
    user's downloads is not one to start."""
    home = tmp_path / "home"
    downloads = home / "Downloads"
    downloads.mkdir(parents=True)
    for name in ("ffmpeg", "ffmpeg.exe", "PresentMon-2.6.0-x64.exe", "PresentMon.exe"):
        (downloads / name).write_bytes(b"MZ")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(presentmon.Path, "home", staticmethod(lambda: home))
    for platform in PLATFORMS:
        release_build(tmp_path, monkeypatch, platform)  # a download without the programs
        with pytest.raises(DeviceError, match="needs ffmpeg"):
            find_ffmpeg()
        assert presentmon.find_presentmon() is None


def test_the_file_names_follow_the_system(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert bundled.program_name("ffmpeg") == "ffmpeg.exe"
    monkeypatch.setattr(sys, "platform", "linux")
    assert bundled.program_name("ffmpeg") == "ffmpeg"


def test_a_folder_in_place_of_a_program_is_not_one(tmp_path, monkeypatch):
    program = release_build(tmp_path, monkeypatch, "linux")
    (program / "tools" / "ffmpeg").mkdir()
    assert bundled.find("ffmpeg") is None
    with pytest.raises(DeviceError, match="needs ffmpeg"):
        find_ffmpeg()


def test_pawnios_installer_is_handed_out_only_as_it_was_fetched(tmp_path, monkeypatch):
    """It is run with administrator rights from a folder the installing user may write to."""
    program = release_build(tmp_path, monkeypatch, "win32")
    assert bundled.pawnio_installer() is None  # not carried
    setup = program / "tools" / bundled.PAWNIO_SETUP
    setup.write_bytes(b"MZ another program")
    assert bundled.pawnio_installer() is None  # replaced
    setup.unlink()
    setup.mkdir()
    assert bundled.pawnio_installer() is None  # not a file
    setup.rmdir()
    monkeypatch.setattr(
        bundled, "PAWNIO_SETUP_SHA256", hashlib.sha256(b"MZ the real one").hexdigest()
    )
    setup.write_bytes(b"MZ the real one")
    assert bundled.pawnio_installer() == str(setup)
    monkeypatch.delattr(sys, "frozen")
    assert bundled.pawnio_installer() is None  # a pip install carries none


def test_the_pin_the_app_checks_is_the_one_the_download_was_fetched_by():
    spec = importlib.util.spec_from_file_location(
        "fetch_tools_pin", Path(__file__).resolve().parent.parent / "packaging" / "fetch_tools.py"
    )
    fetch_tools = importlib.util.module_from_spec(spec)
    sys.modules["fetch_tools_pin"] = fetch_tools
    spec.loader.exec_module(fetch_tools)
    pawnio = [tool for tool in fetch_tools.TOOLS if tool.file == bundled.PAWNIO_SETUP]
    assert [tool.sha256 for tool in pawnio] == [bundled.PAWNIO_SETUP_SHA256]
