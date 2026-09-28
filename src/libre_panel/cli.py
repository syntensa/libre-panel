"""Command line entry point: ``libre-panel <command>``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from libre_panel import __version__
from libre_panel.config import ConfigError, config_dir, load_config, write_default_config
from libre_panel.devices.base import DeviceError
from libre_panel.i18n import t


def _cmd_run(args: argparse.Namespace) -> int:
    from libre_panel.app import run
    from libre_panel.instance import InstanceLock

    config = load_config(args.config)
    if args.driver:
        config.device.driver = args.driver
    if args.theme:
        config.theme = args.theme
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

    path = config_dir() / "logs" / "libre-panel.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    return path


def _background(args: argparse.Namespace, use_icon: bool, open_editor: bool) -> int:
    """Drive the panel and serve the editor until Quit; one instance at a time."""
    import webbrowser

    from libre_panel.instance import AlreadyRunning, InstanceLock
    from libre_panel.service import BackgroundApp, running_instance
    from libre_panel.tray import run_app

    lock = InstanceLock()
    try:
        lock.acquire()
    except AlreadyRunning:
        url = (running_instance() or {}).get("editor")
        if not url:
            raise
        print(t("Libre Panel is already running. Theme editor: {url}", url=url))
        if open_editor:
            webbrowser.open(url)
        return 0
    try:
        log_path = _log_file() if use_icon else None
        app = BackgroundApp(args.config, port=args.port)
        app.on_quit(lambda: _exit_later(cleanup=app.shutdown))
        return run_app(app, use_icon=use_icon, open_editor=open_editor, log_path=log_path)
    finally:
        lock.release()
        _exit_if_threads_hang()


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


def _cmd_quit(args: argparse.Namespace) -> int:
    """Quit the running background app, e.g. before an installer replaces it."""
    import json
    import time
    import urllib.request

    from libre_panel.service import running_instance

    if not _instance_running():
        print(t("Libre Panel is not running."))
        return 0
    info = running_instance() or {}
    url, pid = info.get("editor"), info.get("pid")
    if url:
        request = urllib.request.Request(
            url + "api/app",
            data=json.dumps({"action": "quit"}).encode(),
            headers={"X-Libre-Panel": "1", "Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local, no proxy
        try:
            opener.open(request, timeout=5).read()
        except OSError as exc:
            logging.getLogger("libre_panel").warning("quit request failed: %s", exc)
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
    from libre_panel.autostart import Autostart, launch_command

    autostart = Autostart()
    if args.action == "enable":
        where = autostart.enable(launch_command(args.config))
        print(t("Libre Panel starts in the background when you log in ({where}).", where=where))
    elif args.action == "disable":
        print(t("Autostart removed.") if autostart.disable() else t("Autostart was not enabled."))
    elif autostart.is_enabled():
        print(t("Autostart is enabled ({where}).", where=autostart.location()))
    else:
        print(t("Autostart is disabled."))
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
            snapshot = hub.snapshot()
        finally:
            hub.close()
    else:
        snapshot = demo_snapshot()
    renderer = Renderer(theme)
    frame, _ = renderer.render(snapshot)
    frame.save(args.output)
    for warning in theme.warnings + renderer.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"wrote {args.output} ({theme.width}x{theme.height})")
    return 0


def _cmd_editor(args: argparse.Namespace) -> int:
    import webbrowser

    from libre_panel.editor.server import serve
    from libre_panel.instance import AlreadyRunning, InstanceLock
    from libre_panel.service import running_instance

    try:
        InstanceLock().acquire().release()
    except AlreadyRunning:
        url = (running_instance() or {}).get("editor")
        if url:  # the background app already serves the editor
            print(t("Libre Panel is already running. Theme editor: {url}", url=url))
            if not args.no_browser:
                webbrowser.open(url)
            return 0
    serve(port=args.port, open_browser=not args.no_browser, config_path=args.config)
    return 0


def _cmd_themes(args: argparse.Namespace) -> int:
    from libre_panel.theme.model import list_themes

    for theme in list_themes():
        origin = "built-in" if theme["builtin"] else "user"
        print(f"{theme['id']:<24} {origin:<9} {theme['path']}")
    return 0


def _cmd_plugins(args: argparse.Namespace) -> int:
    from libre_panel.plugins.loader import API_VERSION, discover, plugins_dir

    registry = discover()
    print(f"plugin API {API_VERSION}; plugin folders go into {plugins_dir()}")
    found = registry.all()
    if not found:
        print("no plugins installed")
    for part in found:
        registry.get(part.kind, part.name)  # load it, so errors show here
        state = f"FAILED: {part.error}" if part.error else "ok"
        print(f"{part.kind:<10} {part.name:<24} {state:<6} {part.source}")
    for error in registry.errors:
        print(f"error: {error}")
    return 1 if registry.errors or any(part.error for part in found) else 0


def _cmd_sensors(args: argparse.Namespace) -> int:
    import time

    from libre_panel.app import build_hub

    config = load_config(args.config)
    hub = build_hub(config, demo=args.demo)
    try:
        hub.snapshot()
        time.sleep(1.0)  # let rate counters and background pollers produce values
        snapshot = hub.snapshot()
    finally:
        hub.close()
    for key in sorted(snapshot.readings):
        r = snapshot.readings[key]
        value = f"{r.value:.2f}" if isinstance(r.value, float) else str(r.value)
        print(f"{key:<40} {value:>14} {r.unit:<5} {r.label}")
    return 0


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
    p.set_defaults(func=_cmd_autostart)

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

    p = sub.add_parser("editor", help="open the visual theme editor in the browser")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=_cmd_editor)

    p = sub.add_parser("themes", help="list installed themes")
    p.set_defaults(func=_cmd_themes)

    p = sub.add_parser("sensors", help="list sensor keys and current values")
    p.add_argument("--demo", action="store_true")
    p.set_defaults(func=_cmd_sensors)

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
