"""Synthetic mathematical/safety proofs only; NOT empirical youth validation."""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timezone
from fractions import Fraction

import pytest
from pydantic import ValidationError

from app.intelligence.action_value import (
    ActionValueUnavailable, SpatialMarkovEngine, artifact_document,
    artifact_fingerprint, cell_index, evaluate, fit, load_artifact_json,
    normalized_points, score, score_from_snapshot, subset_snapshot,
    training_fingerprint, validate_artifact,
)
from app.intelligence.contracts import (
    ActionValueEngine, ActionValuePolicy, CohortScope, EvidenceAppearance,
    EvidenceMatch, EvidenceSnapshot, ReviewedAction, StreamDeclaration,
    TrackIdentityRecord, canonical_fingerprint,
)

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)
A, B, C = (0.25, 0.25), (0.75, 0.25), (0.25, 0.75)


def policy(**extra):
    return ActionValuePolicy(**(dict(grid_columns=2, grid_rows=2, min_training_matches=2, min_training_actions=10, min_training_shots=2, min_training_goals=1, min_cell_actions=2) | extra))


def snapshot(matches=2, dates=None):
    scope = CohortScope(workspace_id="private-test", mode="demo", season="2025", age_group="U15", competition="synthetic oracle only", role="all_roles")
    evidence_matches, appearances, events = [], [], []
    for n in range(matches):
        match_id = f"m{n}"
        evidence_matches.append(EvidenceMatch(match_id=match_id, workspace_id=scope.workspace_id, mode="demo", match_date=date(2025, 1, dates[n] if dates else n + 1), season=scope.season, age_group=scope.age_group, competition=scope.competition, duration_seconds=600, stream=StreamDeclaration(coverage="complete_match", eligible_actions_complete=True, both_sides_recorded=True, training_rights_cleared=True, rights_reference="synthetic-unit-test-permission-not-real-footage", declared_by="fixture", declared_at=NOW, source_reference=f"synthetic-{match_id}")))
        for side in ("home", "away"):
            appearances.append(EvidenceAppearance(appearance_id=f"{match_id}-{side}", match_id=match_id, player_id=side, workspace_id=scope.workspace_id, mode="demo", side=side, role="midfielder", played_seconds=600, reviewed_seconds=600, roster_authorized=True))
        # Each match: A has 2 retained moves A->B and 1 failed move;
        # B has one goal, one non-goal shot, one retained move B->A.
        for i, (kind, success, start, end, goal) in enumerate([
            ("pass", True, A, B, None), ("carry", True, A, B, None),
            ("cross", False, A, None, None), ("shot", True, B, None, "goal"),
            ("shot", True, B, None, "no_goal"), ("pass", True, B, A, None),
        ]):
            events.append(ReviewedAction(event_id=f"{match_id}-e{i}", workspace_id=scope.workspace_id, mode="demo", match_id=match_id, player_id="home" if i % 2 == 0 else "away", kind=kind, outcome="successful" if success else "unsuccessful", timestamp_seconds=10 * (i + 1), start_x=start[0], start_y=start[1], end_x=end[0] if end else None, end_y=end[1] if end else None, status="approved", reviewed_by="fixture", reviewed_at=NOW, updated_at=NOW, provenance="synthetic", coordinate_system="attack_normalized", coordinate_provenance="synthetic normalized pitch marks", possession_retained=success if kind != "shot" else None, shot_outcome=goal))
    return EvidenceSnapshot(scope=scope, matches=evidence_matches, appearances=appearances, events=events)


def edit_event(value, index, **changes):
    data = value.model_dump()
    data["events"][index].update(changes)
    return EvidenceSnapshot.model_validate(data)


