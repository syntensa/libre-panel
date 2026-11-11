"""Plugins in the Rust app (rust/app's plugins, docs/RUST.md R10): the Python plugins run in a
process of their own, the plugin host (src/libre_panel/plugin_host.py), which the Rust app
starts where plugin folders are installed. What the app says of them and does with them,
beside what the Python app says and does."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from typing import Any

from test_plugins import SENSOR, SENSOR_PARTS, theme_data
from test_rust_app import DATA, _libraries, _program, _run

from libre_panel import cli

HOST = {"LIBRE_PANEL_PLUGIN_HOST": sys.executable,
        "LIBRE_PANEL_PLUGIN_HOST_ARGS": "-m libre_panel.plugin_host"}  # fmt: skip


def test_the_plugins_are_listed_as_cli_py_lists_them(plugin_folder, isolated_home, capsys):
    data = theme_data()
    data["name"] = "Demo dash"
    files = {"themes/__init__.py": "", "themes/demo-dash/theme.json": json.dumps(data)}
    plugin_folder(SENSOR, SENSOR_PARTS, files=files)
    plugin_folder("x = 1\n", {"libre_panel.services": {"broken": "Missing"}})
    assert cli.main(["plugins"]) == 1
    want = capsys.readouterr().out
    done = _run(isolated_home, "plugins", **HOST)
    assert (done.returncode, done.stdout) == (1, want), done.stderr
    # the theme a plugin brings is one of the app's
    themes = _run(isolated_home, "themes", **HOST)
    assert "demo-dash" in themes.stdout.split()
    out = isolated_home / "frame.png"
    drawn = _run(isolated_home, "render", "--demo", "--theme", "demo-dash", "--output", str(out),
                 **HOST)  # fmt: skip
    assert drawn.returncode == 0 and "theme demo-dash" in drawn.stderr, drawn.stderr


def test_without_plugins_no_host_is_started(isolated_home, capsys):
    assert cli.main(["plugins"]) == 0
    want = capsys.readouterr().out
    # (a host that cannot start would be said)
    done = _run(isolated_home, "plugins", LIBRE_PANEL_PLUGIN_HOST="/nonexistent")
    assert (done.returncode, done.stdout) == (0, want), done.stderr


def test_plugins_without_a_host_are_said(plugin_folder, isolated_home):
    plugin_folder(SENSOR, SENSOR_PARTS)
    done = _run(isolated_home, "plugins", LIBRE_PANEL_PLUGIN_HOST="/nonexistent")
    assert done.returncode == 1
    assert "error: the plugin host /nonexistent did not start" in done.stdout


SERVICE = """
import json

from libre_panel.plugins import Service
from libre_panel.sensors.base import Reading, SensorProvider


class Fans(SensorProvider):
    name = "demo.fans"
    api = 1

    def read(self):
        return {"fan.front": Reading("fan.front", 1200.0, "RPM", "Front")}


class Demo(Service):
    name = "demo"
    api = 1
    options = {"color": ("string", "red")}

    def start(self):
        self.done = False
        self.host.publish("demo.value", 42.0, "%", "Demo")
        self.host.publish_state("demo.round", {"n": 1})
        self.host.on("sample", self.sample)

    def sample(self, snapshot):
        fans, value = snapshot.value("fan.front"), snapshot.value("demo.value")
        if self.done or fans is None or value is None:
            return
        self.done = True
        delivery = self.host.notify("Hello from a plugin")
        heard = {"fans": fans, "value": value, "state": snapshot.state("demo.round"),
                 "delivery": [delivery.shown, delivery.reason]}
        (self.host.data_dir / "heard.json").write_text(json.dumps(heard))
        self.host.show_theme(self.options["color"])

    def summary(self):
        return {"busy": not self.done}
