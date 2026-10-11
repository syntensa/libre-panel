"""The names of the processor, graphics card and memory: found on a Windows and a Linux
computer made by hand, tidied, replaced by the user's own text, and kept out of what is shared.

Nothing here needs Windows or hardware: ``fake_hardware`` has a registry, a firmware table
and a /proc and /sys of its own. Every name in the tests is a public product's, and none of
them is the name of the machine the tests run on (a fixture in conftest.py sees to that)."""

import json
import logging
import platform
import sys
import time
from pathlib import Path

import pytest
from fake_hardware import (
    DDR4,
    DDR5,
    DISPLAY,
    LPDDR5,
    NVIDIA_INFORMATION,
    PC,
    TIDY,
    LinuxPC,
    fake_kernel32,
    gigabytes,
    memory_device,
    smbios_table,
    structure,
    windows_blob,
    windows_registry,
)
from test_app_cli import MemoryStream, StepClock

from libre_panel import app as app_module
from libre_panel import doctor
from libre_panel.app import build_hub
from libre_panel.config import (
    Config,
    HardwareConfig,
    SensorsConfig,
    load_config,
    set_config_value,
    write_default_config,
)
from libre_panel.devices.base import DeviceError
from libre_panel.sensors import hardware, smbios
from libre_panel.sensors.base import (
    Reading,
    SensorHub,
    SensorProvider,
    available_providers,
    create_provider,
)
from libre_panel.sensors.demo import DemoProvider
from libre_panel.sensors.hardware import (
    Adapter,
    Found,
    HardwareProvider,
    describe,
    detected_names,
    memory_label,
    pick_adapter,
    scan,
)
from libre_panel.sensors.smbios import Module

REAL_SCAN = hardware._scan_machine  # conftest.py puts a computer with nothing in its place


@pytest.fixture
def machine(monkeypatch):
    """A computer for ``scan`` to find: ``machine(found)`` (``keep``: what was found before
    stays in the cache). ``machine.asked`` has an entry for every time it was asked."""
    asked = []

    def use(found, keep=False):
        if not keep:
            monkeypatch.setattr(hardware, "_found", None)
        monkeypatch.setattr(hardware, "_scan_machine", lambda *args: asked.append(1) or found)

    use.asked = asked
    return use


# -- the memory modules of an SMBIOS table ----------------------------------------------------


def test_the_modules_come_from_the_memory_devices_of_the_table():
    table = smbios_table(
        memory_device(16384, DDR5, 5600, 5200, "Kingston"),
        memory_device(0),  # an empty slot
        memory_device(8192, DDR4, 3200, None, "Micron Technology"),
    )
    assert smbios.memory_modules(table) == [
        Module(16384, "DDR5", 5200, "Kingston"),  # what it runs at, not what it is made for
        Module(8192, "DDR4", 3200, "Micron Technology"),
    ]


KINGSTON = Module(16384, "DDR5", 5600, "Kingston")


@pytest.mark.parametrize(
    ("device", "module"),
    [
        (dict(megabytes=65536, size_field=0x7FFF), Module(65536, "DDR5", 5600, "Kingston")),
        (dict(size_field=0x8000 | 2048), Module(2, "DDR5", 5600, "Kingston")),  # in kilobytes
        (dict(configured=0), KINGSTON),  # not told how fast it runs: how fast it is made for
        (dict(speed=0, configured=0), Module(16384, "DDR5", 0, "Kingston")),
        (dict(speed=0xFFFF, configured=0xFFFF), Module(16384, "DDR5", 0, "Kingston")),
        (dict(kind=LPDDR5, maker=""), Module(16384, "LPDDR5", 5600, "")),
        (dict(kind=0x03), Module(16384, "", 5600, "Kingston")),  # "DRAM" says nothing
        (dict(length=0x1B), KINGSTON),  # SMBIOS 2.3: the fields up to the part number
        (dict(length=0x15), Module(16384, "DDR5", 0, "")),  # SMBIOS 2.1: how big, what kind
    ],
)
def test_a_memory_device_is_read_by_what_its_firmware_tells(device, module):
    assert smbios.memory_modules(smbios_table(memory_device(**device))) == [module]


@pytest.mark.parametrize(
    "device",
    [
        dict(megabytes=0),  # no module in the slot
        dict(megabytes=1, size_field=0xFFFF),  # not told
        dict(detail=0x1000),  # non-volatile
        dict(kind=0x1F),  # a logical non-volatile device
        dict(length=0x10),  # older than SMBIOS 2.1
    ],
)
def test_what_is_not_working_memory_is_no_module(device):
    assert smbios.memory_modules(smbios_table(memory_device(**device))) == []


def test_serial_numbers_and_part_numbers_are_never_read():
    table = smbios_table(memory_device(16384, DDR5, 5600, 5600, "Kingston"))
    assert b"SERIAL-1234" in table and b"PART-NUMBER-X" in table  # they are in the firmware
    (module,) = smbios.memory_modules(table)
    shown = repr(module)
    assert "SERIAL" not in shown and "PART-NUMBER" not in shown and "ASSET" not in shown


