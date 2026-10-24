"""Versioned, JSON-only boundaries for the three independent evidence engines.

This module contains validation, not fitted models. No observation, prior or
unreviewed track is evidence of a young person's latent talent. Engine owners
may add specialized result fields in their own modules; these shared inputs are
the integration boundary. Missing semantics deliberately remain unavailable.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "touchline-intelligence-v1"
Role = Literal["goalkeeper", "defender", "midfielder", "forward", "unknown"]
Mode = Literal["real", "demo"]
Side = Literal["home", "away"]
Identifier = Annotated[str, Field(min_length=1, max_length=120)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Probability = Annotated[float, Field(ge=0, le=1)]
Seconds = Annotated[float, Field(ge=0, le=14400)]
CoordinateSystem = Literal["unknown", "pitch_normalized", "attack_normalized"]
AttackDirection = Literal["left_to_right", "right_to_left"]


class IntelligenceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True, validate_default=True, from_attributes=True)


class CohortScope(IntelligenceContract):
    """No implicit cross-workspace, mode, season or competition pooling.

    all_roles is an explicitly labelled mixed-role analysis, not a role-adjusted
    model. A role-specific fit includes only contests with both actors in scope.
    """
    workspace_id: Identifier
    mode: Mode
    season: str = Field(min_length=1, max_length=40)
    age_group: str = Field(min_length=2, max_length=20)
    competition: str = Field(min_length=1, max_length=100)
    role: Role | Literal["all_roles"]


class EventSemantics(IntelligenceContract):
    """Additive event semantics; legacy coordinates remain unqualified.

    pitch_normalized is a normalized *pitch*, never an image bounding box.
    For right_to_left normalization rotate both axes by 180 degrees. The
    coordinate reference must describe a reviewed manual pitch mark or a
    validated calibration. No pitch dimensions, meters or speed are implied.
    """
    coordinate_system: CoordinateSystem = "unknown"
    coordinate_provenance: str | None = Field(default=None, min_length=1, max_length=500)
    period: int | None = Field(default=None, ge=1, le=10)
    attacking_direction: AttackDirection | None = None
    possession_retained: bool | None = Field(default=None, strict=True)
    shot_outcome: Literal["goal", "no_goal"] | None = None
    contest_id: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")

    @model_validator(mode="after")
    def validate_semantics(self) -> EventSemantics:
        if self.coordinate_system != "unknown" and not self.coordinate_provenance:
            raise ValueError("qualified pitch coordinates require normalization provenance")
        if self.coordinate_system == "pitch_normalized" and (self.period is None or self.attacking_direction is None):
            raise ValueError("pitch coordinates require explicit period and attack direction")
        if self.coordinate_system == "attack_normalized" and self.attacking_direction == "right_to_left":
            raise ValueError("attack-normalized coordinates already point left to right")
        return self


class EventEvidenceFields(EventSemantics):
    """Internal reviewed identity linkage; never trust an arbitrary request ID."""
    identity_assignment_id: str | None = Field(default=None, min_length=1, max_length=36)
    identity_assignment_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_identity_link(self) -> EventEvidenceFields:
        if (self.identity_assignment_id is None) != (self.identity_assignment_revision is None):
            raise ValueError("identity assignment ID and reviewed revision must be supplied together")
        return self


class StreamDeclaration(IntelligenceContract):
    """Explicit annotator declaration, not independently verified completeness.

    Complete means ALL eligible passes/crosses/carries/ordinary shots, including
    failed moves and non-goal shots, on both sides for the declared full match.
    Processing permission is NOT model-training permission.
    """
    coverage: Literal["unknown", "selected_highlights", "complete_match"] = "unknown"
    eligible_actions_complete: bool = Field(default=False, strict=True)
    both_sides_recorded: bool = Field(default=False, strict=True)
    training_rights_cleared: bool = Field(default=False, strict=True)
    rights_reference: str | None = Field(default=None, min_length=1, max_length=500)
    declared_by: Identifier | None = None
    declared_at: datetime | None = None
    source_reference: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_declaration(self) -> StreamDeclaration:
        if self.training_rights_cleared and not self.rights_reference:
            raise ValueError("training permission needs a rights reference")
        if self.coverage == "complete_match":
            if not self.eligible_actions_complete or not self.both_sides_recorded:
                raise ValueError("complete streams include failed actions and both sides")
            if not self.declared_by or self.declared_at is None or not self.source_reference:
                raise ValueError("complete streams require auditable declaration and source")
        return self


class EvidenceMatch(IntelligenceContract):
    match_id: Identifier
    workspace_id: Identifier
    mode: Mode
    match_date: date
    season: str = Field(min_length=1, max_length=40)
    age_group: str = Field(min_length=2, max_length=20)
    competition: str = Field(min_length=1, max_length=100)
    duration_seconds: float = Field(gt=0, le=14400)
    stream: StreamDeclaration = Field(default_factory=StreamDeclaration)


class EvidenceAppearance(IntelligenceContract):
    appearance_id: Identifier
    match_id: Identifier
    player_id: Identifier
    workspace_id: Identifier
    mode: Mode
    side: Side
    role: Role
    jersey_number: int | None = Field(default=None, ge=0, le=99)
    played_seconds: Seconds = 0
    reviewed_seconds: Seconds = 0
    roster_authorized: bool = Field(strict=True)

    @model_validator(mode="after")
    def validate_exposure(self) -> EvidenceAppearance:
        if self.reviewed_seconds > self.played_seconds:
            raise ValueError("reviewed exposure cannot exceed played exposure")
        return self


class ReviewedAction(EventEvidenceFields):
    event_id: Identifier
    workspace_id: Identifier
    mode: Mode
    match_id: Identifier
    player_id: Identifier
    opponent_player_id: Identifier | None = None
    kind: Literal["pass", "cross", "shot", "tackle", "interception", "recovery", "carry", "duel", "block", "header"]
    outcome: Literal["successful", "unsuccessful", "unknown"]
    timestamp_seconds: Seconds
    end_timestamp_seconds: Seconds | None = None
    start_x: Probability | None = None
    start_y: Probability | None = None
    end_x: Probability | None = None
    end_y: Probability | None = None
    status: Literal["draft", "approved", "rejected"]
    reviewed_by: Identifier | None = None
    reviewed_at: datetime | None = None
    provenance: Literal["manual", "model_candidate", "synthetic"]
    updated_at: datetime

    @model_validator(mode="after")
    def validate_action(self) -> ReviewedAction:
        if self.player_id == self.opponent_player_id:
            raise ValueError("opponent must be distinct from actor")
        if self.end_timestamp_seconds is not None and self.end_timestamp_seconds < self.timestamp_seconds:
            raise ValueError("event end precedes start")
        if (self.start_x is None) != (self.start_y is None) or (self.end_x is None) != (self.end_y is None):
            raise ValueError("coordinates must be paired")
        if self.shot_outcome is not None and self.kind != "shot":
            raise ValueError("only explicit shots have shot outcomes")
        if self.contest_id is not None and self.kind != "duel":
            raise ValueError("contest identity is reserved for explicit duels")
        if self.status == "approved" and (self.outcome == "unknown" or self.reviewed_by is None or self.reviewed_at is None):
            raise ValueError("approved actions require reviewed, known outcomes")
        if self.mode == "real" and self.provenance == "synthetic":
            raise ValueError("synthetic evidence cannot enter real records")
        return self


class EvidenceSnapshot(IntelligenceContract):
    """Bounded, coherent input read in ONE database snapshot by the integrator.

    Engines re-check actor membership/opposite sides, review status, assignment
    revision and cohort; validity here does not grant authorization. Reads must
    use repeatable-read (or a serialized SQLite read transaction), not separate
    committed queries. No derived history is appended or incrementally cached.
    """
    schema_version: Literal["touchline-intelligence-v1"] = "touchline-intelligence-v1"
    scope: CohortScope
    matches: list[EvidenceMatch] = Field(default_factory=list, max_length=1000)
    appearances: list[EvidenceAppearance] = Field(default_factory=list, max_length=10000)
    events: list[ReviewedAction] = Field(default_factory=list, max_length=10000)
    identities: list[TrackIdentityRecord] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def validate_scope_and_links(self) -> EvidenceSnapshot:
        matches = {item.match_id: item for item in self.matches}
        if len(matches) != len(self.matches):
            raise ValueError("duplicate match IDs")
        for match in self.matches:
            if (match.workspace_id, match.mode, match.season, match.age_group, match.competition) != (self.scope.workspace_id, self.scope.mode, self.scope.season, self.scope.age_group, self.scope.competition):
                raise ValueError("match crosses evidence scope")
        roster: dict[tuple[str, str], EvidenceAppearance] = {}
        for row in self.appearances:
            if row.workspace_id != self.scope.workspace_id or row.mode != self.scope.mode or row.match_id not in matches:
                raise ValueError("appearance crosses evidence scope")
            if not row.roster_authorized:
                raise ValueError("unauthorized roster evidence")
            key = (row.match_id, row.player_id)
            if key in roster:
                raise ValueError("duplicate canonical player within a roster")
            if row.played_seconds > matches[row.match_id].duration_seconds:
                raise ValueError("appearance exceeds match duration")
            roster[key] = row
        event_ids: set[str] = set()
        for event in self.events:
            if event.event_id in event_ids:
                raise ValueError("duplicate event IDs")
            event_ids.add(event.event_id)
            if event.workspace_id != self.scope.workspace_id or event.mode != self.scope.mode or event.match_id not in matches:
                raise ValueError("event crosses evidence scope")
            actor = roster.get((event.match_id, event.player_id))
            if actor is None:
                raise ValueError("event actor is not on authorized roster")
            if event.opponent_player_id is not None:
                opponent = roster.get((event.match_id, event.opponent_player_id))
                if opponent is None or opponent.side == actor.side:
                    raise ValueError("event opponent must be an authorized opposite-side actor")
        identity_ids: set[str] = set()
        for identity in self.identities:
            if identity.id in identity_ids:
                raise ValueError("duplicate identity IDs")
            identity_ids.add(identity.id)
            if identity.workspace_id != self.scope.workspace_id or identity.mode != self.scope.mode or identity.match_id not in matches:
                raise ValueError("identity crosses evidence scope")
            if (identity.match_id, identity.player_id) not in roster:
                raise ValueError("identity target is not on authorized roster")
        return self


class ActionValuePolicy(IntelligenceContract):
    method_version: Literal["spatial-markov-v1"] = "spatial-markov-v1"
    grid_columns: int = Field(default=8, ge=2, le=16)
    grid_rows: int = Field(default=6, ge=2, le=12)
    min_training_matches: int = Field(default=3, ge=2, le=1000)
    min_training_actions: int = Field(default=200, ge=10, le=10000)
    min_training_shots: int = Field(default=20, ge=1, le=10000)
    min_training_goals: int = Field(default=3, ge=1, le=10000)
    min_cell_actions: int = Field(default=5, ge=1, le=10000)
    max_iterations: int = Field(default=1000, ge=1, le=2000)
    convergence_tolerance: float = Field(default=1e-10, gt=0, le=1e-3)
    # Zero-reward terminal pseudo-count makes the fitted chain contractive. This
    # is disclosed regularization, NOT a learned youth league baseline.
    terminal_prior_count: float = Field(default=1, ge=0.01, le=100)
    max_training_events: int = Field(default=10000, ge=10, le=10000)


class FitDiagnostics(IntelligenceContract):
    converged: bool
    iterations: int = Field(ge=0, le=10000)
    objective: float | None = None
    max_residual: float | None = Field(default=None, ge=0)
    warnings: list[str] = Field(default_factory=list, max_length=100)


class EvaluationReport(IntelligenceContract):
    """Held-out scores are descriptive; they are not calibrated intervals."""
    status: Literal["unavailable", "evaluated"]
    mode: Mode
    split_method: Literal["grouped_chronological_match_dates"] = "grouped_chronological_match_dates"
    train_match_ids: list[Identifier] = Field(default_factory=list, max_length=1000)
    test_match_ids: list[Identifier] = Field(default_factory=list, max_length=1000)
    train_through: date | None = None
    test_from: date | None = None
    target: str = Field(min_length=1, max_length=300)
    sample_count: int = Field(default=0, ge=0, le=10000)
    excluded_count: int = Field(default=0, ge=0, le=10000)
    model_brier: float | None = Field(default=None, ge=0, le=1)
    baseline_brier: float | None = Field(default=None, ge=0, le=1)
    model_log_loss: float | None = Field(default=None, ge=0)
    baseline_log_loss: float | None = Field(default=None, ge=0)
    baseline_description: str = Field(min_length=1, max_length=500)
    limitations: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_holdout(self) -> EvaluationReport:
        if len(set(self.train_match_ids)) != len(self.train_match_ids) or len(set(self.test_match_ids)) != len(self.test_match_ids):
            raise ValueError("evaluation match IDs must be unique")
        if set(self.train_match_ids) & set(self.test_match_ids):
            raise ValueError("same match cannot train and evaluate a model")
        if self.status == "evaluated":
            if not self.train_match_ids or not self.test_match_ids or self.train_through is None or self.test_from is None or self.train_through >= self.test_from or not self.sample_count:
                raise ValueError("evaluation needs nonempty chronological whole-match holdouts")
            if self.model_brier is None or self.baseline_brier is None:
                raise ValueError("evaluation requires model and baseline Brier scores")
        elif any(value is not None for value in (self.model_brier, self.baseline_brier, self.model_log_loss, self.baseline_log_loss)):
            raise ValueError("unavailable evaluation cannot carry numeric performance claims")
        return self


class ActionValueArtifact(IntelligenceContract):
    """JSON only, bounded grid, no executable serialization or shipped weights.

    Values are internal fitted cell states; public scoring also requires observed
    support in BOTH cells. Unsupported cell states must not be displayed as zero.
    """
    schema_version: Literal["touchline-xt-artifact-v1"] = "touchline-xt-artifact-v1"
    method_version: Literal["spatial-markov-v1"] = "spatial-markov-v1"
    scope: CohortScope
    policy: ActionValuePolicy
    trained_at: datetime
    training_match_ids: list[Identifier] = Field(min_length=1, max_length=1000)
    training_fingerprint: Digest
    training_action_count: int = Field(ge=1, le=10000)
    training_shot_count: int = Field(ge=1, le=10000)
    training_goal_count: int = Field(ge=1, le=10000)
    rights_references: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(min_length=1, max_length=1000)
    coordinate_convention: Literal["attack_normalized_0_1_both_axes_rotate_180"] = "attack_normalized_0_1_both_axes_rotate_180"
    cell_values: list[Probability] = Field(min_length=4, max_length=192)
    cell_action_counts: list[int] = Field(min_length=4, max_length=192)
    diagnostics: FitDiagnostics
    evaluation: EvaluationReport | None = None
    validation_status: Literal["unvalidated_research"] = "unvalidated_research"

    @model_validator(mode="after")
    def validate_grid(self) -> ActionValueArtifact:
        cells = self.policy.grid_columns * self.policy.grid_rows
        if len(self.cell_values) != cells or len(self.cell_action_counts) != cells:
            raise ValueError("artifact grid shape differs from policy")
        if any(count < 0 for count in self.cell_action_counts) or sum(self.cell_action_counts) != self.training_action_count:
            raise ValueError("cell support must be nonnegative and account for training actions")
        if not self.training_goal_count <= self.training_shot_count <= self.training_action_count:
            raise ValueError("goals must be an explicit subset of shots and actions")
        if len(set(self.training_match_ids)) != len(self.training_match_ids):
            raise ValueError("training match IDs must be unique")
        if len(self.training_match_ids) < self.policy.min_training_matches or not self.policy.min_training_actions <= self.training_action_count <= self.policy.max_training_events or self.training_shot_count < self.policy.min_training_shots or self.training_goal_count < self.policy.min_training_goals:
            raise ValueError("artifact training support does not satisfy its disclosed policy")
        if not self.diagnostics.converged:
            raise ValueError("nonconverged fits must not become scoring artifacts")
        if self.evaluation is not None:
            if self.evaluation.mode != self.scope.mode:
                raise ValueError("evaluation crosses real/demo mode")
            if set(self.evaluation.test_match_ids) & set(self.training_match_ids):
                raise ValueError("artifact must never train on evaluation matches")
            if set(self.evaluation.train_match_ids) != set(self.training_match_ids):
                raise ValueError("evaluation must describe this artifact's training set")
        return self


class ActionValueResult(IntelligenceContract):
    event_id: Identifier
    status: Literal["unavailable", "available"]
    value: float | None = Field(default=None, ge=-1, le=1)
    method_version: str = "spatial-markov-v1"
    artifact_fingerprint: Digest | None = None
    reasons: list[str] = Field(default_factory=list)
    interpretation: str = "Provisional spatial Markov difference for an explicitly successful possession-retaining move; not xG, VAEP or talent."

    @model_validator(mode="after")
    def validate_availability(self) -> ActionValueResult:
        if self.status == "unavailable" and self.value is not None:
            raise ValueError("unavailable model outputs must be null")
        if self.status == "available" and (self.value is None or self.artifact_fingerprint is None):
            raise ValueError("available model value needs fitted artifact provenance")
        return self


class DuelRatingPolicy(IntelligenceContract):
    method_version: Literal["regularized-bradley-terry-v1"] = "regularized-bradley-terry-v1"
    prior_standard_deviation: float = Field(default=1.5, ge=0.1, le=5)
    min_contests: int = Field(default=20, ge=1, le=10000)
    min_matches: int = Field(default=3, ge=2, le=1000)
    min_distinct_opponents: int = Field(default=3, ge=1, le=127)
    max_match_share: float = Field(default=0.6, gt=0, le=1)
    max_players: int = Field(default=128, ge=2, le=128)
    max_contests: int = Field(default=10000, ge=1, le=10000)
    max_iterations: int = Field(default=100, ge=1, le=200)
    convergence_tolerance: float = Field(default=1e-8, gt=0, le=1e-3)
    credible_mass: float = Field(default=0.95, ge=0.95, le=0.95)


class DuelRatingEstimate(IntelligenceContract):
    player_id: Identifier
    component_id: str | None = None
    contests: int = Field(ge=0)
    matches: int = Field(ge=0)
    distinct_opponents: int = Field(ge=0)
    log_odds: float | None = None
    posterior_sd: float | None = Field(default=None, gt=0)
    interval_low: float | None = None
    interval_high: float | None = None
    component_rank: int | None = Field(default=None, ge=1)
    eligibility: Literal["unavailable", "provisional", "eligible"]
    reasons: list[str] = Field(default_factory=list)
    uncertainty_method: Literal["laplace_independent_contests_not_match_calibrated"] = "laplace_independent_contests_not_match_calibrated"

    @model_validator(mode="after")
    def validate_rating(self) -> DuelRatingEstimate:
        numeric = (self.log_odds, self.posterior_sd, self.interval_low, self.interval_high, self.component_rank)
        if (not self.contests or self.eligibility == "unavailable") and any(value is not None for value in numeric):
            raise ValueError("unobserved or unavailable players cannot receive numeric ratings")
        if self.log_odds is None and any(value is not None for value in numeric[1:]):
            raise ValueError("uncertainty and ranks require an actual fitted estimate")
        if self.component_rank is not None and (self.component_id is None or self.eligibility != "eligible"):
            raise ValueError("ranks require eligibility and a connected comparison component")
        if self.log_odds is not None and (self.component_id is None or self.posterior_sd is None or self.interval_low is None or self.interval_high is None):
            raise ValueError("numeric duel estimates require component and approximate uncertainty")
        if self.log_odds is not None and self.interval_low is not None and self.interval_high is not None and not self.interval_low <= self.log_odds <= self.interval_high:
            raise ValueError("rating interval must contain the estimate")
        return self


class DuelRatingReport(IntelligenceContract):
    scope: CohortScope
    policy: DuelRatingPolicy
    evidence_fingerprint: Digest
    ratings: list[DuelRatingEstimate] = Field(default_factory=list, max_length=128)
    diagnostics: FitDiagnostics
    accepted_contests: int = Field(ge=0, le=10000)
    excluded_event_ids: list[Identifier] = Field(default_factory=list, max_length=10000)
    duplicate_event_ids: list[Identifier] = Field(default_factory=list, max_length=10000)
    evaluation: EvaluationReport | None = None
    interpretation: str = "Selected reviewed direct duels only; component-specific opponent adjustment, not universal ability. Gaussian-prior MAP with Laplace uncertainty assumes independent contests and is not match-cluster calibrated."

    @model_validator(mode="after")
    def validate_convergence(self) -> DuelRatingReport:
        if not self.diagnostics.converged and any(row.log_odds is not None or row.component_rank is not None for row in self.ratings):
            raise ValueError("nonconverged ratings cannot expose numeric estimates or ranks")
        if len(self.ratings) > self.policy.max_players or self.accepted_contests > self.policy.max_contests:
            raise ValueError("rating output exceeds its disclosed work bounds")
        if len({row.player_id for row in self.ratings}) != len(self.ratings):
            raise ValueError("one canonical player may have only one estimate in a cohort")
        for row in self.ratings:
            if row.contests > self.accepted_contests or row.matches > row.contests or row.distinct_opponents > row.contests:
                raise ValueError("rating evidence counts are inconsistent")
            if row.eligibility == "eligible" and (row.log_odds is None or row.contests < self.policy.min_contests or row.matches < self.policy.min_matches or row.distinct_opponents < self.policy.min_distinct_opponents):
                raise ValueError("eligible ratings must meet their disclosed sample requirements")
        if self.evaluation is not None and self.evaluation.mode != self.scope.mode:
            raise ValueError("duel evaluation crosses real/demo mode")
        return self


class TrackIdentityWrite(IntelligenceContract):
    """A proposal never assigns an approved event. Ambiguity stays unresolved.

    Chunked runs use chunk:<chunk UUID>:<local track ID> in track_id. Identity
    services must verify that exact scoped observation and absolute interval;
    reviewer decisions cannot be erased by replay of a completed chunk.
    """
    upload_id: Identifier
    track_id: str = Field(min_length=1, max_length=80)
    start_seconds: Seconds
    end_seconds: Seconds
    player_id: Identifier
    status: Literal["proposed", "confirmed", "rejected"] = "proposed"
    source: Literal["reviewer", "model_proposal"] = "reviewer"
    confidence: Probability | None = None
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_interval(self) -> TrackIdentityWrite:
        if self.end_seconds < self.start_seconds:
            raise ValueError("identity interval end precedes start")
        if self.track_id.startswith("chunk:") and not re.fullmatch(r"chunk:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}:[A-Za-z0-9_.-]{1,37}", self.track_id):
            raise ValueError("chunk-scoped identities require the canonical chunk UUID and local track ID")
        return self


class TrackIdentityRecord(TrackIdentityWrite):
    id: Identifier
    workspace_id: Identifier
    mode: Mode
    match_id: Identifier
    revision: int = Field(ge=1)
    created_by: Identifier
    created_at: datetime
    reviewed_by: Identifier | None = None
    reviewed_at: datetime | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def validate_review(self) -> TrackIdentityRecord:
        if self.status in {"confirmed", "rejected"} and (self.reviewed_by is None or self.reviewed_at is None):
            raise ValueError("identity decisions require an auditable reviewer")
        return self


def conflicting_identity_ids(identities: list[TrackIdentityRecord]) -> set[str]:
    """Conservative O(n log n) closed-interval sweep, shared by all engines.

    One track cannot be two players, nor one player two tracks, at the same
    instant in the same source. API writes prevent this; legacy/imported
    snapshots must also fail closed rather than choosing an arbitrary identity.
    """
    groups: dict[tuple[str, str, str, str], list[TrackIdentityRecord]] = defaultdict(list)
    for row in identities:
        if row.status == "confirmed":
            groups[(row.match_id, row.upload_id, "track", row.track_id)].append(row)
            groups[(row.match_id, row.upload_id, "player", row.player_id)].append(row)
    conflicts: set[str] = set()
    for rows in groups.values():
        maximum: TrackIdentityRecord | None = None
        for row in sorted(rows, key=lambda item: (item.start_seconds, item.end_seconds, item.id)):
            if maximum is not None and row.start_seconds <= maximum.end_seconds:
                conflicts.update((maximum.id, row.id))
            if maximum is None or row.end_seconds > maximum.end_seconds:
                maximum = row
    return conflicts


class TrackIdentityCorrection(IntelligenceContract):
    expected_revision: int = Field(ge=1)
    player_id: Identifier
    status: Literal["proposed", "confirmed", "rejected"]
    reason: str = Field(min_length=1, max_length=1000)


class RelationshipEvidence(IntelligenceContract):
    player_id: Identifier
    other_player_id: Identifier
    relation: Literal["co_rostered_teammate", "co_rostered_opponent", "direct_duel"]
    match_ids: list[Identifier] = Field(min_length=1, max_length=1000)
    event_ids: list[Identifier] = Field(default_factory=list, max_length=10000)
    first_match_date: date
    last_match_date: date
    interpretation: str = "Shared roster membership does not prove on-pitch overlap, tactical chemistry or friendship."

    @model_validator(mode="after")
    def validate_relation(self) -> RelationshipEvidence:
        if self.player_id == self.other_player_id:
            raise ValueError("relationships require two distinct canonical identities")
        if self.first_match_date > self.last_match_date:
            raise ValueError("relationship dates must be chronological")
        if self.relation == "direct_duel" and not self.event_ids:
            raise ValueError("direct duels require reviewed event evidence")
        return self


class LongitudinalPoint(IntelligenceContract):
    match_id: Identifier
    match_date: date
    appearance_role: Role
    age_group: str
    approved_event_ids: list[Identifier] = Field(default_factory=list, max_length=10000)
    cumulative_reviewed_events: int = Field(ge=0)
    cumulative_matches: int = Field(ge=0)
    cumulative_pass_attempts: int = Field(ge=0)
    cumulative_pass_successes: int = Field(ge=0)
    cumulative_played_seconds: float = Field(ge=0)
    cumulative_reviewed_seconds: float = Field(ge=0)
    observed_pass_completion: Probability | None = None
    coverage_fraction: Probability | None = None

    @model_validator(mode="after")
    def validate_denominators(self) -> LongitudinalPoint:
        if self.cumulative_pass_successes > self.cumulative_pass_attempts or self.cumulative_pass_attempts > self.cumulative_reviewed_events:
            raise ValueError("pass successes/attempts must be supported by reviewed events")
        if self.cumulative_reviewed_seconds > self.cumulative_played_seconds:
            raise ValueError("reviewed exposure cannot exceed played exposure")
        expected = self.cumulative_pass_successes / self.cumulative_pass_attempts if self.cumulative_pass_attempts else None
        coverage = self.cumulative_reviewed_seconds / self.cumulative_played_seconds if self.cumulative_played_seconds else None
        for actual, ratio in ((self.observed_pass_completion, expected), (self.coverage_fraction, coverage)):
            if (actual is None) != (ratio is None) or (actual is not None and ratio is not None and abs(actual - ratio) > 1e-10):
                raise ValueError("observed rates require their actual denominators; missing is null")
        return self


class PlayerKnowledge(IntelligenceContract):
    player_id: Identifier
    scope: CohortScope
    method_version: Literal["reviewed-player-memory-v1"] = "reviewed-player-memory-v1"
    evidence_fingerprint: Digest
    as_of: date | None = None
    history: list[LongitudinalPoint] = Field(default_factory=list, max_length=1000)
    relationships: list[RelationshipEvidence] = Field(default_factory=list, max_length=10000)
    confirmed_identity_ids: list[Identifier] = Field(default_factory=list, max_length=10000)
    unresolved_identity_ids: list[Identifier] = Field(default_factory=list, max_length=10000)
    predictive_model_status: Literal["not_trained_or_validated"] = "not_trained_or_validated"
    limitations: list[str] = Field(default_factory=lambda: ["Accumulated reviewed knowledge, not automatic detector retraining or face recognition", "Chronological current-corrected evidence, not a reconstruction of what reviewers knew at a historical time", "Declared coverage and selected action review may bias trends; age/role grouping does not establish fairness"])


class ActionValueEngine(Protocol):
    def fit(self, snapshot: EvidenceSnapshot, *, policy: ActionValuePolicy | None = None) -> ActionValueArtifact: ...
    def score(self, event: ReviewedAction, *, scope: CohortScope, artifact: ActionValueArtifact | None) -> ActionValueResult: ...
    def evaluate(self, snapshot: EvidenceSnapshot, *, policy: ActionValuePolicy | None = None, test_fraction: float = 0.25) -> EvaluationReport: ...


class DuelRatingEngine(Protocol):
    def fit(self, snapshot: EvidenceSnapshot, *, policy: DuelRatingPolicy | None = None) -> DuelRatingReport: ...
    def evaluate(self, snapshot: EvidenceSnapshot, *, policy: DuelRatingPolicy | None = None, test_fraction: float = 0.25) -> EvaluationReport: ...


class PlayerMemoryEngine(Protocol):
    def build(self, snapshot: EvidenceSnapshot, *, player_id: str, as_of: date | None = None) -> PlayerKnowledge: ...


def canonical_fingerprint(value: Any) -> str:
    """SHA-256 of finite canonical JSON; not a signature or a privacy transform.

    Callers must sort unordered collections by stable IDs BEFORE hashing; list
    order remains meaningful (e.g. chronological actions and grid cells). Include
    method/policy/scope and every correction-relevant input in the payload.
    """
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def chronological_match_split(matches: Sequence[EvidenceMatch], *, test_fraction: float = 0.25) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Deterministic whole-date holdout; ties never cross the train/test boundary.

    This is an evaluation partition, not a model fit. Fewer than two unique dates
    cannot produce a leakage-free temporal evaluation. No random row split.
    """
    if not 0 < test_fraction < 1:
        raise ValueError("test fraction must be strictly inside (0,1)")
    if len(matches) > 1000:
        raise ValueError("match split exceeds bounded input")
    if len({item.match_id for item in matches}) != len(matches):
        raise ValueError("duplicate match IDs")
    dates = sorted({item.match_date for item in matches})
    if len(dates) < 2:
        raise ValueError("at least two distinct match dates are required")
    boundary = max(1, min(len(dates) - 1, int(len(dates) * (1 - test_fraction))))
    test_from = dates[boundary]
    ordered = sorted(matches, key=lambda item: (item.match_date, item.match_id))
    return (tuple(item.match_id for item in ordered if item.match_date < test_from), tuple(item.match_id for item in ordered if item.match_date >= test_from))


def utc_timestamp(value: datetime) -> datetime:
    """SQL SQLite timestamps omit tzinfo; normalize explicit database reads."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


EvidenceSnapshot.model_rebuild()