def identity_snapshot():
    value = snapshot()
    identity = TrackIdentityRecord(id="identity", workspace_id=value.scope.workspace_id, mode="demo", match_id="m0", upload_id="upload", track_id="track", start_seconds=0, end_seconds=100, player_id="home", status="confirmed", source="reviewer", reason="fixture confirmation", revision=1, created_by="fixture", created_at=NOW, reviewed_by="fixture", reviewed_at=NOW, updated_at=NOW)
    data = value.model_dump() | {"identities": [identity.model_dump()]}
    data["events"][0].update(identity_assignment_id="identity", identity_assignment_revision=1)
    return EvidenceSnapshot.model_validate(data)


def test_two_state_analytic_fixed_point_and_negative_differences():
    value = snapshot()
    engine: ActionValueEngine = SpatialMarkovEngine()
    artifact = engine.fit(value, policy=policy())
    # VA=4VB/7; VB=(2+2VA)/7 => VA=8/41 and VB=14/41.
    assert artifact.cell_values == pytest.approx([8 / 41, 14 / 41, 0, 0], abs=2e-10)
    assert artifact.cell_action_counts == [6, 6, 0, 0]
    assert artifact.training_action_count == 12
    assert artifact.training_shot_count == 4
    assert artifact.training_goal_count == 2  # four 'successful' shots != four goals
    assert artifact.validation_status == "unvalidated_research"
    assert artifact.diagnostics.max_residual <= artifact.policy.convergence_tolerance
    assert score(value.events[0], scope=value.scope, artifact=artifact).value == pytest.approx(6 / 41)
    result = score_from_snapshot(value, event_id="m0-e5", artifact=artifact)
    assert result.value == pytest.approx(-6 / 41)
    assert result.current_evidence_checked and result.evidence_ids == ["m0-e5"]
    assert (result.start_cell_actions, result.end_cell_actions, result.training_matches) == (6, 6, 2)
    same_cell = value.events[0].model_copy(update={"end_x": A[0], "end_y": A[1]})
    assert score(same_cell, scope=value.scope, artifact=artifact).value == 0


def test_failure_attempts_are_denominator_evidence_not_discarded():
    value = snapshot()
    original = fit(value, policy=policy())
    changed = value.model_dump()
    for event in changed["events"]:
        if event["outcome"] == "unsuccessful":
            event.update(outcome="successful", possession_retained=True, end_x=B[0], end_y=B[1])
    retained = fit(EvidenceSnapshot.model_validate(changed), policy=policy())
    assert retained.cell_values[0] > original.cell_values[0]
    assert retained.training_action_count == original.training_action_count
    terminal = edit_event(value, 0, possession_retained=False)
    assert fit(terminal, policy=policy()).cell_values[0] < original.cell_values[0]
    assert score(terminal.events[0], scope=value.scope, artifact=original).value is None


def test_boundary_axes_rotation_and_reflection_oracle():
    p = policy()
    for point, expected in [((0, 0), 0), ((1, 0), 1), ((0, 1), 2), ((1, 1), 3), ((0.5, 0.5), 3), ((0.499999, 0.499999), 0)]:
        assert cell_index(point, p) == expected
    value = snapshot()
    physical = value.model_dump()
    for event in physical["events"]:
        event.update(coordinate_system="pitch_normalized", period=2, attacking_direction="right_to_left" if event["player_id"] == "home" else "left_to_right")
        for key in ("start_x", "start_y", "end_x", "end_y"):
            if event[key] is not None and event["player_id"] == "home":
                event[key] = 1 - event[key]
    rotated = EvidenceSnapshot.model_validate(physical)
    assert normalized_points(rotated.events[0]) == (A, B)
    assert fit(rotated, policy=p).cell_values == fit(value, policy=p).cell_values
    # Reflection across y permutes rows; it must not reverse x or value changes.
    reflected = value.model_dump()
    for event in reflected["events"]:
        for key in ("start_y", "end_y"):
            if event[key] is not None:
                event[key] = 1 - event[key]
    mirror = EvidenceSnapshot.model_validate(reflected)
    assert fit(mirror, policy=p).cell_values == pytest.approx([0, 0, 8 / 41, 14 / 41])
    for point in ((-0.1, 0), (1.01, 0), (float("nan"), 0), (0, float("inf"))):
        with pytest.raises(ActionValueUnavailable):
            cell_index(point, p)


