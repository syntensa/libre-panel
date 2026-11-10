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


NAMES = ("cpu.name", "gpu.name", "mem.name", "mem.speed")  # what sensors/hardware.py gives


def _lines(done: subprocess.CompletedProcess[str]) -> list[str]:
    """What `sensors` listed, without the sky's readings (always on, and a test of their
    own)."""
    return [line for line in done.stdout.splitlines() if not line.startswith(("moon.", "sun."))]


def _named(readings: dict, cpu: str = "", prefer: str = "") -> dict:
    """``readings`` with the names of this computer's parts as the Python app adds them
    (sensors/hardware.py, always on): its names win, ``cpu`` is `[hardware] cpu_name`."""
    from libre_panel.sensors import hardware

    # (the tests' computer is empty, conftest.py: this one asks the real one, as the program does)
    parts = (hardware.windows_cpu_name, hardware.windows_adapters, hardware.windows_modules)
    if sys.platform != "win32":
        parts = (hardware.linux_cpu_name, hardware.linux_adapters, hardware.linux_modules)
    provider = hardware.HardwareProvider()
    provider._found = hardware.Found(
        hardware._try(parts[0], "") or hardware._try(hardware._last_resort, ""),
        tuple(hardware._try(parts[1], [])),
        tuple(hardware._try(parts[2], [])),
        hardware._try(hardware._visible_memory, 0),
    )
    provider._custom, provider._prefer = {**provider._custom, "cpu": cpu}, prefer
    names = provider._make()
    return {**readings, **{k: r for k, r in names.items()
                           if k not in readings or k in provider.overrides}}  # fmt: skip


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
            'poll_seconds = 0.2\n[hardware]\ncpu_name = "My own processor"\n',
            encoding="utf-8",
        )
        done = _run(tmp_path, "sensors")
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr
    # (the names of this computer's parts win over LibreHardwareMonitor's, and the user's own)
    readings = _named(lhm.readings_from_tree(tree), cpu="My own processor")
    lines = []
    for key in sorted(readings):
        r = readings[key]
        value = f"{r.value:.2f}" if isinstance(r.value, float) else str(r.value)
        lines.append(f"{key:<40} {value:>14} {r.unit:<5} {r.label}")
    assert _lines(done) == lines
    assert any(line.startswith("cpu.name") and "My own processor" in line for line in lines)
    assert any(line.startswith("mem.temp") for line in lines)


@pytest.mark.skipif(not sys.platform.startswith(("linux", "win")), reason="no load source")
def test_the_load_source_measures_the_processor(tmp_path: Path) -> None:
    """`[sensors] providers = ["load"]`: the processor's load, measured by the source's own
    thread while something reads it (`sensors` asks for it as a theme would)."""
    (tmp_path / "config.toml").write_text('[sensors]\nproviders = ["load"]\n', encoding="utf-8")
    done = _run(tmp_path, "sensors")
    assert done.returncode == 0, done.stderr
    [line] = [line for line in _lines(done) if line.startswith("cpu.load ")]
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
        wanted = _named(provider.read())
    finally:
        provider.close()
    assert done.returncode == 0, done.stderr
    ours = {line[:40].strip(): line[41:] for line in _lines(done)}
    assert sorted(ours) == sorted(wanted), set(ours) ^ set(wanted)
    still = ("cpu.cores", "mem.total", "disk.total", "net.ip", "cpu.name", "gpu.name", "mem.name",
             "mem.speed")  # fmt: skip
    for key, reading in wanted.items():
        # (a value wider than its column moves the unit and the label to the right)
        end = f" {reading.unit:<5} {reading.label}"
        assert ours[key].endswith(end), (key, ours[key])
        value = ours[key][: -len(end)].strip()
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
            for line in _lines(done)}  # fmt: skip
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


@pytest.mark.skipif(os.name == "nt", reason="the tools of Linux")
def test_the_volume_is_read_from_the_tool_on_the_path(tmp_path: Path) -> None:
    """`[sensors] providers = ["audio"]`: the volume as wpctl says it, while something reads
    it (`sensors` asks for audio.* as a theme would)."""
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "wpctl").write_text(
        '#!/bin/sh\n[ "$2" = "@DEFAULT_AUDIO_SINK@" ] && echo "Volume: 0.37" || '
        'echo "Volume: 0.80 [MUTED]"\n',
        encoding="utf-8",
    )
    (tools / "wpctl").chmod(0o755)
    (tmp_path / "config.toml").write_text('[sensors]\nproviders = ["audio"]\n', encoding="utf-8")
    path = f"{tools}{os.pathsep}{os.environ.get('PATH', '')}"
    done = _run(tmp_path, "sensors", PATH=path, LP_SENSORS_WANT="audio.mic.*")
    assert done.returncode == 0, done.stderr
    ours = {line[:40].strip(): (line[41:55].strip(), line[56:61].strip(), line[62:])
            for line in _lines(done)}  # fmt: skip
    ours = {key: said for key, said in ours.items() if key.startswith("audio.")}
    assert ours == {
        "audio.volume": ("37.00", "%", "Volume"), "audio.muted": ("0", "", "Muted"),
        "audio.mic.volume": ("80.00", "%", "Microphone level"),
        "audio.mic.muted": ("1", "", "Microphone muted"),
    }  # fmt: skip
    assert "audio: the volume is read by polling" in done.stderr


@pytest.mark.skipif(os.name == "nt", reason="a stand-in program written for the shell")
def test_a_games_frames_come_from_the_presentmon_named(tmp_path: Path) -> None:
    """`[sensors] providers = ["presentmon"]` with a `path`: the program is started as
    PresentMon is (frames of every program to its output, the one session), its rows are
    counted while something reads game.*, and it is ended with the program."""
    stand_in = tmp_path / "PresentMon"
    stand_in.write_text(
        "#!/bin/sh\n"
        f'echo "$$ $*" > "{tmp_path}/started"\n'
        'echo "Application,ProcessID,MsBetweenPresents,MsGPUBusy"\n'
        'while :; do echo "Game.exe,7,10.0,4.0"; echo "dwm.exe,4,16.6,1.0"; sleep 0.01; done\n',
        encoding="utf-8",
    )
    stand_in.chmod(0o755)
    (tmp_path / "config.toml").write_text(
        f'[sensors]\nproviders = ["presentmon"]\n[sensors.presentmon]\npath = "{stand_in}"\n',
        encoding="utf-8",
    )
    done = _run(tmp_path, "sensors", LP_SENSORS_WANT="game.fps")
    assert done.returncode == 0, done.stderr
    ours = {line[:40].strip(): (line[41:55].strip(), line[56:61].strip(), line[62:])
            for line in _lines(done)}  # fmt: skip
    assert ours["game.fps"] == ("100.00", "fps", "Frames per second"), ours
    assert ours["game.frametime"][:2] == ("10.00", "ms")
    assert ours["game.app"][0] == "Game"
    pid, _, arguments = (tmp_path / "started").read_text(encoding="utf-8").strip().partition(" ")
    assert arguments == (
        "--output_stdout --no_console_stats --session_name LibrePanel --stop_existing_session"
    )
    assert not Path(f"/proc/{pid}").exists()  # (ended with the program that started it)

    # Nothing reads game.*: PresentMon is not started at all.
    (tmp_path / "started").unlink()
    done = _run(tmp_path, "sensors")
    assert done.returncode == 0 and not (tmp_path / "started").exists()
    assert not any(key.startswith("game.") for key in done.stdout.split())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the kernel's own files")
def test_a_linux_pcs_sensors_are_those_of_the_python_sources(tmp_path: Path) -> None:
    """`[sensors] providers = ["linux", "psutil", ...]`: the keys, units and labels the Linux
    source and the psutil source give together on this computer (the chips' temperatures and
    fans from the Linux source alone, as the Python app has it)."""
    pytest.importorskip("psutil")
    from libre_panel.sensors.base import Reading
    from libre_panel.sensors.linux import LinuxProvider
    from libre_panel.sensors.psutil_provider import PsutilProvider

    (tmp_path / "config.toml").write_text(
        '[sensors]\nproviders = ["linux", "psutil", "nope"]\n', encoding="utf-8"
    )
    linux, provider = LinuxProvider({}), PsutilProvider({})
    provider.hwmon = False
    try:
        linux.read(), provider.read()
        # the drives' and the memory's temperatures are read by a thread of their own every
        # few seconds, and the program waits for its first readings: a round is made here
        linux.read_slow()
        done = _run(tmp_path, "sensors")
        time.sleep(0.3)  # (a power needs two readings of its counter, a while apart)
        # (the round before is dropped where the program took longer than SLOW_FRESH: CI)
        linux.read_slow()
        read = {**provider.read(), **linux.read()}
        # a drive that did not answer this round (its own thread may have asked it at the
        # same moment) is asked again, as the program's thread does a few seconds later:
        # CI's NVMe drive often does not answer for a while
        for item in linux._plan.slow:
            for _ in range(30):
                value = item.key not in read and item.read()
                if value is None:
                    time.sleep(0.2)
                    continue
                if value is not False:
                    name = linux._plan.names[item.key]
                    read[item.key] = Reading(item.key, value, item.unit, name)
                break
        wanted = _named(read)
        slow = {item.key: item for item in linux._plan.slow}
    finally:
        linux.close()
        provider.close()
    assert done.returncode == 0, done.stderr
    # (a value longer than its column, as a processor's long name, moves the rest along:
    # each line ends in its unit and its label)
    ours = {line[:40].strip(): line for line in _lines(done)}
    # a drive that did not answer the program's round is asked again, as above (and as the
    # program's own thread does a few seconds later): CI's NVMe drive often does not answer,
    # and the program asks it once in its short life
    for _ in range(12):
        missing = {key for key in wanted if key not in ours} & set(slow)
        if not missing:
            break
        again = _run(tmp_path, "sensors")
        assert again.returncode == 0, again.stderr
        ours.update({line[:40].strip(): line for line in _lines(again)
                     if line[:40].strip() in missing})  # fmt: skip
    # and one the program read that Python's side did not is asked again on Python's side
    for key in (set(ours) - set(wanted)) & set(slow):
        for _ in range(30):
            value = slow[key].read()
            if value is not None:
                wanted[key] = Reading(key, value, slow[key].unit, linux._plan.names[key])
                break
            time.sleep(0.2)
    # A drive that answers neither side's repeated asking for a while (CI's NVMe drive) is
    # left out of the comparison; every other reading is compared, the drives' among them.
    silent = (set(ours) ^ set(wanted)) & set(slow)
    for key in silent:
        ours.pop(key, None)
        wanted.pop(key, None)
    assert sorted(ours) == sorted(wanted)
    for key, r in wanted.items():
        assert ours[key].endswith(f" {r.unit:<5} {r.label}"), (key, ours[key])


