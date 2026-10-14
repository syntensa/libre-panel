"""The routes of the app's Settings (editor/settings.py): what they read from config.toml,
what they write to it and what they refuse, with and without the background app."""

import threading
from types import SimpleNamespace

import pytest
from test_autostart import FakeRegistry, FakeTasks
from test_editor_server import request

from libre_panel.autostart import Autostart
from libre_panel.config import config_dir, default_providers, load_config
from libre_panel.editor import access
from libre_panel.editor.server import make_server


@pytest.fixture
def make(isolated_home):
    """A server with the given background app (None: a plain ``libre-panel editor``)."""
    servers = []

    def start(controls=None):
        server = make_server(port=0, controls=controls)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_address[1]

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()
        server.editor_state.close()


@pytest.fixture
def server(make):
    return make()


class App(SimpleNamespace):
    """What the routes use of the background app: its autostart, its panel, its services."""

    def __init__(self, **kwargs):
        defaults = {
            "autostart": Autostart("linux", home=config_dir().parent / "linux-home", environ={}),
            "autostart_command": ["/opt/libre-panel/libre-panel", "tray", "--background"],
            "panel": SimpleNamespace(
                status=object(), state=lambda: {"state": "showing", "detail": ""}
            ),
            "followed": 0,
        }
        super().__init__(**{**defaults, **kwargs})

    def follow_config(self):
        self.followed += 1


def write(port, **changes):
    return request(port, "POST", "/api/settings", {"set": changes})


def config_text():
    return (config_dir() / "config.toml").read_text(encoding="utf-8")


# -- reading ---------------------------------------------------------------------------------


def test_the_settings_are_the_config_with_its_defaults_filled_in(server):
    status, data = request(server, "GET", "/api/settings")
    assert status == 200
    config = data["config"]
    assert config["device"] == {
        "model": "auto",
        "driver": "auto",
        "brightness": 60,
        "rotate": 0,
        "orientation": "",
        "motion": "auto",
        "output": "libre-panel-frame.png",
        "port": "",
    }
    assert (config["video"]["mode"], config["video"]["fps"], config["video"]["local_clip"]) == (
        "auto",
        50,
        "",
    )
    assert config["video"]["clip_asked"] is False  # nobody has asked yet
    assert config["sensors"]["providers"] == default_providers()
    assert config["sensors"]["librehardwaremonitor"] == {
        "mode": "auto",
        "url": "http://127.0.0.1:8085/data.json",
    }
    assert config["refresh_ms"] is None and config["language"] == "auto"
    assert config["weather"]["enabled"] is False and config["weather"]["latitude"] is None
    assert config["services"] == {"enabled": [], "options": {}}  # nothing runs unasked
    assert data["restart"] == []


def test_what_other_sources_keep_in_config_toml_stays_there(server):
    """Tokens, passwords and calendar addresses are not the page's business: only the
    settings the page shows are sent to it, and a write leaves the rest of the file alone."""
    import json

    config_dir().mkdir(parents=True)
    (config_dir() / "config.toml").write_text(
        '[sensors]\nproviders = ["psutil", "homeassistant", "mqtt"]\n\n'
        '[sensors.homeassistant]\nurl = "http://ha.example:8123"\ntoken = "secret-token"\n\n'
        '[sensors.mqtt]\npassword = "secret-password"\n\n'
        '[sensors.calendar]\nsources = ["https://calendar.example/private-address.ics"]\n',
        encoding="utf-8",
    )
    status, data = request(server, "GET", "/api/settings")
    assert status == 200
    sent = json.dumps(data)
    for secret in ("secret-token", "secret-password", "private-address", "ha.example"):
        assert secret not in sent, secret
    assert write(server, **{"device.brightness": 30, "refresh_ms": 400})[0] == 200
    text = config_text()
    for secret in ("secret-token", "secret-password", "private-address"):
        assert secret in text
    assert load_config().sensors.options["homeassistant"]["token"] == "secret-token"


def test_the_catalog_names_the_sources_the_drivers_and_the_services_with_their_options(server):
    _, data = request(server, "GET", "/api/settings")
    catalog = data["catalog"]
    sources = {source["name"]: source for source in catalog["providers"]}
    assert sources["psutil"]["builtin"] and {"load", "audio", "calendar", "demo"} <= set(sources)
    assert catalog["implicit_providers"] == ["media", "sky", "hardware"]
    assert catalog["default_providers"] == default_providers()
    assert {"auto", "turzx", "virtual"} <= set(catalog["drivers"])
    assert catalog["lhm_modes"] == ["auto", "service", "inprocess", "webserver"]
    services = catalog["services"]
    assert [service["name"] for service in services] == [
        "game", "memory", "notices", "autopilot", "devices", "cooling", "downloads",
    ]  # fmt: skip
    game = next(service for service in services if service["name"] == "game")
    options = {option["key"]: option for option in game["options"]}
    assert game["builtin"] and game["error"] == ""
    assert options["poll_s"] == {"key": "poll_s", "type": "number", "default": 2.0}
    assert options["steam"]["type"] == "bool" and options["programs"]["default"] == []
    memory = next(service for service in services if service["name"] == "memory")
    assert {o["key"]: o["type"] for o in memory["options"]}["report_compare"] == "int"


def test_a_plugins_service_is_listed_with_its_options(server, plugin_folder):
    plugin_folder(
        """
        from libre_panel.plugins.host import Service

        class Tick(Service):
            name = "tick"
            api = 1
            options = {"every_s": ("number", 5.0), "loud": ("bool", False)}
        """,
        {"libre_panel.services": {"tick": "Tick"}},
    )
    _, data = request(server, "GET", "/api/settings")
    names = [service["name"] for service in data["catalog"]["services"]]
    assert names[-1] == "tick" and names[:2] == ["game", "memory"]  # the built-in ones first
    tick = data["catalog"]["services"][-1]
    assert not tick["builtin"] and tick["source"].startswith("folder ")
    assert [o["key"] for o in tick["options"]] == ["every_s", "loud"]
    status, _ = write(server, **{"services.tick.every_s": 2.5, "services.tick.loud": True})
    assert status == 200
    assert load_config().services.options["tick"] == {"every_s": 2.5, "loud": True}