@pytest.mark.parametrize("changes", [
    {"coordinate_system": "unknown", "coordinate_provenance": None},
    {"start_x": None, "start_y": None}, {"possession_retained": None},
    {"end_x": None, "end_y": None}, {"status": "draft"},
    {"timestamp_seconds": 601}, {"end_timestamp_seconds": 601},
    {"outcome": "unsuccessful", "possession_retained": True},
])
def test_incomplete_or_contradictory_eligible_events_fail_whole_fit(changes):
    with pytest.raises(ActionValueUnavailable):
        fit(edit_event(snapshot(), 0, **changes), policy=policy())


@pytest.mark.parametrize("changes", [{"shot_outcome": None}, {"shot_outcome": "goal", "outcome": "unsuccessful"}])
def test_goal_semantics_cannot_be_inferred_or_contradict_outcome(changes):
    with pytest.raises(ActionValueUnavailable):
        fit(edit_event(snapshot(), 3, **changes), policy=policy())


def test_selected_highlights_missing_rights_rosters_and_role_filtered_streams_refused():
    for key, change in [("coverage", "selected_highlights"), ("coverage", "unknown"), ("training_rights_cleared", False)]:
        data = snapshot().model_dump()
        data["matches"][0]["stream"][key] = change
        with pytest.raises(ActionValueUnavailable):
            fit(EvidenceSnapshot.model_validate(data), policy=policy())
    value = snapshot()
    data = value.model_dump()
    data["scope"]["role"] = "midfielder"
    with pytest.raises(ActionValueUnavailable, match="all_roles"):
        fit(EvidenceSnapshot.model_validate(data), policy=policy())
    data = value.model_dump()
    data["appearances"] = [a for a in data["appearances"] if a["side"] == "home"]
    for event in data["events"]:
        event["player_id"] = "home"
    with pytest.raises(ActionValueUnavailable, match="both authorized rosters"):
        fit(EvidenceSnapshot.model_validate(data), policy=policy())


def test_duplicate_ids_and_same_actor_time_cannot_inflate_support():
    value = snapshot()
    data = value.model_dump()
    data["events"].append(dict(data["events"][0]))
    with pytest.raises(ValidationError, match="duplicate event"):
        EvidenceSnapshot.model_validate(data)
    data["events"][-1]["event_id"] = "different-id-same-action"
    with pytest.raises(ActionValueUnavailable, match="duplicate/ambiguous"):
        fit(EvidenceSnapshot.model_validate(data), policy=policy())
    # Caller-side mutation/model_construct must not bypass shared validation.
    value.events.append(value.events[0])
    with pytest.raises(ValidationError, match="duplicate event"):
        fit(value, policy=policy())


def test_unfitted_empty_low_support_and_nonconvergent_never_return_filler():
    value = snapshot()
    assert score(value.events[0], scope=value.scope, artifact=None).value is None
    with pytest.raises(ActionValueUnavailable):
        fit(EvidenceSnapshot(scope=value.scope), policy=policy())
    with pytest.raises(ActionValueUnavailable, match="Insufficient"):
        fit(value)  # default support is intentionally NOT loosened for fixtures
    with pytest.raises(ActionValueUnavailable, match="did not converge"):
        fit(value, policy=policy(max_iterations=1))
    with pytest.raises(ActionValueUnavailable, match="work bound"):
        fit(value, policy=policy(max_training_events=10))
    artifact = fit(value, policy=policy(min_cell_actions=7))
    assert score(value.events[0], scope=value.scope, artifact=artifact).value is None
    artifact = fit(value, policy=policy())
    event = value.events[0].model_copy(update={"end_x": C[0], "end_y": C[1]})
    result = score(event, scope=value.scope, artifact=artifact)
    assert result.status == "unavailable" and result.end_cell_actions == 0
    for index in (2, 3, 4):  # failed move, goal, non-goal shot
        assert score(value.events[index], scope=value.scope, artifact=artifact).value is None
    for kind in ("duel", "tackle", "interception", "recovery", "block", "header"):
        assert score(value.events[0].model_copy(update={"kind": kind}), scope=value.scope, artifact=artifact).value is None


