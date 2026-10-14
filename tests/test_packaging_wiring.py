"""The release build carries ffmpeg, PresentMon and PawnIO's installer, and proves it on each
system. CI is the only place that builds the downloads, so these keep the pieces joined: the
fetch comes before the build, the checks run on the built folder, on what the setup installed
and on the AppImage's files, and every place agrees on what the folder is called."""

import importlib.util
import re
import sys
from pathlib import Path

from libre_panel import bundled

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_the_release_fetches_before_it_builds_and_checks_the_built_folder():
    workflow = read(".github", "workflows", "release.yml")
    fetch = workflow.index("run: python packaging/fetch_tools.py")
    build = workflow.index("run: pyinstaller --noconfirm --clean packaging/pyinstaller")
    check = workflow.index("run: python packaging/check_tools.py dist/libre-panel")
    assert fetch < build < check
    # for both systems: neither step is held back by an ``if:``
    before = workflow[workflow.rindex("- name:", 0, fetch) : fetch]
    assert "if:" not in before
    assert "if:" not in workflow[workflow.rindex("- name:", 0, check) : check]
    # the app started as at login must see the video mode miss nothing
    assert 'smoke_test.py --tools "$GUI" tray --background' in workflow


def test_the_setup_and_the_appimage_are_proven_with_the_same_check():
    installer = read("packaging", "windows", "test_installer.py")
    assert "check_tools.py" in installer and '"--tools"' in installer
    assert "PawnIO_setup.exe" in installer
    appimage = read("packaging", "linux", "test_appimage.sh")
    assert "--appimage-extract" in appimage
    assert 'packaging/check_tools.py" "$unpacked/squashfs-root/usr/lib/libre-panel"' in appimage
    assert appimage.count("smoke_test.py") == 2 and appimage.count("--tools") >= 2


def test_the_appimage_is_built_on_the_oldest_ubuntu_and_checked_for_the_glibc_it_needs(tmp_path):
    """An AppImage takes glibc from the system it runs on: built on a new runner, it asked for
    GLIBC_2.38 and did not start on the oldest Ubuntu LTS still supported (the AppImage
    catalog's test system)."""
    workflow = read(".github", "workflows", "release.yml")
    assert re.search(r"- os: ubuntu-22\.04\n\s+name: linux-x64", workflow)
    appimage = read("packaging", "linux", "test_appimage.sh")
    assert 'check_glibc.py" "$unpacked/squashfs-root" "${GLIBC_LIMIT:-2.35}"' in appimage
    check = load("check_glibc_wiring", PACKAGING / "linux" / "check_glibc.py")
    assert check.shown((2, 38, 0)) == "2.38" and check.shown(()) == "none"
    (tmp_path / "notes.txt").write_text("not a program", encoding="utf-8")
    assert check.main(["check_glibc.py", str(tmp_path), "2.35"]) == 1  # nothing to check: said


def test_the_downloads_carry_the_notice_that_names_their_sources():
    workflow = read(".github", "workflows", "release.yml")
    assert "Copy-Item LICENSE, README.md, NOTICE.md dist/libre-panel/" in workflow  # Windows
    assert "cp LICENSE README.md NOTICE.md dist/libre-panel/" in workflow  # Linux


def test_the_spec_puts_the_fetched_folder_next_to_the_program_under_the_name_the_app_looks_for():
    spec = read("packaging", "pyinstaller", "libre-panel.spec")
    fetch_tools = load("fetch_tools_wiring", PACKAGING / "fetch_tools.py")
    assert fetch_tools.DEFAULT_FOLDER.name == "bundled-tools"
    assert 'ROOT / "packaging" / "bundled-tools"' in spec
    assert "from libre_panel.bundled import TOOLS_FOLDER" in spec
    assert 'program_folder = Path(DISTPATH) / "libre-panel"' in spec  # the built folder
    assert "copytree(tools, program_folder / TOOLS_FOLDER" in spec  # and next to the program
    check_tools = load("check_tools_wiring", PACKAGING / "check_tools.py")
    assert check_tools.TOOLS_FOLDER == bundled.TOOLS_FOLDER == "tools"
    # the setup clears the same folder on an update
    assert re.search(
        r'^Type: filesandordirs; Name: "\{app\}\\tools"$',
        read("packaging", "windows", "libre-panel.iss"),
        re.M,
    )


def test_the_names_the_app_looks_for_are_the_ones_the_table_installs(monkeypatch):
    fetch_tools = load("fetch_tools_wiring", PACKAGING / "fetch_tools.py")
    for platform, system in (("windows", "win32"), ("linux", "linux")):
        monkeypatch.setattr(sys, "platform", system)
        files = {tool.file for tool in fetch_tools.TOOLS if tool.platform == platform}
        assert bundled.program_name("ffmpeg") in files
        if platform == "windows":
            assert {bundled.program_name("PresentMon"), bundled.PAWNIO_SETUP} <= files
            assert len(files) == 3  # and nothing else that the app does not know of
        else:
            assert files == {"ffmpeg"}


def test_the_setup_script_is_read_as_utf8():
    """Inno Setup reads a script as UTF-8 when it starts with the byte order mark; without it
    the German texts of the setup ("Für alle Sensoren …") would come out garbled."""
    script = ROOT.joinpath("packaging", "windows", "libre-panel.iss").read_bytes()
    assert script.startswith(b"\xef\xbb\xbf")
    assert "de.PrivilegesRequiredOverrideCurrentUser=Nur für &mich installieren" in script.decode()


def test_ci_runs_every_test_that_needs_a_browser_where_there_is_one():
    """Only CI's desktop job has Playwright and Chromium: elsewhere a test that needs them skips.
    A file of such tests left out of its list would never run anywhere."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = ci.split("\n  desktop:", 1)[1]
    listed = set(re.findall(r"tests/[\w*]+\.py", job))
    tests = ROOT / "tests"
    uses = re.compile(r"^(?:from|import) playwright|importorskip\(\s*\"playwright", re.M)
    needing = {
        path.name
        for path in tests.glob("test_*.py")
        if uses.search(path.read_text(encoding="utf-8")) and not path.name.startswith("test_zz")
    }
    assert "test_app_browser.py" in needing
    for name in sorted(needing):
        assert any(Path(name).match(Path(pattern).name) for pattern in listed), name
