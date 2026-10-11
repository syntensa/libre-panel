"""The app (Design, the command palette) in a real browser; skipped when Playwright or
Chromium are missing. CI runs it with the other browser tests (the desktop job)."""

import contextlib
import threading

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402

from libre_panel.editor.server import make_server  # noqa: E402


@pytest.fixture
def app_url():
    srv = make_server(port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()
    srv.server_close()
    srv.editor_state.close()


def expected_404(message, allow=()):
    """The app asks for the hardware's names; a server without them (an older background
    app, as one test plays) answers 404, the app hides them, and Chromium logs an error. So
    is a request that a test makes fail on purpose (``allow``: the ends of their addresses)."""
    return message.text.startswith("Failed to load resource") and message.location.get(
        "url", ""
    ).endswith(("/api/hardware", *allow))


@contextlib.contextmanager
def browser_page(url, view="design", strict_csp=False, size=(1440, 900), routes=None, allow=()):
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # browser not installed
            pytest.skip(f"chromium not available: {exc}")
        # The app's CSP forbids eval (good); the tests' own probes need it, except where
        # the CSP itself is what is tested.
        context = browser.new_context(
            viewport={"width": size[0], "height": size[1]}, bypass_csp=not strict_csp
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on(
            "console",
            lambda m: (
                errors.append(m.text) if m.type == "error" and not expected_404(m, allow) else None
            ),
        )
        for pattern, handler in (routes or {}).items():  # stand-ins for the server's answers
            page.route(pattern, handler)
        page.goto(f"{url}#/{view}")
        page.wait_for_selector(".panel-canvas .preview" if view == "design" else ".view > *")
        page.wait_for_timeout(300)
        yield page
        assert errors == []
        browser.close()


@pytest.fixture
def page(app_url):
    with browser_page(app_url) as page:
        yield page


def js(page, expression):
    return page.evaluate(expression)


def widget(page, id):
    return js(page, f"window.libre.doc.widgetById({id!r})")


# -- Start, Look and Live -------------------------------------------------------------------------
# A test waits for the effect of what it did (a setting in config.toml, the store, what the app
# shows), never for "a toast exists": an earlier one may still be up.


# The picture on the panel is 404 before its first frame and while the panel is paused, which
# Start and Live show as such; Chromium logs that as an error, which these tests allow.
PICTURE = ("&format=jpeg",)


@pytest.fixture
def background(isolated_home):
    """The background app with a panel that writes a PNG file, and the address of its app."""
    from test_service import FakeRegistry, write_config

    from libre_panel.autostart import Autostart
    from libre_panel.service import BackgroundApp

    write_config(isolated_home)
    app = BackgroundApp(port=0, autostart=Autostart("win32", registry=FakeRegistry()))
    url = app.start()
    yield app, url
    app.quit()
    app.shutdown()


def choose_theme(home, theme="libre-default"):
    """A config.toml that names a theme: not a first start."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(f'theme = "{theme}"\n', encoding="utf-8")


def theme_ids(page):
    return [item["id"] for item in js(page, "window.libre.store.get().themes")]


def shown_themes(page):
    """The ids of the theme cards on show."""
    return page.locator(".theme-card").evaluate_all("cards => cards.map((c) => c.dataset.theme)")


def open_collections(page):
    """Open every collection's group: its cards are not there before."""
    for toggle in page.locator(".theme-collection-toggle[aria-expanded='false']").all():
        toggle.click()


def own_themes(page):
    """The ids of Libre Panel's own themes and yours that Start leads with: not a collection's,
    and not one kept for a page of its own (the round report)."""
    return js(
        page,
        """(() => {
          const { themes, pages } = window.libre.store.get();
          const apart = pages.pages.filter((p) => p.id !== "normal" && p.theme);
          const kept = new Set(apart.map((p) => p.theme));
          return themes.filter((t) => !t.collection && !kept.has(t.id)).map((t) => t.id);
        })()""",
    )


def a_set(theme_id):
    """The collection a theme is in, for the tests: SPUR II's screens (as the built-in themes
    of v0.5 name it) and one made-up set; Libre Panel's own themes are in none."""
    return "SPUR II" if theme_id.startswith("spur-") else "Old Set" if theme_id == "pico" else ""


def with_collections(collection=a_set):
    """/api/themes with each theme in the collection ``collection(id)`` names, as a route's
    handler: the list of a server whose themes are in sets, whatever this one has."""
    import json

    def handler(route, request):
        listed = route.fetch().json()
        for item in listed:
            item["collection"] = collection(item["id"])
        route.fulfill(status=200, content_type="application/json", body=json.dumps(listed))

    return handler


def finishes(page, kind):
    """The finish of each widget of one type."""
    pick = f"window.libre.doc.widgets().filter((w) => w.type === {kind!r}).map((w) => w.finish)"
    return js(page, pick)


def set_range(locator, value):
    """Move a slider as a hand does: input events while moving, change at the end."""
    locator.evaluate(
        """(el, value) => {
          el.value = value;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        }""",
        str(value),
    )


def a_panel_app(target='Turing 9.2", video 50 fps', hint=""):
    """What /api/app says for a connected 9.2" bar in video mode, as a route's handler: Start
    then sees the model (and what the panel could not read) without a panel on this machine."""
    import json

    stats = {
        "fps": 50.0, "target_fps": 50, "streaming": True, "render_ms": 9.8,
        "render_peak_ms": 21.0, "kbps": 248.3, "blocks_per_s": 50.0, "max_depth": 1,
        "waits": 0, "drops": 0, "usb_errors": 0, "panel_restarts": 0, "ffmpeg_restarts": 0,
        "uptime_s": 10800,
    }  # fmt: skip
    answer = {
        "available": True,
        "panel": {
            "state": "showing", "detail": "", "hint": hint, "target": target,
            "theme": "libre-default", "frames": 9, "mode": None, "stats": stats,
        },
        "brightness": 80, "upside_down": False, "autostart": False, "can_quit": True,
    }  # fmt: skip

    def handler(route, request):
        if request.method == "GET":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(answer))
        else:
            route.continue_()

    return handler


def test_start_lists_the_themes_and_switches_the_panel(app_url, isolated_home):
    from test_service import wait_for

    from libre_panel.config import load_config

    choose_theme(isolated_home)
    with browser_page(app_url, "home", allow=PICTURE) as page:
        page.wait_for_selector(".start.launcher .quick-theme")
        rows = page.locator(".quick-theme")
        ids = rows.evaluate_all("rows => rows.map((row) => row.dataset.theme)")
        assert ids[0] == "libre-default"  # the one on the panel comes first
        assert page.locator(".quick-theme.current").get_attribute("data-theme") == "libre-default"
        assert len(ids) == min(5, len(theme_ids(page))) and set(ids) <= set(theme_ids(page))
        picture = page.locator(".quick-theme img").first.get_attribute("src")
        assert picture.startswith("/api/themes/")
        # a key press on a theme puts it on the panel
        target = ids[1]
        page.locator(f'.quick-theme[data-theme="{target}"]').focus()
        page.keyboard.press("Enter")
        assert wait_for(lambda: load_config().theme == target)
        expect(page.locator(".quick-theme.current")).to_have_attribute("data-theme", target)
        # the open theme follows what is on the panel, and Design edits it
        page.wait_for_function(f"window.libre.store.get().doc?.id === '{target}'")
        page.locator(".panel-card .btn-primary").click()
        expect(page.locator(".tab[aria-current='page']")).to_have_text("Design")
        assert js(page, "window.libre.store.get().doc.id") == target


def test_start_has_a_gallery_of_every_theme(app_url, isolated_home):
    choose_theme(isolated_home)
    with browser_page(app_url, "home", allow=PICTURE) as page:
        page.locator(".start-side-head a").click()  # "All"
        page.wait_for_selector(".start.gallery .theme-card")
        open_collections(page)  # (where the themes name one)
        shown = shown_themes(page)
        assert sorted(shown) == sorted(theme_ids(page))  # whatever is installed, nothing fixed
        assert page.locator(".theme-card.current").get_attribute("data-theme") == "libre-default"
        other = next(i for i in shown if i != "libre-default")
        card = page.locator(f'.theme-card[data-theme="{other}"]')
        card.hover()
        card.locator(".theme-card-edit").click()  # opens it in Design
        expect(page.locator(".tab[aria-current='page']")).to_have_text("Design")
        assert js(page, "window.libre.store.get().doc.id") == other
        # and from Design back to Start by the brand
        page.locator(".brand").click()
        page.wait_for_selector(".start.launcher")


def test_start_begins_empty_or_with_a_file(app_url, isolated_home, tmp_path):
    import json

    from test_editor_server import theme_dict

    choose_theme(isolated_home)
    with browser_page(app_url, "home/themes", allow=PICTURE) as page:
        page.wait_for_selector(".start-actions")
        page.get_by_role("button", name="Start empty").click()
        expect(page.locator(".tab[aria-current='page']")).to_have_text("Design")
        assert js(page, "window.libre.doc.widgets().length") == 0
        assert js(page, "window.libre.store.get().doc.id") is None
        assert js(page, "window.libre.store.get().dirty") is True
        # an unsaved theme is not thrown away by a click: it asks first
        page.goto(f"{app_url}#/home/themes")
        page.wait_for_selector(".start-actions")
        theme = theme_dict()
        theme["name"] = "From a file"
        path = tmp_path / "from-file.json"
        path.write_text(json.dumps(theme), encoding="utf-8")
        with page.expect_file_chooser() as chooser:
            page.get_by_role("button", name="Open a theme file").click()
        chooser.value.set_files(str(path))
        page.locator(".dialog .btn-danger").click()  # discard the empty theme
        page.wait_for_function("window.libre.doc.theme()?.name === 'From a file'")
        expect(page.locator(".tab[aria-current='page']")).to_have_text("Design")


def test_a_first_start_asks_how_it_should_look(app_url, isolated_home):
    from test_service import wait_for

    from libre_panel.config import load_config

    assert not (isolated_home / "config.toml").exists()
    with browser_page(app_url, "home", allow=PICTURE) as page:
        page.wait_for_selector(".start.first .theme-card")
        assert page.locator(".start-head h1").inner_text() == "How should it look?"
        assert page.locator(".panel-strip").inner_text().startswith("No panel found yet")
        # no panel known: Libre Panel's own themes (and yours) are on offer, all sizes
        assert sorted(shown_themes(page)) == sorted(own_themes(page))
        assert page.locator(".theme-card .theme-card-badge").count() == 0  # none is chosen yet
        choice = shown_themes(page)[0]
        page.locator(f'.theme-card[data-theme="{choice}"] .theme-card-main').click()
        expect(page.locator(".tab[aria-current='page']")).to_have_text("Design")  # on to Design
        assert wait_for(lambda: load_config().theme == choice)
        assert js(page, "window.libre.store.get().doc.id") == choice
        page.goto(f"{app_url}#/home")
        page.wait_for_selector(".start.launcher")  # a theme is chosen: the launcher


def test_a_first_start_offers_the_themes_for_the_panel_it_found(app_url, isolated_home):
    with browser_page(app_url, "home", routes={"**/api/app": a_panel_app()}, allow=PICTURE) as page:
        page.wait_for_selector(".start.first .theme-card")
        strip = page.locator(".panel-strip")
        expect(strip).to_contain_text("Your panel is here: Turing 9.2″")
        expect(strip).to_contain_text("1920 × 480")
        themes = js(page, "window.libre.store.get().themes")
        own = set(own_themes(page))
        fits = {i["id"] for i in themes if i["id"] in own and i["size"] == [1920, 480]}
        assert fits and set(shown_themes(page)) == fits and len(fits) < len(themes)
        # the way to the others, and back
        expect(page.locator(".start-actions")).to_contain_text(f"{len(themes) - len(fits)}")
        page.get_by_role("button", name="See all").click()
        loose = [i for i in themes if not i.get("collection")]  # a collection is a closed group
        expect(page.locator(".theme-card")).to_have_count(len(loose))
        page.get_by_role("button", name="Show fewer").click()
        expect(page.locator(".theme-card")).to_have_count(len(fits))
        # another panel can be named
        page.get_by_role("button", name="Another panel").click()
        page.locator(".popover .model-row", has_text="Turing 3.5").first.click()
        expect(strip).to_contain_text("Themes for Turing 3.5″")
        small = {
            i["id"] for i in themes if i["id"] in own and i["size"] in ([480, 320], [320, 480])
        }
        expect(page.locator(".theme-card")).to_have_count(len(small))
        assert set(shown_themes(page)) == small


def test_a_collection_waits_closed_at_the_end_of_the_gallery(app_url, isolated_home):
    """Libre Panel's own themes and yours come first; each collection the themes name is a
    group of its own (A to Z) that opens on a click or a key. Nothing names SPUR: a second
    collection is a group as well."""
    choose_theme(isolated_home)
    with browser_page(
        app_url, "home/themes", routes={"**/api/themes": with_collections()}, allow=PICTURE
    ) as page:
        page.wait_for_selector(".start.gallery .theme-card")
        ids = [i["id"] for i in js(page, "window.libre.store.get().themes")]
        spur = sorted(i for i in ids if i.startswith("spur-"))
        loose = [i for i in ids if i != "pico" and i not in spur]
        assert sorted(shown_themes(page)) == sorted(loose)  # the collections' cards are not there
        groups = page.locator(".theme-collection")
        assert groups.locator(".theme-collection-title strong").all_inner_texts() == [
            "Old Set · Legacy",
            "SPUR II · Legacy",
        ]
        assert groups.locator(".theme-collection-count").all_inner_texts() == ["1", str(len(spur))]
        note = "The screens of the project Libre Panel grew out of"
        expect(groups.first.locator(".theme-collection-note")).to_have_text(note)
        # the groups follow the grid in the page
        assert page.locator(".theme-gallery > :last-child").evaluate(
            "e => e.matches('.theme-collection')"
        )
        toggle = groups.nth(1).locator(".theme-collection-toggle")
        assert toggle.get_attribute("aria-expanded") == "false"
        toggle.focus()
        page.keyboard.press("Enter")  # opens it by a key
        expect(toggle).to_have_attribute("aria-expanded", "true")
        body = groups.nth(1).locator(".theme-collection-body")
        cards = body.locator(".theme-card").evaluate_all("c => c.map((e) => e.dataset.theme)")
        assert sorted(cards) == spur
        assert toggle.get_attribute("aria-controls") == body.get_attribute("id")
        page.keyboard.press("Space")  # and closes it again
        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(page.locator(".theme-card[data-theme='spur-ii']")).to_have_count(0)
        # a card of a collection is chosen like any other
        toggle.click()
        page.locator(".theme-card[data-theme='spur-ii'] .theme-card-main").click()
        page.wait_for_function("window.libre.store.get().pages.pages[0].theme === 'spur-ii'")
        expect(page.locator(".theme-card.current")).to_have_attribute("data-theme", "spur-ii")


def test_a_collection_does_not_lead_a_first_start_or_the_quick_list(app_url, isolated_home):
    routes = {"**/api/themes": with_collections(), "**/api/app": a_panel_app()}
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        page.wait_for_selector(".start.first .theme-card")
        assert page.locator(".theme-collection").count() == 0  # nothing of a collection is offered
        assert set(shown_themes(page)) == {"studio"}  # made for the 9.2" panel: Libre Panel's own
        page.get_by_role("button", name="See all").click()
        expect(page.locator(".theme-collection")).to_have_count(2)  # closed, at the end
        assert page.locator(".theme-card[data-theme='spur-ii']").count() == 0
        page.get_by_role("button", name="Show fewer").click()
        expect(page.locator(".theme-collection")).to_have_count(0)
    choose_theme(isolated_home, "libre-default")
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        page.wait_for_selector(".quick-theme")
        ids = page.locator(".quick-theme").evaluate_all("rows => rows.map((r) => r.dataset.theme)")
        assert "studio" in ids and not [i for i in ids if a_set(i)]
        link = page.locator(".quick-collection")  # one line for the collection made for this panel
        expect(link).to_have_count(1)
        expect(link).to_contain_text("SPUR II · Legacy")
        link.click()  # the way into its group
        page.wait_for_selector(".start.gallery")
        expect(page.locator(".theme-collection-toggle[aria-expanded='true']")).to_have_count(1)
        expect(page.locator(".theme-collection-body .theme-card").first).to_be_visible()
    # what is on the panel is shown first, whichever group it is in
    choose_theme(isolated_home, "spur-classic")
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        page.wait_for_selector(".quick-theme")
        ids = page.locator(".quick-theme").evaluate_all("rows => rows.map((r) => r.dataset.theme)")
        assert ids[0] == "spur-classic" and not [i for i in ids[1:] if a_set(i)]
        page.goto(f"{app_url}#/home/themes")
        page.wait_for_selector(".theme-collection")
        badge = page.locator(".theme-collection", has_text="SPUR II").locator(".theme-card-badge")
        expect(badge).to_have_text("On the panel")  # the group says what is in it


def test_the_launcher_shows_and_controls_the_running_panel(background):
    from test_service import wait_for

    from libre_panel.config import load_config

    app, url = background
    with browser_page(url, "home", allow=PICTURE) as page:
        page.wait_for_selector(".panel-card .state-dot.showing")
        expect(page.locator(".panel-card h2")).to_have_text("Showing “Libre Default”")
        page.wait_for_selector(".panel-picture img")  # the picture on the panel
        assert page.locator(".panel-picture img").get_attribute("src").startswith("blob:")
        page.get_by_role("button", name="Pause panel").click()
        assert wait_for(lambda: app.panel.paused)
        page.wait_for_selector(".panel-card .state-dot.paused")
        page.get_by_role("button", name="Resume panel").click()
        assert wait_for(lambda: not app.panel.paused)
        set_range(page.locator(".panel-card .slider input"), 35)
        assert wait_for(lambda: load_config().device.brightness == 35)
        page.locator(".panel-card [role='switch'][aria-label='Upside down']").click()
        assert wait_for(lambda: load_config().device.rotate == 180)


def test_the_launcher_says_what_the_panel_could_not_read(app_url, isolated_home):
    hint = "CPU temperature and power need administrator rights."
    choose_theme(isolated_home)
    with browser_page(
        app_url, "home", routes={"**/api/app": a_panel_app(hint=hint)}, allow=PICTURE
    ) as page:
        page.wait_for_selector(".hint-card")
        expect(page.locator(".hint-card")).to_contain_text(hint)
        page.get_by_role("button", name="Show details").click()
        page.wait_for_selector(".live")


def test_look_edits_the_theme_and_the_preview_follows(app_url):
    with browser_page(app_url, "look") as page:
        page.wait_for_selector(".look-side .finish-tile")
        page.wait_for_selector(".look-picture img")
        first = js(page, "window.libre.store.get().render.png")
        # a colour world: the look's colours and style in one step, undone in one
        before = js(page, "window.libre.doc.theme().palette.accent")
        page.locator(".look-tile", has_text="Neon").click()
        assert js(page, "window.libre.doc.theme().palette.accent") == "#FF4FD8"
        expect(page.locator(".look-tile[aria-checked='true']")).to_have_text("Neon")
        page.wait_for_function(f"window.libre.store.get().render.png !== {first!r}")
        page.keyboard.press("Control+z")
        assert js(page, "window.libre.doc.theme().palette.accent") == before
        # the finish of everything it can draw, then of the bars alone
        page.locator(".finish-tile", has_text="Light").click()
        lit = (
            "w => ['gauge', 'bar', 'graph', 'metric'].includes(w.type) ? w.finish === 'lit' : true"
        )
        assert js(page, f"window.libre.doc.widgets().every({lit})")
        # the clock cannot be lit, which does not keep the tile from showing what is set
        expect(page.locator(".finish-tile[aria-checked='true']")).to_have_text("Light")
        page.locator(".look-scope button", has_text="Bars").click()
        page.locator(".finish-tile", has_text="Facet").click()
        assert set(finishes(page, "bar")) == {"facet"}
        assert js(page, "window.libre.doc.widgetById('cpu-ring').finish") == "lit"
        expect(page.locator(".finish-tile[aria-checked='true']")).to_have_text("Facet")
        # glow of the bars: every one of them
        set_range(page.locator(".look-slider input").first, 0.6)
        glows = js(
            page, "window.libre.doc.widgets().filter((w) => w.type === 'bar').map((w) => w.glow)"
        )
        assert glows and set(glows) == {0.6}
        assert js(page, "window.libre.doc.widgetById('cpu-ring').glow") != 0.6
        # one step undoes the glow, not the finish
        page.keyboard.press("Control+z")
        assert js(page, "window.libre.doc.widgetById('cpu-bar')?.glow ?? 0") != 0.6
        assert set(finishes(page, "bar")) == {"facet"}


def test_look_blocks_are_on_or_off_and_keep_their_limits(app_url):
    with browser_page(app_url, "look") as page:
        page.wait_for_selector(".look-side .finish-tile")
        assert js(page, "Object.keys(window.libre.doc.theme().look ?? {}).length") == 0
        light = page.locator(".look-block", has_text="Panel light")
        light.locator("[role='switch']").first.click()
        settings = js(page, "window.libre.doc.theme().look.light")
        assert settings["strength"] == 1 and settings["on_hour"] is True and settings["sun"] is True
        light.locator(".look-chip", has_text="Full hour").click()
        assert js(page, "window.libre.doc.theme().look.light.on_hour") is False
        slider = light.locator(".look-slider input").nth(1)  # how long a sweep takes
        assert (slider.get_attribute("min"), slider.get_attribute("max")) == ("0.3", "10")
        set_range(slider, 4.5)
        assert js(page, "window.libre.doc.theme().look.light.sweep_seconds") == 4.5
        # the grid, the dust and its areas
        page.locator(".look-block", has_text="Grid background").locator(
            "[role='switch']"
        ).first.click()
        assert js(page, "window.libre.doc.theme().look.grid.spacing") == 8
        dust = page.locator(".look-block", has_text="Depth dust")
        dust.locator("[role='switch']").first.click()
        dust.locator("summary").click()
        dust.get_by_role("button", name="+ area").click()
        area = js(page, "window.libre.doc.theme().look.dust.areas")
        assert len(area) == 1 and area[0]["dust"] is False and area[0]["w"] > 0
        dust.locator(".dust-area select").select_option("yes")
        assert js(page, "window.libre.doc.theme().look.dust.areas[0].dust") is True
        dust.locator("input[aria-label='dust colours']").fill("#ff8800, @accent")
        dust.locator("input[aria-label='dust colours']").press("Enter")
        assert js(page, "window.libre.doc.theme().look.dust.colors") == ["#ff8800", "@accent"]
        # every block off again: the theme has no look at all
        for name in ("Panel light", "Grid background", "Depth dust"):
            block = page.locator(".look-block", has_text=name)
            block.locator("[role='switch']").first.click()
        assert js(page, "Object.keys(window.libre.doc.theme().look ?? {}).length") == 0
        # the server drew all of it: nothing it refused
        page.wait_for_function("window.libre.store.get().renderError === null")


def test_look_notices_and_ridge_frames(app_url):
    with browser_page(app_url, "look") as page:
        page.wait_for_selector(".look-notices")
        page.locator(".look-notice", has_text="Band").click()
        assert js(page, "window.libre.doc.theme().toast.style") == "band"
        page.locator(".look-notice", has_text="Card").click()
        assert not js(page, "window.libre.doc.theme().toast.style")
        # ridge frames are not offered; a theme that has some can drop them under Advanced
        assert page.locator(".look-advanced").count() == 0
        frame = "{type: 'rect', id: 'frame', x: 4, y: 4, w: 60, h: 40, ridge: 'main'}"
        js(page, f"window.libre.doc.edit(null, (theme) => theme.widgets.push({frame}))")
        page.locator(".look-advanced summary").click()
        page.get_by_role("button", name="Make the frames flat").click()
        assert js(page, "window.libre.doc.widgetById('frame').ridge") == ""
        expect(page.locator(".look-advanced")).to_have_count(0)


def test_look_shows_a_test_notice_on_the_preview(app_url):
    with browser_page(app_url, "look") as page:
        page.wait_for_selector(".look-picture img")
        plain = page.locator(".look-picture img").get_attribute("src")
        page.get_by_role("button", name="Show a notice").click()
        page.locator(".popover .menu-item", has_text="Volume").click()
        page.wait_for_function(f"document.querySelector('.look-picture img').src !== {plain!r}")
        # a change of the theme shows its preview again
        js(page, "window.libre.doc.edit('name', (theme) => { theme.name = 'Other'; })")
        shown = "document.querySelector('.look-picture img').src"
        page.wait_for_function(f"{shown} === window.libre.store.get().render.png")


def test_live_shows_the_panel_and_runs_its_services(background, isolated_home):
    from test_service import wait_for

    from libre_panel.config import load_config, log_file

    app, url = background
    log_file().parent.mkdir(parents=True, exist_ok=True)
    log_file().write_text(
        "2026-10-11 09:12:04,100 INFO libre_panel.app: showing theme 'studio'\n"
        "2026-10-11 09:14:31,000 WARNING libre_panel.x: a message to look at\n",
        encoding="utf-8",
    )
    with browser_page(url, "live", allow=PICTURE) as page:
        page.wait_for_selector(".live-tile")
        # the numbers come once the panel loop has run a second
        page.wait_for_function(
            "document.querySelector('[data-tile=fps] b').textContent.trim() !== '–'", timeout=20000
        )
        expect(page.locator("[data-tile=fps] small")).to_have_text("target 5")
        expect(page.locator("[data-tile=faults] b")).to_have_text("0")
        expect(page.locator("[data-tile=queue] small")).to_have_text("video mode only")
        page.wait_for_selector(".live-picture img")  # the picture on the panel
        # the sources the panel reads, and the services with their switches
        page.wait_for_selector("[data-source=psutil]")
        page.wait_for_selector("[data-service=notices]")
        services = page.locator("[data-service]").evaluate_all(
            "rows => rows.map((r) => r.dataset.service)"
        )
        assert {"autopilot", "game", "notices"} <= set(services)
        switch = page.locator("[data-service=notices] [role=switch]")
        assert switch.get_attribute("aria-checked") == "false"
        switch.click()
        assert wait_for(lambda: "notices" in load_config().services.enabled)
        expect(switch).to_have_attribute("aria-checked", "true")
        # a notice to test, and what became of it
        page.locator("[data-kind=volume]").click()
        expect(page.locator(".live-result")).to_contain_text("Volume:")
        # the log's end, the warning marked
        log = page.locator(".live-log")
        expect(log).to_contain_text("09:12:04  showing theme 'studio'")
        expect(log.locator(".warn")).to_have_text("09:14:31  a message to look at")
        # pause and brightness are in the header
        page.locator(".live-pause").click()
        assert wait_for(lambda: app.panel.paused)
        expect(page.locator(".live-picture-note")).to_contain_text("paused")
        set_range(page.locator(".topbar .slider input"), 30)
        assert wait_for(lambda: load_config().device.brightness == 30)


def test_live_without_the_panel_says_so_and_keeps_the_switches(app_url, isolated_home):
    from test_service import wait_for

    from libre_panel.config import load_config

    with browser_page(app_url, "live", allow=PICTURE) as page:
        page.wait_for_selector(".live-picture-note")
        assert "does not run the panel" in page.locator(".live-picture-note").inner_text()
        assert page.locator(".live-tile b").all_inner_texts() == ["–"] * 5
        page.wait_for_selector(".live-chip")
        expect(page.locator(".live-chip").first).to_be_disabled()
        assert page.locator(".live-pause").count() == 0  # nothing to pause
        page.wait_for_selector("[data-service=game]")
        page.locator("[data-service=game] [role=switch]").click()  # config.toml, as ever
        assert wait_for(lambda: "game" in load_config().services.enabled)


def test_the_new_views_speak_german(app_url, isolated_home):
    from libre_panel import i18n

    choose_theme(isolated_home)
    i18n.set_language("de")
    sets = {"**/api/themes": with_collections()}
    with browser_page(app_url, "home", routes=sets, allow=PICTURE) as page:
        page.wait_for_selector(".start-tile")
        assert page.locator(".start-tile strong").all_inner_texts() == [
            "Gestalten",
            "Look",
            "Live",
            "Einstellungen",
        ]
        assert page.locator(".panel-card .btn-primary").inner_text() == "Gestalten"
        page.goto(f"{app_url}#/home/themes")
        page.wait_for_selector(".theme-collection")
        group = page.locator(".theme-collection").last
        assert group.locator("strong").inner_text() == "SPUR II · Legacy"
        german = "Die Bildschirme des Projekts, aus dem Libre Panel entstand"
        assert group.locator(".theme-collection-note").inner_text() == german
        page.goto(f"{app_url}#/look")
        page.wait_for_selector(".look-side")
        titles = page.locator(".look-side .section-title").all_text_contents()
        assert titles[:3] == ["Farbwelt", "Oberfläche und Schein", "Panel-Licht"]
        page.goto(f"{app_url}#/live")
        page.wait_for_selector(".live-side")
        assert page.locator(".live-side .section-title").all_text_contents() == [
            "Quellen",
            "Dienste",
            "Hinweis testen",
        ]
        assert page.locator(".live-head h2").inner_text() == "Auf dem Panel, jetzt"


def test_start_look_and_live_have_their_commands(app_url, isolated_home):
    from test_service import wait_for

    from libre_panel.config import load_config

    choose_theme(isolated_home)
    with browser_page(app_url, "look") as page:
        page.wait_for_selector(".look-side")
        page.keyboard.press("Control+k")
        page.locator(".palette input").fill("panel light")
        expect(page.locator(".palette-item.active")).to_contain_text("Panel light: on")
        page.keyboard.press("Enter")
        page.wait_for_function("window.libre.doc.theme().look?.light !== undefined")
        page.keyboard.press("Control+k")
        page.locator(".palette input").fill("finish for everything facet")
        page.keyboard.press("Enter")
        page.wait_for_function("window.libre.doc.widgetById('cpu-ring').finish === 'facet'")
        page.keyboard.press("Control+k")
        page.locator(".palette input").fill("slate on the panel")
        expect(page.locator(".palette-item.active")).to_contain_text("Show Slate on the panel")
        page.keyboard.press("Enter")
        assert wait_for(lambda: load_config().theme == "slate")


def test_the_app_opens_without_breaking_its_rules(app_url):
    """The shell, the design view and the preview, under the app's own CSP."""
    with browser_page(app_url, strict_csp=True) as page:
        assert page.locator(".tab[aria-current='page']").inner_text() == "Design"
        assert page.locator(".theme-name").inner_text() == "Libre Default"
        assert page.locator(".tile").count() > 30  # modules, building blocks, widgets
        assert js(page, "window.libre.store.get().render.size") == [480, 320]
        for view, root in (("home", ".start"), ("look", ".look"), ("live", ".live")):
            page.goto(f"{app_url}#/{view}")
            page.wait_for_selector(f".view {root}")
        page.goto(f"{app_url}#/settings/about")
        current = page.locator(".settings-nav [aria-current='page']")
        assert current.inner_text() == "About Libre Panel"


def test_select_move_undo_redo(page):
    box = page.locator('.overlay .box[data-id="cpu-ring"]').bounding_box()
    start = (widget(page, "cpu-ring")["x"], widget(page, "cpu-ring")["y"])
    # from the ring's lower part: the toolbar sits above a selection
    page.mouse.move(box["x"] + 10, box["y"] + box["height"] - 10)
    page.mouse.down()
    page.mouse.move(box["x"] + 60, box["y"] + box["height"] + 20, steps=10)
    page.mouse.up()
    moved = (widget(page, "cpu-ring")["x"], widget(page, "cpu-ring")["y"])
    assert moved != start
    assert js(page, "window.libre.store.get().selection") == ["cpu-ring"]
    assert page.locator(".floating-toolbar").is_visible()
    assert page.locator(".unsaved").is_visible()
    assert page.title() == "● Libre Default – Libre Panel"  # unsaved, in the tab or window
    page.keyboard.press("Control+z")
    assert (widget(page, "cpu-ring")["x"], widget(page, "cpu-ring")["y"]) == start
    page.locator(".topbar [aria-label^='Redo']").click()
    assert (widget(page, "cpu-ring")["x"], widget(page, "cpu-ring")["y"]) == moved
    page.keyboard.press("ArrowRight")  # a nudge
    assert widget(page, "cpu-ring")["x"] == moved[0] + 1


def test_toolbar_sensor_and_finish(page):
    js(page, "window.libre.doc.setSelection(['cpu-ring'])")
    toolbar = page.locator(".floating-toolbar")
    toolbar.locator(".tool-text", has_text="Light").click()
    assert widget(page, "cpu-ring")["finish"] == "lit"
    toolbar.locator(".tool-sensor").click()
    picker = page.locator(".sensor-picker")
    picker.locator("input").fill("gpu load")
    picker.locator(".sensor-row[data-key='gpu.load']").click()
    assert widget(page, "cpu-ring")["sensor"] == "gpu.load"
    assert page.locator(".sensor-picker").count() == 0  # it closed
    # the inspector follows: the sensor and the tile of the finish
    inspector = page.locator(".inspector")
    assert "GPU load" in inspector.locator(".sensor-button").inner_text()
    assert inspector.locator(".finish-tile[aria-checked='true']").inner_text() == "Light"
    page.keyboard.press("Control+z")
    assert widget(page, "cpu-ring")["sensor"] == "cpu.load"
    # only the finishes that show on a type: acrylic is for lettering
    assert "Acrylic" not in toolbar.locator(".tool-text").all_inner_texts()
    js(page, "window.libre.doc.setSelection(['cpu-label'])")
    toolbar.locator(".tool-text", has_text="Acrylic").click()
    assert widget(page, "cpu-label")["finish"] == "acrylic"


def test_a_module_from_the_library(page):
    count = js(page, "window.libre.doc.widgets().length")
    tile = page.locator('.tile[data-item="module:ring"]').bounding_box()
    canvas = page.locator(".panel-canvas").bounding_box()
    page.mouse.move(tile["x"] + 30, tile["y"] + 30)
    page.mouse.down()
    page.mouse.move(tile["x"] + 70, tile["y"] + 50, steps=4)
    page.mouse.move(
        canvas["x"] + canvas["width"] * 0.8, canvas["y"] + canvas["height"] * 0.7, steps=10
    )
    assert page.locator(".drop").count() == 1  # where it would go, on the cells
    page.mouse.up()
    assert js(page, "window.libre.doc.widgets().length") == count + 1
    added = js(page, "window.libre.doc.single()")
    assert added["type"] == "module" and added["module"] == "ring"
    assert page.locator(".inspector .inspector-title").inner_text() == "Ring"
    # a click adds one at the first free cells
    page.locator('.tile[data-item="module:clock"]').click()
    assert js(page, "window.libre.doc.single().module") == "clock"


def test_the_command_palette_finds_and_runs(page):
    count = js(page, "window.libre.doc.widgets().length")
    page.keyboard.press("Control+k")
    palette = page.locator(".palette")
    palette.locator("input").fill("insert big num")
    first = palette.locator(".palette-item.active")
    assert "Big number" in first.inner_text() and first.locator("b").count() > 0
    page.keyboard.press("Enter")
    assert page.locator(".palette").count() == 0
    page.wait_for_function(f"window.libre.doc.widgets().length === {count + 1}")  # it ran
    assert js(page, "window.libre.doc.single().module") == "stat"
    # by keyboard, with the language: everything is reachable from here
    page.keyboard.press("Control+k")
    palette.locator("input").fill("deutsch")
    page.keyboard.press("Enter")
    page.wait_for_function("document.documentElement.lang === 'de'")
    assert page.locator(".tab[aria-current='page']").inner_text() == "Gestalten"
    page.keyboard.press("Control+k")
    palette.locator("input").fill("lüf")  # an umlaut, as typed in German
    assert palette.locator(".palette-item").count() > 0
    palette.locator("input").fill("spur-classic")  # a theme by its id, shown by its name
    assert "SPUR Classic" in palette.locator(".palette-item.active").inner_text()


def test_save_as_and_to_the_panel(page, isolated_home):
    from test_service import wait_for

    from libre_panel.config import load_config
    from libre_panel.theme.model import load_theme

    js(page, "window.libre.doc.edit('t', (theme) => { theme.name = 'Mine'; })")
    page.keyboard.press("Control+s")  # a built-in theme: saved under a new name
    dialog = page.locator(".dialog")
    dialog.locator("input").fill("bad name!")
    assert dialog.locator("button[type='submit']").is_disabled()
    dialog.locator("input").fill("mine")
    dialog.locator("button[type='submit']").click()
    page.wait_for_function("!window.libre.store.get().dirty")
    assert load_theme(isolated_home / "themes" / "mine").name == "Mine"
    assert page.locator(".unsaved").count() == 0
    page.locator(".btn-apply").click()
    # (the save's success toast may still be up: wait for the setting, not for a toast)
    assert wait_for(lambda: load_config().theme == "mine")
    page.wait_for_function("document.querySelector('.toast.success') !== null")


def test_inspector_details_and_theme_settings(page):
    js(page, "window.libre.doc.setSelection(['cpu-ring'])")
    page.locator(".details-button").click()
    x = page.locator(".inspector .quad .number-field input").first
    x.fill("33")
    x.press("Enter")
    assert widget(page, "cpu-ring")["x"] == 33
    page.locator(".inspector [aria-label='Back']").click()
    # an area on the panel (where a bar's lit glow stays): x, y, w, h, or none
    js(page, "window.libre.doc.setSelection(['disk-bar'])")
    page.locator(".details-button").click()
    page.locator(".inspector button", has_text="Set an area").click()
    area = widget(page, "disk-bar")["glow_area"]
    assert len(area) == 4 and area[2] > 0 and area[3] > 0
    page.locator(".inspector button", has_text="Remove the area").click()
    assert widget(page, "disk-bar")["glow_area"] is None
    page.locator(".inspector [aria-label='Back']").click()
    page.keyboard.press("Escape")  # nothing selected: the theme's settings
    assert page.locator(".inspector .inspector-title").inner_text() == "Libre Default"
    name = page.locator(".inspector input[aria-label='Name']")
    name.fill("Renamed")
    assert page.locator(".theme-name").inner_text() == "Renamed"
    # a look: its colours and module style in one step, undone in one
    before = js(page, "window.libre.doc.theme().palette.accent")
    page.locator(".inspector .look-tile", has_text="Neon").click()
    assert js(page, "window.libre.doc.theme().palette.accent") == "#FF4FD8"
    assert js(page, "window.libre.doc.theme().style.card") == "outline"
    assert page.locator(".inspector .look-tile[aria-checked='true']").inner_text() == "Neon"
    page.keyboard.press("Control+z")
    assert js(page, "window.libre.doc.theme().palette.accent") == before


def test_pages_of_the_panel(app_url, isolated_home):
    from libre_panel.config import set_config_value

    set_config_value("modes.game.theme", "slate")
    with browser_page(app_url) as page:
        tabs = page.locator(".page-tab")
        assert [tabs.nth(i).inner_text() for i in range(tabs.count())] == ["Normal", "In game"]
        tabs.nth(1).click()
        page.wait_for_function("window.libre.store.get().doc.id === 'slate'")
        assert js(page, "window.libre.store.get().page") == "game"


def test_the_panel_chip_changes_the_panel(page):
    page.locator(".panel-chip").click()
    page.locator(".model-row", has_text="Turing 9.2").click()
    page.wait_for_function("window.libre.store.get().render.size[0] === 1920")
    assert page.locator(".hidden-strip.top").count() == 1  # the frame hides a strip
    assert "hidden by the frame" in page.locator(".canvas-size").inner_text()
    page.keyboard.press("Control+z")
    page.wait_for_function("window.libre.store.get().render.size[0] === 480")


def test_the_running_panel_in_the_chip(isolated_home):
    """With the background app: its state in the chip, its controls in the popover."""
    from test_service import FakeRegistry, wait_for, write_config

    from libre_panel.autostart import Autostart
    from libre_panel.config import load_config
    from libre_panel.service import BackgroundApp

    write_config(isolated_home)
    app = BackgroundApp(port=0, autostart=Autostart("win32", registry=FakeRegistry()))
    url = app.start()
    try:
        with browser_page(url) as page:
            chip = page.locator(".panel-chip")
            page.wait_for_function("document.querySelector('.panel-chip.showing') !== null")
            assert "Showing" in chip.inner_text()
            chip.click()
            popover = page.locator(".popover")
            popover.locator("[role='switch'][aria-label='Upside down']").click()
            assert wait_for(lambda: load_config().device.rotate == 180)
            popover.locator(".btn", has_text="Pause panel").click()
            assert wait_for(lambda: app.panel.paused)
            page.wait_for_function("document.querySelector('.panel-chip.paused') !== null")
    finally:
        app.quit()
        app.shutdown()


class HardwareNames:
    """A stand-in for the server's /api/hardware: the names, and what the app posts."""

    def __init__(self):
        self.names = {
            "cpu": {"detected": "8-Core Processor", "custom": ""},
            "gpu": {"detected": "Graphics Card", "custom": ""},
            "memory": {"detected": "", "custom": ""},
        }
        self.posted = []

    def __call__(self, route, request):
        import json

        if request.method == "POST":
            change = json.loads(request.post_data or "{}")
            self.posted.append(change)
            for part, text in change.items():
                self.names[part]["custom"] = text
        route.fulfill(status=200, content_type="application/json", body=json.dumps(self.names))


def test_hardware_names_in_settings_and_where_they_show(app_url):
    names = HardwareNames()
    with browser_page(app_url, "settings/sensors", routes={"**/api/hardware": names}) as page:
        cpu = page.locator("#hardware-cpu-row")
        assert cpu.get_attribute("placeholder") == "8-Core Processor"  # the detected name
        assert page.locator("#hardware-memory-row").get_attribute("placeholder") == "not detected"
        cpu.fill("Workstation")
        cpu.press("Enter")
        page.wait_for_function("window.libre.store.get().hardware.cpu.custom === 'Workstation'")
        page.locator(".settings-row", has_text="Processor").locator(".link-btn").click()
        page.wait_for_function("window.libre.store.get().hardware.cpu.custom === ''")
        assert names.posted == [{"cpu": "Workstation"}, {"cpu": ""}]
        # in Design: beside a widget that shows a name
        page.goto(f"{app_url}#/design")
        page.wait_for_selector(".panel-canvas .preview")
        shows_name = "{type: 'metric', id: 'gpu-name', x: 10, y: 10, sensor: 'gpu.name'}"
        js(page, f"window.libre.doc.edit(null, (theme) => theme.widgets.push({shows_name}))")
        js(page, "window.libre.doc.setSelection(['gpu-name'])")
        field = page.locator(".inspector .hardware-name input")
        assert field.get_attribute("placeholder") == "Graphics Card"
        field.fill("Mine")
        field.press("Enter")
        page.wait_for_function("window.libre.store.get().hardware.gpu.custom === 'Mine'")
        assert names.posted[-1] == {"gpu": "Mine"}
        js(page, "window.libre.doc.setSelection(['cpu-ring'])")  # shows no name
        assert page.locator(".inspector .hardware-name").count() == 0


def test_without_hardware_names_nothing_shows(app_url):
    """A server without /api/hardware: no fields, and the section says what is to come."""
    with browser_page(app_url, "settings/sensors") as page:
        page.wait_for_function("window.libre.store.get().hardware === false")
        assert page.locator(".hardware-name").count() == 0
        assert page.locator(".settings-upcoming").is_visible()
