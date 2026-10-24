"""Synthetic mathematical/contract fixtures; never youth-model validation data."""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.intelligence.contracts import (
    CohortScope, DuelRatingPolicy, EvidenceAppearance, EvidenceMatch,
    EvidenceSnapshot, ReviewedAction, TrackIdentityRecord,
)
from app.intelligence.ratings import (
    BradleyTerryEngine, DuelEvaluationReport, OpponentRatingReport,
    RatingCapacityError, _derivatives, _objective, _sigmoid,
)

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)
ENGINE = BradleyTerryEngine()


def snapshot(edges=(), *, isolated=()):
    """Each (home, away, home_wins, away_wins) is its own dated match."""
    scope = CohortScope(workspace_id="w", mode="demo", season="2025", age_group="U15", competition="Synthetic fixtures", role="all_roles")
    matches, appearances, events = [], [], []
    for number, (home, away, wins, losses) in enumerate(edges):
        match_id = f"m{number:03}"
        matches.append(EvidenceMatch(match_id=match_id, workspace_id="w", mode="demo", match_date=date(2025, 1, 1) + timedelta(days=number), season="2025", age_group="U15", competition="Synthetic fixtures", duration_seconds=1000))
        for player, side in ((home, "home"), (away, "away")):
            appearances.append(EvidenceAppearance(appearance_id=f"{match_id}-{player}", match_id=match_id, player_id=player, workspace_id="w", mode="demo", side=side, role="midfielder", played_seconds=1000, reviewed_seconds=500, roster_authorized=True))
        for index in range(wins + losses):
            events.append(ReviewedAction(event_id=f"{match_id}-e{index:05}", contest_id=f"contest-{index:05}", workspace_id="w", mode="demo", match_id=match_id, player_id=home, opponent_player_id=away, kind="duel", outcome="successful" if index < wins else "unsuccessful", timestamp_seconds=10 + index % 900, status="approved", reviewed_by="reviewer", reviewed_at=NOW, provenance="synthetic", updated_at=NOW))
    for player, side in isolated:
        appearances.append(EvidenceAppearance(appearance_id=f"m000-{player}", match_id="m000", player_id=player, workspace_id="w", mode="demo", side=side, role="midfielder", played_seconds=1000, reviewed_seconds=500, roster_authorized=True))
    return EvidenceSnapshot(scope=scope, matches=matches, appearances=appearances, events=events)


def modify(value, **updates):
    return EvidenceSnapshot.model_validate(value.model_dump() | updates)


def rating(report, player):
    return next(row for row in report.ratings if row.player_id == player)


def detail(report, player):
    return next(row for row in report.player_details if row.player_id == player)


def prediction(report, left="A", right="B"):
    return ENGINE.predict(report, player_id=left, opponent_player_id=right)


def permissive(**overrides):
    return DuelRatingPolicy(**(dict(min_contests=1, min_matches=2, min_distinct_opponents=1, max_match_share=1) | overrides))


def test_empty_and_isolated_players_have_no_numeric_rating_or_half_prediction():
    empty = ENGINE.fit(snapshot())
    assert not empty.diagnostics.converged
    assert not empty.ratings
    assert prediction(empty).win_probability is None
    report = ENGINE.fit(snapshot([("A", "B", 2, 0)], isolated=[("C", "away")]))
    isolated = rating(report, "C")
    assert isolated.eligibility == "unavailable"
    assert isolated.contests == 0
    assert isolated.log_odds is isolated.posterior_sd is isolated.component_id is None
    assert detail(report, "C").observed_win_fraction is None
    assert detail(report, "C").probability_against_component_center is None
    assert prediction(report, "A", "C").win_probability is None
    assert prediction(report, "A", "missing").win_probability is None
    assert prediction(report, "A", "A").win_probability is None


def test_one_contest_is_a_complete_pair_with_provisional_prior_shrinkage():
    report = ENGINE.fit(snapshot([("A", "B", 1, 0)]))
    assert report.accepted_contests == 1
    assert detail(report, "A").wins == detail(report, "B").losses == 1
    assert detail(report, "B").wins == detail(report, "A").losses == 0
    assert sum(row.contests for row in report.ratings) == 2
    assert all(row.eligibility == "provisional" and row.component_rank is None for row in report.ratings)
    assert "below_minimum_matches" in rating(report, "A").reasons
    assert "excessive_single_match_concentration" in rating(report, "A").reasons
    assert 0.5 < prediction(report).win_probability < 1
    assert prediction(report).interval_low < 0.5 < prediction(report).interval_high
    assert detail(report, "A").likelihood_contests == 1
    assert detail(report, "A").dependence_adjusted_effective_sample_size is None
    assert report.validation_status == "unvalidated_observed_duel_estimator"
    assert "not match-cluster calibrated" in report.uncertainty_caveat


@pytest.mark.parametrize("wins,losses", [(0, 30), (30, 0), (4, 9), (9, 4), (5, 5)])
@pytest.mark.parametrize("prior_sd", [0.1, 1.5, 5.0])
def test_two_player_map_and_laplace_match_independent_scalar_oracle(wins, losses, prior_sd):
    policy = DuelRatingPolicy(prior_standard_deviation=prior_sd)
    report = ENGINE.fit(snapshot([("A", "B", wins, losses)]), policy=policy)
    assert report.diagnostics.converged
    precision, count = 1 / prior_sd**2, wins + losses
    lower, upper = -100.0, 100.0
    for _ in range(200):
        middle = (lower + upper) / 2
        gradient = precision * middle + count / (1 + math.exp(-2 * middle)) - wins
        if gradient > 0:
            upper = middle
        else:
            lower = middle
    expected = (lower + upper) / 2
    estimate = rating(report, "A")
    assert estimate.log_odds == pytest.approx(expected, abs=3e-8)
    assert rating(report, "B").log_odds == pytest.approx(-expected, abs=3e-8)
    prob = 1 / (1 + math.exp(-2 * expected))
    contrast_variance = 2 / (precision + 2 * count * prob * (1 - prob))
    assert prediction(report).posterior_sd**2 == pytest.approx(contrast_variance, rel=2e-8)
    assert estimate.posterior_sd**2 == pytest.approx(contrast_variance / 4, rel=2e-8)
    assert report.diagnostics.max_residual is not None
    assert report.diagnostics.max_residual <= policy.convergence_tolerance
    assert report.diagnostics.objective is not None
    assert math.isfinite(report.diagnostics.objective)


