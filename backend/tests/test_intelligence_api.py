"""Authenticated integration with isolated fictional footage, never real player data."""
from datetime import timedelta
from uuid import uuid4

import pytest

from app.db import get_session_factory, utcnow
from app.models import ProcessingChunk, ProcessingJob, TrackObservation, ReviewedEvent
from app.services import public_job
from app.intelligence.action_value import ActionValueUnavailable, artifact_document, fit
from app.intelligence.contracts import CohortScope, EvidenceSnapshot
from app.intelligence.service import evidence_read, load_snapshot
from test_action_value import policy, snapshot
from test_api_workflows import event_body, match_setup, player, rights, signup, upload

QUERY = {"season": "2025", "age_group": "U15", "competition": "Test league", "role": "all_roles", "mode": "real"}


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def knowledge(client, actor, **query):
    return ok(client.get(f"/api/players/{actor['id']}/knowledge", params=QUERY | query))


def test_three_matches_known_player_corrected_identity_history_and_no_future_or_tenant_leaks(api, tiny_video):
    _, first, second, settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    mate = player(first, "Fictional teammate")
    roster["appearances"].append({"player_id": mate["id"], "side": "home", "role": "midfielder", "jersey_number": 8, "played_seconds": 600, "reviewed_seconds": 2})
    ok(first.put(f"/api/matches/{match['id']}/roster", json=roster))
    ok(first.post(f"/api/matches/{match['id']}/attestation", json=rights()), 201)
    source = upload(first, match, tiny_video)
    e1 = ok(first.post(f"/api/matches/{match['id']}/events", json=event_body(actor)), 201)
    before = knowledge(first, actor)
    assert before["approved_event_count"] == 1
    matches = [match]
    for day in (2, 3):
        payload = {key: match[key] for key in ("title", "competition", "season", "age_group", "team_name", "opponent_name", "duration_seconds")}
        payload["match_date"] = f"2025-03-0{day}"
        fixture = ok(first.post("/api/matches", json=payload), 201)
        matches.append(fixture)
        ok(first.put(f"/api/matches/{fixture['id']}/roster", json=roster))
        ok(first.post(f"/api/matches/{fixture['id']}/attestation", json=rights()), 201)
        upload(first, fixture, tiny_video)
        ok(first.post(f"/api/matches/{fixture['id']}/events", json=event_body(actor, outcome="unsuccessful" if day == 2 else "successful")), 201)
    after = knowledge(first, actor)
    assert after["approved_event_count"] == 3 and after["reviewed_match_count"] == 3
    assert after["history"][-1]["cumulative_pass_attempts"] == 3
    assert after["history"][-1]["observed_pass_completion"] == 2/3
    assert before["evidence_fingerprint"] != after["evidence_fingerprint"]
    assert {(r["other_player_id"], r["relation"]) for r in after["relationships"]} == {(mate["id"], "co_rostered_teammate"), (opponent["id"], "co_rostered_opponent")}
    prior = knowledge(first, actor, as_of="2025-03-01")
    assert prior["approved_event_count"] == 1 and len(prior["history"]) == 1
    # Anonymous test-only observations enter via the worker persistence boundary.
    with get_session_factory(settings.database_url)() as db:
        job = db.get(ProcessingJob, source["job"]["id"])
        job.status = "needs_review"
        for timestamp in (0.5, 1.0, 1.5):
            db.add(TrackObservation(workspace_id=match["workspace_id"], match_id=match["id"], upload_id=source["upload"]["id"], track_id="fictional-track", timestamp_seconds=timestamp, label="player", bbox=[0.1, 0.1, 0.2, 0.2], confidence=0.8, model_version="test-only"))
        db.commit()
    proposal = {"upload_id": source["upload"]["id"], "track_id": "fictional-track", "player_id": actor["id"], "start_seconds": 0.5, "end_seconds": 1.5, "reason": "Fictional jersey review"}
    candidates = ok(first.post(f"/api/matches/{match['id']}/identity-candidates", json=proposal))
    assert candidates["status"] == "unresolved" and len(candidates["candidates"]) == 3
    link = ok(first.post(f"/api/matches/{match['id']}/identities", json=proposal), 201)
    assert link["status"] == "proposed" and link["confidence"] is None
    correction = {"expected_revision": link["revision"], "player_id": actor["id"], "status": "confirmed", "reason": "Explicit test footage review"}
    link = ok(first.put(f"/api/matches/{match['id']}/identities/{link['id']}", json=correction))
    ok(first.post(f"/api/events/{e1['id']}/identity", json={"assignment_id": link["id"], "expected_revision": link["revision"], "reason": "Bind reviewed source"}))
    assert knowledge(first, actor)["approved_event_count"] == 2  # never silently approve
    ok(first.put(f"/api/events/{e1['id']}", json=event_body(actor)))
    assert knowledge(first, actor)["approved_event_count"] == 3
    link = ok(first.put(f"/api/matches/{match['id']}/identities/{link['id']}", json=correction | {"expected_revision": link["revision"], "player_id": mate["id"], "reason": "Wrong shirt: correct to teammate"}))
    repaired = knowledge(first, actor)
    assert repaired["approved_event_count"] == 2 and repaired["evidence_fingerprint"] != after["evidence_fingerprint"]
    assert knowledge(first, mate)["approved_event_count"] == 0
    profile = ok(first.get(f"/api/players/{actor['id']}/profile"))
    assert sum(m["approved_event_count"] for m in profile["metrics"]) == 2
    # Approval with old actor/assignment revision cannot bypass correction.
    assert first.put(f"/api/events/{e1['id']}", json=event_body(actor)).status_code == 409
    ok(first.put(f"/api/events/{e1['id']}", json=event_body(mate, status="draft")))
    ok(first.post(f"/api/events/{e1['id']}/identity", json={"assignment_id": link["id"], "expected_revision": link["revision"], "reason": "Fresh attribution"}))
    ok(first.put(f"/api/events/{e1['id']}", json=event_body(mate)))
    assert knowledge(first, mate)["approved_event_count"] == 1
    assert knowledge(first, actor, as_of="2025-03-01")["approved_event_count"] == 0
    assert first.put(f"/api/matches/{match['id']}/identities/{link['id']}", json=correction).status_code == 409
    # Future roster/events do not contaminate present knowledge.
    updated = {key: matches[-1][key] for key in ("title", "competition", "season", "age_group", "team_name", "opponent_name", "duration_seconds")}
    updated["match_date"] = (utcnow().date()+timedelta(days=2)).isoformat()
    ok(first.put(f"/api/matches/{matches[-1]['id']}", json=updated))
    assert knowledge(first, actor)["approved_event_count"] == 1
    assert first.get(f"/api/players/{actor['id']}/knowledge", params=QUERY | {"mode": "demo"}).status_code == 404
    signup(second, "other-memory")
    for path in (f"/players/{actor['id']}/knowledge", f"/matches/{match['id']}/identities", f"/jobs/{source['job']['id']}/chunks", f"/jobs/{source['job']['id']}/observations", f"/events/{e1['id']}/action-value"):
        assert second.get('/api'+path, params=QUERY).status_code == 404
    assert ok(second.get("/api/intelligence/snapshot", params=QUERY))["events"] == []
    assert ok(second.get("/api/intelligence/duels", params=QUERY))["accepted_contests"] == 0


