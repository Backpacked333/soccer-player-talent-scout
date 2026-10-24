"""Scoped persistence helpers and bounded local media validation."""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
from datetime import timedelta, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import Principal, fail
from app.config import Settings
from app.db import utcnow
from app.media_geometry import display_geometry
from app.models import Appearance, AuditLog, Match, RightsAttestation, Upload
from app.schemas import EventWrite, JobRead, VideoMetadata

LIST_LIMIT = 1000
EVENT_LIMIT = 10000
STORAGE_KEY = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(mp4|mov|webm)$")
MIME_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}


def owned(db: Session, model, identifier: str, workspace_id: str):
    row = db.scalar(select(model).where(model.id == identifier, model.workspace_id == workspace_id))
    if row is None:
        fail(404, "not_found", "This record was not found in your workspace.")
    return row


def bounded(db: Session, statement, limit: int = LIST_LIMIT):
    rows = list(db.scalars(statement.limit(limit + 1)))
    if len(rows) > limit:
        fail(409, "collection_limit", "This collection exceeds the current workspace limit. Narrow your selection.")
    return rows


def capacity(db: Session, model, workspace_id: str, limit: int = LIST_LIMIT) -> None:
    if (db.scalar(select(func.count()).select_from(model).where(model.workspace_id == workspace_id)) or 0) >= limit:
        fail(409, "workspace_limit", "The current workspace record limit has been reached.")


def audit(db: Session, principal: Principal, match: Match, entity_type: str, entity_id: str, action: str, before: dict | None, after: dict | None, reason: str = "") -> None:
    db.add(AuditLog(workspace_id=principal.workspace_id, account_id=principal.account.id, match_id=match.id, is_synthetic=match.is_synthetic, entity_type=entity_type, entity_id=entity_id, action=action, before_json=before, after_json=after, reason=reason[:1000]))


def check_retention(upload: Upload, rights: RightsAttestation) -> None:
    created = upload.created_at if upload.created_at.tzinfo else upload.created_at.replace(tzinfo=timezone.utc)
    if created + timedelta(days=rights.retention_days) <= utcnow():
        fail(410, "retention_expired", "The video's declared retention period expired. Delete the match to remove its private footage and evidence; physical deletion is not automatic.")


def validate_event(db: Session, principal: Principal, match: Match, data: EventWrite) -> None:
    roster = {row.player_id: row for row in db.scalars(select(Appearance).where(Appearance.workspace_id == principal.workspace_id, Appearance.match_id == match.id, Appearance.is_synthetic == match.is_synthetic))}
    if data.player_id not in roster or (data.opponent_player_id and data.opponent_player_id not in roster):
        fail(404, "roster_actor_not_found", "Every event actor must belong to this match's authorized roster.")
    if data.opponent_player_id and roster[data.player_id].side == roster[data.opponent_player_id].side:
        fail(422, "opponent_side", "An opponent must be on the other side of this match's roster.")
    duration = match.duration_seconds
    if not match.is_synthetic:
        upload = db.scalar(select(Upload).where(Upload.workspace_id == principal.workspace_id, Upload.match_id == match.id, Upload.is_synthetic.is_(False)))
        if upload is None or upload.status != "probed" or not upload.duration_seconds:
            fail(409, "video_required", "Real evidence requires a validated private video upload first.")
        rights = db.scalar(select(RightsAttestation).where(RightsAttestation.workspace_id == principal.workspace_id, RightsAttestation.match_id == match.id, RightsAttestation.is_synthetic.is_(False)))
        if not rights or not all((rights.permission_to_process, rights.guardian_or_club_authorization, rights.opponent_privacy_review, rights.authorized_roster)):
            fail(409, "attestation_required", "Current footage permission declarations are required to review real events.")
        check_retention(upload, rights)
        duration = upload.duration_seconds
    if data.timestamp_seconds > duration or (data.end_timestamp_seconds is not None and data.end_timestamp_seconds > duration):
        fail(422, "timestamp_out_of_bounds", "Event timestamps must be within the source video's duration.")


