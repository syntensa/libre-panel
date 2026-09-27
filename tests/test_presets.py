"""Every editor building block must produce valid widgets."""

import pytest

from libre_panel.editor.presets import PRESETS, TOKEN_DEFAULTS, resolve_tokens
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import THEME_FORMAT, parse_theme


@pytest.mark.parametrize("preset", PRESETS, ids=[p["id"] for p in PRESETS])
@pytest.mark.parametrize("palette", [{}, {"accent": "#ff0000", "text": "#ffffff"}])
def test_preset_is_valid_and_renders(preset, palette):
    widgets = resolve_tokens(preset["widgets"], palette)
    theme = parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": preset["size"][0] + 40, "height": preset["size"][1] + 40},
            "palette": palette,
            "widgets": widgets,
        }
    )
    renderer = Renderer(theme)
    _, boxes = renderer.render(demo_snapshot(fixed_time=1_700_000_000))
    assert renderer.warnings == []
    assert len({w["id"] for w in widgets}) == len(widgets)
    for box in boxes.values():  # the block stays inside its declared size (plus glyph slack)
        assert box[0] >= -4 and box[0] + box[2] <= preset["size"][0] + 8


def test_tokens_use_the_palette_when_possible():
    assert resolve_tokens("$accent", {"accent": "#123456"}) == "@accent"
    assert resolve_tokens("$accent", {}) == TOKEN_DEFAULTS["accent"]
    assert resolve_tokens({"a": ["$text", 1]}, {}) == {"a": [TOKEN_DEFAULTS["text"], 1]}
