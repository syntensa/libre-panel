import base64
import http.client
import json
import threading
import tomllib
from types import SimpleNamespace

import pytest
from editor_token import token_of
from fake_hardware import PC, TIDY

from libre_panel.config import load_config
from libre_panel.devices.panel_files import STANDBY_CLIP
from libre_panel.editor import access
from libre_panel.editor.server import make_server
from libre_panel.sensors import hardware
from libre_panel.theme.model import find_theme, load_theme
from libre_panel.theme.modules import MODULE_KINDS


@pytest.fixture
def server():
    srv = make_server(port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()
    srv.editor_state.close()


def request(port, method, path, body=None, headers=None, host=None):
    """A request as the app makes it: with the token of the server on ``port``, unless
    ``headers`` says otherwise (a header given as None is left out)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    hdrs = {"Host": host or f"127.0.0.1:{port}", access.HEADER: token_of(port)}
    if method == "POST":
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    hdrs = {name: value for name, value in hdrs.items() if value is not None}
    data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
    try:  # (closed also when the answer does not come: no connection is left for the next)
        conn.request(method, path, body=data, headers=hdrs)
        response = conn.getresponse()
        raw = response.read()
    finally:
        conn.close()
    try:
        return response.status, json.loads(raw)
    except ValueError:
        return response.status, raw


def theme_dict():
    return load_theme(find_theme("libre-default")).to_dict()


def test_index_and_specs(server):
    status, body = request(server, "GET", "/")
    assert status == 200 and b'src="/app/main.js"' in body  # the app
    status, specs = request(server, "GET", "/api/specs")
    assert status == 200
    assert "gauge" in specs["widgets"]
    assert any(m["id"] == "turing-9.2-usb" for m in specs["models"])
    band = specs["toasts"]["band"]  # the built-in styles besides the card, with their options
    assert band["label"] == "Band (built in)" and band["options"]["roll_in"] == ["number", 0.45]


def test_a_bookmark_of_the_old_editor_leads_to_the_app(server):
    for path in ("/classic", "/classic/", "/classic/index.html"):
        conn = http.client.HTTPConnection("127.0.0.1", server, timeout=10)
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{server}"})
        response = conn.getresponse()
        response.read()
        conn.close()
        assert response.status == 301 and response.getheader("Location") == "/", path
    assert request(server, "GET", "/classic/", host="evil.example")[0] == 403  # as any page
    # what that editor loaded is gone; what the console's and plugins' pages use is there
    for name in ("editor.js", "index.html"):
        assert request(server, "GET", f"/static/{name}")[0] == 404
    for name in ("kit.js", "editor.css", "stats.js", "icon.png"):
        assert request(server, "GET", f"/static/{name}")[0] == 200


def test_the_sensor_picker_offers_what_sources_read_only_on_demand(server):
    status, sensors = request(server, "GET", "/api/sensors")
    assert status == 200
    keys = [sensor["key"] for sensor in sensors]
    assert keys == sorted(set(keys))  # each once, in order
    wanted = (
        "audio.volume", "audio.muted", "cpu.load", "gpu.load", "media.title", "media.progress",
        "game.fps", "game.frametime_peak", "game.limiter", "game.name",
        "weather.rain.minutes", "weather.rain.text", "net.ping",
    )  # fmt: skip
    assert set(wanted) <= set(keys)
    pictures = {sensor["key"] for sensor in sensors if sensor.get("picture")}
    assert pictures == {"media.cover", "media.app_icon", "game.logo"}  # for an image's "@key"
    assert not any(sensor.get("picture") for sensor in sensors if sensor["key"] not in pictures)
    volume = next(sensor for sensor in sensors if sensor["key"] == "audio.volume")
    assert (volume["unit"], volume["label"]) == ("%", "Volume")


def test_the_picker_does_not_wait_for_the_demo_or_the_panel_to_have_them(server, monkeypatch):
    """A running panel has no volume until a theme shows it: the keys come from the catalog."""
    from libre_panel.editor import server as editor_server
    from libre_panel.sensors.base import Snapshot

    monkeypatch.setattr(editor_server, "demo_snapshot", lambda: Snapshot())
    status, sensors = request(server, "GET", "/api/sensors")
    keys = {sensor["key"] for sensor in sensors}
    assert status == 200 and {"audio.volume", "weather.rain.text", "media.app_icon"} <= keys


def test_the_catalog_of_on_demand_keys_says_what_the_sources_and_the_demo_say():
    from libre_panel.editor.server import ON_DEMAND
    from libre_panel.sensors import load
    from libre_panel.sensors.audio import AudioProvider
    from libre_panel.sensors.base import Reading
    from libre_panel.sensors.demo import demo_snapshot
    from libre_panel.sensors.media import Track

    catalog = {key: (unit, label) for key, unit, label in ON_DEMAND}
    assert len(catalog) == len(ON_DEMAND)  # no key twice
    spoken = {}
    track = Track("playing", "Song", "Band", "Album", 1.0, 2.0, None, "Player", at=0.0)
    spoken.update(track.readings(0.0))
    audio = AudioProvider()
    audio._publish(40.0, False)
    spoken.update(audio._state[1])
    spoken.update({key: Reading(key, 1.0, "%", label) for key, label in load._LABELS.items()})
    assert spoken.keys() >= {"audio.volume", "media.title", "cpu.load", "gpu.load"}
    for key, reading in spoken.items():
        assert catalog[key] == (reading.unit, reading.label), key
    demo = demo_snapshot().readings  # the previews show a value for every one of them...
    for key, (unit, label) in catalog.items():
        if key == "game.name":  # ...but one: the game service publishes this, and a demo value
            assert key not in demo  # of the same name would hide it where the demo is a source
            continue
        assert key in demo and (demo[key].unit, demo[key].label) == (unit, label), key


def test_the_preview_is_never_turned(server, isolated_home):
    """Upside down is for the panel; the editor shows the theme the right way up."""
    from io import BytesIO

    from PIL import Image, ImageChops

    from libre_panel.config import set_config_value
    from libre_panel.render.renderer import Renderer
    from libre_panel.sensors.demo import demo_snapshot
    from libre_panel.theme.model import parse_theme

    set_config_value("device.rotate", 180)
    theme = {"format": "libre-panel-theme/1", "name": "still",
             "display": {"width": 480, "height": 320},
             "widgets": [{"type": "rect", "id": "a", "x": 10, "y": 10, "w": 120, "h": 40,
                          "color": "#ff8800"}]}  # fmt: skip
    status, data = request(server, "POST", "/api/render", {"theme": theme})
    assert status == 200
    preview = Image.open(BytesIO(base64.b64decode(data["png"]))).convert("RGB")
    upright, _ = Renderer(parse_theme(theme), preview=True).render(demo_snapshot())
    assert ImageChops.difference(preview, upright).getbbox() is None


def test_render(server):
    status, data = request(
        server, "POST", "/api/render", {"theme": theme_dict(), "base": "libre-default"}
    )
    assert status == 200
    assert base64.b64decode(data["png"])[:4] == b"\x89PNG"
    assert data["boxes"]["clock"][2] > 0


def test_protections(server):
    assert request(server, "GET", "/api/themes", host="evil.example")[0] == 403
    status, _ = request(
        server, "POST", "/api/render", {"theme": theme_dict()}, headers={"X-Libre-Panel": "0"}
    )
    assert status == 403
    assert request(server, "GET", "/static/..%2F..%2Fconfig.py")[0] == 404
    assert request(server, "POST", "/api/themes/..%2Fx", {"theme": theme_dict()})[0] == 400


def test_refused_requests_get_their_answer(server):
    """A request refused before its body is read must still get the answer, not a
    reset connection (Windows resets a socket closed with unread data)."""
    port = server
    body = {"theme": "x" * 1_900_000}
    for _ in range(10):
        no_header = request(port, "POST", "/api/render", body, headers={"X-Libre-Panel": ""})
        assert no_header[0] == 403
        assert request(port, "POST", "/api/nothing", body)[0] == 404


def test_save_upload_and_adapt(server, isolated_home):
    status, data = request(
        server, "POST", "/api/themes/mine", {"theme": theme_dict(), "source": "libre-default"}
    )
    assert status == 200 and (isolated_home / "themes" / "mine" / "theme.json").exists()

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    status, data = request(
        server,
        "POST",
        "/api/themes/mine/assets?name=logo.png",
        png,
        {"Content-Type": "application/octet-stream"},
    )
    assert status == 200 and data["path"] == "assets/logo.png"
    status, _ = request(server, "POST", "/api/themes/mine/assets?name=run.exe", b"x")
    assert status == 400

    status, data = request(
        server,
        "POST",
        "/api/adapt",
        {"theme": theme_dict(), "model": "turing-8.8-usb", "orientation": "landscape"},
    )
    assert status == 200 and data["theme"]["display"]["width"] == 1920


def test_specs_offer_fonts_icons_and_presets(server):
    _, specs = request(server, "GET", "/api/specs")
    assert "builtin:Barlow-SemiBold" in specs["fonts"]
    assert "weather" in specs["icons"]
    assert {p["id"] for p in specs["presets"]} >= {"ring", "cpu-card", "section"}
    assert "glow" in specs["effect_fields"] and "locked" in specs["common"]


def test_theme_assets_listing(server, isolated_home):
    request(server, "POST", "/api/themes/mine", {"theme": theme_dict(), "source": "libre-default"})
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    request(server, "POST", "/api/themes/mine/assets?name=logo.png", png,
            {"Content-Type": "application/octet-stream"})  # fmt: skip
    status, files = request(server, "GET", "/api/themes/mine/assets")
    assert status == 200 and files == ["assets/logo.png"]


def module_theme(*modules):
    return {
        "format": "libre-panel-theme/1",
        "display": {"model": "turing-9.2-usb", "orientation": "landscape"},
        "widgets": [{"type": "module", "id": f"m{i}", **m} for i, m in enumerate(modules)],
    }


def test_the_preview_can_show_a_test_notice(server):
    """A notice is drawn on the picture in the theme's toast style, for Look to show."""
    from io import BytesIO

    from PIL import Image, ImageChops

    def picture(**more):
        status, data = request(server, "POST", "/api/render", {"theme": theme, **more})
        assert status == 200
        return Image.open(BytesIO(base64.b64decode(data["png"]))).convert("RGB")

    theme = theme_dict()
    plain = picture(base="libre-default")
    card = picture(base="libre-default", notice="volume")
    assert ImageChops.difference(plain, card).getbbox() is not None  # a card in the corner
    theme["toast"] = {"style": "band"}
    band = picture(base="libre-default", notice="volume")
    assert ImageChops.difference(card, band).getbbox() is not None  # the style is the theme's
    status, data = request(server, "POST", "/api/render", {"theme": theme, "notice": "nope"})
    assert status == 400 and "notice" in data["error"]


def test_render_reports_the_grid_and_module_cells(server):
    plain = theme_dict()  # without modules
    plain["widgets"] = [w for w in plain["widgets"] if w["type"] != "module"]
    plain.pop("grid", None)
    status, data = request(server, "POST", "/api/render", {"theme": plain})
    assert status == 200 and data["grid_active"] is False and data["module_boxes"] == {}
    theme = module_theme(
        {"module": "ring", "cols": 2},
        {"module": "ring", "col": 2, "source": "gpu", "fallback": "none"},
    )
    status, data = request(server, "POST", "/api/render", {"theme": theme})
    assert data["grid_active"] is True
    assert (data["grid"]["columns"], data["grid"]["rows"]) == (8, 2)
    assert set(data["module_boxes"]) == {"m0", "m1"}
    assert set(data["boxes"]) <= {"m0", "m1"}  # no parts, no backdrop


def test_specs_carry_modules_and_looks(server):
    _, specs = request(server, "GET", "/api/specs")
    assert "module" in specs["widgets"]
    assert specs["modules"]["kinds"]["ring"]["sources"] == ["cpu", "gpu", "mem", "disk"]
    assert {"arctic", "paper", "mono"} <= set(specs["modules"]["looks"])


def test_specs_carry_the_panel_look_and_the_finishes(server):
    from libre_panel.theme.model import FINISH_FIELD

    _, specs = request(server, "GET", "/api/specs")
    assert specs["look"]["grid"]["spacing"] == ["int", 8]
    assert specs["look"]["light"]["sweep_seconds"] == ["number", 1.6]
    for kind in ("bar", "gauge", "graph", "icon", "text", "metric"):
        assert specs["widgets"][kind]["finish"] == list(FINISH_FIELD)
    # a module's finish may also be "auto", the default: its kind's own (a card is lit)
    assert specs["widgets"]["module"]["finish"] == [
        "enum:auto|" + FINISH_FIELD[0].removeprefix("enum:"), "auto",
    ]  # fmt: skip
    assert FINISH_FIELD[1] == "flat" and "lit" in FINISH_FIELD[0]
    assert "finish" not in specs["widgets"]["rect"]


def test_specs_carry_the_ranges_of_the_look(server):
    """Sliders for the look stop where the theme would refuse a value."""
    from libre_panel.theme.model import LOOK_RANGES

    _, specs = request(server, "GET", "/api/specs")
    ranges = specs["look_ranges"]
    assert ranges["light"]["sweep_seconds"] == [0.3, 10] and ranges["dust"]["rate"] == [1, 60]
    assert {(block, key) for block, keys in ranges.items() for key in keys} == set(LOOK_RANGES)
    for block, keys in ranges.items():
        assert set(keys) <= set(specs["look"][block]), block


def test_module_previews_follow_the_look(server):
    from libre_panel.theme.modules import look_theme_parts

    palette, style = look_theme_parts("paper")
    status, data = request(
        server, "POST", "/api/module-previews", {"palette": palette, "style": style}
    )
    assert status == 200 and len(data["previews"]) == len(MODULE_KINDS)
    assert base64.b64decode(data["previews"]["ring"])[:4] == b"\x89PNG"


def test_every_renderer_a_preview_makes_is_closed_and_freed(plugin_folder, server, monkeypatch):
    """The previews of the editor (the building blocks' pictures, a saved theme's, the panel's
    own, with a plugin's screen and widget type among them) make a renderer each and let go of
    it: it must be closed, and gone with its last reference, not left to the collector, which
    the main loop of the background app, which runs the editor, keeps from working for hours
    (the contexts of a plugin's parts refer to their renderer until it is closed)."""
    import gc
    import time
    import weakref

    from test_plugins import DRAWING, bar, theme_data

    from libre_panel.editor import server as editor
    from libre_panel.editor.presets import PRESETS

    parts = {"libre_panel.screens": {"tint": "Tint"}, "libre_panel.widgets": {"demo.bar": "Bar"}}
    plugin_folder(DRAWING, parts=parts)  # (no part that fails to load: its log would keep a frame)

    made, closed = [], []

    class Tracked(editor.Renderer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(weakref.ref(self))

        def close(self):
            closed.append(id(self))
            super().close()

    monkeypatch.setattr(editor, "Renderer", Tracked)
    palette = {"bg": "#101010", "accent": "#ff8800"}
    calls = [
        ("/api/module-previews", {"palette": palette}, len(MODULE_KINDS)),
        ("/api/preset-previews", {"palette": palette}, len(PRESETS)),
        ("/api/render", {"theme": theme_dict(), "notice": "volume"}, 1),
        ("/api/render", {"theme": theme_data({"name": "tint"}, [bar()])}, 1),  # plugin parts
        ("/api/themes/libre-default/preview?w=200", None, 1),
    ]
    gc.collect()
    gc.disable()  # the collector must not be what frees them
    try:
        for path, body, count in calls:
            before = len(made)
            status, _ = request(server, "POST" if body else "GET", path, body)
            assert status == 200, path
            assert len(made) - before >= count, path  # (a plugin's presets come too)
            assert len(closed) == len(made), path
        deadline = time.monotonic() + 5
        while any(ref() is not None for ref in made) and time.monotonic() < deadline:
            time.sleep(0.02)  # (the handler's last frame goes with its answer)
        assert not [ref for ref in made if ref() is not None]
    finally:
        gc.enable()


def test_templates_fill_the_panel_grid(server):
    status, data = request(
        server, "POST", "/api/templates", {"width": 480, "height": 320, "model": "turing-3.5"}
    )
    assert status == 200 and (data["grid"]["columns"], data["grid"]["rows"]) == (3, 2)
    assert [t["id"] for t in data["templates"]] == ["overview", "performance", "calm"]
    status, _ = request(server, "POST", "/api/templates", {"width": "x"})
    assert status == 400


def test_detach_gives_plain_widgets(server):
    status, data = request(
        server,
        "POST",
        "/api/detach",
        {"theme": module_theme({"module": "clock", "cols": 3}), "id": "m0"},
    )
    assert status == 200
    ids = [w["id"] for w in data["widgets"]]
    assert "m0-time" in ids and all("_module" not in w for w in data["widgets"])
    assert {w["group"] for w in data["widgets"]} == {"m0"}  # (to be put back together)


def test_a_theme_keeps_its_detached_modules_to_put_them_back_together():
    from libre_panel.theme.model import parse_theme

    data = module_theme({"module": "clock", "cols": 3})
    module = dict(data["widgets"][0])
    data["widgets"] = [{"type": "text", "id": "t", "text": "12:00", "group": "m0"}]
    data["detached"] = {"m0": module, "junk": {"type": "text", "id": "x"}, "bad": 3}
    theme = parse_theme(data)
    assert set(theme.detached) == {"m0"} and theme.detached["m0"]["module"] == "clock"
    assert theme.widgets[0]["group"] == "m0" and theme.warnings == []
    again = parse_theme(theme.to_dict())
    assert again.detached == theme.detached and again.widgets[0]["group"] == "m0"


def test_the_app_and_its_fonts_are_served(server):
    status, body = request(server, "GET", "/app/main.js")
    assert status == 200 and b"import" in body
    status, body = request(server, "GET", "/app/vendor/preact.module.js")
    assert status == 200 and len(body) > 5000
    conn = http.client.HTTPConnection("127.0.0.1", server, timeout=10)
    conn.request("GET", "/app/fonts/Barlow-Medium.ttf", headers={"Host": f"127.0.0.1:{server}"})
    response = conn.getresponse()
    assert response.status == 200 and response.getheader("Content-Type") == "font/ttf"
    assert len(response.read()) > 10_000
    conn.close()
    refused = ("/app/fonts/nope.ttf", "/app/../server.py", "/app/%2E%2E/x.py", "/app/README.md")
    for path in refused:
        assert request(server, "GET", path)[0] == 404, path


def test_readings_with_history(server):
    status, data = request(server, "GET", "/api/readings?keys=cpu.load,nope&history=cpu.load")
    assert status == 200 and set(data["readings"]) == {"cpu.load"}
    assert data["readings"]["cpu.load"]["unit"] == "%"
    assert 1 < len(data["history"]["cpu.load"]) <= 60
    status, data = request(server, "GET", "/api/readings")
    assert status == 200 and "gpu.temp" in data["readings"]  # without keys: every reading


def test_a_reading_this_computer_does_not_give_is_marked_a_sample(monkeypatch, server):
    """The pickers fill what this computer does not give with sample values (a fan it does not
    have): they are marked, so that the app does not pretend the panel shows them."""
    from libre_panel.editor.server import EditorState
    from libre_panel.sensors.base import Reading, Snapshot

    live = Snapshot(readings={"cpu.load": Reading("cpu.load", 12.0, "%", "CPU")})
    monkeypatch.setattr(EditorState, "live_snapshot", lambda self: live)
    status, data = request(server, "GET", "/api/readings?keys=cpu.load,fan.lpc.fan_1")
    assert status == 200 and data["live"] is True
    assert data["readings"]["cpu.load"]["sample"] is False
    assert data["readings"]["cpu.load"]["value"] == 12.0
    assert data["readings"]["fan.lpc.fan_1"]["sample"] is True


def test_pages_of_the_panel(server, isolated_home):
    """The normal theme, each mode's (game mode's always) and the round report's."""
    from libre_panel.config import load_config

    status, data = request(server, "GET", "/api/pages")
    assert status == 200 and data["shown"] is None and data["mode"] is None
    pages = {page["id"]: page for page in data["pages"]}
    assert pages["normal"]["theme"] == "libre-default"
    assert pages["game"] == {
        "id": "game", "mode": "game", "theme": None, "fps": None,
        "on": False, "service": "game",  # (the game service is off)
        "suggested": ["arena", "spur-session", "spur-focus", "spur-header"],
    }  # fmt: skip
    assert pages["report"]["theme"] == "round-report" and pages["report"]["on"] is False
    # the pages Libre Panel switches to itself: off until switched on, each with its screen
    assert (pages["media"]["on"], pages["media"]["theme"], pages["media"]["own"]) == (
        False, "encore", True)  # fmt: skip
    assert (pages["idle"]["theme"], pages["idle"]["minutes"]) == ("almanac", 5.0)
    assert pages["idle"]["suggested"] == ["almanac", "almanac-analog"]
    status, data = request(server, "POST", "/api/pages", {"page": "idle", "on": True, "minutes": 2})
    pages = {page["id"]: page for page in data["pages"]}
    assert status == 200 and pages["idle"]["on"] is True and pages["idle"]["minutes"] == 2
    status, data = request(server, "POST", "/api/pages", {"page": "game", "on": True})
    pages = {page["id"]: page for page in data["pages"]}
    assert status == 200 and pages["game"]["on"] is True  # the game service runs for it
    assert load_config().services.enabled == ["game"]
    status, data = request(server, "POST", "/api/pages", {"page": "game", "on": False})
    config = load_config()
    assert config.services.options["game"]["automatic"] is False  # (it may still count)
    assert config.modes["idle"].on is True and config.modes["idle"].minutes == 2

    game = {"page": "game", "theme": "slate", "fps": 30}
    status, data = request(server, "POST", "/api/pages", game)
    pages = {page["id"]: page for page in data["pages"]}
    assert status == 200 and (pages["game"]["theme"], pages["game"]["fps"]) == ("slate", 30)
    status, data = request(server, "POST", "/api/pages", {"page": "report", "theme": "orbit"})
    assert status == 200 and data["pages"][-1]["theme"] == "orbit"
    status, data = request(server, "POST", "/api/pages", {"page": "vr", "theme": "pico"})
    assert status == 200 and [p["id"] for p in data["pages"]] == [
        "normal", "game", "media", "idle", "vr", "report"]  # fmt: skip
    status, data = request(server, "POST", "/api/pages", {"page": "game", "theme": ""})
    assert status == 200 and data["pages"][1]["theme"] is None  # the normal one again
    config = load_config()
    assert config.modes["game"].fps == 30 and config.modes["vr"].theme == "pico"
    assert config.services.options["memory"]["report_theme"] == "orbit"

    for wrong in (
        {"page": "Not A Name", "theme": "slate"},
        {"page": "game", "theme": "nope"},
        {"page": "game", "fps": True},
        {"page": "normal", "theme": "slate", "fps": 20},
        {"page": "report", "theme": ""},
        {"page": "normal", "on": False},  # the normal page is always there
        {"page": "media", "on": "yes"},
        {"page": "media", "minutes": 3},  # (the idle page's)
        {"page": "idle", "minutes": 0},
    ):
        assert request(server, "POST", "/api/pages", wrong)[0] == 400, wrong


def kept_rounds(home):
    """A memory with two rounds of Starfall, the last one with its frame figures and the course of
    its temperatures, and a round of another game without frame rates; their ids."""
    import sqlite3

    from libre_panel.sensors.demo import demo_course
    from libre_panel.services.memory_store import SCHEMA

    folder = home / "plugins-data" / "memory"
    folder.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(folder / "memory.db")
    con.executescript(SCHEMA)
    levels = json.dumps(
        {"edges": [60, 90, 120, 144, 165], "seconds": [26, 52, 235, 1150, 1040, 107]}
    )
    starfall = {"game": "Starfall", "program": "starfall", "duration_s": 2700, "fps_s": 2610,
                "levels": levels, "closed": 1}  # fmt: skip
    rows = [
        {**starfall, "id": 1_700_000_000_000, "started": 1_700_000_000, "ended": 1_700_002_700,
         "fps_mean": 140.0, "cpu_temp_max": 72.0},
        {**starfall, "id": 1_700_090_000_000, "started": 1_700_090_000, "ended": 1_700_092_700,
         "fps_mean": 148.3, "cpu_temp_max": 74.1, "power_max": 468.0, "fps_1low": 97.0,
         "fps_01low": 71.0, "frametime_avg": 6.76, "frametime_max": 38.0,
         "course": json.dumps(demo_course())},
        {"id": 1_700_100_000_000, "game": "Puzzle: Box?", "program": "puzzle",
         "started": 1_700_100_000, "ended": 1_700_100_600, "duration_s": 600, "fps_s": 0,
         "closed": 1},
    ]  # fmt: skip
    for row in rows:
        names = ", ".join(row)
        con.execute(f"INSERT INTO sessions ({names}) VALUES ({','.join('?' * len(row))})",
                    list(row.values()))  # fmt: skip
    con.commit()
    con.close()
    return [row["id"] for row in rows]


def test_a_round_of_history_is_kept_as_a_picture_of_its_report(
    server, isolated_home, tmp_path, monkeypatch
):
    """Live → History's *Keep as a picture*: the round's report as the panel shows it, in a
    picture of the panel, in the Pictures folder, which opens; again: a file of its own."""
    from io import BytesIO
    from pathlib import Path

    from PIL import Image

    from libre_panel import tray
    from libre_panel.config import set_config_value
    from libre_panel.editor import server as editor_server
    from libre_panel.services import downloads

    first, last, puzzle = kept_rounds(isolated_home)
    pictures, opened, themes = tmp_path / "Pictures", [], []
    monkeypatch.setattr(downloads, "pictures_folder", lambda: pictures)
    monkeypatch.setattr(tray, "open_path", opened.append)
    made = editor_server.Renderer

    def renderer(theme, **kw):
        themes.append(theme.root.name)
        return made(theme, **kw)

    monkeypatch.setattr(editor_server, "Renderer", renderer)
    status, data = request(server, "GET", "/api/history?span=month")
    assert status == 200 and puzzle not in {r["id"] for r in data["rounds"] if r["fps"]}

    status, data = request(server, "POST", "/api/history/picture", {"id": last})
    assert status == 200, data
    folder = pictures / "Libre Panel"
    assert data == {"file": data["file"], "folder": str(folder), "opened": True}
    assert opened == [folder] and themes == ["round-report"]  # the report page's own
    kept = Path(data["file"])
    assert kept.parent == folder and kept.name.startswith("Starfall 2023-11-1")
    assert kept.suffix == ".png"
    picture = Image.open(BytesIO(kept.read_bytes()))
    assert picture.width > 1920 and picture.height > 480  # the panel in a frame, as photographed
    assert 2 < picture.width / picture.height < 1920 / 480
    assert len(picture.convert("L").getcolors(65536) or ()) > 50  # the report on it, not a blank

    again = request(server, "POST", "/api/history/picture", {"id": last})[1]
    assert Path(again["file"]).name == kept.name.replace(".png", " (2).png")  # nothing written over
    set_config_value("device.model", "turing-12.3-usb")  # its version for the 12.3"
    set_config_value("services.memory.report_theme", "round-report")
    assert request(server, "POST", "/api/history/picture", {"id": first})[0] == 200
    assert themes[-1] == "round-report-1920x720"

    status, data = request(server, "POST", "/api/history/picture", {"id": puzzle})
    assert status == 404 and "no frame rates" in data["error"]  # no report without them
    assert request(server, "POST", "/api/history/picture", {"id": 5})[0] == 404
    for wrong in ({"id": "1700"}, {"id": True}, {}, [1]):
        assert request(server, "POST", "/api/history/picture", wrong)[0] == 400, wrong
    assert len(list(folder.iterdir())) == 3


def test_pages_say_whether_a_theme_was_chosen(server, isolated_home):
    """A first start has no theme line in config.toml: Start then asks which look to use.
    Choosing one (or any other way of writing the line) ends it."""
    assert request(server, "GET", "/api/pages")[1]["chosen"] is False  # no config.toml at all
    isolated_home.mkdir(parents=True, exist_ok=True)
    (isolated_home / "config.toml").write_text("fps = 5\n", encoding="utf-8")
    assert request(server, "GET", "/api/pages")[1]["chosen"] is False  # a file without the line
    status, data = request(server, "POST", "/api/pages", {"page": "normal", "theme": "slate"})
    assert status == 200 and data["chosen"] is True
    assert request(server, "GET", "/api/pages")[1]["chosen"] is True


def test_theme_pictures_and_files(server, isolated_home):
    from io import BytesIO

    from PIL import Image

    status, body = request(server, "GET", "/api/themes/spur-ii/preview?w=320")
    assert status == 200 and Image.open(BytesIO(body)).size[0] == 320
    assert request(server, "GET", "/api/themes/nope/preview")[0] == 404
    request(server, "POST", "/api/themes/mine", {"theme": theme_dict(), "source": "libre-default"})
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    request(server, "POST", "/api/themes/mine/assets?name=logo.png", png,
            {"Content-Type": "application/octet-stream"})  # fmt: skip
    status, body = request(server, "GET", "/api/themes/mine/assets/assets/logo.png")
    assert status == 200 and body == png
    (isolated_home / "themes" / "mine" / "notes.txt").write_text("not an asset")
    assert request(server, "GET", "/api/themes/mine/assets/notes.txt")[0] == 404
    assert request(server, "GET", "/api/themes/mine/assets/..%2F..%2Fconfig.toml")[0] == 404


def test_the_theme_list_names_each_theme(server, isolated_home):
    """Lists and galleries show a theme's name and panel, from the list itself."""
    (isolated_home / "themes" / "broken").mkdir(parents=True)
    (isolated_home / "themes" / "broken" / "theme.json").write_text("{not json")
    _, themes = request(server, "GET", "/api/themes")
    found = {item["id"]: item for item in themes}
    classic = found["spur-classic"]
    assert classic["name"] == "SPUR Classic" and classic["builtin"]
    assert classic["size"] == [1920, 480] and classic["model"]
    assert classic["collection"] == "SPUR II" and found["studio"]["collection"] == ""
    assert found["broken"]["name"] == "broken" and found["broken"]["size"] is None


def test_building_blocks_in_the_theme_colours(server):
    from libre_panel.editor.presets import PRESETS

    palette = {"bg": "#101010", "accent": "#ff8800"}
    status, data = request(server, "POST", "/api/preset-previews", {"palette": palette})
    assert status == 200 and set(data["previews"]) == {p["id"] for p in PRESETS}
    assert base64.b64decode(data["previews"]["ring"])[:4] == b"\x89PNG"


def test_specs_name_the_primary_fields(server):
    _, specs = request(server, "GET", "/api/specs")
    assert specs["primary"]["gauge"][:3] == ["sensor", "finish", "color"]
    for kind, keys in specs["primary"].items():
        fields = {**specs["common"], **specs["widgets"][kind]}
        assert set(keys) <= set(fields), kind
        assert not set(keys) & {"x", "y", "w", "h"}, kind  # set on the canvas


def test_specs_say_where_each_finish_shows(server):
    _, specs = request(server, "GET", "/api/specs")
    finishes = specs["finishes"]
    assert set(finishes) == {"lit", "facet", "acrylic"}  # flat draws everything
    assert {"gauge", "bar", "graph", "module"} <= set(finishes["lit"])
    assert "clock" in finishes["facet"] and "clock" not in finishes["lit"]
    assert "text" in finishes["acrylic"] and "bar" not in finishes["acrylic"]


def test_plugin_pages_and_panel_pages_are_apart(server):
    status, data = request(server, "GET", "/api/plugins/pages")
    assert status == 200 and isinstance(data, list)  # the editor pages plugins bring
    assert all({"id", "title"} <= set(page) for page in data)


# -- the names of the hardware -------------------------------------------------------------------


@pytest.fixture
def editor():
    """The editor server (``port``, ``state``), polled every 10 ms so that ending it does not
    take half a second, as it does for ``server``."""
    srv = make_server(port=0)
    kwargs = {"poll_interval": 0.01}
    threading.Thread(target=srv.serve_forever, kwargs=kwargs, daemon=True).start()
    yield SimpleNamespace(port=srv.server_address[1], state=srv.editor_state)
    srv.shutdown()
    srv.server_close()
    srv.editor_state.close()


@pytest.fixture
def pc(monkeypatch):
    """A computer for /api/hardware to find, and how often it was asked."""
    asked = []
    monkeypatch.setattr(hardware, "_found", None)
    monkeypatch.setattr(hardware, "_scan_machine", lambda *args: asked.append(1) or PC)
    return asked


def names(editor, body=None, query="", **options):
    """GET /api/hardware, or POST ``body`` to it."""
    method = "GET" if body is None else "POST"
    return request(editor.port, method, "/api/hardware" + query, body, **options)


def hardware_state(custom=("", "", "")):
    parts = zip(("cpu", "gpu", "memory"), custom, strict=True)
    return {part: {"detected": TIDY[part], "custom": text} for part, text in parts}


def parts(data):
    """The three parts of an answer of /api/hardware (its fans aside)."""
    return {part: data[part] for part in ("cpu", "gpu", "memory")}


def written(isolated_home):
    return tomllib.loads((isolated_home / "config.toml").read_text(encoding="utf-8"))


def test_the_hardware_names_are_what_is_found_and_what_the_user_wrote(editor, pc, isolated_home):
    status, data = names(editor)
    assert status == 200 and parts(data) == hardware_state()
    assert data["cpu"]["detected"] == "Ryzen 5 7600"  # tidied, as the panel shows it
    assert not (isolated_home / "config.toml").exists()  # asking writes nothing


def test_the_users_text_is_written_to_config_toml_and_comes_back(editor, pc, isolated_home):
    status, data = names(editor, {"cpu": "My CPU"})
    assert status == 200 and parts(data) == hardware_state(("My CPU", "", ""))
    assert names(editor) == (200, data)
    assert written(isolated_home)["hardware"] == {"cpu_name": "My CPU"}
    assert "# Libre Panel configuration" in (isolated_home / "config.toml").read_text("utf-8")
    status, data = names(editor, {"gpu": "My card", "memory": "2x16 GB"})
    assert status == 200 and parts(data) == hardware_state(("My CPU", "My card", "2x16 GB"))
    assert written(isolated_home)["hardware"] == {
        "cpu_name": "My CPU", "gpu_name": "My card", "memory_name": "2x16 GB",
    }  # fmt: skip


def test_the_fans_are_named_in_the_app_and_written_to_config_toml(
    monkeypatch, editor, pc, isolated_home
):
    """Each fan this computer reads, with its speed now (which tells one from the other), the
    name it goes by without one of the user's own, and the user's, kept in config.toml."""
    from libre_panel.editor.server import EditorState
    from libre_panel.sensors.base import Reading, Snapshot
    from libre_panel.sensors.fan_names import FanNames

    raw = "lhm:/lpc/chip0000/0/fan/0"
    live = Snapshot(
        readings={
            raw: Reading(raw, 820.0, "RPM", "Fan #1"),
            "fan.lpc.fan_1": Reading("fan.lpc.fan_1", 820.0, "RPM", "Fan #1", origin=raw),
            "fan.lpc.fan_10": Reading("fan.lpc.fan_10", 0.0, "RPM", "Fan #10"),
            "fan.lpc.fan_2": Reading("fan.lpc.fan_2", 1140.4, "RPM", "Fan #2"),
        }
    )

    def named(self):  # the live readings go by their names, as the hub gives them
        names = FanNames()
        names.follow_config(load_config())
        return Snapshot(readings=names.apply(dict(live.readings)))

    monkeypatch.setattr(EditorState, "live_snapshot", named)
    status, data = names(editor)
    assert status == 200 and [fan["key"] for fan in data["fans"]] == [
        "fan.lpc.fan_1", "fan.lpc.fan_2", "fan.lpc.fan_10",
    ]  # fmt: skip
    assert data["fans"][0] == {
        "key": "fan.lpc.fan_1",
        "detected": "Fan #1",
        "custom": "",
        "value": 820,
        "look": "",  # as all fans
        "paint": "",
    }
    status, data = names(editor, {"fans": {"fan.lpc.fan_1": "  Intake ", "fan.lpc.fan_2": "CPU"}})
    assert status == 200 and data["fans"][0]["custom"] == "Intake"
    assert data["fans"][0]["detected"] == "Fan #1"  # what it goes back to
    assert written(isolated_home)["hardware"]["fans"] == [
        {"sensor": "fan.lpc.fan_1", "name": "Intake"}, {"sensor": "fan.lpc.fan_2", "name": "CPU"},
    ]  # fmt: skip
    assert parts(data) == hardware_state()  # the other names stay
    status, data = names(editor, {"fans": {"fan.lpc.fan_2": ""}})
    assert status == 200 and [f["custom"] for f in data["fans"]] == ["Intake", "", ""]
    assert written(isolated_home)["hardware"]["fans"] == [
        {"sensor": "fan.lpc.fan_1", "name": "Intake"}
    ]
    for wrong in ({"fans": []}, {"fans": {"": "x"}}, {"fans": {"fan.lpc.fan_1": 1}}):
        assert names(editor, wrong)[0] == 400, wrong


def test_the_fans_look_is_chosen_for_all_and_for_one_and_written_to_config_toml(
    editor, pc, isolated_home
):
    status, data = names(editor)
    assert status == 200 and data["fan_look"]["look"] == "" and data["fan_look"]["paint"] == ""
    assert [look["id"] for look in data["fan_look"]["looks"]] == [
        "noctua-g2", "noctua-a12x25", "noctua-a14", "libre",
    ]  # fmt: skip
    status, data = names(editor, {"fan_look": "noctua-a12x25", "fan_paint": "own"})
    assert status == 200 and data["fan_look"]["look"] == "noctua-a12x25"
    assert written(isolated_home)["hardware"]["fan_look"] == "noctua-a12x25"
    assert written(isolated_home)["hardware"]["fan_paint"] == "own"
    status, data = names(editor, {"fan_looks": {"fan.lpc.fan_2": "noctua-a14"}})
    assert status == 200
    assert written(isolated_home)["hardware"]["fans"] == [
        {"sensor": "fan.lpc.fan_2", "look": "noctua-a14"}  # a look of its own, without a name
    ]
    names(editor, {"fans": {"fan.lpc.fan_2": "Rear"}})
    assert written(isolated_home)["hardware"]["fans"] == [
        {"sensor": "fan.lpc.fan_2", "name": "Rear", "look": "noctua-a14"}
    ]
    names(editor, {"fan_looks": {"fan.lpc.fan_2": ""}})  # as all fans again
    assert written(isolated_home)["hardware"]["fans"] == [
        {"sensor": "fan.lpc.fan_2", "name": "Rear"}
    ]
    for wrong in ({"fan_look": "noctua-a15"}, {"fan_paint": "brown"},
                  {"fan_looks": {"fan.lpc.fan_2": "rgb"}}, {"fan_looks": []}):  # fmt: skip
        assert names(editor, wrong)[0] == 400, wrong


def test_each_fan_look_has_a_picture_for_its_tile(editor, pc):
    import io

    from PIL import Image

    for look in ("noctua-g2", "noctua-a12x25", "noctua-a14", "libre"):
        for paint in ("theme", "own"):
            status, body = request(editor.port, "GET", f"/api/fan-look?look={look}&paint={paint}")
            assert status == 200, (look, paint)
            image = Image.open(io.BytesIO(body))
            assert image.size[0] == image.size[1] >= 112 and image.getbbox() is not None
    assert request(editor.port, "GET", "/api/fan-look?look=rgb")[0] == 404


def test_each_look_of_the_weather_symbols_has_a_picture_for_its_tile(editor, pc):
    import io

    from PIL import Image

    pictures = {}
    for pack in ("classic", "vivid"):
        status, body = request(editor.port, "GET", f"/api/symbols-look?pack={pack}")
        assert status == 200, pack
        image = Image.open(io.BytesIO(body))
        assert image.size == (360, 120) and image.getbbox() is not None
        pictures[pack] = image.tobytes()
    assert pictures["classic"] != pictures["vivid"]
    assert request(editor.port, "GET", "/api/symbols-look?pack=retro")[0] == 404


def test_an_empty_text_gives_the_found_name_back_and_the_others_stay(editor, pc, isolated_home):
    names(editor, {"cpu": "My CPU", "gpu": "My card"})
    status, data = names(editor, {"cpu": ""})
    assert status == 200 and parts(data) == hardware_state(("", "My card", ""))
    assert written(isolated_home)["hardware"]["cpu_name"] == ""


def test_the_users_text_is_one_line_without_the_spaces_around_it(editor, pc, isolated_home):
    status, data = names(editor, {"gpu": "  My   card \n"})
    assert status == 200 and data["gpu"]["custom"] == "My card"
    assert written(isolated_home)["hardware"]["gpu_name"] == "My card"


def test_the_section_in_a_written_config_takes_the_text_and_the_rest_stays(editor, pc):
    from libre_panel.config import load_config, write_default_config

    path = write_default_config()
    before = path.read_text(encoding="utf-8")
    assert "[hardware]" in before  # the template has the section, its keys commented out
    assert names(editor, {"memory": 'Kingston "Fury" 32 GB'})[0] == 200
    after = path.read_text(encoding="utf-8")
    assert after.replace('memory_name = "Kingston \\"Fury\\" 32 GB"\n', "") == before
    assert load_config(path).hardware.memory_name == 'Kingston "Fury" 32 GB'
    assert "# cpu_name" in after and "[sensors.librehardwaremonitor]" in after


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        "text",
        {"cpu": 5},
        {"cpu": None},
        {"cpu": ["a"]},
        {"disk": "x"},
        {"cpu": "fine", "bogus": "y"},  # one wrong key refuses the whole request
        {"cpu": "x" * 101},
        b"{not json",
    ],
)
def test_a_request_that_is_not_names_changes_nothing(editor, pc, isolated_home, body):
    status, data = names(editor, body)
    assert status == 400 and data["error"]
    assert not (isolated_home / "config.toml").exists()


