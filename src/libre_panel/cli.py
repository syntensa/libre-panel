"""Command line entry point: ``libre-panel <command>``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from libre_panel import __version__
from libre_panel.config import ConfigError, config_dir, load_config, log_file, write_default_config
from libre_panel.devices.base import DeviceError
from libre_panel.i18n import t


def _cmd_run(args: argparse.Namespace) -> int:
    from libre_panel.app import run
    from libre_panel.instance import InstanceLock

    config = load_config(args.config)
    if args.driver:
        config.device.driver = args.driver
    if args.theme:
        config.theme, config.theme_given = args.theme, True  # asked for: not the panel's default
    with InstanceLock():
        if args.once:
            run(config, once=True)
            return 0
        from libre_panel.plugins import PluginHost, ServiceManager

        host = PluginHost()
        services = ServiceManager(host)
        services.apply(config)
        try:
            run(config, host=host, on_config=services.apply)
        except KeyboardInterrupt:
            pass
        finally:
            services.stop()
            host.close()
    return 0


def _log_file() -> Path:
    """Background runs log to a file (there is no terminal to read)."""
    from logging.handlers import RotatingFileHandler

    path = log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    return path


def _background(args: argparse.Namespace, use_icon: bool, open_editor: bool) -> int:
    """Drive the panel and serve the editor until Quit; one instance at a time."""
    from libre_panel.editor import access
    from libre_panel.instance import AlreadyRunning, InstanceLock
    from libre_panel.service import BackgroundApp
    from libre_panel.tray import run_app
    from libre_panel.window import open_window

    lock = InstanceLock()
    try:
        lock.acquire()
    except AlreadyRunning:
        # a second click while the first start is still on its way: its app is there in a moment
        url = _editor_of_running_app(STARTING_S if open_editor else 0.0)
        if not url:
            raise
        address = access.with_token(url, access.read_token(url))  # the way into the app
        print(t("Libre Panel is already running. Theme editor: {url}", url=address))
        if open_editor:
            open_window(url, "home", config_path=args.config)
        return 0
    try:
        log_path = _log_file() if use_icon else None
        app = BackgroundApp(args.config, port=args.port)
        app.on_quit(lambda: _exit_later(cleanup=app.shutdown))
        return run_app(app, use_icon=use_icon, open_editor=open_editor, log_path=log_path)
    finally:
        lock.release()
        _exit_if_threads_hang()


STARTING_S = 30.0  # how long a second start waits for the first to serve its app


def _editor_of_running_app(seconds: float) -> str | None:
    """The address of the running app (its editor), waiting up to ``seconds`` for one that is
    starting: it writes the address down once its server runs."""
    import time

    from libre_panel.service import running_instance

    deadline = time.monotonic() + seconds
    while True:
        url = (running_instance() or {}).get("editor")
        if url or time.monotonic() >= deadline:
            return url
        time.sleep(0.25)


def _exit_later(seconds: float = 15.0, cleanup: Callable[[], None] | None = None) -> None:
    """Quit means quit: if a GUI library keeps the process alive, end it anyway.

    ``cleanup`` still runs first (at most 5 s): the panel is left in order even
    when the tray library never returns (seen with pystray on X11 under load).
    """
    import threading

    def force() -> None:
        logging.getLogger("libre_panel").warning(
            "still running %.0f s after Quit; exiting anyway. Threads:\n%s",
            seconds,
            _thread_stacks(),
        )
        if cleanup is not None:
            worker = threading.Thread(target=cleanup, name="cleanup", daemon=True)
            worker.start()
            worker.join(5)
        logging.shutdown()
        os._exit(0)

    timer = threading.Timer(seconds, force)
    timer.daemon = True
    timer.start()


def _thread_stacks() -> str:
    """Where every thread is, for the log when quitting hangs."""
    import threading
    import traceback

    names = {thread.ident: thread.name for thread in threading.enumerate()}
    parts = []
    for ident, frame in sys._current_frames().items():
        stack = "".join(traceback.format_stack(frame)[-6:])
        parts.append(f"--- {names.get(ident, ident)}\n{stack}")
    return "\n".join(parts)


def _exit_if_threads_hang(grace_s: float = 5.0) -> None:
    """A background app must end when it is quit (installers and autostart rely
    on it); a helper thread that hangs, e.g. in a tray library, must not keep
    the process alive."""
    import threading
    import time

    deadline = time.monotonic() + grace_s
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and not thread.daemon:
            thread.join(max(0.0, deadline - time.monotonic()))
    stuck = [
        thread.name
        for thread in threading.enumerate()
        if thread is not threading.current_thread() and not thread.daemon and thread.is_alive()
    ]
    if stuck:
        logging.getLogger("libre_panel").warning(
            "threads did not stop (%s); exiting anyway", ", ".join(stuck)
        )
        logging.shutdown()
        os._exit(0)


def _cmd_start(args: argparse.Namespace) -> int:
    """Everything at once in a terminal: drive the panel and open the editor."""
    return _background(args, use_icon=False, open_editor=not args.no_browser)


def _cmd_tray(args: argparse.Namespace) -> int:
    return _background(args, use_icon=not args.no_icon, open_editor=not args.background)


def _instance_running() -> bool:
    from libre_panel.instance import AlreadyRunning, InstanceLock

    try:
        InstanceLock().acquire().release()
    except AlreadyRunning:
        return True
    return False


def _cmd_notify(args: argparse.Namespace) -> int:
    """Show a notice on the panel of the running app (notify.py), e.g. at the end of a script."""
    import json
    import urllib.error
    import urllib.request

    from libre_panel import notify
    from libre_panel.service import running_instance

    url = (running_instance() or {}).get("editor") if _instance_running() else None
    if not url:
        print(t("Libre Panel is not running."), file=sys.stderr)
        return 1
    body = {"text": args.text, "seconds": args.seconds, "level": args.level}
    if args.title:
        body["title"] = args.title
    if args.icon:
        body["icon"] = args.icon
    request = urllib.request.Request(
        url + "api/notify",
        data=json.dumps(body).encode(),
        headers={notify.HEADER: notify.read_key(), "Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local, no proxy
    try:
        answer = json.load(opener.open(request, timeout=10))
    except urllib.error.HTTPError as exc:
        try:
            print(json.load(exc).get("error") or exc, file=sys.stderr)
        except ValueError:
            print(exc, file=sys.stderr)
        return 1
    except OSError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not answer.get("shown"):
        print(t("Not shown: {why}.", why=answer.get("reason") or "?"), file=sys.stderr)
        return 2
    return 0


def _cmd_quit(args: argparse.Namespace) -> int:
    """Quit the running background app, e.g. before an installer replaces it."""
    import json
    import time
    import urllib.request

    from libre_panel.editor import access
    from libre_panel.service import running_instance

    if not _instance_running():
        print(t("Libre Panel is not running."))
        return 0
    info = running_instance() or {}
    url, pid = info.get("editor"), info.get("pid")
    if url:
        token = access.read_token(url)  # the file the running app wrote for this user
        request = urllib.request.Request(
            url + "api/app",
            data=json.dumps({"action": "quit"}).encode(),
            headers={access.HEADER: token or "", "Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local, no proxy
        try:
            opener.open(request, timeout=5).read()
        except OSError as exc:
            logging.getLogger("libre_panel").warning(
                "quit request failed: %s%s", exc, "" if token else " (no access token to send)"
            )
    import psutil

    def gone() -> bool:
        # The process itself must be gone too, so an uninstaller finds no files in use.
        return not _instance_running() and not (
            isinstance(pid, int) and pid != os.getpid() and psutil.pid_exists(pid)
        )

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if gone():
            print(t("Libre Panel has quit."))
            return 0
        time.sleep(0.2)
    print(t("Libre Panel did not quit (it may run in a terminal)."), file=sys.stderr)
    return 1


def _cmd_udev_rules(args: argparse.Namespace) -> int:
    rules = Path(__file__).resolve().parent / "assets" / "60-libre-panel.rules"
    sys.stdout.write(rules.read_text(encoding="utf-8"))
    return 0


def _cmd_autostart(args: argparse.Namespace) -> int:
    from libre_panel.autostart import (
        Autostart,
        AutostartError,
        launch_command,
        writable_by_user,
    )
    from libre_panel.editor import access

    autostart = Autostart()
    try:
        if args.action == "enable":
            command = launch_command(args.config)
            if args.elevated and writable_by_user(Path(command[0]), Path.home()):
                print(
                    t(
                        "Warning: {program} is in your user folder, where any program you "
                        "run could replace it and so get administrator rights at the next "
                        "login. Safer: install Libre Panel under Program Files.",
                        program=command[0],
                    ),
                    file=sys.stderr,
                )
            why = access.folder_risk(config_dir()) if args.elevated else ""
            if why:
                print(
                    t(
                        "Warning: with the highest rights Libre Panel runs what config.toml and "
                        "the plugins in {folder} name, and {why}: any user who can change them "
                        "can run a program with administrator rights. Keep the settings in your "
                        "user folder (LIBRE_PANEL_HOME), or take the other users' rights away.",
                        folder=config_dir(),
                        why=why,
                    ),
                    file=sys.stderr,
                )
            where = autostart.enable(command, elevated=args.elevated)
            print(t("Libre Panel starts in the background when you log in ({where}).", where=where))
        elif args.action == "disable":
            print(
                t("Autostart removed.") if autostart.disable() else t("Autostart was not enabled.")
            )
        elif autostart.is_enabled():
            print(t("Autostart is enabled ({where}).", where=autostart.location()))
        else:
            print(t("Autostart is disabled."))
        if args.action != "enable" and (hint := autostart.task_hint()):
            print(hint, file=sys.stderr)  # a leftover task of the same name
    except AutostartError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def _cmd_preview(args: argparse.Namespace) -> int:
    from libre_panel.app import build_hub
    from libre_panel.render.renderer import Renderer
    from libre_panel.sensors.demo import demo_snapshot
    from libre_panel.theme.model import find_theme, load_theme

    config = load_config(args.config)
    theme = load_theme(Path(args.theme) if Path(args.theme).is_dir() else find_theme(args.theme))
    if args.live:
        hub = build_hub(config)
        try:
            hub.snapshot()  # rates (network, disk) need two samples
            _wait_for_sources(hub)
            snapshot = hub.snapshot()
        finally:
            hub.close()
    else:
        snapshot = demo_snapshot()
    renderer = Renderer(theme, preview=True)
    renderer.symbols = config.symbols  # the weather symbols and the moon in the chosen look
    frame, _ = renderer.render(snapshot)
    renderer.close()
    frame.save(args.output)
    for warning in theme.warnings + renderer.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"wrote {args.output} ({theme.width}x{theme.height})")
    return 0


def _cmd_editor(args: argparse.Namespace) -> int:
    from libre_panel.editor import access
    from libre_panel.editor.server import serve
    from libre_panel.instance import AlreadyRunning, InstanceLock
    from libre_panel.service import running_instance
    from libre_panel.window import open_window

    try:
        InstanceLock().acquire().release()
    except AlreadyRunning:
        url = (running_instance() or {}).get("editor")
        if url:  # the background app already serves the editor
            address = access.with_token(url, access.read_token(url))  # the way into the app
            print(t("Libre Panel is already running. Theme editor: {url}", url=address))
            if not args.no_browser:
                open_window(url, "design", browser=args.browser, config_path=args.config)
            return 0
    serve(
        port=args.port,
        open_browser=not args.no_browser,
        config_path=args.config,
        browser=args.browser,
    )
    return 0


def _cmd_window(args: argparse.Namespace) -> int:
    """The app's window (the tray starts it as a process of its own), or --check."""
    from libre_panel import window

    if args.check:
        worked, lines = window.check(open_one=args.open)
        print("\n".join(lines))
        return 0 if worked else 1
    if not args.url:
        print("error: the address of the app is missing", file=sys.stderr)
        return 2
    view = args.view if args.view in window.VIEWS else None
    return window.run_window(args.url, view)