def test_zero_goals_cannot_ship_all_zero_model():
    data = snapshot().model_dump()
    for event in data["events"]:
        if event["shot_outcome"] == "goal":
            event["shot_outcome"] = "no_goal"
    with pytest.raises(ActionValueUnavailable, match="Insufficient"):
        fit(EvidenceSnapshot.model_validate(data), policy=policy())


def test_determinism_input_permutations_and_wall_clock_independent_model_id():
    value = snapshot()
    original = fit(value, policy=policy())
    for seed in range(8):
        data = value.model_dump()
        for name in ("events", "matches", "appearances", "identities"):
            random.Random(seed).shuffle(data[name])
        shuffled = fit(EvidenceSnapshot.model_validate(data), policy=policy())
        assert original.trained_at <= shuffled.trained_at
        assert artifact_fingerprint(original) == artifact_fingerprint(shuffled)
        assert original.cell_values == shuffled.cell_values
        assert original.training_fingerprint == shuffled.training_fingerprint


@pytest.mark.parametrize("field,changed", [("workspace_id", "elsewhere"), ("mode", "real"), ("season", "2026"), ("age_group", "U17"), ("competition", "other"), ("role", "forward")])
def test_incompatible_authorized_domain_is_unavailable(field, changed):
    value = snapshot()
    artifact = fit(value, policy=policy())
    scope = value.scope.model_copy(update={field: changed})
    assert score(value.events[0], scope=scope, artifact=artifact).value is None
    with pytest.raises(ActionValueUnavailable, match="authorized"):
        load_artifact_json(artifact_document(artifact).model_dump_json(), authorized_scope=scope)


def test_event_corrections_deletion_reattribution_rights_and_identity_stale_model():
    value = snapshot()
    artifact = fit(value, policy=policy())
    changes = [edit_event(value, 3, shot_outcome="no_goal"), edit_event(value, 0, player_id="away"), edit_event(value, 0, status="rejected")]
    data = value.model_dump()
    data["events"].pop()
    changes.append(EvidenceSnapshot.model_validate(data))
    data = value.model_dump()
    data["matches"][0]["stream"]["training_rights_cleared"] = False
    changes.append(EvidenceSnapshot.model_validate(data))
    for changed in changes:
        result = score_from_snapshot(changed, event_id="m0-e1", artifact=artifact)
        assert result.value is None and "stale" in result.reasons[0]
        assert training_fingerprint(changed, artifact.policy) != artifact.training_fingerprint
    repaired = fit(changes[0], policy=policy())
    assert repaired.training_goal_count == 1
    assert repaired.cell_values[1] < artifact.cell_values[1]
    assert score_from_snapshot(changes[0], event_id="m0-e1", artifact=repaired).status == "available"
    assert score_from_snapshot(value, event_id="deleted", artifact=artifact).value is None
    assert score_from_snapshot(subset_snapshot(value, {"m0"}), event_id="m0-e0", artifact=artifact).value is None


def test_confirmed_identity_current_revision_and_interval_enforced():
    value = identity_snapshot()
    artifact = fit(value, policy=policy())
    assert score(value.events[0], scope=value.scope, artifact=artifact).value is None
    assert score_from_snapshot(value, event_id="m0-e0", artifact=artifact).status == "available"
    for change in ({"revision": 2}, {"status": "proposed"}, {"status": "rejected"}, {"player_id": "away"}, {"start_seconds": 11}):
        data = value.model_dump()
        data["identities"][0].update(change)
        changed = EvidenceSnapshot.model_validate(data)
        assert score_from_snapshot(changed, event_id="m0-e0", artifact=artifact).value is None
        with pytest.raises(ActionValueUnavailable):
            fit(changed, policy=policy())
    data = value.model_dump() | {"identities": []}
    with pytest.raises(ActionValueUnavailable, match="missing"):
        fit(EvidenceSnapshot.model_validate(data), policy=policy())


