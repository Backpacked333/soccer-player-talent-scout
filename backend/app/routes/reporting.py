"""Private approved-evidence analytics, explicit demo creation and safe exports."""
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics import DISCLAIMER, SYNTHETIC_NOTICE, EvidenceLeaderboardResponse, EvidencePlayerProfile, leaderboard, match_metrics, player_profile
from app.auth import Principal, api_db, current_user, fail
from app.db import utcnow
from app.intelligence.service import evidence_read
from app.models import Appearance, Match, Player, ProcessingJob, ReviewedEvent, RightsAttestation, Upload
from app.schemas import AppearanceRead, AttestationRead, DemoSeedResponse, EventRead, IntegrationRead, MatchRead, Mode, PlayerRead, Role, WorkspaceExport, WorkspaceRead
from app.seed import seed_demo
from app.services import EVENT_LIMIT, bounded, owned

router = APIRouter()


@router.get("/players/{player_id}/profile", response_model=EvidencePlayerProfile)
def get_profile(player_id: str, request: Request, principal: Principal = Depends(current_user)):
    with evidence_read(request.app.state.settings) as db:
        result = player_profile(db, principal.workspace_id, player_id)
    if result is None:
        fail(404, "not_found", "This player was not found in your workspace.")
    return result


@router.get("/leaderboard", response_model=EvidenceLeaderboardResponse)
def get_leaderboard(request: Request, age_group: str = Query(min_length=2, max_length=20), role: Role = Query(), competition: str = Query(min_length=1, max_length=100), season: str | None = Query(default=None, min_length=1, max_length=40), mode: Mode = "real", principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    with evidence_read(request.app.state.settings) as snapshot_db:
        return leaderboard(snapshot_db, principal.workspace_id, age_group=age_group, role=role, competition=competition, season=season, mode=mode)


@router.get("/matches/{match_id}/metrics")
def get_match_metrics(match_id: str, request: Request, principal: Principal = Depends(current_user)):
    with evidence_read(request.app.state.settings) as db:
        match = owned(db, Match, match_id, principal.workspace_id)
        return match_metrics(db, principal.workspace_id, match)


@router.get("/dashboard")
def dashboard(mode: Mode = "real", principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    synthetic = mode == "demo"

    def count(model):
        return db.scalar(select(func.count()).select_from(model).where(model.workspace_id == principal.workspace_id, model.is_synthetic == synthetic))

    by_status = {status: count for status, count in db.execute(select(ProcessingJob.status, func.count()).where(ProcessingJob.workspace_id == principal.workspace_id, ProcessingJob.is_synthetic == synthetic).group_by(ProcessingJob.status))}
    review_status = {status: count for status, count in db.execute(select(ReviewedEvent.status, func.count()).where(ReviewedEvent.workspace_id == principal.workspace_id, ReviewedEvent.is_synthetic == synthetic).group_by(ReviewedEvent.status))}
    return {"mode": mode, "is_synthetic": synthetic, "synthetic_notice": SYNTHETIC_NOTICE if synthetic else None, "player_count": count(Player), "match_count": count(Match), "upload_count": count(Upload), "event_counts_by_review_status": review_status, "job_counts_by_status": by_status, "disclaimer": (f"{SYNTHETIC_NOTICE}. " if synthetic else "") + DISCLAIMER}


@router.get("/integrations", response_model=list[IntegrationRead])
def integrations(request: Request, _principal: Principal = Depends(current_user)):
    try:
        from app.vision import get_integrations
    except ImportError:
        fail(503, "registry_unavailable", "The local integration registry module is not installed.")
    return get_integrations(request.app.state.settings)


@router.post("/demo/seed", response_model=DemoSeedResponse)
def create_demo(principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    return seed_demo(db, principal)


@router.get("/exports/workspace", response_model=WorkspaceExport)
def export_workspace(response: Response, mode: Mode = "real", principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    synthetic = mode == "demo"

    def rows(model, schema, limit=1000):
        return [schema.model_validate(row) for row in bounded(db, select(model).where(model.workspace_id == principal.workspace_id, model.is_synthetic == synthetic).order_by(model.id), limit)]

    export = WorkspaceExport(exported_at=utcnow(), mode=mode, synthetic_notice=SYNTHETIC_NOTICE if synthetic else None, workspace=WorkspaceRead.model_validate(principal.workspace), players=rows(Player, PlayerRead), matches=rows(Match, MatchRead), appearances=rows(Appearance, AppearanceRead, 100000), events=rows(ReviewedEvent, EventRead, EVENT_LIMIT), attestations=rows(RightsAttestation, AttestationRead))
    response.headers["Content-Disposition"] = f'attachment; filename="scoutroom-{mode}-evidence.json"'
    return export
