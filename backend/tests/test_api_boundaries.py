"""Additional file/transaction/worker boundaries using isolated synthetic fixtures."""
from datetime import timedelta

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_session_factory, utcnow
from app.models import ProcessingJob, Upload
from test_api_workflows import event_body, match_setup, player, signup, upload


def test_chunked_body_limit_and_multipart_part_limits(api, tiny_video):
    _app, first, _second, settings = api
    signup(first)
    response = first.post("/api/players", content=iter([b"x" * (128 * 1024)] * 3), headers={"Content-Type": "application/json"})
    assert response.status_code == 413, response.text
    actor, opponent, match, roster = match_setup(first)
    endpoint = f"/api/matches/{match['id']}/upload"
    two = first.post(endpoint, files=[("file", ("a.mp4", tiny_video, "video/mp4")), ("file", ("b.mp4", tiny_video, "video/mp4"))])
    assert two.status_code == 400
    wrong = first.post(endpoint, files={"other": ("a.mp4", tiny_video, "video/mp4")})
    assert wrong.status_code == 422
    assert list(settings.storage_root.iterdir()) == []


def test_probe_duration_limits_and_coverage_cannot_exceed_source(api, tiny_video):
    _app, first, _second, settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    roster["appearances"][0]["reviewed_seconds"] = 300
    assert first.put(f"/api/matches/{match['id']}/roster", json=roster).status_code == 200
    response = first.post(f"/api/matches/{match['id']}/upload", files={"file": ("a.mp4", tiny_video, "video/mp4")})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "review_exceeds_video"
    assert list(settings.storage_root.iterdir()) == []
    roster["appearances"][0]["reviewed_seconds"] = 2
    assert first.put(f"/api/matches/{match['id']}/roster", json=roster).status_code == 200
    evidence = upload(first, match, tiny_video)
    roster["appearances"][0]["reviewed_seconds"] = 300
    assert first.put(f"/api/matches/{match['id']}/roster", json=roster).status_code == 422
    assert first.get(f"/api/uploads/{evidence['upload']['id']}/video").content == tiny_video


def test_upload_transaction_failure_does_not_orphan_files_or_jobs(api, tiny_video, monkeypatch):
    _app, first, _second, settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    def unavailable(_session):
        raise SQLAlchemyError("sensitive internal storage detail")
    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", unavailable)
        response = first.post(f"/api/matches/{match['id']}/upload", files={"file": ("a.mp4", tiny_video, "video/mp4")})
    assert response.status_code == 503
    assert "sensitive internal" not in response.text
    assert list(settings.storage_root.iterdir()) == []
    assert first.get("/api/jobs").json() == []
    assert first.get(f"/api/matches/{match['id']}/upload").status_code == 404


def test_delete_transaction_failure_restores_original_private_video(api, tiny_video, monkeypatch):
    _app, first, _second, settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    evidence = upload(first, match, tiny_video)
    def unavailable(_session):
        raise SQLAlchemyError("sensitive internal storage detail")
    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", unavailable)
        response = first.delete(f"/api/matches/{match['id']}")
    assert response.status_code == 503
    assert first.get(f"/api/matches/{match['id']}").status_code == 200
    assert first.get(f"/api/uploads/{evidence['upload']['id']}/video").content == tiny_video
    assert len(list(settings.storage_root.iterdir())) == 1


def test_active_job_blocks_delete_and_terminal_retry_is_atomic(api, tiny_video):
    _app, first, _second, settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    evidence = upload(first, match, tiny_video)
    job_id = evidence["job"]["id"]
    with get_session_factory(settings.database_url)() as db:
        job = db.get(ProcessingJob, job_id)
        job.status = "running"
        job.lease_expires_at = utcnow() + timedelta(minutes=2)
        job.worker_id = "synthetic-test-lease"
        db.commit()
    assert first.delete(f"/api/matches/{match['id']}").status_code == 409
    assert first.post(f"/api/jobs/{job_id}/retry").status_code == 409
    assert first.get(f"/api/uploads/{evidence['upload']['id']}/video").content == tiny_video
    with get_session_factory(settings.database_url)() as db:
        job = db.get(ProcessingJob, job_id)
        job.status = "needs_configuration"
        job.attempts = 1
        db.commit()
    assert first.post(f"/api/jobs/{job_id}/retry").status_code == 200
    assert first.post(f"/api/jobs/{job_id}/retry").status_code == 409


