"""Check the README, the website and the release notes against what a release makes; put the
website together.

    python tools/check_pages.py                  # every picture and link the pages name
    python tools/check_pages.py --build _site    # the website in _site, and every file it names

The pages are written against docs/media (the clips of tools/readme_media.py) and docs/images
(the pictures of tools/readme_images.py). The release makes those files, so until it has, a
path that no recipe makes would only show as a broken picture on the published page: this
says so before. The first form imports the recipes (it needs the package and Pillow); the
second needs nothing but Python, for the website's workflow.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
READMES = ("README.md", "README.de.md")
SITE = ("site/index.html", "site/de/index.html")
NOTES = "CHANGELOG.md"
RAW = "https://raw.githubusercontent.com/syntensa/libre-panel/"
# The files tools/readme_media.py makes of each clip (its publish()): the GIF, the poster and
# the two videos.
CLIP_FILES = ("gif", "png", "mp4", "webm")
# What the website takes from elsewhere in the repository than docs/images and docs/media.
ICON = "src/libre_panel/assets/libre-panel.png"
FONTS = "src/libre_panel/fonts"

_ATTR = re.compile(r'\b(?:src|href|poster|data-poster|srcset)="([^"]*)"')
_CSS_URL = re.compile(r'url\(\s*["\']?([^"\')]+)["\']?\s*\)')
_MD_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _targets(text: str) -> Iterator[str]:
    """Every address a page names: HTML attributes, Markdown links and pictures, CSS url()."""
    for pattern in (_ATTR, _MD_LINK, _CSS_URL):
        for match in pattern.finditer(text):
            for part in match.group(1).split(","):  # (a srcset may list several)
                address = html.unescape(part.strip().split(" ")[0])
                if address:
                    yield address


def _local(address: str) -> bool:
    return not re.match(r"^(?:[a-z][a-z0-9+.-]*:|//|#)", address, re.IGNORECASE)


def _without_code(text: str) -> str:
    """Markdown without its fenced code: a command in a block is no link."""
    out, fenced = [], False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return "\n".join(out)


# -- what the pages show ---------------------------------------------------------------------


def shown(root: Path = ROOT) -> dict[str, set[tuple[str, str]]]:
    """The pictures and clips each page shows, as (folder, file) with folder "media" or
    "images": docs/... in the READMEs, media/... and images/... on the website, and the
    release's own copies in the release notes (raw.githubusercontent.com on the tag)."""
    found: dict[str, set[tuple[str, str]]] = {}
    for name in READMES + SITE + (NOTES,):
        page = root / name
        if not page.exists():
            continue
        text = page.read_text(encoding="utf-8")
        files = found.setdefault(name, set())
        for address in _targets(text):
            if address.startswith(RAW):
                address = "/".join(address[len(RAW) :].split("/")[1:])  # without the tag
            elif not _local(address):
                continue
            address = address.split("#")[0].split("?")[0]
            if name in SITE:
                address = re.sub(r"^(?:\.\./)+", "", address)
                match = re.fullmatch(r"(media|images)/([^/]+)", address)
            else:
                match = re.fullmatch(r"docs/(media|images)/([^/]+)", address)
            if match:
                files.add((match.group(1), match.group(2)))
    return found


def made() -> dict[str, set[str]]:
    """What the recipes make: {"media": names, "images": names}. Imports the tools."""
    saved = list(sys.path)
    sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "src")]
    try:
        import readme_images
        import readme_media
    finally:
        sys.path[:] = saved
    clips = {f"{clip}.{kind}" for clip in readme_media.clips() for kind in CLIP_FILES}
    return {"media": clips, "images": set(readme_images.pictures())}


def check_recipes(root: Path = ROOT, recipes: dict[str, set[str]] | None = None) -> list[str]:
    """A line for every picture or clip a page shows that no recipe makes."""
    recipes = recipes if recipes is not None else made()
    problems = []
    for page, files in sorted(shown(root).items()):
        for folder, name in sorted(files):
            if page in SITE and (folder, name) == ("images", "icon.png"):
                continue  # the app's icon, from the package
            if name not in recipes[folder]:
                problems.append(f"{page}: {folder}/{name} is made by no recipe")
    return problems


# -- links -------------------------------------------------------------------------------------


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading: its text in lower case, without punctuation, with
    a hyphen for each space."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)  # a link: its text
    text = re.sub(r"<[^>]+>", "", text).replace("`", "")
    text = html.unescape(text).strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def anchors(page: Path) -> set[str]:
    """The anchors of a Markdown file (GitHub's, numbered when a heading comes again) or of an
    HTML page (its ids)."""
    text = page.read_text(encoding="utf-8")
    if page.suffix != ".md":
        return set(re.findall(r'\bid="([^"]+)"', text))
    found: set[str] = set()
    seen: dict[str, int] = {}
    for line in _without_code(text).splitlines():
        match = _HEADING.match(line)
        if not match:
            continue
        base = slug(match.group(2))
        count = seen.get(base, 0)
        seen[base] = count + 1
        found.add(base if count == 0 else f"{base}-{count}")
    return found


