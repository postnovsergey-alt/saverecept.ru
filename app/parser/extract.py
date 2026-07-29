"""Извлечение рецепта из HTML тремя способами, от точного к грубому.

1. schema.org в JSON-LD — так размечено большинство кулинарных сайтов, включая
   русские (eda.ru, gastronom.ru, russianfood, povarenok). Данные приходят
   структурированно, ошибок почти не бывает.
2. Микроразметка itemprop — старый вариант той же схемы.
3. Эвристика по вёрстке — ищем заголовки «Ингредиенты» и «Приготовление».
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

WS_RE = re.compile(r"[\s ​]+")
ISO_DUR_RE = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$", re.I)


def clean(text: str | None) -> str:
    if not text:
        return ""
    text = BeautifulSoup(str(text), "html.parser").get_text(" ")
    return WS_RE.sub(" ", text).strip()


def parse_duration(value) -> int:
    """ISO-8601 (PT1H30M) или число минут -> минуты."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, list):
        return max((parse_duration(v) for v in value), default=0)
    s = str(value).strip()
    m = ISO_DUR_RE.match(s)
    if m and any(m.groupdict().values()):
        d = int(m.group("d") or 0)
        h = int(m.group("h") or 0)
        mi = int(m.group("m") or 0)
        return d * 1440 + h * 60 + mi
    # «1 час 30 минут», «40 мин»
    total = 0
    for num, unit in re.findall(r"(\d+)\s*(час|ч\b|мин|минут)", s, re.I):
        total += int(num) * (60 if unit.lower().startswith(("час", "ч")) else 1)
    if total:
        return total
    if s.isdigit():
        return int(s)
    return 0


def _flatten_instructions(value, out: list[str]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        text = clean(value)
        # часто весь рецепт лежит одной строкой с переносами или нумерацией
        parts = re.split(r"(?:\r?\n)+|(?<=[.!?])\s+(?=\d+[.)]\s)", text)
        for p in parts:
            p = clean(p)
            if len(p) > 3:
                out.append(p)
        return
    if isinstance(value, list):
        for v in value:
            _flatten_instructions(v, out)
        return
    if isinstance(value, dict):
        t = value.get("@type", "")
        t = t[0] if isinstance(t, list) and t else t
        if t == "HowToSection":
            name = clean(value.get("name"))
            if name:
                out.append(f"— {name} —")
            _flatten_instructions(value.get("itemListElement"), out)
        else:
            text = clean(value.get("text") or value.get("name") or value.get("description"))
            if text:
                out.append(text)


def _flatten_images(value, base_url: str, out: list[str]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        if value.strip():
            out.append(urljoin(base_url, value.strip()))
        return
    if isinstance(value, list):
        for v in value:
            _flatten_images(v, base_url, out)
        return
    if isinstance(value, dict):
        _flatten_images(value.get("url") or value.get("contentUrl"), base_url, out)


def _iter_jsonld_nodes(soup: BeautifulSoup):
    for tag in soup.find_all("script", type=lambda v: v and "ld+json" in v.lower()):
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        data = None
        for attempt in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):
            try:
                data = json.loads(attempt)
                break
            except json.JSONDecodeError:
                continue
        if data is None:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if "@graph" in node:
                    stack.append(node["@graph"])
                yield node
                for v in node.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)


def _is_recipe(node: dict) -> bool:
    t = node.get("@type")
    if isinstance(t, list):
        return any(str(x).lower() == "recipe" for x in t)
    return str(t).lower() == "recipe"


def from_jsonld(soup: BeautifulSoup, base_url: str) -> dict | None:
    for node in _iter_jsonld_nodes(soup):
        if not _is_recipe(node):
            continue
        ingredients = node.get("recipeIngredient") or node.get("ingredients") or []
        if isinstance(ingredients, str):
            ingredients = [x for x in re.split(r"\r?\n", ingredients) if x.strip()]
        ingredients = [clean(x) for x in ingredients if clean(x)]

        steps: list[str] = []
        _flatten_instructions(node.get("recipeInstructions"), steps)

        images: list[str] = []
        _flatten_images(node.get("image"), base_url, images)

        minutes = parse_duration(node.get("totalTime"))
        if not minutes:
            minutes = parse_duration(node.get("cookTime")) + parse_duration(node.get("prepTime"))

        yield_val = node.get("recipeYield")
        if isinstance(yield_val, list):
            yield_val = yield_val[0] if yield_val else ""

        if not ingredients and not steps:
            continue

        return {
            "title": clean(node.get("name")),
            "description": clean(node.get("description")),
            "ingredients": ingredients,
            "steps": steps,
            "images": images,
            "total_minutes": minutes,
            "servings": clean(yield_val),
            "site_category": clean(node.get("recipeCategory")),
            "method": "jsonld",
        }
    return None


