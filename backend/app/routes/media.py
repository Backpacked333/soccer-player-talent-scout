"""Bounded private uploads, authorized byte ranges and durable job requests."""
from __future__ import annotations

import hashlib
import os
import re
import threading
from uuid import uuid4
from typing import Any, cast

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.responses import StreamingResponse

from app.auth import Principal, api_db, current_user, fail
from app.db import utcnow
from app.models import Appearance, Match, ProcessingChunk, ProcessingJob, RightsAttestation, TrackObservation, Upload
from app.intelligence.service import identity_service
from app.processing_contracts import ProcessingChunkRead
from app.schemas import JobRead, Mode, ObservationPage, ObservationRead, ProcessingChunkPage, TimelineSegment, TimelineWindow, UploadRead, UploadResponse
from app.services import bounded, check_retention, open_private, owned, private_path, probe_upload, public_job, validate_filename

router = APIRouter()
UPLOAD_SLOTS = threading.BoundedSemaphore(4)


def require_upload_rights(db: Session, principal: Principal, match: Match):
    attestation = db.scalar(select(RightsAttestation).where(RightsAttestation.workspace_id == principal.workspace_id, RightsAttestation.match_id == match.id, RightsAttestation.is_synthetic == match.is_synthetic))
    roster = db.scalar(select(Appearance.id).where(Appearance.workspace_id == principal.workspace_id, Appearance.match_id == match.id, Appearance.is_synthetic == match.is_synthetic).limit(1))
    if not attestation or not roster or not all((attestation.permission_to_process, attestation.guardian_or_club_authorization, attestation.opponent_privacy_review, attestation.authorized_roster)):
        fail(409, "attestation_required", "An authorized roster and all footage permission declarations are required.")
    source = db.scalar(select(Upload).where(Upload.workspace_id == principal.workspace_id, Upload.match_id == match.id, Upload.is_synthetic == match.is_synthetic))
    if source:
        check_retention(source, attestation)


def store_upload(file: UploadFile, request: Request, db: Session, principal: Principal, match: Match) -> UploadResponse:
    settings = request.app.state.settings
    name, suffix = validate_filename(file.filename, file.content_type)
    storage_key = f"{uuid4()}{suffix}"
    path = private_path(settings, storage_key)
    created = False
    committed = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        created = True
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(descriptor, "wb") as output:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    fail(413, "file_too_large", "Video exceeds the configured upload limit.")
                digest.update(chunk)
                output.write(chunk)
            if not size:
                fail(422, "empty_upload", "The uploaded video is empty.")
            output.flush()
            os.fsync(output.fileno())
        metadata = probe_upload(path, settings)
        # Re-read relationships immediately before persistence, including concurrent
        # roster/rights changes. A unique match key arbitrates duplicate uploads.
        db.expire_all()
        match = owned(db, Match, match.id, principal.workspace_id)
        match = identity_service(db, principal, match).lock_match(match.id)
        require_upload_rights(db, principal, match)
        excessive_coverage = db.scalar(select(Appearance.id).where(Appearance.workspace_id == principal.workspace_id, Appearance.match_id == match.id, Appearance.reviewed_seconds > metadata.duration_seconds).limit(1))
        if excessive_coverage:
            fail(422, "review_exceeds_video", "Declared reviewed time exceeds this video's duration. Correct the roster coverage before uploading.")
        upload = Upload(workspace_id=principal.workspace_id, match_id=match.id, is_synthetic=match.is_synthetic, original_filename=name, storage_key=storage_key, content_type=file.content_type, size_bytes=size, sha256=digest.hexdigest(), duration_seconds=metadata.duration_seconds, width=metadata.width, height=metadata.height, fps=metadata.fps, status="probed")
        db.add(upload)
        db.flush()
        job = ProcessingJob(workspace_id=principal.workspace_id, match_id=match.id, upload_id=upload.id, is_synthetic=match.is_synthetic, status="queued", stage="queued", progress=0, message="Waiting for private processing", result_json={"metadata": metadata.model_dump()})
        db.add(job)
        db.flush()
        result = UploadResponse(upload=UploadRead.model_validate(upload), job=public_job(job, db))
        db.commit()
        committed = True
        return result
    except OSError:
        fail(503, "upload_storage_unavailable", "Private upload storage is unavailable. No upload was accepted.")
    finally:
        if not committed:
            db.rollback()
            if created:
                path.unlink(missing_ok=True)


