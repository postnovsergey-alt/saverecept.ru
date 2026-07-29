from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import auth, service
from app.auth import (
    COOKIE_MAX_AGE, COOKIE_NAME,
    PIN_COOKIE_MAX_AGE, PIN_COOKIE_NAME,
    current_user, current_user_unlocked, optional_user,
)
from app.categories import CATEGORIES, CATEGORY_BY_SLUG, category_color, category_title
from app.config import MEDIA_DIR, PUBLIC_BASE_URL, TELEGRAM_BOT_TOKEN
from app.db import get_session, init_db
from app.models import User
from app.parser.ingredients import format_amount
from app.parser.pipeline import ParseError
from app.utils import find_url, shorten

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("samobranka")

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Самобранка", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")

templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals.update(
    CATEGORIES=CATEGORIES,
    category_title=category_title,
    category_color=category_color,
    format_amount=format_amount,
    shorten=shorten,
    bot_enabled=bool(TELEGRAM_BOT_TOKEN),
)


@app.on_event("startup")
def _startup():
    init_db()
    log.info("Самобранка запущена. Публичный адрес: %s", PUBLIC_BASE_URL)


@app.middleware("http")
async def _sliding_session(request: Request, call_next):
    """Продлеваем cookie сессии и PIN на каждом визите — «активные» не разлогиниваются.

    Не трогаем cookie, если сам обработчик уже что-то с ней сделал (например,
    /logout удаляет её, /login выпускает новую).
    """
    response = await call_next(request)

    def already_touched(name: str) -> bool:
        prefix = f"{name}=".encode("ascii")
        for header_name, header_value in response.raw_headers:
            if header_name.lower() == b"set-cookie" and header_value.startswith(prefix):
                return True
        return False

    session_raw = request.cookies.get(COOKIE_NAME)
    if session_raw and not already_touched(COOKIE_NAME):
        try:
            auth._signer.loads(session_raw)  # noqa: SLF001 — валидация подписи
        except Exception:  # noqa: BLE001 — битую куку не продлеваем
            pass
        else:
            response.set_cookie(
                COOKIE_NAME, session_raw,
                max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax",
            )

    pin_raw = request.cookies.get(PIN_COOKIE_NAME)
    if pin_raw and not already_touched(PIN_COOKIE_NAME):
        try:
            auth._pin_signer.loads(pin_raw)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass
        else:
            response.set_cookie(
                PIN_COOKIE_NAME, pin_raw,
                max_age=PIN_COOKIE_MAX_AGE, httponly=True, samesite="lax",
            )

    return response


@app.exception_handler(HTTPException)
async def _redirect_on_auth(request: Request, exc: HTTPException):
    if exc.status_code == 307 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    if exc.status_code == 404:
        return templates.TemplateResponse(
            request, "error.html",
            {"message": "Такой страницы нет", "current_user": None},
            status_code=404,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


def _render(request: Request, template: str, ctx: dict, status_code: int = 200,
            user: User | None = None) -> HTMLResponse:
    ctx = {"current_user": user, **ctx}
    return templates.TemplateResponse(request, template, ctx, status_code=status_code)


# ---------------------------------------------------------------- регистрация / вход

@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request, db: Session = Depends(get_session)):
    if optional_user(request, db):
        return RedirectResponse("/", status_code=303)
    return _render(request, "register.html", {"error": "", "email": ""})


def _issue_session(response, user, unlock: bool = True) -> None:
    """Ставим сессионную cookie и (по желанию) сразу PIN-cookie — чтобы после
    свежего логина не заставлять сразу вводить PIN."""
    response.set_cookie(
        COOKIE_NAME, auth.make_session_cookie(user),
        max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax",
    )
    if unlock:
        response.set_cookie(
            PIN_COOKIE_NAME, auth.make_pin_cookie(user),
            max_age=PIN_COOKIE_MAX_AGE, httponly=True, samesite="lax",
        )


@app.post("/register", response_class=HTMLResponse)
def register(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    display_name: str = Form(""),
    db: Session = Depends(get_session),
):
    try:
        user = auth.create_user(db, email, password, display_name)
    except ValueError as e:
        return _render(request, "register.html",
                       {"error": str(e), "email": email}, status_code=400)
    resp = RedirectResponse("/", status_code=303)
    _issue_session(resp, user)
    return resp


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, db: Session = Depends(get_session)):
    if optional_user(request, db):
        return RedirectResponse("/", status_code=303)
    return _render(request, "login.html", {"error": "", "email": ""})


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_session),
):
    user = auth.authenticate(db, email, password)
    if not user:
        return _render(request, "login.html",
                       {"error": "Не подходит email или пароль", "email": email},
                       status_code=401)
    resp = RedirectResponse("/", status_code=303)
    _issue_session(resp, user)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    resp.delete_cookie(PIN_COOKIE_NAME)
    return resp


