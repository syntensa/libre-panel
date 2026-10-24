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
    background,
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
        waiting = {"available": True, "panel": {"state": "waiting", "stats": {}}}
        missing = [["no-panel", "warn", "Report a panel"]]
        assert of(panelInfo=PANEL, app=waiting)["items"] == missing
        # (a panel the background app shows a theme on is there, whatever the bus said here)
        assert of(panelInfo=PANEL)["items"] == []
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
        items = of(panelInfo=PANEL, app=waiting, serviceStates=services)["items"]
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
        assert ids == ["sensor-service"]  # (the panel shows a theme; Nimbus's weather is no fault)
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


# -- report a panel: three steps, what is connected listed by the app ---------------------------


def test_report_a_panel_is_three_steps_with_what_is_connected_listed(app_url, monkeypatch):  # noqa: F811
    """The app lists what is connected itself (no command to run for it), says how to run the
    doctor step by step, and the form opens with the list filled in."""
    from test_editor_settings import panel_on_the_bus, plugged_in

    panel_on_the_bus(monkeypatch, "turing-9.2-usb")
    plugged_in(monkeypatch, usb=[{"vid": "1cbe", "pid": "0092"}])
    with browser_page(app_url, "settings/about") as page:
        card = page.locator("#about-report")
        steps = card.locator(".report-step").evaluate_all("rows => rows.map((r) => r.dataset.step)")
        assert steps == ["list", "doctor", "send"]
        listed = card.locator(".report-list")
        expect(listed).to_contain_text("usb  1cbe:0092  -> turing-9.2-usb")
        expect(listed).to_contain_text('Panel found: Turing 9.2"')
        assert "No serial numbers" in card.locator("[data-step='list']").inner_text()
        # the doctor sends the panel test pictures: it runs outside the app, the way is told
        doctor = card.locator("[data-step='doctor']")
        expect(doctor.locator(".command")).to_have_text("libre-panel doctor")
        expect(doctor.locator(".report-how li")).to_have_count(4)
        expect(doctor).to_contain_text("Open a terminal")  # (this is no Windows)
        form = card.get_by_role("link", name="Open the report form").get_attribute("href")
        assert "template=panel_support.yml" in form and "product=Turing+9.2%22" in form
        assert "devices=Libre+Panel" in form
        # plugged in meanwhile: looked at again on a click
        plugged_in(monkeypatch, usb=[{"vid": "1cbe", "pid": "0088"}])
        card.get_by_role("button", name="Look again").click()
        expect(listed).to_contain_text("usb  1cbe:0088  -> turing-8.8-usb")


def test_report_a_panel_tells_windows_how_to_open_a_terminal(app_url):  # noqa: F811
    told = {"text": "Libre Panel 0.6.0", "product": "", "form": "https://example.invalid/form",
            "platform": "win32"}  # fmt: skip
    with browser_page(app_url, "settings/about", routes={"**/api/report": answer(told)}) as page:
        doctor = page.locator("[data-step='doctor']")
        expect(doctor).to_contain_text("Press Windows+R, type cmd")
        link = page.get_by_role("link", name="Open the report form")
        expect(link).to_have_attribute("href", "https://example.invalid/form")


# -- the sections in plain words, what few change under Advanced --------------------------------