@pytest.mark.parametrize(
    "data",
    [b"", b"\x11", b"\x11\x03\x00\x00", b"\x11\x04\x00\x00\x00", b"\x00" * 40],
)
def test_a_table_that_is_none_gives_no_modules_and_no_error(data):
    assert smbios.memory_modules(data) == []


def test_a_table_cut_off_in_a_structure_keeps_what_came_before():
    table = smbios_table(memory_device(8192, DDR4, 2666, 2666, "Samsung"), memory_device())
    cut = table[: len(table) - len(smbios_table()) - 60]  # into the second device
    assert smbios.memory_modules(cut) == [Module(8192, "DDR4", 2666, "Samsung")]


def test_the_strings_of_a_structure_are_found_whether_there_are_none_or_several():
    data = (
        structure(1, b"\x01\x02", ["one", "two"]) + structure(2, b"") + structure(3, b"\x01", ["x"])
    )
    found = [(kind, strings) for kind, _, strings in smbios.structures(data)]
    assert found == [(1, [b"one", b"two"]), (2, []), (3, [b"x"])]


def test_windows_gives_the_table_as_the_firmware_table_rsmb():
    table = smbios_table(memory_device(), memory_device())
    blob = windows_blob(table)
    kernel32 = fake_kernel32(blob)
    assert smbios.windows_table(kernel32) == table
    signature = int.from_bytes(b"RSMB", "big")
    assert kernel32.calls == [(signature, 0, 0), (signature, 0, len(blob))]  # its size, then it
    assert len(smbios.memory_modules(smbios.windows_table(kernel32))) == 2


def test_a_windows_without_a_table_or_with_a_broken_one_says_so():
    with pytest.raises(OSError, match="no SMBIOS table"):
        smbios.windows_table(fake_kernel32(None))
    with pytest.raises(OSError):
        smbios.windows_table(fake_kernel32(b"\0\0\0"))  # not even the header


def test_linux_gives_the_memory_structures_the_user_may_read(tmp_path):
    pc = LinuxPC(tmp_path)
    pc.modules(
        memory_device(16384, DDR4, 3200, 3200, "G.Skill"),
        memory_device(8192, DDR4, 3200, 3200, "G.Skill"),
        memory_device(4096, DDR4, 3200, 3200, "G.Skill"),
    )
    (pc.dmi / "17-2" / "raw").unlink()
    (pc.dmi / "17-2" / "raw").mkdir()  # one that cannot be read (root's only, on most systems)
    assert hardware.linux_modules(pc.dmi) == [
        Module(16384, "DDR4", 3200, "G.Skill"),
        Module(8192, "DDR4", 3200, "G.Skill"),
    ]
    assert hardware.linux_modules(tmp_path / "nothing") == []


# -- Windows ------------------------------------------------------------------------------------


def test_windows_gives_the_processors_name_from_the_registry():
    registry = windows_registry("AMD Ryzen 5 7600 6-Core Processor")
    assert hardware.windows_cpu_name(registry) == "AMD Ryzen 5 7600 6-Core Processor"
    with pytest.raises(OSError):  # no such key
        hardware.windows_cpu_name(windows_registry())


def test_windows_gives_the_display_adapters_without_the_software_ones():
    integrated = {"HardwareInformation.MemorySize": (512 * 2**20).to_bytes(4, "little")}
    registry = windows_registry(
        "",
        ("Microsoft Basic Display Adapter", {}),
        ("Microsoft Remote Display Adapter", {}),
        ("Parsec Virtual Display Adapter", {}),
        ("Citrix Indirect Display Adapter", {}),
        ("Microsoft Hyper-V Video", {}),
        ("AMD Radeon(TM) Graphics", integrated),
        ("NVIDIA GeForce RTX 4070", {"HardwareInformation.qwMemorySize": gigabytes(12)}),
        ("", {"HardwareInformation.qwMemorySize": gigabytes(1)}),  # no name
    )
    assert hardware.windows_adapters(registry) == [
        Adapter("AMD Radeon(TM) Graphics", 512 * 2**20),
        Adapter("NVIDIA GeForce RTX 4070", gigabytes(12)),
    ]


def test_windows_leaves_out_an_adapter_whose_key_it_may_not_open():
    registry = windows_registry(
        "",
        ("AMD Radeon RX 6800", {}),
        ("NVIDIA GeForce RTX 4070", {}),
    )
    registry.denied.add(DISPLAY + "\\0000")
    assert hardware.windows_adapters(registry) == [Adapter("NVIDIA GeForce RTX 4070", 0)]


