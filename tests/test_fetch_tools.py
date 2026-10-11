"""packaging/fetch_tools.py: only the pinned downloads get through, and only the program is
taken out of them, if it is the build the licence note describes.

The downloads are made here, as small archives laid out like the real ones (FFmpeg-Builds'
zip and tar.xz); nothing is downloaded.
"""

import dataclasses
import hashlib
import importlib.util
import io
import os
import re
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "packaging" / "fetch_tools.py"
spec = importlib.util.spec_from_file_location("fetch_tools", SCRIPT)
fetch_tools = importlib.util.module_from_spec(spec)
sys.modules["fetch_tools"] = fetch_tools  # dataclasses look their module up
spec.loader.exec_module(fetch_tools)

SHA256 = re.compile(r"^[0-9a-f]{64}$")
BUILD = "ffmpeg-n8.1.3-9-g29e619e767-win64-gpl-8.1"  # the folder inside the real archive
GPL_3 = b"    GNU GENERAL PUBLIC LICENSE\n       Version 3, 29 June 2007\n"
CONFIGURATION = b"--enable-gpl --enable-version3 --disable-debug --enable-libx264 --enable-libvpx"
# PawnIO's installer keeps its texts as wide characters, as Windows programs do
PAWNIO_SETUP = b"MZ pawnio setup " + fetch_tools.PAWNIO_PERMISSION.encode("utf-16-le")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def ffmpeg_program(platform, configuration=CONFIGURATION):
    return (b"MZ" if platform == "windows" else b"\x7fELF") + b"\0" * 8 + configuration + b"\0"