def test_balanced_samples_have_exact_symmetric_shrunk_center_and_tied_ranks():
    report = ENGINE.fit(snapshot([("A", "B", 10, 10), ("A", "B", 10, 10)]), policy=permissive())
    assert report.diagnostics.iterations == 0
    assert rating(report, "A").log_odds == rating(report, "B").log_odds == 0
    assert rating(report, "A").component_rank == rating(report, "B").component_rank == 1
    assert prediction(report).win_probability == 0.5  # Observed balanced evidence, not missingness.
    assert sum(report.components[0].covariance[0]) == pytest.approx(0, abs=1e-14)


def test_more_wins_monotonically_raise_same_opponent_estimate():
    means, probabilities = [], []
    for wins in range(1, 18):
        report = ENGINE.fit(snapshot([("A", "B", wins, 8), ("B", "C", 8, 4)]))
        means.append(rating(report, "A").log_odds)
        probabilities.append(prediction(report).win_probability)
    assert all(left < right for left, right in zip(means, means[1:]))
    assert all(left < right for left, right in zip(probabilities, probabilities[1:]))


def test_opponent_adjustment_distinguishes_equal_raw_records():
    report = ENGINE.fit(snapshot([("A", "S", 5, 5), ("B", "W", 5, 5), ("S", "W", 30, 0)]))
    assert detail(report, "A").observed_win_fraction == detail(report, "B").observed_win_fraction == 0.5
    assert rating(report, "A").log_odds > rating(report, "B").log_odds
    assert prediction(report).win_probability > 0.7
    assert detail(report, "A").opponent_ids == ["S"]
    assert detail(report, "B").opponent_ids == ["W"]


def test_prior_strength_shrinks_toward_component_center_without_fake_data():
    value = snapshot([("A", "B", 15, 0)])
    strong = ENGINE.fit(value, policy=DuelRatingPolicy(prior_standard_deviation=0.3))
    weak = ENGINE.fit(value, policy=DuelRatingPolicy(prior_standard_deviation=3))
    assert 0 < rating(strong, "A").log_odds < rating(weak, "A").log_odds
    assert strong.accepted_contests == weak.accepted_contests == 15
    assert strong.evidence_fingerprint != weak.evidence_fingerprint
    assert detail(strong, "A").wins == detail(weak, "A").wins == 15


def test_balanced_repeated_evidence_reduces_model_sd_but_single_match_stays_provisional():
    small = ENGINE.fit(snapshot([("A", "B", 2, 2)]))
    large = ENGINE.fit(snapshot([("A", "B", 100, 100)]))
    assert prediction(large).posterior_sd < prediction(small).posterior_sd
    assert rating(large, "A").eligibility == "provisional"
    assert rating(large, "A").component_rank is None
    assert detail(large, "A").dependence_adjusted_effective_sample_size is None


def test_permutations_are_bitwise_deterministic_including_fingerprint():
    original = snapshot([("A", "B", 8, 3), ("A", "C", 3, 6), ("B", "C", 5, 4)])
    expected = ENGINE.fit(original).model_dump_json()
    for seed in range(15):
        data = original.model_dump()
        for collection in ("matches", "appearances", "events", "identities"):
            random.Random(seed).shuffle(data[collection])
        assert ENGINE.fit(EvidenceSnapshot.model_validate(data)).model_dump_json() == expected


def test_relabeling_preserves_estimates_covariance_and_predictions():
    original = snapshot([("A", "B", 8, 3), ("A", "C", 3, 6), ("B", "C", 5, 4)])
    renamed = original.model_dump()
    mapping = {"A": "zzz", "B": "mmm", "C": "aaa"}
    for row in renamed["appearances"]:
        row["player_id"] = mapping[row["player_id"]]
    for row in renamed["events"]:
        row["player_id"] = mapping[row["player_id"]]
        row["opponent_player_id"] = mapping[row["opponent_player_id"]]
    left, right = ENGINE.fit(original), ENGINE.fit(EvidenceSnapshot.model_validate(renamed))
    for player, renamed_player in mapping.items():
        assert rating(left, player).log_odds == pytest.approx(rating(right, renamed_player).log_odds, abs=1e-10)
        assert rating(left, player).posterior_sd == pytest.approx(rating(right, renamed_player).posterior_sd, abs=1e-10)
    assert prediction(left).win_probability == pytest.approx(prediction(right, "zzz", "mmm").win_probability, abs=1e-10)


@pytest.mark.parametrize("seed", range(8))
def test_reversing_every_outcome_negates_map_and_complements_probability(seed):
    generator = random.Random(seed)
    original = snapshot([(left, right, generator.randint(1, 15), generator.randint(1, 15)) for left, right in [("A", "B"), ("B", "C"), ("A", "C"), ("C", "D")]])
    reversed_data = original.model_dump()
    for event in reversed_data["events"]:
        event["outcome"] = "unsuccessful" if event["outcome"] == "successful" else "successful"
    forward, backward = ENGINE.fit(original), ENGINE.fit(EvidenceSnapshot.model_validate(reversed_data))
    for row in forward.ratings:
        assert row.log_odds == pytest.approx(-rating(backward, row.player_id).log_odds, abs=2e-8)
        assert row.posterior_sd == pytest.approx(rating(backward, row.player_id).posterior_sd, abs=2e-8)
    assert prediction(forward).win_probability == pytest.approx(1 - prediction(backward).win_probability, abs=2e-8)


