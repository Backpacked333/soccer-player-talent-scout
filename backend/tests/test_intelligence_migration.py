"""Schema v1 preservation/atomicity on isolated synthetic SQLite databases."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from sqlalchemy import CheckConstraint, MetaData, Table, event, inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.db import SCHEMA_VERSION, Base, get_engine, init_db, utcnow
from app.migrations import EVENT_COLUMN_DDL, MIGRATION_NAME, migration_checksum, upgrade_v1_to_v2
from app.models import ReviewedEvent, SchemaMigration, SchemaVersion, TrackIdentityAssignment


def legacy_database(tmp_path, *, populated_pipeline=False):
    """Actual pre-v2 columns/constraints, not a v1 label on a v2 database."""
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    engine = get_engine(url)
    legacy = MetaData()
    excluded_columns = {definition.split()[0] for definition in EVENT_COLUMN_DDL}
    for table in Base.metadata.sorted_tables:
        if table.name in {"track_identity_assignments", "schema_migrations", "processing_chunks"}:
            continue
        if table.name in {"reviewed_events", "track_observations"}:
            excluded = excluded_columns if table.name == "reviewed_events" else {"processing_chunk_id"}
            columns = [column._copy() for column in table.columns if column.name not in excluded]
            checks = [CheckConstraint(str(constraint.sqltext), name=constraint.name) for constraint in table.constraints if isinstance(constraint, CheckConstraint)]
            Table(table.name, legacy, *columns, *checks)
        else:
            table.to_metadata(legacy)
    legacy.create_all(engine)
    now = utcnow()
    with engine.begin() as connection:
        connection.execute(legacy.tables["schema_versions"].insert().values(id=1, version=1, applied_at=now))
        connection.execute(legacy.tables["workspaces"].insert().values(id="workspace", name="Synthetic preservation test", created_at=now))
        connection.execute(legacy.tables["accounts"].insert().values(id="reviewer", workspace_id="workspace", email="synthetic@example.test", password_hash="test-only-not-a-password", created_at=now, is_active=True))
        connection.execute(legacy.tables["players"].insert().values(id="player", workspace_id="workspace", display_name="Synthetic player", age_group="U15", primary_role="midfielder", team_name="Test", notes="preserve", created_at=now, is_synthetic=True))
        connection.execute(legacy.tables["matches"].insert().values(id="match", workspace_id="workspace", title="Synthetic match", match_date=date(2025, 1, 1), competition="Test league", season="2025", age_group="U15", team_name="Test", opponent_name="Test away", duration_seconds=600, created_at=now, is_synthetic=True))
        connection.execute(legacy.tables["reviewed_events"].insert().values(id="event", workspace_id="workspace", match_id="match", player_id="player", kind="shot", outcome="successful", timestamp_seconds=1, start_x=0.2, start_y=0.3, status="approved", provenance="synthetic", created_by="reviewer", reviewed_by="reviewer", reviewed_at=now, created_at=now, updated_at=now, is_synthetic=True, notes="Successful generic shot is NOT automatically a goal"))
        if populated_pipeline:
            common = {"workspace_id": "workspace", "match_id": "match", "is_synthetic": True}
            connection.execute(legacy.tables["appearances"].insert().values(id="appearance", player_id="player", side="home", role="midfielder", jersey_number=7, played_seconds=600, reviewed_seconds=60, **common))
            connection.execute(legacy.tables["uploads"].insert().values(id="upload", original_filename="fictional.mp4", storage_key="fixture-only.mp4", content_type="video/mp4", size_bytes=123, sha256="a" * 64, duration_seconds=600, status="probed", created_at=now, **common))
            connection.execute(legacy.tables["processing_jobs"].insert().values(id="job", upload_id="upload", status="needs_review", stage="review", progress=1, result_json={"legacy_note": "preserve", "observations": 1}, created_at=now, updated_at=now, **common))
            connection.execute(legacy.tables["track_observations"].insert().values(id="observation", upload_id="upload", track_id="local-7", timestamp_seconds=1, label="player", bbox=[0.1, 0.2, 0.3, 0.4], confidence=0.7, calibration_valid=False, model_version="fictional-v1", **common))
            connection.execute(legacy.tables["audit_logs"].insert().values(id="audit", account_id="reviewer", entity_type="event", entity_id="event", action="create", after_json={"notes": "legacy semantics unqualified"}, reason="Fictional preservation test", created_at=now, **common))
    return url, engine, legacy


def test_v1_upgrade_preserves_every_original_value_and_does_not_infer_semantics(tmp_path):
    url, engine, legacy = legacy_database(tmp_path, populated_pipeline=True)
    with engine.connect() as connection:
        before = {table.name: [dict(row) for row in connection.execute(select(table)).mappings()] for table in legacy.sorted_tables if table.name != "schema_versions"}
    assert upgrade_v1_to_v2(engine) is True
    assert upgrade_v1_to_v2(engine) is False
    with engine.connect() as connection:
        after = {table.name: [dict(row) for row in connection.execute(select(table)).mappings()] for table in legacy.sorted_tables if table.name != "schema_versions"}
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    assert before == after
    with Session(engine) as db:
        assert db.get(SchemaVersion, 1).version == 2
        ledger = db.get(SchemaMigration, 2)
        assert ledger.name == MIGRATION_NAME
        assert ledger.checksum == migration_checksum(engine)
        item = db.get(ReviewedEvent, "event")
        assert item.start_x == 0.2 and item.start_y == 0.3
        assert item.coordinate_system == "unknown"
        assert item.shot_outcome is None and item.possession_retained is None
        assert item.identity_assignment_id is None and item.contest_id is None
        assert list(db.scalars(select(TrackIdentityAssignment))) == []
    init_db(url)
    with Session(engine) as db:
        assert len(list(db.scalars(select(SchemaMigration)))) == 2
        assert db.get(SchemaVersion, 1).version == SCHEMA_VERSION
    # Also verify v3 preserved the *populated* old roster, observation, job,
    # upload and audit payloads, not only their empty table schemas.
    with engine.connect() as connection:
        latest = {table.name: [dict(row) for row in connection.execute(select(table)).mappings()] for table in legacy.sorted_tables if table.name != "schema_versions"}
        assert before == latest
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert connection.exec_driver_sql("SELECT processing_chunk_id FROM track_observations").scalar_one() is None
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM processing_chunks").scalar_one() == 0


def test_startup_runs_explicit_migration_and_fresh_bootstrap_has_ledger(tmp_path):
    url, engine, _legacy = legacy_database(tmp_path)
    init_db(url)
    with Session(engine) as db:
        assert db.get(SchemaVersion, 1).version == SCHEMA_VERSION
        assert db.get(SchemaMigration, 2).name == MIGRATION_NAME
    fresh_url = f"sqlite:///{tmp_path / 'fresh.db'}"
    init_db(fresh_url)
    init_db(fresh_url)
    with Session(get_engine(fresh_url)) as db:
        assert db.get(SchemaMigration, SCHEMA_VERSION).name == "fresh_v3_bootstrap"


def test_ddl_and_ledger_roll_back_on_mid_migration_failure(tmp_path):
    _url, engine, _legacy = legacy_database(tmp_path)

    def injected_failure(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("ALTER TABLE reviewed_events ADD COLUMN shot_outcome"):
            raise RuntimeError("synthetic migration interruption")

    event.listen(engine, "before_cursor_execute", injected_failure)
    with pytest.raises(RuntimeError, match="interruption"):
        upgrade_v1_to_v2(engine)
    event.remove(engine, "before_cursor_execute", injected_failure)
    inspector = inspect(engine)
    assert not inspector.has_table("schema_migrations")
    assert not inspector.has_table("track_identity_assignments")
    assert "coordinate_system" not in {column["name"] for column in inspector.get_columns("reviewed_events")}
    with engine.connect() as connection:
        assert connection.execute(select(SchemaVersion.version)).scalar_one() == 1
    assert upgrade_v1_to_v2(engine) is True


def test_concurrent_upgrade_is_serialized_and_idempotent(tmp_path):
    _url, engine, _legacy = legacy_database(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: upgrade_v1_to_v2(engine), range(2)))
    assert sorted(outcomes) == [False, True]
    with Session(engine) as db:
        assert len(list(db.scalars(select(SchemaMigration)))) == 1


def test_identity_model_rejects_stale_orm_revision_writes(tmp_path):
    _url, engine, _legacy = legacy_database(tmp_path)
    upgrade_v1_to_v2(engine)
    with engine.begin() as connection:
        connection.execute(Base.metadata.tables["uploads"].insert().values(id="upload", workspace_id="workspace", match_id="match", original_filename="synthetic.mp4", storage_key="synthetic-key.mp4", content_type="video/mp4", size_bytes=1, sha256="a" * 64, is_synthetic=True))
    with Session(engine) as db:
        db.add(TrackIdentityAssignment(id="assignment", workspace_id="workspace", match_id="match", upload_id="upload", player_id="player", track_id="track", start_seconds=0, end_seconds=1, reason="Synthetic proposal", created_by="reviewer", is_synthetic=True))
        db.commit()
    with Session(engine) as first, Session(engine) as stale:
        current = first.get(TrackIdentityAssignment, "assignment")
        old = stale.get(TrackIdentityAssignment, "assignment")
        assert current.revision == old.revision == 1
        current.reason = "First reviewer revision"
        first.commit()
        assert current.revision == 2
        old.reason = "Stale reviewer overwrite must fail"
        with pytest.raises(StaleDataError):
            stale.commit()
        stale.rollback()
    with Session(engine) as db:
        saved = db.get(TrackIdentityAssignment, "assignment")
        assert saved.revision == 2 and saved.reason == "First reviewer revision"


def test_unsupported_and_partial_schema_fail_without_repair(tmp_path):
    url, engine, _legacy = legacy_database(tmp_path)
    with engine.begin() as connection:
        connection.execute(SchemaVersion.__table__.update().values(version=99))
    with pytest.raises(RuntimeError, match="incompatible"):
        init_db(url)
    assert not inspect(engine).has_table("schema_migrations")
    with engine.begin() as connection:
        connection.execute(SchemaVersion.__table__.update().values(version=1))
        connection.exec_driver_sql("ALTER TABLE reviewed_events ADD COLUMN coordinate_system VARCHAR(20)")
    with pytest.raises(RuntimeError, match="partially modified"):
        upgrade_v1_to_v2(engine)
    with engine.connect() as connection:
        assert connection.execute(select(SchemaVersion.version)).scalar_one() == 1
    assert not inspect(engine).has_table("schema_migrations")