def test_duels_inverse_dedup_role_boundary_correction_and_action_value(api, tiny_video):
    _, first, _, _ = api
    signup(first)
    actor, rival, match, _ = match_setup(first)
    upload(first, match, tiny_video)
    body = event_body(actor, kind="duel", contest_id="explicit-fictional-contest", opponent_player_id=rival["id"])
    first_event = ok(first.post(f"/api/matches/{match['id']}/events", json=body), 201)
    reverse = event_body(rival, kind="duel", contest_id="explicit-fictional-contest", opponent_player_id=actor["id"], outcome="unsuccessful")
    inverse = ok(first.post(f"/api/matches/{match['id']}/events", json=reverse), 201)
    report = ok(first.get('/api/intelligence/duels', params=QUERY))
    assert report["accepted_contests"] == 1
    assert all(row["component_rank"] is None for row in report["ratings"])
    assert all(row["interval_low"] < row["interval_high"] for row in report["ratings"] if row["log_odds"] is not None)
    assert ok(first.get('/api/intelligence/duels', params=QUERY | {"role": "midfielder"}))["accepted_contests"] == 0
    pair = ok(first.get('/api/intelligence/duels', params=QUERY | {"player_id": actor["id"], "opponent_player_id": rival["id"]}))
    assert pair["direct_contests"] == 1 and pair["eligibility"] == "provisional"
    assert any(r["relation"] == "direct_duel" for r in knowledge(first, actor)["relationships"])
    # Subject-role memory still includes a factual duel against another role;
    # only rating comparisons require both players to match the role cohort.
    scoped_memory = knowledge(first, actor, role="midfielder")
    assert scoped_memory["approved_event_count"] == 1
    assert any(r["relation"] == "direct_duel" for r in scoped_memory["relationships"])
    ok(first.put(f"/api/events/{inverse['id']}", json=reverse | {"outcome": "successful"}))
    assert ok(first.get('/api/intelligence/duels', params=QUERY))["accepted_contests"] == 0
    assert not any(r["relation"] == "direct_duel" for r in knowledge(first, actor)["relationships"])
    first.delete(f"/api/events/{inverse['id']}")
    assert ok(first.get('/api/intelligence/duels', params=QUERY))["accepted_contests"] == 1
    value = ok(first.get(f"/api/events/{first_event['id']}/action-value"))
    assert value["value"] is None and value["reasons"] and "operator" in value["reasons"][0]
    assert first.get('/api/intelligence/duels').status_code == 422


