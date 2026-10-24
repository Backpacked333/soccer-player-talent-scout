/** Shared wire contracts. Keep synchronized with backend/app/schemas.py and docs/API.md.
 * IDs are UUID strings, datetimes are UTC ISO strings, video times are seconds.
 * Synthetic records never belong in real-mode analytics.
 */
export type Id = string;
export type Mode = 'real' | 'demo';
export type Role = 'goalkeeper' | 'defender' | 'midfielder' | 'forward' | 'unknown';
export type EventKind = 'pass' | 'cross' | 'shot' | 'tackle' | 'interception' | 'recovery' | 'carry' | 'duel' | 'block' | 'header';
export type Outcome = 'successful' | 'unsuccessful' | 'unknown';
export type ReviewStatus = 'draft' | 'approved' | 'rejected';
export type JobStatus = 'queued' | 'running' | 'needs_configuration' | 'needs_review' | 'completed' | 'failed';
export type IntegrationStatus = 'ready' | 'optional_not_installed' | 'requires_model_configuration' | 'license_blocked';
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export const ROLES: readonly Role[] = ['goalkeeper', 'defender', 'midfielder', 'forward', 'unknown'];
export const EVENT_KINDS: readonly EventKind[] = ['pass', 'cross', 'shot', 'tackle', 'interception', 'recovery', 'carry', 'duel', 'block', 'header'];
export const SYNTHETIC_NOTICE = 'Synthetic demonstration — not real player analysis';

export interface LoginRequest {
  email: string;
  password: string;
}
export interface SignupRequest extends LoginRequest {
  workspace_name: string;
}
export interface AccountRead {
  id: Id;
  email: string;
  workspace_id: Id;
}
export interface WorkspaceRead {
  id: Id;
  name: string;
}
export interface SessionResponse {
  account: AccountRead;
  workspace: WorkspaceRead;
  csrf_token: string;
}

export interface PlayerCreate {
  display_name: string;
  age_group: string;
  primary_role?: Role;
  team_name?: string;
  notes?: string;
}
export interface PlayerRead extends Required<PlayerCreate> {
  id: Id;
  workspace_id: Id;
  is_synthetic: boolean;
  created_at: string;
}
export interface MatchCreate {
  title: string;
  match_date: string;
  competition: string;
  season: string;
  age_group: string;
  team_name: string;
  opponent_name: string;
  duration_seconds?: number;
}
export interface MatchRead extends Required<MatchCreate> {
  id: Id;
  workspace_id: Id;
  is_synthetic: boolean;
  created_at: string;
}
export interface AppearanceCreate {
  player_id: Id;
  jersey_number?: number | null;
  side?: 'home' | 'away';
  role?: Role;
  played_seconds?: number;
  reviewed_seconds?: number;
  notes?: string;
}
export interface AppearanceRead extends Required<AppearanceCreate> {
  id: Id;
  workspace_id: Id;
  match_id: Id;
  is_synthetic: boolean;
}
export interface RosterWrite {
  appearances: AppearanceCreate[];
}
export interface AttestationCreate {
  permission_to_process: boolean;
  guardian_or_club_authorization: boolean;
  opponent_privacy_review: boolean;
  authorized_roster: boolean;
  retention_days?: number;
  notes?: string;
}
export interface AttestationRead extends Required<AttestationCreate> {
  id: Id;
  workspace_id: Id;
  match_id: Id;
  account_id: Id;
  is_synthetic: boolean;
  created_at: string;
}

export interface UploadRead {
  id: Id;
  workspace_id: Id;
  match_id: Id;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  status: 'stored' | 'probed' | 'rejected';
  is_synthetic: boolean;
  created_at: string;
}
export interface JobRead {
  processing_coverage?: import('./intelligence-types').Coverage;
  id: Id;
  workspace_id: Id;
  match_id: Id;
  upload_id: Id;
  status: JobStatus;
  stage: string;
  progress: number;
  message: string;
  attempts: number;
  result: Record<string, JsonValue>;
  is_synthetic: boolean;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
}
export interface UploadResponse {
  upload: UploadRead;
  job: JobRead;
}

export interface EventSemantics {
  contest_id?: string | null; period?: number | null;
  coordinate_system?: 'unknown' | 'attack_normalized' | 'pitch_normalized';
  coordinate_provenance?: string | null; attacking_direction?: 'left_to_right' | 'right_to_left' | null;
  possession_retained?: boolean | null; shot_outcome?: 'goal' | 'no_goal' | null;
}
export interface EventWrite extends EventSemantics {
  player_id: Id;
  kind: EventKind;
  outcome?: Outcome;
  timestamp_seconds: number;
  end_timestamp_seconds?: number | null;
  status?: ReviewStatus;
  opponent_player_id?: Id | null;
  start_x?: number | null;
  start_y?: number | null;
  end_x?: number | null;
  end_y?: number | null;
  notes?: string;
}
export interface EventRead extends Required<Omit<EventWrite, keyof EventSemantics>>, EventSemantics {
  source_track_id?: string | null; identity_assignment_id?: string | null; identity_assignment_revision?: number | null;
  id: Id;
  workspace_id: Id;
  match_id: Id;
  provenance: 'manual' | 'model_candidate' | 'synthetic';
  confidence: number | null;
  model_version: string | null;
  created_by: Id;
  reviewed_by: Id | null;
  reviewed_at: string | null;
  is_synthetic: boolean;
  created_at: string;
  updated_at: string;
}
export interface AuditRead {
  id: Id;
  workspace_id: Id;
  account_id: Id;
  match_id: Id | null;
  entity_type: string;
  entity_id: string;
  action: string;
  before: Record<string, JsonValue> | null;
  after: Record<string, JsonValue> | null;
  reason: string;
  is_synthetic: boolean;
  created_at: string;
}

