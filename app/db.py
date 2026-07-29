import logging

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL

log = logging.getLogger(__name__)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True, future=True)

if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _):
        cur = dbapi_connection.cursor()
        try:
            # WAL нужен, чтобы сайт и бот могли писать одновременно
            cur.execute("PRAGMA journal_mode=WAL")
        except Exception:  # noqa: BLE001
            # на сетевых дисках и части FUSE-монтирований WAL недоступен —
            # работаем в обычном режиме, просто с более грубыми блокировками
            cur.execute("PRAGMA journal_mode=DELETE")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_column(table: str, column: str, ddl_type: str) -> None:
    """Мини-миграция «на месте»: добавляет колонку, если её ещё нет.

    Обходимся без Alembic: приложение маленькое, а колонок за всю жизнь
    добавлено штук пять. Работает и для SQLite, и для Postgres — оба
    понимают `ALTER TABLE ... ADD COLUMN ... DEFAULT ...`.
    """
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return
    if column in {c["name"] for c in inspector.get_columns(table)}:
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
    log.info("миграция: %s.%s добавлено", table, column)


def init_db():
    from app import models  # noqa: F401  — регистрация моделей

    models.Base.metadata.create_all(engine)
    _ensure_column("images", "is_source", "BOOLEAN NOT NULL DEFAULT FALSE")
    _ensure_column("users", "pin_hash", "VARCHAR(120) NOT NULL DEFAULT ''")
    _ensure_column("users", "is_admin", "BOOLEAN NOT NULL DEFAULT 0")
