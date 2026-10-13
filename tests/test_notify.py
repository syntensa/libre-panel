"""Notices from other programs (notify.py, /api/notify, libre-panel notify): only where
switched on, only with the notify key, checked as any notice, and the key is this user's."""

import http.client
import json
import os
import stat
import sys
import threading
from types import SimpleNamespace

import pytest
from test_editor_server import request

from libre_panel import notify
from libre_panel.config import set_config_value
from libre_panel.editor.server import make_server
from libre_panel.plugins import PluginHost
from libre_panel.plugins.host import Delivery


def test_a_notice_is_checked_as_the_panel_takes_it():
    made = notify.notice({"text": "Render  finished\n", "title": "Blender", "seconds": "8"})
    assert made == {
        "text": "Render finished", "icon": None, "level": "info", "seconds": 8.0,
        "kind": "outside", "rank": 1, "payload": {"label": "Blender"},
    }  # fmt: skip
    assert notify.notice({"text": "x", "icon": "music", "level": "warning"})["icon"] == "music"
    for wrong in (
        {},
        {"text": "  "},
        {"text": "x" * 161},
        {"text": "x", "title": "y" * 41},
        {"text": "x", "seconds": "soon"},
        {"text": "x", "seconds": 0.5},
        {"text": "x", "seconds": 61},
        {"text": "x", "icon": "skull"},
        {"text": "x", "level": "panic"},
        {"text": 5},
    ):
        with pytest.raises(notify.NoticeError):
            notify.notice(wrong)


def test_the_key_is_made_once_and_kept_for_this_user(isolated_home):
    assert notify.read_key() == "" and notify.key() == ""
    first = notify.key(create=True)
    assert len(first) >= 32 and notify.key(create=True) == first == notify.read_key()
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(notify.key_file()).st_mode) == 0o600
    assert notify.new_key() != first and notify.read_key() != first
    address = notify.address(8765, "abc", "Hello there")
    assert address == "http://127.0.0.1:8765/api/notify?key=abc&text=Hello+there"


@pytest.fixture
def running(isolated_home):
    """The editor of a background app whose panel shows what it is given."""
    host = PluginHost()
    host.judge = lambda toast: Delivery(True, "")
    srv = make_server(port=0, controls=SimpleNamespace(plugin_host=host))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], host
    srv.shutdown()
    srv.server_close()
    srv.editor_state.close()


def plain(port, method, path, body=None, headers=None):
    """A request as another program makes it: no token of the app, maybe the notify key."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=hdrs)
    response = conn.getresponse()
    answer = json.loads(response.read())
    conn.close()
    return response.status, answer


def test_switched_off_nothing_comes_through(running):
    port, host = running
    key = notify.key(create=True)
    status, answer = plain(port, "GET", f"/api/notify?key={key}&text=hi")
    assert status == 403 and "switched off" in answer["error"] and not host._toasts
    assert request(port, "GET", "/api/notify-key") == (200, {"on": False})


def test_only_with_the_key_and_then_on_the_panel(running):
    port, host = running
    set_config_value("allow_notify", True)
    status, info = request(port, "GET", "/api/notify-key")
    key = info["key"]
    assert status == 200 and info["on"] and key == notify.read_key()
    assert info["address"].startswith(f"http://127.0.0.1:{port}/api/notify?key={key}&text=")
    assert info["command"].endswith('notify "Render finished"')
    assert plain(port, "GET", "/api/notify?text=hi")[0] == 403  # no key
    assert plain(port, "GET", "/api/notify?key=wrong&text=hi")[0] == 403
    # the app's own token is no notify key
    assert plain(port, "GET", "/api/notify?text=hi", headers={"X-Libre-Panel": "x"})[0] == 403
    assert not host._toasts
    status, answer = plain(port, "GET", f"/api/notify?key={key}&text=Render+done&seconds=3")
    assert (status, answer) == (200, {"shown": True, "reason": ""})
    toast = host._toasts[-1]
    assert (toast.text, toast.seconds, toast.kind, toast.service) == (
        "Render done",
        3.0,
        "outside",
        "editor",
    )
    status, answer = plain(port, "POST", "/api/notify", {"text": "Backup", "title": "NAS",
                           "level": "warning"}, headers={notify.HEADER: key})  # fmt: skip
    assert status == 200 and host._toasts[-1].payload == {"label": "NAS"}
    assert host._toasts[-1].level == "warning"
    status, answer = plain(port, "GET", f"/api/notify?key={key}&text=x&icon=skull")
    assert status == 400 and "icon" in answer["error"]
    # a new key: the old one no longer works
    status, renewed = request(port, "POST", "/api/notify-key", {})
    assert status == 200 and renewed["key"] != key
    assert plain(port, "GET", f"/api/notify?key={key}&text=hi")[0] == 403
    assert plain(port, "GET", f"/api/notify?key={renewed['key']}&text=hi")[0] == 200


def test_from_a_web_page_nothing_comes_through(running):
    port, host = running
    set_config_value("allow_notify", True)
    key = notify.key(create=True)
    headers = {"Origin": "https://example.com"}
    assert plain(port, "GET", f"/api/notify?key={key}&text=hi", headers=headers)[0] == 403
    assert not host._toasts


def test_without_the_background_app_the_answer_says_so(isolated_home):
    srv = make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        set_config_value("allow_notify", True)
        key = notify.key(create=True)
        status, answer = plain(port, "GET", f"/api/notify?key={key}&text=hi")
        assert status == 200 and answer["shown"] is False and answer["reason"]
    finally:
        srv.shutdown()
        srv.server_close()
        srv.editor_state.close()


def test_the_key_stays_out_of_the_log():
    from libre_panel.editor import access

    line = '"GET /api/notify?key=abcdefghijklmnop1234&text=hi HTTP/1.1" 200'
    assert "abcdefghijklmnop1234" not in access.hide_token(line)


def test_the_command_line_sends_it_with_the_key(running, monkeypatch, capsys):
    from libre_panel import cli

    port, host = running
    set_config_value("allow_notify", True)
    notify.key(create=True)
    monkeypatch.setattr(cli, "_instance_running", lambda: True)
    monkeypatch.setattr(
        "libre_panel.service.running_instance",
        lambda: {"editor": f"http://127.0.0.1:{port}/"},
    )
    assert cli.main(["notify", "Build green", "--title", "CI", "--seconds", "4"]) == 0
    assert host._toasts[-1].text == "Build green" and host._toasts[-1].payload == {"label": "CI"}
    set_config_value("allow_notify", False)
    assert cli.main(["notify", "nope"]) == 1
    assert "switched off" in capsys.readouterr().err
