"""The names of the processor, graphics card and memory as a panel shows them: raw names
as real systems give them (a registry, /proc/cpuinfo, LibreHardwareMonitor, pci.ids,
SMBIOS) become what a heading has room for."""

import pytest

from libre_panel.sensors.hwnames import (
    installed_gb,
    memory_name,
    size_label,
    tidy_cpu,
    tidy_gpu,
    tidy_maker,
)

CPUS = [
    # AMD: the number of cores, "with Radeon Graphics" and the maker go
    ("AMD Ryzen 5 7600 6-Core Processor", "Ryzen 5 7600"),
    ("AMD Ryzen 7 5800X 8-Core Processor            ", "Ryzen 7 5800X"),
    ("AMD Ryzen 9 7950X3D 16-Core Processor", "Ryzen 9 7950X3D"),
    ("AMD Ryzen 5 5600G with Radeon Graphics", "Ryzen 5 5600G"),
    ("AMD Ryzen 7 PRO 5850U with Radeon Graphics", "Ryzen 7 PRO 5850U"),
    ("AMD Ryzen 7 7840HS w/ Radeon 780M Graphics", "Ryzen 7 7840HS"),
    ("AMD Ryzen 3 3200G with Radeon Vega Graphics", "Ryzen 3 3200G"),
    ("AMD Ryzen 5 3400G with Radeon RX Vega 11 Graphics", "Ryzen 5 3400G"),
    ("AMD Ryzen 7 2700U with Radeon Vega Mobile Gfx", "Ryzen 7 2700U"),
    ("AMD Ryzen Threadripper 3970X 32-Core Processor", "Ryzen Threadripper 3970X"),
    ("AMD Ryzen Threadripper PRO 5995WX 64-Cores", "Ryzen Threadripper PRO 5995WX"),
    ("AMD Ryzen 5 2600 Six-Core Processor", "Ryzen 5 2600"),
    ("AMD Ryzen 7 1700 Eight-Core Processor", "Ryzen 7 1700"),
    ("AMD EPYC 7742 64-Core Processor", "EPYC 7742"),
    ("AMD FX(tm)-8350 Eight-Core Processor", "FX-8350"),
    ("AMD Athlon(tm) II X4 640 Processor", "Athlon II X4 640"),
    ("AMD Phenom(tm) II X6 1090T Processor", "Phenom II X6 1090T"),
    ("Quad-Core AMD Opteron(tm) Processor 2376", "Opteron 2376"),
    ("AMD Ryzen 7 5800X3D", "Ryzen 7 5800X3D"),  # as LibreHardwareMonitor says it
    # Intel: the generation, the marks, "CPU" and the clock go
    ("12th Gen Intel(R) Core(TM) i7-12700K", "Core i7-12700K"),
    ("13th Gen Intel(R) Core(TM) i9-13900K", "Core i9-13900K"),
    ("11th Gen Intel(R) Core(TM) i7-11800H @ 2.30GHz", "Core i7-11800H"),
    ("Intel(R) Core(TM) i7-8700K CPU @ 3.70GHz", "Core i7-8700K"),
    ("Intel(R) Core(TM) i5-10400F CPU @ 2.90GHz", "Core i5-10400F"),
    ("       Intel(R) Core(TM) i7-3770 CPU @ 3.40GHz", "Core i7-3770"),
    ("Intel(R) Core(TM) i7 CPU         920  @ 2.67GHz", "Core i7 920"),
    ("Intel(R) Core(TM)2 Duo CPU     E8400  @ 3.00GHz", "Core2 Duo E8400"),
    ("Intel(R) Core(TM)2 Quad CPU    Q6600  @ 2.40GHz", "Core2 Quad Q6600"),
    ("Intel(R) Core(TM) Ultra 7 155H", "Core Ultra 7 155H"),
    ("Intel(R) Core(TM) m3-6Y30 CPU @ 0.90GHz", "Core m3-6Y30"),
    ("Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz", "Xeon E5-2680 v4"),
    ("Intel(R) Xeon(R) Gold 6248R CPU @ 3.00GHz", "Xeon Gold 6248R"),
    ("Intel(R) Xeon(R) W-2295 CPU @ 3.00GHz", "Xeon W-2295"),
    ("Intel(R) Xeon(R) Processor @ 2.80GHz", "Xeon"),
    ("Intel(R) Pentium(R) CPU G4560 @ 3.50GHz", "Pentium G4560"),
    ("Intel(R) Pentium(R) Dual-Core  CPU  E5200  @ 2.50GHz", "Pentium E5200"),
    ("Intel(R) Pentium(R) 4 CPU 3.00GHz", "Pentium 4"),  # a clock without the @
    ("Intel(R) Celeron(R) N5105 @ 2.00GHz", "Celeron N5105"),
    ("Intel(R) Celeron(R) CPU N3350 @ 1.10GHz", "Celeron N3350"),
    ("Intel(R) Atom(TM) CPU N270   @ 1.60GHz", "Atom N270"),
    ("Intel(R) N100", "N100"),
    ("Intel Core i7-12700K", "Core i7-12700K"),  # as LibreHardwareMonitor says it
    ("INTEL(R) CORE(TM) I5-9400F CPU @ 2.90GHZ", "CORE I5-9400F"),  # in capitals: cut the same
    # others, and what the tidying leaves alone
    ("AMD EPYC-Milan Processor", "EPYC-Milan"),
    ("Intel Core Processor (Skylake, IBRS)", "Core (Skylake, IBRS)"),
    (
        "Snapdragon(R) X Elite - X1E78100 - Qualcomm(R) Oryon(TM) CPU",
        "Snapdragon X Elite - X1E78100 - Qualcomm Oryon",
    ),
    ("Raspberry Pi 4 Model B Rev 1.4", "Raspberry Pi 4 Model B Rev 1.4"),
    ("Apple M1", "Apple M1"),
    # nothing would be left: the name stays, less its marks
    ("Intel(R) Processor", "Intel Processor"),
    ("8-Core Processor", "8-Core Processor"),
    ("Genuine Intel(R) CPU 0000 @ 2.60GHz", "Intel CPU 0000 @ 2.60GHz"),
    ("   ", ""),
    ("", ""),
]  # fmt: skip

