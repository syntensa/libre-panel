import pytest

from libre_panel.devices.models import MODELS, get_model, model_for_size, models_for_usb
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Snapshot
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.adapt import adapt_theme
from libre_panel.theme.model import find_theme, list_themes, load_theme, parse_theme


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
    assert get_model("turing-3.5").driver == "supported"  # rev. A, an owner's report
    assert get_model("usbpcmonitor-7").driver == "unverified"  # rev. A too, not seen yet
    assert get_model("turing-5").driver == "unverified"  # rev. C
    assert get_model("turing-9.2-usb").driver == "supported"  # stills and video, on the panel
    assert get_model("turing-8.8-usb").driver == "supported"  # an owner's report
    assert get_model("turing-12.3-usb").driver == "supported"  # the maintainer's
    assert get_model("turing-8.8").driver == "supported"  # the older one, a serial rev. C
    assert {m.driver for m in MODELS} <= {"unverified", "supported"}  # every family has one


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


@pytest.mark.parametrize("name", sorted(p["id"] for p in list_themes()))
def test_every_built_in_theme_adapts_to_every_panel_either_way_round(name):
    """Scaling must not carry a value past what a theme may hold (the Pico's glow, 12 px
    on the 0.96", went past the 64 a glow may have on the 5.2")."""
    data = load_theme(find_theme(name)).to_dict()
    for model in MODELS:
        for orientation in ("landscape", "portrait"):
            theme = parse_theme(adapt_theme(data, model.id, orientation))
            assert (theme.width, theme.height) == model.size(orientation), (model.id, orientation)
            assert theme.warnings == [], (model.id, orientation)


@pytest.mark.parametrize("name", ["lumen", "studio", "libre-default", "spur-studio"])
def test_the_modules_of_an_adapted_theme_stay_in_the_frame_on_every_panel(name):
    """Their grid is pixels too: it came along with its margin and gap and 48 x 48 cells, and
    on the 0.96" the modules ran far past the frame."""
    data = load_theme(find_theme(name)).to_dict()
    for model in MODELS:
        for orientation in ("landscape", "portrait"):
            theme = parse_theme(adapt_theme(data, model.id, orientation), find_theme(name))
            renderer = Renderer(theme, preview=True)
            for module, (x, y, w, h) in renderer.module_boxes.items():
                where = (model.id, orientation, module)
                assert x >= 0 and y >= 0, where
                assert x + w <= theme.width and y + h <= theme.height, where
            renderer.close()


# One panel of each kind: tiny, round, the common 3.5" both ways round, a bar whose bezel hides a
# strip. The rest of the catalog shares their sizes (the adapt test above takes every model).
SAMPLE_PANELS = (
    ("weact-0.96", "landscape"),
    ("weact-0.96", "portrait"),
    ("turing-2.1", "landscape"),
    ("turing-3.5", "landscape"),
    ("turing-3.5", "portrait"),
    ("turing-9.2-usb", "landscape"),
)
SAFE_REACH = 5  # px a part may reach past the frame of a panel that is not tiny


def reach(box, size):
    """How many px the box (x, y, w, h) reaches past a frame of ``size``: 0 inside it."""
    x, y, w, h = box
    return max(0, -x, -y, x + w - size[0], y + h - size[1])


def on_frame(box, size):
    """Whether any of the box (x, y, w, h) lies on a frame of ``size``."""
    x, y, w, h = box
    return x < size[0] and x + w > 0 and y < size[1] and y + h > 0


@pytest.mark.parametrize("name", sorted(p["id"] for p in list_themes()))
def test_every_built_in_theme_renders_on_each_kind_of_panel_with_readings_and_without(name):
    """Nothing raises, warns or comes out another size, and nothing is drawn off the frame. A
    bar's design shrunk onto a 320 px wide panel cannot put a three digit label in 9 px (text
    does not go below 6 px), so a part may reach a few px past the frame of a panel that is not
    tiny, but never wholly off it, on the 0.96" either; the modules, which follow a grid of
    their own, are always on it, and out of the strip the bezel hides."""
    root = find_theme(name)
    data = load_theme(root).to_dict()
    for model_id, orientation in SAMPLE_PANELS:
        model = get_model(model_id)
        theme = parse_theme(adapt_theme(data, model_id, orientation), root)
        size = model.size(orientation)
        tiny = min(size) < 100
        for readings in (demo_snapshot(fixed_time=1_700_000_000), Snapshot()):
            where = (model_id, orientation, bool(readings.readings))
            renderer = Renderer(theme, preview=True)
            frame, boxes = renderer.render(readings, 0.0)
            assert renderer.warnings == [], where
            assert (frame.size, frame.mode) == (size, "RGB"), where
            for part, box in boxes.items():
                assert on_frame(box, size), (where, part, box)
                assert tiny or reach(box, size) <= SAFE_REACH, (where, part, box)
            strip = model.hidden_edges(orientation)
            for module, (x, y, w, h) in renderer.module_boxes.items():
                assert strip["left"] <= x and strip["top"] <= y, (where, module)
                assert x + w <= size[0] - strip["right"], (where, module)
                assert y + h <= size[1] - strip["bottom"], (where, module)
            renderer.close()


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
        # A widget the renderer gives no box was not drawn (it shows only with a reading the demo
        # has not, a round report's, say): taking out all of them at once must leave the frame as
        # it is, which holds for each of them, and costs one render, not one for each.
        undrawn = {widget["id"] for widget in data["widgets"] if widget["id"] not in boxes}
        if undrawn:
            rest = copy.deepcopy(data)
            rest["widgets"] = [x for x in rest["widgets"] if x["id"] not in undrawn]
            frame = Renderer(parse_theme(rest, root)).render(snapshot, 1000.0)[0]
            assert ImageChops.difference(full, frame).getbbox() is None, (
                f"{entry['id']}: {sorted(undrawn)} have no box and still change the frame"
            )
        for widget in data["widgets"]:
            if widget["id"] in undrawn:
                checked += 1
                continue
            x, y, bw, bh = boxes[widget["id"]]
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
