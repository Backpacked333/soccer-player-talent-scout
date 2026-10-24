"""Synthetic oracle/property fixtures, not validation of a youth prediction model."""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.intelligence.contracts import (
    CohortScope, EvidenceAppearance, EvidenceMatch, EvidenceSnapshot,
    ReviewedAction, TrackIdentityRecord,
)
from app.intelligence.memory import ReviewedPlayerKnowledge, ReviewedPlayerMemoryEngine, _conflicting_identities

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def memory_fixture() -> EvidenceSnapshot:
    scope = CohortScope(workspace_id="w", mode="demo", season="2025", age_group="U15", competition="Fixture league", role="all_roles")
    matches = [EvidenceMatch(match_id=f"m{i}", workspace_id="w", mode="demo", match_date=date(2025, 1, i), season="2025", age_group="U15", competition="Fixture league", duration_seconds=600) for i in range(1, 4)]
    appearances = []
    events = []
    for i, match in enumerate(matches):
        for player, side, jersey in (("p", "away" if i == 2 else "home", [7, 12, 9][i]), ("mate", "home", 8), ("rival", "home" if i == 2 else "away", 7)):
            appearances.append(EvidenceAppearance(appearance_id=f"{match.match_id}-{player}", match_id=match.match_id, player_id=player, workspace_id="w", mode="demo", side=side, role=["midfielder", "defender", "forward"][i] if player == "p" else "midfielder", jersey_number=jersey, played_seconds=500, reviewed_seconds=300, roster_authorized=True))
        for j, outcome in enumerate((["successful", "unsuccessful"], ["successful", "successful"], ["unsuccessful"])[i]):
            events.append(ReviewedAction(event_id=f"pass-{i}-{j}", workspace_id="w", mode="demo", match_id=match.match_id, player_id="p", kind="pass", outcome=outcome, timestamp_seconds=5 + j, status="approved", provenance="synthetic", reviewed_by="reviewer", reviewed_at=NOW, updated_at=NOW))
        events.append(ReviewedAction(event_id=f"duel-{i}", workspace_id="w", mode="demo", match_id=match.match_id, player_id="rival" if i == 1 else "p", opponent_player_id="p" if i == 1 else "mate" if i == 2 else "rival", kind="duel", contest_id=f"contest-{i}", outcome="successful", timestamp_seconds=10, status="approved", provenance="synthetic", reviewed_by="reviewer", reviewed_at=NOW, updated_at=NOW))
    return EvidenceSnapshot(scope=scope, matches=matches, appearances=appearances, events=events)


def decision(**overrides) -> TrackIdentityRecord:
    fields = dict(id="identity", workspace_id="w", mode="demo", match_id="m1", upload_id="u1", track_id="7", player_id="p", start_seconds=0, end_seconds=20, status="confirmed", source="reviewer", reason="Private footage reviewed against authorized roster", revision=1, created_by="reviewer", reviewed_by="reviewer", created_at=NOW, reviewed_at=NOW, updated_at=NOW)
    return TrackIdentityRecord(**(fields | overrides))


def changed(snapshot: EvidenceSnapshot, **kwargs) -> EvidenceSnapshot:
    return EvidenceSnapshot.model_validate(snapshot.model_dump() | kwargs)


def build(snapshot=None, **kwargs):
    return ReviewedPlayerMemoryEngine().build(snapshot or memory_fixture(), player_id="p", **kwargs)


