"""Worker tests use isolated DBs and explicitly synthetic local fixtures only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import worker
from app.config import Settings
from app.db import get_session_factory, init_db, utcnow
from app.models import Account, Appearance, Match, Player, ProcessingJob, ReviewedEvent, RightsAttestation, TrackObservation, Upload, Workspace
from app.schemas import CandidateEvent, ObservationWrite, VideoMetadata, VisionResult
from app.vision import MediaError


@pytest.fixture
def queued(tmp_path):
    storage = tmp_path / "private"
    storage.mkdir()
    settings = Settings(_env_file=None, environment="test", database_url=f"sqlite:///{tmp_path / 'worker.sqlite'}", storage_root=storage, probe_timeout_seconds=2, worker_lease_seconds=30)
    init_db(settings.database_url)
    factory = get_session_factory(settings.database_url)
    with factory() as db:
        workspace = Workspace(name="Synthetic worker-test workspace")
        db.add(workspace)
        db.flush()
        account = Account(workspace_id=workspace.id, email="worker-fixture@example.invalid", password_hash="unused-test-only-hash")
        player = Player(workspace_id=workspace.id, display_name="Synthetic fixture player", age_group="U15", primary_role="midfielder")
        match = Match(workspace_id=workspace.id, title="Synthetic fixture match", match_date=date(2026, 1, 1), competition="Test", season="2026", age_group="U15", team_name="Test A", opponent_name="Test B", duration_seconds=60)
        db.add_all([account, player, match])
        db.flush()
        db.add(Appearance(workspace_id=workspace.id, match_id=match.id, player_id=player.id))
        db.add(RightsAttestation(workspace_id=workspace.id, match_id=match.id, account_id=account.id, permission_to_process=True, guardian_or_club_authorization=True, opponent_privacy_review=True, authorized_roster=True, retention_days=30))
        key = f"{uuid4()}.mp4"
        (storage / key).write_bytes(b"test-only-not-a-real-video")
        upload = Upload(workspace_id=workspace.id, match_id=match.id, original_filename="synthetic-test.mp4", storage_key=key, content_type="video/mp4", size_bytes=(storage / key).stat().st_size, sha256="0" * 64)
        db.add(upload)
        db.flush()
        job = ProcessingJob(workspace_id=workspace.id, match_id=match.id, upload_id=upload.id)
        db.add(job)
        db.commit()
        return settings, factory, job.id, upload.id, player.id


def fake_result(**kwargs):
    """Deterministic test fake, NEVER used as a production fallback."""
    return VisionResult(status="needs_configuration", stage="metadata_ready", message="Test-only metadata fixture", metadata=VideoMetadata(duration_seconds=2, width=320, height=240, fps=25, codec="h264"), **kwargs)


def test_atomic_claim_allows_exactly_one_worker(queued):
    settings, factory, job_id, *_ = queued
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: worker.claim_job(settings), range(8)))
    assert sum(item is not None for item in claims) == 1
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.attempts == 1 and job.status == "running"
        assert job.worker_id and job.lease_expires_at
    assert worker.run_once(settings) is False


def test_recovery_is_bounded_and_old_worker_is_fenced(queued):
    settings, factory, job_id, *_ = queued
    old = worker.claim_job(settings)
    with factory() as db:
        db.get(ProcessingJob, job_id).lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    new = worker.claim_job(settings)
    assert old.worker_id != new.worker_id
    with pytest.raises(worker.LostLease):
        worker._persist(settings, old, fake_result())
    worker._fail(settings, old, MediaError("test", "stale"))
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.status == "running" and job.worker_id == new.worker_id and job.attempts == 2
        job.attempts = settings.max_job_attempts
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert worker.claim_job(settings) is None
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.status == "failed" and job.stage == "retry_limit" and job.worker_id is None


def test_missing_configuration_persists_metadata_not_analysis(queued, monkeypatch):
    settings, factory, job_id, upload_id, _ = queued
    monkeypatch.setattr(worker, "_execute", lambda *_: fake_result())
    assert worker.run_once(settings) is True
    assert worker.run_once(settings) is False
    with factory() as db:
        job, upload = db.get(ProcessingJob, job_id), db.get(Upload, upload_id)
        assert job.status == "needs_configuration" and job.progress == 1
        assert job.result_json["analysis_kind"] == "metadata_only"
        assert job.result_json["observation_count"] == 0
        assert job.worker_id is None and job.lease_expires_at is None
        assert upload.duration_seconds == 2 and upload.width == 320 and upload.fps == 25
        assert db.scalars(select(ReviewedEvent)).all() == []
        assert db.scalars(select(TrackObservation)).all() == []


def test_anonymous_evidence_and_candidates_are_unapproved(queued, monkeypatch):
    settings, factory, job_id, _, player_id = queued
    result = fake_result().model_copy(update={"status": "completed", "stage": "test_fake_detection", "observations": [ObservationWrite(track_id="test-track", timestamp_seconds=0.04, label="player", bbox=(0.1, 0.2, 0.3, 0.5), confidence=0.8, model_version="deterministic-test-fake")], "candidates": [CandidateEvent(player_id=player_id, kind="pass", timestamp_seconds=0.5, confidence=0.8, model_version="deterministic-test-fake"), CandidateEvent(kind="shot", timestamp_seconds=1, confidence=0.5, model_version="deterministic-test-fake")]})
    monkeypatch.setattr(worker, "_execute", lambda *_: result)
    worker.run_once(settings)
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.status == "needs_review"  # even an adapter's "completed" is not approval
        assert job.result_json["observation_count"] == 1
        assert len(job.result_json["unresolved_candidates"]) == 1
        observation = db.scalar(select(TrackObservation))
        assert observation.player_id is None and observation.timestamp_seconds == 0.04
        event = db.scalar(select(ReviewedEvent))
        assert event.status == "draft" and event.reviewed_by is None and event.provenance == "model_candidate"


@pytest.mark.parametrize("case", ["rights", "roster", "mode", "tenant", "path", "retention", "size"])
def test_worker_rechecks_permissions_and_storage(queued, monkeypatch, case):
    settings, factory, job_id, upload_id, player_id = queued
    def must_not_execute(*_):
        raise AssertionError("inference must not run on invalid inputs")
    monkeypatch.setattr(worker, "_execute", must_not_execute)
    with factory() as db:
        upload = db.get(Upload, upload_id)
        if case == "rights":
            db.scalar(select(RightsAttestation)).permission_to_process = False
        elif case == "roster":
            db.delete(db.scalar(select(Appearance)))
        elif case == "mode":
            db.get(Player, player_id).is_synthetic = True
        elif case == "tenant":
            workspace = Workspace(name="Other synthetic tenant")
            db.add(workspace)
            db.flush()
            upload.workspace_id = workspace.id
        elif case == "path":
            upload.storage_key = "../escape.mp4"
        elif case == "retention":
            upload.created_at = utcnow() - timedelta(days=31)
        else:
            upload.size_bytes += 1
        db.commit()
    worker.run_once(settings)
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.status == "failed" and job.stage != "processing_failed"
        assert job.result_json.get("observation_count", 0) == 0


def test_invalid_video_real_subprocess_failure_is_persistent(queued):
    settings, factory, job_id, upload_id, _ = queued
    assert worker.run_once(settings)
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.status == "failed" and job.stage == "invalid_video"
        assert db.get(Upload, upload_id).status == "rejected"
        assert str(settings.storage_root) not in job.message


def test_unexpected_error_is_safe_and_not_retried_forever(queued, monkeypatch):
    settings, factory, job_id, *_ = queued
    def failure(*_):
        raise RuntimeError("/private/secret-model-and-child-data")
    monkeypatch.setattr(worker, "_execute", failure)
    worker.run_once(settings)
    assert not worker.run_once(settings)
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.status == "failed" and job.attempts == 1
        assert "secret-model" not in job.message and "secret-model" not in str(job.result_json)


def test_identity_or_invalid_actor_is_rejected(queued, monkeypatch):
    settings, factory, job_id, _, player_id = queued
    result = fake_result().model_copy(update={"status": "needs_review", "observations": [ObservationWrite(player_id=player_id, track_id="bad-auto-identity", timestamp_seconds=1, label="player", bbox=(0, 0, 1, 1), confidence=0.9, model_version="test-fake")]})
    monkeypatch.setattr(worker, "_execute", lambda *_: result)
    worker.run_once(settings)
    with factory() as db:
        assert db.get(ProcessingJob, job_id).stage == "invalid_evidence"
        assert db.scalar(select(TrackObservation)) is None


def test_same_upload_jobs_do_not_run_concurrently(queued):
    settings, factory, job_id, *_ = queued
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        db.add(ProcessingJob(workspace_id=job.workspace_id, match_id=job.match_id, upload_id=job.upload_id))
        db.commit()
    first = worker.claim_job(settings)
    assert first is not None
    assert worker.claim_job(settings) is None
