"""Обложка для видео-источника без авторизации.

YouTube отдаёт превью прямой ссылкой на `img.youtube.com` — работает всегда,
без ключей и токенов. TikTok — через публичный oEmbed
(`www.tiktok.com/oembed?url=...`), тоже без авторизации. Instagram намеренно
не поддерживаем: их oEmbed требует Facebook App ID/Secret и модерации.
"""
from __future__ import annotations

import logging

import httpx

from app.utils import video_embed

log = logging.getLogger(__name__)

_TIMEOUT = 8.0
_UA = "Mozilla/5.0 (Samobranka/1.0)"


def fetch_video_thumbnail(url: str) -> bytes | None:
    """Возвращает байты превью или None. Никогда не бросает исключений —
    обложка не критична, любую ошибку сети/парсинга проглатываем."""
    info = video_embed(url)
    if not info:
        return None
    try:
        if info["kind"] == "youtube":
            return _fetch_youtube(info["video_id"])
        if info["kind"] == "tiktok":
            return _fetch_tiktok(url)
    except Exception as e:  # noqa: BLE001 — обложка не критична
        log.info("Обложка видео не подтянулась %s: %s", url[:120], e)
    return None


def _fetch_youtube(video_id: str) -> bytes | None:
    # maxresdefault есть не у всех роликов (короткие Shorts часто без него),
    # поэтому падаем в hqdefault — он гарантирован для любого публичного видео.
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": _UA}) as client:
        for name in ("maxresdefault", "hqdefault"):
            r = client.get(f"https://img.youtube.com/vi/{video_id}/{name}.jpg")
            # YouTube на отсутствующие превью иногда отвечает 200 + мелкая
            # заглушка «no thumbnail» ~1.5 КБ, поэтому фильтруем по размеру.
            if r.status_code == 200 and len(r.content) > 2000:
                return r.content
    return None


def _fetch_tiktok(url: str) -> bytes | None:
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": _UA}) as client:
        oembed = client.get("https://www.tiktok.com/oembed",
                            params={"url": url})
        oembed.raise_for_status()
        thumb_url = oembed.json().get("thumbnail_url")
        if not thumb_url:
            return None
        img = client.get(thumb_url)
        img.raise_for_status()
        return img.content
