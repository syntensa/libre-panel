"""The Control page: the panel's picture, how it runs, and what can be done about it.

Everything the page shows comes from the background app through ``PageContext`` (the
shown frame, ``status()`` with the main loop's numbers, ``sources()``, the services); in a
plain ``libre-panel editor`` it says so and keeps what works without a panel: the theme
per mode, and the log. What it changes goes into config.toml, so it is kept, and a
running Libre Panel follows at once.
"""

from __future__ import annotations

import io
import sys
from datetime import datetime
from typing import Any

from PIL import Image

from libre_panel.config import ConfigError, load_config, log_file, set_config_value
from libre_panel.i18n import t
from libre_panel.pages.base import ConsolePage, Refused, Request
from libre_panel.plugins.host import LEFT_OUT, OFF_IN_THEME
from libre_panel.plugins.loader import registry
from libre_panel.services.game import MODE as GAME_MODE
from libre_panel.theme.model import ThemeError, find_theme, list_themes

LIVE_WIDTH = 960  # the live picture is this wide unless asked otherwise
JPEG_QUALITY = 78


def jpeg(frame: Image.Image, width: int) -> bytes:
    """``frame`` as a JPEG ``width`` pixels wide (a smaller frame stays as it is)."""
    if width < frame.width:
        height = max(1, round(frame.height * width / frame.width))
        frame = frame.resize((width, height), Image.Resampling.BILINEAR)
    buffer = io.BytesIO()
    frame.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


def service_state(state: str | None) -> tuple[str, str]:
    """What the service manager says as ``(code, detail)``: ``running``, ``stopped``,
    ``not-installed``, ``failed`` (with the reason) or ``off`` (not enabled)."""
    if state is None:
        return "off", ""
    if state.startswith("failed: "):
        return "failed", state[len("failed: ") :]
    return state.replace(" ", "-"), ""


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
        ("GET", "live"): "live",
        ("GET", "log"): "log",
        ("POST", "probe"): "probe",
        ("POST", "restart"): "restart",
        ("POST", "theme"): "theme",
    }

    def __init__(self, context: Any) -> None:
        super().__init__(context)
        self._live: tuple[Image.Image, int, bytes] | None = None  # the last picture, made once

    # -- what the page shows --------------------------------------------------------------

    def status(self, request: Request) -> dict[str, Any]:
        context = self.context
        try:
            config, problem = load_config(context.config_path), ""
        except ConfigError as exc:
            config, problem = None, str(exc)
        panel = context.status()
        background = context.background
        game = config.modes.get(GAME_MODE) if config else None
        return {
            "background": background,
            "panel": panel,
            "config_problem": problem,
            "services": self._services() if background else [],
            "sources": self._sources(config) if background else [],
            "probes": [{"kind": kind, "label": name} for kind, (name, _) in probes().items()],
            "themes": {
                "available": [theme["id"] for theme in list_themes()],
                "normal": config.theme if config else None,
                "game": (game.theme or "") if game else "",  # "": the normal mode's
                "shown": panel["theme"] if panel else None,
                "mode": panel["mode"] if panel else None,
            },
            "log": {"path": str(log_file()), "exists": log_file().is_file()},
        }

    def _services(self) -> list[dict[str, Any]]:
        states = self.context.services()
        names = sorted(set(states) | set(registry().names("services")))
        found = []
        for name in names:
            code, detail = service_state(states.get(name))
            found.append(
                {"name": name, "state": code, "detail": detail, "restartable": name in states}
            )
        return found

    def _sources(self, config: Any) -> list[dict[str, Any]]:
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
                    "details": rows,
                    "hint": hint,
                    "problem": problem,
                }
            )
        return found

    def live(self, request: Request):
        frame = self.context.shown_frame()
        if frame is None:
            return 404, {"error": t("no picture yet")}
        width = request.integer("w", LIVE_WIDTH, 160, 1920)
        made = self._live
        if made is None or made[0] is not frame or made[1] != width:
            made = self._live = (frame, width, jpeg(frame, width))
        return 200, made[2], "image/jpeg"

    def log(self, request: Request) -> dict[str, Any]:
        lines = self.context.log_tail(request.integer("lines", 60, 1, 1000))
        return {"lines": lines, "path": str(log_file()), "exists": log_file().is_file()}

    # -- what it does -------------------------------------------------------------------------

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

    def restart(self, request: Request) -> dict[str, Any]:
        name = request.text("service")
        if not self.context.background:
            raise Refused(t("This editor does not run the services."), 404)
        try:
            state = self.context.restart_service(name)
        except LookupError:
            raise Refused(t("{name} is not enabled in config.toml", name=name), 404) from None
        code, detail = service_state(state)
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