def _cmd_themes(args: argparse.Namespace) -> int:
    from libre_panel.theme.model import list_themes

    for theme in list_themes():
        origin = theme["source"]
        print(f"{theme['id']:<24} {origin:<9} {theme['path']}")
    return 0


def _cmd_plugins(args: argparse.Namespace) -> int:
    from libre_panel.plugins.loader import API_VERSION, BUILT_IN, discover, plugins_dir

    registry = discover()
    print(f"plugin API {API_VERSION}; plugin folders go into {plugins_dir()}")
    found = registry.all()
    if all(part.source == BUILT_IN for part in found):
        print("no plugins installed")
    for part in found:
        registry.get(part.kind, part.name)  # load it, so errors show here
        state = f"FAILED: {part.error}" if part.error else "ok"
        print(f"{part.kind:<10} {part.name:<24} {state:<6} {part.source}")
    for error in registry.errors:
        print(f"error: {error}")
    return 1 if registry.errors or any(part.error for part in found) else 0


SENSORS_SETTLE_SECONDS = 1.0  # between the two readings of `sensors`


def _platform() -> str:
    return sys.platform


class _Reader:
    """Stands in for a theme on show (``want`` needs something that can be forgotten)."""


def _wait_for_sources(hub: Any) -> None:
    """Sources that start in the background (LibreHardwareMonitor inside Libre Panel loads
    .NET first) get the moment they need for their first readings."""
    from libre_panel.sensors.lhm_inprocess import READY_SECONDS

    for provider in hub.providers:
        wait = getattr(provider, "wait_ready", None)
        if callable(wait):
            wait(READY_SECONDS)


