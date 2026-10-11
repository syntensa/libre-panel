"""Fetch the programs that the downloads carry next to Libre Panel, for the build.

    python packaging/fetch_tools.py [FOLDER] [--platform windows|linux] [--archive-dir DIR]

The programs are not part of the repository. This downloads the pinned files over HTTPS,
checks every download against the SHA-256 recorded below, takes out only the program Libre
Panel runs (not ffprobe, ffplay or the documentation of a build) and checks that it is what
the licence note says: ffmpeg built as GPL with libx264 and without the non-free parts, with
the licence text it came with. FOLDER (default ``packaging/bundled-tools``) then holds the
programs, their licence texts in ``licenses`` and ``SOURCES.txt`` (what they are, where the
source code is, the SHA-256 of every file); the PyInstaller spec puts it next to the program
as ``tools``, where libre_panel/bundled.py finds it. It fails, and leaves an existing FOLDER
alone, when anything does not match.

  * ffmpeg (Windows and Linux): the video mode encodes with it. FFmpeg-Builds by BtbN, the
    GPL build with libx264, from a release the project keeps (the last build of a month is
    kept for two years). GPL-3.0-or-later; the downloads pass it on unchanged, with the
    licence text and the address of the exact source.
  * PresentMon (Windows): Intel's console program for the frame rates of games. MIT.
  * PawnIO_setup.exe (Windows): the official installer of the PawnIO driver, which
    LibreHardwareMonitor reads the CPU's sensors through. Not run here: the installer of
    Libre Panel does that. The driver is GPL-2.0-or-later with an exception (NOTICE.md).

``--archive-dir`` reads the files (named like the end of their address) from a folder instead
of the internet, for builds without a network; they are checked all the same.

The licences were read before these were pinned; see NOTICE.md. To move to other versions,
read their licences again, change the table and run ``--print-pins``: it prints the SHA-256 of
what the table's addresses now give (and checks and writes nothing).
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import lzma
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_FOLDER = HERE / "bundled-tools"
LICENSES = HERE / "tools-licenses"  # texts that ship with the programs (not from the downloads)
PLATFORMS = ("windows", "linux")

MZ, ELF = b"MZ", b"\x7fELF"  # how a Windows program and a Linux one begin


@dataclass(frozen=True)
class Text:
    """A licence text inside a download, to be taken along with the program."""

    member: str  # regular expression for the whole name of the file in the archive
    name: str  # how it is saved in ``licenses``
    phrases: tuple[str, ...]  # what it has to say, to be the licence the table names


@dataclass(frozen=True)
class Tool:
    """One program to put into the folder."""

    file: str  # its name in the folder
    program: str  # what it is, for messages: "FFmpeg"
    version: str
    platform: str  # "windows" or "linux"
    url: str  # an https address; with ``member`` a .zip or .tar.xz, else the program itself
    sha256: str  # of what the address gives
    licence: str  # SPDX expression
    source: tuple[str, ...]  # where the source code is, one line each
    magic: bytes  # how the program begins: MZ or ELF
    member: str = ""  # of an archive: the regular expression for the program's name in it
    marks: tuple[bytes, ...] = ()  # what has to be inside the program (a build's configuration)
    forbidden: tuple[bytes, ...] = ()  # what must not be inside it
    texts: tuple[str, ...] = ()  # files of LICENSES to take along
    archive_texts: tuple[Text, ...] = ()  # licence texts inside the archive to take along

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]


FFMPEG_TAG = "autobuild-2026-09-30-13-08"  # FFmpeg-Builds' last build of September 2026
FFMPEG_BUILD = "ffmpeg-n8.1.3-9-g29e619e767"  # FFmpeg release 8.1.3 and nine more commits
FFMPEG_URL = f"https://github.com/BtbN/FFmpeg-Builds/releases/download/{FFMPEG_TAG}/{FFMPEG_BUILD}"
FFMPEG_SOURCE = (
    "FFmpeg: https://github.com/FFmpeg/FFmpeg/tree/29e619e767cde9045a75c29bc9a8278ae7b3a98b "
    "(release/8.1: tag n8.1.3 and nine more commits)",
    "The build recipe, with every library in it and its version (scripts.d): "
    "https://github.com/BtbN/FFmpeg-Builds/tree/6c9aec5fc9a72ec3abedd1fa84db141fa18cf52b "
    f"(tag {FFMPEG_TAG})",
    "x264, the GPL library that does the encoding: https://code.videolan.org/videolan/x264 "
    "(commit 0480cb05fa188d37ae87e8f4fd8f1aea3711f7ee)",
)
# An ffmpeg that lacks these is not the GPL build with libx264 that the video mode needs and
# the licence note describes; one with --enable-nonfree could not be passed on at all.
FFMPEG_MARKS = (b"--enable-gpl", b"--enable-version3", b"--enable-libx264")
GPL_3 = ("GNU GENERAL PUBLIC LICENSE", "Version 3, 29 June 2007")
FFMPEG_LICENCE = Text(r"[^/]+/LICENSE\.txt", "FFmpeg-LICENSE.txt", GPL_3)

# In the order they are listed in SOURCES.txt.
TOOLS = (
    Tool(
        "ffmpeg.exe",
        "FFmpeg",
        "n8.1.3-9-g29e619e767",
        "windows",
        f"{FFMPEG_URL}-win64-gpl-8.1.zip",
        "7b801cdd3a1a0bb54ae6f572187e68b4ed54f52086cfee133fab2180bfb429fa",
        "GPL-3.0-or-later",
        FFMPEG_SOURCE,
        MZ,
        member=r"[^/]+/bin/ffmpeg\.exe",
        marks=FFMPEG_MARKS,
        forbidden=(b"--enable-nonfree",),
        archive_texts=(FFMPEG_LICENCE,),
    ),
    Tool(
        "PresentMon.exe",
        "PresentMon",
        "2.6.0",
        "windows",
        "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/"
        "PresentMon-2.6.0-x64.exe",
        "b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af",
        "MIT",
        (
            "PresentMon: https://github.com/GameTechDev/PresentMon/tree/"
            "e13fce6acdb55a808fd8318175a56863e532d95f (tag v2.6.0)",
        ),
        MZ,
        texts=("PresentMon-LICENSE.txt", "PresentMon-THIRD_PARTY.txt"),
    ),
    Tool(
        "PawnIO_setup.exe",
        "PawnIO installer",
        "2.2.0",
        "windows",
        "https://github.com/namazso/PawnIO.Setup/releases/download/2.2.0/PawnIO_setup.exe",
        "1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032",
        "GPL-2.0-or-later WITH a special exception (the driver it installs)",
        (
            "The driver: https://github.com/namazso/PawnIO/tree/"
            "5cdf470831fdfff3f7f1d06363ca6b230f3bf35a (tag 2.2.0)",
        ),
        MZ,
        texts=("PawnIO-COPYING.txt", "PawnIO-NOTICE.txt"),
    ),
    Tool(
        "ffmpeg",
        "FFmpeg",
        "n8.1.3-9-g29e619e767",
        "linux",
        f"{FFMPEG_URL}-linux64-gpl-8.1.tar.xz",
        "97ce978979194b5cf7e06a5e68020dbdaa7a4f3294c5452b6a1bc347100dbb79",
        "GPL-3.0-or-later",
        FFMPEG_SOURCE,
        ELF,
        member=r"[^/]+/bin/ffmpeg",
        marks=FFMPEG_MARKS,
        forbidden=(b"--enable-nonfree",),
        archive_texts=(FFMPEG_LICENCE,),
    ),
)


class FetchError(Exception):
    """Something is not as pinned; the message says what."""


def download(url: str, attempts: int = 3, pause: Callable[[float], None] = time.sleep) -> bytes:
    """The bytes at an https address (a redirect to anything else is refused). A server error
    or a broken connection is tried again, a few times; a missing file is not."""
    if not url.startswith("https://"):
        raise FetchError(f"{url} is not an https address")
    request = urllib.request.Request(url, headers={"User-Agent": "libre-panel-build"})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                if not response.geturl().startswith("https://"):
                    raise FetchError(
                        f"{url} was redirected to {response.geturl()}, which is not https"
                    )
                return response.read()
        except urllib.error.HTTPError as exc:  # before OSError, which it is one of
            if exc.code < 500 or attempt == attempts:
                raise FetchError(f"{url}: the server says {exc.code} {exc.reason}") from exc
        except (OSError, http.client.HTTPException) as exc:
            if attempt == attempts:
                raise FetchError(f"{url} cannot be read: {exc}") from exc
        pause(5.0 * attempt)
    raise AssertionError("unreachable")  # the loop returns or raises


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check(what: str, data: bytes, expected: str) -> None:
    got = sha256(data)
    if got != expected:
        raise FetchError(
            f"{what} is not what was checked: SHA-256 {got}, expected {expected}. "
            "The download may be damaged, or the file was replaced."
        )


def _members(tool: Tool, data: bytes, patterns: list[str]) -> dict[str, list[tuple[str, bytes]]]:
    """The files of an archive whose whole name matches one of the patterns (name, content),
    per pattern. Nothing is unpacked by the name an archive gives."""
    found: dict[str, list[tuple[str, bytes]]] = {pattern: [] for pattern in patterns}

    def take(name: str, read: Callable[[], bytes]) -> None:
        for pattern in patterns:
            if re.fullmatch(pattern, name):
                found[pattern].append((name, read()))

    try:
        if tool.filename.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for info in archive.infolist():
                    if not info.is_dir():
                        take(info.filename, lambda info=info: archive.read(info))
        elif tool.filename.endswith(".tar.xz"):
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as archive:
                for member in archive:  # one pass: the stream cannot be read twice cheaply
                    if member.isfile() and (stream := archive.extractfile(member)) is not None:
                        take(member.name, stream.read)
        else:
            raise FetchError(f"{tool.filename} is neither a .zip nor a .tar.xz")
    except (zipfile.BadZipFile, tarfile.TarError, lzma.LZMAError, EOFError) as exc:
        raise FetchError(f"{tool.filename} is not an archive that can be read: {exc}") from exc
    return found


def _one(tool: Tool, pattern: str, matches: list[tuple[str, bytes]]) -> bytes:
    if len(matches) != 1:
        names = ", ".join(name for name, _ in matches[:4]) or "none"
        raise FetchError(
            f"{tool.filename} has {len(matches)} files that match {pattern} ({names}); "
            "the table of fetch_tools.py expects one"
        )
    return matches[0][1]


def _take(tool: Tool, data: bytes) -> tuple[bytes, dict[str, bytes]]:
    """Check one download. Returns the program and the licence texts to take along (by the
    name they are saved under). Raises FetchError."""
    _check(f"{tool.program} {tool.version} ({tool.filename})", data, tool.sha256)
    texts: dict[str, bytes] = {}
    if tool.member:
        patterns = [tool.member, *(text.member for text in tool.archive_texts)]
        found = _members(tool, data, patterns)
        program = _one(tool, tool.member, found[tool.member])
        for text in tool.archive_texts:
            content = _one(tool, text.member, found[text.member])
            missing = [p for p in text.phrases if p.encode() not in content]
            if missing:
                raise FetchError(
                    f"{text.name} of {tool.program} {tool.version} does not say {missing[0]!r}: "
                    f"it is not the licence {tool.licence} that the table names"
                )
            texts[text.name] = content
    else:
        program = data
    if not program.startswith(tool.magic):
        raise FetchError(
            f"{tool.file} of {tool.program} {tool.version} is not a program for {tool.platform}"
        )
    absent = [mark.decode() for mark in tool.marks if mark not in program]
    if absent:
        raise FetchError(
            f"{tool.file} of {tool.program} {tool.version} was not built with {absent[0]}: "
            f"it is not the {tool.licence} build the licence note describes"
        )
    present = [mark.decode() for mark in tool.forbidden if mark in program]
    if present:
        raise FetchError(
            f"{tool.file} of {tool.program} {tool.version} was built with {present[0]}, "
            "which cannot be passed on"
        )
    return program, texts


def _sources(tools: list[Tool], digests: dict[str, str]) -> str:
    lines = ["Programs that come with Libre Panel, in this folder (see also NOTICE.md).", ""]
    for tool in tools:
        lines += [
            tool.file,
            f"  {tool.program} {tool.version}, {tool.licence}",
            f"  taken from {tool.url}",
            f"  SHA-256 of the download: {tool.sha256}",
            "  Source code:",
            *(f"    {line}" for line in tool.source),
            "",
        ]
    lines += ["SHA-256 of the files in this folder:"]
    lines += [f"{digests[tool.file]}  {tool.file}" for tool in tools]
    return "\n".join(lines) + "\n"


def fetch(
    folder: Path = DEFAULT_FOLDER,
    platform: str = "windows",
    fetch_file: Callable[[Tool], bytes] | None = None,
    tools: tuple[Tool, ...] = TOOLS,
    licence_texts: Path = LICENSES,
) -> list[str]:
    """Put the programs of ``platform`` into ``folder``, replacing what is there. Returns
    lines that say what was taken (file, program, licence, SHA-256). Raises FetchError."""
    if platform not in PLATFORMS:
        raise FetchError(f"unknown platform {platform!r}: {', '.join(PLATFORMS)}")
    chosen = [tool for tool in tools if tool.platform == platform]
    if not chosen:
        raise FetchError(f"the table has no program for {platform}")
    get = fetch_file or (lambda tool: download(tool.url))
    folder = Path(folder)
    folder.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{folder.name}-", dir=folder.parent))
    work.chmod(0o755)  # mkdtemp makes it private; this is for the build, and to be read
    try:
        (work / "licenses").mkdir()
        digests: dict[str, str] = {}
        lines = []
        for tool in chosen:
            program, texts = _take(tool, get(tool))
            (work / tool.file).write_bytes(program)
            (work / tool.file).chmod(0o755)
            digests[tool.file] = sha256(program)
            for name in tool.texts:
                source = licence_texts / name
                if not source.is_file():
                    raise FetchError(f"{source} is missing: {tool.program} cannot go without it")
                shutil.copyfile(source, work / "licenses" / name)
            for name, content in texts.items():
                (work / "licenses" / name).write_bytes(content)
            lines.append(
                f"{tool.file:<18} {tool.program} {tool.version}, {tool.licence}, "
                f"sha256 {digests[tool.file]}"
            )
        (work / "SOURCES.txt").write_text(_sources(chosen, digests), "utf-8")
        if folder.exists():
            shutil.rmtree(folder)
        work.rename(folder)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return lines


def pins(
    fetch_file: Callable[[Tool], bytes] | None = None, tools: tuple[Tool, ...] = TOOLS
) -> list[str]:
    """The SHA-256 of what each address of the table gives, to be written into it when it
    moves to other versions."""
    get = fetch_file or (lambda tool: download(tool.url))
    return [
        f"{tool.file} ({tool.platform}) {tool.url}\n    sha256 {sha256(get(tool))}"
        for tool in tools
    ]


def _from_folder(archive_dir: Path) -> Callable[[Tool], bytes]:
    def read(tool: Tool) -> bytes:
        path = archive_dir / tool.filename
        try:
            return path.read_bytes()
        except OSError as exc:
            raise FetchError(f"{path} cannot be read: {exc}") from exc

    return read


def default_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    raise FetchError(f"no downloads are built on {sys.platform}: use --platform")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", nargs="?", type=Path, default=DEFAULT_FOLDER)
    parser.add_argument("--platform", choices=PLATFORMS, help="of the download (default: this)")
    parser.add_argument("--archive-dir", type=Path, help="read the files from here, not the net")
    parser.add_argument(
        "--print-pins", action="store_true", help="print the hashes of the downloads, nothing else"
    )
    args = parser.parse_args(argv)
    source = _from_folder(args.archive_dir) if args.archive_dir else None
    try:
        if args.print_pins:
            print("\n".join(pins(source, TOOLS)))
            return 0
        platform = args.platform or default_platform()
        lines = fetch(args.folder, platform, source, TOOLS, LICENSES)
    except (FetchError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{len(lines)} programs in {args.folder}:")
    print("\n".join(f"  {line}" for line in lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
