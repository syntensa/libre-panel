"""Which sensor sources a config starts with, and what `sensors` and `doctor` say when
the readings that need LibreHardwareMonitor do not arrive on Windows."""

import gc
import json
import sys
import time
import tomllib
from pathlib import Path

import pytest
from fake_sensors import FakeSource, Scripted, wait_for

from libre_panel import cli, config, doctor, linux_setup
from libre_panel.app import build_hub
from libre_panel.config import (
    Config,
    default_config_toml,
    default_providers,
    load_config,
    parse_config,
    write_default_config,
)
from libre_panel.sensors import audio, base, load
from libre_panel.sensors.base import Reading, SensorHub, SensorProvider

LHM_TREE = json.loads((Path(__file__).parent / "data" / "lhm_data.json").read_text("utf-8"))
URL = "http://127.0.0.1:8085/data.json"
WINDOWS = ["load", "audio", "librehardwaremonitor", "presentmon", "psutil"]
LINUX = ["load", "audio", "linux", "psutil"]
ELSEWHERE = ["load", "psutil"]  # no way to read the volume there


# -- the list a config starts with ----------------------------------------------------


def test_the_fast_sources_come_before_psutil_and_on_windows_so_does_the_hardware_one():
    assert default_providers("win32") == WINDOWS
    assert default_providers("linux") == LINUX
    assert default_providers() == default_providers(sys.platform)  # this machine's


@pytest.mark.parametrize("platform", ["darwin", "freebsd14", "cygwin"])
def test_the_volume_is_left_out_where_nothing_reads_it(platform):
    assert default_providers(platform) == ELSEWHERE


def test_the_volume_is_in_the_default_list_where_it_can_be_read_and_costs_nothing_unshown(
    monkeypatch,
):
    """Windows reads it through Core Audio and Linux through wpctl or pactl; either way
    the thread starts only while a theme or a service shows ``audio.*``."""
    gc.collect()
    monkeypatch.setattr(base, "_WANTED", {})
    for platform in ("win32", "linux"):
        assert "audio" in default_providers(platform)
    provider = audio.AudioProvider()
    assert provider.read() == {} and provider._thread is None  # nobody shows it: no thread
    provider.close()


@pytest.mark.parametrize(
    ("platform", "expected"), [("win32", WINDOWS), ("linux", LINUX), ("darwin", ELSEWHERE)]
)
def test_a_new_config_file_names_the_list_of_its_platform(platform, expected):
    text = default_config_toml(platform)
    assert "@PROVIDERS@" not in text
    assert f"\nproviders = {json.dumps(expected)}\n" in text
    data = tomllib.loads(text)
    assert data["sensors"]["providers"] == expected
    assert parse_config(data).sensors.providers == expected
    assert config.DEFAULT_CONFIG_TOML == default_config_toml()  # for the machine running this


def test_the_generated_config_tells_what_librehardwaremonitor_needs():
    text = default_config_toml("win32")
    assert "Run it as administrator and turn on Options -> Remote Web Server -> Run." in text
    assert "# stale_seconds = 5" in text and "ten times a second" in text


def test_the_generated_config_tells_what_the_volume_source_is():
    text = default_config_toml("linux")
    words = " ".join(line.removeprefix("# ") for line in text.splitlines())
    assert "audio (the volume and mute of the default output: Windows, Linux with wpctl" in words
    assert "load and audio work only while a theme or a service shows their readings" in words
    assert f"\nproviders = {json.dumps(LINUX)}\n" in text


def test_the_written_config_file_has_the_list(isolated_home):
    path = write_default_config()
    assert load_config(path).sensors.providers == default_providers()
    assert f"providers = {json.dumps(default_providers())}" in path.read_text("utf-8")


def test_a_config_that_names_no_list_gets_the_default(isolated_home, monkeypatch):
    assert Config().sensors.providers == default_providers()  # no config file at all
    assert load_config().sensors.providers == default_providers()
    assert parse_config({}).sensors.providers == default_providers()
    path = isolated_home / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('[sensors.librehardwaremonitor]\nurl = "http://nas:8085/data.json"\n')
    loaded = load_config(path)  # a [sensors] table without the list
    assert loaded.sensors.providers == default_providers()
    assert loaded.sensors.options["librehardwaremonitor"]["url"] == "http://nas:8085/data.json"
    # and on Windows: the default list is looked up when the config is made
    original = config.default_providers
    monkeypatch.setattr(config, "default_providers", lambda: original("win32"))
    assert Config().sensors.providers == WINDOWS
    assert parse_config({"theme": "slate"}).sensors.providers == WINDOWS
    assert load_config().sensors.providers == WINDOWS


