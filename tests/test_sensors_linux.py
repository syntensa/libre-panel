"""The Linux source: hwmon chips, the graphics card, the processor's power and the memory.

A PC's /sys is laid out in a folder (``fake_sysfs``), the way the kernel's drivers lay it out,
and the source reads it as its root. NVIDIA's library is an object with the same functions
(``test_sensors_nvml``). Nothing here needs Linux, or hardware.
"""

import threading
from pathlib import Path

import pytest
from fake_sensors import show, wait_for
from fake_sysfs import Sysfs, amd_card
from test_sensors_nvml import FakeLibrary

from libre_panel.render import lists
from libre_panel.sensors import base, gpus, hardware, hwmon, nvml, psutil_provider, rapl
from libre_panel.sensors.base import SensorHub
from libre_panel.sensors.hardware import Adapter
from libre_panel.sensors.linux import RESCAN_SECONDS, LinuxProvider


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def sysfs(tmp_path):
    return Sysfs(tmp_path / "root")


@pytest.fixture
def make(sysfs):
    """A source reading the fake PC, with a clock of its own; closed after the test."""
    made = []

    def build(clock=None, opener=None, **options):
        clock = clock or Clock()
        provider = LinuxProvider(options, root=sysfs.root, clock=clock, opener=opener)
        made.append(provider)
        provider.clock = clock
        return provider

    yield build
    for provider in made:
        provider.close()


# -- hwmon: the keys PsutilProvider gave, and what is new ------------------------------------


def test_chips_are_named_as_psutil_named_them(sysfs, make):
    sysfs.hwmon(0, "nvme", temp1_input=40000, temp1_label="Composite", temp2_input=44000)
    sysfs.hwmon(1, "nvme", temp1_input=55000, temp1_label="Composite")
    sysfs.hwmon(2, "k10temp", temp1_input=61500, temp1_label="Tctl", temp3_input=58000)
    sysfs.hwmon(4, "amdgpu", fan1_input=900)
    sysfs.hwmon(5, "amdgpu", fan1_input=1200)
    out = make().read()
    assert out["temp.nvme.composite"].value == 40.0 and out["temp.nvme.composite_2"].value == 55.0
    assert out["temp.nvme.composite_2"].label == "NVMe Composite 2"
    assert out["temp.nvme.1"].value == 44.0 and out["temp.nvme.1"].label == "NVMe 2"
    assert out["temp.k10temp.tctl"].value == 61.5 and out["temp.k10temp.tctl"].label == "CPU Tctl"
    assert out["temp.k10temp.1"].value == 58.0  # no label: its place among the chip's sensors
    assert out["fan.amdgpu.0"].value == 900.0 and out["fan.amdgpu.1"].value == 1200.0
    assert out["fan.amdgpu.0"].unit == "RPM" and out["temp.nvme.composite"].unit == "°C"


def test_the_keys_follow_psutils_order_not_the_numbers(sysfs, make):
    """psutil sorted the files as text: hwmon10 comes before hwmon2, so the sensor of the
    chip that comes first gets the plain key."""
    sysfs.hwmon(2, "nvme", temp1_input=30000, temp1_label="Composite")
    sysfs.hwmon(10, "nvme", temp1_input=70000, temp1_label="Composite")
    out = make().read()
    assert out["temp.nvme.composite"].value == 70.0  # hwmon10
    assert out["temp.nvme.composite_2"].value == 30.0  # hwmon2


def test_the_same_keys_as_psutil_for_the_same_sensors(sysfs, make, monkeypatch):
    """Sources are asked in order and the first with a key wins; where Linux has both, the
    names must agree, or lists would show a sensor twice."""
    from collections import namedtuple

    Temp = namedtuple("Temp", "label current high critical")
    Fan = namedtuple("Fan", "label current")
    sysfs.hwmon(0, "nvme", temp1_input=40000, temp1_label="Composite")
    sysfs.hwmon(1, "nvme", temp1_input=55000, temp1_label="Composite")
    sysfs.hwmon(2, "amdgpu", fan1_input=900)
    sysfs.hwmon(3, "amdgpu", fan1_input=1200)
    temps = {"nvme": [Temp("Composite", 40.0, 0, 0), Temp("Composite", 55.0, 0, 0)]}
    fans = {"amdgpu": [Fan("", 900), Fan("", 1200)]}
    monkeypatch.setattr(
        psutil_provider.psutil, "sensors_temperatures", lambda: temps, raising=False
    )
    monkeypatch.setattr(psutil_provider.psutil, "sensors_fans", lambda: fans, raising=False)
    theirs = {}
    provider = psutil_provider.PsutilProvider()
    provider._temperatures(theirs)
    provider._fans(theirs)
    ours = make().read()
    for key in ("temp.nvme.composite", "temp.nvme.composite_2", "fan.amdgpu.0", "fan.amdgpu.1"):
        assert (ours[key].value, ours[key].label) == (theirs[key].value, theirs[key].label), key


