"""Who may talk to the editor's server (editor/access.py): the secret token of a run, the file the
programs of the same user read it from, and the routes that want it."""

import functools
import http.client
import json
import logging
import os
import re
import socket
import stat
import threading

import pytest
from editor_token import token_of
from test_editor_server import request

from libre_panel.config import config_dir
from libre_panel.editor import access
from libre_panel.editor import server as editor
from libre_panel.editor.server import ROUTES, EditorHandler, make_server

real_elevated = access.elevated  # (conftest.py replaces it for the tests; this is the real one)
URL = "http://127.0.0.1:8765/"
TOKEN = "T" * 43


@pytest.fixture
def port():
    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()
    server.editor_state.close()


def sample(route):
    """A path that a route answers: its template with something for every part."""
    return re.sub(r"\{\w+[+*]?\}", "x", route.template)


def ask(port, method, path, token=None):
    """A request whose header says ``token`` (None: there is no such header)."""
    body = {} if method == "POST" else None
    return request(port, method, path, body, headers={access.HEADER: token})


def raw(port, method, path, headers):
    """A request with exactly these headers (and the Host), the status and the body."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, headers={"Host": f"127.0.0.1:{port}", **headers})
        response = conn.getresponse()
        body = response.read()
    finally:
        conn.close()
    return response.status, body


# -- the token ------------------------------------------------------------------------------


def test_every_run_has_a_token_of_its_own_that_a_guess_will_not_hit():
    tokens = {access.new_token() for _ in range(50)}
    assert len(tokens) == 50  # never the same twice
    assert all(len(token) >= 43 and re.fullmatch(r"[A-Za-z0-9_-]+", token) for token in tokens)
    servers = [make_server(port=0), make_server(port=0), make_server(port=0, token=TOKEN)]
    try:
        assert servers[0].token != servers[1].token
        assert servers[2].token == TOKEN  # unless it is given
    finally:
        for server in servers:
            server.server_close()
            server.editor_state.close()


def test_the_token_is_compared_in_constant_time(monkeypatch):
    seen = []
    compare = access.secrets.compare_digest

    def spy(a, b):
        seen.append((a, b))
        return compare(a, b)

    monkeypatch.setattr(access.secrets, "compare_digest", spy)
    assert access.matches("a" * 43, "a" * 43) and not access.matches("a" * 43, "b" * 43)
    assert seen == [(b"a" * 43, b"a" * 43), (b"a" * 43, b"b" * 43)]  # as bytes: any text will do
    assert not access.matches(None, "a" * 43) and not access.matches("", "a" * 43)
    assert not access.matches("tökén", "a" * 43)  # not ASCII: refused, not an error
    assert not access.matches("a" * 43, "")  # a server without a token answers nobody


def test_the_token_in_an_address():
    assert access.with_token(URL, TOKEN) == URL + "#token=" + TOKEN
    assert access.with_token(URL + "#/design", TOKEN) == URL + "#/design&token=" + TOKEN
    assert access.with_token(URL, None) == URL == access.with_token(URL, "")
    assert access.split_token(URL + "#/design&token=" + TOKEN) == (URL + "#/design", TOKEN)
    assert access.split_token(URL + "#token=" + TOKEN) == (URL, TOKEN)
    assert access.split_token(URL + "#token=" + TOKEN + "&/design") == (URL + "#/design", TOKEN)
    assert access.split_token(URL + "#/design") == (URL + "#/design", None)
    assert access.split_token(URL + "#token=short") == (URL, None)  # not one of ours
    # a new token replaces the old one
    again = access.with_token(access.with_token(URL + "#/look", "A" * 43), TOKEN)
    assert again == URL + "#/look&token=" + TOKEN
    line = f'"GET /api/themes/x/preview?w=480&token={TOKEN} HTTP/1.1" 200 -'
    assert TOKEN not in access.hide_token(line) and "w=480" in access.hide_token(line)


# -- the routes -----------------------------------------------------------------------------


def test_the_files_that_need_no_token_are_the_apps_own_and_a_few_pictures():
    """Where a route can be reached without the token, or with it in the query, is written down
    here: a route that is added is closed until this list says so."""
    public = {route.template for route in ROUTES if route.public}
    assert public == {
        "/",
        "/index.html",
        "/classic",
        "/classic/",
        "/classic/index.html",
        "/app/fonts/{name*}",
        "/app/{name*}",
        "/static/{name*}",
        "/plugins/{page}/{name*}",
    }
    assert all(route.method == "GET" for route in ROUTES if route.public)
    pictures = {route.template for route in ROUTES if route.picture}
    assert pictures == {
        "/api/themes/{theme}/assets/{rel+}",
        "/api/themes/{theme}/preview",
        "/api/community/themes/{theme}/preview",  # a community theme's, from GitHub through here
        "/api/plugins/{page}/{rest*}",
        "/api/fan-look",  # a fan's look on its tile in Settings
        "/api/symbols-look",  # ... and the weather symbols' look on theirs
        "/api/logs/file",  # the log to keep, a link in Live → Diagnose
        "/api/start-screen/picture",  # the panel's boot logo and standby clip in Settings
        "/api/start-screen/file",  # ... and the link that downloads them
        "/api/backup/file",  # the backup to keep, a link in Settings → System
        "/api/across/picture",  # the picture across the panels, in Settings → Panel
        "/api/panels/photo",  # ... and the picture of all panels, a link that downloads it
    }
    assert all(route.method == "GET" and not route.public for route in ROUTES if route.picture)
    # another program's notice, with the notify key instead of the token (test_notify.py), and
    # the AI agents' hooks and status line, with the AI key (test_agents.py)
    assert {(r.method, r.template) for r in ROUTES if r.notify} == {
        ("GET", "/api/notify"),
        ("POST", "/api/notify"),
        ("POST", "/api/ai/hook"),
        ("POST", "/api/ai/status"),
    }
    assert not any(route.notify and (route.public or route.picture) for route in ROUTES)
    # and nothing is answered but through the routes
    assert [name for name in dir(EditorHandler) if name.startswith("do_")] == ["do_GET", "do_POST"]


def test_every_route_is_reached_by_its_own_template():
    """The first route that fits a path answers it: none hides another."""
    for route in ROUTES:
        found = [
            other
            for other in ROUTES
            if other.method == route.method and other.pattern.fullmatch(sample(route))
        ]
        assert found[0] is route, (route.method, route.template, found[0].template)


def test_no_route_answers_without_the_token_but_the_apps_own_files(port):
    token = token_of(port)
    for route in ROUTES:
        path, method = sample(route), route.method
        if route.public:  # the app's own files: "not found" is possible, "no" is not
            assert ask(port, method, path)[0] != 403, (method, path)
            continue
        if route.notify:  # the notify key, not the token; switched off it says no to all
            for given in (None, token):
                status, body = ask(port, method, path, given)
                assert status == 403 and "switched off" in body["error"], (method, path)
            continue
        for wrong in (None, "", "wrong", token[:-1], token + "x", token.swapcase(), "tökén"):
            status, body = ask(port, method, path, wrong)
            assert status == 403 and "token" in body["error"], (method, path, wrong)
            assert body["reason"] == "token"  # (the app tells a page of an earlier run by it)
        # in the query it does for pictures, which an <img> asks for, and for nothing else
        status, _ = ask(port, method, f"{path}?token={token}")
        assert (status != 403) is route.picture, (method, path)
        # with it, the server answers (what it says to this request is another matter)
        assert ask(port, method, path, token)[0] != 403, (method, path)
    # a path that is no route at all: not even that is told to those without the token
    assert ask(port, "GET", "/api/nothing")[0] == 403
    assert ask(port, "POST", "/api/nothing")[0] == 403
    assert ask(port, "GET", "/api/nothing", token)[0] == 404
    assert ask(port, "POST", "/api/nothing", token)[0] == 404


class Counted:
    """The reading side of a connection, counting what is taken from it."""

    def __init__(self, rfile):
        self.rfile, self.taken = rfile, 0

    def read(self, size=-1):
        data = self.rfile.read(size)
        self.taken += len(data)
        return data

    def readline(self, size=-1):
        line = self.rfile.readline(size)
        self.taken += len(line)
        return line

    def __getattr__(self, name):
        return getattr(self.rfile, name)


def test_a_route_has_read_the_whole_request_before_the_connection_is_closed(port, monkeypatch):
    """A socket closed with unread data is reset, and Windows then drops the answer the client
    was given: ``ConnectionAbortedError`` [WinError 10053], once in a while, in the test that
    asks every route. Linux hands the answer over first, so only what the server read tells.
    It holds for every answer: with a token or without, from a route that takes the request's
    body and from one that does not."""
    from queue import Queue

    taken = Queue()
    real_setup, real_finish = EditorHandler.setup, EditorHandler.finish

    def setup(self):
        real_setup(self)
        self.rfile = Counted(self.rfile)

    def finish(self):
        taken.put(self.rfile.taken)
        real_finish(self)

    monkeypatch.setattr(EditorHandler, "setup", setup)
    monkeypatch.setattr(EditorHandler, "finish", finish)
    token = token_of(port)
    unread = []
    for route in (route for route in ROUTES if route.method == "POST"):
        for given in (token, None, "wrong"):
            body = b'{"unused": [1, 2, 3]}'
            head = (
                f"POST {sample(route)} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                + (f"{access.HEADER}: {given}\r\n" if given is not None else "")
                + f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
            ).encode()
            with socket.create_connection(("127.0.0.1", port), timeout=30) as sock:
                sock.sendall(head + body)
                while sock.recv(65536):  # to the end of the answer
                    pass
            if taken.get(timeout=30) != len(head) + len(body):
                unread.append((route.template, "token" if given == token else given))
    assert not unread


def test_other_methods_are_not_served_at_all(port):
    for method in ("PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
        status, _ = raw(port, method, "/api/settings", {access.HEADER: token_of(port)})
        assert status == 501, method  # (so no CORS preflight is answered either)


def test_the_header_counts_when_it_is_there_and_the_query_when_it_is_not(port):
    token = token_of(port)
    path = "/api/themes/libre-default/preview?w=100"
    assert raw(port, "GET", f"{path}&token={token}", {})[0] == 200  # an <img> sends no header
    assert raw(port, "GET", path, {access.HEADER: token})[0] == 200
    status, _ = raw(port, "GET", f"{path}&token={token}", {access.HEADER: "wrong"})
    assert status == 403  # a header that is there is the one that counts
    for query in (f"&token={token[:-1]}", "&token=", "&token", "&other=" + token, ""):
        assert raw(port, "GET", path + query, {})[0] == 403, query
    # a theme's own pictures and a plugin page's go the same way
    assert raw(port, "GET", f"/api/themes/libre-default/assets/x.png?token={token}", {})[0] == 404
    assert raw(port, "GET", "/api/themes/libre-default/assets/x.png", {})[0] == 403
    assert raw(port, "GET", f"/api/plugins/control/live?token={token}", {})[0] == 404  # none yet
    assert raw(port, "GET", "/api/plugins/control/live", {})[0] == 403
    # but a page that is data is not given for the token in its query
    assert raw(port, "GET", f"/api/specs?token={token}", {})[0] == 403


def test_a_picture_of_a_theme_is_given_for_the_token_in_its_address(port, isolated_home):
    import base64

    from test_editor_server import theme_dict

    assert request(port, "POST", "/api/themes/mine", {"theme": theme_dict()})[0] == 200
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(
        "POST",
        "/api/themes/mine/assets?name=logo.png",
        body=png,
        headers={"Host": f"127.0.0.1:{port}", access.HEADER: token_of(port)},
    )
    assert conn.getresponse().status == 200
    conn.close()
    path = "/api/themes/mine/assets/assets/logo.png"
    status, body = raw(port, "GET", f"{path}?token={token_of(port)}", {})
    assert status == 200 and body == png
    assert raw(port, "GET", path, {})[0] == 403
    assert raw(port, "GET", f"{path}?token=wrong", {})[0] == 403


def test_a_page_of_another_origin_is_refused_even_with_the_token(port):
    token = token_of(port)
    for origin in ("http://evil.example", "null", f"http://127.0.0.1:{port + 1}", "https://x"):
        status, body = raw(port, "GET", "/api/specs", {access.HEADER: token, "Origin": origin})
        assert status == 403 and b"origin" in body, origin
        assert raw(port, "GET", "/", {"Origin": origin})[0] == 403  # not even the page
    for origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):  # the app's own
        assert raw(port, "GET", "/api/specs", {access.HEADER: token, "Origin": origin})[0] == 200
    assert request(port, "GET", "/api/specs", host="evil.example")[0] == 403  # and the Host


def test_the_token_stays_out_of_the_log_and_the_referrer(port, caplog):
    token = token_of(port)
    caplog.set_level(logging.DEBUG, logger="libre_panel.editor.server")
    status, _ = raw(port, "GET", f"/api/themes/libre-default/preview?w=100&token={token}", {})
    assert status == 200
    request(port, "GET", "/api/specs")
    assert "preview" in caplog.text and token not in caplog.text
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
    assert conn.getresponse().getheader("Referrer-Policy") == "no-referrer"
    conn.close()


def test_a_refused_request_with_a_body_gets_its_answer(port):
    """The body is read before the refusal is sent, or Windows resets the connection."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    big = json.dumps({"theme": "x" * 1_900_000}).encode()
    for _ in range(5):
        conn.request("POST", "/api/render", body=big, headers={"Host": f"127.0.0.1:{port}"})
        response = conn.getresponse()
        response.read()
        assert response.status == 403
    conn.close()


