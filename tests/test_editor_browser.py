"""The editor in a real browser (skipped when Playwright/Chromium are missing).

CI runs this in its own job: pip install playwright && playwright install chromium
"""

import contextlib
import threading

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from libre_panel.editor.server import make_server  # noqa: E402


@pytest.fixture
def editor_url():
    srv = make_server(port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()
    srv.server_close()
    srv.editor_state.close()


@contextlib.contextmanager
def browser_page(url):
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # browser not installed
            pytest.skip(f"chromium not available: {exc}")
        # The editor's CSP forbids eval (good); the test's own probes need it.
        context = browser.new_context(viewport={"width": 1600, "height": 950}, bypass_csp=True)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("dialog", lambda d: d.accept("my-test" if d.type == "prompt" else None))
        page.goto(url)
        page.wait_for_function(
            "document.querySelector('#preview').src.startsWith('data:image/png')"
        )
        page.wait_for_timeout(300)
        yield page
        assert errors == []
        browser.close()


@pytest.fixture
def page(editor_url):
    with browser_page(editor_url) as page:
        yield page


def js(page, expression):
    return page.evaluate(expression)


def clock_xy(page):
    return js(page, "(() => { const w = widgetById('clock'); return [w.x, w.y]; })()")


def test_drag_snap_undo_redo(page):
    start = clock_xy(page)
    box = page.locator('#overlay .box[data-id="clock"]').bounding_box()
    page.mouse.move(box["x"] + 10, box["y"] + 10)
    page.mouse.down()
    page.mouse.move(box["x"] + 70, box["y"] + 33, steps=10)
    page.mouse.up()
    moved = clock_xy(page)
    assert moved != start
    page.keyboard.press("Control+z")
    assert clock_xy(page) == start
    page.keyboard.press("Control+y")
    assert clock_xy(page) == moved


def test_marquee_align_copy_paste_delete(page):
    count = js(page, "state.theme.widgets.length")
    stage = page.locator("#canvas-wrap").bounding_box()
    page.mouse.move(stage["x"] + 5, stage["y"] + stage["height"] * 0.33)
    page.mouse.down()
    page.mouse.move(stage["x"] + stage["width"] * 0.6, stage["y"] + stage["height"] * 0.72, steps=8)
    page.mouse.up()
    selected = js(page, "[...state.selection]")
    assert len(selected) >= 4 and "backdrop" not in selected  # locked layers are not picked up
    page.click("text=⤒ Top")
    page.keyboard.press("Control+c")
    page.keyboard.press("Control+v")
    assert js(page, "state.theme.widgets.length") == count + len(selected)
    page.keyboard.press("Delete")
    assert js(page, "state.theme.widgets.length") == count


def test_building_block_and_palette_rename(page):
    count = js(page, "state.theme.widgets.length")
    page.click('#presets button:has-text("CPU card")')
    assert js(page, "state.theme.widgets.length") > count
    js(page, "setSelection([])")
    index = js(page, "Object.keys(state.theme.palette).indexOf('accent')")
    name = page.locator(".palette-row input[type=text][title^='Name']").nth(index)
    refs = js(page, "JSON.stringify(state.theme).split('@accent\"').length - 1")
    name.fill("signal")
    name.press("Tab")
    assert js(page, "JSON.stringify(state.theme).split('@signal\"').length - 1") == refs
    assert js(page, "'accent' in state.theme.palette") is False


def test_locked_layers_do_not_move_and_save_as(page, isolated_home):
    page.locator('#widget-list li:has(.wid:text-is("clock")) button[title="Lock"]').click()
    start = clock_xy(page)
    box = page.locator('#overlay .box[data-id="clock"]').bounding_box()
    page.mouse.move(box["x"] + 5, box["y"] + 5)
    page.mouse.down()
    page.mouse.move(box["x"] + 80, box["y"] + 40, steps=5)
    page.mouse.up()
    assert clock_xy(page) == start
    page.click("#btn-save")  # built-in theme: asks for a new name ("my-test")
    page.wait_for_function("document.querySelector('#status').textContent.includes('my-test')")
    assert (isolated_home / "themes" / "my-test" / "theme.json").exists()


def test_panel_menu_rescales(page):
    page.select_option("#model-select", "turing-9.2-usb")
    page.wait_for_function("state.size[0] === 1920")
    page.select_option("#model-select", "turing-2.1")
    page.wait_for_function("state.size[0] === 480 && state.size[1] === 480")
    assert js(page, "document.querySelector('#canvas-wrap').classList.contains('round')")


def test_hidden_strip_is_shown_and_snaps(page):
    assert page.locator(".hidden-strip").count() == 0  # the default panel hides nothing
    page.select_option("#model-select", "turing-9.2-usb")
    page.wait_for_function("state.size[0] === 1920 && document.querySelector('.hidden-strip.top')")
    zoom = js(page, "state.zoom")
    strip = page.locator(".hidden-strip.top").bounding_box()
    assert strip["height"] == pytest.approx(18 * zoom, abs=1)
    assert "18 px" in page.locator(".hidden-strip.top").get_attribute("title")
    assert js(page, "hiddenEdges().top") == 18


@pytest.fixture
def drawing_plugin(plugin_folder):
    """A plugin with a screen and a widget type, installed before the editor starts."""
    from test_plugins import DRAWING, DRAWING_PARTS

    return plugin_folder(DRAWING, DRAWING_PARTS)


def test_plugin_screen_and_widget_in_the_editor(drawing_plugin, page):
    assert page.locator("#add-type option[value='demo.bar']").inner_text() == "Demo bar"
    select = page.locator("label.field", has_text="screen").locator("select")
    select.select_option("tint")
    page.wait_for_function("state.theme.screen && state.theme.screen.name === 'tint'")
    page.locator("label.field", has_text="color").first.wait_for()
    page.wait_for_timeout(500)  # the preview renders with the screen
    select.select_option("")
    page.wait_for_function("!state.theme.screen")


@pytest.fixture
def toast_plugin(plugin_folder):
    from test_plugins import TOAST_PARTS, TOAST_STYLE

    return plugin_folder(TOAST_STYLE, TOAST_PARTS)


def test_toast_settings_in_the_editor(toast_plugin, page, isolated_home):
    """Theme → Messages: position, time, hidden kinds and a plugin's style with its options."""
    messages = page.locator("#props")
    messages.locator("label.field", has_text="position").locator("select").select_option(
        "bottom-left"
    )
    messages.locator("label.field", has_text="hidden kinds").locator("input").fill("music, volume")
    messages.locator("label.field", has_text="one after another").locator("input").uncheck()
    messages.locator("label.field", has_text="style").locator("select").select_option("demo.band")
    height = messages.locator("label.field", has_text="height").locator("input")
    height.fill("55")
    height.press("Tab")
    toast = js(page, "state.theme.toast")
    assert toast == {
        "anchor": "bottom-left",
        "queue": False,
        "off": ["music", "volume"],
        "style": "demo.band",
        "options": {"height": 55},
    }
    page.click("#btn-save")  # built-in theme: asks for a new name ("my-test")
    page.wait_for_function("document.querySelector('#status').textContent.includes('my-test')")
    from libre_panel.theme.model import load_theme

    saved = load_theme(isolated_home / "themes" / "my-test")
    assert saved.toast["style"] == "demo.band" and saved.toast["options"] == {"height": 55}
    assert saved.toast_anchor == "bottom-left" and saved.toast["off"] == ["music", "volume"]
    assert saved.toast["queue"] is False


@pytest.fixture
def page_plugin(plugin_folder):
    from test_plugins import PAGE, PAGE_FILES, PAGE_PARTS

    return plugin_folder(PAGE, PAGE_PARTS, files=PAGE_FILES)


def test_plugin_page_opens_in_the_editor(page_plugin, page):
    page.click("#pages-button")
    page.get_by_role("menuitem", name="Cooling").click()
    frame = page.frame_locator("#plugin-frame")
    frame.locator("#out", has_text="1 3").wait_for()  # kit.js reached the page's own API
    assert page.locator("main.layout").is_hidden()
    page.click("#plugin-back")
    assert page.locator("main.layout").is_visible() and page.locator("#plugin-view").is_hidden()


def test_plain_editor_hides_the_panel_controls(page):
    page.wait_for_timeout(300)
    assert page.locator("#app-chip").is_hidden()


def test_panel_controls_of_the_background_app(isolated_home):
    from test_service import FakeRegistry, wait_for, write_config

    from libre_panel.autostart import Autostart
    from libre_panel.config import load_config
    from libre_panel.service import BackgroundApp

    write_config(isolated_home)
    registry = FakeRegistry()
    app = BackgroundApp(port=0, autostart=Autostart("win32", registry=registry))
    url = app.start()
    try:
        with browser_page(url) as page:
            chip = page.locator("#app-chip")
            page.wait_for_function(
                "document.querySelector('#app-chip').dataset.state === 'showing'"
            )
            assert chip.inner_text() == "Panel · Showing"
            assert "PNG file" in chip.get_attribute("title")
            chip.click()
            popover = page.locator("#app-popover")
            assert popover.is_visible()
            assert page.locator("#app-state").inner_text() == "Showing"
            assert page.locator("#app-theme").inner_text() == "libre-default"

            page.locator("#app-brightness").evaluate(
                "(el) => { el.value = 30; el.dispatchEvent(new Event('change')); }"
            )
            assert wait_for(lambda: load_config().device.brightness == 30)

            page.locator("#app-autostart").check()
            assert wait_for(lambda: "Libre Panel" in registry.values)

            page.click("#app-pause")
            page.wait_for_function("document.querySelector('#app-state').textContent === 'Paused'")
            assert app.panel.paused
            assert page.locator("#app-pause").inner_text() == "Resume panel"
            page.click("#app-pause")
            page.wait_for_function(
                "document.querySelector('#app-pause').textContent === 'Pause panel'"
            )

            page.keyboard.press("Escape")
            assert popover.is_hidden()
            chip.click()
            page.click("#app-quit")  # the confirm dialog is accepted by the fixture
            page.wait_for_function("document.querySelector('#status').textContent.includes('quit')")
            assert app.quit_requested.wait(5)
    finally:
        app.quit()
        app.shutdown()


def test_language_switch_keeps_unsaved_work(page, isolated_home):
    from libre_panel.config import load_config

    js(page, "state.theme.name = 'Work in progress'; markDirty()")
    page.select_option("#lang-select", "de")
    page.wait_for_function("document.documentElement.lang === 'de'")
    page.wait_for_function("state.theme && state.theme.name === 'Work in progress'")
    assert load_config().language == "de"
    assert page.locator("#btn-save").inner_text() == "Speichern"
    assert page.locator("#btn-activate").get_attribute("title").startswith("Theme speichern")
    assert js(page, "state.dirty") is True  # still unsaved, nothing lost
    assert page.locator("#add-type option").first.inner_text() == "Fläche"
    js(page, "setSelection(['clock'])")
    assert page.locator("#props-title").text_content() == "Element: Uhr"
    page.select_option("#lang-select", "en")
    page.wait_for_function("document.documentElement.lang === 'en'")
    assert page.locator("#btn-save").inner_text() == "Save"
