from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
def normalize_database_url(url: str) -> str:
    """SQLAlchemy only understands `postgresql://`; many hosts hand out `postgres://`."""
    url = url.strip()
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


_DATABASE_URL = normalize_database_url(settings.database_url)
_is_sqlite = _DATABASE_URL.startswith("sqlite")
engine = create_engine(
    _DATABASE_URL,
    pool_pre_ping=True,
    # SQLite: allow the web server's worker threads to share the file and wait (not fail)
    # when a background task is writing at the same moment.
    connect_args={"check_same_thread": False, "timeout": 15} if _is_sqlite else {},
)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:  # pragma: no cover - trivial
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
        cur.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    from app.migrate import ensure_columns

    ensure_columns(engine)