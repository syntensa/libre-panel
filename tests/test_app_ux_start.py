"""Start in a real browser: the order of the launcher, what is new for who updated, the first
steps for who began with this version, the mounting question where it matters, the actions of
the gallery and the screens in turn; skipped without Playwright or Chromium."""

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

from libre_panel import __version__  # noqa: E402


def updated(home, seen=""):
    """What news.json says where Libre Panel ran in a version from before this release."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "news.json").write_text(json.dumps({"first": "older", "seen": seen}), "utf-8")


def news_file(home):
    return json.loads((home / "news.json").read_text(encoding="utf-8"))


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


def test_who_updated_is_told_what_is_new_once_and_each_thing_is_done_right_there(
    app_url,  # noqa: F811
    isolated_home,
):
    choose_theme(isolated_home, "lumen")
    updated(isolated_home)
    with browser_page(app_url, "home", routes={"**/api/app": a_panel_app()}, allow=PICTURE) as page:
        card = page.locator(".news-card")
        expect(card.locator("h2")).to_have_text("What's new in 0.6")
        expect(page.locator(".first-steps")).to_have_count(0)  # who updated knows the rest
        rows = card.locator(".first-step")
        assert rows.evaluate_all("r => r.map((e) => e.dataset.step)") == [
            "upright",
            "arena",
            "overview",
        ]
        # bars on end: the choice right there
        card.locator("[data-step='upright']").get_by_role("radio", name="Vertical").click()
        waits_for(page, lambda: settings_config().device.orientation == "portrait")
        expect(card.locator("[data-step='upright']")).to_have_class(re.compile(r"\bdone\b"))
        # Arena: game mode on, with Arena
        card.get_by_role("button", name="Use Arena in games").click()
        waits_for(page, lambda: "game" in settings_config().services.enabled)
        assert game_theme() == "arena"
        expect(card.locator("[data-step='arena']")).to_have_class(re.compile(r"\bdone\b"))
        game = page.locator(".pages-card [data-page='game']")
        expect(game.get_by_role("switch")).to_have_attribute("aria-checked", "true")
        # the overview, shown and marked
        card.get_by_role("button", name="Show me").click()
        expect(page.locator(".pages-card")).to_have_class(re.compile(r"\bpointed\b"))
        expect(page.locator(".pages-card")).to_be_in_viewport()
        # closed: kept by the server, for the window and any browser alike
        card.get_by_role("button", name="Got it").click()
        expect(card).to_have_count(0)
        waits_for(page, lambda: news_file(isolated_home)["seen"] == "0.6")
        page.reload()
        page.wait_for_selector(".pages-card")
        page.wait_for_function("window.libre.store.get().news?.seen === '0.6'")
        expect(page.locator(".news-card")).to_have_count(0)
        expect(page.locator(".first-steps")).to_have_count(0)


def test_who_began_with_this_version_gets_the_first_steps_and_no_news(
    app_url,  # noqa: F811
    isolated_home,
):
    """A first start: the choice goes on the panel and Start goes on to the launcher, where the
    panel's card leads and the first steps follow."""
    with browser_page(app_url, "home", routes={"**/api/app": a_panel_app()}, allow=PICTURE) as page:
        page.wait_for_selector(".start.first .theme-card")
        page.locator(".theme-card[data-theme='lumen'] .theme-card-main").click()
        page.wait_for_selector(".start.launcher .first-steps")
        expect(page.locator(".panel-card h2")).to_contain_text("Turing 9.2")
        expect(page.locator(".news-card")).to_have_count(0)
        assert main_column(page)[0] == "panel-card"
        assert news_file(isolated_home) == {"first": __version__, "seen": ""}


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


def test_start_switches_the_volume_on_for_every_screen_and_leads_to_its_look(
    app_url,  # noqa: F811
    isolated_home,
):
    """More for your panel: the volume over every screen, switched on right there, and its
    style a click away in Look."""
    choose_theme(isolated_home, "lumen")
    with browser_page(app_url, "home", routes={"**/api/app": a_panel_app()}, allow=PICTURE) as page:
        tile = page.locator("[data-volume-tile]")
        expect(tile).to_contain_text("When you turn it, on every screen")
        tile.get_by_role("switch", name="Show the volume").click()
        waits_for(page, lambda: "notices" in settings_config().services.enabled)
        expect(tile).to_contain_text("On every screen: As the notices")
        tile.get_by_role("button", name="Style").click()
        page.wait_for_url(re.compile(r"#/look$"))
        expect(page.locator("[data-look-volume]")).to_be_in_viewport()


