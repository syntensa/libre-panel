"""A "Share a theme" issue made into a community theme (.github/workflows/community-theme.yml).

GitHub's issue form (.github/ISSUE_TEMPLATE/share_theme.yml) brings the theme's .zip as an
attachment, and its name, its maker and what it shows as text; Libre Panel's *Share with
everyone…* fills the text in and saves the zip. The workflow runs this in two steps, in two
jobs, so that what comes from the issue is looked into only where nothing can be written:

- ``check`` (no rights): downloads the zip from GitHub's attachments (nowhere else), checks it
  as Libre Panel checks every shared theme (theme/share.py: plain JSON, pictures and fonts,
  nothing runs), works out the panel size it is made for, draws its picture with the demo
  readings (none of anybody's computer) and leaves the zip, the picture and the list's entry
  in a folder; or, where something is wrong, the reason in words.
- ``add`` (the job that may write): takes that folder, checks the entry as the app reads the
  list (editor/community.py), the zip against its SHA-256 and the picture by its first bytes,
  and puts them beside the list, ``community/themes/<id>/``, with the entry in index.json. The
  workflow opens a pull request with them; a person looks at it before it is merged.

Text from the issue is data: it reaches no shell, and the entry keeps it within the list's
lengths.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

FORM_FIELDS = {  # the issue form's labels: the text under each "### <label>"
    "Name": "name",
    "Made by": "author",
    "What it shows": "description",
    "The theme": "theme",
    "Panels you tried it on": "tried",
    "Sharing": "consent",
}
NO_ANSWER = "_No response_"
# GitHub's attachments, and where they lead
ATTACHMENT = re.compile(
    r"https://github\.com/(?:user-attachments/files|syntensa/libre-panel/files)/\d+/[^\s)\]>\"']+\.zip"
)
HOSTS = ("github.com", "objects.githubusercontent.com")
S3 = re.compile(r"^github-production-[a-z0-9-]+\.s3\.amazonaws\.com$")
LIST = Path("community/themes")
TEXTS = {"name": 80, "author": 80, "description": 400}
BRANCH = "community/theme-"  # and the issue's number: the pull request's branch (the workflow's)
TIMEOUT_S = 30


class Refused(ValueError):
    """What keeps the theme out, said to the person who shared it."""


# -- the issue --------------------------------------------------------------------------------


def fields(body: str) -> dict[str, str]:
    """The answers of the issue form, by field: the text under each heading it knows."""
    found: dict[str, list[str]] = {}
    current = None
    for line in body.replace("\r\n", "\n").split("\n"):
        heading = re.match(r"^###\s+(.+?)\s*$", line)
        if heading:
            current = FORM_FIELDS.get(heading[1])
            if current is not None:
                found[current] = []
            continue
        if current is not None:
            found[current].append(line)
    answers = {}
    for key, lines in found.items():
        text = "\n".join(lines).strip()
        answers[key] = "" if text == NO_ANSWER else text
    return answers


def attachment(text: str) -> str:
    """The address of the one .zip attached."""
    found = list(dict.fromkeys(ATTACHMENT.findall(text)))
    if not found:
        raise Refused(
            "There is no .zip under *The theme*. Drag the file Libre Panel saved (it ends in "
            "`.zip`) into that field, then edit the issue: the check runs again by itself."
        )
    if len(found) > 1:
        raise Refused("There is more than one .zip under *The theme*: one theme an issue, please.")
    return found[0]


def _allowed(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and (host in HOSTS or bool(S3.match(host)))


class _GitHubOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed(newurl):
            return None  # (urllib then answers with the redirect as an error)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, limit: int) -> bytes:
    """The attachment's bytes, from GitHub only, at most ``limit`` of them."""
    if not _allowed(url):
        raise Refused("The .zip is not an attachment of this issue.")
    request = urllib.request.Request(url, headers={"User-Agent": "libre-panel-community"})
    try:
        with urllib.request.build_opener(_GitHubOnly).open(request, timeout=TIMEOUT_S) as answer:
            data = answer.read(limit + 1)
    except OSError as exc:
        raise Refused(f"The .zip could not be downloaded ({exc}). Attach it again?") from exc
    if len(data) > limit:
        raise Refused(f"The .zip is larger than {limit // (1024 * 1024)} MB.")
    return data


