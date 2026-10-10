"""The autopilot: rules from the config, the first that holds wins; how long a choice
must hold and a theme must stay; what the load, the microphone, the programs and the
time of day say; resting in game mode. Through the host and the service manager, with
a made-up computer and time by hand."""

from datetime import datetime

import pytest
from fake_system import FakeMachine

from libre_panel.config import Config, ServicesConfig, parse_config
from libre_panel.plugins import PluginHost, ServiceManager
from libre_panel.plugins.host import ServiceHost
from libre_panel.sensors.base import Reading, Snapshot, wanted
from libre_panel.services import autopilot as autopilot_service
from libre_panel.services import machine as computer
from libre_panel.services.autopilot import in_time, parse_rules, parse_time

READINGS = {"music": "media.playing", "cpu": "cpu.load", "gpu": "gpu.load"}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Desk:
    """A host with the autopilot running; time, the computer and the clock are made up."""

    def __init__(self, monkeypatch, rules, **options):
        self.machine = FakeMachine()
        monkeypatch.setattr(computer, "current", lambda: self.machine)
        self.clock = Clock()
        self.at = (12, 0)  # the time of day
        self.missing = set()  # themes that are not installed
        self.host = PluginHost()
        self.manager = ServiceManager(self.host)  # the registry: Libre Panel's own services
        options = {"rules": rules, **options}
        self.manager.apply(Config(services=ServicesConfig(["autopilot"], {"autopilot": options})))
        assert self.manager.state == {"autopilot": "running"}, self.manager.state
        self.service = self.manager.running["autopilot"][0]
        self.service.clock = self.clock
        self.service.wall = lambda: datetime(2026, 10, 10, *self.at)
        self.service.installed = lambda name: name not in self.missing

    def sample(self, seconds=1.0, **readings):
        """Time passes and a snapshot with these readings (music=1, cpu=90) is taken."""
        self.clock.now += seconds
        found = {READINGS[name]: Reading(READINGS[name], value) for name, value in readings.items()}
        self.host.emit("sample", wait=True, snapshot=Snapshot(readings=found))

    def run(self, seconds, step=1.0, **readings):
        for _ in range(round(seconds / step)):
            self.sample(step, **readings)

    @property
    def theme(self):
        return self.host.theme

    def close(self):
        self.manager.stop()
        self.host.close()


@pytest.fixture
def desk(monkeypatch):
    made = []

    def make(rules, **options):
        made.append(Desk(monkeypatch, rules, **options))
        return made[-1]

    yield make
    for each in made:
        each.close()


QUICK = {"hold_s": 0, "min_s": 0}  # no waiting, for what is not about waiting
MUSIC = {"name": "Music", "theme": "studio", "when": {"music": True}}
LOAD = {"name": "Load", "theme": "column", "when": {"cpu_over": 80, "for_s": 10}}


# -- the rules ------------------------------------------------------------------------------------


def test_the_first_rule_that_holds_wins(desk):
    d = desk([MUSIC, LOAD], **QUICK)
    d.run(12, cpu=90)  # only the load: the second rule
    assert d.theme == "column"
    d.sample(cpu=90, music=1)  # both: the first
    assert d.theme == "studio"
    d.sample(cpu=90)  # only the load again
    assert d.theme == "column"
    d.run(10, cpu=10)  # the mean over 10 s is down
    assert d.theme is None


def test_all_conditions_of_a_rule_must_hold(desk):
    d = desk([{"theme": "studio", "when": {"music": True, "microphone": False}}], **QUICK)
    d.machine.microphone = True
    d.sample(music=1)
    assert d.theme is None
    d.machine.microphone = False
    d.run(2, music=1)
    assert d.theme == "studio"


