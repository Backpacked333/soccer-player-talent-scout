"""Versioned, deterministic analysis of reviewer-approved observational evidence.

The posterior is about selected reviewed actions, NOT latent ability. Unobserved
play, event dependence, annotation selection and opponent strength are not modeled.
No hosted LLM, inferred possession change, percentile or universal score is used.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Literal

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models import Appearance, Match, Player, ReviewedEvent
from app.db import utcnow
from app.intelligence.contracts import CohortScope
from app.intelligence.service import load_snapshot
from app.schemas import METHOD_VERSION as METHOD_VERSION
from app.schemas import Cohort, EventRead, Mode, PlayerRead, RateEstimate, Role
# Retain the initial Python interface while using the shared wire contract.
from app.schemas import EligibilityPolicy as EvidencePolicy
from app.schemas import LeaderboardEntry as EvidenceLeaderboardEntry
from app.schemas import LeaderboardResponse as EvidenceLeaderboardResponse
from app.schemas import MetricSummary as EvidenceMetricSummary
from app.schemas import PlayerProfile as EvidencePlayerProfile
from app.services import EVENT_LIMIT, bounded

SYNTHETIC_NOTICE = "Synthetic demonstration — not real player analysis"
DISCLAIMER = "Selected reviewer-approved actions only, not talent or predicted potential. Beta credible intervals assume exchangeable independent action outcomes and do not account for match clustering, selection bias or annotation error. Confidence labels describe evidence sufficiency, not validated accuracy. Coverage is reviewer-declared; no opponent adjustment."
UNAVAILABLE_METRICS = [
    "Expected goals (xG): no trained and validated shot model",
    "Expected threat (xT-style): operator-scoped research artifacts only; inspect individual action availability. VAEP: no trained validated model.",
    "Attacking progression: no reviewed attacking direction or pitch calibration",
    "Turnovers: unsuccessful actions do not establish possession loss; no possession-change annotation",
    "Calibrated speed and distance: no validated pitch tracking",
    "Pressure and off-ball impact: unavailable. Explicit duels: separate provisional Bradley–Terry evidence view, not a global score.",
    "Population percentiles, universal talent rank and predicted potential: not supported",
]


def _beta_fraction(a: float, b: float, x: float) -> float:
    """Lentz continued fraction for regularized incomplete beta (DLMF 8.17)."""
    tiny, tolerance = 1e-300, 3e-14
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    value = d
    for m in range(1, 401):
        m2 = 2 * m
        coefficient = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + coefficient * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        value *= d * c
        coefficient = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + coefficient * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        value *= delta
        if abs(delta - 1.0) < tolerance:
            return value
    raise ArithmeticError("incomplete beta failed to converge")


def beta_cdf(x: float, a: float, b: float) -> float:
    if a <= 0 or b <= 0 or not all(math.isfinite(value) for value in (x, a, b)):
        raise ValueError("finite positive beta parameters required")
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    factor = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    value = factor * _beta_fraction(a, b, x) / a if x < (a + 1) / (a + b + 2) else 1 - factor * _beta_fraction(b, a, 1 - x) / b
    return min(1.0, max(0.0, value))


def beta_quantile(probability: float, a: float, b: float) -> float:
    if not 0 < probability < 1:
        raise ValueError("quantile probability must be strictly inside (0,1)")
    low, high = 0.0, 1.0
    for _ in range(60):
        mid = (low + high) / 2
        if beta_cdf(mid, a, b) < probability:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def approved_query(workspace_id: str, synthetic: bool):
    # Actual actor appearances and all tenant/mode joins are required even for
    # records inserted outside HTTP handlers. No draft/model candidate is scored.
    return select(ReviewedEvent).join(Appearance, and_(Appearance.match_id == ReviewedEvent.match_id, Appearance.player_id == ReviewedEvent.player_id, Appearance.workspace_id == ReviewedEvent.workspace_id, Appearance.is_synthetic == ReviewedEvent.is_synthetic)).join(Match, and_(Match.id == ReviewedEvent.match_id, Match.workspace_id == ReviewedEvent.workspace_id, Match.is_synthetic == ReviewedEvent.is_synthetic)).join(Player, and_(Player.id == ReviewedEvent.player_id, Player.workspace_id == ReviewedEvent.workspace_id, Player.is_synthetic == ReviewedEvent.is_synthetic)).where(ReviewedEvent.workspace_id == workspace_id, ReviewedEvent.is_synthetic == synthetic, ReviewedEvent.status == "approved", ReviewedEvent.outcome.in_(["successful", "unsuccessful"]), ReviewedEvent.reviewed_by.is_not(None), ReviewedEvent.reviewed_at.is_not(None))


def appearance_query(workspace_id: str, synthetic: bool):
    return select(Appearance, Match, Player).join(Match, and_(Match.id == Appearance.match_id, Match.workspace_id == Appearance.workspace_id, Match.is_synthetic == Appearance.is_synthetic)).join(Player, and_(Player.id == Appearance.player_id, Player.workspace_id == Appearance.workspace_id, Player.is_synthetic == Appearance.is_synthetic)).where(Appearance.workspace_id == workspace_id, Appearance.is_synthetic == synthetic)


def current_events(db: Session, workspace_id: str, synthetic: bool, events: list[ReviewedEvent]) -> list[ReviewedEvent]:
    """Use the same current identity/duplicate gates as longitudinal knowledge."""
    from app.intelligence.memory import reviewed_events
    match_ids = {row.match_id for row in events}
    matches = bounded(db, select(Match).where(Match.workspace_id == workspace_id, Match.is_synthetic == synthetic, Match.id.in_(match_ids), Match.match_date <= utcnow().date()))
    accepted: set[str] = set()
    for season, age, competition in sorted({(row.season, row.age_group, row.competition) for row in matches}):
        scope = CohortScope(workspace_id=workspace_id, mode="demo" if synthetic else "real", season=season, age_group=age, competition=competition, role="all_roles")
        selected, _, _, _ = reviewed_events(load_snapshot(db, scope))
        accepted.update(row.event_id for row in selected)
    return [row for row in events if row.id in accepted]


def observed_breakdown(events: list[ReviewedEvent]) -> dict:
    kinds = ("pass", "cross", "shot", "tackle", "interception", "recovery", "carry", "duel", "block", "header")
    return {kind: {"attempts": sum(item.kind == kind for item in events), "successful": sum(item.kind == kind and item.outcome == "successful" for item in events), "unsuccessful": sum(item.kind == kind and item.outcome == "unsuccessful" for item in events)} for kind in kinds}


def rate_estimate(events: list[ReviewedEvent], *, kind: str, reviewed_seconds: float, coverage: float | None, role: str, policy: EvidencePolicy) -> RateEstimate:
    relevant = [item for item in events if item.kind == kind]
    n = len(relevant)
    successes = sum(item.outcome == "successful" for item in relevant)
    match_counts = Counter(item.match_id for item in relevant)
    share = max(match_counts.values()) / n if n else None
    minimum = policy.min_pass_attempts if kind == "pass" else policy.min_duel_attempts
    reasons = []
    if len(match_counts) < policy.min_matches:
        reasons.append(f"Need {kind} evidence from at least {policy.min_matches} matches; observed {len(match_counts)}.")
    if n < minimum:
        reasons.append(f"Need at least {minimum} reviewed {kind} opportunities; observed {n}.")
    if reviewed_seconds < policy.min_reviewed_seconds:
        reasons.append(f"Need at least {policy.min_reviewed_seconds:g} reviewer-declared reviewed seconds; recorded {reviewed_seconds:g}.")
    if coverage is None or coverage < policy.min_coverage_fraction:
        reasons.append(f"Need at least {policy.min_coverage_fraction:.0%} reviewer-declared coverage of playing time.")
    if share is not None and share > policy.max_match_opportunity_share:
        reasons.append(f"No single match may supply more than {policy.max_match_opportunity_share:.0%} of relevant opportunities; observed {share:.1%}.")
    if role == "unknown":
        reasons.append("A reviewed appearance role is required for cohort comparison.")
    eligibility: Literal["eligible", "provisional", "unranked"] = "eligible" if not reasons else "provisional" if n else "unranked"
    a, b = successes + policy.prior_alpha, n - successes + policy.prior_beta
    lower = beta_quantile(0.025, a, b) * 100 if n else None
    upper = beta_quantile(0.975, a, b) * 100 if n else None
    return RateEstimate(metric="pass_completion" if kind == "pass" else "duel_success", attempts=n, successes=successes, failures=n-successes, relevant_match_count=len(match_counts), max_match_opportunity_share=share, observed_pct=100 * successes / n if n else None, posterior_mean_pct=100 * a / (a + b) if n else None, credible_low_pct=lower, credible_high_pct=upper, prior_alpha=policy.prior_alpha, prior_beta=policy.prior_beta, posterior_alpha=a if n else None, posterior_beta=b if n else None, reviewed_seconds=reviewed_seconds, coverage_fraction=coverage, observed_attempts_per_90_reviewed_minutes=n * 5400 / reviewed_seconds if reviewed_seconds > 0 and n else None, eligibility=eligibility, confidence="moderate" if eligibility == "eligible" else "limited" if n else "insufficient", eligibility_reasons=reasons)


def opponent_breakdowns(events: list[ReviewedEvent], cohort: Cohort, synthetic: bool, policy: EvidencePolicy) -> list[dict]:
    groups = defaultdict(list)
    for event in events:
        if event.opponent_player_id:
            groups[(event.player_id, event.opponent_player_id, event.kind)].append(event)
    result = []
    for (actor, opponent, kind), items in sorted(groups.items()):
        n = len(items)
        successes = sum(item.outcome == "successful" for item in items)
        a, b = successes + policy.prior_alpha, n - successes + policy.prior_beta
        # Do not pool action types or mirror one actor's result as their opponent's
        # observed result; these are directional, explicitly linked observations.
        result.append({"actor_player_id": actor, "opponent_player_id": opponent, "kind": kind, "cohort": cohort.model_dump(), "is_synthetic": synthetic, "approved_event_count": n, "match_count": len({item.match_id for item in items}), "successful": successes, "unsuccessful": n-successes, "observed_success_pct": 100*successes/n, "posterior_mean_pct": 100*a/(a+b), "credible_low_pct": 100*beta_quantile(0.025, a, b), "credible_high_pct": 100*beta_quantile(0.975, a, b), "prior_alpha": policy.prior_alpha, "prior_beta": policy.prior_beta, "credible_mass": 0.95, "reviewed_seconds": None, "coverage_fraction": None, "eligibility": "unranked", "confidence": "insufficient", "rank": None, "comparison_score": None, "event_ids": [item.id for item in items], "methodology_version": METHOD_VERSION, "eligibility_reasons": ["Pairwise exposure is unavailable: roster coverage does not establish time facing this opponent", "Selected explicitly linked actions only; not an opponent-adjusted matchup score"]})
    return result


def summarize(player_id: str, cohort: Cohort, synthetic: bool, appearances: list[Appearance], events: list[ReviewedEvent], policy: EvidencePolicy | None = None) -> EvidenceMetricSummary:
    policy = policy or EvidencePolicy()
    played = sum(item.played_seconds for item in appearances)
    reviewed = sum(item.reviewed_seconds for item in appearances)
    coverage = reviewed / played if played > 0 else None
    passes = rate_estimate(events, kind="pass", reviewed_seconds=reviewed, coverage=coverage, role=cohort.role, policy=policy)
    duels = rate_estimate(events, kind="duel", reviewed_seconds=reviewed, coverage=coverage, role=cohort.role, policy=policy)
    return EvidenceMetricSummary(player_id=player_id, cohort=cohort, is_synthetic=synthetic, policy=policy, match_count=len({item.match_id for item in events}), approved_event_count=len(events), pass_attempts=passes.attempts, successful_passes=passes.successes, pass_completion_pct=passes.observed_pct, played_seconds=played, reviewed_seconds=reviewed, coverage_fraction=coverage, confidence=passes.confidence, eligibility=passes.eligibility, eligibility_reasons=passes.eligibility_reasons, rank=None, pass_rate=passes, duel_rate=duels, observed_events=observed_breakdown(events), comparison_score=passes.credible_low_pct if passes.eligibility == "eligible" else None, opponent_breakdowns=opponent_breakdowns(events, cohort, synthetic, policy))


def player_profile(db: Session, workspace_id: str, player_id: str) -> EvidencePlayerProfile | None:
    player = db.scalar(select(Player).where(Player.workspace_id == workspace_id, Player.id == player_id))
    if player is None:
        return None
    rows = list(db.execute(appearance_query(workspace_id, player.is_synthetic).where(Appearance.player_id == player.id)))
    events = bounded(db, approved_query(workspace_id, player.is_synthetic).where(ReviewedEvent.player_id == player.id).order_by(Match.match_date.desc(), ReviewedEvent.timestamp_seconds, ReviewedEvent.id), EVENT_LIMIT)
    events = current_events(db, workspace_id, player.is_synthetic, events)
    groups = defaultdict(list)
    for appearance, match, _player in rows:
        if match.match_date > utcnow().date():
            continue
        groups[(match.age_group, appearance.role, match.competition, match.season)].append(appearance)
    metrics = []
    for key, appearances in sorted(groups.items()):
        match_ids = {item.match_id for item in appearances}
        cohort = Cohort(age_group=key[0], role=key[1], competition=key[2], season=key[3])
        metrics.append(summarize(player.id, cohort, player.is_synthetic, appearances, [item for item in events if item.match_id in match_ids]))
    notice = f"{SYNTHETIC_NOTICE}. " if player.is_synthetic else ""
    return EvidencePlayerProfile(player=PlayerRead.model_validate(player), metrics=metrics, timeline=[EventRead.model_validate(item) for item in events], unavailable_metrics=UNAVAILABLE_METRICS, disclaimer=notice + DISCLAIMER)


def leaderboard(db: Session, workspace_id: str, *, age_group: str, role: Role, competition: str, season: str | None = None, mode: Mode = "real", policy: EvidencePolicy | None = None) -> EvidenceLeaderboardResponse:
    policy = policy or EvidencePolicy()
    synthetic = mode == "demo"
    cohort = Cohort(age_group=age_group, role=role, competition=competition, season=season)
    criteria = [Match.age_group == age_group, Appearance.role == role, Match.competition == competition]
    if season is not None:
        criteria.append(Match.season == season)
    rows = list(db.execute(appearance_query(workspace_id, synthetic).where(*criteria)))
    events = bounded(db, approved_query(workspace_id, synthetic).where(*criteria), EVENT_LIMIT)
    events = current_events(db, workspace_id, synthetic, events)
    rows = [row for row in rows if row[1].match_date <= utcnow().date()]
    if season is None and len({row[1].season for row in rows}) > 1:
        from app.auth import fail
        fail(422, "season_required", "Choose one season; cross-season ranks are not comparable.")
    by_player, events_by_player, identities = defaultdict(list), defaultdict(list), {}
    for appearance, _match, player in rows:
        by_player[player.id].append(appearance)
        identities[player.id] = player
    for event in events:
        events_by_player[event.player_id].append(event)
    entries = []
    for player_id, appearances in by_player.items():
        summary = summarize(player_id, cohort, synthetic, appearances, events_by_player[player_id], policy)
        player = identities[player_id]
        entries.append(EvidenceLeaderboardEntry(**summary.model_dump(), display_name=player.display_name, team_name=player.team_name))
    # Conservative uncertainty-aware comparison only among eligible records.
    # Provisional rows are alphabetical, not silently pseudo-ranked. Equal scores
    # at six decimal percentage points share competition ranks (1,1,3).
    entries.sort(key=lambda item: (item.eligibility != "eligible", -round(item.comparison_score or 0, 6) if item.eligibility == "eligible" else 0, item.display_name.casefold(), item.player_id))
    eligible_index, previous_score, previous_rank = 0, None, None
    for entry in entries:
        if entry.eligibility == "eligible":
            eligible_index += 1
            assert entry.comparison_score is not None
            score = round(entry.comparison_score, 6)
            if score != previous_score:
                previous_rank = eligible_index
            entry.rank = previous_rank
            previous_score = score
    notice = f"{SYNTHETIC_NOTICE}. " if synthetic else ""
    if season is None:
        notice += "Season filter omitted: this requested competition cohort includes all recorded seasons. "
    return EvidenceLeaderboardResponse(cohort=cohort, mode=mode, is_synthetic=synthetic, policy=policy, entries=entries, unavailable_metrics=UNAVAILABLE_METRICS, disclaimer=notice + DISCLAIMER)


def match_metrics(db: Session, workspace_id: str, match: Match) -> dict:
    rows = list(db.execute(appearance_query(workspace_id, match.is_synthetic).where(Match.id == match.id)))
    events = bounded(db, approved_query(workspace_id, match.is_synthetic).where(ReviewedEvent.match_id == match.id), EVENT_LIMIT)
    events = current_events(db, workspace_id, match.is_synthetic, events)
    players, pairs = [], []
    for appearance, _match, player in rows:
        cohort = Cohort(age_group=match.age_group, role=appearance.role, competition=match.competition, season=match.season)
        summary = summarize(player.id, cohort, match.is_synthetic, [appearance], [item for item in events if item.player_id == player.id])
        players.append({"player": PlayerRead.model_validate(player).model_dump(mode="json"), "summary": summary.model_dump(mode="json"), "observed_events": summary.observed_events})
        pairs.extend(summary.opponent_breakdowns)
    return {"match_id": match.id, "is_synthetic": match.is_synthetic, "methodology_version": METHOD_VERSION, "policy": EvidencePolicy().model_dump(), "approved_event_count": len(events), "players": players, "observed_matchups": pairs, "unavailable_metrics": UNAVAILABLE_METRICS, "disclaimer": (f"{SYNTHETIC_NOTICE}. " if match.is_synthetic else "") + DISCLAIMER}
