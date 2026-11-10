"""rust/sensors against the Python app (docs/RUST.md): LibreHardwareMonitor's sensors as
readings (sensors/librehardwaremonitor.py), the names of the hardware (sensors/hwnames.py) and
what the sensor service and Libre Panel say to each other (sensorservice/protocol.py), as the
Rust app has them."""

from __future__ import annotations

import base64
import json
import random
import struct
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
