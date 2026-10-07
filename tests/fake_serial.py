"""Simulated serial panels for tests: they read the byte stream as the device
does, keep a framebuffer, and answer where the device answers."""

from __future__ import annotations

import struct
from types import SimpleNamespace

from PIL import Image


def port_info(device="/dev/ttyACM0", vid=0x1A86, pid=0x5722, serial_number=None):
    """What pyserial's list_ports reports for a port."""
    return SimpleNamespace(device=device, vid=vid, pid=pid, serial_number=serial_number)


def from_rgb565(data: bytes, size: tuple[int, int], byteorder: str = "little") -> Image.Image:
    values = struct.unpack(("<" if byteorder == "little" else ">") + f"{len(data) // 2}H", data)
    rgb = bytearray()
    for v in values:
        rgb += bytes(((v >> 11) << 3, ((v >> 5) & 63) << 2, (v & 31) << 3))
    return Image.frombytes("RGB", size, bytes(rgb))


def quantized(image: Image.Image) -> Image.Image:
    """``image`` as a panel with 16-bit colour (RGB565) shows it."""
    r, g, b = image.convert("RGB").split()
    five, six = (lambda v: v & 0xF8), (lambda v: v & 0xFC)
    return Image.merge("RGB", (r.point(five), g.point(six), b.point(five)))


class FakeSerial:
    """A port: ``write`` is what the PC sends, ``read`` what the panel answered."""

    def __init__(self) -> None:
        self.answers = bytearray()
        self.closed = False
        self.unplugged = False

    def read(self, size: int) -> bytes:
        self._check()
        data, self.answers = bytes(self.answers[:size]), self.answers[size:]
        return data

    def reset_input_buffer(self) -> None:
        self.answers.clear()

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def _check(self) -> None:
        if self.unplugged:
            import serial

            raise serial.SerialException("device disconnected")


def unpack(head: bytes) -> tuple[int, int, int, int]:
    x = (head[0] << 2) | (head[1] >> 6)
    y = ((head[1] & 63) << 4) | (head[2] >> 4)
    ex = ((head[2] & 15) << 6) | (head[3] >> 2)
    ey = ((head[3] & 3) << 8) | head[4]
    return x, y, ex, ey


class FakeRevA(FakeSerial):
    """Turing rev. A: six-byte commands, RGB565 little-endian bitmaps."""

    def __init__(self, native=(320, 480), hello: bytes | None = None) -> None:
        super().__init__()
        self.native = native
        self.hello = hello  # UsbPCMonitor answers; the Turing 3.5" does not
        self.size = native  # (width, height) in the current orientation
        self.orientation = 0
        self.screen = Image.new("RGB", native, "white")
        self.commands: list[int] = []
        self.brightness: list[int] = []
        self.rects: list[tuple[int, int, int, int]] = []
        self._buffer = bytearray()
        self._need = 0
        self._rect: tuple[int, int, int, int] | None = None
        self._pixels = bytearray()

    def write(self, data: bytes) -> int:
        self._check()
        self._buffer += data
        self._parse()
        return len(data)

    def _parse(self) -> None:
        buffer = self._buffer
        while True:
            if self._need:
                take = min(self._need, len(buffer))
                self._pixels += buffer[:take]
                del buffer[:take]
                self._need -= take
                if self._need:
                    return
                self._blit()
                continue
            if len(buffer) < 6:
                return
            if bytes(buffer[:6]) == bytes([69] * 6):
                self.commands.append(69)
                if self.hello:
                    self.answers += self.hello
                del buffer[:6]
                continue
            command = buffer[5]
            x, y, ex, ey = unpack(bytes(buffer[:6]))
            if command == 121:
                if len(buffer) < 16:
                    return
                self.orientation = buffer[6] - 100
                width = (buffer[7] << 8) | buffer[8]
                height = (buffer[9] << 8) | buffer[10]
                portrait = self.orientation in (0, 1)
                expected = self.native if portrait else self.native[::-1]
                assert (width, height) == expected, ((width, height), expected)
                self.size = expected
                self.screen = Image.new("RGB", expected, "white")
                del buffer[:16]
            elif command == 197:
                assert x <= ex < self.size[0] and y <= ey < self.size[1], (
                    (x, y, ex, ey),
                    self.size,
                )
                self._rect = (x, y, ex, ey)
                self.rects.append(self._rect)
                self._need = (ex - x + 1) * (ey - y + 1) * 2
                self._pixels = bytearray()
                del buffer[:6]
            else:
                if command == 110:
                    self.brightness.append(x)
                del buffer[:6]
            self.commands.append(command)

    def _blit(self) -> None:
        x, y, ex, ey = self._rect
        size = (ex - x + 1, ey - y + 1)
        self.screen.paste(from_rgb565(bytes(self._pixels), size), (x, y))


