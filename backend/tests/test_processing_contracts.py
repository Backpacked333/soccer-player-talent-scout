"""Synthetic full-source contracts/schema tests; no detector or full video needed."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import MetaData, event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.config import Settings
from app.db import utcnow
from app.intelligence import TrackIdentityWrite
from app.migrations import CHUNK_MIGRATION_NAME, chunk_migration_checksum, upgrade_v1_to_v2, upgrade_v2_to_v3
from app.models import ProcessingChunk, SchemaMigration, SchemaVersion, TrackIdentityAssignment, TrackObservation
from app.processing_contracts import FullMatchPlan, FullMatchPolicy, ProcessingCoverage, chunk_scoped_track_id
from app.schemas import ChunkRequest, ChunkResult, JobRead, ObservationWrite
from test_intelligence_migration import legacy_database


def plan(duration=5760.25):
    chunks = [dict(sequence=i, start_seconds=start, end_seconds=min(start + 120, duration)) for i, start in enumerate(range(0, int(duration) + (duration % 1 > 0), 120))]
    return dict(source_duration_seconds=duration, source_sha256="a" * 64, policy=FullMatchPolicy(), chunks=chunks)


def v2_database(tmp_path):
    _url, engine, legacy = legacy_database(tmp_path)
    upgrade_v1_to_v2(engine)
    with engine.begin() as connection:
        connection.execute(legacy.tables["matches"].update().values(duration_seconds=5760))
        connection.execute(legacy.tables["uploads"].insert().values(id="upload", workspace_id="workspace", match_id="match", original_filename="synthetic.mp4", storage_key="synthetic.mp4", content_type="video/mp4", size_bytes=1, sha256="a" * 64, duration_seconds=5760, is_synthetic=True))
        connection.execute(legacy.tables["processing_jobs"].insert().values(id="job", workspace_id="workspace", match_id="match", upload_id="upload", is_synthetic=True))
        connection.execute(legacy.tables["track_observations"].insert().values(id="observation", workspace_id="workspace", match_id="match", upload_id="upload", track_id="old-local-17", timestamp_seconds=5700, label="player", bbox=[0.1, 0.1, 0.2, 0.2], confidence=0.8, model_version="synthetic-test", is_synthetic=True))
    with Session(engine) as db:
        db.add(TrackIdentityAssignment(id="identity", workspace_id="workspace", match_id="match", upload_id="upload", player_id="player", track_id="old-local-17", start_seconds=5700, end_seconds=5701, status="confirmed", created_by="reviewer", reviewed_by="reviewer", reviewed_at=utcnow(), reason="Synthetic reviewed identity must survive", is_synthetic=True))
        db.commit()
    return engine


def chunk_fields(**updates):
    fields = dict(id=str(uuid4()), workspace_id="workspace", job_id="job", match_id="match", upload_id="upload", sequence=0, start_seconds=5640, end_seconds=5760, sample_interval_seconds=0.5, plan_fingerprint="a" * 64, is_synthetic=True)
    return fields | updates


def test_upload_defaults_accept_realistic_long_matches_without_allocating_video():
    config = Settings(_env_file=None)
    assert config.max_upload_bytes == 8 * 1024**3
    assert config.max_video_seconds == 10800
    assert config.processing_chunk_seconds == 120
    assert config.processing_sample_interval_seconds == 0.5
    assert Settings(_env_file=None, max_upload_bytes=16 * 1024**3).max_upload_bytes == 16 * 1024**3
    for updates in ({"max_upload_bytes": 16 * 1024**3 + 1}, {"processing_chunk_seconds": 601}, {"processing_max_chunks": 1441}, {"processing_observation_batch_size": 2001}):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **updates)


def test_ninety_minutes_plus_stoppage_and_fractional_tail_are_required():
    value = FullMatchPlan(**plan())
    assert len(value.chunks) == 49
    assert value.chunks[-1].start_seconds == 5760
    assert value.chunks[-1].end_seconds == 5760.25
    assert FullMatchPlan(**plan(5400)).chunks[-1].end_seconds == 5400
    assert len(FullMatchPlan(**plan(10800)).chunks) == 90
    fields = plan()
    for chunks in (fields["chunks"][:-1], fields["chunks"][1:], list(reversed(fields["chunks"])), fields["chunks"] + [fields["chunks"][-1]]):
        with pytest.raises(ValidationError):
            FullMatchPlan(**(fields | {"chunks": chunks}))
    for update in ({"start_seconds": 119}, {"start_seconds": 121}, {"sequence": 99}):
        chunks = [dict(chunk) for chunk in fields["chunks"]]
        chunks[1].update(update)
        with pytest.raises(ValidationError):
            FullMatchPlan(**(fields | {"chunks": chunks}))
    with pytest.raises(ValidationError):
        FullMatchPolicy(chunk_seconds=600, sample_interval_seconds=0.05)
    with pytest.raises(ValidationError):
        FullMatchPlan(**(fields | {"policy": FullMatchPolicy(max_chunks=48)}))


def test_chunk_requests_results_keep_absolute_timestamps_and_scoped_identity():
    chunk_id = str(uuid4())
    request = dict(chunk_id=chunk_id, sequence=47, start_seconds=5640, end_seconds=5760, source_duration_seconds=5760, plan_fingerprint="a" * 64)
    assert ChunkRequest(**request).end_seconds == 5760
    for update in ({"max_sampled_frames": 100}, {"source_duration_seconds": 5400}):
        with pytest.raises(ValidationError):
            ChunkRequest(**(request | update))
    track_id = chunk_scoped_track_id(chunk_id, "cut2.17")
    assert track_id != chunk_scoped_track_id(str(uuid4()), "cut2.17")
    identity = TrackIdentityWrite(upload_id="upload", track_id=track_id, start_seconds=5700, end_seconds=5701, player_id="player", reason="Synthetic reviewed track")
    assert identity.track_id == track_id
    with pytest.raises(ValidationError):
        TrackIdentityWrite(**(identity.model_dump() | {"track_id": "chunk:17"}))
    observation = dict(processing_chunk_id=chunk_id, track_id=track_id, timestamp_seconds=5700, label="player", bbox=[0.1, 0.1, 0.2, 0.2], confidence=0.8, model_version="synthetic-test")
    result = dict(chunk_id=chunk_id, sequence=47, start_seconds=5640, end_seconds=5760, status="completed", processed_through_seconds=5760, decoded_frames=3600, sampled_frames=240, model_version="synthetic-test", observations=[observation], message="Synthetic completed chunk")
    assert ChunkResult(**result).observations[0].timestamp_seconds == 5700
    for update in ({"timestamp_seconds": 60}, {"timestamp_seconds": 5760}, {"track_id": "17"}, {"processing_chunk_id": str(uuid4())}):
        with pytest.raises(ValidationError):
            ChunkResult(**(result | {"observations": [observation | update]}))
    for update in ({"processed_through_seconds": 5700}, {"sampled_frames": 0}, {"error_code": "budget_exhausted"}):
        with pytest.raises(ValidationError):
            ChunkResult(**(result | update))
    assert ChunkResult(**(result | {"status": "failed", "processed_through_seconds": 5700, "error_code": "budget_exhausted"})).status == "failed"
    assert ObservationWrite(**(observation | {"processing_chunk_id": None, "track_id": "legacy-local"})).processing_chunk_id is None


def test_full_scan_coverage_never_implies_reviewed_events_or_metadata_detection():
    fields = dict(status="complete", source_duration_seconds=5760, covered_seconds=5760, planned_chunks=48, completed_chunks=48, sample_interval_seconds=0.5, sampled_frames=11520)
    assert ProcessingCoverage(**fields).reviewed_event_coverage == "not_established_by_detection"
    for update in ({"completed_chunks": 47}, {"covered_seconds": 5400}, {"status": "metadata_only"}, {"sample_interval_seconds": None}):
        with pytest.raises(ValidationError):
            ProcessingCoverage(**(fields | update))
    assert ProcessingCoverage(status="metadata_only", source_duration_seconds=5760).covered_seconds == 0
    assert "observations" not in JobRead.model_fields and "chunks" not in JobRead.model_fields


def test_v2_to_v3_preserves_all_rows_and_confirmed_identity(tmp_path):
    engine = v2_database(tmp_path)
    old = MetaData()
    old.reflect(engine)
    with engine.connect() as connection:
        before = {table.name: [dict(row) for row in connection.execute(select(table)).mappings()] for table in old.sorted_tables if table.name not in {"schema_versions", "schema_migrations"}}
    assert upgrade_v2_to_v3(engine) is True
    assert upgrade_v2_to_v3(engine) is False
    with engine.connect() as connection:
        after = {table.name: [dict(row) for row in connection.execute(select(table)).mappings()] for table in old.sorted_tables if table.name not in {"schema_versions", "schema_migrations"}}
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    assert before == after
    with Session(engine) as db:
        assert db.get(SchemaVersion, 1).version == 3
        assert db.get(SchemaMigration, 3).name == CHUNK_MIGRATION_NAME
        assert db.get(SchemaMigration, 3).checksum == chunk_migration_checksum(engine)
        assert db.get(TrackObservation, "observation").processing_chunk_id is None
        assert db.get(TrackIdentityAssignment, "identity").status == "confirmed"
        assert list(db.scalars(select(ProcessingChunk))) == []


def test_chunk_migration_rolls_back_and_concurrent_retry_is_idempotent(tmp_path):
    engine = v2_database(tmp_path)

    def interrupt(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("ALTER TABLE track_observations ADD COLUMN"):
            raise RuntimeError("synthetic chunk migration interruption")

    event.listen(engine, "before_cursor_execute", interrupt)
    with pytest.raises(RuntimeError, match="interruption"):
        upgrade_v2_to_v3(engine)
    event.remove(engine, "before_cursor_execute", interrupt)
    assert not inspect(engine).has_table("processing_chunks")
    with Session(engine) as db:
        assert db.get(SchemaVersion, 1).version == 2
        assert db.get(SchemaMigration, 3) is None
        assert db.get(TrackIdentityAssignment, "identity").status == "confirmed"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: upgrade_v2_to_v3(engine), range(2)))
    assert sorted(outcomes) == [False, True]


def test_chunk_database_constraints_and_optimistic_fence(tmp_path):
    engine = v2_database(tmp_path)
    upgrade_v2_to_v3(engine)
    fields = chunk_fields()
    with Session(engine) as db:
        db.add(ProcessingChunk(**fields))
        db.commit()
    with Session(engine) as first, Session(engine) as stale:
        current = first.get(ProcessingChunk, fields["id"])
        old = stale.get(ProcessingChunk, fields["id"])
        current.message = "First worker claimed revision"
        first.commit()
        old.message = "Stale worker cannot overwrite"
        with pytest.raises(StaleDataError):
            stale.commit()
        stale.rollback()
    for update in ({}, {"sequence": 1, "status": "completed"}, {"sequence": 1, "status": "running"}, {"sequence": 1, "processed_through_seconds": 60}):
        with Session(engine) as db:
            db.add(ProcessingChunk(**chunk_fields(**update)))
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
    with Session(engine) as db:
        item = db.get(ProcessingChunk, fields["id"])
        item.status = "completed"
        item.progress = 1
        item.processed_through_seconds = item.end_seconds
        item.decoded_frames = 3600
        item.sampled_frames = 240
        item.model_version = "synthetic-test"
        item.result_fingerprint = "b" * 64
        item.finished_at = utcnow()
        db.commit()
        assert db.get(TrackIdentityAssignment, "identity").status == "confirmed"
        assert item.revision == 3


def test_partial_v2_schema_is_not_silently_repaired(tmp_path):
    engine = v2_database(tmp_path)
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE track_observations ADD COLUMN processing_chunk_id VARCHAR(36)")
    with pytest.raises(RuntimeError, match="partially modified"):
        upgrade_v2_to_v3(engine)
    assert not inspect(engine).has_table("processing_chunks")