def test_the_longest_text_is_a_hundred_characters(editor, pc):
    assert names(editor, {"cpu": "x" * 100})[0] == 200
    status, data = names(editor, {"cpu": "x" * 101})
    assert status == 400 and "hardware.cpu_name: at most 100 characters" in data["error"]


def test_a_config_that_cannot_be_read_is_told_and_not_written_over(editor, pc, isolated_home):
    isolated_home.mkdir(parents=True)
    (isolated_home / "config.toml").write_text("this is = not [valid", encoding="utf-8")
    for status, data in (names(editor), names(editor, {"cpu": "My CPU"})):
        assert status == 400 and "config.toml" in data["error"]
    assert (isolated_home / "config.toml").read_text("utf-8") == "this is = not [valid"


def test_the_hardware_routes_keep_the_editors_protections(editor, pc):
    assert names(editor, host="evil.example")[0] == 403
    assert names(editor, {"cpu": "x"}, host="evil.example")[0] == 403
    assert names(editor, {"cpu": "x"}, headers={"X-Libre-Panel": "0"})[0] == 403


def test_the_computer_is_asked_once_and_again_when_the_editor_asks_for_it(editor, pc):
    names(editor)
    names(editor)
    assert len(pc) == 1
    names(editor, query="?refresh=0")
    assert len(pc) == 1
    assert names(editor, query="?refresh=1")[0] == 200
    assert len(pc) == 2
    assert names(editor, query="?refresh")[0] == 200
    assert len(pc) == 3


