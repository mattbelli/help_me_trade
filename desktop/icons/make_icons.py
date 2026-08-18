"""Rasterize brand/stand_in-dark.svg (two rects on a dark ground) into the Tauri icon set.

Run: .venv/bin/python desktop/icons/make_icons.py   (PIL only — no SVG tool on serv)
"""
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
BG = (11, 14, 20, 255)          # cockpit dark ground
HOLLOW = (154, 163, 175, 255)   # #9aa3af — you (away)
SOLID = (233, 235, 238, 255)    # #e9ebee — the agent (standing in)


def render(px: int) -> Image.Image:
    s = px / 120.0
    im = Image.new("RGBA", (px, px), BG)
    d = ImageDraw.Draw(im)
    r = lambda x0, y0, x1, y1: [round(x0 * s), round(y0 * s), round(x1 * s) - 1, round(y1 * s) - 1]
    d.rectangle(r(34, 38, 54, 92), fill=HOLLOW)
    d.rectangle(r(39.5, 43.5, 48.5, 86.5), fill=BG)
    d.rectangle(r(66, 26, 86, 80), fill=SOLID)
    return im


def main() -> None:
    for name, px in (("32x32.png", 32), ("128x128.png", 128), ("128x128@2x.png", 256), ("icon.png", 1024)):
        render(px).save(HERE / name)
    base = render(1024)
    base.save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    base.save(HERE / "icon.icns")
    print("wrote", sorted(p.name for p in HERE.iterdir()))


if __name__ == "__main__":
    main()