def test_a_plugin_that_declares_its_options_wrongly_does_not_take_the_settings_down(
    server, plugin_folder
):
    plugin_folder(
        """
        from libre_panel.plugins.host import Service

        class Slip(Service):
            name = "slip"
            api = 1
            options = {"good": ("bool", False), "bad": "bool", "worse": ("list",), 3: ("int", 1)}
        """,
        {"libre_panel.services": {"slip": "Slip"}},
    )
    status, data = request(server, "GET", "/api/settings")
    assert status == 200
    slip = next(s for s in data["catalog"]["services"] if s["name"] == "slip")
    assert [o["key"] for o in slip["options"]] == ["good", "3"]  # what is (type, default)
    assert write(server, **{"services.slip.good": True})[0] == 200
    assert write(server, **{"services.slip.bad": True})[0] == 400  # not an option it declares


def test_a_broken_config_is_told_not_overwritten(server):
    (config_dir()).mkdir(parents=True)
    (config_dir() / "config.toml").write_text("this is not toml", encoding="utf-8")
    status, data = request(server, "GET", "/api/settings")
    assert status == 409 and "config.toml" in data["error"]
    status, data = write(server, **{"device.brightness": 10})
    assert status == 409 and config_text() == "this is not toml"


# -- writing ---------------------------------------------------------------------------------


def test_settings_are_written_with_the_comments_kept(server):
    status, data = write(server, **{"device.brightness": 35, "device.rotate": 180, "video.fps": 30,
                                    "device.motion": "calm"})  # fmt: skip
    assert status == 200
    assert data["config"]["device"]["brightness"] == 35 and data["config"]["video"]["fps"] == 30
    config = load_config()
    assert (config.device.brightness, config.device.rotate, config.device.video.fps) == (
        35,
        180,
        30,
    )
    assert "# Libre Panel configuration" in config_text()  # the file is the person's
    assert config.device.motion == "calm" and data["config"]["device"]["motion"] == "calm"
    write(server, **{"video.mode": "off", "video.local_clip": "  usr/data/standby.h264 "})
    assert load_config().device.video.mode == "off"
    assert load_config().device.video.local_clip == "usr/data/standby.h264"  # as typed, trimmed


