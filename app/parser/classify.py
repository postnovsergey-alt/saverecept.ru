"""Раскладка рецепта по категориям.

Основа — правила: они работают всегда, мгновенно и бесплатно, а на русских
названиях блюд дают очень приличную точность. LLM (если подключён) даёт
подсказку, но перебивает правила только когда те не уверены.
"""
from __future__ import annotations

import re

from app.categories import CATEGORIES, CATEGORY_BY_SLUG, DEFAULT_CATEGORY

# Приоритет при равном счёте: что «главнее» в названии блюда
PRIORITY = ["soups", "salads", "preserves", "drinks", "sauces", "desserts",
            "baking", "breakfast", "snacks", "sides", "main"]
PRIORITY_INDEX = {slug: i for i, slug in enumerate(PRIORITY)}

W_TITLE_STRONG, W_TITLE_KEY = 10.0, 4.0
W_DESC_STRONG, W_DESC_KEY = 3.0, 1.0
W_BODY_STRONG, W_BODY_KEY = 2.0, 0.5
W_SITE_CATEGORY = 6.0
W_LLM = 7.0


def _compile(marker: str) -> re.Pattern:
    # маркер должен начинаться с начала слова: «уха» не ловится внутри «муха»
    return re.compile(r"(?<![а-яёa-z])" + re.escape(marker.strip()), re.I)


_PATTERNS = {
    c["slug"]: {
        "strong": [(_compile(m), m) for m in c["strong"]],
        "keywords": [(_compile(m), m) for m in c["keywords"]],
    }
    for c in CATEGORIES
}

_SITE_CATEGORY_HINTS = {
    "soups": ["суп", "первые блюда", "soup"],
    "salads": ["салат", "salad"],
    "main": ["второе", "основные", "горячее", "main", "мясо", "рыба", "птица"],
    "sides": ["гарнир", "side"],
    "baking": ["выпечка", "хлеб", "пирог", "тесто", "bread", "bak"],
    "desserts": ["десерт", "сладк", "dessert"],
    "snacks": ["закус", "appetizer", "snack"],
    "breakfast": ["завтрак", "breakfast"],
    "drinks": ["напит", "drink", "beverage"],
    "sauces": ["соус", "sauce"],
    "preserves": ["заготов", "консерв", "на зиму"],
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е"))


def classify(
    title: str,
    description: str = "",
    ingredients: list[str] | None = None,
    steps: list[str] | None = None,
    site_category: str = "",
    llm_category: str = "",
) -> tuple[str, float]:
    """Возвращает (slug категории, уверенность 0..1)."""
    title_n = _norm(title)
    desc_n = _norm(description)
    body_n = _norm(" ".join((ingredients or []) + (steps or [])))[:6000]

    scores: dict[str, float] = {c["slug"]: 0.0 for c in CATEGORIES}
    first_hit: dict[str, int] = {c["slug"]: 10_000 for c in CATEGORIES}

    for slug, pats in _PATTERNS.items():
        for pat, _ in pats["strong"]:
            m = pat.search(title_n)
            if m:
                scores[slug] += W_TITLE_STRONG
                first_hit[slug] = min(first_hit[slug], m.start())
            if pat.search(desc_n):
                scores[slug] += W_DESC_STRONG
            if pat.search(body_n):
                scores[slug] += W_BODY_STRONG
        for pat, _ in pats["keywords"]:
            if pat.search(title_n):
                scores[slug] += W_TITLE_KEY
            if pat.search(desc_n):
                scores[slug] += W_DESC_KEY
            if pat.search(body_n):
                scores[slug] += W_BODY_KEY

    site_n = _norm(site_category)
    if site_n:
        for slug, hints in _SITE_CATEGORY_HINTS.items():
            if any(h in site_n for h in hints):
                scores[slug] += W_SITE_CATEGORY

    llm_slug = (llm_category or "").strip().lower()
    if llm_slug in CATEGORY_BY_SLUG:
        scores[llm_slug] += W_LLM

    best = max(
        scores.items(),
        key=lambda kv: (kv[1], -first_hit[kv[0]], -PRIORITY_INDEX.get(kv[0], 99)),
    )
    slug, score = best
    if score <= 0:
        return DEFAULT_CATEGORY, 0.0

    ordered = sorted(scores.values(), reverse=True)
    runner_up = ordered[1] if len(ordered) > 1 else 0.0
    margin = (score - runner_up) / score if score else 0.0
    confidence = min(1.0, (score / 14.0) * 0.6 + margin * 0.4)
    return slug, round(confidence, 2)