def test_the_card_librehardwaremonitor_is_told_to_use_is_the_card_that_is_found(
    editor, monkeypatch, isolated_home
):
    from fake_hardware import gigabytes

    from libre_panel.sensors.hardware import Adapter, Found

    cards = (
        Adapter("NVIDIA GeForce RTX 4070", gigabytes(12)),
        Adapter("AMD Radeon RX 6800", gigabytes(16)),
    )
    monkeypatch.setattr(hardware, "_found", None)
    monkeypatch.setattr(hardware, "_scan_machine", lambda *args: Found(adapters=cards))
    assert names(editor)[1]["gpu"]["detected"] == "Radeon RX 6800"
    isolated_home.mkdir(parents=True)
    (isolated_home / "config.toml").write_text(
        '[sensors.librehardwaremonitor]\ngpu = "RTX 4070"\n', encoding="utf-8"
    )
    assert names(editor)[1]["gpu"]["detected"] == "GeForce RTX 4070"


def test_the_editors_own_sensors_follow_the_names_it_wrote(editor, pc):
    """A plain `libre-panel editor` reads its previews from a hub of its own."""
    assert editor.state.live_snapshot().value("cpu.name") == "Ryzen 5 7600"
    assert names(editor, {"cpu": "My CPU"})[0] == 200
    assert editor.state.live_snapshot().value("cpu.name") == "My CPU"
    assert names(editor, {"cpu": ""})[0] == 200
    assert editor.state.live_snapshot().value("cpu.name") == "Ryzen 5 7600"


