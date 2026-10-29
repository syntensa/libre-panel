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
    and the toolbar said "No sensor". The cards are named in words, moved, taken out and added.
    Lumen's are those chosen on Start (``cards``) until the theme is given its own, and it can
    take them again."""
    choose_theme(isolated_home, "lumen")
    with browser_page(app_url) as page:
        js(page, "window.libre.doc.setSelection(['card'])")
        expect(page.locator(".floating-toolbar .tool-name")).to_have_text("Card slot")
        expect(page.locator(".floating-toolbar .tool-sensor")).to_have_count(0)
        inspector = page.locator(".inspector")
        cards = inspector.locator(".slot-card-name")
        expect(cards).to_have_text(["Playback card", "AI activity", "Weather card", "Calendar"])
        expect(inspector).to_contain_text("The cards chosen on Start")
        expect(inspector.locator(".inspector-subtitle")).to_have_text(
            "Playback card · AI activity · Weather card · Calendar"
        )
        inspector.locator(".slot-card").nth(2).get_by_role("button", name="Move up").click()
        assert items(page, "card") == "playback\nforecast\nai\ndate"  # (its own now)
        inspector.locator(".slot-card").nth(0).get_by_role("button", name="Take out").click()
        assert items(page, "card") == "forecast\nai\ndate"
        inspector.get_by_role("combobox", name="Add a card…").select_option("instrument")
        assert items(page, "card") == "forecast\nai\ndate\ninstrument"
        expect(cards).to_have_text(["Weather card", "AI activity", "Calendar", "Ring instrument"])
        page.keyboard.press("Control+z")
        assert items(page, "card") == "forecast\nai\ndate"
        inspector.get_by_role("button", name="Take the cards chosen on Start").click()
        assert items(page, "card") == ""
        expect(cards).to_have_text(["Playback card", "AI activity", "Weather card", "Calendar"])
        # a module that shows a source still has its chip
        js(page, "window.libre.doc.setSelection(['cpu-ring'])")
        expect(page.locator(".floating-toolbar .tool-sensor")).to_have_text("CPU")


def test_a_card_slot_takes_turns_each_card_for_its_minutes_and_changes_as_chosen(
    app_url,  # noqa: F811
    isolated_home,
):
    """A slot that leaves its cards to the person shows the person's order and transition and
    sets them (config.toml ``card_order``, ``card_transition``); one with cards of its own has
    its own, and in turn each card's minutes beside its name (``forecast for 10m``)."""
    choose_theme(isolated_home, "lumen")
    with browser_page(app_url) as page:
        js(page, "window.libre.doc.setSelection(['card'])")
        inspector = page.locator(".inspector")
        expect(inspector.get_by_role("radio", name="first with something to show")).to_be_checked()
        inspector.get_by_role("combobox", name="From one card to the next").select_option("slide")
        waits_for(page, lambda: settings_config().card_transition == "slide")
        assert widget(page, "card").get("transition", "light") == "light"  # (the person's)
        expect(inspector.get_by_role("textbox", name=re.compile("Minutes on show"))).to_have_count(
            0
        )
        inspector.locator(".slot-card").nth(2).get_by_role("button", name="Move up").click()
        assert items(page, "card") == "playback\nforecast\nai\ndate"  # (its own now)
        inspector.get_by_role("radio", name="in turn").click()
        assert widget(page, "card")["order"] == "turns"
        expect(inspector).to_contain_text("Each card is on show for its minutes")
        minutes = inspector.get_by_role("textbox", name="Minutes on show: Weather card")
        expect(minutes).to_have_value("5")
        minutes.fill("10")
        minutes.press("Enter")
        assert items(page, "card") == "playback\nforecast for 10m\nai\ndate"
        inspector.get_by_role("combobox", name="From one card to the next").select_option("rise")
        assert widget(page, "card")["transition"] == "rise"
        assert settings_config().card_order == "first"  # (the slot's own, not the person's)


def test_a_block_is_added_with_a_click_and_taken_back_from_the_message(app_url):  # noqa: F811
    """The library said "Drag onto the panel" while a click adds a block too; its "+" and its
    hint say so. What is removed can be taken back from the message, but not past a change
    that came after it."""
    with browser_page(app_url) as page:
        library = page.locator(".library")
        expect(library.locator(".design-steps")).to_contain_text("Click a block below to add it")
        library.get_by_role("button", name="Got it").click()  # (the hint says it after the steps)
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


