# Scoutroom API contract — v1

This file documents the implemented v1 HTTP contracts; code and regression tests are authoritative. See `IMPLEMENTATION.md` for internal module contracts. Python `app.schemas` and `frontend/src/types.ts` define the exact wire fields.

## Transport, security, and conventions

- API prefix `/api`; backend `127.0.0.1:8000`; Vite defaults to `127.0.0.1:5173` and proxies `/api` to the backend. A production reverse proxy should serve `frontend/dist` and private `/api` on one TLS origin; no production deployment or API static shell is bundled. **Never mount upload storage as static files**.
- JSON keys are snake_case; IDs are opaque UUID strings; UTC datetimes are ISO 8601; match dates are `YYYY-MM-DD`; video timestamps and durations are seconds (floating point), relative to the uploaded file, not a wall clock or broadcast clock. Image boxes are normalized `[x1,y1,x2,y2]`; pitch coordinates require explicit calibration and otherwise remain null.
- One account owns one private workspace in v1. Every lookup, relationship assignment, list, mutation, export and video read is scoped to the authenticated account's `workspace_id`. Return 404 for another workspace's identifier. No public player endpoints. Do not infer identity across matches: link `player_id` through the authorized roster.
- Session auth: opaque random cookie `scout_session`, HttpOnly, SameSite=Lax, Secure in production. Store only its SHA-256 digest in `AuthSession.token_hash`. `AuthSession.csrf_token` is a separate random non-authenticating token returned by auth responses. Every authenticated unsafe request includes `X-CSRF-Token`; compare in constant time. Signup/login must validate Origin too. Browser unsafe requests require an allowlisted Origin; reject missing Origin unless explicitly documented non-browser/test handling supplies equivalent protection. CORS allows exact configured origins with credentials, never `*`.
- `GET /auth/me` returns 401 when signed out; session responses are `Cache-Control: no-store`. Never return password hashes, session digests, filesystem paths, raw exception traces or sensitive environment information.
- Error body: `{"detail":{"code":"validation_error","message":"Safe human-readable explanation"}}`. Validation errors may use FastAPI's standard 422 detail list. API handlers should map operational errors to safe messages.
- Most record lists return JSON arrays; full-match chunk/observation pages return `{items,next_cursor}`. `mode=real|demo` defaults to `real` on collection/analytics reads. Real mode excludes `is_synthetic=true`; demo mode includes only synthetic records. Synthetic flag is server-controlled, immutable and returned on records and analytics. Relations must never mix real and demo records. All demo screens and exports must visibly say **Synthetic demonstration — not real player analysis**.
- Normal creates return 201, reads/updates 200, deletions 204. IDs are generated on the server. Request bodies reject unknown keys. General collection pagination is deferred; source observations/chunks use bounded cursor pages. Other lists and all processing resources are capped.

## Authentication

| Method | Path under `/api` | Body | Response |
|---|---|---|---|
| GET | `/health` | — | `{ "status": "ok" }` (no sensitive configuration) |
| POST | `/auth/signup` | `SignupRequest`: email, password (12–128 chars), workspace_name | `SessionResponse`; sets cookie |
| POST | `/auth/login` | `LoginRequest`: email, password | `SessionResponse`; sets rotated cookie |
| GET | `/auth/me` | — | `SessionResponse` |
| POST | `/auth/logout` | — | 204, revokes session and clears cookie |

`SessionResponse = { account: {id,email,workspace_id}, workspace: {id,name}, csrf_token }`.
Use a password KDF (stdlib scrypt is sufficient), generic login errors, request throttling, bounded credentials and finite session lifetime. Account signup creates an empty workspace: no demo data is silently added.

## Players, matches and roster