def check_links(root: Path = ROOT) -> list[str]:
    """A line for every link of the READMEs, the release notes and the website that leads to no
    file or no anchor in the repository (docs/media and docs/images aside: the recipes make
    them, see :func:`check_recipes`)."""
    problems = []
    for name in READMES + (NOTES,) + SITE:
        page = root / name
        if not page.exists():
            continue
        text = page.read_text(encoding="utf-8")
        if page.suffix == ".md":
            text = _without_code(text)
        for address in _targets(text):
            if address.startswith("#"):
                target, anchor = page, address[1:]
            elif _local(address):
                path, _, anchor = address.partition("#")
                path = path.split("?")[0]
                if name in SITE:
                    continue  # (the website's own files: see check_site)
                if re.match(r"docs/(media|images)/", path):
                    continue
                target = root / path
                if not target.exists():
                    problems.append(f"{name}: {address} leads to no file")
                    continue
            else:
                continue
            if anchor and target.is_file() and target.suffix in (".md", ".html"):
                if anchor not in anchors(target):
                    problems.append(f"{name}: {address} leads to no heading")
    return problems


# -- the website -------------------------------------------------------------------------------


def build(root: Path, out: Path) -> None:
    """The website as it is published: site/, the pictures in images/, the clips in media/, the
    icon and the fonts the stylesheet names. Raises FileNotFoundError when docs/media or a
    font is missing."""
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(root / "site", out)
    media = sorted((root / "docs" / "media").glob("*"))
    if not media:
        raise FileNotFoundError(
            "docs/media has no clips: make them with tools/readme_media.py (docs/RELEASING.md)"
        )
    (out / "media").mkdir()
    for clip in media:
        shutil.copy2(clip, out / "media" / clip.name)
    (out / "images").mkdir()
    for picture in sorted((root / "docs" / "images").glob("*.png")):
        shutil.copy2(picture, out / "images" / picture.name)
    shutil.copy2(root / ICON, out / "images" / "icon.png")
    (out / "fonts").mkdir()
    css = (root / "site" / "site.css").read_text(encoding="utf-8")
    fonts = sorted({Path(url).name for url in _CSS_URL.findall(css) if url.startswith("fonts/")})
    for font in fonts:
        shutil.copy2(root / FONTS / font, out / "fonts" / font)
    for licence in (root / FONTS).glob("OFL-*.txt"):
        shutil.copy2(licence, out / "fonts" / licence.name)


def check_site(site: Path) -> list[str]:
    """A line for every file a page or the stylesheet of the website ``site`` names that is not
    there, every anchor of its own that leads nowhere, and everything it would load from
    another site (it loads nothing from elsewhere: fonts, pictures and scripts are its own)."""
    problems = []
    for page in sorted([*site.rglob("*.html"), *site.rglob("*.css")]):
        text = page.read_text(encoding="utf-8")
        where = page.relative_to(site)
        loads = set(re.findall(r'<(?:img|video|source|script)\b[^>]*\bsrc="([^"]+)"', text))
        for tag in re.findall(r"<link\b[^>]*>", text):  # (not canonical or alternate: no load)
            rel, href = re.search(r'\brel="([^"]+)"', tag), re.search(r'\bhref="([^"]+)"', tag)
            if rel and href and set(rel.group(1).split()) & {"stylesheet", "preload", "icon"}:
                loads.add(href.group(1))
        loads |= set(re.findall(r'\bposter="([^"]+)"', text))
        loads |= set(_CSS_URL.findall(text))
        for address in sorted(loads):
            if not _local(address) and not address.startswith("#"):
                problems.append(f"{where}: loads {address} from another site")
        ids = set(re.findall(r'\bid="([^"]+)"', text))
        for address in _targets(text):
            if address.startswith("#"):
                if address[1:] and address[1:] not in ids:
                    problems.append(f"{where}: {address} leads to no id")
                continue
            if not _local(address):
                continue
            path = address.split("#")[0].split("?")[0]
            target = (page.parent / path).resolve()
            if path.endswith("/"):
                target = target / "index.html"
            if not target.exists():
                problems.append(f"{where}: {address} is not there")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build", type=Path, metavar="DIR", help="put the website together in DIR")
    args = parser.parse_args(argv)
    if args.build:
        try:
            build(ROOT, args.build)
        except FileNotFoundError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        problems = check_site(args.build)
    else:
        problems = check_recipes() + check_links()
    for line in problems:
        print(line, file=sys.stderr)
    print(f"{len(problems)} problem(s)" if problems else "all pictures, clips and links are there")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
