"""What services get from the host: the sample signal, readings that reach the next
frame, states (dicts and lists), and asking for readings that work on demand."""

import json
import threading
import time

import pytest
from fake_sensors import wait_for
from PIL import Image

from libre_panel import app
from libre_panel.config import Config, SensorsConfig
from libre_panel.plugins import PluginHost, RenderContext
from libre_panel.plugins.host import EVENTS, ServiceHost
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, SensorHub, SensorProvider, Snapshot, wanted
from libre_panel.theme.model import find_theme, load_theme


class Clock:
    """The main loop's clock, by hand."""

    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def host():
    hub = PluginHost()
    yield hub
    hub.close()


def snapshot_of(**values):
    return Snapshot(readings={k: Reading(k, v) for k, v in values.items()})


# -- the sample signal ------------------------------------------------------------------


def test_sample_is_an_event_services_can_hear(host):
    assert "sample" in EVENTS
    seen = []
    ServiceHost(host, "svc").on("sample", lambda snapshot: seen.append(snapshot))
    snapshot = snapshot_of(a=1)
    host.emit("sample", wait=True, snapshot=snapshot)
    assert seen == [snapshot]
    with pytest.raises(ValueError, match="sample"):  # listed among the known events
        host.listen("samples", lambda **_: None)


def test_samples_come_in_a_thread_of_their_own(host):
    seen = []
    ServiceHost(host, "svc").on(
        "sample", lambda snapshot: seen.append((snapshot, threading.current_thread().name))
    )
    snapshot = snapshot_of(a=1)
    host.emit("sample", snapshot=snapshot)
    assert wait_for(lambda: seen)
    assert seen == [(snapshot, "plugin-samples")]


def test_a_slow_listener_gets_the_newest_sample_not_every_one(host):
    gate = threading.Event()
    seen = []

    def slow(snapshot):
        seen.append(snapshot.value("n"))
        assert gate.wait(10)

    ServiceHost(host, "svc").on("sample", slow)
    host.emit("sample", snapshot=snapshot_of(n=1))
    assert wait_for(lambda: seen == [1])  # inside the callback, blocked
    for n in (2, 3, 4):
        host.emit("sample", snapshot=snapshot_of(n=n))
    gate.set()
    assert wait_for(lambda: seen == [1, 4])  # 2 and 3 were overtaken


def test_a_slow_sample_listener_does_not_hold_up_other_events(host):
    gate = threading.Event()
    heard = []
    service = ServiceHost(host, "svc")
    service.on("sample", lambda snapshot: gate.wait(10))
    service.on("panel-restarted", lambda: heard.append("restarted"))
    host.emit("sample", snapshot=snapshot_of(n=1))
    host.emit("panel-restarted")
    try:
        assert wait_for(lambda: heard == ["restarted"], timeout=5)
    finally:
        gate.set()


def test_a_failing_sample_listener_is_reported_once_and_the_others_go_on(host, caplog):
    heard = []
    first, second = ServiceHost(host, "first"), ServiceHost(host, "second")

    def broken(snapshot):
        raise RuntimeError("cannot judge it")

    first.on("sample", broken)
    second.on("sample", lambda snapshot: heard.append(snapshot.value("n")))
    with caplog.at_level("ERROR", logger="libre_panel.plugins.host"):
        for n in (1, 2, 3):
            host.emit("sample", wait=True, snapshot=snapshot_of(n=n))
    assert heard == [1, 2, 3]
    failures = [r for r in caplog.records if "sample listener failed" in r.getMessage()]
    assert len(failures) == 1 and "cannot judge it" in caplog.text  # not once per sample


def test_a_stopped_service_hears_no_more_samples(host):
    heard = []
    ServiceHost(host, "svc").on("sample", lambda snapshot: heard.append(1))
    host.forget("svc")
    host.emit("sample", wait=True, snapshot=snapshot_of(n=1))
    assert heard == []


