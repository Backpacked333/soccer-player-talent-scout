"""Synthetic orchestration checks, not detector/youth-footage accuracy tests."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import fullmatch, worker
from app.config import Settings
from app.db import utcnow
from app.integrations import VisionSettings
from app.models import ProcessingChunk, ProcessingJob, TrackObservation, Upload
from app.processing_contracts import chunk_scoped_track_id
from app.schemas import ChunkResult, ObservationWrite
from app.vision import MediaError
from test_worker import queued as _queued_fixture


@pytest.fixture(name="queued")
def queued_case(tmp_path):
    return _queued_fixture.__wrapped__(tmp_path)


def configured():
    return VisionSettings(_env_file=None, labels={0: "player"}, checkpoint_sha256="a" * 64)


def prepare(queued, duration=5700):
    settings, factory, job_id, upload_id, _ = queued
    with factory() as db:
        upload = db.get(Upload, upload_id)
        upload.duration_seconds, upload.width, upload.height, upload.fps = duration, 320, 240, 25
        db.get(ProcessingJob, job_id).result_json = {"metadata": {"duration_seconds": duration, "width": 320, "height": 240, "fps": 25, "codec": "synthetic"}}
        path = settings.storage_root / upload.storage_key
        db.commit()
    return settings, factory, job_id, path


def result_for(request):
    return ChunkResult(chunk_id=request.chunk_id, sequence=request.sequence, start_seconds=request.start_seconds,
        end_seconds=request.end_seconds, status="completed", processed_through_seconds=request.end_seconds,
        decoded_frames=2500, sampled_frames=200, model_version="synthetic-test-double",
        observations=[ObservationWrite(processing_chunk_id=request.chunk_id, track_id=chunk_scoped_track_id(request.chunk_id, "player-1"),
            timestamp_seconds=request.start_seconds + .01, label="player", bbox=(.1, .1, .3, .6), confidence=.8,
            model_version="synthetic-test-double")], message="Synthetic fixture segment complete")


def test_plan_covers_90_minutes_and_stoppage_without_frame_cap():
    plan = fullmatch.plan_source(5700, "0" * 64, Settings(_env_file=None), configured())
    assert len(plan.chunks) == 57
    assert plan.chunks[0].start_seconds == 0 and plan.chunks[-1].end_seconds == 5700
    assert all(a.end_seconds == b.start_seconds for a, b in zip(plan.chunks, plan.chunks[1:]))
    assert Settings(_env_file=None).max_upload_bytes == 8 * 1024**3
    assert Settings(_env_file=None).max_video_seconds >= 5700


def test_small_budgets_are_rejected_not_silently_truncated():
    with pytest.raises(MediaError, match="ten-second"):
        fullmatch.plan_source(5400, "0" * 64, Settings(_env_file=None), configured().model_copy(update={"max_frames": 1}))


def test_full_95_minutes_are_processed_in_bounded_turns(queued, monkeypatch):
    settings, factory, job_id, path = prepare(queued)
    seen = []
    def execute(_path, metadata, config, request, _settings):
        assert request.end_seconds <= metadata.duration_seconds
        seen.append((request.start_seconds, request.end_seconds))
        return result_for(request)
    monkeypatch.setattr(fullmatch, "execute_chunk", execute)
    while claim := worker.claim_job(settings):
        fullmatch.process_one(settings, claim, path, configured())
        assert len(seen) <= 57
    assert len(seen) == 57 and seen[-1][1] == 5700
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.status == "needs_review" and job.progress == 1
        assert job.attempts == 0
        assert job.result_json["processing_coverage"]["completed_chunks"] == 57
        assert job.result_json["processing_coverage"]["covered_seconds"] == 5700
        assert "not_established_by_detection" in str(job.result_json)
        observations = list(db.scalars(select(TrackObservation)))
        assert len(observations) == 57
        assert len({o.track_id for o in observations}) == 57
        assert all(o.player_id is None and not o.calibration_valid for o in observations)


def test_restart_preserves_completed_evidence_and_skips_old_segments(queued, monkeypatch):
    settings, factory, job_id, path = prepare(queued, 250)
    monkeypatch.setattr(fullmatch, "execute_chunk", lambda _p, _m, _c, request, _s: result_for(request))
    fullmatch.process_one(settings, worker.claim_job(settings), path, configured())
    with factory() as db:
        first = db.scalar(select(TrackObservation))
        first.player_id = queued[-1]  # stand-in for an already confirmed identity
        first_id, first_track = first.id, first.track_id
        db.commit()
    abandoned = worker.claim_job(settings)
    with factory() as db:
        db.get(ProcessingJob, job_id).lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    recovered = worker.claim_job(settings)
    assert recovered.worker_id != abandoned.worker_id
    with pytest.raises(worker.LostLease):
        fullmatch.process_one(settings, abandoned, path, configured())
    fullmatch.process_one(settings, recovered, path, configured())
    fullmatch.process_one(settings, worker.claim_job(settings), path, configured())
    with factory() as db:
        assert db.get(TrackObservation, first_id).player_id == queued[-1]
        assert db.get(TrackObservation, first_id).track_id == first_track
        assert len(list(db.scalars(select(TrackObservation)))) == 3
        assert db.get(ProcessingJob, job_id).status == "needs_review"


def test_failure_and_changed_model_never_erase_completed_segments(queued, monkeypatch):
    settings, factory, job_id, path = prepare(queued, 250)
    monkeypatch.setattr(fullmatch, "execute_chunk", lambda _p, _m, _c, request, _s: result_for(request))
    fullmatch.process_one(settings, worker.claim_job(settings), path, configured())
    claim = worker.claim_job(settings)
    with pytest.raises(MediaError, match="differs from the saved plan"):
        fullmatch.process_one(settings, claim, path, configured().model_copy(update={"threshold": .7}))
    def fail(*_):
        raise MediaError("processing_timeout", "Synthetic timeout")
    monkeypatch.setattr(fullmatch, "execute_chunk", fail)
    with pytest.raises(MediaError, match="Synthetic timeout"):
        fullmatch.process_one(settings, claim, path, configured())
    worker._fail(settings, claim, MediaError("processing_timeout", "Synthetic timeout"))
    with factory() as db:
        chunks = list(db.scalars(select(ProcessingChunk).order_by(ProcessingChunk.sequence)))
        assert [c.status for c in chunks] == ["completed", "failed", "pending"]
        assert len(list(db.scalars(select(TrackObservation)))) == 1
        assert fullmatch.coverage(chunks, 250).covered_seconds == 100
        job = db.get(ProcessingJob, job_id)
        assert job.status == "failed" and job.progress == .4
        assert job.result_json["metadata"]["codec"] == "synthetic"
        assert job.result_json["processing_coverage"]["failed_chunks"] == 1


def test_stale_detector_result_is_fenced_before_writing(queued, monkeypatch):
    settings, factory, job_id, path = prepare(queued, 250)
    def stale(_p, _m, _c, request, _s):
        with factory() as db:
            db.get(ProcessingJob, job_id).worker_id = "replacement-worker"
            db.commit()
        return result_for(request)
    monkeypatch.setattr(fullmatch, "execute_chunk", stale)
    with pytest.raises(worker.LostLease):
        fullmatch.process_one(settings, worker.claim_job(settings), path, configured())
    with factory() as db:
        assert db.scalar(select(TrackObservation)) is None
        assert db.scalar(select(ProcessingChunk).where(ProcessingChunk.status == "completed")) is None


def test_changed_detection_cap_is_fenced_even_when_interval_layout_matches(queued, monkeypatch):
    settings, factory, job_id, path = prepare(queued, 250)
    settings = settings.model_copy(update={"processing_chunk_seconds": 10})
    monkeypatch.setattr(fullmatch, "execute_chunk", lambda _p, _m, _c, request, _s: result_for(request))
    fullmatch.process_one(settings, worker.claim_job(settings), path, configured())
    with pytest.raises(MediaError, match="differs from the saved plan"):
        fullmatch.process_one(settings, worker.claim_job(settings), path, configured().model_copy(update={"max_detections_per_frame": 50}))
    with factory() as db:
        assert len(db.scalars(select(TrackObservation)).all()) == 1
        assert sum(c.status == "completed" for c in db.scalars(select(ProcessingChunk).where(ProcessingChunk.job_id == job_id))) == 1