def test_three_games_accumulate_canonical_history_without_new_jersey_identity():
    snapshot = memory_fixture()
    for count in (1, 2, 3):
        ids = {f"m{i}" for i in range(1, count + 1)}
        subset = changed(snapshot, matches=[m for m in snapshot.matches if m.match_id in ids], appearances=[a for a in snapshot.appearances if a.match_id in ids], events=[e for e in snapshot.events if e.match_id in ids])
        knowledge = build(subset)
        assert knowledge.roster_match_count == knowledge.reviewed_match_count == count
        assert len(knowledge.history) == count
    assert knowledge.approved_event_count == 7  # Incoming opponent duel is NOT an invented player action.
    assert knowledge.history[-1].cumulative_pass_attempts == 5
    assert knowledge.history[-1].cumulative_pass_successes == 3
    assert knowledge.history[-1].observed_pass_completion == 0.6
    assert knowledge.history[-1].coverage_fraction == 0.6
    assert knowledge.first_observed_date == date(2025, 1, 1)
    assert knowledge.last_observed_date == date(2025, 1, 3)
    assert [row.jersey_number for row in knowledge.match_details] == [7, 12, 9]
    assert [row.side for row in knowledge.match_details] == ["home", "home", "away"]
    assert [row.appearance_role for row in knowledge.history] == ["midfielder", "defender", "forward"]
    assert {row.appearance_role for row in knowledge.role_segments} == {"midfielder", "defender", "forward"}
    assert knowledge.predictive_model_status == "not_trained_or_validated"
    assert ReviewedPlayerKnowledge.model_validate_json(knowledge.model_dump_json()) == knowledge


def test_relationships_mean_roster_presence_or_reviewed_duels_not_shared_field_time():
    knowledge = build()
    edges = {(row.other_player_id, row.relation): row for row in knowledge.relationship_details}
    assert edges["mate", "co_rostered_teammate"].match_count == 2
    assert edges["mate", "co_rostered_opponent"].match_ids == ["m3"]
    assert edges["rival", "co_rostered_opponent"].match_count == 3
    duel = edges["rival", "direct_duel"]
    assert duel.match_ids == ["m1", "m2"]
    assert duel.event_ids == ["duel-0", "duel-1"]
    assert duel.explicit_contest_count == duel.direct_event_count == 2
    assert duel.first_match_date == date(2025, 1, 1)
    assert duel.last_match_date == date(2025, 1, 2)
    assert "not simultaneous field time" in edges["mate", "co_rostered_teammate"].interpretation
    assert not any("partnership" == row.relation for row in knowledge.relationships)


def test_as_of_prevents_future_matches_roles_peers_and_fingerprint_leakage():
    snapshot = memory_fixture()
    original = build(snapshot, as_of=date(2025, 1, 2))
    payload = snapshot.model_dump()
    payload["events"][-1]["outcome"] = "unsuccessful"
    payload["appearances"][-3]["role"] = "goalkeeper"
    payload["identities"] = [decision(id="future", match_id="m3").model_dump()]
    assert build(EvidenceSnapshot.model_validate(payload), as_of=date(2025, 1, 2)) == original
    empty = build(snapshot, as_of=date(2024, 12, 31))
    assert empty.history == empty.relationships == []
    assert empty.first_observed_date is empty.last_observed_date is None
    assert all(row.observed_rate is row.posterior_mean is None for row in empty.tendencies)
    assert original.last_observed_date == date(2025, 1, 2)


def test_order_invariance_for_all_evidence_collections():
    original = memory_fixture()
    original = changed(original, identities=[decision(id="a", status="proposed", reviewed_by=None, reviewed_at=None), decision(id="b", match_id="m2")])
    expected = build(original)
    for seed in range(12):
        data = original.model_dump()
        rng = random.Random(seed)
        for key in ("matches", "appearances", "events", "identities"):
            rng.shuffle(data[key])
        assert build(EvidenceSnapshot.model_validate(data)) == expected


def test_role_filter_is_historical_and_other_cohorts_are_rejected():
    snapshot = memory_fixture()
    defender = changed(snapshot, scope=snapshot.scope.model_dump() | {"role": "defender"})
    result = build(defender)
    assert [row.match_id for row in result.history] == ["m2"]
    assert result.history[-1].observed_pass_completion == 1
    assert result.approved_event_count == 2
    assert result.role_segments[0].appearance_role == "defender"
    for field, value in (("season", "2024"), ("competition", "Other"), ("age_group", "U17"), ("workspace_id", "foreign"), ("mode", "real")):
        unchecked = snapshot.model_copy(deep=True)
        setattr(unchecked.matches[0], field, value)
        with pytest.raises(ValidationError):
            build(unchecked)