@pytest.mark.parametrize(
    ("values", "memory"),
    [
        ({"HardwareInformation.qwMemorySize": gigabytes(16)}, gigabytes(16)),
        ({"HardwareInformation.qwMemorySize": gigabytes(16).to_bytes(8, "little")}, gigabytes(16)),
        ({"HardwareInformation.MemorySize": 4294967295}, 4294967295),  # the old one stops there
        (
            {"HardwareInformation.qwMemorySize": 0, "HardwareInformation.MemorySize": 2**30},
            2**30,
        ),
        ({"HardwareInformation.qwMemorySize": b""}, 0),
        ({}, 0),
    ],
)
def test_windows_tells_a_cards_memory_from_the_64_bit_value_or_else_the_old_one(values, memory):
    registry = windows_registry("", ("AMD Radeon RX 6800", values))
    assert hardware.windows_adapters(registry) == [Adapter("AMD Radeon RX 6800", memory)]


def test_windows_modules_come_from_the_firmware_table():
    table = smbios_table(memory_device(16384, DDR5, 6000, 6000, "G Skill Intl"))
    modules = hardware.windows_modules(fake_kernel32(windows_blob(table)))
    assert modules == [Module(16384, "DDR5", 6000, "G Skill Intl")]


# -- Linux --------------------------------------------------------------------------------------

X86 = """\
processor\t: 0
vendor_id\t: GenuineIntel
cpu family\t: 6
model\t\t: 151
model name\t: 12th Gen Intel(R) Core(TM) i7-12700K
stepping\t: 2

processor\t: 1
model name\t: 12th Gen Intel(R) Core(TM) i7-12700K
"""
# the name every ARM kernel gives its chips, and the board's name next to it
PI = """\
processor\t: 0
model name\t: ARMv7 Processor rev 4 (v7l)
BogoMIPS\t: 38.40

Hardware\t: BCM2835
Revision\t: a02082
Model\t\t: Raspberry Pi 3 Model B Rev 1.2
"""


@pytest.mark.parametrize(
    ("text", "name"),
    [
        (X86, "12th Gen Intel(R) Core(TM) i7-12700K"),
        (PI, "Raspberry Pi 3 Model B Rev 1.2"),
        ("processor : 0\nBogoMIPS : 108.00\nHardware : BCM2711\n", "BCM2711"),
        ("Processor : ARMv7 Processor rev 5 (v7l)\nprocessor : 0\n", "ARMv7 Processor rev 5 (v7l)"),
        ("cpu\t\t: POWER9 (raw)\nmachine\t: PowerNV\n", "POWER9 (raw)"),
        ("processor : 0\nflags : fpu vme\n", ""),
        ("", ""),
    ],
)  # fmt: skip
def test_linux_gives_the_processors_name_from_proc_cpuinfo(tmp_path, text, name):
    pc = LinuxPC(tmp_path)
    pc.write(pc.cpuinfo, text)
    assert hardware.linux_cpu_name(pc.cpuinfo) == name


def test_what_platform_knows_is_the_last_resort_unless_it_is_only_the_architecture(monkeypatch):
    for said, name in (("x86_64", ""), ("aarch64", ""), ("", ""), ("A Processor", "A Processor")):
        monkeypatch.setattr(platform, "processor", lambda said=said: said)
        assert hardware._last_resort() == name


def linux_adapters(pc):
    return hardware.linux_adapters(pc.drm, pc.nvidia, pc.pci_ids, pc.amdgpu_ids)


def test_linux_names_the_cards_by_the_driver_amds_list_or_pci_ids(tmp_path):
    pc = LinuxPC(tmp_path)
    pc.ids()
    pc.card(0, 0x8086, 0x4680)  # an Intel chip in the processor
    pc.card(1, 0x10DE, 0x2786, slot="0000:01:00.0")
    pc.write(pc.nvidia / "gpu0" / "information", NVIDIA_INFORMATION)  # "Bus Location" says which
    pc.card(2, 0x1002, 0x744C, revision=0xCC, vram=24 * 2**30)  # in AMD's list
    pc.card(3, 0x1002, 0x744C, revision=0x01)  # not there: pci.ids has it, with its siblings
    pc.card(4, 0x1002, 0xFFFF)  # nobody knows it
    assert linux_adapters(pc) == [
        Adapter("UHD Graphics 770", 0, False),
        Adapter("NVIDIA GeForce RTX 4070", 0, True),  # as the driver says it
        Adapter("AMD Radeon RX 7900 XTX", 24 * 2**30, False),
        Adapter("Radeon RX 7900 XT/7900 XTX/7900M", 0, False),
    ]


def test_a_card_without_the_nvidia_driver_is_named_by_pci_ids_and_is_a_card_of_its_own(tmp_path):
    pc = LinuxPC(tmp_path)
    pc.ids()
    pc.card(0, 0x10DE, 0x1C82)
    assert linux_adapters(pc) == [Adapter("GeForce GTX 1050 Ti", 0, True)]


