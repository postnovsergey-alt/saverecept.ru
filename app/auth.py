"""Регистрация, вход, сессии и привязка Telegram.

Пароли хранятся в bcrypt-хэше (модуль `bcrypt`, без passlib — свежие версии
passlib ломаются на bcrypt 4.x). Сессия — подписанная кука с id пользователя.
Никаких внешних зависимостей вроде SMTP: чтобы завести аккаунт, достаточно
email и пароля.
"""
from __future__ import annotations

import secrets
from typing import Optional

import bcrypt
from email_validator import EmailNotValidError, validate_email
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import SECRET_KEY
from app.db import get_session
from app.models import User

COOKIE_NAME = "sb_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # год
_signer = URLSafeSerializer(SECRET_KEY, salt="samobranka-session")
_LINK_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # без похожих 0/O, 1/I


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def normalize_email(raw: str) -> str:
    """Валидация без обращения к DNS — иначе тесты не пройдут без сети."""
    v = validate_email(raw, check_deliverability=False)
    return v.normalized.lower()


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.lower()))


def get_by_tg(db: Session, tg_user_id: int) -> User | None:
    return db.scalar(select(User).where(User.tg_user_id == tg_user_id))


def new_link_code() -> str:
    return "".join(secrets.choice(_LINK_CODE_ALPHABET) for _ in range(8))


def create_user(db: Session, email: str, password: str, display_name: str = "") -> User:
    try:
        clean_email = normalize_email(email)
    except EmailNotValidError as e:
        raise ValueError(f"Неправильный email: {e}") from e
    if len(password) < 6:
        raise ValueError("Пароль должен быть не короче 6 символов")
    if get_by_email(db, clean_email):
        raise ValueError("Такой email уже зарегистрирован")

    user = User(
        email=clean_email,
        password_hash=hash_password(password),
        display_name=(display_name or clean_email.split("@", 1)[0])[:120],
        tg_link_code=new_link_code(),
    )
    db.add(user)
    db.commit()
    return user


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = get_by_email(db, email.strip().lower())
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def set_password(db: Session, user: User, new_password: str) -> None:
    if len(new_password) < 6:
        raise ValueError("Пароль должен быть не короче 6 символов")
    user.password_hash = hash_password(new_password)
    db.commit()


def link_telegram(db: Session, code: str, tg_user_id: int) -> User | None:
    """Привязать Telegram-аккаунт к пользователю по одноразовому коду.

    После привязки код сбрасывается — на новый нужно снова нажать кнопку
    в профиле. Один tg_user_id соответствует одному аккаунту (unique).
    """
    code = (code or "").strip().upper()
    if not code:
        return None
    user = db.scalar(select(User).where(User.tg_link_code == code))
    if not user:
        return None
    other = get_by_tg(db, tg_user_id)
    if other and other.id != user.id:
        raise ValueError(
            f"Этот Telegram уже привязан к аккаунту {other.email}. "
            "Отвяжите его в профиле того аккаунта или войдите под ним."
        )
    user.tg_user_id = tg_user_id
    user.tg_link_code = new_link_code()  # код одноразовый
    db.commit()
    return user


def unlink_telegram(db: Session, user: User) -> None:
    user.tg_user_id = None
    user.tg_link_code = new_link_code()
    db.commit()


def regenerate_link_code(db: Session, user: User) -> str:
    user.tg_link_code = new_link_code()
    db.commit()
    return user.tg_link_code


# ------------------------------------------------------ сессии / cookie

def make_session_cookie(user: User) -> str:
    return _signer.dumps({"uid": user.id})


def _uid_from_cookie(raw: str | None) -> Optional[int]:
    if not raw:
        return None
    try:
        data = _signer.loads(raw)
    except BadSignature:
        return None
    uid = data.get("uid")
    return int(uid) if isinstance(uid, int) else None


def optional_user(
    request: Request, db: Session = Depends(get_session)
) -> Optional[User]:
    uid = _uid_from_cookie(request.cookies.get(COOKIE_NAME))
    if uid is None:
        return None
    return db.get(User, uid)


def current_user(
    request: Request, db: Session = Depends(get_session)
) -> User:
    user = optional_user(request, db)
    if user is None:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return user