@pytest.mark.parametrize(
    "providers",
    [["librehardwaremonitor", "psutil"], ["demo"], ["psutil", "load"], ["psutil", "demo"], []],
)
def test_a_list_in_the_users_config_is_left_alone(isolated_home, providers):
    assert parse_config({"sensors": {"providers": providers}}).sensors.providers == providers
    path = isolated_home / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(f"[sensors]\nproviders = {json.dumps(providers)}\n")
    assert load_config(path).sensors.providers == providers


@pytest.mark.parametrize(("platform", "expected"), [("win32", WINDOWS), ("linux", LINUX)])
def test_psutil_alone_as_0_4_wrote_it_means_todays_default(monkeypatch, platform, expected):
    """Every config.toml up to 0.4 named psutil alone: the default then, not a choice. It
    gets today's list, the volume included."""
    monkeypatch.setattr("libre_panel.config.sys.platform", platform)
    old = default_config_toml().replace(json.dumps(default_providers()), '["psutil"]')
    assert parse_config(tomllib.loads(old)).sensors.providers == expected


def test_the_list_0_5_wrote_before_linux_had_a_source_means_todays_default_there_only(
    monkeypatch,
):
    """A config.toml of a 0.5 build before the Linux source named ``load``, ``audio`` and
    ``psutil``: the default then, as 0.4's ``psutil`` was. Elsewhere that list is a choice."""
    earlier = {"sensors": {"providers": ["load", "audio", "psutil"]}}
    monkeypatch.setattr("libre_panel.config.sys.platform", "linux")
    assert parse_config(earlier).sensors.providers == LINUX
    monkeypatch.setattr("libre_panel.config.sys.platform", "win32")
    assert parse_config(earlier).sensors.providers == ["load", "audio", "psutil"]
    monkeypatch.setattr("libre_panel.config.sys.platform", "linux")
    other = {"sensors": {"providers": ["audio", "load", "psutil"]}}
    assert parse_config(other).sensors.providers == ["audio", "load", "psutil"]


def test_psutil_leaves_the_kernels_chips_to_the_linux_source_when_that_runs(monkeypatch):
    def hub_of(providers):
        config = Config()
        config.sensors.providers = providers
        return build_hub(config)

    hub = hub_of(["linux", "psutil"])
    try:
        assert next(p for p in hub.providers if p.name == "psutil").hwmon is False
    finally:
        hub.close()
    hub = hub_of(["psutil"])
    try:
        assert next(p for p in hub.providers if p.name == "psutil").hwmon is True
    finally:
        hub.close()

    real = base.create_provider

    def without_linux(name, options=None):
        if name == "linux":
            raise RuntimeError("it did not start")
        return real(name, options)

    monkeypatch.setattr("libre_panel.app.create_provider", without_linux)
    hub = hub_of(["linux", "psutil"])  # a source that did not start reads nothing: psutil does
    try:
        assert [p.name for p in hub.providers][:1] == ["psutil"]
        assert hub.providers[0].hwmon is True
    finally:
        hub.close()


def test_the_hub_of_a_default_config_starts_with_the_default_sources():
    hub = build_hub(Config())
    try:
        names = [provider.name for provider in hub.providers]
        assert names[: len(default_providers())] == default_providers()
        assert hub.every_frame  # the fast sources are read on every frame
    finally:
        hub.close()


# -- `libre-panel sensors` ------------------------------------------------------------


class Fixed(SensorProvider):
    name = "fixed"

    def __init__(self, values):
        super().__init__()
        self.values = values

    def read(self):
        return {key: Reading(key, value, "", key) for key, value in self.values.items()}


@pytest.fixture
def quick_sensors(monkeypatch):
    """The `sensors` command without its second of waiting, and with no real sound."""
    gc.collect()
    monkeypatch.setattr(base, "_WANTED", {})
    monkeypatch.setattr(cli, "SENSORS_SETTLE_SECONDS", 0)
    monkeypatch.setattr(load, "SAMPLE_SECONDS", 0.005)


def write_providers(home, providers):
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.toml"
    path.write_text(f"[sensors]\nproviders = {json.dumps(providers)}\n", encoding="utf-8")
    return path


