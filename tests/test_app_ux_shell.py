"""The app's shell for 0.6.0: Ctrl+K finds every feature by the words a person uses (German and
English), what it suggests and what it says when it finds nothing, the help, the header the views
share and the way back from Settings, the panel chip, and the messages and dialogs: plain words
for a failure, and unsaved work saved first. Skipped where Playwright or Chromium are missing, as
test_app_browser.py is."""

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402
from test_app_browser import (  # noqa: E402, F401
    PICTURE,
    a_panel_app,
    app_url,
    browser_page,
    choose_theme,
    js,
    no_setup_card,
    settings_config,
    waits_for,
)


def search(page, query, count=3):
    """The titles of the first commands Ctrl+K finds for ``query``."""
    found = "([q, n]) => window.libre.commands.searchCommands(q, n).map((r) => r.title)"
    return page.evaluate(found, [query, count])


def palette(page, query):
    """Ctrl+K with ``query`` typed; the palette's input."""
    page.keyboard.press("Control+k")
    field = page.locator(".palette input")
    field.fill(query)
    return field


def a_bar(home):
    """Not a first start, and the app sees a 9.2" bar running: every command is offered."""
    from libre_panel.config import set_config_value

    choose_theme(home, "lumen")
    set_config_value("device.model", "turing-9.2-usb")
    return {"**/api/app": a_panel_app()}


# -- Ctrl+K by the words a person uses -------------------------------------------------------------

ENGLISH = {
    "portrait": "Panel mounted: vertical",
    "on end": "Panel mounted: vertical",
    "upside down": "Upside down: on",
    "rotate": "Upside down: on",
    "boot logo": "Start screen: boot logo and standby clip",
    "smooth": "Smooth motion (video mode)",
    "video": "Smooth motion (video mode)",
    "game mode": "Game mode: on",
    "music": "Music page: on",
    "idle": "Idle page: on",
    "brightness": "Brightness",
    "place": "Weather for your place",
    "weather": "Weather for your place",
    "notices": "Notices: on",
    "autostart": "Start with system: on",
    "folder": "Open settings folder",
    "log": "Open log",
    "report a bug": "Report a problem or an idea",
    "help": "Help and keyboard shortcuts",
    "keyboard": "Help and keyboard shortcuts",
    "graphics card": "Hardware names",
    "model": "Which panel is connected",
    "language": "Language",
    "screens in turn": "Screens in turn",
}

GERMAN = {
    "hochkant": "Panel eingebaut: hochkant",
    "vertikal": "Panel eingebaut: hochkant",
    "senkrecht": "Panel eingebaut: hochkant",
    "kopfüber": "Auf dem Kopf: an",
    "Spielmodus": "Spielmodus: an",
    "Spiel": "Spielmodus: an",
    "Musik": "Musikseite: an",
    "Logo": "Startbildschirm: Startlogo und Standby-Clip",
    "Bootlogo": "Startbildschirm: Startlogo und Standby-Clip",
    "Helligkeit": "Helligkeit",
    "Wetter": "Wetter für deinen Ort",
    "Ort": "Wetter für deinen Ort",
    "Sprache": "Sprache",
    "Autostart": "Mit dem System starten: an",
    "Ordner": "Ordner deiner Themes öffnen",
    "Fehler melden": "Ein Problem oder eine Idee melden",
    "flüssig": "Flüssige Bewegung (Videomodus)",
    "Video": "Flüssige Bewegung (Videomodus)",
    "Hinweise": "Hinweise: an",
    "Mitteilungen": "Hinweise: an",
    "Leerlauf": "Leerlaufseite: an",
    "Rundenbericht": "Rundenbericht: an",
    "Startbildschirm": "Startbildschirm: Startlogo und Standby-Clip",
    "Abfolge": "Abfolge",
    "Hilfe": "Hilfe und Tastenkürzel",
    "Tastatur": "Hilfe und Tastenkürzel",
    "Grafikkarte": "Hardware-Namen",
    "Modell": "Welches Panel angeschlossen ist",
}


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_palette_finds_every_feature_by_everyday_words(app_url, isolated_home, language):  # noqa: F811
    """A person types what they think of, not the name a card gives it: "hochkant" is how the
    panel is mounted, "Bootlogo" the start screen, "Fehler melden" the issue form. The feature
    leads what is found (or is among the first two where a building block of that name exists)."""
    from libre_panel import i18n

    routes = a_bar(isolated_home)
    i18n.set_language(language)
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        page.wait_for_function("Boolean(window.libre.store.get().pages?.pages)")
        palette(page, "")  # the first search asks for what some commands need (the settings)
        page.wait_for_function("Boolean(window.libre.store.get().settings?.config)")
        page.wait_for_function("Boolean(window.libre.store.get().aboutInfo?.links)")
        page.keyboard.press("Escape")
        words = GERMAN if language == "de" else ENGLISH
        missed = {}
        for query, wanted in words.items():
            found = search(page, query, 2)
            if wanted not in found:
                missed[query] = found
        assert missed == {}