# -- the file the programs of the same user read ---------------------------------------------


def test_the_token_is_written_for_this_user_only():
    path = access.publish(TOKEN, 4711)
    assert path == config_dir() / "private" / "editor.json" == access.token_file()
    assert json.loads(path.read_text()) == {"pid": os.getpid(), "port": 4711, "token": TOKEN}
    assert [p.name for p in path.parent.iterdir()] == ["editor.json"]  # no temporary file is left
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    access.publish("u" * 43, 4712)  # the next run replaces it
    assert json.loads(path.read_text())["token"] == "u" * 43


@pytest.mark.skipif(os.name != "posix", reason="modes of files are for Linux and the like")
def test_the_folder_of_the_token_is_closed_to_the_others_whatever_it_was():
    folder = config_dir() / "private"
    folder.mkdir(parents=True)
    folder.chmod(0o755)  # made earlier by something else, open to all
    access.publish(TOKEN, 4711)
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    old = os.umask(0)  # a program that has no umask at all: the file is still for the user only
    try:
        access.forget(TOKEN)
        access.publish("v" * 43, 4713)
    finally:
        os.umask(old)
    assert stat.S_IMODE(access.token_file().stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="owners of files are for Linux and the like")
def test_a_folder_that_belongs_to_somebody_else_is_not_used(monkeypatch, caplog):
    (config_dir() / "private").mkdir(parents=True)
    monkeypatch.setattr(access.os, "geteuid", lambda: os.stat(config_dir()).st_uid + 1)
    with caplog.at_level(logging.WARNING):
        assert access.publish(TOKEN, 4711) is None  # the app goes on; the log says why not
    assert "could not be written" in caplog.text and not access.token_file().exists()