@pytest.mark.skipif(os.name == "nt", reason="a machine laid out as Linux has it")
def test_a_machine_laid_out_in_a_folder_is_read_as_the_python_source_reads_it(
    tmp_path: Path,
) -> None:
    """The Linux source in the program: a processor's chip, the mainboard's fans, a drive and
    an AMD card with its hwmon chip, read from a folder; every reading as sensors/linux.py
    gives it."""
    from libre_panel.sensors.linux import LinuxProvider

    root = tmp_path / "pc"
    hwmon, card = "sys/class/hwmon/", "sys/class/drm/card1/device/"
    files = {
        hwmon + "hwmon0/name": "k10temp", hwmon + "hwmon0/temp1_input": "61500",
        hwmon + "hwmon0/temp1_label": "Tctl", hwmon + "hwmon1/name": "nct6798",
        hwmon + "hwmon1/fan1_input": "840", hwmon + "hwmon1/fan2_input": "1320",
        hwmon + "hwmon1/fan2_label": "CPU Fan", hwmon + "hwmon1/temp1_input": "0",
        hwmon + "hwmon2/name": "nvme", hwmon + "hwmon2/temp1_input": "38850",
        hwmon + "hwmon2/temp1_label": "Composite",
        hwmon + "hwmon2/device/model": "Samsung SSD 990 PRO 2TB",
        hwmon + "hwmon3/name": "amdgpu", hwmon + "hwmon3/temp1_input": "54000",
        hwmon + "hwmon3/temp1_label": "edge", hwmon + "hwmon3/temp2_input": "66000",
        hwmon + "hwmon3/temp2_label": "junction", hwmon + "hwmon3/fan1_input": "912",
        hwmon + "hwmon3/pwm1": "97", hwmon + "hwmon3/power1_average": "187000000",
        hwmon + "hwmon3/freq1_input": "2400000000", hwmon + "hwmon3/freq1_label": "sclk",
        card + "vendor": "0x1002", card + "device": "0x744c",
        card + "uevent": "DRIVER=amdgpu\nPCI_SLOT_NAME=0000:03:00.0",
        card + "gpu_busy_percent": "37", card + "mem_info_vram_total": str(24 * 2**30),
        card + "mem_info_vram_used": str(3 * 2**30), card + "hwmon/hwmon3/name": "amdgpu",
    }  # fmt: skip
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text + "\n", encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        '[sensors]\nproviders = ["linux", "hardware"]\n', encoding="utf-8"
    )
    done = _run(tmp_path, "sensors", LP_LINUX_ROOT=str(root))
    assert done.returncode == 0, done.stderr
    provider = LinuxProvider({}, root=root)
    try:
        wanted = provider.read()
    finally:
        provider.close()
    lines = []
    for key in sorted(wanted):
        r = wanted[key]
        lines.append(f"{key:<40} {r.value:>14.2f} {r.unit:<5} {r.label}")
    ours = [line for line in _lines(done) if not line.startswith(NAMES)]
    assert ours == lines
    assert {"cpu.temp", "gpu.temp", "gpu.hotspot", "gpu.load", "gpu.power", "gpu.mem.used",
            "fan.lpc.fan_2", "temp.nvme.composite"} <= set(wanted)  # fmt: skip


def test_the_sun_and_the_moon_are_always_there(tmp_path: Path) -> None:
    """The sky's source is on without being named: the moon everywhere, the sun for the
    place `[weather]` has; the readings sensors/sky.py gives at that moment."""
    from libre_panel.sensors import sky

    (tmp_path / "config.toml").write_text(
        '[sensors]\nproviders = ["nope"]\n[weather]\nlatitude = 52.52\nlongitude = 13.405\n',
        encoding="utf-8",
    )
    before = sky.sky_readings(time.time(), 52.52, 13.405)
    done = _run(tmp_path, "sensors")
    after = sky.sky_readings(time.time(), 52.52, 13.405)
    assert done.returncode == 0, done.stderr
    ours = {line[:40].strip(): (line[41:55].strip(), line[56:61].strip(), line[62:])
            for line in done.stdout.splitlines() if line.startswith(("moon.", "sun."))}  # fmt: skip
    assert list(ours) == sorted(before) and {"sun.rise", "sun.set", "moon.phase"} <= set(ours)
    for key, (value, unit, label) in ours.items():
        assert (unit, label) == (before[key].unit, before[key].label), key
        said = [f"{r.value:.2f}" if isinstance(r.value, float) else str(r.value)
                for r in (before[key], after[key])]  # fmt: skip
        if key != "sun.elevation":  # (it moves by the second)
            assert value in said, key


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="MangoHud is Linux's")
def test_a_games_frames_come_from_mangohuds_log_as_it_grows(tmp_path: Path) -> None:
    """The MangoHud source is on without being named: the newest log of the folder
    `[sensors.mangohud]` names is read while something shows game.*, from where it was when
    the reading began."""
    folder = tmp_path / "mangologs"
    folder.mkdir()
    log = folder / "Stray_2026-10-21_19-04-33.csv"
    log.write_text("os,cpu\nLinux,Ryzen\nfps,frametime,cpu_load\n100,999.0,5\n", encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        f'[sensors]\nproviders = ["nope"]\n[sensors.mangohud]\nfolder = "{folder}"\n',
        encoding="utf-8",
    )
    stop = threading.Event()

    def game() -> None:
        with log.open("a", encoding="utf-8") as file:
            while not stop.wait(0.01):
                file.write("100,10.0,5\n")
                file.flush()

    writer = threading.Thread(target=game)
    writer.start()
    try:
        done = _run(tmp_path, "sensors", LP_SENSORS_WANT="game.fps")
    finally:
        stop.set()
        writer.join()
    assert done.returncode == 0, done.stderr
    ours = {line[:40].strip(): (line[41:55].strip(), line[56:61].strip(), line[62:])
            for line in _lines(done)}  # fmt: skip
    assert ours["game.app"][0] == "Stray"
    assert ours["game.frametime"][:2] == ("10.00", "ms")  # (not the 999 ms logged before)
    assert ours["game.fps"] == ("100.00", "fps", "Frames per second")
    assert "mangohud: reading the log of Stray" in done.stderr


