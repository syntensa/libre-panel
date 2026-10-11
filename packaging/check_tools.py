"""Check the programs a download carries, where they are: the folder PyInstaller built, the
folder a setup installed, the folder inside an unpacked AppImage.

    python packaging/check_tools.py PROGRAM_FOLDER

PROGRAM_FOLDER is the one with ``libre-panel`` in it; its ``tools`` folder is checked:

  * every file that ``tools/SOURCES.txt`` lists is there and is the one that was fetched
    (its SHA-256);
  * the app finds them there: with the program's executable as ``sys.executable``, the functions
    of libre_panel that look for ffmpeg and PresentMon answer with the files of this folder, and
    not with what the PATH holds, and the one that hands out PawnIO's installer (which checks
    it against the SHA-256 the app has pinned) accepts the file;
  * ffmpeg lists libx264 among its encoders, and encodes pictures the way the video mode asks
    (the command is the app's own, libre_panel.devices.h264), to a stream that the video mode
    cuts into as many pictures, with the parameter sets it needs;
  * PresentMon (Windows) starts and says its version;
  * PawnIO's installer (Windows) is there. It is not run: it installs a driver.

Needs Libre Panel importable (CI has installed it) and nothing else.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from unittest import mock

TOOLS_FOLDER = "tools"  # libre_panel.bundled.TOOLS_FOLDER
LISTED = re.compile(r"^([0-9a-f]{64})  (\S+)$", re.MULTILINE)
FRAMES = 8
SIZE = (192, 48)  # small, and even: the video mode rotates the bars' 1920x480 the same way


class CheckError(Exception):
    """A program is missing or does not do what it should; the message says what."""


def listed_files(tools: Path) -> dict[str, str]:
    """The SHA-256 of each file that SOURCES.txt lists (file name -> hash)."""
    sources = tools / "SOURCES.txt"
    try:
        text = sources.read_text("utf-8")
    except OSError as exc:
        raise CheckError(f"{sources} cannot be read: {exc}") from exc
    files = {name: digest for digest, name in LISTED.findall(text)}
    if not files:
        raise CheckError(f"{sources} lists no file with its SHA-256")
    return files


def check_files(tools: Path) -> list[str]:
    """Every listed file is there as it was fetched. Returns their names."""
    listed = listed_files(tools)
    for name, digest in listed.items():
        path = tools / name
        try:
            got = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise CheckError(f"{path} cannot be read: {exc}") from exc
        if got != digest:
            raise CheckError(f"{path} is not the file that was fetched: SHA-256 {got}")
        print(f"ok: {name} is the file that was fetched")
    return list(listed)


def run(command: list[str], **options) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, timeout=120, **options)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckError(f"{command[0]} did not run: {exc}") from exc


Runner = Callable[..., subprocess.CompletedProcess]  # ``run``, or a stand-in in a test


def check_ffmpeg(ffmpeg: Path, run: Runner = run) -> None:
    """ffmpeg knows libx264 and encodes like the video mode asks."""
    from libre_panel.devices.h264 import (
        EncoderSettings,
        PictureSplitter,
        ffmpeg_command,
        nal_units,
    )

    banner = run([str(ffmpeg), "-hide_banner", "-version"], text=True).stdout.splitlines()
    encoders = run([str(ffmpeg), "-hide_banner", "-encoders"], text=True)
    if not re.search(r"^\s*V\S*\s+libx264\s", encoders.stdout, re.MULTILINE):
        raise CheckError(f"{ffmpeg} does not list libx264 among its encoders")
    print(f"ok: {banner[0] if banner else ffmpeg} lists libx264")

    command = ffmpeg_command(str(ffmpeg), SIZE, 270, EncoderSettings(fps=25, keyframe_s=1))
    frame = bytes(range(256)) * (SIZE[0] * SIZE[1] * 3 // 256)  # a picture of some detail
    done = run(command, input=frame * FRAMES)
    if done.returncode != 0 or not done.stdout:
        error = done.stderr.decode("utf-8", "replace").strip()
        raise CheckError(f"{ffmpeg} did not encode: exit code {done.returncode} {error}")
    splitter = PictureSplitter()
    pictures = splitter.feed(done.stdout) + splitter.flush()
    kinds = [kind for _, kind in nal_units(done.stdout)]
    keyframes = kinds.count(5)
    if (
        len(pictures) != FRAMES
        or splitter.multi_slice
        or not {5, 7, 8} <= set(kinds)
        or keyframes != 1  # the keyframe interval (25 pictures) asked for is kept
    ):
        raise CheckError(
            f"{ffmpeg} encoded {len(pictures)} pictures of {FRAMES} frames, {keyframes} of them "
            f"keyframes (NAL units {sorted(set(kinds))}, several slices: {splitter.multi_slice})"
        )
    print(f"ok: {ffmpeg.name} encodes {FRAMES} frames to {len(done.stdout)} bytes of H.264")


def check_presentmon(presentmon: Path, run: Runner = run) -> None:
    """PresentMon starts and says its version (its help ends with exit code 1, by design)."""
    done = run([str(presentmon), "--help"], text=True, errors="replace")
    shown = f"{done.stdout}\n{done.stderr}"
    version = re.search(r"PresentMon (\d+\.\d+\.\d+)", shown)
    if version is None:
        raise CheckError(f"{presentmon} said no version: exit code {done.returncode} {shown[:200]}")
    print(f"ok: PresentMon {version.group(1)} starts")


def check_discovery(program: Path, tools: Path, names: list[str]) -> None:
    """The app, started from this build, looks for its programs in this folder first."""
    from libre_panel.bundled import pawnio_installer
    from libre_panel.devices.base import DeviceError
    from libre_panel.devices.h264 import find_ffmpeg
    from libre_panel.sensors.presentmon import find_presentmon

    executables = ("LibrePanel.exe", "libre-panel.exe", "libre-panel")
    exe = next((program / name for name in executables if (program / name).is_file()), None)
    if exe is None:
        raise CheckError(f"{program} has no program of Libre Panel: {', '.join(executables)}")
    asked = {
        "ffmpeg": find_ffmpeg,
        "ffmpeg.exe": find_ffmpeg,
        "PresentMon.exe": find_presentmon,
        "PawnIO_setup.exe": pawnio_installer,
    }
    with (
        mock.patch.object(sys, "frozen", True, create=True),
        mock.patch.object(sys, "executable", str(exe)),
    ):
        for name in names:
            if name not in asked:
                continue
            try:
                found = asked[name]()
            except DeviceError as exc:  # nothing anywhere
                raise CheckError(
                    f"started from {exe.name}, the app finds no {name}: {exc}"
                ) from exc
            if found is None or Path(found).resolve() != (tools / name).resolve():
                raise CheckError(f"started from {exe.name}, the app finds {found} and not {name}")
            print(f"ok: started from {exe.name}, the app finds {name} in {TOOLS_FOLDER}")


def check(program: Path, run: Runner = run) -> None:
    tools = program / TOOLS_FOLDER
    if not tools.is_dir():
        raise CheckError(f"{program} has no {TOOLS_FOLDER} folder")
    names = check_files(tools)
    if not {"ffmpeg", "ffmpeg.exe"} & set(names):
        raise CheckError(f"{tools} has no ffmpeg: the video mode would need one of its own")
    check_discovery(program, tools, names)
    for name in names:
        if name in ("ffmpeg", "ffmpeg.exe"):
            check_ffmpeg(tools / name, run)
        elif name == "PresentMon.exe":
            check_presentmon(tools / name, run)


def main(argv: list[str] | None = None, run: Runner = run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("program", type=Path, help="the folder with libre-panel in it")
    args = parser.parse_args(argv)
    try:
        check(args.program, run)
    except CheckError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
