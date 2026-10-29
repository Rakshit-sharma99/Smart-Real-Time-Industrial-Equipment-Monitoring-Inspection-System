"""
Generate the PWA icons (static/icons/icon-192.png and icon-512.png) with Pillow.

    python scripts/make_icons.py

The icon is the app's "pulse" mark: a light-blue heartbeat line on the dark
sidebar colour. Run once; the PNGs are committed to the repository.
"""
from pathlib import Path

from PIL import Image, ImageDraw

BACKGROUND = (31, 35, 40)  # #1f2328 (sidebar)
LINE = (96, 165, 250)  # #60a5fa (sidebar accent)
OUT_DIR = Path(__file__).resolve().parent.parent / "static" / "icons"

# Pulse line as fractions of the icon size (x, y).
PULSE = [(0.16, 0.54), (0.36, 0.54), (0.44, 0.34), (0.54, 0.72), (0.62, 0.46), (0.68, 0.54), (0.84, 0.54)]


def make_icon(size):
    scale = 4  # draw large, then shrink for smooth edges
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # Full-bleed square so the icon also works as a "maskable" icon (safe zone = centre 80 %).
    draw.rectangle([0, 0, big, big], fill=BACKGROUND)
    points = [(x * big, y * big) for x, y in PULSE]
    draw.line(points, fill=LINE, width=int(big * 0.07), joint="curve")
    radius = big * 0.035
    for x, y in (points[0], points[-1]):
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=LINE)
    return image.resize((size, size), Image.LANCZOS)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        path = OUT_DIR / f"icon-{size}.png"
        make_icon(size).save(path, optimize=True)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