def make_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def make_tar_xz(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return buffer.getvalue()


def ffmpeg_archive(platform, *, program=None, licence=GPL_3, extra=None):
    exe = "ffmpeg.exe" if platform == "windows" else "ffmpeg"
    files = {
        f"{BUILD}/bin/{exe}": program or ffmpeg_program(platform),
        f"{BUILD}/bin/ffprobe": b"not wanted",
        f"{BUILD}/bin/ffplay": b"not wanted",
        f"{BUILD}/doc/ffmpeg.html": b"<html>",
        f"{BUILD}/LICENSE.txt": licence,
        **(extra or {}),
    }
    return (make_zip if platform == "windows" else make_tar_xz)(files)


def lab(**options):
    """Downloads made here and the real table, pinned to them (the member patterns, marks and
    texts are the real ones)."""
    downloads = {
        "windows": ffmpeg_archive("windows", **options),
        "linux": ffmpeg_archive("linux", **options),
        "presentmon": b"MZ presentmon",
        "pawnio": PAWNIO_SETUP,
    }
    by_file = {
        "ffmpeg.exe": downloads["windows"],
        "ffmpeg": downloads["linux"],
        "PresentMon.exe": downloads["presentmon"],
        "PawnIO_setup.exe": downloads["pawnio"],
    }
    tools = tuple(
        dataclasses.replace(tool, sha256=sha(by_file[tool.file])) for tool in fetch_tools.TOOLS
    )
    return {tool.file: by_file[tool.file] for tool in tools}, tools


def run(folder, downloads, tools, platform="windows", **options):
    return fetch_tools.fetch(folder, platform, lambda tool: downloads[tool.file], tools, **options)


# -- the table -------------------------------------------------------------------------------


def test_the_table_pins_what_the_licence_notes_describe():
    windows = {t.file: t for t in fetch_tools.TOOLS if t.platform == "windows"}
    linux = {t.file: t for t in fetch_tools.TOOLS if t.platform == "linux"}
    assert sorted(windows) == ["PawnIO_setup.exe", "PresentMon.exe", "ffmpeg.exe"]
    assert sorted(linux) == ["ffmpeg"]
    assert {t.platform for t in fetch_tools.TOOLS} <= set(fetch_tools.PLATFORMS)
    for tool in fetch_tools.TOOLS:
        assert SHA256.match(tool.sha256), tool.file
        assert tool.url.startswith("https://github.com/"), tool.file
        assert tool.licence and tool.version and tool.source, tool.file
        assert all(line.split(": ", 1)[1].startswith("https://") for line in tool.source), tool.file
        for name in tool.texts:  # the repository carries the texts that go with it
            assert (fetch_tools.LICENSES / name).is_file(), name
        assert tool.texts or tool.archive_texts, f"{tool.file} has no licence text to ship"
    # the same build on both systems, from the release the project keeps for two years
    ffmpeg = [windows["ffmpeg.exe"], linux["ffmpeg"]]
    assert {t.version for t in ffmpeg} == {"n8.1.3-9-g29e619e767"}
    assert all(f"/{fetch_tools.FFMPEG_TAG}/{fetch_tools.FFMPEG_BUILD}-" in t.url for t in ffmpeg)
    assert all("-gpl-" in t.url and "shared" not in t.url for t in ffmpeg)
    for tool in ffmpeg:  # GPL with libx264, and nothing that cannot be passed on
        assert "--enable-libx264" in tool.marks and "--enable-gpl" in tool.marks
        assert "--enable-nonfree" in tool.forbidden
        assert tool.archive_texts and tool.archive_texts[0].phrases == fetch_tools.GPL_3
        assert any("FFmpeg-Builds/tree/" in line for line in tool.source)  # the recipe
        assert any("FFmpeg/FFmpeg/tree/" in line for line in tool.source)  # the source
        assert any("x264" in line for line in tool.source)
    presentmon = windows["PresentMon.exe"]
    assert presentmon.licence == "MIT" and presentmon.version in presentmon.url
    assert presentmon.url.endswith("PresentMon-2.6.0-x64.exe")  # the console program
    assert windows["PawnIO_setup.exe"].url.endswith("/2.2.0/PawnIO_setup.exe")
    pawnio = windows["PawnIO_setup.exe"]
    assert "GPL-2.0-or-later" in pawnio.licence and "redistributed unmodified" in pawnio.licence
    assert pawnio.marks == (fetch_tools.PAWNIO_PERMISSION,)  # the permission it states itself


def test_the_repository_carries_the_licence_texts_that_ship_with_the_programs():
    names = sorted(p.name for p in fetch_tools.LICENSES.glob("*.txt"))
    assert names == [
        "PawnIO-COPYING.txt",
        "PawnIO-NOTICE.txt",
        "PresentMon-LICENSE.txt",
        "PresentMon-THIRD_PARTY.txt",
    ]
    read = lambda name: (fetch_tools.LICENSES / name).read_text("utf-8")  # noqa: E731
    assert "GNU GENERAL PUBLIC LICENSE\n                       Version 2, June 1991" in read(
        names[0]
    )
    assert "special exception" in read(names[1]) and "version 2 of the License" in read(names[1])
    assert fetch_tools.PAWNIO_PERMISSION in read(names[1])  # what the installer says of itself
    assert read(names[2]).startswith("Copyright (C) 2017-2024 Intel Corporation")
    assert "Permission is hereby granted, free of charge" in read(names[2])
    assert "Notice from NVAPI interface" in read(names[3])
    for name in names:  # nobody's address is kept here
        assert "@" not in read(name), name


def test_what_was_fetched_stays_out_of_the_repository():
    assert "packaging/bundled-tools/" in (ROOT / ".gitignore").read_text().splitlines()
    assert fetch_tools.DEFAULT_FOLDER == ROOT / "packaging" / "bundled-tools"


# -- taking the programs ------------------------------------------------------------------------


def test_only_the_program_comes_out_of_a_download(tmp_path):
    downloads, tools = lab()
    folder = tmp_path / "tools"
    lines = run(folder, downloads, tools, "windows")
    assert sorted(p.name for p in folder.iterdir()) == [
        "PawnIO_setup.exe", "PresentMon.exe", "SOURCES.txt", "ffmpeg.exe", "licenses",
    ]  # fmt: skip
    assert (folder / "ffmpeg.exe").read_bytes() == ffmpeg_program("windows")  # no ffprobe, no docs
    assert (folder / "PresentMon.exe").read_bytes() == b"MZ presentmon"  # a plain file as it is
    assert sorted(p.name for p in (folder / "licenses").iterdir()) == [
        "FFmpeg-LICENSE.txt", "PawnIO-COPYING.txt", "PawnIO-NOTICE.txt",
        "PresentMon-LICENSE.txt", "PresentMon-THIRD_PARTY.txt",
    ]  # fmt: skip
    assert (folder / "licenses" / "FFmpeg-LICENSE.txt").read_bytes() == GPL_3  # as it came
    assert [line.split()[0] for line in lines] == [
        "ffmpeg.exe",
        "PresentMon.exe",
        "PawnIO_setup.exe",
    ]
    assert "FFmpeg n8.1.3-9-g29e619e767, GPL-3.0-or-later" in lines[0]
    assert f"sha256 {sha(ffmpeg_program('windows'))}" in lines[0]
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".tools")]  # no scraps