def test_rules_that_can_never_hold_are_reported_and_left_out(desk, caplog):
    rules = [
        "not a table",
        {"name": "No theme", "when": {"music": True}},
        {"name": "No conditions", "theme": "a", "when": {}},
        {"name": "Only a window", "theme": "a", "when": {"for_s": 5}},
        {"name": "Not a table", "theme": "a", "when": "music"},
        {"name": "Unknown", "theme": "a", "when": {"music": True, "weather": "rain"}},
        {"name": "Wrong value", "theme": "a", "when": {"music": "yes"}},
        {"name": "Wrong load", "theme": "a", "when": {"cpu_over": "high"}},
        {"name": "Wrong window", "theme": "a", "when": {"cpu_over": 5, "for_s": -1}},
        {"name": "Wrong programs", "theme": "a", "when": {"apps": []}},
        {"name": "Wrong time", "theme": "a", "when": {"time": "evening"}},
        {"name": "Wrong hold", "theme": "a", "when": {"music": True}, "hold_s": "soon"},
        {"name": "Switched off", "theme": "a", "when": {"music": True}, "enabled": False},
        {"name": "Fine", "theme": "b", "when": {"music": True}},
    ]
    with caplog.at_level("WARNING", logger="libre_panel.service.autopilot"):
        d = desk(rules)
    assert [rule.name for rule in d.service.rules] == ["Fine"]
    for name in (
        "rule 'rule 1'",
        "No theme",
        "No conditions",
        "Only a window",
        "Not a table",
        "Unknown",
    ):
        assert name in caplog.text
    assert "unknown condition 'weather'" in caplog.text
    assert "Switched off" not in caplog.text


def test_a_rule_has_a_name_by_default():
    problems = []
    rules = parse_rules([{"theme": "a", "when": {"music": True}}, 5], problems.append)
    assert [rule.name for rule in rules] == ["rule 1"] and "rule 2" in problems[0]


# -- not too fast ---------------------------------------------------------------------------------


def test_a_new_choice_must_hold_before_the_panel_follows(desk):
    d = desk([MUSIC])  # hold_s 8, min_s 20
    d.run(8, music=1)
    assert d.theme is None  # 7 s since the first sample that said so
    d.sample(music=1)
    assert d.theme == "studio" and d.host.take_transition() == "tube"


def test_a_choice_that_flickers_starts_again(desk):
    d = desk([MUSIC])
    d.run(6, music=1)
    d.sample()  # no music for a moment
    d.run(8, music=1)
    assert d.theme is None  # 7 s since it came back
    d.sample(music=1)
    assert d.theme == "studio"


def test_a_theme_stays_for_the_minimum_time(desk):
    d = desk([MUSIC], hold_s=2)  # min_s 20
    d.run(3, music=1)
    assert d.theme == "studio"
    d.run(19)  # the music stopped; the hold of 2 s is long over
    assert d.theme == "studio"  # but it came 19 s ago
    d.sample()
    assert d.theme is None  # 20 s


def test_a_rule_can_be_immediate_and_have_its_own_hold_time(desk):
    call = {
        "name": "Call",
        "theme": "column",
        "when": {"microphone": True},
        "hold_s": 1,
        "immediate": True,
    }
    d = desk([call, MUSIC], hold_s=4)
    d.run(5, music=1)
    assert d.theme == "studio"
    d.machine.microphone = True  # 5 s after the switch, the minimum is 20 s
    d.sample(music=1)
    assert d.theme == "studio"  # its own hold of 1 s: not yet
    d.sample(music=1)
    assert d.theme == "column"  # immediate: not the 20 s


def test_no_rule_gives_the_users_theme_back(desk):
    d = desk([MUSIC], **QUICK)
    d.sample(music=1)
    d.host.take_transition()
    assert d.theme == "studio"
    d.sample()
    assert d.theme is None and d.host.take_transition() == "tube"


def test_the_transition_is_an_option(desk):
    d = desk([MUSIC], transition="fade", **QUICK)
    d.sample(music=1)
    assert d.host.take_transition() == "fade"
    d = desk([MUSIC], transition="", **QUICK)
    d.sample(music=1)
    assert d.host.take_transition() is None  # the one in config.toml


