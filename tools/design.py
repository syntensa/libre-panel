"""Write the design (src/libre_panel/design.py) into the built-in themes, or check them.

    python tools/design.py          # what differs from the design; exit 1 if anything does
    python tools/design.py --write  # make every built-in theme hold its family's look

For each built-in theme, by the family its name begins with (``design.FAMILIES``):

- every colour of its palette that the family's look has a role for (or a name of SPUR II's
  for a role, ``design.ALIASES``) becomes the look's, with the family's own colours over it;
  colours of other names are the theme's own and stay;
- its ``style`` (where it has one: a theme without modules has none) becomes the family's
  cards;
- a background written as the palette's ``bg`` and ``bg2`` in letters becomes ``@bg`` and
  ``@bg2``, so that it follows the palette.

The themes are written as they are kept: JSON, two spaces in, a line at the end.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from libre_panel.design import ALIASES, STYLE_DEFAULTS, family_of  # noqa: E402

THEMES = ROOT / "src" / "libre_panel" / "themes"


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.upper() == b.upper()
    return a == b


def designed(name: str, data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """``data`` (a theme's theme.json) as the design has it, and what differs."""
    found = family_of(name)
    if found is None:
        return data, [f"{name}: of no family in design.FAMILIES"]
    family_name, family = found
    out = json.loads(json.dumps(data))
    changes: list[str] = []
    palette = out.get("palette") or {}
    background = out.get("background") or {}
    for ref in ("bg", "bg2"):  # in letters first, while the palette is as it was
        if ref not in palette:
            continue
        if isinstance(background.get("color"), str) and _same(background["color"], palette[ref]):
            changes.append(f"{name}: background.color {background['color']} -> @{ref}")
            background["color"] = "@" + ref
        gradient = background.get("gradient")
        if isinstance(gradient, list):
            for i, colour in enumerate(gradient):
                if isinstance(colour, str) and _same(colour, palette[ref]):
                    changes.append(f"{name}: background.gradient[{i}] {colour} -> @{ref}")
                    gradient[i] = "@" + ref
    look = family.palette()
    for key, colour in palette.items():
        role = key if key in look else ALIASES.get(key)
        if role in look and not _same(colour, look[role]):
            changes.append(f"{name}: palette.{key} {colour} -> {look[role]} ({family_name})")
            palette[key] = look[role]
    style = out.get("style")
    if isinstance(style, dict):
        cards = family.style()
        for key, value in cards.items():
            if not _same({**STYLE_DEFAULTS, **style}[key], value):
                changes.append(f"{name}: style.{key} {style.get(key)} -> {value}")
                style[key] = value
    return out, changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="write the design into the themes")
    args = parser.parse_args(argv)
    differs = []
    for path in sorted(THEMES.glob("*/theme.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out, changes = designed(path.parent.name, data)
        differs += changes
        if changes and args.write:
            path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for line in differs:
        print(line)
    if args.write:
        print(f"{len(differs)} changes written")
        return 0
    return 1 if differs else 0


if __name__ == "__main__":
    sys.exit(main())
