"""The app (Design, the command palette) in a real browser; skipped when Playwright or
Chromium are missing. CI runs it with the other browser tests (the desktop job)."""

import contextlib
import re
import threading

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402

from libre_panel.editor import access  # noqa: E402
from libre_panel.editor.server import make_server  # noqa: E402


class AppUrl(str):
    """The address of an app's server and its access token, which the browser is given apart
    from the address (in its fragment) as the window gives it; tests add routes to the address."""

    token: str | None = None


@pytest.fixture(autouse=True)
def no_setup_card(monkeypatch):
    """Settings and Start show the card of the Linux setup only in the tests that bring a Linux
    PC (``linux_pc``). The others see the app as on any other system, whatever this machine is:
    one without polkit would add a note of its own to the page of the sensors."""
    monkeypatch.setattr("libre_panel.linux_setup.supported", lambda: False)


@pytest.fixture
def app_url():
    srv = make_server(port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    url = AppUrl(f"http://127.0.0.1:{srv.server_address[1]}/")
    url.token = srv.token
    yield url
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
        # the token as the window brings it; a background app wrote it down for its user
        token = getattr(url, "token", None) or access.read_token(str(url))
        page.goto(access.with_token(f"{url}#/{view}", token))
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


def default_theme_card(page):
    """The ids of the cards marked as the panel's default (a first start marks one)."""
    marked = page.locator(".theme-card:has(.theme-card-tag.default)")
    return marked.evaluate_all("cards => cards.map((c) => c.dataset.theme)")


def open_collections(page):
    """Open every collection's group: its cards are not there before."""
    for toggle in page.locator(".theme-collection-toggle[aria-expanded='false']").all():
        toggle.click()


def own_themes(page):
    """The ids of Libre Panel's own themes and yours that Start leads with: not a collection's,
    and not one kept for a page (the round report; music's or the clock's page only while it is
    on)."""
    return js(
        page,
        """(() => {
          const { themes, pages } = window.libre.store.get();
          const apart = pages.pages.filter((p) => p.id !== "normal" && p.theme && (p.on || !p.own));
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


def app_answer(target='Turing 9.2", video 50 fps', hint="", brightness=80, **numbers):
    """What /api/app says for a connected 9.2" bar in video mode (``numbers`` change the
    panel's stats: ``fps=20.0``)."""
    stats = {
        "fps": 50.0, "target_fps": 50, "streaming": True, "render_ms": 9.8,
        "render_peak_ms": 21.0, "kbps": 248.3, "blocks_per_s": 50.0, "max_depth": 1,
        "waits": 0, "drops": 0, "usb_errors": 0, "panel_restarts": 0, "ffmpeg_restarts": 0,
        "uptime_s": 10800, **numbers,
    }  # fmt: skip
    return {
        "available": True,
        "panel": {
            "state": "showing", "detail": "", "hint": hint, "target": target,
            "theme": "libre-default", "frames": 9, "mode": None, "stats": stats,
        },
        "brightness": brightness, "upside_down": False, "autostart": False, "can_quit": True,
    }  # fmt: skip


def a_panel_app(*args, **kwargs):
    """``app_answer`` as a route's handler: Start then sees the model (and what the panel could
    not read) without a panel on this machine."""
    import json

    answer = app_answer(*args, **kwargs)

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
        # the panel's own default leads, marked: Libre Default where no bar is known
        assert shown_themes(page)[0] == "libre-default"
        assert default_theme_card(page) == ["libre-default"]
        mark = page.locator(".theme-card-tag.default")
        expect(mark).to_have_text("Default")
        assert mark.get_attribute("title") == "Shown on the panel until you choose another"
        choice = shown_themes(page)[0]
        page.locator(f'.theme-card[data-theme="{choice}"] .theme-card-main').click()
        page.wait_for_selector(".start.launcher")  # on to Start's launcher: the panel, what next
        assert wait_for(lambda: load_config().theme == choice)
        page.wait_for_function(f"window.libre.store.get().doc?.id === {choice!r}")
        page.goto(f"{app_url}#/home")
        page.wait_for_selector(".start.launcher")  # a theme is chosen: the launcher


def test_a_first_start_is_asked_without_the_tabs(app_url, isolated_home):
    """ "How should it look?" stands alone: the views behind the tabs have nothing to show
    before a theme is chosen. The tabs come with the choice, and are there in the gallery,
    in Settings and wherever else the question is not."""
    assert not (isolated_home / "config.toml").exists()
    tabs = ".topbar .tabs"
    with browser_page(app_url, "home", allow=PICTURE) as page:
        page.wait_for_selector(".start.first .theme-card")
        expect(page.locator(tabs)).to_have_count(0)
        assert page.locator(".brand").is_visible() and page.locator(".settings-btn").is_visible()
        page.goto(f"{app_url}#/home/themes")  # the gallery is not the question
        page.wait_for_selector(".start.gallery .theme-card")
        expect(page.locator(tabs)).to_have_count(1)
        page.goto(f"{app_url}#/settings/panel")
        expect(page.locator(tabs)).to_have_count(1)
        page.goto(f"{app_url}#/home")
        page.wait_for_selector(".start.first .theme-card")
        expect(page.locator(tabs)).to_have_count(0)
        choice = shown_themes(page)[0]  # a theme is chosen: the launcher follows, with the tabs
        page.locator(f'.theme-card[data-theme="{choice}"] .theme-card-main').click()
        page.wait_for_selector(".start.launcher")
        expect(page.locator(".tab[aria-current='page']")).to_have_text("Start")
        expect(page.locator(tabs)).to_have_count(1)
        page.goto(f"{app_url}#/home")
        page.wait_for_selector(".start.launcher")
        expect(page.locator(tabs)).to_have_count(1)


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
        assert shown_themes(page)[0] == "lumen" and default_theme_card(page) == ["lumen"]
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
        assert shown_themes(page)[0] == "libre-default"  # the default of the panel named now
        assert default_theme_card(page) == ["libre-default"]


def test_a_first_start_leads_with_the_default_of_the_panel(app_url, isolated_home):
    """A bar is offered Lumen first and Lumen is marked as the default; every other panel is
    offered Libre Default first. The app knows a panel that is not connected from config.toml;
    a theme that is named is a choice, and nothing is marked then."""
    isolated_home.mkdir(parents=True)
    config = isolated_home / "config.toml"
    config.write_text('[device]\nmodel = "turing-9.2-usb"\n', encoding="utf-8")
    with browser_page(app_url, "home", allow=PICTURE) as page:
        page.wait_for_selector(".start.first .theme-card")
        expect(page.locator(".panel-strip")).to_contain_text("No panel found yet")  # not connected
        assert js(page, "window.libre.store.get().pages.default") == "lumen"
        assert shown_themes(page)[0] == "lumen" and default_theme_card(page) == ["lumen"]
        page.get_by_role("button", name="Choose a panel").click()
        for panel, first in (
            ("Turing 3.5", "libre-default"),
            ("Turing 8.8", "lumen"),
            ("Turing 12.3", "libre-default"),  # nothing is made for it: the default leads anyway
            ("Turing 9.2", "lumen"),
        ):
            page.locator(".popover .model-row", has_text=panel).first.click()
            expect(page.locator(".panel-strip")).to_contain_text(f"Themes for {panel}")
            assert shown_themes(page)[0] == first, panel
            assert default_theme_card(page) == [first], panel
            page.get_by_role("button", name="Another panel").click()
    # a theme in config.toml (0.4 wrote libre-default) is a choice, whatever the panel
    config.write_text(
        'theme = "libre-default"\n[device]\nmodel = "turing-9.2-usb"\n', encoding="utf-8"
    )
    with browser_page(app_url, "home/themes", allow=PICTURE) as page:
        page.wait_for_selector(".start.gallery .theme-card")
        assert default_theme_card(page) == []
        assert js(page, "window.libre.store.get().pages.default") == "lumen"
        expect(page.locator(".theme-card.current")).to_have_attribute("data-theme", "libre-default")


# -- Start: the question about the panel's standby clip ----------------------------------------
# The video mode needs the clip's name, which Libre Panel never makes up: where that is all it
# lacks, Start asks once, with the name the vendor app uses in the field. Nothing is written
# before a click, and once it is answered it is not asked again.

CLIP = "usr/data/standby.h264"


def clip_ready(monkeypatch, model="turing-9.2-usb", ffmpeg=True):
    """A panel the video mode is proven on is on the bus, and ffmpeg is there."""
    from test_editor_settings import lacking_ffmpeg, panel_on_the_bus

    panel_on_the_bus(monkeypatch, model)
    lacking_ffmpeg(monkeypatch, found=ffmpeg)


def asks_for_the_clip(page):
    """Whether the card is there, once Start has heard whether to ask (it asks the server a
    moment after it shows, so "no card" is only known then)."""
    page.wait_for_function("window.libre.store.get().clipQuestion !== null")
    page.wait_for_timeout(100)
    return page.locator(".clip-card").count() == 1


def config_file_text(home):
    path = home / "config.toml"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_a_first_start_asks_for_the_standby_clip_and_writes_it_when_told_to(
    app_url, isolated_home, monkeypatch
):
    """A first start asks how it should look first; the question about the clip follows the
    choice, on top of the launcher."""
    clip_ready(monkeypatch)
    assert not (isolated_home / "config.toml").exists()
    with browser_page(app_url, "home", allow=PICTURE) as page:
        page.wait_for_selector(".start.first .theme-card")
        assert asks_for_the_clip(page) is False  # the look first
        choice = shown_themes(page)[0]
        page.locator(f'.theme-card[data-theme="{choice}"] .theme-card-main').click()
        page.wait_for_selector(".start.launcher .clip-card")
        card = page.locator(".clip-card")
        assert card.locator("h2").inner_text() == "Turn on smooth motion"
        assert card.locator("p").inner_text().startswith("Your panel can show Libre Panel as video")
        # in plain words: the name offered is only named on the way to another one; the card
        # is the next step, right under the panel's card
        assert CLIP in card.get_by_role("button", name="The clip has another name").inner_text()
        assert page.get_by_label("Name of the standby clip").count() == 0
        assert page.locator(".panel-card + .clip-card").count() == 1
        # asking writes nothing, nor does looking at it for a while
        chosen = config_file_text(isolated_home)
        page.wait_for_timeout(400)
        assert config_file_text(isolated_home) == chosen
        assert settings_config().device.video.local_clip == ""
        assert settings_config().device.video.clip_asked is False
        page.get_by_role("button", name="Turn on", exact=True).click()
        waits_for(page, lambda: settings_config().device.video.local_clip == CLIP)
        text = config_file_text(isolated_home)
        assert f'local_clip = "{CLIP}"' in text  # exactly the name that was offered
        assert settings_config().device.video.clip_asked is True
        expect(card).to_have_count(0)  # answered
        # (a plain editor does not run the panel: it says when the video mode starts; the toast
        # of the choice may still be up)
        expect(page.locator(".toast", has_text="Smooth motion")).to_contain_text(
            "Saved. Smooth motion starts after Libre Panel is started again."
        )
        page.goto(f"{app_url}#/home")
        page.reload()
        page.wait_for_selector(".start.launcher")
        assert asks_for_the_clip(page) is False  # not again


def test_the_launcher_asks_too_and_writes_the_name_that_was_typed(
    app_url, isolated_home, monkeypatch
):
    clip_ready(monkeypatch)
    choose_theme(isolated_home)
    with browser_page(app_url, "home", allow=PICTURE) as page:
        page.wait_for_selector(".start.launcher .clip-card")
        assert page.locator(".panel-card + .clip-card").count() == 1  # under the panel's card
        page.get_by_role("button", name="The clip has another name").click()
        field = page.get_by_label("Name of the standby clip")
        use = page.get_by_role("button", name="Turn on", exact=True)
        assert field.input_value() == CLIP
        # no name, no "turn on": nothing is made up for an empty field either
        before = config_file_text(isolated_home)
        field.fill("   ")
        expect(use).to_be_disabled()
        field.press("Enter")
        page.wait_for_timeout(300)
        assert config_file_text(isolated_home) == before
        # a name of the person's own is written as typed (Enter is a click on "Turn on")
        field.fill("usr/data/other-clip.h264")
        expect(use).to_be_enabled()
        field.press("Enter")
        other = "usr/data/other-clip.h264"
        waits_for(page, lambda: settings_config().device.video.local_clip == other)
        assert settings_config().device.video.clip_asked is True
        expect(page.locator(".clip-card")).to_have_count(0)


def test_not_now_writes_no_name_and_the_question_is_not_asked_again(
    app_url, isolated_home, monkeypatch
):
    clip_ready(monkeypatch)
    choose_theme(isolated_home)
    with browser_page(app_url, "home", allow=PICTURE) as page:
        page.wait_for_selector(".start.launcher .clip-card")
        page.get_by_role("button", name="Not now").click()
        waits_for(page, lambda: settings_config().device.video.clip_asked is True)
        expect(page.locator(".clip-card")).to_have_count(0)
        # only that it was asked: no name is written, and the video mode waits for one
        assert settings_config().device.video.local_clip == ""
        assert "local_clip" not in config_file_text(isolated_home)
        assert not page.locator(".toast").count()  # nothing was saved to say
        # it does not come back: not on Start, not after a start of the app, not on the tray
        page.goto(f"{app_url}#/settings/panel")
        page.reload()
        page.goto(f"{app_url}#/home")
        page.wait_for_selector(".start.launcher")
        assert asks_for_the_clip(page) is False
        # the field in Settings is still there for when the person wants it
        page.goto(f"{app_url}#/settings/panel")
        clip = page.get_by_label("Name of the standby clip")
        assert clip.input_value() == "" and clip.get_attribute("placeholder") == CLIP
        expect(page.locator("#main .settings-row-status").first).to_have_text(
            "Off for now. Missing: the name of the standby clip."
        )


@pytest.mark.parametrize(
    ("extra", "model", "ffmpeg"),
    [
        pytest.param(f'[video]\nlocal_clip = "{CLIP}"\n', "turing-9.2-usb", True, id="clip-set"),
        pytest.param("", "turing-9.2-usb", False, id="no-ffmpeg"),
        pytest.param("", "turing-8.8-usb", True, id="not-proven"),
        pytest.param("", None, True, id="no-panel"),
        pytest.param('[video]\nmode = "off"\n', "turing-9.2-usb", True, id="mode-off"),
        pytest.param('[video]\nmode = "on"\n', "turing-9.2-usb", True, id="mode-on"),
        pytest.param("[video]\nclip_asked = true\n", "turing-9.2-usb", True, id="asked"),
        pytest.param('[device]\ndriver = "virtual"\n', "turing-9.2-usb", True, id="no-hardware"),
    ],
)
def test_start_does_not_ask_for_the_standby_clip_unless_it_is_all_that_is_missing(
    app_url, isolated_home, monkeypatch, extra, model, ffmpeg
):
    from test_editor_settings import lacking_ffmpeg, panel_on_the_bus

    panel_on_the_bus(monkeypatch, model)
    lacking_ffmpeg(monkeypatch, found=ffmpeg)
    choose_theme(isolated_home)
    with isolated_home.joinpath("config.toml").open("a", encoding="utf-8") as config:
        config.write("\n" + extra)
    with browser_page(app_url, "home", allow=PICTURE) as page:
        page.wait_for_selector(".start.launcher")
        assert asks_for_the_clip(page) is False
    (isolated_home / "config.toml").write_text(extra, encoding="utf-8")  # a first start: no theme
    with browser_page(app_url, "home", allow=PICTURE) as page:
        page.wait_for_selector(".start.first")
        assert asks_for_the_clip(page) is False


def test_the_trays_line_opens_start_at_the_question(app_url, isolated_home, monkeypatch):
    clip_ready(monkeypatch)
    choose_theme(isolated_home)
    with browser_page(app_url, "home/clip", allow=PICTURE) as page:
        page.wait_for_selector(".start.launcher .clip-card")
        expect(page.get_by_role("button", name="Turn on", exact=True)).to_be_focused()
    with browser_page(app_url, "home", allow=PICTURE) as page:  # (Start alone does not take it)
        page.wait_for_selector(".start.launcher .clip-card")
        expect(page.get_by_role("button", name="Turn on", exact=True)).not_to_be_focused()


def test_the_question_about_the_standby_clip_is_asked_in_german_too(
    app_url, isolated_home, monkeypatch
):
    from libre_panel import i18n

    clip_ready(monkeypatch)
    choose_theme(isolated_home)
    i18n.set_language("de")  # (the app's language is the server's: the fixture sets it back)
    with browser_page(app_url, "home", allow=PICTURE) as page:
        page.wait_for_selector(".start.launcher .clip-card")
        card = page.locator(".clip-card")
        assert card.locator("h2").inner_text() == "Flüssige Bewegung einschalten"
        text = card.locator("p").inner_text()
        assert text.startswith("Dein Panel kann Libre Panel als Video zeigen, mit bis zu 50")
        expect(page.get_by_role("button", name="Jetzt nicht")).to_be_visible()
        card.get_by_role("button", name="Der Clip heißt anders").click()
        assert page.get_by_label("Name des Standby-Clips").input_value() == CLIP
        page.get_by_role("button", name="Einschalten", exact=True).click()
        waits_for(page, lambda: settings_config().device.video.local_clip == CLIP)
        expect(page.locator(".toast")).to_contain_text(
            "Gespeichert. Die flüssige Bewegung beginnt, sobald Libre Panel neu gestartet wird."
        )


def test_a_running_panel_switches_by_itself_and_the_trays_line_goes(isolated_home, monkeypatch):
    """With the background app a name is taken in at once (the panel loop follows config.toml),
    so Start says so, and the line in the tray's menu is gone as soon as the question is."""
    from test_editor_settings import lacking_ffmpeg
    from test_service import FakeRegistry, wait_for

    from libre_panel.autostart import Autostart
    from libre_panel.devices import turzx_usb
    from libre_panel.devices.base import DeviceError
    from libre_panel.service import BackgroundApp

    def no_panel(pid=None):
        raise DeviceError("no panel")

    monkeypatch.setattr(turzx_usb.UsbTransport, "open", staticmethod(no_panel))
    lacking_ffmpeg(monkeypatch, found=True)
    isolated_home.mkdir(parents=True, exist_ok=True)
    (isolated_home / "config.toml").write_text(
        'theme = "libre-default"\nfps = 5\n\n[device]\nmodel = "turing-9.2-usb"\n'
        f'output = "{(isolated_home / "frame.png").as_posix()}"\n',
        encoding="utf-8",
    )
    app = BackgroundApp(port=0, autostart=Autostart("win32", registry=FakeRegistry()))
    url = app.start()
    try:
        assert app.clip_question() is True  # the tray shows its line
        with browser_page(url, "home", allow=PICTURE) as page:
            page.wait_for_selector(".start.launcher .clip-card")
            page.get_by_role("button", name="Turn on", exact=True).click()
            waits_for(page, lambda: settings_config().device.video.local_clip == CLIP)
            expect(page.locator(".toast")).to_contain_text(
                "Saved. The panel switches to smooth motion in a moment."
            )
            expect(page.locator(".clip-card")).to_have_count(0)
        assert wait_for(lambda: app.clip_question() is False)
    finally:
        app.quit()
        app.shutdown()


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
        # made for the 9.2" panel: Libre Panel's own, its default first, the others A to Z
        assert shown_themes(page) == ["lumen", "almanac", "almanac-analog", "arena", "encore",
                                      "nimbus", "studio"]  # fmt: skip
        page.get_by_role("button", name="See all").click()
        expect(page.locator(".theme-collection")).to_have_count(2)  # closed, at the end
        assert page.locator(".theme-card[data-theme='spur-ii']").count() == 0
        page.get_by_role("button", name="Show fewer").click()
        expect(page.locator(".theme-collection")).to_have_count(0)
    choose_theme(isolated_home, "libre-default")
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        page.wait_for_selector(".quick-theme")
        ids = page.locator(".quick-theme").evaluate_all("rows => rows.map((r) => r.dataset.theme)")
        assert "lumen" in ids and not [i for i in ids if a_set(i)]
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
        page.wait_for_selector(".panel-picture img[src^='blob:']")  # the picture on the panel
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


def unlock_route(answer, posted):
    """``/api/unlock`` as a Windows download answers it, ``answer`` changed by what the
    tests do; a POST is kept in ``posted`` and its run ends at once, with the service set up
    (or removed)."""
    import json

    def handler(route, request):
        if request.method == "POST":
            asked = json.loads(request.post_data)
            posted.append(asked)
            answer["job"] = {
                "action": asked["action"], "running": False, "code": 0,
                "cancelled": False, "error": "",
            }  # fmt: skip
            removed = asked["action"] == "remove"
            answer["service"] = None if removed else {"state": "running", "disabled": False}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(answer))

    return handler


def windows_download(**changes):
    return {
        "platform": "win32",
        "available": True,
        "service": None,
        "pawnio": None,
        "frame_rates": "no",
        "job": None,
        "command": "libre-panel system-setup --service --pawnio --perf-log-users",
        **changes,
    }


def test_the_launcher_offers_to_unlock_all_sensors(app_url, isolated_home):
    """Without the sensor service, Start says what it reads and sets it up after Windows' one
    question; the card goes once the service is there (and the hint with it)."""
    choose_theme(isolated_home)
    answer, posted = windows_download(), []
    routes = {
        "**/api/unlock": unlock_route(answer, posted),
        "**/api/app": a_panel_app(hint="CPU temperature, power and fans need the sensor service."),
    }
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        card = page.locator(".unlock-card")
        card.wait_for()
        expect(card).to_contain_text("keeps running without administrator rights")
        expect(page.locator(".hint-card:not(.unlock-card)")).to_have_count(0)  # one card
        card.get_by_role("button", name="Unlock all sensors").click()
        expect(page.locator(".unlock-card")).to_have_count(0)
        page.wait_for_selector(".toast.success")
    assert posted == [{"action": "unlock"}]


def test_the_launcher_does_not_offer_what_is_there_or_cannot_be(app_url, isolated_home):
    choose_theme(isolated_home)
    for answer in (
        windows_download(service={"state": "running", "disabled": False}),
        windows_download(available=False),
    ):
        routes = {"**/api/unlock": unlock_route(answer, [])}
        with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
            page.wait_for_selector(".start.launcher")
            page.wait_for_function("window.libre.store.get().unlockInfo !== null")
            expect(page.locator(".unlock-card")).to_have_count(0)


def test_the_sensor_settings_show_the_service_and_take_it_away_after_asking(app_url):
    answer = windows_download(
        service={"state": "running", "disabled": False}, pawnio="2.0.1", frame_rates="sign-in"
    )
    posted = []
    routes = {"**/api/unlock": unlock_route(answer, posted)}
    with browser_page(app_url, "settings/sensors", routes=routes) as page:
        card = page.locator("#sensor-service")
        card.wait_for()
        for text in ("Running", "2.0.1", "From your next sign-in"):
            expect(card).to_contain_text(text)
        card.get_by_role("button", name="Remove").click()
        dialog = page.locator(".btn-danger")
        expect(dialog).to_have_text("Remove")
        dialog.click()
        expect(card).to_contain_text("Not set up")
        expect(card.get_by_role("button", name="Unlock all sensors")).to_be_visible()
    assert posted == [{"action": "remove"}]


def test_the_sensor_settings_name_the_service_as_a_way_to_read(app_url):
    with browser_page(app_url, "settings/sensors") as page:
        page.wait_for_selector("#sensor-advanced")
        page.locator("#sensor-advanced .settings-disclosure").click()  # (it is under Advanced)
        if not page.locator("#sensor-lhm").count():  # (not on Windows, and not in the list)
            return
        labels = page.locator("#sensor-lhm .segmented button").all_inner_texts()
        assert labels == ["Automatic", "Sensor service", "Inside Libre Panel", "Web server"]


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


def test_live_values_are_drawn_again_only_where_the_picture_is_shown(app_url, page):
    """With live values on the preview is drawn every second; in Start, Live or Settings,
    which do not show it, nothing asks the server for it (and in Look it goes on)."""
    renders = []
    page.on("request", lambda r: renders.append(r.url) if r.url.endswith("/api/render") else None)

    def waits_for_renders(more, seconds=8):
        """Until the picture was asked for ``more`` times beyond now."""
        wanted = len(renders) + more
        for _ in range(seconds * 20):
            if len(renders) >= wanted:
                return True
            page.wait_for_timeout(50)
        return False

    js(page, "window.libre.store.set({ prefs: { ...window.libre.store.get().prefs, live: true } })")
    assert waits_for_renders(2)  # Design: again and again
    for view in ("settings/panel", "live", "home"):
        page.goto(f"{app_url}#/{view}")
        page.wait_for_selector(".view > *")
        page.wait_for_timeout(300)  # (one that was on its way)
        asked = len(renders)
        page.wait_for_timeout(1600)  # more than a turn of the loop (a second)
        assert len(renders) == asked, view
    page.goto(f"{app_url}#/look")
    assert waits_for_renders(2)


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
        # (the source of the hardware's names has a name for people like the others)
        expect(page.locator("[data-source=hardware] .live-row-name")).to_have_text("Hardware names")
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


def test_live_says_whether_the_video_mode_is_on_or_what_it_lacks(
    app_url, isolated_home, monkeypatch
):
    """The state is the server's own (``video_mode()``, as Settings has it), and "on" is said
    of a panel that is driven in video mode now."""
    import json

    from test_editor_settings import lacking_ffmpeg, panel_on_the_bus

    panel_on_the_bus(monkeypatch, "turing-9.2-usb")

    def running(streaming):
        """The console's answer for a panel that runs (``streaming``: in video mode)."""
        answer = {"background": True, "panel": app_answer(streaming=streaming)["panel"]}

        def handler(route, request):
            route.fulfill(status=200, content_type="application/json", body=json.dumps(answer))

        return {"**/api/plugins/control/panel": handler}

    def state(config="", ffmpeg=True, streaming=False, app=True):
        lacking_ffmpeg(monkeypatch, found=ffmpeg)
        choose_theme(isolated_home)
        with (isolated_home / "config.toml").open("a", encoding="utf-8") as file:
            file.write(config)
        with browser_page(
            app_url, "live", routes=running(streaming) if app else None, allow=PICTURE
        ) as page:
            page.wait_for_selector(".live-status")
            if not app:
                page.wait_for_timeout(500)
                return None, page.locator(".live-video").count()
            video = page.locator(".live-video")
            expect(video).to_be_visible()
            link = page.locator(".live-video a").all_inner_texts()
            return video.get_attribute("data-video-state"), video.inner_text().strip(), link

    missing = "Off for now. Missing: the name of the standby clip"
    tone, text, link = state(ffmpeg=False)
    assert (tone, link) == ("warn", ["Panel settings"])
    assert text.startswith(f"{missing}, the video program ffmpeg.")
    tone, text, link = state()
    assert tone == "warn" and text.startswith(f"{missing}.")
    with_clip = f'\n[video]\nlocal_clip = "{CLIP}"\n'
    tone, text, link = state(with_clip, streaming=True)
    assert (tone, text, link) == ("ok", "On, 50 fps", [])  # at the rate the panel runs at
    tone, text, link = state(with_clip, streaming=False)
    assert (tone, text) == ("off", "Starts when a panel it runs on is connected.")  # not yet
    tone, text, link = state('\n[video]\nmode = "off"\n')
    assert (tone, text) == ("off", "Off. The panel gets single pictures, about nine a second.")
    assert state(app=False) == (None, 0)  # a plain editor runs no panel: nothing to say


def test_live_without_the_panel_says_so_and_keeps_the_switches(app_url, isolated_home):
    from test_service import wait_for

    from libre_panel.config import load_config

    with browser_page(app_url, "live", allow=PICTURE) as page:
        page.wait_for_selector(".live-status")
        assert "not running in the background" in page.locator(".live-status").inner_text()
        assert page.locator(".live-tile").count() == 0  # no numbers without the panel
        page.wait_for_selector(".live-chip")
        expect(page.locator(".live-chip").first).to_be_disabled()
        assert page.locator(".live-pause").count() == 0  # nothing to pause
        page.wait_for_selector("[data-service=game]")
        page.locator("[data-service=game] [role=switch]").click()  # config.toml, as ever
        assert wait_for(lambda: "game" in load_config().services.enabled)


WATCH_BLOBS = """
  // the blob URLs that are made and not revoked yet
  window.__blobs = new Set();
  const make = URL.createObjectURL.bind(URL);
  const revoke = URL.revokeObjectURL.bind(URL);
  URL.createObjectURL = (blob) => {
    const url = make(blob);
    window.__blobs.add(url);
    return url;
  };
  URL.revokeObjectURL = (url) => {
    window.__blobs.delete(url);
    revoke(url);
  };
  // a picture's body that waits to be let go, as if it were still on its way
  window.__bodies = [];
  window.__let_go = 0;
  const body = Response.prototype.blob;
  Response.prototype.blob = function () {
    return new Promise((resolve) => {
      window.__bodies.push(() =>
        body.call(this).then((blob) => {
          resolve(blob);
          window.__let_go++;
        }),
      );
    });
  };
"""


def test_a_picture_that_arrives_after_live_was_left_is_not_kept(background):
    """The picture's body was still on its way when the view went: the URL made for it then
    was never revoked (a picture of memory kept for every such visit)."""
    app, url = background
    with browser_page(url, "home", allow=PICTURE) as page:
        page.add_init_script(WATCH_BLOBS)
        page.reload()
        page.goto(f"{url}#/live")
        page.wait_for_function("window.__bodies.length > 0")  # a picture is on its way
        page.goto(f"{url}#/settings/panel")  # Live is gone
        page.wait_for_selector(".settings-main")
        js(page, "window.__bodies.forEach((letGo) => letGo())")
        page.wait_for_function("window.__let_go === window.__bodies.length")  # they came after all
        assert js(page, "window.__blobs.size") == 0


def test_the_new_views_speak_german(app_url, isolated_home):
    from libre_panel import i18n

    choose_theme(isolated_home)
    i18n.set_language("de")
    sets = {"**/api/themes": with_collections()}
    with browser_page(app_url, "home", routes=sets, allow=PICTURE) as page:
        page.wait_for_selector(".start-more")
        assert page.locator(".start-more h2").inner_text() == "Mehr für dein Panel"
        assert page.locator(".panel-card .btn-primary").inner_text() == "Theme bearbeiten"
        page.goto(f"{app_url}#/home/themes")
        page.wait_for_selector(".theme-collection")
        group = page.locator(".theme-collection").last
        assert group.locator("strong").inner_text() == "SPUR II · Klassiker"
        german = "Die Screens des Projekts, aus dem Libre Panel entstand"
        assert group.locator(".theme-collection-note").inner_text() == german
        page.goto(f"{app_url}#/look")
        page.wait_for_selector(".look-side")
        titles = page.locator(".look-side .section-title").all_text_contents()
        assert titles[:3] == ["Farbwelt", "Oberfläche und Leuchten", "Panel-Licht"]
        page.goto(f"{app_url}#/live")
        page.wait_for_selector(".live-side")
        assert page.locator(".live-side .section-title").all_text_contents() == [
            "Sensoren",
            "Spielmodus und mehr",
            "Hinweis testen",
        ]
        assert page.locator(".live-head h2").inner_text() == "Jetzt auf dem Panel"


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


# -- the access token (editor/access.py): it comes in the address and leaves it ----------------

KEPT = "sessionStorage.getItem('libre-panel-token')"


def test_the_token_comes_in_the_address_and_leaves_it(app_url):
    """The window gives it after a ``#``; the page keeps it for its tab and takes it out of the
    address, where a browser would show it, keep it in its history and give it to a bookmark."""
    with browser_page(app_url, "look") as page:
        assert js(page, "location.hash") == "#/look"  # (it was ``#/look&token=...``)
        assert js(page, KEPT) == app_url.token
        assert js(page, "window.libre.store.get().ready") is True  # so the requests were let in
        assert js(page, "window.libre.store.get().themes.length") > 0
        # a reload has no token in its address and goes on: the tab has it
        page.reload()
        page.wait_for_function("window.libre.store.get().ready")
        assert js(page, KEPT) == app_url.token
        # a token that comes later (the window brings the new one of an app started anew)
        # replaces the one kept, and a token alone leaves the route where it was
        page.evaluate("sessionStorage.setItem('libre-panel-token', 'x'.repeat(43))")
        page.evaluate(f"location.hash = '#token={app_url.token}'")
        page.wait_for_function("location.hash === '#/look'")
        assert js(page, KEPT) == app_url.token
        # with a route it goes there; the token is out of the address either way
        page.evaluate(f"location.hash = '#/home&token={app_url.token}'")
        page.wait_for_function("location.hash === '#/home'")
        page.wait_for_selector(".view .start")
        page.evaluate(f"location.hash = '#token={app_url.token}&/live'")
        page.wait_for_function("location.hash === '#/live'")
        # and the pictures, which a header cannot be sent with, are asked for with it
        page.goto(f"{app_url}#/home/themes")
        page.wait_for_selector(".start.gallery .theme-card img")
        page.wait_for_function(
            "[...document.querySelectorAll('.theme-card img')]"
            ".every((i) => i.complete && i.naturalWidth > 0)"
        )
        assert js(page, "document.querySelector('.theme-card img').src").endswith(
            f"&token={app_url.token}"
        )


def test_a_page_opened_without_the_token_says_what_to_do(app_url):
    """A bookmark of the address of an earlier run: the page is given, nothing else is."""
    with browser_page(str(app_url), "home", allow=("/api/i18n",)) as page:
        message = page.locator(".view-message.error").inner_text()
        assert "could not start" in message and "Access token missing or wrong" in message
        assert "libre-panel editor" in message
        assert js(page, KEPT) is None and js(page, "location.hash") == "#/home"


def test_a_page_given_the_wrong_token_says_so_too(app_url):
    wrong = AppUrl(str(app_url))
    wrong.token = "w" * 43
    with browser_page(wrong, "home", allow=("/api/i18n",)) as page:
        assert "Access token missing or wrong" in page.locator(".view-message.error").inner_text()
        assert js(page, "location.hash") == "#/home"  # (it is out of the address all the same)


def test_a_plugins_page_in_the_app_has_the_token_too(app_url):
    """The page is in a frame, which shares the tab's storage: it needs no token in its address."""
    with browser_page(app_url, "plugin/control") as page:
        frame = page.frame_locator("iframe.plugin-frame")
        note = frame.locator("#background-note .note")
        note.wait_for()
        assert "does not run the panel" in note.inner_text()  # the answer of ``status``
        assert frame.locator(".note.error").count() == 0


def test_the_picture_of_a_plugins_page_is_asked_for_with_the_token(background):
    from test_service import wait_for

    app, url = background
    assert wait_for(lambda: app.panel.state()["state"] == "showing")
    assert wait_for(lambda: app.plugin_host.shown is not None)  # a picture to show
    with browser_page(url, "plugin/control") as page:
        picture = page.frame_locator("iframe.plugin-frame").locator("#live")
        picture.wait_for(state="visible")  # the page shows it when it has come
        # (it asks for the next one at once: there is a moment with none to measure)
        assert wait_for(lambda: picture.evaluate("img => img.naturalWidth") > 0)
        assert "token=" in picture.get_attribute("src")


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


def header_overflow(page):
    """The parts of the header that reach beyond the window (their classes)."""
    return js(
        page,
        """(() => [...document.querySelectorAll('.topbar > *')]
          .filter((el) => el.getBoundingClientRect().right > innerWidth + 0.5)
          .map((el) => el.className))()""",
    )


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_header_fits_a_small_window_in_either_language(background, language):
    """At 1280 px, and at the window's smallest 1100, every part of the header is in the window
    with unsaved changes, a theme name of some length and the running panel's chip: the search
    field, the name and the chip's text give way, the gear and "To the panel" stay."""
    from libre_panel import i18n

    app, url = background
    i18n.set_language(language)
    with browser_page(url, "design", size=(1280, 800), allow=PICTURE) as page:
        js(page, "window.libre.doc.edit('n', (theme) => { theme.name = 'A theme with a name'; })")
        for size in ((1280, 800), (1100, 700)):
            page.set_viewport_size({"width": size[0], "height": size[1]})
            for view in ("design", "look", "live", "settings", "home"):
                page.goto(f"{url}#/{view}")
                page.wait_for_selector(".view > *")
                assert header_overflow(page) == [], (size, view)
        # what gives way keeps its name: the unsaved mark has one without its words
        page.goto(f"{url}#/look")
        page.wait_for_selector(".look-side")
        assert page.locator(".unsaved").get_attribute("aria-label")
        assert page.locator(".settings-btn").is_visible()
        assert page.locator(".btn-apply").is_visible()


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_search_field_gives_way_before_the_name_of_the_theme(background, language):
    """Where the header is a few pixels too narrow the theme's name used to give some of them
    up, which turned "Libre Default" into "Libre Defa…" (and "Meins" into "Me…") at 1280 and
    1366 px with unsaved changes. The search field gives way, and the name stays whole."""
    from libre_panel import i18n

    app, url = background
    i18n.set_language(language)
    with browser_page(url, "design", size=(1280, 800), allow=PICTURE) as page:
        js(page, "window.libre.doc.edit('n', (theme) => { theme.description = 'changed'; })")
        name = page.locator(".theme-name span")
        expect(name).to_have_text("Libre Default")
        for size in ((1280, 800), (1366, 768), (1440, 900)):
            page.set_viewport_size({"width": size[0], "height": size[1]})
            assert header_overflow(page) == [], size
            cut = name.evaluate(  # the text against its box, in fractions of a pixel
                """(el) => {
                  const range = document.createRange();
                  range.selectNodeContents(el);
                  return range.getBoundingClientRect().width - el.getBoundingClientRect().width;
                }"""
            )
            assert cut < 0.1, (size, cut)


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


def test_undo_acts_where_the_theme_is_shown_and_not_behind_other_views(app_url, page):
    """Ctrl+Z and Ctrl+Y change the theme of Design and Look; pressed in Settings, Live or Start
    they must not undo what the person cannot see (the palette does not offer them there)."""
    js(page, "window.libre.doc.edit('n', (theme) => { theme.name = 'Changed'; })")
    name = "window.libre.doc.theme().name"
    for view in ("settings/panel", "live", "home"):
        page.goto(f"{app_url}#/{view}")
        page.wait_for_selector(".view > *")
        page.keyboard.press("Control+z")
        page.keyboard.press("Control+y")
        page.keyboard.press("Control+k")
        page.locator(".palette input").fill("undo")
        expect(page.locator(".palette-item", has_text="Undo")).to_have_count(0)
        page.keyboard.press("Escape")
        assert js(page, name) == "Changed", view  # nothing was undone behind the view
        assert js(page, "window.libre.store.get().canUndo") is True
    page.goto(f"{app_url}#/look")  # where the theme is shown, it works
    page.wait_for_selector(".look-side")
    page.keyboard.press("Control+z")
    assert js(page, name) == "Libre Default"
    page.keyboard.press("Control+y")
    assert js(page, name) == "Changed"


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


def test_the_librarys_long_names_wrap_instead_of_being_cut(page):
    """German's names are long: "Lüfter mit Drehzahl" took a second line only by being cut
    ("Lüfter mit Dreh…"), and a single word like "Monatskalender" is longer than a small tile.
    A name takes a second line, and a word that cannot fit breaks; no name loses its end."""
    js(page, "window.libre.commands.runCommand('language.de')")
    page.wait_for_function("document.documentElement.lang === 'de'")
    page.wait_for_function("document.fonts.status === 'loaded'")  # the lines are the font's
    expect(page.locator('.tile[data-item="widget:calendar"] .tile-name')).to_have_text(
        "Monats\u00adkalender"  # (a soft hyphen: where the small tile may break it)
    )
    measured = page.locator(".tile-name").evaluate_all(
        """(names) => names.map((el) => {
          const range = document.createRange();
          range.selectNodeContents(el);
          const lines = new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size;
          return [el.textContent, el.scrollWidth > el.clientWidth + 1, lines];
        })"""
    )
    assert [name for name, beyond, lines in measured if beyond or lines > 2] == []
    wrapped = {name for name, beyond, lines in measured if lines == 2}
    assert {"Lüfter mit Drehzahl", "Symbol mit Beschriftung", "Monats\u00adkalender"} <= wrapped


def test_a_long_german_name_breaks_at_its_soft_hyphen_in_the_small_tiles(page):
    """ "Wettersymbol" and "Monatskalender" are longer than a tile of the library's three columns
    and broke in the middle of a syllable ("Wettersymb-ol"); the catalog marks where a compound
    may break, and a typed word still finds the name."""
    js(page, "window.libre.commands.runCommand('language.de')")
    page.wait_for_function("document.documentElement.lang === 'de'")
    page.wait_for_function("document.fonts.status === 'loaded'")
    broken = page.locator('.tile[data-item^="widget:"] .tile-name').evaluate_all(
        """(names) => names.map((el) => {
          const node = el.firstChild;
          const range = document.createRange();
          const tops = [];
          for (let i = 0; i < node.length; i++) {
            range.setStart(node, i);
            range.setEnd(node, i + 1);
            const rects = range.getClientRects();  // (the hyphen that a break makes is a box too)
            tops.push(rects.length ? Math.round(rects[rects.length - 1].top) : null);
          }
          const first = tops.find((top) => top !== null);
          const at = tops.findIndex((top) => top !== null && top > first + 2);
          return [node.data, at < 0 ? null : node.data.slice(0, at)];
        })"""
    )
    lines = {text.replace("\u00ad", ""): upto for text, upto in broken if upto is not None}
    assert lines["Wettersymbol"] == "Wetter\u00ad" and lines["Monatskalender"] == "Monats\u00ad"
    assert lines["Download-Anzeige"] == "Download-"  # (it had its own hyphen)
    page.get_by_placeholder("Baustein suchen").fill("monatskalender")
    expect(page.locator('.tile[data-item="widget:calendar"]')).to_have_count(1)
    page.get_by_placeholder("Baustein suchen").fill("wettersymbol")
    expect(page.locator('.tile[data-item="widget:symbol"]')).to_have_count(1)


def test_every_widget_type_has_a_name_in_both_languages(page):
    """The library, the layers and the inspector name a widget by its type: English knew 16 of
    the 19 and showed the fan, the weather symbol and the download display as "fan", "symbol"
    and "downloads"."""
    types = js(page, "Object.keys(window.libre.store.get().specs.widgets)")
    types.remove("module")  # a module is named by its kind
    singles = page.locator('.tile[data-item^="widget:"] .tile-name')
    for language, names in (
        ("en", ["Fan", "Weather symbol", "Download display"]),
        ("de", ["Lüfter", "Wettersymbol", "Download-Anzeige"]),
    ):
        js(page, f"window.libre.commands.runCommand('language.{language}')")
        page.wait_for_function(f"document.documentElement.lang === '{language}'")
        shown = [
            page.locator(f'.tile[data-item="widget:{kind}"] .tile-name').inner_text()
            for kind in ("fan", "symbol", "downloads")
        ]
        assert [name.replace("\u00ad", "") for name in shown] == names  # (soft hyphens: invisible)
        assert set(singles.all_inner_texts()).isdisjoint(types)  # none shows a raw type name
        assert singles.count() == len(types)


def test_every_source_of_a_module_has_a_name_in_both_languages(page):
    """The fan instrument can show a fan; its chip read "fan" (the names were kept in two
    places, the toolbar's and the inspector's, and neither had it)."""
    page.locator('.tile[data-item="module:instrument"]').click()
    assert js(page, "window.libre.doc.single().module") == "instrument"
    chips = page.locator(".inspector .chips .chip")
    for language, names in (
        ("en", ["CPU", "GPU", "Memory", "Disk", "Fan"]),
        ("de", ["CPU", "GPU", "Arbeitsspeicher", "Laufwerk", "Lüfter"]),
    ):
        js(page, f"window.libre.commands.runCommand('language.{language}')")
        page.wait_for_function(f"document.documentElement.lang === '{language}'")
        assert chips.all_inner_texts() == names
        chips.last.click()  # its source is the fan: the toolbar says so as well
        assert js(page, "window.libre.doc.single().source") == "fan"
        expect(page.locator(".floating-toolbar .tool-sensor")).to_have_text(names[-1])
        chips.first.click()


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


def test_the_palette_gives_the_focus_back(page):
    """After Escape, or a command that opens nothing, the key goes on where it was (it was lost
    to the page, which a keyboard user then has to tab through from its start)."""
    layers = page.locator(".rail-item", has_text="Layers")
    layers.focus()
    page.keyboard.press("Control+k")
    expect(page.locator(".palette input")).to_be_focused()
    page.keyboard.press("Escape")
    expect(layers).to_be_focused()
    page.keyboard.press("Control+k")
    page.locator(".palette input").fill("zoom in")
    page.keyboard.press("Enter")
    expect(page.locator(".palette")).to_have_count(0)
    expect(layers).to_be_focused()
    # a command that opens a dialog leaves the focus to the dialog
    page.keyboard.press("Control+k")
    page.locator(".palette input").fill("help and keyboard shortcuts")  # (not the whole PC's)
    page.keyboard.press("Enter")
    expect(page.locator(".dialog")).to_be_visible()
    page.wait_for_function("document.activeElement.closest('.dialog') !== null")


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


def test_save_as_says_why_a_built_in_theme_asks_for_a_name(page, isolated_home):
    """Someone who pressed "To the panel" on a theme that comes with Libre Panel is asked for a
    name, which is a surprise; the dialog says why. A theme of one's own has nothing to explain."""
    js(page, "window.libre.doc.edit('t', (theme) => { theme.name = 'Mine'; })")
    page.locator(".btn-apply").click()
    dialog = page.locator(".dialog")
    expect(dialog.locator(".dialog-text")).to_have_text(
        "“Libre Default” is built in and cannot be changed: "
        "your changes are saved as a theme of your own."  # (its name, not the title in the edit)
    )
    dialog.locator("input").fill("mine")
    dialog.locator("button[type='submit']").click()
    on_the_panel = "window.libre.store.get().pages.pages[0].theme === 'mine'"
    page.wait_for_function(on_the_panel)  # saved, and on the panel
    page.keyboard.press("Control+Shift+s")  # now it is one's own
    expect(dialog.locator("input")).to_have_value("mine")
    assert dialog.locator(".dialog-text").count() == 0
    page.keyboard.press("Escape")
    # in German, too
    js(page, "window.libre.commands.runCommand('language.de')")
    page.wait_for_function("document.documentElement.lang === 'de'")
    page.locator(".theme-name").click()
    page.locator(".theme-row", has_text="Libre Default").first.click()
    page.wait_for_function("window.libre.doc.theme().name === 'Libre Default'")
    js(page, "window.libre.doc.edit('t', (theme) => { theme.name = 'Meins'; })")
    page.locator(".btn-apply").click()
    expect(dialog.locator(".dialog-text")).to_have_text(
        "„Libre Default“ gehört zu Libre Panel und lässt sich nicht ändern: "
        "Deine Änderungen werden als eigenes Theme gespeichert."
    )


def test_every_way_to_another_theme_asks_before_it_drops_unsaved_changes(app_url, page):
    """The theme menu, the palette and the gallery's pencil all ask; Cancel leaves the work
    where it was, and only Discard opens the other theme."""
    js(page, "window.libre.doc.edit('n', (theme) => { theme.name = 'Unsaved work'; })")
    dialog = page.locator(".dialog")

    def kept():
        assert js(page, "window.libre.doc.theme().name") == "Unsaved work"
        assert js(page, "window.libre.store.get().dirty") is True
        assert js(page, "window.libre.store.get().doc.id") == "libre-default"

    def asked_and_kept(way):
        way()
        expect(dialog).to_contain_text("Discard unsaved changes?")
        dialog.get_by_role("button", name="Cancel").click()
        expect(dialog).to_have_count(0)
        kept()

    def from_the_menu():
        page.locator(".theme-name").click()
        page.locator(".theme-row", has_text="Column").first.click()

    def from_the_palette():
        page.keyboard.press("Control+k")
        page.locator(".palette input").fill("open theme column")
        page.keyboard.press("Enter")

    asked_and_kept(from_the_menu)
    asked_and_kept(from_the_palette)
    page.goto(f"{app_url}#/home/themes")
    page.wait_for_selector(".start.gallery .theme-card")
    card = page.locator('.theme-card[data-theme="column"]')

    def from_the_gallery():
        card.hover()
        card.locator(".theme-card-edit").click()

    asked_and_kept(from_the_gallery)
    assert js(page, "window.libre.store.get().route.view") == "home"  # and it stayed on Start
    from_the_gallery()  # now the work goes
    dialog.locator(".btn-danger").click()
    # (the server draws the gallery's pictures meanwhile, so the theme may take a moment)
    expect(page.locator(".tab[aria-current='page']")).to_have_text("Design", timeout=30000)
    assert js(page, "window.libre.store.get().doc.id") == "column"
    assert js(page, "window.libre.store.get().dirty") is False


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
        # a mode's page takes its theme from a list on its card (it could only be put there
        # from the theme, with the + over the canvas: not found)
        page.locator(".rail-item", has_text="Pages").click()
        game = page.locator(".page-card", has_text="In game")
        choice = game.locator(".page-card-theme select")
        playwright.expect(choice).to_have_value("slate")
        choice.select_option("nimbus")
        waits_for(page, lambda: game_theme() == "nimbus")
        choice.select_option("")  # the normal theme
        waits_for(page, lambda: not game_theme())
        # music's page and the idle clock's: off until switched on, their own screens first
        music = page.locator(".page-card[data-page='media']")
        playwright.expect(music.locator(".page-card-theme select")).to_have_value("encore")
        options = music.locator(".page-card-theme option").all_inner_texts()
        assert options[0] == "Encore" and "Round report" not in options
        music.get_by_role("switch").click()
        waits_for(page, lambda: getattr(settings_config().modes.get("media"), "on", False))
        idle = page.locator(".page-card[data-page='idle']")
        first = idle.locator(".page-card-theme option").all_inner_texts()[:2]
        assert first == ["Almanac", "Almanac Analog"]
        minutes = idle.get_by_role("textbox", name="Minutes without input")
        minutes.fill("10")
        minutes.press("Enter")
        waits_for(page, lambda: getattr(settings_config().modes.get("idle"), "minutes", 0) == 10)


def test_a_first_start_asks_how_the_panel_is_mounted(app_url, isolated_home):
    """Upright, each screen shows in its version for a bar on end: once in the list, by its
    name, with that version's picture and size; Design opens that version, and Start's first
    steps and Settings → Panel switch it back."""
    routes = {"**/api/app": a_panel_app()}
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        page.wait_for_selector(".start.first .theme-card")
        choice = page.locator(".start-mounted").get_by_role("radio", name="Vertical")
        choice.click()
        waits_for(page, lambda: settings_config().device.orientation == "portrait")
        expect(page.locator(".theme-grid.upright")).to_have_count(1)
        cards = shown_themes(page)
        assert "lumen" in cards and not [c for c in cards if c.endswith("-vertical")]
        lumen = page.locator(".theme-card[data-theme='lumen']")
        expect(lumen.locator("img")).to_have_attribute("src", re.compile(r"lumen-vertical"))
        expect(lumen.locator(".theme-card-note")).to_contain_text("480 × 1920")
        assert cards[-1] == "studio"  # no version for a bar on end: last
        lumen.locator(".theme-card-main").click()
        page.wait_for_function("window.libre.store.get().doc?.id === 'lumen-vertical'")
        page.locator(".tab", has_text="Start").click()
        page.wait_for_selector(".first-steps")
        page.locator(".first-steps").get_by_role("radio", name="Horizontal").click()
        waits_for(page, lambda: settings_config().device.orientation == "landscape")
        page.goto(f"{app_url}#/settings/panel")
        row = page.locator(".settings-row", has_text="How the panel is mounted")
        expect(row.get_by_role("radio", name="Horizontal")).to_have_attribute(
            "aria-checked", "true"
        )


def test_start_shows_the_pages_the_first_steps_and_the_way_to_the_start_screen(
    app_url, isolated_home
):
    """Start says what the panel shows when, each page with its switch; the first steps tick
    off what is done and can be hidden; and the panel's own start screen is a click away."""
    from libre_panel.config import set_config_value

    choose_theme(isolated_home, "lumen")
    set_config_value("device.model", "turing-9.2-usb")
    routes = {"**/api/app": a_panel_app()}
    with browser_page(app_url, "home", routes=routes, allow=PICTURE) as page:
        card = page.locator(".pages-card")
        expect(card.locator("[data-page='normal']")).to_contain_text("Lumen")
        card.locator("[data-page='media']").get_by_role("switch").click()
        waits_for(page, lambda: getattr(settings_config().modes.get("media"), "on", False))
        steps = page.locator(".first-steps")
        expect(steps.locator("[data-step='media']")).to_have_class(re.compile(r"\bdone\b"))
        steps.locator("[data-step='game']").get_by_role("switch").click()
        waits_for(page, lambda: "game" in settings_config().services.enabled)
        game = card.locator("[data-page='game']").get_by_role("switch")
        expect(game).to_have_attribute("aria-checked", "true")
        chosen = steps.locator("[data-step='mounted'] [role='radio'][aria-checked='true']")
        expect(chosen).to_have_count(0)  # nothing is marked until it is chosen
        steps.get_by_role("button", name="Hide").click()
        expect(steps).to_have_count(0)
        page.locator(".start-screen-tile").get_by_role("button", name="Set up").click()
        page.wait_for_url(re.compile(r"#/settings/panel/start-screen$"))
        expect(page.locator("#start-screen")).to_be_in_viewport()


def test_a_page_lists_the_screens_in_groups(app_url, isolated_home):
    """The screens made for the page first, then Libre Panel's own, yours, each collection and
    those made for another panel; two themes of one name are told apart, and none is twice."""
    import json

    from libre_panel.config import set_config_value
    from libre_panel.theme.model import find_theme, save_theme

    set_config_value("theme", "lumen")  # a 9.2" bar
    lumen = json.loads((find_theme("lumen") / "theme.json").read_text(encoding="utf-8"))
    save_theme("my-lumen", lumen)  # a copy of yours with the same name
    with browser_page(app_url) as page:
        page.locator(".rail-item", has_text="Pages").click()
        game = page.locator(".page-card[data-page='game'] .page-card-theme select")
        playwright.expect(game.locator("option[value='arena']")).to_have_count(1)
        groups = game.evaluate(
            "s => [...s.querySelectorAll('optgroup')].map((g) => [g.label,"
            " [...g.querySelectorAll('option')].map((o) => o.textContent)])"
        )
        names = [name for name, _ in groups]
        assert names == ["Made for this page", "Libre Panel", "Your themes", "SPUR II",
                         "For other panels"]  # fmt: skip
        listed = dict(groups)
        assert listed["Made for this page"][0] == "Arena"  # Libre Panel's game screen first
        assert "Lumen (lumen)" in listed["Libre Panel"]
        assert listed["Your themes"] == ["Lumen (my-lumen)"]
        assert "Pico" in listed["For other panels"] and "Pico" not in listed["Libre Panel"]
        values = game.locator("option").evaluate_all("o => o.map((x) => x.value)")
        assert len(values) == len(set(values))  # nothing twice


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
    registry = FakeRegistry()
    app = BackgroundApp(port=0, autostart=Autostart("win32", registry=registry))
    url = app.start()
    try:
        with browser_page(url) as page:
            chip = page.locator(".panel-chip")
            page.wait_for_function("document.querySelector('.panel-chip.showing') !== null")
            assert "Showing" in chip.inner_text()
            chip.click()
            popover = page.locator(".popover")
            state = popover.locator(".run-state").inner_text()
            assert "Showing" in state and "libre-default" in state and "PNG file" in state
            set_range(popover.locator(".slider input"), 30)
            assert wait_for(lambda: load_config().device.brightness == 30)
            popover.locator("[role='switch'][aria-label='Upside down']").click()
            assert wait_for(lambda: load_config().device.rotate == 180)
            popover.locator("[role='switch'][aria-label='Start with system']").click()
            assert wait_for(lambda: "Libre Panel" in registry.values)
            popover.locator(".btn", has_text="Pause panel").click()
            assert wait_for(lambda: app.panel.paused)
            page.wait_for_function("document.querySelector('.panel-chip.paused') !== null")
            popover.locator(".btn", has_text="Resume panel").click()
            assert wait_for(lambda: not app.panel.paused)
            page.wait_for_function("document.querySelector('.panel-chip.showing') !== null")
            page.keyboard.press("Escape")
            expect(popover).to_have_count(0)
            chip.click()
            page.locator(".popover .btn", has_text="Quit Libre Panel").click()
            page.locator(".dialog .btn-danger").click()  # asked first
            assert app.quit_requested.wait(5)
            page.wait_for_function("window.libre.store.get().app.quit === true")
    finally:
        app.quit()
        app.shutdown()


def test_the_chip_has_the_panels_numbers_and_a_way_to_live(app_url):
    """The old editor's chip showed how the panel is doing: the frame rate against its target,
    the data rate and the drawing time; its Control button is the Live view's now."""
    routes = {"**/api/app": a_panel_app()}
    with browser_page(app_url, routes=routes, allow=PICTURE) as page:
        page.locator(".panel-chip").click()
        rows = page.locator(".popover .run-number")
        assert rows.evaluate_all("rows => rows.map((r) => r.dataset.number)") == [
            "fps",
            "data",
            "draw",
        ]
        fps, data, draw = (
            page.locator(f"[data-number={name}]") for name in ("fps", "data", "draw")
        )
        expect(fps).to_contain_text("Frames per second")
        expect(fps).to_contain_text("50.0")
        expect(fps).to_contain_text("target 50")
        expect(data).to_contain_text("248")
        expect(data).to_contain_text("KB/s")
        expect(draw).to_contain_text("9.8")
        expect(draw).to_contain_text("peak 21")
        expect(page.locator(".run-number.warn, .run-number.bad")).to_have_count(0)  # as it should
        page.locator(".popover .pop-head .link-btn").click()  # Live
        page.wait_for_selector(".view .live")
        expect(page.locator(".popover")).to_have_count(0)
    # a panel that cannot keep up is marked, as on Live
    with browser_page(app_url, routes={"**/api/app": a_panel_app(fps=20.0)}) as page:
        page.locator(".panel-chip").click()
        expect(page.locator("[data-number=fps]")).to_have_class("run-number bad")


def test_the_chip_numbers_leave_out_what_nothing_counts(background):
    """A panel that is a PNG file has no data rate: no row with a dash for it."""
    app, url = background
    with browser_page(url, allow=PICTURE) as page:
        page.locator(".panel-chip").click()
        page.wait_for_selector("[data-number=fps]")
        page.wait_for_selector("[data-number=draw]")
        assert page.locator("[data-number=data]").count() == 0


def test_brightness_stays_where_it_was_let_go_until_the_panel_answers(app_url):
    """The slider gave the old value back the moment it was let go, until the background app
    answered (a panel takes a moment); it keeps the new one, and returns to the old one only
    when the change is refused."""
    import json

    held = []

    def handler(route, request):
        if request.method == "GET":
            body = json.dumps(app_answer())
            route.fulfill(status=200, content_type="application/json", body=body)
        else:
            held.append((route, json.loads(request.post_data)))

    with browser_page(app_url, routes={"**/api/app": handler}, allow=("/api/app",)) as page:
        page.locator(".panel-chip").click()
        slider = page.locator(".popover input[aria-label='Brightness']")
        shown = page.locator(".popover .slider output")
        expect(slider).to_have_value("80")
        set_range(slider, 35)
        while not held:  # the change is on its way
            page.wait_for_timeout(20)
        assert held[0][1] == {"action": "brightness", "value": 35}
        page.wait_for_timeout(200)  # time enough for the old value to come back, if it would
        expect(slider).to_have_value("35")
        expect(shown).to_have_text("35 %")
        # answered: what the panel says is the same
        route, _ = held.pop()
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(app_answer(brightness=35))
        )
        expect(slider).to_have_value("35")
        # refused: the slider goes back to what the panel has
        set_range(slider, 60)
        while not held:
            page.wait_for_timeout(20)
        expect(slider).to_have_value("60")
        route, _ = held.pop()
        route.fulfill(status=500, content_type="application/json", body='{"error": "no"}')
        expect(slider).to_have_value("80")


