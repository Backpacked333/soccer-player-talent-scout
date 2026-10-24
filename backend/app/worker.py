"""Separate durable DB-backed worker. Atomic claims, expiring leases and fencing.

Run `python -m app.worker` or `python -m app.worker --once` after schema setup.
No background task inside the API is required. Retries only recover abandoned
leases; permanent errors remain actionable terminal failures rather than loops.
"""
from __future__ import annotations

import argparse
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from typing import Any, cast

from sqlalchemy import CursorResult, delete, exists, or_, select, update
from sqlalchemy.orm import Session, aliased

from app.config import Settings, get_settings
from app.db import get_session_factory, init_db, utcnow
from app.integrations import VisionSettings, detector_readiness
from app.models import Account, Appearance, Match, Player, ProcessingChunk, ProcessingJob, ReviewedEvent, RightsAttestation, TrackObservation, Upload
from app.schemas import VisionResult
from app.vision import MediaError, open_video, run_json_child

log = logging.getLogger("scout.worker")


@dataclass(frozen=True)
class Claim:
    id: str
    workspace_id: str
    match_id: str
    upload_id: str
    is_synthetic: bool
    worker_id: str


class LostLease(Exception):
    pass


def _scope(claim: Claim):
    return (ProcessingJob.id == claim.id, ProcessingJob.workspace_id == claim.workspace_id, ProcessingJob.match_id == claim.match_id, ProcessingJob.upload_id == claim.upload_id, ProcessingJob.is_synthetic == claim.is_synthetic, ProcessingJob.worker_id == claim.worker_id, ProcessingJob.status == "running", ProcessingJob.lease_expires_at > utcnow())


def recover_expired(db: Session, settings: Settings) -> None:
    now = utcnow()
    expired = (ProcessingJob.status == "running", or_(ProcessingJob.lease_expires_at <= now, ProcessingJob.lease_expires_at.is_(None)))
    db.execute(update(ProcessingJob).where(*expired, ProcessingJob.attempts >= settings.max_job_attempts).values(status="failed", stage="retry_limit", progress=0, message="The worker stopped repeatedly and reached its retry limit. Ask the operator to inspect the worker before re-uploading.", finished_at=now, updated_at=now, worker_id=None, lease_expires_at=None))
    db.execute(update(ProcessingJob).where(*expired, ProcessingJob.attempts < settings.max_job_attempts).values(status="queued", stage="recovered", progress=0, message="An abandoned worker lease expired. Waiting for a bounded retry.", updated_at=now, worker_id=None, lease_expires_at=None))
    db.execute(update(ProcessingJob).where(ProcessingJob.status == "queued", ProcessingJob.attempts >= settings.max_job_attempts).values(status="failed", stage="retry_limit", progress=0, message="The processing retry limit was reached. Ask the operator to inspect the worker before re-uploading.", finished_at=now, updated_at=now, worker_id=None, lease_expires_at=None))


def claim_job(settings: Settings, worker_id: str | None = None) -> Claim | None:
    worker_id = worker_id or f"worker-{uuid4()}"
    factory = get_session_factory(settings.database_url)
    with factory() as db:
        recover_expired(db, settings)
        db.commit()
    # CAS remains authoritative even if multiple workers select the same row.
    for _ in range(5):
        with factory() as db:
            other = aliased(ProcessingJob)
            active_upload = exists(select(other.id).where(other.upload_id == ProcessingJob.upload_id, other.status == "running", other.lease_expires_at > utcnow(), other.id != ProcessingJob.id))
            job = db.scalar(select(ProcessingJob).where(ProcessingJob.status == "queued", ProcessingJob.attempts < settings.max_job_attempts, ~active_upload).order_by(ProcessingJob.created_at, ProcessingJob.id).limit(1))
            if job is None:
                return None
            # Serialize same-upload claims on PostgreSQL; SQLite serializes the
            # conditional write. Never lock the media row while doing inference.
            db.execute(select(Upload.id).where(Upload.id == job.upload_id, Upload.workspace_id == job.workspace_id).with_for_update())
            now = utcnow()
            updated = cast(CursorResult[Any], db.execute(update(ProcessingJob).where(ProcessingJob.id == job.id, ProcessingJob.workspace_id == job.workspace_id, ProcessingJob.status == "queued", ProcessingJob.attempts < settings.max_job_attempts, ~active_upload).values(status="running", stage="validating_permissions", progress=0.05, message="Validating private footage permissions, roster and storage.", worker_id=worker_id, attempts=ProcessingJob.attempts + 1, started_at=now, finished_at=None, updated_at=now, lease_expires_at=now + timedelta(seconds=settings.worker_lease_seconds)).execution_options(synchronize_session=False))).rowcount
            if updated:
                claim = Claim(job.id, job.workspace_id, job.match_id, job.upload_id, job.is_synthetic, worker_id)
                db.commit()
                return claim
            db.rollback()
    return None