def test_inverse_pair_prediction_inverts_interval_endpoints():
    report = ENGINE.fit(snapshot([("A", "B", 9, 3), ("B", "C", 2, 4)]))
    forward, backward = prediction(report), prediction(report, "B", "A")
    assert forward.win_probability == pytest.approx(1 - backward.win_probability)
    assert forward.interval_low == pytest.approx(1 - backward.interval_high)
    assert forward.interval_high == pytest.approx(1 - backward.interval_low)
    assert forward.posterior_sd == backward.posterior_sd


def test_likelihood_translation_symmetry_prior_explicitly_fixes_gauge():
    theta, shift, precision = [0.4, -0.2, 0.7], 3.0, 1 / 1.5**2
    pairs = [(0, 1, 10, 7), (1, 2, 6, 2)]
    shifted = [value + shift for value in theta]
    difference = _objective(shifted, pairs, precision) - _objective(theta, pairs, precision)
    expected_prior_change = precision / 2 * sum(new**2 - old**2 for new, old in zip(shifted, theta))
    assert difference == pytest.approx(expected_prior_change)
    gradient, hessian = _derivatives(theta, pairs, precision)
    other_gradient, other_hessian = _derivatives(shifted, pairs, precision)
    for i in range(3):
        assert other_gradient[i] - gradient[i] == pytest.approx(precision * shift)
        assert hessian[i] == pytest.approx(other_hessian[i])
    assert _sigmoid(theta[0] - theta[1]) == pytest.approx(_sigmoid(shifted[0] - shifted[1]))


def test_analytic_derivatives_match_finite_difference_oracle():
    theta, pairs, precision = [0.7, -0.4, 0.2], [(0, 1, 9, 7), (1, 2, 11, 3), (0, 2, 3, 2)], 0.8
    gradient, hessian = _derivatives(theta, pairs, precision)
    epsilon = 1e-5
    for i in range(len(theta)):
        plus, minus = theta.copy(), theta.copy()
        plus[i] += epsilon
        minus[i] -= epsilon
        numeric_gradient = (_objective(plus, pairs, precision) - _objective(minus, pairs, precision)) / (2 * epsilon)
        assert gradient[i] == pytest.approx(numeric_gradient, abs=1e-8)
        upper, _ = _derivatives(plus, pairs, precision)
        lower, _ = _derivatives(minus, pairs, precision)
        for j in range(len(theta)):
            assert hessian[j][i] == pytest.approx((upper[j] - lower[j]) / (2 * epsilon), abs=1e-8)


def test_timestamp_translation_is_not_a_rating_signal():
    original = snapshot([("A", "B", 4, 2)])
    data = original.model_dump()
    for event in data["events"]:
        event["timestamp_seconds"] += 300
    left, right = ENGINE.fit(original), ENGINE.fit(EvidenceSnapshot.model_validate(data))
    assert left.ratings == right.ratings
    assert left.evidence_fingerprint != right.evidence_fingerprint


def test_disconnected_components_have_only_local_ranks_no_comparison():
    report = ENGINE.fit(snapshot([("A", "B", 6, 2), ("A", "B", 6, 2), ("C", "D", 20, 0), ("C", "D", 20, 0)]), policy=permissive())
    assert rating(report, "A").component_id != rating(report, "C").component_id
    assert rating(report, "A").component_rank == rating(report, "C").component_rank == 1
    assert prediction(report, "A", "C").win_probability is None
    assert prediction(report, "A", "C").reasons == ["players_in_disconnected_components"]
    assert prediction(report).status == "available"
    assert prediction(report, "C", "D").status == "available"


def test_component_fit_is_unaffected_by_evidence_in_disconnected_component():
    first = ENGINE.fit(snapshot([("A", "B", 4, 2)]))
    combined = ENGINE.fit(snapshot([("A", "B", 4, 2), ("C", "D", 100, 1)]))
    assert rating(first, "A") == rating(combined, "A")
    assert prediction(first).win_probability == prediction(combined).win_probability


def test_mirrors_and_duplicate_annotations_count_once_but_preserve_all_drilldown_ids():
    original = snapshot([("A", "B", 1, 0)])
    row = original.events[0].model_dump()
    inverse = row | dict(event_id="mirror", player_id="B", opponent_player_id="A", outcome="unsuccessful", timestamp_seconds=10.5)
    duplicate = row | dict(event_id="duplicate")
    report = ENGINE.fit(modify(original, events=[row, inverse, duplicate]))
    assert report.accepted_contests == 1
    assert len(report.duplicate_event_ids) == 2
    assert not report.contradictory_event_ids
    assert len(report.contests[0].event_ids) == 3
    assert len(detail(report, "A").event_ids) == 3
    assert report.ratings == ENGINE.fit(original).ratings


def test_distinct_explicit_contests_at_identical_second_are_not_merged():
    original = snapshot([("A", "B", 1, 1)])
    data = original.model_dump()
    for row in data["events"]:
        row["timestamp_seconds"] = 10
    report = ENGINE.fit(EvidenceSnapshot.model_validate(data))
    assert report.accepted_contests == 2
    assert not report.duplicate_event_ids
    assert rating(report, "A").log_odds == 0


def test_same_contest_identifier_in_different_matches_is_not_deduplicated():
    report = ENGINE.fit(snapshot([("A", "B", 1, 0), ("A", "B", 1, 0)]))
    assert report.accepted_contests == 2
    assert len(detail(report, "A").match_ids) == 2
    assert not report.duplicate_event_ids


