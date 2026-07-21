"""SQLAlchemy engine and session helpers."""

from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DATABASE_URL

logger = logging.getLogger(__name__)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_missing_columns() -> None:
    """Poor-man's migration: add any model columns missing from existing tables.

    Cached/older SQLite files (e.g. GitHub Actions' cached tracker.db) only
    get new tables from create_all(), not new columns on tables that already
    existed — this fills that gap so schema changes don't need a manual
    ALTER TABLE on every environment that already has a copy of the db.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # brand new table — create_all() already handled it
            existing_columns = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}"
                logger.info("Migrating: %s", ddl)
                conn.execute(text(ddl))


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
