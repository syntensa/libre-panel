"""rust/app (docs/RUST.md, R5, R6 and R9): `libre-panel`, the program of Rust, run as a
person runs it. It finds the app's files, reads config.toml, draws a theme's frame of readings
and writes it where it is told; its frames are the Rust renderer's, which test_rust_render.py
holds to the Python renderer's pixels. And with a simulated TURZX panel that Libre Panel's
engine has (tests/fake_panel.py behind its socket): its video with its start, its bit rate,
its stop and a hung decoder's restart, and its single pictures."""

from __future__ import annotations

import itertools
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from rust_check import (
    find,
    pillow_libfreetype,
    pillow_libharfbuzz,
    pillow_libjpeg,
    pillow_libwebp,
    python_tzdata,
)

import libre_panel

DATA = Path(libre_panel.__file__).resolve().parent
BUILTIN_THEMES_DIR = DATA / "themes"


def _program() -> Path:
    check = find()
    if check is None:
        pytest.skip("rust/ is not built")
    program = check.with_name("libre-panel.exe" if os.name == "nt" else "libre-panel")
    if not program.is_file():
        pytest.skip("libre-panel is not built")
    return program


def _libraries() -> dict[str, str]:
    """The libraries of C the comparisons use (rust_check.run), named to the program."""
    libraries = {"LP_LIBJPEG": pillow_libjpeg(), "LP_LIBFREETYPE": pillow_libfreetype(),
                 "LP_LIBHARFBUZZ": pillow_libharfbuzz(), "LP_TZDATA": python_tzdata()}  # fmt: skip
    return {**pillow_libwebp(), **{n: f for n, f in libraries.items() if f is not None}}


def _run(home: Path, *args: str, **more: str) -> subprocess.CompletedProcess[str]:
    """The program run with its settings in ``home`` and the app's files the Python app's
    (``more``: what else its environment has)."""
    env = {**os.environ, "LIBRE_PANEL_HOME": str(home), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), **more}  # fmt: skip
    return subprocess.run([str(_program()), *args], env=env, capture_output=True,
                          encoding="utf-8", timeout=120, check=False)  # fmt: skip


# (letters want FreeType, loaded where the program runs: Pillow's own, which its Windows
# wheels have built in; docs/RUST.md, R2)
draws = pytest.mark.skipif(pillow_libfreetype() is None, reason="needs Pillow's FreeType")


@draws
def test_it_draws_a_theme_into_a_png_file(tmp_path: Path) -> None:
    out = tmp_path / "frames" / "frame.png"
    done = _run(tmp_path, "render", "--demo", "--theme", "lumen", "--output", str(out))
    assert done.returncode == 0, done.stderr
    assert "theme lumen" in done.stderr
    with Image.open(out) as frame:
        assert frame.size == (1920, 480) and frame.mode == "RGB"
        assert len(frame.getcolors(1 << 24)) > 500  # (a picture, not one colour)
    assert not out.with_name("frame.png.tmp").exists()


@draws
def test_it_follows_config_toml(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        'theme = "terra"\nfps = 5\n[device]\ndriver = "virtual"\noutput = "panel.png"\n'
        '[sensors]\nproviders = ["psutil", "nope"]\n',
        encoding="utf-8",
    )
    said = _run(tmp_path, "config")
    assert said.returncode == 0, said.stderr
    assert "theme: terra" in said.stdout and "sensors: psutil, nope" in said.stdout
    done = _run(tmp_path, "run", "--frames", "3")
    assert done.returncode == 0, done.stderr
    assert "theme terra" in done.stderr
    assert "not in this build yet: nope" in done.stderr
    with Image.open(tmp_path / "panel.png") as frame:  # (relative: in the settings folder)
        assert frame.mode == "RGB" and frame.width > frame.height


