"""Synchronous SQLAlchemy sessions, versioned bootstrap and additive migration."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Generator, Iterator

from sqlalchemy import Engine, create_engine, event, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings

SCHEMA_VERSION = 3


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@lru_cache(maxsize=8)
def get_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    parsed = make_url(url)
    options: dict = {"pool_pre_ping": True}
    if parsed.get_backend_name() == "sqlite":
        options["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if not parsed.database or parsed.database == ":memory:":
            options["poolclass"] = StaticPool
        else:
            Path(parsed.database).expanduser().resolve().parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    engine = create_engine(url, **options)
    if parsed.get_backend_name() == "sqlite":
        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _record) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            if parsed.database and parsed.database != ":memory:":
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
    return engine


@lru_cache(maxsize=8)
def get_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(database_url), expire_on_commit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency; handlers commit explicitly, rollback/close on failure."""
    with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise


@contextmanager
def session_scope(database_url: str | None = None) -> Iterator[Session]:
    with get_session_factory(database_url)() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def init_db(database_url: str | None = None) -> None:
    """Create v3 if empty; apply explicit audited v1/v2 additive migrations.

    Unsupported versions fail closed. Existing evidence is never reset, seeded
    or inferred. Back up persisted databases before application upgrades.
    """
    from app.models import SchemaMigration, SchemaVersion  # Registers all tables.
    from app.migrations import chunk_migration_checksum, upgrade_to_latest

    engine = get_engine(database_url)
    if inspect(engine).has_table("schema_versions"):
        with engine.connect() as connection:
            version = connection.execute(select(SchemaVersion.version).where(SchemaVersion.id == 1)).scalar_one_or_none()
        if version in {1, 2}:
            upgrade_to_latest(engine)
        elif version is not None and version != SCHEMA_VERSION:
            raise RuntimeError("database schema version is incompatible; an explicit migration is required")
    Base.metadata.create_all(engine)
    with session_scope(database_url) as session:
        row = session.get(SchemaVersion, 1)
        if row is None:
            session.add(SchemaVersion(id=1, version=SCHEMA_VERSION))
            session.add(SchemaMigration(version=SCHEMA_VERSION, name="fresh_v3_bootstrap", checksum=chunk_migration_checksum(engine)))
        elif row.version != SCHEMA_VERSION:
            raise RuntimeError("database schema version is incompatible; an explicit migration is required")


if __name__ == "__main__":
    # Models import app.db.Base, not __main__.Base. Use the canonical module so
    # the CLI and API bootstrap exactly the same metadata/session registry.
    from app.db import init_db as bootstrap
    bootstrap()
    print(f"Database schema version {SCHEMA_VERSION} ready.")