def private_path(settings: Settings, storage_key: str) -> Path:
    if not STORAGE_KEY.fullmatch(storage_key):
        fail(404, "media_unavailable", "Private media is unavailable.")
    root = settings.storage_root
    path = root / storage_key
    if root.is_symlink() or path.is_symlink() or path.resolve().parent != root.resolve():
        fail(404, "media_unavailable", "Private media is unavailable.")
    return path


def open_private(settings: Settings, storage_key: str):
    path = private_path(settings, storage_key)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        fail(404, "media_unavailable", "Private media is unavailable.")
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        fail(404, "media_unavailable", "Private media is unavailable.")
    return os.fdopen(descriptor, "rb")


def validate_filename(filename: str | None, content_type: str | None) -> tuple[str, str]:
    name = filename or ""
    if not name or len(name) > 180 or name.strip() != name or any(ord(c) < 32 or ord(c) == 127 for c in name) or any(c in name for c in ("/", "\\", ":")) or name in {".", ".."}:
        fail(422, "invalid_filename", "Use a plain MP4, MOV or WebM filename without paths or control characters.")
    suffix = Path(name).suffix.lower()
    if suffix not in MIME_TYPES or content_type != MIME_TYPES[suffix]:
        fail(415, "unsupported_media", "Upload an MP4, MOV or WebM with its matching video media type.")
    return name, suffix


def probe_upload(path: Path, settings: Settings) -> VideoMetadata:
    """Validate containers before queuing; metadata is never player analysis.

    No network protocols/playlist demuxers, shell, or unbounded JSON reads.
    """
    with open_private(settings, path.name) as stream:
        header = stream.read(16)
    valid_magic = header.startswith(b"\x1aE\xdf\xa3") if path.suffix == ".webm" else len(header) >= 12 and header[4:8] == b"ftyp"
    if not valid_magic:
        fail(422, "invalid_video", "The file content is not a supported video container.")
    command = [settings.ffprobe_binary, "-v", "error", "-protocol_whitelist", "file", "-format_whitelist", "mov,matroska,webm", "-max_alloc", "67108864", "-probesize", "10000000", "-analyzeduration", "10000000", "-show_streams", "-show_format", "-of", "json"]
    try:
        with open_private(settings, path.name) as media, tempfile.TemporaryFile() as output:
            command += ["-i", f"/proc/self/fd/{media.fileno()}"]
            completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL, pass_fds=(media.fileno(),), timeout=settings.probe_timeout_seconds, check=False, env={"PATH": os.defpath, "LANG": "C.UTF-8"})
            output.seek(0)
            raw = output.read(65537)
        if completed.returncode != 0 or len(raw) > 65536:
            raise ValueError("invalid probe")
        payload = json.loads(raw)
        streams = payload.get("streams", [])
        video = [item for item in streams if item.get("codec_type") == "video"]
        audio = [item for item in streams if item.get("codec_type") == "audio"]
        if len(video) != 1 or len(audio) > 1 or len(streams) != len(video) + len(audio):
            raise ValueError("unsupported streams")
        format_names = set(payload["format"]["format_name"].split(","))
        if path.suffix == ".webm" and not format_names.intersection({"webm", "matroska"}):
            raise ValueError("container mismatch")
        if path.suffix != ".webm" and "mov" not in format_names:
            raise ValueError("container mismatch")
        metadata = VideoMetadata(duration_seconds=float(payload["format"]["duration"]), width=video[0]["width"], height=video[0]["height"], fps=float(Fraction(video[0]["avg_frame_rate"])), codec=video[0]["codec_name"], display_geometry=display_geometry(video[0]))
        if metadata.duration_seconds > settings.max_video_seconds or max(metadata.width, metadata.height) > settings.max_video_dimension:
            raise ValueError("configured limits")
        allowed_codecs = {"vp8", "vp9", "av1"} if path.suffix == ".webm" else {"h264", "hevc", "av1", "mpeg4"}
        if metadata.codec not in allowed_codecs:
            raise ValueError("unsupported video codec")
        return metadata
    except FileNotFoundError:
        fail(503, "probe_unavailable", "Local video validation is not configured. Ask the operator to install ffprobe.")
    except subprocess.TimeoutExpired:
        fail(422, "probe_timeout", "Video validation timed out. Use a shorter, supported video.")
    except (OSError, ValueError, TypeError, KeyError, ZeroDivisionError):
        fail(422, "invalid_video", "The video is invalid, unsupported or exceeds configured duration/dimension limits.")