# -- Design: the work of the hand ----------------------------------------------------------------
# Selecting, aligning, copying, locking, building blocks, the palette, panels of other shapes,
# the modules on their grid, the pages of the console and the numbers they judge.


def drag(page, start, end, steps=8):
    """Press at ``start``, move to ``end`` and let go (screen pixels)."""
    page.mouse.move(*start)
    page.mouse.down()
    page.mouse.move(*end, steps=steps)
    page.mouse.up()


def test_marquee_align_copy_paste_delete(page):
    count = js(page, "window.libre.doc.widgets().length")
    canvas = page.locator(".panel-canvas").bounding_box()
    drag(
        page,
        (canvas["x"] + 5, canvas["y"] + canvas["height"] * 0.33),
        (canvas["x"] + canvas["width"] * 0.6, canvas["y"] + canvas["height"] * 0.72),
    )
    selected = js(page, "window.libre.store.get().selection")
    assert len(selected) >= 4 and "backdrop" not in selected  # a locked layer is not picked up
    page.locator(".floating-toolbar").get_by_label("Align top edges").click()
    boxes = js(page, "window.libre.store.get().render.boxes")
    assert len({boxes[id][1] for id in selected}) == 1  # all on one line
    page.keyboard.press("Control+c")
    page.keyboard.press("Control+v")
    assert js(page, "window.libre.doc.widgets().length") == count + len(selected)
    page.keyboard.press("Delete")  # the pasted ones are the selection
    assert js(page, "window.libre.doc.widgets().length") == count