def test_the_palette_suggests_what_most_people_come_for(app_url, isolated_home):  # noqa: F811
    routes = a_bar(isolated_home)
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        palette(page, "")
        groups = page.locator(".palette-group-title")
        expect(groups.first).to_have_text("Suggested")
        first = page.locator(".palette-group").first.locator(".palette-title")
        titles = first.all_inner_texts()
        assert titles[:3] == ["All themes", "What the panel shows when", "Brightness"]
        assert "Help and keyboard shortcuts" in titles
        assert "To the panel" not in titles  # only where the theme being changed is shown
        page.keyboard.press("Escape")
        page.goto(f"{app_url}#/design")
        page.wait_for_selector(".panel-canvas .preview")
        palette(page, "")
        first = page.locator(".palette-group").first.locator(".palette-title")
        expect(first.first).to_have_text("To the panel")


def test_a_search_that_finds_nothing_says_what_to_try(app_url, isolated_home):  # noqa: F811
    routes = a_bar(isolated_home)
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        field = palette(page, "xyzzy")
        empty = page.locator(".palette-empty")
        expect(empty).to_contain_text("Nothing found for “xyzzy”")
        expect(empty).to_contain_text("Try fewer letters")
        empty.get_by_role("button", name="start screen").click()
        expect(field).to_have_value("start screen")
        expect(field).to_be_focused()
        expect(page.locator(".palette-item.active")).to_contain_text("Start screen")


def test_the_palette_mounts_the_panel_and_switches_a_page(app_url, isolated_home):  # noqa: F811
    """What a card on Start does, Ctrl+K does too: config.toml and the pages follow."""
    from libre_panel.config import load_config

    routes = a_bar(isolated_home)
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        page.wait_for_function("Boolean(window.libre.store.get().pages?.pages)")
        palette(page, "hochkant")
        page.wait_for_function("Boolean(window.libre.store.get().settings?.config)")
        page.locator(".palette input").fill("on end")
        expect(page.locator(".palette-item.active")).to_contain_text("Panel mounted: vertical")
        page.keyboard.press("Enter")
        waits_for(page, lambda: load_config().device.orientation == "portrait")
        expect(page.locator(".toast.success")).to_contain_text("panel on end")
        palette(page, "music")
        expect(page.locator(".palette-item.active")).to_contain_text("Music page: on")
        page.keyboard.press("Enter")
        page.wait_for_function(
            "window.libre.store.get().pages.pages.find((p) => p.id === 'media').on === true"
        )
        palette(page, "music")  # and the same words switch it off again
        expect(page.locator(".palette-item.active")).to_contain_text("Music page: off")


def test_a_setting_in_a_card_is_found_and_shown(app_url, isolated_home):  # noqa: F811
    """A command for a card of Settings opens its section with the card in view."""
    routes = a_bar(isolated_home)
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        palette(page, "bootlogo")
        page.keyboard.press("Enter")
        page.wait_for_function("location.hash === '#/settings/panel/start-screen'")
        expect(page.locator(".settings-nav [aria-current='page']")).to_have_text("Panel")


# -- help ------------------------------------------------------------------------------------------