def test_the_programs_of_the_user_find_the_token_of_the_server_they_mean():
    access.publish(TOKEN, 8765)
    assert access.read_token() == TOKEN
    assert access.read_token(URL) == TOKEN == access.read_token("http://localhost:8765/#/x")
    assert access.read_token("http://127.0.0.1:9999/") is None  # another server's address
    assert access.read_token("http://127.0.0.1/") is None
    assert access.read_token("http://127.0.0.1:99999/") is None  # (not even a port)


def test_the_token_of_a_run_that_ended_is_of_no_use(monkeypatch):
    import psutil

    access.publish(TOKEN, 8765)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    assert access.read_token(URL) is None
    assert access.read_token() == TOKEN  # (asked for the file itself)


def test_a_file_that_is_not_ours_gives_no_token():
    assert access.read_token() is None  # none yet
    path = access.token_file()
    path.parent.mkdir(parents=True)
    for text in ("", "not json", "[]", '{"token": 5}', '{"token": "short"}', '{"token": "a b"}'):
        path.write_text(text)
        assert access.read_token() is None, text


def test_a_run_forgets_only_its_own_token():
    access.publish(TOKEN, 8765)
    access.forget("u" * 43)  # the token of another run
    assert access.read_token() == TOKEN
    access.forget(TOKEN)
    assert not access.token_file().exists()
    access.forget(TOKEN)  # nothing left: no error


