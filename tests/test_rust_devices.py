"""rust/devices against devices/ (docs/RUST.md, R6): pictures saved as JPEGs as Pillow saves
them and dimmed as devices/base.py dims them, and the Hongtai family as devices/hongtai.py
drives it, to the byte: its frames, its pictures, the answer its panels give, and whole
sessions (opened, pictures shown, the brightness set, closed) on a panel the tests script.
The MAJESTY 9.16" (issue #40), which the Python app has no driver for, against what its owner
found on the panel: the same, set up first, its brightness in its pictures."""

from __future__ import annotations

import base64
import dataclasses
import io
import json
import math
import random
import struct
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from rust_check import image, noise, picture, run

from libre_panel.config import DeviceConfig
from libre_panel.devices import base, hongtai
from libre_panel.devices.models import get_model
from libre_panel.devices.serial_link import FoundPort


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _bytes(text: str) -> bytes:
    return base64.b64decode(text)


def _smooth(rng: random.Random, mode: str, size: tuple[int, int]) -> Image.Image:
    """A picture as a panel's frame mostly is: gradients and flat colour, a few sharp edges."""
    w, h = size
    out = Image.linear_gradient("L").resize(size).convert(mode)
    if mode != "L":
        tint = Image.new(mode, size, tuple(rng.randrange(256) for _ in mode))
        out = Image.blend(out, tint, rng.random())
    for _ in range(rng.randrange(4)):
        box = (
            sorted((rng.randrange(w), rng.randrange(w))),
            sorted((rng.randrange(h), rng.randrange(h))),
        )
        colour = tuple(rng.randrange(256) for _ in mode) if mode != "L" else rng.randrange(256)
        out.paste(colour, (box[0][0], box[1][0], box[0][1] + 1, box[1][1] + 1))
    return out


def _pictures(rng: random.Random, modes: tuple[str, ...], sizes: list[tuple[int, int]]) -> list:
    made = []
    for size in sizes:
        for mode in modes:
            made.append(noise(rng, mode, size) if rng.random() < 0.4 else _smooth(rng, mode, size))
    return made


def test_jpegs_as_pillow_saves_them() -> None:
    rng = random.Random(0x1E6)
    sizes = [(1, 1), (7, 3), (8, 8), (16, 16), (17, 9), (64, 48), (161, 97), (480, 960),
             (462, 1920), (3, 500)]  # fmt: skip
    cases, expected = [], []
    for im in _pictures(rng, ("RGB", "L"), sizes):
        for quality in rng.sample([-1, 0, 1, 10, 30, 50, 75, 85, 90, 95, 100, 120, -7], 3):
            buffer = io.BytesIO()
            im.save(buffer, format="JPEG", quality=quality)
            cases.append({"op": "jpeg_save", "image": image(im), "quality": quality})
            expected.append(buffer.getvalue())
            # (4:4:4, as the TURZX panels' stills are saved)
            buffer = io.BytesIO()
            im.save(buffer, format="JPEG", quality=quality, subsampling=0)
            cases.append({"op": "jpeg_save", "image": image(im), "quality": quality,
                          "subsampling": 0})  # fmt: skip
            expected.append(buffer.getvalue())
    ours = run(cases)
    for n, (got, want) in enumerate(zip(ours, expected, strict=True)):
        assert "data" in got, (n, got)
        assert _bytes(got["data"]) == want, (n, cases[n]["quality"], cases[n]["image"]["size"])
    for mode in ("RGBA", "RGBa"):  # (what Pillow does not write as a JPEG)
        [refused] = run(
            [{"op": "jpeg_save", "image": image(Image.new(mode, (4, 4))), "quality": 90}]
        )
        with pytest.raises(OSError, match=refused["error"]):
            Image.new(mode, (4, 4)).save(io.BytesIO(), format="JPEG", quality=90)