@pytest.mark.parametrize("change", [
    dict(event_id="bad", outcome="unsuccessful"),
    dict(event_id="bad", player_id="B", opponent_player_id="A", outcome="successful"),
    dict(event_id="bad", opponent_player_id="C"),
    dict(event_id="bad", period=2),
])
def test_contradictory_duplicates_exclude_entire_contest_instead_of_averaging(change):
    original = snapshot([("A", "B", 1, 0)], isolated=[("C", "away")])
    row = original.events[0].model_dump() | dict(period=1)
    report = ENGINE.fit(modify(original, events=[row, row | change]))
    assert report.accepted_contests == 0
    assert sorted(report.contradictory_event_ids) == sorted([row["event_id"], "bad"])
    assert len(report.excluded_event_ids) == 2
    assert not report.duplicate_event_ids
    assert all(estimate.log_odds is None for estimate in report.ratings)
    assert all("contradictory_contest_annotations" in excluded.reasons for excluded in report.exclusions)


def test_rejecting_a_contradictory_annotation_repairs_fit():
    original = snapshot([("A", "B", 1, 0)])
    row = original.events[0].model_dump()
    conflict = row | dict(event_id="conflict", outcome="unsuccessful")
    before = ENGINE.fit(modify(original, events=[row, conflict]))
    after = ENGINE.fit(modify(original, events=[row, conflict | dict(status="rejected")]))
    assert before.accepted_contests == 0
    assert after.accepted_contests == 1
    assert after.ratings == ENGINE.fit(original).ratings
    assert not after.contradictory_event_ids
    assert after.excluded_event_ids == ["conflict"]


@pytest.mark.parametrize("change,reason", [
    (dict(contest_id=None), "explicit_contest_id_required_no_timestamp_join"),
    (dict(opponent_player_id=None), "explicit_opponent_required"),
    (dict(kind="tackle", contest_id=None), "not_an_explicit_duel"),
    (dict(kind="header", contest_id=None), "not_an_explicit_duel"),
    (dict(kind="interception", contest_id=None), "not_an_explicit_duel"),
    (dict(kind="pass", contest_id=None), "not_an_explicit_duel"),
    (dict(status="draft"), "event_not_approved"),
    (dict(status="rejected"), "event_not_approved"),
    (dict(timestamp_seconds=1000), "event_outside_match_duration"),
    (dict(end_timestamp_seconds=1001), "event_outside_match_duration"),
    (dict(provenance="model_candidate"), "reviewed_track_identity_required_for_model_candidate"),
])
def test_only_explicit_legitimate_reviewed_duels_enter(change, reason):
    original = snapshot([("A", "B", 1, 0)])
    event = original.events[0].model_dump() | change
    report = ENGINE.fit(modify(original, events=[event]))
    assert report.accepted_contests == 0
    assert reason in report.exclusions[0].reasons
    assert rating(report, "A").log_odds is None


def linked_snapshot():
    original = snapshot([("A", "B", 1, 0)])
    identity = TrackIdentityRecord(id="identity", workspace_id="w", mode="demo", match_id="m000", upload_id="upload", track_id="track", start_seconds=5, end_seconds=20, player_id="A", status="confirmed", source="reviewer", reason="reviewer identifies roster player", revision=2, created_by="reviewer", created_at=NOW, reviewed_by="reviewer", reviewed_at=NOW, updated_at=NOW)
    event = original.events[0].model_dump() | dict(identity_assignment_id="identity", identity_assignment_revision=2, provenance="model_candidate")
    return modify(original, events=[event], identities=[identity])


def test_confirmed_current_identity_allows_reviewed_model_candidate():
    value = linked_snapshot()
    assert ENGINE.fit(value).accepted_contests == 1
    data = value.model_dump()
    data["identities"][0]["revision"] = 3
    assert ENGINE.fit(EvidenceSnapshot.model_validate(data)).accepted_contests == 0
    data["events"][0]["identity_assignment_revision"] = 3
    assert ENGINE.fit(EvidenceSnapshot.model_validate(data)).accepted_contests == 1


@pytest.mark.parametrize("change", [dict(status="proposed"), dict(status="rejected"), dict(player_id="B"), dict(revision=3), dict(start_seconds=11), dict(end_seconds=9)])
def test_unresolved_reattributed_stale_or_out_of_interval_identity_excluded(change):
    value = linked_snapshot()
    data = value.model_dump()
    data["identities"][0].update(change)
    report = ENGINE.fit(EvidenceSnapshot.model_validate(data))
    assert report.accepted_contests == 0
    assert report.excluded_event_ids == [value.events[0].event_id]
    assert rating(report, "A").log_odds is None


def test_ineligible_mirror_prevents_cherry_picking_its_valid_counterpart():
    value = linked_snapshot()
    data = value.model_dump()
    data["events"].append(value.events[0].model_dump() | dict(event_id="stale", identity_assignment_revision=1))
    report = ENGINE.fit(EvidenceSnapshot.model_validate(data))
    assert report.accepted_contests == 0
    assert len(report.excluded_event_ids) == 2


def test_identity_deletion_and_manual_reattribution_recompute_from_current_evidence():
    original = linked_snapshot()
    assert ENGINE.fit(original).accepted_contests == 1
    without_identity = modify(original, identities=[])
    assert ENGINE.fit(without_identity).accepted_contests == 0
    # A reviewer can replace track-derived evidence with an explicit manual row.
    manual = original.events[0].model_dump() | dict(player_id="B", opponent_player_id="A", outcome="unsuccessful", identity_assignment_id=None, identity_assignment_revision=None, provenance="manual")
    assert ENGINE.fit(modify(without_identity, events=[manual])).ratings == ENGINE.fit(original).ratings