def from_microdata(soup: BeautifulSoup, base_url: str) -> dict | None:
    scope = soup.find(attrs={"itemtype": re.compile(r"schema\.org/Recipe", re.I)})
    if scope is None:
        return None

    def props(name: str) -> list:
        return scope.find_all(attrs={"itemprop": name})

    def first_text(name: str) -> str:
        els = props(name)
        if not els:
            return ""
        el = els[0]
        return clean(el.get("content") or el.get_text())

    ingredients = [clean(e.get("content") or e.get_text())
                   for e in props("recipeIngredient") + props("ingredients")]
    ingredients = [x for x in ingredients if x]

    steps: list[str] = []
    for e in props("recipeInstructions"):
        sub = e.find_all(attrs={"itemprop": "text"}) or e.find_all(["li", "p"])
        if sub:
            steps.extend(clean(s.get_text()) for s in sub)
        else:
            _flatten_instructions(clean(e.get("content") or e.get_text()), steps)
    steps = [s for s in steps if len(s) > 3]

    images: list[str] = []
    for e in props("image"):
        src = e.get("content") or e.get("src") or e.get("href")
        if src:
            images.append(urljoin(base_url, src))

    if not ingredients and not steps:
        return None

    minutes = parse_duration(
        (props("totalTime") or [{}])[0].get("datetime")
        if props("totalTime") else None) or parse_duration(first_text("totalTime"))

    return {
        "title": first_text("name") or clean(soup.title.get_text() if soup.title else ""),
        "description": first_text("description"),
        "ingredients": ingredients,
        "steps": steps,
        "images": images,
        "total_minutes": minutes,
        "servings": first_text("recipeYield"),
        "site_category": first_text("recipeCategory"),
        "method": "microdata",
    }


INGREDIENT_HEADINGS = re.compile(r"ингредиент|состав|продукт|понадобит|нам нужн", re.I)
STEP_HEADINGS = re.compile(r"приготовл|способ|инструкц|пошагов|как готовить|рецепт", re.I)
NOISE = re.compile(r"реклам|подписа|коммент|похожие|читайте также|поделит|войти|cookie", re.I)


def _collect_after(heading, matcher_stop) -> list[str]:
    """Собирает li/p, идущие за заголовком, до следующего заголовка."""
    items: list[str] = []
    for sib in heading.find_all_next():
        if sib.name in ("h1", "h2", "h3", "h4") and sib is not heading:
            if matcher_stop.search(sib.get_text() or ""):
                break
            if items:
                break
        if sib.name == "li" or (sib.name == "p" and len(items) < 40):
            text = clean(sib.get_text())
            if text and len(text) > 2 and not NOISE.search(text):
                items.append(text)
        if len(items) > 60:
            break
    return items


def from_heuristic(soup: BeautifulSoup, base_url: str) -> dict | None:
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()

    ingredients: list[str] = []
    steps: list[str] = []

    for h in soup.find_all(["h1", "h2", "h3", "h4", "strong", "b"]):
        text = clean(h.get_text())
        if not text or len(text) > 60:
            continue
        if not ingredients and INGREDIENT_HEADINGS.search(text):
            ingredients = _collect_after(h, STEP_HEADINGS)[:50]
        elif not steps and STEP_HEADINGS.search(text):
            steps = [s for s in _collect_after(h, INGREDIENT_HEADINGS) if len(s) > 15][:40]

    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = clean(og_title["content"])
    if not title and soup.h1:
        title = clean(soup.h1.get_text())
    if not title and soup.title:
        title = clean(soup.title.get_text())

    images = []
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        images.append(urljoin(base_url, og_image["content"]))

    desc = ""
    og_desc = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
    if og_desc and og_desc.get("content"):
        desc = clean(og_desc["content"])

    if not ingredients and not steps:
        return None

    return {
        "title": title,
        "description": desc,
        "ingredients": ingredients,
        "steps": steps,
        "images": images,
        "total_minutes": 0,
        "servings": "",
        "site_category": "",
        "method": "heuristic",
    }


def page_text(html: str, limit: int = 16000) -> str:
    """Очищенный текст страницы — вход для LLM-разбора."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside",
                     "form", "iframe", "svg"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [WS_RE.sub(" ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not NOISE.search(ln)]
    return "\n".join(lines)[:limit]


def og_text(html: str) -> str:
    """Собирает текст из og:title + og:description — для SPA-страниц вроде VK,
    где основной HTML пустой шелл, а весь текст поста только в og-тегах."""
    soup = BeautifulSoup(html, "lxml")
    parts: list[str] = []
    ogt = soup.find("meta", property="og:title")
    if ogt and ogt.get("content"):
        title = ogt["content"].strip()
        # у VK у всех постов og:title = «Пост на стене» (в googlebot-версии) —
        # такой заголовок бесполезен, лучше отдать LLM додумать из тела
        if title and title.lower() not in ("пост на стене", "post on wall"):
            parts.append(title)
    ogd = soup.find("meta", property="og:description")
    if ogd and ogd.get("content"):
        raw = re.sub(r"<br\s*/?>", "\n", ogd["content"], flags=re.I)
        text = BeautifulSoup(raw, "html.parser").get_text("\n")
        lines = [re.sub(r"[ \t ​]+", " ", ln).strip() for ln in text.splitlines()]
        lines = [ln for ln in lines if ln]
        if lines:
            parts.append("\n".join(lines))
    return "\n\n".join(parts)


def page_meta(html: str, base_url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    images = []
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        images.append(urljoin(base_url, og["content"]))
    site = soup.find("meta", property="og:site_name")
    return {
        "images": images,
        "site_title": clean(site["content"]) if site and site.get("content") else "",
    }
