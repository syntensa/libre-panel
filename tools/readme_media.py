"""Make the animated pictures of the README, the website and forum posts, from the real thing.

    python tools/readme_media.py [OUT] [--only NAME ...] [--gif-only] [--list]

Each clip is a named recipe: the panel in video mode (the real main loop, frame by frame on a
clock of its own, so a clip is the same on every run) or the app in a browser (Playwright, a
cursor drawn on it). For each, a GIF for the README and forums (960 px wide, 25 frames a
second, under 4 MB, never over 8 MB), an MP4 and a WebM for the website's ``<video autoplay
loop muted playsinline>`` (the GIF is its fallback) and a poster PNG. ``--gif-only`` makes only
the GIFs. OUT is docs/media unless given; ``--list`` says what there is. Needs ffmpeg for the
videos; without it Pillow writes the GIFs.

Which theme a panel clip shows is a parameter: ``--theme volume-band=NAME`` (two names, comma
between, for a clip that goes from one theme to another). A theme is a built-in or user theme,
a folder with a theme in it, or one of the layouts made of modules in tools/media_panel.py.
Clips of the panel are in the device frame of tools/readme_images.py; ``--bare`` leaves it out.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import media_app  # noqa: E402
import media_encode as encode  # noqa: E402
import media_panel  # noqa: E402

Clip = media_panel.PanelClip | media_app.AppClip


def clips() -> dict[str, Clip]:
    """Every recipe, by name."""
    return {**media_panel.CLIPS, **media_app.CLIPS}


def parse_themes(given: list[str], known: dict[str, Clip]) -> dict[str, tuple[str, ...]]:
    """``["video-mode=studio", "theme-tube=studio,layout-bar"]`` as {clip: themes}."""
    found: dict[str, tuple[str, ...]] = {}
    for item in given:
        name, _, themes = item.partition("=")
        clip = known.get(name)
        if clip is None or not themes:
            raise SystemExit(
                f"--theme wants CLIP=THEME[,THEME], with a clip of: {', '.join(known)}"
            )
        if not isinstance(clip, media_panel.PanelClip):
            raise SystemExit(f"--theme: {name} is a clip of the app, which has no theme to choose")
        found[name] = tuple(t.strip() for t in themes.split(",") if t.strip())
        if len(found[name]) != len(clip.themes):
            raise SystemExit(
                f"--theme: {name} shows {len(clip.themes)} theme(s), not {len(found[name])}"
            )
    return found


def describe(known: dict[str, Clip]) -> str:
    """The table ``--list`` prints."""
    lines = []
    for name, clip in known.items():
        shows = ", ".join(clip.themes) if isinstance(clip, media_panel.PanelClip) else "the app"
        lines.append(f"{name:18s} {clip.kind:5s} {clip.seconds:4.1f} s  {clip.about}")
        lines.append(f"{'':18s} shows: {shows}")
    return "\n".join(lines)


def record(name: str, clip: Clip, settings: encode.Settings, folder: Path,
           themes: tuple[str, ...] | None) -> encode.Recorded:  # fmt: skip
    if isinstance(clip, media_panel.PanelClip):
        return media_panel.record(name, clip, settings, folder, themes)
    return media_app.record(name, clip, settings, folder)


def publish(
    name: str, recorded: encode.Recorded, out: Path, settings: encode.Settings
) -> list[tuple[Path, str]]:
    """The files of one clip in ``out``, with a line about each. Raises
    :class:`~media_encode.GifTooLarge`; a missing ffmpeg is told, not raised."""
    frames, files = recorded.frames, []
    gif = encode.write_gif(frames, out / f"{name}.gif", settings)
    colours = f"{gif.look.colors} colours" if gif.look.colors < 256 else "256 colours"
    note = "" if gif.within_target else f", OVER the {encode.human(settings.target)} target"
    files.append((gif.path, f"{gif.fps} frames a second, {colours}{note}"))
    if settings.gif_only:
        return files
    files.append((encode.write_poster(frames, recorded.poster, out / f"{name}.png"), "poster"))
    if encode.find_ffmpeg(settings.ffmpeg) is None:
        files.append((out / f"{name}.mp4", "NOT made: MP4 and WebM need ffmpeg"))
        return files
    files.append(
        (encode.write_mp4(frames, out / f"{name}.mp4", settings), f"{frames.fps} frames a second")
    )
    files.append(
        (encode.write_webm(frames, out / f"{name}.webm", settings), f"{frames.fps} frames a second")
    )
    return files


def make(
    names: list[str],
    out: Path,
    settings: encode.Settings,
    themes: dict[str, tuple[str, ...]] | None = None,
    keep: Path | None = None,
    say=print,  # noqa: ANN001
) -> int:
    """Record and publish the clips ``names`` into ``out``; returns the program's exit status
    (1 if a clip failed: a GIF too large for a forum, an app clip that could not run)."""
    known, themes, failed = clips(), themes or {}, 0
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="readme-media-frames-") as scratch:
        for name in names:
            clip = known[name]
            folder = (keep or Path(scratch)) / name
            if folder.exists():
                shutil.rmtree(folder)
            say(f"{name}: recording {clip.seconds:.1f} s ...")
            began = time.perf_counter()
            try:
                recorded = record(name, clip, settings, folder, themes.get(name))
                files = publish(name, recorded, out, settings)
            except encode.GifTooLarge as error:
                say(f"  ERROR: {error}")
                failed += 1
                continue
            except media_app.Unavailable as error:
                say(f"  skipped: {error}")
                continue
            say(f"  {recorded.frames.count} frames in {time.perf_counter() - began:.0f} s")
            for path, what in files:
                size = path.stat().st_size if path.exists() else 0
                say(f"  {path}  {encode.human(size):>8}  {what}")
            if keep is None:
                shutil.rmtree(folder, ignore_errors=True)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    known = clips()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", nargs="?", default="docs/media", type=Path,
                        help="where the files go (default docs/media)")  # fmt: skip
    parser.add_argument("--only", nargs="+", metavar="NAME", help="make only these clips")
    parser.add_argument("--list", action="store_true", help="say what clips there are, and stop")
    parser.add_argument("--gif-only", action="store_true", help="only the GIFs (for forums)")
    parser.add_argument("--bare", action="store_true", help="panel clips without the device frame")
    parser.add_argument("--theme", action="append", default=[], metavar="CLIP=THEME[,THEME]",
                        help="show this theme in a panel clip (repeat for more clips)")  # fmt: skip
    parser.add_argument("--ffmpeg", metavar="PATH", help="the ffmpeg to use (default: on the PATH)")
    parser.add_argument(
        "--keep-frames",
        type=Path,
        metavar="DIR",
        help="keep the PNG frames of each clip in DIR/NAME",
    )
    parser.add_argument(
        "--draft", action="store_true", help="half the size, half the frames: to try a recipe"
    )
    args = parser.parse_args(argv)
    if args.list:
        print(describe(known))
        return 0
    names = args.only or list(known)
    if unknown := [n for n in names if n not in known]:
        parser.error(f"no such clip: {', '.join(unknown)} (there are: {', '.join(known)})")
    settings = encode.Settings(bare=args.bare, gif_only=args.gif_only, ffmpeg=args.ffmpeg)
    if args.draft:
        settings = replace(settings, scale=0.5, fps=25, gif_width=480, quick=True)
    return make(names, args.out, settings, parse_themes(args.theme, known), args.keep_frames)


if __name__ == "__main__":
    sys.exit(main())