| Method | Path | Body / query | Response |
|---|---|---|---|
| GET / POST | `/players` | GET: mode; POST: `PlayerCreate` | `PlayerRead[]` / `PlayerRead` |
| GET / PUT / DELETE | `/players/{player_id}` | PUT: full `PlayerCreate` | `PlayerRead` / 204; reject destructive deletion when linked to match evidence |
| GET | `/players/{player_id}/profile` | — | `PlayerProfile` with approved-event metrics and timeline |
| GET / POST | `/matches` | GET: mode; POST: `MatchCreate` | `MatchRead[]` / `MatchRead` |
| GET / PUT / DELETE | `/matches/{match_id}` | PUT: full `MatchCreate` | `MatchRead` / 204; safely cascade authorized evidence and remove private file on match delete |
| GET / PUT | `/matches/{match_id}/roster` | PUT: `RosterWrite` | `AppearanceRead[]` |
| GET / POST | `/matches/{match_id}/attestation` | POST: `AttestationCreate` | `AttestationRead` (GET 404 if absent) |

Match deletion returns 409 while a worker has a live lease. Expired or missing leases do not block privacy deletion; delayed worker results are fenced and cannot recreate deleted evidence. File cleanup failures return an actionable error, not a false success.

`PlayerCreate`: display_name (roster label, no birthdate), age_group (e.g. U15), primary_role (`goalkeeper|defender|midfielder|forward|unknown`), team_name, notes. `PlayerRead` adds id, workspace_id, is_synthetic, created_at.

`MatchCreate`: title, match_date, competition, season, age_group, team_name, opponent_name, duration_seconds (planned match length, default 5400). `MatchRead` adds id, workspace_id, is_synthetic, created_at. Video duration belongs to the upload and is distinct from planned match duration.

`RosterWrite = { appearances: AppearanceCreate[] }`. Each appearance: player_id, jersey_number (0–99, optional), side (`home|away`), role, played_seconds, reviewed_seconds, notes. Reviewed seconds must not exceed played seconds; both must not exceed planned match duration. This is **reviewer-declared coverage**, not inferred full-match visibility. API verifies ownership, player age group, synthetic mode, uniqueness, and disallows removal of an actor referenced by an event. One player has one appearance per match. Cross-match identity comes from the same player ID. `AppearanceRead` adds id, match_id, workspace_id, is_synthetic. Replacing roster or coverage records must be audited. Raw tracking does not automatically populate reviewed coverage.

`AttestationCreate`: permission_to_process, guardian_or_club_authorization, opponent_privacy_review, authorized_roster (all must be true), retention_days (1–365; default 30), notes. Attestation is an explicit operator declaration, **not legal verification**. API persists who attested and when and requires an existing authorized roster before upload. `AttestationRead` adds id, match_id, account_id, workspace_id, is_synthetic, created_at. Retention is measured from upload creation plus retention_days. Expiry blocks private playback, processing/retry and new or corrected real events; the review room disables playback/annotation accordingly. Match/evidence deletion remains available, but physical deletion is not automatic. Roster membership changes invalidate the declaration and require reconfirmation.

## Private uploads and durable processing

| Method | Path | Body / query | Response |
|---|---|---|---|
| POST | `/matches/{match_id}/upload` | multipart `file`; attestation and roster must already exist | `UploadResponse = {upload:UploadRead, job:JobRead}` |
| GET | `/matches/{match_id}/upload` | — | `UploadRead` (404 when absent) |
| GET | `/uploads/{upload_id}/video` | optional standard Range header | Authorized private bytes; correct media type; no-store; never a public path |
| GET | `/jobs` | mode | `JobRead[]` |
| GET | `/jobs/{job_id}` | — | `JobRead` |
| POST | `/jobs/{job_id}/retry` | — | `JobRead`; terminal failed/needs_configuration jobs; bounded attempts; metadata/completed chunks preserved |
| GET | `/jobs/{job_id}/chunks` | limit 1–100 (default 50), sequence cursor (default -1) | `ProcessingChunkPage`; source intervals, status and sample counts, no worker leases |
| GET | `/jobs/{job_id}/observations` | limit 1–500 (default 100), stable-ID cursor, optional track_id/chunk_id | `ObservationPage`; current authorized source, completed chunks only; rights/retention checked |

