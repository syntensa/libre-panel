import pytest

from libre_panel.devices.models import MODELS, get_model, model_for_size, models_for_usb
from libre_panel.theme.adapt import adapt_theme
from libre_panel.theme.model import find_theme, load_theme, parse_theme


def test_ids_are_unique():
    ids = [m.id for m in MODELS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "model, landscape",
    [
        ("turing-3.5", (480, 320)),
        ("turing-5", (800, 480)),
        ("turing-8.8-usb", (1920, 480)),
        ("turing-9.2-usb", (1920, 480)),  # 480, not the 462 of the reference library
        ("turing-12.3-usb", (1920, 720)),
        ("turing-2.1", (480, 480)),
    ],
)
def test_sizes(model, landscape):
    panel = get_model(model)
    assert panel.size("landscape") == landscape
    assert panel.size("portrait") == landscape[::-1]


def test_usb_lookup():
    assert [m.id for m in models_for_usb(0x1CBE, 0x0092)] == ["turing-9.2-usb"]
    shared = {m.id for m in models_for_usb(0x1A86, 0x5722)}
    assert {"turing-3.5", "xuanfang-3.5"} <= shared
    assert [m.id for m in models_for_usb(0x1A86, 0x5722, "2017-2-25")] == ["xuanfang-3.5"]
    assert models_for_usb(None, None) == []


def test_driver_status():
    assert {m.driver for m in MODELS if m.protocol == "usb-turing"} <= {"unverified", "supported"}
    assert get_model("turing-3.5").driver == "unverified"  # rev. A
    assert get_model("usbpcmonitor-7").driver == "unverified"
    assert get_model("turing-5").driver == "planned"  # rev. C


def test_model_for_size():
    assert model_for_size(320, 480).id == "turing-3.5"


def test_adapt_scales_and_keeps_round_things_round():
    data = load_theme(find_theme("libre-default")).to_dict()
    result = adapt_theme(data, "turing-9.2-usb", "landscape")
    theme = parse_theme(result)
    assert (theme.width, theme.height) == (1920, 480)
    for widget in theme.widgets:
        if widget["type"] == "gauge":
            assert widget["w"] == widget["h"]
        assert 0 <= widget["x"] <= theme.width and 0 <= widget["y"] <= theme.height


def test_adapt_keep_mode_and_custom_size():
    data = load_theme(find_theme("libre-default")).to_dict()
    kept = adapt_theme(data, "custom", "landscape", mode="keep", width=640, height=400)
    assert kept["display"]["width"] == 640
    assert kept["widgets"] == data["widgets"]
    with pytest.raises(ValueError):
        adapt_theme(data, "custom", "landscape")


def test_hidden_edges_follow_the_frame_into_the_framebuffer():
    """The catalog gives the strip in the framebuffer; a frame's edges get there as
    the USB driver turns them (the doctor saw the 9.2"'s strip at the top in
    landscape and on the left in portrait)."""
    from PIL import Image, ImageDraw

    from libre_panel.devices.turzx_usb import to_native

    model = get_model("turing-9.2-usb")
    assert model.hidden_edges("landscape") == {"top": 18, "right": 0, "bottom": 0, "left": 0}
    assert model.hidden_edges("portrait") == {"top": 0, "right": 0, "bottom": 0, "left": 18}
    for orientation in ("landscape", "portrait"):
        w, h = model.size(orientation)
        frame = Image.new("RGB", (w, h))
        draw = ImageDraw.Draw(frame)
        edges = model.hidden_edges(orientation)
        rects = {
            "top": (0, 0, w - 1, edges["top"] - 1),
            "left": (0, 0, edges["left"] - 1, h - 1),
        }
        for edge, px in edges.items():
            if px:
                draw.rectangle(rects[edge], fill="red")
        native = to_native(frame)
        top, right, bottom, left = model.hidden
        red = native.getchannel("R").point(lambda v: 255 if v else 0).getbbox()
        assert red == (native.width - right, 0, native.width, native.height), orientation
    assert model.to_dict()["hidden"]["landscape"]["top"] == 18
    assert get_model("turing-5").hidden_edges("landscape") == dict.fromkeys(
        ("top", "right", "bottom", "left"), 0
    )


def test_built_in_layouts_keep_clear_of_hidden_strips():
    """Content widgets draw nothing a panel's bezel hides (backgrounds may run under it)."""
    import copy
    import json

    from PIL import ImageChops

    from libre_panel.devices.models import find_model
    from libre_panel.render.renderer import Renderer
    from libre_panel.sensors.base import SensorHub
    from libre_panel.sensors.demo import DemoProvider
    from libre_panel.theme.model import THEME_FILENAME, list_themes

    snapshot = SensorHub([DemoProvider({})]).snapshot()
    checked = 0
    for entry in list_themes():
        root = find_theme(entry["id"])
        data = json.loads((root / THEME_FILENAME).read_text(encoding="utf-8"))
        theme = parse_theme(data, root)
        model = find_model(data.get("display", {}).get("model"))
        if model is None:
            continue
        hidden = model.hidden_edges(theme.orientation)
        if not any(hidden.values()):
            continue
        w, h = theme.width, theme.height
        visible = (hidden["left"], hidden["top"], w - hidden["right"], h - hidden["bottom"])
        full, boxes = Renderer(theme).render(snapshot, 1000.0)
        for widget in data["widgets"]:
            x, y, bw, bh = boxes.get(widget["id"], (0, 0, 0, 0))
            left, top, right, bottom = visible
            box_inside = x >= left and y >= top and x + bw <= right and y + bh <= bottom
            if widget["type"] == "rect" or box_inside:
                checked += 1
                continue
            # the layout box reaches the strip (it includes glow): check the drawn pixels
            without = copy.deepcopy(data)
            without["widgets"] = [x for x in without["widgets"] if x["id"] != widget["id"]]
            frame = Renderer(parse_theme(without, root)).render(snapshot, 1000.0)[0]
            drawn = ImageChops.difference(full, frame).convert("L").point(lambda v: v > 24 and 255)
            box = drawn.getbbox()
            if box:
                inside = (
                    box[0] >= visible[0]
                    and box[1] >= visible[1]
                    and box[2] <= visible[2]
                    and box[3] <= visible[3]
                )
                assert inside, f"{entry['id']}: {widget['id']} draws at {box}, visible {visible}"
            checked += 1
    assert checked > 10  # spur-ii on the 9.2"
