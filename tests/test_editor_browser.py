"""The editor in a real browser (skipped when Playwright/Chromium are missing).

CI runs this in its own job: pip install playwright && playwright install chromium
"""

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


@pytest.fixture
def page(editor_url):
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
        page.goto(editor_url)
        page.wait_for_function(
            "document.querySelector('#preview').src.startsWith('data:image/png')"
        )
        page.wait_for_timeout(300)
        yield page
        assert errors == []
        browser.close()


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
