"""Render the images in docs/images from the built-in themes.

    python tools/readme_images.py [docs/images]

Each theme as the panel shows it (<theme>.png), the README's hero, gallery,
"fits your machine" and module pictures, and the 1280x640 social preview
(upload it in the repository's settings). With Playwright installed, also
the editor screenshots. Demo sensor values and a fixed time keep the pictures stable.
"""

from __future__ import annotations

import argparse
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from libre_panel.fonts import builtin_font_path
from libre_panel.render.renderer import Renderer
from libre_panel.sensors.base import Reading
from libre_panel.sensors.demo import demo_snapshot
from libre_panel.theme.adapt import adapt_theme
from libre_panel.theme.model import find_theme, load_theme, parse_theme
from libre_panel.theme.modules import LOOKS, look_theme_parts, make_grid, templates

WHEN = datetime(2026, 10, 9, 10, 8, 24).timestamp()  # a Friday morning
BG_TOP, BG_BOTTOM = (13, 20, 29), (6, 9, 13)
TX_1, TX_3, ACCENT = (238, 246, 255), (140, 160, 184), (19, 229, 215)


def snapshot():
    snap = demo_snapshot(fixed_time=WHEN)
    weather = {
        "weather.temperature": ("Temperature", "°C", 17.0),
        "weather.apparent_temperature": ("Feels like", "°C", 16.0),
        "weather.humidity": ("Humidity", "%", 58.0),
        "weather.wind_speed": ("Wind", "km/h", 12.0),
        "weather.code": ("Weather code", "", 2.0),
        "weather.description": ("Weather", "", "Partly cloudy"),
    }
    for key, (label, unit, value) in weather.items():
        snap.readings[key] = Reading(key, value, unit, label)
    return snap


def render(theme_id: str, factor: int = 1, drop: tuple[str, ...] = ()) -> Image.Image:
    theme = load_theme(find_theme(theme_id))
    if factor != 1:
        data = adapt_theme(
            theme.to_dict(),
            "custom",
            theme.to_dict()["display"].get("orientation", "landscape"),
            "scale",
            theme.width * factor,
            theme.height * factor,
        )
        theme = parse_theme(data, root=theme.root)
    snap = snapshot()
    for key in drop:
        snap.readings.pop(key, None)
    renderer = Renderer(theme, preview=True)
    frame, _ = renderer.render(snap)
    renderer.close()
    return frame.convert("RGB")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(builtin_font_path(f"builtin:{name}")), size)


def background(w: int, h: int, glow: tuple[float, float] | None = (0.5, 0.0)) -> Image.Image:
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        f = y / max(1, h - 1)
        draw.line(
            [(0, y), (w, y)],
            fill=tuple(round(a + (b - a) * f) for a, b in zip(BG_TOP, BG_BOTTOM, strict=True)),
        )
    if glow:
        halo = Image.new("L", (w, h), 0)
        gx, gy = glow
        ImageDraw.Draw(halo).ellipse(
            [gx * w - w * 0.35, gy * h - h * 0.5, gx * w + w * 0.35, gy * h + h * 0.5], fill=46
        )
        halo = halo.filter(ImageFilter.GaussianBlur(min(w, h) * 0.2))
        img = Image.composite(Image.new("RGB", (w, h), ACCENT), img, halo)
    return img


