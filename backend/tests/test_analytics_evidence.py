"""Auditable numerical and live correction tests for versioned evidence scores."""
from __future__ import annotations

import math
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.analytics import METHOD_VERSION, beta_cdf, beta_quantile, summarize
from app.db import get_session_factory
from app.models import Appearance, ReviewedEvent
from app.schemas import Cohort
from test_api_workflows import signup


def cohort(role="midfielder"):
    return Cohort(age_group="U15", role=role, competition="Synthetic development league", season="2025 demo")


def actions(count, successes=None, *, kind="pass", distribution=None, opponent=None):
    successes = count if successes is None else successes
    distribution = distribution or [str(index % 3) for index in range(count)]
    return [SimpleNamespace(id=str(uuid4()), match_id=distribution[index], player_id="00000000-0000-0000-0000-000000000001", opponent_player_id=opponent, kind=kind, outcome="successful" if index < successes else "unsuccessful") for index in range(count)]


def summary(events, *, reviewed=2700, played=3600, role="midfielder"):
    return summarize("00000000-0000-0000-0000-000000000001", cohort(role), False, [SimpleNamespace(played_seconds=played, reviewed_seconds=reviewed)], events)


@pytest.mark.parametrize("x", [0.001, 0.025, 0.1, 0.25, 0.5, 0.8, 0.975, 0.999])
def test_beta_cdf_against_closed_form_polynomials(x):
    assert beta_cdf(x, 1, 1) == pytest.approx(x, abs=1e-12)
    assert beta_cdf(x, 2, 1) == pytest.approx(x*x, abs=1e-12)
    assert beta_cdf(x, 1, 2) == pytest.approx(1-(1-x)**2, abs=1e-12)
    assert beta_cdf(x, 2, 2) == pytest.approx(3*x*x - 2*x*x*x, abs=1e-12)


@pytest.mark.parametrize("probability", [0.025, 0.5, 0.975])
def test_beta_quantiles_analytical_and_large_sample_roundtrip(probability):
    assert beta_quantile(probability, 1, 1) == pytest.approx(probability, abs=1e-12)
    assert beta_quantile(probability, 2, 1) == pytest.approx(math.sqrt(probability), abs=1e-12)
    assert beta_quantile(probability, 1, 2) == pytest.approx(1-math.sqrt(1-probability), abs=1e-12)
    for a, b in ((1, 10001), (10001, 1), (5001, 5001), (8001, 2001), (31, 11)):
        quantile = beta_quantile(probability, a, b)
        assert 0 < quantile < 1
        assert beta_cdf(quantile, a, b) == pytest.approx(probability, abs=1e-9)
        assert quantile == pytest.approx(1-beta_quantile(1-probability, b, a), abs=1e-10)


def test_no_events_no_fake_neutral_estimates_or_confidence():
    result = summary([])
    assert result.pass_completion_pct is None
    assert result.pass_rate.posterior_mean_pct is None
    assert result.pass_rate.credible_low_pct is None
    assert result.pass_rate.posterior_alpha is None
    assert result.duel_rate.posterior_mean_pct is None
    assert result.comparison_score is None and result.rank is None
    assert result.eligibility == "unranked" and result.confidence == "insufficient"


def test_single_success_is_shrunk_and_remains_provisional():
    result = summary(actions(1))
    assert result.pass_completion_pct == 100
    assert result.pass_rate.posterior_mean_pct == pytest.approx(100 * 2/3)
    assert result.pass_rate.credible_low_pct == pytest.approx(100 * math.sqrt(0.025))
    assert result.pass_rate.credible_high_pct == pytest.approx(100 * math.sqrt(0.975))
    assert result.eligibility == "provisional" and result.comparison_score is None
    assert result.pass_rate.observed_attempts_per_90_reviewed_minutes == 2
    assert result.methodology_version == METHOD_VERSION


def test_interval_narrows_with_sample_and_evidence_score_accounts_for_uncertainty():
    sparse = summary(actions(30, 27))
    dense = summary(actions(600, 480))
    assert sparse.pass_completion_pct > dense.pass_completion_pct
    assert dense.comparison_score > sparse.comparison_score
    assert dense.pass_rate.credible_high_pct - dense.pass_rate.credible_low_pct < sparse.pass_rate.credible_high_pct - sparse.pass_rate.credible_low_pct
    assert dense.pass_rate.posterior_mean_pct < dense.pass_completion_pct


def test_zero_success_and_all_success_have_finite_nonzero_width_intervals():
    for successes in (0, 30):
        result = summary(actions(30, successes))
        assert 0 < result.pass_rate.credible_low_pct < result.pass_rate.credible_high_pct < 100
        assert result.pass_rate.posterior_mean_pct not in (0, 100)