def test_the_only_name_the_nvidia_driver_has_is_the_only_cards_whatever_its_address(tmp_path):
    pc = LinuxPC(tmp_path)
    pc.card(0, 0x10DE, 0x2786)  # no uevent: its address is not known
    elsewhere = NVIDIA_INFORMATION.replace("0000:01:00.0", "0000:65:00.0")
    pc.write(pc.nvidia / "gpu0" / "information", elsewhere)
    assert linux_adapters(pc) == [Adapter("NVIDIA GeForce RTX 4070", 0, True)]


def test_the_nvidia_driver_names_two_cards_each_by_its_address_or_its_folder(tmp_path):
    pc = LinuxPC(tmp_path)
    pc.card(0, 0x10DE, 0x2786, slot="0000:01:00.0")
    pc.card(1, 0x10DE, 0x1C82, slot="slot-b")
    pc.write(pc.nvidia / "a" / "information", NVIDIA_INFORMATION)  # its Bus Location
    pc.write(pc.nvidia / "slot-b" / "information", "Model: NVIDIA GeForce GTX 1050 Ti\nIRQ: 9\n")
    assert [a.name for a in linux_adapters(pc)] == [
        "NVIDIA GeForce RTX 4070",
        "NVIDIA GeForce GTX 1050 Ti",  # no Bus Location: the folder's name is the address
    ]


def test_linux_without_cards_or_a_list_has_nothing_to_name(tmp_path):
    pc = LinuxPC(tmp_path)
    assert linux_adapters(pc) == []
    pc.card(0, 0x8086, 0x4680)  # a card, and no pci.ids on this system
    assert linux_adapters(pc) == []
    nothing = tmp_path / "nothing"
    assert hardware.linux_adapters(nothing, nothing, pc.pci_ids, nothing) == []


def test_pci_ids_name_a_device_under_its_vendor_only(tmp_path):
    pc = LinuxPC(tmp_path)
    pc.ids()
    second = pc.write(
        tmp_path / "other.ids", "10de  NVIDIA Corporation\n\t1c03  GP106 [GeForce GTX 1060 6GB]\n"
    )
    paths = [tmp_path / "missing.ids", *pc.pci_ids, second]
    assert hardware.pci_name(paths, 0x10DE, 0x2786) == "GeForce RTX 4070"  # brackets: the product
    assert hardware.pci_name(paths, 0x8086, 0x1912) == "HD Graphics 530"  # none: the name
    assert hardware.pci_name(paths, 0x10DE, 0x1C03) == "GeForce GTX 1060 6GB"  # the next file
    assert hardware.pci_name(paths, 0x10DE, 0x7340) == ""  # AMD's device, not NVIDIA's
    assert hardware.pci_name(paths, 0x1234, 0x0001) == ""  # no such vendor
    assert hardware.pci_name([tmp_path / "missing.ids"], 0x10DE, 0x2786) == ""


def test_amds_list_names_a_card_by_its_device_and_revision(tmp_path):
    pc = LinuxPC(tmp_path)
    pc.ids()
    assert hardware.amdgpu_name(pc.amdgpu_ids, 0x744C, 0xC8) == "AMD Radeon RX 7900 XT"
    assert hardware.amdgpu_name(pc.amdgpu_ids, 0x744C, 0xCC) == "AMD Radeon RX 7900 XTX"
    assert hardware.amdgpu_name(pc.amdgpu_ids, 0x744C, 0x01) == ""
    assert hardware.amdgpu_name(pc.amdgpu_ids, 0x744C, None) == ""
    assert hardware.amdgpu_name(tmp_path / "missing", 0x744C, 0xC8) == ""


# -- the computer ---------------------------------------------------------------------------------


def test_a_computer_is_asked_part_by_part_and_a_part_that_fails_leaves_the_rest(
    monkeypatch, caplog
):
    def broken():
        raise PermissionError("no")

    monkeypatch.setattr(hardware, "windows_cpu_name", lambda: "Some CPU")
    monkeypatch.setattr(hardware, "windows_adapters", broken)
    monkeypatch.setattr(hardware, "windows_modules", lambda: [Module(8192, "DDR4", 3200, "")])
    monkeypatch.setattr(hardware, "_visible_memory", lambda: gigabytes(8))
    with caplog.at_level(logging.DEBUG, logger=hardware.log.name):
        found = REAL_SCAN("win32")
    assert found == Found("Some CPU", (), (Module(8192, "DDR4", 3200, ""),), gigabytes(8))
    assert "broken failed" in caplog.text  # said at debug level, and without a name in it


def test_linux_is_asked_with_its_own_readers(monkeypatch):
    monkeypatch.setattr(hardware, "linux_cpu_name", lambda: "Some CPU")
    monkeypatch.setattr(hardware, "linux_adapters", lambda: [Adapter("Some GPU")])
    monkeypatch.setattr(hardware, "linux_modules", list)
    monkeypatch.setattr(hardware, "_visible_memory", lambda: gigabytes(16))
    assert REAL_SCAN("linux") == Found("Some CPU", (Adapter("Some GPU"),), (), gigabytes(16))


