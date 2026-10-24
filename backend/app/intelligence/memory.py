"""Deterministic current-corrected memory of reviewed, canonical player evidence.

No learned parameters persist here. Rebuild from one authorized coherent
EvidenceSnapshot after every correction; never append cached cumulative totals.
The engine uses match-date/appearance-role context, NOT current Player metadata.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Literal

from pydantic import Field

from app.analytics import beta_quantile
from app.intelligence.contracts import (
    EvidenceSnapshot, Identifier, IntelligenceContract, LongitudinalPoint,
    PlayerKnowledge, Probability, RelationshipEvidence, ReviewedAction, Role,
    canonical_fingerprint,
)
from app.intelligence.contracts import conflicting_identity_ids as _conflicting_identities

MEMORY_VERSION = "reviewed-player-memory-v1"
MAX_RELATIONSHIPS = 10000
KINDS = ("pass", "cross", "shot", "tackle", "interception", "recovery", "carry", "duel", "block", "header")
RATE_INTERPRETATION = (
    "Beta(1,1) prior, equal-tail 95% posterior interval for selected reviewed outcomes; "
    "assumes exchangeable independent actions. Not match-cluster calibrated, not a population "
    "ability estimate, and not corrected for selection, opponent strength or annotation error."
)


class ObservedTendency(IntelligenceContract):
    kind: str
    attempts: int = Field(ge=0, le=10000)
    successes: int = Field(ge=0, le=10000)
    distinct_matches: int = Field(ge=0, le=1000)
    max_match_opportunity_share: Probability | None
    observed_rate: Probability | None
    posterior_mean: Probability | None
    credible_low: Probability | None
    credible_high: Probability | None
    prior_alpha: float = 1.0
    prior_beta: float = 1.0
    credible_mass: float = 0.95
    status: Literal["unavailable", "provisional"]
    interpretation: str = RATE_INTERPRETATION


class MatchKnowledge(LongitudinalPoint):
    appearance_id: Identifier
    side: Literal["home", "away"]
    jersey_number: int | None
    played_seconds: float = Field(ge=0)
    reviewed_seconds: float = Field(ge=0)
    approved_event_count: int = Field(ge=0)
    pass_attempts: int = Field(ge=0)
    pass_successes: int = Field(ge=0)
    match_pass_completion: Probability | None
    cumulative_roster_matches: int = Field(ge=1)


class RoleKnowledge(IntelligenceContract):
    appearance_role: Role
    roster_matches: int = Field(ge=0)
    reviewed_matches: int = Field(ge=0)
    match_ids: list[Identifier] = Field(max_length=1000)
    first_observed_date: date | None
    last_observed_date: date | None
    played_seconds: float = Field(ge=0)
    reviewed_seconds: float = Field(ge=0)
    coverage_fraction: Probability | None
    tendencies: list[ObservedTendency]


class RelationshipKnowledge(RelationshipEvidence):
    match_count: int = Field(ge=1, le=1000)
    direct_event_count: int = Field(ge=0, le=10000)
    explicit_contest_count: int | None = Field(default=None, ge=1, le=10000)


class ReviewedPlayerKnowledge(PlayerKnowledge):
    """Additive result fields; build() still satisfies PlayerMemoryEngine."""
    match_details: list[MatchKnowledge] = Field(default_factory=list, max_length=1000)
    relationship_details: list[RelationshipKnowledge] = Field(default_factory=list, max_length=10000)
    first_observed_date: date | None = None
    last_observed_date: date | None = None
    roster_match_count: int = Field(default=0, ge=0, le=1000)
    reviewed_match_count: int = Field(default=0, ge=0, le=1000)
    approved_event_count: int = Field(default=0, ge=0, le=10000)
    tendencies: list[ObservedTendency] = Field(default_factory=list)
    role_segments: list[RoleKnowledge] = Field(default_factory=list)
    excluded_event_ids: list[Identifier] = Field(default_factory=list, max_length=10000)
    duplicate_event_ids: list[Identifier] = Field(default_factory=list, max_length=10000)
    exclusion_reasons: dict[str, str] = Field(default_factory=dict)
    relationship_limit: int = MAX_RELATIONSHIPS


def _tendency(events: list[ReviewedAction], kind: str) -> ObservedTendency:
    relevant = [item for item in events if item.kind == kind]
    count = len(relevant)
    successes = sum(item.outcome == "successful" for item in relevant)
    match_counts: dict[str, int] = defaultdict(int)
    for item in relevant:
        match_counts[item.match_id] += 1
    a, b = successes + 1, count - successes + 1
    return ObservedTendency(
        kind=kind, attempts=count, successes=successes, distinct_matches=len(match_counts),
        max_match_opportunity_share=max(match_counts.values()) / count if count else None,
        observed_rate=successes / count if count else None,
        posterior_mean=a / (a + b) if count else None,
        credible_low=beta_quantile(0.025, a, b) if count else None,
        credible_high=beta_quantile(0.975, a, b) if count else None,
        status="provisional" if count else "unavailable",
    )


def _event_signature(event: ReviewedAction) -> str:
    # Review timestamps/IDs are not an independent observation. Different
    # outcomes/semantics remain corrections, not purported extra support.
    return canonical_fingerprint(event.model_dump(mode="json", exclude={"event_id", "updated_at", "reviewed_at", "reviewed_by", "provenance", "identity_assignment_id", "identity_assignment_revision"}))


def _unfiltered_contests(snapshot: EvidenceSnapshot):
    # Memory filters the SUBJECT's historical appearance role, not the opponent's.
    # Rating cohorts require both roles; integrity/relationship facts must not.
    from app.intelligence.ratings import contest_evidence
    return contest_evidence(snapshot.model_copy(update={"scope": snapshot.scope.model_copy(update={"role": "all_roles"})}))


def reviewed_events(snapshot: EvidenceSnapshot) -> tuple[list[ReviewedAction], dict[str, str], list[str], set[str]]:
    """Reusable defensive evidence gate; caller must supply a validated snapshot.

    Duplicate IDs fail at the contract. Identical semantic records count once;
    contradictory explicit contests count zero. Mirrored consistent duel records
    remain directional observations for their own actors, never invented wins.
    """
    matches = {row.match_id: row for row in snapshot.matches}
    identities = {row.id: row for row in snapshot.identities}
    conflicts = _conflicting_identities(snapshot.identities)
    conflicts.update(row.id for row in snapshot.identities if row.end_seconds > matches[row.match_id].duration_seconds)
    excluded: dict[str, str] = {}
    accepted: list[ReviewedAction] = []
    for row in sorted(snapshot.events, key=lambda item: item.event_id):
        reason = None
        if row.status != "approved" or row.outcome == "unknown" or row.reviewed_by is None or row.reviewed_at is None:
            reason = "not_reviewer_approved"
        elif row.timestamp_seconds > matches[row.match_id].duration_seconds or (row.end_timestamp_seconds is not None and row.end_timestamp_seconds > matches[row.match_id].duration_seconds):
            reason = "outside_match_duration"
        elif row.provenance == "model_candidate" and row.identity_assignment_id is None:
            reason = "model_candidate_requires_confirmed_identity"
        elif row.identity_assignment_id is not None:
            identity = identities.get(row.identity_assignment_id)
            if identity is None or identity.status != "confirmed" or identity.id in conflicts:
                reason = "identity_missing_unconfirmed_or_conflicting"
            elif (identity.match_id, identity.player_id, identity.revision) != (row.match_id, row.player_id, row.identity_assignment_revision):
                reason = "identity_revision_or_actor_mismatch"
            elif identity.start_seconds > row.timestamp_seconds or identity.end_seconds < (row.end_timestamp_seconds if row.end_timestamp_seconds is not None else row.timestamp_seconds) or identity.end_seconds > matches[row.match_id].duration_seconds:
                reason = "event_outside_confirmed_interval"
        if reason:
            excluded[row.event_id] = reason
        else:
            accepted.append(row)
    # Explicit contest identity, not equal timestamps, determines mirrored duels.
    # The authoritative selector also excludes valid mirrors of ineligible rows.
    _, duel_exclusions = _unfiltered_contests(snapshot)
    explicit_duels = {row.event_id for row in snapshot.events if row.kind == "duel" and row.contest_id is not None}
    for exclusion in duel_exclusions:
        if exclusion.event_id in explicit_duels or any("ambiguous" in reason or "contradictory" in reason for reason in exclusion.reasons):
            reason = "ambiguous_or_contradictory_contest" if "contradictory_contest_annotations" in exclusion.reasons else "; ".join(exclusion.reasons)
            excluded.setdefault(exclusion.event_id, reason)
    occurrences: dict[str, list[ReviewedAction]] = defaultdict(list)
    for row in accepted:
        # Two approved rows with identical event geometry and actors but
        # incompatible outcomes are ambiguous copies, not two opportunities.
        occurrence = canonical_fingerprint(row.model_dump(mode="json", include={"match_id", "player_id", "opponent_player_id", "kind", "timestamp_seconds", "end_timestamp_seconds", "start_x", "start_y", "end_x", "end_y", "period", "contest_id"}))
        occurrences[occurrence].append(row)
    for rows in occurrences.values():
        outcomes = {(row.outcome, row.shot_outcome, row.possession_retained) for row in rows}
        if len(outcomes) > 1:
            for row in rows:
                excluded.setdefault(row.event_id, "ambiguous_duplicate_outcomes")
    seen: set[str] = set()
    duplicates = []
    unique = []
    for row in accepted:
        if row.event_id in excluded:
            continue
        signature = canonical_fingerprint([row.match_id, row.contest_id, row.player_id]) if row.kind == "duel" and row.contest_id is not None else _event_signature(row)
        if signature in seen:
            duplicates.append(row.event_id)
        else:
            seen.add(signature)
            unique.append(row)
    return unique, excluded, duplicates, conflicts


class ReviewedPlayerMemoryEngine:
    """Recomputes at most 1,000 matches / 10,000 events / 10,000 roster rows.

    `as_of` excludes future match dates but uses today's corrected evidence for
    earlier games. It does NOT reconstruct historical reviewer knowledge. Each
    cohort is isolated; all_roles is explicitly mixed-role, with role segments.
    """
    def build(self, snapshot: EvidenceSnapshot, *, player_id: str, as_of: date | None = None) -> ReviewedPlayerKnowledge:
        # Revalidate to defeat unchecked model_copy/update or mutated callers.
        snapshot = EvidenceSnapshot.model_validate(snapshot.model_dump(mode="json"))
        selected_appearances = [row for row in snapshot.appearances if row.player_id == player_id and (snapshot.scope.role == "all_roles" or row.role == snapshot.scope.role)]
        match_map = {row.match_id: row for row in snapshot.matches if as_of is None or row.match_date <= as_of}
        appearances = sorted((row for row in selected_appearances if row.match_id in match_map), key=lambda row: (match_map[row.match_id].match_date, row.match_id))
        match_ids = {row.match_id for row in appearances}
        # All inputs, IDs and fingerprints are restricted BEFORE processing;
        # future events, future identity decisions and future roster peers cannot
        # influence the as-of result even indirectly through a fingerprint.
        scoped = EvidenceSnapshot(
            scope=snapshot.scope,
            matches=sorted((row for row in snapshot.matches if row.match_id in match_ids), key=lambda row: row.match_id),
            appearances=sorted((row for row in snapshot.appearances if row.match_id in match_ids), key=lambda row: (row.match_id, row.player_id)),
            events=sorted((row for row in snapshot.events if row.match_id in match_ids), key=lambda row: row.event_id),
            identities=sorted((row for row in snapshot.identities if row.match_id in match_ids), key=lambda row: row.id),
        )
        accepted, excluded, duplicates, conflicts = reviewed_events(scoped)
        own_events = [row for row in accepted if row.player_id == player_id]
        own_events.sort(key=lambda row: (match_map[row.match_id].match_date, row.match_id, row.timestamp_seconds, row.event_id))
        by_match: dict[str, list[ReviewedAction]] = defaultdict(list)
        for event in own_events:
            by_match[event.match_id].append(event)
        points = []
        cumulative_events = cumulative_matches = cumulative_passes = cumulative_successes = 0
        played = reviewed = 0.0
        for index, appearance in enumerate(appearances):
            rows = by_match[appearance.match_id]
            passes = [row for row in rows if row.kind == "pass"]
            successes = sum(row.outcome == "successful" for row in passes)
            cumulative_events += len(rows)
            cumulative_matches += bool(rows)
            cumulative_passes += len(passes)
            cumulative_successes += successes
            played += appearance.played_seconds
            reviewed += appearance.reviewed_seconds
            match = match_map[appearance.match_id]
            points.append(MatchKnowledge(
                match_id=match.match_id, match_date=match.match_date, appearance_role=appearance.role, age_group=match.age_group,
                approved_event_ids=[row.event_id for row in rows], cumulative_reviewed_events=cumulative_events,
                cumulative_matches=cumulative_matches, cumulative_pass_attempts=cumulative_passes, cumulative_pass_successes=cumulative_successes,
                cumulative_played_seconds=played, cumulative_reviewed_seconds=reviewed,
                observed_pass_completion=cumulative_successes / cumulative_passes if cumulative_passes else None,
                coverage_fraction=reviewed / played if played else None,
                appearance_id=appearance.appearance_id, side=appearance.side, jersey_number=appearance.jersey_number,
                played_seconds=appearance.played_seconds, reviewed_seconds=appearance.reviewed_seconds,
                approved_event_count=len(rows), pass_attempts=len(passes), pass_successes=successes,
                match_pass_completion=successes / len(passes) if passes else None, cumulative_roster_matches=index + 1,
            ))
        segments = []
        for role in sorted({row.role for row in appearances}):
            role_appearances = [row for row in appearances if row.role == role]
            role_matches = {row.match_id for row in role_appearances}
            events = [row for row in own_events if row.match_id in role_matches]
            dates = [match_map[row.match_id].match_date for row in events]
            role_played = sum(row.played_seconds for row in role_appearances)
            role_reviewed = sum(row.reviewed_seconds for row in role_appearances)
            segments.append(RoleKnowledge(
                appearance_role=role, roster_matches=len(role_matches), reviewed_matches=len({row.match_id for row in events}),
                match_ids=[row.match_id for row in role_appearances], first_observed_date=min(dates) if dates else None,
                last_observed_date=max(dates) if dates else None, played_seconds=role_played, reviewed_seconds=role_reviewed,
                coverage_fraction=role_reviewed / role_played if role_played else None, tendencies=[_tendency(events, kind) for kind in KINDS],
            ))
        own_roster = {row.match_id: row for row in appearances}
        edge_matches: dict[tuple[str, str], set[str]] = defaultdict(set)
        edge_events: dict[tuple[str, str], list[ReviewedAction]] = defaultdict(list)
        for row in scoped.appearances:
            if row.player_id != player_id:
                relation = "co_rostered_teammate" if own_roster[row.match_id].side == row.side else "co_rostered_opponent"
                edge_matches[(row.player_id, relation)].add(row.match_id)
        direct_contests, _ = _unfiltered_contests(scoped)
        direct_ids = {event_id for contest in direct_contests for event_id in contest.event_ids}
        for duel in accepted:
            if duel.event_id in direct_ids and duel.opponent_player_id is not None and player_id in (duel.player_id, duel.opponent_player_id):
                other = duel.opponent_player_id if duel.player_id == player_id else duel.player_id
                edge_matches[(other, "direct_duel")].add(duel.match_id)
                edge_events[(other, "direct_duel")].append(duel)
        if len(edge_matches) > MAX_RELATIONSHIPS:
            raise ValueError("player relationship graph exceeds 10000 edges; narrow the cohort")
        relationships = []
        for (other, relation), supporting_matches in sorted(edge_matches.items()):
            ordered_matches = sorted(supporting_matches, key=lambda identifier: (match_map[identifier].match_date, identifier))
            events = sorted(edge_events[(other, relation)], key=lambda row: row.event_id)
            explanation = "Shared authorized roster membership only: not simultaneous field time, tactical chemistry, pass partnership or friendship."
            if relation == "direct_duel":
                explanation = "Explicit reviewed opposite-side duels only; event counts may include consistent mirrored actor records. No pairwise exposure, automatic winner inference or matchup ability prediction."
            relationships.append(RelationshipKnowledge(
                player_id=player_id, other_player_id=other, relation=relation,
                match_ids=ordered_matches, event_ids=[row.event_id for row in events],
                first_match_date=match_map[ordered_matches[0]].match_date, last_match_date=match_map[ordered_matches[-1]].match_date,
                match_count=len(supporting_matches), direct_event_count=len(events),
                explicit_contest_count=len({(row.match_id, row.contest_id) for row in events}) if events and all(row.contest_id is not None for row in events) else None,
                interpretation=explanation,
            ))
        dates = [match_map[row.match_id].match_date for row in own_events]
        own_identities = [row for row in scoped.identities if row.player_id == player_id]
        relevant_event_ids = {row.event_id for row in scoped.events if row.player_id == player_id or row.opponent_player_id == player_id}
        payload = {"method": MEMORY_VERSION, "player_id": player_id, "as_of": as_of.isoformat() if as_of else None, "rate_prior": [1, 1], "credible_mass": 0.95, "snapshot": scoped.model_dump(mode="json")}
        result = ReviewedPlayerKnowledge(
            player_id=player_id, scope=snapshot.scope, as_of=as_of,
            evidence_fingerprint=canonical_fingerprint(payload),
            history=[LongitudinalPoint(**point.model_dump(include=set(LongitudinalPoint.model_fields))) for point in points],
            relationships=[RelationshipEvidence(**edge.model_dump(include=set(RelationshipEvidence.model_fields))) for edge in relationships],
            match_details=points, relationship_details=relationships,
            confirmed_identity_ids=[row.id for row in own_identities if row.status == "confirmed" and row.id not in conflicts],
            unresolved_identity_ids=[row.id for row in own_identities if row.status == "proposed" or row.id in conflicts],
            first_observed_date=min(dates) if dates else None, last_observed_date=max(dates) if dates else None,
            roster_match_count=len(appearances), reviewed_match_count=len({row.match_id for row in own_events}),
            approved_event_count=len(own_events), tendencies=[_tendency(own_events, kind) for kind in KINDS], role_segments=segments,
            excluded_event_ids=sorted(identifier for identifier in excluded if identifier in relevant_event_ids),
            exclusion_reasons={identifier: excluded[identifier] for identifier in sorted(excluded) if identifier in relevant_event_ids},
            duplicate_event_ids=sorted(identifier for identifier in duplicates if identifier in relevant_event_ids),
        )
        result.limitations += [
            RATE_INTERPRETATION,
            "Cumulative matches counts games with accepted reviewed actions, not roster presence; first/last observed dates also require accepted actions.",
            "Missing action opportunities produce null rates, not zeros or a prior-only 50% estimate. Recorded zero events means no accepted records, not no actions played.",
            "Role segments use historical appearances; mixed-role totals are descriptive, not role-adjusted. Season/age/competition are isolated by the supplied scope.",
            "A trajectory describes changing reviewed samples, not causal development, improvement, talent or future professional potential.",
            "Duplicate semantic records count once; contradictory explicit contests and conflicting duplicate outcomes are excluded. Independent real events sharing identical timestamps/geometry cannot be distinguished without better annotation.",
            "Current identity status is checked but this pure engine cannot inspect upload replacement, permissions or processing leases; the snapshot loader must fence those sources.",
        ]
        return result


# Straightforward implementation name for integration alongside the Protocol.
PlayerMemoryEngine = ReviewedPlayerMemoryEngine
