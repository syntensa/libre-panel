"""The alarm on every panel: a sensor at its crit limit turns every panel's edge red for a
moment, and a panel beside the first shows its alarm theme while it lasts."""

from types import SimpleNamespace

from PIL import Image
from test_notices import desk, temperature  # noqa: F401 - the fixture

from libre_panel import app
from libre_panel.config import Config, SensorsConfig, parse_config
from libre_panel.render.alarm import SECONDS, AlarmEdge
from libre_panel.sensors.base import Reading, SensorHub, SensorProvider, Snapshot


def test_a_sensor_at_its_crit_limit_is_an_alarm_until_it_has_been_below_for_a_while(desk):  # noqa: F811
    d = desk(warnings=[{"key": "cpu.temp", "warn": 80, "crit": 90}])
    published = d.host.published
    assert published()["alarm"].value == 0  # there is a crit limit: no alarm yet
    d.sample(temperature(85))
    assert published()["alarm"].value == 0  # a warning is no alarm
    d.sample(temperature(92))
    assert published()["alarm"].value == 1
    assert published()["alarm.text"].value == "CPU temperature 92 °C"
    d.sample(temperature(70), seconds=5)
    assert published()["alarm"].value == 1  # it holds a while: no blinking at the limit
    d.sample(temperature(95), seconds=4)
    d.sample(temperature(70), seconds=9)
    assert published()["alarm"].value == 1  # from the last value at the limit
    d.sample(temperature(70), seconds=2)
    assert published()["alarm"].value == 0 and "alarm.text" not in published()


def test_there_is_no_alarm_without_a_crit_limit_or_with_it_off(desk):  # noqa: F811
    warned = desk(warnings=[{"key": "cpu.temp", "warn": 80}])
    warned.sample(temperature(99))
    assert "alarm" not in warned.host.published()
    off = desk(warnings=[{"key": "cpu.temp", "crit": 90}], alarm=False)
    off.sample(temperature(99))
    assert "alarm" not in off.host.published()


def test_the_edge_pulses_for_a_moment_when_the_alarm_begins():
    edge, frame = AlarmEdge(), Image.new("RGB", (200, 100), "#000000")
    edge.follow(False, 10.0)
    assert edge.draw(frame, 10.0, "#FF0000", still=False) is frame  # no alarm: as it is
    edge.follow(True, 11.0)
    begun = edge.draw(frame, 11.0, "#FF0000", still=False)
    assert begun is not frame and frame.getpixel((0, 50)) == (0, 0, 0)  # (a new picture)
    r, g, b = begun.getpixel((0, 50))
    assert r > 200 and g == b == 0  # red at the rim
    assert begun.getpixel((100, 50)) == (0, 0, 0)  # and nothing inside
    softer = edge.draw(frame, 11.5, "#FF0000", still=False).getpixel((0, 50))[0]
    assert softer < r  # it pulses
    edge.follow(True, 11.0 + SECONDS)
    assert edge.draw(frame, 11.0 + SECONDS, "#FF0000", still=False) is frame  # over, alarm or not
    edge.follow(False, 30.0)
    edge.follow(True, 31.0)  # a new alarm: a new edge
    assert edge.draw(frame, 31.0, None, still=True) is not frame


def test_on_a_calm_panel_the_edge_stays_still():
    edge, frame = AlarmEdge(), Image.new("RGB", (120, 120), "#000000")
    edge.follow(True, 0.0)
    pictures = {edge.draw(frame, at, "#FF0000", still=True).tobytes() for at in (0.0, 0.5, 1.0)}
    assert len(pictures) == 1


def test_a_panel_beside_the_first_shows_its_alarm_theme_while_the_alarm_lasts():
    config = parse_config({"panels": [{"theme": "slate"}, {"theme": "slate", "alarm_theme": ""}]})
    given, none = config.for_panel(0), config.for_panel(1)
    assert config.panels[0].alarm_theme == "lumen-companion"  # by default: temperatures and load
    assert given.modes["alarm"].theme == "lumen-companion" and "alarm" not in none.modes
    hot = Snapshot(readings={"alarm": Reading("alarm", 1)})
    calm_again = Snapshot(readings={"alarm": Reading("alarm", 0)})

    def mode(config, snapshot, first="game"):
        loop = SimpleNamespace(mode_of=lambda: first, config=config, snapshot=snapshot)
        return app.MainLoop._mode(loop)

    assert mode(given, hot) == "alarm"  # over the first panel's game
    assert app._theme_name(given, None, None, None, None, "alarm") == "lumen-companion"
    assert mode(given, calm_again) == "game" and mode(given, None) == "game"
    assert mode(none, hot) == "game"  # one without an alarm theme keeps its own
    first = SimpleNamespace(mode_of=None, config=given, snapshot=hot)
    assert app.MainLoop._mode(first) is None  # the first panel: its host says


def test_the_alarm_theme_is_set_in_settings_and_kept_empty(tmp_path):
    from libre_panel.editor.settings import Restart, Settings

    path = tmp_path / "config.toml"
    path.write_text('theme = "studio"\n', encoding="utf-8")
    settings = Settings(path, None, Restart())
    tables = [{"theme": "slate"}, {"theme": "slate", "alarm_theme": ""}]
    panels = settings.write({"set": {"panels": tables}})["config"]["panels"]
    assert [panel["alarm_theme"] for panel in panels] == ["lumen-companion", ""]
    assert path.read_text(encoding="utf-8").count("alarm_theme") == 1  # only where it differs


class Alarm(SensorProvider):
    name = "alarm"

    def __init__(self):
        super().__init__()
        self.on = 0

    def read(self):
        return {"alarm": Reading("alarm", self.on)}


def test_every_panel_s_frame_has_the_red_edge_when_the_alarm_begins():
    from test_app_cli import MemoryStream, StepClock

    source, clock = Alarm(), StepClock(1000.0)
    loop = app.MainLoop(Config(theme="slate", sensors=SensorsConfig(providers=[])),
                        display=MemoryStream(), clock=clock, hub=SensorHub([source]))  # fmt: skip
    try:
        frame = loop.step()
        w, h = frame.size
        before = frame.getpixel((0, h // 2))
        source.on = 1
        clock.now += 5.0  # past the next sample
        r, g, b = loop.step().getpixel((0, h // 2))[:3]
        assert r > before[0] + 60 and r > g + 60  # red at the rim
        clock.now += SECONDS + 5.0
        assert loop.step().getpixel((0, h // 2)) == before  # a moment, the alarm or not
    finally:
        loop.close()