def _advance(db: Session, claim: Claim, settings: Settings, **values) -> None:
    now = utcnow()
    values.update(updated_at=now, lease_expires_at=now + timedelta(seconds=settings.worker_lease_seconds))
    if cast(CursorResult[Any], db.execute(update(ProcessingJob).where(*_scope(claim)).values(**values).execution_options(synchronize_session=False))).rowcount != 1:
        raise LostLease()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _inputs(db: Session, claim: Claim, settings: Settings) -> tuple[Upload, str, set[str]]:
    upload = db.scalar(select(Upload).where(Upload.id == claim.upload_id, Upload.workspace_id == claim.workspace_id, Upload.match_id == claim.match_id, Upload.is_synthetic == claim.is_synthetic))
    match = db.scalar(select(Match).where(Match.id == claim.match_id, Match.workspace_id == claim.workspace_id, Match.is_synthetic == claim.is_synthetic))
    account = db.scalar(select(Account).where(Account.workspace_id == claim.workspace_id, Account.is_active.is_(True)))
    rights = db.scalar(select(RightsAttestation).where(RightsAttestation.workspace_id == claim.workspace_id, RightsAttestation.match_id == claim.match_id, RightsAttestation.is_synthetic == claim.is_synthetic))
    if not upload or not match or not account or not rights or rights.account_id != account.id:
        raise MediaError("permission_review", "Footage, account or permission records are missing or inconsistent. Review this match's authorization before processing.")
    if not all((rights.permission_to_process, rights.guardian_or_club_authorization, rights.opponent_privacy_review, rights.authorized_roster)):
        raise MediaError("permission_review", "Footage processing permission and authorized-roster attestation are required.")
    if _aware(upload.created_at) + timedelta(days=rights.retention_days) <= utcnow():
        raise MediaError("retention_expired", "This video's attested retention period has expired. Remove it and review permissions before any new upload.")
    appearances = db.scalars(select(Appearance).where(Appearance.match_id == claim.match_id)).all()
    player_ids = {row.player_id for row in appearances}
    valid_players = set(db.scalars(select(Player.id).where(Player.id.in_(player_ids), Player.workspace_id == claim.workspace_id, Player.is_synthetic == claim.is_synthetic)).all())
    if not appearances or valid_players != player_ids or any(row.workspace_id != claim.workspace_id or row.is_synthetic != claim.is_synthetic for row in appearances):
        raise MediaError("roster_review", "Add a valid authorized roster for this match before processing footage.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", upload.storage_key) or ".." in upload.storage_key:
        raise MediaError("unsafe_media_path", "Media storage validation failed. Re-upload through the private upload form.")
    with open_video(settings.storage_root / upload.storage_key, settings) as media:
        if media.seek(0, 2) != upload.size_bytes:
            raise MediaError("media_changed", "Stored media no longer matches its upload record. Re-upload the original video.")
    return upload, account.id, valid_players


def _execute(path: Path, settings: Settings) -> VisionResult:
    # Explicitly transport only non-secret media limits, not DB credentials,
    # session signing keys, arbitrary environment values or account information.
    fields = ("storage_root", "max_upload_bytes", "max_video_seconds", "max_video_dimension", "probe_timeout_seconds", "ffprobe_binary", "worker_lease_seconds")
    safe_settings = settings.model_dump(mode="json", include=set(fields))
    safe_settings["environment"] = "test"
    try:
        vision_config = VisionSettings().model_dump(mode="json")
    except ValueError:
        vision_config = {"enabled": False}
    raw = run_json_child("app.vision", {"path": str(path), "settings": safe_settings, "vision": vision_config}, timeout=settings.worker_lease_seconds - 5)
    if raw.get("error"):
        # Internal child messages are fixed constants, never subprocess stderr.
        raise MediaError(str(raw["error"]), str(raw.get("message", "Video processing failed; ask the operator to check the worker.")))
    return VisionResult.model_validate(raw["result"])