def test_correction_deletion_and_opponent_reattribution_repair_all_downstream_values():
    original = snapshot([("A", "B", 1, 0)], isolated=[("C", "away")])
    before = ENGINE.fit(original)
    corrected_event = original.events[0].model_dump() | dict(outcome="unsuccessful")
    corrected = ENGINE.fit(modify(original, events=[corrected_event]))
    assert rating(before, "A").log_odds == pytest.approx(-rating(corrected, "A").log_odds)
    assert detail(corrected, "B").wins == 1
    deleted = ENGINE.fit(modify(original, events=[]))
    assert not deleted.contests
    assert all(not row.event_ids and row.likelihood_contests == 0 for row in deleted.player_details)
    assert all(row.log_odds is None for row in deleted.ratings)
    reattributed = ENGINE.fit(modify(original, events=[original.events[0].model_dump() | dict(opponent_player_id="C")]))
    assert rating(reattributed, "B").log_odds is None
    assert detail(reattributed, "B").event_ids == []
    assert detail(reattributed, "C").losses == 1
    assert detail(reattributed, "A").opponent_ids == ["C"]
    assert prediction(reattributed).win_probability is None
    assert prediction(reattributed, "A", "C").win_probability > 0.5
    assert len({item.evidence_fingerprint for item in [before, corrected, deleted, reattributed]}) == 4


def test_role_specific_cohort_needs_both_players_and_is_not_fairness_adjustment():
    value = snapshot([("A", "B", 3, 1)])
    data = value.model_dump()
    data["scope"]["role"] = "midfielder"
    data["appearances"][1]["role"] = "defender"
    report = ENGINE.fit(EvidenceSnapshot.model_validate(data))
    assert [row.player_id for row in report.ratings] == ["A"]
    assert report.accepted_contests == 0
    assert "both_players_must_match_role_scope" in report.exclusions[0].reasons


@pytest.mark.parametrize("change", [
    ("matches", "workspace_id", "foreign"), ("matches", "mode", "real"),
    ("matches", "season", "2024"), ("matches", "age_group", "U17"),
    ("matches", "competition", "Other"), ("appearances", "roster_authorized", False),
    ("events", "provenance", "synthetic"),
])
def test_fit_revalidates_even_constructed_instances_for_scope_or_authorization(change):
    value = snapshot([("A", "B", 1, 0)])
    collection, field, replacement = change
    data = value.model_dump()
    if collection == "events":
        data["scope"]["mode"] = "real"
        for name in ("matches", "appearances", "events"):
            for row in data[name]:
                row["mode"] = "real"
    data[collection][0][field] = replacement
    # Public boundary revalidation must not trust a model_construct caller.
    unsafe = EvidenceSnapshot.model_construct(**data)
    with pytest.warns(UserWarning, match="serializer warnings"), pytest.raises(ValidationError):
        ENGINE.fit(unsafe)


def test_same_side_opponent_and_self_opponent_cannot_enter():
    value = snapshot([("A", "B", 1, 0)])
    data = value.model_dump()
    data["appearances"][1]["side"] = "home"
    with pytest.warns(UserWarning, match="serializer warnings"), pytest.raises(ValidationError):
        ENGINE.fit(EvidenceSnapshot.model_construct(**data))
    data = value.model_dump()
    data["events"][0]["opponent_player_id"] = "A"
    with pytest.warns(UserWarning, match="serializer warnings"), pytest.raises(ValidationError):
        ENGINE.fit(EvidenceSnapshot.model_construct(**data))


def test_thresholds_and_match_concentration_are_disclosed_and_control_ranks():
    value = snapshot([("A", "B", 9, 0), ("A", "B", 1, 0)])
    concentrated = ENGINE.fit(value, policy=permissive(max_match_share=0.6))
    eligible = ENGINE.fit(value, policy=permissive())
    assert rating(concentrated, "A").eligibility == "provisional"
    assert "excessive_single_match_concentration" in rating(concentrated, "A").reasons
    assert detail(concentrated, "A").largest_match_share == 0.9
    assert rating(eligible, "A").eligibility == "eligible"
    assert rating(eligible, "A").component_rank == 1
    assert rating(eligible, "B").component_rank == 2
    assert rating(concentrated, "A").log_odds == rating(eligible, "A").log_odds


def test_solver_iteration_limit_suppresses_all_numbers_even_other_converged_component():
    report = ENGINE.fit(snapshot([("A", "B", 20, 0), ("C", "D", 5, 5)]), policy=DuelRatingPolicy(max_iterations=1))
    assert not report.diagnostics.converged
    assert report.diagnostics.iterations == 1
    assert "iteration_limit_reached" in report.diagnostics.warnings
    assert not report.components
    assert all(row.log_odds is row.posterior_sd is row.component_rank is None for row in report.ratings)
    assert all(row.probability_against_component_center is None for row in report.player_details)
    assert prediction(report).win_probability is None
    assert prediction(report, "C", "D").win_probability is None
    assert report.accepted_contests == 30


def test_numerical_failure_is_an_unavailable_report_not_partial_ranking(monkeypatch):
    def fail(_matrix):
        raise ArithmeticError("injected factorization failure")
    monkeypatch.setattr("app.intelligence.ratings._cholesky", fail)
    report = ENGINE.fit(snapshot([("A", "B", 10, 1)]))
    assert not report.diagnostics.converged
    assert report.ratings[0].log_odds is None
    assert "injected factorization failure" in report.diagnostics.warnings[0]


def test_capacity_limits_refuse_instead_of_silently_truncating():
    value = snapshot([("A", "B", 2, 0)], isolated=[("C", "away")])
    with pytest.raises(RatingCapacityError, match="roster players"):
        ENGINE.fit(value, policy=DuelRatingPolicy(max_players=2))
    with pytest.raises(RatingCapacityError, match="accepted contests"):
        ENGINE.fit(value, policy=DuelRatingPolicy(max_contests=1))


def test_json_roundtrip_preserves_predictions_and_complete_provenance():
    report = ENGINE.fit(snapshot([("A", "B", 6, 2), ("B", "C", 5, 4)]))
    restored = OpponentRatingReport.model_validate_json(report.model_dump_json())
    assert prediction(report) == prediction(restored)
    assert report == restored
    assert report.method_version == report.policy.method_version
    broken = report.model_dump()
    broken["components"][0]["covariance"][0] = [0.0]
    with pytest.raises(ValidationError):
        OpponentRatingReport.model_validate(broken)


