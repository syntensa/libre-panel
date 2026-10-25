"""The panel test in the app (Live → Diagnose): the checks of ``libre_panel.doctor``, run in a
thread of their own while the panel loop gives the panel to them (``PanelService.hold``), their
questions answered with the app's buttons.

The app asks for the state (:meth:`Session.status`) and answers the question it shows
(:meth:`Session.answer`): each has a key (``card-landscape``, ``ruler-top``, ``brightness`` ...)
the app knows it by and says in the person's language, and a kind: ``confirm`` (seen or not, and
what is seen instead), ``choose`` (one of ``options``), ``number`` (or none) and ``wait`` (ready).
A question nobody answers for ten minutes ends the test, as does :meth:`Session.cancel`; the
panel goes back to the loop either way. The test sends what the doctor sends in a terminal: the
handshake, whole frames and the brightness.
"""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from libre_panel.config import ConfigError, load_config

log = logging.getLogger(__name__)

QUESTION_TIMEOUT_S = 600.0
# whole frames for the speed: fewer than in a terminal, as a slow serial panel takes up to half
# a minute for one, and the app waits with the person
FRAMES = 8
REPORT_FORM = "https://github.com/syntensa/libre-panel/issues/new?template=panel_support.yml"
WORKS = "The panel shows the theme correctly"  # the form's answer for a panel that works


class Cancelled(Exception):
    """The test was ended from the app (or nobody answered)."""


class SessionError(Exception):
    """Something the app asked for that cannot be done now."""