class Rig:
    """A simulated TURZX panel (fake_panel.py, behind a socket) that Libre Panel's engine has,
    for the program: config.toml in a settings folder of its own, and the program run on it."""

    def __init__(self, home: Path, pid: int = 0x0092, native: tuple[int, int] = (480, 1920)):
        pytest.importorskip("usb.core")  # (the simulated panel speaks pyusb's errors)
        from fake_panel import FakePanel
        from panel_socket import PanelSocket

        from libre_panel.devices import h264
        from libre_panel.devices.engine import can_send

        self.engine = h264.find_engine()
        if not self.engine or not can_send(self.engine):
            pytest.skip("needs libre-panel-engine with its panel mode (cargo build --release)")
        self.home = home
        self.fake = FakePanel(pid=pid, native=native)
        self.sock = PanelSocket(self.fake)
        # (whether the panel is on the bus, as a file the program looks for)
        self.present = home / "panel-present"
        self._watching = True
        self._watcher = threading.Thread(target=self._watch, daemon=True)
        self._watcher.start()

    def _watch(self) -> None:
        while self._watching:
            if self.fake.present:
                self.present.touch()
            else:
                self.present.unlink(missing_ok=True)
            time.sleep(0.02)

    def env(self, **more: str) -> dict[str, str]:
        return {"LP_ENGINE_LINK": self.sock.link, "LIBRE_PANEL_ENGINE": self.engine,
                "LP_ENGINE_PRESENT": str(self.present), **more}  # fmt: skip

    def config(self, text: str) -> None:
        (self.home / "config.toml").write_text(text, encoding="utf-8")

    def run(self, *args: str, **more: str) -> subprocess.CompletedProcess[str]:
        return _run(self.home, *args, **self.env(**more))

    def close(self) -> None:
        self._watching = False
        self._watcher.join(1)
        self.sock.close()


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    made = Rig(tmp_path)
    yield made
    made.close()


VIDEO = (
    'theme = "lumen"\n[device]\ndriver = "turzx"\nmodel = "turing-9.2-usb"\n'
    'brightness = 70\n[video]\nmode = "on"\nfps = 25\ndevice_fps = 30\n'
    'local_clip = "usr/data/clip.h264"\n'
)


@draws
def test_it_shows_video_on_a_turzx_panel_through_the_engine(rig: Rig) -> None:
    """The video's start as devices/turzx_video.py's VideoSession sends it, pictures of the
    frames, and its stop, the last frame left on the panel as a PNG."""
    from libre_panel.devices.turzx_usb import brightness_arg

    rig.config(VIDEO)
    done = rig.run("run", "--demo", "--frames", "30")
    fake = rig.fake
    assert done.returncode == 0, done.stderr
    assert rig.sock.errors == []
    assert 'showing on Turing 9.2" (firmware turzx_00), video 25 fps' in done.stderr
    # (the 9.2" shows every stream as full range: its video is made so)
    assert "video: encoding with the engine, profile high, full range, 2.0M" in done.stderr
    # the start, command by command, the brightness the loop sets, pictures, then the stop
    assert fake.commands[:9] == [10, 110, 111, 112, 14, 102, 15, 17, 14], done.stderr
    assert fake.clip_names == ["usr/data/clip.h264"]
    assert fake.brightness == [brightness_arg(70)] * 2
    assert fake.fps_log == [30, 30]  # (the player's rate, and the power-on rate given back)
    # (pictures of H.264, Annex B: each begins with a start code)
    assert fake.commands.count(121) >= 1 and fake.blocks[0][:4] == b"\x00\x00\x00\x01"
    assert fake.commands[-3:] == [123, 15, 102]
    layer, last = fake.frames
    assert layer.size == (480, 1920) and layer.getextrema()[3] == (0, 0)
    # (the last frame: the theme's, as the panel lies, nothing see-through)
    assert last.getextrema()[3] == (255, 255) and len(last.convert("RGB").getcolors(1 << 24)) > 500
    assert "video: the engine sent" in done.stderr and "blocks" in done.stderr


@draws
def test_the_bit_rate_auto_goes_on_where_the_last_run_got_to_and_follows_the_panel(
    rig: Rig,
) -> None:
    """video-state.json's rate is where "auto" begins; a panel that takes its blocks slowly
    gets a step less, into the running stream, and that is kept for the next run."""
    state = rig.home / "video-state.json"
    state.write_text(json.dumps({"rates": {"turing-9.2-usb 146 1920x480": 8.0,
                                           "other 1 2x2": 3.0}, "cards": {"kept": True}}),
                     encoding="utf-8")  # fmt: skip
    rig.config(VIDEO)
    rig.fake.block_delay = 0.05  # (each block longer than a frame at 50 a second)
    done = rig.run("run", "--demo", "--frames", "260")
    assert done.returncode == 0, done.stderr
    assert rig.sock.errors == []
    assert "full range, 8.0M" in done.stderr
    assert "video: bit rate 8.0M -> 6.0M (the panel takes less than it is sent)" in done.stderr
    kept = json.loads(state.read_text(encoding="utf-8"))
    assert kept["rates"]["turing-9.2-usb 146 1920x480"] < 8.0
    assert kept["rates"]["other 1 2x2"] == 3.0 and kept["cards"] == {"kept": True}
    assert rig.fake.commands.count(110) == 1  # (no new start for it: the same stream)


