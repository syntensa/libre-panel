"""tools/check_pages.py: the README, the website and the release notes name only pictures and
clips that the recipes make, links that lead somewhere, and a website that has every file it
names and loads nothing from another site."""

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


@pytest.fixture(scope="module")
def pages():
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(TOOLS))
        import check_pages

        yield check_pages


@pytest.fixture(scope="module")
def recipes(pages):
    return pages.made()


def test_every_picture_and_clip_the_pages_show_is_made_by_a_recipe(pages, recipes):
    shown = pages.shown()
    assert set(shown) == {*pages.READMES, *pages.SITE, pages.NOTES}  # all of them, read
    assert ("media", "hero.gif") in shown["README.md"]  # the headline over Lumen in video mode
    assert ("media", "hero-de.gif") in shown["README.de.md"]  # (the headline in German)
    assert ("media", "video-mode.webm") in shown["site/index.html"]
    # the German page shows the same, its own where a picture has words (the top, the buttons)
    german = {(folder, name.replace("-de.", ".")) for folder, name in shown["README.de.md"]}
    assert german == shown["README.md"]
    assert shown["site/index.html"] == shown["site/de/index.html"]
    assert pages.check_recipes(recipes=recipes) == []


def test_a_clip_or_picture_no_recipe_makes_is_told(pages, recipes, tmp_path):
    (tmp_path / "README.md").write_text(
        "![a](docs/media/video-mode.gif) ![b](docs/media/nope.gif)\n"
        '<img src="docs/images/hero.png"> <img src="docs/images/old.png">\n',
        encoding="utf-8",
    )
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "index.html").write_text(
        '<video poster="media/card-ignition.png"><source src="media/card.webm"></video>'
        '<link rel="icon" href="images/icon.png">',
        encoding="utf-8",
    )
    tag = f"{pages.RAW}v0.5.0/docs/media"  # (the release notes: the files of the tag)
    (tmp_path / "CHANGELOG.md").write_text(
        f"![x]({tag}/small-panel.gif) ![y]({tag}/x.gif)", encoding="utf-8"
    )
    assert pages.check_recipes(tmp_path, recipes) == [
        "CHANGELOG.md: media/x.gif is made by no recipe",
        "README.md: images/old.png is made by no recipe",
        "README.md: media/nope.gif is made by no recipe",
        "site/index.html: media/card.webm is made by no recipe",
    ]


def test_every_link_of_the_pages_leads_to_a_file_or_a_heading(pages):
    assert pages.check_links() == []


def test_a_link_to_no_file_or_no_heading_is_told(pages, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "GUIDE.md").write_text(
        "# Guide\n\n## The app's window\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text(
        "## Install and first start\n\n"
        "[a](#install-and-first-start) [b](#nowhere) [c](docs/GUIDE.md#the-apps-window)\n"
        "[d](docs/GUIDE.md#the-window) [e](docs/MISSING.md) [f](https://example.org/#x)\n"
        "```bash\n[g](docs/IN-CODE.md)\n```\n",
        encoding="utf-8",
    )
    assert pages.check_links(tmp_path) == [
        "README.md: #nowhere leads to no heading",
        "README.md: docs/GUIDE.md#the-window leads to no heading",
        "README.md: docs/MISSING.md leads to no file",
    ]


@pytest.mark.parametrize(
    ("heading", "anchor"),
    [
        ("A 30-second tour", "a-30-second-tour"),
        ("Install and first start", "install-and-first-start"),
        ("Unterstützte Panels", "unterstützte-panels"),
        ("The app's window", "the-apps-window"),
        ("Windows sensors (GPU, power, fans)", "windows-sensors-gpu-power-fans"),
        ("`libre-panel doctor` and [more](x.md)", "libre-panel-doctor-and-more"),
    ],
)
def test_headings_get_githubs_anchors(pages, heading, anchor):
    assert pages.slug(heading) == anchor


def fake_repository(root: Path, recipes: dict[str, set[str]], media: bool = True) -> None:
    """The website's sources as the repository has them, with a file of a few bytes for every
    picture and clip the recipes make."""
    shutil.copytree(ROOT / "site", root / "site")
    for folder, names in recipes.items():
        if folder == "media" and not media:
            continue
        (root / "docs" / folder).mkdir(parents=True)
        for name in names:
            (root / "docs" / folder / name).write_bytes(b"x")
    fonts = root / "src" / "libre_panel" / "fonts"
    fonts.mkdir(parents=True)
    for font in (ROOT / "src" / "libre_panel" / "fonts").glob("*.ttf"):
        (fonts / font.name).write_bytes(b"x")
    (fonts / "OFL-Barlow.txt").write_text("licence", encoding="utf-8")
    (root / "src" / "libre_panel" / "assets").mkdir()
    (root / "src" / "libre_panel" / "assets" / "libre-panel.png").write_bytes(b"x")


def test_the_website_is_put_together_with_every_file_it_names(pages, recipes, tmp_path):
    fake_repository(tmp_path / "repo", recipes)
    site = tmp_path / "site"
    pages.build(tmp_path / "repo", site)
    for path in ("index.html", "de/index.html", "site.css", "site.js", "images/icon.png",
                 "images/social-preview.png", "media/video-mode.webm", "media/video-mode.png",
                 "fonts/Barlow-Regular.ttf", "fonts/OFL-Barlow.txt"):  # fmt: skip
        assert (site / path).is_file(), path
    assert not (site / "fonts" / "LiberationSans-Regular.ttf").exists()  # only what it uses
    assert pages.check_site(site) == []


def test_without_the_clips_the_website_is_not_put_together(pages, recipes, tmp_path):
    fake_repository(tmp_path / "repo", recipes, media=False)
    with pytest.raises(FileNotFoundError, match="readme_media"):
        pages.build(tmp_path / "repo", tmp_path / "site")


def test_a_website_that_misses_a_file_or_loads_from_elsewhere_is_told(pages, tmp_path):
    (tmp_path / "de").mkdir()
    (tmp_path / "index.html").write_text(
        '<link rel="stylesheet" href="site.css"><link rel="canonical" href="https://a.example/">'
        '<script src="https://cdn.example/x.js"></script><a href="de/">de</a>'
        '<a href="#top">top</a><a href="#gone">gone</a><img src="media/missing.gif" id="top">',
        encoding="utf-8",
    )
    (tmp_path / "site.css").write_text(
        '@font-face { src: url("fonts/Barlow-Regular.ttf"); }'
        '.x { background: url("https://fonts.example/x.png"); }',
        encoding="utf-8",
    )
    (tmp_path / "de" / "index.html").write_text('<a href="../">en</a>', encoding="utf-8")
    assert pages.check_site(tmp_path) == [
        "index.html: loads https://cdn.example/x.js from another site",
        "index.html: #gone leads to no id",
        "index.html: media/missing.gif is not there",
        "site.css: loads https://fonts.example/x.png from another site",
        "site.css: fonts/Barlow-Regular.ttf is not there",
    ]