def test_sources_say_what_and_where_and_list_the_files_hashes(tmp_path):
    downloads, tools = lab()
    folder = tmp_path / "tools"
    run(folder, downloads, tools, "windows")
    sources = (folder / "SOURCES.txt").read_text("utf-8")
    assert (
        "https://github.com/FFmpeg/FFmpeg/tree/29e619e767cde9045a75c29bc9a8278ae7b3a98b" in sources
    )
    assert (
        "https://github.com/BtbN/FFmpeg-Builds/tree/6c9aec5fc9a72ec3abedd1fa84db141fa18cf52b"
        in sources
    )
    assert "https://github.com/GameTechDev/PresentMon/tree/" in sources
    assert "https://github.com/namazso/PawnIO/tree/" in sources
    assert f"SHA-256 of the download: {tools[0].sha256}" in sources
    listed = dict(
        (name, digest) for digest, name in re.findall(r"^([0-9a-f]{64})  (\S+)$", sources, re.M)
    )
    assert sorted(listed) == ["PawnIO_setup.exe", "PresentMon.exe", "ffmpeg.exe"]
    for name, digest in listed.items():
        assert sha((folder / name).read_bytes()) == digest


def test_linux_gets_ffmpeg_alone_and_able_to_run(tmp_path):
    downloads, tools = lab()
    folder = tmp_path / "tools"
    lines = run(folder, downloads, tools, "linux")
    assert sorted(p.name for p in folder.iterdir()) == ["SOURCES.txt", "ffmpeg", "licenses"]
    assert (folder / "ffmpeg").read_bytes() == ffmpeg_program("linux")
    assert [line.split()[0] for line in lines] == ["ffmpeg"]
    if os.name == "posix":
        assert os.access(folder / "ffmpeg", os.X_OK)


def test_nothing_is_unpacked_by_the_name_an_archive_gives(tmp_path):
    downloads, tools = lab(extra={"../evil.exe": b"MZ", "../../bin/ffmpeg.exe": b"MZ"})
    run(tmp_path / "tools", downloads, tools, "windows")
    assert not (tmp_path / "evil.exe").exists() and not (tmp_path.parent / "evil.exe").exists()


def test_the_unknown_is_refused(tmp_path):
    downloads, tools = lab()
    with pytest.raises(fetch_tools.FetchError, match="unknown platform 'plan9'"):
        run(tmp_path / "tools", downloads, tools, "plan9")
    with pytest.raises(fetch_tools.FetchError, match="no program for linux"):
        run(tmp_path / "tools", downloads, tools[:3], "linux")


# -- what is refused ----------------------------------------------------------------------------


def test_a_scanner_that_holds_the_rename_off_for_a_moment_is_waited_for(tmp_path, monkeypatch):
    downloads, tools = lab()
    folder = tmp_path / "tools"
    real = Path.rename
    refused = []

    def rename(self, target):
        if self.name.startswith(".tools") and len(refused) < 2:
            refused.append(self)
            raise PermissionError("another process has the file open")
        return real(self, target)

    pauses = []
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(fetch_tools.time, "sleep", pauses.append)
    run(folder, downloads, tools, "windows")
    assert len(refused) == 2 and pauses == [1.0, 2.0] and (folder / "ffmpeg.exe").exists()
    # a rename that keeps being refused is a failure, and leaves nothing behind
    monkeypatch.setattr(
        Path, "rename", lambda self, target: (_ for _ in ()).throw(PermissionError())
    )
    other = tmp_path / "other"
    with pytest.raises(PermissionError):
        run(other, downloads, tools, "windows")
    assert not other.exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".other")]


