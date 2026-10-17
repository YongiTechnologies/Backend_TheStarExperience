from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

# settings reads .env and normalizes the URL, so the app, seed scripts and
# Alembic all connect the same way.
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

if engine.dialect.name == "sqlite":
    # SQLite ignores foreign keys unless enabled per connection; PostgreSQL always enforces them.
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False
)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()
