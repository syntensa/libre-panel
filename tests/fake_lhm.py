"""Stand-ins for LibreHardwareMonitor's .NET objects, as pythonnet hands them over.

``str()`` of a sensor's type or an identifier is the name that LibreHardwareMonitor
prints, so plain strings do. The fakes count the calls the source makes.
"""

import copy

from libre_panel.sensors import librehardwaremonitor as lhm


class FakeSensor:
    def __init__(self, identifier, name, sensor_type, value):
        self.Identifier = identifier
        self.Name = name
        self.SensorType = sensor_type
        self.Value = value
        self.ValuesTimeWindow = "a day"


class FakeHardware:
    def __init__(self, identifier, name, hardware_type, sensors=(), sub=()):
        self.Identifier = identifier
        self.Name = name
        self.HardwareType = hardware_type
        self.Sensors = list(sensors)
        self.SubHardware = list(sub)
        self.updates = 0
        self.raises = None  # an exception for Update() to raise
        self.on_update = None  # called with this hardware after each update

    def Update(self):  # noqa: N802 - the name LibreHardwareMonitor gives it
        self.updates += 1
        if self.raises is not None:
            raise self.raises
        if self.on_update is not None:
            self.on_update(self)

    def sensor(self, name):
        return next(s for s in self.Sensors if s.Name == name)


class FakeComputer:
    def __init__(self, hardware):
        self.Hardware = list(hardware)
        self.closed = 0
        self.close_raises = None

    def Close(self):  # noqa: N802
        self.closed += 1
        if self.close_raises is not None:
            raise self.close_raises

    def everything(self):
        for item in self.Hardware:
            yield item
            yield from item.SubHardware


_TYPES = {
    "amdcpu": "Cpu",
    "intelcpu": "Cpu",
    "ram": "Memory",
    "memory": "Memory",
    "lpc": "SuperIO",
    "nic": "Network",
    "gpu-nvidia": "GpuNvidia",
    "gpu-amd": "GpuAmd",
    "gpu-intel": "GpuIntel",
}
_BYTES = {"B/s": 1, "KB/s": 1024, "MB/s": 1024**2, "GB/s": 1024**3}


def hardware_type_of(hardware_id):
    return _TYPES[hardware_id.strip("/").split("/")[0]]


def with_raw_throughput(tree):
    """The web server's tree with bytes per second on every throughput sensor, as the
    version that LibreHardwareMonitor's own library corresponds to writes them."""
    tree = copy.deepcopy(tree)

    def walk(node):
        if node.get("Type") == "Throughput" and "RawValue" not in node:
            value, unit = lhm.parse_value(node.get("Value"))
            node["RawValue"] = None if value is None else value * _BYTES[unit]
        for child in node.get("Children") or []:
            walk(child)

    walk(tree)
    return tree


def computer_from_tree(tree):
    """The same hardware and sensors as a web server's tree, with the numbers it printed
    (bytes per second for throughput, as the library has them)."""
    hardware = []

    def walk(node, current):
        if node.get("HardwareId"):
            current = FakeHardware(
                node["HardwareId"], node["Text"], hardware_type_of(node["HardwareId"])
            )
            hardware.append(current)
        if node.get("SensorId") and current is not None:
            found = lhm._leaf_value(node)
            value = None if found is None else found[0]
            current.Sensors.append(FakeSensor(node["SensorId"], node["Text"], node["Type"], value))
        for child in node.get("Children") or []:
            walk(child, current)

    walk(tree, None)
    return FakeComputer(hardware)


def small_computer():
    """A processor, a graphics card, memory with a module and a mainboard with a Super I/O
    chip (a sub-hardware): enough to tell which node is updated and what needs ring0."""
    cpu = FakeHardware(
        "/amdcpu/0",
        "8-Core Processor",
        "Cpu",
        [
            FakeSensor("/amdcpu/0/load/0", "CPU Total", "Load", 7.0),
            FakeSensor("/amdcpu/0/temperature/2", "Core (Tctl/Tdie)", "Temperature", 55.0),
            FakeSensor("/amdcpu/0/power/0", "Package", "Power", 31.0),
            FakeSensor("/amdcpu/0/clock/1", "Cores (Average)", "Clock", 4200.0),
        ],
    )
    gpu = FakeHardware(
        "/gpu-nvidia/0",
        "Graphics Card",
        "GpuNvidia",
        [
            FakeSensor("/gpu-nvidia/0/temperature/0", "GPU Core", "Temperature", 48.0),
            FakeSensor("/gpu-nvidia/0/load/0", "GPU Core", "Load", 12.0),
            FakeSensor("/gpu-nvidia/0/fan/1", "GPU Fan 1", "Fan", 0.0),
        ],
    )
    memory = FakeHardware(
        "/ram", "Total Memory", "Memory", [FakeSensor("/ram/load/0", "Memory", "Load", 40.0)]
    )
    # a memory module as LibreHardwareMonitor lists it (its DimmMemory): hardware of its own
    # under /memory/dimm/<n>, its thermal sensor "DIMM #<n>", and the limits it reports as
    # temperatures too
    module = FakeHardware(
        "/memory/dimm/1",
        "Maker - PART-1 (#1)",
        "Memory",
        [
            FakeSensor("/memory/dimm/1/temperature/0", "DIMM #1", "Temperature", 38.0),
            FakeSensor(
                "/memory/dimm/1/temperature/3", "Thermal Sensor High Limit", "Temperature", 85.0
            ),
        ],
    )
    chip = FakeHardware(
        "/lpc/nct0000/0",
        "Super I/O Chip",
        "SuperIO",
        [
            FakeSensor("/lpc/nct0000/0/fan/1", "Fan #1", "Fan", 900.0),
            FakeSensor("/lpc/nct0000/0/temperature/0", "System", "Temperature", 33.0),
        ],
    )
    board = FakeHardware("/motherboard", "Mainboard", "Motherboard", [], [chip])
    return FakeComputer([board, cpu, memory, gpu, module])


def ring0_off(computer):
    """What LibreHardwareMonitor gives without administrator rights or the driver."""
    for item in computer.everything():
        for sensor in item.Sensors:
            if item.HardwareType in ("Cpu", "SuperIO") and sensor.SensorType != "Load":
                sensor.Value = 0.0
            elif item.HardwareType == "Memory" and sensor.SensorType == "Temperature":
                sensor.Value = 0.0
    return computer


def lists_only_after_an_update(computer):
    """The Super I/O chip as LibreHardwareMonitor builds it: before its first update it lists
    its fan controls only; its fans and temperatures come with the update that reads them."""
    chip = next(item for item in computer.everything() if item.HardwareType == "SuperIO")
    later, chip.Sensors = (
        chip.Sensors,
        [FakeSensor("/lpc/nct0000/0/control/0", "Fan Control #1", "Control", 40.0)],
    )

    def reveal(hardware):
        known = {sensor.Identifier for sensor in hardware.Sensors}
        hardware.Sensors.extend(s for s in later if s.Identifier not in known)

    chip.on_update = reveal
    return chip