def test_closing_the_host_ends_the_sample_thread(host):
    ServiceHost(host, "svc").on("sample", lambda snapshot: None)
    sampler = host._sampler
    assert sampler is not None and sampler.is_alive()
    host.close()
    assert not sampler.is_alive()
    host.close()  # again: nothing to do


# -- the readings that change between two samples ---------------------------------------------


def test_fresh_tells_which_readings_changed_and_stays_quiet_when_none_did(host):
    assert "fresh" in EVENTS
    seen = []
    ServiceHost(host, "svc").on(
        "fresh",
        lambda snapshot, keys: seen.append((snapshot, keys, threading.current_thread().name)),
    )
    snapshot = snapshot_of(a=1, b=2)
    before = dict(snapshot.readings)
    latest = {"a": Reading("a", 1), "b": Reading("b", 3), "c": Reading("c", 4)}
    host.fresh(snapshot, before, latest)  # b changed, c is new, a is as it was
    assert wait_for(lambda: seen)
    assert seen == [(snapshot, frozenset({"b", "c"}), "plugin-samples")]
    seen.clear()
    host.fresh(snapshot, latest, dict(latest))  # the same values again: nothing to tell
    host.fresh(snapshot, latest, {})
    time.sleep(0.05)
    assert seen == []


def test_fresh_costs_nothing_while_nobody_listens(host):
    class Never(dict):
        def items(self):
            raise AssertionError("the readings were compared for nobody")

    host.fresh(snapshot_of(a=1), {}, Never())
    assert host._sampler is None  # no thread for it either


def test_a_slow_listener_gets_what_moved_in_between_not_every_change(host):
    gate = threading.Event()
    seen = []

    def slow(snapshot, keys):
        seen.append(keys)
        assert gate.wait(10)

    ServiceHost(host, "svc").on("fresh", slow)
    snapshot = snapshot_of()
    host.emit("fresh", snapshot=snapshot, keys=frozenset({"one"}))
    assert wait_for(lambda: seen == [frozenset({"one"})])  # inside the callback, blocked
    for key in ("two", "three", "two"):
        host.emit("fresh", snapshot=snapshot, keys=frozenset({key}))
    gate.set()
    assert wait_for(lambda: len(seen) == 2)
    assert seen[1] == {"two", "three"}  # one call, and nothing that moved is lost


def test_samples_and_fresh_readings_are_heard_in_one_thread_one_after_the_other(host):
    inside, seen, clashes = 0, [], []

    def listen(name):
        def heard(**_):
            nonlocal inside
            inside += 1
            clashes.append(inside > 1)  # two callbacks at once would show here
            time.sleep(0.01)
            seen.append(name)
            inside -= 1

        return heard

    service = ServiceHost(host, "svc")
    service.on("sample", listen("sample"))
    service.on("fresh", listen("fresh"))
    for _ in range(5):
        host.emit("sample", snapshot=snapshot_of(a=1))
        host.emit("fresh", snapshot=snapshot_of(a=1), keys=frozenset({"a"}))
    assert wait_for(lambda: "sample" in seen and "fresh" in seen)
    time.sleep(0.1)
    assert not any(clashes)


def test_a_stopped_service_hears_no_more_fresh_readings(host):
    heard = []
    ServiceHost(host, "svc").on("fresh", lambda snapshot, keys: heard.append(keys))
    host.forget("svc")
    host.fresh(snapshot_of(a=1), {}, {"a": Reading("a", 1)})
    host.emit("fresh", wait=True, snapshot=snapshot_of(a=1), keys=frozenset({"a"}))
    assert heard == []


def config_for(isolated_home, refresh_ms=100):
    config = Config(theme="libre-default", fps=20, sensors=SensorsConfig(providers=["demo"]))
    config.refresh_ms = refresh_ms
    config.device.driver = "virtual"
    config.device.output = str(isolated_home / "frame.png")
    return config


