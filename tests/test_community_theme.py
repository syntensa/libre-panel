"""tools/community_theme.py: a "Share a theme" issue made into an entry of the community's list,
checked where nothing can be written, then added as data."""

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from libre_panel.editor.community import parse_index

ROOT = Path(__file__).resolve().parent.parent
URL = "https://github.com/user-attachments/files/12345/glow.zip"


@pytest.fixture(scope="module")
def tool():
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(ROOT / "tools"))
        import community_theme

        yield community_theme


WHEN = (2026, 1, 1, 12, 0, 0)


def theme_zip(name="Glow", width=480, height=320) -> bytes:
    buffer = io.BytesIO()
    picture = io.BytesIO()
    Image.new("RGB", (8, 8), "#22d3ee").save(picture, "PNG")
    theme = {"format": "libre-panel-theme/1", "name": name, "author": "In the theme",
             "display": {"model": "custom", "width": width, "height": height},
             "background": {"color": "#000000", "image": "assets/back.png"},
             "widgets": [{"type": "module", "id": "clock", "module": "clock",
                          "col": 0, "row": 0, "cols": 2, "rows": 1}]}  # fmt: skip
    with zipfile.ZipFile(buffer, "w") as archive:  # (at a fixed time: the same bytes each call)
        archive.writestr(zipfile.ZipInfo("glow/theme.json", WHEN), json.dumps(theme))
        archive.writestr(zipfile.ZipInfo("glow/assets/back.png", WHEN), picture.getvalue())
    return buffer.getvalue()


def issue(name="Glow", author="", description="Soft light around the time.", theme=None,
          consent=True) -> str:  # fmt: skip
    """An issue as GitHub writes the form's answers."""
    theme = f"[glow.zip]({URL})" if theme is None else theme
    return "\n".join([
        "### Name", "", name or "_No response_", "",
        "### Made by", "", author or "_No response_", "",
        "### What it shows", "", description or "_No response_", "",
        "### The theme", "", theme, "",
        "### Panels you tried it on", "", "Turing 3.5\"", "",
        "### Sharing", "", f"- [{'X' if consent else ' '}] Every picture and font may be shared",
    ])  # fmt: skip


def a_list(root: Path) -> Path:
    (root / "community/themes").mkdir(parents=True)
    (root / "community/themes/index.json").write_text(
        json.dumps({"format": "libre-panel-community/1", "themes": []}), encoding="utf-8"
    )
    return root


def test_the_form_s_answers_and_its_attachment_are_read(tool):
    answers = tool.fields(issue(author=""))
    assert answers["name"] == "Glow" and answers["author"] == ""
    assert answers["description"] == "Soft light around the time." and "[X]" in answers["consent"]
    assert tool.attachment(answers["theme"]) == URL
    with pytest.raises(tool.Refused, match="no .zip"):
        tool.attachment("a picture: https://example.com/glow.zip")
    with pytest.raises(tool.Refused, match="more than one"):
        tool.attachment(f"{URL} https://github.com/user-attachments/files/9/other.zip")
    # only GitHub's attachments and where they lead, over https
    assert tool._allowed(URL) and tool._allowed("https://objects.githubusercontent.com/x")
    assert tool._allowed("https://github-production-user-asset-6210df.s3.amazonaws.com/1")
    assert not tool._allowed("https://example.com/glow.zip")
    assert not tool._allowed("http://github.com/user-attachments/files/1/a.zip")
    assert not tool._allowed("https://evil.s3.amazonaws.com/glow.zip")


def test_a_shared_theme_is_checked_drawn_and_described(tool, tmp_path):
    data = theme_zip()
    asked = []
    checked = tool.check(issue(), "someone", tmp_path / "checked",
                         get=lambda url, limit: asked.append(url) or data)  # fmt: skip
    assert asked == [URL]
    assert checked["name"] == "Glow" and checked["author"] == "In the theme"  # (the theme's)
    shared = (tmp_path / "checked/theme.zip").read_bytes()
    assert checked["sizes"] == ["480x320"]
    assert checked["sha256"] == hashlib.sha256(shared).hexdigest()
    picture = Image.open(tmp_path / "checked/preview.png")
    assert picture.size == (480, 320) and picture.format == "PNG"
    long = "word " * 120
    checked = tool.check(issue(author="Me", description=long), "someone", tmp_path / "again",
                         get=lambda url, limit: data)  # fmt: skip
    assert checked["author"] == "Me, after In the theme" and len(checked["description"]) <= 400
    assert checked["description"].endswith("…") and checked["cut"] == ["description"]
    # a picture pasted under a heading is no part of the answer (#27)
    shot = '<img width="1323" height="867" alt="Image" src="https://github.com/a.png" />'
    checked = tool.check(issue(author=f"Me\n\n{shot}", description="Glow ![x](https://a/b.png)"),
                         "someone", tmp_path / "pasted", get=lambda url, limit: data)  # fmt: skip
    assert checked["author"] == "Me, after In the theme" and checked["description"] == "Glow"