# ---------------------------------------------------------------- PIN-код

def _safe_next(raw: str) -> str:
    """Только внутренние пути — чтобы /unlock?next=... не был open-redirect."""
    raw = (raw or "").strip()
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/"


@app.get("/unlock", response_class=HTMLResponse)
def unlock_form(
    request: Request, next: str = Query("/"),
    user: User = Depends(current_user),
):
    if not user.pin_hash or auth.pin_ok(request, user):
        return RedirectResponse(_safe_next(next), status_code=303)
    return _render(request, "unlock.html",
                   {"error": "", "next": _safe_next(next)}, user=user)


@app.post("/unlock", response_class=HTMLResponse)
def unlock_submit(
    request: Request,
    pin: str = Form(""), next: str = Form("/"),
    user: User = Depends(current_user),
):
    dest = _safe_next(next)
    if not user.pin_hash:
        return RedirectResponse(dest, status_code=303)
    if not auth.verify_pin(pin, user.pin_hash):
        return _render(request, "unlock.html",
                       {"error": "PIN не подошёл", "next": dest},
                       status_code=401, user=user)
    resp = RedirectResponse(dest, status_code=303)
    resp.set_cookie(
        PIN_COOKIE_NAME, auth.make_pin_cookie(user),
        max_age=PIN_COOKIE_MAX_AGE, httponly=True, samesite="lax",
    )
    return resp


def _profile_ctx(db: Session, user: User, error: str = "", notice: str = "") -> dict:
    return {
        "total": service.total_count(db, user.id),
        "bot_enabled": bool(TELEGRAM_BOT_TOKEN),
        "error": error, "notice": notice,
    }


@app.post("/profile/pin", response_class=HTMLResponse)
def profile_set_pin(
    request: Request,
    current_password: str = Form(""),
    new_pin: str = Form(""),
    confirm_pin: str = Form(""),
    user: User = Depends(current_user_unlocked),
    db: Session = Depends(get_session),
):
    if not auth.verify_password(current_password, user.password_hash):
        return _render(request, "profile.html",
                       _profile_ctx(db, user, error="Текущий пароль не подошёл"),
                       status_code=400, user=user)
    if new_pin != confirm_pin:
        return _render(request, "profile.html",
                       _profile_ctx(db, user, error="PIN и подтверждение не совпадают"),
                       status_code=400, user=user)
    try:
        auth.set_pin(db, user, new_pin)
    except ValueError as e:
        return _render(request, "profile.html",
                       _profile_ctx(db, user, error=str(e)),
                       status_code=400, user=user)
    resp = _render(request, "profile.html",
                   _profile_ctx(db, user, notice="PIN сохранён"), user=user)
    resp.set_cookie(
        PIN_COOKIE_NAME, auth.make_pin_cookie(user),
        max_age=PIN_COOKIE_MAX_AGE, httponly=True, samesite="lax",
    )
    return resp


@app.post("/profile/pin/remove", response_class=HTMLResponse)
def profile_remove_pin(
    request: Request,
    current_password: str = Form(""),
    user: User = Depends(current_user_unlocked),
    db: Session = Depends(get_session),
):
    if not auth.verify_password(current_password, user.password_hash):
        return _render(request, "profile.html",
                       _profile_ctx(db, user, error="Текущий пароль не подошёл"),
                       status_code=400, user=user)
    auth.remove_pin(db, user)
    resp = _render(request, "profile.html",
                   _profile_ctx(db, user, notice="PIN снят"), user=user)
    resp.delete_cookie(PIN_COOKIE_NAME)
    return resp


# ---------------------------------------------------------------- профиль

@app.get("/profile", response_class=HTMLResponse)
def profile(
    request: Request,
    user: User = Depends(current_user_unlocked),
    db: Session = Depends(get_session),
):
    return _render(request, "profile.html", {
        "total": service.total_count(db, user.id),
        "bot_enabled": bool(TELEGRAM_BOT_TOKEN),
        "error": "",
        "notice": "",
    }, user=user)