def test_sensors_lists_the_load_and_the_volume_too(
    isolated_home, quick_sensors, monkeypatch, capsys
):
    """They read only while a theme shows them: the command asks for them like a theme."""
    cpu, volume = Scripted(42.0), FakeSource((37.0, False))
    monkeypatch.setattr(load, "_cpu_sampler", lambda: cpu)
    monkeypatch.setattr(load, "_gpu_sampler", lambda: None)
    monkeypatch.setattr(audio, "_open_source", lambda on_change: volume)

    def settle(seconds):  # the second the command waits, as long as the sources need
        assert wait_for(lambda: cpu.reads >= 2 and "audio.volume" in audio_provider.read())

    path = write_providers(isolated_home, ["load", "audio"])
    audio_provider = None

    real_build_hub = build_hub

    def build(config, demo=False):
        nonlocal audio_provider
        hub = real_build_hub(config, demo)
        audio_provider = next(p for p in hub.providers if p.name == "audio")
        return hub

    monkeypatch.setattr("libre_panel.app.build_hub", build)
    monkeypatch.setattr(time, "sleep", settle)
    before = set(base._WANTED)
    assert cli.main(["--config", str(path), "sensors"]) == 0
    lines = {line.split()[0]: line for line in capsys.readouterr().out.splitlines()}
    cpu_line = lines["cpu.load"]
    assert "42.00" in cpu_line and "%" in cpu_line and "CPU load" in cpu_line
    assert "37.00" in lines["audio.volume"] and "Volume" in lines["audio.volume"]
    assert lines["audio.muted"].split()[1] == "0"
    assert set(base._WANTED) == before  # nothing stays asked for


def test_sensors_says_plainly_on_windows_when_the_hardware_readings_do_not_arrive(
    isolated_home, quick_sensors, monkeypatch, capsys
):
    path = write_providers(isolated_home, ["fixed"])
    monkeypatch.setattr("libre_panel.app.build_hub", lambda config, demo=False: SensorHub(
        [Fixed({"cpu.load": 5.0, "net.down": 1.0})]))  # fmt: skip
    monkeypatch.setattr(cli, "_platform", lambda: "win32")
    assert cli.main(["--config", str(path), "sensors"]) == 0
    captured = capsys.readouterr()
    assert "cpu.load" in captured.out and "note:" not in captured.out
    note = captured.err
    assert "note: No CPU or GPU temperature or power readings." in note
    assert "run it as administrator" in note
    assert "Options -> Remote Web Server -> Run" in note and URL in note
    assert 'Add "librehardwaremonitor" to providers' in note  # it is not in this list


def test_the_note_knows_the_address_in_the_config_and_a_listed_source(
    isolated_home, quick_sensors, monkeypatch, capsys
):
    path = write_providers(isolated_home, ["librehardwaremonitor"])
    path.write_text(
        path.read_text() + '[sensors.librehardwaremonitor]\nurl = "http://nas:8085/data.json"\n'
    )
    monkeypatch.setattr(
        "libre_panel.app.build_hub", lambda config, demo=False: SensorHub([Fixed({})])
    )
    monkeypatch.setattr(cli, "_platform", lambda: "win32")
    assert cli.main(["--config", str(path), "sensors"]) == 0
    note = capsys.readouterr().err
    assert "http://nas:8085/data.json" in note and "Add " not in note  # it is listed


def test_sensors_says_on_linux_what_the_setup_brings_and_how(
    isolated_home, quick_sensors, monkeypatch, capsys
):
    path = write_providers(isolated_home, ["fixed"])
    monkeypatch.setattr(
        "libre_panel.app.build_hub",
        lambda config, demo=False: SensorHub([Fixed({"cpu.load": 5.0})]),
    )
    monkeypatch.setattr(cli, "_platform", lambda: "linux")
    needs = linux_setup.Facts(
        panel=True, panel_denied=True, files=dict.fromkeys(linux_setup.OWN_FILES)
    )
    monkeypatch.setattr(linux_setup, "gather", lambda *args, **kwargs: needs)
    assert cli.main(["--config", str(path), "sensors"]) == 0
    captured = capsys.readouterr()
    assert "cpu.load" in captured.out and "note:" not in captured.out
    assert "note: Your user may not use the USB panel yet. This mends it: sudo " in captured.err
    assert "system-setup --linux" in captured.err


@pytest.mark.parametrize(
    ("platform", "values", "demo"),
    [
        ("linux", {"cpu.load": 5.0}, False),  # psutil has them where the system does
        ("win32", {"cpu.load": 5.0, "gpu.temp": 51.0}, False),  # they arrive
        ("win32", {"cpu.load": 5.0, "cpu.power": 40.0}, False),
        ("win32", {"cpu.load": 5.0}, True),  # the demo has made-up values
    ],
)
def test_the_note_is_for_windows_without_the_readings_only(
    isolated_home, quick_sensors, monkeypatch, capsys, platform, values, demo
):
    path = write_providers(isolated_home, ["fixed"])
    monkeypatch.setattr(
        "libre_panel.app.build_hub", lambda config, demo=False: SensorHub([Fixed(values)])
    )
    monkeypatch.setattr(cli, "_platform", lambda: platform)
    assert cli.main(["--config", str(path), "sensors", *(["--demo"] if demo else [])]) == 0
    assert capsys.readouterr().err == ""


# -- `libre-panel doctor` -------------------------------------------------------------


def fetch_of(answer):
    def fetch(url):
        if isinstance(answer, Exception):
            raise answer
        return answer

    return fetch


