"""Scoped identity service integration; no detector/biometric model is exercised."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import get_engine, init_db, utcnow
from app.intelligence.contracts import (
    CohortScope, EvidenceAppearance, EvidenceMatch, EvidenceSnapshot,
    ReviewedAction, TrackIdentityWrite,
)
from app.intelligence.identity import IdentityError, TrackIdentityService, identity_record
from app.intelligence.memory import ReviewedPlayerMemoryEngine
from app.models import (
    Account, Appearance, AuditLog, Match, Player, ProcessingChunk, ProcessingJob,
    ReviewedEvent, RightsAttestation, TrackIdentityAssignment, TrackObservation, Upload, Workspace,
)


@pytest.fixture
def identity_db(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'identity.sqlite'}")
    init_db(str(engine.url))
    with Session(engine, expire_on_commit=False, autoflush=False) as db:
        db.add_all([Workspace(id="w", name="Fixture"), Workspace(id="foreign", name="Other fixture")])
        db.flush()
        db.add_all([Account(id="reviewer", workspace_id="w", email="identity@example.test", password_hash="unused"), Account(id="foreign-reviewer", workspace_id="foreign", email="foreign@example.test", password_hash="unused")])
        db.flush()
        db.add_all([Player(id=name, workspace_id="foreign" if name == "foreign-player" else "w", is_synthetic=name != "real-player", display_name=name, age_group="U15", primary_role="midfielder") for name in ("p", "mate", "rival", "foreign-player", "real-player")])
        db.add(Match(id="m", workspace_id="w", is_synthetic=True, title="Fixture game", match_date=date(2025, 1, 1), competition="Fixture", season="2025", age_group="U15", team_name="First", opponent_name="Second", duration_seconds=600))
        db.flush()
        db.add_all([Appearance(workspace_id="w", is_synthetic=True, match_id="m", player_id=name, side="away" if name == "rival" else "home", jersey_number=7 if name != "mate" else 8, role="midfielder", played_seconds=600, reviewed_seconds=300) for name in ("p", "mate", "rival")])
        db.add(RightsAttestation(workspace_id="w", is_synthetic=True, match_id="m", account_id="reviewer", permission_to_process=True, guardian_or_club_authorization=True, opponent_privacy_review=True, authorized_roster=True, retention_days=30))
        db.add(Upload(id="u", workspace_id="w", is_synthetic=True, match_id="m", original_filename="fixture.mp4", storage_key="fixture", content_type="video/mp4", size_bytes=1, sha256="a" * 64, duration_seconds=600, status="probed"))
        db.flush()
        db.add(ProcessingJob(id="j", workspace_id="w", is_synthetic=True, match_id="m", upload_id="u", status="needs_review"))
        for track in ("7", "8"):
            db.add_all([TrackObservation(workspace_id="w", is_synthetic=True, match_id="m", upload_id="u", track_id=track, timestamp_seconds=t, label="player", bbox=[0.1, 0.1, 0.2, 0.2], confidence=0.9, model_version="fixture") for t in (1, 5, 10, 15, 20)])
        db.commit()
        yield db
    engine.dispose()


def service(db, **kwargs):
    return TrackIdentityService(db, **({"workspace_id": "w", "mode": "demo", "reviewer_id": "reviewer"} | kwargs))


def proposal(**kwargs):
    return TrackIdentityWrite(**(dict(upload_id="u", track_id="7", player_id="p", start_seconds=1, end_seconds=20, reason="Reviewer checked authorized roster and private footage") | kwargs))


def confirm(db, **kwargs):
    svc = service(db)
    proposed = svc.propose("m", proposal(**kwargs))
    return svc.confirm("m", proposed.id, expected_revision=proposed.revision, reason="Explicitly reviewed visible shirt against the match roster")


def make_event(db, **kwargs):
    event = ReviewedEvent(**(dict(id="e", workspace_id="w", is_synthetic=True, match_id="m", player_id="p", kind="pass", outcome="successful", timestamp_seconds=5, status="approved", provenance="synthetic", created_by="reviewer", reviewed_by="reviewer", reviewed_at=utcnow()) | kwargs))
    db.add(event)
    db.flush()
    return event


def snapshot(db) -> EvidenceSnapshot:
    db.flush()
    match = db.get(Match, "m")
    assert match is not None
    return EvidenceSnapshot(
        scope=CohortScope(workspace_id="w", mode="demo", season=match.season, age_group=match.age_group, competition=match.competition, role="all_roles"),
        matches=[EvidenceMatch(match_id=match.id, workspace_id="w", mode="demo", match_date=match.match_date, season=match.season, age_group=match.age_group, competition=match.competition, duration_seconds=match.duration_seconds)],
        appearances=[EvidenceAppearance(appearance_id=a.id, match_id=a.match_id, player_id=a.player_id, workspace_id="w", mode="demo", side=a.side, role=a.role, jersey_number=a.jersey_number, played_seconds=a.played_seconds, reviewed_seconds=a.reviewed_seconds, roster_authorized=True) for a in db.scalars(select(Appearance).where(Appearance.match_id == "m"))],
        events=[ReviewedAction(**({key: getattr(e, key) for key in ReviewedAction.model_fields if hasattr(e, key)} | {"event_id": e.id, "workspace_id": "w", "mode": "demo"})) for e in db.scalars(select(ReviewedEvent).where(ReviewedEvent.match_id == "m"))],
        identities=[identity_record(row) for row in db.scalars(select(TrackIdentityAssignment).where(TrackIdentityAssignment.match_id == "m"))],
    )


def test_candidates_are_conservative_ambiguous_numbers_never_auto_confirm(identity_db):
    db = identity_db
    candidates = service(db).candidates("m", proposal(), jersey_number=7)
    assert candidates.status == "unresolved"
    assert {row.player_id for row in candidates.candidates} == {"p", "rival"}
    assert candidates.observation_count == 5
    single = service(db).candidates("m", proposal(), jersey_number=7, side="home")
    assert len(single.candidates) == 1 and single.status == "unresolved"
    assert db.scalar(select(func.count()).select_from(TrackIdentityAssignment)) == 0
    assert "No automated jersey/team recognition" in candidates.limitations[0]
    assert "confidence" not in type(candidates.candidates[0]).model_fields


def test_propose_confirm_reject_unassign_audits_do_not_assign_observations_or_actions(identity_db):
    db = identity_db
    event = make_event(db, status="draft", reviewed_by=None, reviewed_at=None)
    svc = service(db)
    first = svc.propose("m", proposal(source="model_proposal", confidence=0.999))
    assert first.reviewed_by is None and first.status == "proposed"
    approved = svc.confirm("m", first.id, expected_revision=first.revision, reason="Reviewed separately; detector confidence is not identity confidence")
    assert approved.revision == 2
    assert approved.source == "reviewer" and approved.confidence is None
    db.refresh(event)
    assert event.status == "draft"
    assert all(row.player_id is None for row in db.scalars(select(TrackObservation)))
    removed = svc.unassign("m", approved.id, expected_revision=2, reason="Ambiguous shirt; unassign rather than force identity")
    assert removed.status == "rejected" and removed.revision == 3
    second = svc.propose("m", proposal(player_id="mate"))
    svc.reject("m", second.id, expected_revision=1, reason="Wrong roster candidate")
    db.commit()
    assert [row.action for row in db.scalars(select(AuditLog).where(AuditLog.entity_id == first.id).order_by(AuditLog.created_at))] == ["propose", "confirm", "unassign"]
    audit = db.scalar(select(AuditLog).where(AuditLog.entity_id == first.id, AuditLog.action == "unassign"))
    assert audit.before_json["status"] == "confirmed"
    assert audit.after_json["status"] == "rejected"
    assert audit.reason == removed.reason


def test_reassign_repairs_memory_and_duel_relations_but_never_transfers_approval(identity_db):
    db = identity_db
    identity = confirm(db)
    event = make_event(db, kind="duel", opponent_player_id="rival", contest_id="contest")
    svc = service(db)
    svc.bind_event("m", "e", identity.id, expected_revision=identity.revision, reason="Reviewed event occurs within this identity interval")
    assert event.status == "draft"
    # Explicit event review is a separate act, intentionally outside identity.
    event.status, event.reviewed_by, event.reviewed_at = "approved", "reviewer", utcnow()
    db.commit()
    engine = ReviewedPlayerMemoryEngine()
    before = engine.build(snapshot(db), player_id="p")
    assert before.approved_event_count == 1
    assert any(row.relation == "direct_duel" for row in before.relationships)
    corrected = svc.reassign("m", identity.id, expected_revision=2, player_id="mate", reason="Private footage review corrected a mistaken roster identity")
    db.commit()
    db.refresh(event)
    assert event.player_id == "p" and event.status == "draft"
    assert event.identity_assignment_revision == 2 and corrected.revision == 3
    after = engine.build(snapshot(db), player_id="p")
    assert after.approved_event_count == 0
    assert not any(row.relation == "direct_duel" for row in after.relationships)
    assert after.evidence_fingerprint != before.evidence_fingerprint
    assert engine.build(snapshot(db), player_id="mate").approved_event_count == 0
    event.player_id = "mate"
    db.flush()
    svc.bind_event("m", "e", identity.id, expected_revision=3, reason="Reviewer explicitly corrected the event actor too")
    event.status, event.reviewed_by, event.reviewed_at = "approved", "reviewer", utcnow()
    db.commit()
    assert engine.build(snapshot(db), player_id="mate").approved_event_count == 1
    assert engine.build(snapshot(db), player_id="p").approved_event_count == 0
    logs = list(db.scalars(select(AuditLog).where(AuditLog.action == "identity_invalidated")))
    assert len(logs) == 1
    assert logs[0].before_json["player_id"] == "p" and logs[0].before_json["status"] == "approved"
    db.delete(event)
    db.commit()
    assert engine.build(snapshot(db), player_id="mate").approved_event_count == 0
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "identity_invalidated")) == 1


@pytest.mark.parametrize("overrides,code", [
    ({"player_id": "foreign-player"}, "roster_actor_not_found"),
    ({"player_id": "real-player"}, "roster_actor_not_found"),
    ({"upload_id": "old-upload"}, "source_unavailable"),
    ({"track_id": "not-observed"}, "observations_required"),
    ({"start_seconds": 0}, "unobserved_interval"),
    ({"end_seconds": 601}, "timestamp_out_of_bounds"),
    ({"start_seconds": 6, "end_seconds": 9}, "observations_required"),
    ({"status": "confirmed"}, "proposal_required"),
])
def test_identity_defenses_fail_closed(identity_db, overrides, code):
    with pytest.raises(IdentityError) as error:
        service(identity_db).propose("m", proposal(**overrides))
    assert error.value.code == code
    assert identity_db.scalar(select(func.count()).select_from(TrackIdentityAssignment)) == 0


@pytest.mark.parametrize("scope", [{"workspace_id": "foreign", "reviewer_id": "foreign-reviewer"}, {"reviewer_id": "foreign-reviewer"}, {"mode": "real"}])
def test_cross_tenant_and_mode_decisions_are_not_authorized(identity_db, scope):
    with pytest.raises(IdentityError) as error:
        service(identity_db, **scope).propose("m", proposal())
    assert error.value.status_code == 404


def test_stale_revision_and_overlapping_tracks_are_not_silently_chosen(identity_db):
    db = identity_db
    first = confirm(db)
    svc = service(db)
    with pytest.raises(IdentityError, match="refresh"):
        svc.unassign("m", first.id, expected_revision=1, reason="Stale screen")
    for values in ({"player_id": "mate"}, {"track_id": "8"}):
        other = svc.propose("m", proposal(**values))
        with pytest.raises(IdentityError) as error:
            svc.confirm("m", other.id, expected_revision=1, reason="Overlapping identity must remain unresolved")
        assert error.value.code == "identity_overlap"
        assert db.get(TrackIdentityAssignment, other.id).status == "proposed"
    db.commit()
    assert db.scalar(select(func.count()).select_from(TrackIdentityAssignment).where(TrackIdentityAssignment.status == "confirmed")) == 1


def test_nonoverlapping_tracklets_can_link_same_canonical_player(identity_db):
    db = identity_db
    first = confirm(db, start_seconds=1, end_seconds=5)
    second = confirm(db, track_id="8", start_seconds=10, end_seconds=20)
    assert first.player_id == second.player_id == "p"
    player = db.get(Player, "p")
    player.age_group, player.primary_role, player.team_name = "U17", "forward", "New club"
    db.flush()
    # Changing today's player fields does not rewrite historical roster identity.
    assert service(db).candidates("m", proposal()).candidates
    result = ReviewedPlayerMemoryEngine().build(snapshot(db), player_id="p")
    assert result.history[0].appearance_role == "midfielder" and result.history[0].age_group == "U15"
    assert result.scope.age_group == "U15"


def test_stale_job_and_replacement_upload_require_reviewer_reconfirmation(identity_db):
    db = identity_db
    identity = confirm(db)
    event = make_event(db, identity_assignment_id=identity.id, identity_assignment_revision=identity.revision)
    db.commit()
    job = db.get(ProcessingJob, "j")
    job.status = "running"
    db.commit()
    with pytest.raises(IdentityError) as error:
        service(db).propose("m", proposal(track_id="8"))
    assert error.value.code == "stale_track"
    db.execute(delete(TrackObservation))
    db.commit()
    assert service(db).invalidate_upload("m", "u", reason="Source replacement invalidates old review support") == 1
    db.commit()
    db.refresh(event)
    assert event.status == "draft"
    assert db.get(TrackIdentityAssignment, identity.id).status == "rejected"
    db.delete(db.get(Upload, "u"))
    db.flush()
    db.add(Upload(id="replacement", workspace_id="w", is_synthetic=True, match_id="m", original_filename="replacement.mp4", storage_key="replacement", content_type="video/mp4", size_bytes=1, sha256="b" * 64, duration_seconds=600, status="probed"))
    db.commit()
    with pytest.raises(IdentityError) as error:
        service(db).propose("m", proposal())
    assert error.value.code == "source_unavailable"
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "source_invalidated")) == 1


def test_atomic_savepoint_and_caller_rollback_preserve_audit_consistency(identity_db, monkeypatch):
    db = identity_db
    engine = db.get_bind()
    proposed = service(db).propose("m", proposal())
    # Service must not accidentally commit its SQLite savepoint on release.
    with Session(engine) as outside:
        assert outside.get(TrackIdentityAssignment, proposed.id) is None
    db.rollback()
    assert db.get(TrackIdentityAssignment, proposed.id) is None
    assert db.scalar(select(func.count()).select_from(AuditLog)) == 0
    identity = confirm(db)
    db.commit()
    svc = service(db)
    def audit_failure(*args, **kwargs):
        raise RuntimeError("injected audit failure")
    monkeypatch.setattr(svc, "_audit", audit_failure)
    with pytest.raises(RuntimeError, match="audit failure"):
        svc.reassign("m", identity.id, expected_revision=2, player_id="mate", reason="Must roll back with its audit")
    row = db.get(TrackIdentityAssignment, identity.id)
    db.refresh(row)
    assert row.player_id == "p" and row.revision == 2 and row.status == "confirmed"
    db.commit()
    assert db.scalar(select(func.count()).select_from(AuditLog)) == 2


def test_parallel_confirmations_serialize_conflict_check(identity_db):
    db = identity_db
    first = service(db).propose("m", proposal())
    second = service(db).propose("m", proposal(player_id="mate"))
    db.commit()
    barrier = Barrier(2)
    def contender(identifier):
        with Session(db.get_bind(), expire_on_commit=False, autoflush=False) as local:
            barrier.wait(timeout=5)
            try:
                value = service(local).confirm("m", identifier, expected_revision=1, reason="Concurrent explicit review")
                local.commit()
                return value.status
            except IdentityError as error:
                local.rollback()
                return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(contender, (first.id, second.id)))
    assert sorted(results) == ["confirmed", "identity_overlap"]
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(TrackIdentityAssignment).where(TrackIdentityAssignment.status == "confirmed")) == 1


def test_chunk_tracks_use_absolute_time_and_exact_immutable_source(identity_db):
    db = identity_db
    chunk_id = str(uuid4())
    track = f"chunk:{chunk_id}:7"
    db.add(ProcessingChunk(id=chunk_id, workspace_id="w", is_synthetic=True, job_id="j", match_id="m", upload_id="u", sequence=1, start_seconds=120, end_seconds=240, sample_interval_seconds=0.5, plan_fingerprint="c" * 64, status="completed", progress=1, processed_through_seconds=240, decoded_frames=4, sampled_frames=4, observation_count=2, model_version="fixture", result_fingerprint="d" * 64, finished_at=utcnow()))
    db.flush()
    db.add_all([TrackObservation(processing_chunk_id=chunk_id, workspace_id="w", is_synthetic=True, match_id="m", upload_id="u", track_id=track, timestamp_seconds=t, label="player", bbox=[0, 0, 1, 1], confidence=0.9, model_version="fixture") for t in (125, 130)])
    db.commit()
    valid = confirm(db, track_id=track, start_seconds=125, end_seconds=130)
    assert valid.track_id == track
    with pytest.raises(IdentityError) as error:
        service(db).propose("m", proposal(track_id=track, start_seconds=5, end_seconds=10))
    assert error.value.code == "timestamp_out_of_bounds"
    db.add(ProcessingJob(id="newer", workspace_id="w", is_synthetic=True, match_id="m", upload_id="u", status="needs_review", created_at=utcnow() + timedelta(seconds=1)))
    db.commit()
    with pytest.raises(IdentityError) as error:
        service(db).propose("m", proposal(track_id=track, start_seconds=125, end_seconds=130))
    assert error.value.code == "stale_track"
    # Missing/stale sources must not block removing a mistaken attribution.
    assert service(db).unassign("m", valid.id, expected_revision=2, reason="Stale source must be unresolved").status == "rejected"


def test_no_permissions_no_identity_and_nonplayer_track_not_identity_evidence(identity_db):
    db = identity_db
    rights = db.scalar(select(RightsAttestation))
    rights.authorized_roster = False
    db.commit()
    with pytest.raises(IdentityError) as error:
        service(db).propose("m", proposal())
    assert error.value.code == "attestation_required"
    rights.authorized_roster = True
    for row in db.scalars(select(TrackObservation)):
        row.label = "ball"
    db.commit()
    with pytest.raises(IdentityError) as error:
        service(db).propose("m", proposal())
    assert error.value.code == "observations_required"


def test_approval_gate_rejects_stale_actor_interval_and_orphan_links(identity_db):
    db = identity_db
    identity = confirm(db)
    event = make_event(db, identity_assignment_id=identity.id, identity_assignment_revision=identity.revision)
    svc = service(db)
    assert svc.assert_event_attribution("m", event).id == identity.id
    event.player_id = "mate"
    with pytest.raises(IdentityError) as error:
        svc.assert_event_attribution("m", event)
    assert error.value.code == "event_identity_mismatch"
    event.player_id, event.end_timestamp_seconds = "p", 21
    with pytest.raises(IdentityError) as error:
        svc.assert_event_attribution("m", event)
    assert error.value.code == "event_identity_mismatch"
    event.end_timestamp_seconds, event.identity_assignment_revision = None, 1
    with pytest.raises(IdentityError) as error:
        svc.assert_event_attribution("m", event)
    assert error.value.code == "stale_revision"
    event.identity_assignment_id = None
    with pytest.raises(IdentityError) as error:
        svc.assert_event_attribution("m", event)
    assert error.value.code == "orphan_identity_link"
    event.identity_assignment_revision = None
    assert svc.assert_event_attribution("m", event) is None


def test_retention_and_inactive_reviewer_block_new_identity_but_not_cleanup(identity_db):
    db = identity_db
    identity = confirm(db)
    upload = db.get(Upload, "u")
    upload.created_at = utcnow() - timedelta(days=31)
    db.commit()
    with pytest.raises(IdentityError) as error:
        service(db).propose("m", proposal())
    assert error.value.code == "retention_expired"
    assert service(db).unassign("m", identity.id, expected_revision=2, reason="Expired source no longer supports identity").status == "rejected"
    reviewer = db.get(Account, "reviewer")
    reviewer.is_active = False
    db.commit()
    with pytest.raises(IdentityError) as error:
        service(db).candidates("m", proposal())
    assert error.value.code == "reviewer_not_found"


def test_new_match_requires_own_identity_review_but_reuses_canonical_player(identity_db):
    db = identity_db
    first = confirm(db)
    db.add(Match(id="m2", workspace_id="w", is_synthetic=True, title="Next fixture", match_date=date(2025, 1, 2), competition="Fixture", season="2025", age_group="U15", team_name="New club", opponent_name="Former club", duration_seconds=600))
    db.flush()
    db.add(Appearance(workspace_id="w", is_synthetic=True, match_id="m2", player_id="p", side="away", jersey_number=12, role="defender", played_seconds=600, reviewed_seconds=300))
    db.add(RightsAttestation(workspace_id="w", is_synthetic=True, match_id="m2", account_id="reviewer", permission_to_process=True, guardian_or_club_authorization=True, opponent_privacy_review=True, authorized_roster=True))
    db.add(Upload(id="u2", workspace_id="w", is_synthetic=True, match_id="m2", original_filename="next.mp4", storage_key="next", content_type="video/mp4", size_bytes=1, sha256="c" * 64, duration_seconds=600, status="probed"))
    db.flush()
    db.add(ProcessingJob(id="j2", workspace_id="w", is_synthetic=True, match_id="m2", upload_id="u2", status="needs_review"))
    db.add_all([TrackObservation(workspace_id="w", is_synthetic=True, match_id="m2", upload_id="u2", track_id="7", timestamp_seconds=t, label="player", bbox=[0, 0, 1, 1], confidence=0.9, model_version="fixture") for t in (1, 20)])
    db.commit()
    svc = service(db)
    data = proposal(upload_id="u2")
    candidates = svc.candidates("m2", data)
    assert candidates.status == "unresolved" and candidates.candidates[0].jersey_number == 12
    assert db.scalar(select(func.count()).select_from(TrackIdentityAssignment).where(TrackIdentityAssignment.match_id == "m2")) == 0
    proposed = svc.propose("m2", data)
    second = svc.confirm("m2", proposed.id, expected_revision=1, reason="New game individually reviewed against its own roster")
    assert first.player_id == second.player_id == "p"
    assert first.upload_id != second.upload_id and first.match_id != second.match_id
    assert db.scalar(select(func.count()).select_from(Player).where(Player.id == "p")) == 1
    assert db.get(TrackIdentityAssignment, first.id).status == "confirmed"