def test_a_locked_layer_does_not_move_and_is_saved_locked(page, isolated_home):
    from libre_panel.theme.model import load_theme

    page.locator(".rail-item", has_text="Layers").click()
    row = page.locator(".layer", has=page.locator('.layer-row[data-id="clock"]'))
    row.locator(".layer-flag[aria-label='Lock']").click()
    assert widget(page, "clock")["locked"] is True
    start = (widget(page, "clock")["x"], widget(page, "clock")["y"])
    box = page.locator('.overlay .box[data-id="clock"]').bounding_box()
    drag(page, (box["x"] + 5, box["y"] + 5), (box["x"] + 80, box["y"] + 40), steps=5)
    assert (widget(page, "clock")["x"], widget(page, "clock")["y"]) == start
    row.locator(".layer-row").click()  # a locked layer is picked in the list
    assert js(page, "window.libre.store.get().selection") == ["clock"]
    page.keyboard.press("ArrowRight")  # and does not nudge either
    assert (widget(page, "clock")["x"], widget(page, "clock")["y"]) == start
    page.keyboard.press("Control+s")  # a built-in theme: asks for a new name
    dialog = page.locator(".dialog")
    dialog.locator("input").fill("my-test")
    dialog.locator("button[type='submit']").click()
    page.wait_for_function("!window.libre.store.get().dirty")
    saved = load_theme(isolated_home / "themes" / "my-test")
    assert next(w for w in saved.widgets if w["id"] == "clock")["locked"] is True