def _lhm_status(hub: Any) -> Any:
    from libre_panel.sensors.librehardwaremonitor import LibreHardwareMonitorProvider

    for provider in hub.providers:
        if isinstance(provider, LibreHardwareMonitorProvider):
            return provider.status()
    return None


def _check_lhm(args: argparse.Namespace) -> int:
    """Does LibreHardwareMonitor load and open inside Libre Panel (Windows)? For the build
    to try the bundled libraries: it needs no administrator rights, and the CPU's sensors
    being out of reach is not a failure."""
    from libre_panel.sensors import lhm_inprocess
    from libre_panel.sensors.librehardwaremonitor import dll_dir_of

    options = load_config(args.config).sensors.options.get("librehardwaremonitor", {})
    worked, lines = lhm_inprocess.check(dll_dir_of(options))
    print("\n".join(lines))
    return 0 if worked else 1


def _check_service(args: argparse.Namespace) -> int:
    """Does the sensor service answer this account with readings (Windows)?"""
    from libre_panel.sensors import lhm_service

    worked, lines = lhm_service.check(platform=_platform())
    print("\n".join(lines))
    return 0 if worked else 1


def _cmd_sensors(args: argparse.Namespace) -> int:
    import time

    from libre_panel.app import build_hub
    from libre_panel.sensors.base import want
    from libre_panel.sensors.librehardwaremonitor import DEFAULT_URL, hardware_hint
    from libre_panel.sensorservice.setup import unlock_offered

    if args.check_lhm:
        return _check_lhm(args)
    if args.check_service:
        return _check_service(args)
    config = load_config(args.config)
    hub = build_hub(config, demo=args.demo)
    # Some sources read only while a theme shows their keys. Ask for those that cost
    # nothing (not the ping or the game frame rate), as a theme would, so they are listed;
    # "lhm:*" is every sensor of LibreHardwareMonitor, "temp.*" the SATA drives of Linux (a drive
    # in standby may wake when it is read, so they are read only while something shows them).
    reader = _Reader()
    want(reader, {"cpu.load", "gpu.load", "audio.*", "lhm:*", "temp.*"})
    try:
        hub.snapshot()
        time.sleep(SENSORS_SETTLE_SECONDS)  # rate counters and pollers need time
        _wait_for_sources(hub)
        snapshot = hub.snapshot()
        status = _lhm_status(hub)
    finally:
        hub.close()
    for key in sorted(snapshot.readings):
        r = snapshot.readings[key]
        value = f"{r.value:.2f}" if isinstance(r.value, float) else str(r.value)
        print(f"{key:<40} {value:>14} {r.unit:<5} {r.label}")
    if not args.demo:
        url = config.sensors.options.get("librehardwaremonitor", {}).get("url", DEFAULT_URL)
        hint = hardware_hint(
            snapshot.readings,
            config.sensors.providers,
            url,
            _platform(),
            status,
            unlock_offered(_platform()),
        )
        if not hint and _platform().startswith("linux"):
            hint = _linux_hint()
        if hint:
            print(f"\nnote: {hint}", file=sys.stderr)
    return 0


