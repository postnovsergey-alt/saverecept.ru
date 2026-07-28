from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL

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


def init_db():
    from app import models  # noqa: F401  — регистрация моделей

    models.Base.metadata.create_all(engine)
