from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import auth, service
from app.auth import COOKIE_MAX_AGE, COOKIE_NAME, current_user, optional_user
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
    resp.set_cookie(COOKIE_NAME, auth.make_session_cookie(user),
                    max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
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
    resp.set_cookie(COOKIE_NAME, auth.make_session_cookie(user),
                    max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ---------------------------------------------------------------- профиль

@app.get("/profile", response_class=HTMLResponse)
def profile(
    request: Request,
    user: User = Depends(current_user),
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
    user: User = Depends(current_user),
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
    user: User = Depends(current_user), db: Session = Depends(get_session),
):
    auth.regenerate_link_code(db, user)
    return RedirectResponse("/profile", status_code=303)


@app.post("/profile/telegram/unlink")
def profile_unlink_tg(
    user: User = Depends(current_user), db: Session = Depends(get_session),
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
    user: User = Depends(current_user),
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
    user: User = Depends(current_user), db: Session = Depends(get_session),
):
    recipe = service.get_by_slug(db, user.id, slug)
    if not recipe:
        raise HTTPException(404)
    return _render(request, "recipe.html", {"recipe": recipe}, user=user)


# ---------------------------------------------------------------- добавление

@app.get("/add", response_class=HTMLResponse)
def add_form(
    request: Request, url: str = Query(""),
    user: User = Depends(current_user),
):
    return _render(request, "add.html", {"prefill": url, "autostart": bool(url)},
                   user=user)


@app.get("/share", response_class=HTMLResponse)
def share_target(
    request: Request,
    url: str = Query(""), text: str = Query(""), title: str = Query(""),
    user: User = Depends(current_user),
):
    """Приём из системного «Поделиться» на Android (PWA share target)."""
    link = url.strip() or find_url(text) or find_url(title) or ""
    return _render(request, "add.html",
                   {"prefill": link, "autostart": bool(link)}, user=user)


@app.post("/api/add")
def api_add(
    request: Request, payload: dict,
    user: User = Depends(current_user), db: Session = Depends(get_session),
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
    user: User = Depends(current_user), db: Session = Depends(get_session),
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


# ---------------------------------------------------------------- правки

@app.post("/r/{slug}/category")
def change_category(
    slug: str, category: str = Form(...),
    user: User = Depends(current_user), db: Session = Depends(get_session),
):
    recipe = service.get_by_slug(db, user.id, slug)
    if not recipe or category not in CATEGORY_BY_SLUG:
        raise HTTPException(404)
    service.set_category(db, recipe, category)
    return RedirectResponse(f"/r/{slug}", status_code=303)


@app.post("/r/{slug}/favorite")
def favorite(
    slug: str,
    user: User = Depends(current_user), db: Session = Depends(get_session),
):
    recipe = service.get_by_slug(db, user.id, slug)
    if not recipe:
        raise HTTPException(404)
    service.toggle_favorite(db, recipe)
    return RedirectResponse(f"/r/{slug}", status_code=303)


@app.post("/r/{slug}/delete")
def delete(
    slug: str,
    user: User = Depends(current_user), db: Session = Depends(get_session),
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
const CACHE = 'samobranka-v2';
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET') return;
  if (url.pathname.startsWith('/media/') || url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(CACHE).then(cache =>
        cache.match(event.request).then(hit =>
          hit || fetch(event.request).then(resp => { cache.put(event.request, resp.clone()); return resp; })
        )
      )
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