def _linux_hint() -> str:
    """What is missing on Linux that ``system-setup --linux`` brings, in a sentence and with
    the command that does it; empty when nothing is."""
    from libre_panel import linux_rights, linux_setup

    command = linux_rights.terminal_command(linux_setup.Options(), linux_rights.program())
    return linux_setup.hint(linux_setup.gather(), command)


def _cmd_system_setup(args: argparse.Namespace) -> int:
    """What the system needs an administrator for, one command on both systems. Windows: the
    sensor service, the PawnIO driver and the group game frame rates need (``--service`` ...).
    Linux: the panel's permission, the mainboard's sensor driver and the processor's power
    (``--linux``, which Linux takes for granted unless a flag of Windows' is given)."""
    windows = [
        flag
        for flag, given in (
            ("--service", args.service),
            ("--pawnio", args.pawnio),
            ("--perf-log-users", args.perf_log_users),
            ("--user-sid", args.user_sid),
        )
        if given
    ]
    system = _platform()
    if args.linux or (system.startswith("linux") and not windows):
        if windows:
            print(f"error: {', '.join(windows)} is for Windows, not for --linux", file=sys.stderr)
            return 2
        from libre_panel import linux_setup

        return linux_setup.run_cli(args)
    linux = [
        flag
        for flag, given in (
            ("--rapl", args.rapl),
            ("--no-rapl", args.no_rapl),
            ("--dry-run", args.dry_run),
            ("--json", args.json),
        )
        if given
    ]
    if linux:
        print(f"error: {', '.join(linux)} is for Linux (--linux)", file=sys.stderr)
        return 2
    from libre_panel.sensorservice.setup import run_command

    return run_command(args, platform=system)