def test_designs_pages_say_what_the_panel_shows_when_as_start_does(app_url, isolated_home):  # noqa: F811
    """Design's pages were cards whose picture had to be found to be clicked, "Normal" had no
    word of when it shows, and "+" over the canvas did not say what it adds. They read as Start's
    "What the panel shows when": each page, when it is on show, its switch and its theme, and a
    way to edit it; the tabs' "+ Page" says it shows this theme on another page."""
    from libre_panel.config import set_config_value

    choose_theme(isolated_home, "lumen")
    set_config_value("modes.game.theme", "arena")
    with browser_page(app_url) as page:
        page.locator(".rail-item", has_text="Pages").click()
        side = page.locator(".side")
        expect(side.locator(".side-title").first).to_have_text("What the panel shows when")
        cards = side.locator(".page-card")
        assert cards.evaluate_all("cs => cs.map((c) => c.dataset.page)")[:4] == [
            "normal", "game", "media", "idle"
        ]  # fmt: skip
        normal = side.locator(".page-card[data-page='normal']")
        expect(normal.locator(".page-card-name")).to_contain_text("The rest of the time")
        expect(normal.locator(".page-card-always")).to_have_text("always")
        expect(normal.locator(".page-card-badge")).to_have_text("Being edited")
        game = side.locator(".page-card[data-page='game']")
        expect(game.locator(".page-card-name")).to_contain_text("While a game runs")
        game.get_by_role("button", name="Edit", exact=True).click()
        page.wait_for_function("window.libre.store.get().doc.id === 'arena'")
        expect(game.locator(".page-card-badge")).to_have_count(1)
        expect(page.locator(".page-tab[aria-selected='true']")).to_have_text("In game")
        # the tabs' "+ Page": this theme on another page, said with when that is
        add = page.locator(".page-add")
        expect(add).to_have_text("Page")
        add.click()
        items = page.locator(".popover .menu-item").all_inner_texts()
        assert items[0] == "As the normal theme"
        assert "Music · While music plays" in items and items[-1] == "A plugin's page…"


def test_the_background_colour_says_it_lies_under_the_picture(app_url, isolated_home):  # noqa: F811
    """In Lumen the background picture covers the whole panel, so a colour picked for the
    background changed nothing that could be seen. With a picture the colour says it lies
    under it, and the picture is removed in one click (and back with Ctrl+Z)."""
    choose_theme(isolated_home, "lumen")
    with browser_page(app_url) as page:
        inspector = page.locator(".inspector")
        background = inspector.locator(".section").filter(
            has=page.locator(".section-title", has_text="Background")
        )
        labels = background.locator(".field-label")
        expect(labels).to_have_text(["Picture", "Colour under the picture"])
        expect(background.locator(".background-under")).to_contain_text("see-through")
        background.locator(".swatch").nth(1).click()
        assert js(page, "window.libre.doc.theme().background.color") != "@bg"
        background.get_by_role("button", name="Remove the picture").click()
        assert js(page, "window.libre.doc.theme().background.image") is None
        expect(labels).to_have_text(["Colour", "Picture"])  # the colour is the background now
        expect(background.locator(".background-under")).to_have_count(0)
        page.keyboard.press("Control+z")
        assert js(page, "window.libre.doc.theme().background.image") == "assets/backdrop.png"