def test_maximum_player_bound_is_solvable_without_external_numerical_packages():
    value = snapshot([(f"p{i:03}", f"p{i+1:03}", 1, 1) for i in range(127)])
    report = ENGINE.fit(value)
    assert report.diagnostics.converged
    assert len(report.ratings) == len(report.components[0].player_ids) == 128
    result = prediction(report, "p000", "p127")
    assert result.win_probability == 0.5
    assert 0 < result.interval_low < result.interval_high < 1


def test_extreme_allowed_count_and_prior_remain_finite_under_separation():
    report = ENGINE.fit(snapshot([("A", "B", 10000, 0)]), policy=DuelRatingPolicy(prior_standard_deviation=5))
    assert report.accepted_contests == 10000
    assert report.diagnostics.converged
    assert 0.99 < prediction(report).win_probability < 1
    assert all(row.log_odds is not None and row.posterior_sd is not None and math.isfinite(row.log_odds) and math.isfinite(row.posterior_sd) for row in report.ratings)


def test_holdout_scores_match_manual_brier_and_log_loss_without_test_fit():
    value = snapshot([("A", "B", 8, 2), ("A", "B", 8, 2), ("A", "B", 2, 8), ("C", "D", 10, 0)])
    evaluated = ENGINE.evaluate(value, test_fraction=0.5)
    trained = ENGINE.fit(modify(value, matches=value.matches[:2], appearances=[row for row in value.appearances if row.match_id in {"m000", "m001"}], events=[row for row in value.events if row.match_id in {"m000", "m001"}]))
    prob, base = prediction(trained).win_probability, 17 / 22
    assert evaluated.status == "evaluated"
    assert evaluated.train_match_ids == ["m000", "m001"]
    assert evaluated.test_match_ids == ["m002", "m003"]
    assert evaluated.training_evidence_fingerprint == trained.evidence_fingerprint
    assert evaluated.training_contests == 20
    assert evaluated.sample_count == evaluated.cold_start_contests == evaluated.excluded_count == 10
    assert evaluated.test_contests == 20
    assert evaluated.baseline_home_win_probability == pytest.approx(base)
    assert evaluated.model_brier == pytest.approx((2 * (prob - 1)**2 + 8 * prob**2) / 10)
    assert evaluated.baseline_brier == pytest.approx((2 * (base - 1)**2 + 8 * base**2) / 10)
    assert evaluated.model_log_loss == pytest.approx((-2 * math.log(prob) - 8 * math.log1p(-prob)) / 10)
    assert evaluated.baseline_log_loss == pytest.approx((-2 * math.log(base) - 8 * math.log1p(-base)) / 10)
    assert all(event_id.startswith("m002") for event_id in evaluated.scoring_event_ids)
    assert DuelEvaluationReport.model_validate_json(evaluated.model_dump_json()) == evaluated


def test_test_outcomes_and_test_only_rosters_cannot_change_training_fingerprint_or_baseline():
    value = snapshot([("A", "B", 8, 2), ("A", "B", 8, 2), ("A", "B", 2, 8), ("C", "D", 10, 0)])
    data = value.model_dump()
    for event in data["events"]:
        if event["match_id"] in {"m002", "m003"}:
            event["outcome"] = "successful" if event["outcome"] == "unsuccessful" else "unsuccessful"
        if event["match_id"] == "m003":
            event["player_id"] = "never-in-training"
    for appearance in data["appearances"]:
        if appearance["player_id"] == "C":
            appearance["player_id"] = "never-in-training"
    before, after = ENGINE.evaluate(value, test_fraction=0.5), ENGINE.evaluate(EvidenceSnapshot.model_validate(data), test_fraction=0.5)
    assert before.training_evidence_fingerprint == after.training_evidence_fingerprint
    assert before.baseline_home_win_probability == after.baseline_home_win_probability
    assert before.model_brier != after.model_brier


def test_holdout_fit_never_receives_test_matches_events_appearances_or_identities(monkeypatch):
    value = snapshot([("A", "B", 8, 2), ("A", "B", 8, 2), ("A", "B", 2, 8), ("C", "D", 10, 0)])
    calls = []
    original_fit = ENGINE.fit
    def spy(input_snapshot, *, policy=None):
        calls.append(input_snapshot)
        return original_fit(input_snapshot, policy=policy)
    monkeypatch.setattr(ENGINE, "fit", spy)
    ENGINE.evaluate(value, test_fraction=0.5)
    assert len(calls) == 1
    trained = calls[0]
    assert {row.match_id for row in trained.matches} == {"m000", "m001"}
    assert {row.match_id for row in trained.events + trained.appearances + trained.identities} <= {"m000", "m001"}
    assert {row.player_id for row in trained.appearances} == {"A", "B"}


def test_holdout_does_not_bridge_disconnected_training_components():
    value = snapshot([("A", "B", 5, 2), ("C", "D", 5, 2), ("A", "C", 3, 2), ("A", "B", 3, 2)])
    report = ENGINE.evaluate(value, test_fraction=0.5)
    assert report.status == "evaluated"
    assert report.sample_count == 5
    assert report.disconnected_contests == 5
    assert report.cold_start_contests == 0
    assert report.excluded_count == 5


