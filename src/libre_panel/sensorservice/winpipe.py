"""The named pipe between the sensor service and Libre Panel (Windows).

The service makes ``INSTANCES`` instances of ``\\\\.\\pipe\\LibrePanelSensors``, each served by a
thread of its own and used again for the next client, so the name is never free while the
service runs. The first is made with ``FILE_FLAG_FIRST_PIPE_INSTANCE``: if another program
holds the name already, the service does not join it but tries again later (and Libre Panel,
which checks that the pipe's server is the service, does not talk to that program). Who may
open the pipe is fixed then, by ``pipe_sddl()``:

- the system and administrators may do anything with it;
- people signed in at this PC (interactive logons, Remote Desktop too) may read and write its
  data and read its attributes, nothing more: not make an instance of their own under its
  name, not change who may;
- network logons may do nothing, and Windows turns away clients on other computers
  (``PIPE_REJECT_REMOTE_CLIENTS``).

Every wait has an end: a client that says nothing is let go after ``core.IDLE_SECONDS``, a
message must be through within ``core.MESSAGE_SECONDS``, and stopping the service ends all
waits at once. Bytes go in and out as ``core.serve`` asks, through overlapped I/O, so that a
timeout can cancel a read or a write that hangs.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections.abc import Iterable
from typing import Any

from libre_panel.sensorservice import PIPE_NAME
from libre_panel.sensorservice.core import (
    ConnectionEnded,
    Handler,
    serve,
)
from libre_panel.sensorservice.protocol import (
    MAX_ANSWER,
    MAX_REQUEST,
    encode,
    read_message,
)

log = logging.getLogger(__name__)

INSTANCES = 4  # clients served at once (each signed-in person's Libre Panel is one)
BUFFER = 64 * 1024  # bytes the pipe holds each way
CONNECT_SECONDS = 2.0  # a client waits this long for a free instance
# What the people signed in at this PC may do with the pipe: read and write its data, read its
# attributes and its security descriptor, wait on it (FILE_READ_DATA | FILE_WRITE_DATA |
# FILE_READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE). Not FILE_CREATE_PIPE_INSTANCE (the
# FILE_APPEND_DATA bit of GENERIC_WRITE), WRITE_DAC or WRITE_OWNER.
CLIENT_RIGHTS = 0x120083


def pipe_sddl(readers: Iterable[str] = ("IU",)) -> str:
    """The security descriptor of the pipe, in SDDL. ``readers`` are the SIDs (or SDDL names:
    "IU" is everyone signed in interactively) that may ask for readings. The DACL is protected,
    so nothing is inherited, and network logons are denied first, whatever else they are."""
    aces = ["(D;;GA;;;NU)", "(A;;GA;;;SY)", "(A;;GA;;;BA)"]
    aces += [f"(A;;0x{CLIENT_RIGHTS:x};;;{reader})" for reader in readers]
    return "D:P" + "".join(aces)


def _api() -> Any:
    from libre_panel.sensorservice import winapi

    return winapi


class PipeStream:
    """An open pipe handle, read and written with overlapped I/O: each operation waits for
    its end, the timeout or ``stop`` (a Win32 event), whichever comes first."""

    def __init__(self, handle: Any, stop: Any = None) -> None:
        api = _api()
        self.handle = handle
        self._stop = stop
        self._event = api.new_event()

    def _finish(self, overlapped: Any, deadline: float) -> None:
        """Wait for the operation; cancel it (and wait for that) when time is up or the
        service stops."""
        api = _api()
        k = api.kernel32()
        events = [self._event] + ([self._stop] if self._stop else [])
        handles = (api.w.HANDLE * len(events))(*events)
        wait = max(0, int((deadline - time.monotonic()) * 1000))
        if k.WaitForMultipleObjects(len(events), handles, False, wait) == api.WAIT_OBJECT_0:
            return
        k.CancelIoEx(self.handle, ctypes.byref(overlapped))
        k.GetOverlappedResult(
            self.handle, ctypes.byref(overlapped), ctypes.byref(api.w.DWORD()), True
        )
        raise ConnectionEnded("timed out" if self._stop is None or not self.stopping() else "stop")

    def _io(self, write: bool, data: Any, size: int, deadline: float) -> int:
        api = _api()
        k = api.kernel32()
        overlapped = api.OVERLAPPED()
        k.ResetEvent(self._event)
        overlapped.hEvent = self._event
        call = k.WriteFile if write else k.ReadFile
        if not call(self.handle, data, size, None, ctypes.byref(overlapped)):
            code = ctypes.get_last_error()
            if code != api.ERROR_IO_PENDING:
                raise ConnectionEnded(f"pipe error {code}")
            self._finish(overlapped, deadline)
        done = api.w.DWORD(0)
        if not k.GetOverlappedResult(
            self.handle, ctypes.byref(overlapped), ctypes.byref(done), True
        ):
            raise ConnectionEnded(f"pipe error {ctypes.get_last_error()}")
        if not done.value:
            raise ConnectionEnded("the other side closed the pipe")
        return int(done.value)

    def read(self, size: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        out = bytearray()
        while len(out) < size:
            buffer = ctypes.create_string_buffer(size - len(out))
            got = self._io(False, buffer, len(buffer), deadline)
            out += buffer.raw[:got]
        return bytes(out)

    def write(self, data: bytes, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        sent = 0
        while sent < len(data):
            rest = data[sent:]  # kept alive here until the write is over
            sent += self._io(True, rest, len(rest), deadline)

    def stopping(self) -> bool:
        api = _api()
        return bool(self._stop) and api.kernel32().WaitForSingleObject(self._stop, 0) == 0

    def wait(self, seconds: float) -> bool:
        if not self._stop:
            time.sleep(seconds)
            return False
        api = _api()
        return api.kernel32().WaitForSingleObject(self._stop, int(seconds * 1000)) == 0

    def close(self) -> None:
        _api().close(self._event)


# -- the service's side ----------------------------------------------------------------------------


class PipeServer:
    """``INSTANCES`` instances of the pipe, each answering one client at a time with
    ``handler``, until ``stop()``."""

    def __init__(
        self,
        handler: Handler,
        *,
        name: str = PIPE_NAME,
        sddl: str | None = None,
        instances: int = INSTANCES,
    ) -> None:
        self.handler = handler
        self.name = name
        self.sddl = sddl or pipe_sddl()
        self.instances = max(1, instances)
        self._stop = _api().new_event()
        self._threads: list[threading.Thread] = []

    def _instance(self, first: bool) -> Any:
        api = _api()
        mode = api.PIPE_ACCESS_DUPLEX | api.FILE_FLAG_OVERLAPPED
        if first:
            mode |= api.FILE_FLAG_FIRST_PIPE_INSTANCE
        kind = api.PIPE_TYPE_BYTE | api.PIPE_READMODE_BYTE | api.PIPE_WAIT
        kind |= api.PIPE_REJECT_REMOTE_CLIENTS
        with api.security_attributes(self.sddl) as attributes:
            handle = api.kernel32().CreateNamedPipeW(
                self.name, mode, kind, self.instances, BUFFER, BUFFER, 0, ctypes.byref(attributes)
            )
            code = ctypes.get_last_error()  # before anything else is called
        if not handle or handle == api.INVALID_HANDLE:
            raise api.last_error(f"CreateNamedPipe {self.name}", code)
        return handle

    def start(self) -> None:
        """Make the instances and serve them. Raises OSError when the name is taken (by
        another program, or a service that runs already)."""
        handles = [self._instance(first=True)]
        try:
            handles += [self._instance(first=False) for _ in range(1, self.instances)]
        except OSError:
            for handle in handles:
                _api().close(handle)
            raise
        for number, handle in enumerate(handles):
            thread = threading.Thread(
                target=self._serve, args=(handle,), name=f"pipe-{number}", daemon=True
            )
            self._threads.append(thread)
            thread.start()
        log.info("answering on %s", self.name)

    def _connect(self, handle: Any) -> bool | None:
        """Wait for a client: True when one came, None when it went before it was served
        (try again), False when the service stops."""
        api = _api()
        k = api.kernel32()
        event = api.new_event()
        try:
            overlapped = api.OVERLAPPED()
            overlapped.hEvent = event
            if k.ConnectNamedPipe(handle, ctypes.byref(overlapped)):
                return True
            code = ctypes.get_last_error()
            if code == api.ERROR_PIPE_CONNECTED:
                return True
            if code == api.ERROR_NO_DATA:  # it came and went already
                return None
            if code != api.ERROR_IO_PENDING:
                raise api.last_error("ConnectNamedPipe", code)
            handles = (api.w.HANDLE * 2)(event, self._stop)
            if k.WaitForMultipleObjects(2, handles, False, api.INFINITE) != api.WAIT_OBJECT_0:
                k.CancelIoEx(handle, ctypes.byref(overlapped))
                k.GetOverlappedResult(
                    handle, ctypes.byref(overlapped), ctypes.byref(api.w.DWORD()), True
                )
                return False
            done = api.w.DWORD(0)
            ok = k.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(done), False)
            return True if ok else None
        finally:
            api.close(event)

    def _serve(self, handle: Any) -> None:
        api = _api()
        try:
            while True:
                connected = self._connect(handle)
                if connected is False:
                    return
                if connected:
                    stream = PipeStream(handle, self._stop)
                    try:
                        serve(stream, self.handler)
                    except Exception:  # noqa: BLE001 - one client must not end the instance
                        log.exception("a client could not be served")
                    finally:
                        stream.close()
                api.kernel32().DisconnectNamedPipe(handle)
        except OSError:
            log.exception("an instance of the pipe failed")
        finally:
            api.close(handle)

    def stop(self) -> None:
        api = _api()
        api.kernel32().SetEvent(self._stop)
        for thread in self._threads:
            thread.join(10)
        api.close(self._stop)


# -- Libre Panel's side ----------------------------------------------------------------------------


class Connection:
    """Libre Panel's connection to the service: a request, its answer."""

    def __init__(self, stream: PipeStream) -> None:
        self.stream = stream

    def request(self, message: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.stream.write(encode(message, MAX_REQUEST), timeout)
        return read_message(lambda size: self.stream.read(size, timeout), MAX_ANSWER)

    def server_pid(self) -> int:
        """The process id of the pipe's server (0 where Windows does not say)."""
        api = _api()
        pid = api.w.ULONG(0)
        if not api.kernel32().GetNamedPipeServerProcessId(self.stream.handle, ctypes.byref(pid)):
            return 0
        return int(pid.value)

    def close(self) -> None:
        self.stream.close()
        _api().close(self.stream.handle)


def connect(name: str = PIPE_NAME, timeout: float = CONNECT_SECONDS) -> Connection:
    """Open the pipe as a client. The server may tell who this is, but not act as this
    account (``SECURITY_IDENTIFICATION``). Raises OSError: FileNotFoundError when nothing
    answers under the name, PermissionError when this account may not open it."""
    api = _api()
    k = api.kernel32()
    flags = api.FILE_FLAG_OVERLAPPED | api.SECURITY_SQOS_PRESENT | api.SECURITY_IDENTIFICATION
    deadline = time.monotonic() + timeout
    while True:
        handle = k.CreateFileW(name, api.CLIENT_ACCESS, 0, None, api.OPEN_EXISTING, flags, None)
        if handle and handle != api.INVALID_HANDLE:
            return Connection(PipeStream(handle))
        code = ctypes.get_last_error()
        left = deadline - time.monotonic()
        if code != api.ERROR_PIPE_BUSY or left <= 0:
            raise api.last_error(f"opening {name}", code)
        k.WaitNamedPipeW(name, max(1, int(left * 1000)))  # all instances busy: wait for one