def test_files_one_level_down_are_found_as_on_older_kernels(sysfs, make):
    sysfs.hwmon(0, "coretemp", device=True, temp1_input=47000, temp1_label="Package id 0")
    out = make().read()
    assert out["temp.coretemp.package_id_0"].value == 47.0 and out["cpu.temp"].value == 47.0


def test_a_chip_that_fails_leaves_out_its_reading_only(sysfs, make):
    sysfs.hwmon(
        0, "k10temp", temp1_input=50000, temp1_label="Tctl", temp2_input="", temp3_input="x"
    )
    out = make().read()
    assert set(out) >= {"temp.k10temp.tctl", "cpu.temp"}
    assert not any(key.endswith((".1", ".2")) for key in out)


@pytest.mark.parametrize("raw", [-128000, -61000, 200000, 255000])
def test_a_temperature_a_chip_gives_for_no_sensor_is_left_out(sysfs, make, raw):
    sysfs.hwmon(0, "acpitz", temp1_input=raw)
    assert make().read() == {}


def test_nothing_to_read_gives_nothing(tmp_path):
    provider = LinuxProvider(root=tmp_path / "no-such-root")
    assert provider.read() == {}
    assert provider.status()["chips"] == 0
    provider.close()


# -- the mainboard: fans as Windows names them ----------------------------------------------


def test_the_fans_of_the_mainboards_chip_are_named_as_windows_names_them(sysfs, make):
    sysfs.hwmon(
        1,
        "nct6798",
        fan1_input=820,
        fan2_input=1140,
        fan3_input=0,
        fan6_input=2100,
        fan6_label="Pump",
        temp1_input=34000,
        temp1_label="SYSTIN",
        temp2_input=0,
        temp2_label="AUXTIN1",
    )
    out = make().read()
    assert out["fan.lpc.fan_1"].value == 820.0 and out["fan.lpc.fan_1"].label == "Fan #1"
    assert out["fan.lpc.fan_2"].value == 1140.0 and out["fan.lpc.fan_2"].unit == "RPM"
    assert out["fan.lpc.fan_3"].value == 0.0  # a fan that stands is a reading
    assert out["fan.lpc.fan_6"].label == "Pump"
    assert not any(key.startswith("fan.nct6798") for key in out)
    assert out["temp.nct6798.systin"].value == 34.0
    assert "temp.nct6798.auxtin1" not in out  # an input with nothing on it reads 0


def test_a_second_super_io_chip_is_lpc2_and_other_chips_keep_their_name(sysfs, make):
    sysfs.hwmon(0, "it8688", fan1_input=700)
    sysfs.hwmon(1, "nct6798", fan1_input=800)
    sysfs.hwmon(2, "asusec", fan1_input=1500, fan1_label="CPU_Opt")
    out = make().read()
    assert out["fan.lpc.fan_1"].value == 700.0 and out["fan.lpc2.fan_1"].value == 800.0
    assert out["fan.asusec.cpu_opt"].value == 1500.0 and out["fan.asusec.cpu_opt"].label == (
        "asusec CPU_Opt"
    )


def test_a_list_of_fans_shows_each_fan_once(sysfs, make):
    sysfs.hwmon(1, "nct6798", fan1_input=820, fan2_input=1140)
    amd_card(sysfs)
    out = make().read()
    shown = lists.pick({"items": "fan.*"}, out)
    assert [item.key for item in shown] == [
        "fan.amdgpu.0",
        "fan.lpc.fan_1",
        "fan.lpc.fan_2",
    ]  # gpu.fan is not under fan.*, and repeats fan.amdgpu.0


# -- cpu.temp ------------------------------------------------------------------------------


def cpu(readings):
    reading = readings.get("cpu.temp")
    return None if reading is None else (reading.value, reading.origin)