def test_help_has_the_links_and_the_keys(app_url, isolated_home):  # noqa: F811
    """? (and F1, also from the palette) shows how to find anything, where to read and to report,
    and every view's keys; the focus is on Close, not on a link that would leave the app."""
    routes = a_bar(isolated_home)
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        page.keyboard.press("?")
        dialog = page.locator(".dialog")
        expect(dialog.locator(".dialog-title")).to_have_text("Help")
        expect(dialog.get_by_role("button", name="Close")).to_be_focused()
        links = dialog.locator(".help-link")
        expect(links).to_have_count(3)
        hrefs = links.evaluate_all("links => links.map((a) => a.getAttribute('href'))")
        assert hrefs[0].endswith("#faq") and hrefs[1].endswith("/issues")
        assert "panel_support" in hrefs[2]
        assert all(link == "_blank" for link in links.evaluate_all("l => l.map((a) => a.target)"))
        groups = dialog.locator(".shortcut-group-title").all_inner_texts()
        assert groups[0] == "Everywhere" and "Design" in groups
        page.keyboard.press("Escape")
        expect(dialog).to_have_count(0)
        palette(page, "")
        page.keyboard.press("F1")
        expect(page.locator(".palette")).to_have_count(0)
        expect(page.locator(".dialog .dialog-title")).to_have_text("Help")


def test_help_speaks_german(app_url, isolated_home):  # noqa: F811
    from libre_panel import i18n

    routes = a_bar(isolated_home)
    i18n.set_language("de")
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        page.keyboard.press("?")
        dialog = page.locator(".dialog")
        expect(dialog.locator(".dialog-title")).to_have_text("Hilfe")
        expect(dialog).to_contain_text("„hochkant“")
        expect(dialog.locator(".help-link").first).to_contain_text("Fragen und Antworten")


# -- the header ------------------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_header_is_the_same_in_every_view(app_url, isolated_home, language):  # noqa: F811
    """The search field sits right after the tabs in every view (it was at the right in Look,
    Start and Live and beside the tabs in Design), each tab says what it is for, and the way to
    the settings is in the same corner."""
    from libre_panel import i18n

    routes = a_bar(isolated_home)
    i18n.set_language(language)
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        for view in ("home", "design", "look", "live", "settings/panel"):
            page.goto(f"{app_url}#/{view}")
            page.wait_for_selector(".view > *")
            order = js(
                page,
                """[...document.querySelectorAll('.topbar > *')].map((el) =>
                    el.classList.contains('tabs') ? 'tabs'
                    : el.classList.contains('search-trigger') ? 'search'
                    : el.classList.contains('settings-btn') ? 'settings' : '')
                  .filter(Boolean)""",
            )
            assert order == ["tabs", "search", "settings"], view
        titles = page.locator(".tabs .tab").evaluate_all("tabs => tabs.map((t) => t.title)")
        assert len(titles) == 4 and all(len(title) > 12 for title in titles)


def test_the_settings_button_leads_back_to_where_you_were(app_url, isolated_home):  # noqa: F811
    routes = a_bar(isolated_home)
    with browser_page(app_url, "look", routes=routes, allow=PICTURE) as page:
        page.wait_for_selector(".look-side")
        button = page.locator(".settings-btn")
        button.click()
        page.wait_for_function("location.hash.startsWith('#/settings')")
        page.locator(".settings-nav button", has_text="Sensors").click()  # a section or two later
        page.wait_for_function("location.hash === '#/settings/sensors'")
        expect(button).to_have_attribute("aria-pressed", "true")
        expect(button).to_have_attribute("title", "Back to Look (Esc)")
        button.click()
        page.wait_for_function("location.hash === '#/look'")
        page.wait_for_selector(".look-side")
        button.click()
        page.wait_for_function("location.hash.startsWith('#/settings')")
        page.wait_for_selector(".settings-nav")  # Settings is shown, not only its address
        page.keyboard.press("Escape")
        page.wait_for_function("location.hash === '#/look'")


def test_the_chip_shows_the_running_panel_first(app_url, isolated_home):  # noqa: F811
    """What most people open the chip for (how the panel is doing, brightness, pause) comes
    first; which panel the theme is made for, which fits the theme and not the panel, after it."""
    routes = a_bar(isolated_home)
    with browser_page(app_url, "design", routes=routes, allow=PICTURE) as page:
        page.locator(".panel-chip").click()
        titles = page.locator(".popover .pop-title").all_inner_texts()
        assert [title.lower() for title in titles] == ["on the panel now", "this theme is made for"]
        expect(page.locator(".popover .pop-hint")).to_contain_text("Settings → Panel")


# -- messages and dialogs --------------------------------------------------------------------------


