"""Pictures to files: a folder of numbered PNG frames becomes a GIF, an MP4, a WebM and a poster.

The pieces tools/readme_media.py is made of that do not know Libre Panel: how a clip is kept
while it is recorded (:class:`Frames`), what a run is asked for (:class:`Settings`) and how a
clip is encoded.

GIF: ffmpeg's two passes, a palette taken from the frames that change (``palettegen`` with
``stats_mode=diff``) and an ordered dither that keeps still parts still (``paletteuse`` with
``diff_mode=rectangle``: a frame only holds the box that changed). Without ffmpeg, Pillow
writes the GIF from a palette of its own, dithered the same way. A GIF is tried in a few looks,
from the best to the leanest, until it is under the size a README should stay under; one that
cannot be made smaller than a forum takes is an error.

MP4 (H.264, yuv420p, ``faststart``) and WebM (VP9) are for the website's ``<video autoplay loop
muted playsinline>``, with the GIF as the fallback; they need ffmpeg.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image

MiB = 1024 * 1024
GIF_TARGET = 4 * MiB  # a GIF in the README should stay under this
GIF_LIMIT = 8 * MiB  # what a forum takes: a GIF over it is an error


class EncodeError(RuntimeError):
    """A file could not be made (ffmpeg failed, or ffmpeg is needed and missing)."""


class GifTooLarge(EncodeError):
    """Even the leanest look of a GIF is over the limit."""


@dataclass(frozen=True)
class Settings:
    """What a run asks for. Recorders read the first four, the encoders the rest."""

    fps: int = 50  # panel clips: the frames a second the panel is driven at (video mode)
    scale: float = 1.0  # the panel's pixels: below 1 for drafts and tests
    bare: bool = False  # the panel alone, without the device frame around it
    quick: bool = False  # app clips: short mouse paths and holds (tests, drafts)
    gif_width: int = 960  # README and forums; a smaller picture is not scaled up
    gif_fps: int = 25
    target: int = GIF_TARGET
    limit: int = GIF_LIMIT
    gif_only: bool = False
    ffmpeg: str | None = None  # the program; None: the one on the PATH


DEFAULT = Settings()


@dataclass(frozen=True)
class Frames:
    """Numbered PNG pictures of a clip in a folder, ``fps`` of them a second."""

    folder: Path
    count: int
    fps: int

    def path(self, number: int) -> Path:
        return self.folder / f"{number:05d}.png"

    @property
    def pattern(self) -> str:
        """The names as ffmpeg's image reader asks for them."""
        return str(self.folder / "%05d.png")

    def image(self, number: int) -> Image.Image:
        with Image.open(self.path(number)) as picture:
            return picture.convert("RGB")

    @property
    def size(self) -> tuple[int, int]:
        with Image.open(self.path(0)) as picture:
            return picture.size


@dataclass(frozen=True)
class Recorded:
    """What a recipe gives: the frames of a clip, and the number of the one that is its poster."""

    frames: Frames
    poster: int


class FrameSink:
    """Takes the pictures of a clip one after the other and keeps them as PNG files.

    A clip's frames cannot all be held in memory (eight seconds of a 1920 x 480 panel at 50
    frames a second are a gigabyte), and a folder of them is what the encoders read twice.
    """

    def __init__(self, folder: Path, fps: int) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.folder, self.fps, self.count = folder, fps, 0

    def add(self, picture: Image.Image) -> None:
        picture.convert("RGB").save(self.folder / f"{self.count:05d}.png", compress_level=1)
        self.count += 1

    def frames(self) -> Frames:
        if not self.count:
            raise EncodeError("a clip with no pictures")
        return Frames(self.folder, self.count, self.fps)


def find_ffmpeg(given: str | None = None) -> str | None:
    """The ffmpeg to use: ``given`` (a path or a name), else the one on the PATH, else None."""
    return shutil.which(given or "ffmpeg")


def human(size: int) -> str:
    return f"{size / MiB:.2f} MB" if size >= MiB else f"{size / 1024:.0f} kB"


def _run(command: list[str]) -> None:
    done = subprocess.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if done.returncode:
        tail = "\n".join(done.stderr.strip().splitlines()[-6:])
        raise EncodeError(f"{Path(command[0]).name} failed ({done.returncode}):\n{tail}")


def _ffmpeg(ffmpeg: str, *arguments: str) -> list[str]:
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *arguments]


# -- GIF ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GifLook:
    """One way to make a GIF. ``fps`` None: the settings'; a number is a rate to go down to."""

    colors: int = 256
    dither: int = 3  # the ordered dither's scale, 0 (a visible pattern) to 5 (none)
    fps: int | None = None

    def rate(self, wanted: int) -> int:
        return wanted if self.fps is None else min(wanted, self.fps)


