"""Settings and Live as a person meets them (0.6.0's pass over them): the overview of what needs
attention, the plain words of each section, the report of a panel and Live's status line. In a
real browser, as test_app_browser.py; skipped when Playwright or Chromium are missing."""

import json

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402
from test_app_browser import (  # noqa: E402, F401
    PICTURE,
    a_panel_app,
    app_answer,
    app_url,
    browser_page,
    choose_theme,
    no_setup_card,
    settings_config,
    unlock_route,
    waits_for,
    windows_download,
)


def answer(data):
    """A route's handler that answers ``data`` as JSON, whatever is asked."""

    def handler(route, request):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    return handler


def health(page):
    """The ids of what the overview lists, in its order."""
    return page.locator(".health-item").evaluate_all("rows => rows.map((r) => r.dataset.health)")


# -- the overview: what needs attention ------------------------------------------------------------


HEALTH_OF = """async (facts) => {
  const { healthOf } = await import("/app/views/settings/health.js");
  const base = { app: { available: true, panel: { state: "showing", stats: {} } }, themes: [],
    panelInfo: null, unlockInfo: null, settings: null, settingsError: null, sourceStates: null,
    serviceStates: null, clipQuestion: null };
  const { ready, items, good } = healthOf({ ...base, ...facts });
  const rows = items.map((item) => [item.id, item.tone, item.action?.label ?? null]);
  return { ready, good, items: rows };
}"""

PANEL = {
    "configured": "auto",
    "found": None,
    "model": None,
    "running": {"state": "showing", "detail": ""},
    "video": {"mode": "auto", "fps": 50, "on": False, "usb": True, "gaps": []},
    "weather_theme": None,
}
CLIP = "usr/data/standby.h264"  # the name the vendor app gives the panel's standby clip
TURING = {"id": "turing-9.2-usb", "label": 'Turing 9.2"', "size": [1920, 480], "shape": "rect"}


def config(**changes):
    """What /api/settings says (the parts the overview reads), on Windows."""
    data = {
        "device": {"driver": "auto"},
        "sensors": {"providers": ["load", "librehardwaremonitor", "psutil"]},
        "services": {"enabled": ["game"], "options": {}},
        "weather": {"enabled": False, "latitude": None, "longitude": None, "place": None},
    }
    for path, value in changes.items():
        part, key = path.split(".")
        data[part][key] = value
    return {"config": data, "catalog": {"platform": "win32", "services": []}, "restart": []}