def _cmd_models(args: argparse.Namespace) -> int:
    from libre_panel.devices.models import MODELS, PROTOCOLS

    print(
        f"{'model':<18} {'panel':<42} {'landscape':>10} {'portrait':>10}  {'driver':<12} protocol"
    )
    for m in MODELS:
        lw, lh = m.size("landscape")
        pw, ph = m.size("portrait")
        print(
            f"{m.id:<18} {m.vendor + ' ' + m.label.split(' ', 1)[1]:<42} "
            f"{f'{lw}x{lh}':>10} {f'{pw}x{ph}':>10}  {m.driver:<12} {PROTOCOLS[m.protocol]}"
        )
    return 0


def _describe_candidates(vid: str | None, pid: str | None, serial: str | None) -> str:
    from libre_panel.devices.models import models_for_usb

    if not vid or not pid:
        return ""
    matches = models_for_usb(int(vid, 16), int(pid, 16), serial)
    return ("  -> " + " / ".join(m.id for m in matches)) if matches else ""


def _cmd_devices(args: argparse.Namespace) -> int:
    from libre_panel.devices.base import available_drivers, list_serial_ports, list_usb_devices

    print("drivers:", ", ".join(sorted(available_drivers())))
    try:
        ports = list_serial_ports()
    except DeviceError as exc:
        print(f"serial: {exc}")
        ports = []
    for p in ports:
        ids = f"{p['vid']}:{p['pid']}" if p["vid"] else "----:----"
        hint = _describe_candidates(p["vid"], p["pid"], p["serial_number"])
        print(f"{p['device']:<16} {ids}  {p['description']}  serial={p['serial_number']}{hint}")
    try:
        usb_devices = list_usb_devices()
    except DeviceError as exc:
        print(f"usb: {exc}")
        usb_devices = []
    for d in usb_devices:
        print(f"{'usb':<16} {d['vid']}:{d['pid']}{_describe_candidates(d['vid'], d['pid'], None)}")
    if not ports and not usb_devices:
        print("no panels found")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from libre_panel.doctor import run_doctor
    from libre_panel.instance import InstanceLock

    with InstanceLock():
        report = run_doctor(
            args.report,
            ask_questions=not args.no_questions,
            frames=args.frames,
            config_path=args.config,
        )
    return 1 if report.failed else 0


