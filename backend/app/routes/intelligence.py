"""Private canonical identity, longitudinal knowledge and narrow model endpoints."""
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal, api_db, current_user, fail
from app.db import utcnow
from app.intelligence.action_value import score_from_snapshot
from app.intelligence.contracts import CohortScope, IntelligenceContract, StreamDeclaration, TrackIdentityCorrection, TrackIdentityRecord, TrackIdentityWrite
from app.intelligence.identity import IdentityError, identity_record
from app.intelligence.memory import ReviewedPlayerKnowledge, ReviewedPlayerMemoryEngine
from app.intelligence.ratings import BradleyTerryEngine, RatingCapacityError
from app.intelligence.service import current_stream, evidence_read, identity_failure, identity_service, load_snapshot, operator_artifact, source_signature
from app.models import Match, Player, ReviewedEvent, TrackIdentityAssignment
from app.schemas import Mode, Role
from app.services import audit, bounded, owned

router = APIRouter()


@router.get("/matches/{match_id}/identities", response_model=list[TrackIdentityRecord])
def identities(match_id: str, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    match = owned(db, Match, match_id, principal.workspace_id)
    return [identity_record(row) for row in bounded(db, select(TrackIdentityAssignment).where(TrackIdentityAssignment.workspace_id == principal.workspace_id, TrackIdentityAssignment.match_id == match.id, TrackIdentityAssignment.is_synthetic == match.is_synthetic).order_by(TrackIdentityAssignment.track_id, TrackIdentityAssignment.start_seconds, TrackIdentityAssignment.id), 10000)]


@router.post("/matches/{match_id}/identities", response_model=TrackIdentityRecord, status_code=201)
def propose_identity(match_id: str, data: TrackIdentityWrite, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    match = owned(db, Match, match_id, principal.workspace_id)
    if data.source != "reviewer" or data.confidence is not None:
        fail(422, "reviewer_only", "Reviewer proposals have no guessed identity probabilities.")
    try:
        result = identity_service(db, principal, match).propose(match.id, data)
        db.commit()
        return result
    except IdentityError as error:
        identity_failure(error)


@router.post("/matches/{match_id}/identity-candidates")
def identity_candidates(match_id: str, data: TrackIdentityWrite, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    match = owned(db, Match, match_id, principal.workspace_id)
    try:
        return identity_service(db, principal, match).candidates(match.id, data)
    except IdentityError as error:
        identity_failure(error)


@router.put("/matches/{match_id}/identities/{assignment_id}", response_model=TrackIdentityRecord)
def correct_identity(match_id: str, assignment_id: str, data: TrackIdentityCorrection, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    match = owned(db, Match, match_id, principal.workspace_id)
    try:
        result = identity_service(db, principal, match).correct(match.id, assignment_id, data)
        db.commit()
        return result
    except IdentityError as error:
        identity_failure(error)


class BindIdentity(IntelligenceContract):
    assignment_id: str = Field(min_length=1, max_length=36)
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


@router.post("/events/{event_id}/identity")
def bind_identity(event_id: str, data: BindIdentity, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    event = owned(db, ReviewedEvent, event_id, principal.workspace_id)
    match = owned(db, Match, event.match_id, principal.workspace_id)
    try:
        identity_service(db, principal, match).bind_event(match.id, event.id, data.assignment_id, expected_revision=data.expected_revision, reason=data.reason)
        db.commit()
        return {"status": "draft", "message": "Identity linked; explicitly review the event again before it contributes."}
    except IdentityError as error:
        identity_failure(error)


def scope_query(season: str = Query(min_length=1, max_length=40), age_group: str = Query(min_length=2, max_length=20), competition: str = Query(min_length=1, max_length=100), role: Role | Literal["all_roles"] = "all_roles", mode: Mode = "real", principal: Principal = Depends(current_user)) -> CohortScope:
    return CohortScope(workspace_id=principal.workspace_id, mode=mode, season=season, age_group=age_group, competition=competition, role=role)


@router.get("/players/{player_id}/knowledge", response_model=ReviewedPlayerKnowledge)
def knowledge(player_id: str, request: Request, as_of: date | None = None, scope: CohortScope = Depends(scope_query)):
    with evidence_read(request.app.state.settings) as db:
        player = owned(db, Player, player_id, scope.workspace_id)
        if player.is_synthetic != (scope.mode == "demo"):
            fail(404, "not_found", "Player not found in this mode.")
        cutoff = min(as_of or utcnow().date(), utcnow().date())
        return ReviewedPlayerMemoryEngine().build(load_snapshot(db, scope, as_of=cutoff), player_id=player.id, as_of=cutoff)


@router.get("/intelligence/duels")
def duel_ratings(request: Request, scope: CohortScope = Depends(scope_query), as_of: date | None = None, player_id: str | None = None, opponent_player_id: str | None = None):
    with evidence_read(request.app.state.settings) as db:
        snapshot = load_snapshot(db, scope, as_of=as_of)
    try:
        report = BradleyTerryEngine().fit(snapshot)
    except RatingCapacityError:
        fail(409, "rating_capacity", "Cohort exceeds the 128-player/10,000-contest model bound. Narrow the explicit cohort; no players were silently dropped.")
    if player_id or opponent_player_id:
        if not player_id or not opponent_player_id:
            fail(422, "pair_required", "Supply both canonical players.")
        return BradleyTerryEngine().predict(report, player_id=player_id, opponent_player_id=opponent_player_id)
    return report


@router.get("/events/{event_id}/action-value")
def action_value(event_id: str, request: Request, principal: Principal = Depends(current_user)):
    with evidence_read(request.app.state.settings) as db:
        event = owned(db, ReviewedEvent, event_id, principal.workspace_id)
        match = owned(db, Match, event.match_id, principal.workspace_id)
        scope = CohortScope(workspace_id=principal.workspace_id, mode="demo" if match.is_synthetic else "real", season=match.season, age_group=match.age_group, competition=match.competition, role="all_roles")
        artifact, reason = operator_artifact(request.app.state.settings, scope)
        result = score_from_snapshot(load_snapshot(db, scope), event_id=event.id, artifact=artifact)
        if artifact is None:
            result.reasons = [reason]
        return result


@router.get("/intelligence/snapshot")
def export_snapshot(request: Request, scope: CohortScope = Depends(scope_query)):
    with evidence_read(request.app.state.settings) as db:
        return load_snapshot(db, scope)


@router.get("/matches/{match_id}/action-stream", response_model=StreamDeclaration)
def get_stream(match_id: str, request: Request, principal: Principal = Depends(current_user)):
    with evidence_read(request.app.state.settings) as db:
        return current_stream(db, owned(db, Match, match_id, principal.workspace_id))


class StreamInput(StreamDeclaration):
    # Validation needs declaration metadata, but authenticated values replace these.
    declared_by: str = "pending-authenticated-reviewer"
    declared_at: datetime = Field(default_factory=utcnow)


@router.put("/matches/{match_id}/action-stream", response_model=StreamDeclaration)
def declare_stream(match_id: str, data: StreamInput, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    match = owned(db, Match, match_id, principal.workspace_id)
    identity_service(db, principal, match).lock_match(match.id)
    # Model training permission is a separate explicit declaration, never inferred
    # from processing rights. Store the authenticated declarer, not a body principal.
    declaration = StreamDeclaration.model_validate(data.model_dump() | {"declared_by": principal.account.id, "declared_at": utcnow()})
    before = current_stream(db, match).model_dump(mode="json")
    audit(db, principal, match, "action_stream", match.id, "declare", before, {"declaration": declaration.model_dump(mode="json"), "source_signature": source_signature(db, match)}, "Explicit stream completeness and separate training-rights declaration; not independently verified")
    db.commit()
    return declaration
