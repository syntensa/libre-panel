"""rust/sensors against the Python app (docs/RUST.md): LibreHardwareMonitor's sensors as
readings (sensors/librehardwaremonitor.py), the names of the hardware (sensors/hwnames.py) and
what the sensor service and Libre Panel say to each other (sensorservice/protocol.py), as the
Rust app has them."""

from __future__ import annotations

import base64
import itertools
import json
import math
import random
import struct
import sys
import time
from typing import Any

import pytest
from rust_check import run

from libre_panel.sensors import hwnames, lhm_service
from libre_panel.sensors import librehardwaremonitor as lhm
from libre_panel.sensorservice import protocol


def _hex(value: float) -> str:
    return struct.pack(">d", value).hex()


NAMES = [
    "12th Gen Intel(R) Core(TM) i7-12700K", "Intel(R) Core(TM) i9-14900K CPU @ 3.20GHz",
    "AMD Ryzen 5 7600 6-Core Processor", "AMD Ryzen 7 5700G with Radeon Graphics",
    "AMD Ryzen 9 7950X3D 16-Core Processor", "Genuine Intel(R) CPU 0000 @ 2.40GHz",
    "Intel(R) Core(TM)2 Quad CPU Q6600 @ 2.40GHz", "Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz",
    "AMD Athlon(tm) II X4 640 Quad-Core Processor", "Intel(R) Pentium(R) CPU G4560 @ 3,50GHz",
    "NVIDIA GeForce RTX 4070", "AMD Radeon RX 7800 XT", "Intel(R) UHD Graphics 770",
    "Intel(R) Arc(TM) A770 Graphics", "Advanced Micro Devices, Inc. Radeon RX 580",
    "ATI Radeon HD 5770", "Intel Graphics", "NVIDIA", "AMD", "CPU", "", "  ", "Processor",
    "Apple M2 Pro", "Qualcomm® Snapdragon™ X Elite - X1E80100", "VIA Nano w/ Vega 8 800MHz",
    "13th Gen Intel® Core™ i5-13600KF", "Intel Corporation Core i3", "AMD, Ryzen-5 / 5600X;",
    "Kingston Technology Company, Inc.", "Kingston", "G.Skill Intl", "G-Skill", "GSKILL",
    "Hynix Semiconductor", "SK Hynix", "Team Group Inc.", "CORSAIR", "Corsair Memory, Inc.",
    "Patriot Memory (PDP)", "ADATA Technology Co., Ltd.", "PNY", "Micron Technology",
    "Crucial Technology", "Samsung Electronics Co. Ltd", "859B", "0x859b", "Unknown",
    "To Be Filled By O.E.M.", "Manufacturer0", "GEIL", "TEAMGROUP", "KLEVV MODULES USA",
    "ÉLITE MÉMOIRE GMBH", "0", "Nanya Technology Corp.", "A-DATA", "Lexar Co Limited",
]  # fmt: skip


def test_hardware_names_are_tidied_as_hwnames_tidies_them() -> None:
    rng = random.Random(0x4E)
    words = [w for name in NAMES for w in name.split()] + ["(R)", "(TM)", "@", "3.6GHz", "-", ","]
    names = NAMES + [" ".join(rng.choice(words) for _ in range(rng.randrange(1, 7)))
                     for _ in range(3000)]  # fmt: skip
    visible = [int(g * 2**30) for g in (0.4, 0.95, 1.9, 3.5, 7.6, 15.4, 15.9, 25, 31.2, 47,
                                         60, 63.9, 125.3, 200, 255.9, 5000, 0)]  # fmt: skip
    visible += [rng.randrange(0, 2**43) for _ in range(500)]
    sizes = [32.0, 1.5, 0.25, 16.04, 15.95, 1024.0, 0.0, 2.25, 2.35, 123456.789, 1e7]
    sizes += [rng.uniform(0, 300) for _ in range(300)]
    memory = [[rng.choice(["Kingston", "", "SK hynix"]), rng.choice(["DDR5", "", "DDR4"]),
               rng.choice([0, 3200, 5600]), rng.choice(sizes)] for _ in range(200)]  # fmt: skip
    [ours] = run([{"op": "hwnames", "names": names, "visible": visible, "sizes": sizes,
                   "memory": memory}])  # fmt: skip
    for n, name in enumerate(names):
        assert ours["cpu"][n] == hwnames.tidy_cpu(name), name
        assert ours["gpu"][n] == hwnames.tidy_gpu(name), name
        assert ours["maker"][n] == hwnames.tidy_maker(name), name
    assert ours["installed"] == [hwnames.installed_gb(v) for v in visible]
    assert ours["sizes"] == [hwnames.size_label(s) for s in sizes]
    assert ours["memory"] == [hwnames.memory_name(*m) for m in memory]


def _made_sensors(rng: random.Random) -> list[list[Any]]:
    """A computer's sensors as LibreHardwareMonitor names them, some of everything."""
    hardware = [
        ("/amdcpu/0", "AMD Ryzen 7 7800X3D 8-Core Processor"), ("/intelcpu/0", "Intel Core i9"),
        ("/gpu-nvidia/0", "NVIDIA GeForce RTX 4080"), ("/gpu-amd/0", "AMD Radeon(TM) Graphics"),
        ("/gpu-intel-integrated/0", "Intel(R) UHD Graphics"), ("/gpu-powermonitor/COM3",
        "WireView Pro II"), ("/memory/dimm/0", "DIMM 1"), ("/memory/dimm/1", "DIMM 2"),
        ("/ram", "Generic Memory"), ("/nvme/0", "Samsung SSD 990 PRO"), ("/hdd/1", "WDC WD40"),
        ("/lpc/nct6798d/0", "Nuvoton NCT6798D"), ("/motherboard", "ASUS ROG"),
        ("/nic/%7BAB%7D", "Ethernet"), ("/battery/0", "Battery"), ("/GPU-odd/0", "Odd one"),
    ]  # fmt: skip
    labels = {
        "Temperature": ["CPU Package", "Core (Tctl/Tdie)", "Tctl", "Tdie", "Package", "GPU Core",
                        "GPU Hot Spot", "DIMM #1", "DIMM #2", "Thermal Sensor High Limit",
                        "Onboard Temperature In", "Temperature", "CPU Core #1", "T_Sensor",
                        "gpu memory", "Composite"],
        "Load": ["CPU Total", "GPU Core", "D3D 3D", "GPU Memory", "CPU Core #1", "Memory"],
        "Power": ["CPU Package", "Package", "GPU Package", "GPU Power", "GPU Core", "Total Power"],
        "Clock": ["Cores (Average)", "Core #1", "Core #2", "CPU Core #3", "GPU Core", "Bus Speed",
                  "Core #1 (Effective)"],
        "SmallData": ["GPU Memory Used", "GPU Memory Total", "GPU Memory Free"],
        "Data": ["Memory Used", "Memory Available", "GPU Memory Total", "GPU Memory Used"],
        "Fan": ["GPU Fan", "GPU Fan 1", "Fan #1", "CPU Fan", "Pump"],
        "Current": ["Total Current", *[f"Pin {n} Current" for n in range(1, 7)]],
        "Voltage": [*[f"Pin {n} Voltage" for n in range(1, 7)], "Vcore"],
        "Throughput": ["Upload Speed", "Download Speed"],
    }  # fmt: skip
    units = {"Temperature": ["°C", ""], "Load": ["%"], "Power": ["W"], "Clock": ["MHz"],
             "SmallData": ["MB", "B", "MiB", " gb ", "KB"], "Data": ["GB", "B", "TB", "GiB", "x"],
             "Fan": ["RPM"], "Current": ["A", ""], "Voltage": ["V"],
             "Throughput": ["B/s"]}  # fmt: skip
    rows = []
    for hardware_id, name in rng.sample(hardware, rng.randrange(1, len(hardware) + 1)):
        for _ in range(rng.randrange(0, 14)):
            kind = rng.choice(list(labels))
            label = rng.choice(labels[kind])
            shown = rng.choice([kind, kind, kind.lower(), kind.upper()])
            value = rng.choice([rng.uniform(-5, 9000), float(rng.randrange(0, 5000)), 2048.0,
                                2048.5, 0.0, 3e10])  # fmt: skip
            rows.append([f"{hardware_id}/{kind.lower()}/{len(rows)}", hardware_id, name, shown,
                         label, value, rng.choice(units[kind])])  # fmt: skip
    rng.shuffle(rows)
    return rows


def _readings(found: dict[str, Any]) -> list[list[Any]]:
    return [[key, _hex(r.value) if isinstance(r.value, float) else ["str", str(r.value)],
             r.unit, r.label, r.origin, r.device] for key, r in found.items()]  # fmt: skip


def test_sensors_become_readings_as_librehardwaremonitor_py_makes_them() -> None:
    rng = random.Random(0x1A7)
    cases = [{"sensors": _made_sensors(rng),
              "gpu": rng.choice([None, None, "", "radeon", "RTX", "uhd", "none such"])}
             for _ in range(400)]  # fmt: skip
    [ours] = run([{"op": "lhm_readings", "cases": cases}])
    keys: set[str] = set()
    for case, got in zip(cases, ours, strict=True):
        sensors = [lhm.LhmSensor(*row) for row in case["sensors"]]
        sources: dict[str, tuple[str, ...]] = {}
        wanted = lhm.readings_from_sensors(sensors, case["gpu"], sources)
        assert got["readings"] == _readings(wanted), case
        assert got["sources"] == [[key, list(ids)] for key, ids in sources.items()], case
        keys.update(key for key in wanted if not key.startswith("lhm:"))
    # (every kind of key was made: the aliases, the names, the mean clock's, the memory's
    # and the cable's)
    for key in ("cpu.temp", "cpu.freq", "gpu.temp", "gpu.mem.used", "cpu.name", "gpu.name",
                "mem.temp", "gpu.cable.power", "gpu.cable.pin.max",
                "gpu.cable.pin3.voltage"):  # fmt: skip
        assert key in keys, key
    assert any(key.startswith("temp.") for key in keys)
    assert any(key.startswith("fan.") for key in keys)


def test_the_web_server_s_tree_is_read_as_librehardwaremonitor_py_reads_it() -> None:
    rng = random.Random(0x7EE)
    texts = ["45,0 °C", "1234 RPM", "NaN", "-", "", " -3.5 W ", "12.5", "1,5,3 x", "7 %\n",
             "0045.10 GB", "3e5 V", ".5 A", "5. A", "٣ °C", "-", "- 5", "12 MB / s"]  # fmt: skip

    def node(depth: int) -> dict[str, Any]:
        made: dict[str, Any] = {"Text": rng.choice(["Sensor", "CPU Package", "GPU Core", ""])}
        if rng.random() < 0.3:
            made["HardwareId"] = rng.choice(["/amdcpu/0", "/gpu-nvidia/0", "/nic/1", ""])
        if rng.random() < 0.6:
            made["SensorId"] = rng.choice(["/amdcpu/0/temperature/2", "/gpu-nvidia/0/load/0",
                                           "/nic/1/throughput/7", "/x/0",
                                           "/lpc/a/b/fan/1"])  # fmt: skip
            made["Value"] = rng.choice([*texts, None, 5])
            if rng.random() < 0.7:
                made["Type"] = rng.choice(["Temperature", "Load", "Throughput", "throughput", ""])
            if rng.random() < 0.5:
                made["RawValue"] = rng.choice([1234.5, 77, True, "9", None])
        if depth < 3 and rng.random() < 0.8:
            made["Children"] = [node(depth + 1) for _ in range(rng.randrange(0, 4))]
        return made

    trees = [node(0) for _ in range(500)]
    [ours] = run([{"op": "lhm_tree", "trees": [json.dumps(tree) for tree in trees],
                   "values": texts}])  # fmt: skip
    found = 0
    for tree, got in zip(trees, ours["trees"], strict=True):
        try:
            wanted = [[s.sensor_id, s.hardware_id, s.hardware_name, s.sensor_type, s.label,
                       _hex(s.value), s.unit] for s in lhm.sensors_from_tree(tree)]  # fmt: skip
        except IndexError:  # (a sensor id too short to name its type: none of the tree is read)
            continue
        if any(len(row[0].strip("/").split("/")) < 3 and not row[3] for row in wanted):
            continue
        assert got == wanted, tree
        found += len(wanted)
    assert found > 500
    for text, got in zip(texts, ours["values"], strict=True):
        value, unit = lhm.parse_value(text)
        assert got == (None if value is None else [_hex(value), unit]), text


def test_the_sensor_service_s_answers_are_checked_as_protocol_py_checks_them() -> None:
    rng = random.Random(0x9A)
    sensors = [lhm.LhmSensor(*row) for row in _made_sensors(rng)][:40]
    timing = lhm.Timing(
        lhm.RoundTime((lhm.PartTime("/amdcpu/0", "Ryzen", "Cpu", 0.0123),), False),
        lhm.RoundTime((lhm.PartTime("/amdcpu/0", "Ryzen", "Cpu", 0.02),
                       lhm.PartTime("/nvme/0", "", "Storage", 0.001)), True),
    )  # fmt: skip
    good = [
        protocol.hello_answer("0.8.1"),
        protocol.error_answer("no such op"),
        protocol.read_answer(sensors, state="running", age=0.4, ring0=True, pawnio="2.0.1",
                             timing=timing, memory=lhm.ServiceMemory(88080384, 120586240, None)),
        protocol.read_answer([], state="starting", age=None, ring0=None, pawnio=None),
        protocol.read_answer(sensors[:3], state="failed", age=3.0, ring0=False, pawnio="",
                             error="no .NET", timing=lhm.Timing()),
    ]  # fmt: skip
    odd: list[Any] = [None, True, False, 0, 1, 1.0, 2, -1, 10**30, 1e400, "x", "", "a" * 301,
                      "a" * 300, "a\x00b", [], {}, [1], {"full": True, "parts": []}, 0.5, -0.5,
                      2**48, 2**48 - 1, "running", "é" * 300]  # fmt: skip
    messages: list[Any] = list(good)
    for _ in range(4000):  # one thing (or two) of a good answer made another, or taken away
        message = json.loads(json.dumps(rng.choice(good)))
        for _ in range(rng.randrange(1, 3)):
            where: Any = message
            while True:
                keys = list(where) if isinstance(where, dict) else list(range(len(where)))
                if not keys:
                    break
                key = rng.choice(keys)
                inner = where[key]
                if isinstance(inner, dict | list) and inner and rng.random() < 0.7:
                    where = inner
                    continue
                if rng.random() < 0.15:
                    del where[key]
                else:
                    where[key] = rng.choice(odd)
                break
        messages.append(message)
    bodies = [json.dumps(message).encode() for message in messages]
    bodies += [b"", b"[1]", b"{", b'{"v": 1, "ok": NaN}', b'{"v":1,"ok":true,"x":Infinity}',
               b'\xff{"v":1}', b'{"v":1,"ok":true,"service":"NaN I","protocol":1}',
               b'{"v":1.0,"ok":true,"service":"s","protocol":1}',
               b'{"v":true,"ok":true,"service":"s","protocol":2}', b"null", b'"x"']  # fmt: skip
    hardware = [["/amdcpu/0", "/gpu-nvidia/0", "/amdcpu/0"], [], [f"/x/{n}" for n in range(70)],
                ["/nic/%7Bé%7D"], ["/" + "a" * 199] * 3
                + [f"/{'b' * 150}/{n}" for n in range(30)]]  # fmt: skip
    requests = [[ids, everything] for ids in hardware for everything in (False, True)]
    headers = [struct.pack("<I", n) for n in (0, 1, 4096, protocol.MAX_ANSWER,
                                               protocol.MAX_ANSWER + 1, 2**32 - 1)]  # fmt: skip
    headers += [b"", b"abc", b"abcde"]
    [ours] = run([{
        "op": "sensor_protocol", "bodies": [base64.b64encode(body).decode() for body in bodies],
        "requests": requests, "headers": [base64.b64encode(h).decode() for h in headers],
    }])  # fmt: skip

    def tried(call: Any) -> Any:
        try:
            return call()
        except protocol.ServiceError as exc:
            return {"service": str(exc)}
        except protocol.ProtocolError as exc:
            return {"protocol": str(exc)}

    def round_of(done: Any) -> Any:
        return done and {"full": done.full, "seconds": _hex(done.seconds), "parts": [
            [p.hardware_id, p.name, p.kind, _hex(p.seconds)] for p in done.parts]}  # fmt: skip

    def readings(message: dict[str, Any]) -> Any:
        r = protocol.parse_readings(message)
        return {
            "state": r.state, "error": r.error, "age": None if r.age is None else _hex(r.age),
            "ring0": r.ring0, "pawnio": r.pawnio,
            "sensors": [[s.sensor_id, s.hardware_id, s.hardware_name, s.sensor_type, s.label,
                         _hex(s.value), s.unit] for s in r.sensors],
            "timing": r.timing and {"last": round_of(r.timing.last),
                                    "full": round_of(r.timing.full)},
            "memory": r.memory and [r.memory.private, r.memory.working_set, r.memory.managed],
            "round_line": lhm_service.round_line(r.timing),
        }  # fmt: skip

    taken = refused = 0
    for body, got in zip(bodies, ours["bodies"], strict=True):
        try:
            message = protocol.decode(body)
        except protocol.ProtocolError:
            assert set(got) == {"protocol"}, body
            continue

        def hello(message: dict[str, Any] = message) -> Any:
            found = protocol.parse_hello(message)
            # (a number no protocol has is none of Rust's either: it holds it as its least)
            known = found.protocol if abs(found.protocol) < 2**63 else -(2**63)
            return [found.service, known]

        want_hello, want_readings = tried(hello), tried(lambda m=message: readings(m))
        for want, ours_one in ((want_hello, got["hello"]), (want_readings, got["readings"])):
            if isinstance(want, dict) and set(want) == {"protocol"}:
                # (refused by both; which of two faults is named first is Python's order)
                assert set(ours_one) == {"protocol"}, (body, ours_one, want)
                refused += 1
            else:
                assert ours_one == want, body
                taken += 1
    assert taken > 500 and refused > 2000
    for (ids, everything), got in zip(requests, ours["requests"], strict=True):
        try:
            want: Any = base64.b64encode(
                protocol.encode(protocol.read_request(ids, everything), protocol.MAX_REQUEST)
            ).decode()
        except protocol.ProtocolError as exc:
            want = {"protocol": str(exc)}
        assert got == want, ids
    hello = protocol.encode(protocol.hello_request(), protocol.MAX_REQUEST)
    assert base64.b64decode(ours["hello"]) == hello
    for header, got in zip(headers, ours["lengths"], strict=True):
        assert got == tried(lambda h=header: protocol.body_length(h, protocol.MAX_ANSWER)), header


