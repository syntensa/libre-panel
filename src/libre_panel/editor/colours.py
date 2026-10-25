"""Settings → Panel → Check the colours, run by the app (``/api/colour-check``).

- **The check** (devices/colour_check.py) in a thread of its own, with the settings and the
  frame size of the panel the app is about (the header's choice): its model as it runs, else
  the one on the bus, else the one chosen in Settings, else the 9.2"; its encoder settings as
  the display takes them. The panels are not touched.
- **What it means for that panel**: the encoder it runs and what it feeds it (its counters'
  ``encoder``, "h264_amf, rgb0"), the one "auto" picks and why it passed over a card's encoder
  (the last line ffmpeg said in the probe, :meth:`ColourCheck.auto`), and from the check's
  verdict on the encoder in use the one thing to
  do (:func:`advice`): nothing, or ``video.pixels = "rgb"`` (the processor's conversion, written
  by the card's button through the settings; the display starts ffmpeg anew by itself), or back.
- **The A/B on that panel** (colour_show.py): for 30 s its test picture, fed the new way and the
  old way in turn; ended in a ``finally`` whatever happens, and when the app closes.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from libre_panel import colour_show
from libre_panel.config import ConfigError, load_config
from libre_panel.devices import colour_check, h264, video_state
from libre_panel.devices.base import DeviceError
from libre_panel.devices.h264 import PASS_THROUGH, ffmpeg_command
from libre_panel.devices.models import find_model, get_model

log = logging.getLogger(__name__)

DEFAULT_MODEL = "turing-9.2-usb"  # the size of the picture where no panel says its own
SHIFTS = ("level", "matrix", "other")


class ColourError(ValueError):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def in_use_of(text: str | None) -> tuple[str, str] | None:
    """The encoder and its feed from the counters' ``encoder`` ("h264_amf, rgb0")."""
    parts = [part.strip() for part in (text or "").split(",")]
    if len(parts) < 2 or not parts[0] or parts[1] not in ("rgb0", "rgb24"):
        return None
    return parts[0], parts[1]


def by_card(encoder: str, feed: str) -> bool:
    """Whether the colours of ``encoder`` fed ``feed`` are converted on the graphics card."""
    return encoder in PASS_THROUGH and feed == "rgb0"


def advice(in_use: dict[str, Any] | None, results: list[dict[str, Any]], pixels: str) -> str:
    """The one thing to do, by the check's verdict on the encoder in use:

    "back": the processor converts for the cards (``video.pixels = "rgb"``): back to the card's
    conversion offered; "processor": that encoder takes YUV only, ffmpeg converts either way;
    "refused": a card's encoder that took no four bytes a pixel, converted for by itself;
    "nothing": the card converts as the processor does; "shift": it does not, the processor's
    conversion offered; "" while the check has nothing to say on it."""
    if pixels == "rgb":
        return "back"
    if in_use is None:
        return ""
    encoder = in_use["encoder"]
    if encoder not in PASS_THROUGH:
        return "processor"
    if in_use["feed"] != "rgb0":
        return "refused"
    result = next((r for r in results if r["encoder"] == encoder), None)
    if result is None:
        return ""
    if result["state"] == "refused":
        return "refused"
    if result.get("verdict") == "identical":
        return "nothing"
    if result.get("verdict") in SHIFTS:
        return "shift"
    return ""


