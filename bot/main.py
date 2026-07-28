"""Telegram-бот Самобранки.

Каждое сообщение в бот связывается с аккаунтом на сайте по tg_user_id.
Пока связи нет — бот просит /start <код>, код виден в профиле на сайте.
После привязки рецепты из бота падают в книгу этого пользователя.

Запуск:  python -m bot.main
"""
from __future__ import annotations

import asyncio
import logging
import sys

from io import BytesIO

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from aiogram.client.default import DefaultBotProperties

from app import auth, service
from app.categories import category_title
from app.config import MEDIA_DIR, PUBLIC_BASE_URL, TELEGRAM_BOT_TOKEN
from app.db import SessionLocal, init_db
from app.models import User
from app.parser.pipeline import ParseError
from app.utils import find_url, shorten

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("samobranka.bot")

dp = Dispatcher()

HELLO_LINKED = (
    "Привет, {name}! Я складываю рецепты в вашу книгу.\n\n"
    "Перешлите мне ссылку на рецепт — с любого кулинарного сайта — "
    "или пришлите <b>фото</b> страницы книги/скриншот. "
    "Я вытащу состав, шаги и картинку и разложу по категориям.\n\n"
    "Ещё умею:\n"
    "/find творог — поиск по книге\n"
    "/last — что добавляли недавно\n"
    "/unlink — отвязать этот Telegram от аккаунта\n\n"
    "Сайт: {url}"
)

HELLO_UNLINKED = (
    "Привет! Прежде чем я начну наполнять вашу книгу, нужно подружить этот "
    "Telegram с аккаунтом на сайте.\n\n"
    "1. Откройте <a href=\"{url}/register\">{url}</a> и заведите аккаунт "
    "(либо войдите, если уже есть).\n"
    "2. На странице «Профиль» скопируйте <b>код привязки</b>.\n"
    "3. Пришлите мне: <code>/start КОД</code>\n\n"
    "Без привязки я не знаю, куда класть рецепты."
)


def site_link(slug: str) -> str:
    return f"{PUBLIC_BASE_URL}/r/{slug}"


def keyboard(slug: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Открыть на сайте", url=site_link(slug))
    ]])


def _resolve_user(tg_user_id: int) -> User | None:
    db = SessionLocal()
    try:
        return auth.get_by_tg(db, tg_user_id)
    finally:
        db.close()


def _link_by_code(code: str, tg_user_id: int) -> tuple[User | None, str | None]:
    db = SessionLocal()
    try:
        try:
            user = auth.link_telegram(db, code, tg_user_id)
        except ValueError as e:
            return None, str(e)
        return user, None
    finally:
        db.close()


def _unlink(tg_user_id: int) -> User | None:
    db = SessionLocal()
    try:
        user = auth.get_by_tg(db, tg_user_id)
        if not user:
            return None
        auth.unlink_telegram(db, user)
        return user
    finally:
        db.close()


def _add_sync(owner_id: int, url: str, added_by: str):
    db = SessionLocal()
    try:
        owner = db.get(User, owner_id)
        if owner is None:
            raise ParseError("Аккаунт не найден — привяжите Telegram заново (/start КОД).")
        return service.add_from_url(db, owner, url,
                                    added_from="telegram", added_by=added_by)
    finally:
        db.close()


def _add_photo_sync(owner_id: int, image_bytes: bytes, mime_type: str, added_by: str):
    db = SessionLocal()
    try:
        owner = db.get(User, owner_id)
        if owner is None:
            raise ParseError("Аккаунт не найден — привяжите Telegram заново (/start КОД).")
        return service.add_from_image(db, owner, image_bytes, mime_type,
                                      added_from="telegram", added_by=added_by)
    finally:
        db.close()


def _search_sync(owner_id: int, query: str):
    db = SessionLocal()
    try:
        return service.list_recipes(db, owner_id=owner_id, q=query, limit=8)
    finally:
        db.close()


def _last_sync(owner_id: int):
    db = SessionLocal()
    try:
        return service.list_recipes(db, owner_id=owner_id, limit=8)
    finally:
        db.close()


def who(message: Message) -> str:
    u = message.from_user
    if not u:
        return ""
    return (u.full_name or u.username or str(u.id))[:120]


async def _require_user(message: Message) -> User | None:
    if message.from_user is None:
        return None
    user = await asyncio.to_thread(_resolve_user, message.from_user.id)
    if user is None:
        await message.answer(
            HELLO_UNLINKED.format(url=PUBLIC_BASE_URL),
            disable_web_page_preview=True)
        return None
    return user