@app.post("/profile/password", response_class=HTMLResponse)
def profile_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    user: User = Depends(current_user_unlocked),
    db: Session = Depends(get_session),
):
    if not auth.verify_password(current_password, user.password_hash):
        return _render(request, "profile.html", {
            "total": service.total_count(db, user.id),
            "bot_enabled": bool(TELEGRAM_BOT_TOKEN),
            "error": "Текущий пароль не подошёл", "notice": "",
        }, status_code=400, user=user)
    try:
        auth.set_password(db, user, new_password)
    except ValueError as e:
        return _render(request, "profile.html", {
            "total": service.total_count(db, user.id),
            "bot_enabled": bool(TELEGRAM_BOT_TOKEN),
            "error": str(e), "notice": "",
        }, status_code=400, user=user)
    return _render(request, "profile.html", {
        "total": service.total_count(db, user.id),
        "bot_enabled": bool(TELEGRAM_BOT_TOKEN),
        "error": "", "notice": "Пароль обновлён",
    }, user=user)


@app.post("/profile/telegram/regenerate")
def profile_regenerate_code(
    user: User = Depends(current_user_unlocked), db: Session = Depends(get_session),
):
    auth.regenerate_link_code(db, user)
    return RedirectResponse("/profile", status_code=303)


@app.post("/profile/telegram/unlink")
def profile_unlink_tg(
    user: User = Depends(current_user_unlocked), db: Session = Depends(get_session),
):
    auth.unlink_telegram(db, user)
    return RedirectResponse("/profile", status_code=303)


# ---------------------------------------------------------------- библиотека

@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    q: str = Query("", max_length=120),
    category: str = Query("", max_length=40),
    fav: int = Query(0),
    user: User = Depends(current_user_unlocked),
    db: Session = Depends(get_session),
):
    category = category if category in CATEGORY_BY_SLUG else ""
    recipes = service.list_recipes(
        db, owner_id=user.id, category=category or None,
        q=q.strip() or None, favorites_only=bool(fav), limit=120)
    return _render(request, "index.html", {
        "recipes": recipes,
        "counts": service.count_by_category(db, user.id),
        "total": service.total_count(db, user.id),
        "active_category": category,
        "q": q,
        "fav": bool(fav),
    }, user=user)


@app.get("/r/{slug}", response_class=HTMLResponse)
def recipe_page(
    request: Request, slug: str,
    user: User = Depends(current_user_unlocked), db: Session = Depends(get_session),
):
    recipe = service.get_by_slug(db, user.id, slug)
    if not recipe:
        raise HTTPException(404)
    return _render(request, "recipe.html", {"recipe": recipe}, user=user)


# ---------------------------------------------------------------- добавление

@app.get("/add", response_class=HTMLResponse)
def add_form(
    request: Request, url: str = Query(""),
    user: User = Depends(current_user_unlocked),
):
    return _render(request, "add.html", {"prefill": url, "autostart": bool(url)},
                   user=user)


@app.get("/share", response_class=HTMLResponse)
def share_target(
    request: Request,
    url: str = Query(""), text: str = Query(""), title: str = Query(""),
    user: User = Depends(current_user_unlocked),
):
    """Приём из системного «Поделиться» на Android (PWA share target)."""
    link = url.strip() or find_url(text) or find_url(title) or ""
    return _render(request, "add.html",
                   {"prefill": link, "autostart": bool(link)}, user=user)


@app.post("/api/add")
def api_add(
    request: Request, payload: dict,
    user: User = Depends(current_user_unlocked), db: Session = Depends(get_session),
):
    url = (payload.get("url") or "").strip()
    if not url:
        return JSONResponse({"ok": False, "error": "Пустая ссылка"}, status_code=400)
    try:
        recipe, created = service.add_from_url(
            db, user, url, added_from="web", added_by=user.display_name or user.email)
    except ParseError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=422)
    except Exception as e:  # noqa: BLE001
        log.exception("Не смогли добавить %s", url)
        return JSONResponse({"ok": False, "error": f"Внутренняя ошибка: {e}"}, status_code=500)
    return {
        "ok": True, "created": created, "slug": recipe.slug, "title": recipe.title,
        "category": category_title(recipe.category), "url": f"/r/{recipe.slug}",
    }


@app.post("/add")
def add_submit(
    request: Request, url: str = Form(""),
    user: User = Depends(current_user_unlocked), db: Session = Depends(get_session),
):
    """Запасной путь без JavaScript."""
    try:
        recipe, _created = service.add_from_url(
            db, user, url.strip(), added_from="web",
            added_by=user.display_name or user.email)
    except ParseError as e:
        return _render(request, "add.html",
                       {"prefill": url, "autostart": False, "error": str(e)},
                       status_code=422, user=user)
    return RedirectResponse(f"/r/{recipe.slug}", status_code=303)


