"""rust/sensors against the Python app (docs/RUST.md): LibreHardwareMonitor's sensors as
readings (sensors/librehardwaremonitor.py), the names of the hardware (sensors/hwnames.py) and
what the sensor service and Libre Panel say to each other (sensorservice/protocol.py), as the
Rust app has them."""

from __future__ import annotations

import base64
import json
import random
import struct
import sys
from typing import Any

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