@pytest.mark.parametrize("missing_lease", [False, True])
def test_abandoned_job_does_not_block_privacy_deletion_or_resurrect_evidence(api, tiny_video, missing_lease):
    from app.schemas import VideoMetadata, VisionResult
    from app.worker import LostLease, _persist, claim_job

    _app, first, _second, settings = api
    signup(first)
    actor, _opponent, match, _roster = match_setup(first)
    evidence = upload(first, match, tiny_video)
    created = first.post(f"/api/matches/{match['id']}/events", json=event_body(actor)).json()
    claim = claim_job(settings)
    assert claim is not None
    assert first.delete(f"/api/matches/{match['id']}").status_code == 409
    with get_session_factory(settings.database_url)() as db:
        job = db.get(ProcessingJob, claim.id)
        job.lease_expires_at = None if missing_lease else utcnow() - timedelta(seconds=1)
        db.commit()

    # No worker restart/recovery pass is required to exercise a privacy action.
    assert first.delete(f"/api/matches/{match['id']}").status_code == 204
    assert first.get(f"/api/uploads/{evidence['upload']['id']}/video").status_code == 404
    assert first.get(f"/api/jobs/{claim.id}").status_code == 404
    assert first.delete(f"/api/events/{created['id']}").status_code == 404
    assert first.get(f"/api/players/{actor['id']}/profile").json()["metrics"] == []
    assert list(settings.storage_root.iterdir()) == []

    # A delayed child result cannot recreate a deleted job or evidence.
    result = VisionResult(status="needs_configuration", stage="metadata_ready", message="Test-only metadata", metadata=VideoMetadata(duration_seconds=3, width=320, height=240, fps=10, codec="h264"))
    with pytest.raises(LostLease):
        _persist(settings, claim, result)
    assert first.get("/api/jobs").json() == []


def test_worker_contract_metadata_only_is_persistent_and_not_analysis(api, tiny_video):
    from app.worker import run_once
    _app, first, _second, settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    evidence = upload(first, match, tiny_video)
    assert run_once(settings) is True
    status = first.get(f"/api/jobs/{evidence['job']['id']}").json()
    assert status["status"] == "needs_configuration", status
    assert status["result"]["metadata"]["duration_seconds"] == 3
    assert status["result"]["analysis_kind"] == "metadata_only"
    assert status["result"]["analysis_available"] is False
    assert status["attempts"] == 1
    assert status["finished_at"]
    assert first.get(f"/api/matches/{match['id']}/events").json() == []
    assert run_once(settings) is False
    assert first.post(f"/api/matches/{match['id']}/events", json=event_body(actor)).status_code == 201


def test_retention_expiry_blocks_playback_retry_and_new_review_but_not_deletion(api, tiny_video):
    _app, first, _second, settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    evidence = upload(first, match, tiny_video)
    created = first.post(f"/api/matches/{match['id']}/events", json=event_body(actor)).json()
    with get_session_factory(settings.database_url)() as db:
        record = db.get(Upload, evidence["upload"]["id"])
        record.created_at = utcnow() - timedelta(days=366)
        db.commit()
    assert first.get(f"/api/uploads/{evidence['upload']['id']}/video").status_code == 410
    assert first.head(f"/api/uploads/{evidence['upload']['id']}/video").status_code == 410
    assert first.post(f"/api/jobs/{evidence['job']['id']}/retry").status_code == 410
    assert first.post(f"/api/matches/{match['id']}/events", json=event_body(actor)).status_code == 410
    assert first.put(f"/api/events/{created['id']}", json=event_body(actor)).status_code == 410
    assert first.delete(f"/api/events/{created['id']}").status_code == 204
    assert first.delete(f"/api/matches/{match['id']}").status_code == 204
    assert list(settings.storage_root.iterdir()) == []


def test_database_storage_key_cannot_escape_root(api, tiny_video):
    _app, first, _second, settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    evidence = upload(first, match, tiny_video)
    with get_session_factory(settings.database_url)() as db:
        record = db.get(Upload, evidence["upload"]["id"])
        record.storage_key = "../secret.key"
        db.commit()
    response = first.get(f"/api/uploads/{evidence['upload']['id']}/video")
    assert response.status_code == 404
    assert "secret.key" not in response.text


def test_api_reinstantiation_preserves_private_records(api):
    from fastapi.testclient import TestClient
    from app.main import create_app
    _app, first, _second, settings = api
    signup(first)
    actor = player(first)
    # A separate app instance against the same DB verifies persistence, not a
    # process-global in-memory store. This does not restart anyone's server.
    with TestClient(create_app(settings)) as restarted:
        restarted.headers["Origin"] = "http://localhost:5173"
        response = restarted.post("/api/auth/login", json={"email": "one@example.test", "password": "synthetic-test-password-0123"})
        assert response.status_code == 200
        assert restarted.get(f"/api/players/{actor['id']}").status_code == 200
