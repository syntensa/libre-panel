"""The Rust app's server against editor/server.py (docs/RUST.md, R9): the same requests to
both, the same answers: the status line, the headers (but for the server's name and the date)
and the body, byte for byte."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from test_rust_app import DATA, Rig, _libraries, _program

from libre_panel import i18n
from libre_panel.editor import server as editor
from libre_panel.sensors import hardware
from libre_panel.weather import open_meteo

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 12), reason="the release's http.server is Python 3.12's"
)

TOKEN = "t" * 43


def _asked(
    port: int, request: bytes, done: bool = True, wait: float = 60
) -> tuple[str, dict[str, str], bytes]:
    """What the server on ``port`` answers to ``request``: its status line, its headers (but
    for the server's name and the date) and its body. ``done``: the request's end said by
    closing the way there (else it stays open, as a browser's that waits for a picture);
    ``wait``: seconds without a word from the server before it is taken for hung."""
    with socket.create_connection(("127.0.0.1", port), timeout=wait) as link:
        link.sendall(request)
        if done:
            link.shutdown(socket.SHUT_WR)
        answer = b""
        while chunk := link.recv(65536):
            answer += chunk
    if not answer.startswith(b"HTTP/"):
        return "", {}, answer  # (a request without a version: the body alone)
    head, _, body = answer.partition(b"\r\n\r\n")
    status, *lines = head.decode("latin-1").split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines)
    assert headers.pop("Date").endswith(" GMT") and "LibrePanelEditor/" in headers.pop("Server")
    return status, headers, body


def _failed(ours: tuple[str, dict[str, str], bytes], asked: Any) -> None:
    """Where server.py does not answer a request (an exception it does not catch: the page
    says Libre Panel does not answer), Rust's answers it with what failed: a 500 and why."""
    status, headers, body = ours
    assert status == "HTTP/1.0 500 Internal Server Error", (asked, ours)
    assert headers["Content-Type"].startswith("application/json"), (asked, ours)
    assert json.loads(body)["error"], (asked, ours)


# what this computer's hardware tells a server without sources: its names
OWN = ("cpu.name", "mem.", "gpu.name", "board.", "system.")


@pytest.fixture
def servers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Both servers on a config.toml of their own: ``ask(request)`` gives each one's answer to
    the request, in which ``{host}`` is the server's name and ``{token}`` its token."""
    homes = []
    for side in ("py", "rs"):
        home = tmp_path / side
        home.mkdir()
        (home / "config.toml").write_text(
            'theme = "slate"\nlanguage = "de"\n[device]\ndriver = "virtual"\n'
            "[sensors]\nproviders = []\n",
            encoding="utf-8",
        )
        # the person's own themes: one with files, one that cannot be read, one with no name
        mine = home / "themes" / "mine"
        shutil.copytree(DATA / "themes" / "slate", mine)
        (mine / "pic.PNG").write_bytes(b"\x89PNG picture")
        (mine / "sub").mkdir()
        (mine / "sub" / "a font.ttf").write_bytes(b"font")
        (mine / "notes.txt").write_bytes(b"notes")
        (mine / "clip.webm").write_bytes(b"clip")
        for name, text in (("broken", "{nope"), ("Bad Name", "{}"), ("list", "[1]"),
                           ("odd", '{"name": " ", "display": {"width": 480, "height": true,'
                            ' "model": 5}, "collection": 7, "widgets": [{"type": "module",'
                            ' "module": "cards", "items": " "}]}')):  # fmt: skip
            (home / "themes" / name).mkdir()
            (home / "themes" / name / "theme.json").write_text(text, encoding="utf-8")
        homes.append(home)
    monkeypatch.setenv("LIBRE_PANEL_HOME", str(homes[0]))
    i18n.set_language("de")
    theirs = editor.make_server(0, homes[0] / "config.toml", token=TOKEN)
    serving = threading.Thread(target=theirs.serve_forever, daemon=True)
    serving.start()
    # Open-Meteo's place search as the tests make it up: its answer, the same for both
    places = tmp_path / "places.json"
    places.write_text("{}", encoding="utf-8")

    class Search(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = places.read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    search = ThreadingHTTPServer(("127.0.0.1", 0), Search)
    threading.Thread(target=search.serve_forever, daemon=True).start()
    search_url = f"http://127.0.0.1:{search.server_address[1]}/search"
    monkeypatch.setattr(open_meteo, "GEOCODING_URL", search_url)
    # GitHub's answer on the releases, the same file for both (the update check's)
    releases = tmp_path / "releases.json"
    theirs.editor_state.updates.fetch = lambda: json.loads(releases.read_text(encoding="utf-8"))
    env = {**os.environ, "LIBRE_PANEL_HOME": str(homes[1]), "LIBRE_PANEL_DATA": str(DATA),
           "LP_RELEASES_FILE": str(releases), "XDG_CONFIG_HOME": str(homes[1] / "xdg"),
           "LP_GEOCODING_URL": search_url,
           **_libraries()}  # fmt: skip
    command = [str(_program()), "editor", "--port", "0"]
    ours = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            encoding="utf-8")  # fmt: skip
    try:
        assert ours.stdout is not None
        said = ours.stdout.readline()
        address = said.split(" running at ")[1].split()[0]
        port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]
        sides = ((theirs.server_address[1], TOKEN), (port, token))

        def ask(request: str | bytes, done: bool = True) -> list[tuple[str, dict[str, str], bytes]]:
            answers = []
            for port, token in sides:
                text = request if isinstance(request, bytes) else request.encode("utf-8")
                text = text.replace(b"{host}", f"127.0.0.1:{port}".encode())
                answers.append(_asked(port, text.replace(b"{token}", token.encode()), done))
            return answers

        yield ask, homes
    finally:
        i18n.set_language("en")
        theirs.shutdown()
        theirs.server_close()
        theirs.editor_state.close()
        search.shutdown()
        if os.name == "nt":
            ours.terminate()
        else:
            ours.send_signal(signal.SIGINT)
        said = ours.communicate(timeout=30)[1]
        assert "panicked" not in said, said


def _get(path: str, *headers: str, method: str = "GET", body: str = "") -> str:
    lines = [f"{method} {path} HTTP/1.1", "Host: {host}", *headers]
    return "\r\n".join(lines) + "\r\n\r\n" + body


WITH = "X-Libre-Panel: {token}"


def test_the_apps_files_and_the_way_in_are_answered_as_server_py_answers(servers: Any) -> None:
    """The app's page, modules, fonts and the old editor's files; what is not there; a wrong
    name of the server or page of another site; the token missing, wrong or in the wrong
    place; requests that are none, in http.server's words; the texts in the app's language
    and the state without a background app."""
    font = next(DATA.joinpath("fonts").glob("*.ttf")).stem
    module = next(path for path in DATA.joinpath("editor", "app").rglob("*.js"))
    module = module.relative_to(DATA / "editor" / "app").as_posix()
    old = next(DATA.joinpath("editor", "static").iterdir()).name
    json = "Content-Type: application/json"
    requests: list[str | bytes] = [
        _get("/"), _get("/index.html"), _get("/index.html?x=1#y"), _get("//index.html"),
        _get("/classic"), _get("/classic/"), _get("/classic/index.html"), _get("/classic/x"),
        _get(f"/app/{module}"), _get("/app/"), _get("/app"), _get("/app/nothing.js"),
        _get("/app/../server.py"), _get("/app/%2e%2e/server.py"), _get("/app/.hidden.js"),
        _get("/app/a/b/c/d/e/f.js"), _get(f"/app/{module}.map"), _get("/app/index.HTML"),
        _get(f"/app/fonts/{font}.ttf"), _get("/app/fonts/Nothing.ttf"),
        _get(f"/app/fonts/{font}.otf"), _get(f"/app/fonts/{font}"), _get("/app/fonts/"),
        _get(f"/static/{old}"), _get("/static/nothing.css"), _get("/static/.hidden"),
        _get("/static/a/b.css"), _get("/static/"),
        # the name of the server, and the page that asks
        "GET / HTTP/1.1\r\nHost: evil.example\r\n\r\n", "GET / HTTP/1.1\r\n\r\n",
        "GET / HTTP/1.1\r\nhOsT:   {host}\r\n\r\n",
        "GET / HTTP/1.1\r\nHost: {host}\r\nHost: evil.example\r\n\r\n",
        _get("/", "Origin: http://evil.example"), _get("/", "Origin: http://{host}"),
        _get("/", "Origin: null"),
        "GET / HTTP/1.1\r\nHost: localhost:{host}\r\n\r\n".replace("{host}", "{host}"),
        # the token
        _get("/api/i18n"), _get("/api/i18n", WITH), _get("/api/i18n", "X-Libre-Panel: wrong"),
        _get("/api/i18n", "x-libre-panel: {token}"), _get("/api/i18n?token={token}"),
        _get("/api/i18n", "X-Libre-Panel: {token}x"), _get("/api/i18n", "X-Libre-Panel: tökén"),
        _get("/api/nothing"), _get("/api/nothing", WITH), _get("/nothing"), _get("/nothing", WITH),
        _get("/api/i18n/", WITH), _get("/api/i18n", WITH, method="POST"),
        # the background app's state and controls, without one
        _get("/api/app", WITH),
        _get("/api/app", WITH, json, "Content-Length: 19", method="POST",
             body='{"action": "pause"}'),
        _get("/api/app", WITH, json, "Content-Length: 5", method="POST", body="{nope"),
        _get("/api/app", WITH, json, "Content-Length: 4", method="POST", body="\xff\xfe12"),
        _get("/api/app", WITH, json, method="POST"),
        _get("/api/app", WITH, json, "Content-Length: 99999999", method="POST", body="{}"),
        _get("/api/app", WITH, json, "Content-Length: many", method="POST", body="{}"),
        _get("/api/app", WITH, json, "Content-Length: -1", method="POST", body="{}"),
        _get("/api/app", WITH, json, "Content-Length:  2 ", method="POST", body="[]"),
        _get("/api/app", json, "Content-Length: 2", method="POST", body="{}"),
        # requests that are none
        _get("/", method="PUT"), _get("/", method="HEAD"), _get("/", method="get"),
        _get("/", method="<b>"),
        "GET /\r\n\r\n", "POST /\r\n\r\n", "GET\r\n\r\n", "\r\n", "GET / HTTP/1.1 more\r\n\r\n",
        "GET / HTTP/2.0\r\nHost: {host}\r\n\r\n", "GET / HTTP/1.x\r\n\r\n",
        "GET / FTP/1.1\r\n\r\n", "GET / HTTP/1.12345678901\r\n\r\n", "GET / HTTP/1\r\n\r\n",
        "GET / HTTP/1.0\r\nHost: {host}\r\n\r\n", "GET  /   HTTP/1.1\r\nHost: {host}\r\n\r\n",
        "GET / HTTP/1.1\nHost: {host}\n\n",
        "GET / HTTP/1.1\r\nHost: {host}\r\nX: " + "y" * 70000 + "\r\n\r\n",
        "GET / HTTP/1.1\r\nHost: {host}\r\n" + "X: y\r\n" * 101 + "\r\n",
        "GET /" + "a" * 70000 + " HTTP/1.1\r\nHost: {host}\r\n\r\n",
        "GET / HTTP/1.1\r\nX-Folded: a\r\n b\r\nHost: {host}\r\n\r\n",
        "GET / HTTP/1.1\r\nno colon here\r\nHost: {host}\r\n\r\n",
        b"GET /app/\xff HTTP/1.1\r\nHost: {host}\r\n\r\n",
    ]  # fmt: skip
    seen: dict[str, int] = {}
    ask, _ = servers
    for request in requests:
        theirs, ours = ask(request)
        shown = request[:200]
        assert ours[0] == theirs[0], shown
        assert ours[1] == theirs[1], shown
        assert ours[2] == theirs[2], shown
        seen[theirs[0]] = seen.get(theirs[0], 0) + 1
    assert {status.split()[1] for status in seen if status} >= {
        "200", "301", "400", "403", "404", "413", "414", "431", "501"}, seen  # fmt: skip
    # (a request without a version, and http.server's errors before it knows the version: the
    # body alone)
    assert seen.get("", 0) >= 5


def test_a_request_no_route_answers_is_answered_where_someone_waits(servers: Any) -> None:
    """A request a route comes back from without an answer (a body that says null is none to
    server.py's routes: they answer nothing, and the page says that Libre Panel does not
    answer): whoever still waits (a browser keeps its connection open) is told so by Rust's
    server, a 500 and what was asked; a client that has closed its side gets nothing, as from
    server.py."""
    ask, _ = servers
    request = _post("/api/app", None)
    theirs, ours = ask(request)
    assert theirs[0] == ours[0] == "" and theirs[2] == ours[2] == b""
    theirs, ours = ask(request, done=False)
    assert theirs[0] == "" and theirs[2] == b""
    _failed(ours, request)
    assert json.loads(ours[2]) == {"error": "POST /api/app: no answer was made"}


def test_the_background_app_is_controlled_through_its_server(tmp_path: Path) -> None:
    """`libre-panel start`: the app's server beside the panel. Its address and token are where
    the programs of this user find them; the state and the controls answer (pause, resume,
    brightness, the picture turned, what is no action), and `quit` ends the program, which
    takes its files away."""
    import time

    config = tmp_path / "config.toml"
    config.write_text(
        'theme = "slate"\nfps = 2\nrefresh_ms = 200\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\nbrightness = 50\n[sensors]\nproviders = []\n',
        encoding="utf-8",
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries()}  # fmt: skip
    with subprocess.Popen([str(_program()), "start", "--port", "0", "--no-browser"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            assert program.stdout is not None
            address = program.stdout.readline().split(" running at ")[1].split()[0]
            port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]
            instance = json.loads((tmp_path / "instance.json").read_text(encoding="utf-8"))
            assert instance == {"pid": program.pid, "editor": f"http://127.0.0.1:{port}/"}
            kept = tmp_path / "private" / "editor.json"
            assert json.loads(kept.read_text(encoding="utf-8")) == {
                "pid": program.pid, "port": port, "token": token}  # fmt: skip
            if os.name == "posix":
                assert kept.stat().st_mode & 0o777 == 0o600
                assert kept.parent.stat().st_mode & 0o777 == 0o700

            def asked(action: Any = None, value: Any = None) -> tuple[str, Any]:
                if action is None:
                    request = _get("/api/app", WITH)
                else:
                    body = json.dumps({"action": action, "value": value})
                    request = _get("/api/app", WITH, f"Content-Length: {len(body)}",
                                   method="POST", body=body)  # fmt: skip
                request = request.replace("{host}", f"localhost:{port}")
                status, _, answer = _asked(port, request.replace("{token}", token).encode())
                return status.split()[1], json.loads(answer)

            end = time.monotonic() + 20
            while asked()[1]["panel"]["state"] != "showing" and time.monotonic() < end:
                time.sleep(0.1)
            status, state = asked()
            assert status == "200" and state["available"] is True and state["can_quit"] is True
            assert state["panel"]["state"] == "showing" and state["panel"]["theme"] == "slate"
            assert (state["brightness"], state["upside_down"]) == (50, False)
            assert asked("pause")[1]["panel"]["state"] == "paused"
            assert asked("brightness", 30)[1]["brightness"] == 30
            assert asked("upside_down", True)[1]["upside_down"] is True
            assert "rotate = 180" in config.read_text(encoding="utf-8")
            assert asked("resume")[1]["panel"]["state"] != "paused"
            for action, value, why in (
                ("brightness", 101, "brightness must be between 0 and 100"),
                ("brightness", True, "brightness must be a whole number"),
                ("brightness", 5.5, "brightness must be a whole number"),
                ("upside_down", 1, "upside_down must be true or false"),
                ("fly", None, "unknown action 'fly'"),
            ):
                assert asked(action, value) == ("400", {"error": why}), action
            assert asked("quit")[0] == "200"
            assert program.wait(timeout=30) == 0
        finally:
            if program.poll() is None:
                program.kill()
        said = program.communicate(timeout=30)[1]
    assert "panicked" not in said and f"theme editor at http://127.0.0.1:{port}/" in said, said
    assert token not in said, said
    assert not (tmp_path / "instance.json").exists() and not kept.exists()


def test_a_transition_is_shown_on_the_panel_as_transition_show_py_shows_it(
    tmp_path: Path,
) -> None:
    """Settings → Transitions → Show on the panel (`show_transition`, transition_show.py): the
    panel goes over to a card with the transition's name (the card Python makes of it, pixel
    for pixel), stays a moment and comes back to the theme, with the transition on its way;
    what is no request for one is refused in service.py's words."""
    import io

    from PIL import Image

    from libre_panel import transition_show
    from libre_panel.plugins.render import RenderContext
    from libre_panel.render.light import role_color
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme import load_theme

    folder = tmp_path / "themes" / "red"
    folder.mkdir(parents=True)
    theme = {"format": "libre-panel-theme/1", "name": "Red",
             "display": {"model": "turing-5", "orientation": "landscape", "width": 800,
                         "height": 480},
             "palette": {"bg": "#DC1E1E", "void": "#101820", "text": "#F0F4FF"},
             "font": "builtin:Barlow-Medium",
             "background": {"color": "@bg", "image": None}, "refresh_ms": 1000,
             "widgets": [{"type": "text", "x": 40, "y": 40, "text": "RED", "size": 90,
                          "color": "#FFFFFF"}]}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        'theme = "red"\nfps = 20\nlanguage = "en"\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = []\n',
        encoding="utf-8",
    )
    context = RenderContext(Renderer(load_theme(folder), preview=True))
    card = transition_show.card("Fade", (800, 480), role_color(context, "void"),
                                role_color(context, "text")).tobytes()  # fmt: skip
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries()}  # fmt: skip
    with subprocess.Popen([str(_program()), "start", "--port", "0", "--no-browser"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            assert program.stdout is not None
            address = program.stdout.readline().split(" running at ")[1].split()[0]
            port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]

            def sent(request: str) -> tuple[str, dict[str, str], bytes]:
                request = request.replace("{host}", f"localhost:{port}")
                return _asked(port, request.replace("{token}", token).encode())

            def asked(value: Any) -> tuple[str, Any]:
                body = json.dumps({"action": "show_transition", "value": value})
                status, _, answer = sent(_get("/api/app", WITH, f"Content-Length: {len(body)}",
                                              method="POST", body=body))  # fmt: skip
                return status.split()[1], json.loads(answer)

            def shown() -> bytes | None:
                status, _, body = sent(_get("/api/plugins/control/live?panel=1&format=PNG&w=800",
                                            WITH))  # fmt: skip
                if " 200 " not in status:
                    return None
                with Image.open(io.BytesIO(body)) as picture:
                    return picture.convert("RGB").tobytes()

            before = _waited(shown, 30)
            assert before is not None and before != card
            expected = 'expected {"name": "...", "label": "..."}'
            for value, why in ((None, expected), ("fade", expected), ({"name": 5}, expected),
                               ({"label": "Fade"}, expected),
                               ({"name": "  "}, "the name of a transition"),
                               ({"name": "x" * 65}, "the name of a transition")):  # fmt: skip
                assert asked(value) == ("400", {"error": why}), value
            assert shown() == before  # (nothing of it on the panel)
            status, state = asked({"name": "fade", "label": "Fade"})
            assert status == "200" and state["panel"]["state"] == "showing", state
            seen: list[str] = []  # what the panel showed, in turn: the theme, between, the card
            end = time.monotonic() + 30
            while time.monotonic() < end:
                frame = shown()
                kind = {before: "theme", card: "card"}.get(frame or b"", "between")
                if not seen or seen[-1] != kind:
                    seen.append(kind)
                if kind == "theme" and "card" in seen:
                    break
                time.sleep(0.02)
        finally:
            program.send_signal(signal.SIGINT) if os.name == "posix" else program.terminate()
            said = program.communicate(timeout=30)[1]
    assert "panicked" not in said and "transition fade" not in said, said
    # (the transition both ways: a fade's pictures between the theme and the card)
    assert seen[-1] == "theme" and "card" in seen, seen
    at = seen.index("card")
    assert "between" in seen[:at] and "between" in seen[at:], seen


def test_the_live_picture_is_the_frame_on_each_panel_as_control_py_makes_it(
    tmp_path: Path,
) -> None:
    """Control's live picture (`/api/plugins/control/live`): the frame on the first panel and
    on the one beside it, as a JPEG of the width asked for (the bytes control.py's encode()
    makes of the same frame) or a PNG (its pixels); none for a panel that is not there, and a
    format it does not make refused."""
    import io
    import time

    from PIL import Image

    from libre_panel.pages import control

    for name, colour in (("red", "#DC1E1E"), ("blue", "#1E1EDC")):
        folder = tmp_path / "themes" / name
        folder.mkdir(parents=True)
        theme = {"format": "libre-panel-theme/1", "name": name.title(),
                 "display": {"model": "turing-5", "orientation": "landscape", "width": 800,
                             "height": 480},
                 "palette": {"bg": colour}, "font": "builtin:Barlow-Medium",
                 "background": {"color": "@bg", "image": None}, "refresh_ms": 1000,
                 "widgets": [{"type": "text", "x": 40, "y": 40, "text": name.upper(),
                              "size": 90, "color": "#FFFFFF"}]}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        'theme = "red"\nfps = 2\nlanguage = "en"\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = []\n'
        '[[panels]]\ntheme = "blue"\ndriver = "virtual"\noutput = "beside.png"\n',
        encoding="utf-8",
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries()}  # fmt: skip
    with subprocess.Popen([str(_program()), "start", "--port", "0", "--no-browser"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            assert program.stdout is not None
            address = program.stdout.readline().split(" running at ")[1].split()[0]
            port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]

            def asked(query: str) -> tuple[str, dict[str, str], bytes]:
                request = _get(f"/api/plugins/control/live?{query}", WITH)
                request = request.replace("{host}", f"localhost:{port}")
                return _asked(port, request.replace("{token}", token).encode())

            end = time.monotonic() + 20
            while not (tmp_path / "beside.png").exists() and time.monotonic() < end:
                time.sleep(0.1)
            answers = {query: asked(query) for query in (
                "panel=1", "panel=1&format=PNG&w=400", "panel=2&w=1920", "panel=2&w=100",
                "panel=3", "panel=1&format=gif")}  # fmt: skip
            time.sleep(2.5)  # (the meter's numbers, made once a second)
            request = _get("/api/plugins/control/panel", WITH)
            request = request.replace("{host}", f"localhost:{port}").replace("{token}", token)
            panel = json.loads(_asked(port, request.encode())[2])["panel"]
        finally:
            program.send_signal(signal.SIGINT) if os.name == "posix" else program.terminate()
            said = program.communicate(timeout=30)[1]
    assert "panicked" not in said, said
    frames = {n: Image.open(tmp_path / file).convert("RGB")
              for n, file in ((1, "panel.png"), (2, "beside.png"))}  # fmt: skip
    for query, n, width, kind in (("panel=1", 1, 960, "jpeg"),
                                  ("panel=1&format=PNG&w=400", 1, 400, "png"),
                                  ("panel=2&w=1920", 2, 1920, "jpeg"),
                                  ("panel=2&w=100", 2, 160, "jpeg")):  # fmt: skip
        status, headers, body = answers[query]
        assert status == "HTTP/1.0 200 OK", (query, status, body[:200])
        assert headers["Content-Type"] == control.PICTURES[kind], query
        made = control.encode(frames[n], width, kind)
        if kind == "jpeg":
            assert body == made, query
        else:  # (the same pixels; zlib's stream is not the one of Pillow's wheel)
            with Image.open(io.BytesIO(body)) as ours, Image.open(io.BytesIO(made)) as theirs:
                assert ours.mode == theirs.mode and ours.tobytes() == theirs.tobytes(), query
    # the meter's numbers as meter.py makes them, for a PNG file at 2 frames a second
    from libre_panel.app import RunStatus
    from libre_panel.meter import Meter

    status = RunStatus()
    meter = Meter(status, 0.0)
    meter.drew(0.01)
    meter.tick(1.0, 2, False)
    stats = panel["stats"]
    assert list(stats) == list(status.stats), stats
    assert {k for k, v in stats.items() if v is None} == {
        k for k, v in status.stats.items() if v is None}, stats  # fmt: skip
    assert stats["target_fps"] == 2 and stats["streaming"] is False and stats["fps"] > 0
    assert isinstance(stats["uptime_s"], int) and stats["render_ms"] > 0
    assert [more["index"] for more in panel["more"]] == [0] and panel["more"][0]["fps"] > 0
    assert answers["panel=3"][0].split()[1] == "404"
    assert json.loads(answers["panel=3"][2]) == {"error": "no picture yet"}
    assert answers["panel=1&format=gif"][0].split()[1] == "400"
    assert json.loads(answers["panel=1&format=gif"][2]) == {"error": "format is jpeg or png"}


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the Linux source")
def test_controls_sources_are_those_the_panel_reads_as_control_py_lists_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control's sources (`/api/plugins/control/sources`) while the panel runs: each source of
    config.toml and those always there, in the Python app's order, with what it says of
    itself and its rows, as control.py lists the sources of the Python app's hub; the numbers
    of readings of the sources whose readings do not come and go."""
    import time
    from types import SimpleNamespace

    from libre_panel.app import build_hub
    from libre_panel.config import load_config
    from libre_panel.pages import control
    from libre_panel.plugins import pages
    from libre_panel.plugins.host import PluginHost

    config = tmp_path / "config.toml"
    config.write_text(
        'theme = "slate"\nfps = 2\nlanguage = "en"\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = ["linux", "psutil"]\n',
        encoding="utf-8",
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries()}  # fmt: skip
    with subprocess.Popen([str(_program()), "start", "--port", "0", "--no-browser"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            assert program.stdout is not None
            address = program.stdout.readline().split(" running at ")[1].split()[0]
            port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]

            def asked(route: str) -> Any:
                request = _get(f"/api/plugins/control/{route}", WITH)
                request = request.replace("{host}", f"localhost:{port}")
                return json.loads(_asked(port, request.replace("{token}", token).encode())[2])

            end = time.monotonic() + 20
            while asked("panel")["panel"]["state"] != "showing" and time.monotonic() < end:
                time.sleep(0.1)
            time.sleep(1.5)  # (a few snapshots taken)
            ours = asked("sources")
        finally:
            program.send_signal(signal.SIGINT) if os.name == "posix" else program.terminate()
            said = program.communicate(timeout=30)[1]
    assert "panicked" not in said, said
    # (the tests' computer is empty, conftest.py: here it is the real one, as the program's)
    spec = importlib.util.find_spec("libre_panel.sensors.hardware")
    assert spec is not None and spec.loader is not None
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    monkeypatch.setattr(hardware, "_scan_machine", fresh._scan_machine)
    from libre_panel.sensors import linux

    monkeypatch.setattr(linux, "ROOT", Path("/"))  # (its chips and cards too)
    hub = build_hub(load_config(config))
    hub.providers.append(PluginHost().provider())
    try:
        hub.snapshot()
        time.sleep(0.3)
        hub.snapshot()
        listed = [{"name": p.name, "readings": hub.readings_from(p),
                   "status": pages._status_of(p)} for p in hub.providers]  # fmt: skip
    finally:
        hub.close()
    context = SimpleNamespace(status=lambda: {"hint": ""}, sources=lambda: listed,
                              background=True)  # fmt: skip
    theirs = {"background": True, "sources": control.Control._sources(SimpleNamespace(
        context=context))}  # fmt: skip
    # (the Linux source's and psutil's numbers: whether there are any; the drives' and the
    # rates' readings come with rounds of their own)
    for each in (*ours["sources"], *theirs["sources"]):
        if each["name"] in ("linux", "psutil"):
            each["readings"] = bool(each["readings"])
    assert ours == theirs


def test_a_theme_that_is_gone_leaves_the_panel_lit_and_is_said_as_app_py_says_it(
    tmp_path: Path,
) -> None:
    """A theme config.toml names that is not installed: the panel shows its own, and the
    panel's state names the missing one with the notice service.py gives; a theme chosen
    later that cannot be shown either leaves the one on show where it is."""
    import time

    from libre_panel.devices.models import find_model
    from libre_panel.theme.defaults import default_theme
    from libre_panel.theme.model import ThemeError, find_theme

    config = tmp_path / "config.toml"
    head = 'fps = 2\nlanguage = "en"\n[device]\ndriver = "virtual"\noutput = "panel.png"\n'
    head += 'model = "turing-5"\n[sensors]\nproviders = []\n'
    config.write_text('theme = "gone"\n' + head, encoding="utf-8")
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries()}  # fmt: skip
    with subprocess.Popen([str(_program()), "start", "--port", "0", "--no-browser"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            assert program.stdout is not None
            address = program.stdout.readline().split(" running at ")[1].split()[0]
            port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]

            def panel() -> Any:
                request = _get("/api/plugins/control/panel", WITH)
                request = request.replace("{host}", f"localhost:{port}")
                answer = _asked(port, request.replace("{token}", token).encode())[2]
                return json.loads(answer)["panel"]

            end = time.monotonic() + 20
            while panel()["state"] != "showing" and time.monotonic() < end:
                time.sleep(0.1)
            first = panel()
            config.write_text('theme = "also-gone"\n' + head, encoding="utf-8")
            time.sleep(3)
            later = panel()
        finally:
            program.send_signal(signal.SIGINT) if os.name == "posix" else program.terminate()
            said = program.communicate(timeout=30)[1]
    own = default_theme(find_model("turing-5"))
    notice = i18n.t(
        "The theme “{name}” is no longer installed, so the panel shows “{shown}”. "
        "Choose a theme in the tray menu or on Start.",
        name="gone",
        shown=own,
    )
    assert (first["state"], first["theme"], first["default_theme"]) == ("showing", own, own)
    assert (first["missing_theme"], first["notice"]) == ("gone", notice), first
    assert list(first)[:6] == ["state", "testing", "detail", "hint", "missing_theme", "notice"]
    whys = []
    for name in ("gone", "also-gone"):
        with pytest.raises(ThemeError) as caught:
            find_theme(name)
        whys.append(str(caught.value))
    lines = [line.removeprefix("libre-panel: ") for line in said.splitlines()]
    showing = f"{whys[0]}: showing {own!r}, the panel's own, until it is back or another is chosen"
    assert showing in lines and f"keeping theme {own!r}: {whys[1]}" in lines, said
    assert later["theme"] == own and later["frames"] > first["frames"], later


def test_one_libre_panel_drives_the_panel_and_a_second_start_names_the_first(
    tmp_path: Path,
) -> None:
    """instance.py's lock: while the background app runs, a second start says where its app
    is (the address with the token) and ends, and `run` is refused with the words cli.py
    has; the lock file holds the first one's process number."""
    (tmp_path / "config.toml").write_text(
        'theme = "slate"\nfps = 2\nlanguage = "en"\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = []\n',
        encoding="utf-8",
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries()}  # fmt: skip
    with subprocess.Popen([str(_program()), "start", "--port", "0", "--no-browser"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            assert program.stdout is not None
            address = program.stdout.readline().split(" running at ")[1].split()[0]
            second = subprocess.run([str(_program()), "start", "--no-browser"], env=env,
                                    capture_output=True, encoding="utf-8", timeout=60)  # fmt: skip
            third = subprocess.run([str(_program()), "run"], env=env, capture_output=True,
                                   encoding="utf-8", timeout=60)  # fmt: skip
            lock = (tmp_path / "libre-panel.lock").read_text(encoding="utf-8")
        finally:
            program.send_signal(signal.SIGINT) if os.name == "posix" else program.terminate()
            program.communicate(timeout=30)
    said = i18n.t("Libre Panel is already running. Theme editor: {url}", url=address)
    assert second.returncode == 0 and second.stdout == said + "\n", second
    refused = i18n.t(
        "Libre Panel is already running and drives the panel (look for its tray icon, or stop "
        "`libre-panel run`/`start` in the other terminal)"
    )
    assert third.returncode != 0 and refused in third.stderr, third
    assert lock == str(program.pid)


def test_a_notice_is_sent_and_libre_panel_quits_as_cli_py_does_it(tmp_path: Path) -> None:
    """cli.py's `notify` and `quit`: a notice sent with the notify key is shown on the panel of
    the running app, and `quit` ends the app (its process gone) with the words cli.py has;
    without a running app both say so. `udev-rules` and `devices` say what cli.py says."""
    (tmp_path / "config.toml").write_text(
        'theme = "slate"\nfps = 2\nlanguage = "en"\nallow_notify = true\n[device]\n'
        'driver = "virtual"\noutput = "panel.png"\n[sensors]\nproviders = []\n',
        encoding="utf-8",
    )
    (tmp_path / "notify.json").write_text('{"key": "a-key-of-the-tests"}', encoding="utf-8")
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries()}  # fmt: skip

    def ours(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(_program()), *args], env=env, capture_output=True,
                              encoding="utf-8", timeout=60)  # fmt: skip

    alone = ours("quit"), ours("notify", "Hello")
    with subprocess.Popen([str(_program()), "start", "--port", "0", "--no-browser"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            assert program.stdout is not None
            program.stdout.readline()  # (its address: the app's server runs)
            shown = ours("notify", "Hello", "--title", "Build", "--seconds", "2")
            wrong = ours("notify", "Hello", "--level", "loud")
            quit = ours("quit")
            program.communicate(timeout=30)
        finally:
            if program.poll() is None:
                program.kill()
    not_running = i18n.t("Libre Panel is not running.") + "\n"
    assert alone[0].returncode == 0 and alone[0].stdout == not_running, alone[0]
    assert alone[1].returncode == 1 and alone[1].stderr == not_running, alone[1]
    assert shown.returncode == 0, shown
    assert wrong.returncode != 0 and "invalid choice: 'loud'" in wrong.stderr, wrong
    assert quit.returncode == 0 and quit.stdout == i18n.t("Libre Panel has quit.") + "\n", quit
    assert program.returncode is not None
    rules = (DATA / "assets" / "60-libre-panel.rules").read_text(encoding="utf-8")
    assert ours("udev-rules").stdout == rules
    listed = ours("devices")
    assert listed.returncode == 0 and listed.stdout.startswith("drivers: auto, turzx, virtual\n")


def test_an_update_is_fetched_checked_and_installed_as_updates_py_does(tmp_path: Path) -> None:
    """updates.py's Install, through the app's server: nothing to install before a download;
    the newer release's setup fetched from the release's files and checked against its
    SHA256SUMS; on the second click the command that runs it once the app has quit (Python's
    restart_command, word for word) started, and the app quits."""
    import hashlib
    import urllib.request

    from libre_panel.editor import updates

    name = "libre-panel-9.1.0-windows-x64-setup.exe"
    content = b"the setup " * 100
    files = tmp_path / "release"
    files.mkdir()
    (files / name).write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    (files / "SHA256SUMS").write_text(f"{'0' * 64}  other.zip\n{digest}  {name}\n")
    own = f"{updates.RELEASE_FILES}v9.1.0/"
    answer = {
        "tag_name": "v9.1.0", "html_url": "https://github.com/syntensa/libre-panel/releases/tag/v9.1.0",
        "body": "What is new.", "published_at": "2026-11-01T10:00:00Z", "prerelease": False,
        "assets": [{"name": name, "size": len(content), "browser_download_url": own + name},
                   {"name": "SHA256SUMS", "size": 99, "browser_download_url": own + "SHA256SUMS"}],
    }  # fmt: skip
    (tmp_path / "releases.json").write_text(json.dumps(answer), encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        'theme = "slate"\nfps = 2\nlanguage = "en"\ncheck_updates = true\n[device]\n'
        'driver = "virtual"\noutput = "panel.png"\n[sensors]\nproviders = []\n',
        encoding="utf-8",
    )
    spawned = tmp_path / "spawned.json"
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           "LP_RELEASES_FILE": str(tmp_path / "releases.json"),
           "LP_RELEASE_FILES_DIR": str(files), "LP_INSTALL_KIND": "setup",
           "LP_INSTALL_SPAWN": str(spawned), **_libraries()}  # fmt: skip

    with subprocess.Popen([str(_program()), "start", "--port", "0", "--no-browser"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            assert program.stdout is not None
            address = program.stdout.readline().split(" running at ")[1].split()[0]
            base, token = address.split("#token=")

            def ask(path: str, payload: Any = None) -> tuple[int, Any]:
                data = None if payload is None else json.dumps(payload).encode()
                headers = {"X-Libre-Panel": token, "Content-Type": "application/json"}
                request = urllib.request.Request(base.rstrip("/") + path, data, headers)
                try:
                    with urllib.request.urlopen(request, timeout=10) as response:
                        return response.status, json.loads(response.read())
                except urllib.error.HTTPError as error:
                    return error.code, json.loads(error.read())

            idle = ask("/api/update/install")
            early = ask("/api/update/install", {"action": "install"})
            assert ask("/api/update?now=1")[1]["newer"] is True
            started = ask("/api/update/install", {"action": "download"})
            ready = _waited(
                lambda: s if (s := ask("/api/update/install")[1])["state"] == "ready" else None, 20
            )
            wrong = ask("/api/update/install", {"action": "?"})
            installing = ask("/api/update/install", {"action": "install"})
            told = _waited(
                lambda: json.loads(spawned.read_text()) if spawned.exists() else None, 20
            )
            command, cwd = told
            program.communicate(timeout=30)
        finally:
            if program.poll() is None:
                program.kill()
    assert idle == (200, updates.IDLE)
    assert early == (409, {"error": "Nothing is downloaded to install yet."})
    assert (
        started[0] == 200 and started[1]["state"] == "downloading" and started[1]["how"] == "setup"
    )
    assert ready == {**updates.IDLE, "state": "ready", "version": "9.1.0", "how": "setup",
                     "file": name, "where": ready["where"], "done": len(content),
                     "total": len(content), "checked": True}  # fmt: skip
    setup = Path(ready["where"]) / name
    assert wrong[0] == 400
    assert installing == (200, {**ready, "state": "installing"})
    assert command == updates.restart_command(
        "setup", program.pid, _program().with_name("LibrePanel.exe"), setup, digest
    )
    assert Path(cwd) == setup.parent.parent
    assert program.returncode is not None


class _Item:
    """pystray's MenuItem as tray.py makes it: what the tests read of it."""

    def __init__(
        self,
        text: Any,
        action: Any,
        checked: Any = None,
        radio: bool = False,
        default: bool = False,
        enabled: bool = True,
        visible: Any = True,
    ) -> None:
        self.args = (text, action, checked, radio, default, enabled, visible)


class _Menu:
    """pystray's Menu: its items, or what makes them as the menu opens."""

    SEPARATOR = object()

    def __init__(self, *items: Any) -> None:
        self.items = items

    def listed(self) -> list[Any]:
        made = self.items[0] if len(self.items) == 1 and callable(self.items[0]) else None
        return list(made()) if made is not None else list(self.items)


def _as_listed(menu: _Menu) -> list[dict[str, Any]]:
    """A menu tray.py made, as /api/tray lists one: the visible items, what each says, whether
    it is on, a choice, the default, and its own menu; separators as they are."""
    told: list[dict[str, Any]] = []
    for item in menu.listed():
        if item is _Menu.SEPARATOR:
            told.append({"separator": True})
            continue
        text, action, checked, radio, default, enabled, visible = item.args
        if not (visible(item) if callable(visible) else visible):
            continue
        told.append({
            "text": text(item) if callable(text) else text,
            "enabled": enabled,
            "checked": checked(item) if callable(checked) else checked,
            "radio": radio,
            "default": default,
            "items": _as_listed(action) if isinstance(action, _Menu) else None,
        })  # fmt: skip
    return told


def _without_ids(menu: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """/api/tray's menu without what the tray's program sends back (each item's id)."""
    return [
        {**{k: v for k, v in item.items() if k != "id"},
         **({"items": _without_ids(item["items"])} if item.get("items") else {})}
        for item in menu
    ]  # fmt: skip


def test_the_trays_menu_is_tray_pys_and_what_is_chosen_in_it_is_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tray.py's menu as the Rust app makes it for the tray's program (GET /api/tray): the
    headline, the themes for the panel by name (those of a set apart, the one shown checked),
    the scenes, the panel beside the first with its theme and brightness, the brightness steps,
    the switches, the views and Quit; each item's words, whether it is on and a choice, the
    same. And what is chosen in it (POST /api/tray) is done: a brightness step, a scene, the
    panel beside switched on, the panel paused, Quit; the icon's picture is branding.py's."""
    import types

    from PIL import Image

    from libre_panel.branding import logo
    from libre_panel.service import BackgroundApp
    from libre_panel.tray import Tray, tooltip

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        'theme = "slate"\nfps = 2\nlanguage = "en"\n[device]\ndriver = "virtual"\n'
        f'brightness = 50\noutput = "{(home / "panel.png").as_posix()}"\n[sensors]\n'
        'providers = []\n[[panels]]\ntheme = "orbit"\non = false\n[panels.device]\n'
        'driver = "virtual"\n'
        f'output = "{(home / "panel2.png").as_posix()}"\n[[scenes]]\nname = "Work"\n'
        'themes = ["slate"]\n[[scenes]]\nname = "Play"\nthemes = ["orbit"]\n',
        encoding="utf-8",
    )
    folder = home / "themes" / "desk"
    folder.mkdir(parents=True)
    data = {"format": "libre-panel-theme/1", "name": "Desk", "collection": "Desk Set",
            "display": {"model": "turing-9.2-usb", "orientation": "landscape"},
            "widgets": [{"type": "text", "id": "t", "text": "Desk"}]}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("LIBRE_PANEL_HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))

    def showing(state: dict[str, Any]) -> bool:
        return state["state"] == "showing"

    app = BackgroundApp(port=0)
    app.start()
    try:
        assert _waited(lambda: True if showing(app.panel.state()) else None, 60)
        tray = Tray(app, types.SimpleNamespace(Menu=_Menu, MenuItem=_Item), log_path=None)
        theirs = _as_listed(tray.build_menu())
        their_tip = tooltip(app.panel.state())
    finally:
        app.quit()
        app.shutdown()
    env = {**os.environ, "LIBRE_PANEL_DATA": str(DATA), **_libraries()}
    with subprocess.Popen([str(_program()), "start", "--port", "0", "--no-browser"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            assert program.stdout is not None
            address = program.stdout.readline().split(" running at ")[1].split()[0]
            port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]

            def ask(request: str) -> tuple[str, dict[str, str], bytes]:
                request = request.replace("{host}", f"localhost:{port}")
                return _asked(port, request.replace("{token}", token).encode())

            def told() -> dict[str, Any]:
                return dict(json.loads(ask(_get("/api/tray", WITH))[2]))

            def choose(item_id: str) -> None:
                status = ask(_post("/api/tray", {"id": item_id}).decode())[0]
                assert " 200 " in status, (item_id, status)

            def state() -> dict[str, Any]:
                return dict(json.loads(ask(_get("/api/app", WITH))[2])["panel"])

            assert _waited(lambda: True if showing(state()) else None, 60)
            ours = told()
            assert _without_ids(ours["menu"]) == theirs
            assert ours["title"] == their_tip and ours["state"] == "showing"
            ids = {i["text"]: i for i in ours["menu"] if "text" in i}
            assert ids["Open Libre Panel"]["id"] == "open"
            picture = ask(_get("/api/tray/icon?state=paused&size=32", WITH))[2]
            got = Image.open(__import__("io").BytesIO(picture))
            assert got.tobytes() == logo(32, "paused").tobytes()
            # what is chosen is done, as the tray's actions do it
            # (each in a thread of its own, as tray.py's: one after the other here, each
            # written before the next is chosen)
            config = home / "config.toml"

            def written() -> dict[str, Any]:
                return dict(__import__("tomllib").loads(config.read_text(encoding="utf-8")))

            for item_id, done in (
                ("brightness:75", lambda c: c["device"].get("brightness") == 75),
                ("scene:Play", lambda c: c.get("scene") == "Play"),
                ("panel-on:0", lambda c: c["panels"][0].get("on", True) is True),
            ):
                choose(item_id)
                assert _waited(lambda done=done: True if done(written()) else None, 30), item_id
            choose("pause")
            assert _waited(lambda: True if state()["state"] == "paused" else None, 30)
            choose("quit")
            program.wait(timeout=30)
        finally:
            if program.poll() is None:
                program.send_signal(signal.SIGINT)
            program.communicate(timeout=30)
    assert program.returncode == 0


def _waited(found: Any, seconds: float) -> Any:
    """What ``found()`` gives once it gives something, or None after ``seconds``."""
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if (what := found()) is not None:
            return what
        time.sleep(0.1)
    return None


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("Xvfb") is None,
    reason="the window on a screen of Xvfb's",
)
def test_the_app_opens_its_window_one_window_and_closes_it_as_window_py_does(
    tmp_path: Path,
) -> None:
    """window.py: `start` opens the app in a window of its own, a process apart that says
    where it is (window.json: its process, the server's port and token, what it shows); a
    second start brings that window forward and opens no other; as the app quits the window
    closes, keeps its size (window-state.json) and says it is gone. No browser is opened."""
    (tmp_path / "config.toml").write_text(
        'theme = "slate"\nfps = 2\nlanguage = "en"\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = []\n',
        encoding="utf-8",
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    opened = tmp_path / "browser.txt"
    (tools / "xdg-open").write_text(f'#!/bin/sh\necho "$@" >> {opened}\n', encoding="utf-8")
    (tools / "xdg-open").chmod(0o755)
    read, write = os.pipe()
    screen = subprocess.Popen(["Xvfb", "-displayfd", str(write), "-screen", "0", "1920x1080x24"],
                              pass_fds=(write,), stderr=subprocess.DEVNULL)  # fmt: skip
    os.close(write)
    try:
        display = os.read(read, 16).decode().strip()
    finally:
        os.close(read)
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           "DISPLAY": f":{display}", "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
           "WAYLAND_DISPLAY": "", **_libraries()}  # fmt: skip
    running = tmp_path / "window.json"

    def window() -> dict[str, Any] | None:
        try:
            return dict(json.loads(running.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    try:
        with subprocess.Popen([str(_program()), "start", "--port", "0"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8") as program:  # fmt: skip
            try:
                assert program.stdout is not None
                address = program.stdout.readline().split(" running at ")[1].split()[0]
                first = _waited(window, 60)
                second = subprocess.run([str(_program()), "start"], env=env, timeout=60,
                                        capture_output=True, encoding="utf-8")  # fmt: skip
                again = window()
            finally:
                program.send_signal(signal.SIGINT)
                program.communicate(timeout=30)
        assert first is not None, (tmp_path / "logs" / "window.log").read_text(errors="replace")
        port = int(address.split(":")[2].split("/")[0])
        assert sorted(first) == ["pid", "port", "token", "url"]
        # (its own port, where it answers the other starts; the page it shows, on the app's)
        assert first["port"] != port and first["url"] == f"http://127.0.0.1:{port}/#/home"
        assert first["token"] and first["token"] not in address  # (its own, not the app's)
        assert second.returncode == 0, second
        assert again == first  # (the same window, brought forward)
        gone = _waited(lambda: True if not running.exists() else None, 30)
        assert gone, "the window did not close as the app quit"
        state = json.loads((tmp_path / "window-state.json").read_text(encoding="utf-8"))
        assert sorted(state) == ["height", "maximized", "width"]
        assert not opened.exists(), opened.read_text()
        said = (tmp_path / "logs" / "window.log").read_text(encoding="utf-8")
        assert said.startswith("--- ") and " the window starts\n" in said, said
    finally:
        screen.terminate()
        screen.wait(timeout=30)


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("Xvfb") is None,
    reason="the window on a screen of Xvfb's",
)
def test_a_window_just_closed_is_opened_anew_by_the_next_start(tmp_path: Path) -> None:
    """A closed window leaves nothing for another start to find, before its web view is taken
    down (which can take seconds, WebView2's above all; LP_WINDOW_LINGER_S makes it 30 here):
    its file is gone at once and it answers no start, so the next one (a click on the tray icon
    right after closing the window) opens a window of its own, where it was told before that
    the closed one had come to the front. The closed one's process ends within seconds,
    whatever its web view does. No browser is opened."""
    (tmp_path / "config.toml").write_text(
        'theme = "slate"\nfps = 2\nlanguage = "en"\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = []\n',
        encoding="utf-8",
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    opened = tmp_path / "browser.txt"
    (tools / "xdg-open").write_text(f'#!/bin/sh\necho "$@" >> {opened}\n', encoding="utf-8")
    (tools / "xdg-open").chmod(0o755)
    read, write = os.pipe()
    screen = subprocess.Popen(["Xvfb", "-displayfd", str(write), "-screen", "0", "1920x1080x24"],
                              pass_fds=(write,), stderr=subprocess.DEVNULL)  # fmt: skip
    os.close(write)
    try:
        display = os.read(read, 16).decode().strip()
    finally:
        os.close(read)
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           "DISPLAY": f":{display}", "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
           "WAYLAND_DISPLAY": "", "LP_WINDOW_LINGER_S": "30", **_libraries()}  # fmt: skip
    running = tmp_path / "window.json"

    def window() -> dict[str, Any] | None:
        try:
            return dict(json.loads(running.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def alive(pid: int) -> bool:
        try:  # (a process that ended and was not waited for is there no more)
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except OSError:
            return False

    def told(first: dict[str, Any], *words: str) -> str:
        """What the window answers another start: `close <token>`, `focus <token> <url>`."""
        with socket.create_connection(("127.0.0.1", first["port"]), timeout=10) as link:
            link.sendall((" ".join([words[0], first["token"], *words[1:]]) + "\n").encode())
            return link.makefile().readline().strip()

    try:
        with subprocess.Popen([str(_program()), "start", "--port", "0"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8") as program:  # fmt: skip
            try:
                assert program.stdout is not None
                program.stdout.readline()
                first = _waited(window, 60)
                assert first is not None, "no window"
                assert told(first, "close") == "ok"
                closed = time.monotonic()
                # gone for the other starts at once, its process still at its web view's end
                assert _waited(lambda: True if window() is None else None, 5), window()
                assert alive(first["pid"])
                assert told(first, "focus", first["url"]) == "no"
                again = subprocess.run([str(_program()), "start"], env=env, timeout=60,
                                       capture_output=True, encoding="utf-8")  # fmt: skip
                second = _waited(window, 60)
                # (the first one's end is not waited for: within its five seconds and a few)
                ended = _waited(lambda: True if not alive(first["pid"]) else None, 20)
                took = time.monotonic() - closed
            finally:
                program.send_signal(signal.SIGINT)
                program.communicate(timeout=30)
        assert again.returncode == 0, again
        assert second is not None and second["pid"] != first["pid"], (first, second)
        assert second["url"] == first["url"]
        assert ended and took < 25, took
        assert not opened.exists(), opened.read_text()
    finally:
        screen.terminate()
        screen.wait(timeout=30)


@pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or not (shutil.which("Xvfb") and shutil.which("stalonetray")),
    reason="the tray icon on a screen of Xvfb's, in stalonetray's tray",
)
def test_the_tray_shows_its_icon_and_its_quit_ends_libre_panel(tmp_path: Path) -> None:
    """tray.py's run_app: `libre-panel tray` shows its icon (the program libre-panel-tray,
    in an X tray: "tray icon ready"), which asks the app for its menu; Quit chosen in it ends
    the icon and Libre Panel, which leaves its log in the settings folder. (The icon only
    where config.toml asks for it: tray_icon, off unless turned on.)"""
    (tmp_path / "config.toml").write_text(
        'theme = "slate"\nfps = 2\nlanguage = "en"\ntray_icon = true\n[device]\n'
        'driver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = []\n',
        encoding="utf-8",
    )
    read, write = os.pipe()
    screen = subprocess.Popen(["Xvfb", "-displayfd", str(write), "-screen", "0", "1280x800x24"],
                              pass_fds=(write,), stderr=subprocess.DEVNULL)  # fmt: skip
    os.close(write)
    try:
        display = os.read(read, 16).decode().strip()
    finally:
        os.close(read)
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           "DISPLAY": f":{display}", "WAYLAND_DISPLAY": "", "XDG_CONFIG_HOME": str(tmp_path),
           **_libraries()}  # fmt: skip
    tray = subprocess.Popen(["stalonetray"], env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)  # fmt: skip
    try:
        with subprocess.Popen([str(_program()), "tray", "--background", "--port", "0"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8") as program:  # fmt: skip
            try:
                assert program.stdout is not None and program.stderr is not None
                address = program.stdout.readline().split(" running at ")[1].split()[0]
                port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]
                said = []
                for _ in range(200):  # (what the icon's program says, through the app's)
                    said.append(program.stderr.readline())
                    if "tray icon ready" in said[-1] or "no tray icon" in said[-1]:
                        break
                assert "libre-panel: tray icon ready\n" in said, said
                # (the icon's program among the app's (logind's monitor is one too)
                children = Path(f"/proc/{program.pid}/task/{program.pid}/children")

                def named(pid: str) -> str:
                    try:
                        return Path(f"/proc/{pid}/exe").resolve().name
                    except OSError:
                        return ""

                icon = int(next(p for p in children.read_text().split()
                                if named(p) == "libre-panel-tray"))  # fmt: skip
                request = _post("/api/tray", {"id": "quit"}).decode()
                request = request.replace("{host}", f"localhost:{port}")
                assert " 200 " in _asked(port, request.replace("{token}", token).encode())[0]
                program.wait(timeout=30)
            finally:
                if program.poll() is None:
                    program.send_signal(signal.SIGINT)
                program.communicate(timeout=30)
        assert program.returncode == 0
        assert not Path(f"/proc/{icon}").exists() or "Z" in Path(f"/proc/{icon}/stat").read_text()
        assert (tmp_path / "logs" / "libre-panel.log").is_file()
    finally:
        tray.terminate()
        tray.wait(timeout=30)
        screen.terminate()
        screen.wait(timeout=30)


@pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or not (shutil.which("Xvfb") and shutil.which("stalonetray")),
    reason="the tray icon on a screen of Xvfb's, in stalonetray's tray",
)
def test_the_tray_icon_follows_its_setting_and_comes_back_when_it_ends(tmp_path: Path) -> None:
    """The icon is there where Settings → System asks for it (`tray_icon`, off unless turned
    on): turned on while Libre Panel runs it appears, without a new start; an icon that had
    its place and ends by itself (the desktop's tray went away under it) is started again;
    turned off it goes, and Libre Panel runs on without one."""
    (tmp_path / "config.toml").write_text(
        'theme = "slate"\nfps = 2\nlanguage = "en"\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = []\n',
        encoding="utf-8",
    )
    read, write = os.pipe()
    screen = subprocess.Popen(["Xvfb", "-displayfd", str(write), "-screen", "0", "1280x800x24"],
                              pass_fds=(write,), stderr=subprocess.DEVNULL)  # fmt: skip
    os.close(write)
    try:
        display = os.read(read, 16).decode().strip()
    finally:
        os.close(read)
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           "DISPLAY": f":{display}", "WAYLAND_DISPLAY": "", "XDG_CONFIG_HOME": str(tmp_path),
           **_libraries()}  # fmt: skip
    tray = subprocess.Popen(["stalonetray"], env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)  # fmt: skip
    said: list[str] = []
    try:
        with subprocess.Popen([str(_program()), "tray", "--background", "--port", "0"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8") as program:  # fmt: skip
            try:
                assert program.stdout is not None and program.stderr is not None
                address = program.stdout.readline().split(" running at ")[1].split()[0]
                port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]
                heard = threading.Thread(target=lambda: said.extend(program.stderr), daemon=True)
                heard.start()
                children = Path(f"/proc/{program.pid}/task/{program.pid}/children")

                def icons() -> list[int]:
                    def named(pid: str) -> str:
                        try:
                            return Path(f"/proc/{pid}/exe").resolve().name
                        except OSError:
                            return ""

                    return [int(p) for p in children.read_text().split()
                            if named(p) == "libre-panel-tray"]  # fmt: skip

                def sent(request: bytes) -> str:
                    request = request.replace(b"{host}", f"localhost:{port}".encode())
                    return _asked(port, request.replace(b"{token}", token.encode()))[0]

                def ready(times: int) -> bool | None:
                    return said.count("libre-panel: tray icon ready\n") >= times or None

                time.sleep(2.5)  # (the setting is looked at once a second)
                assert icons() == [] and not ready(1), said  # off unless turned on
                assert " 200 " in sent(_post("/api/settings", {"set": {"tray_icon": True}}))
                assert _waited(lambda: ready(1), 20), said
                [first] = icons()
                # an icon that had its place (it ran a while) ends: it is started again
                time.sleep(10.5)
                os.kill(first, signal.SIGKILL)
                assert _waited(lambda: ready(2), 20), said
                [second] = icons()
                assert second != first
                assert any("the tray icon's program ended" in line and "started again" in line
                           for line in said), said  # fmt: skip
                assert " 200 " in sent(_post("/api/settings", {"set": {"tray_icon": False}}))
                assert _waited(lambda: icons() == [] or None, 20), icons()
                assert program.poll() is None  # (Libre Panel runs on without its icon)
                assert " 200 " in sent(_post("/api/tray", {"id": "quit"}))
                program.wait(timeout=30)
            finally:
                if program.poll() is None:
                    program.send_signal(signal.SIGINT)
                program.wait(timeout=30)
        assert program.returncode == 0, said
    finally:
        tray.terminate()
        tray.wait(timeout=30)
        screen.terminate()
        screen.wait(timeout=30)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a Linux without a desktop")
def test_a_tray_icon_that_ends_at_once_is_tried_again_after_a_while(tmp_path: Path) -> None:
    """Where the icon's program ends as soon as it starts (here: no desktop to put it on; at a
    computer's start: an app too busy to answer it, a tray that is not there yet), Libre Panel
    runs on without it and tries again after a while, where it gave up for good before."""
    (tmp_path / "config.toml").write_text(
        'theme = "slate"\nfps = 2\nlanguage = "en"\ntray_icon = true\n[device]\n'
        'driver = "virtual"\noutput = "panel.png"\n[sensors]\nproviders = []\n',
        encoding="utf-8",
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           "DISPLAY": "", "WAYLAND_DISPLAY": "", **_libraries()}  # fmt: skip
    said: list[str] = []
    with subprocess.Popen([str(_program()), "tray", "--background", "--port", "0"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            assert program.stdout is not None and program.stderr is not None
            program.stdout.readline()
            heard = threading.Thread(target=lambda: said.extend(program.stderr), daemon=True)
            heard.start()
            ended = _waited(lambda: next((line for line in said
                                          if "the tray icon's program ended" in line), None), 20)  # fmt: skip
            assert program.poll() is None  # (Libre Panel runs on)
        finally:
            program.send_signal(signal.SIGINT)
            program.wait(timeout=30)
    assert program.returncode == 0, said
    assert any("no tray icon (no desktop)" in line for line in said), said
    assert ended is not None and ended.rstrip().endswith(": tried again in 20 s"), said


@pytest.mark.skipif(sys.platform != "win32", reason="the end of a Windows session")
def test_libre_panel_has_stopped_when_windows_gets_its_answer_at_the_sessions_end(
    tmp_path: Path,
) -> None:
    """winsession.py: the background app's hidden window answers Windows' question whether the
    session may end at once (never holding it up), and answers the end itself only once the
    app has stopped: its panel let go, its address no longer written down; a cancelled end
    changes nothing."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                    wintypes.LPARAM]  # fmt: skip
    user32.SendMessageW.restype = ctypes.c_ssize_t
    query, end = 0x0011, 0x0016  # WM_QUERYENDSESSION, WM_ENDSESSION
    (tmp_path / "config.toml").write_text(
        'theme = "slate"\nfps = 2\nlanguage = "en"\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = []\n',
        encoding="utf-8",
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries()}  # fmt: skip
    with subprocess.Popen([str(_program()), "start", "--port", "0", "--no-browser"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            assert program.stdout is not None
            program.stdout.readline()
            window = _waited(lambda: user32.FindWindowW("LibrePanelSession", None) or None, 30)
            assert window, "no session window"
            assert user32.SendMessageW(window, query, 0, 0) == 1  # never blocks the end
            user32.SendMessageW(window, end, 0, 0)  # another program cancelled it
            assert program.poll() is None
            assert (tmp_path / "instance.json").is_file()
            user32.SendMessageW(window, end, 1, 0)  # returns once the app has stopped
            assert not (tmp_path / "instance.json").exists()
            assert program.wait(timeout=30) == 0
        finally:
            if program.poll() is None:
                program.terminate()
            program.communicate(timeout=30)


def test_the_themes_are_listed_and_read_as_server_py_reads_them(servers: Any) -> None:
    """The themes that are installed (the app's and the person's own: one with files, one
    that cannot be read, one of an odd shape), a theme by its name with what is said of it,
    the files in its folder, and a file of it, with the token in the header or, as a picture
    asks, in the address."""
    ask, homes = servers
    requests = [
        _get("/api/themes", WITH), _get("/api/themes"), _get("/api/themes/", WITH),
        _get("/api/themes/slate", WITH), _get("/api/themes/mine", WITH),
        _get("/api/themes/nothing", WITH), _get("/api/themes/broken", WITH),
        _get("/api/themes/list", WITH), _get("/api/themes/odd", WITH),
        _get("/api/themes/Bad%20Name", WITH), _get("/api/themes/mine/x", WITH),
        _get("/api/themes/../mine", WITH), _get("/api/themes/slate?x=1", WITH),
        _get("/api/themes/mine/assets", WITH), _get("/api/themes/slate/assets", WITH),
        _get("/api/themes/nothing/assets", WITH), _get("/api/themes/mine/assets/", WITH),
        _get("/api/themes/mine/assets/pic.PNG", WITH), _get("/api/themes/mine/assets/pic.PNG"),
        _get("/api/themes/mine/assets/pic.PNG?token={token}"),
        _get("/api/themes/mine/assets/pic.PNG?token=wrong"),
        _get("/api/themes/mine/assets/pic.png", WITH),
        _get("/api/themes/mine/assets/sub/a%20font.ttf", WITH),
        _get("/api/themes/mine/assets/sub%2Fa%20font.ttf", WITH),
        _get("/api/themes/mine/assets/sub/a font.ttf", WITH),
        _get("/api/themes/mine/assets/clip.webm", WITH),
        _get("/api/themes/mine/assets/notes.txt", WITH),
        _get("/api/themes/mine/assets/theme.json", WITH),
        _get("/api/themes/mine/assets/sub", WITH),
        _get("/api/themes/mine/assets/nothing.png", WITH),
        _get("/api/themes/mine/assets/../../broken/theme.json", WITH),
        _get("/api/themes/mine/assets/%2e%2e/%2e%2e/config.toml", WITH),
        _get("/api/themes/mine/assets//etc/passwd", WITH),
        _get("/api/themes/nothing/assets/pic.PNG", WITH),
    ]  # fmt: skip
    seen: dict[str, int] = {}
    for request in requests:
        answers = []
        for home, (status, headers, body) in zip(homes, ask(request), strict=True):
            for path in (str(home), json.dumps(str(home))[1:-1]):
                body = body.replace(path.encode(), b"<home>")
            headers.pop("Content-Length")  # (the folders' names are of one length on Linux)
            answers.append((status, headers, body))
        assert answers[1] == answers[0], request
        seen[answers[0][0]] = seen.get(answers[0][0], 0) + 1
    assert seen["HTTP/1.0 200 OK"] >= 10 and seen["HTTP/1.0 404 Not Found"] >= 10, seen
    assert seen["HTTP/1.0 403 Forbidden"] >= 3, seen


def test_the_readings_and_the_sensors_are_told_as_server_py_tells_them(servers: Any) -> None:
    """The readings for the app's pickers (all of them, or those asked for, with the history
    of some) and every key a theme can show: the demo's where the computer gives none, each
    with its unit, label and whether it is a sample. The values move with the time: their
    kinds are compared."""
    ask, _ = servers

    def told(request: str) -> list[Any]:
        answers = []
        for status, headers, body in ask(request):
            assert status == "HTTP/1.0 200 OK" and headers["Content-Type"] == "application/json"
            answers.append(json.loads(body))
        return answers

    for query in ("", "?keys=cpu.load,nothing,time.second,_x,cpu.name,,gpu.temp&history=cpu.load,,",
                  "?keys=mem.load&keys=fan.cpu&history=gpu.load&history=cpu.core.3.load",
                  "?history=net.down", "?keys=downloads,timer.text,ai.claude.task"):  # fmt: skip
        theirs, ours = told(_get(f"/api/readings{query}", WITH))
        assert ours["live"] is theirs["live"] is True, query  # (the moon, at the least)
        assert list(ours["readings"]) == list(theirs["readings"]), query
        for key, reading in theirs["readings"].items():
            mine = ours["readings"][key]
            if key.startswith(OWN):  # (the tests keep Python from this computer's names)
                mine["sample"] = reading["sample"]
            kinds = (type(reading.pop("value")), type(mine.pop("value")))
            assert mine == reading and kinds[0] is kinds[1], (query, key)
        assert {k: len(v) for k, v in ours["history"].items()} == {
            k: len(v) for k, v in theirs["history"].items()}, query  # fmt: skip
        assert all(isinstance(v, float) for values in ours["history"].values() for v in values)
    theirs, ours = told(_get("/api/readings", WITH))
    assert len(theirs["readings"]) > 400 and "time.second" in ours["readings"]
    theirs, ours = told(_get("/api/sensors", WITH))
    assert ours == theirs and len(ours) > 400
    assert {"media.cover", "game.name", "cpu.load"} <= {entry["key"] for entry in ours}


def _post(path: str, payload: Any) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    lines = [f"POST {path} HTTP/1.1", "Host: {host}", WITH, "Content-Type: application/json",
             f"Content-Length: {len(body)}"]  # fmt: skip
    return "\r\n".join(lines).encode("utf-8") + b"\r\n\r\n" + body


# fmt: off
SETTINGS = (  # what is written, a request each: what is refused leaves the file as it is
    {"device.brightness": 70, "refresh_ms": 500, "device.rotate": 180},
    {"refresh_ms": None, "device.at": [10, -20], "across": "  a picture.png "},
    {"device.at": None, "across": None, "device.orientation": "portrait"},
    {"device.brightness": 101}, {"device.brightness": True}, {"device.brightness": 7.0},
    {"device.brightness": None}, {"device.rotate": 90}, {"device.rotate": 180.0},
    {"device.model": "auto"}, {"device.model": "nothing"}, {"device.model": 5},
    {"device.driver": "turzx"}, {"device.driver": "other"}, {"device.motion": "calm"},
    {"device.still": "gif"}, {"device.video_range": "full"}, {"device.jpeg": "4:2:0"},
    {"device.jpeg_quality": 49}, {"device.jpeg_quality": 95},
    {"device.night": True, "device.night_from": "7:30", "device.night_to": "sunrise"},
    {"device.night_from": "25:00"}, {"device.night_to": 7}, {"device.dark_on_quit": 1},
    {"device.at": [1]}, {"device.at": [1, 2.5]}, {"device.at": [0, 5001]}, {"device.at": "x"},
    {"device.at": []}, {"device.output": " "}, {"device.output": "out.png"},
    {"device.port": "COM3"}, {"device.port": "x" * 65}, {"device.port": "a\x00b"},
    {"video.mode": "on", "video.fps": 30, "video.device_fps": 120, "video.crf": 18},
    {"video.fps": 61}, {"video.preset": "medium"}, {"video.preset": "slowest"},
    {"video.keyframe_s": 2}, {"video.keyframe_s": 0.5}, {"video.keyframe_s": "2"},
    {"video.maxrate": " 2M "}, {"video.maxrate": "1500k"}, {"video.maxrate": "2.5M"},
    {"video.maxrate": "2G"}, {"video.maxrate": ".5M"}, {"video.maxrate": 2},
    {"video.profile": "high", "video.encoder": "native", "video.engine_encoder": "card"},
    {"video.pixels": "rgb", "video.direct": True, "video.rest": False},
    {"video.local_clip": "clip.mp4", "video.clip_asked": True, "video.ffmpeg": "ffmpeg"},
    {"video.engine_sends": False, "video.engine_layers": "no"},
    {"check_updates": True, "allow_notify": True, "symbols": "classic"},
    {"symbols": "other"}, {"cards": ["ai for 5m", "date", "playback"]}, {"cards": []},
    {"cards": ["ai", "ai"]}, {"cards": ["nothing"]}, {"cards": "ai"}, {"cards": [1]},
    {"cards": ["ai for long"]}, {"cards": None}, {"card_order": "turns"},
    {"card_transition": "wipe"}, {"card_transition": "tube"}, {"accent": "#e0457b"},
    {"accent": "system"}, {"accent": "red"}, {"accent": ""}, {"accent": "#E0457B1"},
    {"card_opacity": 0}, {"card_opacity": 0.0}, {"card_opacity": False}, {"card_opacity": 39},
    {"card_opacity": 80}, {"light_colours": True, "graph_steps": True},
    {"hotkeys.enabled": False, "hotkeys.pause": "ctrl+alt+p", "hotkeys.timer": "Ctrl+Alt+T"},
    {"hotkeys.open": "Ctrl+Alt+P"}, {"hotkeys.open": "p"}, {"hotkeys.open": ""},
    {"hotkeys.pause": "Ctrl+Alt+P"}, {"hotkeys.pause": None}, {"hotkeys.other": "Ctrl+Alt+O"},
    {"hotkeys.enabled": None},
    {"sensors.providers": ["psutil", "load", "psutil"]}, {"sensors.providers": ["psutil"]},
    {"sensors.providers": []}, {"sensors.providers": ["nothing"]}, {"sensors.providers": "x"},
    {"sensors.librehardwaremonitor.mode": "webserver",
     "sensors.librehardwaremonitor.url": "http://127.0.0.1:9/data.json"},
    {"sensors.librehardwaremonitor.url": "ftp://x"}, {"sensors.json.update_s": 1},
    {"sensors.json.update_s": 60},
    {"sensors.json.sources": [
        {"name": "home", "where": "http://127.0.0.1:9/a.json", "fields": {" t ": " a.b "},
         "units": {"t": "°C"}},
        {"name": "file_1", "where": "values.json", "fields": {}, "units": None}]},
    {"sensors.json.sources": [{"name": "a b", "where": "x"}]},
    {"sensors.json.sources": [{"name": "a", "where": "x"}, {"name": "a", "where": "y"}]},
    {"sensors.json.sources": [{"name": "a"}]}, {"sensors.json.sources": [{"where": "x"}]},
    {"sensors.json.sources": [5]}, {"sensors.json.sources": {}},
    {"sensors.json.sources": [{"name": "a", "where": "x", "fields": {"": "v"}}]},
    {"sensors.json.sources": [{"name": "a", "where": "x", "fields": [1]}]},
    {"sensors.json.sources": []},
    {"weather.enabled": True}, {"weather.latitude": 52.52}, {"weather.longitude": 181},
    {"weather.latitude": 52.52, "weather.longitude": 13, "weather.place": " Berlin ",
     "weather.enabled": True, "weather.units": "imperial", "weather.update_minutes": 30},
    {"weather.latitude": None}, {"weather.latitude": None, "weather.longitude": None},
    {"weather.enabled": False, "weather.latitude": None, "weather.longitude": None,
     "weather.place": None},
    {"weather.update_minutes": 4}, {"weather.units": None},
    {"transitions.default": "tube", "transitions.speed": "slow", "transitions.game": "",
     "transitions.media": "random", "transitions.random": ["fade", "cut", "fade"]},
    {"transitions.default": ""}, {"transitions.speed": "slower"}, {"transitions.other": "cut"},
    {"transitions.random": []}, {"transitions.random": ["nothing"]}, {"transitions.game": 5},
    {"switches.glow": True, "switches.lumen.glow": False}, {"switches.glow": None},
    {"switches.Glow": True}, {"switches.a.b.c": True}, {"switches.glow": "on"},
    {"switches.": True},
    {"services.timer.minutes": 5}, {"services.timer.nothing": 5}, {"services.nothing.x": 5},
    {"services.timer": 5}, {"services.timer.minutes": None}, {"services.timer.minutes": "5"},
    {"services.game.games": ["a.exe", 5, 1.5, True]}, {"services.game.games": [["a"]]},
    {"services.game.games": [None]}, {"services.cooling.database": "cooling.db"},
    {"services.notices.sound": True}, {"services.power.idle_minutes": 2.5},
    {"panels": [{"theme": "slate", "on": False, "model": "auto", "brightness": 40,
                 "video": {"mode": "auto", "local_clip": "", "clip_asked": True},
                 "at": [100, 0], "follow": None, "output": "libre-panel-frame-2.png"},
                {"theme": "", "driver": "virtual", "follow": True, "brightness_offset": -10,
                 "alarm_theme": "lumen-companion", "game_theme": "arena", "at": None}]},
    {"panels": [{"video": {"mode": "fast"}}]}, {"panels": [{"video": {"other": 1}}]},
    {"panels": [{"video": []}]}, {"panels": [{"nothing": 1}]}, {"panels": [{"beside": []}]},
    {"panels": [{"brightness": 101}]}, {"panels": [{"brightness_offset": 51}]},
    {"panels": [{"follow": 1}]}, {"panels": [{"model": None}]}, {"panels": [5]},
    {"panels": [{}, {}, {}, {}]}, {"panels": {}}, {"panels": [{"night_from": "sunset"}]},
    {"panels": []},
    {"nothing": 1}, {"device.nothing": 1}, {"theme": "slate"}, {"language": "en"},
    {"device.brightness": 55, "device.rotate": 90},  # (one refused: none written)
)
OTHER = ([], {}, {"set": {}}, {"set": []}, {"set": 5}, 5, "set",
         {"set": {f"switches.s{n}": True for n in range(61)}})  # fmt: skip
SCENES = (
    {"action": "save", "name": "Work"}, {"action": "save", "name": " Play "},
    {"action": "on", "name": "Work"}, {"action": "on", "name": "nothing"},
    {"action": "on"}, {"action": "save", "name": ""}, {"action": "save", "name": "x" * 41},
    {"action": "delete", "name": "Play"}, {"action": "delete", "name": "Play"},
    {"action": "off", "name": "Work"}, {"action": "on", "name": 5}, {"name": "Work"}, [], 5,
    {"action": "on", "name": "Work"},
)
# fmt: on


def _with_rights() -> bool:
    """Whether the tests run with administrator rights (a CI runner on Windows) or as root (a
    container): the program sees that, whatever the tests make Python believe."""
    if sys.platform == "win32":
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    return os.geteuid() == 0


def test_the_settings_are_read_and_written_as_settings_py_does(
    servers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app's Settings: config.toml with the defaults filled in and what there is to choose
    from; each setting written by its rule, or refused in the same words; the panels beside
    the first, the JSON sources, the switches and the services' options; the scenes kept,
    put on and taken out; what the About page says. After each request the two config.toml
    files hold the same. With the highest rights the settings that name a program or a file
    are refused, by both."""
    from libre_panel.editor import access

    ask, homes = servers
    monkeypatch.setattr(access, "elevated", _with_rights)

    def both(request: str | bytes, what: Any) -> Any:
        theirs, ours = ask(request)
        told = []
        for home, (status, headers, body) in zip(homes, (theirs, ours), strict=True):
            assert headers["Content-Type"] == "application/json", what
            text = body.decode("utf-8").replace(json.dumps(str(home))[1:-1], "HOME")
            told.append((status, json.loads(text)))
        assert told[1] == told[0], what
        files = [(home / "config.toml").read_text(encoding="utf-8") for home in homes]
        assert files[1] == files[0], what
        return told[0]

    status, read = both(_get("/api/settings", WITH), "read")
    assert status == "HTTP/1.0 200 OK" and len(read["catalog"]["services"]) == 11
    assert read["config"]["device"]["driver"] == "virtual" and read["restart"] == []
    written = 0
    for changes in SETTINGS:
        status, told = both(_post("/api/settings", {"set": changes}), changes)
        written += status == "HTTP/1.0 200 OK"
        assert ("error" in told) is (status != "HTTP/1.0 200 OK"), changes
    assert 40 < written < len(SETTINGS) - 50
    for payload in OTHER:
        assert both(_post("/api/settings", payload), payload)[0] == "HTTP/1.0 400 Bad Request"
    # (a body that says null is no body to Python's routes: nothing is answered)
    theirs, ours = ask(_post("/api/settings", None))
    assert ours == theirs == ("", {}, b"")
    status, read = both(_get("/api/settings", WITH), "read again")
    assert read["restart"] and read["config"]["panels"] == []
    for payload in SCENES:
        both(_post("/api/scenes", payload), payload)
    status, scenes = both(_get("/api/scenes", WITH), "scenes")
    assert [scene["name"] for scene in scenes["scenes"]] == ["Work"] and scenes["on"] == "Work"
    # a scene is on: the panels written anew take it off
    both(_post("/api/settings", {"set": {"panels": [{"theme": "slate"}]}}), "panels")
    assert both(_get("/api/scenes", WITH), "scenes")[1]["on"] == ""
    # a config.toml that cannot be read
    for home in homes:
        (home / "config.toml").write_text("theme = 5\n", encoding="utf-8")
    for request in (_get("/api/settings", WITH), _get("/api/scenes", WITH),
                    _post("/api/settings", {"set": {"refresh_ms": 500}}),
                    _post("/api/scenes", {"action": "on", "name": ""})):  # fmt: skip
        assert both(request, request)[0] in ("HTTP/1.0 409 Conflict", "HTTP/1.0 400 Bad Request")
    theirs, ours = (json.loads(body) for _, _, body in ask(_get("/api/about", WITH)))
    for home, about in zip(homes, (theirs, ours), strict=True):
        assert about.pop("version") and about["folders"]["config"] == str(home)
        about["folders"] = {k: v.replace(str(home), "HOME") for k, v in about["folders"].items()}
    assert ours == theirs and "Pillow" in ours["notice"]


def test_the_panel_and_the_clip_question_are_told_as_settings_py_tells_them(
    servers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What Start asks about the panel, with none plugged in: the model that is set, what
    smooth motion makes of it and what it lacks (on this panel and on those beside it),
    whether to ask how black looks and for the standby clip's name, the theme that shows the
    weather. (Libre Panel's engine encodes: Python is told its ffmpeg is there.)"""
    from libre_panel.devices import h264
    from libre_panel.devices.models import find_model

    ask, homes = servers
    monkeypatch.setattr(h264, "find_ffmpeg", lambda path="": "ffmpeg")
    proven, seen, unproven = "turing-9.2-usb", "turing-12.3-usb", "turing-8.8-usb"
    assert [find_model(m).video for m in (proven, seen, unproven)] == [True, True, False]
    serial, other = "turing-3.5", "waveshare-2.8-udisp"
    configs = ['[device]\ndriver = "virtual"\n', ""]
    for model in (proven, seen, unproven, serial, other, "jonsbo-zc360-screen"):
        for video in ("", 'mode = "on"', 'mode = "off"', 'local_clip = "clip.mp4"',
                      'mode = "on"\nlocal_clip = "c"', "clip_asked = true"):  # fmt: skip
            for more in ("", 'driver = "turzx"\nvideo_range = "full"', 'driver = "virtual"'):
                configs.append(f'[device]\nmodel = "{model}"\n{more}\n[video]\n{video}\n')
    configs.append(f'theme = "lumen"\n[device]\nmodel = "{proven}"\n[[panels]]\n'
                   f'model = "{seen}"\nvideo = {{ mode = "auto", local_clip = "x" }}\n'
                   f'[[panels]]\nmodel = "{unproven}"\nvideo = {{ mode = "on" }}\n'
                   f"[[panels]]\nvideo = {{ mode = \"auto\" }}\n")  # fmt: skip
    configs.append("theme = 5\n")
    asked = 0
    for config in configs:
        for home in homes:
            (home / "config.toml").write_text(config, encoding="utf-8")
        for path in ("/api/panel", "/api/clip"):
            theirs, ours = ask(_get(path, WITH))
            assert ours[0] == theirs[0] and json.loads(ours[2]) == json.loads(theirs[2]), config
            asked += json.loads(theirs[2]).get("ask", False)
    assert asked >= 3


# fmt: off
PAGES = (  # what a page is to show, a request each
    {"page": "normal", "theme": "lumen"}, {"page": "normal", "theme": "nothing"},
    {"page": "normal", "theme": ""}, {"page": "normal"}, {"page": "normal", "fps": 5},
    {"page": "normal", "theme": "slate", "on": True}, {"page": "normal", "theme": 5},
    {"page": "game", "theme": "arena", "fps": 30}, {"page": "game", "fps": 2.5},
    {"page": "game", "fps": True}, {"page": "game", "on": True}, {"page": "game", "on": False},
    {"page": "game", "on": 1}, {"page": "game", "theme": ""}, {"page": "game"},
    {"page": "media", "on": True}, {"page": "media", "theme": "encore", "on": False},
    {"page": "idle", "on": True, "minutes": 2.5}, {"page": "idle", "minutes": 10},
    {"page": "idle", "minutes": 0.4}, {"page": "idle", "minutes": True},
    {"page": "media", "minutes": 5}, {"page": "idle", "minutes": "5"},
    {"page": "report", "theme": "round-report"}, {"page": "report", "on": True},
    {"page": "report", "on": False}, {"page": "report"}, {"page": "report", "theme": ""},
    {"page": "report", "fps": 5}, {"page": "film", "theme": "terra", "fps": 24, "on": True},
    {"page": "film", "on": False}, {"page": "Film", "theme": "terra"}, {"page": 5}, {},
    [], 5, {"page": "x" * 33, "theme": "terra"}, {"page": "a-b_2", "theme": "slate"},
    {"page": "normal", "theme": "lumen", "panel": 1}, {"page": "normal", "theme": "lumen",
                                                       "panel": 1.0},
    {"page": "normal", "theme": "slate", "panel": 2}, {"page": "normal", "theme": "slate",
                                                       "panel": 3},
    {"page": "normal", "theme": "slate", "panel": "2"}, {"page": "normal", "panel": 2},
    {"page": "normal", "follow": True, "panel": 2}, {"page": "game", "theme": "arena",
                                                     "panel": 2},
    {"page": "game", "theme": "", "panel": 2}, {"page": "media", "panel": 2},
    {"page": "alarm", "theme": "nothing", "panel": 2}, {"page": "idle", "theme": "slate",
                                                        "panel": 2},
    {"page": "normal", "theme": "", "panel": 2}, {"page": "game", "follow": True, "panel": 2},
    {"page": "normal", "theme": "lumen-companion", "panel": 2},
    {"page": "normal", "follow": True, "panel": 2}, {"page": "alarm", "theme": "slate",
                                                     "panel": 0},
)
# fmt: on


def test_the_pages_of_the_panel_are_told_and_set_as_server_py_does(servers: Any) -> None:
    """The pages of the panel: the theme it shows normally, each mode's and the round
    report's, those of a panel beside the first; a page given a theme, a frame rate, its
    minutes, switched on and off, or refused in the same words. After each request the two
    config.toml files hold the same."""
    ask, homes = servers
    start = ('theme = "slate"\nlanguage = "de"\n[device]\ndriver = "virtual"\n[sensors]\n'
             'providers = []\n[[sequence.steps]]\ntheme = "slate"\nseconds = 20\n'
             '[[sequence.steps]]\ntheme = "lumen"\nseconds = 30\n[[panels]]\n'
             'driver = "virtual"\n')  # fmt: skip
    for home in homes:
        (home / "config.toml").write_text(start, encoding="utf-8")

    def both(request: str | bytes, what: Any) -> Any:
        theirs, ours = ask(request)
        assert (ours[0], json.loads(ours[2])) == (theirs[0], json.loads(theirs[2])), what
        files = [(home / "config.toml").read_text(encoding="utf-8")
                 for home in homes if (home / "config.toml").exists()]  # fmt: skip
        assert len(files) != 1 and files[1:] == files[:1], what
        return theirs[0], json.loads(theirs[2])

    done = 0
    for query in ("", "?panel=1", "?panel=2", "?panel=3", "?panel=0", "?panel=x", "?panel=",
                  "?panel=%202&panel=1", "?panel=+2"):  # fmt: skip
        both(_get(f"/api/pages{query}", WITH), query)
    for payload in PAGES:
        status, told = both(_post("/api/pages", payload), payload)
        done += status == "HTTP/1.0 200 OK"
        both(_get("/api/pages", WITH), payload)
        both(_get("/api/pages?panel=2", WITH), payload)
    assert 20 < done < len(PAGES) - 15
    assert told["error"] == "no such panel"
    for home in homes:
        (home / "config.toml").unlink()
    status, told = both(_get("/api/pages", WITH), "no file")
    assert told["chosen"] is False and told["pages"][0]["theme"] == told["default"]
    for home in homes:
        (home / "config.toml").write_text("theme = 5\n", encoding="utf-8")
    for request in (
        _get("/api/pages", WITH),
        _post("/api/pages", {"page": "game", "on": True}),
        _post("/api/pages", {"page": "normal", "theme": "slate", "panel": 2}),
    ):
        assert both(request, request)[0] == "HTTP/1.0 409 Conflict"


def test_the_screens_in_turn_are_told_and_set_as_server_py_does(servers: Any) -> None:
    """The screens in turn: their steps, whether they show, the shortest and longest step;
    steps written in their largest whole unit, or refused in the same words."""
    ask, homes = servers
    # fmt: off
    payloads = (
        {"on": True, "steps": [{"theme": "slate", "seconds": 300},
                               {"theme": " lumen ", "seconds": 7200}]},
        {"on": True, "steps": [{"theme": "slate", "seconds": 90}, {"theme": "terra",
                                                                   "seconds": 30.4}]},
        {"on": False, "steps": [{"theme": "slate", "seconds": 61}]},
        {"on": True, "steps": []}, {"on": True}, {"on": "yes", "steps": []}, {"steps": []},
        {"on": True, "steps": [{"theme": "slate", "seconds": 4}]},
        {"on": True, "steps": [{"theme": "slate", "seconds": 604801}]},
        {"on": True, "steps": [{"theme": "slate", "seconds": 4.6}]},
        {"on": True, "steps": [{"theme": "slate"}]},
        {"on": True, "steps": [{"theme": "slate", "seconds": True}]},
        {"on": True, "steps": [{"theme": "slate", "seconds": "60"}]},
        {"on": True, "steps": [{"theme": "slate", "seconds": -60}]},
        {"on": True, "steps": [{"theme": "", "seconds": 60}]},
        {"on": True, "steps": [{"seconds": 60}]}, {"on": True, "steps": [{"theme": 5,
                                                                          "seconds": 60}]},
        {"on": True, "steps": [{"theme": "nothing", "seconds": 60}]},
        {"on": True, "steps": [{"theme": "slate", "seconds": 60, "minutes": 5}]},
        {"on": True, "steps": ["slate"]}, {"on": True, "steps": {}}, [], 5,
        {"on": True, "steps": [{"theme": "slate", "seconds": 3600}, {"theme": "lumen",
                                                                     "seconds": 86400}]},
    )
    # fmt: on
    for payload in (*payloads, None):
        for request in (_post("/api/sequence", payload), _get("/api/sequence", WITH)):
            theirs, ours = ask(request)
            assert ours[0] == theirs[0], payload
            if theirs[2]:
                assert json.loads(ours[2]) == json.loads(theirs[2]), payload
            else:
                assert ours[2] == theirs[2], payload
            files = [(home / "config.toml").read_text(encoding="utf-8") for home in homes]
            assert files[1] == files[0], payload
    assert "hours = 24" in files[0] and "on = true" in files[0]


def test_the_news_are_told_and_closed_as_news_py_does(servers: Any) -> None:
    """Start's card of what is new: news.json written on the first ask (a config.toml that
    names a theme: an older Libre Panel chose it), a release's news closed, or refused in the
    same words; the file the same after each request."""
    ask, homes = servers
    requests = [_get("/api/news", WITH)]
    for seen in ("0.8", "10.12", "0.8.1", "x", "", 8, None, "1234.5678", "12345.1", ".8"):
        requests += [_post("/api/news", {"seen": seen}), _get("/api/news", WITH)]
    requests += [_post("/api/news", []), _post("/api/news", "0.9")]
    for request in requests:
        theirs, ours = ask(request)
        assert ours[0] == theirs[0] and json.loads(ours[2]) == json.loads(theirs[2]), request
        files = [(home / "news.json").read_text(encoding="utf-8") for home in homes]
        assert files[1] == files[0], request
    assert json.loads(files[0]) == {"first": "older", "seen": "1234.5678"}
    # a damaged file: worked out anew, as on a first ask
    for home in homes:
        (home / "news.json").write_text('{"first": 5}', encoding="utf-8")
    theirs, ours = ask(_get("/api/news", WITH))
    assert json.loads(ours[2]) == json.loads(theirs[2]) == {"first": "older", "seen": ""}


def _release(tag: str, **more: Any) -> dict[str, Any]:
    files = f"https://github.com/syntensa/libre-panel/releases/download/{tag}/"
    names = [f"libre-panel-{tag}-windows-x64-setup.exe", f"libre-panel-{tag}-linux-x64.tar.gz",
             "SHA256SUMS", f"libre-panel-{tag}-x86_64.AppImage"]  # fmt: skip
    assets = [{"name": n, "browser_download_url": files + n, "size": 1000 + i}
              for i, n in enumerate(names)]  # fmt: skip
    page = f"https://github.com/syntensa/libre-panel/releases/tag/{tag}"
    return {"tag_name": tag, "html_url": page,
            "body": "What is new:\nmore.\n\nThe rest. ä", "published_at": "2026-11-01T10:00:00Z",
            "draft": False, "prerelease": False, "assets": assets, **more}  # fmt: skip


def test_the_update_check_is_told_as_updates_py_tells_it(servers: Any) -> None:
    """Whether a newer version is out: GitHub's answer (the same for both) picked as
    updates.py picks it (drafts never, pre-releases only of the running one's minor version,
    the highest), what Start shows of it, updates.json, a card closed ("later"), nothing asked
    while check_updates is off. The moments of the asks are not compared."""
    ask, homes = servers
    releases = homes[0].parent / "releases.json"
    sums = "https://github.com/syntensa/libre-panel/releases/download/v0.8.2/SHA256SUMS"
    answers = [
        [_release("v0.8.1"), _release("v0.8.2rc1", prerelease=True), _release("v0.9.0rc1"),
         _release("v9.0.0", draft=True), _release("0.8.0")],
        [_release("v0.8.1rc3", prerelease=True), _release("v0.8.1rc1"), {"tag_name": 5}, 5],
        [_release("v0.8.1rc3", html_url="http://example.com/x"), _release("v0.7.9")],
        [_release("vnope"), _release("0.8.1.1rc2", body="  \n\nlater\n\n" + "x" * 900)],
        _release("v1.0.0"), _release("v0.8.0"), _release("v0.9.0", prerelease=True), [], {},
        _release("v0.8.2", assets=[{"name": "../x", "browser_download_url": "https://x"},
                                   {"name": "SHA256SUMS", "browser_download_url": sums}]),
        "nothing",
    ]  # fmt: skip

    def both(request: str | bytes, what: Any) -> Any:
        theirs, ours = ask(request)
        assert ours[0] == theirs[0], what
        told = [json.loads(body) for _, _, body in (theirs, ours)]
        for one in told:
            if isinstance(one.get("asked"), float):
                one["asked"] = "a moment"
            if one.get("error") and "answer named no release" not in one["error"]:
                one["error"] = "an error"
        assert told[1] == told[0], what
        kept = []
        for home in homes:
            path = home / "updates.json"
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            if data and "asked" in data:
                data["asked"] = "a moment"
                if data["error"] and "answer named no release" not in data["error"]:
                    data["error"] = "an error"
            kept.append(data)
        assert kept[1] == kept[0], what
        return told[0]

    for check in ("", "check_updates = false\n", "check_updates = true\n"):
        for home in homes:
            (home / "config.toml").write_text(check + 'theme = "slate"\n', encoding="utf-8")
            (home / "updates.json").unlink(missing_ok=True)
        releases.write_text(json.dumps(answers[0]), encoding="utf-8")
        told = both(_get("/api/update", WITH), check)
        assert (told["latest"] is not None) is ("true" in check), told
    seen = set()
    for answer in answers:
        releases.write_text(json.dumps(answer), encoding="utf-8")
        told = both(_get("/api/update?now=1", WITH), answer)
        seen.add(((told["latest"] or {}).get("version"), told["newer"]))
        both(_get("/api/update", WITH), answer)  # (asked a moment ago: not asked again)
    assert len(seen) > 5
    releases.unlink()
    both(_get("/api/update?now", WITH), "no answer")
    for later in ("0.9.0", "v1.0", "nope", "", 5, None):
        both(_post("/api/update", {"later": later}), later)
    both(_post("/api/update", []), "no table")
    for home in homes:
        (home / "updates.json").write_text("[1]", encoding="utf-8")
    both(_get("/api/update", WITH), "a file of no table")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the XDG autostart entry")
def test_starting_with_the_system_is_told_and_set_as_settings_py_does(
    servers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Settings → System: the start at login (the desktop entry, the same file but for the
    program it starts), the language; switched on and off, or refused in the same words.
    And the command line's `autostart`, which writes the same entry."""
    from libre_panel import autostart

    ask, homes = servers
    monkeypatch.setenv("XDG_CONFIG_HOME", str(homes[0] / "xdg"))
    entries = [home / "xdg" / "autostart" / "libre-panel.desktop" for home in homes]

    def both(request: str | bytes, what: Any) -> Any:
        theirs, ours = ask(request)
        assert ours[0] == theirs[0], what
        told = [json.loads(body) for _, _, body in (theirs, ours)]
        for one, home in zip(told, homes, strict=True):
            if "program" in one:
                # (whether the user may write over the program each side starts: its own,
                # the Python app's interpreter)
                program = one["program"]
                risky = bool(program) and autostart.writable_by_user(Path(program), Path.home())
                assert one.pop("risky") == risky, (what, program)
                one["program"] = "a program"
                one["location"] = one["location"].replace(str(home), "HOME")
        assert told[1] == told[0], what
        assert [entry.exists() for entry in entries] == [entries[0].exists()] * 2, what
        if entries[0].exists() and what != "hidden":
            lines = [entry.read_text(encoding="utf-8").splitlines() for entry in entries]
            mine = [line for line in lines[1] if line.startswith("Exec=")]
            program = str(Path(__file__).parents[1] / "rust" / "target" / "release")
            assert mine and mine[0].startswith("Exec=") and "tray --background" in mine[0]
            assert program in mine[0] or "libre-panel" in mine[0]
            assert [x for x in lines[1] if not x.startswith("Exec=")] == [
                x for x in lines[0] if not x.startswith("Exec=")]  # fmt: skip
        return told[0]

    told = both(_get("/api/system", WITH), "read")
    assert told["kind"] == "xdg" and told["enabled"] is False
    for payload in ({"autostart": True}, {"autostart": True}, {"elevated": True},
                    {"elevated": False}, {"autostart": False}, {"autostart": False},
                    {"autostart": 1}, {}, {"other": True}, [], {"autostart": True},
                    {"elevated": "yes"}):  # fmt: skip
        both(_post("/api/system", payload), payload)
    entries[0].write_text("[Desktop Entry]\nHidden = TRUE \n", encoding="utf-8")
    entries[1].write_text("[Desktop Entry]\nHidden = TRUE \n", encoding="utf-8")
    assert both(_get("/api/system", WITH), "hidden")["enabled"] is False
    # the command line: the same entry as the app writes, and what it says
    env = {**os.environ, "LIBRE_PANEL_HOME": str(homes[1]), "LIBRE_PANEL_DATA": str(DATA),
           "XDG_CONFIG_HOME": str(homes[1] / "xdg"), "LANGUAGE": "", "LANG": "C"}  # fmt: skip
    where = str(entries[1])
    for action, said in (
        ("disable", i18n.t("Autostart removed.")),
        ("status", i18n.t("Autostart is disabled.")),
        ("enable", i18n.t("Libre Panel starts in the background when you log in ({where}).",
                          where=where)),
        ("status", i18n.t("Autostart is enabled ({where}).", where=where)),
        ("enable --elevated", i18n.t("Starting with the highest rights is for Windows only.")),
    ):  # fmt: skip
        done = subprocess.run([str(_program()), "autostart", *action.split()], env=env,
                              capture_output=True, encoding="utf-8", timeout=60)  # fmt: skip
        assert said in done.stdout + done.stderr, (action, done)
        assert (done.returncode == 0) is ("elevated" not in action), (action, done)
    # (written by the command line without --config: the start at login takes the usual file)
    command = [str(_program()), "tray", "--background"]
    exec_line = f"Exec={autostart.desktop_exec(command)}"
    assert exec_line in entries[1].read_text(encoding="utf-8").splitlines()


def test_the_video_experiment_is_told_and_refused_as_experiment_py_does(servers: Any) -> None:
    """Settings → Panel → Video experiment (/api/experiment) of a server without the running
    app: the run that never ran, and a start refused (it needs the app), an action that is
    none refused, in the same words."""
    ask, _ = servers
    requests = (
        _get("/api/experiment", WITH),
        _post("/api/experiment", {"action": "start"}),
        _post("/api/experiment", {"action": "go"}),
        _post("/api/experiment", []),
    )
    for request in requests:
        theirs, ours = ask(request)
        assert ours[0] == theirs[0] and json.loads(ours[2]) == json.loads(theirs[2]), request


def test_the_video_experiment_runs_on_the_app_and_brings_the_settings_back(
    tmp_path: Path,
) -> None:
    """The experiment on the running app: its steps as experiment.py makes them (the card's
    encoder only where the graphics card has one), the first step's settings written, and
    when it is stopped the settings from before it back in config.toml."""
    from libre_panel.editor import experiment

    (tmp_path / "config.toml").write_text(
        'theme = "slate"\nfps = 2\nlanguage = "en"\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = []\n[video]\ncrf = 23\n',
        encoding="utf-8",
    )
    before = __import__("tomllib").loads((tmp_path / "config.toml").read_text())["video"]
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries()}  # fmt: skip
    with subprocess.Popen([str(_program()), "start", "--port", "0", "--no-browser"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            assert program.stdout is not None
            address = program.stdout.readline().split(" running at ")[1].split()[0]
            port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]

            def ask(request: str | bytes) -> tuple[str, dict[str, str], bytes]:
                text = request.decode() if isinstance(request, bytes) else request
                text = text.replace("{host}", f"localhost:{port}")
                return _asked(port, text.replace("{token}", token).encode())

            started = json.loads(ask(_post("/api/experiment", {"action": "start"}))[2])
            want = [{"id": s.id, "changes": s.changes, "seconds": s.seconds}
                    for s in experiment.steps("")]  # fmt: skip
            assert started["steps"] == want and started["running"] is True
            config = tmp_path / "config.toml"

            def video() -> dict[str, Any]:
                return dict(__import__("tomllib").loads(config.read_text())["video"])

            # (the first step: the measured settings, crf 20 among them)
            assert _waited(lambda: True if video().get("crf") == 20 else None, 30), video()
            assert json.loads(ask(_post("/api/experiment", {"action": "start"}))[2]) == {
                "error": "the experiment is running"}  # fmt: skip
            ask(_post("/api/experiment", {"action": "stop"}))
            ended = _waited(lambda: (lambda s: s if s["ended"] else None)(
                json.loads(ask(_get("/api/experiment", WITH))[2])), 30)  # fmt: skip
            assert ended and ended["ended"] == "stopped" and not ended["running"], ended
            assert video()["crf"] == before["crf"]
        finally:
            program.send_signal(signal.SIGINT)
            program.communicate(timeout=30)


def test_the_report_the_place_search_and_the_folders_are_as_settings_py_has_them(
    servers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report of a panel for the form on GitHub (this computer, the panel found, what is
    plugged in); the place search refused in the same words, or unreachable (the tests'
    network has no Open-Meteo); a folder shown, or why it cannot be."""
    ask, homes = servers
    places = homes[0].parent / "places.json"
    # (the tests' stand-in for pyusb has no backend: the bus as the program sees it here)
    from libre_panel.devices import base

    monkeypatch.setattr(base, "list_usb_devices", lambda: [])
    theirs, ours = (json.loads(body) for _, _, body in ask(_get("/api/report", WITH)))
    assert ours == theirs and "Libre Panel" in ours["text"]
    for home in homes:
        (home / "config.toml").write_text('[device]\nvideo_range = "full"\n', encoding="utf-8")
    theirs, ours = (json.loads(body) for _, _, body in ask(_get("/api/report", WITH)))
    assert ours == theirs and "Video range: full" in ours["text"]
    for payload in ({"query": "x"}, {"query": "  x  "}, {"query": "x" * 101}, {"query": 5}, {},
                    []):  # fmt: skip
        theirs, ours = ask(_post("/api/location", payload))
        assert (ours[0], json.loads(ours[2])) == (theirs[0], json.loads(theirs[2])), payload
    found = {"results": [
        {"name": "Berlin", "admin1": "Land Berlin", "country": "Deutschland",
         "latitude": 52.52437, "longitude": 13.41053},
        {"name": "Berlin", "admin1": "", "country": "USA", "latitude": 44, "longitude": -71.18},
        {"name": "Nirgends", "latitude": "52", "longitude": 1.0},
        {"name": "Nur Name", "latitude": 1.005, "longitude": -0.125},
    ]}  # fmt: skip
    for answer in (found, {}, {"results": None}, [], "not json"):
        places.write_text(json.dumps(answer) if answer != "not json" else answer, "utf-8")
        for query in ("Berlin", " " + "x" * 100 + " "):
            theirs, ours = ask(_post("/api/location", {"query": query}))
            assert ours[0] == theirs[0], (answer, query)
            if theirs[0] == "HTTP/1.0 200 OK":
                assert json.loads(ours[2]) == json.loads(theirs[2]), (answer, query)
    for payload in ({"folder": "logs"}, {"folder": "config"}, {"folder": "other"}, {}, []):
        theirs, ours = ask(_post("/api/open", payload))
        assert (ours[0], json.loads(ours[2])) == (theirs[0], json.loads(theirs[2])), payload


def test_the_pictures_of_the_themes_are_drawn_as_server_py_draws_them(servers: Any) -> None:
    """A saved theme as a picture for the lists, a width asked or not, on a panel's size or
    not, with the person's accent, cards, switches, see-through cards and light colours; a
    theme that is not there or cannot be read."""
    from io import BytesIO

    import numpy as np
    from PIL import Image

    ask, homes = servers
    requests = [
        "/api/themes/slate/preview", "/api/themes/lumen/preview?w=320",
        "/api/themes/terra/preview?w=10", "/api/themes/arena/preview?w=x",
        "/api/themes/mine/preview?w=2000&size=800x480",
        "/api/themes/lumen/preview?size=1920x480", "/api/themes/slate/preview?size=480x480",
        "/api/themes/almanac/preview?w=+300&size=12x5", "/api/themes/nothing/preview",
        "/api/themes/broken/preview", "/api/themes/list/preview",
    ]  # fmt: skip

    def compare() -> None:
        for path in requests:
            theirs, ours = ask(_get(path, WITH), done=False)
            assert ours[0] == theirs[0], path
            if theirs[1].get("Content-Type") != "image/png":
                told = [
                    json.loads(body.replace(json.dumps(str(home))[1:-1].encode(), b"<home>"))
                    for home, (_, _, body) in zip(homes, (theirs, ours), strict=True)
                ]
                assert told[1] == told[0], path
                continue
            pictures = [Image.open(BytesIO(body)) for _, _, body in (theirs, ours)]
            assert pictures[1].mode == pictures[0].mode == "RGB", path
            assert pictures[1].size == pictures[0].size, path
            # (the themes' light blurred in floating point, as numpy's BLAS adds it: now and
            # then a pixel a level apart, as in the renderer's own comparisons)
            apart = np.abs(np.asarray(pictures[1], int) - np.asarray(pictures[0], int))
            assert apart.max() <= 1, path
            far = (apart.max(axis=2) > 0).sum()
            assert far <= max(2, apart.shape[0] * apart.shape[1] // 10000), (path, far)

    compare()
    # (a browser that left: nobody to draw for, nothing answered)
    assert ask(_get("/api/themes/encore/preview", WITH)) == [("", {}, b"")] * 2
    for home in homes:
        (home / "config.toml").write_text(
            'accent = "#ff8800"\ncard_opacity = 40\nlight_colours = true\n'
            'cards = ["forecast", "music"]\nsymbols = "classic"\n'
            "[switches.'*']\nseconds = false\n[switches.lumen]\nseconds = true\n",
            encoding="utf-8",
        )
    compare()


def test_the_themes_on_every_panel_size_are_told_as_server_py_tells_them(servers: Any) -> None:
    """Every panel size and how a theme comes on it: made for it, laid out from its version
    for the nearest shape, or scaled into it; the app's themes, one's own (with versions for
    more than one size and with one), a theme that is not there or cannot be read."""
    ask, homes = servers
    for home in homes:  # (one's own with a version for a second size)
        for name in ("contrast", "contrast-480x480"):
            shutil.copytree(
                DATA / "themes" / name, home / "themes" / name.replace("contrast", "own")
            )
    for theme in ("slate", "lumen", "arena", "terra", "contrast", "mine", "own", "own-480x480",
                  "broken", "list", "nothing", "Bad%20Name"):  # fmt: skip
        theirs, ours = ask(_get(f"/api/themes/{theme}/sizes", WITH))
        assert ours[0] == theirs[0], theme
        assert json.loads(ours[2]) == json.loads(theirs[2]), theme
    told = json.loads(ask(_get("/api/themes/lumen/sizes", WITH))[0][2])
    assert {entry["how"] for entry in told["sizes"]} >= {"made", "laid_out"}, told


def _zip_of(files: dict[str, bytes], **options: Any) -> bytes:
    """A zip of ``files`` as zipfile writes it (``options``: per name, a ZipInfo's own)."""
    import io
    import zipfile

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            info = zipfile.ZipInfo(name, (2024, 1, 2, 3, 4, 6))
            info.compress_type = zipfile.ZIP_DEFLATED
            for key, value in options.get(name, {}).items():
                setattr(info, key, value)
            archive.writestr(info, body)
    return out.getvalue()


def _unzipped(data: bytes) -> list[tuple[str, int, int, Any, bytes]]:
    """What a zip holds: each file's name, attributes, packing, time (not theme.json's: the
    moment it was made) and bytes."""
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return [(i.filename, i.external_attr, i.compress_type,
                 None if i.filename.endswith("theme.json") else i.date_time, archive.read(i))
                for i in archive.infolist()]  # fmt: skip


def _tree(folder: Path) -> list[tuple[str, bytes]]:
    return sorted((p.relative_to(folder).as_posix(), p.read_bytes())
                  for p in folder.rglob("*") if p.is_file())  # fmt: skip


def test_themes_are_shared_and_taken_in_as_share_py_does(servers: Any) -> None:
    """A theme as a .zip to give to someone: its theme.json without the person's folders and
    whole paths in its pictures' lists, the pictures and fonts it names (by a pattern too),
    nothing else, no link out; one that is not there. A shared theme taken in: under the
    folder's name, the file's or the theme's, a number after one that is taken, only the
    files it names; refused as share.py refuses it (no zip, too much, paths that leave it, a
    link, a password, files of other kinds or not what they say, no theme.json, one that does
    not read or is no theme, a damaged entry)."""
    import zipfile

    ask, homes = servers
    png = (
        (DATA / "themes" / "lumen" / "preview.png").read_bytes()
        if (DATA / "themes" / "lumen" / "preview.png").exists()
        else b"\x89PNG\r\n\x1a\n" + b"x" * 64
    )
    for home in homes:
        own = home / "themes" / "pics"
        (own / "photos").mkdir(parents=True)
        (own / "photos" / "a.png").write_bytes(png)
        (own / "photos" / "b.png").write_bytes(png + b"b")
        (own / "back.jpg").write_bytes(b"\xff\xd8\xff" + b"j" * 40)
        (own / "unused.png").write_bytes(png)
        (own / "font.ttf").write_bytes(b"\x00\x01\x00\x00font")
        os.utime(own / "back.jpg", (1_600_000_000, 1_600_000_000))
        (own / "linked.png").symlink_to(own / "photos" / "a.png")
        theme = json.loads((DATA / "themes" / "slate" / "theme.json").read_text("utf-8"))
        theme["name"] = "Pics"
        theme["background"] = {"color": "#000000", "image": "back.jpg"}
        theme["widgets"] += [
            {"type": "image", "id": "show", "x": 0, "y": 0, "w": 50, "h": 50,
             "slides": f"photos/*.png\nlinked.png\n/etc/x.png\nC:/x.png\n{home}/mine.png\n"
                       "../out.png\n@cover"},
            {"type": "text", "id": "t", "x": 0, "y": 0, "text": f"at {home} here",
             "font": "font.ttf"},
        ]  # fmt: skip
        (own / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    for theme in ("pics", "slate", "mine", "nothing", "broken"):
        theirs, ours = ask(_get(f"/api/themes/{theme}/export", WITH))
        assert ours[0] == theirs[0], theme
        if theirs[1]["Content-Type"] != "application/zip":
            told = [
                json.loads(body.replace(str(home).encode(), b"<home>"))
                for home, (_, _, body) in zip(homes, (theirs, ours), strict=True)
            ]
            assert told[1] == told[0], theme
            continue
        assert ours[1]["Content-Disposition"] == theirs[1]["Content-Disposition"], theme
        assert _unzipped(ours[2]) == _unzipped(theirs[2]), theme
    held = _unzipped(ask(_get("/api/themes/pics/export", WITH))[0][2])
    assert [h[0] for h in held] == [
        "pics/theme.json",
        "pics/back.jpg",
        "pics/font.ttf",
        "pics/photos/a.png",
        "pics/photos/b.png",
    ], held
    # taken in: each a zip as zipfile writes it, the answer and the folder put in place
    form = {"format": "libre-panel-theme/1"}
    tiny = json.dumps({**form, "display": {"width": 1}}).encode()  # (a theme that is none)
    good = json.dumps({**form, "name": "Shared One", "display": {"width": 480, "height": 320},
                       "background": {"color": "#000000", "image": "assets/back.png"},
                       "widgets": []}).encode()  # fmt: skip
    cases: list[tuple[str, bytes]] = [
        ("shared.zip", _zip_of({"folder/theme.json": good, "folder/assets/back.png": png,
                                "folder/assets/spare.png": png, "folder/.DS_Store": b"x",
                                "__MACOSX/folder/._x": b"x", "folder/sub/": b""})),
        ("shared.zip", _zip_of({"folder/theme.json": good, "folder/assets/back.png": png})),
        ("My Theme.ZIP", _zip_of({"theme.json": good, "assets/back.png": png})),
        ("", _zip_of({"theme.json": good, "assets/back.png": png})),
        ("x.zip", b"not a zip at all"),
        ("x.zip", _zip_of({"../theme.json": good})),
        ("x.zip", _zip_of({"a/theme.json": good, "a/C:x.png": png})),
        ("x.zip", _zip_of({"a/theme.json": good, "a\\b.png": png})),
        ("x.zip", _zip_of({"a/theme.json": good, "a/l.png": b"x"},
                          **{"a/l.png": {"external_attr": 0o120777 << 16}})),
        ("x.zip", _zip_of({"a/theme.json": good, "a/run.exe": b"MZ"})),
        ("x.zip", _zip_of({"a/theme.json": good, "b/x.png": png})),
        ("x.zip", _zip_of({"a/x.png": png})),
        ("x.zip", _zip_of({"a/theme.json": b"{nope"})),
        ("x.zip", _zip_of({"a/theme.json": b"\xff\xfe"})),
        ("x.zip", _zip_of({"a/theme.json": tiny})),
        ("x.zip", _zip_of({"a/theme.json": good, "a/assets/back.png": b"GIF89a fake"})),
        ("x.zip", _zip_of({"a/theme.json": good, "a/assets/back.png": png})[:-30]),
        ("x.zip", _zip_of({f"a/{n}.png": png for n in range(1001)})),
    ]  # fmt: skip
    damaged = bytearray(_zip_of({"a/theme.json": good, "a/assets/back.png": png * 4}))
    damaged[damaged.find(b"assets/back.png") + 20] ^= 0xFF
    cases.append(("x.zip", bytes(damaged)))
    locked = bytearray(_zip_of({"a/theme.json": good, "a/p.png": png}))
    at = locked.rfind(b"PK\x01\x02")  # (zipfile writes no password: the flag set after)
    locked[at + 8] |= 1
    cases.append(("x.zip", bytes(locked)))
    stored = _zip_of(
        {"a/theme.json": good, "a/assets/back.png": png},
        **{"a/assets/back.png": {"compress_type": zipfile.ZIP_STORED}},
    )
    cases.append(("x.zip", stored))
    for name, data in cases:
        head = (f"POST /api/themes/import?name={name.replace(' ', '%20')} HTTP/1.1\r\n"
                "Host: {host}\r\nX-Libre-Panel: {token}\r\nContent-Type: application/zip\r\n"
                f"Content-Length: {len(data)}\r\n\r\n").encode()  # fmt: skip
        theirs, ours = ask(head + data)
        told = [json.loads(body) for _, _, body in (theirs, ours)]
        assert (ours[0], told[1]) == (theirs[0], told[0]), name
        if "id" in told[0]:
            trees = [_tree(home / "themes" / told[0]["id"]) for home in homes]
            assert trees[1] == trees[0], name
    for home in homes:
        assert not [p for p in (home / "themes").iterdir() if p.name.startswith(".import-")]
    assert {p.name for p in (homes[0] / "themes").iterdir()} >= {"folder", "folder-2", "a"}


def test_themes_are_saved_and_made_for_a_size_as_server_py_does(servers: Any) -> None:
    """A theme saved (a new one, a copy of another with its pictures and fonts, under a name
    that is none, one that is no theme, a body that is no dict; one named "import"), and a
    version of a theme made for a panel size (laid out from the nearest shape, scaled into it,
    one there already, sizes that are none or out of bounds, a theme that is not there)."""
    ask, homes = servers
    theme = json.loads((DATA / "themes" / "slate" / "theme.json").read_text("utf-8"))
    bodies: list[tuple[str, Any]] = [
        ("/api/themes/new-one", {"theme": theme}),
        ("/api/themes/copy", {"theme": theme, "source": "mine"}),
        ("/api/themes/copy2", {"theme": theme, "source": 5}),
        ("/api/themes/Bad%20Name", {"theme": theme}),
        ("/api/themes/", {"theme": theme}),
        ("/api/themes/odd", {"theme": {"name": 5}}),
        ("/api/themes/odd", {"theme": None}),
        ("/api/themes/odd", ["theme"]),
        ("/api/themes/slate/version", {"width": 800, "height": 480}),
        ("/api/themes/lumen/version", {"width": 1000, "height": 700}),
        ("/api/themes/lumen/version", {"width": 700.9, "height": "1000"}),
        ("/api/themes/lumen/version", {"width": 1920, "height": 480}),
        ("/api/themes/mine/version", {"width": 320, "height": 240}),
        ("/api/themes/mine/version", {"width": "x", "height": 240}),
        ("/api/themes/mine/version", {"width": 10, "height": 240}),
        ("/api/themes/mine/version", {"height": 240}),
        ("/api/themes/mine/version", [1]),
        ("/api/themes/nothing/version", {"width": 320, "height": 240}),
    ]  # fmt: skip
    for path, payload in bodies:
        theirs, ours = ask(_post(path, payload))
        if theirs == ("", {}, b""):  # (an exception server.py does not catch: no answer)
            _failed(ours, path)
            continue
        told = [json.loads(body.replace(json.dumps(str(home))[1:-1].encode(), b"<home>"))
                for home, (_, _, body) in zip(homes, (theirs, ours), strict=True)]  # fmt: skip
        assert (ours[0], told[1]) == (theirs[0], told[0]), path
        if "id" in told[0]:
            trees = [_tree(home / "themes" / told[0]["id"]) for home in homes]
            assert trees[1] == trees[0], path
    theirs, ours = ask(_post("/api/themes/import", {"theme": theme}))  # (JSON: saved)
    assert ours[0] == theirs[0] == "HTTP/1.0 200 OK"
    assert _tree(homes[1] / "themes" / "import") == _tree(homes[0] / "themes" / "import")


def _same_pictures(theirs: str, ours: str, what: Any) -> None:
    """Two PNGs (base64) of one size, a level apart at most where light is blurred in floating
    point (numpy's BLAS), and at few pixels."""
    import base64
    from io import BytesIO

    import numpy as np
    from PIL import Image

    pictures = [Image.open(BytesIO(base64.b64decode(p))) for p in (theirs, ours)]
    assert pictures[1].size == pictures[0].size, what
    apart = np.abs(np.asarray(pictures[1].convert("RGB"), int)
                   - np.asarray(pictures[0].convert("RGB"), int))  # fmt: skip
    assert apart.max() <= 1, what
    far = (apart.max(axis=2) > 0).sum()
    assert far <= max(2, apart.shape[0] * apart.shape[1] // 10000), (what, far)


def test_the_editors_pictures_are_drawn_as_drawings_py_draws_them(servers: Any) -> None:
    """Design's picture of a theme (its boxes, warnings, grid and modules' boxes), with each
    test message on it; a notice that is none, a theme that is none, a body that is no dict.
    The modules drawn for the library, the starting layouts of a grid, a module's parts as
    plain widgets, a theme adapted to another panel; and what they refuse."""
    ask, homes = servers
    slate = json.loads((DATA / "themes" / "slate" / "theme.json").read_text("utf-8"))
    lumen = json.loads((DATA / "themes" / "lumen" / "theme.json").read_text("utf-8"))
    module = next(w["id"] for w in lumen["widgets"] if w.get("type") == "module")
    # (what the demo's readings do not move: both servers draw "now", a moment apart)
    still = {**slate, "widgets": [w for w in slate["widgets"]
                                  if w["type"] in ("rect", "text", "icon", "clock")]}  # fmt: skip
    grid = {**lumen, "widgets": [
        {"type": "module", "id": "c", "module": "text", "col": 0, "row": 0, "cols": 2,
         "text": "Libre"},
        {"type": "module", "id": "d", "module": "date", "col": 2, "row": 0},
        {"type": "module", "id": "t", "module": "text", "col": 0, "row": 1, "text": "Hi"},
    ]}  # fmt: skip
    cases: list[tuple[str, Any]] = [
        ("/api/render", {"theme": still, "base": "slate"}),
        ("/api/render", {"theme": grid, "base": "lumen"}),
        ("/api/render", {"theme": still, "base": "../x"}),
        *[("/api/render", {"theme": grid, "base": "lumen", "notice": n, "look": look})
          for n in ("volume", "music", "warning", "panel", "device", "game")
          for look in ("", "ring")],
        ("/api/render", {"theme": slate, "notice": "nope"}),
        ("/api/render", {"theme": slate, "notice": 5}),
        ("/api/render", {"theme": {"format": "libre-panel-theme/1", "display": {"width": 1}}}),
        ("/api/render", ["theme"]),
        ("/api/module-previews", {}),
        ("/api/module-previews", {"palette": lumen.get("palette"), "style": lumen.get("style")}),
        ("/api/templates", {"width": 800, "height": 480}),
        ("/api/templates", {"width": "320", "height": 240.7, "model": "turing-3.5"}),
        ("/api/templates", {"width": 1920, "height": 480, "model": 5}),
        ("/api/templates", {"height": 480}),
        ("/api/templates", {"width": None, "height": 480}),
        ("/api/templates", {"width": "x", "height": 480}),
        ("/api/templates", [1]),
        ("/api/templates", "text"),
        ("/api/detach", {"theme": lumen, "base": "lumen", "id": module}),
        ("/api/detach", {"theme": lumen, "id": "nothing"}),
        ("/api/detach", {"theme": lumen, "id": None}),
        ("/api/detach", {"theme": None}),
        ("/api/adapt", {"theme": slate, "width": 480, "height": 320}),
        ("/api/adapt", {"theme": slate, "model": "turing-3.5", "orientation": "portrait"}),
        ("/api/adapt", {"theme": lumen, "mode": "layout", "width": "1280", "height": 0}),
        ("/api/adapt", {"theme": slate, "width": "x", "height": 1}),
        ("/api/adapt", {"theme": None}),
        ("/api/adapt", 7),
    ]  # fmt: skip
    for path, payload in cases:
        for _ in range(2):  # (once more where a minute turned between the two)
            theirs, ours = ask(_post(path, payload))
            if theirs == ("", {}, b""):  # (an exception server.py does not catch: no answer)
                _failed(ours, path)
                break
            assert ours[0] == theirs[0], (path, ours, theirs[0])
            told = [json.loads(body) for _, _, body in (theirs, ours)]
            pictures = [t.pop("png", None) for t in told]
            previews = [t.pop("previews", None) for t in told]
            if told[1] != told[0]:
                continue
            if pictures[0] is not None:
                _same_pictures(pictures[0], pictures[1], (path, payload))
            if previews[0] is not None:
                assert previews[1].keys() == previews[0].keys()
                for kind in previews[0]:
                    _same_pictures(previews[0][kind], previews[1][kind], kind)
            break
        assert told[1] == told[0], path


def test_what_the_editor_knows_and_its_blocks_are_as_server_py_has_them(servers: Any) -> None:
    """Every widget type's fields, the look's blocks and ranges, the finishes, the panels, the
    fonts, icons and modules, the building blocks and their tokens (in the app's language);
    the blocks drawn in a theme's palette, or the defaults."""
    ask, _homes = servers
    theirs, ours = (json.loads(body) for _, _, body in ask(_get("/api/specs", WITH)))
    for key in theirs:
        assert ours.get(key) == theirs[key], key
    assert list(ours) == list(theirs)
    lumen = json.loads((DATA / "themes" / "lumen" / "theme.json").read_text("utf-8"))
    for payload in ({}, {"palette": lumen["palette"]}, {"palette": "x"}, [1]):
        theirs, ours = (json.loads(body)["previews"]
                        for _, _, body in ask(_post("/api/preset-previews", payload)))  # fmt: skip
        assert list(ours) == list(theirs), payload
        for block in theirs:
            _same_pictures(theirs[block], ours[block], block)


def test_the_settings_tiles_are_drawn_as_drawings_py_draws_them(servers: Any) -> None:
    """Each fan look in each paint, the weather symbols and the moon in each look, the
    defaults where none is asked for, and a look there is not."""
    import base64

    ask, _homes = servers
    paths = ["/api/fan-look", "/api/symbols-look", "/api/fan-look?look=nope",
             "/api/fan-look?paint=nope", "/api/symbols-look?pack=nope"]  # fmt: skip
    for look in ("noctua-g2", "noctua-a12x25", "noctua-a14", "libre"):
        paths += [f"/api/fan-look?look={look}&paint={paint}" for paint in ("theme", "own")]
    paths += [f"/api/symbols-look?pack={pack}" for pack in ("classic", "vivid")]
    for path in paths:
        theirs, ours = ask(_get(path, WITH))
        assert (ours[0], ours[1].get("Content-Type")) == (theirs[0], theirs[1].get("Content-Type"))
        if theirs[1].get("Content-Type") != "image/png":
            assert json.loads(ours[2]) == json.loads(theirs[2]), path
            continue
        _same_pictures(base64.b64encode(theirs[2]), base64.b64encode(ours[2]), path)


def test_the_log_and_the_language_are_as_server_py_has_them(servers: Any) -> None:
    """The log's last entries by level, its tracebacks, its older parts and a hang, the
    person's folders taken out; the whole of it to keep; its detail switched; the language
    set, refused, and spoken at once."""
    ask, homes = servers
    home = os.path.expanduser("~")
    for side in homes:
        logs = side / "logs"
        logs.mkdir()
        (logs / "libre-panel.log.1").write_bytes(
            b"2026-01-02 03:04:05,001 INFO libre_panel.app: older\n"
        )
        (logs / "libre-panel.log").write_bytes(
            b"noise before any entry\n"
            b"2026-01-02 03:04:06,123 WARNING libre_panel.x: at C:\\Users\\Bob\\f.txt\n"
            b'Traceback (most recent call last):\n  File "' + home.encode() + b'/a.py"\n\n'
            b"2026-01-02 03:04:07 ERROR a-b.c:no space\xff\n"
            b"2026-01-02 03:04:08,5 DEBUG libre_panel.y: detail\x0bmore\n"
            b"2026-01-02 03:04:09,000 NOTE x: not a level\n"
        )
        (logs / "libre-panel-hang.log").write_text("Timeout (0:00:10)!\nThread 1\n", "utf-8")
    for query in ("", "?level=warning", "?level=ERROR", "?level=nope", "?level="):
        theirs, ours = ask(_get(f"/api/logs{query}", WITH))
        assert ours == theirs, query
    theirs, ours = ask(_get("/api/logs/file", WITH))
    assert ours == theirs
    assert b"Bob" not in ours[2] and b"== libre-panel-hang.log ==" in ours[2]
    root = logging.getLogger().level
    try:
        for payload in ({"verbose": True}, {"verbose": False}, {"verbose": 1}, {}, [True]):
            theirs, ours = ask(_post("/api/logs", payload))
            assert ours == theirs, payload
    finally:
        logging.getLogger().setLevel(root)
    for payload in ({"language": "fr"}, {"language": "xx"}, {"language": 5}, [1],
                    {"language": "auto"}, {"language": "de"}):  # fmt: skip
        theirs, ours = ask(_post("/api/language", payload))
        assert ours == theirs, payload
        theirs, ours = ask(_get("/api/i18n", WITH))
        assert ours == theirs, payload
        configs = [(side / "config.toml").read_text("utf-8") for side in homes]
        assert configs[1] == configs[0], payload


def test_the_hardware_settings_are_as_server_py_has_them(
    servers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parts' names as found and as written, the fans named, with their looks, in the
    order people count; each change written into config.toml as Python writes it, and what
    is refused."""
    ask, homes = servers
    # (this computer's parts, as the Rust program finds them, not the tests' empty machine)
    spec = importlib.util.find_spec("libre_panel.sensors.hardware")
    assert spec is not None and spec.loader is not None
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    monkeypatch.setattr(hardware, "_scan_machine", fresh._scan_machine)
    for side in homes:
        with (side / "config.toml").open("a", encoding="utf-8") as config:
            config.write('[hardware]\ncpu_name = "Mine"\n\n[[hardware.fans]]\n'
                         'sensor = "fan.lpc.fan_10"\nname = "Back"\n')  # fmt: skip
    for query in ("", "?refresh", "?refresh=1", "?refresh=0", "?refresh=false"):
        theirs, ours = ask(_get(f"/api/hardware{query}", WITH))
        assert ours[2] == theirs[2], query
        assert ours == theirs, query
    for payload in (
        {"cpu": "My  CPU\x07", "gpu": ""}, {"fans": {"fan.lpc.fan_2": "Intake"}},
        {"fan_looks": {" fan.lpc.fan_2 ": "libre"}, "fan_paints": {"gpu.fan": "own"}},
        {"fan_look": "noctua-a14", "fan_paint": "own"}, {"fan_look": ""},
        {"fans": {"fan.lpc.fan_10": ""}, "memory": "Fast"}, {}, [1], {"cpu": 5},
        {"disk": "x"}, {"fans": {" ": "x"}}, {"fans": {"a": 1}}, {"fans": []},
        {"fan_look": "nope"}, {"fan_looks": {"a": "nope"}}, {"cpu": "x" * 200},
        {"fans": {"fan.lpc.fan_3": "y" * 200}},
    ):  # fmt: skip
        theirs, ours = ask(_post("/api/hardware", payload))
        assert ours == theirs, payload
        configs = [(side / "config.toml").read_text("utf-8") for side in homes]
        assert configs[1] == configs[0], payload


def test_the_history_is_as_server_py_has_it(servers: Any) -> None:
    """What the memory kept over a day, a week and a month: the course in its steps, the
    summary, the rounds and the games, the energy and its cost; no database, one of another
    make, and a span that is none."""
    import sqlite3
    import time

    from libre_panel.services.memory_store import SCHEMA

    ask, homes = servers

    def answers(query: str) -> list[Any]:
        found, since = [], []
        for status, headers, body in ask(_get(f"/api/history{query}", WITH)):
            told = json.loads(body) if body.startswith(b"{") else body
            if isinstance(told, dict) and "since" in told:
                since.append(told.pop("since"))  # (the second each server asked at)
            found.append((status, headers.get("Content-Type"), told))
        assert not since or 0 <= since[1] - since[0] <= 1, since
        return found

    theirs, ours = answers("")
    assert ours == theirs and theirs[2]["found"] is False
    now = int(time.time())
    for side in homes:
        folder = side / "plugins-data" / "memory"
        folder.mkdir(parents=True)
        con = sqlite3.connect(folder / "memory.db")
        con.executescript(SCHEMA)
        for n in range(0, 3000, 7):
            ts = (now - 600 - n * 60) // 60 * 60
            con.execute(
                "INSERT INTO minutes (ts, samples, mode, cpu_temp, gpu_temp, cpu_load,"
                " cpu_power, cpu_temp_max, gpu_power_max) VALUES (?, 60, ?, ?, ?, ?, ?, ?, ?)",
                [ts, "game" if n % 3 else "desk", 40 + n % 17 / 3, None if n % 5 else 55,
                 n % 100, 30.25 + n % 7, 60 + n % 11, 120.5],
            )  # fmt: skip
        for n, game in enumerate(("Starfall", "Starfall", None, "Quay", "Quay", "Quay")):
            started = now - 3600 * (n + 1) - n * 86400 * 4
            con.execute(
                "INSERT INTO sessions (id, game, started, duration_s, fps_s, fps_mean,"
                " fps_p01, fps_low_mean, cpu_temp_max, gpu_temp_max) VALUES"
                " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [started * 1000, game, started, 600 + n * 30, 500, 90.04 + n, 50.5,
                 None if n % 2 else 61.25, 80 + n, None],
            )  # fmt: skip
        con.commit()
        con.close()
        with (side / "config.toml").open("a", encoding="utf-8") as config:
            config.write('[services]\nenabled = ["memory", "power"]\n[services.power]\n'
                         'price = "0.31"\nrest_w = 45\ncurrency = "CHF"\n')  # fmt: skip
    for query in ("", "?span=day", "?span=week", "?span=month", "?span=year", "?span="):
        theirs, ours = answers(query)
        assert ours == theirs, query
    for side in homes:
        con = sqlite3.connect(side / "plugins-data" / "memory" / "memory.db")
        con.execute("DROP TABLE sessions")
        con.commit()
        con.close()
    theirs, ours = answers("?span=week")
    assert ours == theirs and "error" in theirs[2]


def test_a_backup_is_made_and_put_back_as_backup_py_does(
    servers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The settings and own themes in one zip (no hidden files, no links, nothing that is made
    again by itself); a backup put in place with the config.toml before it kept; refused as
    backup.py refuses it (no zip, no manifest or another kind, paths it does not hold, a
    config.toml that does not read), and nothing written then."""
    import io
    import zipfile

    from libre_panel.editor import access

    def elevated() -> bool:  # (as the Rust program finds it: root here, an administrator in CI)
        if sys.platform == "win32":
            from libre_panel.autostart import is_admin

            return is_admin()
        return os.geteuid() == 0

    monkeypatch.setattr(access, "elevated", elevated)
    ask, homes = servers
    for side in homes:
        mine = side / "themes" / "mine"
        (mine / ".hidden").write_bytes(b"x")
        (mine / "layer.npz").write_bytes(b"x")
        (mine / "__pycache__").mkdir()
        (mine / "__pycache__" / "a.txt").write_bytes(b"x")
        (mine / "Zed.txt").write_bytes(b"z")
        if os.name != "nt":  # (a link needs rights there)
            os.symlink(mine / "notes.txt", mine / "link.txt")
    answers = ask(_get("/api/backup/file", WITH))
    zips = []
    for status, headers, body in answers:
        assert status.startswith("HTTP/1.0 200"), status
        zips.append([(n, a, c, t, b) for n, a, c, t, b in _unzipped(body)])
        made = {k: v for k, v in headers.items() if k != "Content-Length"}
        assert made == {k: v for k, v in answers[0][1].items() if k != "Content-Length"}
    for theirs_file, ours_file in zip(*zips, strict=True):
        name, attributes, packing, _, body = theirs_file
        assert ours_file[:3] == (name, attributes, packing), name
        if name == "libre-panel-backup.json":
            theirs_said, ours_said = json.loads(body), json.loads(ours_file[4])
            assert theirs_said.pop("made")[:10] == ours_said.pop("made")[:10]
            assert ours_said == theirs_said
            assert ours_file[4].count(b"\n") == body.count(b"\n")
        else:
            assert ours_file[4] == body, name
    backup = answers[0][2]

    def manifest(**changes: Any) -> bytes:
        return json.dumps({"kind": "libre-panel-backup", **changes}).encode()

    def restore(data: bytes) -> None:
        lines = ["POST /api/backup HTTP/1.1", "Host: {host}", WITH,
                 "Content-Type: application/zip", f"Content-Length: {len(data)}"]  # fmt: skip
        theirs, ours = ask("\r\n".join(lines).encode() + b"\r\n\r\n" + data)
        assert ours == theirs, theirs
        assert _tree(homes[1]) == _tree(homes[0])

    for data in (
        b"nope",
        _zip_of({"a.txt": b"x"}),
        _zip_of({"libre-panel-backup.json": b"[1]"}),
        _zip_of({"libre-panel-backup.json": manifest(kind="other")}),
        _zip_of({"libre-panel-backup.json": manifest(), "themes/../x/y": b"x"}),
        _zip_of({"libre-panel-backup.json": manifest(), "themes/a/b:c": b"x"}),
        _zip_of({"libre-panel-backup.json": manifest(), "other/a/b": b"x"}),
        _zip_of({"libre-panel-backup.json": manifest(), "themes/Bad Name/b": b"x"}),
        _zip_of({"libre-panel-backup.json": manifest(), "themes/a\\b/c": b"x"}),
        _zip_of({"libre-panel-backup.json": manifest(), "config.toml": b"theme = \xff"}),
        _zip_of({"libre-panel-backup.json": manifest(), "config.toml": b"theme = "}),
        _zip_of({"libre-panel-backup.json": manifest(), "config.toml": b"fps = -3\n"}),
        _zip_of({"libre-panel-backup.json": "﻿".encode() + manifest(),
                 "themes/new/./theme.json": b"{}", "themes/new//sub/x.png": b"p"}),
        backup,
    ):  # fmt: skip
        restore(data)
    # (a theme folder of a name no theme has is no file a backup holds: without it, it is put
    # back, the config.toml before it kept)
    for side in homes:
        shutil.rmtree(side / "themes" / "Bad Name")
    backup = ask(_get("/api/backup/file", WITH))[0][2]
    with zipfile.ZipFile(io.BytesIO(backup)) as archive:
        assert "config.toml" in archive.namelist()
    (homes[0] / "config.toml").write_text('theme = "terra"\n', encoding="utf-8")
    (homes[1] / "config.toml").write_text('theme = "terra"\n', encoding="utf-8")
    restore(backup)
    if not elevated():
        kept = (homes[1] / "config.toml.before-restore").read_text("utf-8")
        assert kept == 'theme = "terra"\n'


def test_ai_activity_comes_in_as_server_py_takes_it(servers: Any) -> None:
    """Hook events and the status line of Claude Code and Codex: with the AI key (header or
    address), not without it or while AI activity is off, an agent named, a JSON object; the
    status line answered with the line it shows. Settings' view of the two, and what Libre
    Panel would add to their settings (shown, not written)."""
    ask, homes = servers

    def both(request: str | bytes) -> None:
        theirs, ours = ask(request)
        assert ours == theirs, request

    def hook(path: str, body: str, *headers: str) -> bytes:
        lines = [f"POST {path} HTTP/1.1", "Host: {host}", *headers,
                 f"Content-Length: {len(body.encode())}"]  # fmt: skip
        return "\r\n".join(lines).encode() + b"\r\n\r\n" + body.encode()

    both(hook("/api/ai/hook?agent=claude&key=k", "{}"))  # (off)
    for side in homes:
        (side / "ai.json").write_text('{"key": "k"}', encoding="utf-8")
        with (side / "config.toml").open("a", encoding="utf-8") as config:
            config.write('[services]\nenabled = ["agents"]\n')
    event = json.dumps({"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash",
                        "cwd": "/x/project"})  # fmt: skip
    status = json.dumps({"model": {"display_name": "Opus"}, "session_id": "s1",
                         "context_window": {"used_percentage": 42.4}})  # fmt: skip
    for request in (
        hook("/api/ai/hook?agent=claude", "{}"),
        hook("/api/ai/hook?agent=claude&key=wrong", "{}"),
        hook("/api/ai/hook?agent=claude", "{}", "X-Libre-Panel-AI: wrong"),
        hook("/api/ai/hook?agent=nobody&key=k", "{}"),
        hook("/api/ai/hook?agent=claude&key=k", "[1]"),
        hook("/api/ai/hook?agent=claude&key=k", "{nope"),
        hook("/api/ai/hook?agent=claude&key=k", ""),
        hook("/api/ai/hook?agent=claude", event, "X-Libre-Panel-AI: k"),
        hook("/api/ai/hook?agent=codex&key=k", event),
        hook("/api/ai/status?agent=claude&key=k", status),
        hook("/api/ai/status?agent=codex&key=k", "{}"),
        hook("/api/ai/hook?agent=claude", "{}", WITH),
    ):  # fmt: skip
        both(request)

    def plain(told: dict[str, Any]) -> dict[str, Any]:
        for agent in ("claude", "codex"):
            entry = told[agent]
            entry["heard"] = entry["heard"] is not None  # (seconds ago: when each was asked)
            entry["usage"]["ago"] = entry["usage"]["ago"] is not None
        return told

    theirs, ours = (plain(json.loads(body)) for _, _, body in ask(_get("/api/ai/setup", WITH)))
    ours["port"] = theirs["port"]  # (each server's own)
    assert ours == theirs
    for payload in (
        {"agent": "claude", "action": "preview"},
        {"agent": "claude", "action": "preview", "status_line": True},
        {"agent": "codex", "action": "preview"},
        {"agent": "claude", "action": "nope"}, {"agent": "x", "action": "preview"}, [1],
    ):  # fmt: skip
        theirs, ours = ask(_post("/api/ai/setup", payload))
        # (each server's own port in the hooks it would add)
        said = [
            re.sub(rb"127\.0\.0\.1:\d+", b"127.0.0.1:PORT", body) for *_, body in (theirs, ours)
        ]
        assert theirs[0] == ours[0] and said[1] == said[0], payload


def test_notices_of_other_programs_come_in_as_server_py_takes_them(servers: Any) -> None:
    """A notice from another program: refused while switched off and without the notify key
    (header or address); its text, title, seconds, icon and level in the address or posted,
    checked as notify.py checks them; without a panel it is not shown. Settings' notify key,
    its address and command, and a new key."""
    from libre_panel.icons import ICON_NAMES

    ask, homes = servers

    def both(request: str | bytes) -> None:
        theirs, ours = ask(request)
        assert ours == theirs, request

    both(_get("/api/notify?key=nk&text=Hi"))  # (switched off)
    for side in homes:
        (side / "notify.json").write_text('{"key": "nk"}', encoding="utf-8")
        text = (side / "config.toml").read_text("utf-8")
        (side / "config.toml").write_text("allow_notify = true\n" + text, encoding="utf-8")
    long = "x" * 161
    for query in (
        "text=Hi", "key=wrong&text=Hi", "key=nk", "key=nk&text=%20%20", f"key=nk&text={long}",
        "key=nk&text=Hello%20%0A%20there&title=T&seconds=3&level=warning",
        f"key=nk&text=Hi&icon={ICON_NAMES[0]}", "key=nk&text=Hi&icon=nope",
        "key=nk&text=Hi&seconds=0.5", "key=nk&text=Hi&seconds=x", "key=nk&text=Hi&seconds=nan",
        "key=nk&text=Hi&level=bad", f"key=nk&text=Hi&title={'t' * 41}",
    ):  # fmt: skip
        both(_get(f"/api/notify?{query}"))
    both(_get("/api/notify?text=Hi", "X-Libre-Panel-Notify: nk"))
    both(_get("/api/notify?text=Hi&key=wrong", "X-Libre-Panel-Notify: nk"))
    for payload in ({"text": "Posted", "seconds": 2}, [1], {"text": 5}, {"title": None},
                    {"text": "a", "seconds": True, "icon": "", "level": None}):  # fmt: skip
        body = json.dumps(payload)
        both(_get("/api/notify?key=nk", "Content-Type: application/json",
                  f"Content-Length: {len(body)}", method="POST", body=body))  # fmt: skip
    both(_get("/api/notify?key=nk&text=Hi", "Content-Length: 0", method="POST"))

    def keyed(request: str) -> list[Any]:
        found = []
        for _, _, body in ask(request):
            told = json.loads(body)
            for name in ("address",):
                if name in told:
                    told[name] = re.sub(r"127\.0\.0\.1:\d+", "127.0.0.1:PORT", told[name])
            found.append(told)
        return found

    theirs, ours = keyed(_get("/api/notify-key", WITH))
    assert ours == theirs and theirs["key"] == "nk"
    theirs, ours = keyed(_get("/api/notify-key", WITH, "Content-Length: 0", method="POST"))
    assert ours["key"] != "nk" and len(ours["key"]) == len(theirs["key"])
    for told in (theirs, ours):
        told["address"] = told["address"].replace(told["key"], "KEY")
        told["key"] = "KEY"
    assert ours == theirs


def test_the_built_in_pages_answer_as_pages_py_does(servers: Any) -> None:
    """The pages there are and their files; Control's and Autopilot's own API without a
    background app: what they show, what they change in config.toml, and what they refuse."""
    ask, homes = servers

    # (each side's own settings folder, as JSON writes it)
    own = [json.dumps(str(side))[1:-1].encode() for side in homes]

    def both(request: str | bytes) -> None:
        theirs, ours = ask(request)
        body = ours[2].replace(own[1], own[0])
        ours = (ours[0], {**ours[1], "Content-Length": str(len(body))}, body)
        assert ours == theirs, request
        configs = [(side / "config.toml").read_text("utf-8") for side in homes]
        assert configs[1] == configs[0], request

    for side in homes:
        (side / "logs").mkdir()
        (side / "logs" / "libre-panel.log").write_text(
            "".join(f"2026-01-02 03:04:{n:02} INFO x: line {n}\n" for n in range(50)), "utf-8"
        )
    for path in ("/plugins/control/", "/plugins/autopilot/console.js", "/plugins/control/../x",
                 "/plugins/nope/index.html", "/plugins/control/missing.js",
                 "/plugins/control/index.HTML", "/plugins/control/a.exe"):  # fmt: skip
        both(_get(path))
    both(_get("/api/plugins/pages", WITH))
    for path in ("status", "panel", "sources", "services", "probes", "themes", "live",
                 "live?format=gif&w=5", "log", "log?lines=3", "log?lines=x", "nope",
                 "/status/"):  # fmt: skip
        both(_get(f"/api/plugins/control/{path}", WITH))
    both(_get("/api/plugins/nope/status", WITH))
    for path, payload in (
        ("probe", {"kind": "music"}), ("probe", {"kind": "x"}), ("probe", {}),
        ("probe", [1]), ("services/enable", {"service": "timer", "enabled": True}),
        ("services/enable", {"service": "timer", "enabled": True}),
        ("services/enable", {"service": "nope", "enabled": True}),
        ("services/enable", {"service": "nope", "enabled": False}),
        ("services/enable", {"service": "timer", "enabled": "yes"}),
        ("services/restart", {"service": "timer"}), ("shortcut", {"action": "timer"}),
        ("shortcut", {"action": "x"}), ("theme", {"mode": "normal", "theme": "terra"}),
        ("theme", {"mode": "game", "theme": ""}), ("theme", {"mode": "x", "theme": ""}),
        ("theme", {"mode": "normal", "theme": "nope"}), ("nope", {}),
    ):  # fmt: skip
        both(_post(f"/api/plugins/control/{path}", payload))
    both(_get("/api/plugins/control/status", WITH))
    rule = {"name": "Music", "theme": "lumen", "when": {"music": True}}
    for payload in (
        {"rules": [rule], "enabled": True, "options": {"hold_s": 4, "transition": "fade"}},
        {"rules": [{**rule, "theme": "nope"}]}, {"rules": [{**rule, "when": {}}]},
        {"rules": [{**rule, "when": {"cpu_over": 120}}]}, {"rules": [{**rule, "hold_s": True}]},
        {"rules": [{**rule, "when": {"time": "25:00-1"}}]}, {"rules": [{"theme": "x"}]},
        {"rules": "x"}, {"options": {"min_s": -1}}, {"options": {"transition": 5}},
        {"options": "x"}, {"enabled": "on"}, {"enabled": False}, {},
    ):  # fmt: skip
        both(_post("/api/plugins/autopilot/settings", payload))
        both(_get("/api/plugins/autopilot/settings", WITH))
    both(_get("/api/plugins/autopilot/status", WITH))


def _frames_of(data: bytes) -> list[bytes]:
    """The pixels of each picture of a file, as Pillow reads them."""
    import io

    from PIL import Image, ImageSequence

    with Image.open(io.BytesIO(data)) as picture:
        return [frame.convert("RGBA").tobytes() for frame in ImageSequence.Iterator(picture)]


def test_the_start_screen_is_told_drawn_and_made_as_server_py_does(servers: Any) -> None:
    """Settings → Panel → Start screen (/api/start-screen): the panels, looks and names; the
    app's pictures of the boot logo (PNG) and of the standby clip (animated WebP) and the files
    as the panel takes them (the logo's JPEG; the clip's H.264 where ffmpeg is there), with the
    same pixels; what is not a panel, a picture or a look refused in the same words."""
    ask, _ = servers
    for request in (_get("/api/start-screen", WITH), _get("/api/start-screen?panel=3", WITH)):
        theirs, ours = ask(request)
        assert ours[0] == theirs[0] and json.loads(ours[2]) == json.loads(theirs[2]), request
    for path in ("picture?model=turing-9.2-usb&what=logo&w=300&design=aurora",
                 "picture?model=turing-2.1-usb&what=clip&w=120",
                 "file?model=turing-8.8-usb&what=logo&design=night"):  # fmt: skip
        theirs, ours = ask(_get(f"/api/start-screen/{path}", WITH))
        assert ours[0] == theirs[0] == "HTTP/1.0 200 OK", (path, ours[2][:200])
        # (the same pixels; a PNG packed by another zlib may be a few bytes longer or shorter)
        unsized = [{k: v for k, v in side[1].items() if k != "Content-Length"}
                   for side in (ours, theirs)]  # fmt: skip
        assert unsized[0] == unsized[1], path
        assert _frames_of(ours[2]) == _frames_of(theirs[2]), path
    if shutil.which("ffmpeg"):
        theirs, ours = ask(_get("/api/start-screen/file?model=turing-2.1-usb&what=clip", WITH))
        assert ours[0] == theirs[0] and ours[1] == theirs[1]
        assert ours[2] == theirs[2]
    for path in ("picture?model=nope", "picture?model=turing-9.2-usb&what=x",
                 "picture?model=turing-9.2-usb&design=nope", "file?what=clip"):  # fmt: skip
        theirs, ours = ask(_get(f"/api/start-screen/{path}", WITH))
        assert ours[0] == theirs[0] and json.loads(ours[2]) == json.loads(theirs[2]), path


def test_the_start_screen_is_written_to_the_panel_as_panel_files_py_writes_it(
    tmp_path: Path,
) -> None:
    """POST /api/start-screen on a simulated TURZX panel the engine has: what is on it, the
    boot logo written and shown (the JPEG panel_files.py makes, byte for byte), taken away
    again, the standby clip written, played and saved as the start (H.264 byte for byte where
    ffmpeg is there) and named in config.toml; what is no action, panel or model refused in
    server.py's words."""
    from PIL import Image

    from libre_panel.devices import panel_files
    from libre_panel.devices.turzx_usb import brightness_arg
    from libre_panel.render import bootscreen

    rig = Rig(tmp_path)
    try:
        rig.config(
            'theme = "slate"\nlanguage = "en"\n[device]\ndriver = "turzx"\n'
            'model = "turing-9.2-usb"\nbrightness = 60\n[sensors]\nproviders = []\n'
        )
        env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
               **_libraries(), **rig.env()}  # fmt: skip
        with subprocess.Popen([str(_program()), "editor", "--port", "0"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8") as program:  # fmt: skip
            try:
                assert program.stdout is not None
                address = program.stdout.readline().split(" running at ")[1].split()[0]
                port = int(address.split(":")[2].split("/")[0])
                token = address.split("#token=")[1]

                def post(payload: Any) -> tuple[str, Any]:
                    body = json.dumps(payload)
                    request = _get(
                        "/api/start-screen",
                        f"X-Libre-Panel: {token}",
                        "Content-Type: application/json",
                        f"Content-Length: {len(body)}",
                        method="POST",
                        body=body,
                    )
                    request = request.replace("{host}", f"127.0.0.1:{port}")
                    status, _, answer = _asked(port, request.encode("utf-8"))
                    return status.split(" ", 2)[1], json.loads(answer)

                model = panel_files.model_of("turing-9.2-usb")
                status, said = post({"action": "state"})
                assert status == "200", said
                assert said == {"model": model.id, "files": [], "boot_logo": 0,
                                "standby_clip": 0, "paused": False}  # fmt: skip
                logo = panel_files.logo_jpeg(model, design="aurora")
                status, said = post({"action": "logo", "design": "aurora"})
                assert status == "200", said
                assert said == {"model": model.id, "boot_logo": len(logo), "design": "aurora",
                                "paused": False}  # fmt: skip
                assert rig.fake.files[panel_files.BOOT_LOGO] == logo
                assert rig.fake.shown == [panel_files.BOOT_LOGO]
                status, said = post({"action": "state", "model": model.id})
                assert said["files"] == ["boot.jpg"] and said["boot_logo"] == len(logo), said
                status, said = post({"action": "remove-logo"})
                assert status == "200" and said["boot_logo"] == 0, said
                assert panel_files.BOOT_LOGO not in rig.fake.files
                if shutil.which("ffmpeg"):
                    clip = panel_files.clip_h264(model, shutil.which("ffmpeg"), full=True)
                    status, said = post({"action": "clip"})
                    assert status == "200", said
                    assert said == {"model": model.id, "standby_clip": len(clip),
                                    "name": panel_files.STANDBY_CLIP, "design": "libre-panel",
                                    "paused": False}  # fmt: skip
                    assert rig.fake.files[panel_files.STANDBY_CLIP] == clip
                    assert rig.fake.clip_names[-1] == panel_files.STANDBY_CLIP
                    assert rig.fake.fps_log[-1] == 30
                    assert rig.fake.saved[-1] == bytes([brightness_arg(60), 2, 0, 0, 0, 0])
                    written = (tmp_path / "config.toml").read_text(encoding="utf-8")
                    assert 'local_clip = "usr/data/standby.h264"' in written, written
                    assert "clip_asked = true" in written, written
                # a panel mounted upside down: its start screen turned round, as its frames are
                rig.config(
                    'theme = "slate"\nlanguage = "en"\n[device]\ndriver = "turzx"\n'
                    'model = "turing-9.2-usb"\nbrightness = 60\nrotate = 180\n'
                    "[sensors]\nproviders = []\n"
                )
                logo = bootscreen.boot_logo(model, "landscape", "aurora")
                turned = panel_files.logo_jpeg(model, logo.transpose(Image.Transpose.ROTATE_180))
                status, said = post({"action": "logo", "design": "aurora"})
                assert status == "200" and said["boot_logo"] == len(turned), said
                assert rig.fake.files[panel_files.BOOT_LOGO] == turned
                if shutil.which("ffmpeg"):
                    frames = [frame.transpose(Image.Transpose.ROTATE_180)
                              for frame in bootscreen.standby_frames(model)]  # fmt: skip
                    clip = panel_files.clip_h264(model, shutil.which("ffmpeg"), frames, full=True)
                    status, said = post({"action": "clip"})
                    assert status == "200" and said["standby_clip"] == len(clip), said
                    assert rig.fake.files[panel_files.STANDBY_CLIP] == clip
                for payload, words in (
                    ({"action": "nope"},
                     'expected {"action": "state", "logo", "remove-logo" or "clip"}'),
                    ({"action": "state", "panel": 3}, "panel 3: there is no such panel"),
                    ({"action": "state", "panel": True}, "panel: expected a whole number"),
                    ({"action": "state", "model": "x"}, "'x' is no TURZX USB panel"),
                    ({"action": "logo", "design": "x"}, "'x' is no look of the start screen"),
                ):  # fmt: skip
                    status, said = post(payload)
                    assert (status, said["error"]) == ("400", words), payload
            finally:
                program.send_signal(signal.SIGINT) if os.name != "nt" else program.terminate()
                stderr = program.communicate(timeout=30)[1]
                assert "panicked" not in stderr, stderr
    finally:
        rig.close()


def test_the_colour_check_is_told_and_refused_as_colours_py_does(servers: Any) -> None:
    """Settings → Panel → Check the colours (/api/colour-check) of a server without the
    running app: the state, what is no panel or no action, and the A/B refused (it needs the
    app), in the same words. Who encodes differs by design: the Rust app's video is Libre
    Panel's engine alone ("native"), which converts the colours as the processor does."""
    ask, _ = servers
    theirs, ours = ask(_get("/api/colour-check", WITH))
    assert ours[0] == theirs[0] == "HTTP/1.0 200 OK", ours[2][:300]
    mine, python = json.loads(ours[2]), json.loads(theirs[2])
    assert mine.pop("auto") == {"set": "auto", "picked": "native", "passed": []}
    assert mine.pop("in_use") == {"encoder": "native", "feed": "rgb0", "card": False,
                                  "running": False}  # fmt: skip
    choice = mine.pop("choice")
    assert (choice["encoder"], choice["why"], choice["way"]) == ("native", "x264", "processor")
    python.pop("auto"), python.pop("in_use")
    keys = ("pixels", "full", "range", "model_range", "verdict", "worst", "when", "by")
    python_choice = python.pop("choice")
    assert {k: choice[k] for k in keys} == {k: python_choice[k] for k in keys}
    assert mine == python
    requests = (
        _get("/api/colour-check?panel=x", WITH),
        _get("/api/colour-check?panel=4", WITH),
        _post("/api/colour-check", {"action": "nope"}),
        _post("/api/colour-check", {"action": "range"}),
        _post("/api/colour-check", {"action": "show", "panel": 2}),
        _post("/api/colour-check", {"action": "stop", "panel": True}),
    )
    for request in requests:
        theirs, ours = ask(request)
        assert ours[0] == theirs[0] and json.loads(ours[2]) == json.loads(theirs[2]), request


def test_the_panel_compares_its_range_for_the_eye_and_shows_its_theme_again(
    tmp_path: Path,
) -> None:
    """Start's *Is black too light? Compare* on the running app with a simulated panel in the
    video mode: the A/B begun, the engine's stream made in limited and in full range in turn,
    the test picture on the panel; stopped, and the panel's own range again."""
    rig = Rig(tmp_path)
    try:
        rig.config(
            'theme = "slate"\nlanguage = "en"\n[device]\ndriver = "turzx"\n'
            'model = "turing-9.2-usb"\nvideo_range = "limited"\n[video]\nmode = "on"\n'
            'fps = 10\ndevice_fps = 30\nlocal_clip = "usr/data/clip.h264"\n'
            "[sensors]\nproviders = []\n"
        )
        env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
               **_libraries(), **rig.env()}  # fmt: skip
        command = [str(_program()), "start", "--port", "0", "--no-browser"]
        with subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8") as program:  # fmt: skip
            try:
                assert program.stdout is not None
                address = program.stdout.readline().split(" running at ")[1].split()[0]
                port = int(address.split(":")[2].split("/")[0])
                token = address.split("#token=")[1]

                def ask(request: str | bytes) -> tuple[str, Any]:
                    text = request if isinstance(request, bytes) else request.encode("utf-8")
                    text = text.replace(b"{host}", f"127.0.0.1:{port}".encode())
                    text = text.replace(b"{token}", token.encode())
                    status, _, answer = _asked(port, text)
                    return status.split(" ", 2)[1], json.loads(answer)

                said = _waited(lambda: ask(_get("/api/colour-check", WITH))[1]["video"] or None,
                               30)  # fmt: skip
                assert said, "the panel never showed video"
                state = ask(_get("/api/colour-check", WITH))[1]
                assert state["in_use"] == {"encoder": "native", "feed": "boxes", "card": False,
                                           "running": True}  # fmt: skip
                assert state["ab"] == "x264"
                status, said = ask(_post("/api/colour-check", {"action": "show"}))
                assert (status, said["error"]) == (
                    "409",
                    "panel 1 runs native: the processor converts",
                )
                status, said = ask(_post("/api/colour-check", {"action": "range"}))
                assert status == "200", said
                assert said["show"]["running"] and said["show"]["kind"] == "range"
                assert said["show"]["panel"] == 1 and said["show"]["of"] == 1
                seen = set()

                def both() -> bool | None:
                    seen.add(ask(_get("/api/colour-check", WITH))[1]["show"]["value"])
                    return ({"limited", "full"} <= seen) or None

                assert _waited(both, 15), seen
                time.sleep(1.5)  # (a frame in B: the loop draws a still theme twice a second)
                status, said = ask(_post("/api/colour-check", {"action": "stop"}))
                assert status == "200", said
                ended = _waited(
                    lambda: ask(_get("/api/colour-check", WITH))[1]["show"]["ended"] or None, 5
                )
                assert ended == "stopped"
                assert not (tmp_path / "colour-check.json").exists()
                time.sleep(1.5)  # (a frame after it, in the panel's own range)
            finally:
                program.send_signal(signal.SIGINT) if os.name != "nt" else program.terminate()
                stderr = program.communicate(timeout=60)[1]
        assert "panicked" not in stderr, stderr
        # the engine's stream made in each range, then in the panel's own again
        ranges = re.findall(r"video: encoding with the engine.*?, (limited|full) range", stderr)
        assert ranges[:1] == ["limited"] and "full" in ranges, ranges
        assert ranges[-1] == "limited", ranges
    finally:
        rig.close()


def test_the_picture_across_the_panels_is_kept_and_shown_as_server_py_does(
    servers: Any,
) -> None:
    """Settings → Panels → a picture across them (/api/across): none at first; a picture given
    kept in the settings folder as arrangement.py keeps it (turned as its EXIF says, at most
    4096 pixels long, a JPEG without its metadata), named in config.toml, and shown small for
    the app, with the same pixels; what is no picture refused in the same words."""
    import io

    from PIL import Image

    ask, homes = servers
    theirs, ours = ask(_get("/api/across/picture", WITH))
    assert ours[0] == theirs[0] and json.loads(ours[2]) == json.loads(theirs[2])
    given = Image.effect_mandelbrot((5000, 1300), (-2.0, -1.0, 1.0, 1.0), 40).convert("RGB")
    exif = Image.Exif()
    exif[0x0112] = 6  # (taken on its side: kept upright)
    data = io.BytesIO()
    given.save(data, "JPEG", quality=90, exif=exif.tobytes())

    def upload(body: bytes) -> bytes:
        head = ["POST /api/across HTTP/1.1", "Host: {host}", WITH,
                "Content-Type: image/jpeg", f"Content-Length: {len(body)}"]  # fmt: skip
        return "\r\n".join(head).encode("utf-8") + b"\r\n\r\n" + body

    theirs, ours = ask(upload(data.getvalue()))
    assert ours[0] == theirs[0] == "HTTP/1.0 200 OK", ours[2]
    mine, python = json.loads(ours[2]), json.loads(theirs[2])
    assert mine["across"] == python["across"] == "across/picture.jpg"
    kept = [Image.open(home / "across" / "picture.jpg") for home in homes]
    assert kept[1].size == kept[0].size and kept[0].height == 4096  # (upright now)
    assert kept[1].tobytes() == kept[0].tobytes()
    for home in homes:
        assert 'across = "across/picture.jpg"' in (home / "config.toml").read_text("utf-8")
    theirs, ours = ask(_get("/api/across/picture", WITH))
    assert ours[0] == theirs[0] == "HTTP/1.0 200 OK"
    assert _frames_of(ours[2]) == _frames_of(theirs[2])
    theirs, ours = ask(upload(b"no picture"))
    assert ours[0] == theirs[0] and json.loads(ours[2]) == json.loads(theirs[2])


@pytest.fixture
def pictures_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home folder of the test's own (its Pictures folder, where both servers keep the
    pictures of a round), set before the servers start."""
    home = tmp_path / "person"
    (home / "Pictures").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def test_the_pictures_of_the_panels_and_of_a_round_are_made_as_server_py_makes_them(
    pictures_home: Path, servers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Status → History's *Keep as a picture* (/api/history/picture): the round's report as
    the panel shows it, in a picture of the panel, kept in the Pictures folder under the same
    name, with the same pixels; again: a file of its own; a round without frame rates, one not
    kept and what is no round refused in the same words. The picture of all panels
    (/api/panels/photo) without a panel loop: refused in the same words."""
    from PIL import Image
    from test_editor_server import kept_rounds

    from libre_panel import tray

    monkeypatch.setattr(tray, "open_path", lambda path: None)
    ask, homes = servers
    ids = [kept_rounds(home) for home in homes]
    assert ids[0] == ids[1]
    first, last, puzzle = ids[0]
    folder = pictures_home / "Pictures" / "Libre Panel"
    files = []
    for ident in (last, last):
        theirs, ours = ask(_post("/api/history/picture", {"id": ident}))
        assert theirs[0] == "HTTP/1.0 200 OK", theirs[2]
        python = json.loads(theirs[2])
        assert ours[0] == "HTTP/1.0 200 OK", ours[2]
        mine = json.loads(ours[2])
        assert mine["folder"] == python["folder"] == str(folder)
        assert isinstance(mine["opened"], bool)
        files.append((Path(python["file"]), Path(mine["file"])))
    names = [path.name for pair in files for path in pair]
    assert names[0].startswith("Starfall 2023-11-1") and names[0].endswith(".png")
    # (Python's first, then the next free name for Rust's)
    stem = names[0][: -len(".png")]
    assert names == [f"{stem}.png", f"{stem} (2).png", f"{stem} (3).png", f"{stem} (4).png"]
    python_picture, rust_picture = (Image.open(path) for path in files[0])
    assert rust_picture.size == python_picture.size and python_picture.width > 1920
    assert rust_picture.convert("RGB").tobytes() == python_picture.convert("RGB").tobytes()
    for body in ({"id": puzzle}, {"id": 5}, {"id": "1700"}, {"id": True}, {}, [1]):
        theirs, ours = ask(_post("/api/history/picture", body))
        assert ours[0] == theirs[0], body
        assert json.loads(ours[2]) == json.loads(theirs[2]), body
    theirs, ours = ask(_get("/api/panels/photo", WITH))
    assert ours[0] == theirs[0] == "HTTP/1.0 404 Not Found"
    assert json.loads(ours[2]) == json.loads(theirs[2])


def test_modules_are_sent_to_another_panel_as_distribute_py_sends_them(servers: Any) -> None:
    """Design → a module's menu → Copy to panel 2 (/api/panels/send): a built-in theme gets a
    copy of the person's own with the module on free cells, and the panel shows it from then
    on, the same file and config.toml as Python's; again: into the copy, beside the first,
    with an id of its own; what cannot be sent refused in the same words."""
    ask, homes = servers
    for home in homes:
        with (home / "config.toml").open("a", encoding="utf-8") as config:
            config.write('\n[[panels]]\nmodel = "turing-9.2-usb"\ntheme = "slate"\n')
    cpu = {"type": "module", "id": "cpu", "module": "stat", "source": "cpu", "col": 0,
           "row": 0, "cols": 2, "rows": 1}  # fmt: skip
    theirs, ours = ask(_post("/api/panels/send", {"panel": 2, "modules": [cpu]}))
    assert ours[0] == theirs[0] == "HTTP/1.0 200 OK", ours[2]
    python, mine = json.loads(theirs[2]), json.loads(ours[2])
    assert mine == python == {"theme": "slate-own", "copied": True, "ids": ["cpu"]}
    copies = [(home / "themes" / "slate-own" / "theme.json").read_text("utf-8") for home in homes]
    assert copies[1] == copies[0] and '"Slate (meins)"' in copies[0]  # (in German)
    configs = [(home / "config.toml").read_text("utf-8") for home in homes]
    assert configs[1] == configs[0] and 'theme = "slate-own"' in configs[0]
    theirs, ours = ask(_post("/api/panels/send", {"panel": 2, "modules": [cpu, cpu]}))
    assert ours[0] == theirs[0] == "HTTP/1.0 200 OK", ours[2]
    python, mine = json.loads(theirs[2]), json.loads(ours[2])
    assert mine["theme"] == python["theme"] == "slate-own" and not mine["copied"]
    assert len(mine["ids"]) == len(python["ids"]) == 2 and "cpu" not in mine["ids"]

    def placed(home: Path) -> list[Any]:
        data = json.loads((home / "themes" / "slate-own" / "theme.json").read_text("utf-8"))
        modules = [w for w in data["widgets"] if w.get("type") == "module"]
        return [{k: v for k, v in w.items() if k != "id"} for w in modules]

    assert placed(homes[1]) == placed(homes[0]) and len(placed(homes[0])) == 3
    for body in ({"panel": 3, "modules": [cpu]}, {"panel": 2, "modules": [{"type": "text"}]},
                 {"panel": True, "modules": [cpu]}, {"panel": 2, "modules": []},
                 {"panel": 2, "modules": [1]}, {"panel": 2}, [2]):  # fmt: skip
        theirs, ours = ask(_post("/api/panels/send", body))
        assert ours[0] == theirs[0], body
        assert json.loads(ours[2]) == json.loads(theirs[2]), body


def test_the_panel_test_runs_on_the_app_s_panel_as_doctor_session_py_runs_it(
    tmp_path: Path,
) -> None:
    """Status → Diagnose → panel test on the running app with a simulated panel: the loop gives
    the panel to the test, which asks with the app's buttons (the test cards both ways up, the
    ruler's four edges, the brightness), sends whole frames and the brightness, opens the
    panel again and reports; the loop then has the panel again. A second click on an answered
    question, and a cancel, as Python's."""
    rig = Rig(tmp_path)
    try:
        rig.config(
            'theme = "slate"\nlanguage = "en"\n[device]\ndriver = "turzx"\n'
            'model = "turing-9.2-usb"\nbrightness = 55\n[video]\nmode = "off"\n'
            "[sensors]\nproviders = []\n"
        )
        env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
               **_libraries(), **rig.env()}  # fmt: skip
        command = [str(_program()), "start", "--port", "0", "--no-browser"]
        with subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8") as program:  # fmt: skip
            try:
                assert program.stdout is not None
                address = program.stdout.readline().split(" running at ")[1].split()[0]
                port = int(address.split(":")[2].split("/")[0])
                token = address.split("#token=")[1]

                def ask(request: str | bytes) -> tuple[str, Any]:
                    text = request if isinstance(request, bytes) else request.encode("utf-8")
                    text = text.replace(b"{host}", f"127.0.0.1:{port}".encode())
                    text = text.replace(b"{token}", token.encode())
                    status, _, answer = _asked(port, text)
                    return status.split(" ", 2)[1], json.loads(answer)

                assert _waited(lambda: len(rig.fake.frames) > 0 or None, 30), "no frame shown"
                assert ask(_get("/api/doctor", WITH))[1] == {
                    "state": "idle", "question": None, "error": "", "steps": []}  # fmt: skip
                status, said = ask(_post("/api/doctor", {"action": "start"}))
                assert status == "200" and said["state"] == "running", said
                status, said = ask(_post("/api/doctor", {"action": "start"}))
                assert (status, said["error"]) == ("409", "the panel test is running already")
                before, asked, stale = len(rig.fake.frames), [], None
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    state = ask(_get("/api/doctor", WITH))[1]
                    if state["state"] not in ("running", "asking"):
                        break
                    question = state["question"]
                    if question is None:
                        time.sleep(0.05)
                        continue
                    asked.append(question["key"])
                    answer: dict[str, Any] = {"id": question["id"]}
                    if question["kind"] == "confirm":
                        answer["yes"] = True
                    elif question["kind"] == "number":
                        answer["number"] = 4
                    status, said = ask(_post("/api/doctor", {"action": "answer",
                                                             "answer": answer}))  # fmt: skip
                    assert status == "200", said
                    stale = answer
                assert state["state"] == "done", state
                assert asked == ["card-landscape", "card-portrait", "ruler-top", "ruler-bottom",
                                 "ruler-left", "ruler-right", "brightness-watch",
                                 "brightness"]  # fmt: skip
                status, said = ask(_post("/api/doctor", {"action": "answer", "answer": stale}))
                assert (status, said["error"]) == ("409", "the panel test asks nothing now")
                report = state["report"]
                assert report["passed"] and report["summary"] == "all checks passed", report
                assert report["model"] == {"id": "turing-9.2-usb", "label": 'Turing 9.2"'}
                names = [step["name"] for step in state["steps"]]
                assert names[:4] == ["environment", "find and open a USB panel",
                                     "handshake (command 10)", "test card, landscape"]  # fmt: skip
                assert "close and reconnect" in names and names[-1] == "video mode"
                ruler = next(s for s in state["steps"] if s["name"] == "hidden edges (ruler)")
                assert ruler["detail"] == "hidden: top 2 px, bottom 2 px, left 2 px, right 2 px"
                assert report["text"].startswith("Libre Panel doctor report — ")
                assert "RESULT: all checks passed" in report["text"]
                assert report["form"].startswith(
                    "https://github.com/syntensa/libre-panel/issues/new?template=panel_support"
                    ".yml&doctor=Libre+Panel+doctor+report"
                )
                levels = rig.fake.brightness
                # dark, bright, then config.toml's again (the panel's own scale: 55 % of 102)
                assert any(levels[i : i + 3] == [10, 102, 56] for i in range(len(levels))), levels
                sent = rig.fake.frames[before:]
                assert len(sent) >= 6 and all(f.size == (480, 1920) for f in sent)
                # the loop has the panel again
                after = len(rig.fake.frames)
                assert _waited(lambda: len(rig.fake.frames) > after or None, 30)
                status, said = ask(_post("/api/doctor", {"action": "start"}))
                assert status == "200", said
                assert _waited(lambda: ask(_get("/api/doctor", WITH))[1]["question"], 30)
                status, said = ask(_post("/api/doctor", {"action": "cancel"}))
                assert status == "200", said
                assert _waited(
                    lambda: ask(_get("/api/doctor", WITH))[1]["state"] == "cancelled" or None, 15
                )
                for body in ({"action": "nope"}, {"action": "answer", "answer": 5}, [1]):
                    status, said = ask(_post("/api/doctor", body))
                    assert status in ("400", "409"), (body, said)
            finally:
                program.terminate()
                program.wait(10)
    finally:
        rig.close()


@pytest.fixture
def community_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """GitHub's community folder as a web server of this computer (set before the servers
    start): what is put in the folder it answers, and `moved.zip` a redirect to another
    host."""
    from libre_panel.editor import community

    folder = tmp_path / "site"
    folder.mkdir()

    class Site(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            name = self.path.lstrip("/")
            if name.endswith("moved.zip"):
                self.send_response(302)
                self.send_header("Location", "http://localhost:1/elsewhere.zip")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            path = folder / name
            if not path.is_file():
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    site = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{site.server_address[1]}/index.json"
    monkeypatch.setattr(community, "INDEX_URL", url)
    monkeypatch.setenv("LP_COMMUNITY_INDEX", url)
    try:
        yield folder
    finally:
        site.shutdown()


def test_community_themes_are_listed_and_installed_as_community_py_does(
    community_site: Path, servers: Any
) -> None:
    """Start → Themes → Community themes: the list as GitHub has it (entries that are not
    sound left out), a preview, a theme installed as it came and one adapted to another panel,
    the same files as Python's; a checksum that differs, a size the list does not name, a
    redirect to another host, a theme not listed, a panel not known and what is no request
    refused in the same words; a look at a theme on a panel drawn."""
    import hashlib
    import io

    from PIL import Image

    from libre_panel.theme.model import find_theme, load_theme
    from libre_panel.theme.share import export_theme

    ask, homes = servers
    data, _name = export_theme("slate")
    theme = load_theme(find_theme("slate"))
    size = f"{theme.width}x{theme.height}"
    (community_site / "slate").mkdir()
    (community_site / "slate" / "slate.zip").write_bytes(data)
    (community_site / "wrong").mkdir()
    (community_site / "wrong" / "wrong.zip").write_bytes(data)
    (community_site / "sized").mkdir()
    (community_site / "sized" / "sized.zip").write_bytes(data)
    preview = Image.new("RGB", (40, 20), (10, 120, 200))
    preview.save(community_site / "slate" / "slate.png")
    sha = hashlib.sha256(data).hexdigest()
    entry = {"name": " Slate ", "author": "Libre Panel", "description": "the classic",
             "sizes": [size], "zip": "slate/slate.zip", "preview": "slate/slate.png",
             "sha256": sha.upper()}  # fmt: skip
    index = {"format": "libre-panel-community/1", "themes": [
        entry,
        {**entry, "name": "again"},  # (the same id once)
        {**entry, "zip": "wrong/wrong.zip", "sha256": "0" * 64},
        {**entry, "zip": "sized/sized.zip", "sizes": ["100x100"]},
        {**entry, "zip": "moved/moved.zip"},
        {**entry, "zip": "../up.zip"},  # not sound: left out
        {**entry, "sizes": ["1x1"]},
        {**entry, "name": "x" * 81},
        "nothing",
    ]}  # fmt: skip
    (community_site / "index.json").write_text(json.dumps(index), encoding="utf-8")
    theirs, ours = ask(_get("/api/community/themes", WITH))
    assert ours[0] == theirs[0] == "HTTP/1.0 200 OK", ours[2]
    listed = json.loads(ours[2])
    assert listed == json.loads(theirs[2])
    assert [t["id"] for t in listed["themes"]] == ["slate", "wrong", "sized", "moved"]
    assert listed["themes"][0]["name"] == "Slate" and listed["offline"] is False
    theirs, ours = ask(_get("/api/community/themes/slate/preview", WITH))
    assert ours[0] == theirs[0] == "HTTP/1.0 200 OK" and ours[2] == theirs[2]
    theirs, ours = ask(_get("/api/community/themes/nope/preview", WITH))
    assert ours[0] == theirs[0] == "HTTP/1.0 404 Not Found"
    theirs, ours = ask(_post("/api/community/install", {"id": "slate"}))
    assert ours[0] == theirs[0] == "HTTP/1.0 200 OK", ours[2]
    done = json.loads(ours[2])
    assert done == json.loads(theirs[2])
    kept = [sorted((p.relative_to(h / "themes" / done["id"]), p.read_bytes())
                   for p in (h / "themes" / done["id"]).rglob("*") if p.is_file())
            for h in homes]  # fmt: skip
    assert kept[1] == kept[0] and kept[0]
    asked = {"id": "slate", "model": "turing-2.1-usb"}
    theirs, ours = ask(_post("/api/community/install", asked))
    assert ours[0] == theirs[0] == "HTTP/1.0 200 OK", ours[2]
    mine, python = json.loads(ours[2]), json.loads(theirs[2])
    assert mine == python and mine["adapted"] == [480, 480], mine
    adapted = [(h / "themes" / mine["id"] / "theme.json").read_text("utf-8") for h in homes]
    assert adapted[1] == adapted[0]
    for body in ({"id": "wrong"}, {"id": "sized"}, {"id": "moved"}, {"id": "nope"},
                 {"id": "slate", "model": "nope"}, {"id": "slate", "model": 5}, {"id": 5},
                 [1]):  # fmt: skip
        theirs, ours = ask(_post("/api/community/install", body))
        assert ours[0] == theirs[0], (body, ours, theirs)
        assert json.loads(ours[2]) == json.loads(theirs[2]), body
    theirs, ours = ask(_get("/api/community/themes/slate/look?model=nope", WITH))
    assert ours[0] == theirs[0] and json.loads(ours[2]) == json.loads(theirs[2])
    theirs, ours = ask(_get("/api/community/themes/slate/look?model=turing-12.3-usb", WITH))
    assert ours[0] == theirs[0] == "HTTP/1.0 200 OK", ours[2]
    sizes = [Image.open(io.BytesIO(answer[2])).size for answer in (theirs, ours)]
    assert sizes[0] == sizes[1] == (1920, 720)


def _upload(name: str, data: bytes | None) -> bytes:
    lines = [f"POST /api/player/media?name={name} HTTP/1.1", "Host: {host}", WITH,
             "Content-Type: application/octet-stream"]  # fmt: skip
    if data is not None:
        lines.append(f"Content-Length: {len(data)}")
    return "\r\n".join(lines).encode("utf-8") + b"\r\n\r\n" + (data or b"")


def test_the_player_s_library_is_told_filled_and_refused_as_server_py_does(servers: Any) -> None:
    """Player (/api/player): the library and the panels to play on as Python tells them; a
    picture taken in, its small picture drawn the same, removed again; a name that is no
    video's or picture's, no length, a file that cannot be played, playing without the
    running app and what is no action refused in the same words."""
    import io

    from PIL import Image

    ask, homes = servers

    def told(answer: tuple[str, Any, str], home: Path) -> Any:
        data = json.loads(answer[2])
        if isinstance(data, dict) and "folder" in data:
            data["folder"] = str(Path(data["folder"]).relative_to(home))
        return data

    def same(request: Any, status: str | None = None) -> Any:
        theirs, ours = ask(request)
        assert ours[0] == theirs[0], (request, theirs, ours)
        if status is not None:
            assert ours[0] == status, ours[2]
        mine = told(ours, homes[1])
        assert mine == told(theirs, homes[0]), request
        return mine

    state = same(_get("/api/player", WITH), "HTTP/1.0 200 OK")
    assert state["available"] is False and state["library"] == []
    assert state["panels"][0]["number"] == 1
    picture = io.BytesIO()
    Image.new("RGB", (64, 40), (200, 30, 90)).save(picture, "PNG")
    state = same(_upload("red.png", picture.getvalue()), "HTTP/1.0 200 OK")
    assert [item["name"] for item in state["library"]] == ["red.png"]
    theirs, ours = ask(_get("/api/player/picture?name=red.png&w=32", WITH))
    assert ours[0] == theirs[0] == "HTTP/1.0 200 OK" and ours[2] == theirs[2]
    for query in ("name=red.png&w=x", "name=nope.png", "name=../red.png"):
        theirs, ours = ask(_get(f"/api/player/picture?{query}", WITH))
        assert ours[0] == theirs[0], query
        assert json.loads(ours[2]) == json.loads(theirs[2]), query
    same(_upload(".hidden.png", b"x"))
    same(_upload("clip.exe", b"x"))
    same(_upload("broken.mp4", b"no video"))
    same(_upload("nolength.png", None))
    assert not any(any(Path(h).rglob("broken.mp4")) for h in homes)
    for body in ({"action": "play", "names": ["red.png"]}, {"action": "pause"},
                 {"action": "stop"}, {"action": "seek"}, {"action": "seek", "seconds": "1"},
                 {"action": "set", "fit": "nope"}, {"action": "jump"}, {}, [1], "play",
                 {"action": "remove", "name": "../red.png"}):  # fmt: skip
        same(_post("/api/player", body))
    state = same(_post("/api/player", {"action": "remove", "name": "red.png"}), "HTTP/1.0 200 OK")
    assert state["library"] == []


def test_the_player_plays_a_picture_on_the_app_s_panel_and_gives_it_back(tmp_path: Path) -> None:
    """Player on the running app with a simulated panel: a picture taken in and played shows
    on the panel instead of the theme, filled to its edges; paused it stays; stopped, the
    panel shows its theme again. Play on a panel 0, and names that are none or not there,
    refused."""
    import io

    from PIL import Image

    rig = Rig(tmp_path)
    try:
        rig.config(
            'theme = "slate"\nlanguage = "en"\n[device]\ndriver = "turzx"\n'
            'model = "turing-9.2-usb"\n[video]\nmode = "off"\n[sensors]\nproviders = []\n'
        )
        env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
               **_libraries(), **rig.env()}  # fmt: skip
        command = [str(_program()), "start", "--port", "0", "--no-browser"]
        with subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8") as program:  # fmt: skip
            try:
                assert program.stdout is not None
                address = program.stdout.readline().split(" running at ")[1].split()[0]
                port = int(address.split(":")[2].split("/")[0])
                token = address.split("#token=")[1]

                def ask(request: str | bytes) -> tuple[str, Any]:
                    text = request if isinstance(request, bytes) else request.encode("utf-8")
                    text = text.replace(b"{host}", f"127.0.0.1:{port}".encode())
                    text = text.replace(b"{token}", token.encode())
                    status, _, answer = _asked(port, text)
                    return status.split(" ", 2)[1], json.loads(answer)

                def red(frame: Any) -> bool:
                    colours = frame.convert("RGB").getcolors(4)
                    return colours is not None and [c for _, c in colours] == [(200, 30, 90)]

                assert _waited(lambda: len(rig.fake.frames) > 0 or None, 30), "no frame shown"
                status, state = ask(_get("/api/player", WITH))
                assert status == "200" and state["available"] is True, state
                assert state["panels"] == [{"number": 1, "name": 'Turing 9.2"'}]
                picture = io.BytesIO()
                Image.new("RGB", (64, 40), (200, 30, 90)).save(picture, "PNG")
                status, state = ask(_upload("red.png", picture.getvalue()))
                assert status == "200", state
                status, state = ask(_post("/api/player", {"action": "play", "names": ["red.png"]}))
                assert status == "200" and state["playing"]["name"] == "red.png", state
                before = len(rig.fake.frames)
                assert _waited(lambda: any(red(f) for f in rig.fake.frames[before:]) or None, 30), (
                    "the picture is not shown"
                )
                assert rig.fake.frames[-1].size == (480, 1920)
                status, state = ask(_post("/api/player", {"action": "pause"}))
                assert status == "200" and state["playing"]["paused"] is True, state
                status, state = ask(_post("/api/player", {"action": "stop"}))
                assert status == "200" and state["playing"] is None, state
                after = len(rig.fake.frames)
                assert _waited(
                    lambda: any(not red(f) for f in rig.fake.frames[after:]) or None, 30
                ), "the theme is not shown again"
                for body in ({"action": "play", "names": ["red.png"], "panels": [0]},
                             {"action": "play", "names": []},
                             {"action": "play", "names": ["gone.png"]}):  # fmt: skip
                    status, said = ask(_post("/api/player", body))
                    assert status == "400" and said["error"], (body, said)
            finally:
                program.terminate()
                program.wait(10)
    finally:
        rig.close()


def test_the_load_on_the_pc_is_measured_as_load_report_py_measures_it(tmp_path: Path) -> None:
    """Status → Diagnose → Load (/api/load) on the running app: not ready at first, then this
    process with its processor time, its private memory and working set as psutil measures
    them, its threads by their names, what it holds inside and the text for an issue."""
    import psutil

    rig = Rig(tmp_path)
    try:
        rig.config(
            'theme = "slate"\nlanguage = "en"\n[device]\ndriver = "turzx"\n'
            'model = "turing-9.2-usb"\n[video]\nmode = "off"\n[sensors]\nproviders = []\n'
        )
        env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
               **_libraries(), **rig.env()}  # fmt: skip
        command = [str(_program()), "start", "--port", "0", "--no-browser"]
        with subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8") as program:  # fmt: skip
            try:
                assert program.stdout is not None
                address = program.stdout.readline().split(" running at ")[1].split()[0]
                port = int(address.split(":")[2].split("/")[0])
                token = address.split("#token=")[1]

                def ask() -> Any:
                    text = _get("/api/load", WITH).encode("utf-8")
                    text = text.replace(b"{host}", f"127.0.0.1:{port}".encode())
                    text = text.replace(b"{token}", token.encode())
                    status, _, answer = _asked(port, text)
                    assert status.split(" ", 2)[1] == "200", answer
                    return json.loads(answer)

                assert _waited(lambda: len(rig.fake.frames) > 2 or None, 30), "no frame shown"
                cpus = psutil.cpu_count()
                first = ask()
                assert first == {"ready": False, "cpus": cpus, "window_s": 2.0}
                report = _waited(lambda: (r := ask())["ready"] and r or None, 20)
                assert report, "no load measured"
                assert report["cpus"] == cpus and 1.5 < report["window_s"] < 6
                assert 0 <= report["age_s"] < 6 and report["sample_ms"] >= 0
                main = report["processes"][0]
                assert (main["role"], main["label"], main["count"]) == ("main", "main process", 1)
                me = psutil.Process(program.pid)
                rss, uss = me.memory_info().rss, me.memory_full_info().uss
                assert abs(main["rss"] - rss) < max(rss * 0.3, 16 << 20), (main, rss)
                assert abs(main["private"] - uss) < max(uss * 0.3, 16 << 20), (main, uss)
                # (both from the share before it was rounded, as load_report.py's _shares has it)
                assert main["cpu_core"] >= 0
                # (each rounded to hundredths: the share of the PC from the unrounded core's)
                off = abs(main["cpu_pc"] - main["cpu_core"] / cpus)
                assert off <= 0.005 * (1 + 1 / cpus) + 1e-9, (main, cpus)
                total = report["total"]
                assert total["rss"] == sum(p["rss"] for p in report["processes"])
                names = [t["name"] for t in report["threads"]]
                assert names[-1] == "other" and "load-report" in names, names
                assert report["service"] is None and report["sensors"] is None
                inside = report["inside"]
                assert inside["threads"] >= 3 and inside["heap"]["in_use"] > 0, inside
                text = report["text"]
                assert text.startswith(f"Libre Panel load over {report['window_s']:.1f} s, ")
                assert "\nmain process" in text and "threads of the main process" in text
                assert "the main process holds: C heap " in text
                assert text.endswith(f"this measurement took {report['sample_ms']:.1f} ms\n")
            finally:
                program.terminate()
                program.wait(10)
    finally:
        rig.close()


_XRANDR = r"""#!{python} -I
import os, sys
log = os.environ["FAKE_X11_LOG"]
args = sys.argv[1:]
with open(log, "a", encoding="utf-8") as out:
    out.write(" ".join(["xrandr", *args]) + "\n")
said = open(log, encoding="utf-8").read().splitlines()
made = max((i for i, l in enumerate(said) if "--setmonitor" in l), default=-1)
gone = max((i for i, l in enumerate(said) if "--delmonitor" in l), default=-1)
if args == ["--listmonitors"]:
    own = made > gone
    print(f"Monitors: {{2 + own}}")
    print(" 0: +*DP-1 2560/597x1440/336+0+0  DP-1")
    print(" 1: +HDMI-1 1920/527x1080/296+2560+0  HDMI-1")
    if own:
        print(" 2: LibrePanel 480/127x1920/508+4480+0  none")
elif args == ["-q"]:
    print("Screen 0: minimum 8 x 8, current 4480 x 1440, maximum 32767 x 32767")
"""

_XPROP = r"""#!{python} -I
import sys
args = sys.argv[1:]
if args[:2] == ["-root", "_NET_CLIENT_LIST"]:
    print("_NET_CLIENT_LIST(WINDOW): window id # 0x1400003, 0x2200007, 0x3a00001")
elif args[:2] == ["-id", "0x1400003"]:
    print('_NET_WM_NAME(UTF8_STRING) = "Terminal — say \\"hi\\""')
    print('WM_CLASS(STRING) = "gnome-terminal-server", "Gnome-terminal"')
    print("_NET_WM_STATE(ATOM) = ")
elif args[:2] == ["-id", "0x2200007"]:
    print('_NET_WM_NAME(UTF8_STRING) = "Panel"')
    print('WM_CLASS(STRING) = "panel", "Panel"')
    print("_NET_WM_STATE(ATOM) = _NET_WM_STATE_SKIP_TASKBAR")
elif args[:2] == ["-id", "0x3a00001"]:
    print('_NET_WM_NAME(UTF8_STRING) = "Firefox"')
    print('WM_CLASS(STRING) = "Navigator", "firefox"')
"""

_FFMPEG = r"""#!{python} -I
import os, re, sys
with open(os.environ["FAKE_X11_LOG"], "a", encoding="utf-8") as out:
    out.write(" ".join(["ffmpeg", *sys.argv[1:]]) + "\n")
size = re.findall(r"scale=(\d+):(\d+)", " ".join(sys.argv))[-1]
frame = bytes([0, 200, 100]) * (int(size[0]) * int(size[1]))
try:
    while True:
        sys.stdout.buffer.write(frame)
        sys.stdout.buffer.flush()
except (BrokenPipeError, OSError):
    pass
"""


@pytest.fixture
def x11(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An X11 desktop of programs that say what xrandr, xprop and ffmpeg would (two monitors,
    windows, a monitor of the panel's own once made), set before the servers start; each call
    written to the log the environment names."""
    folder = tmp_path / "x11bin"
    folder.mkdir()
    for name, text in (("xrandr", _XRANDR), ("xprop", _XPROP), ("ffmpeg", _FFMPEG)):
        program = folder / name
        program.write_text(text.format(python=sys.executable), encoding="utf-8")
        program.chmod(0o755)
    log = tmp_path / "x11.log"
    log.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("DISPLAY", ":7")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setenv("FAKE_X11_LOG", str(log))
    return log


@pytest.mark.skipif(os.name == "nt", reason="an X11 desktop")
def test_the_screen_a_panel_can_show_is_told_as_mirror_py_tells_it(x11: Path, servers: Any) -> None:
    """Screen (/api/mirror) on X11: the monitors xrandr lists, the windows xprop does (one that
    skips the taskbar left out), the panels, the fits and the panel's own monitor as Python
    tells them; showing without the running app, a source that is none and setting up without
    a panel's size refused in the same words."""
    ask, homes = servers
    for path in ("/api/mirror", "/api/mirror?windows=1"):
        theirs, ours = ask(_get(path, WITH))
        assert ours[0] == theirs[0] == "HTTP/1.0 200 OK", ours[2]
        told = json.loads(ours[2])
        assert told == json.loads(theirs[2]), path
    assert told["platform"] == "x11" and [m["id"] for m in told["monitors"]] == ["DP-1", "HDMI-1"]
    assert [w["title"] for w in told["windows"]] == ['Terminal — say \\"hi\\"', "Firefox"]
    assert told["extend"] == {"how": "xrandr", "ready": True, "missing": "", "running": False,
                              "said": "", "error": ""}  # fmt: skip
    source = {"kind": "monitor", "id": "DP-1"}
    for body in ({"action": "show", "source": source}, {"action": "stop"},
                 {"action": "setup"}, {"action": "nope"}, [1], {}):  # fmt: skip
        theirs, ours = ask(_post("/api/mirror", body))
        assert ours[0] == theirs[0], body
        assert json.loads(ours[2]) == json.loads(theirs[2]), body
    from libre_panel import mirror

    for raw in (None, {"kind": "tv"}, {"kind": "monitor"}, {"kind": "monitor", "id": 3},
                {"kind": "window", "id": "0x1", "region": [0, 0, 8, 8]},
                {"kind": "monitor", "id": "DP-1", "region": [0, 0, 100, True]},
                {"kind": "monitor", "id": "DP-1", "fit": "zoom"}):  # fmt: skip
        with pytest.raises(mirror.MirrorError) as said:
            mirror.source_of(raw)
        assert str(said.value), raw


@pytest.mark.skipif(os.name == "nt", reason="an X11 desktop")
def test_a_panel_shows_a_monitor_and_its_own_as_mirror_py_does(x11: Path, tmp_path: Path) -> None:
    """The running app with a simulated panel on X11: a part of a monitor shown on the panel,
    captured with ffmpeg's command line as Python's and its pictures on the panel; the panel's
    own monitor made with xrandr's calls as extend.py's and taken off again; stopped, the theme
    again."""
    from libre_panel import capture, extend, mirror

    rig = Rig(tmp_path)
    try:
        rig.config(
            'theme = "slate"\nlanguage = "en"\n[device]\ndriver = "turzx"\n'
            'model = "turing-9.2-usb"\n[video]\nmode = "off"\n[sensors]\nproviders = []\n'
        )
        env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
               **_libraries(), **rig.env()}  # fmt: skip
        command = [str(_program()), "start", "--port", "0", "--no-browser"]
        with subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8") as program:  # fmt: skip
            try:
                assert program.stdout is not None
                address = program.stdout.readline().split(" running at ")[1].split()[0]
                port = int(address.split(":")[2].split("/")[0])
                token = address.split("#token=")[1]

                def ask(request: str | bytes) -> tuple[str, Any]:
                    text = request if isinstance(request, bytes) else request.encode("utf-8")
                    text = text.replace(b"{host}", f"127.0.0.1:{port}".encode())
                    text = text.replace(b"{token}", token.encode())
                    status, _, answer = _asked(port, text)
                    return status.split(" ", 2)[1], json.loads(answer)

                def green(frame: Any) -> bool:
                    colours = frame.convert("RGB").getcolors(4)
                    return colours is not None and [c for _, c in colours] == [(0, 200, 100)]

                assert _waited(lambda: len(rig.fake.frames) > 0 or None, 30), "no frame shown"
                status, told = ask(_get("/api/mirror", WITH))
                assert status == "200" and told["available"] and told["size"] == [1920, 480]
                source = {
                    "kind": "monitor",
                    "id": "HDMI-1",
                    "name": "HDMI-1",
                    "region": [100, 51, 801, 600],
                    "fit": "stretch",
                    "cursor": False,
                }
                status, told = ask(_post("/api/mirror", {"action": "show", "source": source}))
                assert status == "200" and told["showing"]["source"] == source, told
                before = len(rig.fake.frames)
                assert _waited(lambda: any(green(f) for f in rig.fake.frames[before:]) or None,
                               30), "the screen is not shown"  # fmt: skip
                calls = x11.read_text(encoding="utf-8").splitlines()
                line = next(c for c in calls if c.startswith("ffmpeg "))
                rate = int(line.split(",fps=")[1].split()[0])
                monitor = capture.find_monitor("HDMI-1", "x11")
                found = capture.monitor_capture("ffmpeg", monitor, rate, False, (100, 51, 801, 600),
                                                "x11")  # fmt: skip
                wanted = mirror.frames_command("ffmpeg", found, (1920, 480), "stretch", rate)
                assert line == " ".join(wanted), line
                # the panel's own monitor: xrandr told as extend.py tells it
                x11.write_text("", encoding="utf-8")
                status, told = ask(
                    _post("/api/mirror", {"action": "show", "source": {"kind": "virtual"}})
                )
                assert status == "200", told
                status, told = ask(_post("/api/mirror", {"action": "stop"}))
                assert status == "200" and told["showing"] is None, told

                # (what changes the desktop; the panel's loop asks for the monitors as well)
                def changes() -> list[str]:
                    lines = x11.read_text(encoding="utf-8").splitlines()
                    asked = ("xrandr --listmonitors", "xrandr -q")
                    return [c for c in lines if c.startswith("xrandr") and c not in asked]

                ours = changes()
                x11.write_text("", encoding="utf-8")
                extend.attach((1920, 480), "x11")
                extend.detach("x11")
                theirs = changes()
                assert ours == theirs and any("--setmonitor" in c for c in ours), ours
                after = len(rig.fake.frames)
                assert _waited(lambda: any(not green(f) for f in rig.fake.frames[after:]) or None,
                               30), "the theme is not shown again"  # fmt: skip
            finally:
                program.terminate()
                program.wait(10)
    finally:
        rig.close()


def _one_program(text: str | bytes, program: str) -> str:
    """A command line of the setup with the program that starts it said one way: Python's
    starts itself as `python -B -I -m libre_panel`, the Rust app as its own file."""
    from libre_panel import linux_rights

    text = text.decode("utf-8") if isinstance(text, bytes) else text
    python = linux_rights.program()
    if python:
        text = text.replace(shlex.join(python), "PROGRAM")
    # (the tests' Python has no program root can run: it names the one a pip install has)
    text = text.replace("sudo libre-panel ", "sudo PROGRAM ")
    return text.replace(shlex.quote(program), "PROGRAM")


def _a_pc(root: Path) -> None:
    """A computer laid out in a folder: an ASUS board whose fan chip no driver reads yet, a
    kernel that has nct6775 and drivetemp, a SATA drive, two closed energy counters and a
    TURZX panel plugged in."""
    files = {
        "sys/class/dmi/id/board_vendor": "ASUSTeK COMPUTER INC.\n",
        "sys/class/dmi/id/board_name": "ROG STRIX B650E-F\n",
        "sys/class/dmi/id/chassis_type": "3\n",
        "proc/modules": "k10temp 16384 0 - Live 0x0\n",
        "sys/class/hwmon/hwmon0/name": "k10temp\n",
        f"lib/modules/{os.uname().release}/modules.dep":
            "kernel/drivers/hwmon/nct6775.ko.zst: kernel/drivers/hwmon/nct6775-core.ko.zst\n"
            "kernel/drivers/hwmon/drivetemp.ko.zst:\n",
        "sys/block/sda/device/vendor": "ATA\n",
        "sys/class/powercap/intel-rapl:0/energy_uj": "123\n",
        "sys/class/powercap/intel-rapl:0:0/energy_uj": "45\n",
        "sys/bus/usb/devices/1-2/idVendor": "1cbe\n",
        "sys/bus/usb/devices/1-2/idProduct": "0088\n",
        "etc/modules": "# /etc/modules\nloop\n",
    }  # fmt: skip
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for zone in ("intel-rapl:0", "intel-rapl:0:0"):
        (root / "sys/class/powercap" / zone / "energy_uj").chmod(0o400)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux's setup")
def test_the_linux_setup_is_planned_and_told_as_linux_setup_py_does(tmp_path: Path) -> None:
    """`system-setup --linux` on a computer laid out in a folder: what it would do (the rule,
    the board's driver, the drives' driver, the energy counters opened, udev told), with
    --json, to remove, and what the app shows of it, as linux_setup.py and linux_rights.py say;
    a Windows flag refused in the same words."""
    import argparse
    import contextlib
    import io

    from libre_panel import linux_rights, linux_setup

    root = tmp_path / "pc"
    _a_pc(root)
    program = str(_program())
    env = {**os.environ, "LP_SETUP_ROOT": str(root), "LIBRE_PANEL_HOME": str(tmp_path),
           "LIBRE_PANEL_DATA": str(DATA), **_libraries()}  # fmt: skip
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    # (the tests' linux_setup looks at no computer: a copy of the module of its own does)
    spec = importlib.util.find_spec("libre_panel.linux_setup")
    assert spec is not None and spec.loader is not None
    looking = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(looking)
    facts = looking.gather(root)

    def python(**flags: Any) -> tuple[int, str, str]:
        args = argparse.Namespace(**{"status": False, "rapl": False, "no_rapl": False,
                                     "remove": False, "dry_run": False, "json": False,
                                     **flags})  # fmt: skip
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(pytest.MonkeyPatch.context()).delenv("DISPLAY", raising=False)
            code = linux_setup.run_cli(args, facts=facts, root=False, stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    runs = [({"rapl": True, "dry_run": True}, ["--rapl", "--dry-run"]),
            ({"dry_run": True}, ["--dry-run"]),
            ({"dry_run": True, "json": True}, ["--dry-run", "--json"]),
            ({"remove": True, "dry_run": True}, ["--remove", "--dry-run"])]  # fmt: skip
    for flags, words in runs:
        done = subprocess.run([program, "system-setup", "--linux", *words], env=env,
                              capture_output=True, text=True, timeout=60)  # fmt: skip
        theirs = python(**flags)
        assert (done.returncode, done.stdout, done.stderr) == theirs, words
    said = python(rapl=True, dry_run=True)[1]
    assert "60-libre-panel.rules" in said and "nct6775" in said and "drivetemp" in said
    assert "energy counters" in said
    done = subprocess.run([program, "system-setup", "--linux", "--status"], env=env,
                          capture_output=True, text=True, timeout=60)  # fmt: skip
    assert done.returncode == 0, done.stderr
    ours = json.loads(_one_program(done.stdout, program))
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("DISPLAY", raising=False)
        theirs = linux_rights.status(facts)
    theirs.pop("job")
    theirs = json.loads(_one_program(json.dumps(theirs), program))
    assert ours == theirs
    assert [offer["id"] for offer in ours["offers"]] == ["usb", "fans", "drives", "power"]
    done = subprocess.run([program, "system-setup", "--service"], env=env, capture_output=True,
                          text=True, timeout=60)  # fmt: skip
    assert done.returncode == 1
    assert "system-setup is for Windows" in done.stderr


@pytest.fixture
def no_computer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A computer with nothing in it for the setup to look at (set before the servers start):
    the Rust app's, as the tests' Python one looks at none."""
    root = tmp_path / "nothing"
    root.mkdir()
    monkeypatch.setenv("LP_SETUP_ROOT", str(root))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    return root


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux's setup")
def test_unlocking_is_told_and_refused_as_settings_py_does(
    no_computer: Path, servers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Settings → Unlock all sensors (/api/unlock) on Linux without a desktop: what the setup
    would bring this computer and how it is started as Python tells it; what is no request and
    a run that cannot ask for the rights here (or that runs as root already, where the tests
    do) refused in the same words."""
    from libre_panel.editor import access

    # (the Rust app knows whether it runs as root: Python's is told the same)
    monkeypatch.setattr(access, "elevated", lambda: os.geteuid() == 0)
    ask, _homes = servers
    program = str(_program())
    theirs, ours = ask(_get("/api/unlock", WITH))
    assert ours[0] == theirs[0] == "HTTP/1.0 200 OK", ours[2]
    mine, python = (json.loads(_one_program(answer[2], program)) for answer in (ours, theirs))
    assert mine == python and mine["platform"] == "linux"
    for body in ({"action": "nope"}, {"action": "unlock", "more": 1}, [1],
                 {"action": "remove", "rapl": True}, {"action": "unlock", "rapl": "yes"},
                 {"action": "unlock"}):  # fmt: skip
        theirs, ours = ask(_post("/api/unlock", body))
        assert ours[0] == theirs[0], (body, ours, theirs)
        mine, python = (json.loads(_one_program(answer[2], program)) for answer in (ours, theirs))
        assert mine == python, body