GPUS = [
    ("NVIDIA GeForce RTX 4070", "GeForce RTX 4070"),
    ("NVIDIA GeForce RTX 4070 Ti SUPER", "GeForce RTX 4070 Ti SUPER"),
    ("NVIDIA GeForce RTX 2080 SUPER", "GeForce RTX 2080 SUPER"),
    ("NVIDIA GeForce GTX 1660 SUPER", "GeForce GTX 1660 SUPER"),
    ("NVIDIA GeForce GTX 1050 Ti", "GeForce GTX 1050 Ti"),
    ("NVIDIA GeForce RTX 3060 Laptop GPU", "GeForce RTX 3060 Laptop GPU"),  # part of its name
    ("NVIDIA GeForce RTX 2070 with Max-Q Design", "GeForce RTX 2070 with Max-Q Design"),
    ("NVIDIA GeForce MX150", "GeForce MX150"),
    ("NVIDIA RTX A4000", "RTX A4000"),
    ("NVIDIA Quadro P2000", "Quadro P2000"),
    ("NVIDIA TITAN RTX", "TITAN RTX"),
    ("AMD Radeon RX 7800 XT", "Radeon RX 7800 XT"),
    ("AMD Radeon RX 6600M", "Radeon RX 6600M"),
    ("AMD Radeon RX 580 Series", "Radeon RX 580 Series"),
    ("AMD Radeon Pro W6800", "Radeon Pro W6800"),
    ("AMD Radeon(TM) Graphics", "Radeon Graphics"),
    ("AMD Radeon(TM) Vega 8 Graphics", "Radeon Vega 8 Graphics"),
    ("AMD Radeon(TM) 780M Graphics", "Radeon 780M Graphics"),
    ("ATI Radeon HD 4850", "Radeon HD 4850"),
    ("Intel(R) UHD Graphics 770", "UHD Graphics 770"),
    ("Intel(R) UHD Graphics", "UHD Graphics"),
    ("Intel(R) Iris(R) Xe Graphics", "Iris Xe Graphics"),
    ("Intel(R) Iris(R) Plus Graphics 640", "Iris Plus Graphics 640"),
    ("Intel(R) HD Graphics 4600", "HD Graphics 4600"),
    ("Intel(R) Arc(TM) A770 Graphics", "Arc A770 Graphics"),
    ("GeForce RTX 4070", "GeForce RTX 4070"),  # pci.ids has no maker in the names
    ("Radeon RX 7900 XT/7900 XTX/7900M", "Radeon RX 7900 XT/7900 XTX/7900M"),
    ("Haswell-ULT Integrated Graphics Controller", "Haswell-ULT Integrated Graphics Controller"),
    ("VMware SVGA 3D", "VMware SVGA 3D"),
    ("Intel(R) Graphics", "Intel Graphics"),  # "Graphics" alone says nothing: the maker stays
    ("NVIDIA", "NVIDIA"),
    ("", ""),
]


@pytest.mark.parametrize(("raw", "tidy"), CPUS)
def test_a_processors_name_keeps_what_tells_it_apart(raw, tidy):
    assert tidy_cpu(raw) == tidy


@pytest.mark.parametrize(("raw", "tidy"), GPUS)
def test_a_graphics_cards_name_keeps_what_tells_it_apart(raw, tidy):
    assert tidy_gpu(raw) == tidy