def _persist(settings: Settings, claim: Claim, result: VisionResult) -> None:
    with get_session_factory(settings.database_url)() as db:
        # Fence BEFORE deleting/replacing any evidence. This also acquires a DB
        # write lock that protects final persistence from another lease claimant.
        _advance(db, claim, settings, stage="persisting_evidence", progress=0.9, message="Saving source metadata and unreviewed evidence.")
        upload, account_id, roster = _inputs(db, claim, settings)
        metadata = result.metadata
        if metadata.duration_seconds > settings.max_video_seconds or max(metadata.width, metadata.height) > settings.max_video_dimension:
            raise MediaError("invalid_result", "Processor metadata exceeded the configured limits; no evidence was saved.")
        if len(result.observations) > 50000 or len(result.candidates) > 10000:
            raise MediaError("evidence_limit", "Processor evidence exceeded the configured limits. Use a shorter clip.")
        # Configuration-only results can never smuggle inferred evidence into DB.
        if result.status == "needs_configuration" and (result.observations or result.candidates):
            raise MediaError("invalid_result", "Processor evidence did not match its processing state; no evidence was saved.")
        for observation in result.observations:
            if observation.timestamp_seconds > metadata.duration_seconds or observation.player_id is not None or observation.calibration_valid or observation.pitch_x is not None:
                raise MediaError("invalid_evidence", "Detection evidence has unsupported timing, calibration or automatic roster assignments; no evidence was saved.")
        for candidate in result.candidates:
            if candidate.timestamp_seconds > metadata.duration_seconds or (candidate.end_timestamp_seconds is not None and candidate.end_timestamp_seconds > metadata.duration_seconds):
                raise MediaError("invalid_evidence", "Candidate event timing exceeds the video; no evidence was saved.")
            if (candidate.player_id is not None and candidate.player_id not in roster) or (candidate.opponent_player_id is not None and candidate.opponent_player_id not in roster):
                raise MediaError("invalid_evidence", "Candidate actors are not in this match's authorized roster; no evidence was saved.")
        upload.duration_seconds, upload.width, upload.height, upload.fps = metadata.duration_seconds, metadata.width, metadata.height, metadata.fps
        upload.status = "probed"
        # Raw anonymous evidence can be replaced on retry. Reviewed events never
        # are: candidates are deduplicated without deleting reviewer corrections.
        if result.observations:
            db.execute(delete(TrackObservation).where(TrackObservation.workspace_id == claim.workspace_id, TrackObservation.upload_id == claim.upload_id, TrackObservation.is_synthetic == claim.is_synthetic, TrackObservation.processing_chunk_id.is_(None), TrackObservation.player_id.is_(None)))
        for observation in result.observations:
            db.add(TrackObservation(workspace_id=claim.workspace_id, match_id=claim.match_id, upload_id=claim.upload_id, is_synthetic=claim.is_synthetic, **observation.model_dump()))
        unresolved = []
        for candidate in result.candidates:
            if candidate.player_id is None:
                unresolved.append(candidate.model_dump(mode="json"))
                continue
            duplicate = db.scalar(select(ReviewedEvent.id).where(ReviewedEvent.workspace_id == claim.workspace_id, ReviewedEvent.match_id == claim.match_id, ReviewedEvent.player_id == candidate.player_id, ReviewedEvent.kind == candidate.kind, ReviewedEvent.timestamp_seconds == candidate.timestamp_seconds, ReviewedEvent.model_version == candidate.model_version, ReviewedEvent.provenance == "model_candidate", ReviewedEvent.is_synthetic == claim.is_synthetic))
            if duplicate is None:
                db.add(ReviewedEvent(workspace_id=claim.workspace_id, match_id=claim.match_id, is_synthetic=claim.is_synthetic, created_by=account_id, status="draft", provenance="model_candidate", **candidate.model_dump()))
        final_status = "needs_configuration" if result.status == "needs_configuration" else "needs_review"
        # No huge per-frame duplication or raw internal paths in JobRead.result.
        result_json = {"metadata": metadata.model_dump(mode="json"), "analysis_kind": "metadata_only" if final_status == "needs_configuration" else "anonymous_detection", "observation_count": len(result.observations), "candidate_count": len(result.candidates), "unresolved_candidates": unresolved, "warnings": result.warnings, "identity_status": "unassigned", "is_synthetic": claim.is_synthetic}
        chunks = list(db.scalars(select(ProcessingChunk).where(ProcessingChunk.job_id == claim.id, ProcessingChunk.workspace_id == claim.workspace_id).order_by(ProcessingChunk.sequence)))
        progress = 1.0
        if chunks:
            from app.fullmatch import coverage
            saved_coverage = coverage(chunks, metadata.duration_seconds)
            result_json.update(processing_coverage=saved_coverage.model_dump(mode="json"), observation_count=saved_coverage.observation_count,
                analysis_kind="anonymous_detection" if saved_coverage.completed_chunks else "metadata_only", identity_status="review_required")
            progress = saved_coverage.covered_seconds / metadata.duration_seconds
        _advance(db, claim, settings, status=final_status, stage=result.stage, progress=progress, message=result.message, result_json=result_json, finished_at=utcnow())
        db.execute(update(ProcessingJob).where(ProcessingJob.id == claim.id, ProcessingJob.worker_id == claim.worker_id).values(worker_id=None, lease_expires_at=None))
        db.commit()


