"""Screens in turn in the app (Start, #/home/sequence) in a real browser; skipped without
Playwright or Chromium."""

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402
from test_app_browser import PICTURE, app_url, browser_page, choose_theme  # noqa: E402, F401

from libre_panel.config import load_config  # noqa: E402
from libre_panel.sequence import Step  # noqa: E402


def test_screens_in_turn_are_set_up_on_start_in_seconds_minutes_or_hours(app_url, isolated_home):  # noqa: F811
    choose_theme(isolated_home, "lumen")
    with browser_page(app_url, "home/sequence", allow=PICTURE) as page:
        page.wait_for_selector(".sequence-page .sequence-add")
        expect(page.locator(".sequence-empty")).to_be_visible()
        add = page.get_by_role("button", name="Add a screen")
        add.click()
        add.click()
        add.click()
        rows = page.locator(".sequence-step")
        expect(rows).to_have_count(3)
        rows.nth(0).locator("select").select_option("almanac")
        rows.nth(1).locator("select").select_option("nimbus")
        rows.nth(2).locator("select").select_option("studio")
        # five minutes each by default; the third for 10 minutes, the second for 30 seconds
        rows.nth(2).locator("input").fill("10")
        rows.nth(2).locator("input").press("Enter")
        rows.nth(1).locator("input").fill("30")
        rows.nth(1).locator("input").press("Enter")
        rows.nth(1).get_by_role("radio", name="s", exact=True).click()
        expect(page.locator(".sequence-foot")).to_contain_text("Once round: 15 min 30 s")
        # too short a step is said, and cannot be saved
        rows.nth(1).locator("input").fill("2")
        rows.nth(1).locator("input").press("Enter")
        expect(rows.nth(1).locator(".sequence-error")).to_contain_text("At least 5 seconds")
        expect(page.get_by_role("button", name="Save and start")).to_be_disabled()
        rows.nth(1).locator("input").fill("30")
        rows.nth(1).locator("input").press("Enter")
        # the last one first
        rows.nth(2).get_by_role("button", name="Earlier").click()
        page.get_by_role("button", name="Save and start").click()
        expect(page.get_by_role("button", name="Stop")).to_be_visible()
    config = load_config(isolated_home / "config.toml")
    assert config.sequence.on and config.theme == "lumen"
    assert config.sequence.steps == [Step("almanac", 300), Step("studio", 600), Step("nimbus", 30)]
    with browser_page(app_url, "home", allow=PICTURE) as page:
        card = page.locator(".sequence-card")
        expect(card).to_have_class("sequence-card on")
        expect(card.locator(".sequence-chips li")).to_have_count(3)
        expect(card.locator(".sequence-chips li").nth(1)).to_contain_text("10 min")
        card.get_by_role("switch").click()  # off, and kept
        expect(card).to_have_class("sequence-card")
    config = load_config(isolated_home / "config.toml")
    assert not config.sequence.on and len(config.sequence.steps) == 3
