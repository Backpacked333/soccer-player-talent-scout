"""Durable bounded full-source detection. One leased chunk per worker turn."""
from __future__ import annotations

import math
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import insert, select, update

from app.config import Settings
from app.db import get_session_factory, utcnow
from app.integrations import VisionSettings
from app.intelligence.contracts import canonical_fingerprint
from app.models import ProcessingChunk, ProcessingJob, TrackObservation
from app.processing_contracts import ChunkInterval, FullMatchPlan, FullMatchPolicy, ProcessingCoverage
from app.schemas import ChunkRequest, ChunkResult, VideoMetadata
from app.vision import MediaError, probe_video, run_json_child


def plan_source(duration: float, digest: str, settings: Settings, config: VisionSettings) -> FullMatchPlan:
    interval = settings.processing_sample_interval_seconds
    # Budget for the worst allowed number of detections, not just an average
    # football scene; never silently stop at max_frames in a long source.
    chunk_seconds = min(settings.processing_chunk_seconds, config.max_frames * interval,
                        (config.max_observations // config.max_detections_per_frame) * interval)
    if chunk_seconds < 10:
        raise MediaError("chunk_budget", "Detector budgets cannot cover even a ten-second segment. Increase the bounded budgets or reduce the sampling rate.")
    policy = FullMatchPolicy(chunk_seconds=chunk_seconds, sample_interval_seconds=interval,
        max_chunks=settings.processing_max_chunks, max_sampled_frames_per_chunk=config.max_frames,
        max_observations_per_chunk=config.max_observations,
        observation_batch_size=min(settings.processing_observation_batch_size, config.max_observations),
        max_chunk_attempts=settings.processing_max_chunk_attempts)
    count = math.ceil(duration / chunk_seconds)
    if count > policy.max_chunks:
        raise MediaError("chunk_budget", "The complete source exceeds the configured segment budget. No partial plan was accepted.")
    return FullMatchPlan(source_duration_seconds=duration, source_sha256=digest, policy=policy,
        chunks=[ChunkInterval(sequence=i, start_seconds=i * chunk_seconds, end_seconds=min(duration, (i + 1) * chunk_seconds)) for i in range(count)])


def coverage(chunks: list[ProcessingChunk], duration: float) -> ProcessingCoverage:
    complete = [chunk for chunk in chunks if chunk.status == "completed"]
    all_done = bool(chunks) and len(complete) == len(chunks)
    covered = duration if all_done else sum(chunk.end_seconds - chunk.start_seconds for chunk in complete)
    return ProcessingCoverage(status="complete" if all_done else "partial", source_duration_seconds=duration,
        covered_seconds=covered, planned_chunks=len(chunks), completed_chunks=len(complete),
        failed_chunks=sum(chunk.status == "failed" for chunk in chunks),
        sample_interval_seconds=chunks[0].sample_interval_seconds if chunks else None,
        sampled_frames=sum(chunk.sampled_frames for chunk in complete),
        observation_count=sum(chunk.observation_count for chunk in complete))


def execute_chunk(path: Path, metadata: VideoMetadata, config: VisionSettings, request: ChunkRequest, settings: Settings) -> ChunkResult:
    raw = run_json_child("app.adapters.rfdetr_tracker", {"path": str(path), "metadata": metadata.model_dump(mode="json"),
        "config": config.model_dump(mode="json"), "chunk": request.model_dump(mode="json")},
        timeout=min(config.timeout_seconds, settings.worker_lease_seconds - 10))
    if raw.get("error"):
        raise MediaError("detector_unavailable", "The bounded detector could not finish this segment. Completed segments are retained; check the local model and retry.")
    try:
        return ChunkResult.model_validate(raw)
    except ValueError as exc:
        raise MediaError("invalid_chunk", "The detector returned invalid segment evidence; no observations from this attempt were saved.") from exc


def process_one(settings: Settings, claim, path: Path, config: VisionSettings) -> None:
    # Local import keeps the ordinary metadata worker lightweight and testable.
    from app.worker import LostLease, _advance, _inputs

    factory = get_session_factory(settings.database_url)
    with factory() as db:
        _advance(db, claim, settings, stage="planning_full_source", message="Planning every source segment including stoppage time.")
        upload, _, _ = _inputs(db, claim, settings)
        if upload.duration_seconds is None or upload.width is None or upload.height is None or upload.fps is None:
            raise MediaError("metadata_required", "Validated video metadata is required before full-source detection.")
        job = db.get(ProcessingJob, claim.id)
        saved_metadata = job.result_json.get("metadata") if job is not None else None
        metadata = VideoMetadata.model_validate(saved_metadata) if saved_metadata else probe_video(path, settings=settings)
        if (metadata.duration_seconds, metadata.width, metadata.height, metadata.fps) != (upload.duration_seconds, upload.width, upload.height, upload.fps):
            raise MediaError("metadata_changed", "The source metadata differs from the validated upload; existing evidence was preserved.")
        plan = plan_source(metadata.duration_seconds, upload.sha256, settings, config)
        fingerprint = canonical_fingerprint({"plan": plan.model_dump(mode="json"),
            "checkpoint": config.checkpoint_sha256, "architecture": config.architecture,
            "classes": config.num_classes, "labels": config.labels, "threshold": config.threshold,
            "max_detections_per_frame": config.max_detections_per_frame,
            "tracking_gap_seconds": config.tracking_gap_seconds, "method": "rfdetr1.9.2-bt0.29.1-chunk-v1"})
        chunks = list(db.scalars(select(ProcessingChunk).where(ProcessingChunk.job_id == claim.id).order_by(ProcessingChunk.sequence)))
        if not chunks:
            chunks = [ProcessingChunk(workspace_id=claim.workspace_id, job_id=claim.id, match_id=claim.match_id,
                upload_id=claim.upload_id, is_synthetic=claim.is_synthetic, sample_interval_seconds=plan.policy.sample_interval_seconds,
                plan_fingerprint=fingerprint, **interval.model_dump()) for interval in plan.chunks]
            db.add_all(chunks)
            db.flush()
        if len(chunks) != len(plan.chunks) or any(c.plan_fingerprint != fingerprint or c.workspace_id != claim.workspace_id
                or c.upload_id != claim.upload_id or c.match_id != claim.match_id or c.is_synthetic != claim.is_synthetic
                or (c.sequence, c.start_seconds, c.end_seconds) != (i.sequence, i.start_seconds, i.end_seconds)
                for c, i in zip(chunks, plan.chunks)):
            raise MediaError("processing_plan_changed", "The source, model or processing policy differs from the saved plan. Completed evidence was preserved; restore the original configuration before resuming.")
        chunk = next((c for c in chunks if c.status != "completed"), None)
        if chunk is None:
            _finish(db, claim, settings, chunks, metadata)
            db.commit()
            return
        if chunk.attempts >= settings.processing_max_chunk_attempts:
            raise MediaError("chunk_retry_limit", "This segment reached its retry limit. Completed segments remain available; ask the operator to investigate.")
        chunk.status = "running"
        chunk.attempts += 1
        chunk.worker_id = claim.worker_id
        chunk.lease_token = str(uuid4())
        chunk.lease_expires_at = utcnow() + timedelta(seconds=settings.worker_lease_seconds)
        chunk.started_at = utcnow()
        chunk.finished_at = None
        chunk.error_code = None
        chunk.message = "Processing bounded source segment."
        db.flush()
        chunk_id, token, revision = chunk.id, chunk.lease_token, chunk.revision
        request = ChunkRequest(chunk_id=chunk_id, sequence=chunk.sequence, start_seconds=chunk.start_seconds,
            end_seconds=chunk.end_seconds, source_duration_seconds=metadata.duration_seconds,
            sample_interval_seconds=chunk.sample_interval_seconds, max_sampled_frames=config.max_frames,
            max_observations=config.max_observations, plan_fingerprint=fingerprint)
        progress = coverage(chunks, metadata.duration_seconds)
        _advance(db, claim, settings, stage="processing_segments", progress=progress.covered_seconds / metadata.duration_seconds,
            message=f"Processing segment {chunk.sequence + 1} of {len(chunks)}; completed segments survive restarts.",
            result_json={"metadata": metadata.model_dump(mode="json"), "processing_coverage": progress.model_dump(mode="json"), "analysis_kind": "anonymous_detection", "is_synthetic": claim.is_synthetic})
        db.commit()
    try:
        result = execute_chunk(path, metadata, config, request, settings)
        if (result.chunk_id, result.sequence, result.start_seconds, result.end_seconds) != (request.chunk_id, request.sequence, request.start_seconds, request.end_seconds):
            raise MediaError("invalid_chunk", "Detector result does not belong to the claimed source segment.")
        if result.status != "completed":
            raise MediaError(result.error_code or "incomplete_segment", "The segment was not fully processed. Previously completed segments are retained for retry.")
        if result.sampled_frames > request.max_sampled_frames or len(result.observations) > request.max_observations:
            raise MediaError("evidence_limit", "Segment evidence exceeded the configured budget; no partial segment was saved.")
        if any(o.player_id is not None or o.calibration_valid or o.pitch_x is not None or o.pitch_y is not None for o in result.observations):
            raise MediaError("invalid_evidence", "Automatic identities or calibration are not supported; no evidence was saved.")
        with factory() as db:
            _advance(db, claim, settings, stage="saving_segment")
            _inputs(db, claim, settings)
            chunk = db.get(ProcessingChunk, chunk_id)
            from app.worker import _aware
            if chunk is None or chunk.status != "running" or chunk.lease_token != token or chunk.revision != revision or chunk.worker_id != claim.worker_id or chunk.lease_expires_at is None or _aware(chunk.lease_expires_at) <= utcnow():
                raise LostLease()
            now = utcnow()
            # Batch inserts are bounded and committed with the completion mark.
            # Crashes roll back this whole chunk, never a previously saved one.
            for start in range(0, len(result.observations), settings.processing_observation_batch_size):
                rows = [{"id": str(uuid4()), "workspace_id": claim.workspace_id, "match_id": claim.match_id,
                    "upload_id": claim.upload_id, "is_synthetic": claim.is_synthetic, **o.model_dump()}
                    for o in result.observations[start:start + settings.processing_observation_batch_size]]
                db.execute(insert(TrackObservation), rows)
            chunk.status = "completed"
            chunk.progress = 1
            chunk.processed_through_seconds = chunk.end_seconds
            chunk.decoded_frames = result.decoded_frames
            chunk.sampled_frames = result.sampled_frames
            chunk.observation_count = len(result.observations)
            chunk.model_version = result.model_version
            chunk.result_fingerprint = canonical_fingerprint(result.model_dump(mode="json"))
            chunk.message = result.message
            chunk.finished_at = now
            chunk.worker_id = chunk.lease_token = None
            chunk.lease_expires_at = None
            db.flush()
            chunks = list(db.scalars(select(ProcessingChunk).where(ProcessingChunk.job_id == claim.id).order_by(ProcessingChunk.sequence)))
            _finish(db, claim, settings, chunks, metadata)
            db.commit()
    except MediaError as exc:
        with factory() as db:
            _advance(db, claim, settings, stage="segment_failed")
            chunk = db.get(ProcessingChunk, chunk_id)
            if chunk is None or chunk.revision != revision or chunk.lease_token != token:
                raise LostLease()
            chunk.status = "failed"
            chunk.error_code = exc.code
            chunk.message = exc.public_message
            chunk.worker_id = chunk.lease_token = None
            chunk.lease_expires_at = None
            chunk.finished_at = utcnow()
            db.commit()
        raise


def _finish(db, claim, settings, chunks, metadata) -> None:
    from app.worker import _advance
    progress = coverage(chunks, metadata.duration_seconds)
    done = progress.status == "complete"
    _advance(db, claim, settings, status="needs_review" if done else "queued", stage="anonymous_tracks_ready" if done else "segments_queued",
        progress=progress.covered_seconds / metadata.duration_seconds, attempts=0,
        message="Every source segment was sampled. Confirm identities and review events; detection is not full match analysis." if done else "Segment saved. Remaining segments are queued; completed work will not be repeated.",
        result_json={"metadata": metadata.model_dump(mode="json"), "processing_coverage": progress.model_dump(mode="json"),
            "analysis_kind": "anonymous_detection", "observation_count": progress.observation_count, "candidate_count": 0,
            "identity_status": "review_required", "is_synthetic": claim.is_synthetic}, finished_at=utcnow() if done else None)
    db.execute(update(ProcessingJob).where(ProcessingJob.id == claim.id, ProcessingJob.worker_id == claim.worker_id).values(worker_id=None, lease_expires_at=None))