def test_the_sensor_list_has_the_three_names(server):
    status, sensors = request(server, "GET", "/api/sensors")
    keys = {sensor["key"] for sensor in sensors}
    assert status == 200 and {"cpu.name", "gpu.name", "mem.name"} <= keys


def test_on_windows_the_port_is_this_servers_alone():
    """SO_REUSEADDR on Windows lets another program bind the port and read the token."""
    from libre_panel.editor.server import hold_port

    class Socket:
        def __init__(self):
            self.options = []

        def setsockopt(self, *option):
            self.options.append(option)

    import socket

    sock = Socket()
    exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
    if exclusive is None:  # not Windows: the constant is not there to set
        socket.SO_EXCLUSIVEADDRUSE = -5
    try:
        assert hold_port(sock, "win32") is False
        assert sock.options == [(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)]
    finally:
        if exclusive is None:
            del socket.SO_EXCLUSIVEADDRUSE
    linux = Socket()
    assert hold_port(linux, "linux") is True and linux.options == []


def test_the_panel_test_and_the_log_have_their_routes(editor, monkeypatch, isolated_home):
    """Live → Diagnose: the panel test's state and actions, the log's entries, the whole log
    to keep (a link: the token in its address) and the detail it is written with."""
    import logging
    from pathlib import Path

    from libre_panel import logview

    status, body = request(editor.port, "GET", "/api/doctor")
    assert status == 200 and body["state"] == "idle" and body["steps"] == []
    assert request(editor.port, "POST", "/api/doctor", {"action": "explode"})[0] == 400
    status, body = request(editor.port, "POST", "/api/doctor", {"action": "answer", "answer": {}})
    assert status == 409  # nothing is asked

    log = isolated_home / "logs" / "libre-panel.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "2026-10-14 10:00:00,001 INFO libre_panel.app: showing theme 'lumen'\n"
        f"2026-10-14 10:00:01,002 ERROR libre_panel.app: broken: {Path.home() / 'x.toml'}\n",
        "utf-8",
    )
    monkeypatch.setattr(logview, "log_file", lambda: log)
    status, body = request(editor.port, "GET", "/api/logs?level=warning")
    assert status == 200 and [e["level"] for e in body["entries"]] == ["ERROR"]
    assert "~" in body["entries"][0]["message"] and str(Path.home()) not in json.dumps(body)
    status, body = request(editor.port, "GET", "/api/logs/file")
    assert status == 200 and b"showing theme" in body and b"broken: ~" in body
    root = logging.getLogger()
    before = root.level
    try:
        status, body = request(editor.port, "POST", "/api/logs", {"verbose": True})
        assert status == 200 and body == {"verbose": True}
        assert request(editor.port, "POST", "/api/logs", {"verbose": "yes"})[0] == 400
    finally:
        root.setLevel(before)


