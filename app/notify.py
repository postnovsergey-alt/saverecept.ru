"""Слать сообщения в Telegram из веб-приложения (уведомления о фидбеке).

Ходим напрямую в Bot API через httpx — процесс бота держать поднятым не нужно,
достаточно валидного TELEGRAM_BOT_TOKEN. Все ошибки глотаем и логируем: отзыв
уже сохранён в БД, отсутствие уведомления не должно ломать POST.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.config import TELEGRAM_BOT_TOKEN

log = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
_TIMEOUT = 10.0


def _api(method: str) -> str:
    return f"{_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/{method}"


def send_text(chat_id: int, text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        return False
    try:
        r = httpx.post(_api("sendMessage"), timeout=_TIMEOUT, data={
            "chat_id": chat_id, "text": text[:4000],
            "parse_mode": "HTML", "disable_web_page_preview": True,
        })
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Не отправили сообщение в TG (%s): %s", chat_id, e)
        return False


def send_photo(chat_id: int, photo_path: Path, caption: str = "") -> bool:
    if not TELEGRAM_BOT_TOKEN or not photo_path.exists():
        return False
    try:
        with photo_path.open("rb") as f:
            r = httpx.post(
                _api("sendPhoto"), timeout=_TIMEOUT,
                data={"chat_id": chat_id, "caption": caption[:1000],
                      "parse_mode": "HTML"},
                files={"photo": (photo_path.name, f, "image/webp")},
            )
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Не отправили фото в TG (%s): %s", chat_id, e)
        return False