def test_a_processor_the_system_does_not_name_is_named_by_platform_if_that_knows(monkeypatch):
    monkeypatch.setattr(hardware, "linux_cpu_name", lambda: "")
    monkeypatch.setattr(hardware, "linux_adapters", list)
    monkeypatch.setattr(hardware, "linux_modules", list)
    monkeypatch.setattr(platform, "processor", lambda: "A Processor")
    assert REAL_SCAN("linux").cpu == "A Processor"


def test_the_computer_is_asked_once_and_again_only_when_asked_to(machine):
    machine(PC)
    assert scan() is PC and scan() is PC and scan() is PC
    assert len(machine.asked) == 1
    other = Found(cpu="Other CPU")
    machine(other, keep=True)  # the computer changed; what was found stays until it is asked
    assert scan() is PC and len(machine.asked) == 1
    assert scan(refresh=True) is other and scan() is other
    assert len(machine.asked) == 2


def test_the_computer_running_the_tests_can_be_asked():
    """The real readers answer and do not fail, whatever this computer is (a runner may be a
    virtual machine with no card to find and no memory modules to read). What they found is
    not looked at: it is the name of this computer's owner."""
    found = REAL_SCAN()
    assert isinstance(found, Found) and found.memory > 0
    names = describe(found)
    assert set(names) == set(hardware.PARTS) and all(isinstance(n, str) for n in names.values())
    assert names["memory"]  # at the least its size


@pytest.mark.skipif(
    sys.platform != "win32", reason="the registry and the firmware table are Windows'"
)
def test_the_real_registry_and_firmware_table_of_windows():
    assert hardware.windows_cpu_name().strip()  # every Windows has its processor's name
    for adapter in hardware.windows_adapters():  # (none in a virtual machine without a driver)
        assert adapter.name and not hardware.is_software(adapter.name) and adapter.memory >= 0
    try:
        modules = hardware.windows_modules()
    except OSError:  # a virtual machine can have no SMBIOS table
        return
    assert all(module.megabytes > 0 for module in modules)


# -- what is shown --------------------------------------------------------------------------------


def test_the_card_to_name_is_a_discrete_one_or_the_one_with_the_most_memory():
    small = Adapter("AMD Radeon(TM) Graphics", gigabytes(1))
    big = Adapter("AMD Radeon RX 6800", gigabytes(16))
    nvidia = Adapter("GeForce RTX 4070", 0, discrete=True)  # its memory is not told
    assert pick_adapter([small, big]) is big
    assert pick_adapter([big, small]) is big
    assert pick_adapter([small, big, nvidia]) is nvidia
    assert pick_adapter([Adapter("a"), Adapter("b")]).name == "a"  # alike: the first
    assert pick_adapter([]) is None


def test_the_gpu_option_of_librehardwaremonitor_picks_the_card_by_name_here_too():
    big = Adapter("AMD Radeon RX 6800", gigabytes(16))
    small = Adapter("NVIDIA GeForce GTX 1650", gigabytes(4))
    assert pick_adapter([big, small], "gtx 1650") is small
    assert pick_adapter([big, small], "RTX 9999") is big  # no such card: as if not asked
    assert pick_adapter([big, small], "") is big


def test_the_memory_is_named_by_its_modules_or_else_by_its_size():
    assert memory_label(PC) == "Kingston DDR5-5600 32 GB"
    same = Module(8192, "DDR4", 3200, "Samsung")
    assert memory_label(Found(modules=(same, same, same, same))) == "Samsung DDR4-3200 32 GB"
    mixed = Found(modules=(same, Module(8192, "DDR4", 2666, "Corsair")))
    assert memory_label(mixed) == "DDR4-2666 16 GB"  # two makers: neither; the slower speed
    unknown = Found(modules=(Module(16384, "", 0, ""),))
    assert memory_label(unknown) == "16 GB"
    assert memory_label(Found(memory=int(15.4 * 2**30))) == "16 GB"  # what the system leaves
    assert memory_label(Found(memory=gigabytes(64) + 1)) == "64 GB"
    assert memory_label(Found()) == ""


def test_the_names_are_tidied_and_empty_where_nothing_was_found(machine):
    assert describe(PC) == TIDY
    assert describe(Found()) == {"cpu": "", "gpu": "", "memory": ""}
    machine(PC)
    assert detected_names() == TIDY
    assert hardware.PARTS == ("cpu", "gpu", "memory") == tuple(TIDY)


# -- the source ---------------------------------------------------------------------------------


def config_with(cpu="", gpu="", memory="", lhm_gpu=None):
    options = {"librehardwaremonitor": {"gpu": lhm_gpu}} if lhm_gpu else {}
    return Config(hardware=HardwareConfig(cpu, gpu, memory), sensors=SensorsConfig(options=options))


def values_of(provider):
    return {key: reading.value for key, reading in provider.read().items()}