export interface Cohort {
  age_group: string;
  role: Role;
  competition: string;
  season: string | null;
}
export interface EligibilityPolicy {
  min_matches: number;
  min_pass_attempts: number;
  min_reviewed_seconds: number;
  min_coverage_fraction: number;
  ranking_metric: 'pass_completion_pct' | 'pass_completion_posterior_lower_95';
  min_duel_attempts?: number;
  max_match_opportunity_share?: number;
  prior_alpha?: number;
  prior_beta?: number;
  credible_mass?: number;
  methodology_version?: string;
  description: string;
}

/** Additive evidence model from app.analytics; absent on the original v1 response. */
export interface RateEstimate {
  metric: 'pass_completion' | 'duel_success';
  attempts: number;
  successes: number;
  failures: number;
  relevant_match_count: number;
  max_match_opportunity_share: number | null;
  observed_pct: number | null;
  posterior_mean_pct: number | null;
  credible_low_pct: number | null;
  credible_high_pct: number | null;
  credible_mass: number;
  prior_alpha: number;
  prior_beta: number;
  posterior_alpha: number | null;
  posterior_beta: number | null;
  reviewed_seconds: number;
  coverage_fraction: number | null;
  observed_attempts_per_90_reviewed_minutes: number | null;
  eligibility: 'unranked' | 'provisional' | 'eligible';
  confidence: 'insufficient' | 'limited' | 'moderate';
  eligibility_reasons: string[];
  methodology_version: string;
  interpretation: string;
}

export interface OpponentBreakdown {
  actor_player_id: Id;
  opponent_player_id: Id;
  kind: EventKind;
  cohort: Cohort;
  is_synthetic: boolean;
  approved_event_count: number;
  match_count: number;
  successful: number;
  unsuccessful: number;
  observed_success_pct: number;
  posterior_mean_pct: number;
  credible_low_pct: number;
  credible_high_pct: number;
  prior_alpha: number;
  prior_beta: number;
  credible_mass: number;
  reviewed_seconds: null;
  coverage_fraction: null;
  eligibility: 'unranked';
  confidence: 'insufficient';
  rank: null;
  comparison_score: null;
  event_ids: Id[];
  methodology_version: string;
  eligibility_reasons: string[];
}

export interface MetricSummary {
  player_id: Id;
  cohort: Cohort;
  is_synthetic: boolean;
  match_count: number;
  approved_event_count: number;
  pass_attempts: number;
  successful_passes: number;
  pass_completion_pct: number | null;
  played_seconds: number;
  reviewed_seconds: number;
  coverage_fraction: number | null;
  confidence: 'insufficient' | 'limited' | 'moderate';
  eligibility: 'unranked' | 'provisional' | 'eligible';
  eligibility_reasons: string[];
  rank: number | null;
  methodology_version?: string;
  policy?: EligibilityPolicy;
  pass_rate?: RateEstimate;
  duel_rate?: RateEstimate;
  observed_events?: Record<string, { attempts: number; successful: number; unsuccessful: number }>;
  comparison_score?: number | null;
  comparison_score_label?: string;
  exposure_source?: string;
  evidence_gaps?: string[];
  opponent_breakdowns?: OpponentBreakdown[];
}
export interface LeaderboardEntry extends MetricSummary {
  display_name: string;
  team_name: string;
}
export interface LeaderboardResponse {
  cohort: Cohort;
  mode: Mode;
  is_synthetic: boolean;
  policy: EligibilityPolicy;
  entries: LeaderboardEntry[];
  methodology_version?: string;
  unavailable_metrics: string[];
  disclaimer: string;
}
export interface PlayerProfile {
  player: PlayerRead;
  metrics: MetricSummary[];
  policy?: EligibilityPolicy;
  methodology_version?: string;
  timeline: EventRead[];
  unavailable_metrics: string[];
  disclaimer: string;
}
export interface IntegrationRead {
  id: string;
  name: string;
  status: IntegrationStatus;
  license: string;
  purpose: string;
  detail: string;
  source_url: string | null;
}
export interface DemoSeedResponse {
  players_created: number;
  matches_created: number;
  message: string;
}
export interface WorkspaceExport {
  schema_version: '1.0';
  exported_at: string;
  mode: Mode;
  synthetic_notice: string | null;
  workspace: WorkspaceRead;
  players: PlayerRead[];
  matches: MatchRead[];
  appearances: AppearanceRead[];
  events: EventRead[];
  attestations: AttestationRead[];
}
export interface VideoMetadata {
  duration_seconds: number;
  width: number;
  height: number;
  fps: number;
  codec: string;
}
export interface ApiError {
  detail: { code: string; message: string } | { loc: (string | number)[]; msg: string; type: string }[];
}