Only one current upload per match in v1; duplicate upload returns 409, not a silent replacement. Allow `.mp4`, `.mov`, `.webm` with matching accepted media type and validated media probe; original filename is display-only, never a storage path. Generate a private UUID storage key and resolve paths beneath configured storage root. Reject symlinks/path escape, oversized and empty files. Stream bounded chunks; delete partial files on failure. Local ffprobe/ffmpeg binaries are configured server-side only; no remote URLs, arbitrary commands, shell=True, or user-supplied executables. Reject unsupported stream layouts, durations or dimensions before expensive processing. Probe subprocesses have timeouts and a local-only protocol allowlist (for example ffprobe `-protocol_whitelist file,pipe`) so media cannot fetch remote nested resources.

`UploadRead`: id, match_id, workspace_id, original_filename, content_type, size_bytes, sha256, duration_seconds (null until probed), width, height, fps, status (`stored|probed|rejected`), is_synthetic, created_at. Storage key is private and is **not** in the response.

`JobRead`: id, match_id, upload_id, workspace_id, status (`queued|running|needs_configuration|needs_review|completed|failed`), stage, progress (0–1), message (safe), attempts, result (object; allowlisted non-sensitive result only), is_synthetic, created_at, updated_at, started_at, finished_at. A metadata probe is not analysis. Without configured validated inference, end in `needs_configuration`, with metadata and capability explanation, never `completed`/"analyzed". `needs_review` means candidates exist but are not approved. Completed means the configured job stage completed, not that a child has an objective talent score. Additive `processing_coverage` reports source duration, covered seconds, planned/completed/failed chunks, cadence, sampled frames and observations from the durable ledger. The ledger must cover the complete source contiguously before full coverage is claimed. This is detector coverage, not reviewed-event coverage. Whole-match observations are not inlined into the job response.

## Human event review and evidence

| Method | Path | Body / query | Response |
|---|---|---|---|
| GET / POST | `/matches/{match_id}/events` | GET: optional status; POST: `EventWrite` | `EventRead[]` / `EventRead` |
| PUT / DELETE | `/events/{event_id}` | PUT: full `EventWrite` | `EventRead` / 204 |
| GET | `/matches/{match_id}/audit` | — | `AuditRead[]` |

`EventWrite`: player_id (required actor on roster), kind (`pass|cross|shot|tackle|interception|recovery|carry|duel|block|header`), outcome (`successful|unsuccessful|unknown`), timestamp_seconds, end_timestamp_seconds (nullable, >= start), status (`draft|approved|rejected`, default draft), opponent_player_id (nullable, also on roster), start_x/start_y/end_x/end_y (nullable, normalized 0–1 pitch coordinates supplied by reviewer; paired), notes. Opponent cannot equal actor and must be on the opposing roster side. The review editor offers only opposing-side choices and clears the opponent when the actor changes. Approving requires known outcome, explicit actor and reviewer; API validates times against probed duration for uploaded real footage and planned duration for synthetic fixtures. Real reviewed evidence requires a valid uploaded/probed video; no fake events are generated by upload. Outcome is deliberately simple; do not infer pass recipients, goals or spatial value from it. Optional qualified semantics are `contest_id` (duels only), `period` (1–10), `coordinate_system` (`unknown|pitch_normalized|attack_normalized`), `coordinate_provenance`, `attacking_direction` (`left_to_right|right_to_left` or null), `possession_retained` (strict boolean or null) and `shot_outcome` (`goal|no_goal` or null, shots only). Fixed-reference pitch normalization requires period/direction/provenance; right-to-left rotates both axes. A successful shot is not a goal. Never label image boxes as pitch coordinates.

`EventRead` adds id, workspace_id, match_id, provenance (`manual|model_candidate|synthetic`), confidence (nullable 0–1 model score, not a validated accuracy estimate), model_version (nullable), created_by, reviewed_by (nullable), reviewed_at (nullable), is_synthetic, created_at, updated_at. `identity_assignment_id` and `identity_assignment_revision` are read-only linkage fields set through the audited identity endpoint. Provenance, confidence, creator and reviewer fields are server-controlled. PUT fully replaces editable fields and records before/after in an `AuditLog`. DELETE records a correction before removing the event; metrics are computed from current approved events and must reflect corrections immediately. Timestamps support `<video currentTime=...>` evidence seeking.