@draws
def test_single_pictures_go_as_pngs_where_no_video_is_shown(rig: Rig) -> None:
    rig.config(VIDEO.replace('mode = "on"', 'mode = "off"'))
    done = rig.run("run", "--demo", "--frames", "3")
    assert done.returncode == 0, done.stderr
    assert rig.sock.errors == []
    assert "single pictures through the engine" in done.stderr
    assert rig.fake.commands == [10, 14, 102, 102, 102], done.stderr
    for frame in rig.fake.frames:
        assert frame.size == (480, 1920) and frame.getextrema()[3] == (255, 255)
        assert len(frame.convert("RGB").getcolors(1 << 24)) > 500


@draws
def test_the_5_2_takes_its_pictures_as_jpegs_under_a_clear_layer(tmp_path: Path) -> None:
    rig = Rig(tmp_path, pid=0x0050, native=(720, 1280))
    try:
        rig.config(VIDEO.replace("turing-9.2-usb", "turing-5.2-usb")
                   .replace('mode = "on"', 'mode = "auto"'))  # fmt: skip
        done = rig.run("run", "--demo", "--frames", "3")
        assert done.returncode == 0, done.stderr
        assert rig.sock.errors == []
        # (auto: no video on a model it is not proven on; its stills as JPEGs)
        assert "video mode off: the video mode is not proven on the" in done.stderr
        assert rig.fake.commands == [10, 14, 102, 101, 101, 101], done.stderr
        assert rig.fake.frames[0].getextrema()[3] == (0, 0)
        assert [jpeg.size for jpeg in rig.fake.jpegs] == [(720, 1280)] * 3
    finally:
        rig.close()


@draws
def test_a_hung_decoder_restarts_the_panel_and_the_video_starts_again(rig: Rig) -> None:
    """The engine finds the decoder hung (slow blocks, a queue stuck high); the program
    restarts the panel (11), waits for it to leave the bus and to come back, and starts the
    video anew."""
    rig.config(VIDEO)
    rig.fake.restart_away_s = 3.0
    env = {**os.environ, "LIBRE_PANEL_HOME": str(rig.home), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), **rig.env(LP_ENGINE_ARGS="--hang-blocks 4")}  # fmt: skip
    said = rig.home / "said.txt"
    with said.open("w", encoding="utf-8") as out:
        program = subprocess.Popen([str(_program()), "run", "--demo"], env=env, stderr=out)
        try:
            fake = rig.fake

            def wait_for(what: Any, seconds: float) -> None:
                end = time.monotonic() + seconds
                while not what() and time.monotonic() < end and program.poll() is None:
                    time.sleep(0.02)
                assert what(), said.read_text(encoding="utf-8")

            wait_for(lambda: fake.commands.count(121) >= 5, 30)
            fake.block_delay = 0.25  # (a hung decoder takes each block slowly)
            fake.hung_depths = itertools.repeat(24)
            wait_for(lambda: 11 in fake.commands, 60)
            hung_at = fake.commands.index(11)
            wait_for(lambda: fake.commands.count(110) == 2, 60)
            fake.block_delay = 0.0
            before = fake.commands.count(121)
            wait_for(lambda: fake.commands.count(121) >= before + 5, 30)
        finally:
            program.send_signal(signal.SIGINT)
            program.wait(30)
    text = said.read_text(encoding="utf-8")
    assert program.returncode == 0, text
    assert rig.sock.errors == []
    assert fake.commands.count(11) == 1 and fake.restarts == 1
    assert 123 not in fake.commands[:hung_at]  # nothing more is sent to a hung decoder
    assert "restarting the panel (1 of 3 in a row)" in text
    assert "the panel is back after its restart" in text
    assert fake.commands[-3:] == [123, 15, 102]  # (and the stop at its end, as ever)


