"""The sensor service as Windows starts it: ``libre-panel-sensors.exe``.

Started by the Service Control Manager, ``main`` hands Windows the service's entry point
(``StartServiceCtrlDispatcher``), and ``run`` opens LibreHardwareMonitor on every kind of
hardware it knows, makes the pipe and answers until Windows says stop. The service takes no
arguments from anybody: what it reads, where its libraries are and where it writes its log
follow from where its program lies, a folder that only administrators may change.

Two ways to start it by hand (as administrator):

- ``libre-panel-sensors --check``: open LibreHardwareMonitor once, say what it finds, end
  (the build's smoke test);
- ``libre-panel-sensors --console``: run the service in the terminal until Ctrl+C.
"""

from __future__ import annotations

import argparse
import ctypes
import functools
import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from libre_panel import __version__
from libre_panel.sensorservice import SERVICE_NAME

log = logging.getLogger(__name__)

# Opened after the kinds that Libre Panel reads itself (lhm_inprocess.open_computer), one at a
# time, so that a kind that cannot be read (a library missing) costs only itself.
MORE_HARDWARE = ("IsStorageEnabled", "IsControllerEnabled", "IsPsuEnabled", "IsBatteryEnabled")
RETRY_SECONDS = 5.0  # the pipe's name is taken: try again after this long
LOG_BYTES = 1_000_000


def open_everything(dll_dir: Path) -> Any:
    """LibreHardwareMonitor's ``Computer``, open on every kind of hardware it reads: CPU, GPU,
    memory, mainboard and network, then drives, controllers (fan controllers, pumps), power
    supplies and batteries where there are any."""
    from libre_panel.sensors.lhm_inprocess import open_computer

    computer = open_computer(dll_dir)
    for switch in MORE_HARDWARE:
        try:
            setattr(computer, switch, True)  # an open Computer adds the kind at once
        except Exception as exc:  # noqa: BLE001 - a .NET exception: that kind is left out
            log.warning("LibreHardwareMonitor cannot read %s: %s", switch[2:-7], exc)
    return computer


def make_reader() -> Any:
    """The reader of the LibreHardwareMonitor that came with this program."""
    from libre_panel.sensors import lhm_inprocess
    from libre_panel.sensorservice.core import Reader

    folder, why = lhm_inprocess.availability()
    if folder is None:
        return Reader(functools.partial(_unavailable, why), pawnio=lhm_inprocess.pawnio_version)
    return Reader(functools.partial(open_everything, folder), pawnio=lhm_inprocess.pawnio_version)


def _unavailable(why: str) -> Any:
    from libre_panel.sensors.lhm_inprocess import LhmUnavailable

    raise LhmUnavailable(why)


def run(
    stop: threading.Event,
    on_running: Callable[[], None] | None = None,
    *,
    reader_factory: Callable[[], Any] = make_reader,
    server_factory: Callable[[Any], Any] | None = None,
) -> None:
    """The service: read, answer on the pipe, until ``stop`` is set. ``on_running`` is called
    once the reader runs and the pipe was tried (a taken name is tried again every few
    seconds; Windows need not wait for that)."""
    from libre_panel.sensorservice.core import Handler

    if server_factory is None:
        from libre_panel.sensorservice.winpipe import PipeServer

        server_factory = PipeServer
    reader = reader_factory()
    reader.start()
    server = server_factory(Handler(reader))
    running = False
    try:
        while not stop.is_set():
            try:
                server.start()
                running = True
                break
            except OSError as exc:
                log.warning(
                    "the pipe cannot be made (%s): another program holds its name; "
                    "trying again in %.0f s",
                    exc,
                    RETRY_SECONDS,
                )
                if on_running is not None:
                    on_running()
                    on_running = None
                stop.wait(RETRY_SECONDS)
        if on_running is not None:
            on_running()
        while not stop.wait(1.0):  # (a wait without end would not see Ctrl+C on Windows)
            pass
    finally:
        if running:
            server.stop()
        reader.close()


# -- the Service Control Manager's side ----------------------------------------------------------


