"""Settings → Panel → Check the colours, run by the app (``/api/colour-check``).

- **The check** (devices/colour_check.py) in a thread of its own, with the settings, the frame
  size and the range of the panel the app is about (the header's choice): its model as it runs,
  else the one on the bus, else the one chosen in Settings, else the 9.2"; its encoder settings
  as the display takes them; limited or full range as [device] video_range has it for that
  model. A card's encoder converting the colours itself is compared with the processor's
  conversion; what that conversion costs a frame is measured too. The panels are not touched.
  Each verdict on a card's encoder is kept for the automatic choice (devices/card_choice.py),
  which the display follows at its next frame.
- **Who converts for that panel** (:meth:`ColourCheck.choice`): the encoder it runs (its
  counters' ``encoder``, "h264_amf, rgb0" or "h264_amf, rgb0, card"), the one "auto" picks and
  why it passed over a card's encoder (the last line ffmpeg said in the probe,
  :meth:`ColourCheck.auto`), who converts and why: the card where a check found it identical
  (and when), else the processor with the reason (the card's colours differ, it gives no picture
  converting itself, it makes limited range only on a panel in full range, the check is still to
  come), or video.pixels forcing a way, with the way back to the automatic choice.
- **The A/B on that panel** (colour_show.py): for 30 s its test picture, made two ways in turn:
  converted by the card and by the processor (for a card's encoder that can), or in limited and
  in full range (*Is black too light on the panel? Compare*; the person's answer is written as
  that panel's video_range by the card's buttons, through the settings). Ended in a ``finally``
  whatever happens, and when the app closes.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from libre_panel import colour_show
from libre_panel.config import ConfigError, load_config
from libre_panel.devices import card_choice, colour_check, h264, video_state
from libre_panel.devices.base import DeviceError
from libre_panel.devices.h264 import ffmpeg_command
from libre_panel.devices.models import PanelModel, find_model, get_model

log = logging.getLogger(__name__)

DEFAULT_MODEL = "turing-9.2-usb"  # the size of the picture where no panel says its own
WAIT_S = 30.0  # the person's check waits this long for a check in the background to end


class ColourError(ValueError):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def in_use_of(text: str | None) -> tuple[str, str, bool] | None:
    """The encoder, its feed and whether the card converts, from the counters' ``encoder``
    ("h264_amf, rgb0", "h264_amf, rgb0, card")."""
    parts = [part.strip() for part in (text or "").split(",")]
    if len(parts) < 2 or not parts[0] or parts[1] not in ("rgb0", "rgb24"):
        return None
    return parts[0], parts[1], parts[2:3] == ["card"]


def why_of(encoder: str, pixels: str, full: bool, kept: dict | None, refused: bool) -> str:
    """Why the card converts for ``encoder`` or not, the reason the card shows:

    "x264": an encoder of the processor (it takes YUV only); "limited-only": the panel shows
    full range and the card makes limited range only with this encoder; "refused": it gives no
    picture converting the frames itself; "card" and "rgb": video.pixels forces that way; else
    for "auto" the verdict ``kept`` (card_choice.py): "identical" (the card converts), "differs"
    (the processor), or "unchecked" (none yet: the processor)."""
    if encoder not in h264.CARD_ENCODERS:
        return "x264"
    if not card_choice.can(encoder, full):
        return "limited-only"
    if refused or (kept is not None and kept.get("state") == "refused"):
        return "refused"
    if pixels in ("card", "rgb"):
        return pixels
    if kept is None:
        return "unchecked"
    return "identical" if kept.get("card") else "differs"


def way_of(why: str, pixels: str) -> str:
    """Who converts for that reason (:func:`why_of`): "card", "processor" or "rgb" (the
    processor, fed three bytes a pixel)."""
    if why in ("card", "identical"):
        return "card"
    return "rgb" if pixels == "rgb" else "processor"


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
        self.saving_ms: float | None = None  # what the processor's conversion costs a frame
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.show_error = ""
        self.show_ended = ""
        self.show_kind = ""  # of the last A/B begun: "feed" or "range" (colour_show.WAYS)
        self.show_panel = 0
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

    def _full(self, config: Any, model: PanelModel | None) -> bool:
        """Whether the panel gets its video in full range ([device] video_range, for "auto" as
        its model is known to show it)."""
        from libre_panel.devices.turzx_video import full_range

        return full_range(config.device.video_range, model)

    def _model(self, number: int, config: Any) -> PanelModel | None:
        """The model of panel ``number`` as it runs, else as Settings name it (no look at the
        bus: the card asks every second)."""
        status = self._status(number)
        return find_model(getattr(status, "model", "") or "") or find_model(config.device.model)

    def _picture(self, number: int, config: Any) -> dict[str, Any]:
        """The model, the frame size and the range of panel ``number``: as it runs, else the
        panel on the bus (the first), else the one chosen in Settings, else the 9.2"
        (landscape)."""
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
        return {"panel": number, "model": model.id, "size": [int(size[0]), int(size[1])],
                "full": self._full(config, model)}  # fmt: skip

    # -- the check -----------------------------------------------------------------------------

    def start_check(self, number: int) -> None:
        with self._lock:
            if self.running:
                raise ColourError("the colour check is running")
            config = self._config(number)
            video = config.device.video
            picture = self._picture(number, config)
            self.results, self.error, self.ended, self.saving_ms = [], "", "", None
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
        full = picture["full"]
        base = video_settings(video)

        def settings(encoder: str, pixels: str) -> h264.EncoderSettings:
            return resolved(base, picture["model"], encoder, pixels, full)

        def done(result: dict[str, Any]) -> None:
            self.results.append(result)
            card_choice.record(ffmpeg, result, full)  # (the automatic choice follows it)

        try:
            with colour_check.exclusive(WAIT_S) as free:
                if not free:
                    self.error = "another colour check runs; try again in a moment"
                    return
                log.info("colour check: a %dx%d test picture (%s, %s range)", *size,
                         picture["model"], "full" if full else "limited")  # fmt: skip
                works = self.works or h264.encoder_works  # (the probe "auto" goes by)
                colour_check.check(ffmpeg, settings, size, rotation_of(size), works=works,
                                   command=self.command, stop=stop, done=done,
                                   full=full)  # fmt: skip
            if not stop.is_set() and any(
                r.get("compared", [""])[0] == "card" and r["state"] == "done" for r in self.results
            ):  # what the card's own conversion saves
                self.saving_ms = colour_check.conversion_ms(ffmpeg, size, rotation_of(size))
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
        return self._running(number) is not None

    def _running(self, number: int) -> tuple[str, str, bool] | None:
        """The encoder panel ``number`` runs, its feed and whether the card converts: its
        counters' while it streams video, else None."""
        stats = getattr(self._status(number), "stats", None) or {}
        return in_use_of(stats.get("encoder")) if stats.get("streaming") else None

    def start_show(self, number: int, kind: str = "feed") -> None:
        """Panel ``number``'s test picture, A and B of ``kind`` in turn: "feed", the card's
        conversion against the processor's (a card's encoder that can only); "range", limited
        against full range (any encoder)."""
        with self._lock:
            if self.controls is None:
                raise ColourError("showing it on the panel needs the running app")
            if kind not in colour_show.WAYS:
                raise ColourError(f"kind: one of {', '.join(colour_show.WAYS)}", 400)
            config = self._config(number)  # (a panel that is there)
            thread = self._show_thread
            if thread is not None and thread.is_alive():
                raise ColourError("a panel shows the test picture already")
            running = self._running(number)
            if running is None:
                raise ColourError(f"panel {number} shows no video now")
            full = self._full(config, self._model(number, config))
            if kind == "feed" and not card_choice.can(running[0], full):  # (A, B the same)
                raise ColourError(f"panel {number} runs {running[0]}: the processor converts")
            self.show_error, self.show_ended = "", ""
            self.show_kind, self.show_panel = kind, number
            try:  # at once: the card says so as the click is answered
                colour_show.begin(number, kind=kind)
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

    def choice(self, number: int, config: Any, encoder: str | None) -> dict[str, Any]:
        """Who converts the colours for ``encoder`` on panel ``number`` and why
        (:func:`why_of`), with the verdict kept for it (card_choice.py: ``verdict``, ``worst``,
        ``when``, ``by``) and whether its check in the background runs now."""
        video = config.device.video
        model = self._model(number, config)
        full = self._full(config, model)
        found: dict[str, Any] = {"encoder": encoder, "pixels": video.pixels, "full": full,
                                 "range": config.device.video_range,
                                 "model_range": model.video_range if model else "limited",
                                 "way": None, "why": "", "verdict": None, "worst": None,
                                 "when": None, "by": None, "checking": False}  # fmt: skip
        if not encoder:
            return found
        kept, refused = None, False
        if encoder in h264.CARD_ENCODERS:
            try:
                ffmpeg = h264.find_ffmpeg(video.ffmpeg)
            except DeviceError:
                ffmpeg = None
            if ffmpeg is not None:
                refused = video_state.refused(ffmpeg, encoder)
                if card_choice.can(encoder, full):
                    kept = card_choice.verdict(ffmpeg, encoder, full)
                    found["checking"] = card_choice.checking(ffmpeg, encoder, full)
        why = why_of(encoder, video.pixels, full, kept, refused)
        found.update(why=why, way=way_of(why, video.pixels))
        if kept is not None:
            found.update({key: kept.get(key) for key in ("verdict", "worst", "when", "by")})
        return found

    def state(self, number: int = 1) -> dict[str, Any]:
        config = self._config(number)
        video = config.device.video
        running = self._running(number)
        auto = self.auto(video)
        encoder = running[0] if running is not None else auto["picked"]
        choice = self.choice(number, config, encoder)
        in_use = None
        if running is not None:
            encoder, feed, card = running
            in_use = {"encoder": encoder, "feed": feed, "card": card, "running": True}
        elif encoder:  # what its settings would start, as the probe found the encoders
            feed = "rgb24" if choice["way"] == "rgb" else "rgb0"
            in_use = {"encoder": encoder, "feed": feed, "card": choice["way"] == "card",
                      "running": False}  # fmt: skip
        if self.controls is None:
            ab = "app"
        elif running is None:
            ab = "video"
        elif choice["why"] in ("x264", "limited-only"):  # (A and B would be the same)
            ab = choice["why"]
        else:
            ab = ""
        showing = colour_show.state()
        thread = self._show_thread
        return {
            "panel": number,
            "app": self.controls is not None,
            "video": running is not None,
            "ab": ab,  # why the A/B of the card's conversion is not offered ("": it is)
            "pixels": video.pixels,
            "auto": auto,
            "in_use": in_use,
            "choice": choice,
            "check": {
                "running": self.running,
                "results": list(self.results),
                "error": self.error,
                "ended": self.ended,
                "picture": self.picture,
                "saving_ms": self.saving_ms,
                "encoders": list(colour_check.ENCODERS),
            },
            "show": {
                "running": thread is not None and thread.is_alive(),
                "panel": showing["panel"] if showing else None,
                "kind": showing["kind"] if showing else self.show_kind,
                "of": self.show_panel,  # the panel of the last A/B begun
                "letter": showing["letter"] if showing else None,
                "value": showing["value"] if showing else None,
                "left_s": round(showing["left_s"]) if showing else 0,
                "seconds": colour_show.SHOW_S,
                "switch_s": colour_show.SWITCH_S,
                "error": self.show_error,
                "ended": self.show_ended,
            },
        }