# From the best to the leanest. The size of a GIF is mostly what moves in it, so the first
# changes are the ones that cost the picture least: fewer colours, then fewer frames.
LADDER = (
    GifLook(256, 3),
    GifLook(256, 5),
    GifLook(160, 5),
    GifLook(160, 5, 20),
    GifLook(128, 5, 20),
    GifLook(96, 5, 15),
)


@dataclass(frozen=True)
class GifResult:
    path: Path
    size: int
    look: GifLook
    fps: int
    within_target: bool
    tried: tuple[tuple[GifLook, int], ...]  # every look tried, with the size it gave


def _gif_pictures(frames: Frames, fps: int) -> list[int]:
    """The numbers of the frames a GIF at ``fps`` takes: every n-th, from the first."""
    fps = max(1, min(fps, frames.fps))
    return [round(k * frames.fps / fps) for k in range(int(frames.count * fps / frames.fps))]


def _gif_with_ffmpeg(
    ffmpeg: str, frames: Frames, out: Path, width: int, look: GifLook, fps: int, work: Path
) -> None:
    chain = f"fps={fps},scale={width}:-1:flags=lanczos"
    source = ["-framerate", str(frames.fps), "-i", frames.pattern]
    palette = work / "palette.png"
    first = f"{chain},palettegen=max_colors={look.colors}:stats_mode=diff"
    _run(_ffmpeg(ffmpeg, *source, "-vf", first, str(palette)))
    use = f"paletteuse=dither=bayer:bayer_scale={look.dither}:diff_mode=rectangle"
    second = f"{chain}[x];[x][1:v]{use}"
    _run(_ffmpeg(ffmpeg, *source, "-i", str(palette), "-lavfi", second, "-loop", "0", str(out)))


def _bayer(size: int = 8) -> np.ndarray:
    """The ordered dither's threshold matrix (``size`` a power of two), from -0.5 to 0.5."""
    matrix = np.zeros((1, 1))
    while matrix.shape[0] < size:
        matrix = np.block([[4 * matrix, 4 * matrix + 2], [4 * matrix + 3, 4 * matrix + 1]])
    return (matrix + 0.5) / matrix.size - 0.5


