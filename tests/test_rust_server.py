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
from pathlib import Path
from typing import Any

import pytest
from test_rust_app import DATA, _libraries, _program

from libre_panel import i18n
from libre_panel.editor import server as editor

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 12), reason="the release's http.server is Python 3.12's"
)

TOKEN = "t" * 43


def _asked(port: int, request: bytes) -> tuple[str, dict[str, str], bytes]:
    """What the server on ``port`` answers to ``request``: its status line, its headers (but
    for the server's name and the date) and its body."""
    with socket.create_connection(("127.0.0.1", port), timeout=20) as link:
        link.sendall(request)
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
    env = {**os.environ, "LIBRE_PANEL_HOME": str(homes[1]), "LIBRE_PANEL_DATA": str(DATA),
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

        def ask(request: str | bytes) -> list[tuple[str, dict[str, str], bytes]]:
            answers = []
            for port, token in sides:
                text = request if isinstance(request, bytes) else request.encode("utf-8")
                text = text.replace(b"{host}", f"127.0.0.1:{port}".encode())
                answers.append(_asked(port, text.replace(b"{token}", token.encode())))
            return answers

        yield ask, homes
    finally:
        i18n.set_language("en")
        theirs.shutdown()
        theirs.server_close()
        theirs.editor_state.close()
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