def test_chunk_pages_ledger_coverage_large_count_retry_preservation_and_rights(api, tiny_video):
    _, first, _, settings = api
    signup(first)
    _, _, match, _ = match_setup(first)
    source = upload(first, match, tiny_video)
    job_id = source["job"]["id"]
    with get_session_factory(settings.database_url)() as db:
        job = db.get(ProcessingJob, job_id)
        job.status = "failed"
        job.result_json = {"metadata": {"duration_seconds": 3, "width": 320, "height": 240, "fps": 10, "codec": "h264"}, "error_code": "synthetic_failure", "operator_private": "/do-not-leak", "observation_count": 120001}
        for index in range(2):
            chunk = ProcessingChunk(id=str(uuid4()), workspace_id=match["workspace_id"], match_id=match["id"], job_id=job_id, upload_id=source["upload"]["id"], sequence=index, start_seconds=index*1.5, end_seconds=(index+1)*1.5, sample_interval_seconds=0.5, plan_fingerprint="a"*64, status="completed" if index == 0 else "failed", processed_through_seconds=1.5 if index == 0 else None, progress=1 if index == 0 else 0, sampled_frames=3 if index == 0 else 0, decoded_frames=3 if index == 0 else 0, observation_count=3 if index == 0 else 0, model_version="test-only" if index == 0 else None, result_fingerprint="b"*64 if index == 0 else None, finished_at=utcnow() if index == 0 else None)
            db.add(chunk)
            db.flush()
            if index == 0:
                for i in range(3):
                    db.add(TrackObservation(workspace_id=match["workspace_id"], match_id=match["id"], upload_id=source["upload"]["id"], processing_chunk_id=chunk.id, track_id=f"chunk:{chunk.id}:1", timestamp_seconds=i/2, label="player", bbox=[0.1, 0.1, 0.2, 0.2], confidence=0.8, model_version="test-only"))
        db.commit()
        assert public_job(job).result["observation_count"] == 120001
    # Durable ledger is authoritative over a stale worker counter.
    public = ok(first.get(f'/api/jobs/{job_id}'))
    assert public["processing_coverage"]["status"] == "partial"
    assert public["processing_coverage"]["covered_seconds"] == 1.5
    assert public["result"]["observation_count"] == 3
    assert "operator_private" not in public["result"]
    page = ok(first.get(f'/api/jobs/{job_id}/chunks?limit=1'))
    assert len(page["items"]) == 1 and page["next_cursor"] == '0'
    assert len(ok(first.get(f'/api/jobs/{job_id}/chunks?limit=1&cursor=0'))["items"]) == 1
    observations = ok(first.get(f'/api/jobs/{job_id}/observations?limit=2'))
    assert len(observations["items"]) == 2 and observations["next_cursor"]
    last = ok(first.get(f'/api/jobs/{job_id}/observations', params={"limit": 2, "cursor": observations["next_cursor"]}))
    assert len(last["items"]) == 1 and last["next_cursor"] is None
    assert first.get(f'/api/jobs/{job_id}/observations?limit=501').status_code == 422
    assert first.get(f'/api/jobs/{job_id}/chunks?limit=101').status_code == 422
    retry = ok(first.post(f'/api/jobs/{job_id}/retry'))
    assert retry["processing_coverage"]["covered_seconds"] == 1.5
    with get_session_factory(settings.database_url)() as db:
        saved = db.get(ProcessingJob, job_id).result_json
        assert 'error_code' not in saved and saved["metadata"]["duration_seconds"] == 3
    assert len(ok(first.get(f'/api/jobs/{job_id}/observations'))["items"]) == 3


