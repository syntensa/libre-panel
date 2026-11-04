"""One design for the built-in themes: the looks, and the look each family of themes wears.

A look is a palette of the roles the modules paint with (``ROLES`` in theme.modules) and the
way its cards are made (``style``: corners, glow, the screen behind them, the fonts). The
editor offers the looks for a new theme; the modules of a theme without colours of its own
take the default one.

The built-in themes hold their look's colours and cards in their theme.json, as every theme
does (a theme stands on its own: a copy, a share or an edit of it keeps them).
``FAMILIES`` says which look each family wears and what it has of its own: tools/design.py
writes them into the themes, and tests/test_design.py checks that every built-in theme holds
its family's. A colour changed here and the tool run, every theme that wears it follows.

The app draws a few pictures itself (the start screens, its icon, a toast's card where a
theme has no colour for it, the editor's building blocks): they are in the Libre look.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# How the cards are made where a look or a theme does not say.
STYLE_DEFAULTS: dict[str, Any] = {
    "card": "glass",  # glass | flat | outline | none
    "radius": 18,  # corner radius of a card 200 px tall
    "glow": 0.6,  # 0 = none, 1 = strong
    "backdrop": "gradient",  # gradient | flat: the screen behind the modules
    "display_font": "builtin:Barlow-SemiBold",  # numbers and the clock
    "text_font": "builtin:Barlow-Medium",  # labels
    # how much of the background the cards cover (1: all; config.toml card_opacity for every
    # theme): their surface, not what is on them
    "card_opacity": 1.0,
}

# Type and room in a module, by the shorter side of its box (``u``): the share of it, and the
# least and the most in px. Every size of type in a module is a multiple of its labels'.
MODULE_LABEL = (0.075, 9, 22)  # its small labels: 15 px in a module 200 px high
MODULE_PADDING = (0.1, 6, 26)  # between its card's edge and what is on it
MODULE_PADDING_BARE = (0.04, 0, 1 << 16)  # the same without a card

LOOKS: dict[str, dict[str, Any]] = {
    "arctic": {  # SPUR II's: Arena, Agents, Studio, the round report, the SPUR screens
        "name": "Arctic",
        "palette": {
            "bg": "#0A1119", "bg2": "#060A0F", "surface": "#14202E", "line": "#1F3147",
            "track": "#162332", "text": "#EEF6FF", "text2": "#B8CAE0", "text3": "#8CA0B8",
            "accent": "#13E5D7", "cpu": "#13E5D7", "gpu": "#A3AEFF", "mem": "#E0A8FF",
            "disk": "#8FD3FF", "net": "#7EE8A6", "warn": "#FFCB6B", "crit": "#FF7A85",
        },
        "style": {"card": "glass", "radius": 18, "glow": 0.6},
    },
    "libre": {  # the app's own: Lumen, Encore, Almanac, Nimbus, Terra, Libre Default
        "name": "Libre",
        "palette": {
            "bg": "#0A0E13", "bg2": "#06090D", "surface": "#0D1219", "line": "#1F2A36",
            "track": "#18212C", "text": "#E8EEF4", "text2": "#C9D3DE", "text3": "#93A1B2",
            "dim": "#5F6E80", "accent": "#2EE6D6", "cpu": "#2EE6D6", "gpu": "#7F9CFF",
            "mem": "#C792FF", "disk": "#5FB6FF", "net": "#34D399", "warn": "#F5C46B",
            "crit": "#F87171",
        },
        "style": {"card": "glass", "radius": 14, "glow": 0.6, "backdrop": "flat"},
    },
    "neon": {
        "name": "Neon",
        "palette": {
            "bg": "#07060F", "bg2": "#020107", "surface": "#110F26", "line": "#2D2760",
            "track": "#1B1838", "text": "#F7F5FF", "text2": "#CBC4F5", "text3": "#9089C6",
            "accent": "#FF4FD8", "cpu": "#00F0FF", "gpu": "#FF4FD8", "mem": "#B26BFF",
            "disk": "#FFE45C", "net": "#4CFF9A", "warn": "#FFB020", "crit": "#FF3B5C",
        },
        "style": {"card": "outline", "radius": 14, "glow": 1.0},
    },
    "graphite": {
        "name": "Graphite",
        "palette": {
            "bg": "#141516", "bg2": "#0C0D0E", "surface": "#1E2023", "line": "#2C2F33",
            "track": "#2A2D31", "text": "#F2F2F2", "text2": "#C6C9CE", "text3": "#8D9298",
            "accent": "#FF8A3D", "cpu": "#FF8A3D", "gpu": "#5EB1FF", "mem": "#C792EA",
            "disk": "#9CCC65", "net": "#4DD0E1", "warn": "#FFC857", "crit": "#FF5C5C",
        },
        "style": {"card": "flat", "radius": 10, "glow": 0.15},
    },
    "paper": {
        "name": "Paper",
        "palette": {
            "bg": "#E9EEF3", "bg2": "#DCE3EA", "surface": "#FFFFFF", "line": "#D3DBE3",
            "track": "#E2E8EF", "text": "#15212C", "text2": "#3E4C5A", "text3": "#687887",
            "accent": "#0B8C84", "cpu": "#0B8C84", "gpu": "#4B57C9", "mem": "#9B4FC4",
            "disk": "#2F7FB8", "net": "#2E9E5B", "warn": "#C98200", "crit": "#D6434E",
        },
        "style": {"card": "flat", "radius": 16, "glow": 0.0},
    },
    "sunset": {
        "name": "Sunset",
        "palette": {
            "bg": "#170D16", "bg2": "#0B0609", "surface": "#24151F", "line": "#3D2434",
            "track": "#2F1B28", "text": "#FFF2EB", "text2": "#F2CBBA", "text3": "#B98F82",
            "accent": "#FF7E5F", "cpu": "#FF7E5F", "gpu": "#FEB47B", "mem": "#EC6FAA",
            "disk": "#FFD27A", "net": "#7FD8BE", "warn": "#FFC857", "crit": "#FF4E6A",
        },
        "style": {"card": "glass", "radius": 20, "glow": 0.5},
    },
    "mono": {
        "name": "Mono",
        "palette": {
            "bg": "#000000", "bg2": "#000000", "surface": "#0B0B0B", "line": "#2A2A2A",
            "track": "#1E1E1E", "text": "#FFFFFF", "text2": "#C2C2C2", "text3": "#7D7D7D",
            "accent": "#FFFFFF", "cpu": "#FFFFFF", "gpu": "#FFFFFF", "mem": "#FFFFFF",
            "disk": "#FFFFFF", "net": "#FFFFFF", "warn": "#FFC400", "crit": "#FF3D3D",
        },
        "style": {
            "card": "outline", "radius": 4, "glow": 0.0, "backdrop": "flat",
            "display_font": "builtin:JetBrainsMono-Bold",
            "text_font": "builtin:JetBrainsMono-Medium",
        },
    },
    "contrast": {  # High Contrast: black, white and strong colours, for reading from afar
        "name": "High Contrast",
        "palette": {
            "bg": "#000000", "bg2": "#000000", "surface": "#000000", "line": "#FFFFFF",
            "track": "#3A3A3A", "text": "#FFFFFF", "text2": "#F2F2F2", "text3": "#E6E6E6",
            "dim": "#BDBDBD", "accent": "#FFD60A", "cpu": "#FFD60A", "gpu": "#00E5FF",
            "mem": "#FFFFFF", "disk": "#FFFFFF", "net": "#00FF85", "warn": "#FF9F0A",
            "crit": "#FF453A",
        },
        "style": {
            "card": "outline", "radius": 8, "glow": 0.0, "backdrop": "flat",
            "display_font": "builtin:Barlow-Bold", "text_font": "builtin:Barlow-SemiBold",
        },
    },
}  # fmt: skip
DEFAULT_LOOK = "arctic"

# Other names themes give a look's roles (SPUR II's), which follow the role.
ALIASES = {
    "base": "bg", "base_low": "bg2", "surf_1": "surface", "tx_1": "text", "tx_2": "text2",
    "tx_3": "text3", "krit": "crit",
}  # fmt: skip

# The colours of the editor's building blocks (editor.presets, ``$name``) where the theme
# they go into has none of that name: the Libre look's, and a few of their own.
BLOCK_COLOURS = {
    "accent": LOOKS["libre"]["palette"]["accent"],
    "accent_deep": "#0F6B64",
    "text": LOOKS["libre"]["palette"]["text"],
    "muted": LOOKS["libre"]["palette"]["text3"],
    "dim": LOOKS["libre"]["palette"]["dim"],
    "card": "#111822",
    "card2": "#0C1219",
    "edge": LOOKS["libre"]["palette"]["line"],
    "good": LOOKS["libre"]["palette"]["net"],
    "up": "#F472B6",
    "warn": LOOKS["libre"]["palette"]["warn"],
    "crit": LOOKS["libre"]["palette"]["crit"],
}


@dataclass(frozen=True)
class Family:
    """The look a family of built-in themes wears, the colours it has of its own over the
    look's, and its cards over the look's."""

    look: str
    colours: dict[str, str] = field(default_factory=dict)
    cards: dict[str, Any] = field(default_factory=dict)
    why: str = ""

    def palette(self) -> dict[str, str]:
        return {**LOOKS[self.look]["palette"], **self.colours}

    def style(self) -> dict[str, Any]:
        """The cards in full, as a theme of the family is drawn with them."""
        return {**STYLE_DEFAULTS, **LOOKS[self.look]["style"], **self.cards}


_STUDIO = {"line": "#32475E", "disk": "#7EE8A6"}  # Studio's frames and its green disk

# Each built-in theme belongs to the family its name begins with (the longest that fits):
# "lumen-1920x720" to "lumen", "almanac-analog" to "almanac".
FAMILIES: dict[str, Family] = {
    "lumen": Family("libre", cards={"card_opacity": 0.7}),
    "almanac": Family("libre", cards={"card_opacity": 0.7}),
    "nimbus": Family("libre", cards={"card_opacity": 0.7}),
    "encore": Family("libre", cards={"card_opacity": 0.9}),
    "terra": Family("libre", colours={"accent": "#5FC8FF"}, cards={"card_opacity": 0.6}),
    "libre-default": Family("libre", cards={"card": "flat", "radius": 12, "glow": 0.5}),
    "column": Family("libre"),
    "orbit": Family("libre"),
    "slate": Family("libre"),
    "pico": Family("libre"),
    "arena": Family("arctic", cards={"card_opacity": 0.8}),
    "round-report": Family(
        "arctic", cards={"card_opacity": 0.8}, why="it follows a game on Arena: in its design"
    ),
    "agents": Family("arctic", cards={"card_opacity": 0.85}),
    "studio": Family("arctic", _STUDIO, {"radius": 12, "backdrop": "flat", "card_opacity": 0.85}),
    "contrast": Family("contrast"),
}


def family_of(theme: str) -> tuple[str, Family] | None:
    """The family a built-in theme belongs to, by its name (None: none)."""
    fits = [name for name in FAMILIES if theme == name or theme.startswith(name + "-")]
    if not fits:
        return None
    name = max(fits, key=len)
    return name, FAMILIES[name]


def look_colour(look: str, role: str) -> str:
    """A role's colour in ``look`` ("#RRGGBB")."""
    return LOOKS[look]["palette"][role]


def rgb(colour: str) -> tuple[int, int, int]:
    """ "#RRGGBB" as (r, g, b)."""
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)
