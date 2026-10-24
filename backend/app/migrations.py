"""Explicit additive v1 -> v2 -> v3 migrations; no reset or silent inference.

SQLite DDL is transactional only after an explicit BEGIN; BEGIN IMMEDIATE also
serializes concurrent application/worker startups. PostgreSQL locks the singleton
schema-version row. Migration changes and the checksum ledger commit atomically.
Back up production databases before an application upgrade.
"""
from __future__ import annotations

import hashlib

from sqlalchemy import Engine, inspect, insert, select, update
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db import Base, utcnow

MIGRATION_NAME = "v2_reviewed_identity_and_explicit_event_semantics"
# Additive and nullable except the honest unknown coordinate default. No old
# coordinate is promoted, no successful shot becomes a goal, no outcome becomes
# possession retention, and no anonymous track becomes a canonical player.
EVENT_COLUMN_DDL = (
    "coordinate_system VARCHAR(20) NOT NULL DEFAULT 'unknown'",
    "coordinate_provenance VARCHAR(500)",
    "period INTEGER",
    "attacking_direction VARCHAR(20)",
    "possession_retained BOOLEAN",
    "shot_outcome VARCHAR(10)",
    "contest_id VARCHAR(80)",
    "identity_assignment_id VARCHAR(36) REFERENCES track_identity_assignments(id) ON DELETE SET NULL",
    "identity_assignment_revision INTEGER",
)


def migration_checksum(engine: Engine) -> str:
    """Fingerprint actual dialect-specific v2 DDL, not data or environment."""
    from app import models  # noqa: F401  # Register canonical metadata.

    statements = [MIGRATION_NAME, *EVENT_COLUMN_DDL]
    for table in (Base.metadata.tables["schema_migrations"], Base.metadata.tables["track_identity_assignments"]):
        statements.append(str(CreateTable(table).compile(dialect=engine.dialect)))
        statements.extend(str(CreateIndex(index).compile(dialect=engine.dialect)) for index in sorted(table.indexes, key=lambda item: item.name or ""))
    return hashlib.sha256("\n".join(statements).encode("utf-8")).hexdigest()


def upgrade_v1_to_v2(engine: Engine) -> bool:
    """Upgrade an existing v1 database, returning False on an idempotent v2 call.

    Unknown versions and partially modified v1 schemas fail without modification.
    This intentionally supports only the application's SQLite/PostgreSQL engines.
    No table rebuild or foreign-key-disable operation is needed.
    """
    from app.models import SchemaMigration, SchemaVersion

    if engine.dialect.name not in {"sqlite", "postgresql"}:
        raise RuntimeError("schema migration supports only SQLite and PostgreSQL")
    with engine.connect() as connection:
        try:
            if engine.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.begin()
            inspector = inspect(connection)
            if not inspector.has_table("schema_versions"):
                raise RuntimeError("migration requires an explicitly versioned v1 database")
            statement = select(SchemaVersion.version).where(SchemaVersion.id == 1)
            if engine.dialect.name == "postgresql":
                statement = statement.with_for_update()
            version = connection.execute(statement).scalar_one_or_none()
            if version in {2, 3}:
                connection.commit()
                return False
            if version != 1:
                raise RuntimeError("database schema version is incompatible; explicit migration required")
            if not inspector.has_table("reviewed_events"):
                raise RuntimeError("v1 database is missing reviewed_events; refusing repair or reset")
            existing_columns = {item["name"] for item in inspector.get_columns("reviewed_events")}
            additions = {item.split()[0] for item in EVENT_COLUMN_DDL}
            if existing_columns & additions or inspector.has_table("track_identity_assignments") or inspector.has_table("schema_migrations"):
                raise RuntimeError("partially modified v1 schema; restore or inspect before migration")
            # All v1 tables must already exist. Foreign keys must never point into
            # newly fabricated parent records when upgrading a corrupt database.
            required = {"workspaces", "accounts", "players", "matches", "appearances", "rights_attestations", "uploads", "processing_jobs", "track_observations", "auth_sessions", "audit_logs"}
            if not required.issubset(inspector.get_table_names()):
                raise RuntimeError("v1 database is incomplete; refusing repair or reset")
            Base.metadata.tables["schema_migrations"].create(connection)
            Base.metadata.tables["track_identity_assignments"].create(connection)
            for definition in EVENT_COLUMN_DDL:
                connection.exec_driver_sql(f"ALTER TABLE reviewed_events ADD COLUMN {definition}")
            applied_at = utcnow()
            connection.execute(insert(SchemaMigration).values(version=2, name=MIGRATION_NAME, checksum=migration_checksum(engine), applied_at=applied_at))
            result = connection.execute(update(SchemaVersion).where(SchemaVersion.id == 1, SchemaVersion.version == 1).values(version=2, applied_at=applied_at))
            if result.rowcount != 1:
                raise RuntimeError("schema version changed during migration")
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise


