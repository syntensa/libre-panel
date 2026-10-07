"""Render the images in docs/images from the built-in themes.

    python tools/readme_images.py [docs/images]

Each theme as the panel shows it (<theme>.png), the README's hero, gallery and
"fits your machine" pictures, and the 1280x640 social preview (upload it in
the repository's settings). With Playwright installed, also the editor
screenshot. Demo sensor values and a fixed time keep the pictures stable.
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


def editor(path: Path) -> None:
    """The editor with Studio, its GPU ring selected (needs Playwright)."""
    from playwright.sync_api import sync_playwright

    os.environ["LIBRE_PANEL_HOME"] = tempfile.mkdtemp()  # no user themes or config
    from libre_panel.editor.server import make_server

    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    rendered = "document.querySelector('#preview').src.startsWith('data:image/png')"
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
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
            page.wait_for_function(rendered)
            page.evaluate("document.querySelector('#preview').src = ''")
            page.select_option("#theme-select", "studio")
            page.wait_for_function(rendered)
            page.wait_for_timeout(800)
            page.evaluate("setSelection(['gpu-ring'])")
            page.wait_for_timeout(800)
            page.screenshot(path=str(path))
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
    try:
        editor(out / "editor.png")
    except ImportError:
        print("Playwright is not installed: editor.png left as it is")
    print(f"wrote the images to {out}")


if __name__ == "__main__":
    main()