def test_the_token_of_the_environment_is_taken_out_of_it(monkeypatch):
    monkeypatch.setenv(access.ENV, TOKEN)
    assert access.inherited_token() == TOKEN
    assert access.ENV not in os.environ and access.inherited_token() is None
    monkeypatch.setenv(access.ENV, "not a token")
    assert access.inherited_token() is None and access.ENV not in os.environ


# -- the background app and the editor command ---------------------------------------------


def background_app():
    from test_autostart import FakeTasks
    from test_service import FakeRegistry

    from libre_panel.autostart import Autostart
    from libre_panel.service import BackgroundApp

    return BackgroundApp(
        port=0, autostart=Autostart("win32", registry=FakeRegistry(), tasks=FakeTasks())
    )


def test_the_background_app_writes_its_token_and_takes_it_away(isolated_home):
    from libre_panel.service import EditorService

    service = EditorService(port=0)
    try:
        url = service.start()
        port = int(url.rsplit(":", 1)[1].strip("/"))
        assert access.read_token(url) == service.token == token_of(port)
        assert service.address == f"{url}#token={service.token}" and service.url == url
    finally:
        service.stop()
    assert not access.token_file().exists()
    assert service.token is None and service.address is None


def test_a_second_start_prints_the_address_with_the_token(isolated_home, capsys):
    from test_service import write_config

    from libre_panel import cli
    from libre_panel.instance import InstanceLock

    write_config(isolated_home)
    app = background_app()
    with InstanceLock():  # (the app of "another process" holds the lock)
        url = app.start()
        try:
            address = f"{url}#token={app.editor.token}"
            assert cli.main(["tray", "--background"]) == 0
            assert address in capsys.readouterr().out
            assert cli.main(["editor", "--no-browser"]) == 0
            assert address in capsys.readouterr().out
        finally:
            app.quit()
            app.shutdown()


