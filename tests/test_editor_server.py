import base64
import http.client
import json
import threading
import tomllib
from types import SimpleNamespace

import pytest
from fake_hardware import PC, TIDY

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


def written(isolated_home):
    return tomllib.loads((isolated_home / "config.toml").read_text(encoding="utf-8"))


def test_the_hardware_names_are_what_is_found_and_what_the_user_wrote(editor, pc, isolated_home):
    status, data = names(editor)
    assert status == 200 and data == hardware_state()
    assert data["cpu"]["detected"] == "Ryzen 5 7600"  # tidied, as the panel shows it
    assert not (isolated_home / "config.toml").exists()  # asking writes nothing


def test_the_users_text_is_written_to_config_toml_and_comes_back(editor, pc, isolated_home):
    status, data = names(editor, {"cpu": "My CPU"})
    assert status == 200 and data == hardware_state(("My CPU", "", ""))
    assert names(editor) == (200, data)
    assert written(isolated_home)["hardware"] == {"cpu_name": "My CPU"}
    assert "# Libre Panel configuration" in (isolated_home / "config.toml").read_text("utf-8")
    status, data = names(editor, {"gpu": "My card", "memory": "2x16 GB"})
    assert status == 200 and data == hardware_state(("My CPU", "My card", "2x16 GB"))
    assert written(isolated_home)["hardware"] == {
        "cpu_name": "My CPU", "gpu_name": "My card", "memory_name": "2x16 GB",
    }  # fmt: skip


def test_an_empty_text_gives_the_found_name_back_and_the_others_stay(editor, pc, isolated_home):
    names(editor, {"cpu": "My CPU", "gpu": "My card"})
    status, data = names(editor, {"cpu": ""})
    assert status == 200 and data == hardware_state(("", "My card", ""))
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