@pytest.mark.parametrize(("panels", "named"), [("", True), ("beside", False), ("first", True)])
def test_a_standby_clip_written_is_named_in_its_own_panel_s_settings(panels, named, tmp_path):
    """On the person's click: the first panel's video mode then plays it. Written to a panel
    beside the first (a 9.2" beside a 12.3"), it is not the first one's."""
    path = tmp_path / "config.toml"
    first = "turing-12.3-usb" if panels == "beside" else "turing-9.2-usb"
    text = f'[device]\nmodel = "{first}"\n'
    if panels:
        text += '[[panels]]\nmodel = "turing-9.2-usb"\n' if panels == "beside" else (
            '[[panels]]\nmodel = "turing-12.3-usb"\n')  # fmt: skip
    path.write_text(text, encoding="utf-8")
    srv = make_server(port=0, config_path=path)
    written = []
    srv.editor_state.start_screen_jobs = SimpleNamespace(
        write_clip=lambda brightness, model, mounted, where: (
            written.append(model)
            or {"model": "turing-9.2-usb", "standby_clip": 3, "name": "standby.h264"}
        ),
    )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        port = srv.server_address[1]
        status, body = request(port, "POST", "/api/start-screen", {"action": "clip"})
    finally:
        srv.shutdown()
        srv.server_close()
        srv.editor_state.close()
    assert status == 200 and body["standby_clip"] == 3 and written
    video = load_config(path).device.video
    assert (video.local_clip == STANDBY_CLIP) is named and video.clip_asked is named