@pytest.mark.skipif(os.name == "nt", reason="stand-in tools written for the shell")
def test_what_plays_is_read_from_playerctl_or_else_from_busctl(tmp_path: Path) -> None:
    """The media source is on without being named: what plays as playerctl says it, and where
    that is not installed as busctl gives the players of the session's D-Bus (the one that
    plays, not the one that is paused); read only while something shows media.*."""
    from libre_panel.sensors import media

    tools = tmp_path / "bin"
    tools.mkdir()
    line = "\x1f".join(["Playing", "Daft Punk", "Voyager", "Discovery", "61500000",
                        "227000000", "", "spotify"])  # fmt: skip
    (tools / "playerctl").write_text(
        f'#!/bin/sh\n[ "$1" = "metadata" ] && printf \'%s\\n\' "{line}"\n', encoding="utf-8"
    )
    (tools / "playerctl").chmod(0o755)
    (tmp_path / "config.toml").write_text('[sensors]\nproviders = ["nope"]\n', encoding="utf-8")

    def listed(done: subprocess.CompletedProcess[str]) -> dict[str, tuple[str, str, str]]:
        assert done.returncode == 0, done.stderr
        return {line[:40].strip(): (line[41:55].strip(), line[56:61].strip(), line[62:])
                for line in _lines(done) if line.startswith("media.")}  # fmt: skip

    ours = listed(_run(tmp_path, "sensors", PATH=str(tools), LP_SENSORS_WANT="media.title"))
    wanted = media.parse_playerctl(line + "\n", 0.0).readings(0.0)
    assert list(ours) == sorted(wanted)
    for key in ("media.title", "media.artist", "media.album", "media.source", "media.state",
                "media.playing", "media.duration"):  # fmt: skip
        r = wanted[key]
        value = f"{r.value:.2f}" if isinstance(r.value, float) else str(r.value)
        assert ours[key] == (value, r.unit, r.label), key
    assert 61.5 < float(ours["media.position"][0]) < 66.0  # (it moved on since it was asked)
    assert listed(_run(tmp_path, "sensors", PATH=str(tools))) == {}

    def player(status: str, title: str) -> str:
        properties = {
            "PlaybackStatus": {"type": "s", "data": status},
            "Position": {"type": "x", "data": 5_000_000},
            "Metadata": {"type": "a{sv}", "data": {
                "xesam:title": {"type": "s", "data": title},
                "xesam:artist": {"type": "as", "data": ["Air", "Beck"]},
                "mpris:length": {"type": "x", "data": 300_000_000},
            }},
        }  # fmt: skip
        return json.dumps({"type": "a{sv}", "data": [properties]})

    names = {"type": "as", "data": [["org.freedesktop.DBus", "org.mpris.MediaPlayer2.vlc",
                                     "org.mpris.MediaPlayer2.firefox.instance_1"]]}  # fmt: skip
    (tools / "playerctl").unlink()
    (tools / "busctl").write_text(
        "#!/bin/sh\n"
        f"case \"$*\" in\n*ListNames*) printf '%s\\n' '{json.dumps(names)}';;\n"
        f"*MediaPlayer2.vlc*) printf '%s\\n' '{player('Paused', 'Quiet')}';;\n"
        f"*firefox*) printf '%s\\n' '{player('Playing', 'Loud')}';;\nesac\n",
        encoding="utf-8",
    )
    (tools / "busctl").chmod(0o755)
    ours = listed(_run(tmp_path, "sensors", PATH=str(tools), LP_SENSORS_WANT="media.title"))
    assert ours["media.title"] == ("Loud", "", "Title")
    assert ours["media.artist"] == ("Air, Beck", "", "Artist")
    assert ours["media.source"] == ("Firefox", "", "Player")
    assert ours["media.duration"] == ("300.00", "s", "Length")


