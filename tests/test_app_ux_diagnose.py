"""Live → Diagnose in a browser: the switch to it, check and fix, the panel test answered with
buttons (its server answers are stood in for: no panel here) and the log."""

import json

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402
from test_app_browser import app_url, browser_page  # noqa: E402, F401

FORM = "https://github.com/syntensa/libre-panel/issues/new?template=panel_support.yml&doctor=x"


class Doctor:
    """The panel test's server side, a question at a time."""

    QUESTIONS = [
        {"key": "card-landscape", "kind": "confirm", "text": "Is the white frame visible?"},
        {"key": "ruler-top", "kind": "number", "text": "Top edge?"},
        {"key": "brightness-watch", "kind": "wait", "text": "Watch it"},
    ]

    def __init__(self):
        self.asked = -1  # idle
        self.answers = []

    def status(self):
        steps = [{"name": "handshake (command 10)", "result": "ok", "detail": "reply 01 02"}]
        if self.asked < 0:
            return {"state": "idle", "question": None, "error": "", "steps": []}
        if self.asked < len(self.QUESTIONS):
            question = {**self.QUESTIONS[self.asked], "id": self.asked + 1}
            return {"state": "asking", "question": question, "error": "", "steps": steps}
        report = {"text": "Libre Panel doctor report\nRESULT: all checks passed\n", "passed": True,
                  "failed": False, "summary": "all checks passed", "form": FORM,
                  "model": {"id": "turing-9.2-usb", "label": 'Turing 9.2" USB'}}  # fmt: skip
        return {"state": "done", "question": None, "error": "", "steps": steps, "report": report}

    def route(self, route, request):
        if request.method == "POST":
            body = json.loads(request.post_data or "{}")
            if body["action"] == "start":
                self.asked = 0
            elif body["action"] == "answer":
                self.answers.append(body["answer"])
                self.asked += 1
        route.fulfill(status=200, content_type="application/json", body=json.dumps(self.status()))


def test_the_panel_test_is_answered_with_buttons_and_its_report_goes_to_the_form(app_url):  # noqa: F811
    doctor = Doctor()
    with browser_page(app_url, "live/diagnose", routes={"**/api/doctor": doctor.route}) as page:
        switch = page.locator(".live-switch a")
        expect(switch).to_have_count(3)
        expect(page.locator('.live-switch a[aria-current="page"]')).to_have_text("Diagnose")
        expect(page.locator("#diagnose-test-title")).to_have_text("Panel test")
        page.locator('[data-diagnose="test"]').click()
        question = page.locator('[data-question="card-landscape"]')
        expect(question).to_contain_text("lying (landscape)")
        expect(question).to_contain_text("RED, GREEN and BLUE")
        question.locator('[data-answer="yes"]').click()
        ruler = page.locator('[data-question="ruler-top"]')
        expect(ruler).to_contain_text("At the top edge")
        expect(ruler.locator('[data-answer="number"]')).to_be_disabled()
        ruler.locator("input").fill("20")
        ruler.locator('[data-answer="number"]').click()
        page.locator('[data-question="brightness-watch"] [data-answer="go"]').click()
        report = page.locator('[data-diagnose="report"]')
        expect(report).to_have_text("Report the panel as working")
        assert report.get_attribute("href") == FORM and report.get_attribute("target") == "_blank"
        expect(page.locator(".doctor-steps")).to_contain_text("Handshake")  # its name in the app
        assert doctor.answers == [{"yes": True, "id": 1}, {"number": 20, "id": 2}, {"id": 3}]