def test_a_picture_dimmed_as_devices_base_dims_it() -> None:
    rng = random.Random(0xD1)
    cases, expected = [], []
    for im in _pictures(rng, ("RGB", "L", "RGBA"), [(1, 1), (13, 7), (64, 48)]):
        for percent in (-20, 0, 1, 33, 50, 67, 99, 100, 150, rng.randrange(100)):
            cases.append({"op": "dimmed", "image": image(im), "percent": percent})
            expected.append(base.dimmed(im, percent))
    for n, (got, want) in enumerate(zip(run(cases), expected, strict=True)):
        assert picture(got["image"]).tobytes() == want.tobytes(), n


def test_hongtai_frames_and_pictures_as_hongtai_py_makes_them() -> None:
    rng = random.Random(0xF7)
    cases: list[dict[str, Any]] = []
    for command in range(256):
        data = rng.randbytes(rng.choice([0, 1, 2, 5, 17]))
        cases.append({"op": "hongtai_frame", "command": command, "data": _b64(data)})
    for length in (65528, 65529):  # (the longest a frame's length holds, and one byte more)
        cases.append({"op": "hongtai_frame", "command": 0x11, "data": _b64(bytes(length))})
    jpegs = [b"", b"\xff", rng.randbytes(1000), bytes(255) * 300, rng.randbytes(70000)]
    cases += [{"op": "hongtai_picture", "jpeg": _b64(j)} for j in jpegs]
    for case, got in zip(cases, run(cases), strict=True):
        if case["op"] == "hongtai_picture":
            assert _bytes(got["data"]) == hongtai.picture(_bytes(case["jpeg"]))
            continue
        try:
            want = hongtai.frame(case["command"], _bytes(case["data"]))
        except (ValueError, struct.error) as error:
            assert got.get("error") == str(error), case["command"]
        else:
            assert _bytes(got["data"]) == want, case["command"]


def _exactly_80_kb() -> Image.Image:
    """A picture whose JPEG at quality 90 is 80 KB to the byte (Pillow 13.0's libjpeg-turbo):
    the firmware takes it as it is, at that quality."""
    rng = random.Random(0x80)
    im = Image.linear_gradient("L").resize((462, 1920)).convert("RGB")
    px = im.load()
    for _ in range(3007):
        px[rng.randrange(462), rng.randrange(1920)] = (
            rng.randrange(256),
            rng.randrange(256),
            rng.randrange(256),
        )
    im.putpixel((10, 1000), (99, 99, 99))
    return im


def test_hongtai_jpegs_keep_within_what_the_firmware_takes() -> None:
    # (a busy picture of the panel's size goes down the qualities until it is 80 KB at most)
    rng = random.Random(0x80)
    pictures = _pictures(rng, ("RGB",), [(480, 960), (462, 1920), (960, 480), (40, 30)])
    pictures += [noise(rng, "RGB", (480, 960)), noise(rng, "RGB", (462, 1920))]
    pictures.append(_exactly_80_kb())
    ours = run([{"op": "hongtai_jpeg", "image": image(p)} for p in pictures])
    sizes = []
    for p, got in zip(pictures, ours, strict=True):
        want = hongtai.jpeg(p)
        assert _bytes(got["data"]) == want
        sizes.append(len(want))
    assert max(sizes) > hongtai.MAX_JPEG  # (noise beyond it even at the lowest quality)
    assert min(sizes) < hongtai.MAX_JPEG
    assert hongtai.MAX_JPEG in sizes  # (80 KB to the byte: still taken)


class _Panel:
    """A link to a panel the test scripts: what it answers a frame written to it (the whole of
    a write), and what it was sent; read and drain as the serial link's."""

    def __init__(self, replies: list[tuple[bytes, bytes]] = (), pending: bytes = b"") -> None:
        self.out = bytearray()
        self.pending = bytearray(pending)
        self.replies = list(replies)
        self.written = 0

    def write(self, data: bytes) -> None:
        data = bytes(data)
        self.out += data
        self.written += len(data)
        for asked, reply in self.replies:
            if data == asked:
                self.pending += reply

    def read(self, size: int) -> bytes:
        chunk = bytes(self.pending[:size])
        del self.pending[:size]
        return chunk

    def drain_input(self) -> None:
        self.pending.clear()

    def close(self) -> None:
        pass