def test_evaluation_is_whole_date_grouped_order_invariant_and_mirror_invariant():
    value = snapshot([("A", "B", 6, 2)] * 5)
    data = value.model_dump()
    data["matches"][2]["match_date"] = data["matches"][1]["match_date"]
    data["matches"][4]["match_date"] = data["matches"][3]["match_date"]
    value = EvidenceSnapshot.model_validate(data)
    expected = ENGINE.evaluate(value, test_fraction=0.5)
    assert expected.train_match_ids == ["m000"]
    assert expected.test_match_ids == ["m001", "m002", "m003", "m004"]
    assert expected.train_through is not None and expected.test_from is not None
    assert expected.train_through < expected.test_from
    for key in ("matches", "events", "appearances"):
        data[key].reverse()
    assert ENGINE.evaluate(EvidenceSnapshot.model_validate(data), test_fraction=0.5) == expected
    for event in data["events"]:
        event["player_id"], event["opponent_player_id"] = event["opponent_player_id"], event["player_id"]
        event["outcome"] = "unsuccessful" if event["outcome"] == "successful" else "successful"
    mirrored = ENGINE.evaluate(EvidenceSnapshot.model_validate(data), test_fraction=0.5)
    assert mirrored.model_brier == expected.model_brier
    assert mirrored.model_log_loss == expected.model_log_loss
    assert mirrored.baseline_home_win_probability == expected.baseline_home_win_probability


def test_evaluation_deduplicates_test_mirrors_and_explains_invalid_annotation_count():
    value = snapshot([("A", "B", 6, 2), ("A", "B", 1, 0)])
    data = value.model_dump()
    event = data["events"][-1]
    data["events"].extend([event | dict(event_id="mirror", player_id="B", opponent_player_id="A", outcome="unsuccessful"), event | dict(event_id="unknown", contest_id=None, timestamp_seconds=20)])
    report = ENGINE.evaluate(EvidenceSnapshot.model_validate(data), test_fraction=0.5)
    assert report.sample_count == report.test_contests == 1
    assert report.excluded_count == 1


@pytest.mark.parametrize("edges,policy", [
    ([], None),
    ([("A", "B", 2, 0)], None),
    ([("A", "B", 0, 0), ("A", "B", 1, 0)], None),
    ([("A", "B", 3, 0), ("C", "D", 1, 0)], None),
    ([("A", "B", 30, 0), ("A", "B", 1, 0)], DuelRatingPolicy(max_iterations=1)),
    ([("A", "B", 2, 0), ("A", "B", 1, 0)], DuelRatingPolicy(max_contests=1)),
])
def test_unavailable_evaluation_never_invents_validation_scores(edges, policy):
    report = ENGINE.evaluate(snapshot(edges), policy=policy, test_fraction=0.5)
    assert report.status == "unavailable"
    assert report.model_brier is report.baseline_brier is report.model_log_loss is report.baseline_log_loss is None
    assert report.limitations


@pytest.mark.parametrize("fraction", [0, 1, -0.1, float("nan"), float("inf")])
def test_evaluation_rejects_invalid_holdout_fraction(fraction):
    with pytest.raises(ValueError):
        ENGINE.evaluate(snapshot([("A", "B", 2, 0)] * 2), test_fraction=fraction)


def test_pairwise_comparisons_disclose_indirect_evidence_without_inventing_a_relationship():
    report = ENGINE.fit(snapshot([("A", "B", 5, 2), ("B", "C", 4, 1)]))
    direct = prediction(report)
    assert direct.comparison_basis == "direct_and_network_evidence"
    assert direct.direct_contests == 7
    assert direct.direct_match_ids == ["m000"]
    assert len(direct.direct_event_ids) == 7
    indirect = prediction(report, "A", "C")
    assert indirect.status == "available"
    assert indirect.comparison_basis == "indirect_network_evidence"
    assert indirect.direct_contests == 0
    assert not indirect.direct_match_ids and not indirect.direct_event_ids
    assert indirect.posterior_sd > direct.posterior_sd
    unavailable = prediction(report, "A", "unknown")
    assert unavailable.direct_contests is None
    assert unavailable.comparison_basis == "unavailable"


def test_appearance_identifier_ties_do_not_make_fingerprints_order_dependent():
    value = snapshot([("A", "B", 3, 1)])
    data = value.model_dump()
    for appearance in data["appearances"]:
        appearance["appearance_id"] = "legacy-shared-appearance-id"
    first = ENGINE.fit(EvidenceSnapshot.model_validate(data))
    data["appearances"].reverse()
    assert ENGINE.fit(EvidenceSnapshot.model_validate(data)) == first


def test_evaluation_builds_at_most_one_prediction_per_oriented_pair(monkeypatch):
    value = snapshot([("A", "B", 100, 50)] * 4)
    calls = []
    original = ENGINE._predict
    def spy(report, *, player_id, opponent_player_id, direct):
        calls.append((player_id, opponent_player_id))
        return original(report, player_id=player_id, opponent_player_id=opponent_player_id, direct=direct)
    monkeypatch.setattr(ENGINE, "_predict", spy)
    report = ENGINE.evaluate(value, test_fraction=0.5)
    assert report.status == "evaluated" and report.sample_count == 300
    assert calls == [("A", "B")]


def test_near_maximum_contests_and_players_converge_together():
    report = ENGINE.fit(snapshot([(f"p{i:03}", f"p{i+1:03}", 40, 38) for i in range(127)]))
    assert report.accepted_contests == 9906
    assert len(report.ratings) == 128
    assert report.diagnostics.converged
    assert report.diagnostics.iterations <= report.policy.max_iterations
    assert report.diagnostics.max_residual is not None
    assert report.diagnostics.max_residual <= report.policy.convergence_tolerance
    assert rating(report, "p000").log_odds > rating(report, "p127").log_odds


def test_complete_source_declaration_does_not_invent_duel_capture_fraction_or_effective_n():
    value = snapshot([("A", "B", 20, 0)])
    data = value.model_dump()
    data["matches"][0]["stream"] = dict(coverage="complete_match", eligible_actions_complete=True, both_sides_recorded=True, declared_by="reviewer", declared_at=NOW, source_reference="synthetic complete action stream")
    report = ENGINE.fit(EvidenceSnapshot.model_validate(data))
    assert detail(report, "A").duel_capture_fraction is None
    assert detail(report, "A").dependence_adjusted_effective_sample_size is None
    assert detail(report, "A").likelihood_contests == 20


