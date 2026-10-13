"""Design and Look as a person meets them: changing a module, adding and removing blocks, saving
and putting a theme on the panel, the pages, the theme's settings beside Look, the first visit's
tips, the version for a bar on end and the editor's tools in sight. In a real browser; skipped
where Playwright or Chromium are missing (as tests/test_app_browser.py)."""

import re

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


def test_the_themes_settings_keep_to_the_theme_and_lead_to_look(app_url, isolated_home):  # noqa: F811
    """With nothing selected the inspector showed a 48 × 48 grid of numbers to a newcomer, and
    its fine-tuning had Look's panel light and grid background a second time. The colour world
    stays as the quick choice, with the way to Look; the grid and the rest are under
    Fine-tuning, and "messages" are notices, as everywhere else."""
    choose_theme(isolated_home, "lumen")
    with browser_page(app_url) as page:
        inspector = page.locator(".inspector")
        titles = inspector.locator(".section-title")
        expect(titles).to_have_text(["Name", "Colour world", "Background", "Font"])
        expect(inspector.locator(".inspector-note")).to_contain_text("The whole theme")
        inspector.locator(".details-button").click()
        details = inspector.locator(".section-title").all_text_contents()
        assert details[:2] == ["Grid of the modules", "Module style"]
        assert "Notices" in details and "Messages" not in details
        assert "Panel light" not in details and "Grid background" not in details
        inspector.get_by_role("button", name="Open Look").click()
        page.wait_for_selector(".look-side")
        page.go_back()  # the fine-tuning is where it was left
        page.locator(".inspector").get_by_role("button", name="Back").click()
        page.locator(".inspector").get_by_role(
            "button", name="Finish, glow, panel light and notices: in Look"
        ).click()
        page.wait_for_selector(".look-side")


def test_to_the_panel_says_when_the_panel_shows_what_is_edited(app_url, isolated_home):  # noqa: F811
    """The button "To the panel" looked the same whether the panel showed the theme or not.
    Once it does, the button says "On the panel"; a change makes it ask again. A built-in theme
    says it is saved as one's own, and its copy is not a second "Lumen" in every list."""
    from libre_panel.config import load_config
    from libre_panel.theme.model import load_theme

    choose_theme(isolated_home, "lumen")
    with browser_page(app_url) as page:
        apply = page.locator(".btn-apply")
        expect(apply).to_have_text("On the panel")
        expect(apply).not_to_have_class("btn-primary")
        expect(page.locator(".inspector .inspector-builtin")).to_have_count(1)
        js(page, "window.libre.doc.setSelection(['cpu-ring'])")
        page.locator(".floating-toolbar .tool-text", has_text="Flat").click()
        expect(apply).to_have_text("To the panel")
        expect(apply).to_have_class(re.compile(r"\bbtn-primary\b"))
        apply.click()
        dialog = page.locator(".dialog")
        expect(dialog.locator("input")).to_have_value("lumen-mine")  # named after the copy
        dialog.locator("button[type='submit']").click()
        expect(apply).to_have_text("On the panel")
        assert load_config().theme == "lumen-mine"
        assert load_theme(isolated_home / "themes" / "lumen-mine").name == "Lumen (mine)"
        expect(page.locator(".theme-name")).to_have_text("Lumen (mine)")
        # another theme, not on the panel: the button asks
        page.locator(".theme-name").click()
        page.locator(".theme-row", has_text="Nimbus").first.click()
        page.wait_for_function("window.libre.store.get().doc.id === 'nimbus'")
        expect(apply).to_have_text("To the panel")


def test_unsaved_work_is_saved_with_one_click_before_another_theme_opens(app_url, isolated_home):  # noqa: F811
    """Opening another theme asked only to discard the work or to cancel; keeping it took a
    cancel, a save and the same way again. Save is the dialog's first choice now."""
    from libre_panel.theme.model import load_theme

    choose_theme(isolated_home, "lumen")
    with browser_page(app_url) as page:
        js(page, "window.libre.doc.edit('n', (theme) => { theme.description = 'mine now'; })")
        page.locator(".theme-name").click()
        page.locator(".theme-row", has_text="Nimbus").first.click()
        dialog = page.locator(".dialog")
        expect(dialog).to_contain_text("Discard unsaved changes?")
        expect(dialog.locator(".dialog-text")).to_contain_text("“Lumen”")
        expect(dialog.get_by_role("button", name="Save")).to_be_focused()
        dialog.get_by_role("button", name="Save").click()
        dialog.locator("button[type='submit']").click()  # built in: saved as one's own first
        page.wait_for_function("window.libre.store.get().doc.id === 'nimbus'")
        assert load_theme(isolated_home / "themes" / "lumen-mine").description == "mine now"
