"""Contract/property tests only. Fixtures are synthetic, not model validation."""
from __future__ import annotations

import random
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.intelligence import (
    ActionValueArtifact, ActionValuePolicy, ActionValueResult, CohortScope,
    DuelRatingEstimate, DuelRatingPolicy, DuelRatingReport, EvaluationReport, EvidenceAppearance,
    EvidenceMatch, EvidenceSnapshot, FitDiagnostics, RelationshipEvidence,
    ReviewedAction, StreamDeclaration, TrackIdentityCorrection, LongitudinalPoint,
    TrackIdentityRecord, canonical_fingerprint, chronological_match_split,
)
from app.schemas import EventWrite

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def scope(**extra):
    return CohortScope(workspace_id="workspace", mode="demo", season="2025", age_group="U15", competition="Synthetic league", **({"role": "all_roles"} | extra))


def match(identifier="m1", day=1, **extra):
    return EvidenceMatch(match_id=identifier, workspace_id="workspace", mode="demo", match_date=date(2025, 1, day), season="2025", age_group="U15", competition="Synthetic league", duration_seconds=600, **extra)


def roster(player="p1", side="home", **extra):
    return EvidenceAppearance(appearance_id=f"appearance-{player}", player_id=player, match_id="m1", workspace_id="workspace", mode="demo", side=side, role="midfielder", played_seconds=600, reviewed_seconds=300, roster_authorized=True, **extra)


def action(**extra):
    fields = dict(event_id="e1", workspace_id="workspace", mode="demo", match_id="m1", player_id="p1", kind="pass", outcome="successful", timestamp_seconds=5, status="approved", reviewed_by="reviewer", reviewed_at=NOW, updated_at=NOW, provenance="synthetic")
    return ReviewedAction(**(fields | extra))


def artifact(**extra):
    fields = dict(scope=scope(), policy=ActionValuePolicy(grid_columns=2, grid_rows=2), trained_at=NOW, training_match_ids=["m1", "m2", "m3"], training_fingerprint="a" * 64, training_action_count=200, training_shot_count=20, training_goal_count=3, rights_references=["synthetic-fixture"], cell_values=[0.1, 0.2, 0.3, 0.4], cell_action_counts=[50, 50, 50, 50], diagnostics=FitDiagnostics(converged=True, iterations=10, max_residual=1e-12))
    return ActionValueArtifact(**(fields | extra))


def test_legacy_event_defaults_never_infer_spatial_or_goal_semantics():
    event = EventWrite(player_id=str(uuid4()), kind="shot", outcome="successful", status="approved", timestamp_seconds=5, start_x=0.5, start_y=0.5)
    assert event.coordinate_system == "unknown"
    assert event.possession_retained is None
    assert event.shot_outcome is None
    assert event.period is None
    assert event.attacking_direction is None
    assert event.contest_id is None


@pytest.mark.parametrize("fields", [
    {"coordinate_system": "image_pixels"},
    {"coordinate_system": "attack_normalized"},
    {"coordinate_system": "pitch_normalized", "coordinate_provenance": "manual pitch mark"},
    {"coordinate_system": "attack_normalized", "coordinate_provenance": "provider", "attacking_direction": "right_to_left"},
    {"shot_outcome": "goal"},
    {"contest_id": "contest-one"},
    {"possession_retained": "true"},
    {"start_x": float("nan"), "start_y": 0.5},
    {"start_x": 0.5},
    {"period": 0},
])
def test_event_semantics_fail_closed(fields):
    with pytest.raises(ValidationError):
        EventWrite(player_id=str(uuid4()), kind="pass", timestamp_seconds=5, **fields)


def test_both_supported_coordinate_conventions_require_real_provenance():
    for fields in ({"coordinate_system": "attack_normalized", "coordinate_provenance": "reviewer pitch click; normalized to attack"}, {"coordinate_system": "pitch_normalized", "coordinate_provenance": "calibration-42", "period": 2, "attacking_direction": "right_to_left"}):
        value = EventWrite(player_id=str(uuid4()), kind="pass", timestamp_seconds=5, **fields)
        assert value.coordinate_provenance


