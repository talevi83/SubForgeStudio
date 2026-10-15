"""Draws packaging/icon.ico (exe + tray icon): a film frame with two subtitle lines.

Run once after changing the design:  python packaging/make_icon.py   (needs Pillow)
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "icon.ico"
SIZE = 1024
TOP, BOTTOM = (99, 102, 241), (67, 56, 202)    # indigo-500 -> indigo-700, the studio accent


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    # vertical gradient clipped to a rounded square
    grad = Image.new("RGBA", (SIZE, SIZE))
    gd = ImageDraw.Draw(grad)
    for y in range(SIZE):
        t = y / (SIZE - 1)
        gd.line([(0, y), (SIZE, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)) + (255,))
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([40, 40, SIZE - 40, SIZE - 40], radius=220, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    # film frame with sprocket holes
    fx0, fy0, fx1, fy1 = 200, 210, 824, 600
    d.rounded_rectangle([fx0, fy0, fx1, fy1], radius=60, fill=white)
    hole = (79, 70, 229, 255)    # indigo-600
    for i in range(5):
        x = fx0 + 70 + i * 121
        d.rounded_rectangle([x - 26, fy0 + 30, x + 26, fy0 + 78], radius=12, fill=hole)
        d.rounded_rectangle([x - 26, fy1 - 78, x + 26, fy1 - 30], radius=12, fill=hole)
    # play triangle
    cx, cy = (fx0 + fx1) // 2, (fy0 + fy1) // 2
    d.polygon([(cx - 60, cy - 72), (cx - 60, cy + 72), (cx + 80, cy)], fill=hole)
    # subtitle lines
    d.rounded_rectangle([230, 670, 794, 730], radius=30, fill=white)
    d.rounded_rectangle([330, 770, 694, 830], radius=30, fill=(255, 255, 255, 210))
    return img


if __name__ == "__main__":
    draw().save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {OUT}")
