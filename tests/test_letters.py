"""A text laid out letter by letter (letter spacing, equal-width digits, a clock's rolling
digits): each letter goes on as it is where its ink meets none laid before, the same pixels as
each one laid over the layer."""

import pytest
from PIL import Image

from libre_panel.render.renderer import Renderer
from libre_panel.theme.model import parse_theme


def widget(**kw):
    return {"type": "text", "id": "t", "x": 30, "y": 10, "text": "", "font_size": 44,
            "color": "#2EE6D6", "tabular": True, **kw}  # fmt: skip


def renderer(**kw):
    theme = parse_theme({"format": "libre-panel-theme/1",
                         "display": {"width": 400, "height": 80},
                         "background": {"color": "#000000"},
                         "widgets": [widget(**kw)]})  # fmt: skip
    return Renderer(theme, animate=True)


def laid_over(letters) -> Image.Image:
    """The letters each laid over a clear layer, as they were."""
    layer = Image.new("RGBA", letters.size, (0, 0, 0, 0))
    for placed in letters.glyphs:
        if placed is not None:
            Renderer._paste(layer, *placed)
    return layer


@pytest.mark.parametrize("spacing", [0, 4, -9])  # (below nothing: the letters overlap)
@pytest.mark.parametrize("finish", ["flat", "facet"])  # (a finish's own letters: laid over)
def test_a_text_s_letters_are_the_same_pixels_as_each_laid_over_the_layer(spacing, finish):
    drawn = renderer(letter_spacing=spacing, finish=finish)
    shown = widget(letter_spacing=spacing, finish=finish)
    laid = 0
    for text in ("12:45", "07", "AVAWA", "x/y 9%", "  1 1 "):
        letters = drawn._letters(shown, text, "#2EE6D6")
        if letters is None:  # (drawn whole: no spacing, no digits, no letters of a finish)
            continue
        assert drawn._laid(letters).tobytes() == laid_over(letters).tobytes(), (text, spacing)
        laid += 1
    assert laid >= 4


def test_letters_whose_ink_meets_are_laid_over_each_other():
    drawn = renderer(letter_spacing=-14)
    letters = drawn._letters(widget(letter_spacing=-14), "888", "#2EE6D6")
    inks = [ink for ink in letters.inks if ink]
    assert len(inks) == 3  # (the letters' ink, known)
    (_a, x0, _y0), (_b, x1, _y1) = letters.glyphs[:2]
    # the first two letters' ink, across: they overlap at this spacing
    assert x1 + letters.inks[1][0] < x0 + letters.inks[0][2]
    assert drawn._laid(letters).tobytes() == laid_over(letters).tobytes()