def _cmd_config(args: argparse.Namespace) -> int:
    if args.action == "path":
        print(config_dir() / "config.toml")
        return 0
    try:
        path = write_default_config(args.config, overwrite=args.force)
    except FileExistsError as exc:
        print(f"{exc} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    return 0


def _cmd_location(args: argparse.Namespace) -> int:
    from libre_panel.weather.open_meteo import geocode

    results = geocode(args.name)
    if not results:
        print("no matches")
        return 1
    for r in results:
        place = ", ".join(p for p in (r["name"], r["region"], r["country"]) if p)
        print(f"{place}\n  latitude = {r['latitude']}\n  longitude = {r['longitude']}")
    print("\nWeather data by Open-Meteo.com (CC BY 4.0)")
    return 0


def _threads_on_signal() -> None:
    """``kill -USR1 <pid>`` prints where every thread is: what to look at when the
    panel stops changing and nothing is logged (Linux)."""
    import faulthandler
    import signal

    if sys.stderr is None or not hasattr(signal, "SIGUSR1"):
        return
    try:
        faulthandler.register(signal.SIGUSR1, all_threads=True)
    except (AttributeError, OSError, RuntimeError, ValueError):  # no real stderr (a test)
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="libre-panel", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, default=None, help="path to config.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")
    parser.set_defaults(func=_cmd_start, port=8765, no_browser=False)

    p = sub.add_parser("start", help="drive the panel and open the editor (default)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=_cmd_start)

    p = sub.add_parser("tray", help="run in the background with a tray icon")
    p.add_argument("--port", type=int, default=8765, help="editor port (another is used if taken)")
    p.add_argument(
        "--background", action="store_true", help="do not open the editor (used at login)"
    )
    p.add_argument("--no-icon", action="store_true", help="run without a tray icon")
    p.set_defaults(func=_cmd_tray)

    p = sub.add_parser("autostart", help="start Libre Panel when you log in")
    p.add_argument("action", choices=["enable", "disable", "status"])
    p.add_argument(
        "--elevated",
        action="store_true",
        help="Windows: start with the highest rights (Task Scheduler), for the CPU's "
        "temperature and power and for sensor plugins that read the hardware themselves; "
        "run as administrator",
    )
    p.set_defaults(func=_cmd_autostart)

    p = sub.add_parser(
        "notify",
        help="show a notice on the panel (switched on in Settings → Services)",
    )
    p.add_argument("text", help="what the notice says (at most 160 characters)")
    p.add_argument("--title", default="", help="a heading above it")
    p.add_argument("--seconds", type=float, default=5.0, help="how long it shows (1 to 60)")
    p.add_argument(
        "--icon", default="", help="an icon of the themes, e.g. temperature, music, gamepad"
    )
    p.add_argument(
        "--level", default="info", choices=("info", "warning", "error"), help="how urgent"
    )
    p.set_defaults(func=_cmd_notify)

    p = sub.add_parser("quit", help="quit the running background app")
    p.add_argument("--timeout", type=float, default=15, help="seconds to wait")
    p.set_defaults(func=_cmd_quit)

    p = sub.add_parser("udev-rules", help="print the Linux udev rule for USB panel access")
    p.set_defaults(func=_cmd_udev_rules)

    p = sub.add_parser("run", help="drive the display")
    p.add_argument("--once", action="store_true", help="render a single frame and exit")
    p.add_argument("--driver", help="override device.driver")
    p.add_argument("--theme", help="override theme")
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("preview", help="render a theme to a PNG")
    p.add_argument("theme", nargs="?", default="libre-default", help="theme name or folder")
    p.add_argument("-o", "--output", default="preview.png")
    p.add_argument("--live", action="store_true", help="use real sensors instead of demo data")
    p.set_defaults(func=_cmd_preview)

    p = sub.add_parser("editor", help="open the visual theme editor (in its own window)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="only serve it, open nothing")
    p.add_argument("--browser", action="store_true", help="open it in the browser")
    p.set_defaults(func=_cmd_editor)

    p = sub.add_parser("window", help="show the app in its own window (used by the tray)")
    p.add_argument("url", nargs="?", help="the app's address, http://127.0.0.1:<port>/")
    p.add_argument("--view", default=None, help="home, design, look, live or settings")
    p.add_argument("--check", action="store_true", help="only check that a window could open here")
    p.add_argument(
        "--open",
        action="store_true",
        help="with --check: open a real window for a moment, minimized, as the app does",
    )
    p.set_defaults(func=_cmd_window)

    p = sub.add_parser("themes", help="list installed themes")
    p.set_defaults(func=_cmd_themes)

    p = sub.add_parser("sensors", help="list sensor keys and current values")
    p.add_argument("--demo", action="store_true")
    p.add_argument(
        "--check-lhm",
        action="store_true",
        help="Windows: check that LibreHardwareMonitor loads inside Libre Panel, then exit",
    )
    p.add_argument(
        "--check-service",
        action="store_true",
        help="Windows: check that the sensor service answers with readings, then exit",
    )
    p.set_defaults(func=_cmd_sensors)

    p = sub.add_parser(
        "system-setup",
        help="as administrator: set up what the sensors need, and on Linux the panel's access "
        "(Windows: the setup runs this; Linux: --linux)",
    )
    p.add_argument(
        "--service",
        action="store_true",
        help="Windows: the sensor service, which reads the sensors with the rights they need",
    )
    p.add_argument(
        "--pawnio", action="store_true", help="Windows: the PawnIO driver it reads them through"
    )
    p.add_argument(
        "--perf-log-users",
        action="store_true",
        help="Windows: join Performance Log Users, for game frame rates (from the next sign-in)",
    )
    p.add_argument("--user-sid", help="Windows: whose group membership (default: who is signed in)")
    p.add_argument(
        "--linux",
        action="store_true",
        help="Linux: the panel's USB permission, the mainboard's fan chip, SATA drive "
        "temperatures, and with --rapl the processor's power; asks for the password with "
        "pkexec or sudo, prints every step (taken for granted on Linux unless a flag of "
        "Windows' is given)",
    )
    power = p.add_mutually_exclusive_group()
    power.add_argument(
        "--rapl",
        action="store_true",
        help="Linux: also let every user read the processor's energy counters (the kernel "
        "keeps them to root since 2020 because they leak what the processor computes)",
    )
    power.add_argument(
        "--no-rapl",
        action="store_true",
        help="Linux: close the energy counters to other users again",
    )
    p.add_argument("--remove", action="store_true", help="take away what was set up")
    p.add_argument("--status", action="store_true", help="say what is in place (no rights needed)")
    p.add_argument("--dry-run", action="store_true", help="Linux: only say what it would do")
    p.add_argument(
        "--json", action="store_true", help="Linux: print the result as one line of JSON"
    )
    p.set_defaults(func=_cmd_system_setup)

    p = sub.add_parser("plugins", help="list installed plugins and whether they load")
    p.set_defaults(func=_cmd_plugins)

    p = sub.add_parser("models", help="list known panel models and resolutions")
    p.set_defaults(func=_cmd_models)

    p = sub.add_parser("devices", help="list drivers and connected serial/USB devices")
    p.set_defaults(func=_cmd_devices)

    p = sub.add_parser("doctor", help="check a connected panel and write a report")
    p.add_argument("--report", type=Path, help="where to write the report")
    p.add_argument("--no-questions", action="store_true", help="do not ask what the panel shows")
    p.add_argument("--frames", type=int, default=20, help="frames for the speed test")
    p.set_defaults(func=_cmd_doctor)

    p = sub.add_parser("config", help="create or locate config.toml")
    p.add_argument("action", choices=["init", "path"])
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=_cmd_config)

    p = sub.add_parser("location", help="find latitude/longitude for the weather config")
    p.add_argument("name")
    p.set_defaults(func=_cmd_location)
    return parser


def main(argv: list[str] | None = None, default_command: str = "start") -> int:
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else list(argv)
    if default_command == "start":
        args = parser.parse_args(argv)
    else:  # e.g. LibrePanel.exe: `--background` alone means `tray --background`
        args, rest = parser.parse_known_args(argv)
        if args.command is None:
            chosen = parser.parse_args([default_command, *rest])
            chosen.config, chosen.verbose = args.config, args.verbose
            args = chosen
        elif rest:
            parser.error("unrecognized arguments: " + " ".join(rest))
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        # Programs without a console (pythonw, the windowed build) have no stderr.
        handlers=[logging.StreamHandler()] if sys.stderr else [logging.NullHandler()],
    )
    _threads_on_signal()
    from libre_panel import i18n
    from libre_panel.instance import AlreadyRunning
    from libre_panel.theme.model import ThemeError
    from libre_panel.weather.open_meteo import WeatherError

    try:
        i18n.set_language(load_config(args.config).language)
    except ConfigError:
        i18n.set_language("auto")  # the command reports the config error itself
    try:
        return args.func(args)
    except AlreadyRunning as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (ConfigError, ThemeError, DeviceError, WeatherError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