def test_a_building_block_and_a_renamed_palette_colour(page):
    count = js(page, "window.libre.doc.widgets().length")
    page.locator('.tile[data-item="preset:cpu-card"]').click()
    assert js(page, "window.libre.doc.widgets().length") > count
    assert len(js(page, "window.libre.store.get().selection")) > 1  # its parts, together
    js(page, "window.libre.doc.setSelection([])")  # the theme's settings
    page.locator(".details-button").click()
    refs = js(page, "JSON.stringify(window.libre.doc.theme()).split('@accent\"').length - 1")
    assert refs > 0
    names = js(page, "Object.keys(window.libre.doc.theme().palette)")
    row = page.locator(".palette-row").nth(names.index("accent"))
    name = row.get_by_label("Name (a-z, 0-9, - _)")
    name.fill("signal")
    name.press("Enter")
    now = "JSON.stringify(window.libre.doc.theme()).split('@signal\"').length - 1"
    page.wait_for_function(f"({now}) === {refs}", timeout=5000)  # every use follows the name
    assert js(page, "'accent' in window.libre.doc.theme().palette") is False
    assert js(page, "JSON.stringify(window.libre.doc.theme()).includes('@accent\"')") is False


def test_the_fan_block_takes_another_fan_for_all_its_parts_at_once(page):
    """The block's fan, its name and its speed read one sensor: chosen once for the three
    (it read the graphics card's fan, and its name was the text "GPU FAN")."""
    page.locator('.tile[data-item="preset:fan"]').click()
    chosen = js(page, "window.libre.store.get().selection")  # with the unit's text
    reading = [wid for wid in chosen if widget(page, wid).get("sensor")]
    assert len(reading) == 3
    inspector = page.locator(".inspector")
    assert "3" in inspector.locator(".field", has=page.locator(".sensor-button")).inner_text()
    inspector.locator(".sensor-button").click()
    picker = page.locator(".sensor-picker")
    picker.locator("input").fill("fan.lpc.fan_2")
    picker.locator(".sensor-row[data-key='fan.lpc.fan_2']").click()
    parts = [widget(page, wid) for wid in reading]
    assert {part["sensor"] for part in parts} == {"fan.lpc.fan_2"}
    assert [p["format"] for p in parts if p["type"] == "metric"][0] == "{label:upper}"
    page.keyboard.press("Control+z")  # one step back for all three
    assert {widget(page, wid)["sensor"] for wid in reading} == {"gpu.fan"}


def test_a_round_panel_and_the_strip_its_frame_hides(page):
    page.locator(".panel-chip").click()
    page.locator(".model-row", has_text="Turing 9.2").click()
    page.wait_for_function("window.libre.store.get().render.size[0] === 1920")
    zoom = page.locator(".panel-canvas").bounding_box()["width"] / 1920
    strip = page.locator(".hidden-strip.top")
    assert strip.bounding_box()["height"] == pytest.approx(18 * zoom, abs=1)
    assert "18 px" in strip.get_attribute("title")
    assert page.locator(".panel-canvas.round").count() == 0
    page.locator(".model-row", has_text="Turing 2.1").click()
    page.wait_for_function("window.libre.store.get().render.size.join() === '480,480'")
    assert page.locator(".panel-canvas.round").count() == 1
    assert page.locator(".hidden-strip").count() == 0  # a round panel hides nothing


def test_a_portrait_panel_sits_in_its_frame(page):
    """On end, a bar is narrower than the line under it: the frame holds the panel in the
    middle, as on its side, instead of growing to that line with the panel on its left."""
    import json

    from test_editor_server import theme_dict

    theme = theme_dict()
    theme["display"] = {"model": "turing-9.2-usb", "orientation": "portrait"}
    theme["display"].update(width=480, height=1920)
    js(page, f"window.libre.doc.openDoc({{ theme: {json.dumps(theme)}, dirty: true }})")
    page.wait_for_function("window.libre.store.get().render.size.join() === '480,1920'")
    page.wait_for_selector(".hidden-strip")
    frame = page.locator(".panel-frame").bounding_box()
    canvas = page.locator(".panel-canvas").bounding_box()
    assert frame["width"] - canvas["width"] == pytest.approx(22, abs=2)  # its padding, each side
    middle = canvas["x"] + canvas["width"] / 2
    assert middle == pytest.approx(frame["x"] + frame["width"] / 2, abs=1)
    foot = page.locator(".canvas-foot").bounding_box()
    assert foot["width"] > frame["width"]  # (the line under it is wider)


def test_a_theme_that_is_opened_shows_no_picture_of_the_one_before(page):
    """While the new theme is drawn (a slow computer, a big theme) the canvas waits with a
    spinner at the new theme's size, with no boxes: it showed the old picture, whose boxes
    could be picked, at the old size."""
    import json

    from test_editor_server import theme_dict

    held = []
    page.route("**/api/render", lambda route, request: held.append(route))  # not answered yet
    theme = theme_dict()
    theme["display"] = {"model": "turing-9.2-usb", "orientation": "landscape"}
    theme["display"].update(width=1920, height=480)
    theme["widgets"] = [
        {"type": "rect", "id": "probe", "x": 700, "y": 40, "w": 100, "h": 50, "color": "#ff8800"}
    ]
    assert page.locator(".panel-canvas img.preview").count() == 1  # the theme before is drawn
    js(page, f"window.libre.doc.openDoc({{ theme: {json.dumps(theme)}, dirty: true }})")
    expect(page.locator(".panel-canvas .preview-wait .spinner")).to_be_visible()
    assert page.locator(".panel-canvas img.preview").count() == 0
    assert page.locator(".overlay .box").count() == 0
    assert js(page, "window.libre.store.get().render.size") == [1920, 480]
    for _ in range(100):  # the request of the new theme is held
        if held:
            break
        page.wait_for_timeout(20)
    page.unroute("**/api/render")  # lets the held request through
    page.wait_for_selector(".panel-canvas img.preview")  # drawn at last
    expect(page.locator('.overlay .box[data-id="probe"]')).to_have_count(1)
    assert page.locator(".panel-canvas .preview-wait").count() == 0


def test_a_widget_snaps_to_the_strip_the_frame_hides(page):
    """The edge of what the bezel hides is a guide like any box's edge."""
    import json

    from test_editor_server import theme_dict

    theme = theme_dict()
    theme["display"] = {"model": "turing-9.2-usb", "orientation": "landscape"}
    theme["display"].update(width=1920, height=480)
    theme["widgets"] = [
        {"type": "rect", "id": "probe", "x": 700, "y": 40, "w": 100, "h": 50, "color": "#ff8800"}
    ]
    js(page, f"window.libre.doc.openDoc({{ theme: {json.dumps(theme)}, dirty: true }})")
    page.wait_for_function("window.libre.store.get().render.size[0] === 1920")
    page.wait_for_selector(".hidden-strip.top")
    zoom = page.locator(".panel-canvas").bounding_box()["width"] / 1920
    box = page.locator('.overlay .box[data-id="probe"]').bounding_box()
    middle = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    drag(page, middle, (middle[0], middle[1] - 20 * zoom), steps=6)  # its top edge to about y 20
    assert widget(page, "probe")["y"] == 18  # not 20: it clings to the edge of the strip


def test_a_corner_resizes_a_widget(page):
    js(page, "window.libre.doc.setSelection(['cpu-ring'])")
    before = widget(page, "cpu-ring")
    zoom = page.locator(".panel-canvas").bounding_box()["width"] / 480
    handle = page.locator(".handle.se").bounding_box()
    start = (handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2)
    drag(page, start, (start[0] + 30 * zoom, start[1] + 20 * zoom), steps=6)
    now = widget(page, "cpu-ring")
    assert now["w"] == pytest.approx(before["w"] + 30, abs=8)  # (a snap step either way)
    assert now["h"] == pytest.approx(before["h"] + 20, abs=8)
    assert (now["x"], now["y"]) == (before["x"], before["y"])  # the top left stays
    page.keyboard.press("Control+z")
    assert (widget(page, "cpu-ring")["w"], widget(page, "cpu-ring")["h"]) == (
        before["w"],
        before["h"],
    )


def test_snapping_can_be_to_a_single_pixel(page):
    """Snap offers 1 px beside off, 4, 8 and 16: a widget then moves in whole pixels where
    4 px puts it on a step of four."""
    import json

    from test_editor_server import theme_dict

    theme = theme_dict()
    theme["widgets"] = [
        {"type": "rect", "id": "probe", "x": 10, "y": 10, "w": 100, "h": 50, "color": "#ff8800"}
    ]
    js(page, f"window.libre.doc.openDoc({{ theme: {json.dumps(theme)}, dirty: true }})")
    # (no guides: only the step pulls)
    js(
        page,
        "window.libre.store.set({ prefs: { ...window.libre.store.get().prefs, guides: false } })",
    )
    page.locator(".rail-gear").click()
    choices = page.get_by_role("radiogroup", name="Snap")
    assert choices.get_by_role("radio").all_inner_texts() == ["off", "1", "4", "8", "16"]
    zoom = page.locator(".panel-canvas").bounding_box()["width"] / 480
    # the preview is drawn where the probe is: a move starts from the drawn box, which an undo
    # just before has not moved back yet
    drawn_in_place = """(() => {
      const box = window.libre.store.get().render.boxes.probe;
      const probe = window.libre.doc.widgetById('probe');
      return Boolean(box) && box[0] === probe.x && box[1] === probe.y;
    })()"""

    def snapped(step, dx, dy):
        """Where the probe's corner ends after the pointer moved (dx, dy) screen pixels."""
        choices.get_by_role("radio", name=str(step) if step else "off", exact=True).click()
        assert js(page, "window.libre.store.get().prefs.snap") == step
        page.keyboard.press("Escape")  # the popover
        page.wait_for_function(drawn_in_place, timeout=5000)
        box = page.locator('.overlay .box[data-id="probe"]').bounding_box()
        middle = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        drag(page, middle, (middle[0] + dx, middle[1] + dy), steps=6)
        found = (widget(page, "probe")["x"], widget(page, "probe")["y"])
        page.keyboard.press("Control+z")
        assert (widget(page, "probe")["x"], widget(page, "probe")["y"]) == (10, 10)
        page.locator(".rail-gear").click()
        return found

    dx, dy = 40, 10  # screen pixels: 28.9 and 7.2 panel pixels, which no step of four makes
    assert snapped(1, dx, dy) == (round(10 + dx / zoom), round(10 + dy / zoom))
    assert snapped(4, dx, dy) == (40, 16)
    assert snapped(0, dx, dy) == (round(10 + dx / zoom), round(10 + dy / zoom))  # (off: the same)


def test_a_choice_that_none_of_the_options_has_is_still_in_the_tab_order(app_url):
    """A group of choices with a value that matches no option (a setting from a version that
    had other steps) must keep one button reachable by Tab, or the keyboard cannot get in."""
    with browser_page(app_url, "design") as page:
        page.add_init_script("localStorage.setItem('libre-panel-prefs', '{\"snap\": 2}')")
        page.reload()
        page.wait_for_selector(".panel-canvas .preview")
        page.locator(".rail-gear").click()
        radios = page.get_by_role("radiogroup", name="Snap").get_by_role("radio")
        tab_order = "buttons => buttons.map((b) => b.tabIndex)"
        assert radios.evaluate_all(tab_order) == [0, -1, -1, -1, -1]
        assert radios.evaluate_all("buttons => buttons.map((b) => b.ariaChecked)") == ["false"] * 5
        radios.nth(2).click()  # and a choice made is the one in the tab order
        assert radios.evaluate_all(tab_order) == [-1, -1, 0, -1, -1]


@pytest.fixture
def drawing_plugin(plugin_folder):
    """A plugin with a screen and a widget type, installed before the app starts."""
    from test_plugins import DRAWING, DRAWING_PARTS

    return plugin_folder(DRAWING, DRAWING_PARTS)


def test_a_plugins_widget_type_and_screen(drawing_plugin, page):
    tile = page.locator(".tile[data-item='widget:demo.bar']")
    assert tile.inner_text() == "Demo bar"
    assert page.locator(".tile[data-item='preset:demo.bar-1']").count() == 1  # and its block
    count = js(page, "window.libre.doc.widgets().length")
    tile.click()
    assert js(page, "window.libre.doc.widgets().length") == count + 1
    assert js(page, "window.libre.doc.single().type") == "demo.bar"
    js(page, "window.libre.doc.setSelection([])")  # the theme's settings: a screen under it all
    page.locator(".details-button").click()
    screen = page.locator("select[aria-label='screen']")
    before = js(page, "window.libre.store.get().render.png")
    screen.select_option("tint")
    page.wait_for_function("window.libre.doc.theme().screen?.name === 'tint'")
    page.get_by_role("group", name="color", exact=True).wait_for()  # its options
    page.wait_for_function(f"window.libre.store.get().render.png !== {before!r}")  # drawn with it
    assert js(page, "window.libre.store.get().renderError") is None
    screen.select_option("")
    page.wait_for_function("!window.libre.doc.theme().screen")


@pytest.fixture
def toast_plugin(plugin_folder):
    from test_plugins import TOAST_PARTS, TOAST_STYLE

    return plugin_folder(TOAST_STYLE, TOAST_PARTS)


def test_the_notice_settings_with_a_plugins_style(toast_plugin, page, isolated_home):
    """The theme's Messages: position, hidden kinds, the queue, and a plugin's style with its
    options, saved with the theme."""
    from libre_panel.theme.model import load_theme

    page.locator(".details-button").click()
    page.locator("select[aria-label='position']").select_option("bottom-left")
    page.get_by_label("hidden kinds").fill("music, volume")
    page.get_by_role("switch", name="one after another").click()
    page.locator("select[aria-label='style']").select_option("demo.band")
    height = page.get_by_label("height", exact=True)
    height.fill("55")
    height.press("Enter")
    assert js(page, "window.libre.doc.theme().toast") == {
        "anchor": "bottom-left",
        "queue": False,
        "off": ["music", "volume"],
        "style": "demo.band",
        "options": {"height": 55},
    }
    page.keyboard.press("Control+s")  # a built-in theme: asks for a new name
    dialog = page.locator(".dialog")
    dialog.locator("input").fill("my-test")
    dialog.locator("button[type='submit']").click()
    page.wait_for_function("!window.libre.store.get().dirty")
    saved = load_theme(isolated_home / "themes" / "my-test")
    assert saved.toast["style"] == "demo.band" and saved.toast["options"] == {"height": 55}
    assert saved.toast_anchor == "bottom-left" and saved.toast["off"] == ["music", "volume"]
    assert saved.toast["queue"] is False


@pytest.fixture
def page_plugin(plugin_folder):
    from test_plugins import PAGE, PAGE_FILES, PAGE_PARTS

    return plugin_folder(PAGE, PAGE_PARTS, files=PAGE_FILES)


def test_a_plugin_page_opens_in_the_app(page_plugin, page):
    page.locator(".rail-item", has_text="Pages").click()
    page.locator(".plugin-pages .list-row", has_text="Analysis").click()
    page.wait_for_function("location.hash === '#/plugin/analysis'")
    frame = page.frame_locator(".plugin-frame")
    frame.locator("#out", has_text="1 3").wait_for()  # kit.js reached the page's own API
    assert page.locator(".panel-canvas").count() == 0  # the page took the view's place
    page.locator(".plugin-bar").get_by_role("button", name="Back").click()
    page.wait_for_selector(".panel-canvas .preview")
    page.keyboard.press("Control+k")  # and Ctrl+K finds it
    page.locator(".palette input").fill("analysis")
    expect(page.locator(".palette-item.active")).to_contain_text("Analysis")
    page.keyboard.press("Enter")
    page.wait_for_function("location.hash === '#/plugin/analysis'")
    page.frame_locator(".plugin-frame").locator("#out", has_text="1 3").wait_for()