def test_the_app_started_in_a_terminal_says_the_address_with_the_token(isolated_home, capsys):
    from test_service import wait_for, write_config

    from libre_panel.tray import run_app

    write_config(isolated_home)
    app = background_app()
    done = []
    thread = threading.Thread(target=lambda: done.append(run_app(app, use_icon=False)))
    thread.start()
    try:
        assert wait_for(lambda: app.editor.token is not None and app.panel.running)
        said = []
        assert wait_for(
            lambda: said.append(capsys.readouterr().out) or "Theme editor" in "".join(said)
        )
        assert f"#token={app.editor.token}" in "".join(said)
    finally:
        app.quit()
        thread.join(10)
    assert done == [0]


def test_the_editor_command_prints_the_address_with_the_token(monkeypatch, capsys):
    opened, made = [], []
    monkeypatch.setattr(editor.webbrowser, "open", opened.append)
    make = editor.make_server
    monkeypatch.setattr(
        editor, "make_server", lambda *a, **k: made.append(make(*a, **k)) or made[0]
    )
    thread = threading.Thread(
        target=lambda: editor.serve(port=0, open_browser=True, browser=True), daemon=True
    )
    thread.start()
    try:
        for _ in range(200):
            if opened:
                break
            thread.join(0.05)
        address = re.search(r"running at (\S+)", capsys.readouterr().out).group(1)
        assert re.fullmatch(r"http://127\.0\.0\.1:\d+/#token=[A-Za-z0-9_-]{43}", address)
        assert opened == [address.replace("/#", "/#/design&")]  # the browser gets it with the view
        port = int(address.split(":")[2].split("/")[0])
        assert token_of(port) == address.rsplit("=", 1)[1]
        assert request(port, "GET", "/api/specs")[0] == 200
    finally:
        made[0].shutdown()
        thread.join(10)


# -- rights --------------------------------------------------------------------------------------


def test_root_and_an_administrator_are_elevated(monkeypatch):
    monkeypatch.setattr(access.sys, "platform", "linux")
    monkeypatch.setattr(access.os, "geteuid", lambda: 0, raising=False)
    assert real_elevated() is True
    monkeypatch.setattr(access.os, "geteuid", lambda: 1000, raising=False)
    assert real_elevated() is False
    monkeypatch.delattr(access.os, "geteuid", raising=False)  # no such thing there
    assert real_elevated() is False

    from libre_panel import autostart

    monkeypatch.setattr(access.sys, "platform", "win32")
    for answer in (True, False):
        monkeypatch.setattr(autostart, "is_admin", lambda answer=answer: answer)
        assert real_elevated() is answer


