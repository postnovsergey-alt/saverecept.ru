"""Слой работы с базой — общий для сайта и Telegram-бота.

Все операции с рецептами привязаны к владельцу (User). Сайт передаёт
current user, бот берёт пользователя по tg_user_id. Дубли ссылок ищутся
внутри одной книги: если у Пети уже есть рецепт этого блюда, а Оля кинула
ту же ссылку — у Оли добавится своя копия.
"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Image, Ingredient, Recipe, Step, User
from app.parser.pipeline import ParsedRecipe, parse_url
from app.utils import slugify, url_key


def _unique_slug(db: Session, owner_id: int, title: str) -> str:
    base = slugify(title) or "recipe"
    slug = base
    n = 2
    while db.scalar(
        select(func.count()).select_from(Recipe).where(
            Recipe.owner_id == owner_id, Recipe.slug == slug)
    ):
        slug = f"{base}-{n}"
        n += 1
    return slug


def find_by_source(db: Session, owner_id: int, url: str) -> Recipe | None:
    return db.scalar(select(Recipe).where(
        Recipe.owner_id == owner_id, Recipe.source_key == url_key(url)))


def save_parsed(
    db: Session, owner: User, parsed: ParsedRecipe,
    added_from: str = "web", added_by: str = "",
) -> Recipe:
    recipe = Recipe(
        owner_id=owner.id,
        slug=_unique_slug(db, owner.id, parsed.title),
        title=parsed.title,
        description=parsed.description,
        source_url=parsed.source_url,
        source_key=parsed.source_key,
        source_domain=parsed.source_domain,
        source_title=parsed.source_title,
        category=parsed.category,
        category_confidence=parsed.category_confidence,
        servings=parsed.servings,
        total_minutes=parsed.total_minutes,
        parse_method=parsed.parse_method,
        added_from=added_from,
        added_by=added_by[:120],
        search_blob=parsed.search_blob,
    )
    for i, item in enumerate(parsed.ingredients):
        recipe.ingredients.append(Ingredient(
            position=i, raw=item["raw"], name=item["name"], name_norm=item["name_norm"],
            amount=item["amount"], unit=item["unit"], note=item["note"],
        ))
    for i, text in enumerate(parsed.steps):
        recipe.steps.append(Step(position=i, text=text))
    for i, img in enumerate(parsed.images):
        recipe.images.append(Image(
            position=i, filename=img["filename"], thumb_filename=img["thumb_filename"],
            width=img["width"], height=img["height"], bytes=img["bytes"],
            source_url=img.get("source_url", ""),
        ))
    db.add(recipe)
    db.commit()
    return recipe


def add_from_url(
    db: Session, owner: User, url: str,
    added_from: str = "web", added_by: str = "",
) -> tuple[Recipe, bool]:
    """Возвращает (рецепт, создан_ли_новый). Повтор ссылки одним и тем же
    пользователем дубля не создаёт; у разных пользователей — отдельные копии."""
    existing = find_by_source(db, owner.id, url)
    if existing:
        return existing, False
    parsed = parse_url(url)
    return save_parsed(db, owner, parsed, added_from=added_from, added_by=added_by), True


def list_recipes(
    db: Session, owner_id: int,
    category: str | None = None,
    q: str | None = None,
    favorites_only: bool = False,
    limit: int = 60,
    offset: int = 0,
) -> list[Recipe]:
    stmt = select(Recipe).where(Recipe.owner_id == owner_id)
    if category:
        stmt = stmt.where(Recipe.category == category)
    if favorites_only:
        stmt = stmt.where(Recipe.is_favorite.is_(True))
    if q:
        needle = f"%{q.lower().replace('ё', 'е')}%"
        stmt = stmt.where(or_(
            func.lower(Recipe.title).like(needle),
            Recipe.search_blob.like(needle),
        ))
    stmt = stmt.order_by(Recipe.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def count_by_category(db: Session, owner_id: int) -> dict[str, int]:
    rows = db.execute(
        select(Recipe.category, func.count())
        .where(Recipe.owner_id == owner_id)
        .group_by(Recipe.category)
    ).all()
    return {slug: n for slug, n in rows}


def get_by_slug(db: Session, owner_id: int, slug: str) -> Recipe | None:
    return db.scalar(select(Recipe).where(
        Recipe.owner_id == owner_id, Recipe.slug == slug))


def get_by_id(db: Session, owner_id: int, recipe_id: int) -> Recipe | None:
    recipe = db.get(Recipe, recipe_id)
    return recipe if recipe and recipe.owner_id == owner_id else None


def total_count(db: Session, owner_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(Recipe).where(Recipe.owner_id == owner_id)
    ) or 0


def set_category(db: Session, recipe: Recipe, category: str) -> None:
    recipe.category = category
    recipe.category_locked = True
    recipe.category_confidence = 1.0
    db.commit()


def toggle_favorite(db: Session, recipe: Recipe) -> bool:
    recipe.is_favorite = not recipe.is_favorite
    db.commit()
    return recipe.is_favorite


def delete_recipe(db: Session, recipe: Recipe) -> None:
    db.delete(recipe)
    db.commit()
