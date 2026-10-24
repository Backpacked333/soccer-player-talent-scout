# Implementation ownership and executable contracts

## Integrated scope and original ownership

The integrated product now includes HTTP handlers, private uploads/playback, durable metadata jobs, approved-event analytics, React screens and executable shell/API tests. See the root README for validated commands and limits. Optional patched CPU detection is disabled by default and requires operator-reviewed local weights/configuration (FULL_MATCH.md); unrelated research references are not installed capabilities. Current reviewed identity/memory, explicit-duel estimates and optional spatial action values are integrated (ALGORITHMS.md). The ownership table below records the original collaboration split, not outstanding missing modules. An empty account remains empty until its operator adds records or explicitly seeds synthetic data.

| Owner | Files / responsibility |
|---|---|
| Foundation | Root README, `.gitignore`, `.env.example`, `package.json`, `Makefile`; `backend/pyproject.toml`, lockfile initially; `backend/app/{__init__,config,db,models,schemas}.py`; `frontend/{package.json,package-lock.json,tsconfig*.json,vite.config.ts}` initially; `frontend/src/types.ts`; this document and `API.md` |
| API/security agent | `backend/app/{main,auth,api,demo}.py` or focused route modules, API/security tests; **backend pyproject/uv.lock after foundation handoff**; authorized streaming/upload/export, data CRUD, origin/CSRF and session isolation |
| Vision/worker agent | `backend/app/{vision,worker,analytics}.py`, dedicated pipeline/analytics tests; `backend/requirements-vision.txt` only for optional dependencies; integration registry; durable job claims and safe bounded media probe |
| Frontend agent | React application, CSS, `frontend/index.html`, UI tests if any; **frontend package.json/package-lock.json after foundation handoff**; consume shared types and exact API |
| Parent/integration agent | Reconcile interfaces, end-to-end shell checks, migrations/security gaps; coordinate changes to foundational models/schemas/types rather than silently forking names |

All agents share this working tree. No branch changes, commits, pushes, PRs, deletion of others' work, server restarts, external account provisioning, paid compute, model downloads or browser testing are authorized.

## Run targets

- Python 3.11. `make setup` uses the frozen `backend/uv.lock` and `npm ci`; use `BACKEND_EXTRAS="--extra dev --extra postgres"` consistently for the optional PostgreSQL driver.
- Schema setup: `cd backend && uv run python -m app.db`. Current schema version is 3, with explicit additive v1/v2 migration paths and version checks. SQLAlchemy's `create_all` alone is not a migration engine; back up and test private persistent upgrades.
- API: `cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`.
- Separate durable worker: `cd backend && uv run python -m app.worker`; it must poll the same database/storage, not depend on an in-process background task. Add `--once` for isolated tests and operational one-shot checks.
- Frontend: `cd frontend && npm ci && npm run dev -- --host 0.0.0.0 --port 5173`; Vite proxies `/api` to port 8000, including cookies and authorized video Range requests. `npm run build` typechecks and builds `frontend/dist`.
- Root `make setup`, `make db`, `make api`, `make worker`, `make web`, `make check` wrap the above. Run long-lived commands separately. `make check` runs Python/frontend lint, type checks, tests and build. Local preview is not production; detector, migrations and safeguarding limits are documented in the README.

## Shared persistence

`app.db` exports `Base`, `get_engine()`, `get_session_factory()`, `get_db()` (FastAPI dependency generator), `session_scope()` (committing context manager), `init_db()` and `utcnow()`. `get_engine`/`get_session_factory` accept an optional explicit database URL for isolated tests. `init_db(database_url=None)` initializes version 3, explicitly migrates supported older versions and checks the stored version. Use SQLAlchemy 2 synchronous `Session`, `select` and `update`; no async engine. `get_db` closes but does **not** auto-commit: route handlers commit explicitly. SQLite uses foreign keys, WAL and a busy timeout; its configured connection timeout is finite. PostgreSQL uses `postgresql+psycopg://...`.

`app.models` exports: `Workspace`, `Account`, `AuthSession`, `Player`, `Match`, `Appearance`, `RightsAttestation`, `Upload`, `ProcessingJob`, `ProcessingChunk`, `TrackObservation`, `TrackIdentityAssignment`, `ReviewedEvent`, `AuditLog`, `SchemaVersion`. Primary keys are UUID strings except the session token digest and schema singleton. Every domain row carries `workspace_id`; this is an authorization boundary, **not** a substitute for scoped queries. IDs in bodies require the same tenant checks as IDs in paths. SQLite/PostgreSQL foreign keys validate parent existence, while handler/worker validation must additionally enforce matching tenant, mode, roster and match. Do not query by a bare ID and mutate before checking its workspace.

`Upload.storage_key` is an application-generated relative basename, never an API field. Only the upload owner can access private media. `ProcessingJob` persists status, progress, attempts, timestamps, `lease_expires_at`, `worker_id` and `result_json`. `ReviewedEvent` persists source, actor, review attribution and source seconds. `AuditLog` stores privacy-conscious before/after JSON (`before_json`, `after_json`) and safe correction rationale; no credentials. Optional anonymous track observations are paginated source evidence, not automatic player analysis or pitch coordinates; reviewed identity assignments carry canonical attribution.

Model field names mirror schema fields except:
- `ProcessingJob.result_json` is serialized as public `JobRead.result` after allowlisting;
- `AuditLog.before_json`/`after_json` are serialized as `AuditRead.before`/`after`;
- ORM `Match`, `Player` etc. include internal fields omitted by public read schemas;
- `AuthSession.token_hash` is SHA-256 of the raw cookie, never the cookie itself.

