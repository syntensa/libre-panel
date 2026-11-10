"""The Rust app's server against editor/server.py (docs/RUST.md, R9): the same requests to
both, the same answers: the status line, the headers (but for the server's name and the date)
and the body, byte for byte."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from test_rust_app import DATA, _libraries, _program

from libre_panel import i18n
from libre_panel.editor import server as editor
from libre_panel.weather import open_meteo

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 12), reason="the release's http.server is Python 3.12's"
)

TOKEN = "t" * 43


def _asked(port: int, request: bytes, done: bool = True) -> tuple[str, dict[str, str], bytes]:
    """What the server on ``port`` answers to ``request``: its status line, its headers (but
    for the server's name and the date) and its body. ``done``: the request's end said by
    closing the way there (else it stays open, as a browser's that waits for a picture)."""
    with socket.create_connection(("127.0.0.1", port), timeout=60) as link:
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
    with subprocess.Popen([str(_program()), "start", "--port", "0"], env=env,
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
    assert [find_model(m).video for m in (proven, seen, unproven)] == [True, False, False]
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
        if theirs == ("", {}, b""):  # (an exception the server does not catch: no answer)
            assert ours == theirs, path
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
            if theirs == ("", {}, b""):  # (an exception the server does not catch: no answer)
                assert ours == theirs, (path, ours)
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