def _gif_with_pillow(frames: Frames, out: Path, width: int, look: GifLook, fps: int) -> None:
    """The same GIF without ffmpeg: one palette for all frames (median cut over a sample of
    them), an ordered dither (the same pattern in every frame, so what stands still stays
    the same pixels), and what stands still painted with the transparent colour, as ffmpeg's
    GIF writer does: the pixels that did not change compress to nothing."""
    numbers = _gif_pictures(frames, fps)
    w0, h0 = frames.size
    size = (min(width, w0), round(h0 * min(width, w0) / w0))
    pictures = [frames.image(n).resize(size, Image.Resampling.LANCZOS) for n in numbers]
    sample = np.linspace(0, len(pictures) - 1, min(len(pictures), 12)).round().astype(int)
    sheet = Image.new("RGB", (size[0], size[1] * len(sample)))
    for row, n in enumerate(sample):
        sheet.paste(pictures[n], (0, row * size[1]))
    colors = min(look.colors, 255)  # the last index is the transparent one
    palette = sheet.quantize(colors, Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    palette.putpalette((palette.getpalette() or [])[: 3 * colors] + [0, 0, 0] * (256 - colors))
    spread = 14.0 * (6 - look.dither) / 4  # 0: a visible pattern, 5: hardly any
    columns, rows = size
    tile = np.tile(_bayer(), (rows // 8 + 1, columns // 8 + 1))[:rows, :columns, None] * spread
    painted: list[Image.Image] = []
    before: np.ndarray | None = None
    for picture in pictures:
        shifted = np.clip(np.asarray(picture, np.float32) + tile, 0, 255).astype(np.uint8)
        indexed = Image.fromarray(shifted).quantize(palette=palette, dither=Image.Dither.NONE)
        now = np.asarray(indexed)
        shown = now if before is None else np.where(now != before, now, 255).astype(np.uint8)
        frame = Image.fromarray(shown, "P")
        frame.putpalette(palette.getpalette() or [])
        painted.append(frame)
        before = now
    painted[0].save(
        out, save_all=True, append_images=painted[1:], duration=round(1000 / fps), loop=0,
        disposal=1, transparency=255,
    )  # fmt: skip


def write_gif(
    frames: Frames, out: Path, settings: Settings = DEFAULT, ladder: tuple[GifLook, ...] = LADDER
) -> GifResult:
    """``frames`` as a GIF at ``out``: the best look that is under ``settings.target``, else the
    smallest one; :class:`GifTooLarge` if that is over ``settings.limit`` (nothing is left
    at ``out`` then)."""
    ffmpeg = find_ffmpeg(settings.ffmpeg)
    width = min(settings.gif_width, frames.size[0])
    wanted = min(settings.gif_fps, frames.fps)  # (a GIF does not take frames the clip lacks)
    tried: list[tuple[GifLook, int, int]] = []
    best: tuple[int, Path] | None = None
    with tempfile.TemporaryDirectory(prefix="readme-media-") as folder:
        work = Path(folder)
        for n, look in enumerate(ladder):
            fps = look.rate(wanted)
            candidate = work / f"look-{n}.gif"
            if ffmpeg:
                _gif_with_ffmpeg(ffmpeg, frames, candidate, width, look, fps, work)
            else:
                _gif_with_pillow(frames, candidate, width, look, fps)
            size = candidate.stat().st_size
            tried.append((look, fps, size))
            if best is None or size < best[0]:
                best = (size, candidate)
            if size <= settings.target:
                break
        assert best is not None
        size, chosen = best
        look, fps, _ = next(t for t in tried if t[2] == size)
        record = tuple((lk, sz) for lk, _, sz in tried)
        if size > settings.limit:
            sizes = ", ".join(human(sz) for _, _, sz in tried)
            raise GifTooLarge(
                f"{out.name} would be {human(size)} at best (tried {sizes}); a forum takes "
                f"{human(settings.limit)}. Make the clip shorter or the panel's picture quieter."
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(chosen, out)
    return GifResult(out, size, look, fps, size <= settings.target, record)


# -- video ----------------------------------------------------------------------------------

# Colours as browsers read them: converted with the BT.709 matrix, and said so, because a
# player takes an untagged picture of this size for BT.709 and one of another size for BT.601
# (a teal that shifts is a different accent).
_COLOURS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-color_range", "tv"]  # fmt: skip
_TO_YUV = "scale=trunc(iw/2)*2:trunc(ih/2)*2:out_color_matrix=bt709:out_range=tv,format=yuv420p"


def _need_ffmpeg(settings: Settings, what: str) -> str:
    ffmpeg = find_ffmpeg(settings.ffmpeg)
    if ffmpeg is None:
        raise EncodeError(f"{what} needs ffmpeg, which was not found (--ffmpeg PATH names it)")
    return ffmpeg


def write_mp4(frames: Frames, out: Path, settings: Settings = DEFAULT) -> Path:
    """H.264 in yuv420p, with the index first (``faststart``) so a page can play it as it loads."""
    ffmpeg = _need_ffmpeg(settings, "an MP4")
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(_ffmpeg(ffmpeg, "-framerate", str(frames.fps), "-i", frames.pattern, "-vf", _TO_YUV,
                 "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-tune", "animation",
                 "-pix_fmt", "yuv420p", *_COLOURS, "-movflags", "+faststart", "-an",
                 str(out)))  # fmt: skip
    return out


def write_webm(frames: Frames, out: Path, settings: Settings = DEFAULT) -> Path:
    """VP9 in yuv420p, one pass at a constant quality."""
    ffmpeg = _need_ffmpeg(settings, "a WebM")
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(_ffmpeg(ffmpeg, "-framerate", str(frames.fps), "-i", frames.pattern, "-vf", _TO_YUV,
                 "-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-row-mt", "1",
                 "-deadline", "good", "-cpu-used", "2", "-pix_fmt", "yuv420p", *_COLOURS, "-an",
                 str(out)))  # fmt: skip
    return out


def write_poster(frames: Frames, number: int, out: Path) -> Path:
    """One frame as a PNG, the picture a ``<video poster>`` shows before it plays."""
    out.parent.mkdir(parents=True, exist_ok=True)
    frames.image(max(0, min(number, frames.count - 1))).save(out, optimize=True)
    return out


def fade_loop(frames: Frames, overlap: int) -> Frames:
    """Make a clip loop without a jump.

    The last ``overlap`` frames are the clip's own continuation past its end. They are blended
    into its first ``overlap`` frames, and then dropped: the clip now starts where its end
    would have gone on, and gives way to its real start over those frames."""
    count = frames.count - overlap
    if overlap < 1 or count < overlap:
        raise EncodeError(f"cannot fade {overlap} frames of a clip of {frames.count}")
    for number in range(overlap):
        x = number / overlap
        share = x * x * (3 - 2 * x)  # from the continuation (0) to the start (1)
        blended = Image.blend(frames.image(count + number), frames.image(number), share)
        blended.save(frames.path(number), compress_level=1)
    for number in range(count, frames.count):
        frames.path(number).unlink()
    return replace(frames, count=count)