Timezone note: database datetimes are UTC; SQLite may return naive datetimes. Normalize to UTC before comparisons to aware values (especially session expiry and worker leases). Requests never accept client-provided workspace IDs, synthetic flags, provenance or reviewers.

## Configuration contract

`app.config` exports `Settings`, `get_settings()` and `ensure_local_secret(settings)`. Environment prefix is `SCOUT_`; see `.env.example`. `Settings` exposes `environment`, `database_url`, `storage_root` (Path), `allowed_origins` (list[str]), `secret_key` (optional), `cookie_secure`, `session_ttl_hours`, `max_upload_bytes`, `max_video_seconds`, `max_video_dimension`, `probe_timeout_seconds`, `ffprobe_binary`, `worker_poll_seconds`, `worker_lease_seconds`, `max_job_attempts` and `allow_signup`. `ensure_local_secret` returns configured secret or a securely created private local file; production must supply a strong secret and secure cookies/exact HTTPS origins. Do not log it or check it into git. Configuration creates no network requests. `SCOUT_ALLOWED_ORIGINS` is a JSON array, not a comma string.

## Worker ↔ vision contract (vision/worker owner)

Implemented in `app.vision` (`get_integrations` is re-exported from `app.integrations`):

```python
from pathlib import Path
from app.config import Settings
from app.schemas import VisionResult, IntegrationRead

def analyze_video(video_path: Path, *, settings: Settings) -> VisionResult: ...
def get_integrations(settings: Settings) -> list[IntegrationRead]: ...
```

`VisionResult` is defined in shared schemas: status `needs_configuration|needs_review|completed`, stage, message, metadata (`VideoMetadata`: duration_seconds, width, height, fps, codec), candidates (list of `CandidateEvent`, default empty), observations (list of `ObservationWrite`, default empty), warnings (list[str]). In baseline mode a successful probe returns `needs_configuration`, stage `metadata_ready`, real probed metadata, zero candidates and zero observations. Never manufacture events/coordinates/metrics. A future configured adapter may return candidates but never approved events. `CandidateEvent` includes roster `player_id` only if actually resolved; a null actor remains unresolved and cannot enter approved metrics. Worker persists only resolved, owned roster candidates as draft `ReviewedEvent`; unresolved candidates stay in safe job review output. Observation track IDs are match-local; they are not persistent identities.

`app.worker` implements `run_once(settings: Settings | None = None) -> bool` (true when a job was claimed), and a CLI `--once`. Atomically claim one queued job with a conditional update; set worker ID and lease, increment attempts and commit before processing. Handle abandoned leases with bounded retries; do not allow two workers to process one live lease. On every update, ensure claimant/lease ownership and tenant/mode integrity. Use isolated sessions per claim/result. Map expected media failures to safe `failed` status, never traceback text in `message`. Store successful metadata on `Upload`, result under `ProcessingJob.result_json`; filter internal paths before serializing. Lease must exceed bounded probe runtime or renew it. Do not commit fake approved events or mark metadata-only processing analyzed.

## Analytics contract (vision/analytics owner)

Implemented in `app.analytics` using canonical shared schemas and compatibility aliases:

```python
from sqlalchemy.orm import Session
from app.schemas import PlayerProfile, LeaderboardResponse

def player_profile(db: Session, workspace_id: str, player_id: str) -> PlayerProfile | None: ...
def leaderboard(db: Session, workspace_id: str, *, age_group: str,
                role: str, competition: str, season: str | None = None,
                mode: str = "real") -> LeaderboardResponse: ...
```

The exact JSON shapes live in `schemas.py` / `types.ts`. Approved-only events, explicit opportunities, reviewer-declared coverage and actual match/appearance cohorts are mandatory. The published policy uses >=3 matches, >=30 pass opportunities (20 for duels), >=1800 reviewed seconds, >=0.6 coverage and <=0.6 single-match opportunity share. Beta(1,1) regularization and 95% credible endpoints are disclosed; eligible pass ranking uses the lower endpoint, not raw completion. These are engineering thresholds, not validated talent science. Below threshold: rank null. Recompute from live evidence after correction; synthetic mode is disjoint from real. No automatic opponent adjustment, xG, xT, VAEP, speed/distance, overall talent prediction or hosted LLM statistics.

## API ↔ frontend contract

Use `docs/API.md`, Pydantic schemas and TypeScript interfaces, not inferred endpoint names. Frontend uses relative `/api`, `credentials: 'include'`, and sends the auth response's CSRF token on every unsafe authenticated request. Persist no authentication token in localStorage. Recover session using `/auth/me`; an empty account gets an empty workspace UI. Select real/demo mode visibly. Handle polling `needs_configuration` as an honest actionable terminal state, not fake success. Access video only at `/api/uploads/{id}/video`; source event timestamps seek into that video. All sensitive paths remain authenticated even when UI navigation is hidden.

## Acceptance checks and honest boundaries

Run shell-based Python tests, compile/type checks, API integration tests with isolated temporary SQLite/storage, and frontend build. Exercise two-account isolation for lists, direct IDs, roster assignments, event actors, exports, job reads and video reads; test Origin/CSRF, upload path/size limits and approval requirements. Test worker recovery and no-configuration results. Test rankings with small samples and mode isolation. No demo data is real analysis. No browser/UI testing is authorized in this task. Retention expiry blocks access/new review but automated deletion is not implemented. Production migrations, shared abuse controls, validated ML models and jurisdiction-specific permissions still need implementation/review. RF-DETR's installable vulnerable recipe was removed; its registry and public adapter are unconditionally held, regardless of operator flags (see INTEGRATIONS.md).