def test_a_download_that_is_not_the_checked_one_is_refused(tmp_path):
    downloads, tools = lab()
    downloads["PresentMon.exe"] = b"MZ another program"
    folder = tmp_path / "tools"
    with pytest.raises(
        fetch_tools.FetchError, match=r"PresentMon 2.6.0 .* is not what was checked"
    ):
        run(folder, downloads, tools, "windows")
    assert not folder.exists()  # not even the first one stays
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".tools")]


def test_a_failure_leaves_the_folder_that_was_there(tmp_path):
    downloads, tools = lab()
    folder = tmp_path / "tools"
    run(folder, downloads, tools, "windows")
    downloads["PawnIO_setup.exe"] = b"damaged"
    with pytest.raises(fetch_tools.FetchError):
        run(folder, downloads, tools, "windows")
    assert (folder / "PawnIO_setup.exe").read_bytes() == PAWNIO_SETUP  # as it was


def test_a_new_run_replaces_the_old_folder_whole(tmp_path):
    downloads, tools = lab()
    folder = tmp_path / "tools"
    folder.mkdir()
    (folder / "ffprobe.exe").write_bytes(b"left over from another version")
    run(folder, downloads, tools, "windows")
    assert not (folder / "ffprobe.exe").exists() and (folder / "ffmpeg.exe").exists()


@pytest.mark.parametrize("platform", ["windows", "linux"])
def test_a_build_without_libx264_or_the_gpl_is_refused(tmp_path, platform):
    """A build of the right release that has no libx264 (the LGPL variant) cannot encode the
    video mode's stream, and would not be the GPL build NOTICE.md describes."""
    for configuration in (b"--enable-gpl --enable-version3", b"--enable-libx264 --enable-gpl"):
        downloads, tools = lab(program=ffmpeg_program(platform, configuration))
        with pytest.raises(fetch_tools.FetchError, match=r"does not contain '--enable-"):
            run(tmp_path / "tools", downloads, tools, platform)


def test_a_non_free_build_is_refused(tmp_path):
    configuration = CONFIGURATION + b" --enable-nonfree --enable-libfdk-aac"
    downloads, tools = lab(program=ffmpeg_program("windows", configuration))
    with pytest.raises(fetch_tools.FetchError, match="contains '--enable-nonfree'"):
        run(tmp_path / "tools", downloads, tools, "windows")


def test_an_installer_that_no_longer_says_it_may_be_passed_on_is_refused(tmp_path):
    """PawnIO's installer says so itself; a version that does not has terms nobody has read."""
    downloads, tools = lab()
    other = b"MZ pawnio setup " + "All rights reserved.".encode("utf-16-le")
    downloads["PawnIO_setup.exe"] = other
    tools = tuple(
        dataclasses.replace(t, sha256=sha(other)) if t.file == "PawnIO_setup.exe" else t
        for t in tools
    )
    with pytest.raises(fetch_tools.FetchError, match="PawnIO_setup.exe .* does not contain 'This"):
        run(tmp_path / "tools", downloads, tools, "windows")
    # the sentence is found as it is, too, and as wide characters
    for stated in (fetch_tools.PAWNIO_PERMISSION.encode(), PAWNIO_SETUP):
        assert fetch_tools._contains(b"MZ " + stated, fetch_tools.PAWNIO_PERMISSION)


def test_a_licence_text_that_is_not_the_gpl_3_is_refused(tmp_path):
    for licence in (b"The MIT License", b"GNU GENERAL PUBLIC LICENSE\n Version 2, June 1991\n"):
        downloads, tools = lab(licence=licence)
        with pytest.raises(fetch_tools.FetchError, match=r"FFmpeg-LICENSE.txt .* does not say"):
            run(tmp_path / "tools", downloads, tools, "windows")