def test_synthetic_predictive_signal_can_outperform_training_only_home_rate():
    # Player A wins 80% whether rostered home or away. Not real youth validation.
    report = ENGINE.evaluate(snapshot([("A", "B", 8, 2), ("B", "A", 2, 8)] * 2), test_fraction=0.5)
    assert report.status == "evaluated"
    assert report.baseline_home_win_probability == 0.5
    assert report.model_brier is not None and report.baseline_brier is not None
    assert report.model_log_loss is not None and report.baseline_log_loss is not None
    assert report.model_brier < report.baseline_brier
    assert report.model_log_loss < report.baseline_log_loss


def test_a_holdout_report_cannot_be_attached_to_full_data_fit_and_misrepresented():
    value = snapshot([("A", "B", 8, 2)] * 4)
    full = ENGINE.fit(value)
    evaluation = ENGINE.evaluate(value, test_fraction=0.5)
    with pytest.raises(ValidationError, match="fitted on its test matches"):
        OpponentRatingReport.model_validate(full.model_dump() | dict(evaluation=evaluation))
    train_ids = set(evaluation.train_match_ids)
    training = modify(value, matches=[row for row in value.matches if row.match_id in train_ids], appearances=[row for row in value.appearances if row.match_id in train_ids], events=[row for row in value.events if row.match_id in train_ids])
    fit = ENGINE.fit(training)
    attached = OpponentRatingReport.model_validate(fit.model_dump() | dict(evaluation=evaluation))
    assert attached.evaluation is not None
    assert attached.evaluation.sample_count == 20


@pytest.mark.parametrize("contest_id", ["different-id", None])
@pytest.mark.parametrize("outcome", ["successful", "unsuccessful"])
def test_exact_inverse_with_other_or_missing_id_stays_unresolved_not_double_counted(contest_id, outcome):
    value = snapshot([("A", "B", 1, 0)])
    original = value.events[0].model_dump()
    inverse = original | dict(event_id="inverse", player_id="B", opponent_player_id="A", outcome=outcome, contest_id=contest_id)
    report = ENGINE.fit(modify(value, events=[original, inverse]))
    assert report.accepted_contests == 0
    assert set(report.ambiguous_event_ids) == {original["event_id"], "inverse"}
    assert not report.duplicate_event_ids
    assert not report.contradictory_event_ids  # Not proof these IDs are one contest.
    assert all("ambiguous_inverse_annotations_across_contest_ids" in row.reasons for row in report.exclusions)
    assert all(row.log_odds is None for row in report.ratings)
    if contest_id is None:
        assert "explicit_contest_id_required_no_timestamp_join" in next(row.reasons for row in report.exclusions if row.event_id == "inverse")


def test_corrected_shared_contest_id_resolves_cross_id_inverse_ambiguity():
    value = snapshot([("A", "B", 1, 0)])
    original = value.events[0].model_dump()
    inverse = original | dict(event_id="inverse", player_id="B", opponent_player_id="A", outcome="unsuccessful", contest_id="different-id")
    assert ENGINE.fit(modify(value, events=[original, inverse])).accepted_contests == 0
    fixed = ENGINE.fit(modify(value, events=[original, inverse | dict(contest_id=original["contest_id"])]))
    assert fixed.accepted_contests == 1
    assert len(fixed.duplicate_event_ids) == 1
    assert not fixed.ambiguous_event_ids
    assert fixed.ratings == ENGINE.fit(value).ratings


def test_timestamp_collision_between_unrelated_pairs_is_not_a_duplicate_or_ambiguity():
    value = snapshot([("A", "B", 1, 0), ("C", "D", 1, 0)])
    data = value.model_dump()
    data["matches"] = data["matches"][:1]
    for row in data["appearances"]:
        row["match_id"] = "m000"
    data["events"][1].update(match_id="m000", contest_id="separate-contest")
    report = ENGINE.fit(EvidenceSnapshot.model_validate(data))
    assert report.accepted_contests == 2
    assert not report.duplicate_event_ids and not report.ambiguous_event_ids
    assert len(report.components) == 2


def test_cross_id_inverse_detection_is_exact_not_a_fuzzy_temporal_join():
    value = snapshot([("A", "B", 1, 0)])
    original = value.events[0].model_dump()
    inverse = original | dict(event_id="other", player_id="B", opponent_player_id="A", outcome="unsuccessful", contest_id="another-contest", timestamp_seconds=10.01)
    report = ENGINE.fit(modify(value, events=[original, inverse]))
    assert report.accepted_contests == 2
    assert not report.ambiguous_event_ids


def test_known_distinct_periods_do_not_create_cross_id_inverse_ambiguity():
    value = snapshot([("A", "B", 1, 0)])
    original = value.events[0].model_dump() | dict(period=1)
    inverse = original | dict(event_id="other", player_id="B", opponent_player_id="A", outcome="unsuccessful", contest_id="another-contest", period=2)
    report = ENGINE.fit(modify(value, events=[original, inverse]))
    assert report.accepted_contests == 2
    assert not report.ambiguous_event_ids


def test_cross_id_ambiguity_excludes_all_mirrors_in_the_affected_groups():
    value = snapshot([("A", "B", 1, 0)])
    original = value.events[0].model_dump()
    inverse = original | dict(event_id="inverse", player_id="B", opponent_player_id="A", outcome="unsuccessful", contest_id="different-id")
    extra = original | dict(event_id="extra", timestamp_seconds=10.5)
    report = ENGINE.fit(modify(value, events=[original, inverse, extra]))
    assert report.accepted_contests == 0
    assert len(report.ambiguous_event_ids) == 3
    reversed_report = ENGINE.fit(modify(value, events=[extra, inverse, original]))
    assert report == reversed_report