@dp.message(CommandStart())
async def start(message: Message):
    if message.from_user is None:
        return
    args = (message.text or "").partition(" ")[2].strip()
    if args:
        user, error = await asyncio.to_thread(_link_by_code, args, message.from_user.id)
        if error:
            return await message.answer(error)
        if not user:
            return await message.answer(
                "Такой код не подошёл. Загляните в профиль на сайте — "
                "там свежий код: " + PUBLIC_BASE_URL + "/profile")
        return await message.answer(
            f"Готово! Этот Telegram теперь привязан к <b>{user.email}</b>. "
            "Кидайте ссылки — сложу в вашу книгу.")

    user = await asyncio.to_thread(_resolve_user, message.from_user.id)
    if user:
        await message.answer(
            HELLO_LINKED.format(name=user.display_name or "друг", url=PUBLIC_BASE_URL),
            disable_web_page_preview=True)
    else:
        await message.answer(
            HELLO_UNLINKED.format(url=PUBLIC_BASE_URL),
            disable_web_page_preview=True)


@dp.message(Command("unlink"))
async def unlink(message: Message):
    if message.from_user is None:
        return
    user = await asyncio.to_thread(_unlink, message.from_user.id)
    if user:
        await message.answer(f"Готово — Telegram отвязан от {user.email}.")
    else:
        await message.answer("Этот Telegram и так ни к чему не привязан.")


@dp.message(Command("find"))
async def find(message: Message):
    user = await _require_user(message)
    if not user:
        return
    query = (message.text or "").partition(" ")[2].strip()
    if not query:
        return await message.answer("Напишите, что искать: <code>/find творог</code>")
    results = await asyncio.to_thread(_search_sync, user.id, query)
    if not results:
        return await message.answer(f"По запросу «{query}» ничего не нашлось.")
    lines = [f"Нашёл {len(results)}:"]
    for r in results:
        lines.append(f"• <a href=\"{site_link(r.slug)}\">{r.title}</a> — {category_title(r.category)}")
    await message.answer("\n".join(lines), disable_web_page_preview=True)


@dp.message(Command("last"))
async def last(message: Message):
    user = await _require_user(message)
    if not user:
        return
    results = await asyncio.to_thread(_last_sync, user.id)
    if not results:
        return await message.answer("Книга пока пустая. Пришлите первую ссылку!")
    lines = ["Последние рецепты:"]
    for r in results:
        lines.append(f"• <a href=\"{site_link(r.slug)}\">{r.title}</a> — {category_title(r.category)}")
    await message.answer("\n".join(lines), disable_web_page_preview=True)


async def _send_result(message: Message, status: Message, recipe, created: bool):
    head = "Готово!" if created else "Такой рецепт уже был:"
    caption_parts = [
        f"{head}\n<b>{recipe.title}</b>",
        f"Полка: {category_title(recipe.category)}",
    ]
    if recipe.time_label:
        caption_parts.append(f"Время: {recipe.time_label}")
    if recipe.ingredients:
        preview = ", ".join(i.name for i in recipe.ingredients[:6])
        caption_parts.append(f"Продукты: {shorten(preview, 180)}")
    caption = "\n".join(caption_parts)

    cover = recipe.cover
    if cover and (MEDIA_DIR / cover.filename).exists():
        await status.delete()
        await message.answer_photo(
            FSInputFile(MEDIA_DIR / cover.filename),
            caption=caption, reply_markup=keyboard(recipe.slug))
    else:
        await status.edit_text(caption, reply_markup=keyboard(recipe.slug),
                               disable_web_page_preview=True)


@dp.message(F.photo)
async def handle_photo(message: Message):
    user = await _require_user(message)
    if not user:
        return

    status = await message.answer("Читаю фото…")
    try:
        # берём самое крупное превью — самое чёткое для распознавания
        photo = message.photo[-1]
        buf = BytesIO()
        await message.bot.download(photo, destination=buf)
        image_bytes = buf.getvalue()
    except Exception as e:  # noqa: BLE001
        log.exception("Фото не скачалось")
        return await status.edit_text(f"Фото не загрузилось: {e}")

    try:
        recipe, created = await asyncio.to_thread(
            _add_photo_sync, user.id, image_bytes, "image/jpeg", who(message))
    except ParseError as e:
        return await status.edit_text(f"Не получилось: {e}")
    except Exception as e:  # noqa: BLE001
        log.exception("Ошибка при разборе фото")
        return await status.edit_text(f"Что-то сломалось: {e}")

    await _send_result(message, status, recipe, created)


@dp.message(F.text)
async def handle_link(message: Message):
    user = await _require_user(message)
    if not user:
        return
    url = find_url(message.text or "")
    if not url:
        return await message.answer(
            "Пришлите ссылку или фото рецепта — или /find, чтобы поискать в книге.")

    status = await message.answer("Читаю страницу…")
    try:
        recipe, created = await asyncio.to_thread(_add_sync, user.id, url, who(message))
    except ParseError as e:
        return await status.edit_text(f"Не получилось: {e}")
    except Exception as e:  # noqa: BLE001
        log.exception("Ошибка при добавлении %s", url)
        return await status.edit_text(f"Что-то сломалось: {e}")

    await _send_result(message, status, recipe, created)


async def main():
    if not TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN не задан — боту нечего запускать. "
                  "Получите токен у @BotFather и положите его в .env")
        sys.exit(1)
    init_db()
    bot = Bot(TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    log.info("Бот запущен. Сайт: %s", PUBLIC_BASE_URL)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
