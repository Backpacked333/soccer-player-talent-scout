"""Workspace-scoped CRUD and auditable reviewer-confirmed evidence."""
from __future__ import annotations

import os
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.auth import Principal, api_db, current_user, fail
from app.db import utcnow
from app.intelligence.service import identity_service
from app.models import Appearance, AuditLog, Match, Player, ProcessingJob, ReviewedEvent, RightsAttestation, TrackIdentityAssignment, Upload
from app.schemas import AppearanceRead, AttestationCreate, AttestationRead, AuditRead, EventRead, EventWrite, MatchCreate, MatchRead, Mode, PlayerCreate, PlayerRead, ReviewStatus, RosterWrite
from app.services import EVENT_LIMIT, audit, bounded, capacity, owned, private_path, validate_event

router = APIRouter()


def scoped_list(db, model, principal, mode):
    return bounded(db, select(model).where(model.workspace_id == principal.workspace_id, model.is_synthetic == (mode == "demo")).order_by(model.created_at.desc(), model.id))


@router.get("/players", response_model=list[PlayerRead])
def list_players(mode: Mode = "real", principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    return scoped_list(db, Player, principal, mode)


@router.post("/players", response_model=PlayerRead, status_code=201)
def create_player(data: PlayerCreate, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    capacity(db, Player, principal.workspace_id)
    row = Player(**data.model_dump(), workspace_id=principal.workspace_id, is_synthetic=False)
    db.add(row)
    db.commit()
    return row


@router.get("/players/{player_id}", response_model=PlayerRead)
def get_player(player_id: str, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    return owned(db, Player, player_id, principal.workspace_id)


@router.put("/players/{player_id}", response_model=PlayerRead)
def update_player(player_id: str, data: PlayerCreate, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    row = owned(db, Player, player_id, principal.workspace_id)
    appearances = bounded(db, select(Appearance).where(Appearance.workspace_id == principal.workspace_id, Appearance.player_id == row.id))
    # The profile's current age band may advance; historical cohorts use the
    # match's age band rather than retroactively relabeling old appearances.
    before = PlayerRead.model_validate(row).model_dump(mode="json")
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    for appearance in appearances:
        match = owned(db, Match, appearance.match_id, principal.workspace_id)
        audit(db, principal, match, "player", row.id, "update", before, PlayerRead.model_validate(row).model_dump(mode="json"), "Roster identity details corrected")
    db.commit()
    return row


@router.delete("/players/{player_id}", status_code=204)
def delete_player(player_id: str, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    row = owned(db, Player, player_id, principal.workspace_id)
    linked = db.scalar(select(Appearance.id).where(Appearance.workspace_id == principal.workspace_id, Appearance.player_id == row.id).limit(1))
    evidence = db.scalar(select(ReviewedEvent.id).where(ReviewedEvent.workspace_id == principal.workspace_id, or_(ReviewedEvent.player_id == row.id, ReviewedEvent.opponent_player_id == row.id)).limit(1))
    if linked or evidence:
        fail(409, "player_has_evidence", "Remove this player's match evidence and roster links before deleting the identity.")
    db.delete(row)
    db.commit()


@router.get("/matches", response_model=list[MatchRead])
def list_matches(mode: Mode = "real", principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    return scoped_list(db, Match, principal, mode)


@router.post("/matches", response_model=MatchRead, status_code=201)
def create_match(data: MatchCreate, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    capacity(db, Match, principal.workspace_id)
    row = Match(**data.model_dump(), workspace_id=principal.workspace_id, is_synthetic=False)
    db.add(row)
    db.commit()
    return row


@router.get("/matches/{match_id}", response_model=MatchRead)
def get_match(match_id: str, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    return owned(db, Match, match_id, principal.workspace_id)


@router.put("/matches/{match_id}", response_model=MatchRead)
def update_match(match_id: str, data: MatchCreate, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    row = owned(db, Match, match_id, principal.workspace_id)
    row = identity_service(db, principal, row).lock_match(row.id)
    appearances = bounded(db, select(Appearance).where(Appearance.workspace_id == principal.workspace_id, Appearance.match_id == row.id), 100)
    if appearances and data.age_group != row.age_group:
        fail(409, "linked_age_group", "Match age group cannot change while a roster is assigned.")
    if any(item.played_seconds > data.duration_seconds for item in appearances):
        fail(422, "duration_below_coverage", "Planned duration cannot be shorter than recorded playing time.")
    if row.is_synthetic:
        beyond_duration = db.scalar(select(ReviewedEvent.id).where(ReviewedEvent.workspace_id == principal.workspace_id, ReviewedEvent.match_id == row.id, or_(ReviewedEvent.timestamp_seconds > data.duration_seconds, ReviewedEvent.end_timestamp_seconds > data.duration_seconds)).limit(1))
        if beyond_duration:
            fail(422, "duration_below_evidence", "Duration cannot be shorter than existing synthetic evidence.")
    before = MatchRead.model_validate(row).model_dump(mode="json")
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    audit(db, principal, row, "match", row.id, "update", before, MatchRead.model_validate(row).model_dump(mode="json"), "Match context corrected; live cohort metrics recompute")
    db.commit()
    return row


@router.delete("/matches/{match_id}", status_code=204)
def delete_match(match_id: str, request: Request, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    row = owned(db, Match, match_id, principal.workspace_id)
    row = identity_service(db, principal, row).lock_match(row.id)
    # Abandoned leases must not indefinitely prevent privacy deletion. A stale
    # worker is fenced at persistence; it cannot resurrect this match or its job.
    running = db.scalar(select(ProcessingJob.id).where(ProcessingJob.workspace_id == principal.workspace_id, ProcessingJob.match_id == row.id, ProcessingJob.status == "running", ProcessingJob.lease_expires_at > utcnow()).limit(1))
    if running:
        fail(409, "job_running", "Wait for the active worker lease to finish before deleting this match.")
    upload = db.scalar(select(Upload).where(Upload.workspace_id == principal.workspace_id, Upload.match_id == row.id))
    original = quarantine = None
    if upload:
        original = private_path(request.app.state.settings, upload.storage_key)
        if original.exists():
            quarantine = original.with_name(f".deleting-{uuid4()}")
            try:
                os.rename(original, quarantine)
            except OSError:
                fail(503, "media_delete_failed", "Private media could not be removed. No match records were changed.")
    try:
        # Deletion is a privacy action: do not retain identifying audit payloads.
        db.execute(delete(AuditLog).where(AuditLog.workspace_id == principal.workspace_id, AuditLog.match_id == row.id))
        db.delete(row)
        db.commit()
    except Exception:
        db.rollback()
        if quarantine is not None and original is not None:
            os.rename(quarantine, original)
        raise
    if quarantine is not None:
        try:
            quarantine.unlink()
        except OSError:
            # Inaccessible quarantine, not a playable upload. Operator must clean up.
            fail(503, "media_cleanup_required", "Match records were deleted; an operator must finish private media cleanup.")


@router.get("/matches/{match_id}/roster", response_model=list[AppearanceRead])
def get_roster(match_id: str, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    match = owned(db, Match, match_id, principal.workspace_id)
    return bounded(db, select(Appearance).where(Appearance.workspace_id == principal.workspace_id, Appearance.match_id == match.id).order_by(Appearance.side, Appearance.jersey_number, Appearance.id), 100)


@router.put("/matches/{match_id}/roster", response_model=list[AppearanceRead])
def put_roster(match_id: str, data: RosterWrite, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    match = owned(db, Match, match_id, principal.workspace_id)
    match = identity_service(db, principal, match).lock_match(match.id)
    existing = get_roster(match.id, principal, db)
    actor_ids = {item.player_id for item in data.appearances}
    active_identity = db.scalar(select(TrackIdentityAssignment.id).where(TrackIdentityAssignment.workspace_id == principal.workspace_id, TrackIdentityAssignment.match_id == match.id, TrackIdentityAssignment.status.in_(["proposed", "confirmed"]), TrackIdentityAssignment.player_id.not_in(actor_ids)).limit(1))
    if active_identity:
        fail(409, "identity_has_roster", "Reject/unassign track identities before removing their canonical roster player.")
    source = db.scalar(select(Upload).where(Upload.workspace_id == principal.workspace_id, Upload.match_id == match.id, Upload.status == "probed"))
    for appearance in data.appearances:
        if source and source.duration_seconds is not None and appearance.reviewed_seconds > source.duration_seconds:
            fail(422, "review_exceeds_video", "Reviewer-declared coverage cannot exceed the validated source video's duration.")
        player = owned(db, Player, appearance.player_id, principal.workspace_id)
        historical_member = any(item.player_id == player.id for item in existing)
        if (player.age_group != match.age_group and not historical_member) or player.is_synthetic != match.is_synthetic:
            fail(422, "roster_cohort_mismatch", "New roster identities must match this match's age group and real/demo mode. Existing historical appearances retain the match's age cohort.")
        if appearance.played_seconds > match.duration_seconds:
            fail(422, "coverage_out_of_bounds", "Playing and review time cannot exceed the planned match duration.")
    events = bounded(db, select(ReviewedEvent).where(ReviewedEvent.workspace_id == principal.workspace_id, ReviewedEvent.match_id == match.id), EVENT_LIMIT)
    sides = {item.player_id: item.side for item in data.appearances}
    for event in events:
        if event.player_id not in actor_ids or (event.opponent_player_id and event.opponent_player_id not in actor_ids):
            fail(409, "actor_has_evidence", "Correct or delete linked events before removing a roster actor.")
        if event.opponent_player_id and sides[event.player_id] == sides[event.opponent_player_id]:
            fail(409, "opponent_has_evidence", "This side change conflicts with existing opponent evidence.")
    before = {"appearances": [AppearanceRead.model_validate(item).model_dump(mode="json") for item in existing]}
    db.execute(delete(Appearance).where(Appearance.workspace_id == principal.workspace_id, Appearance.match_id == match.id))
    rows = [Appearance(**item.model_dump(), workspace_id=principal.workspace_id, match_id=match.id, is_synthetic=match.is_synthetic) for item in data.appearances]
    db.add_all(rows)
    db.flush()
    after = {"appearances": [AppearanceRead.model_validate(item).model_dump(mode="json") for item in rows]}
    audit(db, principal, match, "roster", match.id, "replace", before, after, "Reviewer-declared playing time and reviewed coverage")
    if actor_ids != {item.player_id for item in existing}:
        # Reconfirm permissions after membership changes; do not silently reuse an
        # attestation for a different authorized roster.
        attestation = db.scalar(select(RightsAttestation).where(RightsAttestation.workspace_id == principal.workspace_id, RightsAttestation.match_id == match.id))
        if attestation:
            audit(db, principal, match, "attestation", attestation.id, "invalidate", AttestationRead.model_validate(attestation).model_dump(mode="json"), None, "Roster membership changed; explicit attestation required again")
            db.delete(attestation)
    db.commit()
    return rows


@router.get("/matches/{match_id}/attestation", response_model=AttestationRead)
def get_attestation(match_id: str, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    match = owned(db, Match, match_id, principal.workspace_id)
    row = db.scalar(select(RightsAttestation).where(RightsAttestation.workspace_id == principal.workspace_id, RightsAttestation.match_id == match.id))
    if row is None:
        fail(404, "attestation_required", "Confirm permitted footage and the authorized roster first.")
    return row


@router.post("/matches/{match_id}/attestation", response_model=AttestationRead, status_code=201)
def attest(match_id: str, data: AttestationCreate, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    match = owned(db, Match, match_id, principal.workspace_id)
    match = identity_service(db, principal, match).lock_match(match.id)
    if not get_roster(match.id, principal, db):
        fail(409, "roster_required", "Assign the authorized roster before recording footage permissions.")
    row = db.scalar(select(RightsAttestation).where(RightsAttestation.workspace_id == principal.workspace_id, RightsAttestation.match_id == match.id))
    before = AttestationRead.model_validate(row).model_dump(mode="json") if row else None
    if row is None:
        row = RightsAttestation(**data.model_dump(), workspace_id=principal.workspace_id, match_id=match.id, account_id=principal.account.id, is_synthetic=match.is_synthetic)
        db.add(row)
    else:
        for key, value in data.model_dump().items():
            setattr(row, key, value)
        row.account_id = principal.account.id
        row.created_at = utcnow()
    db.flush()
    audit(db, principal, match, "attestation", row.id, "confirm", before, AttestationRead.model_validate(row).model_dump(mode="json"), "Operator declaration; not legal verification or automated retention")
    db.commit()
    return row


@router.get("/matches/{match_id}/events", response_model=list[EventRead])
def list_events(match_id: str, status: ReviewStatus | None = None, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    match = owned(db, Match, match_id, principal.workspace_id)
    statement = select(ReviewedEvent).where(ReviewedEvent.workspace_id == principal.workspace_id, ReviewedEvent.match_id == match.id)
    if status:
        statement = statement.where(ReviewedEvent.status == status)
    return bounded(db, statement.order_by(ReviewedEvent.timestamp_seconds, ReviewedEvent.id), EVENT_LIMIT)


@router.post("/matches/{match_id}/events", response_model=EventRead, status_code=201)
def create_event(match_id: str, data: EventWrite, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    match = owned(db, Match, match_id, principal.workspace_id)
    match = identity_service(db, principal, match).lock_match(match.id)
    validate_event(db, principal, match, data)
    if not match.is_synthetic:
        get_attestation(match.id, principal, db)
    capacity(db, ReviewedEvent, principal.workspace_id, EVENT_LIMIT)
    row = ReviewedEvent(**data.model_dump(), workspace_id=principal.workspace_id, match_id=match.id, created_by=principal.account.id, provenance="synthetic" if match.is_synthetic else "manual", is_synthetic=match.is_synthetic, confidence=None, model_version=None)
    if data.status in {"approved", "rejected"}:
        row.reviewed_by = principal.account.id
        row.reviewed_at = utcnow()
    db.add(row)
    db.flush()
    audit(db, principal, match, "event", row.id, "create", None, EventRead.model_validate(row).model_dump(mode="json"), "Reviewer-created evidence")
    db.commit()
    return row


@router.put("/events/{event_id}", response_model=EventRead)
def update_event(event_id: str, data: EventWrite, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    row = owned(db, ReviewedEvent, event_id, principal.workspace_id)
    match = owned(db, Match, row.match_id, principal.workspace_id)
    match = identity_service(db, principal, match).lock_match(match.id)
    db.refresh(row)
    validate_event(db, principal, match, data)
    if not match.is_synthetic:
        get_attestation(match.id, principal, db)
    before = EventRead.model_validate(row).model_dump(mode="json")
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    row.reviewed_by = principal.account.id if data.status in {"approved", "rejected"} else None
    row.reviewed_at = utcnow() if row.reviewed_by else None
    row.updated_at = utcnow()
    if row.status == "approved":
        if row.provenance == "model_candidate" and row.identity_assignment_id is None:
            fail(409, "confirmed_identity_required", "Model candidates need a current reviewer-confirmed track identity before approval.")
        identity_service(db, principal, match).assert_event_attribution(match.id, row)
    audit(db, principal, match, "event", row.id, "correct", before, EventRead.model_validate(row).model_dump(mode="json"), "Identity/outcome correction; approved-only metrics recompute immediately")
    db.commit()
    return row


@router.delete("/events/{event_id}", status_code=204)
def delete_event(event_id: str, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    row = owned(db, ReviewedEvent, event_id, principal.workspace_id)
    match = owned(db, Match, row.match_id, principal.workspace_id)
    identity_service(db, principal, match).lock_match(match.id)
    db.refresh(row)
    audit(db, principal, match, "event", row.id, "delete", EventRead.model_validate(row).model_dump(mode="json"), None, "Evidence removed from current metrics")
    db.delete(row)
    db.commit()


@router.get("/matches/{match_id}/audit", response_model=list[AuditRead])
def get_audit(match_id: str, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    match = owned(db, Match, match_id, principal.workspace_id)
    return bounded(db, select(AuditLog).where(AuditLog.workspace_id == principal.workspace_id, AuditLog.match_id == match.id).order_by(AuditLog.created_at.desc(), AuditLog.id), 20000)