def test_beta_oracle_no_prior_only_estimates_and_no_fake_opportunities():
    snapshot = memory_fixture()
    single = changed(snapshot, events=[snapshot.events[0]])
    result = build(single)
    rate = next(row for row in result.tendencies if row.kind == "pass")
    assert rate.posterior_mean == pytest.approx(2 / 3)
    assert rate.credible_low == pytest.approx(math.sqrt(0.025), abs=1e-12)
    assert rate.credible_high == pytest.approx(math.sqrt(0.975), abs=1e-12)
    assert rate.prior_alpha == rate.prior_beta == 1
    assert rate.distinct_matches == 1 and rate.max_match_opportunity_share == 1
    assert result.history[-1].cumulative_matches == 1
    assert result.roster_match_count == 3
    assert result.history[-1].cumulative_reviewed_events == 1
    missing = next(row for row in result.tendencies if row.kind == "shot")
    assert missing.attempts == 0
    assert missing.observed_rate is missing.posterior_mean is missing.credible_low is missing.credible_high is None
    assert missing.status == "unavailable"


def test_event_corrections_deletions_and_reassignment_repair_derived_memory():
    original = memory_fixture()
    before = build(original)
    payload = original.model_dump()
    payload["events"][0]["player_id"] = "mate"
    payload["events"][1]["outcome"] = "successful"
    payload["events"] = [row for row in payload["events"] if row["event_id"] != "duel-0"]
    after = build(EvidenceSnapshot.model_validate(payload))
    assert after.evidence_fingerprint != before.evidence_fingerprint
    assert after.history[-1].cumulative_pass_attempts == 4
    assert after.history[-1].cumulative_pass_successes == 3
    assert after.approved_event_count == before.approved_event_count - 2
    rival_duel = next(row for row in after.relationship_details if row.other_player_id == "rival" and row.relation == "direct_duel")
    assert rival_duel.match_ids == ["m2"]
    assert rival_duel.first_match_date == date(2025, 1, 2)


def test_identity_confirmation_does_not_approve_events_and_stale_revision_excluded():
    snapshot = memory_fixture()
    payload = snapshot.model_dump()
    payload["identities"] = [decision().model_dump()]
    payload["events"][0] |= {"identity_assignment_id": "identity", "identity_assignment_revision": 1}
    payload["events"][1] |= {"status": "draft", "reviewed_by": None, "reviewed_at": None}
    valid = build(EvidenceSnapshot.model_validate(payload))
    assert valid.confirmed_identity_ids == ["identity"]
    assert valid.exclusion_reasons["pass-0-1"] == "not_reviewer_approved"
    payload["identities"][0]["revision"] = 2
    stale = build(EvidenceSnapshot.model_validate(payload))
    assert stale.exclusion_reasons["pass-0-0"] == "identity_revision_or_actor_mismatch"
    assert stale.history[-1].cumulative_pass_attempts == valid.history[-1].cumulative_pass_attempts - 1
    payload["identities"][0]["status"] = "proposed"
    unresolved = build(EvidenceSnapshot.model_validate(payload))
    assert unresolved.unresolved_identity_ids == ["identity"]
    assert unresolved.confirmed_identity_ids == []


def test_conflicting_identity_intervals_exclude_both_not_choose_largest_confidence():
    snapshot = memory_fixture()
    payload = snapshot.model_dump()
    payload["identities"] = [decision(confidence=0.99).model_dump(), decision(id="other", player_id="mate", confidence=0.2).model_dump()]
    payload["events"][0] |= {"identity_assignment_id": "identity", "identity_assignment_revision": 1}
    result = build(EvidenceSnapshot.model_validate(payload))
    assert result.confirmed_identity_ids == []
    assert result.unresolved_identity_ids == ["identity"]
    assert result.exclusion_reasons["pass-0-0"] == "identity_missing_unconfirmed_or_conflicting"


def test_interval_sweep_matches_quadratic_oracle():
    for seed in range(20):
        rng = random.Random(seed)
        rows = [decision(id=f"i{i}", start_seconds=(start := rng.randrange(100)), end_seconds=start + rng.randrange(10), track_id=str(rng.randrange(3)), player_id=str(rng.randrange(4))) for i in range(35)]
        expected = {a.id for a in rows for b in rows if a.id != b.id and (a.track_id == b.track_id or a.player_id == b.player_id) and a.start_seconds <= b.end_seconds and b.start_seconds <= a.end_seconds}
        assert _conflicting_identities(rows) == expected


