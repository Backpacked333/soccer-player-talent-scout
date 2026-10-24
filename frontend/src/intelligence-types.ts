import type { Mode, Role } from './types';
export interface Scope { workspace_id: string; mode: Mode; season: string; age_group: string; competition: string; role: Role | 'all_roles' }
export interface Tendency { kind: string; attempts: number; successes: number; distinct_matches: number; observed_rate: number | null; posterior_mean: number | null; credible_low: number | null; credible_high: number | null; interpretation: string }
export interface Knowledge {
  player_id: string; scope: Scope; as_of: string; method_version: string; evidence_fingerprint: string; predictive_model_status: string;
  roster_match_count: number; reviewed_match_count: number; approved_event_count: number; limitations: string[]; tendencies: Tendency[];
  confirmed_identity_ids: string[]; unresolved_identity_ids: string[];
  match_details: { match_id: string; match_date: string; appearance_role: Role; jersey_number: number | null; approved_event_ids: string[]; approved_event_count: number; cumulative_reviewed_events: number; cumulative_pass_attempts: number; cumulative_pass_successes: number; observed_pass_completion: number | null }[];
  relationship_details: { other_player_id: string; relation: string; match_ids: string[]; event_ids: string[]; match_count: number; direct_event_count: number; explicit_contest_count: number | null; interpretation: string }[];
  role_segments: { appearance_role: Role; roster_matches: number; reviewed_matches: number; coverage_fraction: number | null; tendencies: Tendency[] }[];
  exclusion_reasons: Record<string, string>;
}
export interface Identity { id: string; upload_id: string; track_id: string; start_seconds: number; end_seconds: number; player_id: string; status: 'proposed' | 'confirmed' | 'rejected'; revision: number; reason: string; reviewed_by: string | null; reviewed_at: string | null }
export interface Observation { id: string; track_id: string; timestamp_seconds: number; label: string; confidence: number; processing_chunk_id: string | null; bbox: [number, number, number, number]; model_version: string }
export interface Page<T> { items: T[]; next_cursor: string | null }
export interface Coverage { status: string; source_duration_seconds: number | null; covered_seconds: number; planned_chunks: number; completed_chunks: number; failed_chunks: number; sample_interval_seconds: number | null; sampled_frames: number; observation_count: number; interpretation: string }
export interface Chunk { id: string; sequence: number; start_seconds: number; end_seconds: number; status: string; sampled_frames: number; observation_count: number; attempts: number }
export interface DuelReport {
 scope: Scope; method_version: string; validation_status: string; evidence_fingerprint: string; interpretation: string; uncertainty_caveat: string; accepted_contests: number; limitations: string[];
 policy: { prior_standard_deviation: number; min_contests: number; min_matches: number; min_distinct_opponents: number; max_match_share: number };
 ratings: { player_id: string; component_id: string | null; contests: number; matches: number; distinct_opponents: number; log_odds: number | null; interval_low: number | null; interval_high: number | null; component_rank: number | null; eligibility: string; reasons: string[] }[];
 player_details: { player_id: string; wins: number; losses: number; event_ids: string[]; match_ids: string[]; opponent_ids: string[]; largest_match_share: number | null }[];
 contests: { match_id: string; contest_id: string; player_ids: string[]; winner_id: string; loser_id: string; event_ids: string[] }[];
 exclusions: { event_id: string; reasons: string[] }[];
}
export interface Pairwise { status: string; eligibility: string; win_probability: number | null; interval_low: number | null; interval_high: number | null; direct_contests: number | null; comparison_basis: string; reasons: string[]; interpretation: string }
export interface ActionValue { event_id: string; status: string; value: number | null; method_version: string; reasons: string[]; interpretation: string; start_cell_actions?: number | null; end_cell_actions?: number | null; training_matches?: number | null; limitations?: string[] }