def public_job(job, db: Session | None = None) -> JobRead:
    """Never leak arbitrary worker internals or filesystem paths in JSON output."""
    value = JobRead.model_validate(job)
    source = job.result_json if isinstance(job.result_json, dict) else {}
    result: dict[str, Any] = {}
    if isinstance(source.get("metadata"), dict):
        try:
            result["metadata"] = VideoMetadata.model_validate(source["metadata"]).model_dump()
        except ValueError:
            pass
    for key in ("candidate_count", "observation_count", "unresolved_candidate_count", "persisted_candidate_count", "persisted_observation_count"):
        number = source.get(key)
        if isinstance(number, int) and not isinstance(number, bool) and 0 <= number <= 72000000:
            result[key] = number
    result["analysis_available"] = False  # No automatic approved player statistics.
    result["automated_evidence_available"] = bool(result.get("candidate_count", 0) or result.get("observation_count", 0))
    if source.get("analysis_kind") in {"metadata_only", "anonymous_detection"}:
        result["analysis_kind"] = source["analysis_kind"]
    if source.get("identity_status") == "unassigned":
        result["identity_status"] = "unassigned"
    if isinstance(source.get("unresolved_candidates"), list):
        result["unresolved_candidate_count"] = min(len(source["unresolved_candidates"]), 10000)
    if db is not None:
        from app.models import ProcessingChunk
        from app.processing_contracts import ProcessingCoverage
        from app.fullmatch import coverage
        chunks = bounded(db, select(ProcessingChunk).where(ProcessingChunk.workspace_id == job.workspace_id, ProcessingChunk.is_synthetic == job.is_synthetic, ProcessingChunk.job_id == job.id, ProcessingChunk.upload_id == job.upload_id, ProcessingChunk.match_id == job.match_id).order_by(ProcessingChunk.sequence), 1440)
        upload = db.scalar(select(Upload).where(Upload.id == job.upload_id, Upload.workspace_id == job.workspace_id, Upload.is_synthetic == job.is_synthetic))
        duration = upload.duration_seconds if upload else None
        if chunks and duration:
            valid = chunks[0].start_seconds == 0 and chunks[-1].end_seconds == duration and all(row.sequence == index and (index == 0 or chunks[index-1].end_seconds == row.start_seconds) and row.sample_interval_seconds == chunks[0].sample_interval_seconds for index, row in enumerate(chunks))
            if valid:
                value.processing_coverage = coverage(chunks, duration)
                result["observation_count"] = value.processing_coverage.observation_count
                result["automated_evidence_available"] = bool(value.processing_coverage.observation_count)
            else:
                value.processing_coverage = ProcessingCoverage(status="unavailable", source_duration_seconds=duration)
        else:
            value.processing_coverage = ProcessingCoverage(status="metadata_only" if duration and job.status not in {"queued", "running"} else "not_started", source_duration_seconds=duration)
    value.result = result
    known_stages = {"queued", "probing", "metadata_ready", "processing", "needs_review", "completed", "failed", "retry_queued", "validating", "initializing", "inference", "persisting", "anonymous_tracks_ready", "detector_configuration", "probe_and_optional_detection", "persisting_evidence"}
    value.stage = job.stage if job.stage in known_stages else job.status
    messages = {"queued": "Waiting for private processing.", "running": "Private processing is in progress.", "needs_configuration": "Video metadata is ready. Validated player/event inference is not configured; use manual review.", "needs_review": "Candidate evidence needs human review. Unapproved candidates do not count in statistics.", "completed": "The configured processing stage completed. Only approved events count in statistics.", "failed": "Processing could not complete. Check video format and operator configuration before retrying."}
    value.message = messages[job.status]
    return value