def test_arbitrary_identity_link_not_accepted_by_existing_write_endpoint_schema():
    with pytest.raises(ValidationError):
        EventWrite(player_id=str(uuid4()), kind="pass", timestamp_seconds=5, identity_assignment_id=str(uuid4()), identity_assignment_revision=1)
    with pytest.raises(ValidationError):
        action(identity_assignment_id="assignment")


def test_complete_stream_is_an_audited_declaration_not_a_default():
    assert StreamDeclaration().coverage == "unknown"
    with pytest.raises(ValidationError):
        StreamDeclaration(coverage="complete_match")
    with pytest.raises(ValidationError):
        StreamDeclaration(training_rights_cleared=True)
    declaration = StreamDeclaration(coverage="complete_match", eligible_actions_complete=True, both_sides_recorded=True, declared_by="reviewer", declared_at=NOW, source_reference="synthetic-complete-game", training_rights_cleared=True, rights_reference="synthetic-fixture")
    assert StreamDeclaration.model_validate_json(declaration.model_dump_json()) == declaration


def test_snapshot_validates_opposite_side_and_authorized_scope():
    original = EvidenceSnapshot(scope=scope(), matches=[match()], appearances=[roster(), roster("p2", "away")], events=[action(opponent_player_id="p2")])
    assert original.events[0].opponent_player_id == "p2"
    for path, value in [("workspace_id", "foreign"), ("mode", "real"), ("season", "2024"), ("age_group", "U17"), ("competition", "Other league")]:
        changed = original.model_dump(mode="json")
        changed["matches"][0][path] = value
        with pytest.raises(ValidationError):
            EvidenceSnapshot.model_validate(changed)
    for key, value in [("side", "home"), ("roster_authorized", False), ("mode", "real")]:
        changed = original.model_dump(mode="json")
        changed["appearances"][1][key] = value
        with pytest.raises(ValidationError):
            EvidenceSnapshot.model_validate(changed)
    changed = original.model_dump(mode="json")
    changed["events"].append(changed["events"][0])
    with pytest.raises(ValidationError):
        EvidenceSnapshot.model_validate(changed)
    with pytest.raises(ValidationError):
        EvidenceSnapshot(scope=scope(), matches=[match()], appearances=[roster()], events=[action(player_id="not-on-roster")])


def test_approved_synthetic_events_cannot_be_relabelled_real():
    with pytest.raises(ValidationError):
        action(mode="real")
    with pytest.raises(ValidationError):
        action(reviewed_by=None)


def test_temporal_split_is_permutation_invariant_whole_date_grouped():
    values = [match(f"m{n}", day) for n, day in enumerate([1, 2, 2, 3, 3, 4])]
    expected = chronological_match_split(values, test_fraction=0.5)
    for seed in range(20):
        shuffled = values.copy()
        random.Random(seed).shuffle(shuffled)
        assert chronological_match_split(shuffled, test_fraction=0.5) == expected
    train, test = expected
    assert set(train).isdisjoint(test)
    assert set(train) | set(test) == {row.match_id for row in values}
    assert max(row.match_date for row in values if row.match_id in train) < min(row.match_date for row in values if row.match_id in test)
    for invalid in ([], [match()], [match("a"), match("b")], [match("a", 1), match("a", 2)]):
        with pytest.raises(ValueError):
            chronological_match_split(invalid)
    for fraction in (0, 1, -1, float("nan")):
        with pytest.raises(ValueError):
            chronological_match_split(values, test_fraction=fraction)


def test_canonical_fingerprint_order_corrections_and_nonfinite_protection():
    assert canonical_fingerprint({"a": 1, "b": 2}) == canonical_fingerprint({"b": 2, "a": 1})
    assert canonical_fingerprint(action()) != canonical_fingerprint(action(outcome="unsuccessful"))
    assert canonical_fingerprint(["a", "b"]) != canonical_fingerprint(["b", "a"])
    with pytest.raises(ValueError):
        canonical_fingerprint({"score": float("nan")})


