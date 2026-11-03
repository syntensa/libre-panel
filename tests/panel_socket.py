"""The simulated TURZX panel of fake_panel.py behind a socket, for Libre Panel's engine.

``libre-panel-engine panel --link tcp:HOST:PORT`` talks to it instead of USB (engine/src/link.rs,
TcpLink): "W" len timeout data is a bulk write, answered "K" or "E" len text; "R" len timeout
is a bulk read, answered "D" len data, "T" (nothing in time) or "E" len text (u32 little endian
each). The panel checks every packet as it does for the Python driver, so a test of the
engine sees what the panel was sent, exactly as one of devices/turzx_usb.py does.
"""

from __future__ import annotations

import socket
import struct
import threading

import usb.core
from fake_panel import FakePanel


class PanelSocket:
    """Serves ``panel`` to one engine at a time, each connection in a thread of its own; what
    went wrong in the panel's checks (an AssertionError) is kept in :attr:`errors` and answered
    as a failed transfer."""

    def __init__(self, panel: FakePanel) -> None:
        self.panel = panel
        self.errors: list[str] = []
        self.lock = threading.Lock()  # (the panel is not made for two at once)
        self._server = socket.create_server(("127.0.0.1", 0))
        self.address = "{}:{}".format(*self._server.getsockname())
        self._thread = threading.Thread(target=self._serve, name="panel-socket", daemon=True)
        self._thread.start()

    @property
    def link(self) -> str:
        return f"tcp:{self.address}"

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._talk, args=(conn,), daemon=True).start()

    @staticmethod
    def _exact(conn: socket.socket, n: int) -> bytes | None:
        data = bytearray()
        while len(data) < n:
            part = conn.recv(n - len(data))
            if not part:
                return None
            data += part
        return bytes(data)

    def _talk(self, conn: socket.socket) -> None:
        with conn:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            while True:
                head = self._exact(conn, 9)
                if head is None:
                    return
                kind, length, timeout = head[:1], *struct.unpack("<II", head[1:])
                if kind == b"W":
                    data = self._exact(conn, length)
                    if data is None:
                        return
                    conn.sendall(self._write(data, timeout))
                elif kind == b"R":
                    conn.sendall(self._read(length, timeout))
                else:
                    return

    def _failed(self, text: str) -> bytes:
        said = text.encode("utf-8")
        return b"E" + struct.pack("<I", len(said)) + said

    def _write(self, data: bytes, timeout: int) -> bytes:
        try:
            with self.lock:
                self.panel.write(data, timeout)
        except AssertionError as exc:
            self.errors.append(str(exc))
            return self._failed(f"the panel refused it: {exc}")
        except usb.core.USBError as exc:
            return self._failed(str(exc))
        return b"K"

    def _read(self, length: int, timeout: int) -> bytes:
        try:
            with self.lock:
                reply = self.panel.read(length, timeout)
        except AssertionError as exc:
            self.errors.append(str(exc))
            return self._failed(f"the panel refused it: {exc}")
        except usb.core.USBTimeoutError:
            return b"T"
        except usb.core.USBError as exc:
            return self._failed(str(exc))
        return b"D" + struct.pack("<I", len(reply)) + reply

    def close(self) -> None:
        self._server.close()
