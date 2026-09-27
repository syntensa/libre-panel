"""Command line entry point: ``libre-panel <command>``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from libre_panel import __version__
from libre_panel.config import ConfigError, config_dir, load_config, write_default_config
from libre_panel.devices.base import DeviceError


def _cmd_run(args: argparse.Namespace) -> int:
    from libre_panel.app import run
    from libre_panel.instance import InstanceLock

    config = load_config(args.config)
    if args.driver:
        config.device.driver = args.driver
    if args.theme:
        config.theme = args.theme
    with InstanceLock():
        try:
            run(config, once=args.once)
        except KeyboardInterrupt:
            pass
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
        print(f"Libre Panel is already running. Theme editor: {url}")
        if open_editor:
            webbrowser.open(url)
        return 0
    try:
        log_path = _log_file() if use_icon else None
        app = BackgroundApp(args.config, port=args.port)
        return run_app(app, use_icon=use_icon, open_editor=open_editor, log_path=log_path)
    finally:
        lock.release()


def _cmd_start(args: argparse.Namespace) -> int:
    """Everything at once in a terminal: drive the panel and open the editor."""
    return _background(args, use_icon=False, open_editor=not args.no_browser)


def _cmd_tray(args: argparse.Namespace) -> int:
    return _background(args, use_icon=not args.no_icon, open_editor=not args.background)


def _cmd_autostart(args: argparse.Namespace) -> int:
    from libre_panel.autostart import Autostart, launch_command

    autostart = Autostart()
    if args.action == "enable":
        where = autostart.enable(launch_command(args.config))
        print(f"Libre Panel starts in the background when you log in ({where}).")
    elif args.action == "disable":
        print("Autostart removed." if autostart.disable() else "Autostart was not enabled.")
    else:
        state = "enabled" if autostart.is_enabled() else "disabled"
        print(f"Autostart is {state} ({autostart.location()}).")
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
            print(f"Libre Panel is running. Theme editor: {url}")
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
        report = run_doctor(args.report, ask_questions=not args.no_questions, frames=args.frames)
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
    from libre_panel.instance import AlreadyRunning
    from libre_panel.theme.model import ThemeError
    from libre_panel.weather.open_meteo import WeatherError

    try:
        return args.func(args)
    except AlreadyRunning as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (ConfigError, ThemeError, DeviceError, WeatherError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
