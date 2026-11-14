#!/usr/bin/env python3
"""Chrome拡張機能のアイコン（tools/icons/icon16〜128.png）を作る.

Aurora の店舗リサーチアプリ（紺地にカメラ）と同じデザインに、
「鑑定」を表す金色の ¥ バッジを付けて見分けられるようにしている。要 Pillow。
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "icons"
NAVY, LIGHT, TEAL, GOLD, INK = "#0b1728", "#dcecf6", "#4fc3d9", "#f2b631", "#0b1728"


def draw(size: int = 1024) -> Image.Image:
    s = size / 128
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(26 * s), fill=NAVY)
    # カメラ本体（店舗リサーチアプリのアイコンと同じ形）
    d.rounded_rectangle([int(30 * s), int(26 * s), int(58 * s), int(40 * s)], radius=int(4 * s), fill=LIGHT)
    d.rounded_rectangle([int(16 * s), int(36 * s), int(104 * s), int(96 * s)], radius=int(14 * s),
                        outline=LIGHT, width=int(8 * s))
    d.ellipse([int(42 * s), int(46 * s), int(78 * s), int(82 * s)], outline=TEAL, width=int(9 * s))
    d.ellipse([int(88 * s), int(44 * s), int(96 * s), int(52 * s)], fill=TEAL)
    # 鑑定バッジ（¥）
    cx, cy, r = 96 * s, 96 * s, 26 * s
    d.ellipse([cx - r - 5 * s, cy - r - 5 * s, cx + r + 5 * s, cy + r + 5 * s], fill=NAVY)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=GOLD)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", int(36 * s))
    except OSError:
        font = ImageFont.load_default()
    d.text((cx, cy + 1 * s), "¥", fill=INK, font=font, anchor="mm")
    return im


def main() -> None:
    OUT.mkdir(exist_ok=True)
    big = draw()
    for n in (16, 32, 48, 128):
        big.resize((n, n), Image.LANCZOS).save(OUT / f"icon{n}.png")
    print(f"アイコンを作成: {OUT}")


if __name__ == "__main__":
    main()