def test_the_cpu_temperature_is_the_packages_then_the_dies_then_tctl(sysfs, make):
    sysfs.hwmon(0, "k10temp", temp1_input=70000, temp1_label="Tctl", temp2_input=50000)
    sysfs.hwmon(0, "k10temp", temp2_input=50000, temp2_label="Tdie")
    assert cpu(make().read()) == (50.0, "temp.k10temp.tdie")  # Tdie: without the offset in Tctl


def test_an_intel_chip_gives_its_package(sysfs, make):
    sysfs.hwmon(
        0,
        "coretemp",
        temp1_input=45000,
        temp1_label="Package id 0",
        temp2_input=41000,
        temp2_label="Core 0",
    )
    assert cpu(make().read()) == (45.0, "temp.coretemp.package_id_0")


def test_no_processor_chip_means_no_cpu_temperature_unless_the_zones_tell(sysfs, make):
    sysfs.hwmon(0, "nvme", temp1_input=40000, temp1_label="Composite")
    assert cpu(make().read()) is None
    sysfs2 = Sysfs(sysfs.root / "zones")
    sysfs2.thermal_zone(0, "acpitz", 41000)
    sysfs2.thermal_zone(1, "x86_pkg_temp", 52000)
    provider = LinuxProvider(root=sysfs2.root)
    try:
        # no chip has a temperature: the thermal zones are read, the processor's first
        assert cpu(provider.read()) == (52.0, "temp.x86_pkg_temp.0")
    finally:
        provider.close()


def test_a_processors_sensor_without_a_label_will_do(sysfs, make):
    sysfs.hwmon(0, "cpu_thermal", temp1_input=39000)
    assert cpu(make().read()) == (39.0, "temp.cpu_thermal.0")


# -- the memory ----------------------------------------------------------------------------


def test_the_memory_temperature_is_the_hottest_modules(sysfs, make):
    sysfs.hwmon(0, "spd5118", temp1_input=41000)
    sysfs.hwmon(1, "spd5118", temp1_input=47500)
    provider = make()
    provider.read()
    assert wait_for(lambda: "mem.temp" in provider.read())  # the slow ones come from a thread
    reading = provider.read()["mem.temp"]
    assert (reading.value, reading.unit, reading.label) == (47.5, "°C", "Memory temperature")
    assert reading.origin == "temp.spd5118.spd5118_2" or reading.origin.startswith("temp.spd5118")


# -- drives ---------------------------------------------------------------------------------


def test_an_nvme_drive_is_named_by_its_model_and_read_by_a_thread(sysfs, make):
    sysfs.hwmon(0, "nvme", temp1_input=43000, temp1_label="Composite", temp2_input=39000)
    sysfs.hwmon(0, "nvme", temp2_label="Sensor 1")
    sysfs.write("sys/class/hwmon/hwmon0/device/model", "Example NVMe 1TB")
    provider = make()
    out = provider.read()
    assert out["temp.nvme.composite"].value == 43.0  # the first read waits for the thread
    assert out["temp.nvme.composite"].label == "Example NVMe 1TB"
    assert out["temp.nvme.sensor_1"].label == "Example NVMe 1TB Sensor 1"
    assert provider._thread is not None and provider._thread.name == "sensors-linux"


def test_a_slow_reading_that_is_too_old_is_dropped(sysfs, make):
    sysfs.hwmon(0, "nvme", temp1_input=43000, temp1_label="Composite")
    clock = Clock()
    provider = make(clock=clock)
    provider.close()  # no thread: the test reads the drives itself
    provider._stop.clear()
    provider.scan()
    provider.read_slow()
    assert provider.read()["temp.nvme.composite"].value == 43.0
    clock.advance(31)
    assert "temp.nvme.composite" not in provider.read()


def test_a_sata_drive_is_asked_only_while_a_theme_shows_it(sysfs, make, monkeypatch):
    """drivetemp may wake a drive that stands by: nobody asks it unless a theme shows its
    temperature."""
    monkeypatch.setattr(base, "_WANTED", {})
    sysfs.hwmon(0, "drivetemp", temp1_input=36000)
    provider = make()
    provider.close()
    provider._stop.clear()
    provider.scan()
    provider.read_slow()
    assert "temp.drivetemp.0" not in provider.read()
    theme = show("temp.*")
    provider.read_slow()
    assert provider.read()["temp.drivetemp.0"].value == 36.0
    del theme


# -- coming and going -------------------------------------------------------------------------


