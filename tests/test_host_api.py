"""What services get from the host: the sample signal, readings that reach the next
frame, states (dicts and lists), and asking for readings that work on demand."""

import json
import threading
import time

import pytest
from fake_sensors import wait_for

from libre_panel import app
from libre_panel.config import Config, SensorsConfig
from libre_panel.plugins import PluginHost, RenderContext
from libre_panel.plugins.host import EVENTS, ServiceHost
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading, SensorHub, Snapshot, wanted
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