def run_loop(config, host):
    stop = threading.Event()
    thread = threading.Thread(target=app.run, args=(config,), kwargs={"stop": stop, "host": host})
    thread.start()

    def finish():
        stop.set()
        thread.join(10)
        host.close()

    return finish


def test_the_main_loop_emits_every_new_snapshot(isolated_home):
    host = PluginHost()
    seen = []
    ServiceHost(host, "svc").on("sample", lambda snapshot: seen.append(snapshot))
    finish = run_loop(config_for(isolated_home), host)
    try:
        assert wait_for(lambda: len(seen) >= 3, timeout=10)
    finally:
        finish()
    assert len({id(s) for s in seen}) == len(seen)  # a new snapshot each time
    assert all(s.value("cpu.load") is not None for s in seen)  # with the readings in it


# -- readings that reach the next frame -----------------------------------------------------


def test_a_published_reading_reaches_the_next_frame_not_only_the_next_sample(host):
    hub = SensorHub([host.provider()])
    assert hub.every_frame
    snapshot = hub.snapshot()
    ServiceHost(host, "svc").publish("demo.x", 5, "u", "X")
    assert snapshot.value("demo.x") is None  # the sample was taken before
    snapshot.readings = {**snapshot.readings, **hub.fresh()}  # what the loop does between samples
    assert snapshot.value("demo.x") == 5
    assert snapshot.readings["demo.x"].unit == "u"
    ServiceHost(host, "svc").publish("demo.x", 7)
    snapshot.readings = {**snapshot.readings, **hub.fresh()}
    assert snapshot.value("demo.x") == 7
    assert len(hub.snapshot().history["demo.x"]) == 1  # graphs: one point a sample


def test_the_main_loop_shows_a_publish_before_the_next_sample(isolated_home):
    host = PluginHost()
    config = config_for(isolated_home, refresh_ms=60_000)  # one sample in this test
    finish = run_loop(config, host)
    try:
        assert wait_for(lambda: host.latest is not None)
        first = host.latest
        ServiceHost(host, "svc").publish("demo.late", 42)
        assert wait_for(lambda: host.latest.value("demo.late") == 42, timeout=5)
        assert host.latest is first  # the same sample, its readings brought up to date
    finally:
        finish()


def test_the_main_loop_tells_a_change_between_two_samples_on_the_frame_it_shows_in(
    isolated_home,
):
    """A source that follows on every frame (the volume) moves between two samples: the
    services hear of it on that frame, and of nothing else. No sample is taken for it."""
    from test_app_cli import MemoryStream, StepClock

    class Level(SensorProvider):
        name = "level"
        every_frame = True
        value = 30.0

        def read(self):
            return {"zz.level": Reading("zz.level", self.value, "%", "Level")}

    clock, level, host = StepClock(1000.0), Level(), PluginHost()
    config = config_for(isolated_home, refresh_ms=60_000)  # one sample in this test
    hub = SensorHub([level])
    loop = app.MainLoop(config, host=host, hub=hub, display=MemoryStream(), clock=clock)
    told, samples = [], []
    service = ServiceHost(host, "svc")
    service.on("fresh", lambda snapshot, keys: told.append((snapshot.value("zz.level"), keys)))
    service.on("sample", lambda snapshot: samples.append(snapshot))
    try:
        for _ in range(10):  # the first frame takes the sample; the others change nothing
            clock.now += 0.02
            loop.step(clock.now)
        assert wait_for(lambda: len(samples) == 1)
        time.sleep(0.05)
        assert told == []  # nothing moved, so nobody was woken
        level.value = 35.0
        clock.now += 0.02
        loop.step(clock.now)
        assert wait_for(lambda: told)  # on this frame, not at the next sample
        assert told == [(35.0, frozenset({"zz.level"}))] and len(samples) == 1
        clock.now += 0.02
        loop.step(clock.now)  # it stays at 35: nothing new
        time.sleep(0.05)
        assert len(told) == 1
    finally:
        loop.close()
        host.close()