def test_the_weather_comes_from_the_forecast_of_the_place(tmp_path: Path) -> None:
    """With `[weather]` on, the forecast is asked for the place in the settings (here of a
    server of the test's own, which sees what is asked) and read as weather/open_meteo.py
    reads it; an answer that is none is said and nothing more."""
    from datetime import UTC, datetime, timedelta
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from libre_panel.config import WeatherConfig
    from libre_panel.weather import open_meteo

    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    start -= timedelta(hours=start.hour)
    data = {
        "utc_offset_seconds": 0,
        "current_units": {
            "temperature_2m": "°C",
            "wind_speed_10m": "km/h",
            "relative_humidity_2m": "%",
            "apparent_temperature": "°C",
        },  # fmt: skip
        "current": {
            "temperature_2m": 18.5,
            "apparent_temperature": 17,
            "weather_code": 61,
            "relative_humidity_2m": 70,
            "wind_speed_10m": 12.25,
            "is_day": 1,
        },  # fmt: skip
        "hourly_units": {"temperature_2m": "°C", "precipitation_probability": "%"},
        "hourly": {
            "time": [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(72)],
            "temperature_2m": [h / 2 for h in range(72)],
            "weather_code": [3] * 72,
            "precipitation_probability": [40] * 72,
            "is_day": [1] * 72,
        },  # fmt: skip
        "daily_units": {"temperature_2m_max": "°C", "temperature_2m_min": "°C"},
        "daily": {
            "time": [(start + timedelta(days=d)).strftime("%Y-%m-%d") for d in range(3)],
            "temperature_2m_max": [20.5, 21, 22],
            "temperature_2m_min": [10, 11.5, 12],
            "weather_code": [61, 3, 0],
            "precipitation_probability_max": [80, 20, 0],
        },  # fmt: skip
        "minutely_15": {
            "time": [
                (start + timedelta(minutes=15 * s)).strftime("%Y-%m-%dT%H:%M") for s in range(288)
            ],
            "precipitation": [1.5] * 288,
        },  # fmt: skip
    }
    asked: list[tuple[str, str]] = []
    answer = [json.dumps(data).encode()]

    class Forecast(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            asked.append((self.path, self.headers.get("User-Agent", "")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(answer[0])))
            self.end_headers()
            self.wfile.write(answer[0])

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Forecast)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        (tmp_path / "config.toml").write_text(
            '[sensors]\nproviders = ["nope"]\n[weather]\nenabled = true\nlatitude = 52.52\n'
            'longitude = 13.405\nunits = "imperial"\n',
            encoding="utf-8",
        )
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/forecast"
        env = {"LP_WEATHER_URL": url, "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
        before = time.time()
        done = _run(tmp_path, "sensors", **env)
        after = time.time()
        assert done.returncode == 0, done.stderr
        cfg = WeatherConfig(latitude=52.52, longitude=13.405, units="imperial")
        from urllib.parse import urlencode

        [(path, agent)] = asked
        assert path == f"/v1/forecast?{urlencode(open_meteo.build_params(cfg))}"
        assert agent.startswith("libre-panel/")
        ours = {line[:40].strip(): line[41:]
                for line in done.stdout.splitlines() if line.startswith("weather.")}  # fmt: skip
        wanted = [{**open_meteo.parse_current(data), **open_meteo.forecast_readings(data, at)}
                  for at in (before, after)]  # fmt: skip
        assert sorted(ours) in [sorted(readings) for readings in wanted]
        assert {"weather.temperature", "weather.hour.1.time", "weather.day.0.name",
                "weather.rain.text"} <= set(ours)  # fmt: skip
        for key, rest in ours.items():  # (the value, its unit, its label)
            said = [f"{r.value:.2f}" if isinstance(r.value, float) else str(r.value)
                    for r in (readings[key] for readings in wanted if key in readings)]  # fmt: skip
            reading = wanted[0].get(key) or wanted[1][key]
            assert any(rest.lstrip().startswith(text) for text in said), key
            assert rest.endswith(f" {reading.unit:<5} {reading.label}"), key

        asked.clear()
        answer[0] = b"<html>no forecast today</html>"
        done = _run(tmp_path, "sensors", **env)
        assert done.returncode == 0, done.stderr
        assert len(asked) == 1 and "weather." not in done.stdout
        assert "weather update failed" in done.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_home_assistant_and_json_sources_are_asked_and_read(tmp_path: Path) -> None:
    """`homeassistant` and `json` among the sources: Home Assistant's states are asked for
    with the token, each JSON source at its address (with its headers) or in its file; the
    readings are those sensors/homeassistant.py and sensors/json_source.py make of the
    answers, and a source that cannot be is said."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from libre_panel.sensors import homeassistant, json_source

    states = [
        {"entity_id": "sensor.living_room", "state": "21.5",
         "attributes": {"friendly_name": "Living room", "unit_of_measurement": "°C"}},
        {"entity_id": "binary_sensor.door", "state": "on", "attributes": {}},
        {"entity_id": "climate.hall", "state": "heat",
         "attributes": {"friendly_name": "Hall", "current_temperature": 19.5, "min_temp": 7,
                        "preset": "eco"}},
        {"entity_id": "light.kitchen", "state": "off", "attributes": {"brightness": 0}},
    ]  # fmt: skip
    nas = {"cpu": {"total": 12.5, "cores": [1, 2]}, "sensors": [{"value": 1}, {"value": 48}],
           "host": "nas", "up": True}  # fmt: skip
    values = {"power": 230, "note": "fine", "deep": {"a": {"b": {"c": 1}}}}
    asked: list[tuple[str, str, str]] = []

    class Answers(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            headers = self.headers
            asked.append((self.path, headers.get("Authorization", ""), headers.get("X-Key", "")))
            body = json.dumps(states if self.path == "/api/states" else nas).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Answers)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        file = tmp_path / "values.json"
        file.write_bytes(b"\xef\xbb\xbf" + json.dumps(values).encode())
        entries = [
            {"name": "nas", "url": f"{base}/nas", "headers": {"X-Key": "abc"},
             "fields": {"cpu": "cpu.total", "temp": "sensors[1].value", "host": "host",
                        "gone": "nothing.here"},
             "units": {"cpu": "%", "temp": "°C", "host": "?"}},
            {"name": "My file", "file": file.as_posix()},
            {"name": "bad", "url": "ftp://nope"},
        ]  # fmt: skip

        def toml(value: Any) -> str:
            if isinstance(value, str):
                return json.dumps(value)
            return (
                "{ "
                + ", ".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in value.items())
                + " }"
            )

        tables = "".join(
            "[[sensors.json.sources]]\n"
            + "".join(f"{key} = {toml(value)}\n" for key, value in entry.items())
            for entry in entries
        )
        (tmp_path / "config.toml").write_text(
            '[sensors]\nproviders = ["homeassistant", "json"]\n'
            f'[sensors.homeassistant]\nurl = "{base}/"\ntoken = "secret-token"\n'
            'entities = ["sensor.*", "climate.*"]\n'
            f"[sensors.json]\nupdate_s = 5\n{tables}",
            encoding="utf-8",
        )
        done = _run(tmp_path, "sensors", NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
        assert done.returncode == 0, done.stderr
        assert sorted(set(asked)) == [("/api/states", "Bearer secret-token", ""),
                                      ("/nas", "", "abc")]  # fmt: skip
        wanted = homeassistant.readings_from_states(states, ["sensor.*", "climate.*"])
        for entry, data in zip(entries[:2], (nas, values), strict=True):
            wanted.update(json_source.Source(entry).readings(data))
        listed = [line for line in done.stdout.splitlines() if line.startswith(("ha.", "json."))]
        ours = {line[:40].strip(): line[41:] for line in listed}
        assert sorted(ours) == sorted(wanted) and len(ours) == 9
        for key, rest in ours.items():  # (the value, its unit, its label)
            reading = wanted[key]
            value = reading.value
            text = f"{value:.2f}" if isinstance(value, float) else str(value)
            assert rest.lstrip().startswith(text), key
            assert rest.endswith(f" {reading.unit:<5} {reading.label}"), key
        assert "json: json source 'bad': the url must start with http://" in done.stderr

        (tmp_path / "config.toml").write_text(
            '[sensors]\nproviders = ["homeassistant", "json"]\n'
            '[sensors.homeassistant]\npoll_seconds = "often"\n[sensors.json]\nsources = [5]\n',
            encoding="utf-8",
        )
        done = _run(tmp_path, "sensors", NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
        assert done.returncode == 0, done.stderr
        assert "ha." not in done.stdout and "json." not in done.stdout
        assert 'sensor source "homeassistant" not started' in done.stderr
        assert "json: sources[1] is no table, left out" in done.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_a_ups_is_asked_of_upsd(tmp_path: Path) -> None:
    """`nut` among the sources: upsd (here one of the test's own) is asked as sensors/nut.py
    asks it, with the user's name where one is set and for the first UPS where none is
    named; the readings are Python's, and a server that is not there is said."""
    import socketserver

    from libre_panel.sensors import nut

    variables = {"battery.charge": "87", "battery.runtime": "1530", "ups.load": "23",
                 "ups.realpower.nominal": "405", "input.voltage": "231.5",
                 "ups.status": "OB DISCHRG", "ups.model": 'Back-UPS "ES" 700G'}  # fmt: skip
    heard: list[str] = []

    class Upsd(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            for raw in self.rfile:
                line = raw.decode().rstrip("\n")
                heard.append(line)
                if line.startswith(("USERNAME ", "PASSWORD ")):
                    answer = "OK\n"
                elif line == "LIST UPS":
                    answer = 'BEGIN LIST UPS\nUPS desk "The desk\'s UPS"\nEND LIST UPS\n'
                elif line == "LIST VAR desk":
                    rows = "".join('VAR desk {} "{}"\n'.format(name, value.replace('"', '\\"'))
                                   for name, value in variables.items())  # fmt: skip
                    answer = f"BEGIN LIST VAR desk\n{rows}END LIST VAR desk\n"
                elif line == "LOGOUT":
                    answer = "OK Goodbye\n"
                else:
                    answer = "ERR UNKNOWN-UPS\n"
                self.wfile.write(answer.encode())

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Upsd)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        options = {"host": "127.0.0.1", "port": port, "username": "panel", "password": "pw"}

        def said_goodbye() -> None:
            """Waits until the server has heard a talk to its end."""
            until = time.monotonic() + 5
            while "LOGOUT" not in heard and time.monotonic() < until:
                time.sleep(0.01)

        python = nut.NutProvider(options, start=False)
        python.fetch()
        wanted = python.read()
        said_goodbye()
        asked = list(heard)
        heard.clear()
        (tmp_path / "config.toml").write_text(
            f'[sensors]\nproviders = ["nut"]\n[sensors.nut]\nport = {port}\n'
            'username = "panel"\npassword = "pw"\n',
            encoding="utf-8",
        )
        done = _run(tmp_path, "sensors")
        assert done.returncode == 0, done.stderr
        said_goodbye()
        assert heard[: len(asked)] == asked and asked[0] == "USERNAME panel"
        ours = {line[:40].strip(): line[41:]
                for line in done.stdout.splitlines() if line.startswith("ups.")}  # fmt: skip
        assert list(ours) == sorted(wanted) and len(ours) == 7
        for key, rest in ours.items():  # (the value, its unit, its label)
            reading = wanted[key]
            value = reading.value
            text = f"{value:.2f}" if isinstance(value, float) else str(value)
            assert rest.lstrip().startswith(text), key
            assert rest.endswith(f" {reading.unit:<5} {reading.label}"), key

        heard.clear()
        (tmp_path / "config.toml").write_text(
            f'[sensors]\nproviders = ["nut"]\n[sensors.nut]\nport = {port}\nups = "attic"\n',
            encoding="utf-8",
        )
        done = _run(tmp_path, "sensors")
        assert done.returncode == 0, done.stderr
        said_goodbye()
        assert heard[:2] == ["LIST VAR attic", "LOGOUT"] and "ups." not in done.stdout
        assert f"nut: 127.0.0.1:{port} does not answer: UNKNOWN-UPS" in done.stderr
    finally:
        server.shutdown()
        server.server_close()


def test_mqtt_messages_come_from_a_broker(tmp_path: Path) -> None:
    """`mqtt` among the sources: the broker (here one of the test's own) is greeted as MQTT
    3.1.1 asks, with the user's name, and the topics are subscribed; its messages, in
    pieces and long ones too, become the readings sensors/mqtt.py makes of them. A broker
    that refuses is said."""
    import socket

    from libre_panel.sensors import mqtt

    def text(value: bytes) -> bytes:
        return len(value).to_bytes(2, "big") + value

    def packet(first: int, body: bytes) -> bytes:
        length, size = b"", len(body)
        while True:
            size, low = divmod(size, 128)
            length += bytes([low | (0x80 if size else 0)])
            if not size:
                return bytes([first]) + length + body

    messages = [("home/kitchen/temperature", b"21.5"), ("home/hall/temperature", b" 19 "),
                ("zigbee2mqtt/plug", b'{"power": 12.5, "state": "ON", "note": "' + b"x" * 200
                 + b'"}'), ("home/kitchen/light", b"on")]  # fmt: skip
    heard: list[bytes] = []
    refuse = [False]
    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(0.2)
    done_serving = threading.Event()

    def packets_of(stream: socket.socket, count: int) -> None:
        """Reads `count` packets from the client into `heard`."""
        for _ in range(count):
            first = stream.recv(1)
            size, shift = 0, 0
            while True:
                byte = stream.recv(1)[0]
                size |= (byte & 0x7F) << shift
                shift += 7
                if not byte & 0x80:
                    break
            body = b""
            while len(body) < size:
                body += stream.recv(size - len(body))
            heard.append(first + body)

    def serve() -> None:
        while not done_serving.is_set():
            try:
                stream, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return  # (the test is over: the listener is closed)
            with stream:
                stream.settimeout(10)
                try:
                    packets_of(stream, 1)
                    if refuse[0]:
                        stream.sendall(b"\x20\x02\x00\x05")
                        continue
                    stream.sendall(b"\x20\x02\x00\x00")
                    packets_of(stream, 2)
                    sent = b"".join(packet(0x30, text(topic.encode()) + payload)
                                    for topic, payload in messages)  # fmt: skip
                    stream.sendall(sent[:7])
                    time.sleep(0.05)
                    stream.sendall(sent[7:] + b"\x90\x03\x00\x01\x00\x90\x03\x00\x02\x00")
                    while stream.recv(64):  # (until the client leaves)
                        pass
                except (OSError, IndexError):
                    pass

    broker = threading.Thread(target=serve, daemon=True)
    broker.start()
    try:
        port = listener.getsockname()[1]
        (tmp_path / "config.toml").write_text(
            f'[sensors]\nproviders = ["mqtt"]\n[sensors.mqtt]\nhost = "127.0.0.1"\nport = {port}\n'
            'username = "panel"\npassword = "pw"\n'
            'topics = ["home/+/temperature", "zigbee2mqtt/#"]\n'
            'units = { "home/+/temperature" = "°C", "zigbee2mqtt/#" = "W" }\n',
            encoding="utf-8",
        )
        done = _run(tmp_path, "sensors")
        assert done.returncode == 0, done.stderr
        connect, first, second = heard[:3]
        assert connect[:11] == b"\x10" + text(b"MQTT") + b"\x04\xc2\x00\x3c"
        assert connect.endswith(text(b"panel") + text(b"pw")) and b"libre-panel-" in connect
        assert first == b"\x82\x00\x01" + text(b"home/+/temperature") + b"\x00"
        assert second == b"\x82\x00\x02" + text(b"zigbee2mqtt/#") + b"\x00"
        units = {"home/+/temperature": "°C", "zigbee2mqtt/#": "W"}
        wanted: dict[str, Any] = {}
        for topic, payload in messages:
            wanted.update(mqtt.readings_from_message(topic, payload, units))
        listed = [line for line in done.stdout.splitlines() if line.startswith("mqtt.")]
        ours = {line[:40].strip(): line[41:] for line in listed}
        assert list(ours) == sorted(wanted) and len(ours) == 6
        for key, rest in ours.items():  # (the value, its unit, its label)
            reading = wanted[key]
            value = reading.value
            said = f"{value:.2f}" if isinstance(value, float) else str(value)
            assert rest.lstrip().startswith(said), key
            assert rest.endswith(f" {reading.unit:<5} {reading.label}"), key

        refuse[0] = True
        done = _run(tmp_path, "sensors")
        assert done.returncode == 0, done.stderr
        assert "mqtt." not in done.stdout
        assert f"MQTT: 127.0.0.1:{port}: the broker refused the connection (5)" in done.stderr
    finally:
        done_serving.set()
        broker.join(5)
        listener.close()


def test_the_next_events_come_from_calendars(tmp_path: Path) -> None:
    """`calendar` among the sources: an .ics file and an address (webcal:// asked as
    https:// would be; here a server of the test's own) give the next events as
    sensors/calendar.py reads them; a calendar that is not there is said by its number,
    not by its address."""
    from datetime import UTC, datetime, timedelta
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from libre_panel.sensors import calendar

    def event(title: str, start: datetime, rule: str = "") -> str:
        lines = ["BEGIN:VEVENT", f"UID:{title}", f"SUMMARY:{title}",
                 f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}", "DURATION:PT1H"]  # fmt: skip
        return "\r\n".join(lines + ([f"RRULE:{rule}"] if rule else []) + ["END:VEVENT"])

    start = datetime.now(UTC).replace(second=0, microsecond=0)
    work = "\r\n".join(["BEGIN:VCALENDAR", "X-WR-CALNAME:Work",
                        event("Stand-up", start + timedelta(hours=3), "FREQ=DAILY;COUNT=3"),
                        event("Review", start - timedelta(minutes=20)),
                        "END:VCALENDAR"])  # fmt: skip
    home = "\r\n".join(["BEGIN:VCALENDAR", event("Dentist", start + timedelta(days=2)),
                        "BEGIN:VEVENT", "SUMMARY:Holiday", "LOCATION:Sea",
                        f"DTSTART;VALUE=DATE:{(start + timedelta(days=4)).strftime('%Y%m%d')}",
                        "END:VEVENT", "END:VCALENDAR"])  # fmt: skip
    asked: list[str] = []

    class Calendars(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            asked.append(self.path)
            body = home.encode() if self.path == "/secret/home.ics" else b"gone"
            self.send_response(200 if self.path == "/secret/home.ics" else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Calendars)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        file = tmp_path / "work.ics"
        file.write_text(work, encoding="utf-8")
        (tmp_path / "config.toml").write_text(
            '[sensors]\nproviders = ["calendar"]\n[sensors.calendar]\ndays = 7\n'
            f'sources = ["{file.as_posix()}", "{base}/secret/home.ics",\n'
            f'           "{base}/secret/gone.ics"]\n',
            encoding="utf-8",
        )
        before = datetime.now()
        done = _run(tmp_path, "sensors", NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
        after = datetime.now()
        assert done.returncode == 0, done.stderr
        assert sorted(set(asked)) == ["/secret/gone.ics", "/secret/home.ics"]
        assert "calendar 3 not loaded: the server's answer 404" in done.stderr
        assert "secret" not in done.stderr
        listed = [line for line in done.stdout.splitlines() if line.startswith("calendar.")]
        ours = {line[:40].strip(): line[41:] for line in listed}
        wanted = []
        for now in (before, after):
            events = [e for text in (work, home) for e in calendar.events_from_ics(text, now, 7)]
            events.sort(key=lambda e: (e.start, not e.all_day, e.title))
            wanted.append(calendar.calendar_readings(events, now))
        assert sorted(ours) in [sorted(readings) for readings in wanted]
        assert ours["calendar.events"].lstrip().startswith("6.00")
        assert ours["calendar.1.title"].lstrip().startswith("Review")
        assert "calendar.1.now" in ours and "calendar.6.location" in ours
        for key, rest in ours.items():  # (the value, its unit, its label)
            said = [f"{r.value:.2f}" if isinstance(r.value, float) else str(r.value)
                    for r in (readings[key] for readings in wanted)]  # fmt: skip
            if not key.endswith(".minutes"):  # (they pass)
                assert any(rest.lstrip().startswith(text) for text in said), key
            reading = wanted[0][key]
            assert rest.endswith(f" {reading.unit:<5} {reading.label}"), key
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(os.name != "nt", reason="a memory shared by its name is Windows'")
def test_a_games_frames_come_from_the_memory_rtss_shares(tmp_path: Path) -> None:
    """`rtss` among the sources: the memory of the name `[sensors.rtss] mapping` gives (here
    one the test shares as RTSS does) is opened and read while something shows game.*; the
    program whose entry goes on is the game, its last frame's time the frame time."""
    import mmap
    import struct

    def memory(time1: int) -> bytes:
        entry = struct.pack("<I260s5I", 4242, b"C:\\Games\\Witcher 3\\bin\\witcher3.exe", 0,
                            time1 - 1000, time1, 100, 10_000)  # fmt: skip
        idle = struct.pack("<I260s5I", 77, b"C:\\Windows\\idle.exe", 0, 5, 6, 1, 50_000)
        header = struct.pack("<5I", 0x52545353, 0x00020007, 300, 32, 3) + b"\0" * 12
        return header + entry.ljust(300, b"\0") + b"\0" * 300 + idle.ljust(300, b"\0")

    name = f"LibrePanelTestRTSS{os.getpid()}"
    shared = mmap.mmap(-1, 4096, tagname=name)
    shared[: len(memory(0))] = memory(1000)
    (tmp_path / "config.toml").write_text(
        f'[sensors]\nproviders = ["rtss"]\n[sensors.rtss]\nmapping = "{name}"\n', encoding="utf-8"
    )
    stop = threading.Event()

    def game() -> None:
        time1 = 1000
        while not stop.wait(0.02):
            time1 += 20
            shared[: len(memory(0))] = memory(time1)

    writer = threading.Thread(target=game)
    writer.start()
    try:
        done = _run(tmp_path, "sensors", LP_SENSORS_WANT="game.fps")
    finally:
        stop.set()
        writer.join()
        shared.close()
    assert done.returncode == 0, done.stderr
    ours = {line[:40].strip(): (line[41:55].strip(), line[56:61].strip(), line[62:])
            for line in _lines(done)}  # fmt: skip
    assert ours["game.app"][0].startswith("witcher3")
    assert ours["game.frametime"][:2] == ("10.00", "ms")
    assert ours["game.fps"] == ("100.00", "fps", "Frames per second")
    assert "rtss: reading RivaTuner's frame rates" in done.stderr


def test_the_loop_shows_what_a_service_asks_for(tmp_path: Path) -> None:
    """A service (here one that follows the test's script) through its host: a theme of its
    own for a while, a mode with the page config.toml has for it, a message; the switches
    fade as `[transitions]` says, and the panel is back at config.toml's theme after each."""
    for name, colour in (("red", "#DC1E1E"), ("blue", "#1E1EDC")):
        folder = tmp_path / "themes" / name
        folder.mkdir(parents=True)
        theme = {"format": "libre-panel-theme/1", "name": name.title(),
                 "display": {"model": "turing-5", "orientation": "landscape", "width": 800,
                             "height": 480},
                 "palette": {"bg": colour}, "font": "builtin:Barlow-Medium",
                 "background": {"color": "@bg", "image": None}, "refresh_ms": 1000,
                 "widgets": []}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        'theme = "red"\nfps = 20\n[device]\ndriver = "virtual"\noutput = "panel.png"\n'
        '[sensors]\nproviders = []\n[services]\nenabled = ["nope"]\n'
        '[transitions]\ndefault = "fade"\nspeed = "slow"\ngame = "fade"\n'
        '[modes.game]\ntheme = "blue"\n',
        encoding="utf-8",
    )
    script = tmp_path / "script.json"
    script.write_text(json.dumps([
        [0.8, "theme", "blue", None, 0, "*"],
        [3.0, "theme", None],
        [5.0, "mode", "game", None],
        [7.0, "mode", None],
        [9.0, "notify", "Hello panel", "", 0, 30],
    ]), encoding="utf-8")  # fmt: skip
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), "LP_HOST_SCRIPT": str(script)}  # fmt: skip
    seen: list[tuple[int, int, int]] = []
    last: list[Image.Image] = []
    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        try:
            until = time.monotonic() + 11
            while time.monotonic() < until:
                try:
                    with Image.open(tmp_path / "panel.png") as frame:
                        picture = frame.convert("RGB")
                    seen.append(picture.getpixel((400, 240)))
                    last[:] = [picture]
                except (OSError, SyntaxError, ValueError):
                    pass  # (not there yet, or being put in its place)
                time.sleep(0.02)
        finally:
            if os.name == "nt":
                program.terminate()
            else:
                program.send_signal(signal.SIGINT)
            said = program.communicate(timeout=30)[1]
    wanted = ["theme red", "service script: shows theme 'blue'", "theme blue",
              "service script: restores the theme", "theme red", "mode: game", "theme blue",
              "mode: normal", "theme red"]  # fmt: skip
    lines = [line.removeprefix("libre-panel: ") for line in said.splitlines()]
    assert [line for line in lines if line in wanted] == wanted, said
    assert "services not in this build yet: nope" in said and "failed" not in said
    red, blue = (220, 30, 30), (30, 30, 220)
    assert red in seen and blue in seen
    # between them, the fades: both colours mixed
    mixed = [c for c in seen if 60 < c[0] < 190 and 60 < c[2] < 190 and abs(c[1] - 30) < 3]
    assert len(set(mixed)) >= 3, sorted(set(seen))
    # the message on the theme's ground at the end
    [picture] = last
    data, ground = picture.tobytes(), bytes(red)
    other = sum(1 for at in range(0, len(data), 3) if data[at : at + 3] != ground)
    assert 500 < other < 800 * 480 // 2, other


def test_the_timer_runs_and_says_when_its_time_is_up(tmp_path: Path) -> None:
    """`timer` among the services: started by its shortcut (here pressed by the test's
    script), it counts its minutes down and shows its notice on the panel when they are
    up."""
    folder = tmp_path / "themes" / "red"
    folder.mkdir(parents=True)
    theme = {"format": "libre-panel-theme/1", "name": "Red",
             "display": {"model": "turing-5", "orientation": "landscape", "width": 800,
                         "height": 480},
             "palette": {"bg": "#DC1E1E"}, "font": "builtin:Barlow-Medium",
             "background": {"color": "@bg", "image": None}, "refresh_ms": 1000,
             "widgets": []}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        'theme = "red"\nfps = 10\n[device]\ndriver = "virtual"\noutput = "panel.png"\n'
        '[sensors]\nproviders = []\n[services]\nenabled = ["timer"]\n'
        "[services.timer]\nminutes = 0.02\n",
        encoding="utf-8",
    )
    script = tmp_path / "script.json"
    script.write_text(json.dumps([[1.0, "shortcut", "timer"]]), encoding="utf-8")
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), "LP_HOST_SCRIPT": str(script)}  # fmt: skip
    ground = bytes((220, 30, 30))
    others: list[tuple[float, int]] = []
    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        began = time.monotonic()
        try:
            while time.monotonic() < began + 5:
                try:
                    with Image.open(tmp_path / "panel.png") as frame:
                        data = frame.convert("RGB").tobytes()
                    other = sum(1 for at in range(0, len(data), 3) if data[at : at + 3] != ground)
                    others.append((time.monotonic() - began, other))
                except (OSError, SyntaxError, ValueError):
                    pass  # (not there yet, or being put in its place)
                time.sleep(0.05)
        finally:
            if os.name == "nt":
                program.terminate()
            else:
                program.send_signal(signal.SIGINT)
            said = program.communicate(timeout=30)[1]
    assert "service 'timer' started" in said, said
    # nothing but the theme's ground until its time is up, then the notice on it
    assert others and all(other == 0 for at, other in others if at < 1.5), others[:20]
    assert any(other > 500 for at, other in others if at > 2.0), others[-20:]