def test_operator_artifact_activation_scope_refusal_and_current_correction(api, tiny_video, tmp_path):
    app, first, _, _ = api
    signup(first)
    actor, rival, match, roster = match_setup(first)
    oracle = snapshot()
    event_ids = []
    for index in range(2):
        if index:
            payload = {key: match[key] for key in ("title", "competition", "season", "age_group", "team_name", "opponent_name", "duration_seconds")}
            payload["match_date"] = "2025-03-02"
            match = ok(first.post('/api/matches', json=payload), 201)
            ok(first.put(f"/api/matches/{match['id']}/roster", json=roster))
            ok(first.post(f"/api/matches/{match['id']}/attestation", json=rights()), 201)
        upload(first, match, tiny_video)
        for position, source in enumerate(oracle.events[:6]):
            body = event_body(actor if source.player_id == 'home' else rival, timestamp_seconds=0.1+position*0.3,
                kind=source.kind, outcome=source.outcome, start_x=source.start_x, start_y=source.start_y,
                end_x=source.end_x, end_y=source.end_y, coordinate_system=source.coordinate_system,
                coordinate_provenance="Fictional test marks only", possession_retained=source.possession_retained, shot_outcome=source.shot_outcome)
            event_ids.append(ok(first.post(f"/api/matches/{match['id']}/events", json=body), 201)["id"])
        declaration = {"coverage": "complete_match", "eligible_actions_complete": True, "both_sides_recorded": True,
            "training_rights_cleared": True, "rights_reference": "Fictional test permission only", "source_reference": "Synthetic clip", "declared_by": "spoofed"}
        result = ok(first.put(f"/api/matches/{match['id']}/action-stream", json=declaration))
        assert result["declared_by"] != "spoofed" and result["declared_at"]
        assert ok(first.get(f"/api/matches/{match['id']}/action-stream")) == result
    current = EvidenceSnapshot.model_validate(ok(first.get('/api/intelligence/snapshot', params=QUERY)))
    artifact = fit(current, policy=policy())
    path = tmp_path / 'operator-model.json'
    path.write_text(artifact_document(artifact).model_dump_json())
    app.state.settings.action_value_artifact_path = path
    value = ok(first.get(f'/api/events/{event_ids[0]}/action-value'))
    assert value["status"] == "available" and value["value"] > 0 and value["current_evidence_checked"]
    assert value["training_matches"] == 2
    # A foreign workspace/mode/domain artifact reveals neither model metadata nor local path.
    path.write_text(artifact_document(fit(oracle, policy=policy())).model_dump_json())
    unavailable = ok(first.get(f'/api/events/{event_ids[0]}/action-value'))
    assert unavailable["value"] is None and "compatible authorized" in unavailable["reasons"][0]
    assert str(path) not in str(unavailable) and "private-test" not in str(unavailable)
    path.write_text(artifact_document(artifact).model_dump_json())
    response = first.delete(f'/api/events/{event_ids[-1]}')
    assert response.status_code == 204
    stale = ok(first.get(f'/api/events/{event_ids[0]}/action-value'))
    assert stale["value"] is None and stale["reasons"]
    # Not only the old model: deleted attempts invalidate stream completeness,
    # so an operator cannot simply refit a now-success-biased denominator.
    assert not ok(first.get(f"/api/matches/{match['id']}/action-stream"))["eligible_actions_complete"]
    incomplete = EvidenceSnapshot.model_validate(ok(first.get('/api/intelligence/snapshot', params=QUERY)))
    with pytest.raises(ActionValueUnavailable, match="complete"):
        fit(incomplete, policy=policy())
    assert first.post('/api/intelligence/model', json={"path": str(path)}).status_code == 404


