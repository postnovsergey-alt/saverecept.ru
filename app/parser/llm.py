"""Запасной разбор через LLM — для страниц без разметки.

Основной провайдер — Gemini: у него OpenAI-совместимый эндпоинт, поэтому весь
код ниже обычный «chat/completions» и переезд на любого другого провайдера
это правка трёх строк в .env.

Провайдеры пробуются по очереди: сначала основной, при отказе (лимит, сеть,
битый ответ) — запасной, если он настроен. Модуль никогда не бросает исключение
наружу: если LLM недоступен, пайплайн просто идёт по эвристике.
"""
from __future__ import annotations

import json
import logging
import re
import time

import httpx

from app.categories import CATEGORIES
from app.config import LLM_PROVIDERS

log = logging.getLogger(__name__)

_CATEGORY_LIST = ", ".join(f'{c["slug"]} ({c["title"]})' for c in CATEGORIES)

SYSTEM = (
    "Ты извлекаешь рецепты из текста веб-страниц. Отвечай только JSON-объектом, "
    "без пояснений и без markdown-обёртки."
)

PROMPT = """Из текста страницы извлеки рецепт. Верни JSON:

{{
  "is_recipe": true/false,
  "title": "название блюда",
  "description": "1-2 предложения, можно пустую строку",
  "ingredients": ["500 г куриного филе", "2 ст. л. оливкового масла"],
  "steps": ["шаг 1", "шаг 2"],
  "total_minutes": 45,
  "servings": "4 порции",
  "category": "один слаг из списка"
}}

Категории: {categories}

Правила:
- ингредиенты записывай строкой с количеством, как на странице;
- шаги — связные предложения, без нумерации в начале;
- ничего не выдумывай: чего нет на странице, оставляй пустым;
- если это не рецепт (статья, подборка, магазин), верни is_recipe: false.

Текст страницы:
---
{text}
---"""


def llm_enabled() -> bool:
    return bool(LLM_PROVIDERS)


def _extract_json(content: str) -> dict | None:
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", content, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _call(provider: dict, text: str, timeout: float) -> str:
    payload = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": PROMPT.format(categories=_CATEGORY_LIST, text=text)},
        ],
        "temperature": 0.1,
        "max_tokens": 3000,
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.post(
            f"{provider['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {provider['api_key']}",
                     "Content-Type": "application/json"},
            json=payload,
        )
        if r.status_code == 429:
            raise RuntimeError("лимит запросов исчерпан")
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def _normalize(data: dict) -> dict | None:
    if not data or data.get("is_recipe") is False:
        return None
    ingredients = [str(x).strip() for x in (data.get("ingredients") or []) if str(x).strip()]
    steps = [str(x).strip() for x in (data.get("steps") or []) if str(x).strip()]
    if not ingredients and not steps:
        return None
    try:
        minutes = int(data.get("total_minutes") or 0)
    except (TypeError, ValueError):
        minutes = 0
    return {
        "title": str(data.get("title") or "").strip(),
        "description": str(data.get("description") or "").strip(),
        "ingredients": ingredients,
        "steps": steps,
        "images": [],
        "total_minutes": minutes,
        "servings": str(data.get("servings") or "").strip(),
        "site_category": "",
        "llm_category": str(data.get("category") or "").strip(),
        "method": "llm",
    }


def extract_with_llm(text: str, timeout: float = 60.0) -> dict | None:
    if not LLM_PROVIDERS or not text.strip():
        return None

    for provider in LLM_PROVIDERS:
        started = time.monotonic()
        try:
            content = _call(provider, text, timeout)
        except Exception as e:  # noqa: BLE001 — LLM опционален, падать нельзя
            log.warning("LLM %s (%s) не ответил: %s", provider["name"], provider["model"], e)
            continue

        result = _normalize(_extract_json(content) or {})
        took = time.monotonic() - started
        if result:
            log.info("LLM %s разобрал страницу за %.1f с", provider["name"], took)
            return result
        log.warning("LLM %s вернул ответ, из которого рецепт не собрался", provider["name"])
    return None


def ping() -> list[dict]:
    """Проверка провайдеров — используется в tools/check_llm.py и в деплое."""
    report = []
    for provider in LLM_PROVIDERS:
        entry = {"name": provider["name"], "model": provider["model"], "ok": False, "detail": ""}
        try:
            started = time.monotonic()
            content = _call(provider, "Проверка связи. Верни {\"is_recipe\": false}.", 30.0)
            entry["ok"] = True
            entry["detail"] = f"ответил за {time.monotonic() - started:.1f} с: {content[:60]}"
        except Exception as e:  # noqa: BLE001
            entry["detail"] = str(e)[:200]
        report.append(entry)
    return report