def test_the_source_gives_the_three_names_tidied_and_the_speed_of_the_memory(machine):
    machine(PC)
    provider = HardwareProvider()
    readings = provider.read()
    assert {key: (r.value, r.unit, r.label) for key, r in readings.items()} == {
        "cpu.name": ("Ryzen 5 7600", "", "CPU"),
        "gpu.name": ("GeForce RTX 4070", "", "GPU"),
        "mem.name": ("Kingston DDR5-5600 32 GB", "", "Memory"),
        "mem.speed": (5600.0, "MT/s", "Memory speed"),
    }
    # only the names win over other sources' (the user's own text replaces a found name)
    assert provider.name == "hardware" and provider.overrides == hardware.NAME_KEYS
    assert {*hardware.NAME_KEYS, hardware.SPEED_KEY} == set(readings)


def test_the_memory_runs_at_the_speed_of_its_slowest_module(machine):
    slow, fast = Module(8192, "DDR4", 2666, "Corsair"), Module(8192, "DDR4", 3200, "Samsung")
    silent = Module(8192, "DDR4", 0, "")  # a module that does not tell
    assert hardware.memory_speed(Found(modules=(fast, slow))) == 2666
    assert hardware.memory_speed(Found(modules=(silent, fast))) == 3200
    assert hardware.memory_speed(Found(modules=(silent,))) == 0
    assert hardware.memory_speed(Found(memory=gigabytes(8))) == 0  # no table to read (Linux)
    machine(Found(modules=(fast, slow)))
    speed = HardwareProvider().read()[hardware.SPEED_KEY]
    assert (speed.value, speed.unit, speed.label) == (2666.0, "MT/s", "Memory speed")
    machine(Found(memory=gigabytes(8)))  # the size only: no speed to give
    assert hardware.SPEED_KEY not in HardwareProvider().read()


def test_a_name_that_was_not_found_is_no_reading(machine):
    machine(Found(cpu="Intel(R) Core(TM) i5-12400", memory=gigabytes(8)))
    assert values_of(HardwareProvider()) == {"cpu.name": "Core i5-12400", "mem.name": "8 GB"}
    machine(Found())
    assert HardwareProvider().read() == {}  # the names of other sources stay


def test_the_users_own_text_is_the_reading_as_it_is(machine):
    machine(PC)
    provider = HardwareProvider()
    provider.follow_config(config_with(cpu="AMD Ryzen 5 7600 6-Core Processor", memory="2x16 GB"))
    assert values_of(provider) == {  # not tidied: it is what the user wrote
        "cpu.name": "AMD Ryzen 5 7600 6-Core Processor",
        "gpu.name": "GeForce RTX 4070",
        "mem.name": "2x16 GB",
        "mem.speed": 5600.0,  # (a name is text; the speed is still what the modules say)
    }
    provider.follow_config(config_with())  # emptied: the found ones again
    names = dict(zip(hardware.NAME_KEYS, TIDY.values(), strict=True))
    assert values_of(provider) == {**names, "mem.speed": 5600.0}


def test_the_users_text_shows_where_nothing_was_found(machine):
    machine(Found())
    provider = HardwareProvider()
    provider.follow_config(config_with(gpu="My graphics card"))
    assert values_of(provider) == {"gpu.name": "My graphics card"}


def test_the_card_the_user_picked_for_librehardwaremonitor_is_the_card_named(machine):
    nvidia = Adapter("NVIDIA GeForce RTX 4070", gigabytes(12))
    radeon = Adapter("AMD Radeon RX 6800", gigabytes(16))
    machine(Found(adapters=(nvidia, radeon)))
    provider = HardwareProvider()
    assert provider.read()["gpu.name"].value == "Radeon RX 6800"  # the one with more memory
    provider.follow_config(config_with(lhm_gpu="RTX 4070"))
    assert provider.read()["gpu.name"].value == "GeForce RTX 4070"
    assert detected_names("rtx")["gpu"] == "GeForce RTX 4070"


def test_reading_costs_a_copy_and_never_asks_the_computer_again(machine):
    machine(PC)
    provider = HardwareProvider()
    for _ in range(500):
        provider.read()
    for _ in range(20):
        provider.follow_config(config_with(cpu="x"))
    assert len(machine.asked) == 1
    provider.read().clear()  # whoever reads may do what it likes with the dict
    assert len(provider.read()) == 4


def test_a_refresh_reaches_the_source_at_its_next_reading(machine):
    machine(PC)
    provider = HardwareProvider()
    assert provider.read()["gpu.name"].value == "GeForce RTX 4070"
    swapped = Found(
        PC.cpu, (Adapter("NVIDIA GeForce RTX 4070 SUPER", gigabytes(16)),), PC.modules, PC.memory
    )
    machine(swapped, keep=True)  # the card was changed while Libre Panel ran...
    assert provider.read()["gpu.name"].value == "GeForce RTX 4070"  # ...and nobody asked
    scan(refresh=True)
    assert provider.read()["gpu.name"].value == "GeForce RTX 4070 SUPER"


