"""Text that does not fit its room is shortened with an ellipsis, the width counted in the font
that draws each character: the card toast cut it off at the edge of the card."""

import pytest
from PIL import Image

from libre_panel.fonts import builtin_font_path
from libre_panel.fonts.fallback import FallbackFont, Fallbacks, set_fallbacks
from libre_panel.plugins import Toast
from libre_panel.render.light import ellipsize
from libre_panel.render.overlays import ToastLayer
from libre_panel.render.renderer import Renderer
from libre_panel.theme.model import parse_theme


def bundled(name: str) -> str:
    return str(builtin_font_path("builtin:" + name))


@pytest.fixture
def liberation():
    """Barlow lacks Cyrillic: a text in it takes its widths from Liberation Sans."""
    before = set_fallbacks(Fallbacks([(bundled("LiberationSans-Regular"), None, 0)]))
    yield
    set_fallbacks(before)


def font(size: int = 30) -> FallbackFont:
    return FallbackFont(bundled("Barlow-Medium"), size)


def test_a_text_that_fits_is_left_alone():
    assert ellipsize("Midnight Drive", font(), 1000) == "Midnight Drive"
    assert ellipsize("", font(), 0) == ""


def test_a_text_that_does_not_fit_is_cut_and_ends_with_an_ellipsis():
    face, text = font(), "An Extraordinarily Long Title That Cannot Fit"
    for room in (60, 150, 299.5):
        cut = ellipsize(text, face, room)
        assert cut.endswith("…") and text.startswith(cut[:-1].rstrip()) and cut != text
        assert face.getlength(cut) <= room
        prefix = cut[:-1]
        for end in range(len(prefix) + 1, len(text)):  # no longer prefix would have fit
            longer = text[:end].rstrip()
            if len(longer) > len(prefix):
                assert face.getlength(longer + "…") > room


def test_a_space_before_the_ellipsis_goes_and_a_letter_always_stays():
    face = font()
    assert ellipsize("Hello World", face, face.getlength("Hello …") - 1) == "Hello…"
    assert ellipsize("Hello World", face, 1) == "H…"  # (no room at all)


def test_the_width_is_that_of_the_font_that_draws_each_character(liberation):
    face = font()
    text = "Привет, мир! Это очень длинный текст"
    cut = ellipsize(text, face, 200)
    assert cut.endswith("…") and face.getlength(cut) <= 200
    # counted by Barlow's boxes it would have been another length: Liberation draws the letters
    boxes = FallbackFont(bundled("Barlow-Medium"), 30, chain=Fallbacks())
    assert ellipsize(text, boxes, 200) != cut


def card(text: str, width: int = 480, height: int = 320, **toast) -> Image.Image:
    """The frame with the card toast for ``text``."""
    data = {
        "format": "libre-panel-theme/1",
        "name": "t",
        "display": {"model": "custom", "width": width, "height": height},
        "background": {"color": "#000000"},
        "palette": {"accent": "#00ffff", "bg": "#000000"},
        "widgets": [],
        "toast": {"style": "card", "seconds": 3, "queue": False, **toast},
    }
    renderer = Renderer(parse_theme(data), preview=True)
    layer = ToastLayer(renderer)
    try:
        layer.add([Toast(text, seconds=3)])
        layer.advance(0.0)
        return layer.apply(Image.new("RGB", (width, height)), 1.0)
    finally:
        layer.close()
        renderer.close()


def shortened_to(text: str, frame_of) -> str | None:
    """The prefix of ``text`` with "…" that the card shows for the whole text: the shortest one
    that draws the same (a longer one is cut to it too), found by looking at what the card
    draws for each; None where none is the same."""
    shown = frame_of(text).tobytes()
    for end in range(1, len(text) + 1):
        candidate = text[:end].rstrip() + "…"
        if frame_of(candidate).tobytes() == shown:
            return candidate
    return None


def card_alone(frame: Image.Image) -> Image.Image:
    """The card, cut out of the frame."""
    return frame.crop(frame.getbbox())


@pytest.mark.parametrize("size", [(480, 320), (1920, 480), (320, 240)])
def test_the_card_toast_shortens_a_text_that_does_not_fit_with_an_ellipsis(size):
    text = "Midnight Drive — The Night Owls: an extraordinarily long notice that cannot fit. " * 3
    cut = shortened_to(text, lambda t: card(t, *size))
    assert cut is not None and cut.endswith("…") and 4 < len(cut) < 120
    box = card(text, *size).getbbox()
    assert box[2] - box[0] <= size[0] * 2 // 3 + 1  # (the card keeps to two thirds of the panel)
    # the text is shortened, not cut off: all of it, the ellipsis too, is on a card that has room
    wide = (size[0] * 2, size[1])  # (as high: the type is as big)
    assert card_alone(card(cut, *size)).tobytes() == card_alone(card(cut, *wide)).tobytes()


def test_the_card_toast_leaves_a_text_that_fits(liberation):
    short = card("Volume 30 %")
    assert short.getbbox()[2] - short.getbbox()[0] < 480 // 3
    assert (
        shortened_to("Volume 30 %", card) is None
        or card("Volume 30 %…").tobytes() == short.tobytes()
    )
    # a text of 120 letters or more was cut at 119 with an ellipsis before; it is cut to the room
    assert card("x" * 130).getbbox() == card("x" * 400).getbbox()


def test_the_card_toast_counts_the_letters_of_the_fallback_font(liberation):
    text = "Привет, мир! Это очень длинный текст для карточки с уведомлением " * 2
    cut = shortened_to(text, card)
    assert cut is not None and cut.endswith("…") and 4 < len(cut) < len(text)
    assert card_alone(card(cut)).tobytes() == card_alone(card(cut, 960, 320)).tobytes()
