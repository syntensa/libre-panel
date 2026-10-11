"""The Control page: the panel's picture, how it runs, and what can be done about it.

Everything the page shows comes from the background app through ``PageContext`` (the
shown frame, ``status()`` with the main loop's numbers, ``sources()``, the services); in a
plain ``libre-panel editor`` it says so and keeps what works without a panel: the theme
per mode, which services are switched on, and the log. What it changes goes into
config.toml, so it is kept, and a running Libre Panel follows at once.

The routes are a JSON API of their own (docs/ARCHITECTURE.md, "The console API"): one for
each thing a screen shows or does, so a screen of any design can use them. ``status``
gathers the parts a screen polls together.
"""

from __future__ import annotations

import io
import sys
from datetime import datetime
from typing import Any

from PIL import Image

from libre_panel.config import Config, ConfigError, load_config, log_file, set_config_value
from libre_panel.i18n import t
from libre_panel.pages.base import ConsolePage, Refused, Request
from libre_panel.plugins.host import LEFT_OUT, OFF_IN_THEME
from libre_panel.plugins.loader import registry
from libre_panel.services.game import MODE as GAME_MODE
from libre_panel.theme.model import ThemeError, find_theme, list_themes

LIVE_WIDTH = 960  # the live picture is this wide unless asked otherwise
JPEG_QUALITY = 78
PICTURES = {"jpeg": "image/jpeg", "png": "image/png"}  # the formats of the live picture
RESTARTABLE = ("running", "stopped", "failed")  # a service in these states can be restarted


def encode(frame: Image.Image, width: int, kind: str = "jpeg") -> bytes:
    """``frame`` as a JPEG or PNG ``width`` pixels wide (a smaller frame stays as it is)."""
    if width < frame.width:
        height = max(1, round(frame.height * width / frame.width))
        frame = frame.resize((width, height), Image.Resampling.BILINEAR)
    if frame.mode != "RGB":
        frame = frame.convert("RGB")
    buffer = io.BytesIO()
    if kind == "png":
        frame.save(buffer, format="PNG", compress_level=3)
    else:
        frame.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


def service_state(state: str | None, enabled: bool | None = None) -> tuple[str, str]:
    """What a screen says about a service, as ``(code, detail)``, from what the service
    manager says (``state``; None when it does not know the service) and from config.toml
    (``enabled``; None when the file cannot be read: then the manager is all there is).

    The codes: ``running``, ``stopped``, ``failed`` (``detail`` is the reason),
    ``not-installed``, ``off`` (not enabled), and ``pending``: config.toml says one thing
    and the app has not followed yet (it does within a second or two)."""
    if enabled is None:
        enabled = state is not None
    if state is None:
        return ("pending" if enabled else "off"), ""
    if not enabled:
        return "pending", ""  # it still runs; the app is about to stop it
    if state.startswith("failed: "):
        return "failed", state[len("failed: ") :]
    return state.replace(" ", "-"), ""


def with_service(enabled: list[str], name: str, on: bool) -> list[str]:
    """The list ``services.enabled`` with ``name`` in it (``on``) or without it; the order
    of the others stays (it is the order the services start in)."""
    if on:
        return list(enabled) if name in enabled else [*enabled, name]
    return [other for other in enabled if other != name]


def why_not(reason: str) -> str:
    """The reason a message does not show (``Delivery.reason``) in words."""
    if reason == OFF_IN_THEME:
        return t("the theme switches this kind off (toast.off)")
    if reason == LEFT_OUT:
        return t("the screen shows this kind itself")
    return t("no panel is connected")


def probes() -> dict[str, tuple[str, dict[str, Any]]]:
    """A test message of each kind: the name the page gives it and the arguments of
    ``notify``. They are made as the services make theirs (rank, time, the label and the
    bar a style draws), so the theme and the screen treat them alike."""
    test = t("Test message")

    def probe(kind: str, name: str, text: str = test, label: str = "", **options: Any):
        payload = {"label": label or name, **options.pop("payload", {})}
        return name, {"text": text, "kind": kind, "payload": payload, **options}

    time = datetime.now().strftime("%H:%M")
    return {
        "volume": probe("volume", t("Volume"), "42", rank=0, payload={"progress": 0.42}),
        "music": probe(
            "music",
            t("Music"),
            label=t("Now playing"),
            rank=1,
            seconds=5.0,
            icon="music",
            payload={"progress": 0.4},
        ),
        "warning": probe(
            "warning",
            t("Warning"),
            rank=2,
            seconds=5.0,
            icon="temperature",
            level="warning",
            payload={"progress": 0.9},
        ),
        "panel": probe(
            "panel", t("Panel"), t("Panel restarted {time}", time=time), rank=2, seconds=8.0
        ),
        "device": probe("device", t("Device"), rank=1, seconds=4.0, icon="usb"),
        "game": probe("game", t("Game"), label=t("Game mode"), rank=1, seconds=3.0, icon="gamepad"),
    }