def test_the_hints_say_what_librehardwaremonitor_py_says() -> None:
    # (every way LibreHardwareMonitor may be read and fail, with and without readings, for
    # the command line and for a person, in English and in German)
    import itertools

    from libre_panel import i18n

    statuses: list[dict[str, Any] | None] = [None]
    for mode, elevated, ring0, pawnio, error, service in itertools.product(
        ("service", "inprocess", "webserver"), (None, True, False), (None, True, False),
        (None, "", "2.0.1", "1.9", "10.2", "x"), ("", "no .NET"), ("", "the pipe is busy"),
    ):  # fmt: skip
        statuses.append({"mode": mode, "elevated": elevated, "ring0": ring0, "pawnio": pawnio,
                         "error": error, "service": service})  # fmt: skip
    rng = random.Random(0x41)
    cases = []
    for status in statuses:
        for keys in ([], ["cpu.temp"], ["gpu.power", "cpu.load"], ["cpu.load"]):
            cases.append({
                "keys": keys, "status": status, "windows": rng.random() < 0.9,
                "ready": rng.random() < 0.9, "unlock": rng.random() < 0.5,
                "providers": rng.choice([["psutil"], ["psutil", "librehardwaremonitor"]]),
                "url": lhm.DEFAULT_URL,
            })  # fmt: skip
    said: set[str] = set()
    for language in ("en", "de"):
        [ours] = run([{"op": "lhm_hints", "cases": cases, "locale": language,
                       "locale_dir": str(i18n.LOCALE_DIR)}])  # fmt: skip
        i18n.set_language(language)
        try:
            for case, got in zip(cases, ours, strict=True):
                readings = {key: lhm.Reading(key, 1.0) for key in case["keys"]}
                status = case["status"] and lhm.LhmStatus(**case["status"])
                platform = "win32" if case["windows"] else "linux"
                assert got["steps"] == (status and lhm.ring0_steps(status, case["unlock"])), case
                assert got["hint"] == lhm.hardware_hint(
                    readings, case["providers"], case["url"], platform, status, case["unlock"]
                ), case
                want = lhm.user_hint(readings, platform, status, case["ready"], case["unlock"])
                assert got["user"] == want, case
                said.add(want)
        finally:
            i18n.set_language("en")
    assert len(said) > 12  # (every sentence, in both languages, and none)


def test_loads_and_cores_are_worked_out_as_load_py_and_topology_py_do(tmp_path: Any) -> None:
    from collections import namedtuple

    from libre_panel.sensors import load, topology

    rng = random.Random(0x10AD)
    # the load between two samples of the system's times
    cpu: list[list[Any]] = [[None, [1, 2, 3]], [[1, 2, 3], None], [[5, 9, 9], [5, 9, 9]]]
    for _ in range(300):
        before = [rng.randrange(10**9) for _ in range(3)]
        after = [n + rng.randrange(0, 10**6) for n in before]
        if rng.random() < 0.2:
            after[rng.randrange(3)] = before[0] - rng.randrange(10)
        cpu.append([before, after])
    # the cards by their memory in use, and a card's load of its engines
    luids = ["luid_0x00000000_0x0000D2E4", "luid_0x00000000_0x00011111", "luid_0x0_0x2"]

    def instances(count: int) -> list[list[Any]]:
        made = []
        for _ in range(count):
            name = rng.choice([f"pid_{rng.randrange(9000)}_", ""]) + rng.choice(luids)
            name += rng.choice(["_phys_0", "_phys_1", ""])
            name += rng.choice([f"_eng_{rng.randrange(4)}_engtype_3D", ""])
            made.append([rng.choice([name, name, "none", ""]), rng.choice([0.0, 1.5, 250.0,
                         rng.uniform(0, 99), float(rng.randrange(2**30))])])  # fmt: skip
        return made

    memory = [instances(rng.randrange(0, 9)) for _ in range(300)]
    engines = [[rng.choice(luids), instances(rng.randrange(0, 20))] for _ in range(300)]
    # the cores' loads over runs of samples: unevenly apart, a core coming and going
    Times = namedtuple("Times", "user nice system idle iowait irq softirq steal guest guest_nice")
    runs, lines = [], []
    for _ in range(60):
        count, now, run_ = rng.randrange(1, 9), rng.uniform(0, 100), []
        state = [[rng.uniform(0, 1000) for _ in range(10)] for _ in range(count)]
        for _ in range(rng.randrange(2, 14)):
            if rng.random() < 0.1:
                state = state[:-1] or [[1.0] * 10] if rng.random() < 0.5 else [*state, [2.0] * 10]
            for times in state:
                for n in range(10):
                    times[n] += rng.choice([0.0, rng.uniform(0, 0.2)])
            run_.append([now, [list(times) for times in state]])
            lines += [list(times) for times in state]
            now += rng.choice([0.05, 0.1, 0.11, 0.3, 0.31, 0.7])
        runs.append(run_)
    busy = [[_hex(v) for v in load._busy(Times(*times))] for times in lines]
    wanted_runs = []
    for run_ in runs:
        clock = iter([now for now, _ in run_])
        sampled = iter([[Times(*times) for times in sample] for _, sample in run_])
        cores = load._Cores(times=lambda sampled=sampled: next(sampled),
                            clock=lambda clock=clock: next(clock))  # fmt: skip
        got = [cores.read() for _ in run_[1:]]
        wanted_runs.append([loads and [_hex(v) for v in loads] for loads in got])
    sent = [[[now, [list(load._busy(Times(*t))) for t in sample]] for now, sample in run_]
            for run_ in runs]  # fmt: skip

    # the cores as Windows writes them, as sysfs has them, and checked
    def record(relation: int, masks: list[tuple[int, int]], short: int = 0) -> bytes:
        body = struct.pack("<BB20xH", 0, 0, len(masks))
        body += b"".join(struct.pack("<QH6x", mask, group) for mask, group in masks)
        body = body[: len(body) - short]
        return struct.pack("<II", relation, 8 + len(body)) + body

    records = [b"", b"\x00" * 7, struct.pack("<II", 0, 4), struct.pack("<II", 0, 400)]
    for _ in range(200):
        raw = b""
        for _ in range(rng.randrange(0, 12)):
            masks = [(rng.choice([1, 3, 0b1100, 1 << 63, rng.getrandbits(64), 0]),
                      rng.randrange(3)) for _ in range(rng.randrange(0, 3))]  # fmt: skip
            raw += record(rng.choice([0, 0, 0, 1, 3]), masks, rng.choice([0, 0, 0, 0, 5]))
        records.append(raw + rng.choice([b"", b"\x01\x02\x03"]))
    roots = []
    for n in range(40):
        root = tmp_path / f"cpu{n}"
        root.mkdir()
        for number in rng.sample(range(20), rng.randrange(0, 12)):
            folder = root / rng.choice([f"cpu{number}", f"cpu{number}", f"cpu0{number}"])
            (folder / "topology").mkdir(parents=True, exist_ok=True)
            if rng.random() < 0.85:
                (folder / "topology" / "physical_package_id").write_text(f"{number % 2}\n")
                (folder / "topology" / "core_id").write_text(f" {number // 2 % 4}\n")
        for other in ("cpufreq", "cpuidle", "cpu1x", "online"):
            (root / other).mkdir(exist_ok=True)
        roots.append(root)
    checks = [[None, 4], [[], 0], [[[0], [1]], 2], [[[0, 1], [2, 3]], 4], [[[2, 3], [0, 1]], 4],
              [[[0, 1], [2]], 4], [[[0, 1], [1, 2]], 3], [[[0, 2], [1]], 3]]  # fmt: skip
    for _ in range(200):
        threads = rng.randrange(1, 12)
        order = rng.sample(range(threads), threads)
        made = [order[i : i + rng.choice([1, 2, 2, 4])] for i in range(0, threads, 2)]
        checks.append([[sorted(core) for core in made], rng.choice([threads, threads, 3])])
    [ours] = run([{
        "op": "load_math", "cpu": cpu, "memory": memory, "engines": engines, "runs": sent,
        "times": lines, "records": [base64.b64encode(r).decode() for r in records],
        "sysfs": [str(root) for root in roots], "checked": checks,
    }])  # fmt: skip
    for (before, after), got in zip(cpu, ours["cpu"], strict=True):
        want = load.cpu_load(before and tuple(before), after and tuple(after))
        assert got == (None if want is None else _hex(want)), (before, after)
    for found, got in zip(memory, ours["adapters"], strict=True):
        assert got == load.pick_adapter([tuple(i) for i in found]), found
    for (adapter, found), got in zip(engines, ours["loads"], strict=True):
        assert got == _hex(load.adapter_load(adapter, [tuple(i) for i in found])), found
    assert ours["busy"] == busy
    assert ours["cores"] == wanted_runs
    assert sum(loads is None for run_ in wanted_runs for loads in run_) > 5  # (cores went)

    def cores_of(raw: bytes) -> Any:
        try:
            return topology.parse_processor_cores(raw)
        except struct.error:  # (a record cut short: no cores are told)
            return None

    assert ours["records"] == [cores_of(raw) for raw in records]
    for root, got in zip(roots, ours["sysfs"], strict=True):
        # (the folder's own order of its threads, which Python's glob and Rust both take)
        want = topology._linux_cores(root)
        assert sorted(got or []) == sorted(want or []) and (got is None) == (want is None), root
    for (cores, threads), got in zip(checks, ours["checked"], strict=True):
        assert got == topology.checked(cores, threads), (cores, threads)
    assert sum(got is not None for got in ours["checked"]) > 20


def test_the_network_link_is_read_as_netlink_py_reads_it() -> None:
    from libre_panel.sensors import netlink

    rng = random.Random(0x11F)
    speeds = [0.0, 0.4, 10.0, 100.0, 866.0, 866.7, 999.4, 999.5, 999.96, 1000.0, 1040.0, 1050.0,
              2500.0, 9999.0, 10000.0, 100000.0]  # fmt: skip
    speeds += [rng.uniform(0, 20000) for _ in range(300)]
    head = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    routes = ["", head, head + "eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"]
    for _ in range(300):
        lines = []
        for _ in range(rng.randrange(0, 6)):
            name = rng.choice(["eth0", "wlan0", "enp5s0", "wlp3s0"])
            lines.append("\t".join([name, rng.choice(["00000000", "0000A8C0"]), "0101A8C0", "0003",
                                    "0", "0", rng.choice(["0", "100", "600", "x", "-1", "20"]),
                                    "00000000", "0", "0", "0"][: rng.choice([11, 6, 7])])
                         )  # fmt: skip
        routes.append(head + "\n".join(lines) + rng.choice(["", "\n"]))
    top = ("Inter-| sta-|   Quality        |   Discarded packets\n"
           " face | tus | link level noise |  nwid  crypt   frag\n")  # fmt: skip
    wireless = [["", "wlan0"], [top, "wlan0"]]
    for _ in range(300):
        rows = "".join(
            f"{rng.choice([' ', ''])}{rng.choice(['wlan0', 'wlp3s0'])}: 0000   "
            f"{rng.choice(['57.', '70.', '0.', '99', '31.5', 'x', '-4.'])}  -53.  -256\n"
            for _ in range(rng.randrange(0, 3))
        )
        wireless.append([top + rows + rng.choice(["", "wlan0:\n", "wlan0: 0000\n"]), "wlan0"])
    iw = ["", "Not connected.\n", "Connected to aa:bb\n\ttx bitrate: 866.7 MBit/s VHT-MCS 9\n",
          "\trx bitrate: 650.0 MBit/s\n\tTX BITRATE:1200 mbit/s\n", "tx bitrate: . MBit/s",
          "tx bitrate: 1.2.3 MBit/s", "tx bitrate:  54 MBit/s", "tx bitrate: ٣ MBit/s"]  # fmt: skip
    [ours] = run([{"op": "netlink", "speeds": speeds, "routes": routes, "wireless": wireless,
                   "iw": iw}])  # fmt: skip
    assert ours["speeds"] == [netlink.speed_text(speed) for speed in speeds]
    assert ours["routes"] == [netlink.default_interface(route) for route in routes]
    assert len({name for name in ours["routes"]}) > 3
    for (text, name), got in zip(wireless, ours["quality"], strict=True):
        try:
            want = netlink.wireless_quality(text, name)
        except ValueError:
            assert set(got) == {"error"}, text
            continue
        assert got == (None if want is None else _hex(want)), text
    for out, got in zip(iw, ours["rates"], strict=True):
        found = netlink._IW_RATE.search(out)
        try:
            want = float(found.group(1)) if found else None
        except ValueError:  # (a rate that is no number: Python raises, and no link is told)
            want = None
        assert got == (None if want is None else _hex(want)), out


def test_the_volume_s_tools_are_read_as_audio_py_reads_them() -> None:
    from libre_panel.sensors import audio

    rng = random.Random(0xA0D)
    texts = ["", "Volume: 0.37\n", "Volume: 0.37 [MUTED]\n", "Volume: 1.00", "Volume:.5",
             "Volume: 1.", "Volume:  12", "volume: 0.2", "Mute: yes\n", "Mute: no", "Mute:yes",
             "Mute: maybe", "Volume: front-left: 24248 /  37% / -26.45 dB,   front-right: 2 / 3%",
             "Volume: 0.3.4 [MUTED]", "100%", "٣٧%", "Volume: ٠.٥", "x % 5%% 150%"]  # fmt: skip
    pieces = ["Volume:", " ", "0.37", "1", ".", "[MUTED]", "Mute:", "yes", "no", "37%", "%", "\n"]
    texts += ["".join(rng.choice(pieces) for _ in range(rng.randrange(1, 8))) for _ in range(500)]
    states = [[rng.choice([-5.0, 0.0, 0.04, 0.05, 36.95, 37.25, 99.96, 100.0, 180.0,
                           rng.uniform(0, 100)]), rng.random() < 0.4, rng.random() < 0.4]
              for _ in range(300)]  # fmt: skip
    [ours] = run([{"op": "audio", "texts": texts, "states": states}])
    for text, wpctl, volume, mute in zip(texts, ours["wpctl"], ours["volume"], ours["mute"],
                                         strict=True):  # fmt: skip
        want = audio.parse_wpctl(text)
        assert wpctl == (want and [_hex(want[0]), want[1]]), text
        level = audio.parse_pactl_volume(text)
        assert volume == (None if level is None else _hex(level)), text
        assert mute == audio.parse_pactl_mute(text), text
    provider = audio.AudioProvider({})
    for (level, muted, mic), got in zip(states, ours["readings"], strict=True):
        if mic:
            provider._publish_mic(level, muted)
            found = provider._mic[1]
        else:
            provider._publish(level, muted)
            found = provider._state[1]
        want = [[key, _hex(r.value) if isinstance(r.value, float) else ["int", str(r.value)],
                 r.unit, r.label] for key, r in found.items()]  # fmt: skip
        assert got == want, (level, muted, mic)