def test_the_overview_tells_each_thing_with_what_mends_it(app_url):  # noqa: F811
    """Each thing that keeps the panel from what it should do, in the order of how bad it is,
    with its click or the way to where it is done; nothing at all when all is well."""
    found = {**PANEL, "found": TURING, "model": TURING}
    with browser_page(app_url, "home", allow=PICTURE) as page:

        def of(**facts):
            return page.evaluate(HEALTH_OF, facts)

        assert of() == {"ready": False, "good": True, "items": []}  # nothing known yet
        assert of(panelInfo=found) == {"ready": True, "good": True, "items": []}
        # no panel, or another one than the settings name: the next step, or the click
        assert of(panelInfo=PANEL)["items"] == [["no-panel", "warn", "Report a panel"]]
        chosen = {**found, "model": {**TURING, "id": "turing-8.8-usb", "label": 'Turing 8.8"'}}
        assert of(panelInfo=chosen)["items"] == [["other-panel", "warn", "Use the Turing 9.2″"]]
        # the panel paused, or stopped with a fault
        paused = {"available": True, "panel": {"state": "paused"}}
        assert of(panelInfo=found, app=paused)["items"] == [["paused", "warn", "Resume panel"]]
        broken = {"available": True, "panel": {"state": "error", "detail": "no USB library"}}
        assert of(panelInfo=found, app=broken)["items"] == [["error", "error", "Show the log"]]
        # Libre Panel not in the background: said, with what to do in words (no command)
        alone = of(panelInfo={**found, "running": None}, app={"available": False})
        assert alone["items"] == [["background", "warn", None]]
        # smooth motion: the clip's name (only where it is asked for), ffmpeg
        video = {**found, "video": {**PANEL["video"], "gaps": ["clip"]}}
        assert of(panelInfo=video)["items"] == []  # asked before, and "Not now"
        asked = {"ask": True, "name": CLIP}
        clip = ["video-clip", "warn", "Turn on"]
        assert of(panelInfo=video, clipQuestion=asked)["items"] == [clip]
        on = {**video, "video": {**video["video"], "mode": "on"}}
        assert of(panelInfo=on, clipQuestion={**asked, "ask": False})["items"][0][0] == "video-clip"
        ffmpeg = {**found, "video": {**PANEL["video"], "gaps": ["clip", "ffmpeg"]}}
        assert of(panelInfo=ffmpeg)["items"] == [["video-ffmpeg", "warn", "Advanced"]]
        proving = {**found, "video": {**PANEL["video"], "gaps": ["model"]}}
        assert of(panelInfo=proving)["items"] == []  # not proven on it: off is no fault
        # sensors: the sensor service, else the hint of the running app
        locked = windows_download()
        assert of(panelInfo=found, unlockInfo=locked)["items"] == [
            ["sensor-service", "warn", "Unlock all sensors"]
        ]
        stopped = windows_download(service={"state": "stopped", "disabled": False})
        assert of(panelInfo=found, unlockInfo=stopped)["items"] == [
            ["sensor-service", "warn", "Set up again"]
        ]
        hint = {"available": True, "panel": {"state": "showing", "hint": "CPU sensors need …"}}
        assert of(panelInfo=found, app=hint)["items"] == [["readings", "warn", "Sensor settings"]]
        # games without a frame rate: its source off, PresentMon missing, the permission
        assert of(panelInfo=found, settings=config())["items"] == [
            ["game-fps", "warn", "Switch it on"]
        ]
        reading = config(**{"sensors.providers": ["presentmon", "psutil"]})
        running = {"state": "running", "disabled": False}
        missing = windows_download(service=running, presentmon=False)
        assert of(panelInfo=found, settings=reading, unlockInfo=missing)["items"] == [
            ["game-fps", "warn", None]
        ]
        sign_in = {**missing, "presentmon": True, "frame_rates": "sign-in"}
        result = of(panelInfo=found, settings=reading, unlockInfo=sign_in)
        assert result == {"ready": True, "good": True, "items": [["game-fps", "info", None]]}
        # the weather: a theme that shows it while it is off, with a place or without
        weather = {**found, "weather_theme": "nimbus"}
        assert of(panelInfo=weather, settings=config())["items"][-1] == [
            "weather",
            "info",
            "Choose a place",
        ]
        placed = config(**{"weather.latitude": 1.0, "weather.longitude": 2.0, "weather.place": "X"})
        assert of(panelInfo=weather, settings=placed)["items"][-1] == [
            "weather",
            "info",
            "Turn on the weather",
        ]
        # what runs by itself: a service that stopped is the worst, and has its Restart
        services = {
            "background": True,
            "services": [
                {"name": "notices", "state": "failed", "detail": "a fault", "enabled": True},
                {"name": "devices", "state": "stopped", "detail": "", "enabled": False},
            ],
        }
        items = of(panelInfo=PANEL, serviceStates=services)["items"]
        assert items == [
            ["service-notices", "error", "Restart"],
            ["no-panel", "warn", "Report a panel"],
        ]


def test_the_overview_mends_what_it_can_with_one_click(
    app_url,  # noqa: F811
    isolated_home,
    monkeypatch,
):
    """Settings opens on the overview: another panel than the one chosen is mended with one
    click, a theme that shows the weather leads to the place, and the count beside the overview
    says how much is left while another section is open."""
    from test_editor_settings import lacking_ffmpeg, panel_on_the_bus

    from libre_panel.config import set_config_value

    panel_on_the_bus(monkeypatch, "turing-9.2-usb")
    lacking_ffmpeg(monkeypatch, found=True)
    choose_theme(isolated_home, "nimbus")
    set_config_value("device.model", "turing-8.8-usb")
    routes = {"**/api/app": a_panel_app()}  # the app runs: no "not in the background"
    with browser_page(app_url, "settings", routes=routes, allow=PICTURE) as page:
        assert page.locator("#settings-title").inner_text() == "Overview"
        page.wait_for_selector(".health-item")
        assert health(page) == ["other-panel", "weather"]
        expect(page.locator("#health-title")).to_have_text("What needs attention")
        item = page.locator("[data-health='other-panel']")
        expect(item).to_contain_text("The settings name the Turing 8.8″")
        item.get_by_role("button", name="Use the Turing 9.2″").click()
        waits_for(page, lambda: settings_config().device.model == "auto")
        expect(page.locator("[data-health='other-panel']")).to_have_count(0)
        # the 9.2" takes smooth motion, which waits for the name of its standby clip
        expect(page.locator("[data-health='video-clip']")).to_have_count(1)
        # the theme on the panel shows the weather, which has no place yet
        weather = page.locator("[data-health='weather']")
        expect(weather).to_contain_text("Nimbus can show the weather")
        # at a glance, each part with the way to its section
        glance = page.locator("[data-glance='panel']")
        expect(glance).to_contain_text("Turing 9.2″, connected")
        page.goto(f"{app_url}#/settings/about")
        expect(page.locator(".settings-nav-count")).to_have_text("1")  # the clip is left
        page.locator(".settings-nav button", has_text="Overview").click()
        weather.get_by_role("button", name="Choose a place").click()
        page.wait_for_function("location.hash === '#/settings/weather'")
        # the clip's name is written only on the click, and it is the one offered
        page.goto(f"{app_url}#/settings/overview")
        clip = page.locator("[data-health='video-clip']")
        expect(clip).to_contain_text(CLIP)
        assert settings_config().device.video.local_clip == ""
        clip.get_by_role("button", name="Turn on").click()
        waits_for(page, lambda: settings_config().device.video.local_clip == CLIP)
        expect(page.locator("#health-title")).to_have_text("All good")
        assert health(page) == ["weather"]  # (a suggestion, nothing that needs attention)
        expect(page.locator(".settings-nav-count")).to_have_count(0)