# -- the hub --------------------------------------------------------------------------------------


class Other(SensorProvider):
    """A source that has names of its own (LibreHardwareMonitor has two of them)."""

    name = "other"

    def __init__(self, **values):
        super().__init__()
        self.values = values

    def read(self):
        return {key: Reading(key, value, "", key) for key, value in self.values.items()}


def test_what_this_source_has_wins_over_a_source_before_it_and_what_it_lacks_does_not(machine):
    machine(Found(cpu="Intel(R) Core(TM) i5-12400 CPU @ 2.50GHz"))
    other = Other(**{"cpu.name": "Core i5-12400F", "gpu.name": "Other GPU", "cpu.load": 5.0})
    provider = HardwareProvider()
    hub = SensorHub([other, provider])
    snapshot = hub.snapshot()
    assert snapshot.value("cpu.name") == "Core i5-12400"  # this computer says so
    assert snapshot.value("gpu.name") == "Other GPU"  # it has no card to say: the other's
    assert snapshot.value("cpu.load") == 5.0
    assert hub.readings_from(provider) == 1 and hub.readings_from(other) == 2
    assert [p.name for p in hub.providers] == ["other", "hardware"]  # the order is the user's


def test_the_users_text_wins_over_every_other_source(machine):
    machine(PC)
    hub = SensorHub([Other(**{"cpu.name": "Plugin's CPU"}), HardwareProvider()])
    hub.follow_config(config_with(cpu="My CPU"))
    assert hub.snapshot().value("cpu.name") == "My CPU"


def test_a_source_that_does_not_override_comes_second_as_ever():
    first, second = Other(**{"cpu.name": "first"}), Other(**{"cpu.name": "second"})
    assert SensorHub([first, second]).snapshot().value("cpu.name") == "first"
    assert SensorProvider.overrides == ()


def test_the_hub_tells_the_sources_the_config_and_a_broken_one_does_not_stop_the_rest(caplog):
    heard = []

    class Listens(Other):
        def follow_config(self, config):
            heard.append(config)

    class Breaks(Other):
        name = "breaks"

        def follow_config(self, config):
            raise RuntimeError("no")

    class Bare:  # written without the base class
        name = "bare"

        def read(self):
            return {}

    config = Config()
    SensorHub([Breaks(), Bare(), Listens()]).follow_config(config)
    assert heard == [config]
    assert "sensor provider breaks failed" in caplog.text
    assert SensorProvider().follow_config(config) is None  # a source with no settings of this kind


# -- the hub of a config --------------------------------------------------------------------------


def names_of(hub):
    snapshot = hub.snapshot()
    return {key: snapshot.value(key) for key in hardware.NAME_KEYS}


def test_a_default_hub_has_the_hardware_source_after_the_others(machine):
    machine(PC)
    hub = build_hub(Config())
    try:
        providers = [p.name for p in hub.providers]
        assert providers[-1] == "hardware" and providers.count("hardware") == 1
        assert names_of(hub) == dict(zip(hardware.NAME_KEYS, TIDY.values(), strict=True))
    finally:
        hub.close()


def test_the_users_text_in_the_config_is_there_from_the_first_reading(machine):
    machine(PC)
    hub = build_hub(config_with(gpu="Card of my own"))
    try:
        assert names_of(hub)["gpu.name"] == "Card of my own"
        assert names_of(hub)["cpu.name"] == "Ryzen 5 7600"
    finally:
        hub.close()


@pytest.mark.parametrize("demo", [True, False])
def test_a_demo_keeps_its_made_up_names(machine, demo):
    machine(PC)
    hub = build_hub(Config(sensors=SensorsConfig(providers=["demo"])), demo=demo)
    try:
        assert "hardware" not in [p.name for p in hub.providers]
        names = names_of(hub)
        assert names["cpu.name"] == "8-Core Processor" and names["mem.name"] == "DDR5-5600 32 GB"
    finally:
        hub.close()


def test_a_list_that_names_the_source_puts_it_where_the_user_wants_it(machine):
    machine(PC)
    config = Config(
        hardware=HardwareConfig(cpu_name="My CPU"),
        sensors=SensorsConfig(providers=["hardware", "psutil"]),
    )
    hub = build_hub(config)
    try:
        providers = [p.name for p in hub.providers]
        assert providers[:2] == ["hardware", "psutil"] and providers.count("hardware") == 1
        assert names_of(hub)["cpu.name"] == "My CPU"  # the config reached it too
    finally:
        hub.close()


def test_the_source_is_built_in_and_listed():
    assert available_providers()["hardware"] == "built in"
    assert isinstance(create_provider("hardware"), HardwareProvider)


# -- the panel follows config.toml --------------------------------------------------------------