def test_new_matches_are_scored_without_automatic_retraining_but_identity_checked():
    full = snapshot(matches=3)
    artifact = fit(subset_snapshot(full, {"m0", "m1"}), policy=policy())
    assert score_from_snapshot(full, event_id="m2-e0", artifact=artifact).status == "available"
    changed = edit_event(full, 12, identity_assignment_id="unresolved", identity_assignment_revision=1)
    assert score_from_snapshot(changed, event_id="m2-e0", artifact=artifact).value is None
    # Changes to a non-training match do not contaminate the trained model.
    changed = edit_event(full, 15, shot_outcome="no_goal")
    assert score_from_snapshot(changed, event_id="m2-e0", artifact=artifact).status == "available"


def test_finite_checksum_version_shape_support_and_residual_import_validation():
    value = snapshot()
    artifact = fit(value, policy=policy())
    document = artifact_document(artifact)
    assert load_artifact_json(document.model_dump_json(), authorized_scope=value.scope) == artifact
    changes = [
        {"cell_values": [0.2, 0.3, 0, 0]}, {"cell_values": [float("nan"), 0, 0, 0]},
        {"cell_values": [float("inf"), 0, 0, 0]}, {"cell_values": [-0.1, 0, 0, 0]},
        {"cell_values": [0, 0, 0]}, {"cell_action_counts": [1, 1, 0, 0]},
        {"schema_version": "not-supported"}, {"validation_status": "validated"},
        {"diagnostics": {"converged": True, "iterations": 9999, "max_residual": 1}},
    ]
    for change in changes:
        data = document.model_dump(mode="json")
        data["artifact"].update(change)
        with pytest.raises(ActionValueUnavailable):
            load_artifact_json(json.dumps(data), authorized_scope=value.scope)
    with pytest.raises(ActionValueUnavailable, match="8 MiB"):
        load_artifact_json(b" " * (8 * 1024 * 1024 + 1), authorized_scope=value.scope)
    data = document.model_dump(mode="json")
    data["artifact"]["cell_values"][2] = 0.3
    data["content_sha256"] = canonical_fingerprint(data["artifact"])
    with pytest.raises(ActionValueUnavailable):
        load_artifact_json(json.dumps(data), authorized_scope=value.scope)
    # Mutation must be revalidated even when Pydantic already saw the instance.
    artifact.cell_values[0] = float("nan")
    assert score(value.events[0], scope=value.scope, artifact=artifact).value is None
    with pytest.raises(ValueError):
        validate_artifact(artifact)


def test_bad_coordinates_revalidated_after_model_copy():
    value = snapshot()
    artifact = fit(value, policy=policy())
    for x in (float("nan"), float("inf"), -0.1, 1.1):
        event = value.events[0].model_copy(update={"start_x": x})
        assert score(event, scope=value.scope, artifact=artifact).value is None
        with pytest.raises(ValueError):
            normalized_points(event)