def test_a_program_for_another_system_is_refused(tmp_path):
    downloads, tools = lab(program=ffmpeg_program("linux"))
    with pytest.raises(fetch_tools.FetchError, match="is not a program for windows"):
        run(tmp_path / "tools", downloads, tools, "windows")


@pytest.mark.parametrize(
    "files, wanted",
    [
        (  # and it says what the archive holds, to put the table right
            {"ffmpeg-x/bin/ffprobe.exe": b"MZ", "ffmpeg-x/LICENSE.txt": GPL_3},
            r"has 0 files that match .*ffmpeg.* \(it holds, among others: ffmpeg-x/bin/ffprobe.exe",
        ),
        (
            {"a/bin/ffmpeg.exe": b"MZ", "b/bin/ffmpeg.exe": b"MZ", "a/LICENSE.txt": GPL_3},
            "has 2 files that match",
        ),
        (
            {f"{BUILD}/bin/ffmpeg.exe": ffmpeg_program("windows")},
            r"has 0 files that match .*LICENSE",
        ),
    ],
)
def test_an_archive_with_other_files_than_the_table_expects_is_refused(tmp_path, files, wanted):
    downloads, tools = lab()
    archive = make_zip(files)
    downloads["ffmpeg.exe"] = archive
    tools = tuple(
        dataclasses.replace(t, sha256=sha(archive)) if t.file == "ffmpeg.exe" else t for t in tools
    )
    with pytest.raises(fetch_tools.FetchError, match=wanted):
        run(tmp_path / "tools", downloads, tools, "windows")


@pytest.mark.parametrize("platform", ["windows", "linux"])
def test_a_download_that_is_no_archive_is_refused(tmp_path, platform):
    downloads, tools = lab()
    name = "ffmpeg.exe" if platform == "windows" else "ffmpeg"
    downloads[name] = b"not an archive"
    tools = tuple(
        dataclasses.replace(t, sha256=sha(b"not an archive")) if t.file == name else t
        for t in tools
    )
    with pytest.raises(fetch_tools.FetchError, match="not an archive that can be read"):
        run(tmp_path / "tools", downloads, tools, platform)


def test_a_licence_text_of_the_repository_cannot_be_left_out(tmp_path):
    downloads, tools = lab()
    texts = tmp_path / "texts"
    texts.mkdir()
    with pytest.raises(fetch_tools.FetchError, match=r"PresentMon-LICENSE.txt is missing"):
        run(tmp_path / "tools", downloads, tools, "windows", licence_texts=texts)
    assert not (tmp_path / "tools").exists()


# -- downloading -------------------------------------------------------------------------------


class Answer:
    def __init__(self, data, url):
        self.data, self.url = data, url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.data

    def geturl(self):
        return self.url


def test_downloads_are_https_only(monkeypatch):
    asked = []

    def urlopen(request, timeout=None):
        asked.append((request.full_url, timeout))
        return Answer(b"bytes", request.full_url)

    monkeypatch.setattr(fetch_tools.urllib.request, "urlopen", urlopen)
    assert fetch_tools.download("https://github.com/x/releases/a.zip") == b"bytes"
    assert asked == [("https://github.com/x/releases/a.zip", 120)]
    with pytest.raises(fetch_tools.FetchError, match="not an https address"):
        fetch_tools.download("http://github.com/x/releases/a.zip")
    assert len(asked) == 1  # not even asked

    monkeypatch.setattr(
        fetch_tools.urllib.request,
        "urlopen",
        lambda request, timeout=None: Answer(b"", "http://x/"),
    )
    with pytest.raises(fetch_tools.FetchError, match="redirected to http://x/"):
        fetch_tools.download("https://github.com/x/releases/a.zip")


