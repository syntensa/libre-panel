"""The looks of the volume message: each draws the volume on every screen, where it belongs on
the panel and clear of what its frame hides; the toast layer draws only the volume in it."""

import pytest
from PIL import ImageChops

from libre_panel.devices.models import find_model
from libre_panel.plugins.host import Toast
from libre_panel.render.overlays import ToastLayer
from libre_panel.render.renderer import Renderer
from libre_panel.render.volume_looks import VOLUME_LOOKS
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import parse_theme


def renderer(orientation="landscape"):
    theme = parse_theme(
        {
            "format": "libre-panel-theme/1",
            "display": {"model": "turing-9.2-usb", "orientation": orientation},
            "background": {"color": "#000000"},
            "widgets": [],
        }
    )
    return Renderer(theme, preview=True)


def volume(look, share=0.42, muted=False, kind="volume"):
    payload = {"label": "Volume", "progress": 0.0 if muted else share, "muted": muted}
    if look:
        payload["look"] = look
    return Toast("Muted" if muted else f"{share * 100:.0f}", None, "info", None, kind, 0, payload)


def drawn(made, toast, age=1.0):
    frame, _ = made.render(demo_snapshot(fixed_time=1_700_000_000))
    layer = ToastLayer(made)
    try:
        layer.add([toast])
        layer.advance(0.0)
        return frame, layer.draw(frame, age), layer
    finally:
        layer.close()


@pytest.mark.parametrize("look", sorted(VOLUME_LOOKS))
@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
def test_each_look_draws_the_volume_clear_of_the_hidden_strip(look, orientation):
    made = renderer(orientation)
    frame, shown, _ = drawn(made, volume(look))
    box = ImageChops.difference(frame.convert("RGB"), shown.convert("RGB")).getbbox()
    assert box is not None, look  # it drew something
    hidden = find_model("turing-9.2-usb").hidden_edges(orientation)
    w, h = frame.size
    assert box[1] >= hidden["top"] and box[3] <= h - hidden["bottom"], (look, box, hidden)
    assert box[0] >= hidden["left"] and box[2] <= w - hidden["right"], (look, box, hidden)


@pytest.mark.parametrize("look", sorted(VOLUME_LOOKS))
def test_a_look_shows_the_level_and_muted(look):
    made = renderer()
    _, low, _ = drawn(made, volume(look, 0.1))
    _, high, _ = drawn(made, volume(look, 0.9))
    _, muted, _ = drawn(made, volume(look, muted=True))
    assert ImageChops.difference(low.convert("RGB"), high.convert("RGB")).getbbox() is not None
    assert ImageChops.difference(low.convert("RGB"), muted.convert("RGB")).getbbox() is not None


def test_only_the_volume_goes_in_its_look_every_other_message_in_the_theme_s_style():
    made = renderer()
    layer = ToastLayer(made)
    try:
        assert type(layer.style_of(volume("bar"))).__name__ == "BarLook"
        assert layer.style_of(volume("")) is layer.style  # no look: the theme's
        assert layer.style_of(volume("sparkles")) is layer.style  # not one: the theme's
        assert layer.style_of(volume("bar", kind="music")) is layer.style  # only the volume
    finally:
        layer.close()


def test_a_look_comes_and_goes_and_is_gone_after_its_lifetime():
    made = renderer()
    frame, _, _ = drawn(made, volume("big"))
    layer = ToastLayer(made)
    try:
        layer.add([volume("big")])
        layer.advance(0.0)
        early = layer.draw(frame, 0.02)
        full = layer.draw(frame, 1.0)
        diff = ImageChops.difference
        assert diff(early.convert("RGB"), full.convert("RGB")).getbbox() is not None  # fading in
        layer.advance(30.0)
        assert layer.current is None  # its time was up
    finally:
        layer.close()