def _text(answers: dict[str, str], key: str, fallback: str) -> tuple[str, bool]:
    """A text of the entry within its length (cut at a word, said), else ``fallback``."""
    text = " ".join((answers.get(key) or "").split()) or fallback
    most = TEXTS[key]
    if len(text) <= most:
        return text, False
    return text[: most - 1].rsplit(" ", 1)[0].rstrip(".,;: ") + "…", True


# -- check (no rights) -------------------------------------------------------------------------


def _picture(data: bytes) -> bytes:
    """The theme drawn with the demo readings, as a PNG."""
    from libre_panel.render.renderer import Renderer
    from libre_panel.sensors.demo import demo_snapshot
    from libre_panel.theme.model import load_theme
    from libre_panel.theme.share import import_theme

    with tempfile.TemporaryDirectory() as place:
        done = import_theme(data, themes=Path(place))
        renderer = Renderer(load_theme(Path(place) / done["id"]), preview=True)
        try:
            frame, _ = renderer.render(demo_snapshot())
        finally:
            renderer.close()
    buffer = io.BytesIO()
    frame.convert("RGB").save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def check(body: str, login: str, out: Path, get=download) -> dict[str, Any]:
    """The theme of an issue checked and drawn: its zip, picture and entry in ``out``
    (``theme.zip``, ``preview.png``, ``entry.json``). :class:`Refused` where it cannot go in."""
    from libre_panel.editor.community import MAX_PREVIEW
    from libre_panel.theme.share import MAX_ZIP, ShareError, inspect_shared

    answers = fields(body)
    if "[x]" not in answers.get("consent", "").lower():
        raise Refused("Please tick that every picture and font in the theme may be shared.")
    data = get(attachment(answers.get("theme", "")), MAX_ZIP)
    try:
        theme = inspect_shared(data)
        picture = _picture(data)
    except ShareError as exc:
        raise Refused(f"Libre Panel cannot take this theme: {exc}") from exc
    if len(picture) > MAX_PREVIEW:
        raise Refused("Its picture comes out larger than the list takes; fewer photos, perhaps?")
    name, cut_name = _text(answers, "name", theme["name"])
    author, cut_author = _text(answers, "author", theme["author"] or login)
    description, cut_description = _text(answers, "description", "")
    width, height = theme["size"]
    sha256 = hashlib.sha256(data).hexdigest()
    entry = {"name": name, "author": author, "description": description,
             "sizes": [f"{width}x{height}"], "sha256": sha256}  # fmt: skip
    out.mkdir(parents=True, exist_ok=True)
    (out / "theme.zip").write_bytes(data)
    (out / "preview.png").write_bytes(picture)
    (out / "entry.json").write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")  # fmt: skip
    cut = [what for what, was in (("name", cut_name), ("maker", cut_author),
                                  ("description", cut_description)) if was]  # fmt: skip
    return {**entry, "cut": cut, "tried": answers.get("tried", "")}


# -- add (the job that may write) --------------------------------------------------------------


def _taken(root: Path) -> set[str]:
    from libre_panel.theme.model import list_themes

    index = json.loads((root / LIST / "index.json").read_text(encoding="utf-8"))
    names = {Path(entry.get("zip", "")).stem for entry in index.get("themes", [])}
    names |= {path.name for path in (root / LIST).iterdir() if path.is_dir()}
    return names | {theme["id"] for theme in list_themes()}  # (not to hide a built-in one)


def theme_id(name: str, number: int, taken: set[str]) -> str:
    """The entry's id: its folder, its zip's name, its folder where it is installed."""
    from libre_panel.theme.model import valid_theme_name

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48].strip("-")
    base = slug if valid_theme_name(slug) else f"theme-{number}"
    chosen, n = base, 1
    while chosen in taken:
        n += 1
        chosen = f"{base}-{n}"
    return chosen


