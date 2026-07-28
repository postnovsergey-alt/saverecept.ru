"""Пайплайн: ссылка -> готовый рецепт.

Порядок разбора: JSON-LD -> микроразметка -> LLM -> эвристика.
Первый способ, давший осмысленный результат, побеждает; недостающие поля
дополняются из остальных.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from app.parser import extract
from app.parser.classify import classify
from app.parser.fetch import FetchError, fetch_html
from app.parser.images import download_and_process
from app.parser.ingredients import parse_ingredient_line
from app.parser.llm import extract_with_llm
from app.utils import domain_of, normalize_url, url_key

log = logging.getLogger(__name__)


class ParseError(RuntimeError):
    pass


@dataclass
class ParsedRecipe:
    title: str
    description: str = ""
    ingredients: list[dict] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)
    total_minutes: int = 0
    servings: str = ""
    category: str = "main"
    category_confidence: float = 0.0
    source_url: str = ""
    source_key: str = ""
    source_domain: str = ""
    source_title: str = ""
    parse_method: str = ""

    @property
    def search_blob(self) -> str:
        parts = [self.title, self.description, self.source_domain]
        parts += [i.get("name", "") for i in self.ingredients]
        return " ".join(p for p in parts if p).lower().replace("ё", "е")[:4000]


def _quality(candidate: dict | None) -> int:
    if not candidate:
        return 0
    ing = len(candidate.get("ingredients") or [])
    steps = len(candidate.get("steps") or [])
    if ing >= 2 and steps >= 1:
        return 3
    if ing >= 2 or steps >= 2:
        return 2
    if ing or steps:
        return 1
    return 0


def _merge(base: dict, extra: dict | None) -> dict:
    if not extra:
        return base
    for key in ("title", "description", "servings", "site_category"):
        if not base.get(key) and extra.get(key):
            base[key] = extra[key]
    if not base.get("total_minutes") and extra.get("total_minutes"):
        base["total_minutes"] = extra["total_minutes"]
    for key in ("ingredients", "steps", "images"):
        if not base.get(key) and extra.get(key):
            base[key] = extra[key]
    if not base.get("llm_category") and extra.get("llm_category"):
        base["llm_category"] = extra["llm_category"]
    # картинки складываем: og:image часто лучше, чем то, что в разметке
    seen = set(base.get("images") or [])
    for img in extra.get("images") or []:
        if img not in seen:
            base.setdefault("images", []).append(img)
            seen.add(img)
    return base


def parse_html(html: str, url: str, use_llm: bool = True) -> ParsedRecipe:
    soup = BeautifulSoup(html, "lxml")
    meta = extract.page_meta(html, url)

    candidates: list[dict] = []
    for extractor in (extract.from_jsonld, extract.from_microdata):
        try:
            result = extractor(BeautifulSoup(html, "lxml"), url)
        except Exception as e:  # noqa: BLE001
            log.warning("%s упал: %s", extractor.__name__, e)
            result = None
        if result:
            candidates.append(result)
        if _quality(result) >= 3:
            break

    best = max(candidates, key=_quality) if candidates else None

    if _quality(best) < 3 and use_llm:
        llm_result = extract_with_llm(extract.page_text(html))
        if _quality(llm_result) > _quality(best):
            best, llm_result = llm_result, best
        best = _merge(best or {}, llm_result)

    if _quality(best) < 2:
        heuristic = extract.from_heuristic(BeautifulSoup(html, "lxml"), url)
        if _quality(heuristic) > _quality(best):
            best, heuristic = heuristic, best
        best = _merge(best or {}, heuristic)

    for other in candidates:
        best = _merge(best or {}, other)

    if not best or _quality(best) < 1:
        raise ParseError(
            "Рецепт со страницы вытащить не получилось. Бывает на сайтах, где рецепт "
            "только в видео или прячется за скриптами."
        )

    best = _merge(best, {"images": meta["images"]})

    title = (best.get("title") or "").strip() or "Рецепт без названия"
    ingredients = [parse_ingredient_line(x) for x in (best.get("ingredients") or [])]
    ingredients = [i for i in ingredients if i["name"]]
    steps = [s for s in (best.get("steps") or []) if s and len(s) > 2]

    category, confidence = classify(
        title=title,
        description=best.get("description", ""),
        ingredients=[i["raw"] for i in ingredients],
        steps=steps,
        site_category=best.get("site_category", ""),
        llm_category=best.get("llm_category", ""),
    )

    return ParsedRecipe(
        title=title[:400],
        description=(best.get("description") or "")[:1500],
        ingredients=ingredients,
        steps=steps,
        images=[{"source_url": u} for u in (best.get("images") or [])],
        total_minutes=int(best.get("total_minutes") or 0),
        servings=(best.get("servings") or "")[:60],
        category=category,
        category_confidence=confidence,
        source_url=normalize_url(url),
        source_key=url_key(url),
        source_domain=domain_of(url),
        source_title=meta.get("site_title", "")[:300],
        parse_method=best.get("method", "mixed"),
    )


def parse_url(url: str, download_images: bool = True, use_llm: bool = True) -> ParsedRecipe:
    url = normalize_url(url)
    if not url:
        raise ParseError("Пустая ссылка")
    try:
        html, final_url = fetch_html(url)
    except FetchError as e:
        raise ParseError(str(e)) from e

    recipe = parse_html(html, final_url, use_llm=use_llm)

    if download_images and recipe.images:
        recipe.images = download_and_process([i["source_url"] for i in recipe.images])
    elif not download_images:
        recipe.images = []
    return recipe
