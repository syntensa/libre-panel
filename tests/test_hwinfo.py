"""HWiNFO as a sensor source: its shared memory read, the readings named as
LibreHardwareMonitor's are (sensors.hwinfo)."""

import struct

from libre_panel.sensors import hwinfo

SENSORS = [
    "CPU [#0]: AMD Ryzen 9 7950X",
    "CPU [#0]: AMD Ryzen 9 7950X: Enhanced",
    "GPU [#0]: NVIDIA GeForce RTX 4090: ",
    "ASUS ROG CROSSHAIR X670E HERO (Nuvoton NCT6799D)",
    "S.M.A.R.T.: Samsung SSD 990 PRO 2TB",
]
# (type, sensor, label, unit, value)
READINGS = [
    (6, 0, "Core 0 Clock (perf #1)", "MHz", 5450.0),
    (1, 1, "CPU (Tctl/Tdie)", "°C", 71.5),
    (7, 0, "Total CPU Usage", "%", 23.0),
    (5, 1, "CPU Package Power", "W", 98.2),
    (6, 0, "Core Clocks (avg)", "MHz", 5012.0),
    (1, 2, "GPU Temperature", "°C", 62.0),
    (1, 2, "GPU Hot Spot Temperature", "°C", 74.0),
    (7, 2, "GPU Core Load", "%", 97.0),
    (5, 2, "GPU Power", "W", 412.0),
    (6, 2, "GPU Clock", "MHz", 2745.0),
    (8, 2, "GPU Memory Allocated", "MB", 14123.0),
    (3, 2, "GPU Fan1", "RPM", 1650.0),
    (3, 3, "Chassis1", "RPM", 900.0),
    (1, 4, "Drive Temperature", "°C", 44.0),
    (8, 4, "Read Rate", "MB/s", 12.5),
    (1, 3, "Broken", "°C", float("nan")),
]


def memory_of(sensors=SENSORS, readings=READINGS, version=2, signature=hwinfo.SIGNATURE,
              polled=1_700_000_000):  # fmt: skip
    """HWiNFO's memory as it lays it out (version 2: the names in UTF-8 after each)."""
    s_size = hwinfo.SENSOR.size + (hwinfo.SENSOR_UTF.size if version >= 2 else 0)
    r_size = hwinfo.READING.size + (hwinfo.READING_UTF.size if version >= 2 else 0)
    s_at = hwinfo.HEADER.size + 4  # (a field of a newer version between)
    r_at = s_at + s_size * len(sensors)
    out = bytearray(hwinfo.HEADER.pack(signature, version, 0, polled, s_at, s_size, len(sensors),
                                       r_at, r_size, len(readings)))  # fmt: skip
    out += b"\0" * 4
    for n, name in enumerate(sensors):
        raw = name.encode("cp1252", "replace")
        out += hwinfo.SENSOR.pack(0xF000 + n, 0, raw, raw)
        if version >= 2:
            out += hwinfo.SENSOR_UTF.pack(name.encode("utf-8"))
    for n, (kind, sensor, label, unit, value) in enumerate(readings):
        raw, units = label.encode("cp1252", "replace"), unit.encode("cp1252", "replace")
        out += hwinfo.READING.pack(kind, sensor, 0x1000 + n, raw, raw, units, value, value,
                                   value, value)  # fmt: skip
        if version >= 2:
            out += hwinfo.READING_UTF.pack(label.encode("utf-8"), unit.encode("utf-8"))
    return out


class Memory:
    def __init__(self, data):
        self.data = data
        self.closed = False

    def read(self, offset, size):
        if offset + size > len(self.data):
            raise ValueError("beyond")
        return bytes(self.data[offset : offset + size])

    def close(self):
        self.closed = True


