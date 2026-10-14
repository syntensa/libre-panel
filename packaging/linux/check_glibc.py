"""Check that nothing an AppImage carries needs a newer C library (glibc) than ``LIMIT``.

    python3 packaging/linux/check_glibc.py squashfs-root 2.35

An AppImage takes glibc from the system it runs on (it does not carry it), so every program and
library in it must be built against a glibc no newer than the oldest system it is meant for. The
AppImage catalog tests on the oldest Ubuntu LTS still in standard support (22.04: glibc 2.35); a
build on a newer runner asks for symbols that system does not have (GLIBC_2.38, from the Python
of setup-python on Ubuntu 24.04), and the AppImage does not even start there. objdump says which
versions of glibc each file asks for.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

NEEDED = re.compile(r"\bGLIBC_(\d+)\.(\d+)(?:\.(\d+))?\b")


def is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as file:
            return file.read(4) == b"\x7fELF"
    except OSError:
        return False


def needs(path: Path) -> tuple[int, ...]:
    """The newest glibc version ``path`` asks for, () for none: from what it references, not
    from what a library defines itself."""
    out = subprocess.run(["objdump", "-p", str(path)], capture_output=True, text=True).stdout
    _, _, references = out.partition("Version References:")
    versions = [tuple(int(n or 0) for n in m.groups()) for m in NEEDED.finditer(references)]
    return max(versions, default=())


def shown(version: tuple[int, ...]) -> str:
    return ".".join(map(str, version[:2] if version[2:] == (0,) else version)) or "none"


def main(argv: list[str]) -> int:
    root, limit = Path(argv[1]), tuple(int(n) for n in argv[2].split("."))
    found = [(needs(p), p) for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()
             and is_elf(p)]  # fmt: skip
    if not found:
        print(f"no programs or libraries under {root}", file=sys.stderr)
        return 1
    newest, where = max(found)
    print(f"{len(found)} programs and libraries; the newest glibc one asks for: {shown(newest)} "
          f"({where.relative_to(root)})")  # fmt: skip
    over = [(v, p) for v, p in found if v[: len(limit)] > limit]
    for version, path in sorted(over, reverse=True)[:20]:
        print(f"  needs glibc {shown(version)}: {path.relative_to(root)}",
              file=sys.stderr)  # fmt: skip
    if over:
        print(f"{len(over)} file(s) need a glibc newer than {argv[2]}: build on an older system",
              file=sys.stderr)  # fmt: skip
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
