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


def expected_404(message):
    """The app asks for the hardware's names; a server without them (an older background
    app, as one test plays) answers 404, the app hides them, and Chromium logs an error."""
    return message.text.startswith("Failed to load resource") and message.location.get(
        "url", ""
    ).endswith("/api/hardware")


@contextlib.contextmanager
def browser_page(url, view="design", strict_csp=False, size=(1440, 900), routes=None):
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
            lambda m: errors.append(m.text) if m.type == "error" and not expected_404(m) else None,
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
    """A server without /api/hardware (an older background app): no fields, and the section
    says what is to come."""
    missing = {"**/api/hardware": lambda route: route.fulfill(status=404, body="")}
    with browser_page(app_url, "settings/sensors", routes=missing) as page:
        page.wait_for_function("window.libre.store.get().hardware === false")
        assert page.locator(".hardware-name").count() == 0
        assert page.locator(".settings-upcoming").is_visible()