class _Host:
    """ServiceMain and the control handler, for StartServiceCtrlDispatcher."""

    def __init__(self) -> None:
        from libre_panel.sensorservice import winapi

        self.api = winapi
        self.stop = threading.Event()
        self.handle: Any = None
        self.checkpoint = 0
        # kept here: Windows calls them for as long as the service runs
        self.main_function = winapi.SERVICE_MAIN_FUNCTION(self.main)
        self.handler_function = winapi.HANDLER_FUNCTION_EX(self.control)

    def report(self, state: int, wait_hint: int = 0, failed: bool = False) -> None:
        api = self.api
        pending = state in (api.SERVICE_START_PENDING, api.SERVICE_STOP_PENDING)
        self.checkpoint = self.checkpoint + 1 if pending else 0
        accepted = api.SERVICE_ACCEPT_STOP | api.SERVICE_ACCEPT_SHUTDOWN
        status = api.SERVICE_STATUS(
            api.SERVICE_WIN32_OWN_PROCESS,
            state,
            accepted if state == api.SERVICE_RUNNING else 0,
            1066 if failed else 0,  # ERROR_SERVICE_SPECIFIC_ERROR: the recovery restarts it
            1 if failed else 0,
            self.checkpoint,
            wait_hint,
        )
        if not api.advapi32().SetServiceStatus(self.handle, ctypes.byref(status)):
            log.warning("SetServiceStatus failed: %s", api.last_error("SetServiceStatus"))

    def control(self, code: int, event: int, data: Any, context: Any) -> int:
        api = self.api
        if code in (api.SERVICE_CONTROL_STOP, api.SERVICE_CONTROL_SHUTDOWN):
            self.report(api.SERVICE_STOP_PENDING, wait_hint=15_000)
            self.stop.set()
            return api.NO_ERROR
        if code == api.SERVICE_CONTROL_INTERROGATE:
            return api.NO_ERROR
        return api.ERROR_CALL_NOT_IMPLEMENTED

    def main(self, argc: int, argv: Any) -> None:
        api = self.api
        self.handle = api.advapi32().RegisterServiceCtrlHandlerExW(
            SERVICE_NAME, self.handler_function, None
        )
        if not self.handle:
            log.error("RegisterServiceCtrlHandlerEx failed: %s", api.last_error("Register"))
            return
        self.report(api.SERVICE_START_PENDING, wait_hint=30_000)
        try:
            run(self.stop, on_running=lambda: self.report(api.SERVICE_RUNNING))
        except Exception:  # noqa: BLE001 - said in the log; Windows' recovery starts it again
            log.exception("the sensor service failed")
            self.report(api.SERVICE_STOPPED, failed=True)
            return
        log.info("the sensor service stops")
        self.report(api.SERVICE_STOPPED)

    def dispatch(self) -> int:
        api = self.api
        table = (api.SERVICE_TABLE_ENTRYW * 2)()
        table[0].lpServiceName = SERVICE_NAME
        table[0].lpServiceProc = self.main_function
        if api.advapi32().StartServiceCtrlDispatcherW(table):
            return 0
        code = ctypes.get_last_error()
        if code == api.ERROR_FAILED_SERVICE_CONTROLLER_CONNECT:
            print(
                "This is Libre Panel's sensor service: Windows starts it. To try it by hand, "
                "as administrator: --check, or --console.",
                file=sys.stderr,
            )
            return 2
        log.error("StartServiceCtrlDispatcher failed: %s", api.last_error("dispatch", code))
        return 1


def _log_to_file() -> None:
    """The service's log, next to its program (a folder only administrators may change):
    never in a folder that another user could have made first."""
    from logging.handlers import RotatingFileHandler

    if not getattr(sys, "frozen", False):
        return
    folder = Path(sys.executable).resolve().parent / "logs"
    try:
        folder.mkdir(exist_ok=True)
        handler = RotatingFileHandler(
            folder / "service.log", maxBytes=LOG_BYTES, backupCount=1, encoding="utf-8"
        )
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)


def check() -> int:
    """Open LibreHardwareMonitor on everything once and say what it finds."""
    from libre_panel.sensors import lhm_inprocess

    worked, lines = lhm_inprocess.check(opener=open_everything)
    print(f"Libre Panel sensor service {__version__}")
    print("\n".join(lines))
    return 0 if worked else 1


def _unblock() -> None:
    """The mark of the internet off the service's own files (libre_panel.unblock)."""
    try:
        from libre_panel.unblock import unblock_program

        unblock_program()
    except Exception as exc:  # noqa: BLE001 - only a help
        log.debug("the download mark stays: %s", exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="libre-panel-sensors", description="Libre Panel's sensor service (Windows)."
    )
    parser.add_argument("--check", action="store_true", help="open the sensors once, then end")
    parser.add_argument("--console", action="store_true", help="run in this terminal")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler()] if sys.stderr else [logging.NullHandler()],
    )
    _unblock()  # (before LibreHardwareMonitor, which is .NET)
    if args.check:
        return check()
    _log_to_file()
    if args.console:
        stop = threading.Event()
        try:
            run(stop, on_running=lambda: print(f"answering; Ctrl+C ends ({__version__})"))
        except KeyboardInterrupt:
            stop.set()
        return 0
    return _Host().dispatch()
