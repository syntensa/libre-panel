"""Put Libre Panel's own engine, as the build made it, into the folder of the download's tools.

    python packaging/add_engine.py FOLDER ENGINE --x264 VERSION --x264-source LINE [...]
        [--cargo-target TARGET]

FOLDER is the one packaging/fetch_tools.py filled (default ``packaging/bundled-tools``); ENGINE
the program cargo built in engine/ (libre-panel-engine, or .exe), with libx264 linked in. It is
copied into FOLDER, said in ``SOURCES.txt`` (what it is, the x264 it carries and where that
one's source is, the SHA-256 of the file, which packaging/check_tools.py checks), and x264's
licence text goes into ``licenses``, with the licences of the Rust crates the engine is built
from (as cargo resolved them for TARGET, the one it was built for: their names, versions and
licence files, :data:`CRATES`). It fails when FOLDER has no SOURCES.txt, or ENGINE is not a
program of the platform's kind.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_FOLDER = HERE / "bundled-tools"
LICENCE = HERE / "tools-licenses" / "x264-COPYING.txt"  # GPL-2.0, x264's own COPYING
DIGESTS = "SHA-256 of the files in this folder:"
MANIFEST = HERE.parent / "engine" / "Cargo.toml"
# The licences of the crates in the engine, as their licences ask of a program built from them.
CRATES = "libre-panel-engine-crates.txt"
LICENCE_FILES = ("license", "licence", "copying", "notice", "unlicense")
MAGIC = {".exe": b"MZ", "": b"\x7fELF"}


class AddError(Exception):
    pass


def crate_notices(manifest: Path = MANIFEST, target: str | None = None) -> str:
    """The Rust crates built into the engine (``cargo metadata``: what it depends on, on
    ``target``, leaving out what only its tests and its build need), each with its version, its
    licence and the text of its licence files; Libre Panel's own engine is not among them."""
    if not target:  # this machine's, as cargo builds for it
        said = subprocess.run(["rustc", "-vV"], check=True, capture_output=True, text=True).stdout
        target = next(line.split(":", 1)[1].strip() for line in said.splitlines()
                      if line.startswith("host:"))  # fmt: skip
    command = ["cargo", "metadata", "--format-version", "1", "--manifest-path", str(manifest),
               "--filter-platform", target]  # fmt: skip
    done = subprocess.run(command, check=True, capture_output=True, text=True)
    meta = json.loads(done.stdout)
    packages = {package["id"]: package for package in meta["packages"]}
    nodes = {node["id"]: node for node in meta["resolve"]["nodes"]}
    built, todo = set(), [meta["resolve"]["root"]]
    while todo:
        for dep in nodes[todo.pop()]["deps"]:
            normal = any(kind.get("kind") is None for kind in dep.get("dep_kinds", []))
            if normal and dep["pkg"] not in built:
                built.add(dep["pkg"])
                todo.append(dep["pkg"])
    parts = [
        "The Rust crates Libre Panel's engine (libre-panel-engine) is built from, with their",
        "licences. The engine itself is GPL-3.0-or-later; its source is engine/ in Libre Panel's",
        "repository, at the commit of this download.",
        "",
    ]
    for package in sorted((packages[p] for p in built), key=lambda p: (p["name"], p["version"])):
        folder = Path(package["manifest_path"]).parent
        licence = package.get("license") or package.get("license_file") or "-"
        parts += ["=" * 78, f"{package['name']} {package['version']}", f"Licence: {licence}"]
        if package.get("repository"):
            parts.append(f"Source: {package['repository']}")
        texts = sorted(
            path for path in folder.iterdir()
            if path.is_file() and path.name.lower().startswith(LICENCE_FILES)
        )  # fmt: skip
        for path in texts:
            parts += ["", f"--- {path.name}", path.read_text("utf-8", "replace").rstrip(), ""]
        if not texts:
            parts += ["", "(no licence file in the crate: its licence is the one named above)", ""]
    return "\n".join(parts) + "\n"


def add(folder: Path, engine: Path, x264: str, source: list[str], crates: str | None = None) -> str:
    """Copy ``engine`` into ``folder`` and say it in SOURCES.txt, and with ``crates`` (the
    crates' notices, :func:`crate_notices`) those into ``licenses``; its line there."""
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
    if crates is not None:
        (folder / "licenses" / CRATES).write_text(crates, "utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    text = sources.read_text("utf-8")
    if DIGESTS not in text:
        raise AddError(f"{sources} does not list its files' SHA-256")
    block = [
        target.name,
        "  Libre Panel's own engine (engine/ in Libre Panel's repository), GPL-3.0-or-later,",
        f"  built for this download, with libx264 {x264} linked in (GPL-2.0-or-later;",
        f"  its licence: licenses/{LICENCE.name})",
        *(
            [f"  The Rust crates it is built from, with their licences: licenses/{CRATES}"]
            if crates is not None
            else []
        ),  # fmt: skip
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
    parser.add_argument("--cargo-target", default=None,
                        help="the target it was built for (else this machine's)")  # fmt: skip
    args = parser.parse_args(argv)
    try:
        crates = crate_notices(MANIFEST, args.cargo_target)
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, StopIteration) as exc:
        print(f"add_engine: the crates' licences: {exc}", file=sys.stderr)
        return 1
    try:
        print(add(args.folder, args.engine, args.x264, args.x264_source, crates))
    except AddError as exc:
        print(f"add_engine: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