def update_route(latest=None, kind="setup"):
    """/api/update as the server answers it, GitHub's part made up: whether to look comes from
    config.toml as the server reads it; a POST of "later" is kept for the next answer."""
    later = {"version": ""}

    def handler(route, request):
        if request.method == "POST":
            later["version"] = json.loads(request.post_data)["later"]
        check = settings_config().check_updates
        body = {
            "check": check,
            "current": __version__,
            "kind": kind,
            "latest": latest if check else None,
            "newer": bool(check and latest),
            "asked": 1_790_000_000 if check else None,
            "error": "",
            "later": later["version"],
        }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    return handler


NEW = {
    "version": "9.1.0",
    "page": "https://github.com/syntensa/libre-panel/releases/tag/v9.1.0",
    "download": "https://github.com/syntensa/libre-panel/releases/download/v9.1.0/setup.exe",
    "notes": "Something new on every screen.",
    "published": "2026-11-01T10:00:00Z",
}


def test_start_asks_once_whether_to_look_for_new_versions(app_url, isolated_home):  # noqa: F811
    choose_theme(isolated_home, "lumen")
    routes = {"**/api/update*": update_route()}
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        card = page.locator("[data-update='ask']")
        expect(card).to_contain_text("Hear of new versions?")
        expect(card).to_contain_text("nothing of you or your panel is sent")
        card.get_by_role("button", name="Yes, look").click()
        waits_for(page, lambda: settings_config().check_updates is True)
        expect(card).to_have_count(0)  # asked once: nothing newer, no card
        page.reload()
        page.wait_for_selector(".view > *")
        expect(page.locator("[data-update]")).to_have_count(0)


def test_no_thanks_is_kept_and_settings_switch_it_on_later(app_url, isolated_home):  # noqa: F811
    choose_theme(isolated_home, "lumen")
    routes = {"**/api/update*": update_route()}
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        page.get_by_role("button", name="No, thanks").click()
        waits_for(page, lambda: settings_config().check_updates is False)
        expect(page.locator("[data-update]")).to_have_count(0)
        page.goto(f"{app_url}#/settings/system")
        row = page.locator("[data-updates]")
        switch = row.get_by_role("switch", name="Look for new versions")
        expect(switch).to_have_attribute("aria-checked", "false")
        switch.click()
        waits_for(page, lambda: settings_config().check_updates is True)
        expect(row).to_contain_text(f"Up to date ({__version__})")
        expect(row.get_by_role("button", name="Look now")).to_be_visible()


def test_a_newer_version_shows_with_its_download_until_later(app_url, isolated_home):  # noqa: F811
    from libre_panel.config import set_config_value

    choose_theme(isolated_home, "lumen")
    set_config_value("check_updates", True)
    routes = {"**/api/update*": update_route(NEW)}
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        card = page.locator("[data-update='new']")
        expect(card).to_contain_text("Libre Panel 9.1.0 is out")
        expect(card).to_contain_text("Something new on every screen.")
        expect(card).to_contain_text(f"You have {__version__}.")
        expect(card.get_by_role("link", name="Download")).to_have_attribute("href", NEW["download"])
        expect(card.get_by_role("link", name="What is new")).to_have_attribute("href", NEW["page"])
        with page.expect_request(lambda r: r.url.endswith("/api/update") and r.method == "POST"):
            card.get_by_role("button", name="Later").click()
        expect(card).to_have_count(0)
        page.goto(f"{app_url}#/settings/system")
        row = page.locator("[data-updates]")
        expect(row).to_contain_text("Libre Panel 9.1.0 is out.")
        expect(row.get_by_role("link", name="Get 9.1.0")).to_have_attribute("href", NEW["download"])


def test_installed_with_pip_the_card_gives_the_command(app_url, isolated_home):  # noqa: F811
    from libre_panel.config import set_config_value

    choose_theme(isolated_home, "lumen")
    set_config_value("check_updates", True)
    routes = {"**/api/update*": update_route({**NEW, "download": ""}, kind="python")}
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        card = page.locator("[data-update='new']")
        expect(card.locator("code")).to_contain_text("libre-panel@v9.1.0")
        expect(card.get_by_role("link", name="Download")).to_have_count(0)
        expect(card.get_by_role("link", name="What is new")).to_be_visible()