def _answer_frame(data: bytes, command: int = 6) -> bytes:
    head = b"\x55\xaa" + struct.pack("<H", len(data) + 7) + bytes((command,)) + data
    return head + struct.pack("<H", sum(head) & 0xFFFF)


SIZES: list[Any] = [480, 960, 462, 1920, "462", " 1920 ", "1_920", 462.9, 0, -5, True, False,
                    None, "x", "", math.nan, math.inf, [1], {"a": 1}, [], "٤٦٢", -462.7,
                    -0.5]  # fmt: skip


def _info(rng: random.Random) -> dict[str, Any]:
    info: dict[str, Any] = {"status": 200}
    if rng.random() < 0.9:
        info["model"] = rng.choice(["TXW818", "MAJESTY", "x" * 60, 42, None, ["a"], "Grüße"])
    for key, natural in (("width", [480, 462]), ("height", [960, 1920])):
        if rng.random() < 0.95:
            info[key] = rng.choice(natural) if rng.random() < 0.6 else rng.choice(SIZES)
    return info


def _stream(rng: random.Random) -> bytes:
    """What a panel may answer 06: its frame with JSON, often; garbage, a frame cut short, other
    JSON, no JSON, bytes that are no UTF-8, NULs around it."""
    pick = rng.random()
    others = ["[1, 2]", "42", "not json", "{", '{"width": 480,}', '"text"', "null", "{}", "NaN"]
    text = json.dumps(_info(rng)) if rng.random() < 0.8 else rng.choice(others)
    data = text.encode()
    if rng.random() < 0.2:
        data = b"\0\0" + data + b"\0"
    if rng.random() < 0.1:
        data = data.replace(b"TXW818", b"T\xffX\xc3W")  # (no UTF-8: replaced as Python does)
    framed = _answer_frame(data)
    if pick < 0.1:
        return rng.randbytes(rng.randrange(80))
    if pick < 0.2:
        return framed[: rng.randrange(len(framed))]
    if pick < 0.3:
        return rng.randbytes(rng.randrange(70)) + framed  # (to its start, 64 bytes at most)
    if pick < 0.35:
        return b"\x55\x00" + framed
    return framed


def test_hongtai_answers_as_hongtai_py_reads_them() -> None:
    rng = random.Random(0xA5)
    streams = [_stream(rng) for _ in range(400)]
    streams += [_answer_frame(b"[" * 2000 + b"]" * 2000), _answer_frame(b'{"a": ' * 3000)]
    ours = run([{"op": "hongtai_answer", "stream": _b64(s)} for s in streams])
    for n, (stream, got) in enumerate(zip(streams, ours, strict=True)):
        try:
            want = hongtai.HongtaiDisplay._answer(SimpleNamespace(link=_Panel(pending=stream)))
        except RecursionError:
            assert "error" in got, n
            continue
        assert got.get("info") == repr(want), (n, stream[:80])


def _session(rng: random.Random, majesty: bool = False) -> dict[str, Any]:
    model = rng.choice([None, "hongtai-480x960", "hongtai-1920x462"]) if not majesty else None
    steps: list[dict[str, Any]] = []
    for _ in range(rng.randrange(5)):
        if rng.random() < 0.4:
            steps.append({"brightness": rng.choice([-20, 0, 1, 40, 73, 100, 140])})
        else:
            size = rng.choice([(480, 960), (960, 480), (462, 1920), (1920, 462), (320, 240),
                               (100, 300), (1, 1)])  # fmt: skip
            mode = rng.choice(["RGB", "RGB", "RGBA", "L"])
            made = noise(rng, mode, size) if rng.random() < 0.3 else _smooth(rng, mode, size)
            steps.append({"show": made})
    answer = _stream(rng) if rng.random() < 0.3 else _answer_frame(json.dumps(_info(rng)).encode())
    stale = rng.randbytes(100) if rng.random() < 0.3 else b""  # (what came before 06: drained)
    return {"model": model, "answer": answer, "steps": steps, "stale": stale}