def test_a_chip_that_appears_later_is_found_by_the_next_scan(sysfs, make):
    sysfs.hwmon(0, "k10temp", temp1_input=50000, temp1_label="Tctl")
    provider = make()
    assert "fan.lpc.fan_1" not in provider.read()
    sysfs.hwmon(1, "nct6798", fan1_input=820)  # system-setup loaded the module
    assert "fan.lpc.fan_1" not in provider.read()  # not yet: a scan costs a listing
    provider.clock.advance(RESCAN_SECONDS)
    assert provider.read()["fan.lpc.fan_1"].value == 820.0


def test_a_chip_that_goes_takes_its_readings_with_it(sysfs, make):
    sysfs.hwmon(0, "k10temp", temp1_input=50000, temp1_label="Tctl")
    provider = make()
    assert "cpu.temp" in provider.read()
    (sysfs.root / "sys/class/hwmon/hwmon0/temp1_input").unlink()
    assert "cpu.temp" not in provider.read()  # a file that is gone is a reading that is gone
    provider.clock.advance(RESCAN_SECONDS)
    assert provider.read() == {}


def test_readings_follow_the_files(sysfs, make):
    sysfs.hwmon(0, "k10temp", temp1_input=50000, temp1_label="Tctl")
    provider = make()
    assert provider.read()["cpu.temp"].value == 50.0
    sysfs.set("sys/class/hwmon/hwmon0/temp1_input", 71250)
    assert provider.read()["cpu.temp"].value == 71.25


# -- the processor's power -----------------------------------------------------------------


def test_the_processors_power_is_the_energy_between_two_readings(sysfs, make):
    sysfs.powercap("intel-rapl:0", "package-0", energy=1_000_000)
    sysfs.powercap("intel-rapl:0:0", "core", energy=500_000)
    provider = make()
    assert "cpu.power" not in provider.read()  # one reading has nothing to compare with
    provider.clock.advance(2.0)
    sysfs.set("sys/class/powercap/intel-rapl:0/energy_uj", 1_000_000 + 30_000_000)
    reading = provider.read()["cpu.power"]
    assert (reading.value, reading.unit, reading.label) == (15.0, "W", "CPU power")


def test_a_counter_that_starts_again_is_not_a_negative_power(sysfs, make):
    sysfs.powercap("intel-rapl:0", "package-0", energy=262_143_000_000, wrap=262_143_328_850)
    provider = make()
    provider.read()
    provider.clock.advance(1.0)
    sysfs.set("sys/class/powercap/intel-rapl:0/energy_uj", 20_000_000 - 328_850)
    # 328,850 µJ to the end of the range, then 19,671,150 µJ: 20 J in a second
    assert provider.read()["cpu.power"].value == pytest.approx(20.0)


def test_two_sockets_add_up_and_the_memory_mapped_copy_is_not_added(sysfs, make):
    sysfs.powercap("intel-rapl:0", "package-0", energy=0)
    sysfs.powercap("intel-rapl:1", "package-1", energy=0)
    sysfs.powercap("intel-rapl-mmio:0", "package-0", energy=0)
    provider = make()
    provider.read()
    provider.clock.advance(1.0)
    for zone, joules in (("intel-rapl:0", 10), ("intel-rapl:1", 6), ("intel-rapl-mmio:0", 99)):
        sysfs.set(f"sys/class/powercap/{zone}/energy_uj", joules * 1_000_000)
    assert provider.read()["cpu.power"].value == pytest.approx(16.0)


def test_counters_nobody_may_read_give_no_power_and_no_error(sysfs, make, monkeypatch):
    sysfs.powercap("intel-rapl:0", "package-0", energy=5)
    real = hwmon.read_text

    def refuse(path):
        return None if path.name == "energy_uj" else real(path)

    monkeypatch.setattr(hwmon, "read_text", refuse)
    provider = make()
    for _ in range(3):
        provider.clock.advance(1.0)
        assert "cpu.power" not in provider.read()


def test_the_power_of_a_chip_stands_in_where_rapl_cannot_be_read(sysfs, make):
    sysfs.hwmon(
        0,
        "zenpower",
        power1_input=41_000_000,
        power1_label="SVI2_P_Core",
        power2_input=9_500_000,
        power2_label="SVI2_P_SoC",
    )
    reading = make().read()["cpu.power"]
    assert reading.value == pytest.approx(50.5) and reading.unit == "W"


