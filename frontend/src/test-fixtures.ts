/** Synthetic test-only fixtures. Never imported by the application entrypoint. */
import type { AppearanceRead, EventRead, MatchRead, MetricSummary, PlayerRead, RateEstimate, SessionResponse } from './types';

export const testRate: RateEstimate = {
  metric: 'pass_completion', attempts: 4, successes: 3, failures: 1,
  relevant_match_count: 1, max_match_opportunity_share: 1, observed_pct: 75,
  posterior_mean_pct: 66.6667, credible_low_pct: 28.36, credible_high_pct: 94.73,
  credible_mass: 0.95, prior_alpha: 1, prior_beta: 1, posterior_alpha: 4, posterior_beta: 2,
  reviewed_seconds: 600, coverage_fraction: 1 / 3, observed_attempts_per_90_reviewed_minutes: 36,
  eligibility: 'provisional', confidence: 'limited', eligibility_reasons: ['Needs more reviewed matches'],
  methodology_version: 'reviewed-actions-beta-v1.0.0',
  interpretation: 'Conditional estimate for selected reviewed actions; not a population percentile or a forecast.',
};
export const testSession: SessionResponse = { account: { id: 'account-test', email: 'scout@example.test', workspace_id: 'workspace-test' }, workspace: { id: 'workspace-test', name: 'Test workspace' }, csrf_token: 'csrf-test-not-a-credential' };
export const testPlayer: PlayerRead = { id: 'player-test', workspace_id: 'workspace-test', display_name: 'Synthetic roster 01', age_group: 'U15', primary_role: 'midfielder', team_name: 'Synthetic home', notes: '', is_synthetic: true, created_at: '2025-01-01T00:00:00Z' };
export const testMatch: MatchRead = { id: 'match-test', workspace_id: 'workspace-test', title: 'Synthetic home vs Synthetic away', age_group: 'U15', match_date: '2025-01-02', competition: 'Synthetic development league', season: '2025', team_name: 'Synthetic home', opponent_name: 'Synthetic away', duration_seconds: 5400, is_synthetic: true, created_at: '2025-01-01T00:00:00Z' };
export const testAppearance: AppearanceRead = { id: 'appearance-test', player_id: testPlayer.id, match_id: testMatch.id, workspace_id: 'workspace-test', jersey_number: 7, side: 'home', role: 'midfielder', played_seconds: 1800, reviewed_seconds: 600, notes: '', is_synthetic: true };
export const testEvent: EventRead = { id: 'event-test', player_id: testPlayer.id, match_id: testMatch.id, workspace_id: 'workspace-test', kind: 'pass', outcome: 'successful', timestamp_seconds: 65.5, end_timestamp_seconds: 68, status: 'approved', opponent_player_id: null, start_x: null, start_y: null, end_x: null, end_y: null, notes: 'Synthetic test-only observation', provenance: 'synthetic', confidence: null, model_version: null, created_by: 'account-test', reviewed_by: 'account-test', reviewed_at: '2025-01-02T00:00:00Z', is_synthetic: true, created_at: '2025-01-02T00:00:00Z', updated_at: '2025-01-02T00:00:00Z' };
export const testMetric: MetricSummary = { player_id: testPlayer.id, cohort: { age_group: 'U15', role: 'midfielder', competition: 'Synthetic development league', season: '2025' }, is_synthetic: true, match_count: 1, approved_event_count: 4, pass_attempts: 4, successful_passes: 3, pass_completion_pct: 75, played_seconds: 1800, reviewed_seconds: 600, coverage_fraction: 1 / 3, confidence: 'insufficient', eligibility: 'provisional', eligibility_reasons: ['Needs more reviewed matches'], rank: null };
