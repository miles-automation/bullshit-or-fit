"""SQLAlchemy engine + session for the jobtrends data engine.

Sync SQLAlchemy 2.0 over psycopg3 — the same shape the rest of the fleet uses
(see human-index-v2/backend/app/db.py). `create_engine` does not open a
connection at import time, so importing this module is free; the landing/lead web
app never imports it, keeping the public site independent of Postgres.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