def test_the_overview_says_all_good_calmly_and_the_short_form_says_nothing(
    app_url,  # noqa: F811
    isolated_home,
    monkeypatch,
):
    from test_editor_settings import lacking_ffmpeg, panel_on_the_bus

    from libre_panel.config import set_config_value

    panel_on_the_bus(monkeypatch, "turing-9.2-usb")
    lacking_ffmpeg(monkeypatch, found=True)
    choose_theme(isolated_home, "almanac")  # (no weather on it)
    set_config_value("video.local_clip", "usr/data/standby.h264")
    with browser_page(
        app_url, "settings/overview", routes={"**/api/app": a_panel_app()}, allow=PICTURE
    ) as page:
        expect(page.locator("#health-title")).to_have_text("All good")
        assert health(page) == []
        expect(page.locator("[data-glance='video']")).to_contain_text("On, 50 fps")
        page.evaluate(SHORT_FORM)
        page.wait_for_function("window.libre.store.get().serviceStates !== null")
        page.wait_for_timeout(500)
        assert page.locator("#short-form").inner_text() == ""  # nothing while all is well


# The short form of the card, as Start is to show it, drawn into a box of its own.
SHORT_FORM = """async () => {
  const { html, render } = await import("/app/lib/preact.js");
  const { HealthCard } = await import("/app/views/settings/health.js");
  const box = document.createElement("div");
  box.id = "short-form";
  document.body.append(box);
  render(html`<${HealthCard} compact />`, box);
}"""


def test_the_short_form_lists_what_needs_attention_with_its_clicks(
    app_url,  # noqa: F811
    isolated_home,
    monkeypatch,
):
    """For Start: what needs attention (no suggestions), each with its click."""
    from test_editor_settings import panel_on_the_bus

    panel_on_the_bus(monkeypatch)  # none
    choose_theme(isolated_home, "nimbus")
    hint = "CPU temperature, power and fans need the sensor service."
    routes = {
        "**/api/app": a_panel_app(hint=hint),
        "**/api/unlock": unlock_route(windows_download(), []),
    }
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        page.evaluate(SHORT_FORM)
        box = page.locator("#short-form")
        box.locator("[data-health='sensor-service']").wait_for()
        assert box.locator("h2").inner_text() == "What needs attention"
        ids = box.locator(".health-item").evaluate_all("rows => rows.map((r) => r.dataset.health)")
        assert ids == ["no-panel", "sensor-service"]  # (Nimbus's weather is no fault)
        expect(box.get_by_role("button", name="Unlock all sensors")).to_be_visible()
        box.get_by_role("button", name="Settings").click()
        page.wait_for_function("location.hash === '#/settings/overview'")


def test_the_overview_speaks_german(app_url, isolated_home):  # noqa: F811
    from libre_panel import i18n

    choose_theme(isolated_home, "nimbus")
    i18n.set_language("de")
    with browser_page(app_url, "settings", allow=PICTURE) as page:
        expect(page.locator("#settings-title")).to_have_text("Übersicht")
        page.wait_for_selector("[data-health='background']")
        expect(page.locator("#health-title")).to_have_text("Was Aufmerksamkeit braucht")
        expect(page.locator("[data-health='background']")).to_contain_text(
            "Libre Panel läuft nicht im Hintergrund"
        )
        expect(page.locator("[data-health='weather']")).to_contain_text(
            "Nimbus kann das Wetter zeigen"
        )
        expect(page.locator("#glance-title")).to_have_text("Auf einen Blick")