def test_power_is_counted_and_kept(tmp_path: Path) -> None:
    """`power` among the services: the watts another source reports (here the test's script)
    with the rest of the PC add up, sample after sample, to the energy of the day, which is
    kept in the service's folder when the program ends."""
    (tmp_path / "config.toml").write_text(
        'theme = "slate"\nfps = 2\nrefresh_ms = 200\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = []\n[services]\nenabled = ["power"]\n'
        "[services.power]\nrest_w = 3500\nprice = 0.3\n",
        encoding="utf-8",
    )
    script = tmp_path / "script.json"
    script.write_text(json.dumps([[0.0, "publish", "cpu.power", 100.0, "W", "CPU"]]),
                      encoding="utf-8")  # fmt: skip
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), "LP_HOST_SCRIPT": str(script)}  # fmt: skip
    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        time.sleep(4)
        if os.name == "nt":
            program.terminate()
            program.communicate(timeout=30)
            pytest.skip("ended at once on Windows: the totals are kept every minute")
        program.send_signal(signal.SIGINT)
        said = program.communicate(timeout=30)[1]
    assert "service 'power' started" in said, said
    kept = json.loads((tmp_path / "plugins-data" / "power" / "power.json").read_text("utf-8"))
    assert kept["day"] == time.strftime("%Y-%m-%d") and kept["month"] == time.strftime("%Y-%m")
    # 3600 W for some three seconds: about a watt hour a second
    assert 1.5 < kept["today_wh"] < 4.5 and kept["month_wh"] == kept["today_wh"], kept