def test_relevant_match_distribution_gate_is_not_fooled_by_empty_appearances():
    result = summary(actions(30, 25, distribution=["one"]*30))
    assert result.pass_rate.relevant_match_count == 1
    assert result.eligibility == "provisional"
    assert any("matches" in reason for reason in result.eligibility_reasons)
    result = summary(actions(30, 25, distribution=["one"]*28 + ["two", "three"]))
    assert result.pass_rate.relevant_match_count == 3
    assert result.eligibility == "provisional"
    assert any("single match" in reason for reason in result.eligibility_reasons)


@pytest.mark.parametrize("reviewed,played", [(0, 3600), (1799, 1800), (1800, 3600), (0, 0)])
def test_exposure_and_coverage_gate_scores(reviewed, played):
    result = summary(actions(36, 30), reviewed=reviewed, played=played)
    assert result.eligibility != "eligible"
    assert result.comparison_score is None
    if not played:
        assert result.coverage_fraction is None
    if not reviewed:
        assert result.pass_rate.observed_attempts_per_90_reviewed_minutes is None


def test_actual_declared_review_time_is_rate_denominator():
    result = summary(actions(30, 20), reviewed=1800, played=2000)
    assert result.pass_rate.observed_attempts_per_90_reviewed_minutes == 90
    assert result.coverage_fraction == 0.9
    assert result.reviewed_seconds == 1800
    assert result.eligibility == "eligible"
    assert "not inferred visibility" in result.exposure_source


def test_kind_opportunities_and_duel_uncertainty_not_cross_contaminated():
    result = summary(actions(30, 20) + actions(21, 12, kind="duel") + actions(5, 5, kind="cross") + actions(4, 3, kind="carry"))
    assert result.pass_attempts == 30 and result.successful_passes == 20
    assert result.duel_rate.attempts == 21 and result.duel_rate.successes == 12
    assert result.duel_rate.eligibility == "eligible"
    assert result.duel_rate.posterior_mean_pct == pytest.approx(100*13/23)
    assert result.observed_events["cross"] == {"attempts": 5, "successful": 5, "unsuccessful": 0}
    assert result.observed_events["carry"] == {"attempts": 4, "successful": 3, "unsuccessful": 1}
    assert result.opponent_breakdowns == []


def test_explicit_opponents_only_no_mirrored_or_invented_matchup_exposure():
    opponent = str(uuid4())
    result = summary(actions(12, 9, kind="duel", opponent=opponent) + actions(4, kind="duel"))
    assert len(result.opponent_breakdowns) == 1
    pair = result.opponent_breakdowns[0]
    assert pair["opponent_player_id"] == opponent
    assert pair["approved_event_count"] == 12 and pair["successful"] == 9
    assert pair["posterior_mean_pct"] == pytest.approx(100*10/14)
    assert pair["coverage_fraction"] is None and pair["reviewed_seconds"] is None
    assert pair["rank"] is None and pair["eligibility"] == "unranked"
    assert pair["cohort"] == cohort().model_dump()


def test_unknown_role_never_eligible():
    result = summary(actions(60, 55), role="unknown")
    assert result.eligibility == "provisional"
    assert result.rank is None and result.comparison_score is None
    assert any("role" in reason for reason in result.eligibility_reasons)


def demo_query():
    return {"mode": "demo", "age_group": "U15", "role": "midfielder", "competition": "Synthetic development league", "season": "2025 demo"}


def test_live_cohort_leaderboard_policy_uncertainty_and_empty_modes(api):
    _app, first, _second, _settings = api
    signup(first)
    empty = first.get("/api/leaderboard", params=demo_query()).json()
    assert empty["entries"] == []
    assert first.post("/api/demo/seed").status_code == 200
    response = first.get("/api/leaderboard", params=demo_query())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["policy"]["ranking_metric"] == "pass_completion_posterior_lower_95"
    assert data["policy"]["min_matches"] == 3 and data["policy"]["min_pass_attempts"] == 30
    assert data["methodology_version"] == METHOD_VERSION
    eligible = [entry for entry in data["entries"] if entry["eligibility"] == "eligible"]
    assert [entry["rank"] for entry in eligible] == [1, 2]
    assert eligible[0]["comparison_score"] >= eligible[1]["comparison_score"]
    assert all(entry["is_synthetic"] for entry in data["entries"])
    assert data["entries"][-1]["rank"] is None
    for overrides in ({"mode": "real"}, {"competition": "Disconnected competition"}, {"age_group": "U17"}, {"role": "goalkeeper"}, {"season": "different"}):
        assert first.get("/api/leaderboard", params={**demo_query(), **overrides}).json()["entries"] == []