def test_back_from_a_plugin_page_stays_in_the_app(app_url):
    """Back goes to the view the page was opened from, and to Start where the app was opened
    on the page (history.back() left the app for the page before it)."""

    def back(page):
        page.locator(".plugin-bar").get_by_role("button", name="Back").click()

    with browser_page(app_url, "plugin/control") as page:
        back(page)
        page.wait_for_function("location.hash === '#/home'")
        page.wait_for_selector(".view .start")
        page.goto(f"{app_url}#/look")
        page.wait_for_selector(".look-side")
        page.goto(f"{app_url}#/plugin/control")
        page.wait_for_selector(".plugin-bar")
        back(page)
        page.wait_for_function("location.hash === '#/look'")
        page.wait_for_selector(".look-side")


def test_a_view_that_cannot_be_drawn_says_so_and_the_others_stay_reachable(app_url):
    """A view that throws (here: a plugin list that is not a list) left the whole page blank.
    The view's place says what happened, the header works, and the view can try again."""
    with browser_page(app_url, "home") as page:
        page.goto(f"{app_url}#/plugin/anything")
        page.wait_for_selector(".plugin-view")
        js(page, "window.libre.store.set({ plugins: null })")
        alert = page.locator(".view-message.error")
        expect(alert).to_contain_text("This view could not be drawn")
        assert page.locator(".topbar").is_visible()
        js(page, "window.libre.store.set({ plugins: [] })")  # what was wrong is mended
        alert.get_by_role("button", name="Try again").click()
        expect(page.locator(".plugin-view .view-message")).to_have_text(
            "This plugin page is not installed."
        )
        assert alert.count() == 0
        js(page, "window.libre.store.set({ plugins: null })")
        expect(alert).to_be_visible()
        page.get_by_role("link", name="Live").click()  # another view is drawn, as it should be
        page.wait_for_selector(".view .live")
        assert alert.count() == 0


def test_a_bad_address_does_not_break_the_gallery(app_url):
    """The gallery opens the group the address names; "%E0%A4%A" is no text, and decoding it
    threw in the middle of drawing Start."""
    with browser_page(app_url, "home/themes/%E0%A4%A", allow=PICTURE) as page:
        page.wait_for_selector(".start.gallery .theme-card")
        assert page.locator(".view-message.error").count() == 0


def test_a_plain_editor_has_no_panel_controls(page):
    chip = page.locator(".panel-chip")
    assert "480 × 320" in chip.inner_text()  # no panel runs: the size to design for
    assert page.locator(".panel-chip.preview").count() == 1
    chip.click()
    popover = page.locator(".popover")
    assert popover.locator(".model-row").count() > 5  # which panel the theme is for
    assert popover.get_by_role("switch").count() == 0  # but nothing to switch or pause
    assert popover.get_by_text("On the panel now").count() == 0


def new_theme_from_a_layout(page, panel="Turing 9.2"):
    """Choose a panel, then make a theme from the first layout of the New theme dialog."""
    page.locator(".panel-chip").click()
    row = page.locator(".model-row", has_text=panel)
    row.click()
    # the theme is fitted to it by the server: whether that left it unsaved is known once it came
    # back (the palette runs New theme a moment after Enter, which asks about unsaved changes)
    expect(row).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Escape")  # the popover
    page.keyboard.press("Control+k")
    page.locator(".palette input").fill("new theme")
    page.keyboard.press("Enter")
    if js(page, "window.libre.store.get().dirty"):
        page.locator(".dialog .btn-danger").click()  # a panel change is unsaved: discard it
    page.wait_for_selector(".dialog .template-card")
    page.locator(".dialog .btn-primary").click()  # Create: the first layout, in Arctic
    page.wait_for_function(
        "window.libre.doc.widgets().filter(window.libre.doc.isModule).length === 5"
    )


def modules(page):
    cells = "w => [w.id, w.module, w.col, w.row, w.cols, w.rows]"
    return js(page, f"window.libre.doc.widgets().filter(window.libre.doc.isModule).map({cells})")


def cell_center(page, col, row):
    """The middle of one cell of the module grid, in screen pixels."""
    grid = js(page, "window.libre.store.get().render.grid")
    canvas = page.locator(".panel-canvas").bounding_box()
    zoom = canvas["width"] / js(page, "window.libre.store.get().render.size[0]")
    x = grid["x"] + col * (grid["cell_w"] + grid["gap"]) + grid["cell_w"] / 2
    y = grid["y"] + row * (grid["cell_h"] + grid["gap"]) + grid["cell_h"] / 2
    return canvas["x"] + x * zoom, canvas["y"] + y * zoom


def test_modules_from_a_layout_drag_resize_swap_and_look(page):
    new_theme_from_a_layout(page)
    page.wait_for_function("window.libre.store.get().render.grid?.columns === 8")

    # a module from the library onto a free cell
    js(page, "window.libre.doc.setSelection(['network-2'])")
    page.keyboard.press("Delete")
    tile = page.locator(".tile[data-item='module:stat']").bounding_box()
    page.mouse.move(tile["x"] + 20, tile["y"] + 20)
    page.mouse.down()
    page.mouse.move(tile["x"] + 60, tile["y"] + 40, steps=4)
    page.mouse.move(*cell_center(page, 1, 1), steps=8)
    page.mouse.up()
    assert ["stat", "stat", 1, 1, 1, 1] in modules(page)

    # a click adds one at the first free spot
    page.locator(".tile[data-item='module:date']").click()
    assert ["date", "date", 0, 1, 1, 1] in modules(page)

    # wider at its east edge, then moved by keyboard is blocked by a neighbour
    js(page, "window.libre.doc.setSelection(['stat'])")
    handle = page.locator(".handle.e").bounding_box()
    start = (handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2)
    drag(page, start, (cell_center(page, 2, 1)[0], start[1]), steps=6)
    assert ["stat", "stat", 1, 1, 2, 1] in modules(page)
    page.keyboard.press("ArrowLeft")  # the date is in the way
    assert ["stat", "stat", 1, 1, 2, 1] in modules(page)

    # dropped on another module of the same size: they swap
    drag(page, cell_center(page, 5, 0), cell_center(page, 5, 1))
    rings = {m[0]: m[2:4] for m in modules(page) if m[1] == "ring"}
    assert rings == {"ring-4": [5, 1], "ring-5": [5, 0]}
    page.keyboard.press("Control+z")
    rings = {m[0]: m[2:4] for m in modules(page) if m[1] == "ring"}
    assert rings == {"ring-4": [5, 0], "ring-5": [5, 1]}

    # a look restyles the theme
    js(page, "window.libre.doc.setSelection([])")
    page.locator(".inspector .look-tile", has_text="Paper").click()
    assert js(page, "window.libre.doc.theme().style.card") == "flat"
    expect(page.locator(".inspector .look-tile[aria-checked='true']")).to_have_text("Paper")


def test_every_handle_of_a_module_resizes_it_one_cell_further_out(page):
    """The eight handles of a module: the one pressed must stay under the pointer while it
    is dragged (it once left with the first move, and the drag never reached the module)."""
    js(
        page,
        """window.libre.doc.edit('grid', (theme) => {
          const at = { col: 4, row: 3, cols: 1, rows: 1 };
          theme.widgets = [{ type: 'module', id: 'box', module: 'stat', ...at }];
          theme.grid = { columns: 9, rows: 7, gap: 0, margin: 0 };
        })""",
    )
    page.wait_for_function("window.libre.store.get().render.grid?.columns === 9")
    fine = "window.libre.store.set({prefs: {...window.libre.store.get().prefs, cells: 'fine'}})"
    js(page, fine)  # cell by cell: each handle one cell further out
    js(page, "window.libre.doc.setSelection(['box'])")
    cells = "(w => [w.col, w.row, w.cols, w.rows])(window.libre.doc.widgetById('box'))"
    frames = "new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))"

    # each handle takes the pointer one cell beyond the module's edge; the module then fills
    # more of the grid, so the next handle has room (and stands where the module's edge is)
    steps = [
        ("e", [4, 3, 2, 1]),
        ("s", [4, 3, 2, 2]),
        ("w", [3, 3, 3, 2]),
        ("n", [3, 2, 3, 3]),
        ("se", [3, 2, 4, 4]),
        ("sw", [2, 2, 5, 5]),
        ("ne", [2, 1, 6, 6]),
        ("nw", [1, 0, 7, 7]),
    ]
    for edge, expected in steps:
        col, row, cols, rows = js(page, cells)
        handle = page.locator(f".handle.{edge}").bounding_box()
        start = (handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2)
        beyond_col = col + cols if "e" in edge else col - 1 if "w" in edge else None
        beyond_row = row + rows if "s" in edge else row - 1 if "n" in edge else None
        # three quarters into the cell beyond: its middle is exactly half a cell from the edge,
        # where rounding the pointer's way into cells could go either way
        step_x = cell_center(page, 1, 0)[0] - cell_center(page, 0, 0)[0]
        step_y = cell_center(page, 0, 1)[1] - cell_center(page, 0, 0)[1]
        out_x = step_x / 4 if "e" in edge else -step_x / 4
        out_y = step_y / 4 if "s" in edge else -step_y / 4
        end = (
            cell_center(page, beyond_col, row)[0] + out_x if beyond_col is not None else start[0],
            cell_center(page, col, beyond_row)[1] + out_y if beyond_row is not None else start[1],
        )
        drag(page, start, end, steps=6)
        page.wait_for_function(f"({cells}).join() === {expected!r}.join()", timeout=5000)
        js(page, frames)  # the handles stand where the module's edges are now


def test_a_module_on_a_fine_grid_comes_in_whole_cells_chosen_and_moves_by_the_grid_mode(page):
    """Lumen's grid has 48 × 48 lines: a module from the library was one of its cells, a
    sliver hard to see or click. It comes as large as its kind is meant to be (whole cells as
    an automatic grid has them, as the pictures of the modules show them), chosen, with its
    settings at once; it moves by whole cells, half ones, or each line, as the mode says."""
    js(
        page,
        """window.libre.doc.edit('grid', (theme) => {
          theme.widgets = [];
          theme.grid = { columns: 48, rows: 48, gap: 1, margin: 8 };
        })""",
    )
    page.wait_for_function("window.libre.store.get().render.grid?.columns === 48")
    page.locator('.tile[data-item="module:ring"]').click()
    added = js(page, "window.libre.doc.single()")
    assert added["module"] == "ring" and added["cols"] >= 8 and added["rows"] >= 8
    expect(page.locator(".inspector .inspector-title")).to_have_text("Ring")
    page.wait_for_function(f"window.libre.store.get().render.boxes[{added['id']!r}]?.[2] > 60")
    col = "window.libre.doc.single().col"
    set_mode = "window.libre.store.set({prefs: {...window.libre.store.get().prefs, cells: %r}})"
    moved = {}
    for mode in ("fine", "medium", "coarse"):
        js(page, set_mode % mode)
        before = js(page, col)
        page.keyboard.press("ArrowRight")
        moved[mode] = js(page, col) - before
    assert moved["fine"] == 1 and moved["medium"] > 1 and moved["coarse"] >= 2 * moved["medium"] - 1


def test_a_modules_source_and_detaching_it(page):
    new_theme_from_a_layout(page, "Turing 3.5")
    ring = js(page, "window.libre.doc.widgets().find((w) => w.module === 'ring').id")
    js(page, f"window.libre.doc.setSelection([{ring!r}])")
    page.locator(".inspector .chip", has_text="Disk").click()
    assert widget(page, ring)["source"] == "disk"
    page.locator(".details-button").click()
    page.get_by_role("button", name="Detach into widgets").click()
    page.wait_for_function(f"!window.libre.doc.widgetById({ring!r})")
    assert len(js(page, "window.libre.store.get().selection")) > 2  # its parts, to change each
    page.keyboard.press("Control+z")
    assert widget(page, ring)["source"] == "disk"


def test_a_detach_that_is_answered_after_its_module_went_changes_nothing(page):
    """The server's answer takes a moment; a module removed meanwhile is not there to be
    replaced (the splice at index -1 replaced the last widget of the theme instead)."""
    new_theme_from_a_layout(page, "Turing 3.5")
    ring = js(page, "window.libre.doc.widgets().find((w) => w.module === 'ring').id")
    js(page, f"window.libre.doc.setSelection([{ring!r}])")
    held = []
    page.route("**/api/detach", lambda route, request: held.append(route))
    page.locator(".details-button").click()
    page.get_by_role("button", name="Detach into widgets").click()
    for _ in range(100):  # the request is held
        if held:
            break
        page.wait_for_timeout(20)
    assert held
    js(
        page,
        f"""window.libre.doc.edit(null, (theme) => {{
          theme.widgets = theme.widgets.filter((w) => w.id !== {ring!r});
        }})""",
    )
    ids = js(page, "window.libre.doc.widgets().map((w) => w.id)")
    with page.expect_response("**/api/detach"):
        page.unroute("**/api/detach")  # lets the request through
    page.wait_for_timeout(300)  # (the answer is handled, or ignored, in this moment)
    assert js(page, "window.libre.doc.widgets().map((w) => w.id)") == ids


def test_another_kind_keeps_the_source_it_has_or_takes_the_first_it_offers(page):
    """A big number can show the network, a ring cannot: switched to a ring it does not keep
    "net" (the inspector would show no choice, the panel would draw the processor), it takes
    the ring's first source. One that the new kind has too stays."""
    new_theme_from_a_layout(page, "Turing 3.5")
    ring = js(page, "window.libre.doc.widgets().find((w) => w.module === 'ring').id")
    js(page, f"window.libre.doc.setSelection([{ring!r}])")
    kind = page.get_by_label("Module", exact=True)
    page.locator(".inspector .chip", has_text="Disk").click()
    kind.select_option("stat")
    assert (widget(page, ring)["module"], widget(page, ring)["source"]) == ("stat", "disk")
    page.locator(".inspector .chip", has_text="Network").click()
    assert widget(page, ring)["source"] == "net"
    kind.select_option("ring")
    assert (widget(page, ring)["module"], widget(page, ring)["source"]) == ("ring", "cpu")
    chosen = page.locator(".inspector .chip[aria-pressed='true']")
    expect(chosen).to_have_text("CPU")  # and the inspector shows it
    page.keyboard.press("Control+z")  # kind and source go back together
    assert (widget(page, ring)["module"], widget(page, ring)["source"]) == ("stat", "net")


def test_the_size_popover_shows_all_its_buttons(page):
    """The toolbar's size of a module has the width and the height in a line each: the last
    "+" was cut off by the popover's edge (the German labels are longer still)."""
    new_theme_from_a_layout(page, "Turing 3.5")
    ring = js(page, "window.libre.doc.widgets().find((w) => w.module === 'ring').id")
    js(page, f"window.libre.doc.setSelection([{ring!r}])")
    inside = """(() => {
      const frame = document.querySelector('.popover').getBoundingClientRect();
      return [...document.querySelectorAll('.popover .size-steps .icon-btn')].map((button) => {
        const box = button.getBoundingClientRect();
        return box.left >= frame.left && box.right <= frame.right
          && box.top >= frame.top && box.bottom <= frame.bottom;
      });
    })()"""
    for language in ("en", "de"):
        js(page, f"window.libre.commands.runCommand('language.{language}')")
        page.wait_for_function(f"document.documentElement.lang === '{language}'")
        page.locator(".floating-toolbar .tool-text.mono").click()
        expect(page.locator(".popover .size-steps")).to_be_visible()
        assert js(page, inside) == [True] * 4  # Narrower, Wider, Lower, Taller
        page.keyboard.press("Escape")
        expect(page.locator(".popover")).to_have_count(0)


def test_the_weather_forecast_setting(page):
    new_theme_from_a_layout(page)
    weather = js(page, "window.libre.doc.widgets().find((w) => w.module === 'weather').id")
    js(page, f"window.libre.doc.setSelection([{weather!r}])")
    page.locator(".inspector .chip", has_text="hours").first.click()
    assert widget(page, weather)["forecast"] == "hours"
    page.locator(".details-button").click()  # how far apart the columns are comes with it
    hours = page.get_by_label("Hours between columns")
    hours.fill("2")
    hours.press("Enter")
    assert widget(page, weather)["step"] == 2
    page.locator(".inspector").get_by_label("Back").click()
    page.locator(".inspector .chip").filter(has_text="off").first.click()
    assert widget(page, weather)["forecast"] == "off"


def test_a_list_modules_settings(page):
    new_theme_from_a_layout(page)
    first = js(page, "window.libre.doc.widgets().find(window.libre.doc.isModule).id")
    js(page, f"window.libre.doc.setSelection([{first!r}])")
    kind = page.locator(".inspector select[aria-label='Module']")
    kind.select_option("processes")
    assert widget(page, first)["module"] == "processes"
    page.get_by_role("radio", name="memory").click()
    assert widget(page, first)["sort"] == "memory"
    kind.select_option("values")
    readings = page.locator(".inspector .field", has_text="Readings").locator("textarea")
    assert "cpu.load = CPU" in readings.get_attribute("placeholder")
    readings.fill("cpu.load = CPU\nfan.*")
    page.wait_for_function(
        f"window.libre.doc.widgetById({first!r}).items === 'cpu.load = CPU\\nfan.*'"
    )


def test_a_time_modules_settings(page):
    new_theme_from_a_layout(page)
    first = js(page, "window.libre.doc.widgets().find(window.libre.doc.isModule).id")
    js(page, f"window.libre.doc.setSelection([{first!r}])")
    kind = page.locator(".inspector select[aria-label='Module']")
    kind.select_option("countdown")
    page.get_by_label("Counts down to").fill("12-24")
    page.wait_for_function(f"window.libre.doc.widgetById({first!r}).target === '12-24'")
    kind.select_option("analog")
    page.get_by_label("Time zone").fill("Asia/Tokyo")
    page.wait_for_function(f"window.libre.doc.widgetById({first!r}).timezone === 'Asia/Tokyo'")
    kind.select_option("image")
    page.locator(".details-button").click()
    seconds = page.get_by_label("Seconds per picture")
    seconds.fill("30")
    seconds.press("Enter")
    assert widget(page, first)["seconds"] == 30
    page.locator(".inspector").get_by_label("Back").click()
    kind.select_option("world")
    places = page.locator(".inspector .field", has_text="Places").locator("textarea")
    assert "Asia/Tokyo = Tokyo" in places.get_attribute("placeholder")


def test_the_look_and_a_finish_are_saved_with_the_theme(app_url, isolated_home):
    """The grid and the panel light set from their specs, a bar's finish; all of it in the file."""
    from libre_panel.theme.model import load_theme

    with browser_page(app_url, "look") as page:
        page.wait_for_selector(".look-side .finish-tile")
        grid = page.locator(".look-block", has_text="Grid background")
        grid.locator("[role='switch']").first.click()
        grid.locator("summary").click()
        spacing = grid.get_by_label("Spacing")
        spacing.fill("10")
        spacing.press("Enter")
        assert js(page, "window.libre.doc.theme().look.grid") == {
            "spacing": 10, "major": 5, "color": None, "major_color": None, "alpha": 1,
        }  # fmt: skip
        light = page.locator(".look-block", has_text="Panel light")
        light.locator("[role='switch']").first.click()
        light.locator(".look-chip", has_text="Full hour").click()
        assert js(page, "window.libre.doc.theme().look.light.on_hour") is False
        bar = js(page, "window.libre.doc.widgets().find((w) => w.type === 'bar').id")
        page.locator(".look-scope button", has_text="Bars").click()
        page.locator(".finish-tile", has_text="Light").click()
        assert widget(page, bar)["finish"] == "lit"
        page.keyboard.press("Control+s")  # a built-in theme: asks for a new name
        dialog = page.locator(".dialog")
        dialog.locator("input").fill("my-test")
        dialog.locator("button[type='submit']").click()
        page.wait_for_function("!window.libre.store.get().dirty")
        saved = load_theme(isolated_home / "themes" / "my-test")
        assert saved.look["grid"]["spacing"] == 10 and saved.look["light"]["on_hour"] is False
        assert next(w for w in saved.widgets if w["id"] == bar)["finish"] == "lit"
        grid.locator("[role='switch']").first.click()  # switched off, the block is gone again
        assert "grid" not in js(page, "window.libre.doc.theme().look")