def test_panel_two_gets_its_own_start_screen_and_its_clip_in_its_own_settings(tmp_path):
    """The Start screen of the panel chosen at the top: panel 2 (a 9.2" on end beside a 12.3")
    is opened where its loop finds it, drawn upright and in its own brightness, and its clip is
    named in its own [[panels]] video, not in the first panel's; two panels alike: the second
    passes over the first's."""
    path = tmp_path / "config.toml"
    path.write_text('[device]\nmodel = "turing-12.3-usb"\n[[panels]]\nmodel = "turing-9.2-usb"\n'
                    'orientation = "portrait"\nbrightness = 40\nfollow_brightness = false\n'
                    '[[panels]]\nmodel = "turing-9.2-usb"\n', encoding="utf-8")  # fmt: skip
    srv = make_server(port=0, config_path=path)
    asked = []

    def write_clip(brightness, model, mounted, where):
        asked.append((brightness, mounted, where))
        return {"model": "turing-9.2-usb", "standby_clip": 3, "name": STANDBY_CLIP}

    srv.editor_state.start_screen_jobs = SimpleNamespace(
        write_clip=write_clip,
        write_logo=lambda model, mounted, where: asked.append((mounted, where)) or {"boot_logo": 9},
    )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        port = srv.server_address[1]
        status, body = request(port, "GET", "/api/start-screen?panel=1")
        assert status == 200 and body["panel"] == 1 and body["model"] == "turing-9.2-usb"
        assert body["orientation"] == "portrait"
        status, body = request(port, "POST", "/api/start-screen", {"action": "clip", "panel": 1})
        assert status == 200 and body["standby_clip"] == 3
        status, _ = request(port, "POST", "/api/start-screen", {"action": "logo", "panel": 2})
        assert status == 200
        assert request(port, "POST", "/api/start-screen", {"action": "logo", "panel": 3})[0] == 400
        assert (
            request(port, "POST", "/api/start-screen", {"action": "logo", "panel": "2"})[0] == 400
        )
    finally:
        srv.shutdown()
        srv.server_close()
        srv.editor_state.close()
    beside = ("turing-12.3-usb", "turing-9.2-usb")
    assert asked[0] == (40, "portrait", (beside, 0))  # its own brightness, upright
    assert asked[1] == ("landscape", (beside, 1))  # the second 9.2": past the first one's
    config = load_config(path)
    assert config.device.video.local_clip == "" and not config.device.video.clip_asked
    assert config.panels[0].local_clip == STANDBY_CLIP and config.panels[0].clip_asked
    assert config.panels[1].local_clip == "" and config.panels[0].device.brightness == 40


