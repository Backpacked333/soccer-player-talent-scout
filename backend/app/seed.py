"""Explicit per-workspace synthetic fixtures; never invoked on import/startup."""
from datetime import date
from typing import Any, cast

from sqlalchemy import CursorResult, update
from sqlalchemy.orm import Session

from app.analytics import SYNTHETIC_NOTICE
from app.auth import Principal
from app.db import utcnow
from app.models import Appearance, Match, Player, ReviewedEvent, Workspace
from app.schemas import DemoSeedResponse
from app.services import capacity


def seed_demo(db: Session, principal: Principal) -> DemoSeedResponse:
    changed = db.execute(update(Workspace).where(Workspace.id == principal.workspace_id, Workspace.demo_seeded_at.is_(None)).values(demo_seeded_at=utcnow()))
    if cast(CursorResult[Any], changed).rowcount != 1:
        db.rollback()
        return DemoSeedResponse(players_created=0, matches_created=0, message=f"{SYNTHETIC_NOTICE}. Demo fixtures were already created for this workspace.")
    capacity(db, Player, principal.workspace_id, 996)
    capacity(db, Match, principal.workspace_id, 997)
    players = []
    for index, label in enumerate(("Demo player 07", "Demo player 12", "Demo player 18", "Demo opponent 04")):
        player = Player(workspace_id=principal.workspace_id, is_synthetic=True, display_name=label, age_group="U15", primary_role="midfielder" if index < 3 else "defender", team_name="Synthetic North" if index < 3 else "Synthetic South", notes=SYNTHETIC_NOTICE)
        db.add(player)
        players.append(player)
    db.flush()
    for match_index in range(3):
        match = Match(workspace_id=principal.workspace_id, is_synthetic=True, title=f"Synthetic review fixture {match_index + 1}", match_date=date(2025, 3, 1 + match_index * 7), competition="Synthetic development league", season="2025 demo", age_group="U15", team_name="Synthetic North", opponent_name="Synthetic South", duration_seconds=1200)
        db.add(match)
        db.flush()
        for player_index, player in enumerate(players):
            if player_index == 2 and match_index > 0:
                continue
            appearance = Appearance(workspace_id=principal.workspace_id, match_id=match.id, player_id=player.id, is_synthetic=True, jersey_number=(7, 12, 18, 4)[player_index], side="home" if player_index < 3 else "away", role=player.primary_role, played_seconds=1200 if player_index != 2 else 600, reviewed_seconds=900 if player_index != 2 else 120, notes=SYNTHETIC_NOTICE)
            db.add(appearance)
            count = 12 if player_index < 2 else 4
            for event_index in range(count):
                success = (event_index + player_index) % (6 if player_index == 0 else 4) != 0
                event = ReviewedEvent(workspace_id=principal.workspace_id, match_id=match.id, player_id=player.id, is_synthetic=True, kind="pass" if player_index < 3 else "duel", outcome="successful" if success else "unsuccessful", timestamp_seconds=30 + event_index * 45 + player_index * 3, status="approved", provenance="synthetic", confidence=None, model_version=None, created_by=principal.account.id, reviewed_by=principal.account.id, reviewed_at=utcnow(), opponent_player_id=players[0].id if player_index == 3 else None, contest_id=f"fictional-demo-contest-{match_index}-{event_index}" if player_index == 3 else None, notes=SYNTHETIC_NOTICE)
                db.add(event)
            if player_index < 2:
                db.add(ReviewedEvent(workspace_id=principal.workspace_id, match_id=match.id, player_id=player.id, is_synthetic=True, kind="carry", outcome="successful", timestamp_seconds=700 + player_index * 10, status="approved", provenance="synthetic", created_by=principal.account.id, reviewed_by=principal.account.id, reviewed_at=utcnow(), notes=SYNTHETIC_NOTICE))
    db.commit()
    return DemoSeedResponse(players_created=4, matches_created=3, message=f"{SYNTHETIC_NOTICE}. Four invented roster labels and three fixture matches were added only to demo mode.")
