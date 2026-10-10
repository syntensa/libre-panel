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
    assert status == 200 and b"Theme Editor" in body
    status, specs = request(server, "GET", "/api/specs")
    assert status == 200
    assert "gauge" in specs["widgets"]
    assert any(m["id"] == "turing-9.2-usb" for m in specs["models"])


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