def test_snapshot_is_coherent_and_unidentified_model_candidate_cannot_be_approved(api, tiny_video):
    _, first, _, settings = api
    signup(first)
    actor, _, match, _ = match_setup(first)
    upload(first, match, tiny_video)
    event = ok(first.post(f"/api/matches/{match['id']}/events", json=event_body(actor)), 201)
    scope = CohortScope(workspace_id=match['workspace_id'], **QUERY)
    # WAL writer commits between the reader's first and subsequent queries.
    with evidence_read(settings) as db:
        before = load_snapshot(db, scope)
        with get_session_factory(settings.database_url)() as writer:
            row = writer.get(ReviewedEvent, event['id'])
            row.status = 'draft'
            row.provenance = 'model_candidate'
            row.reviewed_by = None
            row.reviewed_at = None
            writer.commit()
        again = load_snapshot(db, scope)
        assert before.events[0].status == again.events[0].status == 'approved'
    with evidence_read(settings) as db:
        assert load_snapshot(db, scope).events[0].status == 'draft'
    assert first.put(f"/api/events/{event['id']}", json=event_body(actor)).status_code == 409
    assert knowledge(first, actor)['approved_event_count'] == 0


@pytest.mark.parametrize("mutation", ["create", "update", "delete"])
def test_action_stream_requires_fresh_declaration_after_evidence_changes(api, tiny_video, mutation):
    _, client, _, _ = api
    signup(client)
    actor, _, match, _ = match_setup(client)
    upload(client, match, tiny_video)
    event = ok(client.post(f"/api/matches/{match['id']}/events", json=event_body(actor)), 201)
    declaration = {"coverage": "complete_match", "eligible_actions_complete": True, "both_sides_recorded": True,
        "training_rights_cleared": True, "rights_reference": "Fictional permission", "source_reference": "Fictional fixture"}
    stream_path = f"/api/matches/{match['id']}/action-stream"
    ok(client.put(stream_path, json=declaration))
    assert ok(client.get(stream_path))["eligible_actions_complete"]
    if mutation == "create":
        ok(client.post(f"/api/matches/{match['id']}/events", json=event_body(actor, timestamp_seconds=1.5)), 201)
    elif mutation == "update":
        ok(client.put(f"/api/events/{event['id']}", json=event_body(actor, outcome="unsuccessful")))
    else:
        assert client.delete(f"/api/events/{event['id']}").status_code == 204
    assert not ok(client.get(stream_path))["eligible_actions_complete"]
    assert not ok(client.get('/api/intelligence/snapshot', params=QUERY))["matches"][0]["stream"]["eligible_actions_complete"]
    # Completeness is still an authenticated human attestation, not proof that
    # these sparse fictional rows constitute an actual whole-game stream.
    ok(client.put(stream_path, json=declaration))
    assert ok(client.get(stream_path))["eligible_actions_complete"]
    audits = ok(client.get(f"/api/matches/{match['id']}/audit"))
    assert sum(row["entity_type"] == "action_stream" for row in audits) == 2


@pytest.mark.parametrize("corruption", ["invalid_coordinate", "orphan_revision", "same_side_opponent"])
def test_skipped_legacy_action_withdraws_complete_stream_not_denominator(api, tiny_video, corruption):
    _, client, _, settings = api
    signup(client)
    actor, rival, match, _ = match_setup(client)
    upload(client, match, tiny_video)
    event = ok(client.post(f"/api/matches/{match['id']}/events", json=event_body(actor)), 201)
    declaration = {"coverage": "complete_match", "eligible_actions_complete": True, "both_sides_recorded": True,
        "training_rights_cleared": True, "rights_reference": "Fictional test permission", "source_reference": "Fictional fixture"}
    ok(client.put(f"/api/matches/{match['id']}/action-stream", json=declaration))
    assert ok(client.get('/api/intelligence/snapshot', params=QUERY))["matches"][0]["stream"]["eligible_actions_complete"]
    with get_session_factory(settings.database_url)() as db:
        row = db.get(ReviewedEvent, event['id'])
        if corruption == "invalid_coordinate":
            row.coordinate_system = "unrecognized-legacy-coordinates"
        elif corruption == "orphan_revision":
            row.identity_assignment_revision = 1
        else:
            # Out-of-band side correction violates the HTTP route's guard.
            from sqlalchemy import select
            from app.models import Appearance
            row.opponent_player_id = rival["id"]
            appearance = db.scalar(select(Appearance).where(Appearance.match_id == match["id"], Appearance.player_id == rival["id"]))
            appearance.side = "home"
        db.commit()
    # Even a mistaken fresh declaration cannot silently drop malformed rows
    # from a supposedly complete denominator at the snapshot boundary.
    ok(client.put(f"/api/matches/{match['id']}/action-stream", json=declaration))
    assert ok(client.get(f"/api/matches/{match['id']}/action-stream"))["eligible_actions_complete"]
    current = ok(client.get('/api/intelligence/snapshot', params=QUERY))
    assert current["events"] == []
    assert current["matches"][0]["stream"]["eligible_actions_complete"] is False
    # Legacy rows remain stored for an explicit corrective review, not deleted.
    with get_session_factory(settings.database_url)() as db:
        assert db.get(ReviewedEvent, event['id']) is not None


