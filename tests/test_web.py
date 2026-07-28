"""Проверка сайта целиком: страницы отдаются, рецепты сохраняются, картинки жмутся.

Запуск: python -m tests.test_web
Сеть не нужна: страницы берутся из fixtures, картинка рисуется на месте.
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP = Path(tempfile.mkdtemp(prefix="samobranka-test-"))
os.environ["DATA_DIR"] = str(TMP)
os.environ["MEDIA_DIR"] = str(TMP / "media")
os.environ["DATABASE_URL"] = f"sqlite:///{TMP / 'test.db'}"
os.environ["SECRET_KEY"] = "test-secret"

from fastapi.testclient import TestClient    # noqa: E402
from PIL import Image as PILImage            # noqa: E402

from app import auth, service                # noqa: E402
from app.db import SessionLocal, init_db     # noqa: E402
from app.main import app                     # noqa: E402
from app.parser.images import process_image_bytes  # noqa: E402
from app.parser.pipeline import parse_html   # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
failures: list[str] = []

TEST_EMAIL = "olya@example.com"
TEST_PASSWORD = "hello123"


def check(condition: bool, label: str, got=None) -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}" + (f"  -> {got!r}" if got is not None else ""))
        failures.append(label)


def make_photo(w=2400, h=1600) -> bytes:
    import random
    random.seed(7)
    small = PILImage.new("RGB", (w // 12, h // 12))
    px = small.load()
    for y in range(small.height):
        for x in range(small.width):
            base = (int(200 - x * 60 / small.width), int(150 + y * 60 / small.height), 90)
            px[x, y] = tuple(max(0, min(255, c + random.randint(-22, 22))) for c in base)
    img = small.resize((w, h), PILImage.BICUBIC)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def test_images():
    print("\n[1] Сжатие картинок")
    raw = make_photo()
    info = process_image_bytes(raw, source_url="https://example.ru/photo.jpg")
    check(info is not None, "картинка обработана")
    if not info:
        return
    media = Path(os.environ["MEDIA_DIR"])
    main_size = (media / info["filename"]).stat().st_size
    thumb_size = (media / info["thumb_filename"]).stat().st_size
    ratio = len(raw) / main_size
    print(f"       оригинал {len(raw)/1024:.0f} КБ -> основная {main_size/1024:.0f} КБ "
          f"+ миниатюра {thumb_size/1024:.0f} КБ (в {ratio:.1f} раза меньше)")
    check(info["width"] == 1280, "ширина приведена к 1280", info["width"])
    check(main_size < len(raw) / 3, "основная версия минимум втрое легче оригинала")
    check(thumb_size < main_size, "миниатюра легче основной")
    check((media / info["thumb_filename"]).exists(), "миниатюра сохранена")

    again = process_image_bytes(raw, source_url="https://example.ru/photo.jpg")
    check(again["filename"] == info["filename"], "одинаковые картинки не дублируются")

    tiny = io.BytesIO()
    PILImage.new("RGB", (60, 60), (10, 20, 30)).save(tiny, "PNG")
    check(process_image_bytes(tiny.getvalue()) is None, "иконки и логотипы отсеиваются")


def seed():
    """Кладём в базу три рецепта из фикстур для тестового пользователя."""
    init_db()
    db = SessionLocal()
    user = auth.get_by_email(db, TEST_EMAIL)
    if user is None:
        user = auth.create_user(db, TEST_EMAIL, TEST_PASSWORD, display_name="Оля")
    photo = process_image_bytes(make_photo(1600, 1100), "https://example.ru/seed.jpg")
    urls = {
        "jsonld_borsch.html": "https://example.ru/recipes/borsch",
        "microdata_salad.html": "https://example.ru/salat-cezar/",
        "plain_pie.html": "https://blog.example.ru/sharlotka",
    }
    for name, url in urls.items():
        parsed = parse_html((FIXTURES / name).read_text(encoding="utf-8"), url, use_llm=False)
        parsed.images = [photo] if photo else []
        service.save_parsed(db, user, parsed, added_from="web", added_by="тест")
    db.close()
    return user


def _authed_client(email: str = TEST_EMAIL, password: str = TEST_PASSWORD) -> TestClient:
    client = TestClient(app)
    r = client.post("/login", data={"email": email, "password": password},
                    follow_redirects=False)
    assert r.status_code in (303, 302), f"login failed: {r.status_code} {r.text[:200]}"
    return client


def test_auth():
    print("\n[2] Регистрация и вход")
    client = TestClient(app)

    r = client.get("/", follow_redirects=False)
    check(r.status_code in (302, 303), "неавторизованного шлём на /login", r.status_code)

    r = client.post("/register",
                    data={"email": "bob@example.com", "password": "secret1", "display_name": "Боб"},
                    follow_redirects=False)
    check(r.status_code in (302, 303), "регистрация прошла", r.status_code)
    check(any(c for c in r.headers.get("set-cookie", "").split(";") if "sb_session" in c),
          "после регистрации выдана сессия-cookie")

    r = client.post("/register",
                    data={"email": "bob@example.com", "password": "secret1"},
                    follow_redirects=False)
    check(r.status_code == 400, "повторная регистрация с тем же email — 400", r.status_code)

    r = client.post("/register", data={"email": "junk", "password": "secret1"},
                    follow_redirects=False)
    check(r.status_code == 400, "невалидный email — 400", r.status_code)

    r = client.post("/register",
                    data={"email": "short@example.com", "password": "abc"},
                    follow_redirects=False)
    check(r.status_code == 400, "короткий пароль — 400", r.status_code)

    logged_out = TestClient(app)
    r = logged_out.post("/login",
                        data={"email": "bob@example.com", "password": "wrong"},
                        follow_redirects=False)
    check(r.status_code == 401, "неправильный пароль — 401", r.status_code)

    r = logged_out.post("/login",
                        data={"email": "bob@example.com", "password": "secret1"},
                        follow_redirects=False)
    check(r.status_code in (302, 303), "правильный пароль — редирект на /", r.status_code)


def test_isolation():
    print("\n[3] Изоляция книг разных пользователей")
    seed()  # Оля с борщом
    client_olya = _authed_client()

    r = client_olya.get("/")
    check(r.status_code == 200 and "Борщ украинский" in r.text,
          "Оля видит свой борщ")

    client_bob = TestClient(app)
    client_bob.post("/register",
                    data={"email": "eve@example.com", "password": "another1", "display_name": "Ева"},
                    follow_redirects=False)
    r = client_bob.get("/")
    check("Борщ украинский" not in r.text, "Ева НЕ видит рецепты Оли")
    check("Книга пока пустая" in r.text or "empty" in r.text.lower()
          or "Добавьте первый рецепт" in r.text,
          "Ева видит пустую книгу")

    r = client_bob.get("/r/borsch-ukrainskiy-s-pampushkami", follow_redirects=False)
    check(r.status_code in (303, 302, 404), "чужой рецепт по slug недоступен", r.status_code)


def test_pages():
    print("\n[4] Страницы сайта под аккаунтом")
    client = _authed_client()

    r = client.get("/")
    check(r.status_code == 200, "главная отвечает", r.status_code)
    check("Борщ украинский" in r.text, "борщ есть в библиотеке")
    check("Шарлотка с яблоками" in r.text, "шарлотка есть в библиотеке")
    check("Супы" in r.text and "Выпечка" in r.text, "полки категорий показаны")

    r = client.get("/?category=soups")
    check("Борщ украинский" in r.text, "фильтр по супам показывает борщ")
    check("Салат Цезарь" not in r.text, "фильтр по супам не показывает салат")

    r = client.get("/?q=яблоки")
    check("Шарлотка" in r.text, "поиск по продукту находит рецепт")

    r = client.get("/?q=пармезан")
    check("Цезарь" in r.text, "поиск ищет и по составу, не только по названию")

    r = client.get("/r/borsch-ukrainskiy-s-pampushkami")
    check(r.status_code == 200, "страница рецепта отвечает", r.status_code)
    check("Говядина на кости" in r.text, "состав выведен")
    check("600 г" in r.text, "количества выведены")
    check("Бульон" in r.text, "раздел шагов виден")
    check("example.ru/recipes/borsch" in r.text, "ссылка на источник сохранена")
    check("2 ч 15 мин" in r.text, "время приготовления показано")

    r = client.get("/add")
    check(r.status_code == 200 and "Разобрать" in r.text, "страница добавления открывается")

    r = client.get("/share?text=Смотри%20рецепт%20https://example.ru/recipes/borsch")
    check("https://example.ru/recipes/borsch" in r.text,
          "share target вытащил ссылку из поля text")

    r = client.get("/profile")
    check(r.status_code == 200 and TEST_EMAIL in r.text, "профиль показывает email пользователя")

    r = client.get("/manifest.webmanifest")
    data = r.json()
    check(data.get("share_target", {}).get("action") == "/share",
          "в манифесте есть share_target для Android")

    r = client.get("/healthz")
    check(r.json()["recipes"] >= 3, "в базе минимум три рецепта", r.json())
    check(r.json()["users"] >= 1, "в базе есть пользователи", r.json())

    r = client.get("/r/net-takogo")
    check(r.status_code == 404, "несуществующий рецепт -> 404", r.status_code)


def test_dedupe_and_edit():
    print("\n[5] Дубли и правки")
    db = SessionLocal()
    user = auth.get_by_email(db, TEST_EMAIL)
    before = service.total_count(db, user.id)
    parsed = parse_html((FIXTURES / "jsonld_borsch.html").read_text(encoding="utf-8"),
                        "https://example.ru/recipes/borsch?utm_source=vk", use_llm=False)
    existing = service.find_by_source(db, user.id, parsed.source_url)
    check(existing is not None, "та же ссылка с другим utm распознана как дубль")
    check(service.total_count(db, user.id) == before, "лишний рецепт не создался")

    recipe = service.get_by_slug(db, user.id, "sharlotka-s-yablokami")
    service.set_category(db, recipe, "desserts")
    check(recipe.category == "desserts" and recipe.category_locked,
          "категорию можно поправить руками, и она больше не перебивается")

    client = _authed_client()
    r = client.get("/?category=desserts")
    check("Шарлотка" in r.text, "рецепт переехал на новую полку")
    db.close()


def test_telegram_link():
    print("\n[6] Привязка Telegram")
    db = SessionLocal()
    user = auth.get_by_email(db, TEST_EMAIL)
    code = user.tg_link_code
    check(bool(code) and len(code) == 8, "у пользователя выдан 8-символьный код", code)

    linked = auth.link_telegram(db, code, tg_user_id=123456)
    check(linked is not None and linked.id == user.id, "привязка по коду сработала")
    check(auth.get_by_tg(db, 123456) is not None, "поиск по tg_user_id находит")
    check(linked.tg_link_code != code, "после привязки код сбрасывается")

    other = auth.link_telegram(db, "WRONGCOD", tg_user_id=999999)
    check(other is None, "неверный код возвращает None")
    db.close()


if __name__ == "__main__":
    try:
        init_db()  # TestClient не запускает FastAPI-lifespan
        test_images()
        test_auth()
        test_isolation()
        test_pages()
        test_dedupe_and_edit()
        test_telegram_link()
        print("\n" + "=" * 56)
        if failures:
            print(f"Провалено проверок: {len(failures)}")
            for f in failures:
                print("  -", f)
            sys.exit(1)
        print("Все проверки пройдены.")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
