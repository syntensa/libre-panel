"""The app (Design, the command palette) in a real browser; skipped when Playwright or
Chromium are missing. CI runs it with the other browser tests (the desktop job)."""

import contextlib
import threading

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from libre_panel.editor.server import make_server  # noqa: E402


@pytest.fixture
def app_url():
    srv = make_server(port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()
    srv.server_close()
    srv.editor_state.close()


def expected_404(message, allow=()):
    """The app asks for the hardware's names; a server without them (an older background
    app, as one test plays) answers 404, the app hides them, and Chromium logs an error. So
    is a request that a test makes fail on purpose (``allow``: the ends of their addresses)."""
    return message.text.startswith("Failed to load resource") and message.location.get(
        "url", ""
    ).endswith(("/api/hardware", *allow))


@contextlib.contextmanager
def browser_page(url, view="design", strict_csp=False, size=(1440, 900), routes=None, allow=()):
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # browser not installed
            pytest.skip(f"chromium not available: {exc}")
        # The app's CSP forbids eval (good); the tests' own probes need it, except where
        # the CSP itself is what is tested.
        context = browser.new_context(
            viewport={"width": size[0], "height": size[1]}, bypass_csp=not strict_csp
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on(
            "console",
            lambda m: (
                errors.append(m.text) if m.type == "error" and not expected_404(m, allow) else None
            ),
        )
        for pattern, handler in (routes or {}).items():  # stand-ins for the server's answers
            page.route(pattern, handler)
        page.goto(f"{url}#/{view}")
        page.wait_for_selector(".panel-canvas .preview" if view == "design" else ".view > *")
        page.wait_for_timeout(300)
        yield page
        assert errors == []
        browser.close()


@pytest.fixture
def page(app_url):
    with browser_page(app_url) as page:
        yield page


def js(page, expression):
    return page.evaluate(expression)


def widget(page, id):
    return js(page, f"window.libre.doc.widgetById({id!r})")


def test_the_app_opens_without_breaking_its_rules(app_url):
    """The shell, the design view and the preview, under the app's own CSP."""
    with browser_page(app_url, strict_csp=True) as page:
        assert page.locator(".tab[aria-current='page']").inner_text() == "Design"
        assert page.locator(".theme-name").inner_text() == "Libre Default"
        assert page.locator(".tile").count() > 30  # modules, building blocks, widgets
        assert js(page, "window.libre.store.get().render.size") == [480, 320]
        for view in ("home", "look", "live"):  # the other views' places
            page.goto(f"{app_url}#/{view}")
            page.wait_for_selector(".view .slot")
        page.goto(f"{app_url}#/settings/about")
        current = page.locator(".settings-nav [aria-current='page']")
        assert current.inner_text() == "About Libre Panel"


def test_select_move_undo_redo(page):
    box = page.locator('.overlay .box[data-id="cpu-ring"]').bounding_box()
    start = (widget(page, "cpu-ring")["x"], widget(page, "cpu-ring")["y"])
    # from the ring's lower part: the toolbar sits above a selection
    page.mouse.move(box["x"] + 10, box["y"] + box["height"] - 10)
    page.mouse.down()
    page.mouse.move(box["x"] + 60, box["y"] + box["height"] + 20, steps=10)
    page.mouse.up()
    moved = (widget(page, "cpu-ring")["x"], widget(page, "cpu-ring")["y"])
    assert moved != start
    assert js(page, "window.libre.store.get().selection") == ["cpu-ring"]
    assert page.locator(".floating-toolbar").is_visible()
    assert page.locator(".unsaved").is_visible()
    assert page.title() == "● Libre Default – Libre Panel"  # unsaved, in the tab or window
    page.keyboard.press("Control+z")
    assert (widget(page, "cpu-ring")["x"], widget(page, "cpu-ring")["y"]) == start
    page.locator(".topbar [aria-label^='Redo']").click()
    assert (widget(page, "cpu-ring")["x"], widget(page, "cpu-ring")["y"]) == moved
    page.keyboard.press("ArrowRight")  # a nudge
    assert widget(page, "cpu-ring")["x"] == moved[0] + 1


def test_toolbar_sensor_and_finish(page):
    js(page, "window.libre.doc.setSelection(['cpu-ring'])")
    toolbar = page.locator(".floating-toolbar")
    toolbar.locator(".tool-text", has_text="Light").click()
    assert widget(page, "cpu-ring")["finish"] == "lit"
    toolbar.locator(".tool-sensor").click()
    picker = page.locator(".sensor-picker")
    picker.locator("input").fill("gpu load")
    picker.locator(".sensor-row[data-key='gpu.load']").click()
    assert widget(page, "cpu-ring")["sensor"] == "gpu.load"
    assert page.locator(".sensor-picker").count() == 0  # it closed
    # the inspector follows: the sensor and the tile of the finish
    inspector = page.locator(".inspector")
    assert "GPU load" in inspector.locator(".sensor-button").inner_text()
    assert inspector.locator(".finish-tile[aria-checked='true']").inner_text() == "Light"
    page.keyboard.press("Control+z")
    assert widget(page, "cpu-ring")["sensor"] == "cpu.load"
    # only the finishes that show on a type: acrylic is for lettering
    assert "Acrylic" not in toolbar.locator(".tool-text").all_inner_texts()
    js(page, "window.libre.doc.setSelection(['cpu-label'])")
    toolbar.locator(".tool-text", has_text="Acrylic").click()
    assert widget(page, "cpu-label")["finish"] == "acrylic"


def test_a_module_from_the_library(page):
    count = js(page, "window.libre.doc.widgets().length")
    tile = page.locator('.tile[data-item="module:ring"]').bounding_box()
    canvas = page.locator(".panel-canvas").bounding_box()
    page.mouse.move(tile["x"] + 30, tile["y"] + 30)
    page.mouse.down()
    page.mouse.move(tile["x"] + 70, tile["y"] + 50, steps=4)
    page.mouse.move(
        canvas["x"] + canvas["width"] * 0.8, canvas["y"] + canvas["height"] * 0.7, steps=10
    )
    assert page.locator(".drop").count() == 1  # where it would go, on the cells
    page.mouse.up()
    assert js(page, "window.libre.doc.widgets().length") == count + 1
    added = js(page, "window.libre.doc.single()")
    assert added["type"] == "module" and added["module"] == "ring"
    assert page.locator(".inspector .inspector-title").inner_text() == "Ring"
    # a click adds one at the first free cells
    page.locator('.tile[data-item="module:clock"]').click()
    assert js(page, "window.libre.doc.single().module") == "clock"


def test_the_command_palette_finds_and_runs(page):
    count = js(page, "window.libre.doc.widgets().length")
    page.keyboard.press("Control+k")
    palette = page.locator(".palette")
    palette.locator("input").fill("insert big num")
    first = palette.locator(".palette-item.active")
    assert "Big number" in first.inner_text() and first.locator("b").count() > 0
    page.keyboard.press("Enter")
    assert page.locator(".palette").count() == 0
    page.wait_for_function(f"window.libre.doc.widgets().length === {count + 1}")  # it ran
    assert js(page, "window.libre.doc.single().module") == "stat"
    # by keyboard, with the language: everything is reachable from here
    page.keyboard.press("Control+k")
    palette.locator("input").fill("deutsch")
    page.keyboard.press("Enter")
    page.wait_for_function("document.documentElement.lang === 'de'")
    assert page.locator(".tab[aria-current='page']").inner_text() == "Gestalten"
    page.keyboard.press("Control+k")
    palette.locator("input").fill("lüf")  # an umlaut, as typed in German
    assert palette.locator(".palette-item").count() > 0
    palette.locator("input").fill("spur-classic")  # a theme by its id, shown by its name
    assert "SPUR Classic" in palette.locator(".palette-item.active").inner_text()


def test_save_as_and_to_the_panel(page, isolated_home):
    from test_service import wait_for

    from libre_panel.config import load_config
    from libre_panel.theme.model import load_theme

    js(page, "window.libre.doc.edit('t', (theme) => { theme.name = 'Mine'; })")
    page.keyboard.press("Control+s")  # a built-in theme: saved under a new name
    dialog = page.locator(".dialog")
    dialog.locator("input").fill("bad name!")
    assert dialog.locator("button[type='submit']").is_disabled()
    dialog.locator("input").fill("mine")
    dialog.locator("button[type='submit']").click()
    page.wait_for_function("!window.libre.store.get().dirty")
    assert load_theme(isolated_home / "themes" / "mine").name == "Mine"
    assert page.locator(".unsaved").count() == 0
    page.locator(".btn-apply").click()
    # (the save's success toast may still be up: wait for the setting, not for a toast)
    assert wait_for(lambda: load_config().theme == "mine")
    page.wait_for_function("document.querySelector('.toast.success') !== null")


def test_inspector_details_and_theme_settings(page):
    js(page, "window.libre.doc.setSelection(['cpu-ring'])")
    page.locator(".details-button").click()
    x = page.locator(".inspector .quad .number-field input").first
    x.fill("33")
    x.press("Enter")
    assert widget(page, "cpu-ring")["x"] == 33
    page.locator(".inspector [aria-label='Back']").click()
    # an area on the panel (where a bar's lit glow stays): x, y, w, h, or none
    js(page, "window.libre.doc.setSelection(['disk-bar'])")
    page.locator(".details-button").click()
    page.locator(".inspector button", has_text="Set an area").click()
    area = widget(page, "disk-bar")["glow_area"]
    assert len(area) == 4 and area[2] > 0 and area[3] > 0
    page.locator(".inspector button", has_text="Remove the area").click()
    assert widget(page, "disk-bar")["glow_area"] is None
    page.locator(".inspector [aria-label='Back']").click()
    page.keyboard.press("Escape")  # nothing selected: the theme's settings
    assert page.locator(".inspector .inspector-title").inner_text() == "Libre Default"
    name = page.locator(".inspector input[aria-label='Name']")
    name.fill("Renamed")
    assert page.locator(".theme-name").inner_text() == "Renamed"
    # a look: its colours and module style in one step, undone in one
    before = js(page, "window.libre.doc.theme().palette.accent")
    page.locator(".inspector .look-tile", has_text="Neon").click()
    assert js(page, "window.libre.doc.theme().palette.accent") == "#FF4FD8"
    assert js(page, "window.libre.doc.theme().style.card") == "outline"
    assert page.locator(".inspector .look-tile[aria-checked='true']").inner_text() == "Neon"
    page.keyboard.press("Control+z")
    assert js(page, "window.libre.doc.theme().palette.accent") == before


def test_pages_of_the_panel(app_url, isolated_home):
    from libre_panel.config import set_config_value

    set_config_value("modes.game.theme", "slate")
    with browser_page(app_url) as page:
        tabs = page.locator(".page-tab")
        assert [tabs.nth(i).inner_text() for i in range(tabs.count())] == ["Normal", "In game"]
        tabs.nth(1).click()
        page.wait_for_function("window.libre.store.get().doc.id === 'slate'")
        assert js(page, "window.libre.store.get().page") == "game"


def test_the_panel_chip_changes_the_panel(page):
    page.locator(".panel-chip").click()
    page.locator(".model-row", has_text="Turing 9.2").click()
    page.wait_for_function("window.libre.store.get().render.size[0] === 1920")
    assert page.locator(".hidden-strip.top").count() == 1  # the frame hides a strip
    assert "hidden by the frame" in page.locator(".canvas-size").inner_text()
    page.keyboard.press("Control+z")
    page.wait_for_function("window.libre.store.get().render.size[0] === 480")


def test_the_running_panel_in_the_chip(isolated_home):
    """With the background app: its state in the chip, its controls in the popover."""
    from test_service import FakeRegistry, wait_for, write_config

    from libre_panel.autostart import Autostart
    from libre_panel.config import load_config
    from libre_panel.service import BackgroundApp

    write_config(isolated_home)
    app = BackgroundApp(port=0, autostart=Autostart("win32", registry=FakeRegistry()))
    url = app.start()
    try:
        with browser_page(url) as page:
            chip = page.locator(".panel-chip")
            page.wait_for_function("document.querySelector('.panel-chip.showing') !== null")
            assert "Showing" in chip.inner_text()
            chip.click()
            popover = page.locator(".popover")
            popover.locator("[role='switch'][aria-label='Upside down']").click()
            assert wait_for(lambda: load_config().device.rotate == 180)
            popover.locator(".btn", has_text="Pause panel").click()
            assert wait_for(lambda: app.panel.paused)
            page.wait_for_function("document.querySelector('.panel-chip.paused') !== null")
    finally:
        app.quit()
        app.shutdown()


# -- Settings: each section's main path, against the real server and config.toml ----------------


def settings_config():
    from libre_panel.config import load_config

    return load_config()


def waits_for(page, predicate, timeout=10.0):
    """Until the predicate holds (the app writes config.toml a moment after a click)."""
    from test_service import wait_for

    assert wait_for(predicate, timeout), "config.toml did not change as expected"


def game_option(name):
    """An option of the game service as config.toml has it now (None: it is not there)."""
    return settings_config().services.options.get("game", {}).get(name)


def radio(page, group, name):
    return page.locator(f"[role='radiogroup'][aria-label='{group}'] [role='radio']", has_text=name)


def settings_app(isolated_home, autostart=None):
    """The background app with a panel that writes a PNG file, as the tray runs it."""
    from test_autostart import FakeTasks
    from test_service import FakeRegistry, write_config

    from libre_panel.autostart import Autostart
    from libre_panel.service import BackgroundApp

    write_config(isolated_home)
    auto = autostart or Autostart("win32", registry=FakeRegistry(), tasks=FakeTasks())
    return BackgroundApp(port=0, autostart=auto, autostart_command=["/opt/libre-panel", "tray"])


def test_the_settings_open_under_the_strict_csp(app_url):
    """No section needs what the app's CSP forbids (inline scripts, inline styles)."""
    with browser_page(app_url, "settings/panel", strict_csp=True) as page:
        for section in ("panel", "sensors", "services", "weather", "system", "about"):
            page.goto(f"{app_url}#/settings/{section}")
            page.wait_for_selector(".settings-main .settings-card")
            page.wait_for_timeout(200)


def test_settings_panel(app_url, isolated_home, monkeypatch):
    """The panel found, the video mode (and its clip name, written only when typed), the
    frame rate, brightness, upside down, another panel, and Advanced."""
    from test_editor_settings import lacking_ffmpeg, panel_on_the_bus

    panel_on_the_bus(monkeypatch, "turing-9.2-usb")
    lacking_ffmpeg(monkeypatch)
    with browser_page(app_url, "settings/panel") as page:
        assert page.locator(".panel-found-name").inner_text() == "Turing 9.2″"
        detail = page.locator(".panel-found-detail").inner_text()
        assert "USB · 1920 × 480" in detail and "Connected" in detail
        status = page.locator("#main .settings-row-status").first
        assert status.inner_text() == "Off for now. Missing: the name of the standby clip, ffmpeg."
        # the clip's name is never made up: the placeholder is no value, and nothing is
        # written until the person types it
        clip = page.get_by_label("Name of the standby clip")
        assert clip.get_attribute("placeholder") == "usr/data/standby.h264"
        assert clip.input_value() == ""
        clip.focus()
        clip.press("Tab")
        page.wait_for_timeout(200)
        assert not (isolated_home / "config.toml").exists()
        lacking_ffmpeg(monkeypatch, found=True)  # ffmpeg is installed meanwhile
        clip.fill("usr/data/standby.h264")
        clip.press("Enter")
        waits_for(
            page, lambda: settings_config().device.video.local_clip == "usr/data/standby.h264"
        )
        video = page.locator("#main .settings-row-status").first
        playwright.expect(video).to_contain_text("On, 50 fps")
        radio(page, "Frames per second", "30").click()
        waits_for(page, lambda: settings_config().device.video.fps == 30)
        playwright.expect(video).to_contain_text("On, 30 fps")
        radio(page, "Video mode", "Off").click()
        waits_for(page, lambda: settings_config().device.video.mode == "off")
        playwright.expect(video).to_contain_text("single pictures")
        assert page.get_by_label("Name of the standby clip").count() == 0  # not needed now
        radio(page, "Video mode", "Automatic").click()
        waits_for(page, lambda: settings_config().device.video.mode == "auto")

        slider = page.get_by_role("slider", name="Brightness")
        slider.focus()
        slider.press("ArrowLeft")
        slider.press("ArrowLeft")
        waits_for(page, lambda: settings_config().device.brightness == 50)
        page.get_by_role("switch", name="Upside down").click()
        waits_for(page, lambda: settings_config().device.rotate == 180)
        page.get_by_role("switch", name="Upside down").click()
        waits_for(page, lambda: settings_config().device.rotate == 0)

        # another panel, by hand; the one on the bus is still told as it is
        page.get_by_role("button", name="Another panel").click()
        page.locator(".popover [role='option']", has_text="Turing 8.8").last.click()
        waits_for(page, lambda: settings_config().device.model == "turing-8.8-usb")
        playwright.expect(page.locator(".panel-found-name")).to_contain_text("8.8")
        playwright.expect(page.locator(".panel-found")).to_contain_text(
            "Another panel is connected: Turing 9.2″"
        )
        page.get_by_role("button", name="Another panel").click()
        page.locator(".popover [role='option']", has_text="Detect automatically").click()
        waits_for(page, lambda: settings_config().device.model == "auto")

        # Advanced: the driver and the files it names
        page.locator(".settings-disclosure", has_text="Advanced").click()
        page.get_by_label("Driver").select_option("virtual")
        waits_for(page, lambda: settings_config().device.driver == "virtual")
        output = page.get_by_label("Picture file")
        assert output.input_value() == "libre-panel-frame.png"
        output.fill("shown.png")
        output.press("Enter")
        waits_for(page, lambda: settings_config().device.output == "shown.png")
        port = page.get_by_label("Serial port")
        port.fill("COM5")
        port.press("Enter")
        waits_for(page, lambda: settings_config().device.port == "COM5")
    text = (isolated_home / "config.toml").read_text(encoding="utf-8")
    assert "# Libre Panel configuration" in text  # the file is still the person's


def test_settings_panel_without_a_panel(app_url, monkeypatch):
    from test_editor_settings import panel_on_the_bus

    panel_on_the_bus(monkeypatch)
    with browser_page(app_url, "settings/panel") as page:
        assert page.locator(".panel-found-name").inner_text() == "No panel found"
        assert "Plug it in" in page.locator(".panel-found-detail").inner_text()
        assert page.locator(".panel-outline.none").count() == 1
        status = page.locator("#main .settings-row-status").first.inner_text()
        assert status == "Starts when a panel it runs on is connected."


def test_settings_panel_says_why_it_waits(app_url):
    """The running app knows why no panel is in use (no permission, a library missing): said."""
    import json

    found = {
        "configured": "auto",
        "found": None,
        "model": None,
        "running": {"state": "waiting", "detail": "cannot open the USB device: access denied"},
        "video": {"mode": "auto", "fps": 50, "on": False, "usb": True, "gaps": []},
    }

    def answer(route, request):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(found))

    with browser_page(app_url, "settings/panel", routes={"**/api/panel": answer}) as page:
        why = page.locator(".panel-found-why")
        playwright.expect(why).to_contain_text("Waiting for the panel: cannot open the USB device")
        assert page.locator(".panel-found-name").inner_text() == "No panel found"


def test_settings_sensors(app_url, isolated_home):
    """The sources in their order and on or off, how often they are read."""
    from libre_panel.config import default_providers

    with browser_page(app_url, "settings/sensors") as page:
        page.wait_for_selector(".source-row")
        rows = page.locator(".source-row:not(.unused)")
        assert [rows.nth(i).get_attribute("data-source") for i in range(rows.count())] == (
            default_providers()
        )  # in the order they are asked
        assert page.locator(".source-row.unused[data-source='demo']").count() == 1
        assert page.locator(".source-row[data-source='media']").count() == 0  # starts by itself
        assert page.locator(".note.warn").count() == 0
        page.locator(".source-row[data-source='audio'] [role='switch']").click()
        waits_for(page, lambda: "audio" not in settings_config().sensors.providers)
        # the sources are made when the panel starts: said, with nothing to restart here
        note = page.locator(".note.warn")
        assert "after Libre Panel is started again" in note.inner_text()
        assert note.locator("button").count() == 0
        page.locator(".source-row[data-source='audio'] [role='switch']").click()
        waits_for(page, lambda: "audio" in settings_config().sensors.providers)
        # a source that is switched on goes before psutil, which fills in the rest
        waits_for(page, lambda: settings_config().sensors.providers == ["load", "audio", "psutil"])
        page.get_by_role("button", name="Ask Fast load later").click()
        waits_for(page, lambda: settings_config().sensors.providers[:2] == ["audio", "load"])
        page.get_by_role("button", name="Back to the usual").click()
        waits_for(page, lambda: settings_config().sensors.providers == default_providers())
        playwright.expect(page.get_by_role("button", name="Back to the usual")).to_have_count(0)
        # psutil alone would be read as the usual list again (config.py), so that switch is
        # not offered: quick clicks follow each other, and the last one is refused
        page.locator(".source-row[data-source='audio'] [role='switch']").click()
        page.locator(".source-row[data-source='demo'] [role='switch']").click()
        waits_for(page, lambda: settings_config().sensors.providers == ["load", "demo", "psutil"])
        page.locator(".source-row[data-source='demo'] [role='switch']").click()
        waits_for(page, lambda: settings_config().sensors.providers == ["load", "psutil"])
        playwright.expect(
            page.locator(".source-row[data-source='load'] [role='switch']")
        ).to_be_disabled()
        playwright.expect(
            page.locator(".source-row[data-source='psutil'] [role='switch']")
        ).to_be_enabled()
        # how often they are read: empty is the theme's choice
        interval = page.get_by_label("Reading interval")
        assert interval.input_value() == "" and interval.get_attribute("placeholder") == "Automatic"
        interval.fill("500")
        interval.press("Enter")
        waits_for(page, lambda: settings_config().refresh_ms == 500)
        playwright.expect(page.locator("#main")).to_contain_text("Every 0.5 s")
        page.locator(".row-action .link-btn", has_text="Automatic").click()
        waits_for(page, lambda: settings_config().refresh_ms is None)
    assert "refresh_ms = 500" not in (isolated_home / "config.toml").read_text(encoding="utf-8")


def test_settings_sensors_of_the_running_app(isolated_home):
    """What each source reads right now, and starting the panel again for a change to take."""
    from test_service import wait_for

    app = settings_app(isolated_home)
    url = app.start()
    try:
        assert wait_for(lambda: app.panel.state()["state"] == "showing")
        with browser_page(url, "settings/sensors") as page:
            row = page.locator(".source-row[data-source='psutil']")
            page.wait_for_function(
                "document.querySelector(\"[data-source='psutil'] .status\") !== null"
            )
            assert "readings" in row.locator(".status").inner_text()
            loop = app.panel.status
            page.locator(".source-row[data-source='audio'] [role='switch']").click()
            waits_for(page, lambda: "audio" not in settings_config().sensors.providers)
            note = page.locator(".note.warn")
            assert "after a restart of the panel" in note.inner_text()
            note.get_by_role("button", name="Restart the panel").click()
            assert wait_for(lambda: app.panel.status is not loop and app.panel.running)
            page.wait_for_function("document.querySelector('.note.warn') === null")
    finally:
        app.quit()
        app.shutdown()


def test_settings_services(app_url, isolated_home):
    """A service on or off, and its options by the type each declares."""
    with browser_page(app_url, "settings/services") as page:
        page.wait_for_selector(".settings-card.service")
        cards = page.locator(".settings-card.service")
        assert [cards.nth(i).get_attribute("data-service") for i in range(cards.count())] == [
            "notices", "game", "autopilot", "devices", "memory", "cooling", "downloads",
        ]  # fmt: skip
        assert page.locator("[role='switch'][aria-checked='true']").count() == 0  # off until asked
        game = page.locator("[data-service='game']")
        game.get_by_role("switch", name="Game mode", exact=True).click()
        waits_for(page, lambda: settings_config().services.enabled == ["game"])
        game.locator(".settings-disclosure").click()
        poll = game.locator("[data-option='poll_s'] input")
        assert poll.input_value() == "2"  # the service's own default, until it is changed
        poll.fill("1,5")
        poll.press("Enter")
        waits_for(page, lambda: game_option("poll_s") == 1.5)
        game.locator("[data-option='steam'] [role='switch']").click()
        waits_for(page, lambda: game_option("steam") is False)
        programs = game.locator("[data-option='programs'] input")
        programs.fill("emulator.exe = Retro")
        programs.press("Enter")
        programs.fill("othergame.exe")
        programs.press("Enter")
        waits_for(
            page,
            lambda: game_option("programs") == ["emulator.exe = Retro", "othergame.exe"],
        )
        game.get_by_role("button", name="Remove othergame.exe").click()
        waits_for(
            page,
            lambda: game_option("programs") == ["emulator.exe = Retro"],
        )
        # back to the default takes the setting out of config.toml
        game.locator("[data-option='poll_s'] .link-btn").click()
        waits_for(page, lambda: game_option("poll_s") is None)
        playwright.expect(game.locator("[data-option='poll_s'] .link-btn")).to_have_count(0)
        game.get_by_role("switch", name="Game mode", exact=True).click()
        waits_for(page, lambda: settings_config().services.enabled == [])
        # a service whose rules have a page of their own
        auto = page.locator("[data-service='autopilot']")
        auto.locator(".settings-disclosure").click()
        assert auto.locator("[data-option='rules']").inner_text().count("Edit") == 1
        auto.locator("[data-option='rules'] button").click()
        page.wait_for_function("location.hash === '#/plugin/autopilot'")


def test_settings_services_of_the_running_app(isolated_home):
    """Each service's state as the running app tells it, and Restart."""
    from test_service import wait_for

    app = settings_app(isolated_home)
    url = app.start()
    try:
        assert wait_for(lambda: app.panel.state()["state"] == "showing")
        with browser_page(url, "settings/services") as page:
            notices = page.locator("[data-service='notices']")
            notices.get_by_role("switch", name="Messages", exact=True).click()
            assert wait_for(lambda: app.services.state.get("notices") == "running")
            page.wait_for_function(
                "document.querySelector(\"[data-service='notices'] .status.ok\") !== null"
            )
            assert "Running" in notices.locator(".status").inner_text()
            first = app.services.running["notices"][0]
            notices.get_by_role("button", name="Restart").click()
            assert wait_for(lambda: app.services.running["notices"][0] is not first)
            # an option changes: the service starts again by itself, with it
            notices.locator(".settings-disclosure").click()
            notices.locator("[data-option='music'] [role='switch']").click()
            assert wait_for(lambda: app.services.running["notices"][0].options["music"] is False)
            notices.get_by_role("switch", name="Messages", exact=True).click()
            assert wait_for(lambda: "notices" not in app.services.running)
            page.wait_for_function(
                "document.querySelector(\"[data-service='notices'] .status\") === null"
            )
    finally:
        app.quit()
        app.shutdown()


def test_settings_weather_and_place(app_url, isolated_home, monkeypatch):
    """A place by its name, the weather on and off, units and interval; the place is kept
    to about a kilometre and the weather needs one."""
    from libre_panel.weather import open_meteo

    asked = []

    def geocode(name, count=5, language="en"):
        asked.append(name)
        return [
            {"name": "Springfield", "region": "Region", "country": "Country",
             "latitude": 50.123456, "longitude": 8.987654},
            {"name": "Springfield", "region": "", "country": "Elsewhere",
             "latitude": -33.5, "longitude": 151.25},
        ]  # fmt: skip

    monkeypatch.setattr(open_meteo, "geocode", geocode)
    with browser_page(app_url, "settings/weather") as page:
        toggle = page.get_by_role("switch", name="Weather")
        assert toggle.is_disabled()  # no place yet
        assert "Choose a place first" in page.locator("#main").inner_text()
        assert asked == []  # nothing goes anywhere before the person asks
        search = page.get_by_label("Name of a place")
        assert page.get_by_role("button", name="Search", exact=True).is_disabled()
        search.fill("Spring")
        search.press("Enter")
        places = page.locator(".places li")
        page.wait_for_selector(".places li")
        assert places.count() == 2 and asked == ["Spring"]
        assert "50.12, 8.99" in places.first.inner_text()
        places.first.locator("button").click()
        waits_for(  # the place is one write; it does not switch the weather on
            page,
            lambda: (
                (settings_config().weather.latitude, settings_config().weather.longitude)
                == (50.12, 8.99)
                and settings_config().weather.place == "Springfield, Region, Country"
                and not settings_config().weather.enabled
            ),
        )
        playwright.expect(page.locator("#place")).to_contain_text("Springfield, Region, Country")
        playwright.expect(page.locator(".places")).to_have_count(0)
        toggle.click()
        waits_for(page, lambda: settings_config().weather.enabled)
        radio(page, "Units", "°F").click()
        waits_for(page, lambda: settings_config().weather.units == "imperial")
        radio(page, "Update", "30").click()
        waits_for(page, lambda: settings_config().weather.update_minutes == 30)
        assert "after Libre Panel is started again" in page.locator(".note.warn").inner_text()
        # taking the place away switches the weather off with it, after a question
        page.locator("#place .link-btn", has_text="Remove").click()
        page.locator(".dialog .btn-danger").click()
        waits_for(
            page,
            lambda: (
                settings_config().weather.latitude is None
                and settings_config().weather.place == ""
                and not settings_config().weather.enabled
                and settings_config().weather.units == "imperial"
            ),
        )
        playwright.expect(toggle).to_be_disabled()


def test_settings_weather_search_fails_politely(app_url, monkeypatch):
    from libre_panel.weather import open_meteo

    def offline(name, count=5, language="en"):
        raise open_meteo.WeatherError("timed out")

    monkeypatch.setattr(open_meteo, "geocode", offline)
    with browser_page(app_url, "settings/weather", allow=("/api/location",)) as page:
        page.get_by_label("Name of a place").fill("Nowhere")
        page.get_by_role("button", name="Search", exact=True).click()
        page.wait_for_selector(".note.error")
        assert "timed out" in page.locator(".note.error").inner_text()
        assert page.get_by_role(
            "button", name="Search", exact=True
        ).is_enabled()  # it can be tried again


def test_settings_start_and_language(app_url, isolated_home):
    """The language switches the whole app, the tray and the panel (config.toml keeps it)."""
    with browser_page(app_url, "settings/system") as page:
        assert page.locator("#settings-title").inner_text() == "Start and system"
        radio(page, "Language", "Deutsch").click()
        waits_for(page, lambda: settings_config().language == "de")
        playwright.expect(page.locator("#settings-title")).to_have_text("Start und System")
        assert page.locator(".settings-nav button").first.inner_text() == "Panel"
        playwright.expect(radio(page, "Sprache", "Deutsch")).to_have_attribute(
            "aria-checked", "true"
        )
        page.locator(".settings-nav button", has_text="Dienste").click()
        page.wait_for_selector("[data-service='game']")
        assert "Spielmodus" in page.locator("[data-service='game']").inner_text()
        page.goto(f"{app_url}#/settings/panel")
        page.wait_for_selector(".panel-found")
        text = page.locator("#main").inner_text()
        assert "Videomodus" in text and "Bilder pro Sekunde" in text and "Auf dem Kopf" in text
        page.goto(f"{app_url}#/settings/system")
        radio(page, "Sprache", "System").click()
        waits_for(page, lambda: settings_config().language == "auto")
        radio(page, "Language", "English").click()
        waits_for(page, lambda: settings_config().language == "en")


def test_settings_start_with_the_system(isolated_home):
    """Starting with the system is what the command line's autostart does."""
    from libre_panel.autostart import Autostart

    home = isolated_home.parent / "linux-home"
    autostart = Autostart("linux", home=home, environ={})
    app = settings_app(isolated_home, autostart)
    url = app.start()
    try:
        with browser_page(url, "settings/system") as page:
            toggle = page.get_by_role("switch", name="Start when you log in")
            assert toggle.get_attribute("aria-checked") == "false"
            assert page.get_by_role("switch", name="With administrator rights").count() == 0
            toggle.click()
            waits_for(page, lambda: autostart.is_enabled())
            page.wait_for_function(
                "document.querySelector(\"[aria-label='Start when you log in']\")"
                ".getAttribute('aria-checked') === 'true'"
            )
            assert "libre-panel.desktop" in page.locator("#main").inner_text()
            toggle.click()
            waits_for(page, lambda: not autostart.is_enabled())
    finally:
        app.quit()
        app.shutdown()


def test_settings_the_highest_rights_only_when_asked_and_by_an_administrator(isolated_home):
    from test_autostart import FakeRegistry, FakeTasks

    from libre_panel.autostart import Autostart

    registry, tasks = FakeRegistry(), FakeTasks()
    autostart = Autostart("win32", registry=registry, tasks=tasks, admin=lambda: False)
    app = settings_app(isolated_home, autostart)
    url = app.start()
    try:
        with browser_page(url, "settings/system") as page:
            assert (
                page.get_by_role("switch", name="Start with Windows").get_attribute("aria-checked")
                == "false"
            )
            rights = page.get_by_role("switch", name="With administrator rights")
            assert rights.get_attribute("aria-checked") == "false"  # never the default
            assert "temperature and power of the processor" in page.locator("#main").inner_text()
            assert "needs Libre Panel to run as administrator" in page.locator("#main").inner_text()
            rights.click()  # not an administrator: told what to do, nothing is set up
            dialog = page.locator(".dialog")
            assert "Run as administrator" in dialog.inner_text()
            assert (
                "libre-panel autostart enable --elevated" in dialog.locator(".command").inner_text()
            )
            dialog.get_by_role("button", name="Close").click()
            assert not tasks.tasks and registry.get("Libre Panel") is None
            autostart._admin = lambda: True  # started as an administrator
            page.goto(f"{url}#/settings/panel")
            page.goto(f"{url}#/settings/system")
            page.wait_for_selector("[role='switch'][aria-label='With administrator rights']")
            rights.click()
            dialog = page.locator(".dialog")
            assert "highest rights at every login" in dialog.inner_text()
            dialog.get_by_role("button", name="Cancel").click()
            assert not tasks.tasks  # a question first
            rights.click()
            page.locator(".dialog .btn-primary").click()
            waits_for(page, lambda: "Libre Panel" in tasks.tasks)
            page.wait_for_function(
                "document.querySelector(\"[aria-label='With administrator rights']\")"
                ".getAttribute('aria-checked') === 'true'"
            )
    finally:
        app.quit()
        app.shutdown()


def test_settings_about(app_url, isolated_home, monkeypatch):
    from libre_panel import __version__, tray

    opened = []
    monkeypatch.setattr(tray, "open_path", opened.append)
    with browser_page(app_url, "settings/about") as page:
        assert f"Libre Panel {__version__}" in page.locator("#main").inner_text()
        assert "GPL-3.0-or-later" in page.locator("#main").inner_text()
        links = page.locator("#main a.btn")
        hrefs = [links.nth(i).get_attribute("href") for i in range(links.count())]
        assert "https://github.com/syntensa/libre-panel" in hrefs
        assert any(href.endswith("template=panel_support.yml") for href in hrefs)
        commands = page.locator(".command")
        assert [commands.nth(i).inner_text().split()[-1] for i in range(commands.count())] == [
            "doctor",
            "devices",
        ]
        assert str(isolated_home) in page.locator("[data-folder='config']").inner_text()
        page.locator("[data-folder='themes'] button").click()
        page.locator("[data-folder='logs'] button").click()
        waits_for(page, lambda: len(opened) == 2)
        assert opened == [isolated_home / "themes", isolated_home / "logs"]
        # the notices of the software it is built on: shown in the app, from NOTICE.md
        assert page.locator(".notices").count() == 0
        page.locator(".settings-disclosure", has_text="Notices and credits").click()
        notices = page.locator(".notices")
        assert "turing-smart-screen-python" in notices.inner_text()
        assert notices.locator("table").count() >= 1 and notices.locator("a").count() > 3
        assert notices.locator("a").first.get_attribute("rel") == "noopener noreferrer"


def test_settings_say_when_config_toml_has_a_mistake(app_url, isolated_home):
    (isolated_home).mkdir(parents=True)
    (isolated_home / "config.toml").write_text("this is not toml", encoding="utf-8")
    allow = ("/api/settings", "/api/panel", "/api/system", "/api/pages", "/api/active")
    with browser_page(app_url, "settings/panel", allow=allow) as page:
        page.wait_for_selector(".note.error")
        assert "config.toml has a mistake in it." in page.locator(".note.error").inner_text()
        assert page.locator(".settings-card").count() == 0  # nothing to change meanwhile
        (isolated_home / "config.toml").write_text("fps = 10\n", encoding="utf-8")
        page.get_by_role("button", name="Try again").click()
        page.wait_for_selector(".panel-found")
        assert page.locator(".note.error").count() == 0


class HardwareNames:
    """A stand-in for the server's /api/hardware: the names, and what the app posts."""

    def __init__(self):
        self.names = {
            "cpu": {"detected": "8-Core Processor", "custom": ""},
            "gpu": {"detected": "Graphics Card", "custom": ""},
            "memory": {"detected": "", "custom": ""},
        }
        self.posted = []

    def __call__(self, route, request):
        import json

        if request.method == "POST":
            change = json.loads(request.post_data or "{}")
            self.posted.append(change)
            for part, text in change.items():
                self.names[part]["custom"] = text
        route.fulfill(status=200, content_type="application/json", body=json.dumps(self.names))


def test_hardware_names_in_settings_and_where_they_show(app_url):
    names = HardwareNames()
    with browser_page(app_url, "settings/sensors", routes={"**/api/hardware": names}) as page:
        cpu = page.locator("#hardware-cpu-row")
        assert cpu.get_attribute("placeholder") == "8-Core Processor"  # the detected name
        assert page.locator("#hardware-memory-row").get_attribute("placeholder") == "not detected"
        cpu.fill("Workstation")
        cpu.press("Enter")
        page.wait_for_function("window.libre.store.get().hardware.cpu.custom === 'Workstation'")
        page.locator(".settings-row", has_text="Processor").locator(".link-btn").click()
        page.wait_for_function("window.libre.store.get().hardware.cpu.custom === ''")
        assert names.posted == [{"cpu": "Workstation"}, {"cpu": ""}]
        # in Design: beside a widget that shows a name
        page.goto(f"{app_url}#/design")
        page.wait_for_selector(".panel-canvas .preview")
        shows_name = "{type: 'metric', id: 'gpu-name', x: 10, y: 10, sensor: 'gpu.name'}"
        js(page, f"window.libre.doc.edit(null, (theme) => theme.widgets.push({shows_name}))")
        js(page, "window.libre.doc.setSelection(['gpu-name'])")
        field = page.locator(".inspector .hardware-name input")
        assert field.get_attribute("placeholder") == "Graphics Card"
        field.fill("Mine")
        field.press("Enter")
        page.wait_for_function("window.libre.store.get().hardware.gpu.custom === 'Mine'")
        assert names.posted[-1] == {"gpu": "Mine"}
        js(page, "window.libre.doc.setSelection(['cpu-ring'])")  # shows no name
        assert page.locator(".inspector .hardware-name").count() == 0


def test_without_hardware_names_nothing_shows(app_url):
    """A server without /api/hardware (an older background app): no fields; the rest of
    the section is there."""
    missing = {"**/api/hardware": lambda route: route.fulfill(status=404, body="")}
    with browser_page(app_url, "settings/sensors", routes=missing) as page:
        page.wait_for_function("window.libre.store.get().hardware === false")
        assert page.locator(".hardware-name").count() == 0
        assert page.locator("#sensor-sources").is_visible()
        assert page.locator(".settings-upcoming").count() == 0