@pytest.mark.parametrize("raw", [tidy for _, tidy in CPUS if tidy and "0000" not in tidy])
def test_a_tidy_processor_name_stays_as_it_is(raw):
    """LibreHardwareMonitor's names and the user's own are not tidied twice into something else."""
    assert tidy_cpu(raw) == raw


@pytest.mark.parametrize("raw", [tidy for _, tidy in GPUS if tidy])
def test_a_tidy_graphics_name_stays_as_it_is(raw):
    assert tidy_gpu(raw) == raw


def test_the_marks_are_gone_in_every_spelling():
    assert tidy_cpu("Intel® Core™ i5-12400") == "Core i5-12400"
    assert tidy_cpu("intel(r) core(tm) i5-12400 cpu @ 2.50ghz") == "core i5-12400"
    assert tidy_gpu("AMD Radeon™ RX 7600") == "Radeon RX 7600"


def test_no_name_is_longer_than_what_it_came_from():
    for raw, _ in CPUS + GPUS:
        assert len(tidy_cpu(raw)) <= len(raw.strip()) and len(tidy_gpu(raw)) <= len(raw.strip())


MAKERS = [
    ("Kingston", "Kingston"),
    ("Kingston Technology", "Kingston"),
    ("Kingston Technology Company, Inc.", "Kingston"),
    ("KINGSTON", "Kingston"),
    ("Micron Technology", "Micron"),
    ("Micron Technology Inc.", "Micron"),
    ("G.Skill", "G.Skill"),
    ("G-Skill", "G.Skill"),
    ("G Skill Intl", "G.Skill"),
    ("GSKILL", "G.Skill"),
    ("Corsair", "Corsair"),
    ("CORSAIR", "Corsair"),
    ("Samsung", "Samsung"),
    ("SAMSUNG", "Samsung"),
    ("SK Hynix", "SK hynix"),
    ("Hynix Semiconductor", "SK hynix"),
    ("Team Group Inc.", "TeamGroup"),
    ("TEAMGROUP INC", "TeamGroup"),
    ("A-DATA Technology", "ADATA"),
    ("ADATA Technology Co., Ltd.", "ADATA"),
    ("Patriot Memory", "Patriot"),
    ("Patriot Memory (PDP Systems)", "Patriot"),
    ("Nanya Technology", "Nanya"),
    ("Apacer Technology", "Apacer"),
    ("Transcend Information", "Transcend"),
    ("Geil", "GeIL"),
    ("PNY Technologies", "PNY"),
    ("OCZ Technology Group", "OCZ"),
    ("Lexar Co., Limited", "Lexar"),
    ("Gloway International Co Ltd", "Gloway"),
    ("Smart Modular Technologies", "Smart Modular"),
    ("Elpida", "Elpida"),
    ("Super Talent", "Super Talent"),
    # what firmware writes when it does not know: no maker, not the placeholder
    ("", ""),
    ("   ", ""),
    ("Unknown", ""),
    ("Undefined", ""),
    ("Not Specified", ""),
    ("To Be Filled By O.E.M.", ""),
    ("Manufacturer0", ""),
    ("859B", ""),  # a code, not a name
    ("0x802C", ""),
]


@pytest.mark.parametrize(("raw", "maker"), MAKERS)
def test_a_memory_makers_name_is_shortened(raw, maker):
    assert tidy_maker(raw) == maker


@pytest.mark.parametrize(
    ("visible", "installed"),
    [
        (0.9, 1), (3.7, 4), (7.4, 8), (7.7, 8), (11.5, 12), (15.4, 16), (15.9, 16), (16.0, 16),
        (19.5, 20), (22.0, 24), (25.0, 25), (30.0, 32), (31.4, 32), (31.9, 32), (47.0, 48),
        (62.5, 64), (125.0, 128), (255.0, 256), (5000.0, 5000),
    ],
)  # fmt: skip
def test_the_memory_a_computer_has_is_what_it_leaves_visible_rounded_up_to_a_size_sold(
    visible, installed
):
    assert installed_gb(int(visible * 2**30)) == installed


def test_the_memory_is_named_by_what_is_known():
    assert memory_name("Kingston", "DDR5", 5600, 32) == "Kingston DDR5-5600 32 GB"
    assert memory_name("", "DDR4", 3200, 16) == "DDR4-3200 16 GB"
    assert memory_name("Samsung", "LPDDR5", 0, 16) == "Samsung LPDDR5 16 GB"  # no speed told
    assert memory_name("Kingston", "", 3200, 8) == "Kingston 8 GB"  # a speed means nothing alone
    assert memory_name("", "", 0, 1.5) == "1.5 GB"
    assert size_label(32) == "32 GB" and size_label(0.5) == "0.5 GB"
    assert size_label(12.75) == "12.8 GB"