def test_smooth_motion_asks_for_the_clip_and_takes_the_panels_own_name_on_a_click(
    app_url,  # noqa: F811
    isolated_home,
    monkeypatch,
):
    """Settings → Panel calls the video mode smooth motion; while it lacks the standby clip's
    name the row offers the one the panel's own app gives it (written only on the click), and
    once it is set the name is under Advanced, which an address opens."""
    from test_app_browser import radio
    from test_editor_settings import lacking_ffmpeg, panel_on_the_bus

    panel_on_the_bus(monkeypatch, "turing-9.2-usb")
    lacking_ffmpeg(monkeypatch, found=True)
    with browser_page(app_url, "settings/panel") as page:
        expect(page.locator(".settings-lead")).to_contain_text("smooth motion")
        card = page.locator("#panel-video")
        expect(card.locator(".settings-row-title").first).to_have_text("Smooth motion")
        expect(card).to_contain_text("Off for now. Missing: the name of the standby clip.")
        clip = card.locator(".clip-row")
        expect(clip).to_contain_text(f"Its own app calls it {CLIP}")
        assert not (isolated_home / "config.toml").exists()  # nothing written by asking
        clip.get_by_role("button", name="Use this name").click()
        waits_for(page, lambda: settings_config().device.video.local_clip == CLIP)
        assert settings_config().device.video.clip_asked is True  # Start need not ask
        expect(card).to_contain_text("On, 50 fps")
        expect(card.locator(".clip-row")).to_have_count(0)
        # the name is kept under Advanced, in plain words; the address opens it
        page.goto(f"{app_url}#/settings/panel/panel-advanced")
        advanced = page.locator("#panel-advanced")
        expect(advanced.locator(".settings-disclosure")).to_have_attribute("aria-expanded", "true")
        expect(page.get_by_label("Name of the standby clip")).to_have_value(CLIP)
        titles = advanced.locator(".settings-row-title").all_inner_texts()
        assert titles == [
            "Advanced",
            "Where the pictures go",
            "Picture file",
            "Name of the standby clip",
            "Port of a serial panel",
            "Video program (ffmpeg)",
        ]
        # off: neither the rate nor the clip is asked for
        radio(page, "Smooth motion", "Off").click()
        waits_for(page, lambda: settings_config().device.video.mode == "off")
        expect(page.get_by_role("radiogroup", name="Frames per second")).to_have_count(0)
        expect(page.get_by_label("Name of the standby clip")).to_have_count(0)


def test_the_sensors_lead_with_what_is_in_use_and_fold_the_rest(app_url, isolated_home):  # noqa: F811
    """The sources in use are listed; the others wait under More sources, and how often they
    are read and how LibreHardwareMonitor is reached are under Advanced. What a source says when
    readings are missing shows on its row."""
    from libre_panel.config import set_config_value

    set_config_value("sensors.providers", ["load", "librehardwaremonitor", "psutil"])
    hint = "CPU temperature, power and fans need the sensor service."
    sources = {
        "background": True,
        "sources": [
            {"name": "librehardwaremonitor", "readings": 3, "problem": True, "hint": hint,
             "status": {}, "details": [["Mode", "inside Libre Panel"]]},
        ],
    }  # fmt: skip
    routes = {"**/api/plugins/control/sources": answer(sources)}
    with browser_page(app_url, "settings/sensors", routes=routes) as page:
        card = page.locator("#sensor-sources")
        expect(card.locator("h2")).to_have_text("Where the readings come from")
        expect(card.locator("[data-source='librehardwaremonitor']")).to_contain_text(hint)
        expect(card.locator(".source-row.unused")).to_have_count(0)
        card.locator(".sources-more").click()
        expect(card.locator(".source-row.unused[data-source='demo']")).to_have_count(1)
        # how often, and LibreHardwareMonitor: folded away until asked for
        expect(page.get_by_label("Reading interval")).to_have_count(0)
        page.locator("#sensor-advanced .settings-disclosure").click()
        expect(page.get_by_label("Reading interval")).to_be_visible()
        lhm = page.locator("#sensor-lhm")
        expect(lhm).to_contain_text("inside Libre Panel")
        expect(lhm.get_by_role("radiogroup", name="How it is read")).to_be_visible()


