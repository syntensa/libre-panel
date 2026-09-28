"""Plugins: finding them, services and their host, modes, and the main loop."""

import itertools
import sys
import threading
import time

import pytest

from libre_panel import app, cli
from libre_panel.config import Config, ModeConfig, SensorsConfig, ServicesConfig, parse_config
from libre_panel.plugins import PluginHost, ServiceManager, discover
from libre_panel.plugins.loader import API_VERSION, reset_registry

_names = itertools.count()

SERVICE = """
from libre_panel.plugins import Service

events = []


class Counter(Service):
    name = "counter"
    api = 1
    options = {"step": ("int", 1), "label": ("string", "Count")}

    def start(self):
        events.append(("start", dict(self.options)))
        self.host.publish("demo.count", 0, "", self.options["label"])
        self.host.on("theme-changed", lambda theme: events.append(("theme", theme)))
        self.host.on("panel-connected", lambda: events.append(("connected",)))

    def stop(self):
        events.append(("stop",))


class NoApi(Service):
    name = "no-api"


class Broken(Service):
    name = "broken"
    api = 1

    def start(self):
        raise RuntimeError("cannot reach the game")
"""

SERVICE_PARTS = {
    "libre_panel.services": {"counter": "Counter", "no-api": "NoApi", "broken": "Broken"}
}


def events_of(package):
    return sys.modules[package].events