def test_it_asks_for_the_lowest_priority_in_normal_mode_only(desk):
    d = desk([MUSIC], **QUICK)
    d.sample(music=1)
    assert d.theme == "studio"
    ServiceHost(d.host, "report").show_theme("slate", priority=10)
    assert d.theme == "slate"  # a service with more to say goes first,
    ServiceHost(d.host, "report").restore_theme()
    assert d.theme == "studio"  # and the choice is back when it is done


# -- the signals ----------------------------------------------------------------------------------


def test_the_load_counts_once_four_fifths_of_its_window_has_been_seen(desk):
    d = desk([LOAD], **QUICK)  # the mean over 10 s
    d.run(8, cpu=95)  # samples 1 to 8 s after the first: 7 s seen
    assert d.theme is None
    d.sample(cpu=95)
    assert d.theme == "column"


def test_one_peak_is_no_load_and_no_reading_is_no_load(desk):
    d = desk([LOAD], **QUICK)
    d.run(9, cpu=10)
    d.sample(cpu=100)  # a peak in a window of 10 samples: the mean is 19
    assert d.theme is None
    d.run(12, cpu=None)  # a source that gives nothing
    d = desk([LOAD], **QUICK)
    d.run(15)
    assert d.theme is None


def test_the_load_of_the_gpu_is_a_condition_too(desk):
    d = desk([{"theme": "orbit", "when": {"gpu_over": 50, "for_s": 5}}], **QUICK)
    d.run(6, gpu=60)
    assert d.theme == "orbit"


def test_right_after_the_system_started_the_load_is_no_signal(desk):
    d = desk([LOAD, MUSIC], **QUICK)
    d.machine.uptime = 100  # it started 100 s ago; 240 s is the quiet time
    d.run(12, cpu=99)
    assert d.theme is None
    d.sample(music=1, cpu=99)
    assert d.theme == "studio"  # music is no load
    d.machine.uptime = 241
    d.sample(cpu=99)
    assert d.theme == "column"
    asked = d.machine.asked["uptime"]
    d.run(5, cpu=99)
    assert d.machine.asked["uptime"] == asked  # settled: not asked any more


def test_an_unknown_uptime_does_not_hold_the_load_back(desk):
    d = desk([LOAD], **QUICK)
    d.machine.uptime = None
    d.run(12, cpu=90)
    assert d.theme == "column"


def test_the_microphone_is_a_condition(desk):
    d = desk([{"theme": "column", "when": {"microphone": True}}], **QUICK)
    d.sample()
    assert d.theme is None
    d.machine.microphone = True
    d.sample()
    assert d.theme == "column"
    d.machine.microphone = False
    d.sample()
    assert d.theme is None


def test_slow_signals_are_asked_once_a_second(desk):
    d = desk([{"theme": "column", "when": {"microphone": True}}], **QUICK)
    for _ in range(10):
        d.sample(0.1)
    assert d.machine.asked["microphone"] == 1  # in a second of samples
    d.run(2)
    assert d.machine.asked["microphone"] == 3


def test_a_program_in_front_is_a_condition_where_that_is_known(desk):
    d = desk([{"theme": "slate", "when": {"apps": ["Code.EXE", "devenv.exe"]}}], **QUICK)
    d.machine.start("code.exe", 5)
    d.machine.start("browser.exe", 6)
    d.machine.front = 6
    d.sample()
    assert d.theme is None  # it runs, but it is not in front
    d.machine.front = 5
    d.sample()
    assert d.theme == "slate"
    assert d.machine.asked["programs"] == 0  # nobody listed the programs


def test_where_the_program_in_front_is_not_known_a_running_one_counts(desk):
    d = desk([{"theme": "slate", "when": {"apps": ["code.exe"]}}], **QUICK)
    d.machine.has_foreground = False
    d.machine.start("browser.exe", 6)
    d.sample()
    assert d.theme is None
    d.machine.start("Code.exe", 5)
    d.sample(3)
    assert d.theme == "slate"