def test_the_log_shows_its_problems_or_all_and_is_written_in_detail_on_request(app_url):  # noqa: F811
    asked, detailed = [], []

    def logs(route, request):
        if request.method == "POST":
            detailed.append(json.loads(request.post_data)["verbose"])
            body = {"verbose": detailed[-1]}
        else:
            level = request.url.split("level=")[1]
            asked.append(level)
            error = {"time": "2026-10-14 10:00:01", "level": "ERROR",
                     "logger": "libre_panel.app", "message": "panel stopped: ~/x"}  # fmt: skip
            entries = [error]
            if level != "WARNING":
                info = {"time": "2026-10-14 10:00:00", "level": "INFO",
                        "logger": "libre_panel.app", "message": "showing theme"}  # fmt: skip
                entries.insert(0, info)
            body = {"entries": entries, "verbose": bool(detailed and detailed[-1]),
                    "levels": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]}  # fmt: skip
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    with browser_page(
        app_url, "live/diagnose", routes={"**/api/logs?*": logs, "**/api/logs": logs}
    ) as page:
        box = page.locator(".diagnose-log-box")
        expect(box.locator(".log-entry")).to_have_count(1)  # the problems first
        expect(box.locator(".log-entry.error")).to_contain_text("panel stopped")
        page.locator(".diagnose-log-bar").get_by_role("radio", name="Everything").click()
        expect(box.locator(".log-entry")).to_have_count(2)
        assert asked[0] == "WARNING" and "INFO" in asked
        page.locator(".diagnose-detail").get_by_role("switch").click()
        expect(page.locator(".diagnose-detail").get_by_role("switch")).to_have_attribute(
            "aria-checked", "true"
        )
        assert detailed == [True]
        assert "/api/logs/file?" in page.locator(".diagnose-log a[download]").get_attribute("href")


def test_check_and_fix_says_what_it_did(app_url):  # noqa: F811
    with browser_page(app_url, "live/diagnose") as page:
        fix = page.locator('[data-diagnose="fix"]')
        expect(fix).to_be_enabled()
        expect(page.locator('[data-diagnose="restart"]')).to_be_disabled()  # no panel loop here
        fix.click()
        expect(page.locator(".diagnose-done")).to_contain_text(
            "Nothing that Libre Panel could mend by itself."
        )


def test_a_port_other_devices_have_too_is_asked_about_and_can_be_none(app_url):  # noqa: F811
    class Gadget(Doctor):
        QUESTIONS = [{"key": "gadget", "kind": "choose", "text": "Is the device on COM12 ...?",
                      "options": ['Turing 2.1" round', 'Turing 5"', 'Turing 8.8"']}]  # fmt: skip

    doctor = Gadget()
    with browser_page(app_url, "live/diagnose", routes={"**/api/doctor": doctor.route}) as page:
        page.locator('[data-diagnose="test"]').click()
        question = page.locator('[data-question="gadget"]')
        expect(question).to_contain_text("which other USB devices have too")
        expect(question.locator("button")).to_have_count(4)  # the three, and none of them
        question.locator('[data-answer="none"]').click()
        expect(page.locator('[data-diagnose="report"]')).to_be_visible()
        assert doctor.answers == [{"id": 1}]


MB = 1024**2
LOAD = {
    "ready": True, "cpus": 8, "window_s": 2.0, "sample_ms": 1.4, "age_s": 0.3,
    "total": {"cpu_core": 41.0, "cpu_pc": 5.12, "private": 382 * MB, "rss": 735 * MB},
    "processes": [
        {"role": "main", "panel": None, "name": "", "count": 1, "cpu_core": 25.0, "cpu_pc": 3.12,
         "private": 150 * MB, "rss": 200 * MB, "label": "main process"},
        {"role": "panel", "panel": 2, "name": "", "count": 1, "cpu_core": 10.0, "cpu_pc": 1.25,
         "private": 70 * MB, "rss": 90 * MB, "label": "panel 2"},
        {"role": "ffmpeg", "panel": 2, "name": "", "count": 1, "cpu_core": 4.0, "cpu_pc": 0.5,
         "private": 30 * MB, "rss": 40 * MB, "label": "ffmpeg (panel 2)"},
        {"role": "webview", "panel": None, "name": "", "count": 2, "cpu_core": 0.0, "cpu_pc": 0.0,
         "private": 129 * MB, "rss": 150 * MB, "label": "WebView2"},
        {"role": "service", "panel": None, "name": "", "count": 1, "cpu_core": 2.0, "cpu_pc": 0.25,
         "private": None, "rss": 80 * MB, "label": "sensor service",
         "found_by": "service manager"},
        {"role": "other", "panel": None, "name": "conhost.exe", "count": 1, "cpu_core": None,
         "cpu_pc": None, "private": 3 * MB, "rss": 5 * MB, "label": "other: conhost.exe"},
    ],
    "threads": [
        {"name": "panel", "count": 1, "cpu_core": 15.0, "cpu_pc": 1.88},
        {"name": "lhm-service", "count": 1, "cpu_core": 2.0, "cpu_pc": 0.25},
        {"name": "other", "count": 3, "cpu_core": 8.0, "cpu_pc": 1.0},
    ],
    "service": {"state": "running", "found_by": "service manager"},
    "sensors": {
        "mode": "service", "version": "0.7.1", "error": "", "reported": True,
        "last": {"ms": 9.8, "full": False, "parts": [
            {"kind": "Cpu", "name": "8-Core Processor", "ms": 6.1},
            {"kind": "GpuNvidia", "name": "Graphics Card", "ms": 3.7}]},
        "full": {"ms": 30.3, "full": True, "parts": [
            {"kind": "SuperIO", "name": "Super I/O Chip", "ms": 20.0},
            {"kind": "Cpu", "name": "8-Core Processor", "ms": 6.1},
            {"kind": "GpuNvidia", "name": "Graphics Card", "ms": 3.7},
            {"kind": "Memory", "name": "Total Memory", "ms": 0.5}]},
    },
    "text": "Libre Panel load over 2.0 s, 8 logical processors\nmain process 3.1 %\n",
}  # fmt: skip