def test_look_says_what_each_part_does_and_tries_a_choice_on(app_url, isolated_home):  # noqa: F811
    """Look's parts had no word of what they do, or one about "SPUR II's screens" and "a panel
    that is sent pictures". Each says it in a line, and a choice the pointer rests on is tried
    on the picture before a click keeps it: a colour world, a notice style with a notice."""
    choose_theme(isolated_home, "lumen")
    with browser_page(app_url, "look") as page:
        page.wait_for_selector(".look-picture img")
        side = page.locator(".look-side")
        expect(side.locator(".look-intro")).to_contain_text("try it on")
        text = side.inner_text()
        assert "SPUR II" not in text and "sent pictures" not in text
        for name in ("Finish and glow", "Panel light", "Grid background", "Depth dust", "Notices"):
            part = side.locator(".section").filter(
                has=page.locator(".section-title", has_text=name)
            )
            expect(part.locator(".look-about").first).not_to_be_empty()
        picture = page.locator(".look-picture img")
        plain = picture.get_attribute("src")
        accent = js(page, "window.libre.doc.theme().palette.accent")
        side.locator(".look-tile", has_text="Neon").hover()
        expect(picture).not_to_have_attribute("src", plain)
        assert js(page, "window.libre.doc.theme().palette.accent") == accent  # only tried on
        side.locator(".look-intro").hover()
        expect(picture).to_have_attribute("src", plain)
        # a notice style is tried on with a notice on show
        asked = []
        page.on(
            "request",
            lambda r: asked.append(r.post_data_json) if r.url.endswith("/api/render") else None,
        )
        side.locator(".look-notice", has_text="Band").hover()
        expect(picture).not_to_have_attribute("src", plain)
        tried = [body for body in asked if body.get("notice")]
        assert tried and tried[-1]["theme"]["toast"]["style"] == "band"
        assert not js(page, "window.libre.doc.theme().toast?.style")
        side.locator(".look-notice", has_text="Band").click()
        assert js(page, "window.libre.doc.theme().toast.style") == "band"
        shown = "document.querySelector('.look-picture img').src"
        page.wait_for_function(f"{shown} === window.libre.store.get().render.png")


def test_a_first_visit_has_three_steps_and_an_empty_theme_says_how_to_fill_it(app_url):  # noqa: F811
    """Design opened with no word of where to start: three steps are on top of the blocks until
    "Got it" (remembered), and a theme with nothing on it says how to fill it, with the way to
    the blocks where they are not open. The layers of an empty theme say what they will be."""
    with browser_page(app_url) as page:
        steps = page.locator(".design-steps")
        expect(steps.locator("li")).to_have_count(3)
        expect(steps).to_contain_text("Ctrl+Z takes back any step")
        expect(page.locator(".library-hint")).to_have_count(0)  # the steps say it
        steps.get_by_role("button", name="Got it").click()
        expect(steps).to_have_count(0)
        expect(page.locator(".library-hint")).to_have_count(1)
        page.reload()
        page.wait_for_selector(".panel-canvas .preview")
        expect(page.locator(".library")).to_be_visible()
        expect(steps).to_have_count(0)  # remembered
        # a theme with nothing on it
        js(page, "window.libre.doc.edit(null, (theme) => { theme.widgets = []; })")
        empty = page.locator(".canvas-empty")
        expect(empty).to_contain_text("This theme is still empty")
        expect(empty).to_contain_text("Click a block on the left")
        expect(empty.get_by_role("button")).to_have_count(0)  # the blocks are open
        page.locator(".rail-item", has_text="Layers").click()
        hints = page.locator(".side .side-hint")
        expect(hints).to_have_count(1)
        expect(hints).to_contain_text("Nothing on the panel yet")
        empty.get_by_role("button", name="Show the blocks").click()
        page.locator('.tile[data-item="module:clock"]').click()
        expect(empty).to_have_count(0)


def test_the_tips_name_what_is_not_in_sight_and_the_view_says_what_it_does(app_url):  # noqa: F811
    """Selecting several, the menu of a right click, locking, moving freely and the keys were
    nowhere to be read in Design; the rail's Tips name them and lead to every key and to the
    three steps again. The view's choices each say what they do, above their buttons (the module
    grid's hint stood beside them as a column of single words)."""
    with browser_page(app_url) as page:
        page.locator(".design-steps").get_by_role("button", name="Got it").click()
        page.locator(".rail-tips").click()
        tips = page.locator(".popover .tip-list li")
        expect(tips).to_have_count(6)
        expect(tips.first).to_contain_text("Shift+click")
        expect(tips.last).to_contain_text("Ctrl+K finds every block")
        page.get_by_role("button", name="The three steps again").click()
        expect(page.locator(".design-steps")).to_have_count(1)
        page.locator(".rail-tips").click()
        page.get_by_role("button", name="All keyboard shortcuts").click()
        expect(page.locator(".dialog .dialog-title")).to_have_text("Help")
        expect(page.locator(".dialog #help-keys")).to_be_in_viewport()  # at its keys
        page.keyboard.press("Escape")
        page.locator(".rail-gear").click()
        view = page.locator(".popover")
        expect(view.locator(".row.stacked .row-label")).to_have_count(2)
        expect(view.get_by_role("radiogroup", name="Steps of the modules")).to_be_visible()
        expect(view).to_contain_text("Alt while dragging: free")