def add(checked: Path, number: int, root: Path) -> dict[str, Any]:
    """The checked theme beside the list, with its entry in index.json: the entry as written.
    What ``check`` left is read as data again: the entry as the app reads the list, the zip by
    its SHA-256, the picture by its first bytes."""
    from libre_panel.editor.community import INDEX_FORMAT, MAX_PREVIEW, parse_index
    from libre_panel.theme.share import MAX_ZIP

    raw = json.loads((checked / "entry.json").read_text(encoding="utf-8"))
    data, picture = (checked / "theme.zip").read_bytes(), (checked / "preview.png").read_bytes()
    if len(data) > MAX_ZIP or hashlib.sha256(data).hexdigest() != raw.get("sha256"):
        raise Refused("The checked zip is not the one its entry names.")
    if len(picture) > MAX_PREVIEW or not picture.startswith(b"\x89PNG\r\n\x1a\n"):
        raise Refused("The checked picture is no PNG of the size the list takes.")
    chosen = theme_id(str(raw.get("name", "")), number, _taken(root))
    entry = {key: raw.get(key) for key in ("name", "author", "description", "sizes")}
    entry.update(preview=f"{chosen}/preview.png", zip=f"{chosen}/{chosen}.zip",
                 sha256=raw.get("sha256"))  # fmt: skip
    if not parse_index(json.dumps({"format": INDEX_FORMAT, "themes": [entry]}).encode()):
        raise Refused("The entry is not one the app can read.")
    folder = root / LIST / chosen
    folder.mkdir(parents=True)
    (folder / f"{chosen}.zip").write_bytes(data)
    (folder / "preview.png").write_bytes(picture)
    index_path = root / LIST / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["themes"].append(entry)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**entry, "id": chosen}


def pull_request_text(entry: dict[str, Any], number: int) -> str:
    """What the pull request says: the entry, from where it came, what to look at."""
    sizes = ", ".join(size.replace("x", " × ") for size in entry["sizes"])
    return "\n".join([
        f"A community theme shared in #{number}: **{entry['name']}** by {entry['author']}, "
        f"made for {sizes}.",
        "",
        f"> {entry['description']}" if entry["description"] else "> (no description)",
        "",
        f"![{entry['name']}](https://github.com/syntensa/libre-panel/blob/{BRANCH}{number}/"
        f"community/themes/{entry['preview']}?raw=true)",
        "",
        "Checked by the workflow as Libre Panel checks every shared theme, and drawn with the "
        "demo readings. Before merging, look that it shows what its entry says, that nothing "
        "in it should not be shared, and that its license is named.",
        "",
        f"Closes #{number}",
    ])  # fmt: skip


# -- the command line ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="step", required=True)
    one = sub.add_parser("check", help="check and draw the theme of an issue (no rights)")
    one.add_argument("--issue", type=Path, required=True, help="the issue's text, in a file")
    one.add_argument("--login", required=True, help="who opened the issue")
    one.add_argument("--out", type=Path, required=True, help="the folder for what is checked")
    two = sub.add_parser("add", help="put a checked theme beside the list")
    two.add_argument("--checked", type=Path, required=True)
    two.add_argument("--number", type=int, required=True, help="the issue's number")
    two.add_argument("--root", type=Path, default=Path("."))
    two.add_argument("--pull-request", type=Path, required=True, help="its text, written here")
    two.add_argument("--refusal", type=Path, required=True, help="what to say, where refused")
    args = parser.parse_args(argv)
    if args.step == "check":
        try:
            checked = check(args.issue.read_text(encoding="utf-8"), args.login, args.out)
        except Refused as exc:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / "refused.md").write_text(f"{exc}\n", encoding="utf-8")
            print(f"refused: {exc}")
            return 0  # (the next job says it on the issue)
        print(f"checked: {checked['name']} for {', '.join(checked['sizes'])}")
        return 0
    refused = args.checked / "refused.md"
    try:
        if refused.exists():
            raise Refused(refused.read_text(encoding="utf-8").strip())
        entry = add(args.checked, args.number, args.root)
    except Refused as exc:
        args.refusal.write_text(
            f"{exc}\n\nEdit the issue to try again: the check runs by itself.\n", encoding="utf-8"
        )
        return 2
    args.pull_request.write_text(pull_request_text(entry, args.number) + "\n", encoding="utf-8")
    print(entry["id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