class ColourCheck:
    """The check and the A/B for the app: :meth:`start_check`, :meth:`start_show`,
    :meth:`stop_show`, and :meth:`state` of a panel (1 the first, 2 the first beside it, ...).
    ``controls`` is the running app (None in a plain editor: the check runs, the A/B does not).
    """

    TICK_S = 0.2  # how often the A/B looks whether it is over

    def __init__(
        self,
        config_path: Path | None,
        controls: Any = None,
        works: Callable[[str, str], bool] | None = None,
        command: colour_check.Command = ffmpeg_command,
    ) -> None:
        self.config_path = config_path
        self.controls = controls
        self.works = works  # (tests: which encoders of the card start here)
        self.command = command  # (tests: an ffmpeg that shifts the colours)
        self._lock = threading.Lock()
        self.running = False
        self.results: list[dict[str, Any]] = []
        self.error = ""
        self.ended = ""  # "done", "stopped", "" while it runs or never ran
        self.picture: dict[str, Any] = {}  # the panel, the model and the size it was made for
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.show_error = ""
        self.show_ended = ""
        self._show_stop = threading.Event()
        self._show_thread: threading.Thread | None = None

    # -- the panel the app is about ------------------------------------------------------------

    def _config(self, number: int) -> Any:
        try:
            config = load_config(self.config_path)
        except ConfigError as exc:
            raise ColourError(str(exc)) from exc
        if isinstance(number, bool) or not isinstance(number, int):
            raise ColourError("panel: expected a whole number", 400)
        if not 1 <= number <= 1 + len(config.panels):
            raise ColourError(f"panel {number}: there is no such panel", 400)
        return config if number == 1 else config.for_panel(number - 2)

    def _status(self, number: int) -> Any:
        status = getattr(getattr(self.controls, "panel", None), "status", None)
        if status is None or number == 1:
            return status
        return (getattr(status, "more", None) or {}).get(number - 2)

    def _picture(self, number: int, config: Any) -> dict[str, Any]:
        """The model and the frame size of panel ``number``: as it runs, else the panel on the
        bus (the first), else the one chosen in Settings, else the 9.2" (landscape)."""
        status = self._status(number)
        model = find_model(getattr(status, "model", "") or "")
        if model is None and number == 1:
            try:
                from libre_panel.devices.turzx_usb import present_model

                model = present_model(config.device.beside)
            except Exception:  # no USB library is no panel
                model = None
        model = model or find_model(config.device.model) or get_model(DEFAULT_MODEL)
        size = getattr(status, "size", None)
        if not size:
            size = model.size(config.device.orientation or "landscape")
        return {"panel": number, "model": model.id, "size": [int(size[0]), int(size[1])]}

    # -- the check -----------------------------------------------------------------------------

    def start_check(self, number: int) -> None:
        with self._lock:
            if self.running:
                raise ColourError("the colour check is running")
            config = self._config(number)
            video = config.device.video
            picture = self._picture(number, config)
            self.results, self.error, self.ended = [], "", ""
            self.picture = picture
            try:
                ffmpeg = h264.find_ffmpeg(video.ffmpeg)
            except DeviceError as exc:
                self.error, self.ended = str(exc), "done"
                return
            self._stop = threading.Event()
            self.running = True
            self._thread = threading.Thread(
                target=self._check,
                args=(ffmpeg, video, picture, self._stop),
                name="colour-check",
                daemon=True,
            )
            self._thread.start()

    def _check(self, ffmpeg: str, video: Any, picture: dict[str, Any], stop: threading.Event):
        from libre_panel.devices.turzx_video import resolved, rotation_of, video_settings

        size = (picture["size"][0], picture["size"][1])
        base = video_settings(video)

        def settings(encoder: str, pixels: str) -> h264.EncoderSettings:
            return resolved(base, picture["model"], encoder, pixels)

        try:
            log.info("colour check: a %dx%d test picture (%s)", *size, picture["model"])
            works = self.works or h264.encoder_works  # (the probe "auto" goes by)
            colour_check.check(ffmpeg, settings, size, rotation_of(size), works=works,
                               command=self.command, stop=stop,
                               done=self.results.append)  # fmt: skip
        except Exception as exc:  # the app shows it
            self.error = str(exc) or type(exc).__name__
            log.exception("the colour check stopped at a fault")
        finally:
            self.ended = "stopped" if stop.is_set() else "done"
            self.running = False

    # -- the A/B on the panel --------------------------------------------------------------------

    def shows_video(self, number: int) -> bool:
        """Whether panel ``number`` of the running app streams video now (its counters name
        the encoder only then)."""
        stats = getattr(self._status(number), "stats", None) or {}
        return bool(stats.get("streaming")) and in_use_of(stats.get("encoder")) is not None

    def start_show(self, number: int) -> None:
        with self._lock:
            if self.controls is None:
                raise ColourError("showing it on the panel needs the running app")
            self._config(number)  # (a panel that is there)
            thread = self._show_thread
            if thread is not None and thread.is_alive():
                raise ColourError("a panel shows the test picture already")
            if not self.shows_video(number):
                raise ColourError(f"panel {number} shows no video now")
            self.show_error, self.show_ended = "", ""
            try:  # at once: the card says so as the click is answered
                colour_show.begin(number)
            except Exception as exc:
                colour_show.end()
                raise ColourError(f"the test picture did not go to panel {number}: {exc}") from exc
            self._show_stop = threading.Event()
            self._show_thread = threading.Thread(
                target=self._show, args=(number, self._show_stop), name="colour-show", daemon=True
            )
            self._show_thread.start()

    def _show(self, number: int, stop: threading.Event) -> None:
        """The A/B begun (:meth:`start_show`) until it is over; the panel shows its theme
        again whatever happens on the way."""
        try:
            self._watch(stop)
        except Exception as exc:  # the panel shows its theme again all the same
            self.show_error = str(exc) or type(exc).__name__
            log.warning("colour check: the picture on panel %d ended at a fault: %s", number, exc)
        finally:
            colour_show.end()
            self.show_ended = "stopped" if stop.is_set() else "done"

    def _watch(self, stop: threading.Event) -> None:
        """Until the A/B's time is over, or it is stopped."""
        while colour_show.state() is not None and not stop.wait(self.TICK_S):
            pass

    def stop_show(self) -> None:
        self._show_stop.set()

    def stop_check(self) -> None:
        """The check stops: its ffmpeg under way is ended, the next encoder not begun."""
        self._stop.set()
        colour_check.kill_all()

    def close(self) -> None:
        """The app ends: the check stops, the panel shows its theme (and the marker goes)."""
        self.stop_check()
        self._show_stop.set()
        for thread in (self._thread, self._show_thread):
            if thread is not None:
                thread.join(5)

    # -- what the card shows ---------------------------------------------------------------------

    def auto(self, video: Any) -> dict[str, Any]:
        """What video.encoder starts: the one set, or what "auto" picks (the first of the card's
        encoders the probe found working, else x264) and each of the card's it passed over, with
        the last line ffmpeg said in the probe. Only answers the probe has given already (this
        run's, or the week's in video-state.json): none is asked for here; "picked" is None until
        they are all known (the check asks them)."""
        found: dict[str, Any] = {"set": video.encoder, "picked": None, "passed": []}
        if video.encoder != "auto":
            found["picked"] = video.encoder
            return found
        try:
            ffmpeg = h264.find_ffmpeg(video.ffmpeg)
        except DeviceError:
            return found
        for candidate in h264.AUTO_ENCODERS:
            works = (
                self.works(ffmpeg, candidate)
                if self.works is not None
                else h264.known_works(ffmpeg, candidate)
            )
            if works is None:
                return found  # not asked yet
            if works:
                found["picked"] = candidate
                return found
            found["passed"].append(
                {"encoder": candidate, "said": h264.probe_said(ffmpeg, candidate)}
            )
        found["picked"] = "libx264"
        return found

    def state(self, number: int = 1) -> dict[str, Any]:
        config = self._config(number)
        video = config.device.video
        status = self._status(number)
        stats = getattr(status, "stats", None) or {}
        running = in_use_of(stats.get("encoder")) if stats.get("streaming") else None
        auto = self.auto(video)
        picked = auto["picked"]
        in_use = None
        if running is not None:
            encoder, feed = running
            in_use = {"encoder": encoder, "feed": feed, "running": True}
        elif picked:  # what its settings would start, as the probe found the encoders
            refused = False
            if picked in PASS_THROUGH and video.pixels != "rgb":
                try:
                    refused = video_state.refused(h264.find_ffmpeg(video.ffmpeg), picked)
                except DeviceError:
                    refused = False
            feed = "rgb24" if video.pixels == "rgb" or refused else "rgb0"
            in_use = {"encoder": picked, "feed": feed, "running": False}
        if in_use is not None:
            in_use["card"] = by_card(in_use["encoder"], in_use["feed"])
        showing = colour_show.state()
        thread = self._show_thread
        return {
            "panel": number,
            "app": self.controls is not None,
            "video": self.shows_video(number),
            "pixels": video.pixels,
            "auto": auto,
            "in_use": in_use,
            "advice": advice(in_use, self.results, video.pixels),
            "check": {
                "running": self.running,
                "results": list(self.results),
                "error": self.error,
                "ended": self.ended,
                "picture": self.picture,
                "encoders": list(colour_check.ENCODERS),
            },
            "show": {
                "running": thread is not None and thread.is_alive(),
                "panel": showing["panel"] if showing else None,
                "letter": showing["letter"] if showing else None,
                "left_s": round(showing["left_s"]) if showing else 0,
                "seconds": colour_show.SHOW_S,
                "switch_s": colour_show.SWITCH_S,
                "error": self.show_error,
                "ended": self.show_ended,
            },
        }