def device(screen: Image.Image, border: int, round_panel: bool = False) -> Image.Image:
    """The screen in a dark bezel with a soft shadow (RGBA, shadow margin included)."""
    sw, sh = screen.size
    pad = border * 4  # room for the shadow
    w, h = sw + 2 * border + 2 * pad, sh + 2 * border + 2 * pad
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    body = [pad, pad, w - pad - 1, h - pad - 1]
    radius = border * 2

    shadow = Image.new("L", (w, h), 0)
    sd = ImageDraw.Draw(shadow)
    shifted = [body[0], body[1] + border, body[2], body[3] + border]
    if round_panel:
        sd.ellipse(shifted, fill=200)
    else:
        sd.rounded_rectangle(shifted, radius, fill=200)
    shadow = shadow.filter(ImageFilter.GaussianBlur(border * 1.6))
    out.paste(Image.new("RGBA", (w, h), (0, 0, 0, 255)), (0, 0), shadow)

    d = ImageDraw.Draw(out)
    edge, fill = (52, 64, 80, 255), (11, 14, 19, 255)
    if round_panel:
        d.ellipse(body, fill=edge)
        d.ellipse([body[0] + 2, body[1] + 2, body[2] - 2, body[3] - 2], fill=fill)
    else:
        d.rounded_rectangle(body, radius, fill=edge)
        d.rounded_rectangle(
            [body[0] + 2, body[1] + 2, body[2] - 2, body[3] - 2], radius - 2, fill=fill
        )

    mask = Image.new("L", screen.size, 0)
    md = ImageDraw.Draw(mask)
    if round_panel:
        md.ellipse([0, 0, sw - 1, sh - 1], fill=255)
    else:
        md.rounded_rectangle([0, 0, sw - 1, sh - 1], max(2, border // 3), fill=255)
    out.paste(screen, (pad + border, pad + border), mask)

    # a faint reflection across the glass
    sheen = Image.new("L", screen.size, 0)
    sd = ImageDraw.Draw(sheen)
    for x in range(sw):
        sd.line([(x, 0), (x, sh)], fill=max(0, round(7 * (1 - x / (0.55 * sw)))))
    sheen = Image.composite(sheen, Image.new("L", screen.size, 0), mask)
    out.paste(
        Image.new("RGBA", screen.size, (255, 255, 255, 255)), (pad + border, pad + border), sheen
    )
    return out


def shadow_margin(border: int) -> int:
    return border * 4


def text_center(draw, cx, y, text, fnt, fill):
    w = draw.textlength(text, font=fnt)
    draw.text((cx - w / 2, y), text, font=fnt, fill=fill)


def hero(studio2x: Image.Image) -> Image.Image:
    """Studio on its 9.2" bar."""
    screen = studio2x.resize((1920, 480), Image.LANCZOS)
    border = 18
    dev = device(screen, border)
    m = shadow_margin(border)
    w = 2000
    h = dev.height - 2 * m + 2 * 56
    img = background(w, h, glow=(0.5, 1.1)).convert("RGBA")
    img.alpha_composite(dev, ((w - dev.width) // 2, 56 - m))
    return img.convert("RGB")


def gallery(shots: dict[str, Image.Image]) -> Image.Image:
    w, margin, gap, border = 2000, 44, 44, 12
    label, sub = font("Barlow-SemiBold", 30), font("Barlow-Regular", 24)
    m = shadow_margin(border)

    # row 1: SPUR II across the width
    bar_w = w - 2 * margin - 2 * border
    bar = shots["spur-ii"].resize((bar_w, bar_w // 4), Image.LANCZOS)
    # row 2: the smaller panels at one screen height (Pico larger than life)
    hh = 270
    row = [
        ("slate", "Slate", '5" · 800×480'),
        ("libre-default", "Libre Default", '3.5" · 480×320'),
        ("orbit", "Orbit", '2.1" / 2.8" round'),
        ("column", "Column", '3.5" portrait'),
        ("pico", "Pico", '0.96" · 160×80'),
    ]
    screens = []
    for theme_id, name, size in row:
        src = shots[theme_id]
        if theme_id == "pico":
            scr = src.resize((320, 160), Image.LANCZOS)
        else:
            scr = src.resize((round(src.width * hh / src.height), hh), Image.LANCZOS)
        screens.append((scr, name, size, theme_id == "orbit"))
    total = sum(s.width + 2 * border for s, *_ in screens)
    spacing = (w - 2 * margin - total) / (len(screens) - 1)

    caption_h = 92
    row1_h = bar.height + 2 * border
    row2_h = hh + 2 * border
    h = margin + row1_h + caption_h + gap // 2 + row2_h + caption_h + margin // 2
    img = background(w, h, glow=(0.5, 0.0)).convert("RGBA")
    draw = ImageDraw.Draw(img)

    y = margin
    dev = device(bar, border)
    img.alpha_composite(dev, (margin - m, y - m))
    cy = y + row1_h + 22
    text_center(draw, w / 2, cy, "SPUR II", label, TX_1)
    text_center(draw, w / 2, cy + 38, '8.8" / 9.2" bar · 1920×480', sub, TX_3)

    y = margin + row1_h + caption_h + gap // 2
    x = margin
    for scr, name, size, is_round in screens:
        dev = device(scr, border, round_panel=is_round)
        top = y + (row2_h - (scr.height + 2 * border)) // 2
        img.alpha_composite(dev, (round(x) - m, top - m))
        cx = x + (scr.width + 2 * border) / 2
        text_center(draw, cx, y + row2_h + 22, name, label, TX_1)
        text_center(draw, cx, y + row2_h + 60, size, sub, TX_3)
        x += scr.width + 2 * border + spacing
    return img.convert("RGB")


def social(studio2x: Image.Image) -> Image.Image:
    """1280x640 for the repository's social preview."""
    w, h = 1280, 640
    img = background(w, h, glow=(0.5, 1.0)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    title, tag = font("Barlow-Bold", 96), font("Barlow-Medium", 38)
    small = font("Barlow-Regular", 28)
    draw.text((72, 44), "Libre Panel", font=title, fill=TX_1)
    draw.rounded_rectangle([76, 158, 76 + 120, 158 + 7], 3, fill=ACCENT)
    draw.text((72, 188), "Free software for USB system-monitor displays", font=tag, fill=TX_1)
    draw.text(
        (72, 240),
        "Visual theme editor · every panel size · Windows, Linux, macOS",
        font=small,
        fill=TX_3,
    )
    border = 10
    screen = studio2x.resize((1120, 280), Image.LANCZOS)
    dev = device(screen, border)
    m = shadow_margin(border)
    img.alpha_composite(dev, ((w - dev.width) // 2, 304 - m))
    return img.convert("RGB")


def adapts(variants: list[tuple[Image.Image, str, str]]) -> Image.Image:
    """Studio on machines without weather or without a GPU sensor."""
    w, margin, border = 1800, 48, 14
    label, sub = font("Barlow-SemiBold", 34), font("Barlow-Regular", 28)
    m = shadow_margin(border)
    bar_w = w - 2 * margin - 2 * border
    bar_h = bar_w // 4
    block = 64 + bar_h + 2 * border + 40
    img = background(w, margin + block * len(variants), glow=(0.5, 0.0)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    y = margin
    for screen, title, note in variants:
        draw.text((margin + 4, y), title, font=label, fill=TX_1)
        draw.text(
            (margin + 12 + draw.textlength(title, font=label), y + 5), note, font=sub, fill=TX_3
        )
        dev = device(screen.resize((bar_w, bar_h), Image.LANCZOS), border)
        img.alpha_composite(dev, (margin - m, y + 56 - m))
        y += block
    return img.convert("RGB")


def module_frame(model: str, look: str, modules: list[dict], orientation: str = "landscape"):
    """A theme made of modules, rendered; and its module boxes."""
    palette, style = look_theme_parts(look)
    theme = parse_theme(
        {
            "format": "libre-panel-theme/1",
            "display": {"model": model, "orientation": orientation},
            "palette": palette,
            "style": style,
            "background": {"color": palette["bg"]},
            "widgets": [{"type": "module", "id": f"m{i}", **m} for i, m in enumerate(modules)],
        }
    )
    renderer = Renderer(theme, preview=True)
    frame, _ = renderer.render(snapshot())
    return frame.convert("RGB"), renderer.module_boxes


def template(model: str, which: str, orientation: str = "landscape") -> list[dict]:
    from libre_panel.devices.models import get_model

    width, height = get_model(model).size(orientation)
    grid = make_grid(width, height, None, model)
    chosen = next(t for t in templates(grid.columns, grid.rows) if t["id"] == which)
    return chosen["modules"]


def module_sizes() -> Image.Image:
    """One CPU ring module at five sizes: the larger, the more it shows."""
    spans = [
        ((1, 1), "1 × 1", "the load"),
        ((2, 1), "2 × 1", "+ name, temperature, power"),
        ((3, 1), "3 × 1", "+ history"),
        ((2, 2), "2 × 2", "big: details over history"),
        ((4, 2), "4 × 2", "everything, large"),
    ]
    crops = []
    for (cols, rows), size, note in spans:
        frame, boxes = module_frame(
            "turing-9.2-usb",
            "arctic",
            [{"module": "ring", "source": "cpu", "cols": cols, "rows": rows}],
        )
        x, y, w, h = boxes["m0"]
        crops.append((frame.crop((x - 12, y - 12, x + w + 12, y + h + 12)), size, note))
    label, sub = font("Barlow-SemiBold", 30), font("Barlow-Regular", 24)
    margin, gap, head = 44, 36, 70
    rows = [crops[:3], crops[3:]]
    width = max(sum(c.width for c, *_ in row) + gap * (len(row) - 1) for row in rows) + 2 * margin
    height = margin + sum(max(c.height for c, *_ in row) + head for row in rows) + gap + margin // 2
    img = background(width, height, glow=(0.5, 0.0))
    draw = ImageDraw.Draw(img)
    y = margin
    for row in rows:
        x = margin
        for crop, size, note in row:
            draw.text((x + 12, y), size, font=label, fill=TX_1)
            draw.text(
                (x + 12 + draw.textlength(size + "  ", font=label), y + 5),
                note,
                font=sub,
                fill=TX_3,
            )
            img.paste(crop, (x, y + head - 12))
            x += crop.width + gap
        y += max(c.height for c, *_ in row) + head + gap
    return img


def looks() -> Image.Image:
    """The same layout in every look."""
    modules = template("turing-9.2-usb", "overview")
    shots = [
        (look["name"], module_frame("turing-9.2-usb", key, modules)[0])
        for key, look in LOOKS.items()
    ]
    border, margin, gap, caption = 10, 44, 34, 52
    label = font("Barlow-SemiBold", 30)
    cell_w = 940
    cell_h = cell_w // 4
    m = shadow_margin(border)
    width = 2 * margin + 2 * (cell_w + 2 * border) + gap
    height = margin + 3 * (cell_h + 2 * border + caption) + 2 * gap // 2 + margin // 2
    img = background(width, height, glow=(0.5, 0.0)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    for i, (name, frame) in enumerate(shots):
        col, row = i % 2, i // 2
        x = margin + col * (cell_w + 2 * border + gap)
        y = margin + row * (cell_h + 2 * border + caption + gap // 2)
        dev = device(frame.resize((cell_w, cell_h), Image.LANCZOS), border)
        img.alpha_composite(dev, (x - m, y - m))
        draw.text((x + 4, y + cell_h + 2 * border + 10), name, font=label, fill=TX_1)
    return img.convert("RGB")


def module_layouts() -> Image.Image:
    """Starting layouts on several panels, each in another look."""
    bars = [
        (
            "Performance · Neon",
            module_frame("turing-9.2-usb", "neon", template("turing-9.2-usb", "performance"))[0],
        ),
        (
            "Calm · Sunset",
            module_frame("turing-9.2-usb", "sunset", template("turing-9.2-usb", "calm"))[0],
        ),
    ]
    specs = [
        ('3.5" · Paper', "turing-3.5", "paper", "overview", "landscape"),
        ('5" · Graphite', "turing-5", "graphite", "performance", "landscape"),
        ('3.5" portrait · Mono', "turing-3.5", "mono", "overview", "portrait"),
        ('2.1" round · Arctic', "turing-2.1", "arctic", "performance", "landscape"),
    ]
    small = [
        (
            name,
            module_frame(model, look, template(model, which, side), side)[0],
            model == "turing-2.1",
        )
        for name, model, look, which, side in specs
    ]
    border, margin, gap, caption = 12, 44, 40, 56
    label = font("Barlow-SemiBold", 28)
    m = shadow_margin(border)
    bar_w = 940
    hh = 300
    sized = []
    for name, frame, is_round in small:
        sized.append(
            (
                name,
                frame.resize((round(frame.width * hh / frame.height), hh), Image.LANCZOS),
                is_round,
            )
        )
    width = 2 * margin + 2 * (bar_w + 2 * border) + gap
    row1 = bar_w // 4 + 2 * border
    height = margin + row1 + caption + gap // 2 + hh + 2 * border + caption + margin // 2
    img = background(width, height, glow=(0.5, 0.0)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    for i, (name, frame) in enumerate(bars):
        x = margin + i * (bar_w + 2 * border + gap)
        dev = device(frame.resize((bar_w, bar_w // 4), Image.LANCZOS), border)
        img.alpha_composite(dev, (x - m, margin - m))
        draw.text((x + 4, margin + row1 + 10), name, font=label, fill=TX_1)
    total = sum(f.width + 2 * border for _, f, _ in sized)
    spacing = (width - 2 * margin - total) / (len(sized) - 1)
    x = margin
    y = margin + row1 + caption + gap // 2
    for name, frame, is_round in sized:
        dev = device(frame, border, round_panel=is_round)
        img.alpha_composite(dev, (round(x) - m, y - m))
        draw.text((x + 4, y + hh + 2 * border + 10), name, font=label, fill=TX_1)
        x += frame.width + 2 * border + spacing
    return img.convert("RGB")


def editor_shots(out: Path) -> None:
    """The editor: a new theme from a layout, dragging a module, resizing one."""
    from playwright.sync_api import sync_playwright

    os.environ["LIBRE_PANEL_HOME"] = tempfile.mkdtemp()  # no user themes or config
    from libre_panel.editor.server import make_server

    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    rendered = "document.querySelector('#preview').src.startsWith('data:image/png')"
    pictures = (
        "[...document.querySelectorAll('#new-templates img')].every(i => i.src.startsWith('data:'))"
    )
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1600, "height": 900},
                device_scale_factor=1.5,
                locale="en-US",
                bypass_csp=True,  # for the waits below
            )
            page = context.new_page()
            page.on("dialog", lambda d: d.accept())
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
            page.wait_for_function(rendered)
            page.select_option("#model-select", "turing-9.2-usb")
            page.wait_for_function("state.size[0] === 1920")
            page.click("#btn-new")
            page.wait_for_selector("#new-dialog[open]")
            page.wait_for_function(pictures)
            page.wait_for_timeout(500)
            page.locator("#new-dialog").screenshot(path=str(out / "editor-new.png"))
            page.click("#new-create")
            page.wait_for_function("state.theme.widgets.filter(isModule).length === 5")
            page.wait_for_timeout(1200)
            page.evaluate("setSelection(['ring-4'])")
            page.wait_for_timeout(1000)
            page.screenshot(path=str(out / "editor.png"))
            # drag a module from the library onto free cells
            page.evaluate("setSelection(['network-2'])")
            page.keyboard.press("Delete")
            page.wait_for_timeout(1000)
            tile = page.locator(".module-tile[data-kind='graph']").bounding_box()
            box = page.evaluate("cellBox(1, 1, 1, 1)")
            zoom = page.evaluate("state.zoom")
            wrap = page.locator("#canvas-wrap").bounding_box()
            page.mouse.move(tile["x"] + 30, tile["y"] + 20)
            page.mouse.down()
            page.mouse.move(tile["x"] + 80, tile["y"] + 40, steps=4)
            page.mouse.move(
                wrap["x"] + (box[0] + 40) * zoom, wrap["y"] + (box[1] + 60) * zoom, steps=12
            )
            page.wait_for_timeout(300)
            page.screenshot(path=str(out / "editor-drag.png"))
            page.mouse.up()
            browser.close()
    finally:
        server.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", nargs="?", default="docs/images", type=Path)
    out = parser.parse_args().out
    out.mkdir(parents=True, exist_ok=True)
    ids = ["studio", "spur-ii", "libre-default", "orbit", "slate", "column", "pico"]
    for theme_id in ids:  # true pixels, as the panel shows them
        render(theme_id).save(out / f"{theme_id}.png", optimize=True)
    big = {theme_id: render(theme_id, 2) for theme_id in ids}  # sharp when scaled
    hero(big["studio"]).save(out / "hero.png", optimize=True)
    gallery(big).save(out / "gallery.png", optimize=True)
    social(big["studio"]).save(out / "social-preview.png", optimize=True)
    weather = tuple(k for k in snapshot().readings if k.startswith("weather."))
    gpu = tuple(k for k in snapshot().readings if k.startswith("gpu."))
    adapts(
        [
            (render("studio", 2, weather), "Weather off", "· a calendar sheet takes the card"),
            (render("studio", 2, gpu), "No GPU sensor", "· the disk takes its ring"),
        ]
    ).save(out / "studio-adapts.png", optimize=True)
    module_sizes().save(out / "module-sizes.png", optimize=True)
    looks().save(out / "looks.png", optimize=True)
    module_layouts().save(out / "module-layouts.png", optimize=True)
    try:
        editor_shots(out)
    except ImportError:
        print("Playwright is not installed: the editor pictures are left as they are")
    print(f"wrote the images to {out}")


if __name__ == "__main__":
    main()
