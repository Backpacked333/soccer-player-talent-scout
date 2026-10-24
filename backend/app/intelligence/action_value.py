"""Small, auditable spatial Markov estimator; no pretrained/default pitch grid.

Equations follow the xT construction documented by socceraction and Karun Singh.
This is an independently implemented, regularized variant, not socceraction/VAEP.
Only reviewed successful possession-retaining moves receive public differences.
See docs/ACTION_VALUE.md for the statistical assumptions and operator contract.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic import Field, ValidationError, model_validator

from .contracts import (
    ActionValueArtifact, ActionValuePolicy, ActionValueResult, CohortScope,
    Digest, EvaluationReport, EvidenceSnapshot, FitDiagnostics, Identifier,
    IntelligenceContract, ReviewedAction, canonical_fingerprint,
    chronological_match_split, conflicting_identity_ids,
)

MOVES = frozenset({"pass", "cross", "carry"})
ELIGIBLE = MOVES | {"shot"}
LIMITATIONS = [
    "Unvalidated research estimator; engineering support thresholds are not youth validation.",
    "Only spatial state: ignores pressure, body part, game state, teammates and opponents.",
    "Successful possession-retaining moves only; not shot quality, defensive value, turnover cost, VAEP or talent.",
    "Zero-reward terminal pseudo-count shrinks values; no calibrated uncertainty intervals are supplied.",
    "Continuation into unobserved cells is zero under this regularized fit, not proof of zero threat; sparse downstream support can depress fitted values.",
    "Completeness, ordinary-shot semantics and training rights are operator declarations, not independently verified.",
    "Age/cohort restrictions prevent pooling but do not establish fairness or future potential.",
]


class ActionValueUnavailable(ValueError):
    """An inspectable failed eligibility/convergence gate, never a zero estimate."""


class ScoredActionValue(ActionValueResult):
    """Additional drilldown fields; compatible with the shared result interface."""
    evidence_ids: list[Identifier] = Field(default_factory=list, max_length=1)
    start_cell: int | None = Field(default=None, ge=0, le=191)
    end_cell: int | None = Field(default=None, ge=0, le=191)
    start_cell_actions: int | None = Field(default=None, ge=0, le=10000)
    end_cell_actions: int | None = Field(default=None, ge=0, le=10000)
    min_cell_actions: int | None = Field(default=None, ge=1, le=10000)
    training_actions: int | None = Field(default=None, ge=1, le=10000)
    training_matches: int | None = Field(default=None, ge=1, le=1000)
    coordinate_provenance: str | None = None
    current_evidence_checked: bool = False
    limitations: list[str] = Field(default_factory=lambda: list(LIMITATIONS))


def _validated_snapshot(snapshot: EvidenceSnapshot) -> EvidenceSnapshot:
    # model_copy/update and mutable nested lists bypass normal Pydantic checks.
    result = EvidenceSnapshot.model_validate(snapshot.model_dump(mode="python"))
    if len({row.appearance_id for row in result.appearances}) != len(result.appearances):
        raise ActionValueUnavailable("Duplicate appearance IDs make source provenance ambiguous.")
    return result


def _validated_policy(policy: ActionValuePolicy | None) -> ActionValuePolicy:
    return ActionValuePolicy.model_validate((policy or ActionValuePolicy()).model_dump())


def subset_snapshot(snapshot: EvidenceSnapshot, match_ids: set[str]) -> EvidenceSnapshot:
    """Whole-match subset only; no action-row split and no hidden data sampling."""
    if not match_ids <= {m.match_id for m in snapshot.matches}:
        raise ActionValueUnavailable("Training matches are missing from the current authorized snapshot.")
    return EvidenceSnapshot(
        scope=snapshot.scope,
        matches=[m for m in snapshot.matches if m.match_id in match_ids],
        appearances=[a for a in snapshot.appearances if a.match_id in match_ids],
        events=[e for e in snapshot.events if e.match_id in match_ids],
        identities=[i for i in snapshot.identities if i.match_id in match_ids],
    )


def training_fingerprint(snapshot: EvidenceSnapshot, policy: ActionValuePolicy) -> str:
    """All correction-relevant source data, including rejected rows and identities."""
    payload = snapshot.model_dump(mode="json")
    for collection, key in (("matches", "match_id"), ("appearances", "appearance_id"), ("events", "event_id"), ("identities", "id")):
        payload[collection] = sorted(payload[collection], key=lambda row: row[key])
    return canonical_fingerprint({"snapshot": payload, "policy": policy.model_dump(mode="json")})


def artifact_fingerprint(artifact: ActionValueArtifact) -> str:
    """Stable model content ID, independent of wall-clock fit time/evaluation.

    A SHA-256 checksum is NOT a signature, permission check or anonymization.
    """
    return canonical_fingerprint(artifact.model_dump(mode="json", exclude={"trained_at", "evaluation"}))


def normalized_points(event: ReviewedAction) -> tuple[tuple[float, float], tuple[float, float] | None]:
    """Validate before binning; rotate BOTH pitch axes for right-to-left attack."""
    event = ReviewedAction.model_validate(event.model_dump())
    if event.coordinate_system == "unknown" or not event.coordinate_provenance:
        raise ActionValueUnavailable(f"{event.event_id}: qualified pitch-coordinate provenance is required.")
    if event.start_x is None or event.start_y is None:
        raise ActionValueUnavailable(f"{event.event_id}: starting pitch coordinates are unavailable.")
    start = (event.start_x, event.start_y)
    end = None if event.end_x is None or event.end_y is None else (event.end_x, event.end_y)
    if event.coordinate_system == "pitch_normalized" and event.attacking_direction == "right_to_left":
        start = (1 - start[0], 1 - start[1])
        if end is not None:
            end = (1 - end[0], 1 - end[1])
    return start, end


def cell_index(point: tuple[float, float], policy: ActionValuePolicy) -> int:
    """Row-major (y, x); [0,1] boundary points belong to the outermost cells."""
    x, y = point
    if not all(math.isfinite(value) and 0 <= value <= 1 for value in point):
        raise ActionValueUnavailable("Pitch coordinates must be finite and inside [0,1]; no clipping.")
    return min(int(y * policy.grid_rows), policy.grid_rows - 1) * policy.grid_columns + min(int(x * policy.grid_columns), policy.grid_columns - 1)


def _identity_problem(event: ReviewedAction, snapshot: EvidenceSnapshot, identities: dict[str, Any], conflicts: set[str]) -> str | None:
    if event.identity_assignment_id is None:
        return "Model candidates require a current reviewer-confirmed track identity." if event.provenance == "model_candidate" else None
    assignment = identities.get(event.identity_assignment_id)
    if assignment is None or assignment.id in conflicts or assignment.status != "confirmed" or assignment.revision != event.identity_assignment_revision:
        return "Linked identity is missing, unresolved, rejected or at a stale reviewed revision."
    if (assignment.workspace_id, assignment.mode, assignment.match_id, assignment.player_id) != (snapshot.scope.workspace_id, snapshot.scope.mode, event.match_id, event.player_id):
        return "Linked identity does not match the scoped canonical actor."
    if not assignment.start_seconds <= event.timestamp_seconds <= (event.end_timestamp_seconds if event.end_timestamp_seconds is not None else event.timestamp_seconds) <= assignment.end_seconds:
        return "Event interval falls outside its confirmed identity interval."
    return None


def _identity_conflicts(snapshot: EvidenceSnapshot) -> set[str]:
    conflicts = conflicting_identity_ids(snapshot.identities)
    durations = {row.match_id: row.duration_seconds for row in snapshot.matches}
    conflicts.update(row.id for row in snapshot.identities if row.end_seconds > durations[row.match_id])
    return conflicts


def _eligible_events(snapshot: EvidenceSnapshot, *, require_rights: bool) -> list[ReviewedAction]:
    """Fail the whole supplied stream rather than train on convenient successes."""
    if snapshot.scope.role != "all_roles":
        raise ActionValueUnavailable("Spatial possession dynamics require all_roles complete streams; role-filtered models are unsupported.")
    if not snapshot.matches:
        raise ActionValueUnavailable("No complete reviewed matches are available.")
    matches = {m.match_id: m for m in snapshot.matches}
    sides: dict[str, set[str]] = {m: set() for m in matches}
    for appearance in snapshot.appearances:
        sides[appearance.match_id].add(appearance.side)
    for match in snapshot.matches:
        declaration = match.stream
        if declaration.coverage != "complete_match" or not declaration.eligible_actions_complete or not declaration.both_sides_recorded:
            raise ActionValueUnavailable(f"{match.match_id}: training/evaluation requires an explicitly complete both-side eligible-action stream, not highlights.")
        if require_rights and not declaration.training_rights_cleared:
            raise ActionValueUnavailable(f"{match.match_id}: separate model-training permission is not cleared.")
        if sides[match.match_id] != {"home", "away"}:
            raise ActionValueUnavailable(f"{match.match_id}: both authorized rosters are required.")
    identities = {row.id: row for row in snapshot.identities}
    conflicts = _identity_conflicts(snapshot)
    seen: set[tuple[str, str, float]] = set()
    result: list[ReviewedAction] = []
    per_match: dict[str, int] = {m: 0 for m in matches}
    roster_sides = {(a.match_id, a.player_id): a.side for a in snapshot.appearances}
    observed_sides: dict[str, set[str]] = {m: set() for m in matches}
    directions: dict[tuple[str, int | None, str], str | None] = {}
    for event in sorted(snapshot.events, key=lambda e: (e.match_id, e.timestamp_seconds, e.event_id)):
        if event.kind not in ELIGIBLE or event.status == "rejected":
            continue
        if event.status != "approved":
            raise ActionValueUnavailable(f"{event.event_id}: eligible draft actions make the supplied stream incomplete.")
        if max(event.timestamp_seconds, event.end_timestamp_seconds or 0) > matches[event.match_id].duration_seconds:
            raise ActionValueUnavailable(f"{event.event_id}: action lies outside the declared match duration.")
        problem = _identity_problem(event, snapshot, identities, conflicts)
        if problem:
            raise ActionValueUnavailable(f"{event.event_id}: {problem}")
        # Same-actor same-instant ambiguity is not two independent observations.
        key = (event.match_id, event.player_id, event.timestamp_seconds)
        if key in seen:
            raise ActionValueUnavailable(f"{event.event_id}: duplicate/ambiguous eligible action at the same actor timestamp.")
        seen.add(key)
        _, end = normalized_points(event)
        side = roster_sides[(event.match_id, event.player_id)]
        observed_sides[event.match_id].add(side)
        if event.coordinate_system == "pitch_normalized":
            direction_key = (event.match_id, event.period, side)
            opposite_key = (event.match_id, event.period, "away" if side == "home" else "home")
            if direction_key in directions and directions[direction_key] != event.attacking_direction:
                raise ActionValueUnavailable(f"{event.event_id}: inconsistent pitch attacking directions for the same side/period.")
            if opposite_key in directions and directions[opposite_key] == event.attacking_direction:
                raise ActionValueUnavailable(f"{event.event_id}: opposite sides cannot attack in the same pitch direction in one period.")
            directions[direction_key] = event.attacking_direction
        if event.kind == "shot":
            if event.shot_outcome is None:
                raise ActionValueUnavailable(f"{event.event_id}: explicit goal/no_goal is required; successful shot is not a goal label.")
            if event.shot_outcome == "goal" and event.outcome != "successful":
                raise ActionValueUnavailable(f"{event.event_id}: an explicit goal contradicts an unsuccessful shot outcome.")
        else:
            if event.possession_retained is None:
                raise ActionValueUnavailable(f"{event.event_id}: every move attempt needs explicit possession retention.")
            if event.outcome == "unsuccessful" and event.possession_retained:
                raise ActionValueUnavailable(f"{event.event_id}: unsuccessful-but-retained move semantics are unsupported; resolve the annotation.")
            if event.outcome == "successful" and event.possession_retained and end is None:
                raise ActionValueUnavailable(f"{event.event_id}: retained move is missing destination pitch coordinates.")
        result.append(event)
        per_match[event.match_id] += 1
    if any(count == 0 for count in per_match.values()):
        raise ActionValueUnavailable("Each declared training/evaluation match must contain eligible reviewed actions.")
    if any(sides != {"home", "away"} for sides in observed_sides.values()):
        raise ActionValueUnavailable("Each declared complete match must include eligible observed actions from both sides.")
    return result


@dataclass
class _Counts:
    actions: list[int]
    shots: list[int]
    goals: list[int]
    transitions: list[dict[int, int]]


def _counts(events: list[ReviewedAction], policy: ActionValuePolicy) -> _Counts:
    cells = policy.grid_columns * policy.grid_rows
    counts = _Counts([0] * cells, [0] * cells, [0] * cells, [{} for _ in range(cells)])
    for event in events:
        start, end = normalized_points(event)
        source = cell_index(start, policy)
        counts.actions[source] += 1
        if event.kind == "shot":
            counts.shots[source] += 1
            counts.goals[source] += int(event.shot_outcome == "goal")
        elif event.outcome == "successful" and event.possession_retained:
            assert end is not None  # checked by complete-stream extraction
            target = cell_index(end, policy)
            counts.transitions[source][target] = counts.transitions[source].get(target, 0) + 1
    return counts


def _solve(counts: _Counts, policy: ActionValuePolicy) -> tuple[list[float], FitDiagnostics]:
    """V_i=(goals_i + sum_j successful_moves_ij V_j)/(actions_i+alpha).

    alpha>0 gives a substochastic contraction even for an all-move cycle.
    Iteration starts at zero, producing bounded nondecreasing state values.
    The stop criterion is the Bellman residual, NOT an empirical error bar.
    """
    denominator = [count + policy.terminal_prior_count for count in counts.actions]
    transition = [tuple(sorted(row.items())) for row in counts.transitions]
    contraction = max((sum(row.values()) / denominator[i] for i, row in enumerate(counts.transitions)), default=0.0)

    def step(values: list[float]) -> list[float]:
        return [(counts.goals[i] + math.fsum(count * values[j] for j, count in row)) / denominator[i] for i, row in enumerate(transition)]

    values = [0.0] * len(denominator)
    for iteration in range(1, policy.max_iterations + 1):
        values = step(values)
        next_values = step(values)
        residual = max(abs(a - b) for a, b in zip(values, next_values, strict=True))
        if residual <= policy.convergence_tolerance:
            bound = residual / (1 - contraction)
            return values, FitDiagnostics(converged=True, iterations=iteration, max_residual=residual, warnings=[
                f"Terminal zero-reward pseudo-count per cell: {policy.terminal_prior_count:g}.",
                f"Infinity-norm contraction bound: {contraction:.12g}; numerical solution error bound: {bound:.12g} (not statistical uncertainty).",
                "No calibrated sampling uncertainty; sparse cells are masked at inference, not imputed as zero.",
            ])
    raise ActionValueUnavailable(f"Bellman solver did not converge within {policy.max_iterations} iterations; no artifact was produced.")


def fit(snapshot: EvidenceSnapshot, *, policy: ActionValuePolicy | None = None) -> ActionValueArtifact:
    snapshot, policy = _validated_snapshot(snapshot), _validated_policy(policy)
    if len(snapshot.events) > policy.max_training_events:
        raise ActionValueUnavailable("Input exceeds the disclosed training-event work bound; no silent sampling.")
    events = _eligible_events(snapshot, require_rights=True)
    counts = _counts(events, policy)
    if len(snapshot.matches) < policy.min_training_matches or len(events) < policy.min_training_actions or sum(counts.shots) < policy.min_training_shots or sum(counts.goals) < policy.min_training_goals:
        raise ActionValueUnavailable(
            "Insufficient training support: observed matches/actions/explicit shots/goals "
            f"{len(snapshot.matches)}/{len(events)}/{sum(counts.shots)}/{sum(counts.goals)}; "
            f"policy requires {policy.min_training_matches}/{policy.min_training_actions}/{policy.min_training_shots}/{policy.min_training_goals}."
        )
    values, diagnostics = _solve(counts, policy)
    return ActionValueArtifact(
        scope=snapshot.scope, policy=policy, trained_at=datetime.now(timezone.utc),
        training_match_ids=sorted(m.match_id for m in snapshot.matches),
        training_fingerprint=training_fingerprint(snapshot, policy),
        training_action_count=len(events), training_shot_count=sum(counts.shots), training_goal_count=sum(counts.goals),
        rights_references=sorted({m.stream.rights_reference for m in snapshot.matches if m.stream.rights_reference is not None}),
        cell_values=values, cell_action_counts=counts.actions, diagnostics=diagnostics,
    )


def validate_artifact(artifact: ActionValueArtifact) -> ActionValueArtifact:
    """Revalidate mutable in-memory models as well as JSON-loaded artifacts."""
    result = ActionValueArtifact.model_validate(artifact.model_dump(mode="python"))
    if result.scope.role != "all_roles":
        raise ActionValueUnavailable("Role-filtered spatial artifacts are unsupported.")
    if result.diagnostics.max_residual is None or result.diagnostics.max_residual > result.policy.convergence_tolerance or not 1 <= result.diagnostics.iterations <= result.policy.max_iterations:
        raise ActionValueUnavailable("Artifact lacks a converged residual within the disclosed solver bounds.")
    if len(set(result.rights_references)) != len(result.rights_references):
        raise ActionValueUnavailable("Duplicate rights references in artifact provenance.")
    if any(count == 0 and value != 0 for count, value in zip(result.cell_action_counts, result.cell_values, strict=True)):
        raise ActionValueUnavailable("Unobserved cells cannot contain invented fitted values.")
    return result


def _unavailable(event_id: str, reason: str, **fields: Any) -> ScoredActionValue:
    return ScoredActionValue(event_id=event_id, status="unavailable", reasons=[reason], evidence_ids=[event_id], **fields)


def _score(event: ReviewedAction, *, scope: CohortScope, artifact: ActionValueArtifact | None, context_checked: bool) -> ScoredActionValue:
    if artifact is None:
        return _unavailable(event.event_id, "No authorized fitted spatial model is available.")
    try:
        event = ReviewedAction.model_validate(event.model_dump())
        scope = CohortScope.model_validate(scope.model_dump())
        artifact = validate_artifact(artifact)
    except ValueError:
        return _unavailable(event.event_id, "Invalid action, scope or artifact; finite validated inputs are required.")
    fields: dict[str, Any] = {"artifact_fingerprint": artifact_fingerprint(artifact), "coordinate_provenance": event.coordinate_provenance, "current_evidence_checked": context_checked}
    if scope != artifact.scope or (event.workspace_id, event.mode) != (scope.workspace_id, scope.mode):
        return _unavailable(event.event_id, "Artifact/action is incompatible with the authorized workspace, mode or cohort.", **fields)
    if event.provenance == "model_candidate" and event.identity_assignment_id is None:
        return _unavailable(event.event_id, "Model candidates require a current reviewer-confirmed track identity.", **fields)
    if not context_checked and event.identity_assignment_id is not None:
        return _unavailable(event.event_id, "Linked identity needs current-snapshot confirmation; use score_from_snapshot.", **fields)
    if event.status != "approved" or event.kind not in MOVES or event.outcome != "successful" or event.possession_retained is not True:
        return _unavailable(event.event_id, "Only approved successful explicitly possession-retaining passes, crosses and carries are scored.", **fields)
    try:
        start, end = normalized_points(event)
        if end is None:
            raise ActionValueUnavailable("Destination pitch coordinates are unavailable.")
        source, target = cell_index(start, artifact.policy), cell_index(end, artifact.policy)
    except ValueError as error:
        return _unavailable(event.event_id, str(error), **fields)
    fields.update(start_cell=source, end_cell=target, start_cell_actions=artifact.cell_action_counts[source], end_cell_actions=artifact.cell_action_counts[target], min_cell_actions=artifact.policy.min_cell_actions, training_actions=artifact.training_action_count, training_matches=len(artifact.training_match_ids))
    if min(artifact.cell_action_counts[source], artifact.cell_action_counts[target]) < artifact.policy.min_cell_actions:
        return _unavailable(event.event_id, "Both queried cells require the disclosed minimum observed starting-action support.", **fields)
    if not context_checked:
        fields["limitations"] = list(LIMITATIONS) + ["Low-level scoring: caller must authorize this event/cohort and verify the artifact against current training evidence; this call cannot inspect roster/history."]
    return ScoredActionValue(event_id=event.event_id, status="available", value=artifact.cell_values[target] - artifact.cell_values[source], evidence_ids=[event.event_id], **fields)


def score(event: ReviewedAction, *, scope: CohortScope, artifact: ActionValueArtifact | None) -> ScoredActionValue:
    """Protocol adapter for trusted server callers; NOT an authorization endpoint.

    Prefer score_from_snapshot for application use: it also detects source
    corrections, missing training matches and current identity revisions.
    """
    return _score(event, scope=scope, artifact=artifact, context_checked=False)


def score_from_snapshot(snapshot: EvidenceSnapshot, *, event_id: str, artifact: ActionValueArtifact | None) -> ScoredActionValue:
    """Fail closed on corrections/deletions/reattribution until an explicit refit.

    The snapshot must include every original training match plus the queried
    match, read atomically by the authorized service. New matches do not silently
    retrain this model, and unavailable is not replaced with a prior-only score.
    """
    try:
        snapshot = _validated_snapshot(snapshot)
        event = next((e for e in snapshot.events if e.event_id == event_id), None)
        if event is None:
            return _unavailable(event_id, "Event is absent from the current authorized snapshot.")
        if artifact is None:
            return _unavailable(event_id, "No authorized fitted spatial model is available.")
        artifact = validate_artifact(artifact)
        if snapshot.scope != artifact.scope:
            return _unavailable(event_id, "Artifact and current snapshot have incompatible scopes.")
        current_training = subset_snapshot(snapshot, set(artifact.training_match_ids))
        if training_fingerprint(current_training, artifact.policy) != artifact.training_fingerprint:
            return _unavailable(event_id, "Training evidence/rights/identity changed; the artifact is stale and must be explicitly refitted.")
        # Fingerprint compatibility alone cannot grandfather an older artifact
        # through stricter source gates (e.g. unidentified legacy candidates).
        _eligible_events(current_training, require_rights=True)
        problem = _identity_problem(event, snapshot, {row.id: row for row in snapshot.identities}, _identity_conflicts(snapshot))
        if problem:
            return _unavailable(event_id, problem)
        match = next(m for m in snapshot.matches if m.match_id == event.match_id)
        if max(event.timestamp_seconds, event.end_timestamp_seconds or 0) > match.duration_seconds:
            return _unavailable(event_id, "Event is outside the match duration.")
        return _score(event, scope=snapshot.scope, artifact=artifact, context_checked=True)
    except (ValueError, StopIteration):
        return _unavailable(event_id, "Invalid/incomplete authorized snapshot or artifact; cannot verify current training evidence.")


def _loss(probability: float, label: int) -> float:
    # Reporting-only numerical guard, explicitly disclosed in evaluation.
    bounded = min(1 - 1e-12, max(1e-12, probability))
    return -math.log(bounded if label else 1 - bounded)


def evaluate(snapshot: EvidenceSnapshot, *, policy: ActionValuePolicy | None = None, test_fraction: float = 0.25) -> EvaluationReport:
    """Evaluate ONLY shot-cell goal frequency, not long-horizon xT calibration.

    The contracts contain no possession IDs/ending labels. We do not invent them.
    This diagnostic uses a train-only goal/shot estimator with the same terminal
    shrinkage, and a train-only global goal/shot baseline on identical test rows.
    """
    snapshot, policy = _validated_snapshot(snapshot), _validated_policy(policy)
    fields: dict[str, Any] = dict(
        mode=snapshot.scope.mode,
        target="Explicit goal/no_goal on held-out ordinary shots: spatial shot-cell conversion component only, NOT long-horizon xT calibration.",
        baseline_description="Train-only global goals / (shots + terminal_prior_count); same zero-goal pseudo-count as each local shot cell.",
        limitations=list(LIMITATIONS) + [
            "Whole matches and whole dates held out; no tuning on the test set. Evaluation does not promote unvalidated_research status.",
            "Only training-supported shot cells are evaluated; selection coverage is disclosed by sample_count/excluded_count.",
            "Brier uses unmodified probabilities; log loss clips only for numerical reporting to [1e-12, 1-1e-12].",
            "No possession-ending labels: these scores do not validate Markov continuation, move differences or individual player ability.",
        ],
    )
    try:
        train_ids, test_ids = chronological_match_split(snapshot.matches, test_fraction=test_fraction)
        fields.update(train_match_ids=list(train_ids), test_match_ids=list(test_ids), train_through=max(m.match_date for m in snapshot.matches if m.match_id in train_ids), test_from=min(m.match_date for m in snapshot.matches if m.match_id in test_ids))
        train = subset_snapshot(snapshot, set(train_ids))
        test = subset_snapshot(snapshot, set(test_ids))
        fit(train, policy=policy)  # Eligibility/support/convergence cannot be bypassed by the diagnostic.
        train_events = _eligible_events(train, require_rights=True)
        test_events = _eligible_events(test, require_rights=True)
        counts = _counts(train_events, policy)
        baseline = sum(counts.goals) / (sum(counts.shots) + policy.terminal_prior_count)
        probabilities: list[float] = []
        labels: list[int] = []
        excluded = 0
        for event in test_events:
            if event.kind != "shot":
                continue
            start, _ = normalized_points(event)
            cell = cell_index(start, policy)
            if counts.shots[cell] < policy.min_cell_actions:
                excluded += 1
                continue
            probabilities.append(counts.goals[cell] / (counts.shots[cell] + policy.terminal_prior_count))
            labels.append(int(event.shot_outcome == "goal"))
        fields.update(sample_count=len(labels), excluded_count=excluded)
        if not labels:
            raise ActionValueUnavailable("No held-out shots have sufficient train-only shot-cell support.")
        return EvaluationReport(status="evaluated", **fields,
            model_brier=math.fsum((p - y) ** 2 for p, y in zip(probabilities, labels, strict=True)) / len(labels),
            baseline_brier=math.fsum((baseline - y) ** 2 for y in labels) / len(labels),
            model_log_loss=math.fsum(_loss(p, y) for p, y in zip(probabilities, labels, strict=True)) / len(labels),
            baseline_log_loss=math.fsum(_loss(baseline, y) for y in labels) / len(labels))
    except ValueError as error:
        fields["limitations"].append(str(error))
        return EvaluationReport(status="unavailable", **fields)


class ArtifactDocument(IntelligenceContract):
    """Checksummed JSON envelope. Integrity is not authenticity or authorization."""
    format: str = Field(default="touchline-xt-document-v1", pattern=r"^touchline-xt-document-v1$")
    content_sha256: Digest
    artifact: ActionValueArtifact

    @model_validator(mode="after")
    def validate_content(self) -> ArtifactDocument:
        validate_artifact(self.artifact)
        if canonical_fingerprint(self.artifact) != self.content_sha256:
            raise ValueError("artifact content checksum mismatch")
        return self


def artifact_document(artifact: ActionValueArtifact) -> ArtifactDocument:
    artifact = validate_artifact(artifact)
    return ArtifactDocument(content_sha256=canonical_fingerprint(artifact), artifact=artifact)


def load_artifact_json(data: str | bytes, *, authorized_scope: CohortScope) -> ActionValueArtifact:
    """Bound before parsing; scope is supplied by an already authorized caller.

    This function performs no filesystem/network access. A request must never
    choose a server filesystem path or treat a checksum as an approval signature.
    """
    if (len(data) if isinstance(data, bytes) else len(data.encode("utf-8"))) > 8 * 1024 * 1024:
        raise ActionValueUnavailable("Artifact exceeds the 8 MiB JSON import limit.")
    try:
        document = ArtifactDocument.model_validate_json(data)
    except ValidationError as error:
        raise ActionValueUnavailable("Invalid versioned, finite, checksummed artifact JSON.") from error
    if document.artifact.scope != authorized_scope:
        raise ActionValueUnavailable("Imported artifact does not match the authorized workspace/mode/cohort.")
    return document.artifact


class SpatialMarkovEngine:
    """Concrete implementation of the shared ActionValueEngine protocol."""
    fit = staticmethod(fit)
    score = staticmethod(score)
    evaluate = staticmethod(evaluate)
    score_from_snapshot = staticmethod(score_from_snapshot)