def test_the_common_readings_get_the_keys_librehardwaremonitors_have():
    readings = hwinfo.readings_of(hwinfo.sensors_of(Memory(memory_of())))
    values = {key: r.value for key, r in readings.items()}
    assert values["cpu.temp"] == 71.5 and values["cpu.load"] == 23.0
    assert values["cpu.power"] == 98.2 and values["cpu.freq"] == 5012.0
    assert values["gpu.temp"] == 62.0 and values["gpu.hotspot"] == 74.0
    assert values["gpu.load"] == 97.0 and values["gpu.power"] == 412.0
    assert values["gpu.freq"] == 2745.0 and values["gpu.mem.used"] == 14123.0
    assert values["gpu.fan"] == 1650.0
    assert readings["cpu.name"].value == "Ryzen 9 7950X"  # (tidied as LibreHardwareMonitor's)
    assert readings["gpu.name"].value == "GeForce RTX 4090"
    assert values["fan.lpc.chassis1"] == 900.0  # the mainboard's fans, as LibreHardwareMonitor's
    assert any(k.startswith("temp.storage.") for k in values)  # every temperature by device
    rate = next(
        r for k, r in readings.items() if k.startswith("hwinfo:") and r.label == "Read Rate"
    )
    assert rate.unit == "B/s" and rate.value == 12.5 * 1024**2  # a rate in bytes
    assert all(not key.startswith("lhm:") for key in readings)
    assert readings["cpu.temp"].origin.startswith("hwinfo:")  # where it comes from
    assert not any(r.label == "Broken" for r in readings.values())  # (no value: left out)
    # each says the device it is read from: the editor lists the raw ones under it
    assert readings["gpu.temp"].device == "NVIDIA GeForce RTX 4090"
    assert rate.device == "Samsung SSD 990 PRO 2TB"
    assert readings["fan.lpc.chassis1"].device.startswith("ASUS ROG")


def test_names_as_the_person_named_them_and_the_first_version_too():
    named = memory_of(
        sensors=["GPU [#0]: Grafikkarte äöü"], readings=[(1, 0, "GPU Temperature", "°C", 50.0)]
    )
    readings = hwinfo.readings_of(hwinfo.sensors_of(Memory(named)))
    assert readings["gpu.name"].value.endswith("äöü") and readings["gpu.temp"].value == 50.0
    old = memory_of(version=1)
    assert hwinfo.readings_of(hwinfo.sensors_of(Memory(old)))["cpu.temp"].value == 71.5


def test_another_memory_or_sharing_switched_off_gives_nothing():
    assert hwinfo.sensors_of(Memory(memory_of(signature=0x12345678))) is None
    clock = [100.0]
    memory = Memory(memory_of(signature=hwinfo.STOPPED))
    source = hwinfo.HWiNFOProvider(open_memory=lambda: memory, clock=lambda: clock[0])
    assert source.read() == {} and source.status()["state"] == "stopped"


def test_it_reads_when_hwinfo_polled_anew_and_looks_for_it_while_it_is_gone():
    clock = [100.0]
    opened = []
    memory = Memory(memory_of())

    def open_memory():
        opened.append(clock[0])
        return memory if len(opened) > 1 else None

    source = hwinfo.HWiNFOProvider(open_memory=open_memory, clock=lambda: clock[0])
    assert source.read() == {} and source.status()["state"] == "looking"
    clock[0] += 1.5
    assert source.read() == {}  # (looked again only after LOOK_SECONDS)
    clock[0] += hwinfo.LOOK_SECONDS
    assert source.read()["cpu.temp"].value == 71.5 and source.status()["state"] == "reading"
    memory.data = memory_of(readings=[(1, 1, "CPU (Tctl/Tdie)", "°C", 80.0)])  # the same poll
    clock[0] += 2
    assert source.read()["cpu.temp"].value == 71.5  # not polled anew: as it was
    memory.data = memory_of(readings=[(1, 1, "CPU (Tctl/Tdie)", "°C", 80.0)], polled=1_700_000_002)
    clock[0] += 2
    assert source.read()["cpu.temp"].value == 80.0
    memory.data = b"\0" * 8  # HWiNFO gone mid-read
    clock[0] += 2
    assert source.read() == {} and memory.closed
    source.close()


def test_it_is_a_built_in_source():
    from libre_panel.sensors.base import available_providers, create_provider

    assert available_providers()["hwinfo"] == "built in"
    source = create_provider("hwinfo")
    assert source.name == "hwinfo" and struct.calcsize("<III128s128s16s4d") == hwinfo.READING.size
    source.close()
