import base64
import http.client
import json
import threading

import pytest

from libre_panel.editor.server import make_server
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
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    hdrs = {"Host": host or f"127.0.0.1:{port}"}
    if method == "POST":
        hdrs["X-Libre-Panel"] = "1"
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
    conn.request(method, path, body=data, headers=hdrs)
    response = conn.getresponse()
    raw = response.read()
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
    status, body = request(server, "GET", "/classic/")
    assert status == 200 and b"Theme Editor" in body  # the editor before it, until it is retired
    status, specs = request(server, "GET", "/api/specs")
    assert status == 200
    assert "gauge" in specs["widgets"]
    assert any(m["id"] == "turing-9.2-usb" for m in specs["models"])
    band = specs["toasts"]["band"]  # the built-in styles besides the card, with their options
    assert band["label"] == "Band (built in)" and band["options"]["roll_in"] == ["number", 0.45]


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


def test_render_reports_the_grid_and_module_cells(server):
    status, data = request(server, "POST", "/api/render", {"theme": theme_dict()})
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
    for kind in ("bar", "gauge", "graph", "icon", "text", "metric", "module"):
        assert specs["widgets"][kind]["finish"] == list(FINISH_FIELD)
    assert FINISH_FIELD[1] == "flat" and "lit" in FINISH_FIELD[0]
    assert "finish" not in specs["widgets"]["rect"]


def test_module_previews_follow_the_look(server):
    from libre_panel.theme.modules import look_theme_parts

    palette, style = look_theme_parts("paper")
    status, data = request(
        server, "POST", "/api/module-previews", {"palette": palette, "style": style}
    )
    assert status == 200 and len(data["previews"]) == len(MODULE_KINDS)
    assert base64.b64decode(data["previews"]["ring"])[:4] == b"\x89PNG"


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


def test_pages_of_the_panel(server, isolated_home):
    """The normal theme, each mode's (game mode's always) and the round report's."""
    from libre_panel.config import load_config

    status, data = request(server, "GET", "/api/pages")
    assert status == 200 and data["shown"] is None and data["mode"] is None
    pages = {page["id"]: page for page in data["pages"]}
    assert pages["normal"]["theme"] == "libre-default"
    assert pages["game"] == {"id": "game", "mode": "game", "theme": None, "fps": None}
    assert pages["report"]["theme"] == "round-report" and pages["report"]["on"] is False

    game = {"page": "game", "theme": "slate", "fps": 30}
    status, data = request(server, "POST", "/api/pages", game)
    pages = {page["id"]: page for page in data["pages"]}
    assert status == 200 and (pages["game"]["theme"], pages["game"]["fps"]) == ("slate", 30)
    status, data = request(server, "POST", "/api/pages", {"page": "report", "theme": "orbit"})
    assert status == 200 and data["pages"][-1]["theme"] == "orbit"
    status, data = request(server, "POST", "/api/pages", {"page": "vr", "theme": "pico"})
    assert status == 200 and [p["id"] for p in data["pages"]] == ["normal", "game", "vr", "report"]
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
    ):
        assert request(server, "POST", "/api/pages", wrong)[0] == 400, wrong


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