def test_messages_wait_for_a_panel_that_is_away_without_piling_up(isolated_home):
    """While the panel is unplugged the loop goes on, and the messages of the services wait
    for it: the newest ``MAX_TOASTS`` of them, not every one of hours (a music message
    carries its cover). When the panel is back they show."""
    from test_app_cli import StepClock

    from libre_panel.devices.base import DeviceError, Display

    class Panel(Display):
        away = True
        shown = 0

        def open(self):
            if self.away:
                raise DeviceError("unplugged")

        def show(self, frame, region=None):
            self.shown += 1

    clock, host, panel = StepClock(1000.0), PluginHost(), Panel(None)
    loop = app.MainLoop(config_for(isolated_home), host=host, display=panel, clock=clock)
    music = ServiceHost(host, "music")
    try:
        for track in range(200):
            cover = Image.new("RGB", (64, 64))
            music.notify(f"Track {track}", kind="music", key=f"t{track}", payload={"image": cover})
            clock.now += 0.05
            loop.step(clock.now)
        assert panel.shown == 0
        waiting = len(loop.toasts.inbox) + len(loop.toasts.queue) + len(host.take_toasts())
        assert waiting <= PluginHost.MAX_TOASTS
        music.notify("Track 200", kind="music")
        panel.away = False
        clock.now += 60  # the next try to open it
        loop.step(clock.now)
        assert panel.shown == 1
        assert loop.toasts.current is not None  # a message waited for it
    finally:
        loop.close()
        host.close()


def test_a_theme_a_service_asks_for_that_is_broken_leaves_the_panel_its_own(isolated_home):
    """The autopilot or game mode asks for a theme whose file has the wrong shape (a colour
    where the background's object belongs): the panel keeps the theme it shows, at once and
    after a new start, instead of the loop failing and starting over every few seconds."""
    from test_app_cli import MemoryStream, StepClock

    from libre_panel.config import user_themes_dir

    folder = user_themes_dir() / "broken"
    folder.mkdir(parents=True)
    theme = {"format": "libre-panel-theme/1", "display": {"width": 480, "height": 320},
             "background": "#101418", "widgets": []}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    clock, host, config = StepClock(1000.0), PluginHost(), config_for(isolated_home)
    loops = [app.MainLoop(config, host=host, display=MemoryStream(), clock=clock)]
    try:
        ServiceHost(host, "autopilot").show_theme("broken")
        for _ in range(3):
            clock.now += 0.1
            loops[0].step(clock.now)
        assert (loops[0].shown, loops[0].refused) == ("libre-default", "broken")
        loops.append(app.MainLoop(config, host=host, display=MemoryStream()))
        assert loops[1].shown == "libre-default"  # the panel loop started anew
    finally:
        for loop in loops:
            loop.close()
        host.close()


# -- states -------------------------------------------------------------------------------


def test_a_state_is_json_text_with_the_time_it_was_published(host):
    clock = Clock(now=250.5)
    hub = PluginHost(clock=clock)
    service = ServiceHost(hub, "svc")
    service.publish_state("svc.round", {"name": "Größe", "scores": [1, 2.5, None]}, label="Round")
    reading = hub.published()["svc.round"]
    assert json.loads(reading.value) == {"name": "Größe", "scores": [1, 2.5, None]}
    assert "Größe" in reading.value and " " not in reading.value  # compact, readable
    assert (reading.label, reading.stamp) == ("Round", 250.5)

    clock.now = 260.0
    service.publish_state("svc.list", [3, 4])
    snapshot = SensorHub([hub.provider()]).snapshot()
    assert snapshot.state("svc.round") == {"name": "Größe", "scores": [1, 2.5, None]}
    assert snapshot.state("svc.list") == [3, 4]
    assert snapshot.state_stamp("svc.round") == 250.5 and snapshot.state_stamp("svc.list") == 260.0
    assert snapshot.state_stamp("svc.missing") is None
    hub.close()