def test_a_failure_says_what_happened_in_plain_words(app_url, isolated_home):  # noqa: F811
    """The server's reason without the name of the exception and the system's error numbers: a
    file that may not be changed or another program holds, a full disk, a bug in Libre Panel."""
    import json

    def refused(route, request):
        if request.method != "POST":
            return route.continue_()
        reason = "[Errno 13] Permission denied: 'C:\\\\Panel\\\\themes\\\\mine\\\\theme.json'"
        body = json.dumps({"error": reason})
        route.fulfill(status=409, content_type="application/json", body=body)

    routes = {**a_bar(isolated_home), "**/api/themes/mine": refused}
    with browser_page(app_url, "design", routes=routes, allow=("/api/themes/mine",)) as page:
        cases = [
            ("[WinError 32] The process cannot access the file: 'theme.json'", 409),
            ("OSError: [Errno 28] No space left on device", 500),
            ("KeyError: 'palette'", 500),
            ("the theme has no widgets", 400),
            ("", 400),
        ]
        said = page.evaluate(
            """async (cases) => {
              const { readable } = await import('/app/core/api.js');
              return cases.map(([text, status]) => readable(text, status));
            }""",
            cases,
        )
        assert said == [
            "Libre Panel may not change theme.json: it is read-only, "
            "or another program has it open.",
            "The disk is full: nothing could be saved.",
            "Something went wrong in Libre Panel: 'palette'",
            "the theme has no widgets",  # a sentence of the server's own stays as it is
            "Something went wrong.",
        ]
        js(page, "window.libre.doc.edit('t', (theme) => { theme.name = 'Mine'; })")
        page.keyboard.press("Control+s")  # Lumen is built in: saved under a name of one's own
        page.locator(".dialog input").fill("mine")
        page.locator(".dialog button[type='submit']").click()
        expect(page.locator(".toast.error")).to_have_text(
            "Libre Panel may not change C:\\Panel\\themes\\mine\\theme.json: it is read-only, "
            "or another program has it open."
        )
        assert js(page, "window.libre.store.get().dirty") is True  # nothing is lost


def test_unsaved_changes_can_be_saved_before_another_theme_opens(app_url, isolated_home):  # noqa: F811
    """Opening another theme with unsaved changes offers to save them first (it was Discard or
    Cancel, and the work had to be saved by hand and the other theme opened again)."""
    from libre_panel.theme.model import load_theme

    routes = a_bar(isolated_home)
    with browser_page(app_url, "design", routes=routes, allow=PICTURE) as page:
        js(page, "window.libre.doc.edit('t', (theme) => { theme.name = 'My Lumen'; })")
        palette(page, "open theme encore")
        page.keyboard.press("Enter")
        dialog = page.locator(".dialog")
        expect(dialog).to_contain_text("The changes to “My Lumen” since it was last saved are lost")
        save = dialog.get_by_role("button", name="Save first")
        expect(save).to_be_focused()  # Enter keeps the work
        save.click()
        dialog.locator("input").fill("my-lumen")  # Lumen is built in: it asks for a name
        dialog.locator("button[type='submit']").click()
        page.wait_for_function("window.libre.store.get().doc.id === 'encore'")
        assert load_theme(isolated_home / "themes" / "my-lumen").name == "My Lumen"
        assert js(page, "window.libre.store.get().dirty") is False
        # Cancel in the name's dialog: nothing is saved, and the theme stays open
        js(page, "window.libre.doc.edit('t', (theme) => { theme.name = 'My Encore'; })")
        palette(page, "open theme arena")
        page.keyboard.press("Enter")
        dialog.get_by_role("button", name="Save first").click()
        dialog.get_by_role("button", name="Cancel").click()
        expect(dialog).to_have_count(0)
        assert js(page, "window.libre.store.get().doc.id") == "encore"
        assert js(page, "window.libre.doc.theme().name") == "My Encore"


# -- the keyboard and screen readers ---------------------------------------------------------------

NAMELESS = """(root) => [...document.querySelectorAll(`${root} :is(button, a[href], input, select,
    [role=switch], [role=option], [role=menuitem])`)]
  .filter((el) => el.offsetParent !== null)
  .filter((el) => !(el.getAttribute('aria-label') || (el.labels?.[0]?.textContent) || el.innerText
    || '').trim())
  .map((el) => el.outerHTML.slice(0, 120))"""

FOCUSED = """() => {
  const el = document.activeElement;
  const style = getComputedStyle(el);
  return { text: (el.getAttribute('aria-label') || el.innerText || '').trim(),
           ring: style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 2 };
}"""