def test_a_game_s_frames_are_counted_as_presentmon_py_counts_them() -> None:
    from libre_panel.sensors import presentmon as pm

    rng = random.Random(0xF95)
    apps = ["Game.exe", "game.EXE", "Launcher.exe", "dwm.exe", "Other", "", "Discord.exe"]
    runs = []
    for n in range(120):
        every_frame = n % 4 != 0
        now, events = rng.uniform(0, 1000), []
        pace = {app: rng.choice([4.0, 8.3, 16.6, 33.3, 250.0]) for app in apps}
        for _ in range(rng.randrange(20, 400)):
            what = rng.random()
            if what < 0.02:
                events.append(["target", rng.choice([None, "game.exe", "Launcher.exe", ""])])
            elif what < 0.10:
                events.append(["read", now])
            elif what < 0.12:
                now += rng.choice([0.6, 3.5, 31.0, 6.5])  # (a stall, a pause, a round's end)
            else:
                app = rng.choice(apps[:3] if rng.random() < 0.8 else apps)
                ms = pace[app] * rng.choice([1.0, 1.0, 1.0, 0.5, 2.0, 12.0])
                ms = rng.choice([ms, ms, ms, 0.0, -1.0, 5000.0, 4999.0, 150.0, 0.004])
                now += max(ms, 0.0) / 1000 * rng.choice([1.0, 1.0, 0.0, 3.0])
                waits = None
                if rng.random() < 0.7:
                    waits = [ms * rng.random(), ms * rng.random() * 0.4, ms * rng.random(),
                             ms * rng.random() * 0.4]  # fmt: skip
                events.append(["add", app, ms, now, waits])
        events.append(["read", now + rng.choice([0.0, 0.2, 2.0, 4.0])])
        runs.append({"every_frame": every_frame, "events": events})
    rounds = [[], [16.6], [0.001, 0.004, 0.005, 99.994, 99.996, 100.0, 100.4, 100.5, 101.5, 4000.0]]
    rounds += [[rng.choice([rng.uniform(1, 30), rng.uniform(1, 30), rng.uniform(90, 400)])
                for _ in range(rng.randrange(1, 3000))] for _ in range(60)]  # fmt: skip
    head = "Application,ProcessID,MsBetweenPresents,MsCPUBusy,MsCPUWait,MsGPUBusy,MsGPUWait"
    tables = [
        ["warning: something", head, "Game.exe,12,16.6,5,1,9,0.5", "Game.exe,12,NA,5,1,9,0.5",
         '"My, Game.exe",12, 8.25 ,NA,1,9,0.5', "short", 'x,1,nan,1,2,3,4', '"a""b",1,1e1,1,2,3'],
        ["Application,FrameTime,CPUBusy,CPUWait,GPUBusy,GPUWait,Application", "g,7,1,2,3,4,h"],
        ["ProcessID,msBetweenPresents", "Application,MsBetweenAppStart", "g.exe,_1_0", "g.exe,1_0"],
        ["Application,MsBetweenPresents,CPUBusy,CPUWait,GPUBusy", "g.exe,5,1,2,3", ""],
    ]  # fmt: skip
    limits = [[[], 10.0], [[[1, 2, 3, 4]] * 2, 10.0], [[[1, 2, 3, 4]] * 3, 0.0]]
    for _ in range(300):
        mean = rng.uniform(1, 40)
        waits = [[mean * rng.random(), mean * rng.random() * 0.5, mean * rng.random(),
                  mean * rng.random() * 0.5] for _ in range(rng.randrange(3, 9))]  # fmt: skip
        limits.append([waits, mean])
    [ours] = run([{"op": "game_frames", "runs": runs, "rounds": rounds, "tables": tables,
                   "limits": limits}])  # fmt: skip

    def said(found: dict[str, Any]) -> list[list[Any]]:
        return [[key, _hex(r.value) if isinstance(r.value, float) else ["str", str(r.value)],
                 r.unit, r.label] for key, r in found.items()]  # fmt: skip

    keys: set[str] = set()
    for case, got in zip(runs, ours["runs"], strict=True):
        counter = pm.FrameCounter(every_frame=case["every_frame"])
        reads = []
        for event in case["events"]:
            if event[0] == "target":
                counter.set_target(event[1])
            elif event[0] == "add":
                waits = event[4] and tuple(event[4])
                counter.add(event[1], event[2], event[3], waits)
            else:
                found = counter.readings(event[1])
                keys.update(found)
                reads.append(said(found))
        assert got == reads, case["every_frame"]
    assert {"game.fps", "game.fps_trace", "game.limiter", "game.fps_01low", "game.app",
            "game.frametime_peak"} <= keys  # fmt: skip
    for times, got in zip(rounds, ours["rounds"], strict=True):
        round_ = pm.FrameTimes()
        for ms in times:
            round_.add(ms)
        wanted = [round_.percentile(p) for p in (1, 500, 990, 999, 1000)]
        assert got["percentiles"] == [None if w is None else _hex(w) for w in wanted]
        figures = round_.figures()
        assert got["figures"] == (figures and [_hex(figures[name]) for name in pm.ROUND_READINGS])
    import csv

    for lines, got in zip(tables, ours["tables"], strict=True):
        columns, wanted = None, []
        for row in csv.reader(lines):
            if columns is None:
                columns = pm.Columns.of(row)
                wanted.append(columns and [columns.app, columns.frame,
                                           columns.waits and list(columns.waits)])  # fmt: skip
            else:
                frame = columns.frame_of(row)
                wanted.append(frame and [frame[0], _hex(frame[1]),
                                         frame[2] and [_hex(w) for w in frame[2]]])  # fmt: skip
        assert got == wanted, lines
    for (waits, mean), got in zip(limits, ours["limits"], strict=True):
        assert got == pm.limited_by([tuple(w) for w in waits], mean), (waits, mean)
    assert {"cpu", "gpu", "cap", None} == set(ours["limits"])


def test_the_computers_parts_are_named_as_hardware_names_them(tmp_path: Any) -> None:
    """sensors/hardware.py and smbios.py: the processor of /proc/cpuinfo, the cards of
    /sys/class/drm by the NVIDIA driver, AMD's list and pci.ids, the memory modules of the
    SMBIOS table, and what the source makes of them."""
    from pathlib import Path

    from libre_panel.sensors import hardware, smbios

    rng = random.Random(0x4A)
    cpu_lines = [
        "processor\t: 0", "model name\t: AMD Ryzen 7 7800X3D 8-Core Processor", "model name : ",
        "model name\t: ARMv7 Processor rev 4 (v7l)", "model name: ARM", "model name: Armada 370",
        "model name: ARMv8", "model name: armx", "model name: ARM_1", "Model\t\t: Raspberry Pi 4",
        "Hardware\t: BCM2835", "Processor\t: AArch64 Processor rev 4", "cpu model\t: MIPS 24Kc",
        "cpu\t\t: POWER9, altivec supported", "Model Name\t: Loongson-3A5000", "flags : a:b: c",
        "no colon here", "", ": lonely", "model name\t: Intel(R) Core(TM) i7-12700K",
        "Model : \xa0Banana Pi ", "cpu : é\x85ü",
    ]  # fmt: skip
    cpuinfo = []
    for n in range(300):
        file = tmp_path / f"cpuinfo{n}"
        lines = [rng.choice(cpu_lines) for _ in range(rng.randrange(0, 9))]
        data = rng.choice(["\n", "\n", "\r\n", "\r"]).join(lines).encode()
        if rng.random() < 0.1:
            data = data.replace(b"o", b"\xff\xc3", 1)
        if n % 40 != 39:  # (one in forty is not there)
            file.write_bytes(data)
        cpuinfo.append(str(file))

    pci_text = (
        "# a comment\n\n1002  Advanced Micro Devices, Inc. [AMD/ATI]\n"
        "\t744c  Navi 31 [Radeon RX 7900 XT/7900 XTX/7900 GRE/7900M]\n"
        "\t\t1da2 e471  NITRO+ RX 7900 XTX Vapor-X\n\t73bf  Navi 21 [Radeon RX 6800/6800 XT]\n"
        "\t15bf  Phoenix1\n10DE  NVIDIA Corporation\n\t2786  AD104 [GeForce RTX 4070]\n"
        "\t1c82  GP107 [GeForce GTX 1050 Ti] [rev a1]\n\t0000  [] Odd [ [x] y\n"
        "8086  Intel Corporation\n\t56a0  DG2 [Arc A770]\n\t4680  AlderLake-S GT1\r\n"
        "10020 Not a vendor\n\t744c  Wrong block\nC 03  Display controller\n\t00  VGA\n"
    )
    amd_text = (
        "# List of AMDGPU IDs\n1.0.0\n744C,\tC8,\tAMD Radeon RX 7900 XTX\n"
        "744C,\tCC,\tAMD Radeon RX 7900 XT\n73BF, C1, AMD Radeon RX 6800 XT\nzz, 00, Nothing\n"
        "0x15BF,0xC1,AMD Radeon 780M, with a comma\n#744C,\t00,\tA comment\n"
    )
    ids = [("0x1002", "0x744c", "0xc8"), ("0x1002", "0x744c", "0x00"), ("0x1002", "0x73bf", "0xc1"),
           ("0x1002", "0x15bf", "0xc1"), ("0x10de", "0x2786", "0xa1"), ("0x10de", "0x1c82", "a1"),
           ("0x10DE", "0x0000", ""), ("0x8086", "0x56a0", "0x08"), ("0x8086", "0x4680", "0x0c"),
           ("0x8086", "0x9999", "0x00"), ("0x1234", "0x1111", "0x02"), ("nope", "0x1", "0x0"),
           ("1002", "744C", "c8"), (" 0x10de\n", "0x_27_86", "")]  # fmt: skip
    trees = []
    for n in range(120):
        root = tmp_path / f"tree{n}"
        drm, nvidia = root / "drm", root / "nvidia"
        drm.mkdir(parents=True)
        slots = []
        for number in rng.sample(range(12), rng.randrange(0, 4)):
            vendor, product, revision = rng.choice(ids)
            device = drm / f"card{number}" / "device"
            device.mkdir(parents=True)
            (drm / f"card{number}-DP-1").mkdir()
            slot = f"0000:{rng.randrange(1, 9):02X}:00.0"
            slots.append(slot)
            (device / "vendor").write_text(vendor + "\n", encoding="utf-8")
            if rng.random() < 0.95:
                (device / "device").write_text(product + "\n", encoding="utf-8")
            if revision or rng.random() < 0.5:
                (device / "revision").write_text(revision + "\n", encoding="utf-8")
            if rng.random() < 0.8:
                (device / "uevent").write_text(
                    f"DRIVER=amdgpu\nPCI_ID=1002:744C\nPCI_SLOT_NAME={slot}\n", encoding="utf-8"
                )
            if rng.random() < 0.5:
                (device / "mem_info_vram_total").write_text(
                    rng.choice(["25753026560\n", "0\n", "x\n", "8_589_934_592"]), encoding="utf-8"
                )
        (drm / "renderD128").mkdir()
        (drm / "version").write_text("drm 1.1.0", encoding="utf-8")
        for slot in rng.sample(slots, rng.randrange(0, len(slots) + 1)):
            named = rng.choice([slot.lower(), slot, "0"])
            # (a slot's name has colons, which a folder on Windows cannot have)
            gpu = nvidia / ("0" if sys.platform == "win32" else named)
            gpu.mkdir(parents=True, exist_ok=True)
            (gpu / "information").write_text(
                rng.choice([
                    f"Model: \t\t NVIDIA GeForce RTX 4070\nIRQ: \t 125\nBus Location: \t {slot}\n",
                    "Model: \t\t GeForce GTX 1050 Ti\nBus Type: \t PCIe\n",
                    "IRQ: 1\nModel:\n", f"Bus Location: {slot}\rModel: Quadro\x0bP400\n",
                ]), encoding="utf-8",
            )  # fmt: skip
        pci = [root / "hwdata.ids", root / "misc.ids"]
        if rng.random() < 0.8:
            pci[rng.randrange(2)].write_text(pci_text, encoding="utf-8", newline="")
        if rng.random() < 0.3:
            pci[0].write_text("1002  AMD\n\t15bf  First file\n", encoding="utf-8")
        if rng.random() < 0.7:
            (root / "amdgpu.ids").write_text(amd_text, encoding="utf-8")
        trees.append({"drm": str(drm), "nvidia": str(nvidia), "pci_ids": [str(p) for p in pci],
                      "amdgpu_ids": str(root / "amdgpu.ids")})  # fmt: skip

    def structure(kind: int, body: bytes, texts: list[bytes]) -> bytes:
        fixed = bytes([kind, len(body) + 4, 0, 0]) + body
        return fixed + (b"\0".join(texts) + b"\0\0" if texts else b"\0\0")

    def slot_body() -> tuple[bytes, list[bytes]]:
        body = bytearray(rng.choice([0x11, 0x13, 0x17, 0x1C, 0x1E, 0x24, 0x58]))
        for at in range(len(body)):
            body[at] = rng.choice([0, 0, 0, rng.randrange(256)])
        if len(body) >= 0x0A:
            size = rng.choice([0, 0xFFFF, 0x7FFF, 8192, 16384, 0x8000 | 4096, 0x8400, 512, 1])
            struct.pack_into("<H", body, 0x08, size)
        if len(body) >= 0x0F:
            body[0x0E] = rng.choice([0x1A, 0x22, 0x18, 0x1F, 0x23, 0x02, 0x1A, 0x22])
            struct.pack_into("<H", body, 0x0F, rng.choice([0x80, 0x1000, 0x4080, 0]))
        if len(body) >= 0x13:
            struct.pack_into("<H", body, 0x11, rng.choice([0, 0xFFFF, 3200, 5600, 6000]))
        if len(body) >= 0x14:
            body[0x13] = rng.randrange(0, 5)
        if len(body) >= 0x1C:
            struct.pack_into("<I", body, 0x18, rng.choice([32768, 0x80000000 | 65536, 0]))
        if len(body) >= 0x1E:
            struct.pack_into("<H", body, 0x1C, rng.choice([0, 0xFFFF, 3000, 5200]))
        texts = [rng.choice([b"Kingston", b"G.Skill Intl", b" Corsair ", b"859B", b"\xffSK\xc3",
                             b"Unknown", b"Micron Technology"])
                 for _ in range(rng.randrange(0, 4))]  # fmt: skip
        return bytes(body), texts

    tables = []
    for _ in range(400):
        table = b""
        for _ in range(rng.randrange(0, 6)):
            if rng.random() < 0.7:
                table += structure(17, *slot_body())
            else:
                table += structure(rng.choice([0, 1, 16, 19, 127]), bytes(rng.randrange(0, 20)),
                                   [b"x"] * rng.randrange(0, 3))  # fmt: skip
        if rng.random() < 0.15:
            table = table[: rng.randrange(0, len(table) + 1)]
        tables.append(table)
    entries = []
    for n in range(40):
        folder = tmp_path / f"entries{n}"
        for number in rng.sample(range(14), rng.randrange(0, 5)):
            (folder / f"17-{number}").mkdir(parents=True)
            if rng.random() < 0.9:
                (folder / f"17-{number}" / "raw").write_bytes(structure(17, *slot_body()))
        (folder / "0-0").mkdir(parents=True)
        (folder / "0-0" / "raw").write_bytes(structure(0, b"\x01\x02", [b"BIOS"]))
        entries.append(str(folder))

    card_names = ["NVIDIA GeForce RTX 4070", "AMD Radeon RX 7900 XTX", "Arc A770", "",
                  "Intel(R) UHD Graphics 770", "Radeon 780M", "GeForce GTX 1050 Ti",
                  "nvidia quadro"]  # fmt: skip
    founds, python_founds = [], []
    for _ in range(600):
        adapters = [(rng.choice(card_names), rng.choice([0, 0, 2**31, 2**33, 2**34]),
                     rng.random() < 0.3) for _ in range(rng.randrange(0, 4))]  # fmt: skip
        kinds = rng.choice([["DDR5"], ["DDR4"], [""], ["DDR5", "DDR5", "DDR4"], ["", "DDR4"]])
        modules = [(rng.choice([8192, 16384, 32768, 4096]), rng.choice(kinds),
                    rng.choice([0, 5600, 6000, 3200]),
                    rng.choice(["Kingston", "G.Skill Intl", "Kingston Technology", "", "859B"]))
                   for _ in range(rng.choice([0, 0, 1, 2, 2, 4]))]  # fmt: skip
        counts = sorted((sum(1 for m in modules if m[1] == k) for k in {m[1] for m in modules}
                         if k), reverse=True)  # fmt: skip
        if len(counts) > 1 and counts[0] == counts[1]:
            modules = modules[:1]  # (of kinds as frequent as each other Python takes any)
        case = {
            "cpu": rng.choice(NAMES), "adapters": adapters, "modules": modules,
            "memory": rng.choice([0, 0, 2**34, 31 * 2**30, 67_000_000_000]),
            "prefer": rng.choice(["", "", "radeon", " RTX ", "nvidia", "nothing", "770"]),
            "custom": [rng.choice(["", "", "My CPU"]), rng.choice(["", "", " "]),
                       rng.choice(["", "64 GB of mine"])],
        }  # fmt: skip
        founds.append(case)
        python_founds.append(hardware.Found(
            case["cpu"], tuple(hardware.Adapter(*a) for a in adapters),
            tuple(smbios.Module(*m) for m in modules), case["memory"],
        ))  # fmt: skip
    digits = "0123456789abcdefABCDEFxX_+- \t\n٣"
    texts = ["0x1002", "1002", " 0x10DE\n", "-0x1f", "+12", "1_000", "_1", "1_", "1__0", "0x_1f",
             "0x", "", " ", "x1", "0X1F", "٣٤", "1f", "0b11", "0o7", "0x-1", "--1", "1 2",
             "0_x1"]  # fmt: skip
    ints = [[text, base] for base in (10, 16) for text in texts]
    ints += [["".join(rng.choice(digits) for _ in range(rng.randrange(1, 7))), rng.choice([10, 16])]
             for _ in range(3000)]  # fmt: skip

    [ours] = run([{"op": "hardware", "cpuinfo": cpuinfo, "trees": trees, "entries": entries,
                   "tables": [base64.b64encode(t).decode() for t in tables],
                   "founds": founds, "ints": ints}])  # fmt: skip

    def cpu_name(file: str) -> str | None:
        try:
            return hardware.linux_cpu_name(Path(file))
        except OSError:
            return None

    assert ours["cpus"] == [cpu_name(file) for file in cpuinfo]
    assert len({name for name in ours["cpus"]}) > 12
    theirs = [
        [[a.name, a.memory, a.discrete, a.slot] for a in hardware.linux_adapters(
            Path(t["drm"]), Path(t["nvidia"]), [Path(p) for p in t["pci_ids"]],
            Path(t["amdgpu_ids"]))]
        for t in trees
    ]  # fmt: skip
    assert ours["trees"] == theirs
    assert len({a[0] for cards in theirs for a in cards}) > 8

    def modules_of(found: list[Any]) -> list[list[Any]]:
        return [[m.megabytes, m.kind, m.speed, m.maker] for m in found]

    assert ours["tables"] == [modules_of(smbios.memory_modules(t)) for t in tables]
    assert sum(1 for found in ours["tables"] if found) > 100
    assert ours["entries"] == [
        modules_of(smbios.memory_modules(smbios.linux_table(Path(folder)))) for folder in entries
    ]
    provider = hardware.HardwareProvider()
    for case, found, got in zip(founds, python_founds, ours["founds"], strict=True):
        names = hardware.describe(found, case["prefer"])
        assert got["names"] == [names["cpu"], names["gpu"], names["memory"]], case
        assert got["speed"] == hardware.memory_speed(found)
        picked = hardware.pick_adapter(found.adapters, case["prefer"])
        assert got["picked"] == (None if picked is None else found.adapters.index(picked))
        provider._found, provider._prefer = found, case["prefer"]
        provider._custom = dict(zip(hardware.PARTS, case["custom"], strict=True))
        readings = provider._make()
        assert got["readings"] == [[key, r.value, r.unit, r.label] for key, r in readings.items()]

    def number(text: str, base: int) -> str | None:
        try:
            return str(int(text, base))
        except ValueError:
            return None

    assert ours["ints"] == [number(text, base) for text, base in ints]
    assert sum(1 for n in ours["ints"] if n is not None) > 500