def test_on_a_bar_on_end_design_edits_the_upright_version_and_says_so(app_url, isolated_home):  # noqa: F811
    """On a bar on end Design opened Lumen lying on its side (the settings came after the theme),
    and "On the panel" said the panel showed it. It opens Lumen Vertical, says which version it
    is and what it goes with, follows when the panel is turned, and a copy is named to go with
    a copy of Lumen."""
    from libre_panel.config import set_config_value

    choose_theme(isolated_home, "lumen")
    set_config_value("device.model", "turing-9.2-usb")
    set_config_value("device.orientation", "portrait")
    with browser_page(app_url) as page:
        page.wait_for_function("window.libre.store.get().doc?.id === 'lumen-vertical'")
        version = page.locator(".inspector .inspector-version")
        expect(version).to_contain_text("The version of Lumen for a panel mounted vertically")
        expect(page.locator(".btn-apply")).to_have_text("On the panel")
        # the panel turned in Settings: Design follows it, and back
        mounted = page.locator(".settings-row", has_text="How the panel is mounted")
        for way, theme in (("Horizontal", "lumen"), ("Vertical", "lumen-vertical")):
            page.goto(f"{app_url}#/settings/panel")
            mounted.get_by_role("radio", name=way).click()
            # written before Design loads anew (else, on a busy machine, it reads the old way)
            orientation = "landscape" if way == "Horizontal" else "portrait"
            waits_for(page, lambda o=orientation: settings_config().device.orientation == o)
            page.goto(f"{app_url}#/design")
            page.wait_for_function(f"window.libre.store.get().doc?.id === {theme!r}")
        expect(version).to_have_count(1)
        # saved as one's own: named to go with a copy of Lumen ("lumen-mine")
        js(page, "window.libre.doc.edit('n', (theme) => { theme.description = 'mine'; })")
        page.locator(".btn-apply").click()
        dialog = page.locator(".dialog")
        expect(dialog.locator("input")).to_have_value("lumen-mine-vertical")
        dialog.locator("button[type='submit']").click()
        waits_for(page, lambda: settings_config().theme == "lumen-mine-vertical")


def test_the_volume_is_switched_on_for_every_screen_and_its_look_tried_on_in_look(
    app_url,  # noqa: F811
    isolated_home,
):
    """Look → Notices → Volume: the switch turns the notices and their volume on (settings, not
    the theme's), and a look is tried on with a volume notice before a click keeps it."""
    choose_theme(isolated_home, "lumen")
    with browser_page(app_url, "look", allow=PICTURE) as page:
        volume = page.locator("[data-look-volume]")
        volume.wait_for()
        switch = volume.get_by_role("switch", name="Show the volume")
        expect(switch).to_have_attribute("aria-checked", "false")
        switch.click()
        waits_for(page, lambda: "notices" in settings_config().services.enabled)
        notices = lambda: settings_config().services.options.get("notices", {})  # noqa: E731
        waits_for(page, lambda: notices().get("volume", True) is True)  # (on is the default)
        expect(switch).to_have_attribute("aria-checked", "true")
        names = volume.locator("[role='radio'] strong")
        expect(names).to_have_text(["As the notices", "Bar", "Levels", "Ring", "Large"])
        with page.expect_request(
            lambda r: (
                r.url.endswith("/api/render")
                and '"look": "ring"' in (r.post_data or "").replace('":"', '": "')
            )
        ):
            volume.get_by_role("radio", name=re.compile("^Ring")).hover()
        volume.get_by_role("radio", name=re.compile("^Ring")).click()
        waits_for(page, lambda: notices().get("volume_look") == "ring")
        expect(volume.get_by_role("radio", name=re.compile("^Ring"))).to_have_attribute(
            "aria-checked", "true"
        )
        assert not page.locator(".unsaved").count() or not page.locator(".unsaved").is_visible()