@router.post("/matches/{match_id}/upload", response_model=UploadResponse, status_code=201)
async def upload_video(match_id: str, request: Request, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    # Deliberate Request.form parsing here: auth and ownership run BEFORE multipart
    # input is consumed or spooled to disk, unlike a declarative File parameter.
    match = owned(db, Match, match_id, principal.workspace_id)
    if match.is_synthetic:
        fail(409, "synthetic_upload_disabled", "Synthetic fixtures cannot accept real footage. Create a real match instead.")
    require_upload_rights(db, principal, match)
    if db.scalar(select(Upload.id).where(Upload.workspace_id == principal.workspace_id, Upload.match_id == match.id)):
        fail(409, "upload_exists", "This match already has a video. Duplicate uploads are not replacements.")
    active = db.scalar(select(func.count()).select_from(ProcessingJob).where(ProcessingJob.workspace_id == principal.workspace_id, ProcessingJob.status.in_(["queued", "running"])))
    if (active or 0) >= 5:
        fail(429, "queue_limit", "Finish existing processing jobs before adding more video.")
    stored = db.scalar(select(func.coalesce(func.sum(Upload.size_bytes), 0)).where(Upload.workspace_id == principal.workspace_id))
    if stored + request.app.state.settings.max_upload_bytes > request.app.state.settings.max_upload_bytes * 10:
        fail(409, "storage_limit", "The local workspace video allowance is full. Delete old match footage first.")
    request.app.state.limiter.check("upload", principal.workspace_id, 20)
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data;"):
        fail(415, "multipart_required", "Send exactly one multipart video file named file.")
    if not UPLOAD_SLOTS.acquire(blocking=False):
        fail(503, "upload_capacity", "Private video validation is busy. Try again shortly.")
    try:
        async with request.form(max_files=1, max_fields=0, max_part_size=1024 * 1024) as form:
            items = list(form.multi_items())
            if len(items) != 1 or items[0][0] != "file" or not isinstance(items[0][1], UploadFile):
                fail(422, "one_file_required", "Send exactly one multipart video file named file.")
            return await run_in_threadpool(store_upload, items[0][1], request, db, principal, match)
    finally:
        UPLOAD_SLOTS.release()


@router.get("/matches/{match_id}/upload", response_model=UploadRead)
def get_upload(match_id: str, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    match = owned(db, Match, match_id, principal.workspace_id)
    row = db.scalar(select(Upload).where(Upload.workspace_id == principal.workspace_id, Upload.match_id == match.id, Upload.is_synthetic == match.is_synthetic))
    if row is None:
        fail(404, "upload_not_found", "This match has no private video upload.")
    return row


def byte_range(value: str | None, size: int) -> tuple[int, int, bool]:
    if value is None:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not match or not any(match.groups()) or len(value) > 100:
        raise ValueError("invalid range")
    first, last = match.groups()
    if first:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    else:
        suffix = int(last)
        if suffix <= 0:
            raise ValueError("invalid suffix")
        start, end = max(size - suffix, 0), size - 1
    if start < 0 or start >= size or end < start:
        raise ValueError("unsatisfiable range")
    return start, end, True


@router.api_route("/uploads/{upload_id}/video", methods=["GET", "HEAD"])
def private_video(upload_id: str, request: Request, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    upload = owned(db, Upload, upload_id, principal.workspace_id)
    match = owned(db, Match, upload.match_id, principal.workspace_id)
    require_upload_rights(db, principal, match)
    if upload.status != "probed":
        fail(409, "video_unvalidated", "This video has not passed local media validation.")
    stream = open_private(request.app.state.settings, upload.storage_key)
    size = os.fstat(stream.fileno()).st_size
    if size != upload.size_bytes or size <= 0:
        stream.close()
        fail(409, "video_changed", "Private video integrity differs from its upload record.")
    try:
        start, end, partial = byte_range(request.headers.get("range"), size)
    except ValueError:
        stream.close()
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes", "Cache-Control": "no-store"})
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1), "Cache-Control": "no-store", "Content-Disposition": "inline"}
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if request.method == "HEAD":
        stream.close()
        return Response(status_code=206 if partial else 200, media_type=upload.content_type, headers=headers)

    def chunks():
        try:
            stream.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = stream.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            stream.close()
    return StreamingResponse(chunks(), status_code=206 if partial else 200, media_type=upload.content_type, headers=headers, background=BackgroundTask(stream.close))


@router.get("/jobs", response_model=list[JobRead])
def list_jobs(mode: Mode = "real", principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    jobs = bounded(db, select(ProcessingJob).where(ProcessingJob.workspace_id == principal.workspace_id, ProcessingJob.is_synthetic == (mode == "demo")).order_by(ProcessingJob.created_at.desc(), ProcessingJob.id))
    return [public_job(job, db) for job in jobs]


@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: str, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    return public_job(owned(db, ProcessingJob, job_id, principal.workspace_id), db)


@router.post("/jobs/{job_id}/retry", response_model=JobRead)
def retry_job(job_id: str, request: Request, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    job = owned(db, ProcessingJob, job_id, principal.workspace_id)
    upload = owned(db, Upload, job.upload_id, principal.workspace_id)
    match = owned(db, Match, job.match_id, principal.workspace_id)
    require_upload_rights(db, principal, match)
    if upload.match_id != match.id or upload.is_synthetic != job.is_synthetic or match.is_synthetic != job.is_synthetic:
        fail(409, "job_relationship", "This job's evidence relationships are inconsistent.")
    with open_private(request.app.state.settings, upload.storage_key):
        pass
    saved = {key: value for key, value in job.result_json.items() if key != "error_code"}
    changed = db.execute(update(ProcessingJob).where(ProcessingJob.id == job.id, ProcessingJob.workspace_id == principal.workspace_id, ProcessingJob.status.in_(["failed", "needs_configuration"]), ProcessingJob.attempts < request.app.state.settings.max_job_attempts).values(status="queued", stage="retry_queued", progress=0, message="Queued for a bounded retry", worker_id=None, lease_expires_at=None, started_at=None, finished_at=None, updated_at=utcnow(), result_json=saved))
    if cast(CursorResult[Any], changed).rowcount != 1:
        fail(409, "retry_unavailable", "Only failed or configuration-needed jobs below the attempt limit can be retried.")
    db.commit()
    db.refresh(job)
    return public_job(job, db)


@router.get("/jobs/{job_id}/chunks", response_model=ProcessingChunkPage)
def chunk_page(job_id: str, cursor: int = Query(default=-1, ge=-1, le=1440), limit: int = Query(default=50, ge=1, le=100), principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    job = owned(db, ProcessingJob, job_id, principal.workspace_id)
    rows = list(db.scalars(select(ProcessingChunk).where(ProcessingChunk.workspace_id == principal.workspace_id, ProcessingChunk.job_id == job.id, ProcessingChunk.upload_id == job.upload_id, ProcessingChunk.match_id == job.match_id, ProcessingChunk.is_synthetic == job.is_synthetic, ProcessingChunk.sequence > cursor).order_by(ProcessingChunk.sequence).limit(limit+1)))
    items = [ProcessingChunkRead.model_validate(row) for row in rows[:limit]]
    for item in items:
        item.message = "Completed sampled source segment." if item.status == "completed" else "Segment awaiting processing or operator review."
        if item.error_code:
            item.error_code = "segment_failed"
    return ProcessingChunkPage(items=items, next_cursor=str(items[-1].sequence) if len(rows) > limit else None)


def observation_statement(db: Session, principal: Principal, job: ProcessingJob):
    match = owned(db, Match, job.match_id, principal.workspace_id)
    require_upload_rights(db, principal, match)
    chunks = select(ProcessingChunk.id).where(ProcessingChunk.workspace_id == principal.workspace_id, ProcessingChunk.job_id == job.id, ProcessingChunk.upload_id == job.upload_id, ProcessingChunk.match_id == job.match_id, ProcessingChunk.is_synthetic == job.is_synthetic, ProcessingChunk.status == "completed")
    statement = select(TrackObservation).where(TrackObservation.workspace_id == principal.workspace_id, TrackObservation.is_synthetic == job.is_synthetic, TrackObservation.match_id == job.match_id, TrackObservation.upload_id == job.upload_id)
    has_chunks = db.scalar(chunks.limit(1))
    if has_chunks:
        statement = statement.where(TrackObservation.processing_chunk_id.in_(chunks))
    else:
        jobs = db.scalar(select(func.count()).select_from(ProcessingJob).where(ProcessingJob.workspace_id == principal.workspace_id, ProcessingJob.upload_id == job.upload_id))
        if jobs != 1 or job.status not in {"needs_review", "completed"}:
            return None
        statement = statement.where(TrackObservation.processing_chunk_id.is_(None))
    return statement


@router.get("/jobs/{job_id}/observations", response_model=ObservationPage)
def observation_page(job_id: str, cursor: str = Query(default="", max_length=36), limit: int = Query(default=100, ge=1, le=500), track_id: str | None = Query(default=None, max_length=80), chunk_id: str | None = Query(default=None, max_length=36), principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    job = owned(db, ProcessingJob, job_id, principal.workspace_id)
    statement = observation_statement(db, principal, job)
    if statement is None:
        return ObservationPage()
    statement = statement.where(TrackObservation.id > cursor)
    if track_id:
        statement = statement.where(TrackObservation.track_id == track_id)
    if chunk_id:
        statement = statement.where(TrackObservation.processing_chunk_id == chunk_id)
    rows = list(db.scalars(statement.order_by(TrackObservation.id).limit(limit+1)))
    return ObservationPage(items=[ObservationRead.model_validate(row) for row in rows[:limit]], next_cursor=rows[limit-1].id if len(rows) > limit else None)


@router.get("/jobs/{job_id}/timeline", response_model=TimelineWindow)
def timeline_window(job_id: str, start: float = Query(ge=0, le=14400, allow_inf_nan=False), end: float = Query(gt=0, le=14400, allow_inf_nan=False), principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    job = owned(db, ProcessingJob, job_id, principal.workspace_id)
    statement = observation_statement(db, principal, job)
    if not 0 < end - start <= 30:
        fail(422, "timeline_interval", "Request a positive source window of at most 30 seconds.")
    result = TimelineWindow(job_id=job.id, upload_id=job.upload_id, start_seconds=start, end_seconds=end)
    segments = db.execute(select(ProcessingChunk.start_seconds, ProcessingChunk.end_seconds, ProcessingChunk.status).where(ProcessingChunk.workspace_id == principal.workspace_id, ProcessingChunk.job_id == job.id, ProcessingChunk.upload_id == job.upload_id, ProcessingChunk.match_id == job.match_id, ProcessingChunk.is_synthetic == job.is_synthetic).order_by(ProcessingChunk.sequence).limit(1440))
    result.segments = [TimelineSegment(start_seconds=a, end_seconds=b, status=status) for a, b, status in segments]
    if statement is not None:
        rows = list(db.scalars(statement.where(TrackObservation.timestamp_seconds >= start, TrackObservation.timestamp_seconds < end).order_by(TrackObservation.timestamp_seconds, TrackObservation.id).limit(5001)))
        result.truncated = len(rows) > 5000
        result.items = [ObservationRead.model_validate(row) for row in rows[:5000]]
    return result