@pytest.fixture
def console_app(isolated_home):
    """The background app with its panel and the notices service on, and the address of its app."""
    from test_autostart import FakeTasks
    from test_service import FakeRegistry, wait_for, write_config

    from libre_panel.autostart import Autostart
    from libre_panel.service import BackgroundApp

    write_config(isolated_home)
    config = isolated_home / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + '\n[services]\nenabled = ["notices"]\n')
    app = BackgroundApp(
        port=0, autostart=Autostart("win32", registry=FakeRegistry(), tasks=FakeTasks())
    )
    url = app.start()
    assert wait_for(lambda: app.plugin_host.shown is not None and "notices" in app.services.running)
    yield app, url
    app.quit()
    app.shutdown()


def open_plugin_page(page, title):
    """A page of the console (or a plugin's) from Design's Pages, in its frame."""
    page.locator(".rail-item", has_text="Pages").click()
    page.locator(".plugin-pages .list-row", has_text=title).click()
    return page.frame_locator(".plugin-frame")


def test_the_control_page_in_the_app(console_app):
    from test_service import wait_for

    from libre_panel.config import load_config

    app, url = console_app
    with browser_page(url) as page:
        frame = open_plugin_page(page, "Control")
        frame.locator("h1", has_text="Control").wait_for()
        assert page.locator(".plugin-bar strong").inner_text() == "Control"

        frame.locator("#live").wait_for(state="visible")  # the picture on the panel
        picture = "document.querySelector('.plugin-frame').contentDocument.querySelector('#live')"
        page.wait_for_function(f"{picture}.naturalWidth > 0")
        frame.locator(".tile .label", has_text="Frame rate").wait_for()
        frame.locator("#sources tr", has_text="psutil").wait_for()
        row = frame.locator("#services tr", has_text="notices")
        row.wait_for()
        assert "running" in row.inner_text()

        frame.get_by_role("button", name="Volume").click()  # a test message
        frame.locator("#probe-result", has_text="Shown on the panel").wait_for()

        frame.locator("#themes select").nth(1).select_option("slate")  # the game mode's theme
        assert wait_for(lambda: getattr(load_config().modes.get("game"), "theme", None) == "slate")
        frame.locator("#themes select").nth(0).select_option("orbit")
        page.wait_for_function(
            "document.querySelector('.plugin-frame').contentDocument"
            ".querySelector('#themes').textContent.includes('Showing now: orbit')",
            timeout=15000,
        )
        assert load_config().theme == "orbit"

        first = app.services.running["notices"][0]
        row.get_by_role("button", name="Restart").click()
        assert wait_for(lambda: app.services.running["notices"][0] is not first)

        frame.locator("#log-box summary").click()  # the log of this run (the tray writes it)
        frame.locator("#log-where").wait_for()

        page.locator(".plugin-bar").get_by_role("button", name="Back").click()
        page.wait_for_selector(".panel-canvas .preview")


def test_the_autopilot_page_in_the_app(console_app):
    from test_service import wait_for

    from libre_panel.config import load_config

    app, url = console_app
    with browser_page(url) as page:
        frame = open_plugin_page(page, "Autopilot")
        frame.locator("h1", has_text="Autopilot").wait_for()
        frame.locator("#rules .note", has_text="No rules").wait_for()
        assert not frame.locator("#enabled").is_checked()

        frame.get_by_role("button", name="Add a rule").click()
        card = frame.locator("#rules .rule")
        card.wait_for()
        name = card.locator("input.name")
        name.fill("Quiet")
        card.locator(".rule-head select").first.select_option("slate")
        card.locator("input[type=checkbox]").first.check()  # a new rule starts switched off
        frame.locator(".condition", has_text="Music plays").locator("input").uncheck()  # no music
        frame.locator("#enabled").check()
        frame.locator("#dirty", has_text="unsaved").wait_for()

        frame.get_by_role("button", name="Save").click()
        # one write: the rules and the switch together
        assert wait_for(lambda: load_config().services.enabled == ["notices", "autopilot"])
        rules = load_config().services.options["autopilot"]["rules"]
        assert rules == [{"name": "Quiet", "theme": "slate", "when": {"music": False}}]
        frame.locator("#dirty", has_text="unsaved").wait_for(state="hidden")
        assert wait_for(lambda: "autopilot" in app.services.running, 15)
        # the service runs: the page says what it does (and the rule holds: no music here)
        frame.locator("#now", has_text="Quiet").wait_for(timeout=20000)
        frame.locator("#signals .chip", has_text="No music").wait_for()
        assert frame.locator("#rules .rule.holds").count() == 1

        card.locator("button[title='Delete the rule']").click()  # and gone again
        frame.get_by_role("button", name="Save").click()
        assert wait_for(
            lambda: not load_config().services.options.get("autopilot", {}).get("rules")
        )


HEALTHY = {
    "fps": 49.8, "target_fps": 50, "streaming": True, "render_ms": 7.4, "render_peak_ms": 19.0,
    "kbps": 251.0, "blocks_per_s": 50.1, "max_depth": 2, "waits": 0, "drops": 0,
    "usb_errors": 0, "panel_restarts": 0, "ffmpeg_restarts": 0, "uptime_s": 120,
}  # fmt: skip

# The Control page's rows (/static/stats.js, which plugins' pages can use too) ...
STATS = """async (stats) => {
  const { describeStats } = await import("/static/stats.js");
  const fill = (text, vars = {}) => text.replace(/\\{(\\w+)\\}/g, (all, name) => vars[name] ?? all);
  const rows = describeStats(stats, fill);
  return Object.fromEntries(rows.map((row) => [row.id, [row.value, row.level]]));
}"""

# ... and Live's tiles (the app's own core/stats.js)
TILES = """async (stats) => {
  const { statTiles } = await import("/app/core/stats.js");
  return Object.fromEntries(statTiles(stats).map((tile) => [tile.id, [tile.value, tile.level]]));
}"""


def test_the_control_pages_numbers_are_judged_against_what_the_panel_should_manage(page):
    def rows(**changes):
        return page.evaluate(STATS, {**HEALTHY, **changes})

    healthy = rows()
    assert healthy == {
        "fps": ["49.8 fps", ""], "render": ["7.4 ms", ""], "data": ["251 KB/s", ""],
        "video": ["50.1 blocks/s", ""], "faults": ["none", ""],
    }  # fmt: skip
    # video mode: under nine tenths of the target is a warning, under half of it bad
    assert rows(fps=40.0)["fps"] == ["40 fps", "warn"]
    assert rows(fps=20.0)["fps"][1] == "bad"
    # (not in video mode a frame is drawn only when something moves)
    assert rows(streaming=False, fps=2.0, target_fps=10)["fps"] == ["2 fps", ""]
    assert rows(render_ms=25.0)["render"][1] == "warn"  # more than the 20 ms a frame has
    assert rows(render_ms=25.0, streaming=False)["render"][1] == ""
    assert rows(drops=1)["video"][1] == "warn" and rows(max_depth=4)["video"][1] == "warn"
    assert rows(usb_errors=1, ffmpeg_restarts=2)["faults"] == ["3 so far", "warn"]
    plain = rows(kbps=None, blocks_per_s=None, max_depth=None, waits=None, drops=None)
    assert set(plain) == {"fps", "render", "faults"}  # what nobody counted is not shown
    assert page.evaluate(STATS, {}) == {}  # no numbers: nothing to show


def test_lives_numbers_are_judged_against_what_the_panel_should_manage(page):
    def tiles(**changes):
        return page.evaluate(TILES, {**HEALTHY, **changes})

    assert tiles() == {
        "fps": ["49.8", ""], "data": ["251", ""], "draw": ["7.4", ""], "queue": ["2", ""],
        "faults": ["0", "ok"],
    }  # fmt: skip
    # video mode: under nine tenths of the target is a warning, under half of it bad
    assert tiles(fps=40.0)["fps"] == ["40.0", "warn"]
    assert tiles(fps=20.0)["fps"][1] == "bad"
    # (not in video mode a frame is drawn only when something moves)
    assert tiles(streaming=False, fps=2.0, target_fps=10)["fps"] == ["2.0", ""]
    assert tiles(render_ms=25.0)["draw"][1] == "warn"  # more than the 20 ms a frame has
    assert tiles(render_ms=25.0, streaming=False)["draw"][1] == ""
    assert tiles(drops=1)["queue"][1] == "warn" and tiles(max_depth=4)["queue"][1] == "warn"
    assert tiles(usb_errors=1, ffmpeg_restarts=2)["faults"] == ["3", "warn"]
    plain = tiles(kbps=None, blocks_per_s=None, max_depth=None, waits=None, drops=None)
    assert plain["data"][0] == "–" and plain["queue"][0] == "–"  # what nobody counted
    assert page.evaluate(TILES, {}) == {
        "fps": ["–", ""], "data": ["–", ""], "draw": ["–", ""], "queue": ["–", ""],
        "faults": ["–", ""],
    }  # fmt: skip


# the colours a console or plugin page has (static/editor.css), and the app's (app.css) they are
CONSOLE_COLOURS = {
    "--bg": "--bg",
    "--panel": "--surface",
    "--panel-2": "--surface-2",
    "--panel-3": "--surface-3",
    "--line": "--line-4",
    "--text": "--text",
    "--muted": "--muted",
    "--dim": "--dim",
    "--accent": "--accent",
    "--accent-ink": "--accent-ink",
    "--ok": "--ok",
    "--warn": "--warn",
    "--error": "--danger",
}


def test_the_console_pages_have_the_apps_colours_and_typeface(app_url):
    """The Control and Autopilot pages, and the pages plugins bring, show inside the app: they
    were a shade off its colours (cyan for its teal, another amber) and had a typeface of the
    system's."""
    read = """names => names.map((name) => {
      const value = getComputedStyle(document.documentElement).getPropertyValue(name);
      return value.trim().toLowerCase();
    })"""
    with browser_page(app_url, "home", allow=PICTURE) as page:
        app = page.evaluate(read, list(CONSOLE_COLOURS.values()))
        page.goto(f"{app_url}plugins/control/")
        page.wait_for_selector("body.plugin-page h1")
        console = page.evaluate(read, list(CONSOLE_COLOURS))
        assert dict(zip(CONSOLE_COLOURS, console, strict=True)) == dict(
            zip(CONSOLE_COLOURS, app, strict=True)
        )
        assert js(page, "getComputedStyle(document.body).fontFamily").startswith("Barlow, ")
        page.wait_for_function("document.fonts.check('14px Barlow')")  # and it is the app's file


def test_the_language_switch_keeps_unsaved_work(page_plugin, page, isolated_home):
    from libre_panel.config import load_config

    js(page, "window.libre.doc.edit('t', (theme) => { theme.name = 'Work in progress'; })")
    assert js(page, "window.libre.store.get().dirty") is True
    calendar = page.locator(".tile[data-item='module:date'] img")
    calendar.wait_for()  # its picture is drawn, in English
    english = calendar.get_attribute("src")
    shown = js(page, "window.libre.store.get().render.png")
    page.keyboard.press("Control+k")
    page.locator(".palette input").fill("deutsch")
    page.keyboard.press("Enter")
    page.wait_for_function("document.documentElement.lang === 'de'")
    assert load_config().language == "de"
    assert js(page, "window.libre.doc.theme().name") == "Work in progress"
    assert js(page, "window.libre.store.get().dirty") is True  # still unsaved, nothing lost
    assert page.locator(".btn-apply").inner_text() == "Aufs Panel"
    assert page.locator(".tile[data-item='widget:rect'] .tile-name").inner_text() == "Fläche"
    # what the server translates came again: a building block's name, a plugin page's title
    assert page.locator(".tile[data-item='preset:cpu-card'] .tile-name").inner_text() == "CPU-Karte"
    titles = js(page, "window.libre.store.get().plugins.map((p) => p.title)")
    assert "Analyse" in titles and "Steuerung" in titles  # also the built-in Control page
    # the pictures hold dates and weather: the panel's preview and the library's are drawn again
    page.wait_for_function("(old) => window.libre.store.get().render.png !== old", arg=shown)
    page.wait_for_function(
        "(old) => document.querySelector(\".tile[data-item='module:date'] img\").src !== old",
        arg=english,
    )
    js(page, "window.libre.doc.setSelection(['clock'])")
    assert page.locator(".inspector .inspector-title").inner_text() == "Uhr"
    page.keyboard.press("Control+k")
    page.locator(".palette input").fill("english")
    page.keyboard.press("Enter")
    page.wait_for_function("document.documentElement.lang === 'en'")
    assert load_config().language == "en"
    assert page.locator(".btn-apply").inner_text() == "To the panel"


# -- Settings: each section's main path, against the real server and config.toml ----------------


def settings_config():
    from libre_panel.config import load_config

    return load_config()


def waits_for(page, predicate, timeout=10.0):
    """Until the predicate holds (the app writes config.toml a moment after a click)."""
    from test_service import wait_for

    assert wait_for(predicate, timeout), "config.toml did not change as expected"


def game_option(name):
    """An option of the game service as config.toml has it now (None: it is not there)."""
    return settings_config().services.options.get("game", {}).get(name)


def radio(page, group, name):
    return page.locator(f"[role='radiogroup'][aria-label='{group}'] [role='radio']", has_text=name)


def settings_app(isolated_home, autostart=None):
    """The background app with a panel that writes a PNG file, as the tray runs it."""
    from test_autostart import FakeTasks
    from test_service import FakeRegistry, write_config

    from libre_panel.autostart import Autostart
    from libre_panel.service import BackgroundApp

    write_config(isolated_home)
    auto = autostart or Autostart("win32", registry=FakeRegistry(), tasks=FakeTasks())
    return BackgroundApp(port=0, autostart=auto, autostart_command=["/opt/libre-panel", "tray"])


def test_the_settings_open_under_the_strict_csp(app_url):
    """No section needs what the app's CSP forbids (inline scripts, inline styles)."""
    with browser_page(app_url, "settings/panel", strict_csp=True) as page:
        for section in ("panel", "sensors", "services", "weather", "system", "about"):
            page.goto(f"{app_url}#/settings/{section}")
            page.wait_for_selector(".settings-main .settings-card")
            page.wait_for_timeout(200)


def test_settings_panel(app_url, isolated_home, monkeypatch):
    """The panel found, the video mode (and its clip name, written only when typed), the
    frame rate, brightness, upside down, another panel, and Advanced."""
    from test_editor_settings import lacking_ffmpeg, panel_on_the_bus

    panel_on_the_bus(monkeypatch, "turing-9.2-usb")
    lacking_ffmpeg(monkeypatch)
    with browser_page(app_url, "settings/panel") as page:
        assert page.locator(".panel-found-name").inner_text() == "Turing 9.2″"
        detail = page.locator(".panel-found-detail").inner_text()
        assert "USB · 1920 × 480" in detail and "Connected" in detail
        status = page.locator("#main .settings-row-status").first
        assert status.inner_text() == (
            "Off for now. Missing: the name of the standby clip, the video program ffmpeg."
        )
        # the clip's name is never made up: the placeholder is no value, and nothing is
        # written until the person types it
        clip = page.get_by_label("Name of the standby clip")
        assert clip.get_attribute("placeholder") == "usr/data/standby.h264"
        assert clip.input_value() == ""
        clip.focus()
        clip.press("Tab")
        page.wait_for_timeout(200)
        assert not (isolated_home / "config.toml").exists()
        lacking_ffmpeg(monkeypatch, found=True)  # ffmpeg is installed meanwhile
        clip.fill("usr/data/standby.h264")
        clip.press("Enter")
        waits_for(
            page, lambda: settings_config().device.video.local_clip == "usr/data/standby.h264"
        )
        assert settings_config().device.video.clip_asked is True  # typed here: Start need not ask
        video = page.locator("#main .settings-row-status").first
        playwright.expect(video).to_contain_text("On, 50 fps")
        radio(page, "Frames per second", "30").click()
        waits_for(page, lambda: settings_config().device.video.fps == 30)
        playwright.expect(video).to_contain_text("On, 30 fps")
        radio(page, "Smooth motion", "Off").click()
        waits_for(page, lambda: settings_config().device.video.mode == "off")
        playwright.expect(video).to_contain_text("single pictures")
        assert page.get_by_label("Name of the standby clip").count() == 0  # not needed now
        radio(page, "Smooth motion", "Automatic").click()
        waits_for(page, lambda: settings_config().device.video.mode == "auto")

        slider = page.get_by_role("slider", name="Brightness")
        slider.focus()
        slider.press("ArrowLeft")
        slider.press("ArrowLeft")
        waits_for(page, lambda: settings_config().device.brightness == 50)
        page.get_by_role("switch", name="Upside down").click()
        waits_for(page, lambda: settings_config().device.rotate == 180)
        page.get_by_role("switch", name="Upside down").click()
        waits_for(page, lambda: settings_config().device.rotate == 0)

        # another panel, by hand; the one on the bus is still told as it is
        page.get_by_role("button", name="Another panel").click()
        page.locator(".popover [role='option']", has_text="Turing 8.8").last.click()
        waits_for(page, lambda: settings_config().device.model == "turing-8.8-usb")
        playwright.expect(page.locator(".panel-found-name")).to_contain_text("8.8")
        playwright.expect(page.locator(".panel-found")).to_contain_text(
            "Another panel is connected: Turing 9.2″"
        )
        page.get_by_role("button", name="Another panel").click()
        page.locator(".popover [role='option']", has_text="Detect automatically").click()
        waits_for(page, lambda: settings_config().device.model == "auto")

        # Advanced: the driver and the files it names
        page.locator(".settings-disclosure", has_text="Advanced").click()
        page.get_by_label("Where the pictures go").select_option("virtual")
        waits_for(page, lambda: settings_config().device.driver == "virtual")
        output = page.get_by_label("Picture file")
        assert output.input_value() == "libre-panel-frame.png"
        output.fill("shown.png")
        output.press("Enter")
        waits_for(page, lambda: settings_config().device.output == "shown.png")
        port = page.get_by_label("Port of a serial panel")
        port.fill("COM5")
        port.press("Enter")
        waits_for(page, lambda: settings_config().device.port == "COM5")
    text = (isolated_home / "config.toml").read_text(encoding="utf-8")
    assert "# Libre Panel configuration" in text  # the file is still the person's


def test_settings_panel_without_a_panel(app_url, monkeypatch):
    from test_editor_settings import panel_on_the_bus

    panel_on_the_bus(monkeypatch)
    with browser_page(app_url, "settings/panel") as page:
        assert page.locator(".panel-found-name").inner_text() == "No panel found"
        assert "Plug it in" in page.locator(".panel-found-detail").inner_text()
        assert page.locator(".panel-outline.none").count() == 1
        status = page.locator("#main .settings-row-status").first.inner_text()
        assert status == "Starts when a panel it runs on is connected."


def test_settings_panel_says_why_it_waits(app_url):
    """The running app knows why no panel is in use (no permission, a library missing): said."""
    import json

    found = {
        "configured": "auto",
        "found": None,
        "model": None,
        "running": {"state": "waiting", "detail": "cannot open the USB device: access denied"},
        "video": {"mode": "auto", "fps": 50, "on": False, "usb": True, "gaps": []},
    }

    def answer(route, request):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(found))

    with browser_page(app_url, "settings/panel", routes={"**/api/panel": answer}) as page:
        why = page.locator(".panel-found-why")
        playwright.expect(why).to_contain_text("Waiting for the panel: cannot open the USB device")
        assert page.locator(".panel-found-name").inner_text() == "No panel found"


