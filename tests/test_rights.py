"""With the highest rights Libre Panel loads and starts nothing that programs without them may
change (rights.py): plugin folders, ffmpeg, PresentMon, the AI agents' programs."""

import os
import sys
from pathlib import Path

import pytest

from libre_panel import rights
from libre_panel.config import Config, VideoConfig
from libre_panel.devices import h264
from libre_panel.devices.turzx import video_gaps
from libre_panel.plugins import loader


@pytest.fixture
def elevated(monkeypatch):
    """Libre Panel runs with the highest rights; a place is safe in a folder named "safe"."""
    monkeypatch.setattr(rights, "elevated", lambda: True)
    monkeypatch.setattr(rights, "safe_place", lambda path: "safe" in Path(path).parts)


def program(folder, name):
    """An executable file ``name`` in ``folder``, as PATH finds it."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (f"{name}.exe" if sys.platform == "win32" else name)
    path.write_text("")
    path.chmod(0o755)
    return path


def found(path):
    """A path found, as the file system compares it (Windows' PATH finds "ffmpeg.EXE")."""
    return os.path.normcase(path) if path else path


def test_without_the_highest_rights_every_place_will_do(tmp_path, monkeypatch):
    monkeypatch.setattr(rights, "elevated", lambda: False)
    assert rights.safe_place(tmp_path / "anything")
    tool = program(tmp_path / "bin", "lp-tool")
    monkeypatch.setenv("PATH", str(tool.parent))
    assert found(rights.which("lp-tool")) == found(str(tool))


def test_on_windows_only_the_folders_of_administrators_are_safe(tmp_path):
    protected = [tmp_path / "Program Files", tmp_path / "Windows"]
    for folder in protected:
        folder.mkdir()
    assert rights._safe(tmp_path / "Program Files" / "ffmpeg" / "ffmpeg.exe", True, protected)
    assert rights._safe(tmp_path / "Windows" / "System32" / "x.exe", True, protected)
    assert not rights._safe(tmp_path / "Users" / "me" / "ffmpeg.exe", True, protected)
    # a way out of a protected folder is followed to where it leads
    assert not rights._safe(tmp_path / "Program Files" / ".." / "Users" / "x.exe", True, protected)
    assert not rights._safe(tmp_path / "Program Files Evil" / "x.exe", True, protected)


@pytest.mark.skipif(sys.platform == "win32", reason="owners and modes are POSIX'")
def test_as_root_a_place_is_safe_where_nobody_else_may_write_above_it(tmp_path):
    owners = (0, os.getuid())  # (the tests' own user stands in for root)
    folder = tmp_path / "bin"
    folder.mkdir(mode=0o755)
    tool = program(folder, "ffmpeg")
    assert rights._safe(tool, False, owners=owners)
    tool.chmod(0o777)  # anyone may change the program
    assert not rights._safe(tool, False, owners=owners)
    tool.chmod(0o755)
    folder.chmod(0o777)  # anyone may replace it
    assert not rights._safe(tool, False, owners=owners)
    folder.chmod(0o1777)  # unless the sticky bit keeps what is not theirs (as /tmp)
    assert rights._safe(tool, False, owners=owners)
    folder.chmod(0o755)
    assert not rights._safe(tool, False, owners=(os.getuid() + 1,))  # someone else's


def test_with_the_highest_rights_only_a_program_from_a_safe_place_is_found(
    tmp_path, monkeypatch, elevated
):
    unsafe = program(tmp_path / "user" / "bin", "lp-tool")
    monkeypatch.setenv("PATH", str(unsafe.parent))
    assert rights.which("lp-tool") is None
    safe = program(tmp_path / "safe" / "bin", "lp-tool")
    monkeypatch.setenv("PATH", os.pathsep.join([str(unsafe.parent), str(safe.parent)]))
    assert found(rights.which("lp-tool")) == found(str(safe))  # the first one that may be started


def test_with_the_highest_rights_the_plugin_folders_are_held_back(plugin_folder, elevated):
    package = plugin_folder("def ring(*args):\n    return None\n",
                            {"libre_panel.widgets": {"mine.ring": "ring"}})  # fmt: skip
    found = loader.discover()
    assert found.held_back == [package]
    assert "mine.ring" not in found.parts.get("widgets", {})
    built_in = [part for parts in found.parts.values() for part in parts.values()]
    assert built_in and all(part.source == loader.BUILT_IN for part in built_in)  # Libre Panel's


def test_a_plugin_folder_in_a_safe_place_is_loaded(plugin_folder, monkeypatch):
    plugin_folder(
        "def ring(*args):\n    return None\n", {"libre_panel.widgets": {"mine.ring": "ring"}}
    )
    monkeypatch.setattr(rights, "elevated", lambda: True)
    monkeypatch.setattr(rights, "safe_place", lambda path: True)  # (root's own folder)
    found = loader.discover()
    assert found.held_back == [] and "mine.ring" in found.parts["widgets"]


def test_with_the_highest_rights_video_ffmpeg_elsewhere_is_passed_over(
    tmp_path, monkeypatch, elevated
):
    named = program(tmp_path / "user", "ffmpeg")
    monkeypatch.setenv("PATH", "")
    with pytest.raises(h264.FfmpegHeldBack):
        h264.find_ffmpeg(str(named))
    assert video_gaps(VideoConfig(ffmpeg=str(named), local_clip="x.h264"), None) == [
        "ffmpeg-rights"
    ]
    other = program(tmp_path / "safe", "ffmpeg")
    monkeypatch.setenv("PATH", str(other.parent))
    assert found(h264.find_ffmpeg(str(named))) == found(
        str(other)
    )  # the panel goes on with a safe one
    assert video_gaps(VideoConfig(ffmpeg=str(named), local_clip="x.h264"), None) == []
    # a path that names nothing is said as before, rights or not
    with pytest.raises(h264.DeviceError) as missing:
        h264.find_ffmpeg(str(tmp_path / "nowhere" / "ffmpeg"))
    assert not isinstance(missing.value, h264.FfmpegHeldBack)


def test_with_the_highest_rights_presentmon_and_the_agents_come_from_safe_places(
    tmp_path, monkeypatch, elevated
):
    from libre_panel import agents
    from libre_panel.sensors.presentmon import find_presentmon

    named = program(tmp_path / "user", "PresentMon")
    monkeypatch.setenv("PATH", "")
    assert find_presentmon(str(named)) is None
    safe = program(tmp_path / "safe", "PresentMon")
    monkeypatch.setenv("PATH", str(safe.parent))
    assert found(find_presentmon(str(named))) == found(str(safe))
    program(tmp_path / "user", "claude")
    monkeypatch.setenv("PATH", str(tmp_path / "user"))
    assert agents.program("claude") is None


def test_the_settings_say_what_a_run_with_the_highest_rights_holds_back(
    tmp_path, monkeypatch, elevated, plugin_folder
):
    from libre_panel.editor.settings import Settings

    package = plugin_folder("", {})
    named = program(tmp_path / "user", "ffmpeg")
    config = Config()
    config.device.video.ffmpeg = str(named)
    config.sensors.options["presentmon"] = {"path": str(program(tmp_path / "safe", "PM"))}
    assert Settings._held_back(config) == {"plugins": [package], "programs": ["video.ffmpeg"]}
    monkeypatch.setattr(rights, "elevated", lambda: False)
    loader.reset_registry()
    assert Settings._held_back(config) == {"plugins": [], "programs": []}