"""


def test_a_plugins_sensor_source_and_service_run_in_the_rust_app(plugin_folder, isolated_home):
    """A plugin's sensor source is read and its service runs, both in the plugin host: the
    service hears the samples (the source's readings and its own among them), publishes, shows
    a toast and asks for a theme, which the panel then shows."""
    plugin_folder(SERVICE, {"libre_panel.sensors": {"demo.fans": "Fans"},
                            "libre_panel.services": {"demo": "Demo"}})  # fmt: skip
    for name, colour in (("red", "#dc1e1e"), ("blue", "#1e1edc")):
        folder = isolated_home / "themes" / name
        folder.mkdir(parents=True)
        theme = {"format": "libre-panel-theme/1", "name": name,
                 "display": {"width": 800, "height": 480}, "palette": {"bg": colour},
                 "background": {"color": "@bg", "image": None}, "refresh_ms": 500,
                 "widgets": []}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    (isolated_home / "config.toml").write_text(
        'theme = "red"\nfps = 10\nrefresh_ms = 500\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = ["demo.fans"]\n'
        '[services]\nenabled = ["demo"]\n[services.demo]\ncolor = "blue"\n',
        encoding="utf-8",
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(isolated_home), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), **HOST}  # fmt: skip
    heard_file = isolated_home / "plugins-data" / "demo" / "heard.json"
    blue = False
    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            until = time.monotonic() + 30
            while time.monotonic() < until and not blue:
                try:
                    from PIL import Image

                    with Image.open(isolated_home / "panel.png") as frame:
                        blue = frame.convert("RGB").getpixel((400, 50)) == (30, 30, 220)
                except (OSError, SyntaxError, ValueError):
                    pass
                time.sleep(0.05)
        finally:
            if os.name == "nt":
                program.terminate()
            else:
                program.send_signal(signal.SIGINT)
            said = program.communicate(timeout=30)[1]
    assert blue, said
    heard = json.loads(heard_file.read_text("utf-8"))
    assert heard["fans"] == 1200.0 and heard["value"] == 42.0, heard
    assert heard["state"] == {"n": 1}
    assert heard["delivery"][0] is True, heard
    lines = [line.removeprefix("libre-panel: ") for line in said.splitlines()]
    assert "service 'demo' started" in lines, said
    assert "service demo: shows theme 'blue'" in lines, said
    assert "not in this build yet" not in said


def test_settings_list_the_plugins_as_settings_py_lists_them(plugin_folder, isolated_home):
    """Settings' catalog: the plugins' services (where each came from, why it does not load,
    the options it declares) among Libre Panel's own in their order, and their sensor sources
    after the built-in ones; an option of a plugin's service checked as it declares it."""
    from test_rust_server import _asked

    from libre_panel.editor.settings import Restart, Settings

    services = {"demo": "Demo", "aaa": "Missing"}
    plugin_folder(SERVICE, {"libre_panel.sensors": {"demo.fans": "Fans"},
                            "libre_panel.services": services})  # fmt: skip
    config = isolated_home / "config.toml"
    config.write_text("[sensors]\nproviders = []\n", encoding="utf-8")
    want = Settings(config, None, Restart())._catalog()
    env = {**os.environ, "LIBRE_PANEL_HOME": str(isolated_home), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), **HOST}  # fmt: skip
    pipe = subprocess.PIPE
    ours = subprocess.Popen([str(_program()), "editor", "--port", "0"], env=env, stdout=pipe,
                            stderr=pipe, encoding="utf-8")  # fmt: skip
    try:
        assert ours.stdout is not None
        address = ours.stdout.readline().split(" running at ")[1].split()[0]
        port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]

        def ask(method: str, path: str, body: str = "") -> tuple[str, dict]:
            request = (f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                       f"X-Libre-Panel: {token}\r\nContent-Type: application/json\r\n"
                       f"Content-Length: {len(body.encode())}\r\n\r\n{body}")  # fmt: skip
            status, _, answer = _asked(port, request.encode())
            return status, json.loads(answer)

        _, told = ask("GET", "/api/settings")
        got = told["catalog"]
        assert got["services"] == want["services"]
        assert got["providers"] == want["providers"]
        status, _ = ask("POST", "/api/settings", json.dumps({"set": {"services.demo.color": 5}}))
        assert status == "HTTP/1.0 400 Bad Request"
        status, _ = ask("POST", "/api/settings",
                        json.dumps({"set": {"services.demo.color": "green"}}))  # fmt: skip
        assert status == "HTTP/1.0 200 OK", status
        assert 'color = "green"' in config.read_text("utf-8")
    finally:
        ours.send_signal(signal.SIGINT) if os.name != "nt" else ours.terminate()
        ours.communicate(timeout=30)