def test_settings_sensors(app_url, isolated_home):
    """The sources in their order and on or off, how often they are read."""
    from libre_panel.config import default_providers

    with browser_page(app_url, "settings/sensors") as page:
        page.wait_for_selector(".source-row")
        rows = page.locator(".source-row:not(.unused)")
        assert [rows.nth(i).get_attribute("data-source") for i in range(rows.count())] == (
            default_providers()
        )  # in the order they are asked
        assert page.locator(".source-row.unused").count() == 0  # folded away
        page.locator(".sources-more").click()
        assert page.locator(".source-row.unused[data-source='demo']").count() == 1
        assert page.locator(".source-row[data-source='media']").count() == 0  # starts by itself
        assert page.locator(".note.warn").count() == 0
        page.locator(".source-row[data-source='audio'] [role='switch']").click()
        waits_for(page, lambda: "audio" not in settings_config().sensors.providers)
        # the sources are made when the panel starts: said, with nothing to restart here
        note = page.locator(".note.warn")
        assert "after Libre Panel is started again" in note.inner_text()
        assert note.locator("button").count() == 0
        page.locator(".source-row[data-source='audio'] [role='switch']").click()
        waits_for(page, lambda: "audio" in settings_config().sensors.providers)
        # a source that is switched on goes before psutil, which fills in the rest
        usual = default_providers()
        others = [name for name in usual if name not in ("audio", "psutil")]
        waits_for(page, lambda: settings_config().sensors.providers == [*others, "audio", "psutil"])
        page.get_by_role("button", name="Ask Fast load later").click()
        waits_for(page, lambda: settings_config().sensors.providers[:2] == [others[1], "load"])
        page.get_by_role("button", name="Back to the usual").click()
        waits_for(page, lambda: settings_config().sensors.providers == usual)
        playwright.expect(page.get_by_role("button", name="Back to the usual")).to_have_count(0)
        # psutil alone would be read as the usual list again (config.py), so that switch is
        # not offered: quick clicks follow each other, and the last one is refused
        for name in ("audio", "linux", "demo"):
            page.locator(f".source-row[data-source='{name}'] [role='switch']").click()
        waits_for(page, lambda: settings_config().sensors.providers == ["load", "demo", "psutil"])
        page.locator(".source-row[data-source='demo'] [role='switch']").click()
        waits_for(page, lambda: settings_config().sensors.providers == ["load", "psutil"])
        playwright.expect(
            page.locator(".source-row[data-source='load'] [role='switch']")
        ).to_be_disabled()
        playwright.expect(
            page.locator(".source-row[data-source='psutil'] [role='switch']")
        ).to_be_enabled()
        # how often they are read (under Advanced): empty is the theme's choice
        page.locator(".settings-disclosure", has_text="Advanced").click()
        interval = page.get_by_label("Reading interval")
        assert interval.input_value() == "" and interval.get_attribute("placeholder") == "Automatic"
        interval.fill("500")
        interval.press("Enter")
        waits_for(page, lambda: settings_config().refresh_ms == 500)
        playwright.expect(page.locator("#main")).to_contain_text("Every 0.5 s")
        page.locator(".row-action .link-btn", has_text="Automatic").click()
        waits_for(page, lambda: settings_config().refresh_ms is None)
    assert "refresh_ms = 500" not in (isolated_home / "config.toml").read_text(encoding="utf-8")


def test_settings_sensors_names_the_linux_source(app_url, isolated_home):
    """The source of Linux is called and explained like the others, and is in use only where the
    usual list has it (on other systems it waits under "Not in use" and says it does not work)."""
    from libre_panel.config import default_providers

    with browser_page(app_url, "settings/sensors") as page:
        page.wait_for_selector(".source-row")
        if page.locator(".sources-more").count():  # (the sources not in use are folded away)
            page.locator(".sources-more").click()
        row = page.locator(".source-row[data-source='linux']")
        playwright.expect(row).to_contain_text("Linux sensors")
        playwright.expect(row).to_contain_text("read from the kernel and from NVIDIA's driver")
        in_use = "linux" in default_providers()
        assert ("unused" not in (row.get_attribute("class") or "")) is in_use
        if not in_use:
            playwright.expect(row).to_contain_text("Not on this system")


def test_settings_sensors_of_the_running_app(isolated_home):
    """What each source reads right now, and starting the panel again for a change to take."""
    from test_service import wait_for

    app = settings_app(isolated_home)
    url = app.start()
    try:
        assert wait_for(lambda: app.panel.state()["state"] == "showing")
        with browser_page(url, "settings/sensors") as page:
            row = page.locator(".source-row[data-source='psutil']")
            page.wait_for_function(
                "document.querySelector(\"[data-source='psutil'] .status\") !== null"
            )
            assert "readings" in row.locator(".status").inner_text()
            loop = app.panel.status
            page.locator(".source-row[data-source='audio'] [role='switch']").click()
            waits_for(page, lambda: "audio" not in settings_config().sensors.providers)
            note = page.locator(".note.warn")
            assert "after a restart of the panel" in note.inner_text()
            note.get_by_role("button", name="Restart the panel").click()
            assert wait_for(lambda: app.panel.status is not loop and app.panel.running)
            page.wait_for_function("document.querySelector('.note.warn') === null")
    finally:
        app.quit()
        app.shutdown()


def test_settings_services(app_url, isolated_home):
    """A service on or off, and its options by the type each declares."""
    with browser_page(app_url, "settings/services") as page:
        page.wait_for_selector(".settings-card.service")
        cards = page.locator(".settings-card.service")
        assert [cards.nth(i).get_attribute("data-service") for i in range(cards.count())] == [
            "game", "memory", "notices", "autopilot", "devices", "cooling", "downloads",
        ]  # fmt: skip
        assert page.locator("[role='switch'][aria-checked='true']").count() == 0  # off until asked
        game = page.locator("[data-service='game']")
        game.get_by_role("switch", name="Game mode", exact=True).click()
        waits_for(page, lambda: settings_config().services.enabled == ["game"])
        game.locator(".settings-disclosure").click()
        poll = game.locator("[data-option='poll_s'] input")
        assert poll.input_value() == "2"  # the service's own default, until it is changed
        poll.fill("1,5")
        poll.press("Enter")
        waits_for(page, lambda: game_option("poll_s") == 1.5)
        game.locator("[data-option='steam'] [role='switch']").click()
        waits_for(page, lambda: game_option("steam") is False)
        programs = game.locator("[data-option='programs'] input")
        programs.fill("emulator.exe = Retro")
        programs.press("Enter")
        programs.fill("othergame.exe")
        programs.press("Enter")
        waits_for(
            page,
            lambda: game_option("programs") == ["emulator.exe = Retro", "othergame.exe"],
        )
        game.get_by_role("button", name="Remove othergame.exe").click()
        waits_for(
            page,
            lambda: game_option("programs") == ["emulator.exe = Retro"],
        )
        # back to the default takes the setting out of config.toml
        game.locator("[data-option='poll_s'] .link-btn").click()
        waits_for(page, lambda: game_option("poll_s") is None)
        playwright.expect(game.locator("[data-option='poll_s'] .link-btn")).to_have_count(0)
        game.get_by_role("switch", name="Game mode", exact=True).click()
        waits_for(page, lambda: settings_config().services.enabled == [])
        # a service whose rules have a page of their own
        auto = page.locator("[data-service='autopilot']")
        auto.locator(".settings-disclosure").click()
        assert auto.locator("[data-option='rules']").inner_text().count("Edit") == 1
        auto.locator("[data-option='rules'] button").click()
        page.wait_for_function("location.hash === '#/plugin/autopilot'")


def game_theme():
    """The theme game mode shows, as config.toml names it ("" for the normal one)."""
    mode = settings_config().modes.get("game")
    return mode.theme if mode else ""


def test_the_game_screen_is_chosen_beside_game_mode(app_url, isolated_home):
    """Which theme game mode shows was only on the pages of Design, where it was not found:
    it is chosen on game mode's card too."""
    with browser_page(app_url, "settings/services", allow=PICTURE) as page:
        game = page.locator("[data-service='game']")
        choice = game.locator(".game-screen select")
        playwright.expect(choice).to_have_value("")  # the normal theme, until one is chosen
        choice.select_option("almanac")
        waits_for(page, lambda: game_theme() == "almanac")
        choice.select_option("")
        waits_for(page, lambda: not game_theme())


def test_settings_services_of_the_running_app(isolated_home):
    """Each service's state as the running app tells it, and Restart."""
    from test_service import wait_for

    app = settings_app(isolated_home)
    url = app.start()
    try:
        assert wait_for(lambda: app.panel.state()["state"] == "showing")
        with browser_page(url, "settings/services") as page:
            notices = page.locator("[data-service='notices']")
            notices.get_by_role("switch", name="Notices", exact=True).click()
            assert wait_for(lambda: app.services.state.get("notices") == "running")
            page.wait_for_function(
                "document.querySelector(\"[data-service='notices'] .status.ok\") !== null"
            )
            assert "Running" in notices.locator(".status").inner_text()
            first = app.services.running["notices"][0]
            notices.get_by_role("button", name="Restart").click()
            assert wait_for(lambda: app.services.running["notices"][0] is not first)
            # an option changes: the service starts again by itself, with it
            notices.locator(".settings-disclosure").click()
            notices.locator("[data-option='music'] [role='switch']").click()
            assert wait_for(lambda: app.services.running["notices"][0].options["music"] is False)
            notices.get_by_role("switch", name="Notices", exact=True).click()
            assert wait_for(lambda: "notices" not in app.services.running)
            page.wait_for_function(
                "document.querySelector(\"[data-service='notices'] .status\") === null"
            )
    finally:
        app.quit()
        app.shutdown()


def test_settings_weather_and_place(app_url, isolated_home, monkeypatch):
    """A place by its name, the weather on and off, units and interval; the place is kept
    to about a kilometre and the weather needs one."""
    from libre_panel.weather import open_meteo

    asked = []

    def geocode(name, count=5, language="en"):
        asked.append(name)
        return [
            {"name": "Springfield", "region": "Region", "country": "Country",
             "latitude": 1.234567, "longitude": 2.345678},
            {"name": "Springfield", "region": "", "country": "Elsewhere",
             "latitude": -3.5, "longitude": 4.75},
        ]  # fmt: skip

    monkeypatch.setattr(open_meteo, "geocode", geocode)
    with browser_page(app_url, "settings/weather") as page:
        toggle = page.get_by_role("switch", name="Weather")
        assert toggle.is_disabled()  # no place yet
        assert "Choose a place first" in page.locator("#main").inner_text()
        assert asked == []  # nothing goes anywhere before the person asks
        search = page.get_by_label("Name of a place")
        assert page.get_by_role("button", name="Search", exact=True).is_disabled()
        search.fill("Spring")
        search.press("Enter")
        places = page.locator(".places li")
        page.wait_for_selector(".places li")
        assert places.count() == 2 and asked == ["Spring"]
        assert "1.23, 2.35" in places.first.inner_text()
        places.first.locator("button").click()
        waits_for(  # the place is one write; it does not switch the weather on
            page,
            lambda: (
                (settings_config().weather.latitude, settings_config().weather.longitude)
                == (1.23, 2.35)
                and settings_config().weather.place == "Springfield, Region, Country"
                and not settings_config().weather.enabled
            ),
        )
        playwright.expect(page.locator("#place")).to_contain_text("Springfield, Region, Country")
        playwright.expect(page.locator(".places")).to_have_count(0)
        toggle.click()
        waits_for(page, lambda: settings_config().weather.enabled)
        radio(page, "Units", "°F").click()
        waits_for(page, lambda: settings_config().weather.units == "imperial")
        radio(page, "Update", "30").click()
        waits_for(page, lambda: settings_config().weather.update_minutes == 30)
        assert "after Libre Panel is started again" in page.locator(".note.warn").inner_text()
        # taking the place away switches the weather off with it, after a question
        page.locator("#place .link-btn", has_text="Remove").click()
        page.locator(".dialog .btn-danger").click()
        waits_for(
            page,
            lambda: (
                settings_config().weather.latitude is None
                and settings_config().weather.place == ""
                and not settings_config().weather.enabled
                and settings_config().weather.units == "imperial"
            ),
        )
        playwright.expect(toggle).to_be_disabled()


def test_settings_weather_search_fails_politely(app_url, monkeypatch):
    from libre_panel.weather import open_meteo

    def offline(name, count=5, language="en"):
        raise open_meteo.WeatherError("timed out")

    monkeypatch.setattr(open_meteo, "geocode", offline)
    with browser_page(app_url, "settings/weather", allow=("/api/location",)) as page:
        page.get_by_label("Name of a place").fill("Nowhere")
        page.get_by_role("button", name="Search", exact=True).click()
        page.wait_for_selector(".note.error")
        assert "timed out" in page.locator(".note.error").inner_text()
        assert page.get_by_role(
            "button", name="Search", exact=True
        ).is_enabled()  # it can be tried again


def test_settings_start_and_language(app_url, isolated_home):
    """The language switches the whole app, the tray and the panel (config.toml keeps it)."""
    with browser_page(app_url, "settings/system") as page:
        assert page.locator("#settings-title").inner_text() == "Start and system"
        language = page.get_by_role("combobox", name="Language")
        expect_options = ["System", "Deutsch", "English", "Español", "Français", "Polski",
                          "Português (Brasil)"]  # fmt: skip
        assert language.locator("option").all_inner_texts() == expect_options
        language.select_option("de")
        waits_for(page, lambda: settings_config().language == "de")
        playwright.expect(page.locator("#settings-title")).to_have_text("Start und System")
        nav = page.locator(".settings-nav button")
        assert nav.first.inner_text().startswith("Übersicht") and nav.nth(1).inner_text() == "Panel"
        playwright.expect(page.get_by_role("combobox", name="Sprache")).to_have_value("de")
        page.locator(".settings-nav button", has_text="Spielmodus und mehr").click()
        page.wait_for_selector("[data-service='game']")
        assert "Spielmodus" in page.locator("[data-service='game']").inner_text()
        page.goto(f"{app_url}#/settings/panel")
        page.wait_for_selector(".panel-found")
        text = page.locator("#main").inner_text()
        assert "Flüssige Bewegung" in text and "Bilder pro Sekunde" in text
        assert "Auf dem Kopf" in text
        page.goto(f"{app_url}#/settings/system")
        page.get_by_role("combobox", name="Sprache").select_option("auto")
        waits_for(page, lambda: settings_config().language == "auto")
        page.get_by_role("combobox", name="Language").select_option("en")
        waits_for(page, lambda: settings_config().language == "en")


def test_settings_start_with_the_system(isolated_home):
    """Starting with the system is what the command line's autostart does."""
    from libre_panel.autostart import Autostart

    home = isolated_home.parent / "linux-home"
    autostart = Autostart("linux", home=home, environ={})
    app = settings_app(isolated_home, autostart)
    url = app.start()
    try:
        with browser_page(url, "settings/system") as page:
            toggle = page.get_by_role("switch", name="Start when you log in")
            assert toggle.get_attribute("aria-checked") == "false"
            assert page.get_by_role("switch", name="With administrator rights").count() == 0
            toggle.click()
            waits_for(page, lambda: autostart.is_enabled())
            page.wait_for_function(
                "document.querySelector(\"[aria-label='Start when you log in']\")"
                ".getAttribute('aria-checked') === 'true'"
            )
            assert "libre-panel.desktop" in page.locator("#main").inner_text()
            toggle.click()
            waits_for(page, lambda: not autostart.is_enabled())
    finally:
        app.quit()
        app.shutdown()


def test_settings_the_highest_rights_only_when_asked_and_by_an_administrator(isolated_home):
    from test_autostart import FakeRegistry, FakeTasks

    from libre_panel.autostart import Autostart

    registry, tasks = FakeRegistry(), FakeTasks()
    autostart = Autostart(  # (the program settings_app gives its autostart command)
        "win32", registry=registry, tasks=tasks, admin=lambda: False, program="/opt/libre-panel"
    )
    app = settings_app(isolated_home, autostart)
    url = app.start()
    try:
        with browser_page(url, "settings/system") as page:
            assert (
                page.get_by_role("switch", name="Start with Windows").get_attribute("aria-checked")
                == "false"
            )
            rights = page.get_by_role("switch", name="With administrator rights")
            assert rights.get_attribute("aria-checked") == "false"  # never the default
            assert "temperature and power of the processor" in page.locator("#main").inner_text()
            assert "needs Libre Panel to run as administrator" in page.locator("#main").inner_text()
            rights.click()  # not an administrator: told what to do, nothing is set up
            dialog = page.locator(".dialog")
            assert "Run as administrator" in dialog.inner_text()
            assert (
                "libre-panel autostart enable --elevated" in dialog.locator(".command").inner_text()
            )
            dialog.get_by_role("button", name="Close").click()
            assert not tasks.tasks and registry.get("Libre Panel") is None
            autostart._admin = lambda: True  # started as an administrator
            page.goto(f"{url}#/settings/panel")
            page.goto(f"{url}#/settings/system")
            page.wait_for_selector("[role='switch'][aria-label='With administrator rights']")
            rights.click()
            dialog = page.locator(".dialog")
            assert "highest rights at every login" in dialog.inner_text()
            dialog.get_by_role("button", name="Cancel").click()
            assert not tasks.tasks  # a question first
            rights.click()
            page.locator(".dialog .btn-primary").click()
            waits_for(page, lambda: "Libre Panel" in tasks.tasks)
            page.wait_for_function(
                "document.querySelector(\"[aria-label='With administrator rights']\")"
                ".getAttribute('aria-checked') === 'true'"
            )
    finally:
        app.quit()
        app.shutdown()


def test_settings_about(app_url, isolated_home, monkeypatch):
    from libre_panel import __version__, tray

    opened = []
    monkeypatch.setattr(tray, "open_path", opened.append)
    with browser_page(app_url, "settings/about") as page:
        assert f"Libre Panel {__version__}" in page.locator("#main").inner_text()
        assert "GPL-3.0-or-later" in page.locator("#main").inner_text()
        links = page.locator("#main a.btn")
        hrefs = [links.nth(i).get_attribute("href") for i in range(links.count())]
        assert "https://github.com/syntensa/libre-panel" in hrefs
        assert any("template=panel_support.yml" in href for href in hrefs)  # (filled in)
        # what is connected the app lists itself; the panel test is in Live → Diagnose, and the
        # doctor runs in a terminal too
        playwright.expect(page.locator('[data-open="panel-test"]')).to_be_visible()
        page.locator(".report-terminal summary").click()
        commands = page.locator(".command-row .command")
        assert [commands.nth(i).inner_text().split()[-1] for i in range(commands.count())] == [
            "doctor"
        ]
        playwright.expect(page.locator(".report-list")).to_contain_text("Panel found:")
        assert str(isolated_home) in page.locator("[data-folder='config']").inner_text()
        page.locator("[data-folder='themes'] button").click()
        page.locator("[data-folder='logs'] button").click()
        waits_for(page, lambda: len(opened) == 2)
        assert opened == [isolated_home / "themes", isolated_home / "logs"]
        # the notices of the software it is built on: shown in the app, from NOTICE.md
        assert page.locator(".notices").count() == 0
        page.locator(".settings-disclosure", has_text="Notices and credits").click()
        notices = page.locator(".notices")
        assert "turing-smart-screen-python" in notices.inner_text()
        assert notices.locator("table").count() >= 1 and notices.locator("a").count() > 3
        assert notices.locator("a").first.get_attribute("rel") == "noopener noreferrer"


def test_settings_say_when_config_toml_has_a_mistake(app_url, isolated_home):
    (isolated_home).mkdir(parents=True)
    (isolated_home / "config.toml").write_text("this is not toml", encoding="utf-8")
    allow = ("/api/settings", "/api/panel", "/api/system", "/api/pages")
    with browser_page(app_url, "settings/panel", allow=allow) as page:
        page.wait_for_selector(".note.error")
        assert "config.toml has a mistake in it." in page.locator(".note.error").inner_text()
        assert page.locator(".settings-card").count() == 0  # nothing to change meanwhile
        (isolated_home / "config.toml").write_text("fps = 10\n", encoding="utf-8")
        page.get_by_role("button", name="Try again").click()
        expect(page.locator(".note.error")).to_have_count(0)  # (the panel is found before)
        page.wait_for_selector(".panel-found")