def test_game_mode_and_more_leads_with_game_mode_and_the_place_comes_first(app_url):  # noqa: F811
    with browser_page(app_url, "settings/services") as page:
        expect(page.locator("#settings-title")).to_have_text("Game mode and more")
        expect(page.locator(".settings-lead")).to_contain_text("Each is off until you switch it on")
        cards = page.locator(".settings-card.service")
        expect(cards.first).to_have_attribute("data-service", "game")
        notices = page.locator("[data-service='notices']")
        expect(notices.locator(".settings-row-title").first).to_have_text("Notices")
        expect(notices.locator(".settings-disclosure")).to_contain_text("Options · ")
        expect(page.locator(".note")).to_have_count(0)  # (what the note said is the lead now)
        # the weather needs a place: the place comes first, then the weather, then the look of
        # its symbols
        page.goto(f"{app_url}#/settings/weather")
        page.wait_for_selector("#weather")
        ids = page.locator(".settings-main .settings-card").evaluate_all(
            "cards => cards.map((c) => c.id)"
        )
        assert ids == ["place", "weather", "symbols"]
        # no command to type for a window of its own
        page.goto(f"{app_url}#/settings/system")
        page.wait_for_selector("#start")
        assert "--check" not in page.locator("#main").inner_text()


def test_the_sections_speak_german(app_url, monkeypatch):  # noqa: F811
    from test_editor_settings import lacking_ffmpeg, panel_on_the_bus

    from libre_panel import i18n

    panel_on_the_bus(monkeypatch, "turing-9.2-usb")
    lacking_ffmpeg(monkeypatch, found=True)
    i18n.set_language("de")
    with browser_page(app_url, "settings/panel") as page:
        card = page.locator("#panel-video")
        expect(card.locator(".settings-row-title").first).to_have_text("Flüssige Bewegung")
        expect(card.get_by_role("button", name="Diesen Namen nehmen")).to_be_visible()
        nav = page.locator(".settings-nav button")
        expect(nav.nth(4)).to_have_text("Spielmodus und mehr")  # (after Panel, Panels, Sensoren)
        page.goto(f"{app_url}#/settings/sensors")
        expect(page.locator("#sensor-sources h2")).to_have_text("Woher die Werte kommen")
        expect(page.locator(".sources-more")).to_contain_text("Weitere Quellen")


# -- Live: how it runs, in a calm sentence ---------------------------------------------------------


RUN_STATE = """async ([background, status]) => {
  const { runState } = await import("/app/views/live/index.js");
  const { tone, title, text } = runState(background, status);
  return [tone, title, text];
}"""


def test_the_video_experiment_writes_each_choice_at_once_and_leads_back(
    app_url,  # noqa: F811
    isolated_home,
    monkeypatch,
):
    """Settings → Panel → Video experiment: the cap, the quality, the speed, the profile, the
    encoder and the keyframes, each written on the click; Live's tiles and the log's lines
    about the video under them; and a way back to what was measured, greyed while it holds."""
    from test_app_browser import radio
    from test_editor_settings import lacking_ffmpeg, panel_on_the_bus

    panel_on_the_bus(monkeypatch, "turing-9.2-usb")
    lacking_ffmpeg(monkeypatch, found=True)
    with browser_page(app_url, "settings/panel") as page:
        card = page.locator("#panel-video-experiment")
        expect(card).to_contain_text("Video experiment")
        back = card.get_by_role("button", name="Back to the defaults")
        expect(back).to_be_disabled()  # nothing but the defaults yet
        radio(page, "Quality", "18").click()
        waits_for(page, lambda: settings_config().device.video.crf == 18)
        radio(page, "Cap on the bit rate", "16 Mbit/s").click()
        waits_for(page, lambda: settings_config().device.video.maxrate == "16M")
        radio(page, "Profile", "high").click()
        waits_for(page, lambda: settings_config().device.video.profile == "high")
        page.get_by_label("Encoder", exact=True).select_option("h264_amf")
        waits_for(page, lambda: settings_config().device.video.encoder == "h264_amf")
        radio(page, "Frames per second", "60").click()  # the panel is told 70 with it
        waits_for(page, lambda: settings_config().device.video.device_fps == 70)
        assert settings_config().device.video.fps == 60
        expect(card.locator(".live-tile")).to_have_count(5)  # as Live shows them
        expect(card.locator(".experiment-log")).to_contain_text("Nothing about the video")
        expect(back).to_be_enabled()
        back.click()
        waits_for(page, lambda: settings_config().device.video.crf == 20)
        video = settings_config().device.video
        assert (video.maxrate, video.profile, video.encoder) == ("auto", "auto", "auto")
        assert (video.fps, video.device_fps) == (50, 60)
        expect(back).to_be_disabled()


