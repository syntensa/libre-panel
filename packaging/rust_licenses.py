"""The licences of the Rust crates Libre Panel's programs are built from, for their download.

    python packaging/rust_licenses.py [--target TARGET] [-o FILE]

What the programs of the workspace in rust/ (libre-panel, libre-panel-window, libre-panel-tray)
depend on, as cargo resolves it for TARGET (else this machine's), leaving out what only their
tests and their build need and Libre Panel's own crates: each crate with its version, its
licence and the text of its licence files, as their licences ask of a program built from them.
The engine's are packaging/add_engine.py's.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE.parent / "rust" / "Cargo.toml"
PROGRAMS = ("libre-panel", "libre-panel-desktop")  # (the packages of the programs shipped)
LICENCE_FILES = ("license", "licence", "copying", "notice", "unlicense")


def host_target() -> str:
    """This machine's target, as cargo builds for it."""
    said = subprocess.run(["rustc", "-vV"], check=True, capture_output=True, text=True).stdout
    return next(line.split(":", 1)[1].strip() for line in said.splitlines()
                if line.startswith("host:"))  # fmt: skip


def crates(manifest: Path, target: str, programs: tuple[str, ...] = PROGRAMS) -> list[dict]:
    """The crates from elsewhere that `programs` are built from on `target` (cargo metadata:
    their normal dependencies, through Libre Panel's own crates too), by name and version."""
    command = ["cargo", "metadata", "--format-version", "1", "--manifest-path", str(manifest),
               "--filter-platform", target]  # fmt: skip
    meta = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
    packages = {package["id"]: package for package in meta["packages"]}
    nodes = {node["id"]: node for node in meta["resolve"]["nodes"]}
    roots = [pid for pid, package in packages.items() if package["name"] in programs]
    if len(roots) != len(programs):
        raise ValueError(f"not all of {', '.join(programs)} are in {manifest}")
    seen, todo = set(roots), list(roots)
    while todo:
        for dep in nodes[todo.pop()]["deps"]:
            normal = any(kind.get("kind") is None for kind in dep.get("dep_kinds", []))
            if normal and dep["pkg"] not in seen:
                seen.add(dep["pkg"])
                todo.append(dep["pkg"])
    found = [packages[pid] for pid in seen if packages[pid].get("source") is not None]
    return sorted(found, key=lambda package: (package["name"], package["version"]))


def notices(found: list[dict]) -> str:
    """The text of the download's licence file for `found`."""
    parts = [
        "The Rust crates Libre Panel's programs (libre-panel, libre-panel-window and",
        "libre-panel-tray) are built from, with their licences. Libre Panel itself is",
        "GPL-3.0-or-later; its source is rust/ in Libre Panel's repository, at the commit of",
        "this download.",
        "",
    ]
    for package in found:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--target", default=None, help="the target built for (else this one)")
    parser.add_argument("-o", "--output", type=Path, default=None, help="the file (else stdout)")
    args = parser.parse_args(argv)
    try:
        text = notices(crates(MANIFEST, args.target or host_target()))
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, StopIteration) as exc:
        print(f"rust_licenses: {exc}", file=sys.stderr)
        return 1
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
