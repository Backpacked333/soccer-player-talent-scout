"""Authorized coherent database snapshots and operator-only model activation.

No incremental cache: current corrections rebuild history and invalidate artifacts.
The separate read transaction pins ALL queries (including SQLite SELECTs) to one
snapshot. Match write fences are used separately by identity/event writers.
"""
from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal, fail
from app.config import Settings
from app.db import get_engine, utcnow
from app.intelligence.action_value import ELIGIBLE, load_artifact_json
from app.intelligence.contracts import (
    ActionValueArtifact, CohortScope, EvidenceAppearance, EvidenceMatch,
    EvidenceSnapshot, ReviewedAction, StreamDeclaration, canonical_fingerprint, utc_timestamp,
)
from app.intelligence.identity import IdentityError, TrackIdentityService, identity_record
from app.models import Appearance, AuditLog, Match, Player, ProcessingChunk, ProcessingJob, ReviewedEvent, RightsAttestation, TrackIdentityAssignment, Upload
from app.services import bounded


@contextmanager
def evidence_read(settings: Settings) -> Iterator[Session]:
    """Fresh read-only application transaction; never use an auth transaction's cache."""
    engine = get_engine(settings.database_url)
    with engine.connect() as connection:
        if engine.dialect.name == "postgresql":
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin():
            if engine.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN")  # sqlite legacy SELECT does not BEGIN
            elif engine.dialect.name == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection, autoflush=False) as session:
                yield session


def identity_service(db: Session, principal: Principal, match: Match) -> TrackIdentityService:
    return TrackIdentityService(db, workspace_id=principal.workspace_id, mode="demo" if match.is_synthetic else "real", reviewer_id=principal.account.id)


def identity_failure(error: IdentityError) -> None:
    fail(error.status_code, error.code, str(error))


def source_signature(db: Session, match: Match) -> str:
    """Bind completeness to source, rights, roster AND exact eligible-action rows."""
    def columns(row):
        if row is None:
            return None
        return {col.name: str(getattr(row, col.name)) for col in row.__table__.columns if col.name not in {"storage_key", "original_filename"}}
    roster = bounded(db, select(Appearance).where(Appearance.workspace_id == match.workspace_id, Appearance.match_id == match.id).order_by(Appearance.id), 100)
    source = db.scalar(select(Upload).where(Upload.workspace_id == match.workspace_id, Upload.match_id == match.id))
    rights = db.scalar(select(RightsAttestation).where(RightsAttestation.workspace_id == match.workspace_id, RightsAttestation.match_id == match.id))
    # Removing a failed attempt or editing an action must require a fresh
    # completeness declaration, not just refitting a newly biased denominator.
    # Including raw eligible rows also invalidates pre-v2 declarations safely.
    events = bounded(db, select(ReviewedEvent).where(ReviewedEvent.workspace_id == match.workspace_id, ReviewedEvent.is_synthetic == match.is_synthetic, ReviewedEvent.match_id == match.id, ReviewedEvent.kind.in_(ELIGIBLE)).order_by(ReviewedEvent.id), 10000)
    return canonical_fingerprint({"declaration_version": 2, "match": columns(match), "roster": [columns(row) for row in roster], "upload": columns(source), "rights": columns(rights), "eligible_actions": [columns(row) for row in events]})


def current_stream(db: Session, match: Match) -> StreamDeclaration:
    row = db.scalar(select(AuditLog).where(AuditLog.workspace_id == match.workspace_id, AuditLog.is_synthetic == match.is_synthetic, AuditLog.match_id == match.id, AuditLog.entity_type == "action_stream").order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(1))
    if row and row.after_json and row.after_json.get("source_signature") == source_signature(db, match):
        try:
            return StreamDeclaration.model_validate(row.after_json["declaration"])
        except (ValueError, KeyError):
            pass
    return StreamDeclaration()