class _MadeUpNvml:
    """A stand-in for NVIDIA's library with the functions sensors/nvml.py calls: the cards a
    case describes, each with the values it gives (those it has not are refused)."""

    def __init__(self, cards: list[dict[str, Any]]) -> None:
        self.cards = cards

    def _value(self, handle: Any, key: str) -> Any:
        return self.cards[handle.value - 1].get(key)

    def _number(self, handle: Any, key: str, ref: Any, field: str = "value") -> int:
        value = self._value(handle, key)
        if value is None:
            return 1
        setattr(ref._obj, field, value)
        return 0

    def nvmlInit_v2(self) -> int:  # noqa: N802 - the library's names
        return 0

    def nvmlShutdown(self) -> int:  # noqa: N802
        return 0

    def nvmlDeviceGetCount_v2(self, ref: Any) -> int:  # noqa: N802
        ref._obj.value = len(self.cards)
        return 0

    def nvmlDeviceGetHandleByPciBusId_v2(self, slot: bytes, ref: Any) -> int:  # noqa: N802
        for n, card in enumerate(self.cards):
            if card["slot"] == slot.decode():
                ref._obj.value = n + 1
                return 0
        return 1

    def nvmlDeviceGetHandleByIndex_v2(self, index: Any, ref: Any) -> int:  # noqa: N802
        if index.value >= len(self.cards):
            return 1
        ref._obj.value = index.value + 1
        return 0

    def nvmlDeviceGetTemperature(self, handle: Any, sensor: int, ref: Any) -> int:  # noqa: N802
        return self._number(handle, "temp", ref)

    def nvmlDeviceGetFanSpeed(self, handle: Any, ref: Any) -> int:  # noqa: N802
        return self._number(handle, "fan", ref)

    def nvmlDeviceGetFanSpeedRPM(self, handle: Any, ref: Any) -> int:  # noqa: N802
        return self._number(handle, "rpm", ref, "speed")

    def nvmlDeviceGetPowerUsage(self, handle: Any, ref: Any) -> int:  # noqa: N802
        return self._number(handle, "power", ref)

    def nvmlDeviceGetUtilizationRates(self, handle: Any, ref: Any) -> int:  # noqa: N802
        return self._number(handle, "busy", ref, "gpu")

    def nvmlDeviceGetMemoryInfo(self, handle: Any, ref: Any) -> int:  # noqa: N802
        memory = self._value(handle, "memory")
        if memory is None:
            return 1
        ref._obj.total, ref._obj.free, ref._obj.used = memory
        return 0

    def nvmlDeviceGetClockInfo(self, handle: Any, clock: int, ref: Any) -> int:  # noqa: N802
        return self._number(handle, "clock" if clock == 0 else "memory_clock", ref)


@pytest.mark.skipif(sys.platform == "win32", reason="folders laid out as Linux names them")
def test_a_linux_pcs_sensors_are_read_as_the_linux_source_reads_them(tmp_path: Any) -> None:
    """sensors/linux.py with hwmon.py, rapl.py, gpus.py and nvml.py: machines laid out in
    folders (chips, thermal zones, energy counters, graphics cards, NVIDIA's library as a
    stand-in), read at the same times after the same changes."""
    from pathlib import Path

    from libre_panel.sensors import base, hardware, nvml
    from libre_panel.sensors.linux import LinuxProvider

    rng = random.Random(0x11B)
    chip_names = ["k10temp", "coretemp", "zenpower", "nct6798", "it8689", "nvme", "nvme",
                  "drivetemp", "spd5118", "jc42", "amdgpu", "i915", "xe", "asus_ec_sensors",
                  "amd_energy", "fam15h_power", "acpitz", "iwlwifi_1", "nouveau", "w83627ehf",
                  "My Chip", "cpu_thermal"]  # fmt: skip
    labels = ["Tctl", "Tdie", "Package id 0", "Core 0", "edge", "junction", "mem", "Composite",
              "Sensor 1", "", "", "CPU Fan", "sclk", "mclk", "Esocket0", "esocket1", "GPU",
              "hot spot", "PKG", "vram"]  # fmt: skip
    machines = []
    for n in range(240):
        root = tmp_path / f"pc{n}"
        hw = root / "sys/class/hwmon"
        hw.mkdir(parents=True)
        files: list[Path] = []  # files of fast chips, whose numbers change between readings
        counters: list[Path] = []
        folders = rng.sample(range(13), rng.randrange(0, 7))
        for number in folders:
            chip = hw / f"hwmon{number}"
            chip.mkdir()
            name = rng.choice(chip_names)
            if rng.random() < 0.95:
                (chip / "name").write_text(name + "\n", encoding="utf-8")
            base_folder = chip / "device" if rng.random() < 0.1 else chip
            base_folder.mkdir(exist_ok=True)
            slow = name in ("nvme", "drivetemp", "spd5118", "jc42")
            for _ in range(rng.randrange(0, 7)):
                kind = rng.choice(["temp", "temp", "temp", "fan", "fan", "pwm", "power", "energy",
                                   "freq"])  # fmt: skip
                at = rng.randrange(1, 12)
                suffix = {"pwm": "", "power": rng.choice(["_average", "_input"])}.get(
                    kind, "_input"
                )
                file = base_folder / f"{kind}{at}{suffix}"
                value = {"temp": rng.choice([41500, 67250, 0, -70000, 250000, 30000]),
                         "fan": rng.choice([0, 840, 1320]), "pwm": rng.randrange(256),
                         "power": rng.choice([48_700_000, 0]), "energy": rng.randrange(10**9),
                         "freq": rng.choice([2_400_000_000, 96_000_000])}[kind]  # fmt: skip
                file.write_text(rng.choice([f"{value}\n", f"{value}\n", "\n", "junk\n"]),
                                encoding="utf-8")  # fmt: skip
                if rng.random() < 0.6:
                    (base_folder / f"{kind}{at}_label").write_text(
                        rng.choice(labels) + "\n", encoding="utf-8"
                    )
                if kind == "energy":
                    counters.append(file)
                elif not slow:
                    files.append(file)
            (base_folder / "temp1_crit").write_text("95000\n", encoding="utf-8")
            (base_folder / "fan1").write_text("7\n", encoding="utf-8")
            if name in ("nvme", "drivetemp") and rng.random() < 0.7:
                model = rng.choice([chip / "device" / "model", chip / "model"])
                model.parent.mkdir(exist_ok=True)
                model.write_text("Samsung SSD 990 PRO 2TB  \n", encoding="utf-8")
        (hw / "hwmonx").mkdir()
        for zone in rng.sample(range(4), rng.randrange(0, 3)):
            folder = root / "sys/class/thermal" / f"thermal_zone{zone}"
            folder.mkdir(parents=True)
            (folder / "type").write_text(rng.choice(["x86_pkg_temp", "acpitz", ""]) + "\n")
            if rng.random() < 0.9:
                (folder / "temp").write_text("52000\n", encoding="utf-8")
                files.append(folder / "temp")
        for zone in rng.sample(["intel-rapl:0", "intel-rapl:1", "intel-rapl:0:0",
                                "intel-rapl-mmio:0", "dtpm"], rng.randrange(0, 4)):  # fmt: skip
            folder = root / "sys/class/powercap" / zone
            folder.mkdir(parents=True)
            (folder / "name").write_text(rng.choice(["package-0", "package-0", "core", "psys"]))
            if rng.random() < 0.9:
                (folder / "energy_uj").write_text(f"{rng.randrange(10**9)}\n", encoding="utf-8")
                counters.append(folder / "energy_uj")
            if rng.random() < 0.7:
                (folder / "max_energy_range_uj").write_text("262143328850\n", encoding="utf-8")
        adapters, library = [], []
        for number in rng.sample(range(4), rng.choice([0, 1, 1, 1, 2])):
            card = root / "sys/class/drm" / f"card{number}"
            device = card / "device"
            (device / "power").mkdir(parents=True)
            (root / "sys/class/drm" / f"card{number}-HDMI-A-1").mkdir()
            vendor, driver = rng.choice([("0x1002", "amdgpu"), ("0x10de", "nvidia"),
                                         ("0x1002", "amdgpu"), ("0x10de", "nvidia"),
                                         ("0x10de", "nouveau"), ("0x8086", "i915"),
                                         ("0x8086", "xe"), ("0x1234", "")])  # fmt: skip
            slot = f"0000:0{number + 1}:00.0"
            (device / "vendor").write_text(vendor + "\n", encoding="utf-8")
            if rng.random() < 0.95:
                (device / "device").write_text("0x744c\n", encoding="utf-8")
            uevent = f"DRIVER={driver}\nPCI_ID=1002:744C\n"
            if rng.random() < 0.85:
                uevent += f"PCI_SLOT_NAME={slot.upper()}\n"
            (device / "uevent").write_text(uevent, encoding="utf-8")
            (device / "power" / "runtime_status").write_text(
                rng.choice(["active"] * 6 + ["suspended"]) + "\n", encoding="utf-8"
            )
            if rng.random() < 0.6:
                total = rng.choice([0, 8 * 2**30, 24 * 2**30])
                (device / "mem_info_vram_total").write_text(f"{total}\n", encoding="utf-8")
                (device / "mem_info_vram_used").write_text(f"{3 * 2**29 + 12345}\n")
            if rng.random() < 0.3:
                (card / "lmem_total_bytes").write_text(f"{16 * 2**30}\n", encoding="utf-8")
            if rng.random() < 0.6:
                (device / "gpu_busy_percent").write_text(rng.choice(["37\n", "140\n", "-3\n"]))
                files.append(device / "gpu_busy_percent")
            if rng.random() < 0.4:
                clock = card / rng.choice(["gt_cur_freq_mhz", "gt/gt0/rps_cur_freq_mhz",
                                           "device/tile0/gt0/freq0/cur_freq"])  # fmt: skip
                clock.parent.mkdir(parents=True, exist_ok=True)
                clock.write_text("1150\n", encoding="utf-8")
            if folders and rng.random() < 0.7:
                (device / "hwmon" / f"hwmon{rng.choice(folders)}").mkdir(parents=True)
            if rng.random() < 0.5:
                adapters.append([rng.choice(["Radeon RX 7900 XTX", "GeForce RTX 4070", "Arc"]),
                                 rng.choice([0, 2**33]), rng.random() < 0.3, slot])  # fmt: skip
            if driver == "nvidia" and rng.random() < 0.8:
                values = {"slot": rng.choice([slot, slot, "0000:0f:00.0"]), "temp": 54,
                          "fan": 38, "rpm": 1240, "power": 187_340, "busy": 71,
                          "memory": [12 * 2**30, 9 * 2**30, 3 * 2**30 + 777], "clock": 2610,
                          "memory_clock": 10501}  # fmt: skip
                for key in rng.sample(list(values)[1:], rng.randrange(0, 3)):
                    del values[key]
                if rng.random() < 0.1:
                    values["memory"] = [0, 0, 0]
                library.append(values)
        steps = []
        for now in (0.0, 0.1, 1.5, 3.0, 13.5, 14.5):
            writes = []
            for file in rng.sample(files, rng.randrange(0, len(files) + 1)):
                writes.append([str(file), f"{rng.choice([30000, 45250, 71000, 1500, 0, 88])}\n"])
            for file in counters:
                if rng.random() < 0.8:
                    writes.append([str(file), rng.choice([f"{rng.randrange(10**9)}\n", "x\n"])])
            if now == 13.5 and rng.random() < 0.5:  # (a chip that appears: found by the rescan)
                writes += [[str(hw / "hwmon99" / "name"), "k10temp\n"],
                           [str(hw / "hwmon99" / "temp1_input"), "49125\n"],
                           [str(hw / "hwmon99" / "temp1_label"), "Tctl\n"]]  # fmt: skip
            steps.append({"now": now, "writes": writes})
        machines.append({
            "root": str(root), "prefer": rng.choice(["", "", "radeon", "RTX", "nothing"]),
            "nvidia": rng.choice(["auto", "auto", "auto", "off", " OFF "]),
            "wanted": rng.choice([[], ["temp.*"], ["temp.drivetemp.0"], ["cpu.load"]]),
            "adapters": adapters, "nvml": rng.choice([library, library, None]), "steps": steps,
        })  # fmt: skip

    # (the steps write files as they go: what each was before is put back for Python's turn)
    before = {file: Path(file).read_bytes() if Path(file).exists() else None
              for machine in machines for step in machine["steps"]
              for file, _ in step["writes"]}  # fmt: skip
    [ours] = run([{"op": "linux_source", "machines": machines}])
    for file, data in before.items():
        if data is None:
            Path(file).unlink(missing_ok=True)
        else:
            Path(file).write_bytes(data)
    seen: dict[str, int] = {}
    for machine, got in zip(machines, ours["machines"], strict=True):
        hwmon99 = Path(machine["root"]) / "sys/class/hwmon/hwmon99"
        if hwmon99.is_dir():
            hwmon99.rmdir()
        times = [0.0]
        cards = machine["nvml"]
        provider = LinuxProvider(
            {"gpu": machine["prefer"], "nvidia": machine["nvidia"]},
            root=Path(machine["root"]),
            clock=lambda times=times: times[0],
            opener=lambda cards=cards: None if cards is None else nvml.Nvml(_MadeUpNvml(cards)),
        )
        named = [hardware.Adapter(*adapter) for adapter in machine["adapters"]]
        provider._adapters = lambda named=named: named  # type: ignore[method-assign]
        base.want(provider, set(machine["wanted"]))
        try:
            for step, said in zip(machine["steps"], got["steps"], strict=True):
                for file, text in step["writes"]:
                    Path(file).parent.mkdir(parents=True, exist_ok=True)
                    Path(file).write_text(text, encoding="utf-8")
                times[0] = step["now"]
                readings = provider.read()
                wanted = [[key, r.value, r.unit, r.label, r.origin]
                          for key, r in readings.items()]  # fmt: skip
                assert said == wanted, (machine["root"], step["now"])
                for key in readings:
                    seen[key] = seen.get(key, 0) + 1
            status = provider.status()
            assert got["status"] == [status["chips"], status["temperatures"], status["fans"],
                                     status["graphics"], status["nvidia driver"] == "NVML",
                                     status["cpu power"]]  # fmt: skip
        finally:
            provider.close()
    for key in ("cpu.temp", "mem.temp", "cpu.power", "gpu.temp", "gpu.load", "gpu.power",
                "gpu.mem.used", "gpu.freq", "gpu.fan", "gpu.fan.percent", "temp.nvidia.gpu",
                "fan.lpc.fan_1"):  # fmt: skip
        assert seen.get(key, 0) > 2, (key, sorted(seen))