def test_the_default_clock_is_the_main_loops():
    before = time.perf_counter()
    host = PluginHost()
    ServiceHost(host, "svc").publish_state("svc.x", {})
    stamp = host.published()["svc.x"].stamp
    assert before <= stamp <= time.perf_counter()


def test_missing_broken_and_null_states_give_the_default(host):
    service = ServiceHost(host, "svc")
    service.publish_state("svc.none", None)
    host.publish(Reading("svc.broken", "{not json"))
    host.publish(Reading("svc.number", 5))
    host.publish(Reading("svc.text", "5"))  # JSON, but no dict or list
    snapshot = SensorHub([host.provider()]).snapshot()
    for key in ("svc.none", "svc.broken", "svc.number", "svc.text", "svc.missing"):
        assert snapshot.state(key) is None
        assert snapshot.state(key, {"x": 1}) == {"x": 1}
    assert host.published()["svc.none"].value == "null"
    assert Snapshot().state("anything", []) == []


def test_states_are_parsed_anew_so_nobody_shares_one_copy(host):
    ServiceHost(host, "svc").publish_state("svc.s", {"items": [1]})
    snapshot = SensorHub([host.provider()]).snapshot()
    snapshot.state("svc.s")["items"].append(2)
    assert snapshot.state("svc.s") == {"items": [1]}


@pytest.mark.parametrize("value", [5, "text", (1, 2), 1.5, True])
def test_only_dicts_and_lists_are_states(host, value):
    with pytest.raises(TypeError, match="dict, a list or None"):
        ServiceHost(host, "svc").publish_state("svc.s", value)
    assert "svc.s" not in host.published()


def test_a_state_that_is_no_json_is_refused_before_it_is_published(host):
    with pytest.raises(TypeError):
        ServiceHost(host, "svc").publish_state("svc.s", {"image": object()})
    assert "svc.s" not in host.published()


def test_states_are_never_in_a_graphs_history(host):
    service = ServiceHost(host, "svc")
    hub = SensorHub([host.provider()])
    for n in range(3):
        service.publish_state("svc.s", {"n": n})
        service.publish("svc.number", n)
        snapshot = hub.snapshot()
    assert "svc.s" not in snapshot.history
    assert snapshot.history["svc.number"] == [0.0, 1.0, 2.0]
    assert snapshot.state("svc.s") == {"n": 2}


def test_a_state_reaches_the_next_frame_and_goes_with_its_service(host):
    service = ServiceHost(host, "svc")
    hub = SensorHub([host.provider()])
    snapshot = hub.snapshot()
    service.publish_state("svc.s", [1])
    snapshot.readings = {**snapshot.readings, **hub.fresh()}
    assert snapshot.state("svc.s") == [1]
    host.forget("svc")
    assert "svc.s" not in host.published()


def test_screens_and_styles_read_states_through_the_context(isolated_home):
    renderer = Renderer(load_theme(find_theme("libre-default")))
    context = RenderContext(renderer)
    assert context.state("svc.s", "nothing") == "nothing"  # nothing drawn yet
    host = PluginHost()
    ServiceHost(host, "svc").publish_state("svc.s", {"a": 1})
    renderer.snapshot = SensorHub([host.provider()]).snapshot()
    assert context.state("svc.s") == {"a": 1}
    assert context.state("svc.other", []) == []
    host.close()


# -- asking for readings that work on demand ------------------------------------------------------


def test_a_service_can_ask_for_readings_no_theme_shows(host):
    assert not wanted("test.reading.")
    service = ServiceHost(host, "svc")
    service.want("test.reading.*", "test.other")
    assert wanted("test.reading.") and wanted("test.other")
    service.want("test.other")  # replaces what it asked for before
    assert not wanted("test.reading.") and wanted("test.other")
    ServiceHost(host, "another").want("test.reading.x")
    host.forget("svc")  # a service that stops asks for nothing any more
    assert not wanted("test.other") and wanted("test.reading.")
    ServiceHost(host, "another").want()
    assert not wanted("test.reading.")