@pytest.mark.parametrize(
    ("window", "at", "holds"),
    [
        ("09:00-17:00", (9, 0), True),
        ("09:00-17:00", (16, 59), True),
        ("09:00-17:00", (17, 0), False),  # the end is not in
        ("09:00-17:00", (8, 59), False),
        ("22:00-06:30", (23, 30), True),  # over midnight
        ("22:00-06:30", (0, 0), True),
        ("22:00-06:30", (6, 29), True),
        ("22:00-06:30", (6, 30), False),
        ("22:00-06:30", (12, 0), False),
        ("22:00-06:30", (21, 59), False),
        ("12:00-12:00", (12, 0), False),
    ],
)
def test_the_time_of_day_may_cross_midnight(desk, window, at, holds):
    d = desk([{"theme": "dim", "when": {"time": window}}], **QUICK)
    d.at = at
    d.sample()
    assert (d.theme == "dim") is holds


def test_times_are_read_as_minutes():
    assert parse_time("22:00-06:30") == (22 * 60, 6 * 60 + 30)
    assert parse_time(" 7:05 - 18:00 ") == (7 * 60 + 5, 18 * 60)
    for bad in ("24:00-01:00", "10:60-11:00", "10-11", "noon", 5, None):
        assert parse_time(bad) is None
    assert in_time((600, 700), 600) and not in_time((600, 700), 700)


# -- game mode and other modes --------------------------------------------------------------------


def test_it_rests_in_game_mode_and_its_choice_is_back_when_the_game_ends(desk):
    d = desk([MUSIC], hold_s=2, min_s=0)
    d.run(3, music=1)
    assert d.theme == "studio"
    d.host.set_mode("game", by="game")
    assert d.theme is None  # resting: the request is there, but not for this mode
    d.run(30)  # the music stopped in the game; nothing is asked
    d.run(30, music=1)
    assert d.theme is None and d.service.theme == "studio"
    d.host.set_mode(None, by="game")
    assert d.theme == "studio"  # at once, nobody has to ask again
    d.sample()  # the music stops
    assert d.theme == "studio"  # a new choice must hold afresh
    d.run(2)
    assert d.theme is None


def test_a_stopped_autopilot_leaves_nothing_behind(desk):
    d = desk([MUSIC], **QUICK)
    d.sample(music=1)
    assert d.theme == "studio"
    d.manager.apply(Config())
    assert d.theme is None


# -- themes that are not there --------------------------------------------------------------------


def test_a_rule_whose_theme_is_not_installed_is_skipped_with_one_line(desk, caplog):
    first = {"name": "Missing", "theme": "no-such", "when": {"music": True}}
    d = desk(
        [first, {"name": "Load", "theme": "column", "when": {"cpu_over": 50, "for_s": 5}}], **QUICK
    )
    d.missing = {"no-such"}
    with caplog.at_level("WARNING", logger="libre_panel.service.autopilot"):
        d.run(8, music=1, cpu=90)
    assert d.theme == "column"  # the next rule that holds
    assert caplog.text.count("theme 'no-such' is not installed") == 1
    d.missing = set()  # installed in the meantime: found within the minute
    d.run(12, music=1, cpu=90)
    assert d.theme == "no-such"


def test_the_real_themes_are_looked_up_when_nothing_is_replaced(desk):
    d = desk([{"name": "Nowhere", "theme": "no-such", "when": {"music": True}}, MUSIC], **QUICK)
    del d.service.installed  # the real check
    assert autopilot_service.theme_installed("libre-default")
    assert not autopilot_service.theme_installed("no-such")
    assert not autopilot_service.theme_installed("../etc")


# -- what it asks for and how it is switched on ---------------------------------------------------


def test_the_readings_the_rules_use_are_asked_for(desk):
    desk([MUSIC, LOAD, {"theme": "a", "when": {"gpu_over": 50}}])
    assert wanted("media.playing") and wanted("cpu.load") and wanted("gpu.load")