def test_the_video_experiment_runs_every_step_by_itself_on_the_running_app(
    background,  # noqa: F811
    monkeypatch,
):
    """With the background app: the run tries every step in turn (shortened here), a table
    says the outcome per step and panel (a PNG file takes no video), and the settings from
    before the run (quality 20) are back at the end."""
    from test_app_browser import radio
    from test_editor_settings import lacking_ffmpeg, panel_on_the_bus

    from libre_panel.editor import experiment

    monkeypatch.setattr(experiment.Experiment, "scale", 0.002)
    panel_on_the_bus(monkeypatch, "turing-9.2-usb")
    lacking_ffmpeg(monkeypatch, found=True)
    _app, url = background
    with browser_page(url, "settings/panel") as page:
        card = page.locator("#panel-video-experiment")
        radio(page, "Quality", "20").click()
        waits_for(page, lambda: settings_config().device.video.crf == 20)
        card.get_by_role("button", name="Run every step by itself").click()
        expect(card.locator(".experiment-run-state")).to_contain_text("Done", timeout=30000)
        rows = card.locator(".experiment-results tbody tr")
        expect(rows.first).to_contain_text("auto cap, four minutes")
        expect(rows.first).to_have_attribute("data-verdict", "no video")
        assert settings_config().device.video.crf == 20  # the person's own, back
        expect(card.get_by_role("button", name="Copy the table")).to_be_visible()


def test_live_says_how_the_panel_runs_in_a_calm_sentence(app_url):  # noqa: F811
    """The numbers are the detail; the sentence above them says what they mean: smooth, slower
    than planned (and what helps), picture by picture, waiting (and why), paused, a fault."""

    def running(**numbers):
        return app_answer(**numbers)["panel"]

    with browser_page(app_url, "live", allow=PICTURE) as page:

        def of(background, status):
            return page.evaluate(RUN_STATE, [background, status])

        alone = of(False, None)
        assert alone[:2] == ["warn", "Libre Panel is not running in the background"]
        assert "Start menu" in alone[2] and "libre-panel" not in alone[2]  # no command
        assert of(True, running())[:2] == ["ok", "Runs smoothly at 50 fps"]
        slow = of(True, running(fps=30.2))
        assert slow[:2] == ["warn", "Runs slower than planned: 30 of 50 fps"]
        assert "Fewer frames per second" in slow[2]
        single = of(True, running(streaming=False, target_fps=5, fps=3.6))
        assert single == [
            "ok",
            "Runs, picture by picture",
            "A new picture whenever something changes, up to 5 a second.",
        ]
        hiccups = of(True, running(usb_errors=1, panel_restarts=1))
        assert hiccups[2] == "2 hiccups since it started; it recovered by itself each time."
        waiting = of(True, {"state": "waiting", "detail": "no panel", "stats": {}})
        assert waiting[:2] == ["warn", "Waiting for the panel"]
        assert waiting[2].startswith("Libre Panel says: no panel")
        assert of(True, {"state": "paused", "stats": {}})[:2] == ["off", "Paused"]
        broken = of(True, {"state": "error", "detail": "no USB library", "stats": {}})
        assert broken == ["error", "The panel needs attention", "no USB library"]