def test_the_shared_parts_have_names_rings_and_escape(app_url, isolated_home):  # noqa: F811
    """Every button of the header, the chip's popover, the theme menu, the palette and the help
    has a name a screen reader says (the Settings button was an icon with only a tooltip where
    the header is narrow); Tab goes through the header in the order it is seen, each with a
    ring; Escape closes what floats and gives the focus back to what opened it."""
    routes = a_bar(isolated_home)
    with browser_page(app_url, "design", routes=routes, allow=PICTURE) as page:
        assert page.evaluate(NAMELESS, ".topbar") == []
        expect(page.locator(".settings-btn")).to_have_attribute("aria-label", "Settings")
        page.reload()
        page.wait_for_selector(".panel-canvas .preview")
        seen = []
        for _ in range(11):
            page.keyboard.press("Tab")
            focused = page.evaluate(FOCUSED)
            assert focused["ring"], focused
            seen.append(focused["text"].split("\n")[0])
        assert seen[:8] == ["Libre Panel: start", "Lumen", "Start", "Design", "Look", "Live",
                            "Player", "Search or command …"]  # fmt: skip
        assert seen[-1] == "Settings"
        for opener, floating, root in (
            (".panel-chip", ".popover", ".popover"),
            (".theme-name", ".popover", ".popover"),
            (".search-trigger", ".palette", ".palette"),
        ):
            page.locator(opener).focus()
            page.keyboard.press("Enter")
            expect(page.locator(floating)).to_be_visible()
            assert page.evaluate(NAMELESS, root) == [], opener
            page.keyboard.press("Escape")
            expect(page.locator(floating)).to_have_count(0)
            expect(page.locator(opener)).to_be_focused()
        page.locator(".settings-btn").focus()
        page.keyboard.press("?")
        expect(page.locator(".dialog")).to_be_visible()
        assert page.evaluate(NAMELESS, ".dialog") == []
        page.keyboard.press("Escape")
        expect(page.locator(".dialog")).to_have_count(0)
        expect(page.locator(".settings-btn")).to_be_focused()
        js(page, "window.libre.doc.edit('t', (theme) => { theme.name = 'Changed'; })")
        expect(page.locator(".unsaved")).to_have_attribute(
            "aria-label", "Not saved: save now (Ctrl S)"
        )


def test_a_low_window_keeps_the_popover_below_and_the_help_from_its_top(app_url, isolated_home):  # noqa: F811
    """At 1280 × 800 the panel chip's popover, taller than the room below the chip, covered the
    chip and the header; it stays below and scrolls. The help, taller than the window there,
    opened scrolled to its end (its Close button has the focus): it shows its top."""
    from libre_panel import i18n

    routes = a_bar(isolated_home)
    i18n.set_language("de")
    with browser_page(app_url, "design", routes=routes, allow=PICTURE, size=(1280, 800)) as page:
        chip = page.locator(".panel-chip")
        chip.click()
        popover = page.locator(".popover")
        expect(popover).to_be_visible()
        below = chip.bounding_box()["y"] + chip.bounding_box()["height"]
        box = popover.bounding_box()
        assert box["y"] >= below and box["y"] + box["height"] <= 800
        assert js(page, "document.querySelector('.popover').scrollHeight") > box["height"]
        page.keyboard.press("Escape")
        page.keyboard.press("?")
        dialog = page.locator(".dialog")
        expect(dialog.get_by_role("button", name="Schließen")).to_be_focused()
        assert js(page, "document.querySelector('.dialog').scrollTop") == 0
        expect(dialog.locator(".dialog-title")).to_be_in_viewport()
        page.keyboard.press("Escape")
        palette(page, "")  # the views' hints are read whole, not cut after a few words
        cut = """[...document.querySelectorAll('.palette-hint')]
            .filter((el) => el.scrollWidth > el.clientWidth + 1).map((el) => el.textContent)"""
        assert js(page, cut) == []
        ends = """[...document.querySelectorAll('.palette-item')]
            .filter((item) => item.querySelector('.kbd'))
            .map((item) => item.getBoundingClientRect().right
              - item.querySelector('.kbd').getBoundingClientRect().right)"""
        assert all(gap < 20 for gap in js(page, ends))  # the keys end the row, hint or none