class FakeScreen(FakeSerial):
    """A framebuffer in the orientation the panel was set to; subclasses parse."""

    def __init__(self, native) -> None:
        super().__init__()
        self.native = native
        self.size = native
        self.screen = Image.new("RGB", native, "white")
        self.commands: list = []
        self.brightness: list[int] = []
        self.rects: list[tuple[int, int, int, int]] = []
        self.buffer = bytearray()

    def write(self, data: bytes) -> int:
        self._check()
        self.buffer += data
        self.parse()
        return len(data)

    def turn(self, landscape: bool) -> None:
        self.size = self.native[::-1] if landscape else self.native
        self.screen = Image.new("RGB", self.size, "white")

    def blit(self, rect, pixels: bytes, byteorder: str) -> None:
        x0, y0, x1, y1 = rect
        assert 0 <= x0 <= x1 < self.size[0] and 0 <= y0 <= y1 < self.size[1], (rect, self.size)
        self.rects.append(rect)
        self.screen.paste(from_rgb565(pixels, (x1 - x0 + 1, y1 - y0 + 1), byteorder), (x0, y0))


class FakeRevB(FakeScreen):
    """XuanFang rev. B: ten-byte packets framed by the command."""

    def __init__(self, native=(320, 480), sub_revision=0x11) -> None:
        super().__init__(native)
        self.sub_revision = sub_revision
        self.cooldowns = 0
        self._bitmap = None

    def parse(self) -> None:
        buffer = self.buffer
        while True:
            if self._bitmap is not None:
                rect, need = self._bitmap
                if len(buffer) < need:
                    return
                self.blit(rect, bytes(buffer[:need]), "big")
                del buffer[:need]
                self._bitmap = None
                continue
            if len(buffer) < 10:
                return
            command, data = buffer[0], bytes(buffer[1:9])
            assert buffer[9] == command, f"packet not framed: {bytes(buffer[:10]).hex()}"
            del buffer[:10]
            self.commands.append(command)
            if command == 0xCA:
                assert data[:5] == b"HELLO"
                self.answers += bytes([0xCA]) + b"HELLO" + bytes([0x0A, self.sub_revision, 0, 0xCA])
            elif command == 0xCB:
                self.turn(landscape=data[0] == 1)
            elif command == 0xCC:
                x0, y0, x1, y1 = struct.unpack(">4H", data)
                self._bitmap = ((x0, y0, x1, y1), (x1 - x0 + 1) * (y1 - y0 + 1) * 2)
            elif command == 0xCE:
                self.brightness.append(data[0])


class FakeRevD(FakeScreen):
    """Kipye rev. D: portrait only, pictures in packets of 0x50 + 63 bytes;
    it acknowledges every write."""

    def __init__(self, native=(320, 480)) -> None:
        super().__init__(native)
        self._rect = None
        self._picture = None  # (rect, pixel bytes still to come, bytes so far)

    def write(self, data: bytes) -> int:
        written = super().write(data)
        self.answers += b"\x06"  # an acknowledgement nobody needs
        return written

    def parse(self) -> None:
        buffer = self.buffer
        while buffer:
            if self._picture is not None and buffer[0] == 0x50:
                rect, need, pixels = self._picture
                take = min(63, need)
                if len(buffer) < 1 + take:
                    return
                pixels += buffer[1 : 1 + take]
                del buffer[: 1 + take]
                self._picture = (rect, need - take, pixels)
                continue
            head = bytes(buffer[:2])
            size = 10 if head == bytes((67, 65)) else 4
            if len(buffer) < size:
                return
            command = bytes(buffer[:size])
            del buffer[:size]
            self.commands.append(command[:2])
            if head == bytes((67, 65)):
                x0, x1, y0, y1 = struct.unpack(">4H", command[2:])
                self._rect = (x0, y0, x1, y1)
            elif head == bytes((68, 0)):
                x0, y0, x1, y1 = self._rect
                self._picture = (self._rect, (x1 - x0 + 1) * (y1 - y0 + 1) * 2, bytearray())
            elif head == bytes((65, 0)):
                rect, need, pixels = self._picture
                assert need == 0, f"{need} bytes of the picture missing"
                self.blit(rect, bytes(pixels), "big")
                self._picture = None
            elif head == bytes((67, 67)):
                self.brightness.append(struct.unpack(">H", command[2:])[0])
            elif head == bytes((67, 72)):
                self.turn(landscape=False)


class FakeWeAct(FakeScreen):
    """WeAct Display FS V1: commands end with 0x0A, pixels little endian."""

    SIZES = {0x02: 3, 0x03: 5, 0x05: 10, 0xC2: 2}

    def __init__(self, native=(320, 480)) -> None:
        super().__init__(native)
        self._bitmap = None

    def parse(self) -> None:
        buffer = self.buffer
        while buffer:
            if self._bitmap is not None:
                rect, need = self._bitmap
                if len(buffer) < need:
                    return
                self.blit(rect, bytes(buffer[:need]), "little")
                del buffer[:need]
                self._bitmap = None
                continue
            command = buffer[0]
            size = self.SIZES.get(command, 2)
            if len(buffer) < size:
                return
            packet = bytes(buffer[:size])
            del buffer[:size]
            assert packet[-1] == 0x0A, f"command not ended: {packet.hex()}"
            self.commands.append(command)
            if command == 0xC2:
                self.answers += bytes([0xC2]) + b"V1.0.0  " + bytes(10)
            elif command == 0x02:
                self.turn(landscape=packet[1] == 2)
            elif command == 0x03:
                self.brightness.append(packet[1])
            elif command == 0x05:
                x0, y0, x1, y1 = struct.unpack("<4H", packet[1:9])
                self._bitmap = ((x0, y0, x1, y1), (x1 - x0 + 1) * (y1 - y0 + 1) * 2)