def test_doctor_says_nothing_about_sensors_where_psutil_has_them(isolated_home):
    assert doctor.sensors_check(None, "linux") is None
    assert doctor.sensors_check(None, "linux", fetch_of(OSError("never asked"))) is None


def test_doctor_says_on_linux_what_the_setup_would_bring(isolated_home, monkeypatch):
    needs = linux_setup.Facts(
        dmi={"board_vendor": "ASUSTeK COMPUTER INC.", "chassis_type": "3"},
        available=frozenset({"nct6775"}),
        panel=True,
        panel_denied=True,
        files=dict.fromkeys(linux_setup.OWN_FILES),
    )
    monkeypatch.setattr(linux_setup, "gather", lambda *args, **kwargs: needs)
    result, said = doctor.sensors_check(None, "linux")
    assert result == "info"
    assert "may not use the USB panel" in said and "fan and temperature chip" in said
    assert said.endswith("libre-panel system-setup --linux")
    assert "ASUS" not in said  # (a report goes into a public issue: no names of the PC)


def test_doctor_says_plainly_that_librehardwaremonitor_does_not_answer(isolated_home):
    path = isolated_home / "config.toml"  # a list without it, whatever this machine's default
    path.parent.mkdir(parents=True)
    path.write_text('[sensors]\nproviders = ["load", "psutil"]\n')
    result, said = doctor.sensors_check(path, "win32", fetch_of(OSError("connection refused")))
    assert result == "info"  # a panel check does not fail for this
    assert said.startswith(f"LibreHardwareMonitor does not answer at {URL}.")
    assert "No CPU or GPU temperature or power readings." in said
    assert "run it as administrator" in said and "Options -> Remote Web Server -> Run" in said
    assert 'Add "librehardwaremonitor" to providers' in said  # the list names no such source


def test_doctor_tells_when_it_answers_but_without_temperatures(isolated_home):
    result, said = doctor.sensors_check(None, "win32", fetch_of({"Children": []}))
    assert result == "info" and "answers, but sends no temperatures or power" in said
    assert "run it as administrator" in said


def test_doctor_is_content_when_the_readings_arrive(isolated_home):
    result, said = doctor.sensors_check(None, "win32", fetch_of(LHM_TREE))
    assert result == "info"
    assert said == f"LibreHardwareMonitor answers at {URL}: temperature and power arrive"


def test_doctor_uses_the_list_and_keeps_a_private_address_out_of_the_report(isolated_home):
    path = write_providers(isolated_home, WINDOWS)
    path.write_text(
        path.read_text()
        + '[sensors.librehardwaremonitor]\nurl = "http://192.168.1.50:8085/data.json"\n'
    )
    asked = []

    def fetch(url):
        asked.append(url)
        raise OSError("timed out")

    _, said = doctor.sensors_check(path, "win32", fetch)
    assert asked == ["http://192.168.1.50:8085/data.json"]  # the address of the config is asked
    assert "192.168" not in said  # but a report goes into a public issue
    assert "the address in [sensors.librehardwaremonitor]" in said
    assert "Add " not in said  # it is in the list


def test_doctor_carries_on_with_a_broken_config(isolated_home):
    path = isolated_home / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text("this is = not [valid")
    _, said = doctor.sensors_check(path, "win32", fetch_of(OSError("refused")))
    assert "does not answer" in said


def no_panel():
    from libre_panel.devices.base import DeviceError

    raise DeviceError("no Turing/TURZX USB panel found")


def run_doctor_without_panel(sensors):
    return doctor.Doctor(
        ask=None,
        say=lambda text: None,
        pause=lambda seconds: None,
        open_transport=no_panel,
        open_serial=lambda: None,
        sensors=sensors,
    ).run()


def test_the_sensors_step_is_in_the_report_even_without_a_panel():
    report = run_doctor_without_panel(lambda: ("info", "LibreHardwareMonitor does not answer"))
    names = [step.name for step in report.steps]
    assert names[:2] == ["environment", "temperature and power readings"]
    step = report.steps[1]
    assert (step.result, step.detail) == ("info", "LibreHardwareMonitor does not answer")
    assert "[INFO] temperature and power readings" in report.text()
    assert report.failed  # no panel; the sensors step is no reason


def test_no_sensors_step_where_there_is_nothing_to_say():
    for sensors in (None, lambda: None):
        report = run_doctor_without_panel(sensors)
        assert "temperature and power readings" not in [step.name for step in report.steps]


def test_a_failing_sensors_check_does_not_stop_the_panel_check():
    def broken():
        raise RuntimeError("no network stack")

    report = run_doctor_without_panel(broken)
    step = report.steps[1]
    assert step.result == "info" and "not checked (RuntimeError: no network stack)" in step.detail
