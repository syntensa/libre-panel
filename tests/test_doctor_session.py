"""The panel test in the app (doctor_session.py), the log for the app (logview.py) and the
loop that gives its panel to the test (PanelService.hold), against the simulated USB panel."""

import time

import pytest

pytest.importorskip("Crypto")
pytest.importorskip("usb")

from fake_panel import FakePanel, transport_for  # noqa: E402

from libre_panel import logview  # noqa: E402
from libre_panel.devices import turzx_usb  # noqa: E402
from libre_panel.devices.base import DeviceError  # noqa: E402
from libre_panel.doctor_session import Session, SessionError  # noqa: E402


@pytest.fixture
def panel(monkeypatch):
    fake = FakePanel()

    def open_transport(pid=None):
        if fake.unplugged:
            raise DeviceError("no Turing/TURZX USB panel found")
        return transport_for(fake)

    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(open_transport))
    return fake


class Loop:
    """The app's panel loop, as far as the test sees it."""

    def __init__(self, running=True):
        self.running, self.calls = running, []

    def hold(self):
        self.calls.append("hold")
        ran, self.running = self.running, False
        return ran

    def release(self, resume):
        self.calls.append(("release", resume))
        self.running = resume


def until(session, *states, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        status = session.status()
        if status["state"] in states:
            return status
        time.sleep(0.02)
    raise AssertionError(f"still {session.status()['state']}")


def test_the_app_answers_the_doctors_questions_and_gets_the_panel_back(panel):
    loop = Loop()
    session = Session(loop, devices=lambda: 'Panel found: Turing 9.2" USB')
    session.start()
    with pytest.raises(SessionError):
        session.start()  # one at a time
    answers = {
        "card-landscape": {"yes": True},
        "card-portrait": {"yes": False, "note": "upside down"},
        "ruler-top": {"number": 20},
        "ruler-bottom": {"number": 2},
        "ruler-left": {"number": 2},
        "ruler-right": {},
        "brightness-watch": {},
        "brightness": {"yes": True},
    }
    asked = []
    while True:
        status = until(session, "asking", "done", "failed")
        if status["state"] != "asking":
            break
        question = status["question"]
        asked.append((question["key"], question["kind"]))
        session.answer({**answers[question["key"]], "id": question["id"]})
        with pytest.raises(SessionError):  # a second click on the same button answers nothing
            session.answer({**answers[question["key"]], "id": question["id"]})
    assert status["state"] == "done", status
    assert asked == [("card-landscape", "confirm"), ("card-portrait", "confirm"),
                     ("ruler-top", "number"), ("ruler-bottom", "number"),
                     ("ruler-left", "number"), ("ruler-right", "number"),
                     ("brightness-watch", "wait"), ("brightness", "confirm")]  # fmt: skip
    report = status["report"]
    assert report["failed"] and "upside down" in report["text"]
    assert "hidden: top 18 px, bottom 0 px, left 0 px, right ?" in report["text"]
    assert report["model"]["id"] == "turing-9.2-usb"
    assert "template=panel_support.yml" in report["form"] and "doctor=" in report["form"]
    assert "result=" not in report["form"]  # it did not pass: the person says what happens
    assert loop.calls == ["hold", ("release", True)] and loop.running


def test_a_panel_that_passes_is_reported_as_working(panel):
    session = Session(Loop(running=False))
    session.start()
    while (status := until(session, "asking", "done", "failed"))["state"] == "asking":
        kind = status["question"]["kind"]
        session.answer({"yes": True} if kind == "confirm" else {"number": 2} if kind == "number"
                       else {})  # fmt: skip
    assert status["report"]["passed"], status["report"]["text"]
    assert "result=The+panel+shows+the+theme+correctly" in status["report"]["form"]
    assert session.panel.calls == ["hold", ("release", False)]  # it did not run: it stays off


def test_the_test_can_be_ended_and_the_panel_goes_back(panel):
    loop = Loop()
    session = Session(loop)
    session.start()
    until(session, "asking")
    session.cancel()
    assert until(session, "cancelled")["state"] == "cancelled"
    session.wait()
    assert loop.calls == ["hold", ("release", True)]
    with pytest.raises(SessionError):
        session.answer({"yes": True})  # nothing asked any more


def test_without_a_panel_the_test_says_so(panel):
    panel.unplugged = True
    loop = Loop()
    session = Session(loop)
    session.start()
    status = until(session, "done", "failed")
    report = status["report"]
    assert report["failed"] and "no Turing/TURZX USB panel found" in report["text"]
    assert loop.calls == ["hold", ("release", True)]


def test_a_loop_that_hangs_on_the_panel_keeps_the_test_off_it(panel):
    class Stuck(Loop):
        def hold(self):
            self.calls.append("hold")
            raise RuntimeError("the panel loop did not let the panel go; restart Libre Panel")

    loop = Stuck()
    session = Session(loop)
    session.start()
    status = until(session, "done", "failed")
    assert status["state"] == "failed" and "did not let the panel go" in status["error"]
    assert loop.calls == ["hold"]  # nothing to give back
    assert panel.commands == [] and panel.frames == []  # the test did not touch the panel


def test_the_loop_gives_its_panel_to_the_test_and_takes_it_back(isolated_home):
    from test_service import wait_for, write_config

    from libre_panel.service import PanelService

    write_config(isolated_home)
    service = PanelService()
    service.start()
    try:
        assert wait_for(lambda: service.state()["state"] == "showing")
        assert service.hold() is True
        assert not service.running and service.state()["testing"]
        service.resume()  # the tray or a shortcut: not while the test has the panel
        assert not service.running
        service.release(True)
        assert wait_for(lambda: service.state()["state"] == "showing")
        assert not service.state()["testing"]
        service.pause()
        assert service.hold() is False
        service.release(False)
        assert service.paused and not service.running  # paused before, paused after
    finally:
        service.stop()


def test_the_log_for_the_app_has_its_entries_and_no_home_folder(tmp_path):
    log = tmp_path / "libre-panel.log"
    (tmp_path / "libre-panel.log.1").write_text(
        "2026-10-14 09:00:00,001 INFO libre_panel.app: showing theme 'lumen'\n", "utf-8"
    )
    log.write_text(
        "2026-10-14 10:00:00,123 WARNING libre_panel.app: panel not available: no panel\n"
        "2026-10-14 10:00:01,000 ERROR libre_panel.service: panel stopped: /home/jo/x.toml\n"
        "Traceback (most recent call last):\n"
        '  File "/home/jo/libre/app.py", line 3, in run\n'
        "2026-10-14 10:00:02,000 DEBUG libre_panel.devices.turing_rev_c: on /dev/ttyACM1\n",
        "utf-8",
    )
    found = logview.entries(path=log, home="/home/jo")
    assert [e["level"] for e in found] == ["INFO", "WARNING", "ERROR", "DEBUG"]
    assert found[2]["message"].startswith("panel stopped: ~/x.toml\nTraceback")
    assert "~/libre/app.py" in found[2]["message"] and "/dev/ttyACM1" in found[3]["message"]
    assert [e["level"] for e in logview.entries("WARNING", path=log)] == ["WARNING", "ERROR"]
    whole = logview.text(path=log, home="/home/jo")
    assert "showing theme" in whole and "/home/jo" not in whole


def test_a_step_of_the_loop_that_hangs_writes_where_every_thread_stands(tmp_path, caplog):
    """A hang after a game (reported: the panel kept Arena, the tray icon went, the log said
    nothing more) leaves where the threads stood next to the log; the log to send has it."""
    from libre_panel.app import HANG_FILENAME, _HangWatch

    log = tmp_path / "libre-panel.log"
    log.write_text("2026-10-15 15:51:20,953 INFO libre_panel.service.game: game over\n", "utf-8")
    watch = _HangWatch(tmp_path / HANG_FILENAME, seconds=0.3)

    def a_step_that_hangs():
        time.sleep(0.8)

    try:
        watch.arm()
        a_step_that_hangs()
        watch.disarm()
        watch.arm()  # a step in time writes nothing
        watch.disarm()
    finally:
        watch.close()
    hang = (tmp_path / HANG_FILENAME).read_text("utf-8")
    assert hang.startswith("-- libre-panel ") and hang.count("Timeout") == 1
    assert "a_step_that_hangs" in hang
    assert "the panel loop stood still" in caplog.text
    whole = logview.text(path=log)
    assert whole.index("game over") < whole.index(HANG_FILENAME) < whole.index("a_step_that_hangs")


def test_the_log_is_written_in_detail_until_it_is_switched_off():
    import logging

    root = logging.getLogger()
    before = root.level
    try:
        logview.set_verbose(True)
        assert logview.verbose() and logging.getLogger("PIL").level == logging.INFO
        logview.set_verbose(False)
        assert not logview.verbose()
    finally:
        root.setLevel(before)
        logging.getLogger("PIL").setLevel(logging.NOTSET)


class SlowSerial:
    """An older serial panel (rev. A, 115200 baud): a whole frame takes 25 seconds."""

    def __init__(self, clock):
        from libre_panel.devices.models import find_model

        self.model = find_model("turing-3.5")
        self.clock, self.shown, self.levels, self.opened = clock, 0, [], 0

    def show(self, image, region=None):
        self.shown += 1
        self.clock[0] += 25.0

    def set_brightness(self, percent):
        self.levels.append(percent)

    def close(self):
        pass

    def open(self):
        self.opened += 1


def test_an_older_serial_panel_is_tested_through_its_driver_and_its_speed_in_brief(monkeypatch):
    """No USB panel: the doctor takes the serial panel (the older protocols, through the driver
    the panel runs with), asks what is seen in the app, and its speed test ends after twenty
    seconds instead of twenty frames of half a minute each."""
    from libre_panel import doctor as doctor_module
    from libre_panel.doctor import Doctor

    clock = [0.0]
    monkeypatch.setattr(doctor_module.time, "perf_counter", lambda: clock[0])
    panel = SlowSerial(clock)

    def no_usb(pid=None):
        raise DeviceError("no Turing/TURZX USB panel found")

    def make(**options):
        return Doctor(open_transport=no_usb, open_serial=lambda: panel, pause=lambda s: None,
                      **{**options, "frames": 20})  # fmt: skip

    session = Session(Loop(), make_doctor=make)
    session.start()
    while (status := until(session, "asking", "done", "failed"))["state"] == "asking":
        kind = status["question"]["kind"]
        session.answer({"yes": True} if kind == "confirm" else {"number": 2} if kind == "number"
                       else {})  # fmt: skip
    report = status["report"]
    assert report["passed"], report["text"]
    assert report["model"]["id"] == "turing-3.5"
    assert "speed (2 full frames)" in report["text"]  # not twenty: it stopped after 20 s
    assert panel.shown == 2 + 2 + 1 + 1  # two test cards, the ruler, the speed, "finished"
    assert panel.levels[:2] == [10, 100] and panel.opened == 1  # brightness, then reconnected