def test_amd_energy_counts_the_sockets(sysfs, make):
    sysfs.hwmon(
        0,
        "amd_energy",
        energy1_input=0,
        energy1_label="Ecore000",
        energy2_input=1_000_000,
        energy2_label="Esocket0",
    )
    provider = make()
    provider.read()
    provider.clock.advance(2.0)
    sysfs.set("sys/class/hwmon/hwmon0/energy2_input", 1_000_000 + 80_000_000)
    assert provider.read()["cpu.power"].value == pytest.approx(40.0)


def test_rapl_counters_are_found_and_judged_unreadable_by_rights(sysfs):
    sysfs.powercap("intel-rapl:0", "package-0", energy=1)
    sysfs.powercap("intel-rapl:0:0", "core", energy=1)
    sysfs.powercap("psys", "psys", energy=1)
    counters = rapl.package_counters(sysfs.root)
    assert [c.path.parent.name for c in counters] == ["intel-rapl:0"]
    assert rapl.unreadable(counters, can_read=lambda path: False) is True
    assert rapl.unreadable(counters, can_read=lambda path: True) is False
    assert rapl.unreadable([], can_read=lambda path: False) is False  # nothing to be denied


# -- the graphics card -------------------------------------------------------------------------


def test_an_amd_card_gives_all_it_has(sysfs, make):
    amd_card(sysfs)
    out = make().read()
    assert out["gpu.temp"].value == 51.0 and out["gpu.temp"].origin == "temp.amdgpu.edge"
    assert out["gpu.hotspot"].value == 63.0 and out["gpu.hotspot"].origin == "temp.amdgpu.junction"
    assert out["gpu.mem.temp"].value == 58.0
    assert out["gpu.fan"].value == 1180.0 and out["gpu.fan"].origin == "fan.amdgpu.0"
    assert out["gpu.fan.percent"].value == pytest.approx(40.0)
    assert out["gpu.power"].value == pytest.approx(142.0)
    assert out["gpu.freq"].value == pytest.approx(2400.0)
    assert out["gpu.mem.freq"].value == pytest.approx(1124.0)
    assert out["gpu.load"].value == 37.0 and out["gpu.load"].unit == "%"
    assert out["gpu.mem.used"].value == 6144.0 and out["gpu.mem.used"].unit == "MB"
    assert out["gpu.mem.total"].value == 16384.0
    assert out["gpu.mem.load"].value == pytest.approx(37.5)


def test_a_list_shows_the_cards_temperatures_once(sysfs, make):
    amd_card(sysfs)
    out = make().read()
    keys = [item.key for item in lists.pick({"items": "gpu.temp = GPU\ntemp.*"}, out)]
    assert keys[0] == "gpu.temp" and "temp.amdgpu.edge" not in keys
    assert "temp.amdgpu.junction" in keys


def test_the_main_card_is_the_one_whose_name_gpu_name_gives(sysfs, make, monkeypatch):
    amd_card(sysfs, number=0, hwmon=3, slot="0000:06:00.0")  # an AMD card with 16 GB
    sysfs.hwmon(7, "amdgpu", temp1_input=40000, temp1_label="edge")
    sysfs.card(1, 0x1002, 0x1681, "amdgpu", "0000:0c:00.0", vram=512 * 1024**2, hwmon=7)
    provider = make()
    assert provider.read()["gpu.temp"].origin == "temp.amdgpu.edge"  # the one with most memory
    # a name that says the other card, as [sensors.librehardwaremonitor] gpu does everywhere
    monkeypatch.setattr(
        hardware,
        "scan",
        lambda: hardware.Found(
            adapters=(
                Adapter("Big Card", 16 * 1024**3, slot="0000:06:00.0"),
                Adapter("Small Graphics", 512 * 1024**2, slot="0000:0c:00.0"),
            )
        ),
    )
    provider.follow_config(config_with(librehardwaremonitor={"gpu": "small"}))
    assert provider.read()["gpu.temp"].value == 40.0
    provider.follow_config(config_with(linux={"gpu": "big"}, librehardwaremonitor={"gpu": "small"}))
    assert provider.read()["gpu.temp"].value == 51.0  # [sensors.linux] is asked first


def config_with(**tables):
    from types import SimpleNamespace

    return SimpleNamespace(sensors=SimpleNamespace(options=tables))


