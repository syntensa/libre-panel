"""Write the icon files from the logo code: python packaging/make_icons.py"""

from pathlib import Path

from libre_panel.branding import logo

ROOT = Path(__file__).resolve().parent.parent
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def main() -> None:
    logo(256).save(ROOT / "src/libre_panel/assets/libre-panel.png", optimize=True)
    logo(64).save(ROOT / "src/libre_panel/editor/static/icon.png", optimize=True)
    icons = ROOT / "packaging/icons"
    icons.mkdir(exist_ok=True)
    logo(512).save(icons / "libre-panel-512.png", optimize=True)
    logo(1024).save(icons / "libre-panel.icns")  # macOS app
    logo(256).save(
        icons / "libre-panel.ico",
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=[logo(s) for s in ICO_SIZES if s != 256],
    )


if __name__ == "__main__":
    main()
