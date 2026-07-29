"""Слой работы с базой — общий для сайта и Telegram-бота.

Все операции с рецептами привязаны к владельцу (User). Сайт передаёт
current user, бот берёт пользователя по tg_user_id. Дубли ссылок ищутся
внутри одной книги: если у Пети уже есть рецепт этого блюда, а Оля кинула
ту же ссылку — у Оли добавится своя копия.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Event, Feedback, Image, Ingredient, Recipe, Step, User
from app.parser.pipeline import ParsedRecipe, parse_image, parse_url
from app.utils import domain_of, normalize_url, slugify, url_key


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
            is_source=bool(img.get("is_source", False)),
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


def add_from_image(
    db: Session, owner: User, image_bytes: bytes, mime_type: str = "image/jpeg",
    added_from: str = "web", added_by: str = "", source_url: str = "",
) -> tuple[Recipe, bool]:
    """Разбор фото → рецепт. Повторная загрузка того же файла дубля не создаёт
    (source_key = хэш байт). Опциональный source_url — ссылка, которую
    пользователь вбил вручную (видео, reel, статья) — сохраняем, чтобы можно
    было вернуться к оригиналу."""
    parsed = parse_image(image_bytes, mime_type)
    existing = db.scalar(select(Recipe).where(
        Recipe.owner_id == owner.id, Recipe.source_key == parsed.source_key))
    if existing:
        return existing, False
    if source_url := source_url.strip():
        parsed.source_url = normalize_url(source_url)
        parsed.source_domain = domain_of(source_url)
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


# ------------------------------------------------------------- события / статистика

_PAGE_VIEW_THROTTLE_SEC = 30
_last_page_view: dict[str, float] = {}  # ключ: "u:<id>" или "a:<ip>"


def log_page_view(db: Session, user_id: int | None, ip: str, path: str) -> None:
    """Пишет заход в events, если такой же ключ давно не писали (30 сек).

    Троттлинг in-memory: перезапуск процесса обнуляет — не страшно,
    в худшем случае получим одну лишнюю запись через 30 сек после рестарта.
    """
    key = f"u:{user_id}" if user_id else f"a:{ip}"
    now = time.monotonic()
    if now - _last_page_view.get(key, 0.0) < _PAGE_VIEW_THROTTLE_SEC:
        return
    _last_page_view[key] = now
    db.add(Event(user_id=user_id, path=path[:200]))
    db.commit()


def admin_summary(db: Session) -> dict:
    """Сводка для /admin: все счётчики одним махом."""
    users_total = db.scalar(select(func.count()).select_from(User)) or 0
    recipes_total = db.scalar(select(func.count()).select_from(Recipe)) or 0

    by_source = dict(db.execute(
        select(Recipe.added_from, func.count())
        .group_by(Recipe.added_from)
    ).all())

    # url vs фото — фото-рецепты идентифицируем по source_key ("photo:<hash>")
    by_photo = db.scalar(
        select(func.count()).select_from(Recipe)
        .where(Recipe.source_key.like("photo:%"))
    ) or 0
    by_link = recipes_total - by_photo

    return {
        "users_total": users_total,
        "recipes_total": recipes_total,
        "recipes_web": int(by_source.get("web", 0)),
        "recipes_telegram": int(by_source.get("telegram", 0)),
        "recipes_by_link": by_link,
        "recipes_by_photo": by_photo,
    }


def admin_users(db: Session) -> list[dict]:
    """Юзеры с числом рецептов и датой последнего захода."""
    recipes_sub = (
        select(Recipe.owner_id, func.count().label("n"))
        .group_by(Recipe.owner_id).subquery())
    last_seen_sub = (
        select(Event.user_id, func.max(Event.ts).label("last_ts"))
        .where(Event.user_id.isnot(None))
        .group_by(Event.user_id).subquery())
    rows = db.execute(
        select(
            User, func.coalesce(recipes_sub.c.n, 0), last_seen_sub.c.last_ts)
        .outerjoin(recipes_sub, recipes_sub.c.owner_id == User.id)
        .outerjoin(last_seen_sub, last_seen_sub.c.user_id == User.id)
        .order_by(User.created_at.desc())
    ).all()
    return [{"user": u, "recipes": int(n), "last_seen": ts} for u, n, ts in rows]


def admin_daily_visits(db: Session, days: int = 30) -> list[dict]:
    """Уникальные user_id и всего хитов по дням за последние N дней."""
    since = datetime.utcnow() - timedelta(days=days)
    day = func.date(Event.ts).label("day")
    rows = db.execute(
        select(day,
               func.count(func.distinct(Event.user_id)).label("uniq"),
               func.count().label("hits"))
        .where(Event.ts >= since)
        .group_by(day).order_by(day)
    ).all()
    return [{"day": str(d), "uniq": int(u), "hits": int(h)} for d, u, h in rows]


# --------------------------------------------------------------- обратная связь

def save_feedback(
    db: Session, user_id: int | None, text: str, attachment_filename: str = "",
) -> Feedback:
    fb = Feedback(user_id=user_id, text=text[:5000],
                  attachment_filename=attachment_filename[:200])
    db.add(fb)
    db.commit()
    return fb


def list_feedback(db: Session, limit: int = 50) -> list[Feedback]:
    return list(db.scalars(
        select(Feedback).order_by(Feedback.created_at.desc()).limit(limit)))


def mark_feedback_read(db: Session, feedback_id: int) -> None:
    fb = db.get(Feedback, feedback_id)
    if fb:
        fb.is_read = True
        db.commit()


def admin_recipients(db: Session) -> list[int]:
    """tg_user_id всех админов, у которых Telegram привязан — им шлём уведомления."""
    rows = db.execute(
        select(User.tg_user_id).where(
            User.is_admin.is_(True), User.tg_user_id.is_not(None))
    ).all()
    return [int(r[0]) for r in rows if r[0]]
