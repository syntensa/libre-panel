"""The mark of the internet off Libre Panel's own files (libre_panel.unblock). Windows keeps it
in a stream "<file>:Zone.Identifier"; here a file of that name stands in for it."""

import sys
import xml.etree.ElementTree as ET

from libre_panel import unblock
from libre_panel.sensorservice import setup

RUNTIME = unblock.SENTINELS[0]


def mark(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    marked = path.with_name(path.name + ":" + unblock.STREAM)
    marked.write_text("[ZoneTransfer]\nZoneId=3\n")
    return marked


def test_a_downloaded_program_folder_loses_the_mark_at_the_start(tmp_path):
    marks = [mark(tmp_path / RUNTIME), mark(tmp_path / "LibrePanel.exe"),
             mark(tmp_path / "_internal" / "lhm" / "LibreHardwareMonitorLib.dll")]  # fmt: skip
    (tmp_path / "_internal" / "other.dll").touch()
    assert unblock.marked(tmp_path / RUNTIME)
    assert unblock.unblock_program(tmp_path, platform="win32") == 3
    assert not any(m.exists() for m in marks) and (tmp_path / "_internal" / "other.dll").exists()
    assert not unblock.marked(tmp_path / RUNTIME)


def test_nothing_happens_without_the_mark_or_elsewhere_than_on_windows(tmp_path):
    (tmp_path / RUNTIME).parent.mkdir(parents=True)
    (tmp_path / RUNTIME).touch()
    other = mark(tmp_path / "readme.txt")  # (the files .NET loads are not marked)
    assert unblock.unblock_program(tmp_path, platform="win32") == 0 and other.exists()
    mark(tmp_path / RUNTIME)
    assert unblock.unblock_program(tmp_path, platform="linux") == 0
    assert unblock.unblock_program(platform="win32") == 0  # (from the sources: not frozen)


def test_the_build_lets_dotnet_load_marked_libraries_too(tmp_path):
    for program in unblock.PROGRAMS:
        (tmp_path / program).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / program).touch()
    made = unblock.write_netfx_configs(tmp_path)
    assert [p.name for p in made] == [f"{p.split('/')[-1]}.config" for p in unblock.PROGRAMS]
    for config in made:
        node = ET.parse(config).getroot().find("runtime/loadFromRemoteSources")
        assert node is not None and node.get("enabled") == "true"


def test_the_service_copied_to_program_files_loses_the_mark(tmp_path, monkeypatch):
    source = tmp_path / "portable" / "sensors"
    marked = mark(source / RUNTIME)
    monkeypatch.setattr(sys, "platform", "win32")
    setup._replace_folder(source, tmp_path / "Program Files" / "sensors")
    copied = tmp_path / "Program Files" / "sensors"
    assert (copied / RUNTIME).exists() and not unblock.marked(copied / RUNTIME)
    assert marked.exists()  # (the portable folder: the start of the app takes care of it)