def test_a_card_that_sleeps_is_not_asked(sysfs, make, monkeypatch):
    """Reading a laptop's second card powers it on: its files are left alone while it is off."""
    amd_card(sysfs, status="suspended")
    asked = []
    for name in ("read_text", "read_number"):
        real = getattr(hwmon, name)

        def spy(path, *args, _real=real, **kwargs):
            asked.append(Path(path).name)
            return _real(path, *args, **kwargs)

        monkeypatch.setattr(hwmon, name, spy)
        monkeypatch.setattr(gpus, name, spy) if hasattr(gpus, name) else None
    provider = make()
    provider.read()
    asked.clear()
    out = provider.read()
    assert not any(key.startswith("gpu.") or "amdgpu" in key for key in out)
    forbidden = {"gpu_busy_percent", "mem_info_vram_used", "temp1_input", "fan1_input"}
    assert not forbidden & set(asked)
    sysfs.set("sys/class/drm/card0/device/power/runtime_status", "active")
    assert "gpu.temp" in make().read()


def test_intel_cards_count_energy_and_tell_their_clock(sysfs, make):
    sysfs.hwmon(2, "xe", energy1_input=0, temp2_input=55000, temp2_label="pkg")
    sysfs.card(0, 0x8086, 0x56A0, "xe", "0000:03:00.0", hwmon=2)
    sysfs.write("sys/class/drm/card0/device/tile0/gt0/freq0/cur_freq", 2100)
    provider = make()
    out = provider.read()
    assert "gpu.power" not in out  # the first reading of a counter has nothing to compare with
    assert out["gpu.temp"].value == 55.0 and out["gpu.freq"].value == 2100.0
    provider.clock.advance(2.0)
    sysfs.set("sys/class/hwmon/hwmon2/energy1_input", 60_000_000)
    assert provider.read()["gpu.power"].value == pytest.approx(30.0)
    assert "gpu.load" not in provider.read()  # nothing in sysfs says how busy it is


def test_older_i915_keeps_its_clock_beside_the_card(sysfs, make):
    sysfs.card(0, 0x8086, 0x4680, "i915", "0000:00:02.0")
    sysfs.write("sys/class/drm/card0/gt_cur_freq_mhz", 1300)
    assert make().read()["gpu.freq"].value == 1300.0


# -- NVIDIA, through its library -------------------------------------------------------------


def nvidia_pc(sysfs, **state):
    sysfs.card(0, 0x10DE, 0x2786, "nvidia", "0000:01:00.0", **state)
    return FakeLibrary(
        {
            "0000:01:00.0": dict(
                temp=61,
                fan=43,
                power=181_500,
                load=88,
                total=12 * 1024**3,
                used=3 * 1024**3,
                graphics=2745,
                memory=10501,
            )
        }
    )


def test_an_nvidia_card_is_read_through_nvml(sysfs, make):
    library = nvidia_pc(sysfs)
    out = make(opener=lambda: nvml.Nvml(library)).read()
    assert out["gpu.temp"].value == 61.0 and out["gpu.temp"].origin == "temp.nvidia.gpu"
    assert out["temp.nvidia.gpu"].value == 61.0
    assert out["gpu.fan.percent"].value == 43.0 and "gpu.fan" not in out  # no RPM from this driver
    assert out["gpu.power"].value == pytest.approx(181.5)
    assert out["gpu.load"].value == 88.0
    assert out["gpu.mem.used"].value == 3072.0 and out["gpu.mem.total"].value == 12288.0
    assert out["gpu.mem.load"].value == pytest.approx(25.0)
    assert out["gpu.freq"].value == 2745.0 and out["gpu.mem.freq"].value == 10501.0


def test_a_driver_that_tells_rpm_gives_gpu_fan(sysfs, make):
    library = nvidia_pc(sysfs)
    library.devices["0000:01:00.0"]["rpm"] = 1450
    out = make(opener=lambda: nvml.Nvml(library)).read()
    assert out["gpu.fan"].value == 1450.0 and out["gpu.fan"].origin == "fan.nvidia.0"
    assert out["fan.nvidia.0"].unit == "RPM"


def test_what_a_card_does_not_tell_is_left_out(sysfs, make):
    library = nvidia_pc(sysfs)
    del library.devices["0000:01:00.0"]["fan"], library.devices["0000:01:00.0"]["power"]
    out = make(opener=lambda: nvml.Nvml(library)).read()
    assert "gpu.fan.percent" not in out and "gpu.power" not in out
    assert out["gpu.temp"].value == 61.0