## Canonical identity, current memory and research models

All routes below require a private session; workspace is derived from the session, never from a client-supplied scope. Cohort queries require **season, age_group, competition**, with `mode=real|demo` (default real) and `role` (default `all_roles`). `as_of` on knowledge/duels is capped at today and excludes future matches. It means current-corrected evidence dated on/before that day, not a reconstruction of what reviewers knew then. Profile/leaderboard snapshots also exclude future evidence.

| Method | Path | Input / response |
|---|---|---|
| GET / POST | `/matches/{match_id}/identities` | List records / propose `TrackIdentityWrite`: upload_id, exact track_id, interval, canonical rostered player_id, reason; only reviewer source, null confidence, proposed status |
| POST | `/matches/{match_id}/identity-candidates` | Same source/interval input; authorized roster candidates only, status unresolved, no guessed probabilities |
| PUT | `/matches/{match_id}/identities/{assignment_id}` | `expected_revision`, player_id, status (`proposed|confirmed|rejected`), nonempty reason; stale/conflicting writes 409 |
| POST | `/events/{event_id}/identity` | assignment_id, expected_revision, reason; current actor and entire interval must agree; returns event to draft |
| GET | `/players/{player_id}/knowledge` | Explicit cohort + optional as_of; history, action counts/rates, role segments, teammate/opponent/direct-duel evidence, exclusions, fingerprint |
| GET | `/intelligence/duels` | Explicit cohort + optional as_of; full report with explicit contests, exclusions, components, gates and approximate uncertainty; optional player_id + opponent_player_id returns pairwise estimate/unavailable reason |
| GET | `/events/{event_id}/action-value` | Cohort is derived from the owned event's match; operator model only; concrete result preserves support/freshness diagnostics |
| GET | `/intelligence/snapshot` | Explicit cohort; bounded private `EvidenceSnapshot` JSON for operator offline fitting; includes excluded/rejected event state needed for provenance, not raw footage/paths |
| GET / PUT | `/matches/{match_id}/action-stream` | Audited `StreamDeclaration` for complete ordinary-action streams and separate training permission; authenticated declarer/time override body metadata |

Identity changes invalidate linked event approvals; they never silently move approval to another child. Correct the actor as a draft, bind the current confirmed identity revision, then explicitly reapprove. Unresolved ambiguity stays unresolved. Manual reviewed roster attribution can remain unlinked; model-candidate provenance requires confirmed current identity before it contributes. The audit endpoint preserves before/after decisions and reasons.

`StreamDeclaration` fields: coverage (`unknown|selected_highlights|complete_match`), eligible_actions_complete, both_sides_recorded, training_rights_cleared, rights_reference, source_reference, declared_by, declared_at. Complete streams require all eligible failed/successful ordinary actions for both sides and a source reference; model-training permission requires its own rights reference. This is an annotator declaration, not independently verified completeness/legal clearance. Source/rights/roster/context changes invalidate the declaration. No upload, model activation or retraining occurs through this route.

Direct-duel ratings only consume explicit reviewed contest IDs. Mirrored records count once; contradictory or ambiguous inverse annotations are excluded. Role-specific fits require both players in that role; `all_roles` admits cross-role evidence without claiming role adjustment. Disconnected components have no joint ranking/prediction. Provisional estimates remain unranked. See [OPPONENT_RATINGS.md](OPPONENT_RATINGS.md).

Spatial value is unavailable without the operator-only `SCOUT_ACTION_VALUE_ARTIFACT_PATH`. No request-selected filesystem path, URL or model upload exists. Strict bounded JSON import validates exact authorized scope before revealing metadata; scoring verifies all original training evidence is still current. Corrections/deletions invalidate stale artifacts. See [ALGORITHMS.md](ALGORITHMS.md) and [ACTION_VALUE.md](ACTION_VALUE.md).

## Transparent analytics and exports

