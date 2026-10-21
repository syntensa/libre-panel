"""Frame rates from MangoHud's log (Linux), values from JSON, and the ping's lost tries."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fake_sensors import show, wait_for

from libre_panel.sensors import base, json_source, mangohud, psutil_provider
from libre_panel.sensors.base import create_provider

HEADER = "os,cpu,gpu,ram,kernel,driver,cpuscheduler\nLinux,CPU,GPU,32,6.8,mesa,none\n"
COLUMNS = "fps,frametime,cpu_load,gpu_load,elapsed\n"


@pytest.fixture(autouse=True)
def nothing_is_shown(monkeypatch):
    monkeypatch.setattr(base, "_WANTED", {})


# -- MangoHud -----------------------------------------------------------------------------


def test_the_log_folder_is_the_one_mangohud_conf_names(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert mangohud.config_folder(tmp_path) == tmp_path  # none named: the home folder
    conf = tmp_path / "config" / "MangoHud" / "MangoHud.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("fps_limit=144\noutput_folder = ~/mangologs\nautostart_log=1\n", "utf-8")
    assert mangohud.config_folder(tmp_path) == mangohud.Path("~/mangologs").expanduser()


def test_the_newest_log_written_just_now_is_the_one_read(tmp_path):
    old = tmp_path / "Doom_2026-10-20_18-00-00.csv"
    new = tmp_path / "Hades_2026-10-21_19-04-33.csv"
    summary = tmp_path / "Hades_2026-10-21_19-04-33_summary.csv"
    for path in (old, new, summary):
        path.write_text(HEADER, "utf-8")
    now = time.time()
    os.utime(old, (now - 3600, now - 3600))  # a game that ended
    os.utime(new, (now - 1, now - 1))
    assert mangohud.newest_log(tmp_path, now) == new
    assert mangohud.app_of(new) == "Hades"
    os.utime(new, (now - 60, now - 60))
    assert mangohud.newest_log(tmp_path, now) is None  # nothing is being written


def test_the_rows_are_read_as_they_are_written_after_the_header(tmp_path):
    log = tmp_path / "Hades_2026-10-21_19-04-33.csv"
    log.write_text(HEADER + COLUMNS + "60,16.6,30,90,100\n", "utf-8")
    reader = mangohud.LogReader(log)
    assert reader.frames() == [16.6]
    with log.open("a", encoding="utf-8") as file:
        file.write("58,17.2,31,91,200\n59,16.9,")  # the last row not ended yet
    assert reader.frames() == [17.2]
    with log.open("a", encoding="utf-8") as file:
        file.write("30,92,300\n")
    assert reader.frames() == [16.9]
    assert reader.frames() == []


def test_the_frame_rate_of_the_game_comes_while_a_theme_shows_it(tmp_path, monkeypatch):
    monkeypatch.setattr(mangohud, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(mangohud, "LOOK_SECONDS", 0.01)
    log = tmp_path / "Hades_2026-10-21_19-04-33.csv"
    log.write_text(HEADER + COLUMNS + "10,100,0,0,0\n", "utf-8")  # logged before: not now
    provider = create_provider("mangohud", {"folder": str(tmp_path)})
    try:
        assert provider.read() == {} and provider._thread is None  # nobody shows it
        theme = show("game.fps")
        provider.read()
        assert wait_for(lambda: provider._thread is not None and provider._thread.is_alive())
        time.sleep(0.1)  # the reader has found the log and skipped what was there

        def frames():
            with log.open("a", encoding="utf-8") as file:
                file.write("".join("120,8.33,40,95,1\n" for _ in range(10)))
            return "game.fps" in provider.read()

        assert wait_for(frames)
        read = provider.read()
        assert read["game.fps"].value == pytest.approx(120, abs=1)
        assert read["game.app"].value == "Hades"
        assert read["game.frametime"].value == pytest.approx(8.33, abs=0.01)
        del theme
    finally:
        provider.close()


# -- JSON ---------------------------------------------------------------------------------


def test_a_path_picks_into_dicts_and_lists():
    data = {"system": {"temp": 41.5}, "sensors": [{"value": 3}, {"value": 4}], "ok": True}
    assert json_source.pick(data, "system.temp") == 41.5
    assert json_source.pick(data, "sensors[1].value") == 4
    assert json_source.pick(data, "sensors.0.value") == 3
    assert json_source.pick(data, "sensors[5].value") is None
    assert json_source.pick(data, "nothing.here") is None
    assert json_source.scalar(True) == 1.0 and json_source.scalar([1]) is None


def test_without_fields_every_value_is_read_by_its_path():
    data = {"cpu": 12, "load": {"1m": 0.5, "deep": {"deeper": {"deepest": 1}}}, "name": "nas"}
    assert json_source.flatten(data) == {"cpu": 12.0, "load.1m": 0.5, "name": "nas"}


class Answers(BaseHTTPRequestHandler):
    body = b"{}"
    seen: list = []

    def do_GET(self):  # noqa: N802 - the server's name
        Answers.seen.append(dict(self.headers))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(Answers.body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    made = ThreadingHTTPServer(("127.0.0.1", 0), Answers)
    threading.Thread(target=made.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{made.server_address[1]}"
    made.shutdown()


def test_an_address_gives_its_fields_with_their_units(server):
    Answers.body = json.dumps({"cpu": 7.5, "sensors": [{"value": 44}], "host": "box"}).encode()
    source = {"name": "my nas", "url": server + "/api", "fields": {"cpu": "cpu",
              "temp": "sensors[0].value", "host": "host", "gone": "nope"},
              "units": {"cpu": "%", "temp": "°C", "host": "x"},
              "headers": {"Authorization": "Bearer token"}}  # fmt: skip
    provider = json_source.JsonProvider({"sources": [source]}, start=False)
    provider.fetch(provider.sources[0])
    read = provider.read()
    assert set(read) == {"json.my_nas.cpu", "json.my_nas.temp", "json.my_nas.host"}
    assert (read["json.my_nas.temp"].value, read["json.my_nas.temp"].unit) == (44.0, "°C")
    assert (read["json.my_nas.host"].value, read["json.my_nas.host"].unit) == ("box", "")
    assert Answers.seen[-1]["Authorization"] == "Bearer token"
    provider.close()


def test_a_file_is_read_and_a_source_that_fails_keeps_its_last_values(tmp_path, caplog):
    path = tmp_path / "values.json"
    path.write_text(json.dumps({"rooms": {"kitchen": 21.5}}), "utf-8")
    provider = json_source.JsonProvider({"sources": [{"name": "home", "file": str(path)}]},
                                        start=False)  # fmt: skip
    provider.fetch(provider.sources[0])
    assert provider.read()["json.home.rooms.kitchen"].value == 21.5
    path.write_text("{ not json", "utf-8")
    provider.fetch(provider.sources[0])
    provider.fetch(provider.sources[0])
    assert provider.read()["json.home.rooms.kitchen"].value == 21.5  # kept for a while
    assert sum("does not answer" in record.message for record in caplog.records) == 1  # once


def test_a_source_without_an_address_or_with_another_scheme_is_left_out(caplog):
    provider = json_source.JsonProvider(
        {"sources": [{"name": "a"}, {"name": "b", "url": "ftp://x"}, "c"]}, start=False
    )
    assert provider.sources == [] and len(caplog.records) == 3


# -- the ping -----------------------------------------------------------------------------


def test_the_ping_says_its_host_and_the_share_of_tries_without_an_answer(monkeypatch):
    provider = psutil_provider.PsutilProvider({"ping": "192.0.2.1:443"})
    answers = iter([True, False, True, True])

    def connect(address, timeout):
        if not next(answers):
            raise OSError("no answer")
        return open(os.devnull)  # something to close

    monkeypatch.setattr(psutil_provider.socket, "create_connection", connect)
    for _ in range(4):
        read = provider._ping()
    assert read["net.ping.lost"].value == 25 and read["net.ping.host"].value == "192.0.2.1"
    assert read["net.ping"].value is not None and read["net.ping"].unit == "ms"
    provider.close()


def test_the_ping_connects_to_each_host_named(monkeypatch):
    provider = psutil_provider.PsutilProvider(
        {"ping": ["192.0.2.1:443", "192.168.1.1:80", "[2001:db8::1]:443", "example.org"]}
    )
    asked = []

    def connect(address, timeout):
        asked.append(address)
        if address[0] == "192.168.1.1":
            raise OSError("no answer")
        return open(os.devnull)

    monkeypatch.setattr(psutil_provider.socket, "create_connection", connect)
    read = provider._ping()
    assert asked == [("192.0.2.1", 443), ("192.168.1.1", 80), ("2001:db8::1", 443),
                     ("example.org", 443)]  # fmt: skip
    assert read["net.ping"].value is not None and read["net.ping.host"].value == "192.0.2.1"
    assert read["net.ping.2"].value is None and read["net.ping.2.lost"].value == 100
    assert read["net.ping.2.host"].value == "192.168.1.1"
    assert read["net.ping.4.host"].value == "example.org"
    provider.close()


def test_a_ping_module_shows_the_host_it_is_given():
    from libre_panel.theme.modules import ping_key

    assert ping_key("") == "net.ping" and ping_key("1") == "net.ping"
    assert ping_key("3") == "net.ping.3" and ping_key("9") == "net.ping"