def test_live_names_things_as_settings_does(app_url, isolated_home):  # noqa: F811
    """A running panel in smooth motion: the calm line on top, the numbers under "In numbers";
    the services and the sensors by the names Settings gives them."""
    import json

    choose_theme(isolated_home)
    panel = {"background": True, "panel": app_answer()["panel"]}

    def handler(route, request):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(panel))

    routes = {"**/api/plugins/control/panel": handler}
    with browser_page(app_url, "live", routes=routes, allow=PICTURE) as page:
        status = page.locator(".live-status")
        expect(status).to_have_text("Runs smoothly at 50 fps")
        expect(status).to_have_attribute("data-tone", "ok")
        expect(page.locator(".live-numbers .live-tile")).to_have_count(5)
        expect(page.locator(".live-side .section-title").first).to_have_text("Smooth motion")
        expect(page.locator("[data-service=notices] .live-row-name")).to_have_text("Notices")
        expect(page.locator("[data-service=memory] .live-row-name")).to_have_text(
            "Round report and history"
        )


def test_a_late_read_of_the_settings_does_not_undo_a_click(app_url, isolated_home):  # noqa: F811
    """Settings → Panel reads config.toml as it opens. On a busy machine that answer came after a
    quick click's write and put the old value back: the choice jumped back, and a click on it
    then wrote nothing (it looked chosen already)."""
    from libre_panel.config import set_config_value

    choose_theme(isolated_home, "lumen")
    set_config_value("device.orientation", "portrait")
    with browser_page(app_url, "home") as page:
        # from now on every read of the settings answers 1.5 s late; writes as ever
        page.evaluate(
            """() => {
              const fetch = window.fetch;
              window.fetch = async (url, options) => {
                const answer = await fetch(url, options);
                if (String(url).includes("/api/settings") && options?.method === "GET")
                  await new Promise((done) => setTimeout(done, 1500));
                return answer;
              };
            }"""
        )
        page.goto(f"{app_url}#/settings/panel")
        mounted = page.locator(".settings-row", has_text="How the panel is mounted")
        mounted.get_by_role("radio", name="Horizontal").click()
        waits_for(page, lambda: settings_config().device.orientation == "landscape")
        page.wait_for_timeout(2500)  # the read from opening the section has answered by now
        horizontal = mounted.get_by_role("radio", name="Horizontal")
        expect(horizontal).to_have_attribute("aria-checked", "true")
        mounted.get_by_role("radio", name="Vertical").click()
        waits_for(page, lambda: settings_config().device.orientation == "portrait")


def test_other_programs_get_a_key_and_an_address_once_it_is_switched_on(app_url, isolated_home):  # noqa: F811
    from libre_panel import notify

    choose_theme(isolated_home, "lumen")
    with browser_page(app_url, "settings/services") as page:
        card = page.locator("#notify")
        expect(card).to_contain_text("Notices from other programs")
        expect(card.locator("[data-notify-address]")).to_have_count(0)
        card.get_by_role("switch", name="Let other programs show notices").click()
        waits_for(page, lambda: settings_config().allow_notify is True)
        address = card.locator("[data-notify-address]")
        expect(address).to_contain_text("/api/notify?key=")
        expect(address).to_contain_text(f"/api/notify?key={notify.read_key()}&text=")
        expect(card.locator("[data-notify-command]")).to_contain_text('notify "Render finished"')
        card.get_by_role("button", name="Try it").click()  # no background app: it says so
        expect(page.locator(".toast").last).to_contain_text("Not shown")
        old = notify.read_key()
        card.get_by_role("button", name="New key").click()
        page.locator(".dialog").get_by_role("button", name="New key").click()
        expect(address).not_to_contain_text(old)
        assert notify.read_key() != old


