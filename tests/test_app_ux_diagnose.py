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
