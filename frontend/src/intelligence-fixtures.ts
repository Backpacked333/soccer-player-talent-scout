// Explicitly fictional UI-contract fixtures; no observed youth data or trained model.
import type { Knowledge } from './intelligence-types';
import { testMatch, testPlayer } from './test-fixtures';
export const testKnowledge: Knowledge = {
  player_id: testPlayer.id, scope: { workspace_id: testPlayer.workspace_id, mode: 'demo', season: testMatch.season, age_group: testMatch.age_group, competition: testMatch.competition, role: 'all_roles' },
  as_of: '2025-04-01', method_version: 'reviewed-player-memory-v1', evidence_fingerprint: 'a'.repeat(64), predictive_model_status: 'unavailable',
  roster_match_count: 3, reviewed_match_count: 3, approved_event_count: 3, limitations: ['Selected observations, not independent validation.'],
  confirmed_identity_ids: ['identity-confirmed'], unresolved_identity_ids: ['identity-proposed'],
  tendencies: [{ kind: 'pass', attempts: 3, successes: 2, distinct_matches: 3, observed_rate: 2 / 3, posterior_mean: 0.6, credible_low: 0.19, credible_high: 0.93, interpretation: 'Provisional selected outcomes only.' }],
  match_details: [1, 2, 3].map((index) => ({ match_id: `match-${index}`, match_date: `2025-03-0${index}`, appearance_role: 'midfielder', jersey_number: 8, approved_event_ids: [`event-${index}`], approved_event_count: 1, cumulative_reviewed_events: index, cumulative_pass_attempts: index, cumulative_pass_successes: Math.min(index, 2), observed_pass_completion: Math.min(index, 2) / index })),
  relationship_details: [{ other_player_id: 'fictional-teammate', relation: 'teammate', match_ids: ['match-1'], event_ids: [], match_count: 1, direct_event_count: 0, explicit_contest_count: null, interpretation: 'Shared roster only, not on-pitch overlap.' }, { other_player_id: 'fictional-rival', relation: 'direct_duel', match_ids: ['match-2'], event_ids: ['duel-1'], match_count: 1, direct_event_count: 1, explicit_contest_count: 1, interpretation: 'Explicit reviewed duel only.' }],
  role_segments: [], exclusion_reasons: {},
};
export const unavailableValue = { event_id: 'event-test', status: 'unavailable', value: null, method_version: 'spatial-markov-v1', reasons: ['No operator-activated spatial model.'], interpretation: 'Not xG, VAEP or talent.' };