def test_duplicate_evidence_and_contradictory_contests_do_not_inflate_support():
    snapshot = memory_fixture()
    payload = snapshot.model_dump()
    payload["events"].append(payload["events"][0] | {"event_id": "zz-duplicate"})
    result = build(EvidenceSnapshot.model_validate(payload))
    assert result.approved_event_count == build(snapshot).approved_event_count
    assert result.duplicate_event_ids == ["zz-duplicate"]
    duel = next(row for row in payload["events"] if row["event_id"] == "duel-0")
    payload["events"].append(duel | {"event_id": "contradiction", "outcome": "unsuccessful"})
    conflict = build(EvidenceSnapshot.model_validate(payload))
    assert conflict.exclusion_reasons["duel-0"] == "ambiguous_or_contradictory_contest"
    assert conflict.exclusion_reasons["contradiction"] == "ambiguous_or_contradictory_contest"
    assert next(row for row in conflict.relationship_details if row.other_player_id == "rival" and row.relation == "direct_duel").match_ids == ["m2"]
    payload["events"].append(payload["events"][0])
    with pytest.raises(ValidationError, match="duplicate event IDs"):
        EvidenceSnapshot.model_validate(payload)


def test_mirrored_direct_duel_is_one_explicit_contest_without_inventing_actor_result():
    snapshot = memory_fixture()
    duel = next(row for row in snapshot.events if row.event_id == "duel-0")
    mirrored = duel.model_dump() | {"event_id": "mirror", "player_id": "rival", "opponent_player_id": "p", "outcome": "unsuccessful"}
    result = build(changed(snapshot, events=[*snapshot.events, mirrored]))
    edge = next(row for row in result.relationship_details if row.other_player_id == "rival" and row.relation == "direct_duel")
    assert edge.explicit_contest_count == 2
    assert edge.direct_event_count == 3
    assert result.approved_event_count == build(snapshot).approved_event_count


def test_absent_exposure_does_not_invent_coverage_and_out_of_bounds_events_excluded():
    snapshot = memory_fixture()
    payload = snapshot.model_dump()
    for row in payload["appearances"]:
        row["played_seconds"] = row["reviewed_seconds"] = 0
    payload["events"][0]["timestamp_seconds"] = 601
    result = build(EvidenceSnapshot.model_validate(payload))
    assert all(row.coverage_fraction is None for row in result.history)
    assert result.exclusion_reasons["pass-0-0"] == "outside_match_duration"
    assert all(row.coverage_fraction is None for row in result.role_segments)


def test_conflicting_duplicate_pass_outcomes_excluded_not_two_opportunities():
    snapshot = memory_fixture()
    copy = snapshot.events[0].model_dump() | {"event_id": "conflicting-copy", "outcome": "unsuccessful"}
    result = build(changed(snapshot, events=[*snapshot.events, copy]))
    assert result.exclusion_reasons["pass-0-0"] == "ambiguous_duplicate_outcomes"
    assert result.exclusion_reasons["conflicting-copy"] == "ambiguous_duplicate_outcomes"
    assert result.history[-1].cumulative_pass_attempts == 4


def test_same_explicit_actor_contest_cannot_inflate_count_via_annotation_metadata():
    snapshot = memory_fixture()
    duel = next(row for row in snapshot.events if row.event_id == "duel-0")
    copy = duel.model_dump() | {"event_id": "zz-contest-copy", "start_x": 0.3, "start_y": 0.4}
    result = build(changed(snapshot, events=[*snapshot.events, copy]))
    assert result.approved_event_count == build(snapshot).approved_event_count
    assert result.duplicate_event_ids == ["zz-contest-copy"]


def test_relationship_bound_fails_closed_not_silently_truncated(monkeypatch):
    monkeypatch.setattr("app.intelligence.memory.MAX_RELATIONSHIPS", 1)
    with pytest.raises(ValueError, match="relationship graph exceeds"):
        build()
