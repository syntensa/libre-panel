"""App clips for tools/readme_media.py: the app in a browser, with a cursor drawn on it.

Placeholder until the recipes are in.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from media_encode import Recorded, Settings


class Unavailable(RuntimeError):
    """The app cannot be recorded here (no Playwright, no Chromium)."""


@dataclass(frozen=True)
class AppClip:
    about: str
    seconds: float
    script: Callable[..., None]
    poster: float = 0.0

    @property
    def kind(self) -> str:
        return "app"


CLIPS: dict[str, AppClip] = {}


def record(name: str, clip: AppClip, settings: Settings, folder: Path) -> Recorded:
    raise Unavailable("app clips are not made yet")
