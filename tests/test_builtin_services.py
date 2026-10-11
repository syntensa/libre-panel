"""The services Libre Panel brings itself: registered without package metadata,
off until the config lists them, and no plugin can take their names."""

import pytest

from libre_panel import cli
from libre_panel.config import Config, parse_config
from libre_panel.plugins import PluginHost, Service, ServiceManager, discover
from libre_panel.plugins.host import apply_options
from libre_panel.plugins.loader import BUILT_IN, BUILT_IN_PARTS

NAMES = sorted(BUILT_IN_PARTS["services"])


@pytest.fixture(autouse=True)
def no_real_devices(monkeypatch):
    """The devices service looks at no real drives, USB ports or controllers here."""
    from libre_panel.services import devices

    monkeypatch.setattr(devices, "on_windows", lambda: False)


def test_the_built_in_services_are_registered_as_such():
    registry = discover()  # no plugin installed, no entry point: the app's own list
    assert registry.names("services") == NAMES
    for name in NAMES:
        assert registry.parts["services"][name].source == BUILT_IN == "built in"
        cls = registry.get("services", name)
        assert issubclass(cls, Service) and cls.api == 1 and cls.name == name


@pytest.mark.parametrize("name", NAMES)
def test_every_built_in_service_has_working_defaults(name):
    cls = discover().get("services", name)
    options = apply_options(cls.options, {}, f"services.{name}")
    assert set(options) == set(cls.options)  # nothing is left without a value


def test_nothing_runs_until_the_config_lists_it():
    assert Config().services.enabled == []
    assert parse_config({}).services.enabled == []
    host = PluginHost()
    manager = ServiceManager(host)
    manager.apply(Config())
    assert manager.running == {} and manager.state == {}
    host.close()


@pytest.mark.parametrize("name", NAMES)
def test_a_built_in_service_starts_and_stops_through_the_manager(name):
    host = PluginHost()
    manager = ServiceManager(host)
    config = parse_config({"services": {"enabled": [name]}})
    manager.apply(config)
    try:
        assert manager.state == {name: "running"}
        assert isinstance(manager.running[name][0], Service)
    finally:
        manager.stop()
        host.close()
    assert manager.state == {name: "stopped"} and manager.running == {}


def test_a_plugin_cannot_take_a_built_in_name(plugin_folder):
    source = """
from libre_panel.plugins import Service


class Mine(Service):
    name = "notices"
    api = 1
"""
    plugin_folder(source, parts={"libre_panel.services": {"notices": "Mine", "mine": "Mine"}})
    registry = discover()
    assert registry.parts["services"]["notices"].source == BUILT_IN  # the app's own stays
    assert registry.names("services") == sorted([*NAMES, "mine"])
    assert any("notices" in error and "built in" in error for error in registry.errors)


def test_the_plugins_command_lists_the_built_in_services(capsys):
    assert cli.main(["plugins"]) == 0
    out = capsys.readouterr().out
    assert "no plugins installed" in out  # they are not plugins
    for name in NAMES:
        assert f"services   {name}" in out and "built in" in out