def test_ai_activity_shows_what_it_adds_then_sets_up_and_takes_it_out(
    app_url,  # noqa: F811
    isolated_home,
    tmp_path,
    monkeypatch,
):
    from libre_panel import agents
    from libre_panel.config import set_config_value

    home = tmp_path / "user"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(agents, "program", lambda name: "/bin/claude" if name == "claude" else None)
    monkeypatch.setattr(agents, "BOARD", agents.Board())  # (nothing heard from anyone yet)
    choose_theme(isolated_home, "lumen")
    set_config_value("services.enabled", ["agents"])
    settings = home / ".claude" / "settings.json"
    with browser_page(app_url, "settings/services") as page:
        card = page.locator("[data-service='agents']")
        expect(card).to_contain_text("AI activity")
        claude = card.locator("[data-agent='claude']")
        expect(claude).to_contain_text("Not set up yet")
        expect(card.locator("[data-agent='codex']")).to_contain_text("Not found on this PC")
        claude.get_by_role("button", name="Set up").click()
        shown = page.locator(".dialog [data-agents-preview]")
        expect(shown).to_contain_text("/api/ai/hook?agent=claude")
        expect(shown).to_contain_text("statusLine")
        assert not settings.exists()  # not before it is confirmed
        page.locator(".dialog").get_by_role("button", name="Set up").click()
        expect(claude.get_by_role("button", name="Remove")).to_be_visible()
        assert agents.installed(agents.read_settings(settings))["status_line"]
        # set up, nothing heard yet: where it reports from
        expect(claude.locator(".settings-row-status")).to_have_text("Set up; no report from it yet")
        expect(claude.locator("[data-agents-waiting]")).to_contain_text("Sessions in the cloud")
        agents.BOARD.hook("claude", {"hook_event_name": "SessionStart", "session_id": "s"})
        expect(claude.locator(".settings-row-status")).to_contain_text(
            "its last report came", timeout=10_000
        )  # (the card asks again every few seconds)
        expect(claude.locator("[data-agents-waiting]")).to_have_count(0)
        claude.get_by_role("button", name="Remove").click()
        expect(claude).to_contain_text("Not set up yet")
        assert agents.read_settings(settings) == {}
        # set up for the address Libre Panel had before its port was taken: set it up again
        agents.write_settings(settings, agents.claude_plan({}, 1, agents.key(create=True), True))
        page.reload()
        claude = page.locator("[data-service='agents'] [data-agent='claude']")
        expect(claude.locator(".settings-row-status")).to_have_text(
            "Set up for another address: set it up again"
        )
        claude.get_by_role("button", name="Set up again").click()
        page.locator(".dialog").get_by_role("button", name="Set up").click()
        expect(claude.get_by_role("button", name="Set up again")).to_have_count(0)
        assert agents.installed(agents.read_settings(settings))["port"] != 1


def test_a_shortcut_is_set_by_pressing_the_keys_and_taken_away_again(app_url, isolated_home):  # noqa: F811
    choose_theme(isolated_home, "lumen")
    # (the settings refuse two of the shortcuts: a 400 the browser logs)
    with browser_page(app_url, "settings/system", allow=("/api/settings",)) as page:
        card = page.locator("#hotkeys")
        expect(card).to_contain_text("Keyboard shortcuts")
        expect(card).to_contain_text("They work while Libre Panel runs in the background.")
        pause = card.locator("[data-hotkey='pause']")
        expect(pause).to_have_text("Not set")
        pause.click()
        expect(pause).to_have_text("Press the keys …")
        page.keyboard.down("Control")
        page.keyboard.down("Alt")  # a modifier alone: it waits for the key
        expect(pause).to_have_attribute("aria-pressed", "true")
        page.keyboard.press("KeyP")
        page.keyboard.up("Alt")
        page.keyboard.up("Control")
        waits_for(page, lambda: settings_config().hotkeys == {"pause": "Ctrl+Alt+P"})
        expect(pause).to_have_text("Ctrl+Alt+P")
        expect(page.locator(".palette")).to_have_count(0)  # Ctrl+K and the rest waited
        # Shift alone would take a letter from typing: refused, nothing written
        brighter = card.locator("[data-hotkey='brightness_up']")
        brighter.click()
        expect(brighter).to_have_text("Press the keys …")
        page.keyboard.press("Shift+KeyB")
        expect(page.locator(".toast").last).to_contain_text("needs Ctrl, Alt or Win")
        assert settings_config().hotkeys == {"pause": "Ctrl+Alt+P"}
        # the same keys for a second action: refused
        card.locator("[data-hotkey='open']").click()
        page.keyboard.press("Control+Alt+KeyP")
        expect(page.locator(".toast").last).to_contain_text("another action")
        brighter.click()
        page.keyboard.press("Escape")  # changed one's mind
        expect(brighter).to_have_text("Not set")
        # all of them off at once: the keys stay, greyed, and come back with the switch
        switch = card.get_by_role("switch", name="Use the shortcuts")
        expect(switch).to_have_attribute("aria-checked", "true")
        switch.click()
        waits_for(page, lambda: settings_config().hotkeys_enabled is False)
        expect(card).to_contain_text("Off: no key is taken")
        expect(pause).to_be_disabled()
        expect(pause).to_have_text("Ctrl+Alt+P")
        assert settings_config().hotkeys == {"pause": "Ctrl+Alt+P"}
        switch.click()
        waits_for(page, lambda: settings_config().hotkeys_enabled is True)
        expect(pause).to_be_enabled()
        card.get_by_role("button", name="Take Pause or resume the panel's shortcut away").click()
        waits_for(page, lambda: settings_config().hotkeys == {})