def test_a_changed_config_gives_the_panel_other_names_without_a_restart(isolated_home, machine):
    machine(PC)
    path = write_default_config()
    set_config_value("theme", "pico", path)
    clock = StepClock(1000.0)
    hub = SensorHub([DemoProvider({"fixed_time": 1_700_000_000}), HardwareProvider()])
    loop = app_module.MainLoop(load_config(path), hub=hub, display=MemoryStream(), clock=clock)

    def steps_until(wanted):
        deadline = time.time() + 30  # the theme is built in the background first (slow CI)
        while loop.snapshot.value("cpu.name") != wanted and time.time() < deadline:
            clock.now += 0.02
            loop.step(clock.now)
            time.sleep(0.005)
        return loop.snapshot.value("cpu.name")

    try:
        loop.step(clock.now)
        assert loop.snapshot.value("cpu.name") == "Ryzen 5 7600"  # found, in front of the demo's
        time.sleep(0.05)  # so that the next write has a new time
        set_config_value("hardware.cpu_name", "My CPU", path)
        assert steps_until("My CPU") == "My CPU"
        assert loop.config.hardware.cpu_name == "My CPU"
        time.sleep(0.05)
        set_config_value("hardware.cpu_name", "", path)  # emptied: as found again
        assert steps_until("Ryzen 5 7600") == "Ryzen 5 7600"
    finally:
        loop.close()


def test_a_name_written_in_the_editor_reaches_the_panel_that_runs(isolated_home, machine):
    """The whole way: the editor's POST writes config.toml, the running panel loop sees the
    file, the sensors take the text, and what the panel draws from has it."""
    from test_autostart import FakeTasks
    from test_editor_server import request
    from test_service import FakeRegistry, wait_for, write_config

    from libre_panel.autostart import Autostart
    from libre_panel.service import BackgroundApp

    machine(PC)
    write_config(isolated_home, theme="pico")
    app = BackgroundApp(
        port=0, autostart=Autostart("win32", registry=FakeRegistry(), tasks=FakeTasks())
    )
    port = int(app.start().rsplit(":", 1)[1].strip("/"))
    try:
        assert wait_for(lambda: app.panel.state()["state"] == "showing", 30)
        shown = app.plugin_host
        assert wait_for(lambda: shown.latest and shown.latest.value("gpu.name") == TIDY["gpu"], 30)
        assert request(port, "POST", "/api/hardware", {"gpu": "My card"})[0] == 200
        assert wait_for(lambda: shown.latest.value("gpu.name") == "My card", 30)
        assert request(port, "POST", "/api/hardware", {"gpu": ""})[0] == 200
        assert wait_for(lambda: shown.latest.value("gpu.name") == TIDY["gpu"], 30)
    finally:
        app.quit()
        app.shutdown()


# -- what stays on this computer -----------------------------------------------------------------


def test_no_name_is_written_to_the_log(machine, monkeypatch, caplog):
    """The log is pasted into issues: it says that a part could not be read, never what a
    part is called."""
    caplog.set_level(logging.DEBUG)

    def no_table():
        raise OSError("no SMBIOS table")

    monkeypatch.setattr(hardware, "windows_cpu_name", lambda: PC.cpu)
    monkeypatch.setattr(hardware, "windows_adapters", lambda: list(PC.adapters))
    monkeypatch.setattr(hardware, "windows_modules", no_table)
    monkeypatch.setattr(hardware, "_found", None)
    monkeypatch.setattr(hardware, "_scan_machine", lambda: REAL_SCAN("win32"))
    hub = build_hub(config_with(cpu="My own CPU"))
    try:
        hub.snapshot()
        hub.follow_config(config_with(cpu="My own CPU", gpu="My own GPU"))
        assert names_of(hub)["cpu.name"] == "My own CPU"
    finally:
        hub.close()
    # it did say something: that a processor and a card were found, no module could be read
    assert "hardware: processor named, 1 graphics card(s), 0 memory module(s)" in caplog.text
    names = [PC.cpu, "Ryzen 5 7600", "RTX 4070", "GeForce", "My own CPU", "My own GPU"]
    assert not [name for name in names if name in caplog.text]


def test_no_name_is_in_the_report_of_doctor(machine):
    """doctor writes a report that people attach to an issue."""
    machine(PC)
    tree = json.loads((Path(__file__).parent / "data" / "lhm_data.json").read_text("utf-8"))

    def no_panel():
        raise DeviceError("no Turing/TURZX USB panel found")

    report = doctor.Doctor(
        ask=None,
        say=lambda text: None,
        pause=lambda seconds: None,
        open_transport=no_panel,
        open_serial=lambda: None,
        sensors=lambda: doctor.sensors_check(None, "win32", lambda url: tree),
    ).run()
    text = report.text()
    assert "temperature and power readings" in text  # the step that reads LibreHardwareMonitor
    names = ["Ryzen", "RTX 4070", "GeForce", "Kingston", "DDR5", "32 GB", PC.cpu]
    assert not [name for name in names if name in text]
