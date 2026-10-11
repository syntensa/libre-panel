"""NVIDIA's library, loaded with ctypes: a fake library object has its functions, the way the
driver's has them (results are written through ``byref`` arguments, codes are returned)."""

import ctypes

import pytest

from libre_panel.sensors import nvml

NOT_SUPPORTED = 3


class FakeLibrary:
    """What ``libnvidia-ml.so.1`` offers, over ``{PCI address: values}``. The values are
    ``temp`` (°C), ``fan`` (%), ``rpm``, ``power`` (mW), ``load`` (%), ``total`` and ``used``
    (bytes), ``graphics`` and ``memory`` (MHz); one that is missing is a card without it (the
    code 3, "not supported")."""

    def __init__(self, devices, init_code=0, rpm_function=True):
        self.devices = devices
        self.order = list(devices)
        self.init_code = init_code
        self.inits = 0
        self.shutdowns = 0
        self.asked = []  # (function, slot) of every query
        self.handles = {}
        if not rpm_function:
            self.nvmlDeviceGetFanSpeedRPM = None  # (getattr then finds nothing callable)

    def __getattr__(self, name):
        raise AttributeError(name)  # a function the driver does not have

    def _handle(self, slot):
        handle = 100 + self.order.index(slot)
        self.handles[handle] = slot
        return handle

    def _slot(self, handle):
        return self.handles[handle.value]

    def _state(self, function, handle):
        slot = self._slot(handle)
        self.asked.append((function, slot))
        return self.devices[slot]

    # -- start and find -------------------------------------------------------------------

    def nvmlInit_v2(self):  # noqa: N802 - the driver's names
        self.inits += 1
        return self.init_code

    def nvmlShutdown(self):  # noqa: N802
        self.shutdowns += 1
        return 0

    def nvmlDeviceGetCount_v2(self, out):  # noqa: N802
        out._obj.value = len(self.order)
        return 0

    def nvmlDeviceGetHandleByIndex_v2(self, index, out):  # noqa: N802
        if index.value >= len(self.order):
            return 2
        out._obj.value = self._handle(self.order[index.value])
        return 0

    def nvmlDeviceGetHandleByPciBusId_v2(self, bus, out):  # noqa: N802
        slot = bus.decode()
        if slot not in self.devices:
            return 6  # not found
        out._obj.value = self._handle(slot)
        return 0

    # -- ask --------------------------------------------------------------------------------

    def _number(self, function, handle, key, out, scale=1):
        state = self._state(function, handle)
        if key not in state:
            return NOT_SUPPORTED
        out._obj.value = int(state[key] * scale)
        return 0

    def nvmlDeviceGetTemperature(self, handle, sensor, out):  # noqa: N802
        assert sensor == 0  # the chip's own sensor
        return self._number("temperature", handle, "temp", out)

    def nvmlDeviceGetFanSpeed(self, handle, out):  # noqa: N802
        return self._number("fan", handle, "fan", out)

    def nvmlDeviceGetFanSpeedRPM(self, handle, out):  # noqa: N802
        info = out._obj
        assert info.version == (12 | (1 << 24))  # the size of the struct, and version 1
        state = self._state("rpm", handle)
        if "rpm" not in state:
            return NOT_SUPPORTED
        info.speed = state["rpm"]
        return 0

    def nvmlDeviceGetPowerUsage(self, handle, out):  # noqa: N802
        return self._number("power", handle, "power", out)

    def nvmlDeviceGetUtilizationRates(self, handle, out):  # noqa: N802
        state = self._state("utilization", handle)
        if "load" not in state:
            return NOT_SUPPORTED
        out._obj.gpu = state["load"]
        return 0

    def nvmlDeviceGetMemoryInfo(self, handle, out):  # noqa: N802
        state = self._state("memory", handle)
        if "total" not in state:
            return NOT_SUPPORTED
        out._obj.total = state["total"]
        out._obj.used = state["used"]
        out._obj.free = state["total"] - state["used"]
        return 0

    def nvmlDeviceGetClockInfo(self, handle, kind, out):  # noqa: N802
        key = {0: "graphics", 2: "memory"}[kind]
        return self._number("clock", handle, key, out)


SLOT = "0000:01:00.0"
CARD = {
    "temp": 61,
    "fan": 43,
    "power": 181_500,
    "load": 88,
    "total": 12 * 1024**3,
    "used": 3 * 1024**3,
    "graphics": 2745,
    "memory": 10501,
}


def opened(**kwargs):
    library = FakeLibrary({SLOT: dict(CARD)}, **kwargs)
    return library, nvml.Nvml(library)