# -- the folder the highest rights trust --------------------------------------------------------


@pytest.mark.skipif(
    os.name != "posix", reason="owners and modes of files are for Linux and the like"
)
def test_a_folder_that_others_can_change_is_no_place_for_the_highest_rights(tmp_path, monkeypatch):
    folder = tmp_path / "settings"
    folder.mkdir()
    folder.chmod(0o755)  # others may look, not change
    assert access.folder_risk(folder) == ""
    (folder / "config.toml").write_text("")
    (folder / "plugins").mkdir()
    for path, mode in ((folder / "config.toml", 0o644), (folder / "plugins", 0o755)):
        path.chmod(mode)
    assert access.folder_risk(folder) == ""
    for path, mode in (
        (folder, 0o775),  # the group may
        (folder, 0o757),  # anybody may
        (folder / "config.toml", 0o666),  # in a folder nobody else may change, but the file
        (folder / "plugins", 0o775),
    ):
        folder.chmod(0o755)
        (folder / "config.toml").chmod(0o644)
        (folder / "plugins").chmod(0o755)
        path.chmod(mode)
        assert access.folder_risk(folder) == "other users may write to it", (path, oct(mode))
    folder.chmod(0o755)
    (folder / "config.toml").chmod(0o644)
    (folder / "plugins").chmod(0o755)
    monkeypatch.setattr(access.os, "geteuid", lambda: os.stat(folder).st_uid + 1)  # root, say
    assert access.folder_risk(folder) == "it belongs to another user"
    assert access.folder_risk(tmp_path / "nothing there") == ""  # (nothing to trust yet)


def test_on_windows_the_profile_is_the_place(tmp_path):
    home = tmp_path / "Users" / "me"
    (home / "AppData" / "Roaming" / "LibrePanel").mkdir(parents=True)
    (tmp_path / "LibrePanelData").mkdir()
    inside = home / "AppData" / "Roaming" / "LibrePanel"
    assert access.folder_risk(inside, home=home, posix=False) == ""
    assert access.folder_risk(home / "LibrePanelData", home=home, posix=False) == ""
    assert "outside your user folder" in access.folder_risk(
        tmp_path / "LibrePanelData", home=home, posix=False
    )


def test_the_log_says_when_the_highest_rights_run_what_others_can_change(
    isolated_home, monkeypatch, caplog
):
    from test_service import write_config

    write_config(isolated_home)
    monkeypatch.setattr(access, "folder_risk", lambda folder: "other users may write to it")
    app = background_app()
    try:
        with caplog.at_level(logging.WARNING, logger="libre_panel.service"):
            app.start()  # (an ordinary user: nothing to say)
            assert "administrator rights" not in caplog.text
            app.quit()
            app.shutdown()
            monkeypatch.setattr(access, "elevated", lambda: True)
            app = background_app()
            app.start()
        assert "runs with administrator rights" in caplog.text
        assert str(isolated_home) in caplog.text and "other users may write to it" in caplog.text
    finally:
        app.quit()
        app.shutdown()


def test_asking_for_the_highest_rights_warns_of_a_folder_others_can_change(monkeypatch, capsys):
    from libre_panel import autostart, cli

    # as on Linux, where the highest rights are refused after the warning (on Windows they
    # would make a real scheduled task)
    monkeypatch.setattr(autostart, "Autostart", functools.partial(autostart.Autostart, "linux"))
    monkeypatch.setattr(access, "folder_risk", lambda folder: "other users may write to it")
    assert cli.main(["autostart", "enable", "--elevated"]) == 1
    err = capsys.readouterr().err
    assert "other users may write to it" in err and str(config_dir()) in err
    assert "LIBRE_PANEL_HOME" in err
    monkeypatch.setattr(access, "folder_risk", lambda folder: "")
    assert cli.main(["autostart", "enable", "--elevated"]) == 1
    assert "LIBRE_PANEL_HOME" not in capsys.readouterr().err