def test_temporal_shot_diagnostic_has_exact_train_only_baseline_and_target():
    value = snapshot(matches=4)
    report = evaluate(value, policy=policy())
    assert report.status == "evaluated"
    assert report.train_match_ids == ["m0", "m1", "m2"] and report.test_match_ids == ["m3"]
    assert report.train_through < report.test_from
    assert report.sample_count == 2 and report.excluded_count == 0
    expected_brier = ((3 / 7 - 1) ** 2 + (3 / 7) ** 2) / 2
    assert report.model_brier == pytest.approx(expected_brier)
    assert report.baseline_brier == pytest.approx(expected_brier)
    assert "NOT long-horizon" in report.target
    # Change ONLY held-out goals: train-only probability remains 3/7, not 2/5.
    changed = edit_event(value, 21, shot_outcome="no_goal")
    changed_report = evaluate(changed, policy=policy())
    assert changed_report.model_brier == pytest.approx((3 / 7) ** 2)
    assert changed_report.baseline_brier == pytest.approx((3 / 7) ** 2)
    original_train = fit(subset_snapshot(value, set(report.train_match_ids)), policy=policy())
    changed_train = fit(subset_snapshot(changed, set(report.train_match_ids)), policy=policy())
    assert artifact_fingerprint(original_train) == artifact_fingerprint(changed_train)


def test_whole_date_groups_holdout_unsupported_shots_and_unavailable_evaluation():
    value = snapshot(matches=5, dates=[1, 2, 2, 3, 3])
    report = evaluate(value, policy=policy(), test_fraction=0.34)
    assert report.status == "unavailable"  # only one train date -> one match, insufficient
    assert report.train_match_ids == ["m0"]
    assert report.test_match_ids == ["m1", "m2", "m3", "m4"]
    value = snapshot(matches=4)
    changed = edit_event(value, 21, start_x=C[0], start_y=C[1])
    report = evaluate(changed, policy=policy())
    assert report.status == "evaluated" and report.sample_count == 1 and report.excluded_count == 1
    changed = edit_event(changed, 22, start_x=C[0], start_y=C[1])
    report = evaluate(changed, policy=policy())
    assert report.status == "unavailable" and report.excluded_count == 2
    assert report.model_brier is None and report.baseline_brier is None
    for value, fraction in [(snapshot(), 0.25), (snapshot(matches=3, dates=[1, 1, 1]), 0.25), (snapshot(matches=4), float("nan"))]:
        assert evaluate(value, policy=policy(), test_fraction=fraction).status == "unavailable"


def test_rejected_events_not_training_actions_but_changes_invalidate_provenance():
    value = snapshot()
    rejected = value.events[0].model_copy(update={"event_id": "rejected-candidate", "status": "rejected", "start_x": None, "start_y": None})
    value.events.append(rejected)
    artifact = fit(value, policy=policy())
    assert artifact.training_action_count == 12
    value.events[-1] = rejected.model_copy(update={"timestamp_seconds": 11})
    assert score_from_snapshot(value, event_id="m0-e0", artifact=artifact).value is None


def test_same_period_side_and_opponent_orientation_contradictions_fail():
    data = snapshot().model_dump()
    for event in data["events"]:
        event.update(coordinate_system="pitch_normalized", period=1, attacking_direction="left_to_right" if event["player_id"] == "home" else "right_to_left")
    fit(EvidenceSnapshot.model_validate(data), policy=policy())
    same_side = EvidenceSnapshot.model_validate(data)
    same_side.events[2] = same_side.events[2].model_copy(update={"attacking_direction": "right_to_left"})
    with pytest.raises(ActionValueUnavailable, match="same side/period"):
        fit(same_side, policy=policy())
    data["events"][1]["attacking_direction"] = "left_to_right"
    with pytest.raises(ActionValueUnavailable, match="opposite sides"):
        fit(EvidenceSnapshot.model_validate(data), policy=policy())


def test_one_side_observed_and_duplicate_roster_provenance_refused():
    data = snapshot().model_dump()
    for event in data["events"]:
        event["player_id"] = "home"
    with pytest.raises(ActionValueUnavailable, match="both sides"):
        fit(EvidenceSnapshot.model_validate(data), policy=policy())
    data = snapshot().model_dump()
    data["appearances"][1]["appearance_id"] = data["appearances"][0]["appearance_id"]
    with pytest.raises(ActionValueUnavailable, match="Duplicate appearance"):
        fit(EvidenceSnapshot.model_validate(data), policy=policy())