def wait_for(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_folder_plugins_are_found_and_checked(plugin_folder, capsys):
    package = plugin_folder(SERVICE, parts=SERVICE_PARTS)
    registry = discover()
    assert registry.names("services") == ["broken", "counter", "no-api"]
    assert registry.get("services", "counter").__name__ == "Counter"
    assert registry.get("services", "no-api") is None  # no api version declared
    assert "plugin API None" in registry.parts["services"]["no-api"].error
    assert registry.parts["services"]["counter"].source.startswith("folder ")
    assert package in sys.modules

    assert cli.main(["plugins"]) == 1  # one part does not load
    out = capsys.readouterr().out
    assert "counter" in out and "ok" in out and "FAILED" in out


def test_plugins_for_another_api_are_refused(plugin_folder):
    plugin_folder(SERVICE, api=API_VERSION + 1, parts=SERVICE_PARTS)
    registry = discover()
    assert registry.names("services") == []
    assert "written for plugin API 2" in registry.errors[0]


def test_a_plugin_that_does_not_import_is_reported(plugin_folder):
    plugin_folder("raise ImportError('needs pypresentmon')\n", parts=SERVICE_PARTS)
    registry = discover()
    assert registry.get("services", "counter") is None
    assert "needs pypresentmon" in registry.parts["services"]["counter"].error


def services_config(enabled, **options):
    return Config(services=ServicesConfig(enabled=list(enabled), options=options))


def test_services_follow_the_config(plugin_folder):
    package = plugin_folder(SERVICE, parts=SERVICE_PARTS)
    host = PluginHost()
    manager = ServiceManager(host, discover())
    manager.apply(services_config(["counter", "broken", "missing"], counter={"step": 5}))
    assert manager.state["counter"] == "running"
    assert manager.state["broken"].startswith("failed: RuntimeError")
    assert manager.state["missing"] == "not installed"
    assert events_of(package) == [("start", {"step": 5, "label": "Count"})]
    assert host.published()["demo.count"].label == "Count"

    # changed options restart it; a wrong type is refused and reported
    manager.apply(services_config(["counter"], counter={"step": 6}))
    assert events_of(package)[-2:] == [("stop",), ("start", {"step": 6, "label": "Count"})]
    manager.apply(services_config(["counter"], counter={"step": "six"}))
    assert manager.state["counter"].startswith("failed: services.counter.step: expected int")

    manager.apply(services_config(["counter"]))
    manager.stop()
    assert events_of(package)[-1] == ("stop",) and not manager.running
    host.close()


def test_a_stopped_service_leaves_nothing_behind(plugin_folder):
    from PIL import Image

    from libre_panel.sensors.base import Reading

    package = plugin_folder(SERVICE, parts=SERVICE_PARTS)
    host = PluginHost()
    manager = ServiceManager(host, discover())
    manager.apply(services_config(["counter"]))
    service = manager.running["counter"][0]
    quits = []
    service.host.on("quit", lambda: quits.append(len(manager.running)))
    service.host.set_mode("game")
    service.host.show_theme("slate")
    service.host.publish_image("demo.cover", Image.new("RGB", (2, 2)))
    host.publish(Reading("x.y", 1), owner="another")  # someone else's reading stays

    manager.apply(services_config([]))
    assert host.mode is None and host.theme is None  # a game mode must not outlive its service
    assert "demo.count" not in host.published() and host.images() == {}
    assert "x.y" in host.published()
    before = list(events_of(package))
    host.emit("panel-connected", wait=True)
    assert events_of(package) == before  # no more events for a stopped service

    manager.apply(services_config(["counter"]))
    manager.running["counter"][0].host.on("quit", lambda: quits.append(len(manager.running)))
    manager.stop()
    assert quits == [1]  # "quit" arrives while the service still runs
    host.close()


def test_option_types():
    from libre_panel.plugins.host import apply_options
    from libre_panel.plugins.loader import PluginError

    schema = {"fps": ("int", 30), "scale": ("number", 1.0), "on": ("bool", True)}
    assert apply_options(schema, {"scale": 2}, "x") == {"fps": 30, "scale": 2, "on": True}
    for bad in ({"fps": True}, {"fps": 2.5}, {"on": "yes"}):
        with pytest.raises(PluginError):
            apply_options(schema, bad, "x")


def test_modes_in_config():
    config = parse_config({"fps": 10, "modes": {"game": {"fps": 30, "theme": "slate"}}})
    assert config.modes["game"] == ModeConfig(fps=30, theme="slate")
    host = PluginHost()
    assert app._fps(config, host) == 10 and app._theme_name(config, host) == config.theme
    host.set_mode("game")
    assert app._fps(config, host) == 30 and app._theme_name(config, host) == "slate"
    host.request_theme("column", by="test")  # a service's request wins over the mode
    assert app._theme_name(config, host) == "column"
    with pytest.raises(Exception, match="between 1 and 60"):
        parse_config({"modes": {"game": {"fps": 0}}})


def test_services_steer_the_main_loop(plugin_folder, isolated_home):
    package = plugin_folder(SERVICE, parts=SERVICE_PARTS)
    host = PluginHost()
    manager = ServiceManager(host, discover())
    config = Config(
        theme="libre-default",
        fps=5,
        sensors=SensorsConfig(providers=["demo"]),
        services=ServicesConfig(enabled=["counter"]),
        modes={"game": ModeConfig(fps=20, theme="column")},
    )
    config.device.driver = "virtual"
    config.device.output = str(isolated_home / "frame.png")
    manager.apply(config)
    service = manager.running["counter"][0]
    status = app.RunStatus()
    stop = threading.Event()
    thread = threading.Thread(
        target=app.run, args=(config,), kwargs={"stop": stop, "status": status, "host": host}
    )
    thread.start()
    try:
        # published readings reach the snapshot, like any sensor
        assert wait_for(lambda: host.latest is not None and "demo.count" in host.latest.readings)
        assert host.latest.value("cpu.load") is not None  # the demo sensors are still there
        assert wait_for(lambda: ("connected",) in events_of(package))

        service.host.show_theme("slate")
        assert wait_for(lambda: status.theme == "slate")
        assert wait_for(lambda: ("theme", "slate") in events_of(package))
        service.host.set_mode("game")
        assert wait_for(lambda: status.mode == "game")
        assert status.theme == "slate"  # the service's own request still wins
        service.host.restore_theme()
        assert wait_for(lambda: status.theme == "column")  # the mode's theme
        service.host.set_mode(None)
        assert wait_for(lambda: status.theme == "libre-default" and status.mode is None)

        service.host.show_theme("does-not-exist")  # refused once, the panel keeps going
        time.sleep(0.5)
        assert status.theme == "libre-default" and thread.is_alive()
    finally:
        stop.set()
        thread.join(10)
        manager.stop()
        host.close()


def test_background_app_runs_and_updates_services(plugin_folder, isolated_home):
    from test_service import FakeRegistry, write_config

    from libre_panel.autostart import Autostart
    from libre_panel.service import BackgroundApp

    package = plugin_folder(SERVICE, parts=SERVICE_PARTS)
    write_config(isolated_home)
    config_file = isolated_home / "config.toml"
    config_file.write_text(
        config_file.read_text() + '\n[services]\nenabled = ["counter"]\n', encoding="utf-8"
    )
    background = BackgroundApp(port=0, autostart=Autostart("win32", registry=FakeRegistry()))
    background.start()
    try:
        assert background.services.state == {"counter": "running"}
        time.sleep(0.05)
        config_file.write_text(
            config_file.read_text().replace('enabled = ["counter"]', "enabled = []"),
            encoding="utf-8",
        )
        assert wait_for(lambda: not background.services.running)  # stopped by the edit
        assert ("stop",) in events_of(package)
        assert "demo.count" not in background.plugin_host.published()
    finally:
        background.quit()
        background.shutdown()


# -- screens and widget types ------------------------------------------------------

DRAWING = """
from PIL import Image, ImageDraw

from libre_panel.plugins import Screen, WidgetType

calls = {"screen": 0, "bar": 0, "closed": 0}


class Tint(Screen):
    name = "tint"
    api = 1
    label = {"en": "Tint", "de": "Tönung"}
    options = {"color": ("color", "#203040"), "animate": ("bool", False)}

    def __init__(self, context, options):
        super().__init__(context, options)
        self.moving = options["animate"]
        self._frame = Image.new("RGB", context.size, context.color(options["color"])[:3])

    def render(self, snapshot, now):
        calls["screen"] += 1
        return self._frame  # the same picture every time: nothing to compose again

    def close(self):
        calls["closed"] += 1


class Bar(WidgetType):
    type = "demo.bar"
    api = 1
    label = {"en": "Demo bar"}
    spec = {"w": ("int", 100), "h": ("int", 10), "sensor": ("sensor", "")}
    spec["color"] = ("color", "#ff0000")
    presets = [{"label": {"en": "CPU bar"}, "widget": {"sensor": "cpu.load", "w": 80}}]

    def draw(self, widget, ctx, snapshot, now):
        calls["bar"] += 1
        value = snapshot.value(widget["sensor"]) or 0
        image = Image.new("RGBA", (widget["w"], widget["h"]), (0, 0, 0, 0))
        filled = round(widget["w"] * min(max(value, 0), 100) / 100)
        box = [0, 0, filled - 1, widget["h"] - 1]
        ImageDraw.Draw(image).rectangle(box, fill=ctx.color(widget["color"]))
        return image


class Failing(Screen):
    name = "failing"
    api = 1

    def render(self, snapshot, now):
        raise RuntimeError("no light layers")


class NoDot(WidgetType):
    type = "bar"
    api = 1


class BadKind(WidgetType):
    type = "demo.bad"
    api = 1
    spec = {"size": ("float", 1.0)}


class Pathy(WidgetType):
    type = "demo.pathy"
    api = 1
    spec = {"picture": ("asset", "")}

    def draw(self, widget, ctx, snapshot, now):
        return None
"""

DRAWING_PARTS = {
    "libre_panel.screens": {"tint": "Tint", "failing": "Failing"},
    "libre_panel.widgets": {
        "demo.bar": "Bar",
        "bar": "NoDot",
        "demo.bad": "BadKind",
        "demo.pathy": "Pathy",
    },
}


def theme_data(screen=None, widgets=()):
    data = {
        "format": "libre-panel-theme/1",
        "name": "plugin test",
        "display": {"model": "custom", "width": 200, "height": 100},
        "background": {"color": "#000000"},
        "widgets": list(widgets),
    }
    if screen is not None:
        data["screen"] = screen
    return data


def bar(**fields):
    return {"type": "demo.bar", "id": "bar", "x": 10, "y": 20, "sensor": "cpu.load", **fields}


def snapshot(load=50.0):
    from libre_panel.sensors.base import Reading, Snapshot

    return Snapshot(readings={"cpu.load": Reading("cpu.load", load, "%")})


def test_screen_and_plugin_widget_are_drawn(plugin_folder):
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme.model import parse_theme

    package = plugin_folder(DRAWING, parts=DRAWING_PARTS)
    theme = parse_theme(theme_data({"name": "tint", "options": {"color": "#102030"}}, [bar()]))
    assert theme.screen == {"name": "tint", "options": {"color": "#102030", "animate": False}}
    renderer = Renderer(theme)
    frame, boxes = renderer.render(snapshot(50), 0.0)
    assert frame.getpixel((150, 80)) == (16, 32, 48)  # the screen under everything
    assert frame.getpixel((12, 22)) == (255, 0, 0)  # half of the 100 px bar is filled
    assert frame.getpixel((70, 22)) == (16, 32, 48)
    assert boxes == {"bar": [10, 20, 100, 10]}  # the screen is not a widget
    calls = sys.modules[package].calls
    renderer.render(snapshot(50), 0.1)
    assert calls["bar"] == 1  # same value: the finished picture is reused
    frame, _ = renderer.render(snapshot(100), 0.2)
    assert calls["bar"] == 2 and frame.getpixel((105, 22)) == (255, 0, 0)
    renderer.close()
    assert calls["closed"] == 1


def test_plugin_widgets_get_the_common_effects(plugin_folder):
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme.model import parse_theme

    plugin_folder(DRAWING, parts=DRAWING_PARTS)
    half = Renderer(parse_theme(theme_data(widgets=[bar(opacity=0.5)])))
    assert 120 < half.render(snapshot(100), 0.0)[0].getpixel((50, 25))[0] < 136  # over black
    glowing = Renderer(parse_theme(theme_data(widgets=[bar(glow=0.8)])))
    assert glowing.render(snapshot(100), 0.0)[0].getpixel((50, 34))[0] > 0  # below the bar


def test_themes_check_plugin_fields(plugin_folder, tmp_path):
    from libre_panel.theme.model import ThemeError, parse_theme

    plugin_folder(DRAWING, parts=DRAWING_PARTS)
    with pytest.raises(ThemeError, match="screen.options.color"):
        parse_theme(theme_data({"name": "tint", "options": {"color": "red-ish"}}))
    with pytest.raises(ThemeError, match="widgets\\[0\\].w: expected an integer"):
        parse_theme(theme_data(widgets=[bar(w="wide")]))
    with pytest.raises(ThemeError, match="leaves the theme folder"):
        pathy = {"type": "demo.pathy", "id": "p", "picture": "../../secret.png"}
        parse_theme(theme_data(widgets=[pathy]), root=tmp_path)
    theme = parse_theme(theme_data({"name": "tint", "options": {"shine": 1}}))
    assert "ignoring unknown option 'shine'" in theme.warnings[0]


def test_missing_plugins_keep_the_theme_intact(isolated_home):
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme.model import parse_theme

    reset_registry()
    data = theme_data({"name": "tint", "options": {"x": 1}}, [bar(extra={"kept": True})])
    theme = parse_theme(data)
    assert any("screen 'tint' needs a plugin" in w for w in theme.warnings)
    assert any("'demo.bar' needs a plugin" in w for w in theme.warnings)
    saved = theme.to_dict()  # saving in the editor loses nothing
    assert saved["screen"] == {"name": "tint", "options": {"x": 1}}
    assert saved["widgets"][0]["extra"] == {"kept": True}
    frame, boxes = Renderer(theme).render(snapshot(), 0.0)
    assert boxes == {} and frame.getpixel((5, 5)) == (0, 0, 0)


def test_broken_screens_and_widget_types(plugin_folder):
    from libre_panel.plugins import discover
    from libre_panel.render.renderer import Renderer
    from libre_panel.theme.model import parse_theme

    plugin_folder(DRAWING, parts=DRAWING_PARTS)
    registry = discover()
    assert registry.get("widgets", "bar") is None
    assert "needs a dot" in registry.parts["widgets"]["bar"].error
    assert registry.get("widgets", "demo.bad") is None
    assert "unknown field kind" in registry.parts["widgets"]["demo.bad"].error
    renderer = Renderer(parse_theme(theme_data({"name": "failing"}, [bar()])))
    frame, boxes = renderer.render(snapshot(100), 0.0)  # the widgets still show
    assert frame.getpixel((12, 22)) == (255, 0, 0)
    assert any("no light layers" in w for w in renderer.warnings)


def test_editor_offers_plugin_widgets_and_screens(plugin_folder):
    from test_editor_server import request

    from libre_panel.editor.server import make_server

    package = plugin_folder(DRAWING, parts=DRAWING_PARTS)
    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        status, specs = request(port, "GET", "/api/specs")
        assert specs["widgets"]["demo.bar"]["w"] == ["int", 100]
        assert specs["widget_labels"]["demo.bar"] == "Demo bar"
        assert specs["screens"]["tint"]["options"]["color"] == ["color", "#203040"]
        preset = next(p for p in specs["presets"] if p["id"] == "demo.bar-1")
        assert preset["name"] == "CPU bar" and preset["widgets"][0]["w"] == 80
        body = {"theme": theme_data({"name": "tint"}, [bar()])}
        status, rendered = request(port, "POST", "/api/render", body)
        assert status == 200 and rendered["boxes"] == {"bar": [10, 20, 100, 10]}
        assert sys.modules[package].calls["closed"] == 1  # the preview's screen ended with it
    finally:
        server.shutdown()
        server.server_close()
        server.editor_state.close()


def test_a_config_change_during_startup_is_not_missed(isolated_home):
    """The main loop compares with config.toml as it was read, not as it is later."""
    import os

    from libre_panel.config import load_config

    path = isolated_home / "config.toml"
    isolated_home.mkdir(parents=True, exist_ok=True)
    path.write_text('theme = "slate"\n', encoding="utf-8")
    config = load_config(path)
    path.write_text('theme = "column"\n', encoding="utf-8")  # edited while starting up
    os.utime(path, ns=(config.stamp + 10**9, config.stamp + 10**9))
    watcher = app._Watcher(path, known={path: config.stamp})
    assert watcher.changed()


# -- toasts and transitions -----------------------------------------------------------


def test_toasts_show_one_after_the_other():
    from PIL import Image

    from libre_panel.plugins import Toast
    from libre_panel.render.overlays import ToastLayer
    from libre_panel.theme.model import find_theme, load_theme

    theme = load_theme(find_theme("spur-ii"))  # the 9.2": 18 px hidden at the top
    layer = ToastLayer(theme)
    frame = Image.new("RGB", (1920, 480), "black")
    layer.add([Toast("first", "gpu", "info", 1.0), Toast("second", None, "error", 1.0)])
    assert layer.apply(frame, 0.0).tobytes() == frame.tobytes()  # fades in from nothing
    shown = layer.apply(frame, 0.5)
    box = Image.frombytes("RGB", shown.size, shown.tobytes()).getbbox()
    assert box is not None and box[1] >= 18 and box[2] <= 1920  # clear of the hidden strip
    assert layer.current[0].text == "first"
    layer.apply(frame, 1.3)  # first is gone: the second starts
    assert layer.current[0].text == "second"
    layer.apply(frame, 1.4)
    layer.apply(frame, 2.8)
    assert not layer.active
    assert layer.apply(frame, 2.9).tobytes() == frame.tobytes()


def test_toast_anchor_in_the_theme():
    from libre_panel.theme.model import ThemeError, parse_theme

    data = theme_data()
    data["toast"] = {"anchor": "bottom-left"}
    theme = parse_theme(data)
    assert theme.toast_anchor == "bottom-left" and theme.to_dict()["toast"] == data["toast"]
    data["toast"] = {"anchor": "middle"}
    with pytest.raises(ThemeError, match="toast.anchor"):
        parse_theme(data)


def test_transitions():
    from PIL import Image

    from libre_panel.render.overlays import transition

    old, new = Image.new("RGB", (100, 10), "black"), Image.new("RGB", (100, 10), "white")
    fade = transition("fade")
    assert fade.frame(old, new, 0.5).getpixel((50, 5)) == (127, 127, 127)
    slide = transition("slide").frame(old, new, 0.5)
    assert slide.getpixel((5, 5)) == (0, 0, 0) and slide.getpixel((95, 5)) == (255, 255, 255)
    assert transition("cut") is None and transition(None) is None
    assert transition("nonexistent") is None  # warned, switches without one


PLUGIN_TRANSITION = """
from libre_panel.plugins import Transition


class Wipe(Transition):
    name = "wipe"
    api = 1
    duration = 0.3

    def frame(self, old, new, t):
        out = old.copy()
        width = round(new.width * t)
        out.paste(new.crop((0, 0, width, new.height)), (0, 0))
        return out
"""


def test_plugin_transition(plugin_folder):
    from libre_panel.render.overlays import transition

    plugin_folder(PLUGIN_TRANSITION, {"libre_panel.transitions": {"wipe": "Wipe"}})
    assert transition("wipe").duration == 0.3


class Capture:
    """A display that keeps every frame."""

    frames = []

    def __init__(self, config):
        self.config = config
        Capture.frames = []

    def open(self):
        pass

    def close(self):
        pass

    def set_brightness(self, percent):
        pass

    def describe(self):
        return "capture"

    def show(self, frame, region=None):
        Capture.frames.append(frame.copy())


def test_main_loop_shows_toasts_and_fades_between_themes(monkeypatch, isolated_home):
    import json

    from PIL import ImageStat

    from libre_panel.plugins import Toast
    from libre_panel.theme.model import find_theme

    # a theme of the same size, plain white, to fade to
    data = json.loads((find_theme("libre-default") / "theme.json").read_text(encoding="utf-8"))
    data.update(name="white", widgets=[], background={"color": "#ffffff"})
    folder = isolated_home / "themes" / "white"
    folder.mkdir(parents=True)
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(app, "create_display", Capture)
    host = PluginHost()
    config = Config(theme="libre-default", fps=20, sensors=SensorsConfig(providers=["demo"]))
    status = app.RunStatus()
    stop = threading.Event()
    options = {"stop": stop, "status": status, "host": host}
    thread = threading.Thread(target=app.run, args=(config,), kwargs=options)
    thread.start()
    try:
        assert wait_for(lambda: len(Capture.frames) > 2)
        plain = Capture.frames[-1]
        host.notify(Toast("hello from a service", None, "info", 0.6))
        assert wait_for(lambda: _differs(Capture.frames[-1], plain))  # the toast is drawn
        time.sleep(1.0)  # it is gone again
        count = len(Capture.frames)
        host.request_theme("white", by="test", transition="fade")
        assert wait_for(lambda: status.theme == "white")
        time.sleep(0.8)
    finally:
        stop.set()
        thread.join(10)
        host.close()
    brightness = [ImageStat.Stat(f.convert("L")).mean[0] for f in Capture.frames[count:]]
    assert brightness[-1] > 250  # white in the end
    assert any(60 < b < 200 for b in brightness)  # and a frame in between: it faded


def _differs(a, b):
    from PIL import ImageChops

    return a.size == b.size and ImageChops.difference(a, b).getbbox() is not None


# -- editor pages ----------------------------------------------------------------------

PAGE = """
from libre_panel.plugins import EditorPage


class Cooling(EditorPage):
    api = 1
    title = {"en": "Cooling", "de": "Kühlung"}
    icon = "fan"

    def handle(self, method, path, query, body):
        if path == "status":
            service = self.context.service("counter")
            return 200, {"running": service is not None, "q": query.get("x", [""])[0]}
        if path == "echo" and method == "POST":
            return 201, {"got": body}
        if path == "boom":
            raise RuntimeError("the analysis failed")
        return 404, {"error": "no such thing"}
"""

PAGE_FILES = {
    "page/index.html": (
        '<!doctype html><html><head><link rel="stylesheet" href="/static/editor.css">'
        '<script type="module" src="app.js"></script></head>'
        '<body><h1 id="title">Cooling</h1><p id="out">…</p></body></html>'
    ),
    "page/app.js": (
        'import { api, loadTexts } from "/static/kit.js";\n'
        "await loadTexts();\n"
        'const status = await api("status?x=1");\n'
        'const echo = await api("echo", { method: "POST", body: { fan: 3 } });\n'
        'document.getElementById("out").textContent = `${status.q} ${echo.got.fan}`;\n'
    ),
    "page/secret.py": "print('not served')\n",
}

PAGE_PARTS = {"libre_panel.editor_pages": {"cooling": "Cooling"}}


@pytest.fixture
def editor(plugin_folder):
    from libre_panel.editor.server import make_server

    plugin_folder(PAGE, PAGE_PARTS, files=PAGE_FILES)
    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()
    server.editor_state.close()


def test_editor_pages_are_served(editor):
    from test_editor_server import request

    status, pages = request(editor, "GET", "/api/plugins/pages")
    assert pages == [{"id": "cooling", "title": "Cooling", "icon": "fan"}]
    status, html = request(editor, "GET", "/plugins/cooling/")
    assert status == 200 and b"Cooling" in html
    assert request(editor, "GET", "/plugins/cooling/app.js")[0] == 200
    assert request(editor, "GET", "/plugins/cooling/secret.py")[0] == 404  # not a page file
    assert request(editor, "GET", "/plugins/cooling/../../plugin.toml")[0] == 404
    assert request(editor, "GET", "/plugins/nothing/")[0] == 404


def test_editor_page_api(editor):
    from test_editor_server import request

    assert request(editor, "GET", "/api/plugins/cooling/status?x=7") == (
        200,
        {"running": False, "q": "7"},  # a plain `libre-panel editor` runs no services
    )
    assert request(editor, "POST", "/api/plugins/cooling/echo", {"a": 1}) == (
        201,
        {"got": {"a": 1}},
    )
    no_header = request(
        editor, "POST", "/api/plugins/cooling/echo", {}, headers={"X-Libre-Panel": ""}
    )
    assert no_header[0] == 403
    status, data = request(editor, "GET", "/api/plugins/cooling/boom")
    assert status == 500 and "the analysis failed" in data["error"]
    assert request(editor, "GET", "/api/plugins/pages")[0] == 200  # the editor keeps going


def test_page_context_finds_running_services(plugin_folder):
    from libre_panel.plugins import PageContext

    plugin_folder(SERVICE, parts=SERVICE_PARTS)
    host = PluginHost()
    manager = ServiceManager(host, discover())
    manager.apply(services_config(["counter"]))

    class Controls:
        services = manager

    assert PageContext(Controls()).service("counter") is manager.running["counter"][0]
    assert PageContext(None).service("counter") is None
    manager.stop()
    host.close()