def test_design_edits_the_theme_of_the_panel_chosen_in_the_header(app_url, isolated_home):  # noqa: F811
    """Panel 1 | Panel 2 in the header: Design opens the theme panel 2 shows, as it is mounted
    (on end), and what is saved there goes to panel 2; the first panel's theme stays."""
    choose_theme(isolated_home, "lumen")
    with isolated_home.joinpath("config.toml").open("a", encoding="utf-8") as config:
        config.write('\n[[panels]]\nmodel = "turing-9.2-usb"\norientation = "portrait"\n'
                     'theme = "lumen-companion"\n')  # fmt: skip
    with browser_page(app_url, "design") as page:
        page.wait_for_function("window.libre.store.get().doc?.id === 'lumen'")
        switch = page.get_by_role("radiogroup", name="Which panel Live shows and Design edits")
        expect(switch.get_by_role("radio")).to_have_text(["Panel 1", "Panel 2"])
        switch.get_by_role("radio", name="Panel 2").click()
        page.wait_for_function("window.libre.store.get().doc?.id === 'lumen-companion-vertical'")
        expect(page.locator(".btn-apply")).to_have_text("On the panel")
        # a change saved as one's own goes on panel 2
        js(page, "window.libre.doc.edit('n', (theme) => { theme.description = 'mine'; })")
        page.locator(".btn-apply").click()
        dialog = page.locator(".dialog")
        dialog.locator("input").fill("my-companion")
        dialog.get_by_role("button", name="Save").click()
        waits_for(page, lambda: settings_config().panels[0].theme == "my-companion")
        assert settings_config().theme == "lumen"
        switch.get_by_role("radio", name="Panel 1").click()
        page.wait_for_function("window.libre.store.get().doc?.id === 'lumen'")


def test_look_and_the_panel_chip_follow_the_panel_chosen_in_the_header(app_url, isolated_home):  # noqa: F811
    """Look edits what Design edits: Panel 2 in the header opens the theme panel 2 shows there
    too, and the chip names the chosen panel's model as it runs (the 12.3" first, the 9.2"
    beside it), not the model the open theme was made for."""
    import json

    from test_app_browser import app_answer

    choose_theme(isolated_home, "lumen")
    with isolated_home.joinpath("config.toml").open("a", encoding="utf-8") as config:
        config.write('\n[[panels]]\nmodel = "turing-9.2-usb"\ntheme = "lumen-companion"\n')
    answer = app_answer()
    answer["panel"] = {**answer["panel"], "theme": "lumen", "model": "turing-12.3-usb",
                       "more": [{"index": 0, "state": "showing", "detail": "", "target": "",
                                 "theme": "lumen-companion", "model": "turing-9.2-usb",
                                 "size": None}]}  # fmt: skip

    def app(route, request):
        if request.method != "GET":
            return route.continue_()
        route.fulfill(status=200, content_type="application/json", body=json.dumps(answer))

    with browser_page(app_url, "look", routes={"**/api/app": app}) as page:
        page.wait_for_function("window.libre.store.get().doc?.id?.startsWith('lumen')")
        chip = page.locator(".panel-chip-name")
        expect(chip).to_contain_text("12.3")
        switch = page.get_by_role("radiogroup", name="Which panel Live shows and Design edits")
        switch.get_by_role("radio", name="Panel 2").click()
        page.wait_for_function(
            "window.libre.store.get().doc?.id?.startsWith('lumen-companion')"
        )  # (in Look too, not only in Design)
        expect(chip).to_contain_text("9.2")
        switch.get_by_role("radio", name="Panel 1").click()
        page.wait_for_function("!window.libre.store.get().doc?.id?.startsWith('lumen-companion')")
        expect(chip).to_contain_text("12.3")


def test_while_its_companion_runs_design_edits_the_pair_version(app_url, isolated_home):  # noqa: F811
    """The panel shows Lumen's pair version beside its companion: Design opens that one, and a
    copy of one's own goes with a copy of Lumen ("lumen-mine-pair")."""
    import json

    from test_app_browser import app_answer

    choose_theme(isolated_home, "lumen")
    answer = app_answer()
    answer["panel"] = {**answer["panel"], "theme": "lumen-pair"}

    def app(route, request):
        if request.method != "GET":
            return route.continue_()
        route.fulfill(status=200, content_type="application/json", body=json.dumps(answer))

    with browser_page(app_url, "design", routes={"**/api/app": app}) as page:
        page.wait_for_function("window.libre.store.get().doc?.id === 'lumen-pair'")
        js(page, "window.libre.doc.edit('n', (theme) => { theme.description = 'mine'; })")
        page.locator(".btn-apply").click()
        expect(page.locator(".dialog input")).to_have_value("lumen-mine-pair")
