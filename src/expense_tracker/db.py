"""Engine and session helpers."""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base


def make_engine(url: str | None = None) -> Engine:
    url = url or get_settings().db_url
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    connect_args = {}
    if url.startswith("sqlite"):
        # The bot syncs in a worker thread while handlers run on the event loop.
        connect_args = {"check_same_thread": False, "timeout": 30}
    return create_engine(url, future=True, connect_args=connect_args)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)


def _add_missing_columns(engine: Engine) -> None:
    """Tiny migration step: add new nullable columns to existing tables.

    Enough while the schema only grows. Switch to Alembic when moving to PostgreSQL.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable:
                    raise RuntimeError(f"Can't auto-add NOT NULL column {table.name}.{column.name}")
                col_type = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}'))


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