def test_the_autopilot_comes_from_the_config():
    text = """
[services]
enabled = ["autopilot"]

[services.autopilot]
hold_s = 3
quiet_after_boot_s = 0

[[services.autopilot.rules]]
name = "Music"
theme = "studio"
when = { music = true }

[[services.autopilot.rules]]
name = "Night"
theme = "dim"
when = { time = "22:00-06:00", for_s = 5 }
immediate = true
"""
    import tomllib

    host = PluginHost()
    manager = ServiceManager(host)
    manager.apply(parse_config(tomllib.loads(text)))
    try:
        service = manager.running["autopilot"][0]
        assert service.options["hold_s"] == 3 and service.options["min_s"] == 20
        assert [(r.name, r.theme, r.immediate) for r in service.rules] == [
            ("Music", "studio", False),
            ("Night", "dim", True),
        ]
        assert service.rules[1].when == {"time": (22 * 60, 6 * 60)}
    finally:
        manager.stop()
        host.close()


def test_what_it_is_doing_can_be_looked_at(desk):
    d = desk([MUSIC], **QUICK)
    d.machine.microphone = True
    assert d.service.status()["theme"] is None and d.service.status()["since_s"] is None
    d.sample(music=1)
    status = d.service.status()
    assert (status["rule"], status["theme"], status["resting"]) == ("Music", "studio", False)
    assert status["since_s"] == 0 and status["signals"]["music"] is True


# -- the real main loop ---------------------------------------------------------------------------


def test_the_autopilot_changes_the_theme_in_the_main_loop(monkeypatch, isolated_home):
    from test_toast_scenarios import Panel, Script, write_theme

    from libre_panel import app
    from libre_panel.config import SensorsConfig
    from libre_panel.render.renderer import Renderer

    for name, color in (("classic", "#102030"), ("focus", "#302010")):
        write_theme(isolated_home, name, color, style="card")
    themes, made = [], []
    render, make = Renderer.render, app.transition

    def rendered(self, snapshot, now=None):
        themes.append(self.theme.name)
        return render(self, snapshot, now)

    def transition(spec, context=None):
        made.append(spec)
        return make(spec, context)

    machine = FakeMachine()
    monkeypatch.setattr(
        autopilot_service, "SLOW_EVERY_S", 0.0
    )  # the frames here are faster than time
    monkeypatch.setattr(computer, "current", lambda: machine)
    monkeypatch.setattr(Renderer, "render", rendered)
    monkeypatch.setattr(app, "transition", transition)
    monkeypatch.setattr(app, "create_display", Panel)
    host = PluginHost()
    manager = ServiceManager(host)
    rules = [{"name": "Call", "theme": "focus", "when": {"microphone": True}}]
    options = {"rules": rules, "hold_s": 0, "min_s": 0, "transition": "fade"}
    config = Config(
        theme="classic",
        fps=50,
        refresh_ms=100,
        sensors=SensorsConfig(providers=["demo"]),
        services=ServicesConfig(["autopilot"], {"autopilot": options}),
        transition="slide",  # not the autopilot's
    )
    manager.apply(config)
    seen = {}

    def call_begins():
        machine.microphone = True

    def watch():  # the loop's samples decide when; wait for them
        if machine.microphone and "on" not in seen and host.theme == "focus":
            seen["on"] = script.n
        elif "on" in seen and "off" not in seen and script.n >= seen["on"] + 100:
            seen["off"] = script.n
            machine.microphone = False

    script = Script(1500, {20: [call_begins], **{n: [watch] for n in range(21, 2000)}})
    monkeypatch.setattr(app, "_Pacer", lambda: script)
    try:
        app.run(config, host=host)
    finally:
        manager.stop()
        host.close()
    assert "on" in seen and "off" in seen, f"the microphone rule did not switch the theme: {seen}"
    assert set(themes[:21]) == {"classic"} and "focus" in themes
    assert themes[-1] == "classic"  # the user's theme is back,
    assert made.count("fade") == 2  # each time with the transition the option names