def test_writes_that_come_at_once_all_arrive(server):
    """Each starts from the file the one before left: none is lost."""
    changes = [
        {"device.brightness": 15},
        {"device.rotate": 180},
        {"video.fps": 25},
        {"refresh_ms": 400},
        {"weather.units": "imperial"},
        {"weather.update_minutes": 45},
        {"services.game.poll_s": 3},
        {"services.notices.quiet_s": 30},
    ]
    results = []
    threads = [
        threading.Thread(target=lambda c=c: results.append(write(server, **c)[0])) for c in changes
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [200] * len(changes)
    config = load_config()
    assert (config.device.brightness, config.device.rotate, config.device.video.fps) == (
        15,
        180,
        25,
    )
    assert (config.refresh_ms, config.weather.units, config.weather.update_minutes) == (
        400,
        "imperial",
        45,
    )
    assert config.services.options["game"] == {"poll_s": 3}
    assert config.services.options["notices"] == {"quiet_s": 30}


def test_a_refused_setting_leaves_all_of_them_as_they_were(server):
    write(server, **{"device.brightness": 40})
    before = config_text()
    refused = [
        {"device.brightness": 101},
        {"device.brightness": True},
        {"device.brightness": "70"},
        {"device.brightness": 70.5},
        {"device.rotate": 90},
        {"device.motion": "wild"},
        {"device.rotate": False},
        {"device.model": "no-such-panel"},
        {"device.driver": "teleport"},
        {"device.output": ""},
        {"video.mode": "always"},
        {"video.fps": 61},
        {"video.local_clip": "x" * 201},
        {"video.local_clip": 5},
        {"video.clip_asked": "yes"},
        {"video.clip_asked": 1},
        {"refresh_ms": 99},
        {"sensors.providers": []},
        {"sensors.providers": ["psutil"]},  # config.py reads that as the usual list
        {"sensors.providers": ["psutil", "psutil"]},  # the same, said twice
        {"sensors.providers": ["psutil", "nonesuch"]},
        {"sensors.providers": "psutil"},
        {"sensors.librehardwaremonitor.mode": "magic"},
        {"sensors.librehardwaremonitor.url": "file:///etc/passwd"},
        {"weather.units": "kelvin"},
        {"weather.update_minutes": 1},
        {"weather.latitude": 91, "weather.longitude": 1},
        {"weather.latitude": float("nan"), "weather.longitude": 1},
        {"device.brightness": None},  # not a thing that can be taken out
        {"services.enabled": ["game"]},  # the services are switched on and off elsewhere
        {"language": "de"},  # and the language has its own route
        {"theme": "slate"},
        {"nonesuch": 1},
        {"device.brightness": 20, "device.rotate": 90},  # one bad one spoils the write
    ]
    for changes in refused:
        status, data = write(server, **changes)
        assert status == 400 and "error" in data, changes
        assert config_text() == before, changes
    for body in ({}, {"set": {}}, {"set": []}, {"set": "x"}, {"device.brightness": 5}):
        assert request(server, "POST", "/api/settings", body)[0] == 400


def test_the_refresh_override_comes_and_goes(server):
    write(server, refresh_ms=500)
    assert load_config().refresh_ms == 500
    status, data = write(server, refresh_ms=None)
    assert status == 200 and data["config"]["refresh_ms"] is None
    assert "refresh_ms = 500" not in config_text()


def test_the_sources_are_a_list_in_the_order_given(server):
    status, data = write(server, **{"sensors.providers": ["psutil", "load", "psutil", "demo"]})
    assert status == 200 and data["config"]["sensors"]["providers"] == ["psutil", "load", "demo"]
    assert load_config().sensors.providers == ["psutil", "load", "demo"]
    write(server, **{"sensors.librehardwaremonitor.mode": "webserver"})
    write(server, **{"sensors.librehardwaremonitor.url": "http://127.0.0.1:9000/data.json"})
    _, data = request(server, "GET", "/api/settings")
    lhm = data["config"]["sensors"]["librehardwaremonitor"]
    assert lhm == {"mode": "webserver", "url": "http://127.0.0.1:9000/data.json"}
    assert "# Windows: the GPU" in config_text()  # the explanations in the file stay


def test_a_source_that_is_in_the_file_stays_even_if_it_is_not_installed(server):
    write(server, **{"sensors.providers": ["psutil", "demo"]})
    path = config_dir() / "config.toml"
    path.write_text(path.read_text().replace('"demo"', '"gone", "demo"'), encoding="utf-8")
    status, data = write(server, **{"sensors.providers": ["gone", "psutil"]})  # moved, not added
    assert status == 200 and data["config"]["sensors"]["providers"] == ["gone", "psutil"]
    assert write(server, **{"sensors.providers": ["gone", "other-gone"]})[0] == 400


def test_a_services_options_follow_the_spec_of_the_service(server):
    status, data = write(
        server,
        **{
            "services.game.poll_s": 1.5,
            "services.game.steam": False,
            "services.game.programs": ["emulator.exe = Retro", "othergame.exe"],
            "services.memory.report_compare": 5,
            "services.notices.quiet_s": 60,
        },
    )
    assert status == 200
    options = data["config"]["services"]["options"]
    assert options["game"] == {
        "poll_s": 1.5,
        "steam": False,
        "programs": ["emulator.exe = Retro", "othergame.exe"],
    }
    assert load_config().services.options["memory"] == {"report_compare": 5}
    assert data["config"]["services"]["enabled"] == []  # an option does not switch a service on
    for wrong in (
        {"services.game.steam": 1},  # a number is not a switch
        {"services.game.poll_s": True},
        {"services.game.poll_s": "2"},
        {"services.game.poll_s": float("inf")},
        {"services.memory.report_compare": 2.5},
        {"services.game.programs": "a.exe"},
        {"services.game.programs": [["a"]]},
        {"services.game.programs": [{"a": 1}]},
        {"services.game.steam_path": 5},
        {"services.game.nonesuch": 1},
        {"services.game": 1},
        {"services.game.poll_s.x": 1},
    ):
        assert write(server, **wrong)[0] == 400, wrong
    assert write(server, **{"services.nonesuch.x": 1})[0] == 404
    # null takes an option out: the default holds again
    status, data = write(server, **{"services.game.poll_s": None})
    assert status == 200 and "poll_s" not in data["config"]["services"]["options"]["game"]


def test_the_weather_needs_a_place_and_the_place_is_both_coordinates_or_none(server):
    status, data = write(server, **{"weather.enabled": True})
    assert status == 400 and "place" in data["error"]
    assert write(server, **{"weather.latitude": 1.5})[0] == 400  # one of two
    place = {"weather.latitude": 1.55, "weather.longitude": 2.25, "weather.place": "Somewhere"}
    status, data = write(server, **place, **{"weather.enabled": True, "weather.units": "imperial"})
    assert status == 200 and data["config"]["weather"] == {
        "enabled": True,
        "latitude": 1.55,
        "longitude": 2.25,
        "place": "Somewhere",
        "units": "imperial",
        "update_minutes": 15,
    }
    config = load_config().weather
    assert config.enabled and config.latitude == 1.55 and config.place == "Somewhere"
    # the place cannot go while the weather needs it, but both can go together
    before = config_text()
    nothing = {"weather.latitude": None, "weather.longitude": None, "weather.place": None}
    assert write(server, **nothing)[0] == 400 and config_text() == before
    status, data = write(server, **nothing, **{"weather.enabled": False})
    assert status == 200 and data["config"]["weather"]["latitude"] is None
    assert load_config().weather.enabled is False and load_config().weather.place == ""
    assert write(server, **{"weather.update_minutes": 30})[0] == 200


def test_what_the_running_app_has_to_start_again_for_is_told_until_it_has(make):
    app = App()
    port = make(app)
    status, data = write(port, **{"device.brightness": 30})
    assert status == 200 and data["restart"] == [] and app.followed == 1  # brightness is live
    status, data = write(port, **{"sensors.providers": ["load", "psutil"], "refresh_ms": 200})
    assert data["restart"] == ["sensors.providers"] and app.followed == 2
    _, data = request(port, "GET", "/api/settings")
    assert data["restart"] == ["sensors.providers"]  # it is the server that remembers
    place = {"weather.latitude": 1.5, "weather.longitude": 2.5}
    _, data = write(port, **place)
    assert data["restart"] == ["sensors.providers", "weather.latitude", "weather.longitude"]
    app.panel.status = object()  # the panel loop started again: it read config.toml anew
    _, data = request(port, "GET", "/api/settings")
    assert data["restart"] == []
    _, data = write(port, **{"weather.units": "imperial"})
    assert data["restart"] == ["weather.units"]


def test_without_the_background_app_a_change_waits_for_the_next_start(server):
    _, data = write(server, **{"sensors.providers": ["load", "psutil"]})
    assert data["restart"] == ["sensors.providers"]
    _, data = write(server, **{"device.brightness": 10})
    assert data["restart"] == ["sensors.providers"]


# -- the panel -------------------------------------------------------------------------------


def lacking_ffmpeg(monkeypatch, found=False):
    from libre_panel.devices import h264
    from libre_panel.devices.base import DeviceError

    def find(configured=""):
        if not found:
            raise DeviceError("nowhere")
        return "/usr/bin/ffmpeg"

    monkeypatch.setattr(h264, "find_ffmpeg", find)


def panel_on_the_bus(monkeypatch, model_id=None, serial=None):
    from libre_panel.devices import serial_link, turzx_usb
    from libre_panel.devices.models import get_model

    model = get_model(model_id) if model_id else None
    monkeypatch.setattr(turzx_usb, "present_model", lambda: model if not serial else None)
    port = None
    if serial:
        found = get_model(serial)
        port = serial_link.FoundPort("COM7", found, None, (found,))
    monkeypatch.setattr(serial_link, "find_port", lambda model=None, port_name="": port)


def test_no_panel_found_says_so_and_has_nothing_to_tell_about_video(server, monkeypatch):
    panel_on_the_bus(monkeypatch)
    status, data = request(server, "GET", "/api/panel")
    assert status == 200
    assert data["found"] is None and data["model"] is None and data["running"] is None
    assert data["configured"] == "auto"
    assert data["video"] == {"mode": "auto", "fps": 50, "on": False, "usb": True, "gaps": []}


def test_a_panel_on_the_bus_is_named_and_the_video_mode_says_what_it_lacks(server, monkeypatch):
    panel_on_the_bus(monkeypatch, "turing-9.2-usb")
    lacking_ffmpeg(monkeypatch)
    status, data = request(server, "GET", "/api/panel")
    found = data["found"]
    assert found["label"] == 'Turing 9.2"' and found["size"] == [1920, 480]
    assert found["connection"] == "usb" and found["port"] is None and found["video"] is True
    assert data["model"]["id"] == "turing-9.2-usb"
    assert data["video"]["gaps"] == ["clip", "ffmpeg"] and data["video"]["on"] is False
    # the clip's name is only what the person writes; with ffmpeg found it runs
    write(server, **{"video.local_clip": "usr/data/standby.h264"})
    lacking_ffmpeg(monkeypatch, found=True)
    _, data = request(server, "GET", "/api/panel")
    assert data["video"]["gaps"] == [] and data["video"]["on"] is True
    write(server, **{"video.mode": "off"})
    _, data = request(server, "GET", "/api/panel")
    assert data["video"]["gaps"] == [] and data["video"]["on"] is False


def test_on_asks_for_no_proof_but_still_for_the_clip_name_and_ffmpeg(server, monkeypatch):
    panel_on_the_bus(monkeypatch, "turing-8.8-usb")
    lacking_ffmpeg(monkeypatch)
    _, data = request(server, "GET", "/api/panel")
    assert data["video"]["gaps"] == ["model"]  # auto: not proven on this panel
    write(server, **{"video.mode": "on"})
    _, data = request(server, "GET", "/api/panel")
    assert data["video"]["gaps"] == ["clip", "ffmpeg"] and data["video"]["on"] is True


def test_a_serial_panel_has_no_video_mode(server, monkeypatch):
    panel_on_the_bus(monkeypatch, serial="turing-3.5")
    _, data = request(server, "GET", "/api/panel")
    found = data["found"]
    assert found["connection"] == "serial" and found["port"] == "COM7" and found["sure"]
    assert data["video"]["usb"] is False and data["video"]["on"] is False
    assert data["video"]["gaps"] == []


def test_the_panel_the_person_chose_wins_over_the_one_found(server, monkeypatch):
    panel_on_the_bus(monkeypatch, "turing-9.2-usb")
    write(server, **{"device.model": "turing-8.8-usb"})
    _, data = request(server, "GET", "/api/panel")
    assert data["configured"] == "turing-8.8-usb" and data["model"]["id"] == "turing-8.8-usb"
    assert data["found"]["id"] == "turing-9.2-usb"  # what is on the bus is told as it is
    write(server, **{"device.model": "auto"})
    assert load_config().device.model == "auto"


def test_a_looking_that_fails_is_no_panel(server, monkeypatch):
    from libre_panel.devices import serial_link, turzx_usb

    def broken(*args):
        raise RuntimeError("no libusb")

    monkeypatch.setattr(turzx_usb, "present_model", broken)
    monkeypatch.setattr(serial_link, "find_port", broken)
    status, data = request(server, "GET", "/api/panel")
    assert status == 200 and data["found"] is None


def test_the_running_app_tells_how_its_panel_is_doing(make, monkeypatch):
    panel_on_the_bus(monkeypatch)
    panel = SimpleNamespace(state=lambda: {"state": "waiting", "detail": "no panel"}, status=None)
    port = make(App(panel=panel))
    _, data = request(port, "GET", "/api/panel")
    assert data["running"] == {"state": "waiting", "detail": "no panel"}


def test_the_panel_says_whether_its_theme_shows_the_weather(make, isolated_home, monkeypatch):
    """The overview says so while the weather is off: the theme on the panel (else the normal
    one) with its modules taken apart as the panel draws them."""
    from libre_panel.editor import settings

    panel_on_the_bus(monkeypatch)
    isolated_home.mkdir(parents=True, exist_ok=True)
    (isolated_home / "config.toml").write_text('theme = "almanac"\n', encoding="utf-8")
    port = make()
    assert request(port, "GET", "/api/panel")[1]["weather_theme"] is None
    (isolated_home / "config.toml").write_text('theme = "nimbus"\n', encoding="utf-8")
    assert request(port, "GET", "/api/panel")[1]["weather_theme"] == "nimbus"
    # the running app shows another one now (a page of its own): that one is asked too
    (isolated_home / "config.toml").write_text('theme = "almanac"\n', encoding="utf-8")
    panel = SimpleNamespace(state=lambda: {"state": "showing", "detail": "", "theme": "lumen"})
    port = make(App(panel=panel))
    assert request(port, "GET", "/api/panel")[1]["weather_theme"] == "lumen"
    assert "weather.temperature" in settings.theme_reads("libre-default")
    assert settings.theme_reads("no-such-theme") == frozenset()


def test_windows_says_whether_presentmon_is_there(server, monkeypatch):
    """Games' frame rates come from PresentMon, which the Windows downloads carry; the path
    config.toml names is the one asked for."""
    from libre_panel.sensors import presentmon

    monkeypatch.setattr("libre_panel.linux_setup.supported", lambda: False)
    monkeypatch.setattr(
        "libre_panel.sensorservice.setup.unlock_state",
        lambda platform=None: {"platform": "win32", "available": False, "service": None},
    )
    asked = []
    monkeypatch.setattr(presentmon, "find_presentmon", lambda path="": asked.append(path))
    assert request(server, "GET", "/api/unlock")[1]["presentmon"] is False
    monkeypatch.setattr(presentmon, "find_presentmon", lambda path="": "C:/x/PresentMon.exe")
    assert request(server, "GET", "/api/unlock")[1]["presentmon"] is True
    assert asked == [""]  # none named here


# -- the question about the standby clip -----------------------------------------------------


def asks(server):
    status, data = request(server, "GET", "/api/clip")
    assert status == 200
    return data["ask"]


def test_the_standby_clip_is_asked_for_where_only_its_name_keeps_the_video_mode_off(
    server, monkeypatch
):
    panel_on_the_bus(monkeypatch, "turing-9.2-usb")
    lacking_ffmpeg(monkeypatch, found=True)
    status, data = request(server, "GET", "/api/clip")
    # the name is the one the vendor app uses: offered, and never written by asking
    assert status == 200 and data == {"ask": True, "name": "usr/data/standby.h264"}
    assert not (config_dir() / "config.toml").exists()
    _, panel = request(server, "GET", "/api/panel")
    assert panel["video"]["gaps"] == ["clip"]  # that is all that is missing, as Settings says


def test_the_standby_clip_is_not_asked_for_unless_all_the_rest_is_there(server, monkeypatch):
    panel_on_the_bus(monkeypatch)
    lacking_ffmpeg(monkeypatch, found=True)
    assert asks(server) is False  # no panel
    panel_on_the_bus(monkeypatch, "turing-8.8-usb")
    assert asks(server) is False  # the video mode is not proven on it
    panel_on_the_bus(monkeypatch, serial="turing-3.5")
    assert asks(server) is False  # a serial panel gets still pictures
    panel_on_the_bus(monkeypatch, "turing-9.2-usb")
    lacking_ffmpeg(monkeypatch)
    assert asks(server) is False  # nothing to encode with: ffmpeg first
    lacking_ffmpeg(monkeypatch, found=True)
    assert asks(server) is True
    for mode in ("on", "off"):  # those decide by themselves, without a question
        write(server, **{"video.mode": mode})
        assert asks(server) is False, mode
    write(server, **{"video.mode": "auto"})
    assert asks(server) is True
    write(server, **{"device.driver": "virtual"})
    assert asks(server) is False  # never the panel
    write(server, **{"device.driver": "auto"})
    assert asks(server) is True


def test_the_standby_clip_is_asked_for_once(server, monkeypatch):
    panel_on_the_bus(monkeypatch, "turing-9.2-usb")
    lacking_ffmpeg(monkeypatch, found=True)
    assert asks(server) is True
    # a name that is there answers it, and so does "not now" (a flag of its own)
    write(server, **{"video.local_clip": "usr/data/standby.h264"})
    assert asks(server) is False
    write(server, **{"video.local_clip": ""})
    assert asks(server) is True
    assert write(server, **{"video.clip_asked": True})[0] == 200
    assert load_config().device.video.clip_asked is True
    assert load_config().device.video.local_clip == ""  # nothing was made up for it
    assert asks(server) is False
    write(server, **{"video.clip_asked": False})  # false asks again
    assert asks(server) is True


def test_a_panel_that_is_named_in_config_toml_is_asked_about_before_it_is_connected(
    server, monkeypatch
):
    panel_on_the_bus(monkeypatch)
    lacking_ffmpeg(monkeypatch, found=True)
    write(server, **{"device.model": "turing-9.2-usb"})
    assert asks(server) is True
    write(server, **{"device.model": "turing-8.8-usb"})
    panel_on_the_bus(monkeypatch, "turing-9.2-usb")
    assert asks(server) is False  # the panel that is named is the one that counts


def test_looking_for_the_panel_that_fails_asks_nothing(server, monkeypatch):
    from libre_panel.devices import turzx_usb

    def broken():
        raise RuntimeError("no libusb")

    monkeypatch.setattr(turzx_usb, "present_model", broken)
    lacking_ffmpeg(monkeypatch, found=True)
    assert asks(server) is False
    config_dir().mkdir(parents=True)
    (config_dir() / "config.toml").write_text("this is not toml", encoding="utf-8")
    status, data = request(server, "GET", "/api/clip")
    assert status == 409 and "error" in data  # the app says so elsewhere


# -- the system ------------------------------------------------------------------------------


def test_starting_with_the_system_on_a_desktop(make, isolated_home):
    app = App()
    port = make(app)
    status, data = request(port, "GET", "/api/system")
    assert status == 200 and data["kind"] == "xdg" and data["enabled"] is False
    assert data["elevated"] is False and data["can_elevate"] is False
    assert data["location"].endswith("libre-panel.desktop")
    assert data["language"]["setting"] == "auto" and "de" in data["language"]["languages"]
    status, data = request(port, "POST", "/api/system", {"autostart": True})
    assert status == 200 and data["enabled"] is True
    entry = app.autostart.path()
    assert entry.is_file() and "/opt/libre-panel/libre-panel" in entry.read_text(encoding="utf-8")
    status, data = request(port, "POST", "/api/system", {"autostart": False})
    assert data["enabled"] is False and not entry.exists()


def test_the_highest_rights_are_set_up_only_when_asked_and_only_by_an_administrator(
    make, monkeypatch, tmp_path
):
    # (a home of its own: on Linux the Windows path below is relative, so it resolves under
    # the working folder, which on CI lies in the home folder and would read as risky)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    registry, tasks = FakeRegistry(), FakeTasks()
    program = r"C:\Tools\libre-panel.exe"
    windows = Autostart(
        "win32", registry=registry, tasks=tasks, admin=lambda: False, program=program
    )
    app = App(autostart=windows, autostart_command=[program, "tray"])
    port = make(app)
    _, data = request(port, "GET", "/api/system")
    assert data["kind"] == "windows" and data["enabled"] is False
    assert data["elevated"] is False and data["can_elevate"] is False
    assert data["command"].endswith("libre-panel autostart enable --elevated")
    assert request(port, "POST", "/api/system", {"autostart": True})[1]["elevated"] is False
    assert registry.get("Libre Panel") is not None and not tasks.tasks  # the plain start
    status, data = request(port, "POST", "/api/system", {"elevated": True})
    assert status == 409 and "administrator" in data["error"] and not tasks.tasks

    windows._admin = lambda: True  # the same app, started as an administrator
    _, data = request(port, "GET", "/api/system")
    assert data["can_elevate"] is True and data["risky"] is False  # not under the user's folder
    status, data = request(port, "POST", "/api/system", {"elevated": True})
    assert status == 200 and data["elevated"] is True and data["enabled"] is True
    assert "Libre Panel" in tasks.tasks and registry.get("Libre Panel") is None
    # a plain start is refused while the other holds, as it is on the command line
    assert request(port, "POST", "/api/system", {"autostart": True})[0] == 409
    status, data = request(port, "POST", "/api/system", {"elevated": False})
    assert status == 200 and data["elevated"] is False and data["enabled"] is True
    assert not tasks.tasks and registry.get("Libre Panel") is not None


def test_the_highest_rights_cannot_be_taken_away_without_an_administrator(make):
    registry, tasks = FakeRegistry(), FakeTasks(allowed=False)
    windows = Autostart(
        "win32", registry=registry, tasks=tasks, admin=lambda: True, program="x.exe"
    )
    windows.enable(["x.exe", "tray"], elevated=True)
    port = make(App(autostart=windows, autostart_command=["x.exe", "tray"]))
    status, data = request(port, "POST", "/api/system", {"elevated": False})
    assert status == 409 and "administrator" in data["error"]
    assert request(port, "GET", "/api/system")[1]["elevated"] is True


def test_a_system_without_autostart_says_so(make):
    port = make(App(autostart=Autostart("darwin")))
    _, data = request(port, "GET", "/api/system")
    assert data["kind"] == "unsupported" and data["enabled"] is None and data["location"] == ""
    status, data = request(port, "POST", "/api/system", {"autostart": True})
    assert status == 409 and "not supported" in data["error"]


def test_the_system_route_takes_only_what_it_knows(make):
    port = make(App())
    for body in ({}, {"autostart": "yes"}, {"elevated": 1}, {"autostart": True, "x": 1}, []):
        assert request(port, "POST", "/api/system", body)[0] == 400, body


# -- the place -------------------------------------------------------------------------------


def places(monkeypatch, found=None, error=None):
    from libre_panel.weather import open_meteo

    asked = []

    def geocode(name, count=5, language="en"):
        asked.append((name, count, language))
        if error:
            raise open_meteo.WeatherError(error)
        return found

    monkeypatch.setattr(open_meteo, "geocode", geocode)
    return asked


def test_a_place_is_looked_up_by_name_and_kept_to_about_a_kilometre(server, monkeypatch):
    asked = places(
        monkeypatch,
        [
            {"name": "Springfield", "region": "Region", "country": "Country",
             "latitude": 1.234567, "longitude": -2.876543},
            {"name": "Springfield", "region": "", "country": "Other",
             "latitude": None, "longitude": 1},
            {"name": "Lonely", "region": "", "country": "",
             "latitude": 10, "longitude": 20},
        ],
    )  # fmt: skip
    status, data = request(server, "POST", "/api/location", {"query": " Springfield "})
    assert status == 200
    assert data["places"] == [
        {"name": "Springfield, Region, Country", "latitude": 1.23, "longitude": -2.88},
        {"name": "Lonely", "latitude": 10, "longitude": 20},  # the one without a latitude is out
    ]
    assert asked == [("Springfield", 6, "en")]  # the typed name goes, and the language
    assert request(server, "GET", "/api/location?query=x")[0] == 404  # only when asked to


def test_the_place_search_asks_in_the_language_of_the_app(server, monkeypatch):
    from libre_panel import i18n

    asked = places(monkeypatch, [])
    i18n.set_language("de")
    request(server, "POST", "/api/location", {"query": "Köln"})
    assert asked == [("Köln", 6, "de")]


def test_a_place_search_that_fails_or_is_asked_wrongly(server, monkeypatch):
    places(monkeypatch, error="timed out")
    status, data = request(server, "POST", "/api/location", {"query": "Nowhere"})
    assert status == 502 and "timed out" in data["error"]
    asked = places(monkeypatch, [])
    for body in ({}, {"query": ""}, {"query": "x"}, {"query": 5}, {"query": "y" * 101}, []):
        assert request(server, "POST", "/api/location", body)[0] == 400, body
    assert asked == []  # nothing was sent anywhere


# -- about -----------------------------------------------------------------------------------


def test_about_gives_the_version_the_licence_the_links_and_the_folders(server, isolated_home):
    from libre_panel import __version__

    status, data = request(server, "GET", "/api/about")
    assert status == 200 and data["version"] == __version__
    assert data["license"]["id"] == "GPL-3.0-or-later"
    assert data["links"]["project"] == "https://github.com/syntensa/libre-panel"
    assert data["links"]["hardware_report"].endswith("template=panel_support.yml")
    assert data["doctor"].endswith("libre-panel doctor") or data["doctor"].endswith("doctor")
    assert data["devices"].endswith("devices")  # the report form asks for both
    assert data["folders"]["config"] == str(isolated_home)
    assert data["folders"]["themes"] == str(isolated_home / "themes")
    assert data["folders"]["logs"] == str(isolated_home / "logs")
    assert data["notice"].startswith("# Notices and credits")


def plugged_in(monkeypatch, ports=(), usb=(), fail=""):
    """What the system lists as plugged in: serial ports (as list_serial_ports gives them) and
    USB panels; ``fail`` is the reason the USB library is not there."""
    from libre_panel.devices import base
    from libre_panel.devices.base import DeviceError

    def usb_devices():
        if fail:
            raise DeviceError(fail)
        return list(usb)

    monkeypatch.setattr(base, "list_serial_ports", lambda: list(ports))
    monkeypatch.setattr(base, "list_usb_devices", usb_devices)


def test_the_report_lists_what_is_connected_without_serial_numbers(server, monkeypatch):
    """What a report of a panel says of this computer, for the form: the version, the system,
    the panel found and what could be one by its USB ids; never a serial number, and a port
    without USB ids (a mainboard's, Bluetooth) is left out."""
    from urllib.parse import parse_qs, urlsplit

    from libre_panel import __version__

    panel_on_the_bus(monkeypatch, "turing-9.2-usb")
    ports = [
        {"device": "COM5", "description": "USB Serial Device (COM5)", "vid": "1a86",
         "pid": "5722", "serial_number": "SER1AL0000", "manufacturer": "wch.cn"},
        {"device": "COM1", "description": "Communications Port (COM1)", "vid": None,
         "pid": None, "serial_number": None, "manufacturer": None},
    ]  # fmt: skip
    plugged_in(monkeypatch, ports, [{"vid": "1cbe", "pid": "0092"}])
    status, data = request(server, "GET", "/api/report")
    assert status == 200
    lines = data["text"].splitlines()
    assert lines[0].startswith(f"Libre Panel {__version__}, ")
    assert lines[1] == 'Panel found: Turing 9.2"'
    assert lines[2].startswith("COM5  1a86:5722  USB Serial Device (COM5)  -> turing-3.5 / ")
    assert lines[3] == "usb  1cbe:0092  -> turing-9.2-usb"
    assert len(lines) == 4 and "SER1AL0000" not in data["text"] and "COM1" not in data["text"]
    assert data["product"] == 'Turing 9.2"'
    form = urlsplit(data["form"])
    assert data["form"].startswith(
        "https://github.com/syntensa/libre-panel/issues/new?template=panel_support.yml&"
    )
    fields = parse_qs(form.query)
    assert fields["product"] == ['Turing 9.2"'] and fields["devices"] == [data["text"]]
    # nothing found, and no USB library: said so, and the form has no product to fill in
    panel_on_the_bus(monkeypatch)
    plugged_in(monkeypatch, fail="pyusb is not installed")
    data = request(server, "GET", "/api/report")[1]
    assert data["text"].splitlines()[1:] == ["Panel found: none", "usb: pyusb is not installed"]
    assert data["product"] == "" and "product" not in parse_qs(urlsplit(data["form"]).query)
    plugged_in(monkeypatch)
    assert request(server, "GET", "/api/report")[1]["text"].splitlines()[2:] == [
        "Nothing plugged in looks like a panel."
    ]


def test_the_settings_routes_in_the_docs_are_the_ones_the_server_answers():
    """docs/ARCHITECTURE.md lists the routes the Settings use; this keeps the list true."""
    import re
    from pathlib import Path

    from libre_panel.editor.server import _SETTINGS_GET, _SETTINGS_POST

    docs = Path(__file__).resolve().parent.parent / "docs" / "ARCHITECTURE.md"
    rows = re.findall(
        r"^\| `(/api/[a-z]+)` \| ([A-Z, ]+) \|", docs.read_text("utf-8"), re.MULTILINE
    )
    documented = {(method, path) for path, methods in rows for method in methods.split(", ")}
    built = {("GET", path) for path in _SETTINGS_GET} | {("POST", path) for path in _SETTINGS_POST}
    assert documented == built and len(rows) == len({path for path, _ in rows})


def test_the_notice_in_the_package_is_the_notice_of_the_project():
    """The app shows NOTICE.md from the package (a pip install and the downloads have no copy
    of the project's file); this makes sure it is the same text."""
    from pathlib import Path

    from libre_panel.editor import settings

    root = Path(__file__).resolve().parent.parent / "NOTICE.md"
    assert settings.notice_text() == root.read_text(encoding="utf-8")
    packaged = Path(settings.__file__).resolve().parents[1] / "assets" / "NOTICE.md"
    assert packaged.read_text(encoding="utf-8") == root.read_text(encoding="utf-8")


def test_the_command_line_program_is_named_as_one_types_it(tmp_path, monkeypatch):
    from libre_panel.editor import settings

    assert settings.cli_command("doctor") == "libre-panel doctor"
    # the Windows download: its program is not on the PATH, so its own is named
    (tmp_path / "LibrePanel.exe").write_text("")
    monkeypatch.setattr(
        settings,
        "sys",
        SimpleNamespace(platform="win32", frozen=True, executable=str(tmp_path / "LibrePanel.exe")),
    )
    assert settings.cli_command("doctor") == "libre-panel doctor"  # no libre-panel.exe beside it
    (tmp_path / "libre-panel.exe").write_text("")
    assert settings.cli_command("doctor") == f'"{tmp_path / "libre-panel.exe"}" doctor'
    # the Linux AppImage is the program itself
    monkeypatch.setattr(
        settings, "sys", SimpleNamespace(platform="linux", frozen=True, executable="/tmp/mount/app")
    )
    monkeypatch.setenv("APPIMAGE", "/home/me/Libre_Panel.AppImage")
    assert settings.cli_command("doctor") == '"/home/me/Libre_Panel.AppImage" doctor'


def test_a_folder_is_opened_in_the_file_manager(server, isolated_home, monkeypatch):
    from libre_panel import tray

    opened = []
    monkeypatch.setattr(tray, "open_path", opened.append)
    for name, folder in (("config", ""), ("themes", "themes"), ("logs", "logs")):
        status, data = request(server, "POST", "/api/open", {"folder": name})
        assert status == 200 and data == {"opened": name}
        assert opened[-1] == (isolated_home / folder if folder else isolated_home)
        assert opened[-1].is_dir()  # made first: a new installation has neither
    for body in ({"folder": "/etc"}, {"folder": "../x"}, {"folder": 1}, {}, []):
        assert request(server, "POST", "/api/open", body)[0] == 400, body
    assert len(opened) == 3

    def refuse(path):
        raise OSError("no xdg-open")

    monkeypatch.setattr(tray, "open_path", refuse)
    status, data = request(server, "POST", "/api/open", {"folder": "config"})
    assert status == 501 and "xdg-open" in data["error"]


# -- the protections of the editor hold for them too ---------------------------------------------


def test_the_settings_routes_are_for_this_computer_only(server):
    for method, path in (
        ("GET", "/api/settings"),
        ("GET", "/api/panel"),
        ("GET", "/api/system"),
        ("GET", "/api/about"),
        ("GET", "/api/report"),
    ):
        assert request(server, method, path, host="evil.example")[0] == 403, path
    for path in ("/api/settings", "/api/system", "/api/location", "/api/open"):
        status, _ = request(server, "POST", path, {"query": "x"}, headers={"X-Libre-Panel": ""})
        assert status == 403, path  # a page on the web cannot write
        assert request(server, "POST", path, {}, host="evil.example")[0] == 403, path


# -- with the highest rights, what names a program or a file is set in config.toml ---------------


@pytest.fixture
def elevated(monkeypatch):
    """Libre Panel runs as administrator (as root on Linux)."""
    monkeypatch.setattr(access, "elevated", lambda: True)


# what a request must not make an elevated Libre Panel start, load or write
NAMING = {
    "video.ffmpeg": "/tmp/ffmpeg",
    "device.output": "/etc/frame.png",
    "services.cooling.database": "/tmp/cooling.db",
    "services.cooling.fancontrol": "/tmp/fancontrol.json",
    "services.memory.database": "/tmp/memory.db",
    "services.memory.fancontrol": "/tmp/fancontrol.json",
    "services.downloads.folder": "/tmp/downloads",
    "services.downloads.steam_path": "/tmp/steam",
    "services.game.steam_path": "/tmp/steam",
}


def test_an_elevated_libre_panel_does_not_start_what_a_request_names(server, elevated):
    write(server, **{"device.brightness": 40})  # (the file exists)
    for name, value in NAMING.items():
        before = config_text()
        status, data = write(server, **{name: value})
        assert status == 403, name
        assert name in data["error"] and "config.toml" in data["error"], data
        assert "administrator rights" in data["error"]
        assert config_text() == before, name  # nothing was written
    # a request that has one of them among others is refused as a whole
    before = config_text()
    status, _ = write(server, **{"device.brightness": 10, "video.ffmpeg": "/tmp/ffmpeg"})
    assert status == 403 and config_text() == before
    # and one that takes such an option out, where it is not set, changes nothing
    status, data = write(server, **{"services.memory.database": None})
    assert status == 200


def test_an_elevated_libre_panel_leaves_alone_what_is_there_already(server, monkeypatch):
    """The file says it: asked for the same, nothing changes and nothing is refused."""
    same = {"video.ffmpeg": "/usr/bin/ffmpeg", "services.cooling.database": "/a.db"}
    assert write(server, **same)[0] == 200
    monkeypatch.setattr(access, "elevated", lambda: True)
    status, data = write(server, **same)
    assert status == 200 and data["config"]["video"]["ffmpeg"] == "/usr/bin/ffmpeg"
    assert write(server, **{"device.output": "libre-panel-frame.png"})[0] == 200  # the default
    for changes in (
        {"video.ffmpeg": ""},
        {"services.cooling.database": "/b.db"},
        {"services.cooling.database": None},
    ):
        assert write(server, **changes)[0] == 403, changes
    assert load_config().device.video.ffmpeg == "/usr/bin/ffmpeg"
    assert load_config().services.options["cooling"]["database"] == "/a.db"


def test_an_elevated_libre_panel_changes_everything_else(server, elevated):
    status, data = write(
        server,
        **{
            "device.brightness": 25,
            "video.local_clip": "clip",  # (a name for the panel, not a file here)
            "sensors.librehardwaremonitor.url": "http://127.0.0.1:8085/data.json",
            "services.autopilot.transition": "fade",
            "services.notices.quiet_s": 30,
            "services.game.poll_s": 2,
        },
    )
    assert status == 200 and data["config"]["device"]["brightness"] == 25


def test_a_libre_panel_without_the_highest_rights_changes_them_all(server):
    for name, value in NAMING.items():
        assert write(server, **{name: value})[0] == 200, name


def test_the_settings_that_name_a_program_or_a_file_are_the_ones_locked():
    """Every setting is told apart here by what it names, so that a new one is a new decision."""
    from libre_panel.editor.settings import LOCKED_WHILE_ELEVATED, RULES, names_a_path
    from libre_panel.plugins.loader import registry

    names = {
        "device.model", "device.driver", "device.brightness", "device.rotate", "device.port",
        "device.orientation", "device.motion",
        "video.mode", "video.fps", "video.local_clip", "video.clip_asked", "refresh_ms",
        "check_updates", "allow_notify", "symbols", "hotkeys.enabled", "hotkeys.pause",
        "hotkeys.brightness_up", "hotkeys.brightness_down", "hotkeys.open",
        "sensors.providers",
        "sensors.librehardwaremonitor.mode", "sensors.librehardwaremonitor.url",
        "weather.enabled", "weather.units", "weather.update_minutes", "weather.latitude",
        "weather.longitude", "weather.place",
    }  # fmt: skip
    assert set(RULES) == names | {"video.ffmpeg", "device.output"}, "tell what the new one names"
    assert {name for name in RULES if names_a_path(name)} == {"video.ffmpeg", "device.output"}
    # what is no rule but a name in config.toml that the sources read
    assert set(LOCKED_WHILE_ELEVATED) >= {
        "sensors.presentmon.path",
        "sensors.librehardwaremonitor.dll_dir",
    }
    assert all(names_a_path(name) for name in LOCKED_WHILE_ELEVATED)
    assert not names_a_path("sensors.mqtt.host") and not names_a_path("services.enabled")
    assert names_a_path("sensors.something.exe_path") and names_a_path("services.x.Folder")
    # the options of the services that are files or folders, by their names
    found = {
        f"services.{name}.{option}"
        for name in registry().names("services")
        for option, (kind, _default) in registry().get("services", name).options.items()
        if kind == "string" and names_a_path(f"services.{name}.{option}")
    }
    assert found == {key for key in NAMING if key.startswith("services.")}


def test_the_message_says_where_to_set_it_in_german_too(server, elevated):
    from libre_panel import i18n

    write(server, **{"device.brightness": 40})
    i18n.set_language("de")
    status, data = write(server, **{"video.ffmpeg": "/tmp/ffmpeg"})
    assert status == 403
    assert "video.ffmpeg" in data["error"] and "config.toml" in data["error"]
    assert "Administratorrechten" in data["error"]