def test_json_artifact_round_trip_shape_scope_finite_and_version_validation():
    value = artifact()
    assert ActionValueArtifact.model_validate_json(value.model_dump_json()) == value
    for update in ({"cell_values": [0.1]}, {"cell_action_counts": [-1, 100, 100, 1]}, {"cell_action_counts": [50, 50, 50, 51]}, {"cell_values": [0.1, 0.2, float("nan"), 0.3]}, {"schema_version": "unknown"}, {"training_goal_count": 21}, {"training_match_ids": ["m1", "m1"]}, {"diagnostics": {"converged": False, "iterations": 10}}, {"training_fingerprint": "not-a-digest"}, {"validation_status": "validated_youth_model"}, {"weights_pickle": "unsafe"}):
        with pytest.raises(ValidationError):
            ActionValueArtifact.model_validate(value.model_dump() | update)


def test_no_numeric_values_for_missing_model_or_unobserved_duels():
    assert ActionValueResult(event_id="e1", status="unavailable").value is None
    with pytest.raises(ValidationError):
        ActionValueResult(event_id="e1", status="unavailable", value=0)
    with pytest.raises(ValidationError):
        ActionValueResult(event_id="e1", status="available", value=0.1)
    estimate = dict(player_id="p1", contests=0, matches=0, distinct_opponents=0, eligibility="unavailable")
    assert DuelRatingEstimate(**estimate).log_odds is None
    with pytest.raises(ValidationError):
        DuelRatingEstimate(**estimate, log_odds=0)
    with pytest.raises(ValidationError):
        DuelRatingEstimate(player_id="p1", contests=20, matches=4, distinct_opponents=3, component_rank=1, eligibility="eligible")
    for fields in ({"max_players": 129}, {"max_contests": 10001}, {"max_iterations": 201}, {"prior_standard_deviation": 0}):
        with pytest.raises(ValidationError):
            DuelRatingPolicy(**fields)


def test_duel_estimates_require_approximate_intervals_and_converged_fits():
    fields = dict(player_id="p1", contests=20, matches=4, distinct_opponents=3, component_id="component", eligibility="eligible", log_odds=0.2, posterior_sd=0.3, interval_low=-0.4, interval_high=0.8)
    estimate = DuelRatingEstimate(**fields)
    for update in ({"posterior_sd": None}, {"interval_high": 0.1}, {"component_id": None}, {"eligibility": "unavailable"}, {"log_odds": None}):
        with pytest.raises(ValidationError):
            DuelRatingEstimate(**(fields | update))
    report_fields = dict(scope=scope(), policy=DuelRatingPolicy(), evidence_fingerprint="a" * 64, ratings=[estimate], diagnostics=FitDiagnostics(converged=True, iterations=10), accepted_contests=20)
    assert DuelRatingReport(**report_fields).ratings == [estimate]
    for update in ({"diagnostics": FitDiagnostics(converged=False, iterations=100)}, {"policy": DuelRatingPolicy(min_contests=21)}, {"ratings": [estimate, estimate]}, {"accepted_contests": 19}, {"policy": DuelRatingPolicy(max_contests=19)}):
        with pytest.raises(ValidationError):
            DuelRatingReport(**(report_fields | update))


def evaluation(**extra):
    fields = dict(status="evaluated", mode="demo", train_match_ids=["m1", "m2", "m3"], test_match_ids=["m4"], train_through=date(2025, 1, 3), test_from=date(2025, 1, 4), target="Synthetic fixture binary label", sample_count=20, model_brier=0.2, baseline_brier=0.25, baseline_description="train-only empirical rate")
    return EvaluationReport(**(fields | extra))


def test_reports_and_artifacts_refuse_evaluation_leakage_and_missing_baseline():
    assert evaluation().mode == "demo"
    for fields in ({"test_match_ids": ["m1"]}, {"test_from": date(2025, 1, 3)}, {"baseline_brier": None}, {"sample_count": 0}, {"status": "unavailable"}):
        with pytest.raises(ValidationError):
            evaluation(**fields)
    with pytest.raises(ValidationError):
        artifact(evaluation=evaluation(train_match_ids=["m2", "m3"], test_match_ids=["m1"]))
    with pytest.raises(ValidationError):
        artifact(evaluation=evaluation(mode="real"))
    assert artifact(evaluation=evaluation()).evaluation.status == "evaluated"