def test_a_plugins_screen_and_widget_type_are_drawn_as_the_python_app_draws_them(
    plugin_folder, isolated_home, tmp_path
):
    from PIL import Image, ImageChops
    from test_plugins import DRAWING, DRAWING_PARTS

    plugin_folder(DRAWING, parts=DRAWING_PARTS)
    data = theme_data({"name": "tint", "options": {"color": "#102030"}}, [
        {"type": "demo.bar", "id": "bar", "x": 10, "y": 20, "w": 120, "h": 14,
         "sensor": "disk.load", "glow": 0.6, "color": "@accent"},
        {"type": "text", "id": "label", "x": 10, "y": 50, "text": "over the screen",
         "font_size": 16},
    ])  # fmt: skip
    data["palette"] = {"accent": "#40c0ff"}
    folder = isolated_home / "themes" / "plugged"
    folder.mkdir(parents=True)
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
    want, got = tmp_path / "python.png", tmp_path / "rust.png"
    # (disk.load: a demo reading that holds still)
    assert cli.main(["preview", "plugged", "--output", str(want)]) == 0
    done = _run(isolated_home, "preview", "plugged", "--output", str(got), **HOST)
    assert done.returncode == 0, done.stderr
    with Image.open(want) as python, Image.open(got) as rust:
        assert rust.size == python.size == (200, 100)
        assert rust.getpixel((190, 90)) == (16, 32, 48)  # the screen under everything
        assert ImageChops.difference(rust.convert("RGB"), python.convert("RGB")).getbbox() is None
    # a screen no plugin has: no frame made in Python's place without it
    data["screen"] = {"name": "nowhere", "options": {}}
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
    done = _run(isolated_home, "preview", "plugged", "--output", str(got),
                LIBRE_PANEL_PLUGIN_HOST="/nonexistent")  # fmt: skip
    assert done.returncode != 0


SWITCHING = """
from PIL import Image, ImageDraw

from libre_panel.plugins import Service, ToastStyle, Transition


class Curtain(Transition):
    name = "demo.curtain"
    api = 1
    duration = 3.0

    def frame(self, old, new, t):
        out = old.copy()
        width = round(new.width * t)
        out.paste(new.crop((0, 0, width, new.height)), (0, 0))
        return out


class Green(ToastStyle):
    name = "demo.green"
    api = 1
    options = {"height": ("int", 40)}
    leave_s = 0.1

    def draw(self, frame, toast, age, previous=None):
        out = frame.copy()
        ImageDraw.Draw(out).rectangle([0, 0, out.width - 1, self.options["height"] - 1],
                                      fill=(0, 220, 0))
        return out


class Switcher(Service):
    name = "switcher"
    api = 1

    def start(self):
        self.done = False
        self.host.on("sample", self.sample)

    def sample(self, snapshot):
        if self.done:
            return
        self.done = True
        self.host.notify("a green band")
        self.host.show_theme("blue", transition=("demo.curtain", {}))
"""


def test_a_plugins_transition_and_toast_style_play_in_the_rust_app(plugin_folder, isolated_home):
    """A theme's toast style from a plugin draws the toasts, and a plugin's transition plays
    when a service switches the theme with it: both run in the plugin host."""
    from PIL import Image

    plugin_folder(SWITCHING, {"libre_panel.toasts": {"demo.green": "Green"},
                              "libre_panel.transitions": {"demo.curtain": "Curtain"},
                              "libre_panel.services": {"switcher": "Switcher"}})  # fmt: skip
    for name, colour in (("red", "#dc1e1e"), ("blue", "#1e1edc")):
        folder = isolated_home / "themes" / name
        folder.mkdir(parents=True)
        theme = {"format": "libre-panel-theme/1", "name": name,
                 "display": {"width": 800, "height": 480}, "palette": {"bg": colour},
                 "background": {"color": "@bg", "image": None}, "refresh_ms": 500,
                 "toast": {"style": "demo.green", "options": {"height": 30}},
                 "widgets": []}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    (isolated_home / "config.toml").write_text(
        'theme = "red"\nfps = 10\nrefresh_ms = 500\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[services]\nenabled = ["switcher"]\n',
        encoding="utf-8",
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(isolated_home), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), **HOST}  # fmt: skip
    seen = {"band": False, "curtain": False, "blue": False}
    red, blue, green = (220, 30, 30), (30, 30, 220), (0, 220, 0)
    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            until = time.monotonic() + 40
            while time.monotonic() < until and not all(seen.values()):
                try:
                    with Image.open(isolated_home / "panel.png") as frame:
                        frame = frame.convert("RGB")
                        top, left, right = (frame.getpixel(at) for at in
                                            ((400, 10), (40, 300), (760, 300)))  # fmt: skip
                except (OSError, SyntaxError, ValueError):
                    time.sleep(0.02)
                    continue
                seen["band"] |= top == green
                seen["curtain"] |= left == blue and right == red
                seen["blue"] |= left == blue and right == blue
                time.sleep(0.02)
        finally:
            if os.name == "nt":
                program.terminate()
            else:
                program.send_signal(signal.SIGINT)
            said = program.communicate(timeout=30)[1]
    assert all(seen.values()), (seen, said)
    assert "failed" not in said and "not installed" not in said, said


