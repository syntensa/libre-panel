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
    assert get_model("turing-3.5").driver == "planned"


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