def test_terminal_regularization_all_move_cycle_and_stronger_prior_shrinkage():
    # Disconnected cells C/D form an observed all-move recurrent class with no
    # reward. Positive terminal count yields a unique zero state, not a singular
    # inverse or spurious positive default; the A/B oracle remains unchanged.
    value = snapshot()
    for n in range(2):
        for j, (source, target) in enumerate([(C, (0.75, 0.75)), ((0.75, 0.75), C)]):
            event = value.events[n * 6].model_copy(update={"event_id": f"cycle-{n}-{j}", "timestamp_seconds": 100 + j, "start_x": source[0], "start_y": source[1], "end_x": target[0], "end_y": target[1]})
            value.events.append(event)
    artifact = fit(value, policy=policy())
    assert artifact.cell_values == pytest.approx([8 / 41, 14 / 41, 0, 0])
    assert artifact.cell_action_counts == [6, 6, 2, 2]
    stronger = fit(value, policy=policy(terminal_prior_count=2))
    assert 0 < stronger.cell_values[0] < artifact.cell_values[0]
    assert 0 < stronger.cell_values[1] < artifact.cell_values[1]
    with pytest.raises(ValidationError):
        policy(terminal_prior_count=0)


@pytest.mark.parametrize("seed", range(8))
def test_four_state_random_chains_against_independent_exact_fraction_elimination(seed):
    randomizer = random.Random(seed)
    transitions = [[randomizer.randrange(4) for _ in range(4)] for _ in range(4)]
    goals = [1] + [randomizer.randrange(3) for _ in range(3)]
    positions = [A, B, C, (0.75, 0.75)]
    value = snapshot()
    template = value.events[0].model_dump()
    value.events = []
    for match in value.matches:
        sequence = 0
        for source, point in enumerate(positions):
            actions = [("shot", True, None, "goal" if k < goals[source] else "no_goal") for k in range(2)]
            actions.append(("pass", False, None, None))
            for target in range(4):
                actions.extend([("pass", True, positions[target], None)] * transitions[source][target])
            for kind, success, destination, goal in actions:
                sequence += 1
                value.events.append(ReviewedAction.model_validate(template | dict(event_id=f"{match.match_id}-random-{sequence}", match_id=match.match_id, player_id="home" if sequence % 2 else "away", timestamp_seconds=sequence, kind=kind, outcome="successful" if success else "unsuccessful", start_x=point[0], start_y=point[1], end_x=destination[0] if destination else None, end_y=destination[1] if destination else None, possession_retained=success if kind != "shot" else None, shot_outcome=goal)))
    # Independent exact solve of (diag(N+alpha)-C)V=G. No engine binning,
    # counters, solver or floating-point linear algebra supplies this oracle.
    counts = [2 * (3 + sum(row)) for row in transitions]
    matrix = [[Fraction((counts[i] + 1 if i == j else 0) - 2 * transitions[i][j]) for j in range(4)] + [Fraction(2 * goals[i])] for i in range(4)]
    for pivot in range(4):
        scale = matrix[pivot][pivot]
        matrix[pivot] = [entry / scale for entry in matrix[pivot]]
        for row in range(4):
            if row != pivot:
                factor = matrix[row][pivot]
                matrix[row] = [entry - factor * base for entry, base in zip(matrix[row], matrix[pivot], strict=True)]
    expected = [float(row[-1]) for row in matrix]
    artifact = fit(value, policy=policy())
    assert artifact.cell_values == pytest.approx(expected, abs=1e-9)
    assert artifact.cell_action_counts == counts
    assert all(0 <= cell <= 1 for cell in artifact.cell_values)
    residual = max(abs(artifact.cell_values[i] - (2 * goals[i] + sum(2 * transitions[i][j] * artifact.cell_values[j] for j in range(4))) / (counts[i] + 1)) for i in range(4))
    assert residual <= artifact.policy.convergence_tolerance * 1.001