def test_the_pages_of_a_panel_beside_the_first_are_read_and_written(tmp_path):
    """Design about panel 2: its theme, and those of a game, music and an alarm; one without its
    own follows panel 1's theme with its companion."""
    path = tmp_path / "config.toml"
    beside = 'model = "turing-9.2-usb"\norientation = "portrait"\ntheme = "lumen-companion"\n'
    path.write_text(f'theme = "lumen"\n[[panels]]\n{beside}', encoding="utf-8")
    srv = make_server(port=0, config_path=path)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        port = srv.server_address[1]
        status, body = request(port, "GET", "/api/pages?panel=2")
        assert status == 200 and body["panel"] == 2
        pages = {page["id"]: page for page in body["pages"]}
        assert list(pages) == ["normal", "game", "media", "alarm"]
        assert pages["normal"]["theme"] == "lumen-companion" and pages["normal"]["companion"]
        assert pages["media"]["theme"] is None and pages["media"]["follows"]
        assert pages["alarm"]["theme"] == "lumen-companion" and not pages["alarm"]["follows"]
        change = {"panel": 2, "page": "media", "theme": "encore-companion"}
        status, body = request(port, "POST", "/api/pages", change)
        assert status == 200 and load_config(path).panels[0].media_theme == "encore-companion"
        status, _ = request(port, "POST", "/api/pages", {**change, "theme": ""})
        assert status == 200 and load_config(path).panels[0].media_theme == ""
        assert request(port, "POST", "/api/pages", {**change, "panel": 5})[0] == 404
        assert request(port, "GET", "/api/pages?panel=3")[0] == 404
        assert load_config(path).theme == "lumen"  # the first panel's is as it was
    finally:
        srv.shutdown()
        srv.server_close()
        srv.editor_state.close()