def test_the_running_app_says_which_shortcut_another_program_has(app_url, isolated_home):  # noqa: F811
    from libre_panel.config import set_config_value

    choose_theme(isolated_home, "lumen")
    set_config_value("hotkeys.pause", "Ctrl+Alt+P")
    set_config_value("hotkeys.open", "Ctrl+Alt+L")
    answer = app_answer()
    answer["hotkeys"] = {
        "supported": True,
        "why": "",
        "keys": {
            "pause": {"combo": "Ctrl+Alt+P", "error": ""},
            "open": {"combo": "Ctrl+Alt+L", "error": "taken"},
        },
    }
    routes = {"**/api/app": answer_route(answer)}
    with browser_page(app_url, "settings/system", routes=routes) as page:
        card = page.locator("#hotkeys")
        expect(card.locator(".settings-row", has_text="Open Libre Panel")).to_contain_text(
            "Another program has this combination"
        )
        expect(card).not_to_contain_text("They work while Libre Panel runs")
        answer["hotkeys"] = {
            "supported": False,
            "why": "Wayland lets no program take keys for itself.",
            "keys": {},
        }
        page.reload()
        expect(card).to_contain_text("Wayland lets no program take keys for itself.")


def answer_route(data):
    """A route's handler that answers what ``data`` holds when it is asked (it may change)."""

    def handler(route, request):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    return handler


def test_the_look_of_the_weather_symbols_is_chosen_on_a_tile_with_its_picture(
    app_url,  # noqa: F811
    isolated_home,
):
    """Settings → Weather and place: two tiles, each with a picture of its look; the second is the
    default, a click writes ``symbols`` to config.toml (the panel takes it at once: no note of a
    restart). The palette finds it under its own name."""
    choose_theme(isolated_home, "nimbus")
    with browser_page(app_url, "settings/weather") as page:
        tiles = page.locator(".symbol-pack-tile")
        expect(tiles).to_have_count(2)
        expect(page.locator('.symbol-pack-tile[data-pack="vivid"]')).to_have_attribute(
            "aria-checked", "true"
        )
        page.wait_for_function(
            "[...document.querySelectorAll('.symbol-pack-tile img')]"
            ".every((i) => i.naturalWidth > 0)"
        )  # each with its picture
        page.locator('.symbol-pack-tile[data-pack="classic"]').click()
        waits_for(page, lambda: settings_config().symbols == "classic")
        expect(page.locator('.symbol-pack-tile[data-pack="classic"]')).to_have_attribute(
            "aria-checked", "true"
        )
        expect(page.locator("#symbols .note.warn")).to_have_count(0)
        page.keyboard.press("Control+k")
        page.keyboard.type("weather symbols")
        expect(page.locator(".palette")).to_contain_text("Look of the weather symbols")


