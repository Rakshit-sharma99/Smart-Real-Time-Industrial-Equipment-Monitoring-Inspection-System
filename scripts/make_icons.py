"""
Generate the PWA icons (static/icons/icon-192.png and icon-512.png) with Pillow.

    python scripts/make_icons.py

The icon is the app's mark: a 3x3 grid of table tiles on the charcoal sidebar
colour, with the centre tile in the teal accent. Run once; the PNGs are committed.
"""
from pathlib import Path

from PIL import Image, ImageDraw

BACKGROUND = (31, 35, 40)  # #1f2328 (sidebar)
TILE = (229, 231, 235)  # light tiles
ACCENT = (15, 118, 110)  # #0f766e (teal accent)
OUT_DIR = Path(__file__).resolve().parent.parent / "static" / "icons"


def make_icon(size):
    scale = 4  # draw large, then shrink for smooth edges
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # Full-bleed square so the icon also works as a "maskable" icon (safe zone = centre 80 %).
    draw.rectangle([0, 0, big, big], fill=BACKGROUND)
    cell, gap, margin = big * 0.16, big * 0.06, big * 0.22
    for row in range(3):
        for col in range(3):
            x = margin + col * (cell + gap)
            y = margin + row * (cell + gap)
            colour = ACCENT if (row, col) == (1, 1) else TILE
            draw.rounded_rectangle([x, y, x + cell, y + cell], radius=big * 0.03, fill=colour)
    return image.resize((size, size), Image.LANCZOS)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        path = OUT_DIR / f"icon-{size}.png"
        make_icon(size).save(path, optimize=True)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