def test_nvml_is_not_started_for_a_card_that_sleeps_or_when_switched_off(sysfs, make):
    library = nvidia_pc(sysfs, status="suspended")
    asked = []
    provider = make(opener=lambda: asked.append(1) or nvml.Nvml(library))
    assert provider.read() == {} and asked == [] and library.inits == 0
    sysfs.set("sys/class/drm/card0/device/power/runtime_status", "active")
    provider.clock.advance(RESCAN_SECONDS)
    assert provider.read()["gpu.temp"].value == 61.0 and asked == [1]
    off = make(nvidia="off", opener=lambda: asked.append(2) or nvml.Nvml(library))
    assert off.read() == {} and asked == [1]


def test_without_the_library_an_nvidia_card_has_no_gpu_readings(sysfs, make):
    sysfs.card(0, 0x10DE, 0x2786, "nvidia", "0000:01:00.0")
    out = make(opener=lambda: None).read()
    assert not any(key.startswith("gpu.") for key in out)


def test_the_open_driver_of_an_nvidia_card_is_read_through_hwmon(sysfs, make):
    sysfs.hwmon(3, "nouveau", temp1_input=48000, fan1_input=1000, pwm1=128)
    sysfs.card(0, 0x10DE, 0x2786, "nouveau", "0000:01:00.0", hwmon=3)
    out = make(opener=lambda: pytest.fail("nouveau has no use for NVML")).read()
    assert out["gpu.temp"].value == 48.0 and out["gpu.fan"].value == 1000.0
    assert out["gpu.fan.percent"].value == pytest.approx(50.2, abs=0.1)


def test_two_cards_the_second_has_keys_of_its_own(sysfs, make):
    amd_card(sysfs, number=0, hwmon=3, slot="0000:06:00.0")
    sysfs.card(1, 0x10DE, 0x2786, "nvidia", "0000:01:00.0")
    library = FakeLibrary({"0000:01:00.0": dict(temp=61, load=5)})
    out = make(opener=lambda: nvml.Nvml(library)).read()
    # an NVIDIA card is the main one (as gpu.name has it), the AMD card keeps its chip's keys
    assert out["gpu.temp"].value == 61.0 and out["temp.amdgpu.edge"].value == 51.0
    assert "gpu.fan" not in out and "gpu.power" not in out  # the main card has none


# -- the source in a hub -----------------------------------------------------------------------


def test_the_source_has_a_name_and_says_what_it_found(sysfs, make):
    sysfs.hwmon(0, "k10temp", temp1_input=50000, temp1_label="Tctl")
    sysfs.hwmon(1, "nct6798", fan1_input=800, fan2_input=900)
    amd_card(sysfs, hwmon=3)
    sysfs.powercap("intel-rapl:0", "package-0", energy=1)
    provider = make()
    provider.read()
    assert provider.name == "linux"
    assert provider.status() == {
        "chips": 3,
        "temperatures": 4,
        "fans": 3,
        "graphics": "amdgpu",
        "nvml": None,
        "cpu power": "RAPL",
    }
    assert base.available_providers()["linux"] == "built in"
    assert isinstance(base.create_provider("linux"), LinuxProvider)


def test_in_a_hub_it_stands_before_psutil_and_wins(sysfs, make, monkeypatch):
    sysfs.hwmon(0, "k10temp", temp1_input=50000, temp1_label="Tctl")
    hub = SensorHub([make()])
    assert hub.snapshot().value("cpu.temp") == 50.0


def test_reading_never_raises_whatever_the_files_are(sysfs, make):
    sysfs.hwmon(0, "k10temp", temp1_input="garbage", temp1_label="Tctl", fan1_input="")
    sysfs.card(0, 0x1002, 0x744C, "amdgpu", "0000:03:00.0", gpu_busy_percent="x", hwmon=0)
    provider = make()
    for _ in range(3):
        provider.clock.advance(RESCAN_SECONDS)
        assert isinstance(provider.read(), dict)


def test_the_thread_ends_with_the_source(sysfs):
    sysfs.hwmon(0, "nvme", temp1_input=40000, temp1_label="Composite")
    provider = LinuxProvider(root=sysfs.root)
    provider.read()
    thread = provider._thread
    assert thread is not None and thread.is_alive()
    provider.close()
    assert not thread.is_alive()
    assert threading.current_thread().name  # (the test thread is still here)
