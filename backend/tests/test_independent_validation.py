"""Independent analytical/adversarial review; fictional fixtures, NOT youth accuracy.

No parameter tuning on these held-out dates, no external data/model downloads.
The benchmark deliberately reports null and distribution-shift cases as well as
an on-model world: a model win is never a test acceptance criterion.
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction

import pytest

from app.intelligence.action_value import (
    ActionValueUnavailable, evaluate as spatial_evaluate, fit, score,
    score_from_snapshot, training_fingerprint,
)
from app.intelligence.contracts import (
    CohortScope, DuelRatingPolicy, EvidenceAppearance, EvidenceMatch,
    EvidenceSnapshot, ReviewedAction,
)
from app.intelligence.memory import ReviewedPlayerMemoryEngine
from app.intelligence.ratings import BradleyTerryEngine
from test_action_value import identity_snapshot, policy, snapshot
from test_opponent_ratings import snapshot as duel_snapshot

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def rational_solve(matrix, rhs):
    """Independent exact Gaussian elimination, not the production iteration/Newton."""
    augmented = [[Fraction(v) for v in row] + [Fraction(rhs[i])] for i, row in enumerate(matrix)]
    n = len(rhs)
    for i in range(n):
        pivot = next(j for j in range(i, n) if augmented[j][i])
        augmented[i], augmented[pivot] = augmented[pivot], augmented[i]
        divisor = augmented[i][i]
        augmented[i] = [v / divisor for v in augmented[i]]
        for j in range(n):
            if j != i:
                multiplier = augmented[j][i]
                augmented[j] = [a - multiplier * b for a, b in zip(augmented[j], augmented[i], strict=True)]
    return [row[-1] for row in augmented]


def test_four_cell_bellman_exact_linear_system_and_absorbing_simulation():
    # Per-match counts. Self loops, failed moves and both shot outcomes coexist.
    moves = [[2, 2, 0, 0], [1, 0, 2, 0], [0, 1, 0, 3], [0, 0, 1, 1]]
    goals, misses, failures = [0, 1, 2, 1], [1, 1, 2, 3], [2, 1, 0, 2]
    source = snapshot()
    events = []
    # Independent extraction-free construction: terminal misses/failures do not
    # get fabricated destinations or transitions; every attempt has unit weight.
    for match in source.matches:
        for cell in range(4):
            tokens = ["goal"] * goals[cell] + ["miss"] * misses[cell] + ["failure"] * failures[cell]
            tokens += [str(j) for j in range(4) for _ in range(moves[cell][j])]
            for token in tokens:
                index = len(events)
                shot = token in {"goal", "miss"}
                target = int(token) if token.isdigit() else None
                events.append(ReviewedAction(
                    event_id=f"event-{index}", workspace_id=source.scope.workspace_id, mode="demo",
                    match_id=match.match_id, player_id="home" if index % 2 else "away",
                    kind="shot" if shot else "pass", outcome="unsuccessful" if token == "failure" else "successful",
                    shot_outcome=("goal" if token == "goal" else "no_goal") if shot else None,
                    possession_retained=None if shot else target is not None,
                    timestamp_seconds=index + 1, start_x=0.25 + 0.5 * (cell % 2), start_y=0.25 + 0.5 * (cell // 2),
                    end_x=None if target is None else 0.25 + 0.5 * (target % 2),
                    end_y=None if target is None else 0.25 + 0.5 * (target // 2),
                    coordinate_system="attack_normalized", coordinate_provenance="fictional exact-state oracle",
                    status="approved", reviewed_by="fixture", reviewed_at=NOW, updated_at=NOW, provenance="synthetic",
                ))
    source = EvidenceSnapshot.model_validate(source.model_dump() | {"events": events})
    result = fit(source, policy=policy())
    totals = [2 * (sum(moves[i]) + goals[i] + misses[i] + failures[i]) + 1 for i in range(4)]
    system = [[(totals[i] if i == j else 0) - 2 * moves[i][j] for j in range(4)] for i in range(4)]
    exact = rational_solve(system, [2 * g for g in goals])
    assert result.cell_values == pytest.approx([float(v) for v in exact], abs=3e-10)
    assert result.training_goal_count == 2 * sum(goals)
    # Independently simulate the fitted absorbing chain, including its one
    # artificial zero terminal per cell. This validates units, not real football.
    generator = random.Random(1729)
    trials = 12000
    for start in range(4):
        successes = 0
        for _ in range(trials):
            cell = start
            for _step in range(1000):
                draw = generator.randrange(totals[cell])
                if draw < 2 * goals[cell]:
                    successes += 1
                    break
                draw -= 2 * (goals[cell] + misses[cell] + failures[cell]) + 1
                if draw < 0:
                    break
                for target in range(4):
                    draw -= 2 * moves[cell][target]
                    if draw < 0:
                        cell = target
                        break
            else:
                pytest.fail("bounded synthetic chain failed to absorb")
        # Six binomial SDs plus 1/N; fixed in advance, not fitted to this seed.
        expected = float(exact[start])
        assert abs(successes / trials - expected) < 6 * math.sqrt(expected * (1 - expected) / trials) + 1 / trials


def test_three_player_centered_covariance_matches_exact_precision_inverse():
    source = duel_snapshot([("A", "B", 4, 4), ("A", "C", 7, 7), ("B", "C", 2, 2)])
    report = BradleyTerryEngine().fit(source, policy=DuelRatingPolicy(prior_standard_deviation=1))
    # At balanced MAP zero, weights are n/4; H is exactly this rational matrix.
    hessian = [[Fraction(13, 2), -2, Fraction(-7, 2)], [-2, 4, -1], [Fraction(-7, 2), -1, Fraction(11, 2)]]
    columns = [rational_solve(hessian, [int(i == j) for i in range(3)]) for j in range(3)]
    posterior = report.components[0]
    assert posterior.centered_log_odds == [0, 0, 0]
    for i in range(3):
        for j in range(3):
            assert posterior.covariance[i][j] == pytest.approx(float(columns[j][i] - Fraction(1, 3)), abs=2e-15)
        assert sum(posterior.covariance[i]) == pytest.approx(0, abs=2e-15)


def simulated_duels(world: str, seed: int) -> EvidenceSnapshot:
    generator = random.Random(seed)
    scope = CohortScope(workspace_id="independent-fixtures", mode="demo", season="2025", age_group="U15", competition="Fictional simulation", role="all_roles")
    matches, appearances, events = [], [], []
    strengths = {"P0": -1.2, "P1": -0.4, "P2": 0.4, "P3": 1.2}
    # Each date is one whole match, two players; repeated canonical identities
    # are intentional, not a per-event randomized train/test split.
    for day in range(48):
        home, away = generator.sample(list(strengths), 2)
        match_id = f"match-{day:02}"
        matches.append(EvidenceMatch(match_id=match_id, workspace_id=scope.workspace_id, mode="demo", match_date=date(2025, 1, 1) + timedelta(days=day), season=scope.season, age_group=scope.age_group, competition=scope.competition, duration_seconds=600))
        for player, side in ((home, "home"), (away, "away")):
            appearances.append(EvidenceAppearance(appearance_id=f"{match_id}-{player}", match_id=match_id, player_id=player, workspace_id=scope.workspace_id, mode="demo", side=side, role="midfielder", played_seconds=600, reviewed_seconds=600, roster_authorized=True))
        difference = 0 if world == "null" else strengths[home] - strengths[away]
        if world == "reversal" and day >= 36:
            difference = -difference
        probability = 1 / (1 + math.exp(-difference))
        for index in range(20):
            events.append(ReviewedAction(event_id=f"{match_id}-e{index}", workspace_id=scope.workspace_id, mode="demo", match_id=match_id, player_id=home, opponent_player_id=away, kind="duel", contest_id=f"duel-{index}", outcome="successful" if generator.random() < probability else "unsuccessful", timestamp_seconds=index + 1, status="approved", reviewed_by="fixture", reviewed_at=NOW, updated_at=NOW, provenance="synthetic"))
    return EvidenceSnapshot(scope=scope, matches=matches, appearances=appearances, events=events)


@pytest.mark.parametrize("world", ["stable", "null", "reversal"])
@pytest.mark.parametrize("seed", [1729, 2026])
def test_seeded_temporal_benchmarks_report_baseline_without_requiring_model_win(world, seed):
    engine = BradleyTerryEngine()
    source = simulated_duels(world, seed)
    evaluation = engine.evaluate(source)
    assert evaluation.status == "evaluated" and evaluation.sample_count == 240
    assert evaluation.excluded_count == 0
    assert len(evaluation.train_match_ids) == 36 and len(evaluation.test_match_ids) == 12
    # Independently select dates and calculate both proper scoring rules. Do not
    # use production contest selection, split helper, sigmoid or loss functions.
    train_matches = {m.match_id for m in source.matches if m.match_date < date(2025, 2, 6)}
    training = EvidenceSnapshot(scope=source.scope, matches=[m for m in source.matches if m.match_id in train_matches], appearances=[a for a in source.appearances if a.match_id in train_matches], events=[e for e in source.events if e.match_id in train_matches])
    report = engine.fit(training)
    theta = {r.player_id: r.log_odds for r in report.ratings}
    train_wins = sum(e.outcome == "successful" for e in training.events)
    baseline = (train_wins + 1) / (len(training.events) + 2)
    brier, loss, baseline_brier, baseline_loss = [], [], [], []
    for event in source.events:
        if event.match_id in train_matches:
            continue
        probability = 1 / (1 + math.exp(-(theta[event.player_id] - theta[event.opponent_player_id])))
        outcome = int(event.outcome == "successful")
        brier.append((probability - outcome) ** 2)
        baseline_brier.append((baseline - outcome) ** 2)
        loss.append(-math.log(probability if outcome else 1 - probability))
        baseline_loss.append(-math.log(baseline if outcome else 1 - baseline))
    assert evaluation.model_brier == pytest.approx(sum(brier) / 240)
    assert evaluation.baseline_brier == pytest.approx(sum(baseline_brier) / 240)
    assert evaluation.model_log_loss == pytest.approx(sum(loss) / 240)
    assert evaluation.baseline_log_loss == pytest.approx(sum(baseline_loss) / 240)
    # Adversarially change every test label; no model fit/baseline can change.
    changed = source.model_dump()
    for event in changed["events"]:
        if event["match_id"] not in train_matches:
            event["outcome"] = "unsuccessful" if event["outcome"] == "successful" else "successful"
    altered = engine.evaluate(EvidenceSnapshot.model_validate(changed))
    assert altered.training_evidence_fingerprint == evaluation.training_evidence_fingerprint
    assert altered.baseline_home_win_probability == evaluation.baseline_home_win_probability
    print(f"BENCH {world} seed={seed}: n=240 model/base Brier={evaluation.model_brier:.6f}/{evaluation.baseline_brier:.6f}; logloss={evaluation.model_log_loss:.6f}/{evaluation.baseline_log_loss:.6f}")


def test_shot_component_holdout_scores_equal_independent_global_baseline():
    source = snapshot(matches=4)
    report = spatial_evaluate(source, policy=policy())
    # Three train matches: six shots, three goals, one zero-terminal prior.
    # Only one shot cell: spatial and global baselines MUST coincide, not win.
    probability = 3 / 7
    assert report.sample_count == 2 and report.excluded_count == 0
    assert report.model_brier == report.baseline_brier == pytest.approx(((1 - probability) ** 2 + probability ** 2) / 2)
    assert report.model_log_loss == report.baseline_log_loss == pytest.approx(-(math.log(probability) + math.log(1 - probability)) / 2)


def test_spatial_rejects_unidentified_legacy_candidate_in_fit_and_old_artifact():
    source = snapshot()
    artifact = fit(source, policy=policy())
    data = source.model_dump()
    data["events"][0]["provenance"] = "model_candidate"
    unsafe = EvidenceSnapshot.model_validate(data)
    with pytest.raises(ActionValueUnavailable, match="reviewer-confirmed"):
        fit(unsafe, policy=policy())
    assert score(unsafe.events[0], scope=unsafe.scope, artifact=artifact).value is None
    # Simulate a checksummed old artifact whose fingerprint matches unsafe data.
    # New gates must inspect training evidence, not trust the checksum alone.
    legacy = artifact.model_copy(update={"training_fingerprint": training_fingerprint(unsafe, artifact.policy)})
    assert score_from_snapshot(unsafe, event_id=unsafe.events[1].event_id, artifact=legacy).value is None


@pytest.mark.parametrize("failure", ["overlap", "outside_duration"])
def test_all_engines_refuse_ambiguous_or_out_of_bounds_identity(failure):
    source = identity_snapshot()
    data = source.model_dump()
    if failure == "overlap":
        data["identities"].append(data["identities"][0] | {"id": "conflict", "player_id": "away"})
    else:
        data["identities"][0]["end_seconds"] = 601
    invalid = EvidenceSnapshot.model_validate(data)
    with pytest.raises(ActionValueUnavailable):
        fit(invalid, policy=policy())
    event = data["events"][0]
    event.update(kind="duel", contest_id="duel-identity", opponent_player_id="away", possession_retained=None)
    data["events"] = [event]
    duel = EvidenceSnapshot.model_validate(data)
    assert BradleyTerryEngine().fit(duel).accepted_contests == 0
    memory = ReviewedPlayerMemoryEngine().build(duel, player_id="home")
    assert memory.approved_event_count == 0
    assert not any(r.relation == "direct_duel" for r in memory.relationships)


def test_memory_and_ratings_share_explicit_contest_identity_not_equal_timestamps():
    data = duel_snapshot([("A", "B", 1, 0)]).model_dump()
    original = data["events"][0]
    data["events"].append(original | {"event_id": "mirror", "player_id": "B", "opponent_player_id": "A", "outcome": "unsuccessful", "timestamp_seconds": original["timestamp_seconds"] + 0.5})
    source = EvidenceSnapshot.model_validate(data)
    assert BradleyTerryEngine().fit(source).accepted_contests == 1
    for player in ("A", "B"):
        memory = ReviewedPlayerMemoryEngine().build(source, player_id=player)
        assert memory.approved_event_count == 1
        assert next(r for r in memory.relationship_details if r.relation == "direct_duel").explicit_contest_count == 1
    # A confirmed-identity failure on one mirror invalidates the ENTIRE contest.
    data["events"][0].update(provenance="model_candidate")
    invalid = EvidenceSnapshot.model_validate(data)
    assert BradleyTerryEngine().fit(invalid).accepted_contests == 0
    assert ReviewedPlayerMemoryEngine().build(invalid, player_id="B").approved_event_count == 0


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_detector_confidence_is_not_an_allowed_rating_input(confidence):
    source = duel_snapshot([("A", "B", 7, 3)])
    data = source.model_dump()
    data["events"][0]["confidence"] = confidence
    # Detector scores have no field at this evidence boundary: they cannot be
    # treated as fractional outcomes or calibrated identity probabilities.
    with pytest.raises(ValueError, match="confidence"):
        EvidenceSnapshot.model_validate(data)


def test_perfect_match_dependence_is_disclosed_not_claimed_to_be_adjusted():
    # Deliberately correlated world: one latent match outcome repeated as many
    # differently identified duels. No assertion of calibrated interval coverage.
    latent = [random.Random(seed).random() < 0.5 for seed in range(1729, 1741)]
    reports = [BradleyTerryEngine().fit(duel_snapshot([("A", "B", repeats if win else 0, 0 if win else repeats) for win in latent])) for repeats in (1, 20)]
    sparse, repeated = [next(r for r in report.ratings if r.player_id == "A") for report in reports]
    assert sparse.matches == repeated.matches == 12
    assert repeated.posterior_sd < sparse.posterior_sd
    # The conditional iid Laplace interval narrows even though the *independent*
    # information is unchanged. The API must not invent a dependence-adjusted N.
    assert all(d.dependence_adjusted_effective_sample_size is None for d in reports[1].player_details)
    assert repeated.component_rank is None  # one opponent remains insufficient


@pytest.mark.parametrize("scope_role", ["unknown", "all_roles"])
def test_unknown_appearance_roles_never_receive_a_rank(scope_role):
    data = duel_snapshot([("A", "B", 20, 20), ("A", "B", 20, 20)]).model_dump()
    data["scope"]["role"] = scope_role
    for appearance in data["appearances"]:
        appearance["role"] = "unknown"
    report = BradleyTerryEngine().fit(EvidenceSnapshot.model_validate(data), policy=DuelRatingPolicy(min_matches=2, min_distinct_opponents=1))
    assert report.accepted_contests == 80
    assert all(r.component_rank is None and r.eligibility == "provisional" for r in report.ratings)
    assert all("reviewed_appearance_role_required_for_rank" in r.reasons for r in report.ratings)