class Load:
    """/api/load's answers, counted."""

    def __init__(self, answer):
        self.answer, self.asked = answer, 0

    def route(self, route, request):
        self.asked += 1
        route.fulfill(status=200, content_type="application/json", body=json.dumps(self.answer))


def test_the_load_card_has_a_title_of_its_own_that_german_words_as_utilisation(app_url):  # noqa: F811
    """The card is titled by what it measures, not by "Load" (a word that German has twice: the
    load of the processor is its Auslastung, a burden its Last)."""
    from libre_panel import i18n

    i18n.set_language("de")
    with browser_page(app_url, "live/diagnose", routes={"**/api/load": Load(LOAD).route}) as page:
        expect(page.locator(".diagnose-load #diagnose-load-title")).to_have_text(
            "Auslastung dieses PCs"
        )


def test_the_load_card_shows_what_libre_panel_costs_and_copies_it_as_text(app_url):  # noqa: F811
    load = Load(LOAD)
    with browser_page(app_url, "live/diagnose", routes={"**/api/load": load.route}) as page:
        card = page.locator(".diagnose-load")
        expect(card.locator("#diagnose-load-title")).to_have_text("Load on this PC")
        expect(card.locator('[data-load="cpu"]')).to_have_text("5.1 %")
        # the private memory (the Task Manager's Memory column), the working set beside it
        expect(card.locator('[data-load="memory"]')).to_have_text("382 MB")
        expect(card.locator('[data-load="working-set"]')).to_have_text("Working set: 735 MB")
        expect(card.locator(".load-headline")).to_contain_text(
            "41.0 % of one core · 8 logical processors"
        )
        expect(card.locator(".load-table thead th")).to_have_text(
            ["Process", "Of the PC", "Of one core", "Memory", "Working set"]
        )
        rows = card.locator(".load-table tbody tr")
        expect(rows).to_have_count(6)
        expect(rows.nth(0)).to_contain_text("Main process")
        expect(rows.nth(0)).to_contain_text("3.1 %")
        expect(rows.nth(0)).to_contain_text("25.0 %")
        expect(rows.nth(0).locator('[data-load="private"]')).to_have_text("150 MB")
        expect(rows.nth(0).locator('[data-load="rss"]')).to_have_text("200 MB")
        expect(rows.nth(1)).to_contain_text("Panel 2")
        expect(rows.nth(2)).to_contain_text("Video of panel 2 (ffmpeg)")
        expect(rows.nth(3)).to_contain_text("Browser engine of the window ×2")
        expect(rows.nth(4)).to_contain_text("Sensor service")
        expect(rows.nth(4).locator('[data-load="private"]')).to_have_text("–")  # refused
        expect(rows.nth(4).locator('[data-load="rss"]')).to_have_text("80 MB")
        expect(rows.nth(5)).to_contain_text("conhost.exe")
        expect(rows.nth(5)).to_contain_text("–")  # not measured yet
        threads = card.locator(".load-threads")
        expect(threads.locator("li").first).not_to_be_visible()  # folded
        threads.locator("summary").click()
        expect(threads.locator("li")).to_have_count(3)
        expect(threads.locator("li").first).to_contain_text("panel")
        expect(threads.locator("li").last).to_contain_text("Other threads ×3")
        sensors = card.locator('[data-load="sensors"]')
        expect(sensors).to_contain_text("Through the sensor service 0.7.1")
        rounds = sensors.locator(".load-round")
        expect(rounds).to_have_count(2)
        expect(rounds.nth(0)).to_contain_text("Last round (every second)")
        expect(rounds.nth(0)).to_contain_text("9.8 ms")
        expect(rounds.nth(0)).to_contain_text("8-Core Processor 6.1 ms")
        expect(rounds.nth(1)).to_contain_text("All hardware (now and then)")
        expect(rounds.nth(1)).to_contain_text("30.3 ms")
        expect(rounds.nth(1).locator(".load-part")).to_have_count(3)  # the three slowest
        expect(rounds.nth(1)).not_to_contain_text("Total Memory")
        expect(card).to_contain_text("Per PC is what the Task Manager shows")
        expect(card).to_contain_text(
            "Memory is each process's private memory, as the Task Manager's Memory column shows it"
        )
        expect(card).to_contain_text("Measuring took 1.4 ms.")
        page.evaluate(
            "Object.defineProperty(navigator.clipboard, 'writeText',"
            " {value: async (text) => { window.copied = text; }})"
        )
        card.locator('[data-load="copy"]').click()
        assert page.evaluate("window.copied") == LOAD["text"]
        # every two seconds while it is on screen; not once it has left
        asked = load.asked
        page.wait_for_timeout(2300)
        assert load.asked > asked
        page.evaluate("location.hash = '#/settings'")
        page.wait_for_timeout(300)
        asked = load.asked
        page.wait_for_timeout(2500)
        assert load.asked == asked


