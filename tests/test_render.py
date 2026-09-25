import pytest
from PIL import Image

from libre_panel.render.renderer import Renderer, _rule_color, changed_region
from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.model import THEME_FORMAT, find_theme, load_theme, parse_theme


@pytest.mark.parametrize("name", ["libre-default", "spur-ii"])
def test_builtin_themes_render(name):
    theme = load_theme(find_theme(name))
    renderer = Renderer(theme)
    frame, boxes = renderer.render(demo_snapshot(fixed_time=1_700_000_000))
    assert frame.size == (theme.width, theme.height)
    assert frame.mode == "RGB"
    assert renderer.warnings == []
    visible = {w["id"] for w in theme.widgets if w["visible"]}
    assert set(boxes) == visible


def test_rendering_is_deterministic_with_fixed_time():
    theme = load_theme(find_theme("libre-default"))
    a, _ = Renderer(theme).render(demo_snapshot(fixed_time=1_700_000_000))
    b, _ = Renderer(theme).render(demo_snapshot(fixed_time=1_700_000_000))
    assert changed_region(a, b) is None


def test_missing_sensors_show_fallback_instead_of_crashing():
    theme = parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": 200, "height": 100},
            "widgets": [
                {"type": "metric", "id": "m", "sensor": "gpu.temp", "fallback": "n/a"},
                {"type": "bar", "id": "b", "sensor": "gpu.temp"},
                {"type": "gauge", "id": "g", "sensor": "gpu.temp"},
                {"type": "graph", "id": "h", "sensor": "gpu.temp"},
                {"type": "weather", "id": "w"},
                {"type": "image", "id": "i", "src": "missing.png"},
            ],
        }
    )
    renderer = Renderer(theme)
    frame, boxes = renderer.render(Snapshot())
    assert set(boxes) == {"m", "b", "g", "h", "w", "i"}
    assert frame.size == (200, 100)


def test_string_values_fall_back_to_raw_text():
    theme = parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": 300, "height": 60},
            "widgets": [{"type": "weather", "id": "w", "field": "description"}],
        }
    )
    snap = Snapshot(readings={"weather.description": Reading("weather.description", "Overcast")})
    _, boxes = Renderer(theme).render(snap)
    assert boxes["w"][2] > 40  # rendered the word, not the 2-char fallback


def test_color_rules():
    widget = {
        "color": "#000000",
        "color_rules": [{"above": 90, "color": "#ff0000"}, {"above": 70, "color": "#ffff00"}],
    }
    assert _rule_color(widget, 50) == "#000000"
    assert _rule_color(widget, 75) == "#ffff00"
    assert _rule_color(widget, 95) == "#ff0000"
    assert _rule_color(widget, None) == "#000000"


def test_bar_fill_color_follows_value():
    theme = parse_theme(
        {
            "format": THEME_FORMAT,
            "display": {"width": 100, "height": 20},
            "background": {"color": "#000000"},
            "widgets": [
                {
                    "type": "bar",
                    "id": "b",
                    "w": 100,
                    "h": 20,
                    "sensor": "x",
                    "radius": 0,
                    "background": None,
                    "color": "#00ff00",
                    "color_rules": [{"above": 80, "color": "#ff0000"}],
                }
            ],
        }
    )
    frame, _ = Renderer(theme).render(Snapshot(readings={"x": Reading("x", 90.0)}))
    assert frame.getpixel((10, 10)) == (255, 0, 0)
    assert frame.getpixel((97, 10)) == (0, 0, 0)  # 90 % filled


def test_changed_region():
    a = Image.new("RGB", (10, 10))
    b = a.copy()
    assert changed_region(a, b) is None
    b.putpixel((3, 4), (255, 255, 255))
    assert changed_region(a, b) == (3, 4, 4, 5)
    assert changed_region(None, b) == (0, 0, 10, 10)
