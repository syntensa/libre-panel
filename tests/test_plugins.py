"""Plugins: finding them, services and their host, modes, and the main loop."""

import itertools
import sys
import textwrap
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


@pytest.fixture
def plugin_folder(isolated_home):
    """A plugin installed as a folder, as in the Windows setup."""
    created = []

    def make(source=SERVICE, api=API_VERSION, parts=None):
        package = f"lp_test_plugin_{next(_names)}"
        folder = isolated_home / "plugins" / package
        (folder / package).mkdir(parents=True)
        (folder / package / "__init__.py").write_text(textwrap.dedent(source), encoding="utf-8")
        parts = parts or {
            "counter": f"{package}:Counter",
            "no-api": f"{package}:NoApi",
            "broken": f"{package}:Broken",
        }
        table = "\n".join(f'"{name}" = "{target}"' for name, target in parts.items())
        (folder / "plugin.toml").write_text(
            f'name = "{package}"\napi = {api}\n\n[entry-points."libre_panel.services"]\n{table}\n',
            encoding="utf-8",
        )
        created.append((package, folder))
        reset_registry()
        return package

    yield make
    for package, folder in created:
        sys.modules.pop(package, None)
        if str(folder) in sys.path:
            sys.path.remove(str(folder))
    reset_registry()


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
    package = plugin_folder()
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
    plugin_folder(api=API_VERSION + 1)
    registry = discover()
    assert registry.names("services") == []
    assert "written for plugin API 2" in registry.errors[0]


def test_a_plugin_that_does_not_import_is_reported(plugin_folder):
    plugin_folder(source="raise ImportError('needs pypresentmon')\n")
    registry = discover()
    assert registry.get("services", "counter") is None
    assert "needs pypresentmon" in registry.parts["services"]["counter"].error


def services_config(enabled, **options):
    return Config(services=ServicesConfig(enabled=list(enabled), options=options))


def test_services_follow_the_config(plugin_folder):
    package = plugin_folder()
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
    package = plugin_folder()
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

    package = plugin_folder()
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
        assert events_of(package)[-1] == ("stop",)
    finally:
        background.quit()
        background.shutdown()