def test_same_jersey_opponents_unresolved_track_and_roster_audit_repairs(api, tiny_video):
    _, client, _, settings = api
    signup(client)
    actor, opponent, match, roster = match_setup(client)
    roster["appearances"][1]["jersey_number"] = 7
    ok(client.put(f"/api/matches/{match['id']}/roster", json=roster))
    source = upload(client, match, tiny_video)
    with get_session_factory(settings.database_url)() as db:
        db.get(ProcessingJob, source["job"]["id"]).status = "needs_review"
        for timestamp in (0.5, 1, 1.5):
            db.add(TrackObservation(workspace_id=match["workspace_id"], match_id=match["id"], upload_id=source["upload"]["id"], track_id="ambiguous-seven", timestamp_seconds=timestamp, label="player", bbox=[0.1, 0.1, 0.2, 0.2], confidence=0.99, model_version="fictional-only"))
        db.commit()
    proposal = {"upload_id": source["upload"]["id"], "track_id": "ambiguous-seven", "player_id": actor["id"], "start_seconds": 0.5, "end_seconds": 1.5, "reason": "Cannot disambiguate equal shirt numbers"}
    candidates = ok(client.post(f"/api/matches/{match['id']}/identity-candidates", json=proposal))
    assert candidates["status"] == "unresolved"
    assert len(candidates["candidates"]) == 2
    identity = ok(client.post(f"/api/matches/{match['id']}/identities", json=proposal), 201)
    assert identity["status"] == "proposed" and identity["confidence"] is None
    before = knowledge(client, actor)
    assert before["confirmed_identity_ids"] == [] and before["unresolved_identity_ids"] == [identity["id"]]
    assert knowledge(client, opponent)["confirmed_identity_ids"] == []
    # A draft event cannot acquire approved identity from detector confidence.
    event = ok(client.post(f"/api/matches/{match['id']}/events", json=event_body(actor, status="draft")), 201)
    assert client.post(f"/api/events/{event['id']}/identity", json={"assignment_id": identity["id"], "expected_revision": identity["revision"], "reason": "Still ambiguous"}).status_code == 409
    assert knowledge(client, actor)["approved_event_count"] == 0
    assert knowledge(client, opponent)["approved_event_count"] == 0
    rejected = ok(client.put(f"/api/matches/{match['id']}/identities/{identity['id']}", json={"expected_revision": identity["revision"], "player_id": actor["id"], "status": "rejected", "reason": "No independent identity evidence"}))
    assert rejected["status"] == "rejected"
    ok(client.put(f"/api/events/{event['id']}", json=event_body(actor)))
    assert knowledge(client, actor)["approved_event_count"] == 1
    assert client.delete(f"/api/events/{event['id']}").status_code == 204
    assert knowledge(client, actor)["approved_event_count"] == 0
    # Reusing/changing jerseys never changes a canonical player ID. Roster side
    # and role corrections recompute relationship meaning and historical role.
    roster["appearances"][0].update(role="forward", jersey_number=9)
    roster["appearances"][1].update(side="home", jersey_number=7)
    ok(client.put(f"/api/matches/{match['id']}/roster", json=roster))
    after = knowledge(client, actor)
    assert after["match_details"][0]["appearance_role"] == "forward"
    assert after["relationships"][0]["other_player_id"] == opponent["id"]
    assert after["relationships"][0]["relation"] == "co_rostered_teammate"
    assert after["evidence_fingerprint"] != before["evidence_fingerprint"]
    audit = ok(client.get(f"/api/matches/{match['id']}/audit"))
    assert any(r["entity_type"] == "track_identity" and r["before"] and r["before"]["status"] == "proposed" and r["after"]["status"] == "rejected" for r in audit)
    assert any(r["entity_type"] == "event" and r["action"] == "delete" and r["before"]["id"] == event["id"] for r in audit)
    assert any(r["entity_type"] == "roster" and r["before"] and r["after"] for r in audit)
