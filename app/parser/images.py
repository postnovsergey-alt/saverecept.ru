"""Скачивание и сжатие картинок.

Оригиналы не храним: приводим к WebP шириной до IMAGE_MAX_WIDTH плюс
миниатюра для списка. Типичная фотография с кулинарного сайта весит
1-3 МБ, после обработки — 60-150 КБ, то есть экономия примерно в 15 раз.
"""
from __future__ import annotations

import hashlib
import io
import logging

from PIL import Image as PILImage
from PIL import ImageOps

from app.config import (
    IMAGE_MAX_COUNT, IMAGE_MAX_WIDTH, IMAGE_QUALITY, IMAGE_THUMB_WIDTH, MEDIA_DIR,
)
from app.parser.fetch import fetch_bytes

log = logging.getLogger(__name__)

MIN_SIDE = 200        # мельче — это иконка или логотип, не еда
PILImage.MAX_IMAGE_PIXELS = 80_000_000


def _resize_to_width(img: PILImage.Image, width: int) -> PILImage.Image:
    if img.width <= width:
        return img.copy()
    height = max(1, round(img.height * width / img.width))
    return img.resize((width, height), PILImage.LANCZOS)


def _save_webp(img: PILImage.Image, path, quality: int) -> int:
    img.save(path, "WEBP", quality=quality, method=5)
    return path.stat().st_size


def process_image_bytes(data: bytes, source_url: str = "") -> dict | None:
    """Возвращает описание сохранённых файлов или None, если картинка не годится."""
    digest = hashlib.sha256(data).hexdigest()[:20]
    main_name = f"{digest}.webp"
    thumb_name = f"{digest}_t.webp"
    main_path = MEDIA_DIR / main_name
    thumb_path = MEDIA_DIR / thumb_name

    try:
        with PILImage.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            if min(img.size) < MIN_SIDE:
                return None
            if img.mode in ("RGBA", "LA", "P"):
                background = PILImage.new("RGB", img.size, (255, 255, 255))
                converted = img.convert("RGBA")
                background.paste(converted, mask=converted.split()[-1])
                img = background
            else:
                img = img.convert("RGB")

            if main_path.exists() and thumb_path.exists():  # уже сохраняли такую
                main = _resize_to_width(img, IMAGE_MAX_WIDTH)
                return {
                    "filename": main_name, "thumb_filename": thumb_name,
                    "width": main.width, "height": main.height,
                    "bytes": main_path.stat().st_size, "source_url": source_url,
                }

            main = _resize_to_width(img, IMAGE_MAX_WIDTH)
            size = _save_webp(main, main_path, IMAGE_QUALITY)
            thumb = _resize_to_width(img, IMAGE_THUMB_WIDTH)
            _save_webp(thumb, thumb_path, IMAGE_QUALITY)

            return {
                "filename": main_name, "thumb_filename": thumb_name,
                "width": main.width, "height": main.height,
                "bytes": size, "source_url": source_url,
            }
    except Exception as e:  # noqa: BLE001 — битая картинка не должна ронять рецепт
        log.warning("Картинку %s обработать не вышло: %s", source_url[:120], e)
        return None


def download_and_process(urls: list[str], limit: int = IMAGE_MAX_COUNT) -> list[dict]:
    saved: list[dict] = []
    seen: set[str] = set()
    for url in urls:
        if len(saved) >= limit:
            break
        if not url or url in seen or url.startswith("data:"):
            continue
        seen.add(url)
        try:
            data = fetch_bytes(url)
        except Exception as e:  # noqa: BLE001
            log.info("Картинка не скачалась %s: %s", url[:120], e)
            continue
        info = process_image_bytes(data, source_url=url)
        if info and all(info["filename"] != s["filename"] for s in saved):
            saved.append(info)
    return saved