@pytest.mark.skipif(os.name == "nt", reason="stand-in programs written for the shell")
def test_ai_activity_comes_from_the_board_and_the_agents_programs(tmp_path: Path) -> None:
    """`agents` among the services: what an agent's status line and hooks say (here the
    test's script) and, while the theme shows `ai.*`, the plan's usage the programs on the
    PATH answer with: Claude Code asked headless with its own reports off, Codex's
    app-server asked for its limits."""
    folder = tmp_path / "themes" / "red"
    folder.mkdir(parents=True)

    def bar(row: int, sensor: str, colour: str) -> dict[str, object]:
        return {"type": "bar", "id": f"bar{row}", "x": 0, "y": row * 60, "w": 800, "h": 40,
                "sensor": sensor, "color": colour, "background": "#000000",
                "radius": 0}  # fmt: skip

    theme = {"format": "libre-panel-theme/1", "name": "Red",
             "display": {"model": "turing-5", "orientation": "landscape", "width": 800,
                         "height": 480},
             "palette": {"bg": "#DC1E1E"}, "font": "builtin:Barlow-Medium",
             "background": {"color": "@bg", "image": None}, "refresh_ms": 200,
             "widgets": [bar(0, "ai.claude.five_hour", "#00FF00"),
                         bar(1, "ai.codex.week", "#0000FF"),
                         bar(2, "ai.claude.context", "#FFFF00")]}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        'theme = "red"\nfps = 10\n[device]\ndriver = "virtual"\noutput = "panel.png"\n'
        '[sensors]\nproviders = []\n[services]\nenabled = ["agents"]\n',
        encoding="utf-8",
    )
    tools = tmp_path / "bin"
    tools.mkdir()
    usage = {"type": "control_response",
             "response": {"request_id": "libre-panel-usage", "subtype": "success",
                          "response": {"rate_limits_available": True, "rate_limits": {
                              "five_hour": {"utilization": 62.5,
                                            "resets_at": "2099-01-01T00:00:00Z"}}}}}  # fmt: skip
    limits = {"id": 2, "result": {"rateLimits": {"secondary": {
        "usedPercent": 40, "windowDurationMins": 10080, "resetsAt": 4070908800}}}}  # fmt: skip
    (tools / "claude").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "2.1.300 (Claude Code)"; exit 0; fi\n'
        f'echo "$@" > "{tmp_path}/claude.args"\n'
        'echo "$DISABLE_TELEMETRY-$CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" '
        f'> "{tmp_path}/claude.env"\n'
        f'read line\necho "$line" > "{tmp_path}/claude.in"\n'
        """echo '{"type": "system", "subtype": "init"}'\n"""
        f"echo '{json.dumps(usage)}'\nsleep 20\n",
        encoding="utf-8",
    )
    (tools / "codex").write_text(
        "#!/bin/sh\n"
        f'echo "$@" > "{tmp_path}/codex.args"\n'
        "while read line; do\n"
        '  case "$line" in\n'
        """    *'"id": 1'*) echo '{"id": 1, "result": {}}';;\n"""
        f"""    *'"id": 2'*) echo '{json.dumps(limits)}';;\n"""
        """    *'"id": 3'*) echo '{"id": 3, "result": {"dailyUsageBuckets": []}}';;\n"""
        "  esac\ndone\n",
        encoding="utf-8",
    )
    for tool in ("claude", "codex"):
        (tools / tool).chmod(0o755)
    script = tmp_path / "script.json"
    script.write_text(json.dumps([
        [0.5, "status", "claude", {"session_id": "s", "context_window": {"used_percentage": 25}}],
        [0.6, "hook", "claude", {"hook_event_name": "PreToolUse", "session_id": "s"}],
    ]), encoding="utf-8")  # fmt: skip
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), "LP_HOST_SCRIPT": str(script),
           "PATH": f"{tools}{os.pathsep}{os.environ.get('PATH', '')}",
           "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "0"}  # fmt: skip
    colours = {bytes((0, 255, 0)): 500, bytes((0, 0, 255)): 320, bytes((255, 255, 0)): 200}
    counts: dict[bytes, int] = {}
    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        began = time.monotonic()
        try:
            while time.monotonic() < began + 20:
                try:
                    with Image.open(tmp_path / "panel.png") as frame:
                        data = frame.convert("RGB").tobytes()
                    dots = [data[at : at + 3] for at in range(0, len(data), 3)]
                    counts = {colour: dots.count(colour) for colour in colours}
                except (OSError, SyntaxError, ValueError):
                    pass  # (not there yet, or being put in its place)
                if counts and all(abs(counts[c] - columns * 40) <= 80
                                  for c, columns in colours.items()):  # fmt: skip
                    break
                time.sleep(0.1)
        finally:
            program.send_signal(signal.SIGINT)
            said = program.communicate(timeout=30)[1]
    assert "service 'agents' started" in said, said
    # each bar as long as its share: 62.5 % of the 5 hours, 40 % of the week, a quarter of
    # the context window
    assert {c: round(n / 40) for c, n in counts.items()} == colours, (counts, said)
    assert "AI activity: the first word from claude (statusLine)" in said, said
    assert (tmp_path / "claude.args").read_text() == (
        "--safe-mode --no-session-persistence -p --input-format stream-json --output-format "
        "stream-json --verbose\n"
    )
    assert json.loads((tmp_path / "claude.in").read_text()) == {
        "type": "control_request", "request_id": "libre-panel-usage",
        "request": {"subtype": "get_usage", "skip_behaviors": True}}  # fmt: skip
    # (its own reports off, and without the switch that would keep it from asking)
    assert (tmp_path / "claude.env").read_text() == "1-\n"
    assert (tmp_path / "codex.args").read_text() == "app-server\n"