class AppQuestions:
    """The doctor's questions (doctor.Terminal's methods), put to the app: each waits for its
    answer from :meth:`Session.answer`."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def confirm(self, key: str, question: str) -> tuple[bool, str]:
        answer = self.session.ask({"key": key, "kind": "confirm", "text": question})
        return bool(answer.get("yes")), str(answer.get("note") or "").strip()[:500]

    def choose(self, key: str, question: str, options: list[str]) -> int | None:
        answer = self.session.ask({"key": key, "kind": "choose", "text": question,
                                   "options": options})  # fmt: skip
        index = answer.get("index")
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(options):
            return index
        return None

    def number(self, key: str, question: str) -> int | None:
        answer = self.session.ask({"key": key, "kind": "number", "text": question})
        value = answer.get("number")
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10_000:
            return value
        return None

    def wait(self, key: str, text: str) -> None:
        self.session.ask({"key": key, "kind": "wait", "text": text.strip()})


class Session:
    """One panel test at a time. ``panel`` is the app's PanelService (None in a plain editor,
    where no loop holds the panel); ``make_doctor`` makes the doctor (tests give it a panel)."""

    def __init__(self, panel: Any = None, config_path: Path | None = None,
                 make_doctor: Any = None, devices: Any = None,
                 load: Any = None) -> None:  # fmt: skip
        self.panel = panel
        self.config_path = config_path
        self.make_doctor = make_doctor
        self.devices = devices  # the list of what is plugged in, for the report form
        self.load = load  # what Libre Panel costs the PC, as text (load_report.py), for the report
        self._load_text = ""  # ... as it was when the test began
        self._lock = threading.Lock()
        self._answers: queue.Queue[dict[str, Any]] = queue.Queue()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._doctor: Any = None
        self.state = "idle"  # running, asking, done, cancelled, failed
        self.question: dict[str, Any] | None = None
        self._asked = 0  # the number of the last question: an answer says which it answers
        self.error = ""
        self.report: Any = None

    # -- the app's side ---------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        doctor, report = self._doctor, self.report
        steps = list(report.steps if report is not None else doctor.report.steps if doctor else [])
        out: dict[str, Any] = {
            "state": self.state,
            "question": self.question,
            "error": self.error,
            "steps": [{"name": s.name, "result": s.result, "detail": s.detail} for s in steps],
        }
        if report is not None:
            out["report"] = {
                "text": self._text(report),
                "passed": report.passed,
                "failed": report.failed,
                "summary": report.summary,
                "model": {"id": report.model.id, "label": report.model.label}
                if report.model is not None
                else None,
                "form": self._form(report),
            }
        return out

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.state in ("running", "asking"):
                raise SessionError("the panel test is running already")
            self._answers = queue.Queue()
            self._cancel = threading.Event()
            self.state, self.question, self.error, self.report = "running", None, "", None
            self._doctor, self._load_text = None, ""
            self._thread = threading.Thread(target=self._run, name="panel test", daemon=True)
            self._thread.start()
        return self.status()

    def answer(self, answer: Any) -> dict[str, Any]:
        """The answer to the question shown; with ``"id"``, only to that one (a second click
        on the same button answers nothing)."""
        if not isinstance(answer, dict):
            raise SessionError("an answer is an object")
        with self._lock:
            question = self.question
            if self.state != "asking" or question is None:
                raise SessionError("the panel test asks nothing now")
            if "id" in answer and answer["id"] != question["id"]:
                raise SessionError("that question was answered already")
            self.question, self.state = None, "running"
            self._answers.put(answer)
        return self.status()

    def cancel(self) -> dict[str, Any]:
        if self.state in ("running", "asking"):
            self._cancel.set()
            self._answers.put({})
        return self.status()

    def wait(self, timeout: float = 30.0) -> None:
        """Until the test is over (tests)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # -- the doctor's side ------------------------------------------------------------------------

    def ask(self, question: dict[str, Any]) -> dict[str, Any]:
        if self._cancel.is_set():
            raise Cancelled
        with self._lock:
            self._asked += 1
            self.question, self.state = {**question, "id": self._asked}, "asking"
        try:
            answer = self._answers.get(timeout=QUESTION_TIMEOUT_S)
        except queue.Empty:
            raise Cancelled from None
        finally:
            with self._lock:
                self.question = None
                if self.state == "asking":
                    self.state = "running"
        if self._cancel.is_set():
            raise Cancelled
        return answer

    def _run(self) -> None:
        from libre_panel.doctor import Doctor

        ran = held = False
        try:
            self._measure_load()  # before the test holds the panel: Libre Panel as it runs
            if self.panel is not None:
                ran, held = self.panel.hold(), True
            try:
                device = load_config(self.config_path).device
                options = {"brightness": device.brightness, "port": device.port,
                           "model_id": "" if device.model == "auto" else device.model,
                           "video": device.video}  # fmt: skip
            except ConfigError:
                options = {}
            make = self.make_doctor or Doctor
            self._doctor = make(questions=AppQuestions(self), say=self._say, frames=FRAMES,
                                **options)  # fmt: skip
            self.report = self._doctor.run()
            self.state = "done"
            log.info("panel test: %s", self.report.summary)
        except Cancelled:
            self.state = "cancelled"
            log.info("panel test ended before its end")
        except Exception as exc:  # noqa: BLE001 - the panel goes back to the loop in any case
            log.exception("panel test failed")
            self.state, self.error = "failed", str(exc) or type(exc).__name__
        finally:
            self.question = None
            if held:
                self.panel.release(ran)

    def _measure_load(self) -> None:
        if self.load is None:
            return
        try:
            self._load_text = self.load()
        except Exception:  # noqa: BLE001 - the load is a help in the report, not a must
            log.debug("no load for the report", exc_info=True)

    def _text(self, report: Any) -> str:
        """The doctor's report, and what Libre Panel cost the PC before the test."""
        text = report.text()
        return f"{text}\n{self._load_text}" if self._load_text else text

    @staticmethod
    def _say(text: str) -> None:
        if text.strip():
            log.debug("panel test: %s", text.strip())

    def _form(self, report: Any) -> str:
        """The report form on GitHub with this test filled in (the person sends it)."""
        fields = {"doctor": self._text(report)}
        if report.model is not None:
            fields["product"] = report.model.label
        if report.passed:
            fields["result"] = WORKS
        if self.devices is not None:
            try:
                fields["devices"] = self.devices()
            except Exception:  # noqa: BLE001 - the list is a help, not a must
                log.debug("no list of devices for the report", exc_info=True)
        return f"{REPORT_FORM}&{urlencode(fields)}"
