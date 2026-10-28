"""Sharing themes (theme/share.py, editor/community.py): a theme out as a .zip and back in as
one of yours, what an import refuses before anything is written, and the community's themes
from a list on GitHub, played here by a server of the test's own."""

import hashlib
import http.client
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from editor_token import token_of
from PIL import Image
from test_editor_server import request, server  # noqa: F401

from libre_panel.config import user_themes_dir
from libre_panel.editor import access, community
from libre_panel.fonts import builtin_font_path
from libre_panel.theme import share
from libre_panel.theme.model import find_theme, load_theme, save_theme
from libre_panel.theme.share import ShareError, export_theme, import_theme, referenced_files


def png(colour="#22d3ee", size=(48, 32)):
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def theme_json(**more):
    data = {
        "format": "libre-panel-theme/1",
        "name": "Shared",
        "display": {"model": "custom", "width": 480, "height": 320},
        "background": {"color": "#000000", "image": "assets/back.png"},
        "widgets": [],
    }
    return json.dumps({**data, **more})


def names_in(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return sorted(archive.namelist())


def what_is_there():
    themes = user_themes_dir()
    return sorted(path.name for path in themes.iterdir()) if themes.is_dir() else []


@pytest.fixture
def mine(isolated_home):
    """A theme of the person's with a backdrop, a slideshow of a folder, a font of its own and
    files it does not name; one slide names a folder of this computer only."""
    folder = user_themes_dir() / "mine"
    (folder / "assets" / "photos").mkdir(parents=True)
    (folder / "assets" / "back.png").write_bytes(png())
    (folder / "assets" / "photos" / "a.jpg").write_bytes(b"\xff\xd8\xff a photo")
    (folder / "assets" / "photos" / "b.jpg").write_bytes(b"\xff\xd8\xff another")
    (folder / "assets" / "unused.png").write_bytes(png("#ff0000"))
    (folder / "notes.txt").write_text("not part of the theme", encoding="utf-8")
    font = builtin_font_path("builtin:Barlow-Medium")
    (folder / "assets" / "Own.ttf").write_bytes(font.read_bytes())
    home_photos = str(isolated_home / "Pictures" / "*.jpg")
    data = json.loads(theme_json(name="Mine", font="assets/Own.ttf"))
    data["widgets"] = [
        {"type": "image", "id": "show", "x": 0, "y": 0, "w": 100, "h": 80,
         "slides": f"assets/photos/*.jpg\n{home_photos}\n/etc/*.jpg\n@media.cover"},
        {"type": "text", "id": "where", "text": f"made in {isolated_home}"},
    ]  # fmt: skip
    save_theme("mine", data)
    return folder


def test_a_theme_goes_out_with_the_files_it_names_and_comes_back_as_one_of_yours(
    mine, isolated_home
):
    data, filename = export_theme("mine")
    assert filename == "mine.zip"
    assert names_in(data) == [
        "mine/assets/Own.ttf",
        "mine/assets/back.png",
        "mine/assets/photos/a.jpg",
        "mine/assets/photos/b.jpg",
        "mine/theme.json",
    ]  # not the picture it does not name, nor the notes
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        text = archive.read("mine/theme.json").decode("utf-8")
    shared = json.loads(text)
    # no line names the person's folders, nor a whole path, in the theme that goes out
    assert str(isolated_home) not in text and "/etc/" not in text
    assert shared["widgets"][0]["slides"] == "assets/photos/*.jpg\n@media.cover"
    assert shared["widgets"][1]["text"] == ""
    assert "path" not in shared and "root" not in shared

    done = import_theme(data)  # back on this computer: its name is taken, by itself
    assert done == {"id": "mine-2", "name": "Mine", "files": 4}
    again = load_theme(find_theme("mine-2"))
    assert again.to_dict() == {**load_theme(mine).to_dict(), "widgets": again.widgets}
    assert sorted(referenced_files(again.root, again.to_dict())) == [
        "assets/Own.ttf",
        "assets/back.png",
        "assets/photos/a.jpg",
        "assets/photos/b.jpg",
    ]
    assert not (again.root / "notes.txt").exists()


def test_a_built_in_theme_comes_in_under_a_name_of_its_own_each_time():
    data, _ = export_theme("lumen")
    assert names_in(data) == ["lumen/assets/backdrop.png", "lumen/theme.json"]
    assert import_theme(data)["id"] == "lumen-2"  # the built-in Lumen stays what it is
    assert import_theme(data)["id"] == "lumen-3"
    assert what_is_there() == ["lumen-2", "lumen-3"]  # (and nothing half-written beside them)
    assert load_theme(find_theme("lumen-3")).to_dict() == load_theme(find_theme("lumen")).to_dict()


def test_a_zip_without_a_folder_is_named_after_its_file_or_its_theme():
    files = {"theme.json": theme_json(name="Nordlicht Glow"), "assets/back.png": png()}
    assert import_theme(zipped(files), name="C:\\Downloads\\aurora.zip")["id"] == "aurora"
    assert import_theme(zipped(files))["id"] == "nordlicht-glow"
    # what the file managers of macOS and Windows add is left out, as files the theme does not name
    files.update({"__MACOSX/._back.png": "x", ".DS_Store": "x", "assets/spare.png": png()})
    done = import_theme(zipped(files), name="aurora.zip")
    assert done == {"id": "aurora-2", "name": "Nordlicht Glow", "files": 1}
    folder = find_theme("aurora-2")
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")) == [
        "assets",
        "assets/back.png",
        "theme.json",
    ]