| Method | Path | Query | Response |
|---|---|---|---|
| GET | `/leaderboard` | mode, **age_group**, **role**, **competition**, optional season | `LeaderboardResponse` |
| GET | `/integrations` | — | `IntegrationRead[]`; authenticated |
| POST | `/demo/seed` | — | `{players_created,matches_created,message}`; explicit, idempotent per workspace |
| GET | `/exports/workspace` | mode (default real) | JSON download `WorkspaceExport` (no cookies, passwords, storage paths or raw video) |

`MetricSummary` and `LeaderboardResponse` in shared schemas are the exact output contracts. Only approved events count. Display approved event count, distinct match count, relevant opportunities, played/reviewed seconds, coverage fraction (null if denominator zero), eligibility reasons, confidence label and cohort on **every score**. The comparison dimension is reviewed pass completion with a Beta(successes+1, failures+1) posterior. `comparison_score` is 100 times its lower 95% credible endpoint, not raw observed completion, talent, opponent adjustment, predicted potential, or a cross-role composite. The uniform prior is a disclosed mathematical regularizer, not a learned cohort mean. A conservative configurable policy gates ranking on sample size and reviewer-declared coverage; publish its actual thresholds in `policy`, mark below-threshold entries `provisional` or `unranked`, and set their rank to null. A ranking is within the requested age/role/competition/season cohort only; disconnected competitions must not be merged implicitly. No hosted LLM calculates statistics.

`PlayerProfile = {player, metrics:MetricSummary[], timeline:EventRead[], unavailable_metrics:string[], disclaimer:string, policy, methodology_version}`. Metrics are grouped by actual appearance role and match competition/season/age group; do not blend a player's cross-cohort observations into a single rank. `MetricSummary` includes player_id, cohort, is_synthetic, match_count, approved_event_count, pass_attempts, successful_passes, pass_completion_pct, played_seconds, reviewed_seconds, coverage_fraction, confidence (`insufficient|limited|moderate`), eligibility (`unranked|provisional|eligible`), eligibility_reasons, rank (nullable). Enriched fields include `pass_rate`, `duel_rate`, `comparison_score` (nullable), `evidence_gaps`, `opponent_breakdowns`, and `methodology_version`. `RateEstimate` publishes observed counts/rate, posterior mean and 95% bounds, prior parameters, match balance and eligibility. `LeaderboardResponse` includes policy, methodology, gaps and nullable ranks; shared Pydantic models preserve these fields during response/export validation. Defaults: 3 matches, 30 pass or 20 duel opportunities, 1,800 reviewed seconds, 60% coverage and at most 60% opportunities from one match. Null percentages mean no opportunities, not zero performance. Legacy profile rates are not opponent-adjusted. The separate knowledge, explicit-duel and action-value endpoints below expose actual research-engine outputs or explicit unavailable reasons. VAEP, xG, calibrated speed/distance, pressure and generic off-ball/talent scores remain unavailable.

`IntegrationRead`: id, name, status (`ready|optional_not_installed|requires_model_configuration|license_blocked`), license, purpose, detail, source_url. Registry must reflect detected installation/configuration, not imply all research components were imported. Manual review and local metadata probing may be ready after checks. Supervision/Sports/ByteTrack MIT, kloppy BSD-3-Clause and socceraction MIT are optional when absent. RF-DETR defaults to `requires_model_configuration`; opt-in readiness requires the patched CPU dependency set, operator-reviewed local checkpoint/rights/security flags and configuration described in FULL_MATCH.md. No weights are bundled/downloaded. The registry separately lists reviewed memory, direct-duel estimates and operator-gated spatial action value; readiness does not establish youth predictive accuracy. sn-gamestate GPL-3.0 is blocked pending adoption review (GPL is not inherently non-commercial); FOOTPASS blocked because Apache LICENSE conflicts with CC BY-NC README and NDA-gated data; SoccerMaster blocked pending code/dependency review, research-only; Qwen is optional explanations only and not installed by default. No automatic downloads.

`WorkspaceExport`: schema_version (`"1.0"`), exported_at, mode, synthetic_notice (nullable for real mode), workspace:{id,name}, players, matches, appearances, events, attestations. JSON excludes unreviewed raw model internals and privileged account/session data. Exports preserve source seconds and provenance.
