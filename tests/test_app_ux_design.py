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
