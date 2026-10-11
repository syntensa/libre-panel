"""The theme editor and the dust: its form comes from the model, its preview shows the grains."""

import base64
import http.client
import json
import threading
from io import BytesIO

import pytest
from PIL import Image, ImageChops

from libre_panel.editor.server import make_server
from libre_panel.theme.model import DUST_LIMITS, DUST_SPEC, find_theme, load_theme


@pytest.fixture
def editor():
    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()
    server.editor_state.close()


def ask(port, method, path, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    headers = {
        "Host": f"127.0.0.1:{port}",
        "X-Libre-Panel": "1",
        "Content-Type": "application/json",
    }
    data = None if body is None else json.dumps(body).encode()
    connection.request(method, path, body=data, headers=headers)
    response = connection.getresponse()
    answer = json.loads(response.read())
    connection.close()
    return response.status, answer


def preview(port, theme):
    status, data = ask(port, "POST", "/api/render", {"theme": theme, "base": "libre-default"})
    assert status == 200
    return Image.open(BytesIO(base64.b64decode(data["png"]))).convert("RGB"), data


def test_specs_hand_the_dust_form_to_the_editor(editor):
    """The editor draws the fields the model lists, with the limits the model checks."""
    status, specs = ask(editor, "GET", "/api/specs")
    assert status == 200
    dust = specs["dust"]
    assert set(dust["fields"]) == set(DUST_SPEC) and set(dust["limits"]) == set(DUST_LIMITS)
    assert dust["fields"]["count"] == ["int", 1800]
    assert dust["limits"]["rate"] == [1, 60]
    assert dust["max_colors"] >= 1 and dust["max_areas"] >= 1


def test_specs_carry_the_fan_and_the_facet_finish(editor):
    _, specs = ask(editor, "GET", "/api/specs")
    assert specs["widgets"]["fan"]["max_rpm"] == ["number", 3000]
    assert specs["widgets"]["fan"]["frame"][0] == "enum:ring|square|none"
    for kind in ("text", "metric", "bar", "gauge", "graph"):
        assert "facet" in specs["widgets"][kind]["finish"][0], kind


def test_the_preview_shows_the_dust_and_switching_it_off_hides_it(editor):
    theme = {
        "format": "libre-panel-theme/1",
        "name": "still",
        "display": {"width": 480, "height": 320},
        "widgets": [
            {"type": "rect", "id": "a", "x": 40, "y": 60, "w": 360, "h": 80, "color": "#ff8800"}
        ],
    }  # nothing here moves, so two pictures differ only by the dust
    plain, _ = preview(editor, theme)
    theme["look"] = {"dust": {"count": 1800}}
    dusty, data = preview(editor, theme)
    assert data["warnings"] == []
    assert ImageChops.difference(plain, dusty).getbbox() is not None
    theme["look"]["dust"]["enabled"] = False  # the settings stay, the grains go
    off, _ = preview(editor, theme)
    assert ImageChops.difference(plain, off).getbbox() is None


def test_the_look_survives_a_save(editor, isolated_home):
    """Saving writes the dust the editor edited, areas and colours included."""
    theme = load_theme(find_theme("libre-default")).to_dict()
    area = {"x": 0, "y": 0, "w": 100, "h": 50, "dust": False}
    theme["look"] = {"dust": {"count": 900, "colors": ["#ff8800"], "areas": [area]}}
    status, _ = ask(
        editor, "POST", "/api/themes/dusty", {"theme": theme, "source": "libre-default"}
    )
    assert status == 200
    saved = load_theme(find_theme("dusty"))
    assert saved.look["dust"]["count"] == 900
    assert saved.look["dust"]["colors"] == ["#ff8800"] and saved.look["dust"]["areas"] == [area]
