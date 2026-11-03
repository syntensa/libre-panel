"""Put Libre Panel's own engine, as the build made it, into the folder of the download's tools.

    python packaging/add_engine.py FOLDER ENGINE --x264 VERSION --x264-source LINE [...]

FOLDER is the one packaging/fetch_tools.py filled (default ``packaging/bundled-tools``); ENGINE
the program cargo built in engine/ (libre-panel-engine, or .exe), with libx264 linked in. It is
copied into FOLDER, said in ``SOURCES.txt`` (what it is, the x264 it carries and where that
one's source is, the SHA-256 of the file, which packaging/check_tools.py checks), and x264's
licence text goes into ``licenses``. It fails when FOLDER has no SOURCES.txt, or ENGINE is not
a program of the platform's kind.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_FOLDER = HERE / "bundled-tools"
LICENCE = HERE / "tools-licenses" / "x264-COPYING.txt"  # GPL-2.0, x264's own COPYING
DIGESTS = "SHA-256 of the files in this folder:"
MAGIC = {".exe": b"MZ", "": b"\x7fELF"}


class AddError(Exception):
    pass


def add(folder: Path, engine: Path, x264: str, source: list[str]) -> str:
    """Copy ``engine`` into ``folder`` and say it in SOURCES.txt; its line there."""
    sources = folder / "SOURCES.txt"
    if not sources.is_file():
        raise AddError(f"{sources} is missing: run packaging/fetch_tools.py first")
    magic = MAGIC.get(engine.suffix)
    if magic is None or engine.read_bytes()[: len(magic)] != magic:
        raise AddError(f"{engine} is not a program for this platform")
    target = folder / engine.name
    shutil.copyfile(engine, target)
    target.chmod(0o755)
    shutil.copyfile(LICENCE, folder / "licenses" / LICENCE.name)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    text = sources.read_text("utf-8")
    if DIGESTS not in text:
        raise AddError(f"{sources} does not list its files' SHA-256")
    block = [
        target.name,
        "  Libre Panel's own engine (engine/ in Libre Panel's repository), GPL-3.0-or-later,",
        f"  built for this download, with libx264 {x264} linked in (GPL-2.0-or-later;",
        f"  its licence: licenses/{LICENCE.name})",
        "  Source code:",
        "    Libre Panel: the same commit as this download (engine/)",
        *(f"    {line}" for line in source),
        "",
    ]
    head, tail = text.split(DIGESTS, 1)
    line = f"{digest}  {target.name}"
    listed = DIGESTS + tail.rstrip("\n") + "\n" + line + "\n"
    sources.write_text(head + "\n".join(block) + "\n" + listed, "utf-8")
    return line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", type=Path, nargs="?", default=DEFAULT_FOLDER)
    parser.add_argument("engine", type=Path)
    parser.add_argument("--x264", required=True, help="the version of libx264 linked in")
    parser.add_argument("--x264-source", action="append", required=True,
                        help="where its source code is (one line each)")  # fmt: skip
    args = parser.parse_args(argv)
    try:
        print(add(args.folder, args.engine, args.x264, args.x264_source))
    except AddError as exc:
        print(f"add_engine: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