def test_a_plugins_editor_page_is_served_as_server_py_serves_it(plugin_folder, isolated_home):
    """A plugin's page among the app's own (its title in the app's language, its icon), its
    files from its folder and nothing beside them, and its API answered in the plugin host:
    a query, a body, a status of its own and its failure."""
    from test_plugins import PAGE, PAGE_FILES, PAGE_PARTS
    from test_rust_server import _asked

    plugin_folder(PAGE, PAGE_PARTS, files=PAGE_FILES)
    (isolated_home / "config.toml").write_text(
        '[device]\ndriver = "virtual"\n[sensors]\nproviders = []\n', encoding="utf-8"
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(isolated_home), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), **HOST}  # fmt: skip
    with subprocess.Popen([str(_program()), "editor", "--port", "0"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            assert program.stdout is not None
            address = program.stdout.readline().split(" running at ")[1].split()[0]
            port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]

            def ask(method: str, path: str, body: Any = None) -> tuple[int, Any]:
                data = json.dumps(body).encode() if body is not None else b""
                head = (f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                        f"X-Libre-Panel: {token}\r\nContent-Type: application/json\r\n"
                        f"Content-Length: {len(data)}\r\n\r\n")  # fmt: skip
                status, _, answer = _asked(port, head.encode() + data)
                code = int(status.split()[1])
                try:
                    return code, json.loads(answer)
                except ValueError:
                    return code, answer

            assert ask("GET", "/api/plugins/pages") == (200, [
                {"id": "analysis", "title": "Analysis", "icon": "graph"},
                {"id": "autopilot", "title": "Autopilot", "icon": ""},
                {"id": "control", "title": "Control", "icon": ""},
            ])  # fmt: skip
            status, html = ask("GET", "/plugins/analysis/")
            assert status == 200 and b"Analysis" in html
            assert ask("GET", "/plugins/analysis/app.js")[0] == 200
            assert ask("GET", "/plugins/analysis/secret.py")[0] == 404
            assert ask("GET", "/plugins/analysis/../../plugin.toml")[0] == 404
            assert ask("GET", "/plugins/nothing/")[0] == 404
            assert ask("GET", "/api/plugins/analysis/status?x=7") == (
                200, {"running": False, "q": "7"})  # fmt: skip
            assert ask("POST", "/api/plugins/analysis/echo", {"a": 1}) == (201, {"got": {"a": 1}})
            status, data = ask("GET", "/api/plugins/analysis/boom")
            assert status == 500 and "the analysis failed" in data["error"]
            assert ask("GET", "/api/plugins/pages")[0] == 200
        finally:
            if os.name == "nt":
                program.terminate()
            else:
                program.send_signal(signal.SIGINT)
            program.communicate(timeout=30)


WATCHING = """
import json
from pathlib import Path

from PIL import Image

from libre_panel.plugins import EditorPage, Screen, Service

NOTES = Path(__file__).parent / "seen.json"


class Watcher(Screen):
    name = "watcher"
    api = 1

    def render(self, snapshot, now):
        shown, toast = self.context.shown, self.context.toast
        NOTES.write_text(json.dumps({
            "shown": list(shown.size) if shown is not None else None,
            "corner": list(shown.getpixel((5, 400))[:3]) if shown is not None else None,
            "toast": toast[0].text if toast is not None else None,
        }))
        return Image.new("RGB", self.context.size, (40, 90, 160))


class Teller(Service):
    name = "teller"
    api = 1

    def start(self):
        self.host.on("sample", lambda snapshot: self.host.notify("hello there", seconds=30))


class Inspect(EditorPage):
    api = 1
    title = {"en": "Inspect"}

    def handle(self, method, path, query, body):
        ctx = self.context
        if path == "frame":
            frame = ctx.shown_frame()
            return 200, {"size": list(frame.size) if frame is not None else None,
                         "picture": list(ctx.shown_picture().size)
                         if ctx.shown_picture() is not None else None,
                         "second": ctx.shown_frame(2) is not None}
        if path == "snapshot":
            snapshot = ctx.snapshot()
            return 200, {"has": snapshot is not None and "moon.phase" in snapshot.readings}
        if path == "sources":
            return 200, {"names": [s["name"] for s in ctx.sources()]}
        if path == "notify":
            delivery = ctx.notify("from the page", payload={"image": Image.new("RGB", (8, 8))})
            return 200, {"shown": delivery.shown, "reason": delivery.reason}
        if path == "state":
            return 200, {"background": ctx.background,
                         "state": (ctx.status() or {}).get("state"),
                         "services": ctx.services()}
        return 404, {"error": "no"}
"""


def test_plugins_see_the_frame_shown_and_the_toast_and_pages_the_running_app(
    plugin_folder, isolated_home
):
    """A plugin's screen sees the frame the panel showed last and the toast on show (context
    .shown, context.toast), as the Python app's renderer gives them; a plugin's page in the
    background app reads the panel's frames, readings, sources and state and sends a message
    with a picture, through the app."""
    package = plugin_folder(WATCHING, {"libre_panel.screens": {"watcher": "Watcher"},
                                       "libre_panel.services": {"teller": "Teller"},
                                       "libre_panel.editor_pages": {"inspect": "Inspect"}},
                            files={"page/index.html": "<h1>Inspect</h1>"})  # fmt: skip
    folder = isolated_home / "themes" / "watched"
    folder.mkdir(parents=True)
    theme = {"format": "libre-panel-theme/1", "name": "watched",
             "display": {"width": 800, "height": 480}, "screen": {"name": "watcher"},
             "background": {"color": "#000000", "image": None}, "refresh_ms": 300,
             "widgets": []}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    (isolated_home / "config.toml").write_text(
        'theme = "watched"\nfps = 10\nrefresh_ms = 300\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = []\n[services]\nenabled = ["teller"]\n',
        encoding="utf-8",
    )
    notes = isolated_home / "plugins" / package / package / "seen.json"
    env = {**os.environ, "LIBRE_PANEL_HOME": str(isolated_home), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), **HOST}  # fmt: skip
    from test_rust_server import _asked

    with subprocess.Popen([str(_program()), "start", "--port", "0", "--no-browser"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            assert program.stdout is not None
            address = program.stdout.readline().split(" running at ")[1].split()[0]
            port, token = int(address.split(":")[2].split("/")[0]), address.split("#token=")[1]

            def ask(path: str) -> Any:
                head = (f"GET /api/plugins/inspect/{path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                        f"X-Libre-Panel: {token}\r\n\r\n")  # fmt: skip
                status, _, answer = _asked(port, head.encode())
                assert status.split()[1] == "200", (status, answer)
                return json.loads(answer)

            seen: dict[str, Any] = {}
            until = time.monotonic() + 40
            while time.monotonic() < until:
                try:
                    seen = json.loads(notes.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    seen = {}
                if seen.get("shown") and seen.get("toast"):
                    break
                time.sleep(0.1)
            assert seen.get("shown") == [800, 480] and seen.get("toast") == "hello there", seen
            assert seen["corner"] == [40, 90, 160]  # the screen's own colour, as it showed
            assert ask("frame") == {"size": [800, 480], "picture": [800, 480], "second": False}
            state = ask("state")
            assert state["background"] is True and state["state"] == "showing", state
            assert state["services"].get("teller") == "running", state
            assert ask("notify") == {"shown": True, "reason": ""}
            assert isinstance(ask("sources")["names"], list)
            assert ask("snapshot") == {"has": True}  # (the sky's readings are always there)
        finally:
            if os.name == "nt":
                program.terminate()
            else:
                program.send_signal(signal.SIGINT)
            said = program.communicate(timeout=30)[1]
    assert "panicked" not in said and "Traceback" not in said, said