def _python_session(case: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """devices/hongtai.py's session on the scripted panel: what it was sent."""
    monkeypatch.setattr(hongtai.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(hongtai, "LIVE_EVERY_S", 3600.0)  # (no 11 of the keeper's in a case)
    model = get_model(case["model"]) if case["model"] else None
    display = hongtai.HongtaiDisplay(DeviceConfig(driver="turzx"), model, configured=False)
    panel = _Panel(
        [(hongtai.RESET, case["stale"]), (hongtai.frame(hongtai.CMD_INFO), case["answer"])]
    )
    display.link = panel
    sentinel = dataclasses.replace(get_model("hongtai-480x960"), id="sentinel")  # (none named)
    try:
        found = display.identify(FoundPort("COM5", sentinel, None))
    except Exception as error:  # noqa: BLE001 (what Python raises, as it is)
        return {"error": str(error), "written": bytes(panel.out)}
    display._keeper.start()
    failed = None
    for step in case["steps"]:
        try:
            if "brightness" in step:
                display.set_brightness(step["brightness"])
            else:
                display.show(step["show"])
        except Exception as error:  # noqa: BLE001
            failed = str(error)
            break
    counted = display.counters.bytes
    display.close()
    named = found.id if found is not sentinel else None
    return {"written": bytes(panel.out), "native": list(display.native), "named": named,
            "error": failed, "bytes": counted}  # fmt: skip


def _rust_case(case: dict[str, Any], kind: str = "family") -> dict[str, Any]:
    native = None
    if case["model"]:
        found = get_model(case["model"])
        native = [found.native_width, found.native_height]
    steps = [{"brightness": s["brightness"]} if "brightness" in s else {"show": image(s["show"])}
             for s in case["steps"]]  # fmt: skip
    return {"op": "hongtai_session", "kind": kind, "native": native, "port": "COM5",
            "answer": _b64(case["answer"]), "setup_answer": case.get("setup_answer"),
            "stale": _b64(case.get("stale", b"")),
            "setup_wait_ms": case.get("setup_wait_ms", 50), "steps": steps}  # fmt: skip


def test_hongtai_sessions_as_hongtai_py_drives_them(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = random.Random(0x5E5)
    cases = [_session(rng) for _ in range(60)]
    ours = run([_rust_case(c) for c in cases])
    opened = 0
    for n, (case, got) in enumerate(zip(cases, ours, strict=True)):
        want = _python_session(case, monkeypatch)
        assert _bytes(got["written"]) == want["written"], (n, got.get("error"), want.get("error"))
        if "native" not in want:  # (it did not open: the same error)
            assert got["error"] == want["error"], n
            continue
        opened += 1
        assert got["native"] == want["native"] and got["named"] == want["named"], n
        assert got["error"] == want["error"] and got["bytes"] == want["bytes"], n
    assert opened > 30


MAJESTY_SETUP = b"\x55\xaa\x09\x00\x15\x00\x03\x20\x01"  # (15 with 00 03 and its sum: 0x120)


def _majesty_expected(case: dict[str, Any]) -> bytes:
    """What the MAJESTY is sent, as its owner found it on the panel (issue #40): the family's
    opening, its setting up (15 with 00 03), the backlight at 95 and the live picture; then each
    picture as the family's, dimmed first, and the one on show again where the brightness
    changes; 21 at the end."""
    out = bytearray(hongtai.RESET + hongtai.frame(hongtai.CMD_INFO) + MAJESTY_SETUP)
    out += hongtai.frame(hongtai.CMD_LIGHT, bytes((95,))) + hongtai.frame(hongtai.CMD_LIVE)
    native, brightness, last = (462, 1920), 100, None
    for step in case["steps"]:
        if "brightness" in step:
            brightness = max(0, min(100, step["brightness"]))
            if last is None:
                continue
        else:
            shown = step["show"]
            if (shown.width > shown.height) != (native[0] > native[1]):
                shown = shown.transpose(Image.Transpose.ROTATE_270)
            if shown.size != native:
                shown = shown.resize(native, Image.Resampling.LANCZOS)
            last = shown.convert("RGB")
        out += hongtai.frame(hongtai.CMD_LIVE) + hongtai.picture(
            hongtai.jpeg(base.dimmed(last, brightness))
        )
    return bytes(out + hongtai.frame(hongtai.CMD_CLOSE))


def test_the_majesty_is_set_up_and_dims_its_pictures_as_its_owner_found() -> None:
    rng = random.Random(0x7792)
    assert sum(MAJESTY_SETUP[:-2]) == 0x120 and MAJESTY_SETUP[2] == len(MAJESTY_SETUP)
    cases = []
    for n in range(16):
        case = _session(rng, majesty=True)
        said = {"status": 200, "model": "MAJESTY", "width": 462, "height": 1920}
        case["answer"] = _answer_frame(json.dumps(said).encode())
        if n % 2:  # (its answer to the setting up; without one it goes on after the wait)
            case["setup_answer"] = _b64(_answer_frame(b"\x00", command=0x15))
        else:
            case["setup_wait_ms"] = 30
        cases.append(case)
    ours = run([_rust_case(c, "majesty") for c in cases])
    for n, (case, got) in enumerate(zip(cases, ours, strict=True)):
        assert got["error"] is None, (n, got["error"])
        assert got["native"] == [462, 1920] and got["named"] is None, n
        assert got["set_up"] == (0x15 if n % 2 else None), n
        assert _bytes(got["written"]) == _majesty_expected(case), n


def test_the_family_is_never_sent_the_majesty_s_setting_up(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = random.Random(0x15)
    cases = [_session(rng) for _ in range(10)]
    for got in run([_rust_case(c) for c in cases]):
        assert MAJESTY_SETUP not in _bytes(got["written"])


@pytest.mark.parametrize("kind", ["family", "majesty"])
def test_the_live_picture_is_kept_while_nothing_changes(kind: str) -> None:
    # (11 alone again while no picture goes out, from the keeper's thread; no picture again)
    rng = random.Random(0x11)
    said = {"status": 200, "model": "TXW818", "width": 480, "height": 960}
    case = {"model": None, "answer": _answer_frame(json.dumps(said).encode()), "stale": b"",
            "steps": [{"show": _smooth(rng, "RGB", (960, 480))}]}  # fmt: skip
    asked = _rust_case(case, kind)
    asked["live_every_ms"] = 40
    asked["steps"].append({"wait_ms": 400})
    [got] = run([asked])
    written = _bytes(got["written"])
    live, close = hongtai.frame(hongtai.CMD_LIVE), hongtai.frame(hongtai.CMD_CLOSE)
    after = written[written.rindex(hongtai.END) + len(hongtai.END) :]  # (after the one picture)
    kept = len(after[: -len(close)]) // len(live)
    assert after == live * kept + close  # (11 alone, again and again; no picture again)
    assert kept >= 3  # (some 10 in the time; a slow machine fewer)


# -- TURZX USB panels (devices/turzx_usb.py, devices/h264.py's bit rate) --------------------


def test_the_bit_rate_follows_the_panel_as_ratecontrol_does() -> None:
    from libre_panel.devices import h264

    rng = random.Random(0xB17)
    cases, wanted = [], []
    for start in (1.0, 2.0, 2.4, 3.0, 5.7, 32.0):
        for remembered in (0.0, 1.5, 4.0, 100.0):
            control = h264.RateControl(start, remembered)
            steps = [[None, control.rate, control.floored, control.changes]]
            events, now, waits, blocks, slow = [], 100.0, 0, 0, 0
            trouble = rng.choice([0.02, 0.2, 0.6])  # (how often the panel lags)
            for _ in range(400):
                now += rng.choice([0.04, 0.5, 1.0, 3.0, 7.0, 11.0, 61.0, 700.0])
                more = rng.randrange(0, 300)
                blocks += more
                if rng.random() < trouble:
                    waits += rng.randrange(0, 9)
                    slow += rng.randrange(0, more + 1)
                counted = SimpleNamespace(waits=waits, blocks=blocks, slow_blocks=slow)
                moved = control.judge(counted, now)
                steps.append([moved, control.rate, control.floored, control.changes])
                events.append([waits, blocks, slow, now])
            cases.append({"op": "rate_control", "start": start, "remembered": remembered,
                          "events": events})  # fmt: skip
            wanted.append(steps)
    sizes = [(25, 1920, 480), (50, 1920, 480), (60, 1920, 720), (1, 320, 240), (30, 480, 1920),
             (33, 1919, 487), (50, 3840, 1100)]  # fmt: skip
    texts = ["auto", "", " 2M ", "1500k", "1500K", "2000000", "1.5M", "0.0004M", "x", "2m",
             "1e3k", "٣M", " ", "M", "12.3456k"]  # fmt: skip
    cases.append({"op": "rate_control", "start": 1.0, "remembered": 0, "auto": sizes,
                  "mbit": texts})  # fmt: skip
    ours = run(cases)
    for case, got, want in zip(cases, ours, wanted, strict=False):
        assert got["steps"] == want, (case["start"], case["remembered"])
    moved = [step[0] for steps in wanted for step in steps if step[0] is not None]
    assert len(moved) > 100 and any(steps[-1][2] for steps in wanted)  # (steps, and a floor)
    auto = [h264.auto_rate(SimpleNamespace(fps=fps), (w, h)) for fps, w, h in sizes]
    assert ours[-1]["auto"] == [[rate, h264.rate_text(rate)] for rate in auto]
    assert ours[-1]["mbit"] == [h264.mbit(text) for text in texts]


def test_a_panel_is_picked_among_those_on_the_bus_as_python_picks() -> None:
    from libre_panel.devices import turzx_usb

    class Core:
        def __init__(self, devices: list[Any]) -> None:
            self.devices = devices

        def find(self, find_all: bool, idVendor: int, idProduct: int, backend: Any) -> list[Any]:
            assert find_all and idVendor == turzx_usb.VENDOR_ID
            return [d for d in self.devices if d.pid == idProduct]

    rng = random.Random(0x9C)
    pids = list(turzx_usb.USB_PIDS)
    names = [model.id for model in turzx_usb.USB_PIDS.values()] + ["nope", "turing-3.5"]
    cases, wanted = [], []
    for _ in range(600):
        found = [SimpleNamespace(pid=rng.choice(pids[:5]), bus=rng.randrange(1, 4), address=n)
                 for n in range(rng.randrange(0, 5))]  # fmt: skip
        beside = tuple(rng.choice(names) for _ in range(rng.randrange(0, 3)))
        pid = rng.choice([None, None, *pids[:5]])
        skip = rng.choice([0, 0, 0, 1, 2])
        picked = turzx_usb._pick(pid, beside, Core(found), None, skip=skip)
        wanted.append(None if picked is None else found.index(picked[0]))
        cases.append({"op": "turzx_pick", "pid": pid, "beside": list(beside), "skip": skip,
                      "devices": [[d.pid, d.bus, d.address] for d in found]})  # fmt: skip
    assert run(cases) == wanted
    assert wanted.count(None) > 50 and len(set(wanted)) > 3


def test_turzx_pictures_as_the_python_driver_makes_them() -> None:
    from libre_panel.devices import turzx_usb
    from libre_panel.devices.models import find_model

    rng = random.Random(0x7C)
    frames = [_smooth(rng, "RGB", size) for size in
              [(1920, 480), (480, 1920), (1920, 720), (320, 240), (65, 33)]]  # fmt: skip
    frames += [noise(rng, "RGB", (1920, 480)), noise(rng, "RGB", (1920, 720))]
    models = ["turing-9.2-usb", "turing-5.2-usb", "turing-12.3-usb", ""]
    cases = [{"op": "turzx_pictures", "image": image(frame), "model": model, "still": still}
             for frame in frames for model in models
             for still in rng.sample(["auto", "png", "jpeg"], 2)]  # fmt: skip
    busy = 0
    for case, got in zip(cases, run(cases), strict=True):
        frame, model = picture(case["image"]), find_model(case["model"])
        native = turzx_usb.to_native(frame)
        assert picture(got["native"]).tobytes() == native.tobytes()
        assert got["still"] == turzx_usb.still_format(case["still"], model)
        assert got["jpeg_width"] == turzx_usb.jpeg_width(model, native.width)
        # (the JPEG to the byte; the PNG's pixels, and no larger than Pillow's by much)
        try:
            jpeg = turzx_usb.encode_jpeg(native, model=model)
        except base.FrameError:
            assert got["jpeg"]["error"].startswith("frame too detailed for the panel (")
        else:
            assert _bytes(got["jpeg"]["data"]) == jpeg
        try:
            png = turzx_usb.encode_frame(native)
        except base.FrameError:
            assert got["png"]["error"].startswith("frame too detailed for the panel (")
            busy += 1
        else:
            ours = _bytes(got["png"]["data"])
            with Image.open(io.BytesIO(ours)) as made:
                assert made.mode == "RGBA" and made.tobytes() == native.convert("RGBA").tobytes()
            assert len(ours) <= turzx_usb.MAX_PAYLOAD
            assert len(ours) <= len(png) * 1.1 + 2048, (len(ours), len(png))
        with Image.open(io.BytesIO(_bytes(got["layer"]["data"]))) as layer:
            assert layer.mode == "RGBA" and layer.size == native.size
            assert layer.getextrema() == ((0, 0),) * 4
    assert busy  # (noise is too detailed for a PNG of a megabyte)


def test_the_parts_of_a_frame_that_changed_cover_what_differs() -> None:
    from PIL import ImageChops

    from libre_panel.devices.yuv import block_of

    rng = random.Random(0xD1F)
    cases = []
    for size in [(1920, 480), (480, 1920), (320, 240), (200, 104), (65, 33)]:
        for changes in (0, 1, 3, 12):
            before = _smooth(rng, "RGB", size)
            now = before.copy()
            for _ in range(changes):
                w, h = rng.randrange(1, 90), rng.randrange(1, 40)
                x, y = rng.randrange(0, size[0]), rng.randrange(0, size[1])
                now.paste(tuple(rng.randrange(256) for _ in range(3)), (x, y, x + w, y + h))
            cases.append({"op": "turzx_changed", "before": image(before), "now": image(now)})
    for case, got in zip(cases, run(cases), strict=True):
        before, now = picture(case["before"]), picture(case["now"])
        block = block_of(now.size) or 1
        assert got["block"] == block
        differs = ImageChops.difference(before, now).convert("L").point(lambda v: 255 if v else 0)
        covered = Image.new("L", now.size, 0)
        for x0, y0, x1, y1 in got["boxes"]:
            assert 0 <= x0 < x1 <= now.width and 0 <= y0 < y1 <= now.height
            assert x0 % block == 0 and y0 % block == 0
            assert x1 % block == 0 or x1 == now.width
            assert y1 % block == 0 or y1 == now.height
            assert differs.crop((x0, y0, x1, y1)).getbbox() is not None  # (none for nothing)
            # (and no wider than the blocks that changed in its rows)
            left, _, right, _ = differs.crop((0, y0, now.width, y1)).getbbox()
            assert x0 == left - left % block and x1 == min(now.width, right + (-right) % block)
            covered.paste(255, (x0, y0, x1, y1))
        assert ImageChops.subtract(differs, covered).getbbox() is None  # (all that differs)
        assert bool(got["boxes"]) == (differs.getbbox() is not None)