def yes_no(value: bool | None) -> str:
    return t("unknown") if value is None else t("yes") if value else t("no")


def lhm_rows(status: dict[str, Any]) -> list[list[str]]:
    """How LibreHardwareMonitor is read and what keeps the CPU's sensors out of reach."""
    inside = status.get("mode") == "inprocess"
    rows = [[t("Mode"), t("inside Libre Panel") if inside else t("web server")]]
    if inside:
        rows.append([t("Administrator"), yes_no(status.get("elevated"))])
        ring0, pawnio = status.get("ring0"), status.get("pawnio")
        reach = t("unknown") if ring0 is None else t("reachable") if ring0 else t("out of reach")
        driver = t("not installed") if pawnio is None else pawnio or t("unknown")
        rows += [[t("CPU sensors"), reach], [t("PawnIO driver"), driver]]
    if status.get("error"):
        rows.append([t("Error"), str(status["error"])])
    return rows


class Control(ConsolePage):
    title = {"en": "Control", "de": "Steuerung"}
    routes = {
        ("GET", "status"): "status",
        ("GET", "panel"): "panel",
        ("GET", "live"): "live",
        ("GET", "sources"): "sources",
        ("GET", "services"): "services",
        ("GET", "probes"): "probes",
        ("GET", "themes"): "themes",
        ("GET", "log"): "log",
        ("POST", "probe"): "probe",
        ("POST", "services/enable"): "enable",
        ("POST", "services/restart"): "restart",
        ("POST", "theme"): "theme",
    }

    def __init__(self, context: Any) -> None:
        super().__init__(context)
        self._live: tuple[Image.Image, int, str, bytes] | None = None  # the last picture

    def _config(self) -> tuple[Config | None, str]:
        """config.toml as it is now, and what is wrong with it ("" when nothing)."""
        try:
            return load_config(self.context.config_path), ""
        except ConfigError as exc:
            return None, str(exc)

    # -- what a screen shows --------------------------------------------------------------

    def status(self, request: Request) -> dict[str, Any]:
        """Everything but the picture and the log lines, for a screen that polls once."""
        config, problem = self._config()
        return {
            "background": self.context.background,
            "panel": self.context.status(),
            "config_problem": problem,
            "services": self._services(config),
            "sources": self._sources(config),
            "probes": self._probes(),
            "themes": self._themes(config),
            "log": self._log_file(),
        }

    def panel(self, request: Request) -> dict[str, Any]:
        """The panel's state and its numbers: cheap, for polling every second."""
        return {"background": self.context.background, "panel": self.context.status()}

    def sources(self, request: Request) -> dict[str, Any]:
        config, _ = self._config()
        return {"background": self.context.background, "sources": self._sources(config)}

    def services(self, request: Request) -> dict[str, Any]:
        config, problem = self._config()
        return {
            "background": self.context.background,
            "services": self._services(config),
            "config_problem": problem,
        }

    def probes(self, request: Request) -> dict[str, Any]:
        return {"probes": self._probes()}

    def themes(self, request: Request) -> dict[str, Any]:
        config, problem = self._config()
        return {**self._themes(config), "config_problem": problem}

    def live(self, request: Request):
        frame = self.context.shown_frame()
        if frame is None:
            return 404, {"error": t("no picture yet")}
        kind = request.param("format", "jpeg").lower()
        if kind not in PICTURES:
            raise Refused(t("format is jpeg or png"))
        width = request.integer("w", LIVE_WIDTH, 160, 1920)
        made = self._live
        if made is None or made[0] is not frame or made[1:3] != (width, kind):
            made = self._live = (frame, width, kind, encode(frame, width, kind))
        return 200, made[3], PICTURES[kind]

    def log(self, request: Request) -> dict[str, Any]:
        lines = self.context.log_tail(request.integer("lines", 60, 1, 1000))
        return {"lines": lines, **self._log_file()}

    # -- the parts, shared by the single routes and ``status`` --------------------------------

    @staticmethod
    def _log_file() -> dict[str, Any]:
        return {"path": str(log_file()), "exists": log_file().is_file()}

    @staticmethod
    def _probes() -> list[dict[str, str]]:
        return [{"kind": kind, "label": name} for kind, (name, _) in probes().items()]

    def _themes(self, config: Config | None) -> dict[str, Any]:
        panel = self.context.status()
        game = config.modes.get(GAME_MODE) if config else None
        return {
            "available": [theme["id"] for theme in list_themes()],
            "normal": config.theme if config else None,
            "game": (game.theme or "") if game else "",  # "": the normal mode's
            "shown": panel["theme"] if panel else None,
            "mode": panel["mode"] if panel else None,
        }

    def _service(self, name: str, enabled: bool, applied: dict[str, str]) -> dict[str, Any]:
        """One service: ``enabled`` is what config.toml says, ``applied`` the manager's."""
        if self.context.background:
            code, detail = service_state(applied.get(name), enabled)
        else:
            code, detail = "unknown", ""  # this editor does not run services
        return {
            "name": name,
            "state": code,
            "detail": detail,
            "enabled": enabled,
            "installed": name in registry().names("services"),
            "restartable": code in RESTARTABLE,
        }

    def _services(self, config: Config | None) -> list[dict[str, Any]]:
        applied = self.context.services()
        enabled = set(config.services.enabled) if config else set(applied)
        names = sorted({*registry().names("services"), *applied, *enabled})
        return [self._service(name, name in enabled, applied) for name in names]

    def _sources(self, config: Config | None) -> list[dict[str, Any]]:
        from libre_panel.sensors.librehardwaremonitor import DEFAULT_URL, LhmStatus, hardware_hint

        sources = self.context.sources()
        snapshot = self.context.snapshot()
        found = []
        for source in sources:
            status, rows, hint = source["status"], [], None
            if status is not None and source["name"] == "librehardwaremonitor":
                rows = lhm_rows(status)
                if snapshot is not None and config is not None:
                    options = config.sensors.options.get("librehardwaremonitor", {})
                    hint = hardware_hint(
                        snapshot.readings,
                        [s["name"] for s in sources],
                        options.get("url", DEFAULT_URL),
                        sys.platform,
                        LhmStatus(**status),
                    )
            elif status is not None:
                rows = [[k.replace("_", " "), str(v)] for k, v in status.items() if v is not None]
            problem = bool(hint) or bool(status and status.get("error"))
            found.append(
                {
                    "name": source["name"],
                    "readings": source["readings"],
                    "status": status,
                    "details": rows,
                    "hint": hint,
                    "problem": problem,
                }
            )
        return found

    # -- what a screen does -----------------------------------------------------------------

    def probe(self, request: Request) -> dict[str, Any]:
        kind = request.text("kind")
        made = probes().get(kind)
        if made is None:
            raise Refused(t("unknown kind of message: {kind}", kind=kind))
        name, options = made
        delivery = self.context.notify(**options)
        if delivery.shown:
            message = t("Shown on the panel.")
        else:
            message = t("Not shown: {why}.", why=why_not(delivery.reason))
        return {
            "kind": kind,
            "name": name,
            "shown": delivery.shown,
            "reason": delivery.reason,
            "message": message,
        }

    def enable(self, request: Request) -> dict[str, Any]:
        """Switch a service on or off in config.toml (``services.enabled``); the running
        app follows within a second or two, until then its state is ``pending``."""
        name, on = request.text("service"), request.body.get("enabled")
        if not isinstance(on, bool):
            raise Refused(t("enabled is on or off"))
        config, problem = self._config()
        if config is None:
            raise Refused(problem, 409)
        if on and name not in registry().names("services"):
            raise Refused(t("{name} is not an installed service", name=name), 404)
        names = list(config.services.enabled)
        if on != (name in names):  # asked twice, written once
            names = with_service(names, name, on)
            try:
                set_config_value("services.enabled", names, self.context.config_path)
            except (ConfigError, OSError) as exc:
                raise Refused(str(exc), 409) from exc
        row = self._service(name, on, self.context.services())
        return {"service": name, "enabled": on, "state": row["state"], "detail": row["detail"]}

    def restart(self, request: Request) -> dict[str, Any]:
        name = request.text("service")
        if not self.context.background:
            raise Refused(t("This editor does not run the services."), 404)
        try:
            state = self.context.restart_service(name)
        except LookupError:
            raise Refused(t("{name} is not enabled in config.toml", name=name), 404) from None
        code, detail = service_state(state, True)
        return {"service": name, "state": code, "detail": detail}

    def theme(self, request: Request) -> dict[str, Any]:
        mode, theme = request.text("mode"), request.body.get("theme")
        if mode not in ("normal", "game") or not isinstance(theme, str):
            raise Refused(t("mode is normal or game, and theme a name"))
        if theme or mode == "normal":  # the game mode may name none: it shows the normal one
            try:
                find_theme(theme)
            except ThemeError:
                raise Refused(t("Theme {theme} not found", theme=theme)) from None
        key = "theme" if mode == "normal" else f"modes.{GAME_MODE}.theme"
        try:
            set_config_value(key, theme, self.context.config_path)
        except (ConfigError, OSError) as exc:
            raise Refused(str(exc), 409) from exc
        return {"mode": mode, "theme": theme}