def symlink_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("t/theme.json", theme_json())
        link = zipfile.ZipInfo("t/assets/back.png")
        link.external_attr = (0o120777 << 16) | 0  # a link, as unzip on Linux would make it
        archive.writestr(link, "/etc/passwd")
    return buffer.getvalue()


def locked_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("t/theme.json", theme_json())
        archive.writestr("t/assets/back.png", png())
        archive.getinfo("t/assets/back.png").flag_bits |= 0x1  # (as the list at the end says)
    return buffer.getvalue()


def big_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("t/theme.json", theme_json())
        archive.writestr("t/assets/back.png", b"\x89PNG" + bytes(51 * 1024 * 1024))
    return buffer.getvalue()


def lazily(files):
    """A zip of these files, made when the test runs."""
    return lambda: zipped(files)


@pytest.mark.parametrize(
    "make, said",
    [
        (lazily({"t/theme.json": theme_json(), "t/../../x.png": png()}), "leaves the"),
        (lazily({"theme.json": theme_json(), "../evil.png": png()}), "leaves the"),
        (lazily({"theme.json": theme_json(), "/etc/evil.png": png()}), "leaves the"),
        (lazily({"theme.json": theme_json(), "C:/evil.png": png()}), "leaves the"),
        (lazily({"theme.json": theme_json(), "assets\\..\\x.png": png()}), "leaves the"),
        (symlink_zip, "is a link"),
        (locked_zip, "locked with a password"),
        (big_zip, "more than 50 MB unpacked"),
        (lambda: b"PK" + bytes(21 * 1024 * 1024), "larger than 20 MB"),
        (lazily({"t/theme.json": theme_json(), "t/assets/run.exe": b"MZ"}), "not a file a"),
        (lazily({"t/theme.json": theme_json(), "t/readme.svg": "<svg/>"}), "not a file a"),
        (lazily({"t/theme.json": theme_json(), "t/assets/back.png": b"<html>"}), "not the pic"),
        (lazily({"t/assets/back.png": png()}), "no theme.json"),
        (lazily({"a/theme.json": theme_json(), "b/theme.json": theme_json()}), "no theme.json"),
        (lazily({"theme.json": "{not json"}), "cannot be read"),
        (lazily({"theme.json": b"\xef\xbb\xbf" + theme_json().encode()}), "cannot be read"),
        (lazily({"theme.json": theme_json(format="else")}), "not a theme Libre Panel"),
        (lazily({"theme.json": theme_json(background={"image": "../../x.png"})}), "not a theme"),
        (lambda: b"no zip at all", "not a .zip"),
    ],
)
def test_nothing_is_written_from_a_file_that_is_not_a_sound_shared_theme(make, said):
    user_themes_dir().mkdir(parents=True)
    with pytest.raises(ShareError, match=said):
        import_theme(make())
    assert what_is_there() == []  # not even the folder it was checked in


# -- the routes ------------------------------------------------------------------------------


