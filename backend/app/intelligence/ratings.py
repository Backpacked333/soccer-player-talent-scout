"""Auditable direct-duel estimates, not talent/potential or match-win ratings.

Pure Python, bounded Gaussian-prior Bradley--Terry MAP and centered Laplace
covariance. Every fit starts from current reviewed evidence; no mutable cache,
external model, hidden training data, timestamp-based joins or incremental Elo.
See docs/OPPONENT_RATINGS.md for the statistical target and limitations.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import NormalDist
from typing import Literal

from pydantic import Field, model_validator

from .contracts import (
    Digest, DuelRatingEstimate, DuelRatingPolicy, DuelRatingReport,
    EvaluationReport, EvidenceSnapshot, FitDiagnostics, Identifier,
    IntelligenceContract, Probability, ReviewedAction, canonical_fingerprint,
    chronological_match_split, conflicting_identity_ids,
)

METHOD_VERSION: Literal["regularized-bradley-terry-v1"] = "regularized-bradley-terry-v1"
_Z95 = NormalDist().inv_cdf(0.975)
UNCERTAINTY_CAVEAT = (
    "95% centered Laplace posterior intervals conditional on independent selected "
    "contests and the disclosed Gaussian prior; not match-cluster calibrated, "
    "not youth-model validation or a confidence guarantee."
)


class RatingCapacityError(ValueError):
    """Refuse oversized cohorts instead of silently selecting players or events."""


class ContestEvidence(IntelligenceContract):
    match_id: Identifier
    contest_id: str = Field(min_length=1, max_length=80)
    player_ids: list[Identifier] = Field(min_length=2, max_length=2)
    winner_id: Identifier
    loser_id: Identifier
    home_player_id: Identifier
    event_ids: list[Identifier] = Field(min_length=1, max_length=10000)


class ExcludedEvidence(IntelligenceContract):
    event_id: Identifier
    reasons: list[str] = Field(min_length=1, max_length=20)


class PlayerDuelEvidence(IntelligenceContract):
    player_id: Identifier
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    observed_win_fraction: Probability | None = None
    # This is a likelihood count, NOT a dependence-corrected effective N.
    likelihood_contests: int = Field(ge=0)
    dependence_adjusted_effective_sample_size: None = None
    # The denominator of all possible duels is not supplied by this contract.
    duel_capture_fraction: None = None
    match_ids: list[Identifier] = Field(default_factory=list, max_length=1000)
    opponent_ids: list[Identifier] = Field(default_factory=list, max_length=127)
    event_ids: list[Identifier] = Field(default_factory=list, max_length=10000)
    largest_match_share: Probability | None = None
    probability_against_component_center: Probability | None = None
    probability_interval_low: Probability | None = None
    probability_interval_high: Probability | None = None


class ComponentPosterior(IntelligenceContract):
    """JSON-only sufficient fitted state for same-component comparisons.

    Covariance is for theta_i - component_mean(theta), not an identified global
    ability. The zero common-mode direction is intentional. No pickle/weights.
    """
    component_id: str
    player_ids: list[Identifier] = Field(min_length=2, max_length=128)
    centered_log_odds: list[float] = Field(min_length=2, max_length=128)
    covariance: list[list[float]] = Field(min_length=2, max_length=128)
    diagnostics: FitDiagnostics

    @model_validator(mode="after")
    def validate_shape(self) -> ComponentPosterior:
        n = len(self.player_ids)
        if len(set(self.player_ids)) != n or len(self.centered_log_odds) != n or len(self.covariance) != n:
            raise ValueError("component posterior shape or canonical IDs invalid")
        if any(len(row) != n for row in self.covariance):
            raise ValueError("component covariance must be square")
        if not self.diagnostics.converged:
            raise ValueError("a nonconverged fit is not a posterior artifact")
        for i in range(n):
            if self.covariance[i][i] <= 0:
                raise ValueError("identified component contrasts need positive marginal variance")
            for j in range(i):
                if abs(self.covariance[i][j] - self.covariance[j][i]) > 1e-10:
                    raise ValueError("component covariance must be symmetric")
        return self


class OpponentRatingReport(DuelRatingReport):
    """Shared report plus audit detail. Preserve this subtype when serializing."""
    method_version: Literal["regularized-bradley-terry-v1"] = METHOD_VERSION
    validation_status: Literal["unvalidated_observed_duel_estimator"] = "unvalidated_observed_duel_estimator"
    training_match_ids: list[Identifier] = Field(default_factory=list, max_length=1000)
    player_details: list[PlayerDuelEvidence] = Field(default_factory=list, max_length=128)
    contests: list[ContestEvidence] = Field(default_factory=list, max_length=10000)
    exclusions: list[ExcludedEvidence] = Field(default_factory=list, max_length=10000)
    contradictory_event_ids: list[Identifier] = Field(default_factory=list, max_length=10000)
    ambiguous_event_ids: list[Identifier] = Field(default_factory=list, max_length=10000)
    components: list[ComponentPosterior] = Field(default_factory=list, max_length=64)
    uncertainty_caveat: str = UNCERTAINTY_CAVEAT
    rank_tie_tolerance: float = 1e-10
    limitations: list[str] = Field(default_factory=lambda: [
        "Sample eligibility is not model validation; component ranks order eligible MAP point estimates, not statistically proven differences.",
        "Only explicitly reviewed selected duels are modeled. Overall duel capture fraction and dependence-adjusted effective sample size are unavailable.",
        "Age/role/competition scoping does not establish fairness or remove selection, tactical context or within-match dependence.",
        "Accumulated corrected observations refit a narrow performance estimator; no detector retraining, face recognition, future-potential score or match-win attribution.",
    ])

    @model_validator(mode="after")
    def validate_evaluation_provenance(self) -> OpponentRatingReport:
        match_ids = set(self.training_match_ids)
        if len(match_ids) != len(self.training_match_ids):
            raise ValueError("fit match IDs must be unique")
        if any(contest.match_id not in match_ids for contest in self.contests):
            raise ValueError("contest evidence must belong to the fitted match set")
        if self.evaluation is not None:
            if set(self.evaluation.test_match_ids) & set(self.training_match_ids):
                raise ValueError("cannot attach held-out evaluation to a model fitted on its test matches")
            if set(self.evaluation.train_match_ids) != set(self.training_match_ids):
                raise ValueError("evaluation must describe this report's actual training match set")
        return self


class PairwisePrediction(IntelligenceContract):
    player_id: Identifier
    opponent_player_id: Identifier
    status: Literal["unavailable", "available"]
    eligibility: Literal["unavailable", "provisional", "eligible"] = "unavailable"
    method_version: Literal["regularized-bradley-terry-v1"] = METHOD_VERSION
    evidence_fingerprint: Digest
    component_id: str | None = None
    comparison_basis: Literal["unavailable", "direct_and_network_evidence", "indirect_network_evidence"] = "unavailable"
    direct_contests: int | None = Field(default=None, ge=0, le=10000)
    direct_match_ids: list[Identifier] = Field(default_factory=list, max_length=1000)
    direct_event_ids: list[Identifier] = Field(default_factory=list, max_length=10000)
    win_probability: Probability | None = None
    interval_low: Probability | None = None
    interval_high: Probability | None = None
    log_odds_difference: float | None = None
    posterior_sd: float | None = Field(default=None, gt=0)
    reasons: list[str] = Field(default_factory=list)
    interpretation: str = (
        "Plug-in MAP win propensity for another selected direct duel in this cohort; "
        "interval is approximate posterior uncertainty in that propensity, not a "
        "prediction interval for a binary outcome. " + UNCERTAINTY_CAVEAT
    )

    @model_validator(mode="after")
    def validate_availability(self) -> PairwisePrediction:
        values = (self.win_probability, self.interval_low, self.interval_high, self.log_odds_difference, self.posterior_sd)
        if self.status == "unavailable" and (any(value is not None for value in values) or self.eligibility != "unavailable" or self.direct_contests is not None or self.comparison_basis != "unavailable"):
            raise ValueError("unavailable comparisons cannot carry numeric predictions")
        if self.status == "available":
            if any(value is None for value in values) or self.component_id is None or self.eligibility == "unavailable" or self.direct_contests is None or self.comparison_basis == "unavailable":
                raise ValueError("available comparisons require identified posterior uncertainty")
            if (self.direct_contests > 0) != (self.comparison_basis == "direct_and_network_evidence"):
                raise ValueError("direct evidence must be distinguished from indirect network comparisons")
            assert self.interval_low is not None and self.win_probability is not None and self.interval_high is not None
            if not self.interval_low <= self.win_probability <= self.interval_high:
                raise ValueError("probability interval must contain the estimate")
        return self


class DuelEvaluationReport(EvaluationReport):
    training_evidence_fingerprint: Digest | None = None
    training_contests: int = Field(default=0, ge=0, le=10000)
    test_contests: int = Field(default=0, ge=0, le=10000)
    baseline_home_win_probability: Probability | None = None
    scoring_event_ids: list[Identifier] = Field(default_factory=list, max_length=10000)
    cold_start_contests: int = Field(default=0, ge=0, le=10000)
    disconnected_contests: int = Field(default=0, ge=0, le=10000)


@dataclass
class _Selection:
    players: list[str]
    contests: list[ContestEvidence]
    exclusions: list[ExcludedEvidence]
    duplicates: list[str]
    contradictions: list[str]
    ambiguities: list[str]


def _validated(snapshot: EvidenceSnapshot) -> EvidenceSnapshot:
    # Revalidate even instances created with model_copy/model_construct, which
    # bypass Pydantic validation. Authorization remains the snapshot service's job.
    return EvidenceSnapshot.model_validate(snapshot.model_dump(mode="python"))


def _fingerprint(snapshot: EvidenceSnapshot, policy: DuelRatingPolicy) -> str:
    data = snapshot.model_dump(mode="json")
    for collection, key in (("matches", "match_id"), ("appearances", "appearance_id"), ("events", "event_id"), ("identities", "id")):
        # Roster membership (match, player) is unique in the shared contract;
        # appearance_id itself is not required to be unique there.
        data[collection] = sorted(data[collection], key=lambda row: (row[key], row.get("match_id", ""), row.get("player_id", "")))
    return canonical_fingerprint({"method_version": METHOD_VERSION, "policy": policy.model_dump(mode="json"), "snapshot": data})


def _select(snapshot: EvidenceSnapshot) -> _Selection:
    roster = {(row.match_id, row.player_id): row for row in snapshot.appearances}
    matches = {row.match_id: row for row in snapshot.matches}
    identities = {row.id: row for row in snapshot.identities}
    identity_conflicts = conflicting_identity_ids(snapshot.identities)
    identity_conflicts.update(row.id for row in snapshot.identities if row.end_seconds > matches[row.match_id].duration_seconds)
    role = snapshot.scope.role
    players = sorted({row.player_id for row in snapshot.appearances if role == "all_roles" or row.role == role})
    excluded: dict[str, list[str]] = {}
    groups: dict[tuple[str, str], list[ReviewedAction]] = defaultdict(list)

    # Two identifiers must not let exact inverse annotations count twice. A
    # timestamp collision alone never identifies a physical contest: different
    # IDs with reversed actors at the exact same frame/pair stay unresolved.
    # Missing-ID records can reveal that ambiguity but cannot supply outcomes.
    def annotation_group(event: ReviewedAction) -> tuple[str, str, str]:
        return (event.match_id, "contest", event.contest_id) if event.contest_id is not None else (event.match_id, "event", event.event_id)

    buckets: dict[tuple[str, float, str, str], dict[tuple[str, int | None], set[tuple[str, str, str]]]] = defaultdict(lambda: defaultdict(set))
    for event in snapshot.events:
        if event.status == "approved" and event.kind == "duel" and event.opponent_player_id is not None:
            left, right = sorted((event.player_id, event.opponent_player_id))
            buckets[(event.match_id, event.timestamp_seconds, left, right)][(event.player_id, event.period)].add(annotation_group(event))
    ambiguous_groups: set[tuple[str, str, str]] = set()
    for (_, _, left, right), entries in buckets.items():
        for (actor, period), actor_groups in entries.items():
            if actor != left:
                continue
            for (other, other_period), other_groups in entries.items():
                if other != right or (period is not None and other_period is not None and period != other_period):
                    continue
                candidates = actor_groups | other_groups
                if len(candidates) > 1:
                    ambiguous_groups.update(candidates)

    def reasons_for(event: ReviewedAction) -> list[str]:
        reasons: list[str] = []
        actor = roster[(event.match_id, event.player_id)]
        opponent = roster.get((event.match_id, event.opponent_player_id or ""))
        if opponent is None:
            reasons.append("explicit_opponent_required")
        if role != "all_roles" and (actor.role != role or (opponent is not None and opponent.role != role)):
            reasons.append("both_players_must_match_role_scope")
        if event.timestamp_seconds >= matches[event.match_id].duration_seconds or (event.end_timestamp_seconds is not None and event.end_timestamp_seconds > matches[event.match_id].duration_seconds):
            reasons.append("event_outside_match_duration")
        if event.provenance == "model_candidate" and event.identity_assignment_id is None:
            reasons.append("reviewed_track_identity_required_for_model_candidate")
        if event.identity_assignment_id is not None:
            identity = identities.get(event.identity_assignment_id)
            if identity is None or identity.id in identity_conflicts or identity.status != "confirmed" or identity.revision != event.identity_assignment_revision or identity.player_id != event.player_id or identity.match_id != event.match_id:
                reasons.append("identity_not_currently_confirmed_for_actor")
            elif not identity.start_seconds <= event.timestamp_seconds <= (event.end_timestamp_seconds if event.end_timestamp_seconds is not None else event.timestamp_seconds) <= identity.end_seconds:
                reasons.append("event_outside_confirmed_identity_interval")
        return reasons

    for event in sorted(snapshot.events, key=lambda row: row.event_id):
        if event.status != "approved":
            excluded[event.event_id] = ["event_not_approved"]
        elif event.kind != "duel":
            excluded[event.event_id] = ["not_an_explicit_duel"]
        elif event.contest_id is None:
            excluded[event.event_id] = ["explicit_contest_id_required_no_timestamp_join"]
            if annotation_group(event) in ambiguous_groups:
                excluded[event.event_id].append("ambiguous_inverse_annotations_across_contest_ids")
        else:
            groups[(event.match_id, event.contest_id)].append(event)

    contests: list[ContestEvidence] = []
    duplicates: list[str] = []
    contradictions: list[str] = []
    for (match_id, contest_id), events in sorted(groups.items()):
        reasons = {event.event_id: reasons_for(event) for event in events}
        pairs = {tuple(sorted((event.player_id, event.opponent_player_id or ""))) for event in events}
        winners = {event.player_id if event.outcome == "successful" else event.opponent_player_id for event in events}
        periods = {event.period for event in events if event.period is not None}
        conflict = len(pairs) != 1 or len(winners) != 1 or len(periods) > 1
        ambiguity = (match_id, "contest", contest_id) in ambiguous_groups
        if conflict or ambiguity or any(reasons.values()):
            common_reasons = (["contradictory_contest_annotations"] if conflict else []) + (["ambiguous_inverse_annotations_across_contest_ids"] if ambiguity else []) + (["contest_contains_ineligible_annotation"] if any(reasons.values()) else [])
            for event in events:
                excluded[event.event_id] = sorted(set(reasons[event.event_id] + common_reasons))
            if conflict:
                contradictions.extend(event.event_id for event in events)
            continue
        event = events[0]
        assert event.opponent_player_id is not None
        pair = sorted((event.player_id, event.opponent_player_id))
        winner = event.player_id if event.outcome == "successful" else event.opponent_player_id
        contests.append(ContestEvidence(
            match_id=match_id, contest_id=contest_id, player_ids=pair,
            winner_id=winner, loser_id=next(player for player in pair if player != winner),
            home_player_id=next(player for player in pair if roster[(match_id, player)].side == "home"),
            event_ids=[item.event_id for item in events],
        ))
        duplicates.extend(item.event_id for item in events[1:])
    ambiguities = sorted(event_id for event_id, reasons in excluded.items() if "ambiguous_inverse_annotations_across_contest_ids" in reasons)
    return _Selection(players, contests, [ExcludedEvidence(event_id=key, reasons=value) for key, value in sorted(excluded.items())], sorted(duplicates), sorted(contradictions), ambiguities)


def contest_evidence(snapshot: EvidenceSnapshot) -> tuple[list[ContestEvidence], list[ExcludedEvidence]]:
    """Shared reviewed contest selection without fitting or assigning predictions."""
    selection = _select(_validated(snapshot))
    return selection.contests, selection.exclusions


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exponent = math.exp(value)
    return exponent / (1 + exponent)


def _softplus(value: float) -> float:
    return max(value, 0) + math.log1p(math.exp(-abs(value)))


def _cholesky(matrix: list[list[float]]) -> list[list[float]]:
    n = len(matrix)
    lower = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            value = matrix[i][j] - math.fsum(lower[i][k] * lower[j][k] for k in range(j))
            if i == j:
                if not math.isfinite(value) or value <= 0:
                    raise ArithmeticError("posterior Hessian is not positive definite")
                lower[i][j] = math.sqrt(value)
            else:
                lower[i][j] = value / lower[j][j]
    return lower


def _solve(lower: list[list[float]], rhs: list[float]) -> list[float]:
    n = len(rhs)
    forward = [0.0] * n
    for i in range(n):
        forward[i] = (rhs[i] - math.fsum(lower[i][j] * forward[j] for j in range(i))) / lower[i][i]
    solution = [0.0] * n
    for i in range(n - 1, -1, -1):
        solution[i] = (forward[i] - math.fsum(lower[j][i] * solution[j] for j in range(i + 1, n))) / lower[i][i]
    return solution


# Aggregated pair entries: canonical index i, index j, contests, i-wins.
_Pair = tuple[int, int, int, int]


def _objective(theta: list[float], pairs: list[_Pair], precision: float) -> float:
    # Separate win/loss softplus terms avoid cancellation for separated samples.
    return 0.5 * precision * math.fsum(value * value for value in theta) + math.fsum(
        wins * _softplus(theta[j] - theta[i]) + (count - wins) * _softplus(theta[i] - theta[j])
        for i, j, count, wins in pairs
    )


def _derivatives(theta: list[float], pairs: list[_Pair], precision: float) -> tuple[list[float], list[list[float]]]:
    n = len(theta)
    gradient = [precision * value for value in theta]
    hessian = [[precision if i == j else 0.0 for j in range(n)] for i in range(n)]
    for i, j, count, wins in pairs:
        probability = _sigmoid(theta[i] - theta[j])
        residual = count * probability - wins
        weight = count * probability * (1 - probability)
        gradient[i] += residual
        gradient[j] -= residual
        hessian[i][i] += weight
        hessian[j][j] += weight
        hessian[i][j] -= weight
        hessian[j][i] -= weight
    return gradient, hessian


def _fit_component(players: list[str], contests: list[ContestEvidence], policy: DuelRatingPolicy, component_id: str) -> tuple[ComponentPosterior | None, FitDiagnostics]:
    indices = {player: index for index, player in enumerate(players)}
    counts: dict[tuple[int, int], list[int]] = {}
    for contest in contests:
        i, j = (indices[player] for player in contest.player_ids)
        entry = counts.setdefault((i, j), [0, 0])
        entry[0] += 1
        entry[1] += int(contest.winner_id == players[i])
    pairs = [(i, j, count, wins) for (i, j), (count, wins) in sorted(counts.items())]
    n = len(players)
    theta = [0.0] * n
    precision = 1 / policy.prior_standard_deviation ** 2
    objective = _objective(theta, pairs, precision)
    residual: float | None = None
    iteration = 0
    try:
        for iteration in range(policy.max_iterations + 1):
            gradient, hessian = _derivatives(theta, pairs, precision)
            residual = max(abs(value) for value in gradient)
            if not math.isfinite(residual):
                raise ArithmeticError("nonfinite_gradient")
            if residual <= policy.convergence_tolerance:
                lower = _cholesky(hessian)
                inverse_columns = [_solve(lower, [float(i == j) for i in range(n)]) for j in range(n)]
                # H 1 = precision * 1 exactly: remove the unlearned common mode.
                common_variance = policy.prior_standard_deviation ** 2 / n
                covariance = [[(inverse_columns[j][i] + inverse_columns[i][j]) / 2 - common_variance for j in range(n)] for i in range(n)]
                mean = math.fsum(theta) / n
                diagnostics = FitDiagnostics(converged=True, iterations=iteration, objective=objective, max_residual=residual)
                posterior = ComponentPosterior(component_id=component_id, player_ids=players, centered_log_odds=[value - mean for value in theta], covariance=covariance, diagnostics=diagnostics)
                return posterior, diagnostics
            if iteration == policy.max_iterations:
                break
            step = _solve(_cholesky(hessian), gradient)
            decrement = math.fsum(g * d for g, d in zip(gradient, step, strict=True))
            if not math.isfinite(decrement) or decrement <= 0:
                raise ArithmeticError("non_descent_newton_step")
            scale = 1.0
            for _ in range(40):
                candidate = [value - scale * change for value, change in zip(theta, step, strict=True)]
                candidate_objective = _objective(candidate, pairs, precision)
                # Permit roundoff near the optimum; convergence still requires
                # the gradient criterion, never just an unchanged objective.
                slack = 1e-13 * max(1.0, abs(objective))
                if math.isfinite(candidate_objective) and candidate_objective <= objective - 1e-4 * scale * decrement + slack:
                    theta, objective = candidate, candidate_objective
                    break
                scale *= 0.5
            else:
                raise ArithmeticError("line_search_exhausted")
    except (ArithmeticError, ValueError) as error:
        return None, FitDiagnostics(converged=False, iterations=iteration, objective=objective if math.isfinite(objective) else None, max_residual=residual if residual is not None and math.isfinite(residual) else None, warnings=[f"numerical_failure:{error}"])
    return None, FitDiagnostics(converged=False, iterations=iteration, objective=objective, max_residual=residual, warnings=["iteration_limit_reached"])


def _components(contests: list[ContestEvidence]) -> list[list[str]]:
    adjacent: dict[str, set[str]] = defaultdict(set)
    for contest in contests:
        left, right = contest.player_ids
        adjacent[left].add(right)
        adjacent[right].add(left)
    remaining = set(adjacent)
    groups: list[list[str]] = []
    while remaining:
        stack = [min(remaining)]
        group: set[str] = set()
        while stack:
            player = stack.pop()
            if player not in group:
                group.add(player)
                stack.extend(sorted(adjacent[player] - group, reverse=True))
        remaining -= group
        groups.append(sorted(group))
    return groups


class BradleyTerryEngine:
    """Implements contracts.DuelRatingEngine; stateless and deterministic."""

    def fit(self, snapshot: EvidenceSnapshot, *, policy: DuelRatingPolicy | None = None) -> OpponentRatingReport:
        snapshot = _validated(snapshot)
        policy = DuelRatingPolicy.model_validate((policy or DuelRatingPolicy()).model_dump())
        selection = _select(snapshot)
        if len(selection.players) > policy.max_players:
            raise RatingCapacityError(f"cohort has {len(selection.players)} roster players; limit is {policy.max_players}; narrow the explicit cohort")
        if len(selection.contests) > policy.max_contests:
            raise RatingCapacityError(f"cohort has {len(selection.contests)} accepted contests; limit is {policy.max_contests}; narrow the explicit cohort")
        posteriors: list[ComponentPosterior] = []
        diagnostics: list[FitDiagnostics] = []
        component_for: dict[str, str] = {}
        for players in _components(selection.contests):
            component_id = "duel-" + canonical_fingerprint({"scope": snapshot.scope.model_dump(mode="json"), "players": players})[:24]
            component_for.update({player: component_id for player in players})
            player_set = set(players)
            posterior, diagnostic = _fit_component(players, [contest for contest in selection.contests if contest.winner_id in player_set], policy, component_id)
            diagnostics.append(diagnostic)
            if posterior is not None:
                posteriors.append(posterior)
        converged = bool(selection.contests) and all(item.converged for item in diagnostics)
        if not converged:
            posteriors = []  # Shared contract suppresses every numerical result.
        warnings = [warning for diagnostic in diagnostics for warning in diagnostic.warnings]
        if not selection.contests:
            warnings.append("no_eligible_reviewed_contests")
        if selection.contradictions:
            warnings.append("contradictory_contests_excluded_not_averaged")
        if selection.ambiguities:
            warnings.append("ambiguous_inverse_annotations_excluded_not_timestamp_merged")
        if len(diagnostics) > 1:
            warnings.append("disconnected_components_have_no_shared_ranking_or_predictions")
        fitted = {player: (posterior, index) for posterior in posteriors for index, player in enumerate(posterior.player_ids)}
        ratings: list[DuelRatingEstimate] = []
        details: list[PlayerDuelEvidence] = []
        by_player: dict[str, list[ContestEvidence]] = defaultdict(list)
        for contest in selection.contests:
            for player in contest.player_ids:
                by_player[player].append(contest)
        roster_roles = {(row.match_id, row.player_id): row.role for row in snapshot.appearances}
        for player in selection.players:
            evidence = by_player[player]
            matches = Counter(contest.match_id for contest in evidence)
            opponents = sorted({other for contest in evidence for other in contest.player_ids if other != player})
            wins = sum(contest.winner_id == player for contest in evidence)
            largest_share = max(matches.values()) / len(evidence) if evidence else None
            reasons: list[str] = []
            if not evidence:
                reasons.append("no_eligible_reviewed_contests")
            elif not converged:
                reasons.append("solver_not_converged")
            else:
                if any(roster_roles[(contest.match_id, player)] == "unknown" for contest in evidence):
                    reasons.append("reviewed_appearance_role_required_for_rank")
                if len(evidence) < policy.min_contests:
                    reasons.append("below_minimum_contests")
                if len(matches) < policy.min_matches:
                    reasons.append("below_minimum_matches")
                if len(opponents) < policy.min_distinct_opponents:
                    reasons.append("below_minimum_distinct_opponents")
                if largest_share is not None and largest_share > policy.max_match_share:
                    reasons.append("excessive_single_match_concentration")
            estimate = DuelRatingEstimate(player_id=player, component_id=component_for.get(player), contests=len(evidence), matches=len(matches), distinct_opponents=len(opponents), eligibility="unavailable", reasons=reasons)
            detail = PlayerDuelEvidence(player_id=player, wins=wins, losses=len(evidence) - wins, likelihood_contests=len(evidence), observed_win_fraction=wins / len(evidence) if evidence else None, match_ids=sorted(matches), opponent_ids=opponents, event_ids=sorted(event_id for contest in evidence for event_id in contest.event_ids), largest_match_share=largest_share)
            if player in fitted:
                posterior, index = fitted[player]
                mean = posterior.centered_log_odds[index]
                sd = math.sqrt(posterior.covariance[index][index])
                estimate = DuelRatingEstimate(**(estimate.model_dump() | dict(log_odds=mean, posterior_sd=sd, interval_low=mean - _Z95 * sd, interval_high=mean + _Z95 * sd, eligibility="provisional" if reasons else "eligible")))
                detail.probability_against_component_center = _sigmoid(mean)
                detail.probability_interval_low = _sigmoid(mean - _Z95 * sd)
                detail.probability_interval_high = _sigmoid(mean + _Z95 * sd)
            ratings.append(estimate)
            details.append(detail)
        for estimate in ratings:
            if estimate.eligibility == "eligible" and estimate.log_odds is not None:
                # Ties share competition rank; names/IDs never break ties.
                estimate.component_rank = 1 + sum(other.eligibility == "eligible" and other.component_id == estimate.component_id and other.log_odds is not None and other.log_odds > estimate.log_odds + 1e-10 for other in ratings)
        return OpponentRatingReport(
            scope=snapshot.scope, policy=policy, evidence_fingerprint=_fingerprint(snapshot, policy), ratings=ratings,
            training_match_ids=[match.match_id for match in sorted(snapshot.matches, key=lambda item: (item.match_date, item.match_id))],
            diagnostics=FitDiagnostics(converged=converged, iterations=max((item.iterations for item in diagnostics), default=0), objective=math.fsum(item.objective for item in diagnostics if item.objective is not None) if diagnostics else None, max_residual=max((item.max_residual for item in diagnostics if item.max_residual is not None), default=None), warnings=sorted(set(warnings))),
            accepted_contests=len(selection.contests), excluded_event_ids=[item.event_id for item in selection.exclusions], duplicate_event_ids=selection.duplicates,
            player_details=details, contests=selection.contests, exclusions=selection.exclusions, contradictory_event_ids=selection.contradictions, ambiguous_event_ids=selection.ambiguities, components=posteriors,
        )

    def predict(self, report: OpponentRatingReport, *, player_id: str, opponent_player_id: str) -> PairwisePrediction:
        """No fallback prior/50% prediction for missing or disconnected players."""
        pair = sorted((player_id, opponent_player_id))
        direct = [contest for contest in report.contests if contest.player_ids == pair]
        return self._predict(report, player_id=player_id, opponent_player_id=opponent_player_id, direct=direct)

    def _predict(self, report: OpponentRatingReport, *, player_id: str, opponent_player_id: str, direct: list[ContestEvidence]) -> PairwisePrediction:
        result = PairwisePrediction(player_id=player_id, opponent_player_id=opponent_player_id, status="unavailable", evidence_fingerprint=report.evidence_fingerprint)
        ratings = {row.player_id: row for row in report.ratings}
        left, right = ratings.get(player_id), ratings.get(opponent_player_id)
        reason: str | None = None
        if player_id == opponent_player_id:
            reason = "distinct_players_required"
        elif not report.diagnostics.converged:
            reason = "no_converged_posterior"
        elif left is None or right is None or left.log_odds is None or right.log_odds is None:
            reason = "both_players_require_training_contests"
        elif left.component_id != right.component_id:
            reason = "players_in_disconnected_components"
        if reason:
            result.reasons = [reason]
            return result
        assert left is not None and right is not None
        component = next((item for item in report.components if item.component_id == left.component_id), None)
        if component is None or player_id not in component.player_ids or opponent_player_id not in component.player_ids:
            result.reasons = ["posterior_covariance_unavailable"]
            return result
        i, j = component.player_ids.index(player_id), component.player_ids.index(opponent_player_id)
        delta = component.centered_log_odds[i] - component.centered_log_odds[j]
        variance = component.covariance[i][i] + component.covariance[j][j] - 2 * component.covariance[i][j]
        if not math.isfinite(variance) or variance <= 0:
            result.reasons = ["invalid_contrast_variance"]
            return result
        sd = math.sqrt(variance)
        provisional = left.eligibility != "eligible" or right.eligibility != "eligible"
        return PairwisePrediction(**(result.model_dump() | dict(status="available", eligibility="provisional" if provisional else "eligible", component_id=component.component_id, comparison_basis="direct_and_network_evidence" if direct else "indirect_network_evidence", direct_contests=len(direct), direct_match_ids=sorted({contest.match_id for contest in direct}), direct_event_ids=sorted(event_id for contest in direct for event_id in contest.event_ids), win_probability=_sigmoid(delta), interval_low=_sigmoid(delta - _Z95 * sd), interval_high=_sigmoid(delta + _Z95 * sd), log_odds_difference=delta, posterior_sd=sd, reasons=["one_or_both_players_below_support_thresholds"] if provisional else [])))

    def evaluate(self, snapshot: EvidenceSnapshot, *, policy: DuelRatingPolicy | None = None, test_fraction: float = 0.25) -> DuelEvaluationReport:
        """Fit earliest whole dates only; exclude unknown/disconnected test pairs."""
        snapshot = _validated(snapshot)
        policy = DuelRatingPolicy.model_validate((policy or DuelRatingPolicy()).model_dump())
        if not 0 < test_fraction < 1:
            raise ValueError("test fraction must be strictly inside (0,1)")
        report = DuelEvaluationReport(
            status="unavailable", mode=snapshot.scope.mode,
            target="Home-rostered player's outcome in an explicit reviewed direct duel; not match result or talent",
            baseline_description="Training-only home-side duel win rate with Beta(1,1) smoothing; no test-result or test-roster updates",
            limitations=[UNCERTAINTY_CAVEAT, "One grouped chronological split, not independent youth/subgroup validation; repeated players and selective review can bias scores.", "Excluded count is unscored test annotation rows after accepted mirrored duplicates are removed; sample count is unique scored contests.", "Whole dates stay together. Unseen or disconnected training opponents receive no prediction, including no prior-only 50% fallback."],
        )
        try:
            train_ids, test_ids = chronological_match_split(snapshot.matches, test_fraction=test_fraction)
        except ValueError:
            report.limitations.append("At least two distinct match dates are required.")
            return report
        train_set, test_set = set(train_ids), set(test_ids)

        def subset(match_ids: set[str]) -> EvidenceSnapshot:
            return EvidenceSnapshot(scope=snapshot.scope, matches=[item for item in snapshot.matches if item.match_id in match_ids], appearances=[item for item in snapshot.appearances if item.match_id in match_ids], events=[item for item in snapshot.events if item.match_id in match_ids], identities=[item for item in snapshot.identities if item.match_id in match_ids])

        report.train_match_ids, report.test_match_ids = list(train_ids), list(test_ids)
        report.train_through = max(item.match_date for item in snapshot.matches if item.match_id in train_set)
        report.test_from = min(item.match_date for item in snapshot.matches if item.match_id in test_set)
        test = subset(test_set)
        selected = _select(test)
        report.test_contests = len(selected.contests)
        report.excluded_count = len(test.events) - len(selected.duplicates)
        try:
            fitted = self.fit(subset(train_set), policy=policy)
        except RatingCapacityError as error:
            report.limitations.append(str(error))
            return report
        report.training_evidence_fingerprint = fitted.evidence_fingerprint
        report.training_contests = fitted.accepted_contests
        if not fitted.diagnostics.converged:
            report.limitations.append("Training fit unavailable or nonconverged; no held-out scores produced.")
            return report
        baseline = (1 + sum(contest.winner_id == contest.home_player_id for contest in fitted.contests)) / (2 + len(fitted.contests))
        report.baseline_home_win_probability = baseline
        probabilities: list[float] = []
        outcomes: list[int] = []
        direct_by_pair: dict[tuple[str, ...], list[ContestEvidence]] = defaultdict(list)
        for contest in fitted.contests:
            direct_by_pair[tuple(contest.player_ids)].append(contest)
        # Local to this immutable training fit, never cached across corrections.
        # One posterior lookup per oriented pair, not a quadratic event join.
        pair_predictions: dict[tuple[str, str], PairwisePrediction] = {}
        for contest in selected.contests:
            opponent = next(player for player in contest.player_ids if player != contest.home_player_id)
            key = (contest.home_player_id, opponent)
            if key not in pair_predictions:
                pair_predictions[key] = self._predict(fitted, player_id=contest.home_player_id, opponent_player_id=opponent, direct=direct_by_pair.get(tuple(contest.player_ids), []))
            prediction = pair_predictions[key]
            if prediction.win_probability is None:
                if "players_in_disconnected_components" in prediction.reasons:
                    report.disconnected_contests += 1
                else:
                    report.cold_start_contests += 1
                continue
            probabilities.append(prediction.win_probability)
            outcomes.append(int(contest.winner_id == contest.home_player_id))
            report.scoring_event_ids.append(contest.event_ids[0])
        report.sample_count = len(outcomes)
        report.excluded_count -= len(outcomes)
        if not outcomes:
            report.limitations.append("No held-out contests connect two identified players in the same training component.")
            return report

        def log_loss(probability: float, outcome: int) -> float:
            probability = min(1 - 1e-15, max(1e-15, probability))
            return -math.log(probability) if outcome else -math.log1p(-probability)

        report.status = "evaluated"
        report.model_brier = math.fsum((probability - outcome) ** 2 for probability, outcome in zip(probabilities, outcomes, strict=True)) / len(outcomes)
        report.baseline_brier = math.fsum((baseline - outcome) ** 2 for outcome in outcomes) / len(outcomes)
        report.model_log_loss = math.fsum(log_loss(probability, outcome) for probability, outcome in zip(probabilities, outcomes, strict=True)) / len(outcomes)
        report.baseline_log_loss = math.fsum(log_loss(baseline, outcome) for outcome in outcomes) / len(outcomes)
        return DuelEvaluationReport.model_validate(report.model_dump())


# Convenient concrete implementation name for integrators importing this module.
DuelRatingEngine = BradleyTerryEngine
