"""Собирает /static/og-cover.png (1200×630) для превью в мессенджерах и поиске.

Запускать разово при смене брендинга: `python tools/make_og_cover.py`.
Отдельный скрипт, а не runtime — картинке незачем перегенериться на каждый запрос.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "static" / "og-cover.png"

W, H = 1200, 630
BG = (16, 14, 12)
INK = (238, 231, 219)
MUTED = (168, 158, 143)
AMBER = (233, 168, 63)
EMBER = (212, 101, 58)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    # Ставим предпочитаемые шрифты; если системного нет — берём то, что найдётся.
    candidates_serif = [
        "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
        "/System/Library/Fonts/SFGeorgian.ttf",
        "/System/Library/Fonts/Times.ttc",
    ]
    candidates_sans = [
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in (candidates_serif if bold else candidates_sans):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _radial(cx: int, cy: int, r: int, color: tuple[int, int, int], alpha: int) -> Image.Image:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for i in range(r, 0, -6):
        a = int(alpha * (1 - i / r) ** 2)
        draw.ellipse((cx - i, cy - i, cx + i, cy + i), fill=(*color, a))
    return layer


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    img = img.convert("RGBA")
    img = Image.alpha_composite(img, _radial(180, 100, 520, AMBER, 60))
    img = Image.alpha_composite(img, _radial(1100, 60, 380, EMBER, 55))

    draw = ImageDraw.Draw(img)

    # Скатерть-марка сверху слева
    mark_x, mark_y = 80, 78
    draw.ellipse((mark_x - 4, mark_y - 4, mark_x + 44, mark_y + 44),
                 outline=AMBER, width=4)
    draw.rectangle((mark_x - 10, mark_y + 26, mark_x + 50, mark_y + 38),
                   fill=AMBER)

    brand_font = _font(34)
    draw.text((mark_x + 62, mark_y + 4), "Самобранка",
              font=brand_font, fill=INK)

    # Заголовок
    title_font = _font(84, bold=True)
    accent_font = _font(84, bold=True)
    draw.text((80, 210), "Личная книга",
              font=title_font, fill=INK)
    draw.text((80, 300), "рецептов —",
              font=title_font, fill=INK)
    draw.text((80, 390), "собирается сама.",
              font=accent_font, fill=AMBER)

    sub_font = _font(30)
    draw.text((80, 500),
              "Ссылка · фото · голосовое — аккуратный рецепт",
              font=sub_font, fill=MUTED)

    domain_font = _font(24)
    draw.text((80, 555), "saverecept.ru",
              font=domain_font, fill=AMBER)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(OUT, format="PNG", optimize=True)
    print(f"написали {OUT}  ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