def test_a_card_tells_what_it_knows_in_the_units_people_use():
    library, started = opened()
    values = started.by_slot(SLOT).read()
    assert values == {
        "temp": 61.0,
        "fan_percent": 43.0,
        "power": 181.5,
        "load": 88.0,
        "mem_used": 3072.0,
        "mem_total": 12288.0,
        "mem_load": 25.0,
        "freq": 2745.0,
        "mem_freq": 10501.0,
    }


def test_a_card_without_a_fan_or_a_sensor_leaves_them_out():
    library = FakeLibrary({SLOT: {"temp": 40}})
    values = nvml.Nvml(library).by_slot(SLOT).read()
    assert values == {"temp": 40.0}


def test_newer_drivers_tell_the_fans_rpm():
    library, started = opened()
    library.devices[SLOT]["rpm"] = 1450
    assert started.by_slot(SLOT).read()["fan_rpm"] == 1450.0
    older, started = opened(rpm_function=False)
    assert "fan_rpm" not in started.by_slot(SLOT).read()  # only the percentage is known


def test_the_load_alone_costs_one_call():
    library, started = opened()
    assert started.by_slot(SLOT).utilization() == 88.0
    assert library.asked == [("utilization", SLOT)]


def test_a_card_is_found_by_its_address_or_its_number():
    library, started = opened()
    assert started.by_slot(SLOT) is started.by_slot(SLOT)  # the handle is kept
    assert started.by_slot("0000:09:00.0") is None
    assert started.by_index(0) is not None and started.by_index(1) is None
    assert started.count() == 1


def test_the_library_is_looked_for_in_the_usual_folders(monkeypatch):
    monkeypatch.setattr(nvml.sys, "platform", "linux")
    library = FakeLibrary({SLOT: dict(CARD)})
    tried = []

    def loader(name):
        tried.append(name)
        if name != "/usr/lib64/libnvidia-ml.so.1":
            raise OSError(name)
        return library

    started = nvml.Nvml.open(loader)
    assert started is not None and library.inits == 1
    assert tried[0] == "libnvidia-ml.so.1"  # the loader's own search first
    assert tried[-1] == "/usr/lib64/libnvidia-ml.so.1"


def test_no_driver_means_no_library(monkeypatch):
    monkeypatch.setattr(nvml.sys, "platform", "linux")

    def loader(name):
        raise OSError(name)

    assert nvml.Nvml.open(loader) is None


def test_a_library_that_does_not_start_is_none_and_said_in_the_log(monkeypatch, caplog):
    monkeypatch.setattr(nvml.sys, "platform", "linux")
    library = FakeLibrary({}, init_code=18)  # the driver and the library differ
    with caplog.at_level("INFO", logger=nvml.__name__):
        assert nvml.Nvml.open(lambda name: library) is None
    assert any("did not start" in record.getMessage() for record in caplog.records)


def test_only_linux_has_the_library(monkeypatch):
    monkeypatch.setattr(nvml.sys, "platform", "win32")
    assert nvml.Nvml.open(lambda name: pytest.fail("loaded")) is None


def test_a_call_that_the_driver_does_not_have_is_no_answer():
    class Old:
        def nvmlInit_v2(self):  # noqa: N802
            return 0

    started = nvml.Nvml(Old())
    assert started.call("nvmlDeviceGetCount_v2") is None and started.count() == 0
    assert started.by_index(0) is None


def test_the_one_nvml_of_the_program_is_tried_again_after_a_minute():
    answers = [None, "library"]
    clock = [0.0]
    asked = []

    def opener():
        asked.append(1)
        return answers.pop(0)

    nvml.forget()
    try:
        assert nvml.shared(opener, lambda: clock[0]) is None
        clock[0] = 59
        assert nvml.shared(opener, lambda: clock[0]) is None and len(asked) == 1
        clock[0] = 61
        assert nvml.shared(opener, lambda: clock[0]) == "library" and len(asked) == 2
        assert nvml.shared(opener, lambda: clock[0]) == "library" and len(asked) == 2  # kept
    finally:
        nvml.forget()


def test_shutdown_forgets_the_cards():
    library, started = opened()
    started.by_slot(SLOT)
    started.close()
    assert library.shutdowns == 1 and started.by_slot(SLOT) is not None


def test_the_structs_are_the_drivers():
    assert ctypes.sizeof(nvml._Utilization) == 8
    assert ctypes.sizeof(nvml._Memory) == 24
    assert ctypes.sizeof(nvml._FanSpeed) == 12
