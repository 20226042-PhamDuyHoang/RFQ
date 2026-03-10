from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from backend.config import settings

_is_sqlite = "sqlite" in settings.database_url

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
    echo=settings.debug,
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency: tao database session cho moi request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Tao toan bo bang trong database neu chua ton tai."""
    from backend import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