def test_event_correction_recomputes_posterior_rank_and_review_status(api):
    _app, first, _second, settings = api
    signup(first)
    first.post("/api/demo/seed")
    before = first.get("/api/leaderboard", params=demo_query()).json()
    leader = before["entries"][0]
    profile = first.get(f"/api/players/{leader['player_id']}/profile").json()
    pass_events = [event for event in profile["timeline"] if event["kind"] == "pass" and event["outcome"] == "successful"]
    for event in pass_events[:8]:
        body = {key: event[key] for key in ("player_id", "kind", "outcome", "timestamp_seconds", "end_timestamp_seconds", "status", "opponent_player_id", "start_x", "start_y", "end_x", "end_y", "notes")}
        body["outcome"] = "unsuccessful"
        assert first.put(f"/api/events/{event['id']}", json=body).status_code == 200
    changed = first.get("/api/leaderboard", params=demo_query()).json()
    corrected = next(entry for entry in changed["entries"] if entry["player_id"] == leader["player_id"])
    assert corrected["successful_passes"] == leader["successful_passes"] - 8
    assert corrected["comparison_score"] < leader["comparison_score"]
    assert corrected["rank"] == 2
    # Draft/rejected events cannot contribute even when they carry known outcomes.
    with get_session_factory(settings.database_url)() as db:
        event = db.get(ReviewedEvent, pass_events[8]["id"])
        event.status = "draft"
        event.reviewed_by = None
        event.reviewed_at = None
        db.commit()
    after = first.get(f"/api/players/{leader['player_id']}/profile").json()["metrics"][0]
    assert after["pass_attempts"] == leader["pass_attempts"] - 1
    assert after["methodology_version"] == METHOD_VERSION


def test_roster_coverage_correction_changes_eligibility_without_changing_actions(api):
    _app, first, _second, _settings = api
    signup(first)
    first.post("/api/demo/seed")
    before = first.get("/api/leaderboard", params=demo_query()).json()["entries"][0]
    matches = first.get("/api/matches?mode=demo").json()
    for match in matches:
        roster = first.get(f"/api/matches/{match['id']}/roster").json()
        payload = []
        for row in roster:
            data = {key: row[key] for key in ("player_id", "jersey_number", "side", "role", "played_seconds", "reviewed_seconds", "notes")}
            if row["player_id"] == before["player_id"]:
                data["reviewed_seconds"] = 0
            payload.append(data)
        assert first.put(f"/api/matches/{match['id']}/roster", json={"appearances": payload}).status_code == 200
    current = next(entry for entry in first.get("/api/leaderboard", params=demo_query()).json()["entries"] if entry["player_id"] == before["player_id"])
    assert current["pass_attempts"] == before["pass_attempts"]
    assert current["reviewed_seconds"] == 0 and current["coverage_fraction"] == 0
    assert current["rank"] is None and current["comparison_score"] is None


def test_current_age_band_can_advance_without_rewriting_historical_cohorts(api):
    _app, first, _second, _settings = api
    signup(first)
    first.post("/api/demo/seed")
    before = first.get("/api/leaderboard", params=demo_query()).json()["entries"][0]
    actor = first.get(f"/api/players/{before['player_id']}").json()
    body = {key: actor[key] for key in ("display_name", "age_group", "primary_role", "team_name", "notes")}
    body["age_group"] = "U16"
    assert first.put(f"/api/players/{actor['id']}", json=body).status_code == 200
    historical = next(entry for entry in first.get("/api/leaderboard", params=demo_query()).json()["entries"] if entry["player_id"] == actor["id"])
    assert historical["pass_attempts"] == before["pass_attempts"]
    assert historical["cohort"]["age_group"] == "U15"
    profile = first.get(f"/api/players/{actor['id']}/profile").json()
    assert profile["player"]["age_group"] == "U16"
    assert profile["metrics"][0]["cohort"]["age_group"] == "U15"
    match = first.get("/api/matches?mode=demo").json()[0]
    roster = first.get(f"/api/matches/{match['id']}/roster").json()
    payload = [{key: row[key] for key in ("player_id", "jersey_number", "side", "role", "played_seconds", "reviewed_seconds", "notes")} for row in roster]
    assert first.put(f"/api/matches/{match['id']}/roster", json={"appearances": payload}).status_code == 200
    assert first.get("/api/leaderboard", params={**demo_query(), "age_group": "U16"}).json()["entries"] == []


def test_actual_appearance_role_not_current_primary_role_defines_cohort(api):
    _app, first, _second, settings = api
    signup(first)
    first.post("/api/demo/seed")
    before = first.get("/api/leaderboard", params=demo_query()).json()["entries"][0]
    player = first.get(f"/api/players/{before['player_id']}").json()
    body = {key: player[key] for key in ("display_name", "age_group", "primary_role", "team_name", "notes")}
    body["primary_role"] = "forward"
    assert first.put(f"/api/players/{player['id']}", json=body).status_code == 200
    same = next(entry for entry in first.get("/api/leaderboard", params=demo_query()).json()["entries"] if entry["player_id"] == player["id"])
    assert same["cohort"]["role"] == "midfielder" and same["pass_attempts"] == before["pass_attempts"]
    with get_session_factory(settings.database_url)() as db:
        appearance = db.scalar(select(Appearance).where(Appearance.player_id == player["id"]))
        appearance.role = "forward"
        db.commit()
    profile = first.get(f"/api/players/{player['id']}/profile").json()
    assert {metric["cohort"]["role"] for metric in profile["metrics"]} == {"midfielder", "forward"}
    assert all(metric["rank"] is None for metric in profile["metrics"])