def _fail(settings: Settings, claim: Claim, error: MediaError) -> None:
    with get_session_factory(settings.database_url)() as db:
        job = db.scalar(select(ProcessingJob).where(*_scope(claim)))
        if job is None:
            return
        saved = {**job.result_json, "error_code": error.code[:50], "is_synthetic": claim.is_synthetic}
        progress = 0.0
        chunks = list(db.scalars(select(ProcessingChunk).where(ProcessingChunk.job_id == claim.id, ProcessingChunk.workspace_id == claim.workspace_id).order_by(ProcessingChunk.sequence)))
        upload = db.get(Upload, claim.upload_id)
        if chunks and upload is not None and upload.duration_seconds:
            from app.fullmatch import coverage
            saved_coverage = coverage(chunks, upload.duration_seconds)
            saved["processing_coverage"] = saved_coverage.model_dump(mode="json")
            progress = saved_coverage.covered_seconds / upload.duration_seconds
        # A stale worker cannot fail a newer attempt or mutate another tenant.
        updated = cast(CursorResult[Any], db.execute(update(ProcessingJob).where(*_scope(claim)).values(status="failed", stage=error.code[:50], progress=progress, message=error.public_message[:500], result_json=saved, finished_at=utcnow(), updated_at=utcnow(), worker_id=None, lease_expires_at=None).execution_options(synchronize_session=False))).rowcount
        if updated and error.code in {"invalid_video", "invalid_video_metadata", "duration_limit", "resolution_limit", "invalid_media_size"}:
            db.execute(update(Upload).where(Upload.id == claim.upload_id, Upload.workspace_id == claim.workspace_id, Upload.match_id == claim.match_id, Upload.is_synthetic == claim.is_synthetic).values(status="rejected"))
        db.commit()


def run_once(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    claim = claim_job(settings)
    if claim is None:
        return False
    try:
        with get_session_factory(settings.database_url)() as db:
            upload, _, _ = _inputs(db, claim, settings)
            path = settings.storage_root / upload.storage_key
            _advance(db, claim, settings, stage="probe_and_optional_detection", progress=0.2, message="Validating source video; optional configured detection runs in an isolated bounded process.")
            db.commit()
        try:
            config = VisionSettings()
            ready = detector_readiness(config, settings)[0] == "ready"
        except ValueError:
            ready = False
        if ready:
            from app.fullmatch import process_one
            process_one(settings, claim, path, config)
        else:
            result = _execute(path, settings)
            _persist(settings, claim, result)
    except LostLease:
        log.warning("Discarded a stale processing attempt after lease ownership changed.")
    except MediaError as exc:
        _fail(settings, claim, exc)
    except Exception:
        _fail(settings, claim, MediaError("processing_failed", "Processing could not complete. Ask the operator to check the worker installation; no approved statistics were generated."))
        log.warning("A processing attempt failed; sensitive exception details were suppressed.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Private durable soccer-video worker")
    parser.add_argument("--once", action="store_true", help="Claim at most one job then exit")
    args = parser.parse_args()
    settings = get_settings()
    init_db(settings.database_url)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.once:
        print("Processed one queued job." if run_once(settings) else "No queued jobs.")
        return
    log.info("Private worker ready; no models or datasets are downloaded.")
    try:
        while True:
            if not run_once(settings):
                time.sleep(settings.worker_poll_seconds)
    except KeyboardInterrupt:
        log.info("Worker stopped. Any unfinished lease will recover on expiry.")


if __name__ == "__main__":
    main()