CHUNK_MIGRATION_NAME = "v3_durable_full_match_processing_chunks"
OBSERVATION_COLUMN_DDL = "processing_chunk_id VARCHAR(36) REFERENCES processing_chunks(id) ON DELETE SET NULL"


def chunk_migration_checksum(engine: Engine) -> str:
    from app import models  # noqa: F401

    table = Base.metadata.tables["processing_chunks"]
    observation_index = next(index for index in Base.metadata.tables["track_observations"].indexes if index.name == "ix_observation_chunk_time")
    statements = [CHUNK_MIGRATION_NAME, OBSERVATION_COLUMN_DDL, str(CreateTable(table).compile(dialect=engine.dialect))]
    statements.extend(str(CreateIndex(index).compile(dialect=engine.dialect)) for index in sorted(table.indexes, key=lambda item: item.name or ""))
    statements.append(str(CreateIndex(observation_index).compile(dialect=engine.dialect)))
    return hashlib.sha256("\n".join(statements).encode("utf-8")).hexdigest()


def upgrade_v2_to_v3(engine: Engine) -> bool:
    """Add chunk ledger and nullable observation provenance; old coverage is unknown."""
    from app.models import SchemaMigration, SchemaVersion

    if engine.dialect.name not in {"sqlite", "postgresql"}:
        raise RuntimeError("schema migration supports only SQLite and PostgreSQL")
    with engine.connect() as connection:
        try:
            if engine.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.begin()
            inspector = inspect(connection)
            if not inspector.has_table("schema_versions"):
                raise RuntimeError("migration requires an explicitly versioned v2 database")
            statement = select(SchemaVersion.version).where(SchemaVersion.id == 1)
            if engine.dialect.name == "postgresql":
                statement = statement.with_for_update()
            version = connection.execute(statement).scalar_one_or_none()
            if version == 3:
                connection.commit()
                return False
            if version != 2:
                raise RuntimeError("database schema version is incompatible; v2 required")
            required = set(Base.metadata.tables) - {"processing_chunks"}
            if not required.issubset(inspector.get_table_names()):
                raise RuntimeError("v2 database is incomplete; refusing repair or reset")
            columns = {item["name"] for item in inspector.get_columns("track_observations")}
            if inspector.has_table("processing_chunks") or "processing_chunk_id" in columns:
                raise RuntimeError("partially modified v2 schema; restore or inspect before migration")
            if connection.execute(select(SchemaMigration.version).where(SchemaMigration.version == 2)).scalar_one_or_none() is None:
                raise RuntimeError("v2 migration ledger is missing; refusing unaudited repair")
            Base.metadata.tables["processing_chunks"].create(connection)
            connection.exec_driver_sql(f"ALTER TABLE track_observations ADD COLUMN {OBSERVATION_COLUMN_DDL}")
            observation_index = next(index for index in Base.metadata.tables["track_observations"].indexes if index.name == "ix_observation_chunk_time")
            observation_index.create(connection)
            applied_at = utcnow()
            connection.execute(insert(SchemaMigration).values(version=3, name=CHUNK_MIGRATION_NAME, checksum=chunk_migration_checksum(engine), applied_at=applied_at))
            result = connection.execute(update(SchemaVersion).where(SchemaVersion.id == 1, SchemaVersion.version == 2).values(version=3, applied_at=applied_at))
            if result.rowcount != 1:
                raise RuntimeError("schema version changed during migration")
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise


def upgrade_to_latest(engine: Engine) -> bool:
    first = upgrade_v1_to_v2(engine)
    second = upgrade_v2_to_v3(engine)
    return first or second


if __name__ == "__main__":
    from app.db import get_engine

    changed = upgrade_to_latest(get_engine())
    print("Database migrated to schema v3." if changed else "Database is already schema v3.")
