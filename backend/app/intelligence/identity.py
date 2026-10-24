"""Reviewer-only canonical track attribution, with transactional correction fences.

This service never identifies faces, reads jerseys from pixels, assigns raw
observations, approves actions, or commits the caller's transaction. Every write
locks the match before checking conflicts, and records its audit in the same
savepoint. Callers must commit on success / roll back on failure. HTTP handlers
must supply the authenticated account/workspace, never request-body principals.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal, cast

from pydantic import Field
from sqlalchemy import CursorResult, func, or_, select, update
from sqlalchemy.orm import Session

from app.db import utcnow
from app.intelligence.contracts import (
    Identifier, IntelligenceContract, Mode, TrackIdentityCorrection,
    TrackIdentityRecord, TrackIdentityWrite, utc_timestamp,
)
from app.models import (
    Account, Appearance, AuditLog, Match, Player, ProcessingChunk, ProcessingJob,
    ReviewedEvent, RightsAttestation, TrackIdentityAssignment, TrackObservation, Upload,
)

MAX_IDENTITIES = 10000
MAX_LINKED_EVENTS = 10000
MAX_ROSTER = 100
MAX_TRACK_OBSERVATIONS = 50000


class IdentityError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class RosterCandidate(IntelligenceContract):
    player_id: Identifier
    appearance_id: Identifier
    side: Literal["home", "away"]
    role: str
    jersey_number: int | None
    # Deliberately no probability: an authorized roster is not visual evidence.
    evidence: str = "Authorized match roster only; requires a reviewer inspecting the private footage."


class IdentityCandidates(IntelligenceContract):
    match_id: Identifier
    upload_id: Identifier
    track_id: str
    observation_count: int = Field(ge=1, le=MAX_TRACK_OBSERVATIONS)
    candidates: list[RosterCandidate] = Field(max_length=MAX_ROSTER)
    status: Literal["unresolved"] = "unresolved"
    limitations: list[str] = Field(default_factory=lambda: [
        "No automated jersey/team recognition or face identification is available.",
        "Optional number/side filters are reviewer-supplied hints, not detector evidence.",
        "Even a single candidate is not a confirmed identity; jersey numbers do not link games.",
        "Sampled track support does not prove continuous visibility or unchanged identity between samples.",
    ])


def identity_record(row: TrackIdentityAssignment) -> TrackIdentityRecord:
    return TrackIdentityRecord(
        id=row.id, workspace_id=row.workspace_id, mode="demo" if row.is_synthetic else "real",
        match_id=row.match_id, upload_id=row.upload_id, track_id=row.track_id,
        start_seconds=row.start_seconds, end_seconds=row.end_seconds, player_id=row.player_id,
        status=row.status, source=row.source, confidence=row.confidence, reason=row.reason,
        revision=row.revision, created_by=row.created_by, created_at=utc_timestamp(row.created_at),
        reviewed_by=row.reviewed_by,
        reviewed_at=utc_timestamp(row.reviewed_at) if row.reviewed_at else None,
        updated_at=utc_timestamp(row.updated_at),
    )


def _event_state(row: ReviewedEvent) -> dict:
    """Full column snapshot makes reattribution decisions inspectable in the audit."""
    result = {}
    for column in ReviewedEvent.__table__.columns:
        value = getattr(row, column.name)
        result[column.name] = value.isoformat() if hasattr(value, "isoformat") else value
    return result


class TrackIdentityService:
    def __init__(self, db: Session, *, workspace_id: str, mode: Mode, reviewer_id: str):
        if mode not in {"real", "demo"}:
            raise IdentityError("invalid_mode", "Explicit real/demo scope is required.", 422)
        self.db, self.workspace_id, self.mode, self.reviewer_id = db, workspace_id, mode, reviewer_id
        self.synthetic = mode == "demo"

    def _scope(self, model):
        return (model.workspace_id == self.workspace_id, model.is_synthetic == self.synthetic)

    def _account(self) -> None:
        account = self.db.scalar(select(Account.id).where(Account.id == self.reviewer_id, Account.workspace_id == self.workspace_id, Account.is_active.is_(True)))
        if account is None:
            raise IdentityError("reviewer_not_found", "An active reviewer in this workspace is required.", 404)

    def _match(self, match_id: str, *, lock: bool = False) -> Match:
        if lock:
            # A harmless UPDATE obtains a real SQLite write transaction and a
            # PostgreSQL row lock before the read/conflict checks. A SELECT FOR
            # UPDATE alone is not a SQLite concurrency fence. Do this BEFORE
            # begin_nested: releasing a first SQLite SAVEPOINT can otherwise
            # commit a transaction the caller thought was still uncommitted.
            changed = self.db.execute(update(Match).where(Match.id == match_id, *self._scope(Match)).values(title=Match.title).execution_options(synchronize_session=False))
            if cast(CursorResult[Any], changed).rowcount != 1:
                raise IdentityError("not_found", "Match not found in this scope.", 404)
        self._account()
        match = self.db.scalar(select(Match).where(Match.id == match_id, *self._scope(Match)).execution_options(populate_existing=True))
        if match is None:
            raise IdentityError("not_found", "Match not found in this scope.", 404)
        return match

    def _bounded(self, statement, maximum: int):
        rows = list(self.db.scalars(statement.limit(maximum + 1)))
        if len(rows) > maximum:
            raise IdentityError("collection_limit", "Evidence exceeds the bounded review limit; narrow the selection.")
        return rows

    def _roster(self, match: Match) -> list[Appearance]:
        return self._bounded(select(Appearance).join(Player, Player.id == Appearance.player_id).where(
            Appearance.match_id == match.id, *self._scope(Appearance), *self._scope(Player),
        ).order_by(Appearance.player_id), MAX_ROSTER)

    def _target(self, match: Match, player_id: str) -> None:
        if not any(row.player_id == player_id for row in self._roster(match)):
            raise IdentityError("roster_actor_not_found", "Target must be a canonical player on this match's scoped roster.", 404)

    def _observed(self, match: Match, data: TrackIdentityWrite) -> int:
        upload = self.db.scalar(select(Upload).where(Upload.id == data.upload_id, Upload.match_id == match.id, *self._scope(Upload)).execution_options(populate_existing=True))
        if upload is None or upload.status != "probed" or upload.duration_seconds is None:
            raise IdentityError("source_unavailable", "The current validated private upload is required.")
        if data.end_seconds > min(upload.duration_seconds, match.duration_seconds):
            raise IdentityError("timestamp_out_of_bounds", "Identity interval exceeds the source or match duration.", 422)
        rights = self.db.scalar(select(RightsAttestation).where(RightsAttestation.match_id == match.id, *self._scope(RightsAttestation)).execution_options(populate_existing=True))
        if rights is None or not all((rights.permission_to_process, rights.guardian_or_club_authorization, rights.opponent_privacy_review, rights.authorized_roster)):
            raise IdentityError("attestation_required", "Current footage and roster permissions are required.")
        if utc_timestamp(upload.created_at) + timedelta(days=rights.retention_days) <= utcnow():
            raise IdentityError("retention_expired", "The private upload's declared retention has expired.", 410)
        jobs = self._bounded(select(ProcessingJob).where(ProcessingJob.upload_id == upload.id, ProcessingJob.match_id == match.id, *self._scope(ProcessingJob)).order_by(ProcessingJob.created_at.desc(), ProcessingJob.id.desc()), 1000)
        if not jobs:
            raise IdentityError("track_source_unverified", "No processing source for these observations is available.")
        chunk_id = None
        if data.track_id.startswith("chunk:"):
            chunk_id = data.track_id.split(":")[1]
            chunk = self.db.scalar(select(ProcessingChunk).where(ProcessingChunk.id == chunk_id, ProcessingChunk.match_id == match.id, ProcessingChunk.upload_id == upload.id, *self._scope(ProcessingChunk)).execution_options(populate_existing=True))
            if chunk is None or chunk.status != "completed" or chunk.job_id != jobs[0].id:
                raise IdentityError("stale_track", "Only immutable completed chunks of the current job can be reviewed.")
            if data.start_seconds < chunk.start_seconds or data.end_seconds >= chunk.end_seconds:
                raise IdentityError("timestamp_out_of_bounds", "Track interval must be inside its absolute [start,end) chunk.", 422)
        elif len(jobs) != 1 or jobs[0].status not in {"needs_review", "completed"}:
            # Legacy observations have no job FK. Never guess which retried job
            # produced an unscoped tracker-local ID.
            raise IdentityError("stale_track", "Legacy tracks require a single finished job; retried/active sources are ambiguous.")
        base = (TrackObservation.match_id == match.id, TrackObservation.upload_id == upload.id, TrackObservation.track_id == data.track_id, *self._scope(TrackObservation))
        orphan = self.db.scalar(select(TrackObservation.id).where(*base, TrackObservation.processing_chunk_id.is_not(None) if chunk_id is None else or_(TrackObservation.processing_chunk_id.is_(None), TrackObservation.processing_chunk_id != chunk_id)).limit(1))
        if orphan:
            raise IdentityError("stale_track", "Observation provenance disagrees with its scoped track ID.")
        count, first, last = self.db.execute(select(func.count(), func.min(TrackObservation.timestamp_seconds), func.max(TrackObservation.timestamp_seconds)).where(*base, TrackObservation.processing_chunk_id == chunk_id, TrackObservation.label.in_(["player", "goalkeeper"]))).one()
        if not count or first is None or last is None:
            raise IdentityError("observations_required", "No player observations support this exact match/upload track.")
        if count > MAX_TRACK_OBSERVATIONS:
            raise IdentityError("collection_limit", "Track observation count exceeds the review bound.")
        if data.start_seconds < first or data.end_seconds > last:
            raise IdentityError("unobserved_interval", "Identity intervals cannot extrapolate beyond sampled track support.")
        within = self.db.scalar(select(func.count()).select_from(TrackObservation).where(*base, TrackObservation.label.in_(["player", "goalkeeper"]), TrackObservation.timestamp_seconds >= data.start_seconds, TrackObservation.timestamp_seconds <= data.end_seconds)) or 0
        if not within:
            raise IdentityError("observations_required", "No sampled player observation lies in this interval.")
        return within

    def _conflicts(self, match: Match, data: TrackIdentityWrite, *, ignore_id: str | None = None) -> None:
        statement = select(TrackIdentityAssignment.id).where(
            *self._scope(TrackIdentityAssignment), TrackIdentityAssignment.match_id == match.id,
            TrackIdentityAssignment.upload_id == data.upload_id, TrackIdentityAssignment.status == "confirmed",
            TrackIdentityAssignment.start_seconds <= data.end_seconds, TrackIdentityAssignment.end_seconds >= data.start_seconds,
            or_(TrackIdentityAssignment.track_id == data.track_id, TrackIdentityAssignment.player_id == data.player_id),
        )
        if ignore_id is not None:
            statement = statement.where(TrackIdentityAssignment.id != ignore_id)
        if self.db.scalar(statement.limit(1)) is not None:
            raise IdentityError("identity_overlap", "Overlapping confirmed track/player identities are ambiguous; unassign or narrow the earlier decision first.")

    def _assignment(self, match_id: str, assignment_id: str, revision: int | None = None) -> TrackIdentityAssignment:
        row = self.db.scalar(select(TrackIdentityAssignment).where(TrackIdentityAssignment.id == assignment_id, TrackIdentityAssignment.match_id == match_id, *self._scope(TrackIdentityAssignment)).execution_options(populate_existing=True))
        if row is None:
            raise IdentityError("not_found", "Identity decision not found in this scope.", 404)
        if revision is not None and row.revision != revision:
            raise IdentityError("stale_revision", "Identity changed; refresh the evidence before deciding again.")
        return row

    def _audit(self, match_id: str, entity_type: str, entity_id: str, action: str, before: dict | None, after: dict | None, reason: str) -> None:
        self.db.add(AuditLog(workspace_id=self.workspace_id, is_synthetic=self.synthetic, account_id=self.reviewer_id, match_id=match_id, entity_type=entity_type, entity_id=entity_id, action=action, before_json=before, after_json=after, reason=reason))

    def _invalidate_events(self, row: TrackIdentityAssignment, reason: str) -> None:
        events = self._bounded(select(ReviewedEvent).where(ReviewedEvent.identity_assignment_id == row.id, ReviewedEvent.match_id == row.match_id, *self._scope(ReviewedEvent)).order_by(ReviewedEvent.id), MAX_LINKED_EVENTS)
        for event in events:
            before = _event_state(event)
            # Preserve the old actor and linkage revision as evidence of what was
            # actually reviewed. Never transfer approval to a different child.
            event.status, event.reviewed_by, event.reviewed_at = "draft", None, None
            event.updated_at = utcnow()
            self._audit(row.match_id, "event", event.id, "identity_invalidated", before, _event_state(event), reason)

    def candidates(self, match_id: str, data: TrackIdentityWrite, *, jersey_number: int | None = None, side: Literal["home", "away"] | None = None) -> IdentityCandidates:
        """data.player_id is NOT used as a candidate filter or a match prediction."""
        match = self._match(match_id)
        data = TrackIdentityWrite.model_validate(data.model_dump())
        if jersey_number is not None and (isinstance(jersey_number, bool) or not 0 <= jersey_number <= 99):
            raise IdentityError("invalid_jersey", "Reviewer jersey hint must be within 0..99.", 422)
        if side is not None and side not in {"home", "away"}:
            raise IdentityError("invalid_side", "Reviewer side hint must be home or away.", 422)
        count = self._observed(match, data)
        roster = self._roster(match)
        candidates = [RosterCandidate(player_id=row.player_id, appearance_id=row.id, side=row.side, role=row.role, jersey_number=row.jersey_number) for row in roster if (jersey_number is None or row.jersey_number == jersey_number) and (side is None or row.side == side)]
        return IdentityCandidates(match_id=match.id, upload_id=data.upload_id, track_id=data.track_id, observation_count=count, candidates=candidates)

    def propose(self, match_id: str, data: TrackIdentityWrite) -> TrackIdentityRecord:
        data = TrackIdentityWrite.model_validate(data.model_dump())
        if data.status != "proposed":
            raise IdentityError("proposal_required", "Create an unresolved proposal, then explicitly confirm or reject it.", 422)
        match = self._match(match_id, lock=True)
        with self.db.begin_nested():
            self._target(match, data.player_id)
            self._observed(match, data)
            count = self.db.scalar(select(func.count()).select_from(TrackIdentityAssignment).where(TrackIdentityAssignment.match_id == match.id, *self._scope(TrackIdentityAssignment))) or 0
            if count >= MAX_IDENTITIES:
                raise IdentityError("collection_limit", "Match identity limit reached.")
            row = TrackIdentityAssignment(**data.model_dump(), workspace_id=self.workspace_id, is_synthetic=self.synthetic, match_id=match.id, created_by=self.reviewer_id)
            self.db.add(row)
            self.db.flush()
            result = identity_record(row)
            self._audit(match.id, "track_identity", row.id, "propose", None, result.model_dump(mode="json"), data.reason)
        return result

    def correct(self, match_id: str, assignment_id: str, data: TrackIdentityCorrection, *, action: str = "correct") -> TrackIdentityRecord:
        data = TrackIdentityCorrection.model_validate(data.model_dump())
        match = self._match(match_id, lock=True)
        with self.db.begin_nested():
            row = self._assignment(match_id, assignment_id, data.expected_revision)
            before = identity_record(row)
            value = TrackIdentityWrite(**(before.model_dump(include=set(TrackIdentityWrite.model_fields)) | {"player_id": data.player_id, "status": data.status, "reason": data.reason, "source": "reviewer", "confidence": None}))
            if data.status != "rejected":
                self._target(match, data.player_id)
                self._observed(match, value)
            elif data.player_id != row.player_id:
                raise IdentityError("reject_target_changed", "Reject the existing attribution without silently changing its target.", 422)
            if data.status == "confirmed":
                self._conflicts(match, value, ignore_id=row.id)
            self._invalidate_events(row, data.reason)
            row.player_id, row.status, row.reason = data.player_id, data.status, data.reason
            row.source, row.confidence = "reviewer", None
            row.reviewed_by = self.reviewer_id if data.status != "proposed" else None
            row.reviewed_at = utcnow() if row.reviewed_by else None
            row.updated_at = utcnow()
            self.db.flush()  # ORM revision CAS also protects stale identity maps.
            result = identity_record(row)
            self._audit(match_id, "track_identity", row.id, action, before.model_dump(mode="json"), result.model_dump(mode="json"), data.reason)
        return result

    def confirm(self, match_id: str, assignment_id: str, *, expected_revision: int, reason: str) -> TrackIdentityRecord:
        row = self._assignment(match_id, assignment_id)
        return self.correct(match_id, assignment_id, TrackIdentityCorrection(expected_revision=expected_revision, player_id=row.player_id, status="confirmed", reason=reason), action="confirm")

    def reject(self, match_id: str, assignment_id: str, *, expected_revision: int, reason: str) -> TrackIdentityRecord:
        row = self._assignment(match_id, assignment_id)
        return self.correct(match_id, assignment_id, TrackIdentityCorrection(expected_revision=expected_revision, player_id=row.player_id, status="rejected", reason=reason), action="reject")

    def unassign(self, match_id: str, assignment_id: str, *, expected_revision: int, reason: str) -> TrackIdentityRecord:
        """Soft rejection, not deletion: retain the old decision and its audit."""
        row = self._assignment(match_id, assignment_id)
        return self.correct(match_id, assignment_id, TrackIdentityCorrection(expected_revision=expected_revision, player_id=row.player_id, status="rejected", reason=reason), action="unassign")

    def reassign(self, match_id: str, assignment_id: str, *, expected_revision: int, player_id: str, reason: str) -> TrackIdentityRecord:
        return self.correct(match_id, assignment_id, TrackIdentityCorrection(expected_revision=expected_revision, player_id=player_id, status="confirmed", reason=reason), action="reassign")

    def lock_match(self, match_id: str) -> Match:
        """Fence related roster/source/event writes in the SAME transaction.

        On PostgreSQL all writers of attribution-relevant match evidence must
        participate in this match-row lock (SQLite serializes writes globally).
        Take the lock BEFORE reading/validating or mutating that evidence.
        """
        return self._match(match_id, lock=True)

    def assert_event_attribution(self, match_id: str, event: ReviewedEvent) -> TrackIdentityRecord | None:
        """Approval gate for callers updating linked events; never approves one.

        Pass the caller's current event state after its requested actor/time
        correction. This does not reload/overwrite that state. Manual events
        with no linkage remain manual and use the existing event validator.
        """
        match = self._match(match_id, lock=True)
        if (event.workspace_id, event.is_synthetic, event.match_id) != (self.workspace_id, self.synthetic, match_id):
            raise IdentityError("not_found", "Event is outside this scoped match.", 404)
        if event.identity_assignment_id is None:
            if event.identity_assignment_revision is not None:
                raise IdentityError("orphan_identity_link", "The source identity was removed; explicitly review and reattribute the event.")
            return None
        if event.identity_assignment_revision is None:
            raise IdentityError("orphan_identity_link", "The event has no reviewed identity revision.")
        row = self._assignment(match_id, event.identity_assignment_id, event.identity_assignment_revision)
        if row.status != "confirmed":
            raise IdentityError("identity_unconfirmed", "Linked events require a currently confirmed identity.")
        if event.player_id != row.player_id or event.timestamp_seconds < row.start_seconds or (event.end_timestamp_seconds if event.end_timestamp_seconds is not None else event.timestamp_seconds) > row.end_seconds:
            raise IdentityError("event_identity_mismatch", "The corrected actor/time is outside its reviewed identity interval.")
        self._target(match, row.player_id)
        record = identity_record(row)
        value = TrackIdentityWrite(**record.model_dump(include=set(TrackIdentityWrite.model_fields)))
        self._observed(match, value)
        self._conflicts(match, value, ignore_id=row.id)
        return record

    def bind_event(self, match_id: str, event_id: str, assignment_id: str, *, expected_revision: int, reason: str) -> None:
        """Link an explicitly attributed event; always requires separate re-review.

        No actor/outcome is inferred or copied. An event correction endpoint must
        set the desired actor first, then bind and explicitly review the action.
        """
        match = self._match(match_id, lock=True)
        with self.db.begin_nested():
            row = self._assignment(match_id, assignment_id, expected_revision)
            value = TrackIdentityCorrection(expected_revision=expected_revision, player_id=row.player_id, status="confirmed", reason=reason)
            if row.status != "confirmed":
                raise IdentityError("identity_unconfirmed", "Only confirmed identities may support attributed events.")
            self._target(match, row.player_id)
            decision = TrackIdentityWrite(**identity_record(row).model_dump(include=set(TrackIdentityWrite.model_fields)))
            self._observed(match, decision)
            self._conflicts(match, decision, ignore_id=row.id)
            event = self.db.scalar(select(ReviewedEvent).where(ReviewedEvent.id == event_id, ReviewedEvent.match_id == match_id, *self._scope(ReviewedEvent)).execution_options(populate_existing=True))
            if event is None:
                raise IdentityError("not_found", "Event not found in this scope.", 404)
            if event.player_id != row.player_id or event.timestamp_seconds < row.start_seconds or (event.end_timestamp_seconds if event.end_timestamp_seconds is not None else event.timestamp_seconds) > row.end_seconds:
                raise IdentityError("event_identity_mismatch", "The event actor and full timestamp interval must agree with the confirmed decision.")
            before = _event_state(event)
            event.identity_assignment_id, event.identity_assignment_revision = row.id, row.revision
            event.status, event.reviewed_by, event.reviewed_at, event.updated_at = "draft", None, None, utcnow()
            self._audit(match_id, "event", event.id, "identity_bound", before, _event_state(event), value.reason)

    def invalidate_upload(self, match_id: str, upload_id: str, *, reason: str) -> int:
        """Call BEFORE replacing/deleting a source; does not delete private media.

        This is also safe when its observations are missing or a job has failed.
        The API remains responsible for storage deletion and worker lease fencing.
        """
        self._match(match_id, lock=True)
        value = TrackIdentityCorrection(expected_revision=1, player_id="validation", status="rejected", reason=reason)
        with self.db.begin_nested():
            rows = self._bounded(select(TrackIdentityAssignment).where(TrackIdentityAssignment.match_id == match_id, TrackIdentityAssignment.upload_id == upload_id, *self._scope(TrackIdentityAssignment)).order_by(TrackIdentityAssignment.id), MAX_IDENTITIES)
            for row in rows:
                before = identity_record(row)
                self._invalidate_events(row, value.reason)
                row.status, row.reviewed_by, row.reviewed_at = "rejected", self.reviewer_id, utcnow()
                row.source, row.confidence, row.reason, row.updated_at = "reviewer", None, value.reason, utcnow()
                self.db.flush()
                self._audit(match_id, "track_identity", row.id, "source_invalidated", before.model_dump(mode="json"), identity_record(row).model_dump(mode="json"), value.reason)
        return len(rows)