def raw_request(port, method, path, body=None, headers=None):
    """A request with the token; the status, the headers and the body as they come."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    hdrs = {"Host": f"127.0.0.1:{port}", access.HEADER: token_of(port), **(headers or {})}
    conn.request(method, path, body=body, headers=hdrs)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, dict(response.getheaders()), data


def test_the_routes_give_a_theme_as_a_file_and_take_one_in(server, mine):  # noqa: F811
    status, headers, data = raw_request(server, "GET", "/api/themes/mine/export")
    assert status == 200 and headers["Content-Type"] == "application/zip"
    assert headers["Content-Disposition"] == 'attachment; filename="mine.zip"'
    assert "mine/assets/back.png" in names_in(data)
    raw = {"Content-Type": "application/octet-stream"}
    status, done = request(server, "POST", "/api/themes/import?name=mine.zip", data, raw)
    assert status == 200 and done["id"] == "mine-2"
    status, themes = request(server, "GET", "/api/themes")
    assert "mine-2" in [theme["id"] for theme in themes]
    # in words, and nothing written
    status, answer = request(server, "POST", "/api/themes/import", b"not a zip", raw)
    assert status == 400 and answer["error"].startswith("This file is not a .zip")
    big = {**raw, "Content-Length": str(21 * 1024 * 1024)}
    status, _, body = raw_request(server, "POST", "/api/themes/import", b"", big)
    assert status == 413 and b"larger than 20 MB" in body
    assert request(server, "GET", "/api/themes/nothing-here/export")[0] == 404


def test_a_theme_named_import_is_saved_as_any_other(server):  # noqa: F811
    theme = load_theme(find_theme("libre-default")).to_dict()
    status, answer = request(server, "POST", "/api/themes/import", {"theme": theme})
    assert status == 200 and answer["id"] == "import"
    assert (user_themes_dir() / "import" / "theme.json").is_file()


# -- the community's themes ------------------------------------------------------------------


class GitHub(BaseHTTPRequestHandler):
    """The project's GitHub as the tests play it: ``files`` by their path, each request
    counted in ``asked``."""

    files: dict = {}
    asked: list = []

    def do_GET(self):  # noqa: N802 - the server's name
        GitHub.asked.append(self.path)
        found = GitHub.files.get(self.path)
        if isinstance(found, tuple):  # (status, location): a redirect
            self.send_response(found[0])
            self.send_header("Location", found[1])
            self.end_headers()
            return
        self.send_response(200 if found is not None else 404)
        self.end_headers()
        self.wfile.write(found or b"")

    def log_message(self, *args):
        pass


def an_entry(name="Aurora", zip_path="aurora/aurora.zip", sha256="", **more):
    return {"name": name, "author": "Someone", "description": "Northern lights.",
            "sizes": ["480x320", "1920x480"], "preview": "aurora/preview.png", "zip": zip_path,
            "sha256": sha256, **more}  # fmt: skip


@pytest.fixture
def github(monkeypatch):
    """A list of one sound theme (and two that are not) beside a zip and a preview; the
    app's list address points at it. Yields the files, which a test may change."""
    made = ThreadingHTTPServer(("127.0.0.1", 0), GitHub)
    threading.Thread(target=made.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{made.server_address[1]}"
    monkeypatch.setattr(community, "INDEX_URL", f"{base}/main/community/themes/index.json")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    # (its folder in the zip has another name: the list's is the one it is installed under)
    theme = zipped({"north/theme.json": theme_json(name="Aurora"), "north/assets/back.png": png()})
    index = {
        "format": community.INDEX_FORMAT,
        "themes": [
            an_entry(sha256=hashlib.sha256(theme).hexdigest()),
            an_entry(name="Elsewhere", zip_path="../../../other/repo/x.zip", sha256="0" * 64),
            {"name": "Broken"},
        ],
    }
    GitHub.files = {
        "/main/community/themes/index.json": json.dumps(index).encode(),
        "/main/community/themes/aurora/aurora.zip": theme,
        "/main/community/themes/aurora/preview.png": png("#7c3aed"),
    }
    GitHub.asked = []
    yield GitHub.files
    made.shutdown()
    made.server_close()


def test_the_community_list_shows_its_sound_entries_and_their_pictures(
    server,  # noqa: F811
    github,
):
    status, answer = request(server, "GET", "/api/community/themes")
    assert status == 200 and answer["offline"] is False
    assert answer["themes"] == [
        {
            "id": "aurora",
            "name": "Aurora",
            "author": "Someone",
            "description": "Northern lights.",
            "sizes": [[480, 320], [1920, 480]],
        },
    ]  # fmt: skip (the one whose zip is elsewhere and the broken one are left out)
    status, picture = request(server, "GET", "/api/community/themes/aurora/preview")
    assert status == 200 and picture.startswith(b"\x89PNG")
    assert request(server, "GET", "/api/community/themes/nothing/preview")[0] == 404
    request(server, "GET", "/api/community/themes")
    request(server, "GET", "/api/community/themes/aurora/preview")
    # kept: GitHub was asked once for the list and once for the picture
    assert GitHub.asked == [
        "/main/community/themes/index.json",
        "/main/community/themes/aurora/preview.png",
    ]


def test_a_community_theme_is_installed_once_its_checksum_is_right(server, github):  # noqa: F811
    status, done = request(server, "POST", "/api/community/install", {"id": "aurora"})
    assert status == 200 and done == {"id": "aurora", "name": "Aurora", "files": 1}
    assert load_theme(find_theme("aurora")).name == "Aurora"
    status, done = request(server, "POST", "/api/community/install", {"id": "aurora"})
    assert done["id"] == "aurora-2"  # again: beside the first
    status, answer = request(server, "POST", "/api/community/install", {"id": "nothing"})
    assert status == 400 and "not in the community's list" in answer["error"]
    assert request(server, "POST", "/api/community/install", {})[0] == 400


def test_a_community_theme_for_another_panel_is_adapted_and_can_be_seen_so_first(
    server,  # noqa: F811
    github,
):
    """How it looks on the chosen panel: drawn adapted, nothing installed; installed for that
    panel, it is adapted the same way (theme/adapt.py); made for it already, it stays as it is.
    The zip comes once for both."""
    status, picture = request(server, "GET",
                              "/api/community/themes/aurora/look?model=turing-9.2-usb")  # fmt: skip
    assert status == 200 and Image.open(io.BytesIO(picture)).size == (1920, 480)
    assert what_is_there() == []
    status, answer = request(server, "GET", "/api/community/themes/aurora/look?model=nonsense")
    assert status == 400 and "unknown panel model" in answer["error"]
    status, done = request(server, "POST", "/api/community/install",
                           {"id": "aurora", "model": "turing-9.2-usb"})  # fmt: skip
    assert status == 200 and done["adapted"] == [1920, 480]
    theme = load_theme(find_theme("aurora"))
    assert (theme.width, theme.height, theme.model) == (1920, 480, "turing-9.2-usb")
    assert GitHub.asked.count("/main/community/themes/aurora/aurora.zip") == 1
    status, done = request(server, "POST", "/api/community/install",
                           {"id": "aurora", "model": "turing-3.5"})  # fmt: skip
    assert status == 200 and "adapted" not in done  # (made for its 480 x 320 already)
    assert load_theme(find_theme(done["id"])).width == 480
    status, answer = request(server, "POST", "/api/community/install",
                             {"id": "aurora", "model": "nonsense"})  # fmt: skip
    assert status == 400 and "unknown panel model" in answer["error"]


def test_a_download_that_is_not_the_listed_file_is_not_installed(server, github):  # noqa: F811
    github["/main/community/themes/aurora/aurora.zip"] = zipped(
        {"aurora/theme.json": theme_json(name="Not Aurora"), "aurora/assets/back.png": png()}
    )
    status, answer = request(server, "POST", "/api/community/install", {"id": "aurora"})
    assert status == 400 and "checksum differs" in answer["error"]
    assert what_is_there() == []


def test_a_theme_made_for_another_panel_than_its_entry_says_is_not_installed(
    server,  # noqa: F811
    github,
):
    theme = zipped({"aurora/theme.json": theme_json(name="Aurora", display={"model": "custom",
                    "width": 800, "height": 480}), "aurora/assets/back.png": png()})  # fmt: skip
    index = json.loads(github["/main/community/themes/index.json"])
    index["themes"][0]["sha256"] = hashlib.sha256(theme).hexdigest()
    github["/main/community/themes/index.json"] = json.dumps(index).encode()
    github["/main/community/themes/aurora/aurora.zip"] = theme
    status, answer = request(server, "POST", "/api/community/install", {"id": "aurora"})
    assert status == 400 and "made for 800 × 480" in answer["error"]
    assert what_is_there() == []
    assert share.inspect_shared(theme)["size"] == [800, 480]


def test_offline_the_section_says_so_and_nothing_is_installed(server):  # noqa: F811
    # (conftest.py points the list at a port of this computer where nothing answers)
    status, answer = request(server, "GET", "/api/community/themes")
    assert status == 200 and answer == {"themes": [], "offline": True}
    assert request(server, "GET", "/api/community/themes/aurora/preview")[0] == 404
    status, answer = request(server, "POST", "/api/community/install", {"id": "aurora"})
    assert status == 400 and answer["error"] == "The community themes cannot be reached right now."


def test_the_list_is_kept_an_hour_and_files_come_only_from_beside_it(github):
    now = [1000.0]
    kept = community.Community(clock=lambda: now[0])
    assert [theme["id"] for theme in kept.themes()["themes"]] == ["aurora"]
    now[0] += community.KEEP_S - 1
    kept.themes()
    assert GitHub.asked == ["/main/community/themes/index.json"]
    now[0] += 2
    kept.themes()  # an hour on: asked again
    assert GitHub.asked == ["/main/community/themes/index.json"] * 2
    # a redirect to another host is not followed
    other = "http://localhost:1/main/community/themes/aurora/preview.png"
    github["/main/community/themes/aurora/preview.png"] = (302, other)
    assert kept.preview("aurora") is None
    assert community.beside(community.INDEX_URL, "aurora/aurora.zip") == (
        community.INDEX_URL.rsplit("/", 1)[0] + "/aurora/aurora.zip"
    )