@pytest.mark.parametrize(("model", "said"), [("turing-3.5", True), ("turing-9.2-usb", False)])
def test_an_older_serial_panel_says_what_it_cannot_do(app_url, isolated_home, model, said):  # noqa: F811
    """Settings → Panel: a panel on a virtual COM port is slower and has no smooth motion, and
    the card says so under its name; a USB panel's card does not. Its motion is calm by itself,
    and full motion is a click away."""
    isolated_home.mkdir(parents=True, exist_ok=True)
    (isolated_home / "config.toml").write_text(
        f'theme = "libre-default"\n\n[device]\nmodel = "{model}"\n', encoding="utf-8"
    )
    with browser_page(app_url, "settings/panel") as page:
        page.wait_for_selector("#panel-found")
        note = page.locator("#panel-found [data-serial-note]")
        motion = page.locator(".motion-row")
        if said:
            expect(note).to_contain_text("no smooth motion")
            expect(motion.locator(".settings-row-status")).to_have_text(
                "Calm, as it is a USB serial panel"
            )
        else:
            expect(page.locator("#panel-found")).to_contain_text("USB")
            expect(note).to_have_count(0)
            expect(motion.locator(".settings-row-status")).to_have_count(0)
        motion.get_by_role("radio", name="Full motion").click()
        waits_for(page, lambda: settings_config().device.motion == "full")
        expect(motion.locator(".settings-row-status")).to_have_count(0)


def test_a_panel_written_from_notes_says_what_it_cannot_do(app_url, isolated_home):  # noqa: F811
    """Settings → Panel: what the panel cannot do under its name, in place of what an older
    serial panel says (the DS916 is on a serial port too, but takes whole pictures)."""
    isolated_home.mkdir(parents=True, exist_ok=True)
    (isolated_home / "config.toml").write_text(
        'theme = "libre-default"\n\n[device]\nmodel = "jonsbo-ds916"\n', encoding="utf-8"
    )
    with browser_page(app_url, "settings/panel") as page:
        page.wait_for_selector("#panel-found")
        note = page.locator("#panel-found [data-limits-note]")
        expect(note).to_contain_text("Not yet seen on the hardware")
        expect(note).to_contain_text("no backlight control")
        expect(page.locator("#panel-found [data-serial-note]")).to_have_count(0)


def test_a_port_that_may_be_a_turing_panel_is_chosen_with_a_click(app_url, isolated_home):  # noqa: F811
    """Settings → Panel: a port with the USB ids other devices have too (an awake Turing rev. C
    panel whose serial number Windows does not tell) is offered with the panels it may be; a
    click on one is the panel."""
    models = [
        {"id": m, "label": label, "size": size, "shape": shape, "connection": "serial",
         "port": "COM12", "sure": False, "video": False}
        for m, label, size, shape in (
            ("turing-2.1", 'Turing 2.1" round', [480, 480], "round"),
            ("turing-5", 'Turing 5"', [800, 480], "rect"),
            ("turing-8.8", 'Turing 8.8"', [1920, 480], "rect"),
        )
    ]  # fmt: skip
    panel = {
        "configured": "auto", "found": None, "model": None, "running": None,
        "maybe": [{"port": "COM12", "models": models}],
        "video": {"mode": "auto", "fps": 50, "on": False, "usb": True, "gaps": []},
        "weather_theme": None,
    }  # fmt: skip
    with browser_page(app_url, "settings/panel", routes={"**/api/panel": answer(panel)}) as page:
        maybe = page.locator('[data-maybe-port="COM12"]')
        expect(maybe).to_contain_text("COM12 may be a Turing panel (rev. C)")
        expect(maybe.locator("button")).to_have_count(3)
        maybe.get_by_role("button", name="Turing 8.8″").click()
        waits_for(page, lambda: settings_config().device.model == "turing-8.8")
