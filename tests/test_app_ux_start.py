"""Start in a real browser: the order of the launcher, the mounting question where it
matters, the actions of the gallery and the screens in turn; skipped without Playwright or
Chromium."""

import json
import re

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402
from test_app_browser import (  # noqa: E402, F401
    PICTURE,
    a_panel_app,
    app_answer,
    app_url,
    browser_page,
    choose_theme,
    clip_ready,
    game_option,
    game_theme,
    settings_config,
    shown_themes,
    unlock_route,
    waits_for,
    windows_download,
)


def main_column(page):
    """The classes of the launcher's main column, from the top (the first one of each)."""
    return page.locator(".start-main > *").evaluate_all(
        "parts => parts.map((p) => p.className.split(' ')[0])"
    )


def an_app(state="showing", theme="lumen", **kwargs):
    """``app_answer`` with the panel in ``state`` (and on it ``theme``), as a route's handler."""
    answer = app_answer(**kwargs)
    answer["panel"].update(state=state, theme=theme)

    def handler(route, request):
        if request.method == "GET":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(answer))
        else:
            route.continue_()

    return handler


def test_the_launcher_answers_what_is_on_the_panel_before_what_to_do_next(
    app_url,  # noqa: F811
    isolated_home,
    monkeypatch,
):
    """What is missing comes first, with its fix; then what the panel shows now; then the next
    steps (smooth motion, the first steps); then what it shows when. The tiles that repeated the
    tabs are gone."""
    clip_ready(monkeypatch)
    choose_theme(isolated_home, "lumen")
    with browser_page(app_url, "home", routes={"**/api/app": a_panel_app()}, allow=PICTURE) as page:
        page.wait_for_selector(".start.launcher .clip-card")
        page.wait_for_selector(".first-steps")
        assert main_column(page) == ["panel-card", "clip-card", "first-steps", "pages-card"]
        assert page.locator(".start-tile").count() == 0
        expect(page.locator(".start-more h2")).to_have_text("More for your panel")
    routes = {
        "**/api/app": a_panel_app(hint="CPU temperature, power and fans need the service."),
        "**/api/unlock": unlock_route(windows_download(), []),
    }
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        page.wait_for_selector(".unlock-card")
        assert main_column(page)[:2] == ["hint-card", "panel-card"]


def test_the_mounting_question_is_asked_only_where_it_changes_something(
    app_url,  # noqa: F811
    isolated_home,
):
    """A bar has screens in a version for it on end; a 3.5″ has none, so it is not asked how it
    is mounted, neither on a first start nor in the first steps."""
    small = {"**/api/app": a_panel_app(target='Turing 3.5"')}
    with browser_page(app_url, "home", routes=small, allow=PICTURE) as page:
        page.wait_for_selector(".start.first .theme-card")
        expect(page.locator(".panel-strip")).to_contain_text("Turing 3.5")
        expect(page.locator(".start-mounted")).to_have_count(0)
        expect(page.locator(".start-step-number")).to_have_count(0)  # one step: no numbers
        page.locator(".theme-card-main").first.click()
        page.wait_for_selector(".first-steps")
        expect(page.locator(".first-steps [data-step='mounted']")).to_have_count(0)
    choose_theme(isolated_home, "lumen")  # (back to a first start's question below)
    (isolated_home / "config.toml").unlink()
    with browser_page(app_url, "home", routes={"**/api/app": a_panel_app()}, allow=PICTURE) as page:
        page.wait_for_selector(".start.first .theme-card")
        expect(page.locator(".start-mounted")).to_have_count(1)
        expect(page.locator(".start-step-number")).to_have_text(["1", "2"])


def test_game_mode_switched_on_from_the_overview_gets_its_game_screen(
    app_url,  # noqa: F811
    isolated_home,
):
    """As in the first steps: the row names the screen game mode shows, Arena on a bar, and the
    switch puts it there; off and on again, it stays."""
    choose_theme(isolated_home, "lumen")
    with browser_page(app_url, "home", routes={"**/api/app": a_panel_app()}, allow=PICTURE) as page:
        row = page.locator(".pages-card [data-page='game']")
        expect(row.locator(".pages-card-theme")).to_have_text("Arena")
        row.get_by_role("switch").click()
        waits_for(page, lambda: "game" in settings_config().services.enabled)
        assert game_theme() == "arena"
        row.get_by_role("switch").click()
        waits_for(page, lambda: game_option("automatic") is False)
        expect(row.locator(".pages-card-theme")).to_have_text("Arena")
        assert game_theme() == "arena"


def test_the_panels_card_shows_the_theme_while_the_panel_shows_nothing(
    app_url,  # noqa: F811
    isolated_home,
):
    choose_theme(isolated_home, "lumen")
    routes = {"**/api/app": an_app("waiting", target="")}
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        picture = page.locator(".panel-picture.still")
        expect(picture.locator("img")).to_have_attribute("src", re.compile(r"/api/themes/lumen/"))
        expect(picture.locator(".panel-picture-note")).to_have_text(
            "No panel yet: this is the theme it will show."
        )
        expect(page.locator(".panel-card h2")).to_have_text("Waiting for the panel")


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


def test_screens_in_turn_say_what_is_missing_and_what_is_not_saved(
    app_url,  # noqa: F811
    isolated_home,
):
    choose_theme(isolated_home, "lumen")
    with browser_page(app_url, "home/sequence", allow=PICTURE) as page:
        page.wait_for_selector(".sequence-add")
        foot = page.locator(".sequence-foot")
        expect(foot.locator(".sequence-unsaved")).to_have_count(0)
        page.get_by_role("button", name="Add a screen").click()
        expect(page.locator(".sequence-step select")).to_have_value("lumen")  # the one on the panel
        expect(foot).to_contain_text("Add another screen: with one, nothing changes.")
        expect(foot.locator(".sequence-unsaved")).to_have_text("Not saved yet")
        page.get_by_role("button", name="Add a screen").click()
        expect(foot).not_to_contain_text("Add another screen")
        page.get_by_role("button", name="Save and start").click()
        expect(page.get_by_role("button", name="Stop")).to_be_visible()
        expect(foot.locator(".sequence-unsaved")).to_have_count(0)
    assert settings_config().sequence.on
