"""Names of the processor, graphics card and memory, tidied up for a panel.

A system names its parts for a spec sheet ("12th Gen Intel(R) Core(TM) i7-12700K"); a
heading on a panel has room for the part that tells them apart ("Core i7-12700K"). One
set of rules serves every source of names (the system itself, LibreHardwareMonitor), so
a name reads the same whichever of them it came from.
"""

from __future__ import annotations

import re

# "(R)", "(TM)" and the like: "Intel(R) Core(TM)2 Duo" -> "Intel Core2 Duo"
_MARKS = re.compile(r"\((?:R|TM|C)\)|[®™©]", re.IGNORECASE)

# What a processor's name loses, in this order (each a regular expression, cut out):
_CPU_CUTS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\s*@?\s*\b\d+(?:[.,]\d+)?\s*[GM]Hz\b",  # the clock: "@ 3.60GHz"
        r"\s+(?:with|w/)\s+(?:AMD\s+)?(?:Radeon|Vega)\b.*$",  # "with Radeon Graphics"
        r"\s*\b(?:\d+|[A-Za-z]+)-Cores?\b(?:\s+Processor)?",  # "6-Core Processor", "Quad-Core"
        r"^\s*\d+(?:st|nd|rd|th)\s+Gen\b",  # "12th Gen"
        r"\b(?:CPU|Processor)\b",  # a heading that says CPU needs no CPU
    )
)
# The maker in front: "AMD Ryzen 5 7600" is a "Ryzen 5 7600" under a CPU heading.
_VENDOR = re.compile(
    r"^(?:AMD|Intel|NVIDIA|ATI|Advanced\s+Micro\s+Devices)\b[,.]?\s*"
    r"(?:Inc\.?|Corporation|Corp\.?)?",
    re.IGNORECASE,
)
_GENUINE = re.compile(r"^Genuine\s+", re.IGNORECASE)  # "Genuine Intel(R) CPU 0000"
# A name that is left with only these says nothing: keep the maker then ("Intel Graphics").
_GENERIC = {"", "cpu", "gpu", "graphics", "processor", "adapter", "device"}


def _plain(text: str) -> str:
    """One line, single spaces, no loose dashes or commas at the ends."""
    return " ".join(text.split()).strip(" -,;:/")


def _tidy(raw: str, cuts: tuple[re.Pattern[str], ...]) -> str:
    named = _plain(_GENUINE.sub("", _MARKS.sub("", raw)))
    text = named
    for cut in cuts:
        text = cut.sub(" ", text)
    text = _plain(_VENDOR.sub("", _plain(text)))
    if text.lower() in _GENERIC or not any(char.isalpha() for char in text):
        return named
    return text


def tidy_cpu(raw: str) -> str:
    """A processor's name for a panel: "AMD Ryzen 5 7600 6-Core Processor" becomes "Ryzen 5
    7600". The maker, trademark marks, "CPU", the clock, the number of cores, the generation
    ("12th Gen") and "with Radeon Graphics" go."""
    return _tidy(raw, _CPU_CUTS)


def tidy_gpu(raw: str) -> str:
    """A graphics card's name for a panel: "NVIDIA GeForce RTX 4070" becomes "GeForce RTX 4070"
    (the maker and trademark marks go)."""
    return _tidy(raw, ())


# -- memory --------------------------------------------------------------------------------

# Makers whose name is not what their modules say: "Hynix Semiconductor" is "SK hynix".
# Compared on letters and digits only ("G.Skill Intl", "G-Skill" and "GSKILL" are one); any
# other maker is the firmware's name without the words that close a company's name.
_MAKERS = {
    "adata": "ADATA",
    "geil": "GeIL",
    "gskill": "G.Skill",
    "hynix": "SK hynix",
    "skhynix": "SK hynix",
    "team": "TeamGroup",
}
# What firmware writes where it does not know the maker.
_UNKNOWN_MAKERS = {
    "", "unknown", "undefined", "not specified", "none", "n/a", "na", "default string",
    "to be filled by o.e.m.", "manufacturer", "manufacturer0", "oem", "bad index",
    "[empty]", "empty", "invalid", "0",
}  # fmt: skip
# "Kingston Technology Company, Inc." is "Kingston": these words close a company's name.
_COMPANY = re.compile(
    r"(?:[\s,.]+(?:technology|technologies|corporation|corp|company|co|inc|ltd|limited|"
    r"gmbh|semiconductor|semiconductors|electronics|international|intl|group|memory|"
    r"modules?|information|usa))+[\s,.]*$",
    re.IGNORECASE,
)


def tidy_maker(raw: str) -> str:
    """Who made a memory module, shortened: "Kingston Technology" -> "Kingston". Empty
    where the firmware does not know (or only has a code, as "859B")."""
    text = _plain(raw)
    if text.lower() in _UNKNOWN_MAKERS or re.fullmatch(r"(?:0x)?[0-9a-f]{4}", text, re.I):
        return ""
    letters = re.sub(r"[^a-z0-9]", "", text.lower())
    for prefix, name in _MAKERS.items():
        if letters.startswith(prefix):
            return name
    text = _plain(_COMPANY.sub("", re.sub(r"\s*\([^)]*\)$", "", text)))  # "Patriot Memory (PDP)"
    if text.isupper() and len(text) > 4:  # "CORSAIR" -> "Corsair"; "ADATA", "PNY" stay
        text = text.title()
    return text


# What the memory is worth saying, in sizes people sell it in. A computer sees a little less
# than it has (firmware and a graphics chip that shares it keep some).
_SIZES_GB = (
    1, 2, 3, 4, 6, 8, 12, 16, 20, 24, 32, 36, 40, 48, 64, 80, 96, 128, 192, 256, 384, 512, 768,
    1024, 1536, 2048, 3072, 4096,
)  # fmt: skip


def installed_gb(visible_bytes: int) -> int:
    """The memory a computer has, from the part of it that the system leaves to programs:
    15.4 GiB visible is a 16 GB machine, 25 GiB is 25."""
    gib = visible_bytes / 2**30
    for size in _SIZES_GB:
        if gib <= size:
            return size if gib >= size * 0.9 else round(gib)
    return round(gib)


def size_label(gigabytes: float) -> str:
    """A size as people say it: "32 GB", "1.5 GB"."""
    return f"{round(gigabytes, 1):g} GB"


def memory_name(maker: str, kind: str, speed: int, gigabytes: float) -> str:
    """The memory's name, "Kingston DDR5-5600 32 GB"; what is not known is left out."""
    memory = f"{kind}-{speed}" if kind and speed else kind
    return " ".join(part for part in (maker, memory, size_label(gigabytes)) if part)