def test_a_server_that_fails_for_a_moment_is_asked_again(monkeypatch):
    answers = [
        fetch_tools.urllib.error.HTTPError("u", 502, "Bad Gateway", {}, None),
        ConnectionResetError("reset"),
        Answer(b"third time", "https://x/"),
    ]
    pauses = []

    def urlopen(request, timeout=None):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(fetch_tools.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(fetch_tools.time, "sleep", pauses.append)
    assert fetch_tools.download("https://x/a") == b"third time"
    assert pauses == [5.0, 10.0]  # waiting longer each time


def test_a_missing_file_is_not_asked_for_again_and_a_dead_server_is_given_up_on(monkeypatch):
    calls = []

    def missing(request, timeout=None):
        calls.append(1)
        raise fetch_tools.urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    monkeypatch.setattr(fetch_tools.urllib.request, "urlopen", missing)
    monkeypatch.setattr(fetch_tools.time, "sleep", lambda seconds: None)
    with pytest.raises(fetch_tools.FetchError, match="the server says 404 Not Found"):
        fetch_tools.download("https://x/a")
    assert len(calls) == 1

    def dead(request, timeout=None):
        calls.append(1)
        raise TimeoutError("timed out")

    calls.clear()
    monkeypatch.setattr(fetch_tools.urllib.request, "urlopen", dead)
    with pytest.raises(fetch_tools.FetchError, match="cannot be read: timed out"):
        fetch_tools.download("https://x/a")
    assert len(calls) == 3


# -- the command -------------------------------------------------------------------------------


def test_the_pins_of_a_new_set_are_printed_not_checked(tmp_path, capsys, monkeypatch):
    kept = sorted(fetch_tools.DEFAULT_FOLDER.glob("*"))  # a build machine may have fetched
    downloads, tools = lab()
    lines = fetch_tools.pins(lambda tool: downloads[tool.file], tools)
    assert (
        lines[1] == f"PresentMon.exe (windows) {tools[1].url}\n    sha256 {sha(b'MZ presentmon')}"
    )
    assert len(lines) == len(tools)
    monkeypatch.setattr(fetch_tools, "TOOLS", tools)
    archives = tmp_path / "archives"
    archives.mkdir()
    for tool in tools:
        (archives / tool.filename).write_bytes(downloads[tool.file])
    assert fetch_tools.main(["--print-pins", "--archive-dir", str(archives)]) == 0
    assert "PresentMon-2.6.0-x64.exe" in capsys.readouterr().out
    assert sorted(fetch_tools.DEFAULT_FOLDER.glob("*")) == kept  # nothing written


def test_the_command_reports_and_fails_with_a_message(tmp_path, capsys, monkeypatch):
    downloads, tools = lab()
    monkeypatch.setattr(fetch_tools, "TOOLS", tools)
    archives = tmp_path / "archives"
    archives.mkdir()
    for tool in tools:
        (archives / tool.filename).write_bytes(downloads[tool.file])
    folder = tmp_path / "tools"
    assert (
        fetch_tools.main([str(folder), "--platform", "windows", "--archive-dir", str(archives)])
        == 0
    )
    out = capsys.readouterr().out
    assert "3 programs in" in out and "ffmpeg.exe" in out and (folder / "ffmpeg.exe").exists()

    (archives / tools[1].filename).write_bytes(b"damaged")
    args = [str(folder), "--platform", "windows", "--archive-dir", str(archives)]
    assert fetch_tools.main(args) == 1
    error = capsys.readouterr().err
    assert "PresentMon 2.6.0" in error and "is not what was checked" in error
    (archives / tools[1].filename).unlink()
    assert fetch_tools.main(args) == 1
    assert "PresentMon-2.6.0-x64.exe cannot be read" in capsys.readouterr().err
    assert (folder / "ffmpeg.exe").exists()  # the folder from before is as it was


def test_the_platform_is_the_one_of_the_system_unless_told(monkeypatch):
    for system, platform in {"win32": "windows", "linux": "linux"}.items():
        monkeypatch.setattr(sys, "platform", system)
        assert fetch_tools.default_platform() == platform
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(fetch_tools.FetchError, match="use --platform"):
        fetch_tools.default_platform()
