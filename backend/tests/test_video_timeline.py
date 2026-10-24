"""Source-window contracts against synthetic footage in isolated workspaces."""
from datetime import timedelta
from uuid import uuid4

import pytest

from app.db import get_session_factory, utcnow
from app.models import ProcessingChunk, ProcessingJob, TrackObservation, Upload
from test_api_workflows import match_setup, signup, upload


@pytest.fixture
def timeline_source(api, tiny_video):
    _, first, second, settings = api
    signup(first)
    _, _, match, _ = match_setup(first)
    source = upload(first, match, tiny_video)
    factory = get_session_factory(settings.database_url)
    job_id = source["job"]["id"]
    upload_id = source["upload"]["id"]
    with factory() as db:
        db.get(ProcessingJob, job_id).status = "needs_review"
        for sequence, status in enumerate(("completed", "failed")):
            chunk = ProcessingChunk(id=str(uuid4()), workspace_id=match["workspace_id"], match_id=match["id"], upload_id=upload_id, job_id=job_id, sequence=sequence, start_seconds=sequence * 1.5, end_seconds=(sequence + 1) * 1.5, sample_interval_seconds=.5, plan_fingerprint="a" * 64, status=status, progress=1 if sequence == 0 else 0, processed_through_seconds=1.5 if sequence == 0 else None, decoded_frames=3, sampled_frames=3, model_version="synthetic-test-only", result_fingerprint="b" * 64, finished_at=utcnow())
            db.add(chunk)
            db.flush()
            for timestamp in ((1.0, .5, 0.0) if sequence == 0 else (2.0,)):
                db.add(TrackObservation(workspace_id=match["workspace_id"], match_id=match["id"], upload_id=upload_id, processing_chunk_id=chunk.id, track_id=f"chunk:{chunk.id}:player-1", timestamp_seconds=timestamp, label="player", bbox=[.1, .1, .2, .3], confidence=.8, model_version="synthetic-test-only"))
        db.commit()
    return first, second, factory, job_id, upload_id, match


def test_window_ordering_half_open_interval_and_completed_evidence_only(timeline_source):
    first, _, _, job, source, _ = timeline_source
    response = first.get(f"/api/jobs/{job}/timeline?start=0.5&end=1")
    assert response.status_code == 200, response.text
    assert "no-store" in response.headers["cache-control"]
    body = response.json()
    assert body["job_id"] == job and body["upload_id"] == source
    assert [row["timestamp_seconds"] for row in body["items"]] == [.5]
    assert body["truncated"] is False
    assert [row["status"] for row in body["segments"]] == ["completed", "failed"]
    body = first.get(f"/api/jobs/{job}/timeline?start=0&end=3").json()
    assert [row["timestamp_seconds"] for row in body["items"]] == [0, .5, 1]
    assert all(row["player_id"] is None for row in body["items"])


@pytest.mark.parametrize("query", ["start=0&end=31", "start=1&end=1", "start=2&end=1", "start=-1&end=2", "start=nan&end=2", "start=0&end=inf", "start=0&end=14401"])
def test_window_bounds(timeline_source, query):
    first, _, _, job, _, _ = timeline_source
    assert first.get(f"/api/jobs/{job}/timeline?{query}").status_code == 422


def test_timeline_requires_current_session_workspace_and_retention(timeline_source):
    first, second, factory, job, source, _ = timeline_source
    path = f"/api/jobs/{job}/timeline?start=0&end=3"
    assert second.get(path).status_code == 401
    signup(second, "timeline-other")
    assert second.get(path).status_code == 404
    with factory() as db:
        db.get(Upload, source).created_at = utcnow() - timedelta(days=31)
        db.commit()
    assert first.get(path).status_code == 410


def test_other_jobs_cannot_claim_a_completed_jobs_observations(timeline_source):
    first, _, factory, job, source, match = timeline_source
    other = str(uuid4())
    with factory() as db:
        db.add(ProcessingJob(id=other, workspace_id=match["workspace_id"], match_id=match["id"], upload_id=source, status="needs_review"))
        db.commit()
    body = first.get(f"/api/jobs/{other}/timeline?start=0&end=3").json()
    assert body["items"] == [] and body["segments"] == []
    assert len(first.get(f"/api/jobs/{job}/timeline?start=0&end=3").json()["items"]) == 3


def test_dense_windows_explicitly_report_truncation(timeline_source):
    first, _, factory, job, source, match = timeline_source
    with factory() as db:
        chunk = db.query(ProcessingChunk).filter_by(job_id=job, status="completed").one()
        db.add_all([TrackObservation(workspace_id=match["workspace_id"], match_id=match["id"], upload_id=source, processing_chunk_id=chunk.id, track_id=f"chunk:{chunk.id}:player-{index}", timestamp_seconds=.2, label="player", bbox=[.1, .1, .2, .3], confidence=.8, model_version="synthetic-density-test") for index in range(5001)])
        db.commit()
    body = first.get(f"/api/jobs/{job}/timeline?start=0&end=1").json()
    assert body["truncated"] and len(body["items"]) == 5000