def test_notices_say_the_volume_and_a_sensor_past_its_limit(tmp_path: Path) -> None:
    """`notices` among the services: the volume another source reports (here the test's
    script) is news when it moves, not at its first value; a sensor at its crit limit is a
    warning on the panel and the alarm in the log."""
    folder = tmp_path / "themes" / "red"
    folder.mkdir(parents=True)
    theme = {"format": "libre-panel-theme/1", "name": "Red",
             "display": {"model": "turing-5", "orientation": "landscape", "width": 800,
                         "height": 480},
             "palette": {"bg": "#DC1E1E"}, "font": "builtin:Barlow-Medium",
             "background": {"color": "@bg", "image": None}, "refresh_ms": 200,
             "widgets": []}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        'theme = "red"\nfps = 10\n[device]\ndriver = "virtual"\noutput = "panel.png"\n'
        '[sensors]\nproviders = []\n[services]\nenabled = ["notices"]\n'
        '[services.notices]\nwarnings = [{ key = "cpu.temp", crit = 90 }]\n',
        encoding="utf-8",
    )
    script = tmp_path / "script.json"
    script.write_text(json.dumps([
        [0.3, "publish", "audio.volume", 30.0, "%", "Volume"],
        [0.3, "publish", "cpu.temp", 60.0, "°C", "CPU"],
        [2.5, "publish", "audio.volume", 60.0, "%", "Volume"],
        [9.0, "publish", "cpu.temp", 95.0, "°C", "CPU"],
    ]), encoding="utf-8")  # fmt: skip
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), "LP_HOST_SCRIPT": str(script)}  # fmt: skip
    ground = bytes((220, 30, 30))
    others: list[tuple[float, int]] = []
    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        began = time.monotonic()
        try:
            while time.monotonic() < began + 11.5:
                try:
                    with Image.open(tmp_path / "panel.png") as frame:
                        data = frame.convert("RGB").tobytes()
                    other = sum(1 for at in range(0, len(data), 3) if data[at : at + 3] != ground)
                    others.append((time.monotonic() - began, other))
                except (OSError, SyntaxError, ValueError):
                    pass  # (not there yet, or being put in its place)
                time.sleep(0.05)
        finally:
            if os.name == "nt":
                program.terminate()
            else:
                program.send_signal(signal.SIGINT)
            said = program.communicate(timeout=30)[1]
    assert "service 'notices' started" in said, said
    # the first volume is no news; the second is, for a while; then the warning
    assert others and all(other == 0 for at, other in others if at < 2.3), others[:40]
    assert any(other > 500 for at, other in others if 2.6 < at < 5.0), others
    assert any(other == 0 for at, other in others if 6.0 < at < 9.0), others
    assert any(other > 500 for at, other in others if at > 9.3), others[-40:]
    if os.name != "nt":  # (ended at once on Windows: what it said last may be lost)
        assert "alarm: CPU 95 °C" in said, said


@pytest.mark.skipif(os.name == "nt", reason="a stand-in game copied from the shell's sleep")
def test_game_mode_comes_with_a_game_and_goes_with_it(tmp_path: Path) -> None:
    """`game` among the services: a program from its list starts, and the panel shows the
    theme of `[modes.game]`; some seconds after it ended, config.toml's theme is back."""
    import shutil

    sleep = shutil.which("sleep")
    if sleep is None:
        pytest.skip("no sleep to stand in for a game")
    for name, colour in (("red", "#DC1E1E"), ("blue", "#1E1EDC")):
        folder = tmp_path / "themes" / name
        folder.mkdir(parents=True)
        theme = {"format": "libre-panel-theme/1", "name": name.title(),
                 "display": {"model": "turing-5", "orientation": "landscape", "width": 800,
                             "height": 480},
                 "palette": {"bg": colour}, "font": "builtin:Barlow-Medium",
                 "background": {"color": "@bg", "image": None}, "refresh_ms": 200,
                 "widgets": []}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        'theme = "red"\nfps = 10\n[device]\ndriver = "virtual"\noutput = "panel.png"\n'
        '[sensors]\nproviders = []\n[services]\nenabled = ["game"]\n'
        '[services.game]\nprograms = ["MyGame.bin = My Game"]\nsteam = false\n'
        "launchers = false\npoll_s = 0.3\nlinger_s = 1.0\nentrance = false\n"
        '[transitions]\ndefault = "cut"\n[modes.game]\ntheme = "blue"\n',
        encoding="utf-8",
    )
    stand_in = tmp_path / "MyGame.bin"
    shutil.copy(sleep, stand_in)
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries()}  # fmt: skip
    colours = {(220, 30, 30): "red", (30, 30, 220): "blue"}
    seen: list[tuple[float, str]] = []

    def look(began: float) -> str:
        try:
            with Image.open(tmp_path / "panel.png") as frame:
                counted = frame.convert("RGB").getcolors(1 << 16) or []
        except (OSError, SyntaxError, ValueError):
            return ""  # (not there yet, or being put in its place)
        most, colour = max(counted, default=(0, None))
        whole = most > 0.9 * sum(count for count, _ in counted)
        shown = colours.get(colour, "other") if whole else "other"
        seen.append((time.monotonic() - began, shown))
        return shown

    def wait_for(colour: str, began: float, seconds: float) -> bool:
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if look(began) == colour:
                return True
            time.sleep(0.05)
        return False

    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        began = time.monotonic()
        game = None
        try:
            assert wait_for("red", began, 10), seen[-20:]
            time.sleep(1.0)
            assert look(began) == "red", seen[-20:]
            game = subprocess.Popen([str(stand_in), "60"])
            there = wait_for("blue", began, 10)
            game.kill()
            game.wait()
            ended = time.monotonic()
            back = there and wait_for("red", began, 10)
            took = time.monotonic() - ended
        finally:
            if game is not None and game.poll() is None:
                game.kill()
            program.send_signal(signal.SIGINT)
            said = program.communicate(timeout=30)[1]
    assert "service 'game' started" in said, said
    assert there, (seen[-20:], said)
    assert back and took > 0.5, (took, seen[-20:], said)
    assert "game: My Game" in said and "game over" in said, said


def test_the_autopilot_shows_the_theme_of_the_rule_that_holds(tmp_path: Path) -> None:
    """`autopilot` among the services: while music plays (here said by the test's script),
    the theme of its rule shows; config.toml's is back when it stops."""
    for name, colour in (("red", "#DC1E1E"), ("blue", "#1E1EDC")):
        folder = tmp_path / "themes" / name
        folder.mkdir(parents=True)
        theme = {"format": "libre-panel-theme/1", "name": name.title(),
                 "display": {"model": "turing-5", "orientation": "landscape", "width": 800,
                             "height": 480},
                 "palette": {"bg": colour}, "font": "builtin:Barlow-Medium",
                 "background": {"color": "@bg", "image": None}, "refresh_ms": 200,
                 "widgets": []}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        'theme = "red"\nfps = 10\n[device]\ndriver = "virtual"\noutput = "panel.png"\n'
        '[sensors]\nproviders = []\n[services]\nenabled = ["autopilot"]\n'
        '[services.autopilot]\nhold_s = 0.5\nmin_s = 0\ntransition = ""\n'
        'rules = [{ name = "music", theme = "blue", when = { music = true } },\n'
        '         { theme = "nowhere", when = { music = false } }]\n',
        encoding="utf-8",
    )
    script = tmp_path / "script.json"
    script.write_text(json.dumps([
        [0.2, "publish", "media.state", "Paused", "", "State"],
        [2.5, "publish", "media.state", "Playing", "", "State"],
        [6.0, "publish", "media.state", "Paused", "", "State"],
    ]), encoding="utf-8")  # fmt: skip
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), "LP_HOST_SCRIPT": str(script)}  # fmt: skip
    colours = {(220, 30, 30): "red", (30, 30, 220): "blue"}
    seen: list[tuple[float, str]] = []
    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        began = time.monotonic()
        try:
            while time.monotonic() < began + 9.5:
                try:
                    with Image.open(tmp_path / "panel.png") as frame:
                        counted = frame.convert("RGB").getcolors(1 << 16) or []
                    most, colour = max(counted, default=(0, None))
                    if most > 0.9 * sum(count for count, _ in counted):
                        seen.append((time.monotonic() - began, colours.get(colour, "other")))
                except (OSError, SyntaxError, ValueError):
                    pass  # (not there yet, or being put in its place)
                time.sleep(0.05)
        finally:
            if os.name == "nt":
                program.terminate()
            else:
                program.send_signal(signal.SIGINT)
            said = program.communicate(timeout=30)[1]
    assert "service 'autopilot' started" in said, said
    assert seen and all(shown == "red" for at, shown in seen if at < 2.4), seen[:40]
    assert any(shown == "blue" for at, shown in seen if 3.0 < at < 6.0), seen
    assert all(shown == "red" for at, shown in seen if at > 8.0), seen[-40:]
    assert any(at > 8.0 for at, _ in seen), seen[-5:]
    if os.name != "nt":  # (ended at once on Windows: what it said last may be lost)
        assert "rule 'music': theme 'blue'" in said, said
        assert "rule 'rule 2' skipped: theme 'nowhere' is not installed" in said, said
        assert "no rule holds: the theme of config.toml" in said, said