def load_snapshot(db: Session, scope: CohortScope, *, as_of: date | None = None) -> EvidenceSnapshot:
    synthetic = scope.mode == "demo"
    cutoff = min(as_of or utcnow().date(), utcnow().date())
    matches = bounded(db, select(Match).where(Match.workspace_id == scope.workspace_id, Match.is_synthetic == synthetic, Match.season == scope.season, Match.age_group == scope.age_group, Match.competition == scope.competition, Match.match_date <= cutoff).order_by(Match.id), 1000)
    ids = [row.id for row in matches]
    def rows(model, maximum=10000):
        return bounded(db, select(model).where(model.workspace_id == scope.workspace_id, model.is_synthetic == synthetic, model.match_id.in_(ids)).order_by(model.id), maximum)
    appearances = rows(Appearance)
    player_ids = set(db.scalars(select(Player.id).where(Player.workspace_id == scope.workspace_id, Player.is_synthetic == synthetic)))
    appearances = [row for row in appearances if row.player_id in player_ids]
    roster = {(row.match_id, row.player_id): row for row in appearances}
    uploads = {row.match_id: row for row in rows(Upload, 1000)}
    rights = {row.match_id: row for row in rows(RightsAttestation, 1000)}
    jobs = rows(ProcessingJob, 10000)
    latest = {}
    for job in sorted(jobs, key=lambda row: (row.created_at, row.id)):
        latest[job.match_id] = job
    assignments = rows(TrackIdentityAssignment)
    chunk_ids = {row.track_id.split(":")[1] for row in assignments if row.track_id.startswith("chunk:")}
    chunks = {row.id: row for row in bounded(db, select(ProcessingChunk).where(ProcessingChunk.workspace_id == scope.workspace_id, ProcessingChunk.is_synthetic == synthetic, ProcessingChunk.id.in_(chunk_ids)), 10000)}
    identities = []
    for row in assignments:
        upload, permission, job = uploads.get(row.match_id), rights.get(row.match_id), latest.get(row.match_id)
        if (row.match_id, row.player_id) not in roster or not upload or row.upload_id != upload.id or upload.status != "probed" or not job or job.upload_id != upload.id:
            continue
        if not permission or not all((permission.permission_to_process, permission.guardian_or_club_authorization, permission.opponent_privacy_review, permission.authorized_roster)) or utc_timestamp(upload.created_at) + timedelta(days=permission.retention_days) <= utcnow():
            continue
        if row.track_id.startswith("chunk:"):
            chunk = chunks.get(row.track_id.split(":")[1])
            if not chunk or chunk.job_id != job.id or chunk.status != "completed" or row.start_seconds < chunk.start_seconds or row.end_seconds >= chunk.end_seconds:
                continue
        elif len([item for item in jobs if item.upload_id == upload.id]) != 1 or job.status not in {"needs_review", "completed"}:
            continue
        identities.append(identity_record(row))
    events = []
    incomplete_streams: set[str] = set()
    for row in rows(ReviewedEvent):
        # An invalid eligible row may be omitted from descriptive memory, but
        # MUST NOT silently turn a declared complete stream into a success-only
        # training sample. Keep the source record; withdraw completeness.
        def mark_incomplete():
            if row.kind in ELIGIBLE and row.status != "rejected":
                incomplete_streams.add(row.match_id)
        actor, opponent = roster.get((row.match_id, row.player_id)), roster.get((row.match_id, row.opponent_player_id))
        if actor is None or (row.opponent_player_id and (opponent is None or opponent.side == actor.side)):
            mark_incomplete()
            continue
        # Orphan links must NEVER become unlinked manual evidence.
        if row.identity_assignment_id is None and row.identity_assignment_revision is not None:
            mark_incomplete()
            continue
        values = {key: getattr(row, key) for key in ReviewedAction.model_fields if hasattr(row, key)}
        values.update(event_id=row.id, mode=scope.mode)
        try:
            events.append(ReviewedAction.model_validate(values))
        except ValueError:
            # Invalid out-of-band rows fail closed rather than poison other records.
            mark_incomplete()
            continue
    return EvidenceSnapshot(scope=scope,
        matches=[EvidenceMatch(match_id=row.id, workspace_id=row.workspace_id, mode=scope.mode, match_date=row.match_date, season=row.season, age_group=row.age_group, competition=row.competition, duration_seconds=max(row.duration_seconds, uploads[row.id].duration_seconds or 0) if row.id in uploads else row.duration_seconds, stream=StreamDeclaration() if row.id in incomplete_streams else current_stream(db, row)) for row in matches],
        appearances=[EvidenceAppearance(appearance_id=row.id, match_id=row.match_id, player_id=row.player_id, workspace_id=row.workspace_id, mode=scope.mode, side=row.side, role=row.role, jersey_number=row.jersey_number, played_seconds=row.played_seconds, reviewed_seconds=row.reviewed_seconds, roster_authorized=True) for row in appearances],
        events=events, identities=identities)


def operator_artifact(settings: Settings, scope: CohortScope) -> tuple[ActionValueArtifact | None, str]:
    """One operator path, never request-controlled; validate before revealing ANY metadata."""
    path = settings.action_value_artifact_path
    if path is None:
        return None, "No operator-activated spatial model; xT-style values unavailable."
    try:
        if path.is_symlink() or settings.storage_root == path.resolve() or settings.storage_root in path.resolve().parents:
            raise ValueError("unsafe artifact location")
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024 * 1024:
                raise ValueError("bounded regular JSON required")
            artifact = load_artifact_json(stream.read(8 * 1024 * 1024 + 1), authorized_scope=scope)
        return artifact, "Operator-activated unvalidated research model; current source evidence must still match."
    except (OSError, ValueError):
        return None, "No compatible authorized spatial model for this exact workspace/mode/cohort, or operator artifact unavailable."