def test_settings_say_when_the_app_does_not_answer_and_not_that_config_toml_is_wrong(app_url):
    """No answer at all is not a mistake in config.toml: the note says what is the matter."""
    with browser_page(app_url, "home", allow=("/api/settings",)) as page:
        page.route("**/api/settings", lambda route, request: route.abort())
        page.goto(f"{app_url}#/settings/panel")
        page.wait_for_selector(".note.error")
        note = page.locator(".note.error").inner_text()
        assert "Libre Panel does not answer." in note
        assert "config.toml" not in note  # (the browser's own "Failed to fetch" is not shown)
        page.unroute("**/api/settings")
        page.get_by_role("button", name="Try again").click()
        expect(page.locator(".note.error")).to_have_count(0)  # (the panel is found before)
        page.wait_for_selector(".panel-found")


def test_the_app_says_when_the_server_stops_answering_and_when_it_is_back(app_url):
    """A background app that quit (from its tray menu, say) leaves the page behind with the
    last state it knew: a bar says so, the chip is not "Showing" any more, and both go when
    the app answers again."""
    with browser_page(app_url, "design", allow=("/api/app",)) as page:
        assert page.locator(".offline-bar").count() == 0
        page.route("**/api/app", lambda route, request: route.abort())
        bar = page.locator(".offline-bar")
        expect(bar).to_be_visible(timeout=10000)  # the next of the app's polls every two seconds
        assert "Libre Panel does not answer." in bar.inner_text()
        expect(page.locator(".panel-chip.gone")).to_contain_text("not answering")
        page.unroute("**/api/app")
        expect(bar).to_have_count(0, timeout=10000)
        expect(page.locator(".panel-chip.preview")).to_have_count(1)


def test_a_page_of_an_earlier_run_says_what_to_do_not_that_it_will_recover(app_url):
    """A tab that stayed open while Libre Panel was started again has the old run's token: the
    server answers every request with 403 and no waiting helps. The bar says what to do, with
    the server's own sentence, instead of promising that the page tries again by itself."""
    import json

    refusal = {"error": "Access token missing or wrong. Open Libre Panel from its tray icon."}

    def refuse(route, request):
        route.fulfill(status=403, content_type="application/json", body=json.dumps(refusal))

    with browser_page(app_url, "design", allow=("/api/app",)) as page:
        page.route("**/api/app", refuse)
        bar = page.locator(".offline-bar")
        expect(bar).to_be_visible(timeout=10000)
        assert bar.inner_text().strip() == refusal["error"]
        assert "tries again" not in bar.inner_text()
        page.unroute("**/api/app")  # (an answer is an answer: the bar goes)
        expect(bar).to_have_count(0, timeout=10000)


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


def test_the_fans_are_named_in_settings(app_url):
    """Each fan by the name it goes by, its key and its speed now; a name of one's own, and
    back to the detected one."""
    import json

    names = HardwareNames()
    names.names["fans"] = [
        {"key": "fan.lpc.fan_1", "detected": "Fan #1", "custom": "", "value": 820},
        {"key": "fan.lpc.fan_2", "detected": "Rear", "custom": "", "value": None},
    ]

    def answer(route, request):
        if request.method == "POST":
            change = json.loads(request.post_data or "{}")
            names.posted.append(change)
            for key, text in change.get("fans", {}).items():
                next(f for f in names.names["fans"] if f["key"] == key)["custom"] = text
        route.fulfill(status=200, content_type="application/json", body=json.dumps(names.names))

    with browser_page(app_url, "settings/sensors", routes={"**/api/hardware": answer}) as page:
        first = page.locator('[id="fan-name-fan.lpc.fan_1"]')
        assert first.get_attribute("placeholder") == "Fan #1"
        row = page.locator(".settings-row", has=first)
        expect(row).to_contain_text("fan.lpc.fan_1 · 820 RPM now")
        expect(page.locator(".settings-row", has_text="Rear")).to_contain_text("not read now")
        first.fill("Intake")
        first.press("Enter")
        page.wait_for_function("window.libre.store.get().hardware.fans[0].custom === 'Intake'")
        row.locator(".link-btn").click()  # Automatic
        page.wait_for_function("window.libre.store.get().hardware.fans[0].custom === ''")
        assert names.posted == [
            {"fans": {"fan.lpc.fan_1": "Intake"}},
            {"fans": {"fan.lpc.fan_1": ""}},
        ]


def test_the_fans_look_is_chosen_in_settings_for_all_and_for_one(app_url):
    """A tile with a picture for each look, for all fans; colours, the theme's or the look's;
    and a fan's own look ("as all fans" goes back)."""
    import json

    names = HardwareNames()
    looks = [{"id": "noctua-g2", "label": "Noctua NF-A12x25 / NF-A14x25 G2"},
             {"id": "noctua-a12x25", "label": "Noctua NF-A12x25 (G1)"},
             {"id": "noctua-a14", "label": "Noctua NF-A14 (G1, 140 mm)"},
             {"id": "libre", "label": "Libre Panel"}]  # fmt: skip
    names.names["fan_look"] = {"look": "", "paint": "", "looks": looks}
    names.names["fans"] = [{"key": "fan.lpc.fan_1", "detected": "Fan #1", "custom": "",
                            "value": 820, "look": "", "paint": ""}]  # fmt: skip

    def answer(route, request):
        if request.method == "POST":
            change = json.loads(request.post_data or "{}")
            names.posted.append(change)
            for key in ("fan_look", "fan_paint"):
                if key in change:
                    names.names["fan_look"][key.removeprefix("fan_")] = change[key]
            for key, look in change.get("fan_looks", {}).items():
                next(f for f in names.names["fans"] if f["key"] == key)["look"] = look
        route.fulfill(status=200, content_type="application/json", body=json.dumps(names.names))

    with browser_page(app_url, "settings/sensors", routes={"**/api/hardware": answer}) as page:
        tiles = page.locator(".fan-look-tile")
        expect(tiles).to_have_count(4)
        assert tiles.first.get_attribute("aria-checked") == "true"  # the default
        page.wait_for_function(
            "[...document.querySelectorAll('.fan-look-tile img')].every((i) => i.naturalWidth > 0)"
        )  # each with its picture
        page.locator('.fan-look-tile[data-look="noctua-a12x25"]').click()
        page.wait_for_function(
            "window.libre.store.get().hardware.fan_look.look === 'noctua-a12x25'"
        )
        page.locator(".settings-row", has_text="Colours").get_by_role("radio").nth(1).click()
        page.wait_for_function("window.libre.store.get().hardware.fan_look.paint === 'own'")
        assert "paint=own" in page.locator(".fan-look-tile img").first.get_attribute("src")
        page.locator('[data-fan="fan.lpc.fan_1"] select').select_option("noctua-a14")
        page.wait_for_function("window.libre.store.get().hardware.fans[0].look === 'noctua-a14'")
        assert names.posted == [
            {"fan_look": "noctua-a12x25"},
            {"fan_paint": "own"},
            {"fan_looks": {"fan.lpc.fan_1": "noctua-a14"}},
        ]


def test_without_hardware_names_nothing_shows(app_url):
    """A server without /api/hardware (an older background app): no fields; the rest of
    the section is there."""
    missing = {"**/api/hardware": lambda route: route.fulfill(status=404, body="")}
    with browser_page(app_url, "settings/sensors", routes=missing) as page:
        page.wait_for_function("window.libre.store.get().hardware === false")
        assert page.locator(".hardware-name").count() == 0
        assert page.locator("#sensor-sources").is_visible()
        assert page.locator(".settings-upcoming").count() == 0


# -- Set up sensors and panel access (Linux) -----------------------------------------------------
# The server is the real one; the computer is made up (a board with a fan chip to load, a panel
# the user may not open, energy counters nobody may read) and the password window is a stand-in
# that records what pkexec is asked to run.

APPIMAGE = "/home/someone/Apps/Libre_Panel-x86_64.AppImage"


class Pkexec:
    """The administrator's rights: ``answer`` is what the run ends with (exit code, standard
    output, standard error); ``gate`` holds it back while the "password window" is open."""

    def __init__(self, answer=None):
        import json
        import threading

        self.asked = []
        self.gate = threading.Event()
        self.gate.set()
        report = {
            "ok": True,
            "results": [
                {
                    "id": "udev",
                    "status": "done",
                    "items": {"/etc/udev/rules.d/60.rules": "written"},
                },
                {"id": "modules", "status": "done", "items": {"it87": "none", "nct6775": "loaded"}},
                {"id": "reload", "status": "done", "items": {}},
            ],
        }
        self.answer = answer or (0, json.dumps(report), "")

    def __call__(self, argv):
        self.asked.append(tuple(argv))
        assert self.gate.wait(30)
        return self.answer


@pytest.fixture
def linux_pc(monkeypatch):
    """A Linux desktop with polkit, whose setup is not done: ``linux_pc(...)`` changes the facts
    and the answer; it gives the stand-in for pkexec."""
    from libre_panel import linux_rights as rights
    from libre_panel import linux_setup as setup

    def make(answer=None, method=True, **changes):
        facts = {
            "dmi": {"board_vendor": "ASUSTeK COMPUTER INC.", "chassis_type": "3"},
            "available": frozenset({"nct6775", "it87", "drivetemp"}),
            "tools": {"udevadm": "/usr/bin/udevadm", "chmod": "/usr/bin/chmod"},
            "files": dict.fromkeys(setup.OWN_FILES),
            "panel": True,
            "panel_denied": True,
            "rapl": ("/sys/class/powercap/intel-rapl:0/energy_uj",),
            "rapl_closed": ("/sys/class/powercap/intel-rapl:0/energy_uj",),
            **changes,
        }
        computer = setup.Facts(**facts)
        monkeypatch.setattr(setup, "gather", lambda *args, **kwargs: computer)
        monkeypatch.setattr("libre_panel.linux_setup.supported", lambda: True)
        monkeypatch.setattr(rights, "program", lambda *args, **kwargs: [APPIMAGE])
        monkeypatch.setattr(
            rights.shutil, "which", lambda name: f"/usr/bin/{name}" if method else None
        )
        monkeypatch.setenv("DISPLAY", ":0")
        pkexec = Pkexec(answer)
        monkeypatch.setattr(rights, "JOB", rights.Job(pkexec))
        return pkexec

    return make


def start_with_setup(app_url, isolated_home, **kwargs):
    choose_theme(isolated_home)
    return browser_page(
        app_url, "home", routes={"**/api/app": a_panel_app()}, allow=PICTURE, **kwargs
    )


def test_start_offers_the_setup_while_something_is_missing(app_url, isolated_home, linux_pc):
    linux_pc()
    with start_with_setup(app_url, isolated_home) as page:
        card = page.locator(".setup-card")
        expect(card).to_be_visible()
        text = card.inner_text()
        assert "Your user may not use the USB panel yet." in text
        assert "No fan readings: the driver for the chip on the mainboard is not loaded." in text
        assert "The processor's power cannot be read" in text
        # what the button does, said exactly
        assert "A rule for udev lets your user use the TURZX USB panels" in text
        assert "it tries nct6775" in text and "nothing is forced" in text
        assert "Linux has kept the energy counters of the processor from everyone but root" in text
        power = card.get_by_role("switch", name="Let Libre Panel read the processor's power")
        assert power.get_attribute("aria-checked") == "false"  # off unless turned on
        expect(card.get_by_role("button", name="Set up")).to_be_enabled()


def test_start_stays_quiet_where_nothing_is_missing(app_url, isolated_home, linux_pc):
    linux_pc(rule_elsewhere=True, dmi={}, panel=False, panel_denied=False, rapl=(), rapl_closed=())
    with start_with_setup(app_url, isolated_home) as page:
        page.wait_for_function("window.libre.store.get().unlockInfo !== null")
        assert page.locator(".setup-card").count() == 0


def test_another_system_has_no_setup(app_url, isolated_home, linux_pc, monkeypatch):
    linux_pc()
    monkeypatch.setattr("libre_panel.linux_setup.supported", lambda: False)
    with start_with_setup(app_url, isolated_home) as page:
        page.wait_for_function(
            "window.libre.store.get().unlockInfo !== null"
            " && !window.libre.store.get().unlockInfo.offers"
        )
        assert page.locator(".setup-card").count() == 0
        page.goto(f"{app_url}#/settings/sensors")
        page.wait_for_selector("#sensor-sources")
        assert page.locator("#sensor-setup").count() == 0


def test_one_click_asks_pkexec_and_tells_how_it_went(app_url, isolated_home, linux_pc):
    pkexec = linux_pc()
    with start_with_setup(app_url, isolated_home) as page:
        page.locator(".setup-card").get_by_role("button", name="Set up").click()
        waits_for(page, lambda: len(pkexec.asked) == 1)
        assert pkexec.asked[0] == (
            "/usr/bin/pkexec",
            "--disable-internal-agent",
            APPIMAGE,
            "system-setup",
            "--linux",
            "--json",  # (the energy counters stay closed: they were not asked for)
        )
        result = page.locator(".setup-card .setup-result")
        expect(result).to_contain_text("The rule for the USB panel is installed.")
        expect(result).to_contain_text("The driver nct6775 is loaded now and at every start.")
        expect(result).to_contain_text("it87: no such chip on this board.")
        page.locator(".setup-card").get_by_role("button", name="Close").click()
        expect(page.locator(".setup-card")).to_have_count(0)


def test_the_processors_power_is_asked_for_only_when_its_switch_is_on(
    app_url, isolated_home, linux_pc
):
    pkexec = linux_pc()
    with start_with_setup(app_url, isolated_home) as page:
        card = page.locator(".setup-card")
        card.get_by_role("switch", name="Let Libre Panel read the processor's power").click()
        card.get_by_role("button", name="Set up").click()
        waits_for(page, lambda: len(pkexec.asked) == 1)
        assert pkexec.asked[0][-2:] == ("--rapl", "--json")


def test_the_run_shows_that_the_password_is_asked_for(app_url, isolated_home, linux_pc):
    pkexec = linux_pc()
    pkexec.gate.clear()  # the password window is open
    with start_with_setup(app_url, isolated_home) as page:
        page.locator(".setup-card").get_by_role("button", name="Set up").click()
        waiting = page.locator(".setup-card .note")
        expect(waiting).to_contain_text("Your system is asking for the administrator's password")
        expect(page.locator(".setup-card .btn-primary")).to_be_disabled()
        assert page.locator(".setup-card .link-btn").is_disabled()  # "Not now" waits too
        pkexec.gate.set()
        expect(page.locator(".setup-card .setup-result")).to_be_visible()


def test_closing_the_password_window_changes_nothing(app_url, isolated_home, linux_pc):
    pkexec = linux_pc(answer=(126, "", "Error executing command as another user: Not authorized"))
    with start_with_setup(app_url, isolated_home) as page:
        page.locator(".setup-card").get_by_role("button", name="Set up").click()
        expect(page.locator(".setup-card")).to_contain_text("Cancelled. Nothing was changed.")
        expect(
            page.locator(".setup-card .btn-primary")
        ).to_be_enabled()  # and it can be tried again
        assert len(pkexec.asked) == 1


def test_not_now_keeps_the_card_away_until_something_else_is_missing(
    app_url, isolated_home, linux_pc
):
    linux_pc()
    with start_with_setup(app_url, isolated_home) as page:
        page.locator(".setup-card").get_by_role("button", name="Not now").click()
        expect(page.locator(".setup-card")).to_have_count(0)
        assert js(page, "localStorage.getItem('libre-panel-setup-dismissed')") == "usb,fans,power"
        page.goto(f"{app_url}#/settings/sensors")  # Settings has it all the same
        expect(page.locator("#sensor-setup")).to_be_visible()


def test_without_a_way_to_ask_the_terminal_command_is_all_there_is(
    app_url, isolated_home, linux_pc
):
    pkexec = linux_pc(method=False)
    with start_with_setup(app_url, isolated_home) as page:
        card = page.locator(".setup-card")
        expect(card.get_by_role("button", name="Set up")).to_be_disabled()
        expect(card).to_contain_text("This system cannot show a password window from here")
        command = card.locator(".command")
        assert command.inner_text() == f"sudo {APPIMAGE} system-setup --linux"
        assert pkexec.asked == []


def test_settings_sensors_has_the_setup_beside_the_sources(app_url, linux_pc):
    pkexec = linux_pc()
    with browser_page(app_url, "settings/sensors") as page:
        card = page.locator("#sensor-setup")
        expect(card).to_be_visible()
        assert page.locator(".settings-card").evaluate_all("cards => cards.map((c) => c.id)")[
            :2
        ] == [
            "sensor-sources",
            "sensor-setup",
        ]
        expect(card.locator("h2")).to_have_text("Set up sensors and panel access")
        assert "Nothing else is changed" in card.inner_text()
        card.locator("summary", has_text="Or in a terminal").click()  # always there, never needed
        expect(card.locator(".command")).to_have_text(f"sudo {APPIMAGE} system-setup --linux")
        card.get_by_role("button", name="Set up").click()
        waits_for(page, lambda: len(pkexec.asked) == 1)
        expect(card.locator(".setup-result")).to_be_visible()
        assert card.get_by_role("button", name="Remove again").count() == 0  # nothing of ours yet


def test_what_the_setup_put_in_can_be_taken_out_again(app_url, linux_pc):
    from libre_panel import linux_setup as setup

    own = {path: "x\n" for path in setup.OWN_FILES}
    pkexec = linux_pc(files=own, rule_elsewhere=True, dmi={}, rapl_closed=())
    with browser_page(app_url, "settings/sensors") as page:
        card = page.locator("#sensor-setup")
        # the counters are open, as the setup left them: the switch says so, and it can close them
        power = card.get_by_role("switch", name="Let Libre Panel read the processor's power")
        assert power.get_attribute("aria-checked") == "true"
        power.click()
        card.get_by_role("button", name="Set up").click()
        waits_for(page, lambda: len(pkexec.asked) == 1)
        assert pkexec.asked[0][-2:] == ("--no-rapl", "--json")
        card.get_by_role("button", name="Remove again").click()
        waits_for(page, lambda: len(pkexec.asked) == 2)
        assert pkexec.asked[1][-2:] == ("--remove", "--json")


def test_settings_say_nothing_where_there_is_nothing_to_set_up(app_url, linux_pc):
    linux_pc(rule_elsewhere=True, dmi={}, panel=False, panel_denied=False, rapl=(), rapl_closed=())
    with browser_page(app_url, "settings/sensors") as page:
        page.wait_for_function("window.libre.store.get().unlockInfo !== null")
        assert page.locator("#sensor-setup").count() == 0
        assert page.locator("#sensor-sources").is_visible()


def test_the_setup_speaks_german(app_url, isolated_home, linux_pc):
    from libre_panel import i18n

    linux_pc()
    i18n.set_language("de")
    with browser_page(app_url, "settings/sensors") as page:
        card = page.locator("#sensor-setup")
        expect(card.locator("h2")).to_have_text("Sensoren und Panel-Zugriff einrichten")
        expect(card.get_by_role("button", name="Einrichten")).to_be_visible()
        assert "Das USB-Panel benutzen" in card.inner_text()
        assert "Lüfter und Temperaturen des Mainboards" in card.inner_text()
        assert "Oder in einem Terminal" in card.inner_text()


def test_the_palette_finds_the_setup(app_url, linux_pc):
    linux_pc()
    with browser_page(app_url, "home", routes={"**/api/app": a_panel_app()}, allow=PICTURE) as page:
        page.wait_for_function("Boolean(window.libre.store.get().unlockInfo?.offers)")
        found = js(
            page,
            "window.libre.commands.availableCommands().map((c) => c.id).includes('setup.open')",
        )
        assert found is True