def test_a_shared_theme_is_installed_under_the_name_and_maker_its_entry_shows(tool, tmp_path):
    """A theme made from another one (here a copy of a built-in theme, renamed in the editor):
    the zip in the list carries the form's name, maker and description, its first maker kept
    as whom it is after; every other file as it was."""
    from libre_panel.theme.share import inspect_shared

    data = theme_zip("Libre Default (mine)")
    checked = tool.check(issue(name="All in", author="Varulf", description="All at a glance."),
                         "someone", tmp_path / "checked", get=lambda url, limit: data)  # fmt: skip
    assert checked["name"] == "All in" and checked["author"] == "Varulf, after In the theme"
    shared = (tmp_path / "checked/theme.zip").read_bytes()
    assert inspect_shared(shared)["name"] == "All in"
    with zipfile.ZipFile(io.BytesIO(shared)) as archive, zipfile.ZipFile(io.BytesIO(data)) as was:
        assert archive.namelist() == was.namelist()
        theme = json.loads(archive.read("glow/theme.json"))
        assert theme["author"] == "Varulf, after In the theme"
        assert theme["description"] == "All at a glance." and theme["widgets"]
        assert archive.read("glow/assets/back.png") == was.read("glow/assets/back.png")
        assert archive.getinfo("glow/theme.json").date_time == WHEN
    # the maker the form names is the theme's already, or the form names none: as it is
    assert tool._maker("In the theme", "In the theme") == "In the theme"
    assert tool._maker("", "In the theme") == "In the theme" and tool._maker("Me", "") == "Me"


def test_a_theme_s_picture_beside_the_list_is_not_kept_out_of_git():
    """The list's pictures (community/themes/<id>/preview.png) go into the pull request."""
    import subprocess

    found = subprocess.run(["git", "check-ignore", "-q", "community/themes/glow/preview.png"],
                           cwd=ROOT, check=False)  # fmt: skip
    assert found.returncode == 1  # (0: ignored)


def test_what_keeps_a_theme_out_is_said(tool, tmp_path):
    with pytest.raises(tool.Refused, match="tick"):
        tool.check(issue(consent=False), "someone", tmp_path, get=lambda url, limit: b"")
    with pytest.raises(tool.Refused, match="cannot take this theme"):
        tool.check(issue(), "someone", tmp_path, get=lambda url, limit: b"no zip")
    # on the command line: the reason left for the next job, which says it on the issue
    (tmp_path / "issue.md").write_text(issue(theme="nothing attached"), encoding="utf-8")
    assert tool.main(["check", "--issue", str(tmp_path / "issue.md"), "--login", "someone",
                      "--out", str(tmp_path / "out")]) == 0  # fmt: skip
    assert "no .zip" in (tmp_path / "out/refused.md").read_text(encoding="utf-8")
    code = tool.main(["add", "--checked", str(tmp_path / "out"), "--number", "7",
                      "--root", str(a_list(tmp_path / "repo")), "--pull-request",
                      str(tmp_path / "pr.md"), "--refusal", str(tmp_path / "say.md")])  # fmt: skip
    assert code == 2 and "Edit the issue" in (tmp_path / "say.md").read_text(encoding="utf-8")


def test_a_checked_theme_goes_beside_the_list_under_a_name_of_its_own(tool, tmp_path):
    root = a_list(tmp_path / "repo")
    checked = tmp_path / "checked"
    tool.check(issue(), "someone", checked, get=lambda url, limit: theme_zip())
    entry = tool.add(checked, 12, root)
    assert entry["id"] == "glow" and entry["zip"] == "glow/glow.zip"
    index = (root / "community/themes/index.json").read_bytes()
    assert [e["id"] for e in parse_index(index)] == ["glow"]  # as the app reads it
    assert (root / "community/themes/glow/glow.zip").read_bytes() == (
        checked / "theme.zip"
    ).read_bytes()
    assert (root / "community/themes/glow/preview.png").read_bytes()[:4] == b"\x89PNG"
    assert tool.add(checked, 13, root)["id"] == "glow-2"  # the name is taken: beside it
    tool.check(issue(name="Lumen"), "someone", tmp_path / "lumen",
               get=lambda url, limit: theme_zip("Lumen"))  # fmt: skip
    assert tool.add(tmp_path / "lumen", 14, root)["id"] == "lumen-2"  # not over a built-in one
    text = tool.pull_request_text(entry, 12)
    assert "Closes #12" in text and "480 × 320" in text and "community/theme-12" in text
    # what the first job left is data: a zip that is not the one its entry names stays out
    (checked / "theme.zip").write_bytes(theme_zip("Other"))
    with pytest.raises(tool.Refused, match="not the one"):
        tool.add(checked, 15, root)


def test_the_form_and_the_workflow_fit_the_tool(tool):
    form = (ROOT / ".github/ISSUE_TEMPLATE/share_theme.yml").read_text(encoding="utf-8")
    labels = re.findall(r"^      label: (.+)$", form, flags=re.MULTILINE)
    assert labels == list(tool.FORM_FIELDS)  # the headings the tool reads
    flow = (ROOT / ".github/workflows/community-theme.yml").read_text(encoding="utf-8")
    check, add = flow.split("\n  add:\n")
    # what comes from the issue is looked into without rights; the job that writes adds data
    assert "permissions:\n      contents: read\n    steps:" in check
    assert "community_theme.py check" in check and "community_theme.py check" not in add
    assert "needs: check" in add and "contents: write" in add
    # the issue's text reaches the steps only through the environment, never the script itself
    runs = re.findall(r"run: \|\n((?:          .*\n?)+)", flow)
    assert runs and not any("${{" in run for run in runs)