@pytest.mark.parametrize(
    "zone", [None, "Europe/Berlin", "America/St_Johns", "Pacific/Kiritimati", "Antarctica/Troll"]
)
def test_the_sun_and_the_moon_are_computed_as_sky_computes_them(
    zone: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sensors/sky.py: the moon's phase and the sun's times at moments of a century, at
    places from pole to pole (polar nights and midnight suns among them) and with none, in
    the computer's zone and in others."""
    from libre_panel.sensors import sky

    if zone is not None:
        if sys.platform == "win32":
            pytest.skip("the computer's zone set by TZ and tzset")
        monkeypatch.setenv("TZ", zone)
        time.tzset()
    try:
        rng = random.Random(0x5C1)
        places = [(52.52, 13.405), (-33.87, 151.21), (78.22, 15.65), (-77.85, 166.67), (0.0, 0.0),
                  (90.0, 0.0), (-90.0, 45.0), (64.15, -21.94), (35.68, 139.69), (69.65, 18.96),
                  (89.9999, 179.9999), (66.5622, 25.0), (-66.5622, -180.0)]  # fmt: skip
        moments = []
        for _ in range(4000):
            place = rng.choice([*places, (rng.uniform(-90, 90), rng.uniform(-180, 180)), None,
                                (None, 13.4)])  # fmt: skip
            t = rng.choice([rng.uniform(0, 4.1e9),
                            float(rng.randrange(1_700_000_000, 1_900_000_000)),
                            rng.randrange(1_750_000_000, 1_800_000_000) + 0.5])  # fmt: skip
            moments.append([t, *(place or (None, None))])
        [ours] = run([{"op": "sky", "moments": moments}])
        seen: set[str] = set()
        for (t, latitude, longitude), got in zip(moments, ours["moments"], strict=True):
            wanted = sky.sky_readings(t, latitude, longitude)
            assert got == [[key, r.value, r.unit, r.label] for key, r in wanted.items()], (
                t, latitude, longitude,
            )  # fmt: skip
            for (key, value, *_), reading in zip(got, wanted.values(), strict=True):
                assert type(value) is type(reading.value), key
                seen.add(key)
            if wanted.get("sun.daylight") is not None and "sun.rise" not in wanted:
                seen.add(f"always {wanted['sun.up'].value}")
        assert seen >= {"moon.name", "sun.next_rise", "sun.progress", "always 0", "always 1"}
    finally:
        monkeypatch.undo()
        if sys.platform != "win32":
            time.tzset()


@pytest.mark.skipif(sys.platform == "win32", reason="MangoHud is Linux's")
def test_mangohuds_logs_are_found_and_read_as_python_reads_them(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sensors/mangohud.py: the folder MangoHud.conf names, the newest log of a folder, the
    program of a log's name, and the frame times of a log as it grows."""
    import os
    from pathlib import Path

    from libre_panel.sensors import mangohud

    rng = random.Random(0x3A9)
    conf_lines = ["output_folder=/games/logs", "output_folder = $LOGS/mango ", "fps_limit=60",
                  "# output_folder=/no", "output_folder=", "output_folder=~/logs",
                  "output_folder=~",
                  "output_folder=${LOGS}x/$NOPE/${NOPE}/$", "autostart_log=1", "no equals sign",
                  " output_folder\t=\t/tabs/logs", "output_folder=$LOGS${LOGS}$1a",
                  "output_folder=${LOGS", "output_folder=a=b"]  # fmt: skip
    configs = []
    for n in range(200):
        home = tmp_path / f"home{n}"
        env = {"HOME": str(home)}
        base = home / ".config"
        if rng.random() < 0.4:
            base = tmp_path / f"xdg{n}"
            env["XDG_CONFIG_HOME"] = rng.choice([str(base), str(base), ""])
            if not env["XDG_CONFIG_HOME"]:
                base = home / ".config"
        if rng.random() < 0.5:
            env["LOGS"] = rng.choice(["/var/logs", "", "relative dir"])
        if rng.random() < 0.9:
            (base / "MangoHud").mkdir(parents=True)
            lines = [rng.choice(conf_lines) for _ in range(rng.randrange(0, 4))]
            (base / "MangoHud" / "MangoHud.conf").write_bytes(
                rng.choice(["\n", "\r\n"]).join(lines).encode() + rng.choice([b"", b"\n\xff"])
            )
        configs.append({"home": str(home), "env": env})
    stamp = "_2026-10-21_19-04-33"
    log_names = [f"Cyberpunk2077{stamp}.csv", "My Game_v2_2026-01-02_03-04-05.csv",
                 f"Cyberpunk2077{stamp}_summary.csv", "notes.csv", f"{stamp}.csv",
                 f"a_b{stamp}.csv", f"x{stamp}.CSV", "x_26-10-21_19-04-33.csv",
                 f"game{stamp}.csv.bak", f"Ünï{stamp}.csv"]  # fmt: skip
    now = 1_800_000_000.0
    folders = []
    for n in range(150):
        folder = tmp_path / f"logs{n}"
        folder.mkdir()
        for name in rng.sample(log_names, rng.randrange(0, 6)):
            (folder / name).write_text("x", encoding="utf-8")
            changed = now - rng.choice([0.0, 1.5, 9.99, 10.0, 10.01, 300.0, -2.0, 4.25])
            os.utime(folder / name, (changed, changed))
        folders.append([str(folder), now])
    folders.append([str(tmp_path / "no such folder"), now])
    cells = ["16.6", "8.33", " 7.5 ", "", "abc", "1e1", "0", "-3", "1_0.5", "٣", "33.3", "4.0"]
    logs = []
    for n in range(120):
        header = rng.choice(["fps,frametime,cpu_load", "FPS, FrameTime ,x", "frametime", "a,b",
                             "os,cpu,gpu\nLinux,Ryzen,Radeon\nfps,frametime,cpu_load"])  # fmt: skip
        rows = [header] + [
            ",".join(rng.choice(cells) for _ in range(rng.randrange(0, 4)))
            for _ in range(rng.randrange(0, 40))
        ]
        data = rng.choice(["\n", "\r\n"]).join(rows).encode() + rng.choice([b"", b"\n"])
        if rng.random() < 0.1:
            data += b"9" * 6000 + b"\n5.5,6.5,7.5\n"
        if rng.random() < 0.1:
            data = data.replace(b"1", b"\xff", 2)
        cuts = sorted(rng.randrange(0, len(data) + 1) for _ in range(rng.randrange(0, 5)))
        chunks = [data[a:b] for a, b in itertools.pairwise([0, *cuts, len(data)])]
        logs.append({"path": str(tmp_path / f"log{n}_2026-10-21_19-04-33.csv"), "chunks": chunks})
    names = [str(tmp_path / name) for name in log_names]

    sent = [{**log, "chunks": [base64.b64encode(c).decode() for c in log["chunks"]]}
            for log in logs]  # fmt: skip
    [ours] = run([{"op": "mangohud", "configs": configs, "folders": folders, "names": names,
                   "logs": sent}])  # fmt: skip
    for config, got in zip(configs, ours["configs"], strict=True):
        with monkeypatch.context() as patch:
            for name in ("XDG_CONFIG_HOME", "LOGS", "NOPE"):
                patch.delenv(name, raising=False)
            for name, value in config["env"].items():
                patch.setenv(name, value)
            assert got == str(mangohud.config_folder(Path(config["home"]))), config
    assert len(set(ours["configs"])) > 12
    wanted = [mangohud.newest_log(Path(folder), at) for folder, at in folders]
    assert ours["folders"] == [None if path is None else str(path) for path in wanted]
    assert sum(1 for path in wanted if path) > 40
    assert ours["names"] == [mangohud.app_of(Path(name)) for name in names]
    for log, got in zip(logs, ours["logs"], strict=True):
        path = Path(log["path"])
        path.write_bytes(b"")
        reader = mangohud.LogReader(path)
        rounds = []
        for chunk in log["chunks"]:
            with path.open("ab") as file:
                file.write(chunk)
            rounds.append([ms if math.isfinite(ms) else repr(ms) for ms in reader.frames()])
        assert got == {"app": reader.app, "rounds": rounds}, log["path"]
    assert sum(len(frames) for got in ours["logs"] for frames in got["rounds"]) > 500


def test_what_plays_is_read_as_the_media_source_reads_it(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sensors/media.py: a track's readings as time goes on, a player's name, what
    playerctl's line and busctl's answer say plays, and a cover from its bytes, a file or
    the web as the picture the source shows."""
    import hashlib
    import http.server
    import io
    import threading

    from PIL import Image

    from libre_panel.sensors import media

    rng = random.Random(0x3ED1A)

    def track_list(track: media.Track | None) -> list[Any] | None:
        if track is None:
            return None
        return [track.state, track.title, track.artist, track.album, track.position,
                track.duration, track.cover, track.source, track.at, track.app_id]  # fmt: skip

    words = ["Song", "", "Ünï cödé", "a, b", "Daft Punk", "0", " ", "ǆungla", "ß", "x" * 40]
    tracks = []
    for _ in range(800):
        track = [rng.choice(["playing", "paused", "stopped", "Playing", "", "ǆ"]),
                 rng.choice(words), rng.choice(words), rng.choice(words),
                 rng.choice([None, 0.0, 12.25, 200.0, 1e9, -3.0]),
                 rng.choice([None, 0.0, 180.0, 200.0, 0.5]), rng.choice(words),
                 rng.choice([0.0, 10.0, 99.5])]  # fmt: skip
        tracks.append([track, rng.choice([0.0, 10.0, 11.5, 250.0, 5.0])])
    names = ["Spotify.exe", "Microsoft.ZuneMusic_8wekyb3d8bbwe!App", "C:\\Apps\\foo.bar\\VLC.EXE",
             "", "chrome", "com.squirrel.Teams.Teams", "a_b!c!d", "x.exe.exe", ".exe", "exe",
             "MSEdge", "308046B0AF4A39CB", "ünï.EXE", "a\\b_c.d!e"]  # fmt: skip
    fields = ["Playing", "Paused", "Stopped", "playing", "", "Artist", "Title", "Album", "0",
              "183000000", "12345678", "-5", "1_000", "x", "file:///tmp/a.png", "spotify",
              "firefox.instance_1_23", "9" * 30, " 42 ", "٣"]  # fmt: skip
    playerctl = []
    for _ in range(1500):
        count = rng.choice([8, 8, 8, 8, 7, 9, 1])
        line = "\x1f".join(rng.choice(fields) for _ in range(count))
        playerctl.append(line + rng.choice(["", "\n", "\n\n", " "]))

    def typed(value: Any) -> Any:
        return {"type": rng.choice(["s", "x", "as", "v"]), "data": value}

    def loose(value: Any) -> Any:
        return rng.choice([value, typed(value), typed(typed(value))])

    mpris = []
    for _ in range(1200):
        metadata = {}
        for key in rng.sample(["xesam:title", "xesam:artist", "xesam:album", "mpris:length",
                               "mpris:artUrl", "xesam:url"], rng.randrange(0, 7)):  # fmt: skip
            metadata[key] = loose(rng.choice([
                "Text", "", ["A", "", "B"], [], ["Solo"], 183000000, 0, 12.5, True, None,
                {"type": "s"}, [1, None, "x", {"a": 1}], "file:///c.jpg", -7, 2**70,
            ]))  # fmt: skip
        properties = {}
        if rng.random() < 0.9:
            properties["Metadata"] = loose(rng.choice([metadata, metadata, metadata, "no", None]))
        if rng.random() < 0.9:
            properties["PlaybackStatus"] = loose(rng.choice(["Playing", "Paused", "Stopped", "",
                                                             None, 5, "PLAYING"]))  # fmt: skip
        if rng.random() < 0.8:
            properties["Position"] = loose(rng.choice([0, 61500000, 1.5e6, True, "7", None, -1]))
        reply = rng.choice([{"type": "a{sv}", "data": [properties]}] * 6 + [
            {"data": []}, {"data": "text"}, {"data": [7]}, [properties], {"type": "x"}, None,
            {"data": {"0": properties}},
        ])  # fmt: skip
        name = rng.choice(["spotify", "firefox.i1", "", "VLC.x.y", "σΑΣ.b"])
        name = rng.choice(["org.mpris.MediaPlayer2." + name, "vlc"])
        mpris.append([json.dumps(reply), name])

    def picture(mode: str, size: tuple[int, int], kind: str) -> bytes:
        image = Image.effect_noise(size, 60).convert(mode)
        if mode in ("RGB", "RGBA"):
            image = Image.merge(mode, [image.getchannel(0).rotate(90 * n).resize(size)
                                       for n in range(len(mode))])  # fmt: skip
        out = io.BytesIO()
        image.save(out, kind)
        return out.getvalue()

    pictures = [picture("RGB", (800, 600), "JPEG"), picture("RGB", (2400, 1300), "JPEG"),
                picture("RGBA", (100, 40), "PNG"), picture("L", (513, 512), "PNG"),
                picture("P", (640, 640), "GIF"), picture("RGB", (1, 1), "PNG"),
                picture("L", (3000, 20), "JPEG"), picture("RGB", (700, 512), "WEBP")]  # fmt: skip
    files = []
    for n, data in enumerate(pictures[:4]):
        (tmp_path / f"cover {n}.img").write_bytes(data)
        files.append(tmp_path / f"cover {n}.img")
    (tmp_path / "big.img").write_bytes(pictures[0] + bytes(media.MAX_COVER))
    (tmp_path / "broken.img").write_bytes(pictures[0][:300])

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the handler's name for GET
            body = pictures[2] if self.path == "/cover.png" else b""
            self.send_response(200 if body else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    web = f"http://127.0.0.1:{server.server_port}"
    for name in ("NO_PROXY", "no_proxy"):  # (this computer's own server is asked directly)
        monkeypatch.setenv(name, "127.0.0.1,localhost")
    places = [str(files[0]), files[1].as_uri(), files[2].as_uri() + "?x=1#y", str(files[3]),
              str(tmp_path / "big.img"), str(tmp_path / "broken.img"), str(tmp_path / "none.img"),
              str(tmp_path), "", "file://", f"{web}/cover.png", f"{web}/nothing",
              "http://127.0.0.1:1/x.png"]  # fmt: skip
    covers: list[Any] = [*pictures, b"", b"not a picture", pictures[0][:500], *places]
    try:
        sent = [{"bytes": base64.b64encode(c).decode()} if isinstance(c, bytes) else {"place": c}
                for c in covers]  # fmt: skip
        [ours] = run([{"op": "media", "tracks": tracks, "names": names, "playerctl": playerctl,
                       "mpris": mpris, "covers": sent}])  # fmt: skip
        shown = []
        for cover in covers:
            image = media.load_cover(cover)
            shown.append(None if image is None else
                         [*image.size, hashlib.sha256(image.tobytes()).hexdigest()])  # fmt: skip
            assert image is None or image.mode == "RGBA"
    finally:
        server.shutdown()
    for (t, now), got in zip(tracks, ours["readings"], strict=True):
        track = media.Track(t[0], t[1], t[2], t[3], t[4], t[5], None, t[6], t[7])
        wanted = [[key, r.value, r.unit, r.label] for key, r in track.readings(now).items()]
        assert got == wanted, (t, now)
        for (_, value, *_), (_, said, *_) in zip(got, wanted, strict=True):
            assert type(value) is type(said)
    assert ours["names"] == [media._app_name(name) for name in names]
    assert ours["playerctl"] == [track_list(media.parse_playerctl(t, 12.5)) for t in playerctl]
    assert sum(1 for track in ours["playerctl"] if track) > 150
    wanted = [track_list(media.parse_mpris(json.loads(reply), name, 12.5))
              for reply, name in mpris]  # fmt: skip
    assert ours["mpris"] == wanted
    assert sum(1 for track in wanted if track) > 80
    assert ours["covers"] == shown
    assert sum(1 for cover in shown if cover) == 13  # (8 pictures, 4 files, 1 from the web)


@pytest.mark.skipif(sys.platform == "win32", reason="folders laid out as Linux names them")
def test_a_players_icon_is_found_as_appicon_finds_it(tmp_path: Any) -> None:
    """sensors/appicon.py: the program names of a player's id, the logo a package's manifest
    names and the file behind it, and on Linux a player's desktop entry, its icon's name and
    file; every icon as the badge of 96 pixels."""
    import hashlib
    from pathlib import Path

    from PIL import Image

    from libre_panel.sensors import appicon

    rng = random.Random(0xA11C)

    def shown(image: Image.Image | None) -> list[Any] | None:
        if image is None:
            return None
        assert image.mode == "RGBA"
        return [*image.size, hashlib.sha256(image.tobytes()).hexdigest()]

    def picture(file: Path, mode: str, size: tuple[int, int]) -> None:
        file.parent.mkdir(parents=True, exist_ok=True)
        noise = Image.effect_noise(size, 70)
        bands = [noise.rotate(90 * n).resize(size) for n in range(len(mode))]
        Image.merge(mode, bands).save(file, "PNG")

    ids = ["Spotify.exe", "MSEdge", "Chrome.ABCDEF", "SpotifyAB.SpotifyMusic_zpdnekdrz!Spotify",
           "", " ", "a!b!c", "Pkg_hash!", "!App", "x.y.z.EXE", " VLC ", "ǅ.exe", "a_b",
           "İ.x"]  # fmt: skip
    parts = ["Logo", "Square44x44Logo", "150x150", "scale-200", "scale-100", "targetsize-48",
             "targetsize-96", "targetsize-256", "altform-unplated", "altform-lightunplated",
             "ALTFORM-UNPLATED", "TargetSize-128", "scale-", "targetsize-x", "scale-99999",
             "contrast-black", "targetsize-95"]  # fmt: skip
    ranks = [[".".join(rng.sample(parts, rng.randrange(1, 5))) + ".png", rng.choice([44, 150, 0])]
             for _ in range(1500)]  # fmt: skip

    manifests, logos, packages = [], [], []
    for n in range(200):
        folder = tmp_path / f"pkg{n}"
        folder.mkdir()
        apps = []
        for app in rng.sample(["App", "Spotify", "Other", ""], rng.randrange(0, 4)):
            names = rng.sample(["Square44x44Logo", "Square150x150Logo", "Logo", "SmallLogo",
                                "BackgroundColor"], rng.randrange(0, 4))  # fmt: skip
            files = [rng.choice(["Assets\\\\" + name + ".png", ""]) for name in names]
            attrs = " ".join(f'{name}="{file}"' for name, file in zip(names, files, strict=True))
            visual = rng.choice([f"<uap:VisualElements {attrs}/>",
                                 f"<Extensions><uap:VisualElements {attrs}></uap:VisualElements>"
                                 "</Extensions>", "", f"<VisualElements {attrs}/>"])  # fmt: skip
            identity = f' Id="{app}"' if app or rng.random() < 0.5 else ""
            apps.append(f"<Application{identity}>{visual}</Application>")
        text = ('<?xml version="1.0" encoding="utf-8"?>\n<Package xmlns="http://a/foundation" '
                'xmlns:uap="http://a/uap"><Applications>' + "".join(apps)
                + "</Applications></Package>")  # fmt: skip
        if rng.random() < 0.08:
            text = text[: rng.randrange(20, len(text))]
        if rng.random() < 0.9:
            (folder / "AppxManifest.xml").write_text(
                text, encoding=rng.choice(["utf-8", "utf-8", "utf-8-sig"])
            )
        application = rng.choice(["App", "Spotify", "", "None"])
        manifests.append([str(folder), application])
        forms = ["", ".scale-100", ".scale-200", ".scale-400", ".targetsize-48",
                 ".targetsize-96", ".targetsize-256_altform-unplated",
                 ".targetsize-128_altform-lightunplated", ".contrast-black_scale-80",
                 ".targetsize-24_altform-unplated"]  # fmt: skip
        logo_names = ["Square44x44Logo", "Square150x150Logo", "Logo", "SmallLogo"]
        base = rng.choice(logo_names)
        for logo in logo_names:
            for form in rng.sample(forms, rng.randrange(0, 5)):
                side = rng.choice([24, 44, 96, 150])
                size = (side, rng.choice([side, 60]))
                picture(folder / "Assets" / f"{logo}{form}.png", "RGBA", size)
        (folder / "Assets").mkdir(exist_ok=True)
        (folder / "Assets" / f"{base}.txt").write_text("no picture", encoding="utf-8")
        logos.append(str(folder / "Assets" / f"{base}.png"))
        packages.append([f"Family_hash!{application}", [str(folder)]])
    for n in range(0, 200, 7):  # (several versions installed: the highest folder name first)
        packages.append(["Fam!App", [str(tmp_path / f"pkg{m}") for m in (n, n + 1, n + 2)]])

    files = []
    kinds = [("RGBA", (256, 256)), ("RGB", (300, 120)), ("L", (40, 97)), ("RGBA", (96, 96)),
             ("LA", (1, 1)), ("RGB", (31, 30))]  # fmt: skip
    for n, (mode, size) in enumerate(kinds):
        picture(tmp_path / f"icon{n}.png", mode, size)
        files.append(str(tmp_path / f"icon{n}.png"))
    (tmp_path / "icon.svg").write_text("<svg/>", encoding="utf-8")
    files += [str(tmp_path / "icon.svg"), str(tmp_path / "none.png"), str(tmp_path)]

    dirs = []
    for n in range(60):
        env = {}
        if rng.random() < 0.5:
            env["XDG_DATA_DIRS"] = rng.choice(["/a/share:/b/share", "", ":/usr/share::/usr/share/",
                                                "/opt/x//share:/opt/x/share"])  # fmt: skip
        if rng.random() < 0.5:
            env["XDG_DATA_HOME"] = rng.choice(["/home/u/data", "", "/usr/share"])
        dirs.append({"env": env, "home": f"/home/user{n}"})

    players = []
    for n in range(150):
        roots = [tmp_path / f"data{n}a", tmp_path / f"data{n}b"]
        player = rng.choice(["spotify", "Firefox", "vlc", "chromium", "Rhythmbox"])
        name = player.lower()
        for root in roots:
            (root / "applications").mkdir(parents=True)
            for entry in rng.sample([f"{name}.desktop", f"org.mozilla.{name}.desktop",
                                     f"{name}-esr.desktop", f"com.{name}.desktop", "other.desktop",
                                     f"{name}-beta-channel.desktop", f"x{name}.desktop"],
                                    rng.randrange(0, 4)):  # fmt: skip
                icon = rng.choice([name, f"{name}-icon", str(tmp_path / "icon0.png"), "", "none"])
                lines = rng.sample(["[Desktop Entry]", "Name=Player", f"Icon={icon}",
                                    "[Desktop Action New]", "Icon=wrong", f" Icon = {icon}",
                                    f"  Icon={icon}  "], rng.randrange(1, 6))  # fmt: skip
                if rng.random() < 0.6:
                    lines = ["[Desktop Entry]", "Name=Player", f"Icon={rng.choice([name, icon])}"]
                (root / "applications" / entry).write_text("\n".join(lines), encoding="utf-8")
            for size in rng.sample(["512x512", "256x256", "48x48", "32x32", "scalable"],
                                   rng.randrange(0, 4)):  # fmt: skip
                side = int(size.split("x")[0]) if "x" in size else 64
                file = root / "icons/hicolor" / size / "apps" / f"{name}.png"
                picture(file, "RGBA", (side, side))
            if rng.random() < 0.3:
                picture(root / "pixmaps" / f"{name}-icon.png", "RGB", (48, 40))
            if rng.random() < 0.2:
                (root / "pixmaps").mkdir(exist_ok=True)
                (root / "pixmaps" / f"{name}.xpm").write_text("/* XPM */", encoding="utf-8")
        players.append([player, [str(root) for root in roots]])

    [ours] = run([{"op": "appicon", "ids": ids, "ranks": ranks, "manifests": manifests,
                   "logos": logos, "packages": packages, "files": files, "dirs": dirs,
                   "players": players}])  # fmt: skip
    assert ours["names"] == [sorted(appicon.program_names(app_id)) for app_id in ids]
    wanted = [appicon._logo_rank(name, base) for name, base in ranks]
    assert ours["ranks"] == [[plain, str(near)] for plain, near in wanted]
    assert ours["manifests"] == [appicon.manifest_logo(Path(f), app) for f, app in manifests]
    assert sum(1 for logo in ours["manifests"] if logo) > 15
    for declared, got in zip(logos, ours["logos"], strict=True):
        file = appicon.logo_file(Path(declared))
        assert (got is None) == (file is None), declared
        if file is not None:  # (of files as good as each other Python takes any)
            base = 44 if "44" in declared else 150 if "150" in declared else 44
            assert Path(got).is_file()
            assert appicon._logo_rank(Path(got).name, base) == appicon._logo_rank(file.name, base)
    assert ours["packages"] == [
        shown(appicon.package_logo(app, folders=lambda family, f=folders: [Path(p) for p in f]))
        for app, folders in packages
    ]
    assert sum(1 for logo in ours["packages"] if logo) > 20
    assert ours["files"] == [shown(appicon.load_file(Path(file))) for file in files]
    assert sum(1 for icon in ours["files"] if icon) == 6
    for case, got in zip(dirs, ours["dirs"], strict=True):
        assert [Path(folder) for folder in got] == appicon.data_dirs(
            case["env"], Path(case["home"])
        ), case
    found = 0
    for (player, roots), got in zip(players, ours["players"], strict=True):
        folders = [Path(root) for root in roots]
        entry = appicon.desktop_entry(player, folders)
        assert (got[0] is None) == (entry is None), (player, roots)
        if entry is None:
            continue
        # (of entries with names as long as each other Python takes any: the same folder)
        assert Path(got[0]).parent == entry.parent and len(Path(got[0]).name) == len(entry.name)
        name = appicon.desktop_icon_name(Path(got[0]))
        file = appicon.icon_file(name, folders) if name else None
        assert got[1:3] == [name, None if file is None else str(file)]
        assert got[3] == shown(appicon.load_file(file) if file else None)
        found += got[3] is not None
    assert found > 15


def _made_up_answer(rng: random.Random) -> tuple[dict[str, Any], float]:
    """An answer as Open-Meteo gives one for a made-up place and day, with what an answer may
    lack or have wrong, and a moment near it."""
    from datetime import UTC, datetime, timedelta

    offset = rng.choice([0, 3600, 7200, -18000, 19800, 45900, -34200])
    start = datetime(rng.randrange(2001, 2090), rng.randrange(1, 13), rng.randrange(1, 29))
    now = (start - timedelta(seconds=offset)).replace(tzinfo=UTC).timestamp()
    step = rng.randrange(0, 96) * 900
    now += rng.choice([rng.uniform(-90000, 8 * 86400), rng.uniform(0, 86400), step, step - 1e-6])

    def some(values: list[Any]) -> list[Any]:
        """The values with gaps, or cut short."""
        values = [None if rng.random() < 0.03 else v for v in values]
        return values[: rng.randrange(len(values) + 1)] if rng.random() < 0.1 else values

    def stamp(at: datetime, date: bool = False) -> str:
        if rng.random() < 0.01:
            return rng.choice(["", "soon", "2024-13-01T00:00", "2024-02-30"])
        return at.strftime("%Y-%m-%d") if date else at.strftime("%Y-%m-%dT%H:%M")

    hours = rng.choice([168, 168, 168, 30, 3, 0])
    days = rng.choice([7, 7, 7, 3, 9, 0])
    steps = rng.choice([672, 672, 96, 40, 5, 0])
    wet = rng.choice([0.0, 0.3, 0.6, 1.0])
    run_length = rng.choice([1, 3, 10, 40])
    rain: list[Any] = []
    while len(rain) < steps:
        amount = (
            rng.choice([0.1, 0.2, 1.5, 12]) if rng.random() < wet else rng.choice([0, 0.0, 0.05])
        )
        rain.extend([amount] * rng.randrange(1, run_length + 1))
    data: dict[str, Any] = {
        "latitude": 52.52,
        "utc_offset_seconds": offset,
        "current_units": {
            "temperature_2m": "°C",
            "apparent_temperature": "°C",
            "relative_humidity_2m": "%",
            "wind_speed_10m": "km/h",
            "weather_code": "wmo code",
            "is_day": "",
        },  # fmt: skip
        "current": {
            "time": "x",
            "interval": 900,
            "temperature_2m": rng.uniform(-30, 40),
            "apparent_temperature": rng.randrange(-30, 40),
            "relative_humidity_2m": rng.randrange(0, 101),
            "wind_speed_10m": round(rng.uniform(0, 90), 1),
            "weather_code": rng.choice([0, 1, 2, 3, 45, 61, 95, 99, 4, 100, 3.0, None]),
            "is_day": rng.randrange(2),
        },  # fmt: skip
        "hourly_units": {
            "temperature_2m": "°F",
            "precipitation_probability": "%",
            "weather_code": "wmo code",
            "is_day": "",
        },  # fmt: skip
        "hourly": {
            "time": [stamp(start + timedelta(hours=h)) for h in range(hours)],
            "temperature_2m": some([round(rng.uniform(-30, 40), 1) for _ in range(hours)]),
            "weather_code": some([rng.choice([0, 3, 61, 80]) for _ in range(hours)]),
            "precipitation_probability": some([rng.randrange(0, 101) for _ in range(hours)]),
            "is_day": some([rng.randrange(2) for _ in range(hours)]),
        },
        "daily_units": {
            "temperature_2m_max": "°C",
            "temperature_2m_min": "°C",
            "weather_code": "wmo code",
            "precipitation_probability_max": "%",
        },  # fmt: skip
        "daily": {
            "time": [stamp(start + timedelta(days=d), date=True) for d in range(days)],
            "temperature_2m_max": some([round(rng.uniform(0, 40), 1) for _ in range(days)]),
            "temperature_2m_min": some([round(rng.uniform(-30, 10), 1) for _ in range(days)]),
            "weather_code": some([rng.choice([0, 3, 61, 80]) for _ in range(days)]),
            "precipitation_probability_max": some([rng.randrange(0, 101) for _ in range(days)]),
        },
        "minutely_15": {
            "time": [stamp(start + timedelta(minutes=15 * s)) for s in range(steps)],
            "precipitation": some(rain[:steps]),
        },
    }
    for key in list(data):
        if rng.random() < 0.02:
            data[key] = rng.choice([None, {}])
        elif rng.random() < 0.02:
            del data[key]
    for part in ("current", "current_units", "hourly_units", "daily_units"):
        table = data.get(part)
        for key in list(table or {}):
            if rng.random() < 0.03:
                del table[key]
    return data, now


@pytest.mark.parametrize("language", ["en", "de", "fr", "zh"])
def test_the_weather_is_read_as_open_meteo_py_reads_it(language: str) -> None:
    """weather/open_meteo.py: the forecast's address, the present weather, the hours and
    the days to come and the rain of the next two hours of made-up answers at moments near
    them, in the panel's languages; and the times as datetime.fromisoformat reads them."""
    from datetime import datetime, timedelta

    from libre_panel import i18n
    from libre_panel.config import WeatherConfig
    from libre_panel.weather import open_meteo

    rng = random.Random(0x0E7E0 + len(language) + ord(language[0]))
    places = [[52.52, 13.405, False], [-33.87, 151.21, True], [0.0, -0.0, False],
              [1e-7, 1e16, True], [89.99999, -179.5, False]]  # fmt: skip
    answers = [_made_up_answer(rng) for _ in range(1500)]
    # fmt: off
    iso = [
        "2024-05-01", "20240501", "2024-W18-3", "2024W183", "2024-W18", "2024W18", "2020-W53-7",
        "2021-W53-1", "2024-05-01T12", "2024-05-01 12:30", "2024-05-01T12:30:45",
        "2024-05-01T123045", "2024-05-01T1230", "2024-05-01T12:30:45.5",
        "20240501T123045,123456789", "2024-05-01T12:30:45.1234567", "2024-05-01T12:30Z",
        "2024-05-01T12:30+02:00", "2024-05-01T12:30:45.25-05:30", "2024-05-01T12:30+02",
        "2024-05-01T12:30+0200", "2024-05-01T12:30+02:00:30.5", "2024-05-01T12:30-23:59:59.999999",
        "2024-05-01T12:30+24:00", "2024-05-01T12:30+00:00", "2024-05-01T12:30Zx",
        "2024-05-01T24:00", "2024-05-01T12:60", "2024-05-01T12:30:60", "2024-05-01T",
        "2024-05-01T1", "2024-05-01T12:3", "2024-05-01T12:30:", "2024-05-01T12:30:45.",
        "2024-05-01T12:30:45:1", "2024-05-01T12304567", "2024-05-01T1230456", "2024-05-01é12:30",
        "2024-5-1", "2024-05-1", "0000-01-01", "9999-12-31T23:59:59.999999", "9999-W52-7",
        "9999-W53-1", "0001-W01-1", "2024-W00-1", "2024-W54-1", "2024-W18-0", "2024-W18-8",
        "2024-W1", "2024-W18-", "2024-W18-3T10", "2024W183T10", "2024-W18T10", "2024-W18-10",
        "2024W1810", "2024-02-30", "2024-00-10", "2024-13-10", "202405", "2024-05", "", "x",
        "2024-05-01T12:30+", "2024-05-01T-02:00", "2024-05-01T12.5", "2024-05-01T12,5+01",
    ]
    # fmt: on
    letters = "0123456789-:.,TWZ+ "
    for _ in range(3000):
        text = rng.choice(iso[:40])
        for _ in range(rng.randrange(1, 3)):
            at = rng.randrange(len(text) + 1)
            text = (
                text[:at] + rng.choice(letters) * rng.randrange(2) + text[at + rng.randrange(2) :]
            )
        iso.append(text)
    [ours] = run([{"op": "weather", "locale": str(i18n.LOCALE_DIR), "language": language,
                   "urls": places, "iso": iso,
                   "answers": [[json.dumps(data), now] for data, now in answers]}])  # fmt: skip

    for (latitude, longitude, imperial), url in zip(places, ours["urls"], strict=True):
        cfg = WeatherConfig(latitude=latitude, longitude=longitude,
                            units="imperial" if imperial else "metric")  # fmt: skip
        params = open_meteo.build_params(cfg)
        from urllib.parse import urlencode

        assert url == f"{open_meteo.FORECAST_URL}?{urlencode(params)}"

    def rows(readings: dict[str, Any]) -> list[list[Any]]:
        return [[key, {"float": repr(r.value)} if isinstance(r.value, float) else r.value,
                 r.unit, r.label] for key, r in readings.items()]  # fmt: skip

    seen: set[str] = set()
    i18n.set_language(language)
    try:
        for (data, now), (current, forecast) in zip(answers, ours["answers"], strict=True):
            assert current == rows(open_meteo.parse_current(data)), data
            wanted = open_meteo.forecast_readings(data, now)
            assert forecast == rows(wanted), (now, data)
            seen.update(key.rsplit(".", 1)[-1] if "rain" not in key else key for key in wanted)
            if "weather.rain.minutes" not in wanted and "weather.rain.text" in wanted:
                seen.add("stays")
            seen.update(f"hours {sum(key.endswith('.time') for key in wanted)}" for _ in "1")
    finally:
        i18n.set_language("en")
    assert seen >= {"weather.rain.minutes", "weather.rain.text", "stays", "time", "name", "high",
                    "hours 24", "hours 0"}  # fmt: skip

    valid = 0
    for text, got in zip(iso, ours["iso"], strict=True):
        try:
            at = datetime.fromisoformat(text)
        except ValueError:
            assert got is None, text
            continue
        valid += 1
        stamp = [at.year, at.month, at.day, at.hour, at.minute, at.second, at.microsecond]
        assert got is not None and got[0] == stamp, text
        if at.tzinfo is None:
            assert len(got) == 1, text
        else:
            assert got[1] * 10**6 + got[2] == at.utcoffset() // timedelta(microseconds=1), text
    assert valid > 500


def _made_up_json(rng: random.Random, depth: int = 0) -> Any:
    """A value as an address may answer it: tables and lists in one another, numbers, texts."""
    kind = rng.random()
    if depth < 5 and kind < 0.3:
        keys = ["cpu", "temp", "a", "b", "c", "0", "1", "name", "My Value!", "größe", "", "__x__",
                "a.b", "x" * 50, "items", "²", "list"]  # fmt: skip
        if rng.random() < 0.03:
            keys = [f"k{n}" for n in range(130)]
            return {key: _made_up_json(rng, 5) for key in keys}
        count = rng.randrange(0, 7)
        return {key: _made_up_json(rng, depth + 1) for key in rng.sample(keys, count)}
    if depth < 5 and kind < 0.5:
        if rng.random() < 0.03:
            return [_made_up_json(rng, 5) for _ in range(120)]
        return [_made_up_json(rng, depth + 1) for _ in range(rng.randrange(0, 5))]
    return rng.choice([True, False, None, 0, 7, -3, 2.5, 1e300, "text", "", "x" * 300, "21.5",
                       rng.random(), rng.randrange(-1000, 1000), 2**70,
                       10**400 if rng.random() < 0.05 else 1])  # fmt: skip


def test_home_assistant_and_json_answers_are_read_as_python_reads_them() -> None:
    """sensors/homeassistant.py and sensors/json_source.py: the entities a pattern names
    with their numbers, texts and attributes; what an entry of `sources` describes or why it
    is none, a field's path into an answer, and every value of an answer without fields, to
    the limits; an error where Python raises."""
    from libre_panel.sensors import homeassistant, json_source

    rng = random.Random(0x4A50)
    ids = ["sensor.living_room_temperature", "sensor.kitchen", "binary_sensor.door",
           "climate.hall", "light.kitchen", "", None, 5, "Sensor.Upper"]  # fmt: skip
    raw = ["21.5", "on", "unavailable", " Unknown ", "NONE", "", None, True, False, 5, 2.5,
           10**400, -(10**400), "1_000", "٣", [1], {"a": 1}, "1e400", " 7 ", "nan"]  # fmt: skip
    pattern_sets = [["sensor.*", "binary_sensor.*"], ["*"], ["climate.*", "light.kitchen"],
                    ["sensor.[lk]*"], [], ["*.kitchen"], ["[!s]*"]]  # fmt: skip
    cases = []
    for _ in range(600):
        states: list[Any] = []
        for _ in range(rng.randrange(0, 8)):
            state: dict[str, Any] = {"state": rng.choice(raw)}
            if rng.random() < 0.95:
                state["entity_id"] = rng.choice(ids)
            attributes: Any = {}
            names = ["friendly_name", "unit_of_measurement", "icon", "min_temp", "brightness",
                     "current_temperature", "device_class", "mode", "list", "flag"]  # fmt: skip
            for key in rng.sample(names, rng.randrange(0, 6)):
                attributes[key] = rng.choice(["Living room", "°C", "", None, 5, 21.5, True,
                                              10**400, [1, 2], {"a": 1}, "heat"])  # fmt: skip
            if rng.random() < 0.9:
                state["attributes"] = attributes
            if rng.random() < 0.03:
                state["attributes"] = rng.choice([None, [], [1], "text", 0, 5])
            states.append(state if rng.random() < 0.985 else rng.choice([None, "x", [1], 5]))
        cases.append((states, rng.choice(pattern_sets)))

    paths = ["cpu", "a.b", "a.b[0].c", "a.b.0.c", "list[2]", "[0]", "a..b", "items[²]", "items.²",
             "items.٣", "list[٣]", "0", "list.1.name", "a[0][1]", "", ".", "a[x]", "a[1", "a.-1",
             "temp.0", "name", "c[12345678901234567890]"]  # fmt: skip
    sources = []
    for _ in range(500):
        entry: dict[str, Any] = {}
        for key, values in {
            "name": ["nas", "My NAS!", "", None, 5, "é" * 50, "__", "a" * 60, 2.5, True],
            "url": ["http://nas.local/api", "ftp://nas", " https://y/z ", "", None, 5, "HTTP://X"],
            "file": ["/tmp/values.json", "", " ~/v.json ", None, 7],
            "fields": [{}, None, [1], "x", {"cpu": rng.choice(paths), "My Temp!": rng.choice(paths),
                                           "n": 5, "t": True, "l": [1, "a"], "f": 2.50,
                                           "same!": "a", "same?": rng.choice(paths)}],
            "units": [{}, None, {"cpu": "%", "My Temp!": "°C", "n": 5, "0": "W", "a": None}, [2]],
            "headers": [{}, None, {"Authorization": "Bearer x", "X-N": 5, "x": None}, "h"],
        }.items():  # fmt: skip
            if rng.random() < 0.8:
                entry[key] = rng.choice(values)
        sources.append((entry, [_made_up_json(rng, rng.choice([0, 0, 4])) for _ in range(6)]))

    [ours] = run([{"op": "web_sources",
                   "states": [[json.dumps(states), patterns] for states, patterns in cases],
                   "json": [[json.dumps(entry), [json.dumps(answer) for answer in answers]]
                            for entry, answers in sources]}])  # fmt: skip

    def rows(readings: dict[str, Any]) -> list[list[Any]]:
        return [[key, {"float": repr(r.value)} if isinstance(r.value, float) else r.value,
                 r.unit, r.label] for key, r in readings.items()]  # fmt: skip

    def tried(make: Any) -> Any:
        try:
            return rows(make())
        except Exception:  # noqa: BLE001 - whatever Python raises, Rust says an error
            return "error"

    def said(got: Any) -> Any:
        return "error" if isinstance(got, dict) and "error" in got else got

    seen = {"error": 0, "readings": 0, "attribute": 0, "no source": 0, "limit": 0}
    for (states, patterns), got in zip(cases, ours["states"], strict=True):
        want = tried(lambda s=states, p=patterns: homeassistant.readings_from_states(s, p))
        assert said(got) == want, (states, patterns)
        seen["error" if want == "error" else "readings"] += 1
        seen["attribute"] += want != "error" and any(row[0].count(".") > 2 for row in want)
    for (entry, answers), got in zip(sources, ours["json"], strict=True):
        try:
            source = json_source.Source(entry)
        except ValueError as exc:
            assert got == {"error": str(exc)}, entry
            seen["no source"] += 1
            continue
        assert "error" not in got, (entry, got)
        described = (source.name, source.url, source.file, [list(p) for p in source.fields.items()],
                     [list(p) for p in source.units.items()],
                     [list(p) for p in source.headers.items()])  # fmt: skip
        assert (got["name"], got["url"], got["file"], got["fields"], got["units"],
                got["headers"]) == described, entry  # fmt: skip
        for answer, read in zip(answers, got["answers"], strict=True):
            want = tried(lambda a=answer, s=source: s.readings(a))
            assert said(read) == want, (entry, answer)
            seen["error" if want == "error" else "readings"] += 1
            seen["limit"] += want != "error" and len(want) == json_source.MAX_VALUES
    assert all(count > 5 for count in seen.values()), seen


@pytest.mark.parametrize("language", ["en", "de"])
def test_a_ups_is_read_as_nut_py_reads_it(language: str) -> None:
    """sensors/nut.py: upsd's lines split as shlex splits them (quotes, backslashes, what
    does not close), and the readings of a UPS's variables: the battery, the runtime in
    minutes, the power the UPS says or its load of the nominal power, the status in words."""
    import shlex

    from libre_panel import i18n
    from libre_panel.sensors import nut

    rng = random.Random(0x0705)
    lines = ['VAR ups battery.charge "100"', 'UPS ups "My \\"big\\" UPS"', "", "  ", "a  b\tc",
             'VAR u ups.model "Back-UPS ES 700G"', "a\\ b", '"a\\b"', "'a\\'", '"', "a\\", "''",
             'x"y z"w', "a '' b", '"\\\\"', "é 'ü ö'", 'a"b\'c"d']  # fmt: skip
    for _ in range(4000):
        lines.append("".join(rng.choice("ab \"'\\ \t\n\r#é") for _ in range(rng.randrange(0, 12))))
    names = ["battery.charge", "battery.runtime", "ups.load", "ups.realpower", "input.voltage",
             "ups.realpower.nominal", "ups.status", "ups.model"]  # fmt: skip
    texts = ["100", "12.5", "abc", "", " 7 ", "1e3", "3599", "90", "0", "-5", "450", "230.4", "1_0"]
    flags = ["OL", "OB LB", "OB", "OL CHRG", "", "  ", "LB", "OL  LB", "ob", "OB\tDISCHRG", "X"]
    values = []
    for _ in range(2000):
        ups = {name: rng.choice(texts) for name in names if rng.random() < 0.7}
        if "ups.status" in ups:
            ups["ups.status"] = rng.choice(flags)
        values.append(ups)
    [ours] = run([{"op": "nut", "locale": str(i18n.LOCALE_DIR), "language": language,
                   "lines": lines, "values": values}])  # fmt: skip

    seen = {"error": 0, "quoted": 0, "computed": 0, "low": 0}
    for line, got in zip(lines, ours["lines"], strict=True):
        try:
            assert got == shlex.split(line), line
            seen["quoted"] += "" in got
        except ValueError as exc:
            assert got == {"error": str(exc)}, line
            seen["error"] += 1
    i18n.set_language(language)
    try:
        for ups, got in zip(values, ours["values"], strict=True):
            wanted = nut.readings(ups)
            rows = [[key, r.value, r.unit, r.label] for key, r in wanted.items()]
            for row in rows:
                if isinstance(row[1], float):
                    row[1] = {"float": repr(row[1])}
            assert got == rows, ups
            seen["computed"] += isinstance(getattr(wanted.get("ups.power"), "value", None), int)
            seen["low"] += "LB" in ups.get("ups.status", "").split()
    finally:
        i18n.set_language("en")
    assert all(count > 20 for count in seen.values()), seen


def test_mqtt_messages_are_read_as_mqtt_py_reads_them() -> None:
    """sensors/mqtt.py: a topic against patterns with + and #, a message's payload as a
    number, a text or a JSON object taken apart, with the unit of the first pattern that
    matches; and the client's packets as MQTT 3.1.1 writes them."""
    from libre_panel.sensors import mqtt

    rng = random.Random(0x1883)
    levels = ["home", "kitchen", "temperature", "+", "#", "", "a.b", "my room", "zigbee2mqtt"]
    topics = ["/".join(rng.choice(levels) for _ in range(rng.randrange(1, 5))) for _ in range(300)]
    matches = [[rng.choice(topics), rng.choice(topics)] for _ in range(3000)]
    payloads = [b"21.5", b" 40 ", b"on", b"", b"true", b"null", b"[1, 2]", b'"text"', b"NaN",
                b'{"temperature": 21.5, "humidity": 40, "state": "ON", "ok": true, "x": null}',
                b'{"my value": " 7 ", "list": [1], "big": 1e400, "n": {"a": 1}}', b"1e400",
                b"\xff\xfe21", b"caf\xc3\xa9", b"\xc3", b'{"a": ', b"x" * 300, b"-Infinity",
                b'{"a b": "' + b"y" * 250 + b'"}', str(10**400).encode(), b"1_0", b"\xe2\x82",
                b'{"temp": "21,5", "t2": "\\u0663"}', b"\xf0\x9f\x98\x80 ok", b"0x10"]  # fmt: skip
    unit_sets = [[], [["home/+/temperature", "°C"], ["#", "?"]], [["zigbee2mqtt/#", "%"]],
                 [["+/kitchen", "W"], ["home/#", "V"]]]  # fmt: skip
    messages = [(rng.choice(topics), rng.choice(payloads), rng.choice(unit_sets))
                for _ in range(3000)]  # fmt: skip

    def text(value: int | bytes, wide: int = 2) -> bytes:
        if isinstance(value, int):
            return value.to_bytes(wide, "big")
        return len(value).to_bytes(2, "big") + value

    publish = b"\x30" + bytes([2 + 3 + 4]) + text(b"a/b") + b"21.5"
    long = text(b"long/topic") + b"z" * 300
    packets = [publish, publish[:5], b"", b"\x30", b"\x20\x02\x00\x00", b"\x20\x02\x00\x05",
               b"\x90\x03\x00\x01\x00", b"\xd0\x00rest", b"\x30\xb8\x02" + long,
               b"\x30\xb8\x02" + long[:200], b"\x32\x07" + text(b"q") + b"\x00\x09ok",
               b"\x30\xff\xff\xff\xff\x01", b"\x30\x80\x80\x80\x01", b"\x30\x01\x00", b"\x20\x00",
               b"\x30\x04\x00\x05ab"]  # fmt: skip
    [ours] = run([{"op": "mqtt", "matches": matches,
                   "messages": [[topic, base64.b64encode(payload).decode(), units]
                                for topic, payload, units in messages],
                   "packets": [base64.b64encode(packet).decode() for packet in packets],
                   "client": "libre-panel-1", "username": "üser", "password": "pw",
                   "topic": "home/+/temperature"}])  # fmt: skip

    wanted = [mqtt.topic_matches(pattern, topic) for pattern, topic in matches]
    assert ours["matches"] == wanted and 100 < sum(wanted) < 2900
    seen = {"apart": 0, "text": 0, "number": 0, "unit": 0, "nothing": 0}
    for (topic, payload, units), got in zip(messages, ours["messages"], strict=True):
        readings = mqtt.readings_from_message(topic, payload, dict(units))
        rows = [[key, r.value, r.unit, r.label] for key, r in readings.items()]
        seen["apart"] += len(rows) > 1
        seen["nothing"] += not rows
        for row in rows:
            seen["unit"] += bool(row[2])
            seen["text" if isinstance(row[1], str) else "number"] += 1
            if isinstance(row[1], float):
                row[1] = {"float": repr(row[1])}
        assert got == rows, (topic, payload, units)
    assert all(count > 50 for count in seen.values()), seen

    b64 = base64.b64encode
    assert ours["packets"] == [
        ["message", "a/b", b64(b"21.5").decode(), 11], None, None, None, ["connected", 0, 4],
        ["connected", 5, 4], ["other", 5], ["other", 2],
        ["message", "long/topic", b64(b"z" * 300).decode(), 315], None,
        ["message", "q", b64(b"ok").decode(), 9],
        {"error": "a packet's length of more than four bytes"},
        {"error": "a packet of 2097152 bytes"}, {"error": "a short PUBLISH"},
        {"error": "a short CONNACK"}, {"error": "a short PUBLISH"},
    ]  # fmt: skip
    body = (text(b"MQTT") + b"\x04\xc2" + text(60) + text(b"libre-panel-1")
            + text("üser".encode()) + text(b"pw"))  # fmt: skip
    assert base64.b64decode(ours["connect"]) == b"\x10" + bytes([len(body)]) + body
    body = text(7) + text(b"home/+/temperature") + b"\x00"
    assert base64.b64decode(ours["subscribe"]) == b"\x82" + bytes([len(body)]) + body


def _made_up_calendar(rng: random.Random, now: Any) -> str:
    """An .ics text as calendars write them, with what they may have odd or wrong: events
    around ``now`` in zones of several kinds, whole days, repeating ones with their rules,
    left-out and moved days, alarms inside, folded lines."""
    from datetime import timedelta

    def stamp(at: Any, kind: str | None = None) -> str:
        kind = kind or rng.choice(["utc", "utc", "zone", "zone", "windows", "mozilla", "floating",
                                   "nowhere", "day", "date", "bad", "lower"])  # fmt: skip
        clock = at.strftime("%Y%m%dT%H%M%S")
        return {
            "utc": f":{clock}Z",
            "zone": f";TZID={rng.choice(['Europe/Berlin', 'America/New_York', 'Asia/Kolkata'])}"
                    f":{clock}",
            "windows": f';TZID="{rng.choice(["W. Europe Standard Time", "Tokyo Standard Time"])}"'
                       f":{clock}",
            "mozilla": f";TZID=/mozilla.org/20050126_1/America/Chicago:{clock}",
            "floating": f":{clock}",
            "nowhere": f";TZID=Nowhere/Land:{clock}",
            "day": f";VALUE=DATE:{at.strftime('%Y%m%d')}",
            "date": f":{at.strftime('%Y%m%d')}",
            "bad": rng.choice([":2024-05-01", ":20240230T100000", ":", ":20240501T2500", ":x",
                               ":20240501T100060Z", ": 20240501T100000 ", ":20240501T1000"]),
            "lower": f":{clock.lower()}z",
        }[kind]  # fmt: skip

    rules = ["FREQ=DAILY", "FREQ=DAILY;INTERVAL=2;COUNT=5", "FREQ=DAILY;INTERVAL=3",
             "FREQ=WEEKLY;BYDAY=MO,WE,FR", "FREQ=WEEKLY", "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU,TU",
             "FREQ=WEEKLY;UNTIL={until}", "FREQ=DAILY;UNTIL={day}", "FREQ=MONTHLY;BYDAY=2TU",
             "FREQ=MONTHLY;BYDAY=-1FR,1MO", "FREQ=MONTHLY;BYMONTHDAY=15,-1", "FREQ=MONTHLY",
             "FREQ=MONTHLY;BYDAY=0MO", "FREQ=MONTHLY;BYDAY=6MO", "FREQ=MONTHLY;BYDAY=XX,+2WE",
             "FREQ=YEARLY", "FREQ=YEARLY;INTERVAL=2", "FREQ=HOURLY", "", "freq=daily;count=3",
             "FREQ=DAILY;INTERVAL=abc", "FREQ=DAILY;COUNT=²", "FREQ=MONTHLY;BYDAY=+MO",
             "FREQ=MONTHLY;BYMONTHDAY=--5", "FREQ=DAILY;INTERVAL=0", "FREQ=DAILY;INTERVAL=",
             "FREQ=DAILY;INTERVAL=999999999999", "FREQ=MONTHLY;INTERVAL=99999;BYMONTHDAY=1",
             "FREQ=YEARLY;INTERVAL=5000", "FREQ=WEEKLY;COUNT=0", "FREQ=DAILY;UNTIL=soon",
             "FREQ=MONTHLY;BYMONTHDAY=31", "FREQ=WEEKLY;INTERVAL=99999999999",
             "FREQ=MONTHLY;BYDAY=99999999999TU", "FREQ=DAILY;COUNT=7;UNTIL={until}"]  # fmt: skip
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0"]
    if rng.random() < 0.5:
        lines.append(rng.choice(["X-WR-CALNAME:Work\\, home", "X-WR-CALNAME:", "x-wr-calname:Me"]))
    if rng.random() < 0.3:
        lines += ["BEGIN:VTIMEZONE", "TZID:Europe/Berlin", "BEGIN:STANDARD",
                  "DTSTART:19701025T030000", "END:STANDARD", "END:VTIMEZONE"]  # fmt: skip
    for _ in range(rng.randrange(0, 7)):
        start = now + timedelta(days=rng.choice([0, 0, 1, 2, 6, 7, 13, 30, -1, -2, -400, -3000]),
                                minutes=rng.randrange(-1500, 1500))  # fmt: skip
        start = start.replace(second=0, microsecond=0)
        if rng.random() < 0.1:  # (where the clocks change, and a leap day)
            start = start.replace(month=rng.choice([3, 10, 11]), day=rng.randrange(1, 29),
                                  hour=rng.randrange(0, 4))  # fmt: skip
        if rng.random() < 0.03:
            start = start.replace(year=2024, month=2, day=29)
        kind = rng.choice([None, None, "utc", "zone", "day"])
        lines += ["BEGIN:VEVENT", f"UID:u{rng.randrange(4)}", f"DTSTART{stamp(start, kind)}"]
        ends = rng.random()
        if ends < 0.4:
            end = start + timedelta(minutes=rng.choice([30, 60, 90, 1440, 2880, -30, 0]))
            lines.append(f"DTEND{stamp(end, kind)}")
        elif ends < 0.7:
            lines.append("DURATION:" + rng.choice(["PT1H30M", "P1D", "-PT1H", "P1W", "PT", "x",
                                                   "P2DT3H4M5S", " PT45M ", "P999999999D",
                                                   "PT99999999999999999999S", "+P1D"]))  # fmt: skip
        if rng.random() < 0.9:
            titles = ["Team\\, sync\\nroom\\; x \\\\ y", "Lunch", "", "Zahnärztin", "a:b",
                      "Call \\N two", "Lunch"]  # fmt: skip
            lines.append("SUMMARY:" + rng.choice(titles))
        if rng.random() < 0.3:
            lines.append("LOCATION" + rng.choice([":Room 1", ';LANGUAGE="de:DE":Büro', ":"]))
        if rng.random() < 0.08:
            lines.append(rng.choice(["STATUS:CANCELLED", "STATUS:cancelled", "STATUS:CONFIRMED"]))
        if rng.random() < 0.6:
            until = start + timedelta(days=rng.randrange(-2, 40))
            rule = rng.choice(rules).format(until=until.strftime("%Y%m%dT%H%M%SZ"),
                                            day=until.strftime("%Y%m%d"))  # fmt: skip
            lines.append(f"RRULE:{rule}")
            for _ in range(rng.randrange(0, 3)):
                out = start + timedelta(days=rng.randrange(0, 15))
                more = start + timedelta(days=rng.randrange(0, 15))
                also = rng.choice(["", f",{more.strftime('%Y%m%dT%H%M%S')}", ",junk"])
                lines.append(f"EXDATE{stamp(out, kind)}{also}")
        if rng.random() < 0.15:
            moved = start + timedelta(days=rng.randrange(0, 15))
            lines.append(f"RECURRENCE-ID{stamp(moved, kind)}")
        if rng.random() < 0.2:
            lines += ["BEGIN:VALARM", "TRIGGER:-PT15M", "SUMMARY:alarm", "DTSTART:19990101",
                      "END:VALARM"]  # fmt: skip
        lines.append("END:VEVENT" if rng.random() < 0.97 else "END:VTODO")
    lines.append("END:VCALENDAR")
    if rng.random() < 0.3:  # (a long line folded)
        at = rng.randrange(len(lines))
        cut = rng.randrange(len(lines[at]) + 1)
        lines[at] = lines[at][:cut] + rng.choice(["\n ", "\n\t"]) + lines[at][cut:]
    return rng.choice(["\r\n", "\n", "\r"]).join(lines) + "\n"


@pytest.mark.parametrize(
    ("zone", "language"),
    [(None, "en"), ("Europe/Berlin", "de"), ("America/New_York", "en"), ("Asia/Kolkata", "fr"),
     ("Australia/Lord_Howe", "en")],
)  # fmt: skip
def test_calendars_are_read_as_calendar_py_reads_them(
    zone: str | None, language: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sensors/calendar.py: the events of made-up .ics texts (times of zones, whole days,
    rules with their ends, counts, left-out and moved days) in the computer's zone and in
    others, and the readings of the next ones in the panel's languages; a text Python
    cannot read is none here."""
    from datetime import datetime, timedelta

    from libre_panel import i18n
    from libre_panel.sensors import calendar

    if zone is not None:
        if sys.platform == "win32":
            pytest.skip("the computer's zone set by TZ and tzset")
        monkeypatch.setenv("TZ", zone)
        time.tzset()
    try:
        rng = random.Random(0x1C5 + len(language) + (len(zone) if zone else 0))
        cases = []
        for _ in range(1200):
            now = datetime(2020, 1, 1) + timedelta(seconds=rng.randrange(0, 12 * 366 * 86400),
                                                   microseconds=rng.randrange(10**6))  # fmt: skip
            if rng.random() < 0.3:
                now = now.replace(minute=0, second=0, microsecond=0)
            days = rng.choice([1, 14, 14, 60, 366])
            name = rng.choice(["", "", "Mine"])
            cases.append((_made_up_calendar(rng, now), now, days, name))
        [ours] = run([{"op": "calendar", "locale": str(i18n.LOCALE_DIR), "language": language,
                       "cases": [[text, [now.year, now.month, now.day, now.hour, now.minute,
                                         now.second, now.microsecond], days, name]
                                 for text, now, days, name in cases]}])  # fmt: skip

        def fields(at: datetime) -> list[int]:
            return [at.year, at.month, at.day, at.hour, at.minute, at.second, at.microsecond]

        seen = {"error": 0, "events": 0, "repeated": 0, "whole days": 0, "going": 0, "moved": 0}
        i18n.set_language(language)
        try:
            for (text, now, days, name), got in zip(cases, ours["cases"], strict=True):
                try:
                    events = calendar.events_from_ics(text, now, days, name)
                except Exception:  # noqa: BLE001 - whatever Python raises, Rust says an error
                    assert "error" in got, text
                    seen["error"] += 1
                    continue
                assert "error" not in got, (text, got)
                found = [[e.title, fields(e.start), fields(e.end), e.all_day, e.location,
                          e.calendar] for e in events]  # fmt: skip
                assert got["events"] == found, (text, now)
                rows = [[key, r.value, r.unit, r.label]
                        for key, r in calendar.calendar_readings(events, now).items()]  # fmt: skip
                for row in rows:
                    if isinstance(row[1], float):
                        row[1] = {"float": repr(row[1])}
                assert got["readings"] == rows, (text, now)
                seen["events"] += len(events)
                seen["repeated"] += len(events) > 8
                seen["whole days"] += any(e.all_day for e in events)
                seen["going"] += any(row[0].endswith(".now") for row in rows)
                seen["moved"] += "RECURRENCE-ID" in text and len(events) > 2
        finally:
            i18n.set_language("en")
        assert all(count > 20 for count in seen.values()), seen
    finally:
        monkeypatch.undo()
        if sys.platform != "win32":
            time.tzset()


class _SharedBytes:
    """Bytes as a shared memory that Windows' mapping gives: nothing beyond its end."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self, offset: int, size: int) -> bytes:
        if offset < 0 or offset + size > len(self.data):
            raise ValueError("beyond the memory")
        return self.data[offset : offset + size]

    def close(self) -> None:
        pass


def test_rtss_and_hwinfo_memories_are_read_as_python_reads_them() -> None:
    """sensors/rtss.py and sensors/hwinfo.py: the programs RTSS's memory names look after
    look and the frame rates their samples come to; HWiNFO's sensors and readings (names
    of both kinds, version 1 and 2, memory and rates in their units) as
    LibreHardwareMonitor's keys; memories that are another program's, off, or cut short."""
    from libre_panel.sensors import hwinfo, rtss

    rng = random.Random(0x4755)
    b64 = base64.b64encode

    def rtss_memory(
        programs: list[Any], size: int = 300, signature: int = rtss.SIGNATURE,
        version: int = 0x00020007,
    ) -> bytes:  # fmt: skip
        data = struct.pack("<5I", signature, version, size, 32, len(programs)) + b"\0" * 12
        for pid, path, time0, time1, frames, frame_us in programs:
            entry = struct.pack("<I260s5I", pid, path, 0, time0, time1, frames, frame_us)
            data += entry.ljust(size, b"\0")
        return data

    paths = [b"C:\\Games\\Witcher 3\\bin\\witcher3.exe", b"D:/Steam/cs2.exe", b"game.exe",
             b"C:\\Jeux\\caf\xe9.exe", b"", b"C:\\dir\\", b"dwm.exe",
             b"C:\\x\\Elden Ring.exe"]  # fmt: skip
    runs = []
    for _ in range(150):
        programs = [[rng.choice([0, 100, 200, 300, 4000]), rng.choice(paths),
                     rng.randrange(10**6), rng.randrange(10**6), rng.randrange(200),
                     rng.choice([0, 6944, 16667, 33333, 8333])]
                    for _ in range(rng.randrange(0, 5))]  # fmt: skip
        now = rng.uniform(0, 100)
        looks = []
        for _ in range(rng.randrange(1, 60)):
            now += rng.choice([0.1, 0.1, 0.1, 0.5, 3.0])
            for program in programs:
                if rng.random() < 0.7:  # (its period went on, or a frame came)
                    program[3] += 1000
                    program[5] = rng.choice([program[5], 6944, 16667, 0, 20000])
            kind = rng.random()
            memory = rtss_memory([tuple(p) for p in programs])
            if kind < 0.03:
                memory = rtss_memory([tuple(p) for p in programs], signature=0x1234)
            elif kind < 0.06:
                memory = rtss_memory([tuple(p) for p in programs], version=0x00010005)
            elif kind < 0.09:
                memory = memory[: rng.randrange(len(memory))]
            elif kind < 0.12:
                memory = rtss_memory([tuple(p) for p in programs], size=200)
            looks.append((memory, now))
        runs.append(looks)

    names = ["CPU [#0]: AMD Ryzen 9 7950X: Enhanced", "CPU [#0]: AMD Ryzen 9 7950X",
             "GPU [#0]: NVIDIA GeForce RTX 4090", "GPU [#1]: Intel UHD Graphics: extra: more",
             "ASUS ROG STRIX X670E (Nuvoton NCT6798D)", "S.M.A.R.T.: Samsung SSD 990 PRO",
             "Drive: WDC WD40", "System", "Memory Timings", "Network: Intel I225-V",
             "Battery: BAT0",
             "Windows Hardware Errors (WHEA)", "", "CPU [#0]:", "DIMM [#1]: G.Skill", "drive:",
             "GPU [#٣]: Arc A770", "Gr\xf6\xdfe \x80 \x81"]  # fmt: skip
    labels = ["CPU (Tctl/Tdie)", "CPU Package", "Total CPU Usage", "CPU Package Power", "CPU PPT",
              "Core Clocks (avg)", "GPU Temperature", "GPU Hot Spot Temperature", "GPU Core Load",
              "GPU Memory Usage", "Total Board Power (TBP)", "GPU Power", "GPU Clock", "GPU Fan1",
              "GPU Memory Allocated", "Chassis1", "CPU", "Core 0 Clock", "Read Rate", "Vcore", "",
              "Drive Temperature", "T_Sensor", "Pump Fan"]  # fmt: skip
    units = ["°C", "MB", "GB", "KB/s", "mb/s", "GB/s", "RPM", "%", "W", "MHz", "", " mb ", "V", "A"]

    def name_bytes(text: str, utf: bool = False) -> bytes:
        return text.encode("utf-8" if utf else "latin-1", "replace")

    def hwinfo_memory() -> bytes:
        version = rng.choice([1, 2, 2])
        s_size = rng.choice([264, 264 + 128, 300])
        r_size = rng.choice([316, 316 + 144, 400])
        sensors = b""
        count = rng.randrange(1, 7)
        for n in range(count):
            original = rng.choice(names[:3] if rng.random() < 0.5 else names)
            own = rng.choice([*names, "", "", "", ""])
            record = struct.pack("<II128s128s", n, 0, name_bytes(original), name_bytes(own))
            if s_size >= 264 + 128:
                utf = rng.choice(["", "", "CPU [#0]: Größe", own])
                record += struct.pack("<128s", name_bytes(utf, utf=True))
            sensors += record.ljust(s_size, b"\0")
        readings = b""
        r_count = rng.randrange(0, 30)
        for n in range(r_count):
            value = rng.choice([rng.uniform(0, 100), 42.0, 0.0, float("nan"), float("inf"), -5.5,
                                3500.0, 1e12])  # fmt: skip
            kind_no = rng.choice([1, 1, 2, 3, 3, 4, 5, 6, 7, 8, 8, 8, 0, 9])
            label, own = rng.choice(labels), rng.choice([*labels, "", ""])
            if rng.random() < 0.4:  # (what LibreHardwareMonitor's keys are made from)
                kind_no, label = rng.choice([(1, "CPU (Tctl/Tdie)"), (1, "GPU Temperature"),
                                             (7, "GPU Core Load"), (7, "Total CPU Usage"),
                                             (5, "GPU Power"), (3, "GPU Fan1")])  # fmt: skip
                own = ""
            record = struct.pack("<III128s128s16s4d", kind_no,
                                 rng.choice([*range(count), count, 99]), n, name_bytes(label),
                                 name_bytes(own), name_bytes(rng.choice(units)), value, 0.0,
                                 100.0, 50.0)  # fmt: skip
            if r_size >= 316 + 144:
                utf_label = name_bytes(rng.choice(["", "", "Lüfter"]), True)
                utf_unit = name_bytes(rng.choice(["", "", "°C", "MB"]), True)
                record += struct.pack("<128s16s", utf_label, utf_unit)
            readings += record.ljust(r_size, b"\0")
        signature, s_at = hwinfo.SIGNATURE, 44 + rng.choice([0, 0, 20])
        r_at = s_at + len(sensors)
        s_said, r_said = count, r_count
        kind = rng.random()
        if kind < 0.04:
            signature = rng.choice([hwinfo.STOPPED, 0])
        elif kind < 0.08:
            s_said = rng.choice([0, 20000])
        elif kind < 0.11:
            r_said = rng.choice([20000, r_count + 3])
        elif kind < 0.14:
            s_size = 100
        elif kind < 0.17:
            r_at += 10**6
        header = struct.pack("<IIIqIIIIII", signature, version, 5, rng.randrange(10**9), s_at,
                             s_size, s_said, r_at, r_size, r_said)  # fmt: skip
        memory = header.ljust(s_at, b"\0") + sensors + readings
        return memory[: rng.randrange(len(memory))] if kind > 0.96 else memory

    memories = [hwinfo_memory() for _ in range(800)]
    [ours] = run([{"op": "shared_memory",
                   "rtss": [[[b64(memory).decode(), now] for memory, now in looks]
                            for looks in runs],
                   "hwinfo": [b64(memory).decode() for memory in memories]}])  # fmt: skip

    seen = {"error": 0, "programs": 0, "frames": 0, "none": 0, "sensors": 0, "keys": 0}
    for looks, (found, readings) in zip(runs, ours["rtss"], strict=True):
        provider = rtss.RTSSProvider(opener=lambda _name: None, clock=lambda: 0.0)
        for (memory, now), got in zip(looks, found, strict=True):
            try:
                wanted = [[e.pid, e.app, e.time0, e.time1, e.frames, e.frame_us]
                          for e in rtss.entries(_SharedBytes(memory))]  # fmt: skip
                assert got == wanted, memory
                seen["programs"] += len(wanted)
            except (ValueError, struct.error):
                assert "error" in got, memory
                seen["error"] += 1
            try:
                provider.poll(_SharedBytes(memory), now)
            except (ValueError, struct.error):
                pass
        wanted = _readings(provider._counter.readings(looks[-1][1]))
        assert readings == wanted, looks
        seen["frames"] += any(row[0] == "game.fps" for row in wanted)
    for memory, got in zip(memories, ours["hwinfo"], strict=True):
        try:
            sensors = hwinfo.sensors_of(_SharedBytes(memory))
        except (ValueError, struct.error):
            assert isinstance(got, dict) and "error" in got, memory
            seen["error"] += 1
            continue
        if sensors is None:
            assert got is None, memory
            seen["none"] += 1
            continue
        assert got is not None and "error" not in got, memory
        assert got[0] == [[s.sensor_id, s.hardware_id, s.hardware_name, s.sensor_type, s.label,
                           _hex(s.value), s.unit] for s in sensors], memory  # fmt: skip
        readings = hwinfo.readings_of(sensors)
        assert got[1] == _readings(readings), memory
        seen["sensors"] += len(sensors)
        seen["keys"] += any(key in readings for key in ("cpu.temp", "gpu.temp", "gpu.load"))
    assert all(count > 30 for count in seen.values()), list(seen.items())