MAX_PHOTO_BYTES = 20_000_000


@app.post("/api/add_photo")
async def api_add_photo(
    photo: UploadFile = File(...),
    user: User = Depends(current_user_unlocked), db: Session = Depends(get_session),
):
    data = await photo.read()
    if not data:
        return JSONResponse({"ok": False, "error": "Файл пустой"}, status_code=400)
    if len(data) > MAX_PHOTO_BYTES:
        return JSONResponse({"ok": False, "error": "Фото больше 20 МБ"}, status_code=413)
    mime = photo.content_type or "image/jpeg"
    try:
        recipe, created = await run_in_threadpool(
            service.add_from_image, db, user, data, mime,
            "web", user.display_name or user.email,
        )
    except ParseError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=422)
    except Exception as e:  # noqa: BLE001
        log.exception("Не смогли разобрать фото")
        return JSONResponse({"ok": False, "error": f"Внутренняя ошибка: {e}"},
                            status_code=500)
    return {
        "ok": True, "created": created, "slug": recipe.slug, "title": recipe.title,
        "category": category_title(recipe.category), "url": f"/r/{recipe.slug}",
    }


# ---------------------------------------------------------------- правки

@app.post("/r/{slug}/category")
def change_category(
    slug: str, category: str = Form(...),
    user: User = Depends(current_user_unlocked), db: Session = Depends(get_session),
):
    recipe = service.get_by_slug(db, user.id, slug)
    if not recipe or category not in CATEGORY_BY_SLUG:
        raise HTTPException(404)
    service.set_category(db, recipe, category)
    return RedirectResponse(f"/r/{slug}", status_code=303)


@app.post("/r/{slug}/favorite")
def favorite(
    slug: str,
    user: User = Depends(current_user_unlocked), db: Session = Depends(get_session),
):
    recipe = service.get_by_slug(db, user.id, slug)
    if not recipe:
        raise HTTPException(404)
    service.toggle_favorite(db, recipe)
    return RedirectResponse(f"/r/{slug}", status_code=303)


@app.post("/api/r/{slug}/favorite")
def api_favorite(
    slug: str,
    user: User = Depends(current_user_unlocked), db: Session = Depends(get_session),
):
    """AJAX-тумблер для карточек в списке — без перезагрузки страницы."""
    recipe = service.get_by_slug(db, user.id, slug)
    if not recipe:
        return JSONResponse({"ok": False, "error": "Рецепт не найден"}, status_code=404)
    is_favorite = service.toggle_favorite(db, recipe)
    return {"ok": True, "is_favorite": is_favorite}


@app.post("/r/{slug}/delete")
def delete(
    slug: str,
    user: User = Depends(current_user_unlocked), db: Session = Depends(get_session),
):
    recipe = service.get_by_slug(db, user.id, slug)
    if not recipe:
        raise HTTPException(404)
    service.delete_recipe(db, recipe)
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------- PWA

@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse({
        "name": "Самобранка",
        "short_name": "Самобранка",
        "description": "Личная книга рецептов",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#100e0c",
        "theme_color": "#100e0c",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
        "share_target": {
            "action": "/share",
            "method": "GET",
            "params": {"title": "title", "text": "text", "url": "url"},
        },
    }, media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    js = """
const CACHE = 'samobranka-v3';
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(
  caches.keys().then(names => Promise.all(
    names.filter(n => n !== CACHE).map(n => caches.delete(n))
  )).then(() => self.clients.claim())
));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET') return;
  // /media/ — cache-first (имя = хэш, старой версии не бывает)
  if (url.pathname.startsWith('/media/')) {
    event.respondWith(
      caches.open(CACHE).then(cache =>
        cache.match(event.request).then(hit =>
          hit || fetch(event.request).then(resp => { cache.put(event.request, resp.clone()); return resp; })
        )
      )
    );
    return;
  }
  // /static/ — network-first, чтобы правки CSS/иконок доезжали сразу
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      fetch(event.request)
        .then(resp => { caches.open(CACHE).then(c => c.put(event.request, resp.clone())); return resp; })
        .catch(() => caches.match(event.request))
    );
  }
});
"""
    return Response(js, media_type="application/javascript")


@app.get("/healthz")
def healthz(db: Session = Depends(get_session)):
    from sqlalchemy import func, select
    from app.models import Recipe, User
    total_recipes = db.scalar(select(func.count()).select_from(Recipe)) or 0
    total_users = db.scalar(select(func.count()).select_from(User)) or 0
    return {"ok": True, "recipes": total_recipes, "users": total_users,
            "public_url": PUBLIC_BASE_URL}