def test_identity_requires_audited_decision_and_revision_and_relations_need_evidence():
    fields = dict(id="identity", workspace_id="workspace", mode="demo", match_id="m1", upload_id="upload", track_id="track", start_seconds=0, end_seconds=10, player_id="p1", reason="Reviewed jersey and roster", revision=1, created_by="reviewer", created_at=NOW, updated_at=NOW)
    assert TrackIdentityRecord(**fields).status == "proposed"
    with pytest.raises(ValidationError):
        TrackIdentityRecord(**fields, status="confirmed")
    with pytest.raises(ValidationError):
        TrackIdentityCorrection(expected_revision=0, player_id="p1", status="confirmed", reason="reviewed")
    assert TrackIdentityRecord(**fields, status="confirmed", reviewed_by="reviewer", reviewed_at=NOW).revision == 1
    with pytest.raises(ValidationError):
        RelationshipEvidence(player_id="p1", other_player_id="p2", relation="direct_duel", match_ids=["m1"], first_match_date=date(2025, 1, 1), last_match_date=date(2025, 1, 1))
    with pytest.raises(ValidationError):
        RelationshipEvidence(player_id="p1", other_player_id="p2", relation="tactical_chemistry", match_ids=["m1"], first_match_date=date(2025, 1, 1), last_match_date=date(2025, 1, 1))


def test_longitudinal_rates_need_actual_denominators_and_scope_is_explicit():
    fields = dict(match_id="m1", match_date=date(2025, 1, 1), appearance_role="midfielder", age_group="U15", cumulative_reviewed_events=0, cumulative_matches=1, cumulative_pass_attempts=0, cumulative_pass_successes=0, cumulative_played_seconds=0, cumulative_reviewed_seconds=0)
    assert LongitudinalPoint(**fields).observed_pass_completion is None
    for update in ({"observed_pass_completion": 0}, {"coverage_fraction": 0.5}, {"cumulative_pass_successes": 1}, {"cumulative_reviewed_seconds": 1}):
        with pytest.raises(ValidationError):
            LongitudinalPoint(**(fields | update))
    good = fields | dict(cumulative_reviewed_events=4, cumulative_pass_attempts=2, cumulative_pass_successes=1, cumulative_played_seconds=600, cumulative_reviewed_seconds=300, observed_pass_completion=0.5, coverage_fraction=0.5)
    assert LongitudinalPoint(**good).coverage_fraction == 0.5
    without_role = scope().model_dump(exclude={"role"})
    with pytest.raises(ValidationError):
        CohortScope.model_validate(without_role)


def test_artifact_cannot_bypass_its_own_sufficiency_policy():
    for fields in ({"training_match_ids": ["m1"]}, {"training_shot_count": 3}, {"training_goal_count": 1}, {"rights_references": [""]}):
        with pytest.raises(ValidationError):
            artifact(**fields)


def test_new_event_semantics_persist_and_correct_through_existing_private_api(api, tiny_video):
    from test_api_workflows import event_body, match_setup, signup, upload

    _app, client, _outsider, _settings = api
    signup(client)
    actor, _opponent, match_record, _roster = match_setup(client)
    upload(client, match_record, tiny_video)
    endpoint = f"/api/matches/{match_record['id']}/events"
    fields = dict(kind="shot", coordinate_system="attack_normalized", coordinate_provenance="Synthetic test manual pitch mark", start_x=0.8, start_y=0.4, shot_outcome="no_goal")
    response = client.post(endpoint, json=event_body(actor, **fields))
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["shot_outcome"] == "no_goal"
    assert created["outcome"] == "successful"  # Generic success is a separate fact.
    assert created["possession_retained"] is None
    response = client.put(f"/api/events/{created['id']}", json=event_body(actor, **(fields | {"shot_outcome": "goal"})))
    assert response.status_code == 200, response.text
    assert client.get(endpoint).json()[0]["shot_outcome"] == "goal"
    audits = client.get(f"/api/matches/{match_record['id']}/audit").json()
    corrected = next(row for row in audits if row["action"] == "correct")
    assert corrected["before"]["shot_outcome"] == "no_goal"
    assert corrected["after"]["shot_outcome"] == "goal"
    assert corrected["after"]["coordinate_provenance"] == fields["coordinate_provenance"]