def test_the_load_card_measures_first_and_tells_an_older_service(app_url):  # noqa: F811
    load = Load({"ready": False, "cpus": 8, "window_s": 2.0})
    with browser_page(app_url, "live/diagnose", routes={"**/api/load": load.route}) as page:
        card = page.locator(".diagnose-load")
        expect(card).to_contain_text("Measuring …")
        expect(card.locator('[data-load="copy"]')).to_be_disabled()
        older = {
            **LOAD["sensors"],
            "version": "0.6.0",
            "reported": False,
            "last": None,
            "full": None,
        }
        load.answer = {**LOAD, "sensors": older}
        expect(card.locator('[data-load="unreported"]')).to_have_text(
            "Not reported by this service version."
        )
        expect(card.locator('[data-load="sensors"]')).to_contain_text(
            "Through the sensor service 0.6.0"
        )
        expect(card.locator('[data-load="copy"]')).to_be_enabled()


def test_the_load_card_reads_the_real_measurement(app_url):  # noqa: F811
    """With the server's own sampler: this process, measured."""
    with browser_page(app_url, "live/diagnose") as page:
        card = page.locator(".diagnose-load")
        expect(card.locator('[data-load="cpu"]')).to_contain_text("%", timeout=15000)
        expect(card.locator(".load-table tbody tr").first).to_contain_text("Main process")


def test_a_hidden_window_asks_the_app_nothing_until_it_is_shown(app_url):  # noqa: F811
    """A window that is minimized or hidden (WebView2 says the page is hidden) asks the app's
    server nothing: not how the app is doing, not the load, not the log; shown, it asks again.
    (Closed, its browser engine's processes end with it.)"""
    load = Load(LOAD)
    with browser_page(app_url, "live/diagnose", routes={"**/api/load": load.route}) as page:
        asked = []
        page.on("request", lambda r: asked.append(r.url) if "/api/" in r.url else None)
        page.wait_for_timeout(2500)
        assert any("/api/load" in url for url in asked)  # (shown: it asks)
        page.evaluate(
            """() => {
              Object.defineProperty(document, "hidden", {configurable: true, get: () => true});
              Object.defineProperty(document, "visibilityState",
                                    {configurable: true, get: () => "hidden"});
              document.dispatchEvent(new Event("visibilitychange"));
            }"""
        )
        page.wait_for_timeout(500)  # (a request on its way when it was hidden comes back)
        asked.clear()
        page.wait_for_timeout(4500)  # longer than any of them waits between two
        assert asked == []
        page.evaluate(
            """() => {
              Object.defineProperty(document, "hidden", {configurable: true, get: () => false});
              Object.defineProperty(document, "visibilityState",
                                    {configurable: true, get: () => "visible"});
              document.dispatchEvent(new Event("visibilitychange"));
            }"""
        )
        page.wait_for_timeout(2500)
        assert any("/api/load" in url for url in asked) and any("/api/app" in url for url in asked)
