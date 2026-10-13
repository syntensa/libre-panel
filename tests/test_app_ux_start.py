"""Start in a real browser: the actions of the gallery; skipped without Playwright or
Chromium."""

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402
from test_app_browser import (  # noqa: E402, F401
    PICTURE,
    app_url,
    browser_page,
    choose_theme,
    shown_themes,
)


def test_every_card_of_the_gallery_names_both_its_actions(app_url, isolated_home):  # noqa: F811
    """A click on a card puts it on the panel (its name says so); "Edit" is always there, with
    its name, and covers nothing of the card's text in either language."""
    from libre_panel import i18n

    choose_theme(isolated_home, "lumen")
    with browser_page(app_url, "home/themes", allow=PICTURE) as page:
        page.wait_for_selector(".theme-card")
        almanac = page.locator(".theme-card[data-theme='almanac']")
        expect(almanac.locator(".theme-card-main")).to_have_attribute(
            "aria-label", "Show “Almanac” on the panel"
        )
        edit = almanac.get_by_role("button", name="Edit “Almanac”")
        expect(edit).to_be_visible()  # without a pointer over it
        expect(edit).to_have_text("Edit")
        assert page.locator(".theme-card-edit").count() == len(shown_themes(page))
    i18n.set_language("de")
    with browser_page(app_url, "home/themes", allow=PICTURE) as page:
        page.wait_for_selector(".theme-card")
        for card in page.locator(".theme-card").all():
            edit = card.locator(".theme-card-edit").bounding_box()
            for part in (".theme-card-name", ".theme-card-note"):
                text = card.locator(part).bounding_box()
                apart = (
                    text["x"] + text["width"] <= edit["x"]
                    or text["y"] >= edit["y"] + edit["height"]
                )
                assert apart, (card.get_attribute("data-theme"), part)