def test_a_config_that_is_wrong_is_said_in_config_py_s_words(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("fps = 99\n", encoding="utf-8")
    done = _run(tmp_path, "run", "--frames", "1")
    assert done.returncode == 1
    assert "fps: must be between 1 and 60" in done.stderr


def test_it_lists_what_it_knows(tmp_path: Path) -> None:
    themes = _run(tmp_path, "themes").stdout.split()
    assert themes == sorted(p.name for p in BUILTIN_THEMES_DIR.iterdir()
                            if (p / "theme.json").is_file())  # fmt: skip
    models = _run(tmp_path, "models").stdout
    assert "majesty-9.16" in models
    assert _run(tmp_path, "nope").returncode == 1
    assert "libre-panel run" in _run(tmp_path).stdout


def test_it_lists_librehardwaremonitor_s_readings_as_the_python_app_names_them(
    tmp_path: Path,
) -> None:
    """`libre-panel sensors` with LibreHardwareMonitor's web server as the source: the
    readings the Python app makes of the same tree, in the Python app's lines."""
    import http.server
    import threading

    from libre_panel.sensors import librehardwaremonitor as lhm

    def sensor(sensor_id: str, kind: str, text: str, value: str, **more: object) -> dict:
        return {"Text": text, "SensorId": sensor_id, "Type": kind, "Value": value, **more}

    tree = {"Text": "Sensor", "Children": [{"Text": "PC", "Children": [
        {"Text": "AMD Ryzen 7 7800X3D 8-Core Processor", "HardwareId": "/amdcpu/0", "Children": [
            sensor("/amdcpu/0/temperature/2", "Temperature", "Core (Tctl/Tdie)", "61,5 °C"),
            sensor("/amdcpu/0/load/0", "Load", "CPU Total", "12,3 %"),
            sensor("/amdcpu/0/clock/1", "Clock", "Core #1", "4875,0 MHz"),
            sensor("/amdcpu/0/clock/2", "Clock", "Core #2", "5012,5 MHz"),
            sensor("/amdcpu/0/power/0", "Power", "Package", "48,7 W")]},
        {"Text": "NVIDIA GeForce RTX 4080", "HardwareId": "/gpu-nvidia/0", "Children": [
            sensor("/gpu-nvidia/0/temperature/0", "Temperature", "GPU Core", "54,0 °C"),
            sensor("/gpu-nvidia/0/smalldata/1", "SmallData", "GPU Memory Used", "3120,0 MB"),
            sensor("/gpu-nvidia/0/fan/1", "Fan", "GPU Fan 1", "1240 RPM")]},
        {"Text": "Ethernet", "HardwareId": "/nic/%7B1%7D", "Children": [
            sensor("/nic/%7B1%7D/throughput/7", "Throughput", "Download Speed", "1,2 MB/s",
                   RawValue=1258291.2)]},
        {"Text": "DIMM 1", "HardwareId": "/memory/dimm/0", "Children": [
            sensor("/memory/dimm/0/temperature/0", "Temperature", "DIMM #1", "41,2 °C")]},
    ]}]}  # fmt: skip
    body = json.dumps(tree).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the handler's name for GET
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        (tmp_path / "config.toml").write_text(
            '[sensors]\nproviders = ["librehardwaremonitor"]\n[sensors.librehardwaremonitor]\n'
            f'mode = "webserver"\nurl = "http://127.0.0.1:{server.server_port}/data.json"\n'
            "poll_seconds = 0.2\n",
            encoding="utf-8",
        )
        done = _run(tmp_path, "sensors")
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr
    readings = lhm.readings_from_tree(tree)
    lines = []
    for key in sorted(readings):
        r = readings[key]
        value = f"{r.value:.2f}" if isinstance(r.value, float) else str(r.value)
        lines.append(f"{key:<40} {value:>14} {r.unit:<5} {r.label}")
    assert done.stdout.splitlines() == lines
    assert any(line.startswith("cpu.name") and "Ryzen 7 7800X3D" in line for line in lines)
    assert any(line.startswith("mem.temp") for line in lines)


@pytest.mark.skipif(not sys.platform.startswith(("linux", "win")), reason="no load source")
def test_the_load_source_measures_the_processor(tmp_path: Path) -> None:
    """`[sensors] providers = ["load"]`: the processor's load, measured by the source's own
    thread while something reads it (`sensors` asks for it as a theme would)."""
    (tmp_path / "config.toml").write_text('[sensors]\nproviders = ["load"]\n', encoding="utf-8")
    done = _run(tmp_path, "sensors")
    assert done.returncode == 0, done.stderr
    [line] = [line for line in done.stdout.splitlines() if line.startswith("cpu.load ")]
    key, value, unit, *label = line.split()
    assert 0.0 <= float(value) <= 100.0 and unit == "%" and label == ["CPU", "load"]


def test_the_computer_s_own_readings_are_those_of_the_python_source(tmp_path: Path) -> None:
    """`[sensors] providers = ["psutil", ...]`: the same keys, units and labels as
    sensors/psutil_provider.py gives on this computer, and the same values where they stand
    still (the drives, the memory in all, the threads, the address)."""
    pytest.importorskip("psutil")
    from libre_panel.sensors.psutil_provider import PsutilProvider

    (tmp_path / "config.toml").write_text(
        '[sensors]\nproviders = ["psutil", "nope"]\n', encoding="utf-8"
    )
    provider = PsutilProvider({})
    try:
        provider.read()
        done = _run(tmp_path, "sensors")
        wanted = provider.read()
    finally:
        provider.close()
    assert done.returncode == 0, done.stderr
    ours = {}
    for line in done.stdout.splitlines():
        key, value = line[:40].strip(), line[41:55].strip()
        ours[key] = (value, line[56:61].strip(), line[62:])
    assert sorted(ours) == sorted(wanted), set(ours) ^ set(wanted)
    still = ("cpu.cores", "mem.total", "disk.total", "net.ip")
    for key, reading in wanted.items():
        value, unit, label = ours[key]
        assert (unit, label) == (reading.unit, reading.label), key
        if key in still or (key.startswith("disk.") and key.endswith((".name", ".total"))):
            said = reading.value
            assert value == (f"{said:.2f}" if isinstance(said, float) else str(said)), key
    assert {"cpu.core.1.load", "disk.1.name", "net.today.down", "sys.uptime"} <= set(ours)


def test_programs_ping_and_link_are_read_while_a_theme_shows_them(tmp_path: Path) -> None:
    """What the psutil source reads only while something shows it: the programs that use the
    most processor time and memory, the time a connection to a host takes, how the PC is
    connected; under the keys sensors/psutil_provider.py gives them."""
    import socket

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(8)
        port = server.getsockname()[1]
        (tmp_path / "config.toml").write_text(
            '[sensors]\nproviders = ["psutil", "nope"]\n[sensors.psutil]\n'
            f'ping = ["127.0.0.1:{port}", "127.0.0.1:1", "[::1]"]\n',
            encoding="utf-8",
        )
        done = _run(tmp_path, "sensors", LP_SENSORS_WANT="proc.*,net.ping*,net.link*")
    assert done.returncode == 0, done.stderr
    ours = {line[:40].strip(): (line[41:55].strip(), line[56:61].strip(), line[62:])
            for line in done.stdout.splitlines()}  # fmt: skip
    assert ours["net.ping.host"] == ("127.0.0.1", "", "Ping host")
    value, unit, label = ours["net.ping"]
    assert 0.0 <= float(value) < 2000.0 and (unit, label) == ("ms", "Ping 127.0.0.1")
    assert ours["net.ping.lost"] == ("0", "%", "Ping lost")
    # (nothing listens on port 1: no answer, all of its tries lost)
    assert ours["net.ping.2"] == ("None", "ms", "Ping")
    assert ours["net.ping.2.lost"] == ("100", "%", "Ping lost")
    assert ours["net.ping.3.host"] == ("::1", "", "Ping host")
    rows = {line[:40].strip(): line[41:] for line in done.stdout.splitlines()}
    for sort in ("cpu", "mem"):
        # (a program's name may be longer than the column of the values: its label is whole)
        name = rows[f"proc.{sort}.1.name"].rsplit(" " * 7, 1)[-1]
        assert name and rows[f"proc.{sort}.1.name"] == f"{name:>14} {'':<5} {name}"
        value, unit, label = ours[f"proc.{sort}.1.value"]
        assert 0.0 <= float(value) and (unit, label) == ("%", name)
    assert float(ours["proc.mem.1.value"][0]) >= float(ours["proc.mem.2.value"][0])
    if "net.link" in ours:  # (a default route: how the PC is connected)
        assert ours["net.link.kind"][0] in ("lan", "wlan")
        assert ours["net.link.text"][0].startswith(ours["net.link"][0])