def test_a_browsers_download_is_on_the_panel_while_it_loads(tmp_path: Path) -> None:
    """`downloads` among the services: a part file in the folder named is a download on the
    theme's download module; once the file it was becoming is there, it is done for a few
    seconds, then the module is as it was."""
    from libre_panel import design

    look = design.LOOKS[sorted(design.LOOKS)[0]]
    folder = tmp_path / "themes" / "loads"
    folder.mkdir(parents=True)
    theme = {"format": "libre-panel-theme/1", "name": "Loads",
             "display": {"model": "turing-5", "orientation": "landscape", "width": 800,
                         "height": 480},
             "palette": dict(look["palette"]), "style": dict(look["style"]),
             "grid": {"columns": 1, "rows": 1, "gap": 8, "margin": 24}, "refresh_ms": 200,
             "widgets": [{"type": "module", "id": "m0", "module": "download", "col": 0, "row": 0,
                          "cols": 1, "rows": 1, "finish": "flat"}]}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    loads = tmp_path / "dl"
    loads.mkdir()
    (tmp_path / "config.toml").write_text(
        'theme = "loads"\nfps = 10\n[device]\ndriver = "virtual"\noutput = "panel.png"\n'
        '[sensors]\nproviders = []\n[services]\nenabled = ["downloads"]\n'
        f"[services.downloads]\nsteam = false\nfolder = {json.dumps(str(loads))}\nmin_mb = 0\n",
        encoding="utf-8",
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries()}  # fmt: skip
    seen: list[tuple[float, bytes]] = []
    part = loads / "film.mkv.part"
    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        began = time.monotonic()
        try:
            while (at := time.monotonic() - began) < 17.5:
                if 3.0 < at < 7.0:
                    with open(part, "ab") as stream:  # (it grows while it loads)
                        stream.write(bytes(200_000))
                elif at >= 7.0 and part.exists():
                    part.rename(loads / "film.mkv")
                try:
                    with Image.open(tmp_path / "panel.png") as frame:
                        seen.append((at, frame.convert("RGB").tobytes()))
                except (OSError, SyntaxError, ValueError):
                    pass  # (not there yet, or being put in its place)
                time.sleep(0.05)
        finally:
            if os.name == "nt":
                program.terminate()
            else:
                program.send_signal(signal.SIGINT)
            said = program.communicate(timeout=30)[1]
    assert "service 'downloads' started" in said, said
    before = [frame for at, frame in seen if 1.0 < at < 2.9]
    assert before and all(frame == before[-1] for frame in before), len(before)
    # the download, then that it is done: neither is the module with nothing to show
    loading = [frame for at, frame in seen if 5.0 < at < 6.9]
    done = [frame for at, frame in seen if 9.0 < at < 12.0]
    assert loading and all(frame != before[-1] for frame in loading), len(loading)
    assert done and all(frame != before[-1] for frame in done), len(done)
    assert not set(done) & set(loading)
    after = [frame for at, frame in seen if at > 16.0]
    assert after and all(frame == before[-1] for frame in after), len(after)


def test_the_memory_keeps_a_round_and_shows_its_report(tmp_path: Path) -> None:
    """`memory` among the services: while a game is told (here by the test's script), its
    round is recorded; when it ends, the report's theme shows for `report_seconds`, then
    config.toml's is back; the database holds the round and the minute."""
    import sqlite3

    for name, colour in (("red", "#DC1E1E"), ("blue", "#1E1EDC")):
        folder = tmp_path / "themes" / name
        folder.mkdir(parents=True)
        theme = {"format": "libre-panel-theme/1", "name": name.title(),
                 "display": {"model": "turing-5", "orientation": "landscape", "width": 800,
                             "height": 480},
                 "palette": {"bg": colour}, "font": "builtin:Barlow-Medium",
                 "background": {"color": "@bg", "image": None}, "refresh_ms": 200,
                 "widgets": []}  # fmt: skip
        (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        'theme = "red"\nfps = 10\n[device]\ndriver = "virtual"\noutput = "panel.png"\n'
        '[sensors]\nproviders = []\n[services]\nenabled = ["memory"]\n'
        '[services.memory]\nreport_minutes = 0\nreport_seconds = 3\nreport_theme = "blue"\n'
        'report_transition = ""\n',
        encoding="utf-8",
    )
    script = tmp_path / "script.json"
    script.write_text(json.dumps([
        [0.2, "publish", "cpu.temp", 55.0, "°C", "CPU"],
        [1.0, "publish", "game.name", "Doom", "", "Game"],
        [1.0, "publish", "game.app", "Doom.exe", "", "Program"],
        [1.0, "publish", "game.fps", 100.0, "fps", "FPS"],
        [3.0, "publish", "game.fps", 140.0, "fps", "FPS"],
        [5.5, "unpublish", "game.name"],
    ]), encoding="utf-8")  # fmt: skip
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries(), "LP_HOST_SCRIPT": str(script)}  # fmt: skip
    colours = {(220, 30, 30): "red", (30, 30, 220): "blue"}
    seen: list[tuple[float, str]] = []
    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        began = time.monotonic()
        try:
            while time.monotonic() < began + 13.5:
                try:
                    with Image.open(tmp_path / "panel.png") as frame:
                        counted = frame.convert("RGB").getcolors(1 << 16) or []
                    most, colour = max(counted, default=(0, None))
                    if most > 0.9 * sum(count for count, _ in counted):
                        seen.append((time.monotonic() - began, colours.get(colour, "other")))
                except (OSError, SyntaxError, ValueError):
                    pass  # (not there yet, or being put in its place)
                time.sleep(0.05)
        finally:
            if os.name == "nt":
                program.terminate()
            else:
                program.send_signal(signal.SIGINT)
            said = program.communicate(timeout=30)[1]
    assert "service 'memory' started" in said, said
    assert seen and all(shown == "red" for at, shown in seen if at < 5.4), seen[:40]
    assert any(shown == "blue" for at, shown in seen if 7.2 < at < 9.0), seen
    assert all(shown == "red" for at, shown in seen if at > 12.0), seen[-40:]
    assert any(at > 12.0 for at, _ in seen), seen[-5:]
    connection = sqlite3.connect(tmp_path / "plugins-data" / "memory" / "memory.db")
    try:
        rounds = connection.execute(
            "SELECT game, program, closed, fps_s, fps_mean, cpu_temp_max FROM sessions"
        ).fetchall()
        minutes = connection.execute("SELECT samples, cpu_temp, mode FROM minutes").fetchall()
        cooling = connection.execute("SELECT mode, game, cpu_temp FROM cooling").fetchall()
    finally:
        connection.close()
    [(game, program, closed, fps_s, fps_mean, warmest)] = rounds
    assert (game, program, closed, warmest) == ("Doom", "doom", 1, 55.0), rounds
    assert 3 <= fps_s <= 6 and 100.0 < fps_mean < 140.0, rounds
    assert ("game", "Doom", 55.0) in cooling and ("normal", None, 55.0) in cooling, cooling
    if os.name != "nt":  # (ended at once on Windows: the minute under way is not written)
        assert minutes and all(row[1] == 55.0 for row in minutes), minutes
        assert "round report: Doom, 0 min, 1" in said, said


def test_the_cooling_analysis_starts_and_names_the_file_it_reads(tmp_path: Path) -> None:
    """`cooling` among the services: it does nothing on its own (a page asks it), and says
    at its start which file it reads: the memory service's, unless another is named."""
    (tmp_path / "config.toml").write_text(
        'theme = "slate"\nfps = 2\nrefresh_ms = 200\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = []\n[services]\nenabled = ["cooling"]\n'
        '[services.cooling]\nlimits = [{ key = "cpu.temp", max = 95 }, 5]\n',
        encoding="utf-8",
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries()}  # fmt: skip
    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        time.sleep(3)
        if os.name == "nt":
            program.terminate()
        else:
            program.send_signal(signal.SIGINT)
        said = program.communicate(timeout=30)[1]
    assert "service 'cooling' started" in said, said
    reads = tmp_path / "plugins-data" / "memory" / "memory.db"
    assert f"cooling analysis: reads {reads}" in said, said
    assert "limits[2]: needs a reading and a maximum" in said, said


def test_the_devices_are_looked_at_where_windows_tells_them(tmp_path: Path) -> None:
    """`devices` among the services: on Windows it asks the system for the drives, the
    controllers, the USB devices and the storage (what is there at the start is taken in
    silently); elsewhere it says that it works on Windows only."""
    (tmp_path / "config.toml").write_text(
        'theme = "slate"\nfps = 2\nrefresh_ms = 200\n[device]\ndriver = "virtual"\n'
        'output = "panel.png"\n[sensors]\nproviders = []\n[services]\nenabled = ["devices"]\n'
        '[services.devices]\nusb_ignore = ["1a86:7523", "nothing"]\n',
        encoding="utf-8",
    )
    env = {**os.environ, "LIBRE_PANEL_HOME": str(tmp_path), "LIBRE_PANEL_DATA": str(DATA),
           **_libraries()}  # fmt: skip
    with subprocess.Popen([str(_program()), "run"], env=env, stderr=subprocess.PIPE,
                          encoding="utf-8") as program:  # fmt: skip
        time.sleep(5)
        alive = program.poll() is None
        if os.name == "nt":
            program.terminate()
        else:
            program.send_signal(signal.SIGINT)
        said = program.communicate(timeout=30)[1]
    assert alive and "panicked" not in said, said
    assert "service 'devices' started" in said, said
    assert "usb_ignore: 'nothing' is no USB id" in said, said
    assert ("devices: this works on Windows only" in said) == (os.name != "nt"), said
