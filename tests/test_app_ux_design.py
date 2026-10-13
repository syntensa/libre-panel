"""Design and Look as a person meets them: changing a module, adding and removing blocks, saving
and putting a theme on the panel, the pages, the theme's settings beside Look, the first visit's
tips, the version for a bar on end and the editor's tools in sight. In a real browser; skipped
where Playwright or Chromium are missing (as tests/test_app_browser.py)."""

import pytest
from test_app_browser import (  # noqa: F401
    PICTURE,
    a_panel_app,
    app_url,
    browser_page,
    choose_theme,
    js,
    no_setup_card,
    settings_config,
    waits_for,
    widget,
)

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402


def items(page, id):
    return widget(page, id)["items"]


def test_a_card_slot_names_its_cards(app_url, isolated_home):  # noqa: F811
    """Lumen's slot holds cards; its field read "Readings" and explained sensors and patterns,
    and the toolbar said "No sensor". The cards are named in words, moved, taken out and added."""
    choose_theme(isolated_home, "lumen")
    with browser_page(app_url) as page:
        js(page, "window.libre.doc.setSelection(['card'])")
        expect(page.locator(".floating-toolbar .tool-name")).to_have_text("Card slot")
        expect(page.locator(".floating-toolbar .tool-sensor")).to_have_count(0)
        inspector = page.locator(".inspector")
        cards = inspector.locator(".slot-card-name")
        expect(cards).to_have_text(["Playback card", "Weather card", "Calendar"])
        expect(inspector.locator(".inspector-subtitle")).to_have_text(
            "Playback card · Weather card · Calendar"
        )
        inspector.locator(".slot-card").nth(2).get_by_role("button", name="Move up").click()
        assert items(page, "card") == "playback\ndate\nforecast"
        inspector.locator(".slot-card").nth(0).get_by_role("button", name="Take out").click()
        assert items(page, "card") == "date\nforecast"
        inspector.get_by_role("combobox", name="Add a card…").select_option("instrument")
        assert items(page, "card") == "date\nforecast\ninstrument"
        expect(cards).to_have_text(["Calendar", "Weather card", "Ring instrument"])
        page.keyboard.press("Control+z")
        assert items(page, "card") == "date\nforecast"
        # a module that shows a source still has its chip
        js(page, "window.libre.doc.setSelection(['cpu-ring'])")
        expect(page.locator(".floating-toolbar .tool-sensor")).to_have_text("CPU")


def test_a_block_is_added_with_a_click_and_taken_back_from_the_message(app_url):  # noqa: F811
    """The library said "Drag onto the panel" while a click adds a block too; its "+" and its
    hint say so. What is removed can be taken back from the message, but not past a change
    that came after it."""
    with browser_page(app_url) as page:
        library = page.locator(".library")
        expect(library.locator(".library-hint")).to_contain_text("Click a block to add it")
        tile = page.locator('.tile[data-item="module:clock"]')
        tile.hover()
        expect(tile.locator(".tile-add")).to_have_css("opacity", "1")
        count = js(page, "window.libre.doc.widgets().length")
        tile.click()
        assert js(page, "window.libre.doc.widgets().length") == count + 1
        added = js(page, "window.libre.doc.single().id")
        page.keyboard.press("Delete")
        message = page.locator(".toast", has_text="Removed “Time”")
        message.get_by_role("button", name="Undo").click()
        assert widget(page, added) is not None
        assert js(page, "window.libre.store.get().canRedo") is True  # it was the step back
        # removed, then something else changed: the message's Undo leaves that change alone
        js(page, f"window.libre.doc.setSelection([{added!r}])")
        page.keyboard.press("Delete")
        js(page, "window.libre.doc.edit('n', (theme) => { theme.name = 'Changed'; })")
        page.locator(".toast", has_text="Removed “Time”").get_by_role("button", name="Undo").click()
        assert js(page, "window.libre.doc.theme().name") == "Changed"
        expect(page.locator(".toast", has_text="takes them back one by one")).to_have_count(1)
        # a cut says it copied, and nothing about a removal
        js(page, "window.libre.doc.setSelection(['clock'])")
        page.keyboard.press("Control+x")
        expect(page.locator(".toast", has_text="Copied 1 widget")).to_have_count(1)
        expect(page.locator(".toast", has_text="Removed “Clock”")).to_have_count(0)
