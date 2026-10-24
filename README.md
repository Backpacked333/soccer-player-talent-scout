# Scoutroom

A private youth soccer scouting workspace built around **reviewed evidence**, authorized rosters and transparent within-cohort comparisons. FastAPI/SQLAlchemy, a separate durable worker and React/TypeScript/Vite form a runnable local product. Uploading or probing video is **not** player analysis.

## What works now

- Private signup/login/session isolation, players, matches, authorized rosters and footage-rights declarations.
- Bounded private uploads, real local metadata probing, authenticated ranged video playback, persisted worker jobs with claims/leases/retries and safe errors.
- Manual draft/approve/reject/correct/delete event review tied to source-video seconds, reviewer attribution and correction audit history.
- Player profiles and cohort leaderboards recomputed from current approved evidence; posterior uncertainty, sample size, declared coverage, eligibility reasons and methodology are disclosed. Corrections persist and change statistics.
- Explicit, idempotent, private synthetic-demo seeding. Demo data never merges into real lists, cohorts or exports. JSON exports omit sessions, credentials and storage paths.
- Canonical track-to-roster identity review with reasons, revisions and audit history; current-corrected longitudinal player records and evidence-linked teammate/opponent relationships. Identity ambiguity remains unresolved, never guessed.
- Explicit-contest Bradley–Terry duel estimates, graph/sample gates and approximate uncertainty; optional operator-activated spatial Markov action values with strict scope/freshness/support checks. No fitted youth model is bundled.
- The default video job ends in **`needs_configuration`**, with metadata and **zero invented observations or events**. An opt-in patched CPU detector can process complete source spans through durable bounded chunks; anonymous tracklets still require reviewer-confirmed identity. See [FULL_MATCH.md](docs/FULL_MATCH.md). Detector coverage is never reviewed-event coverage.

No model weights, datasets, hosted API credentials, real minors' data or GPUs are bundled or required. No inference benchmark or youth-domain accuracy claim is made. There is no automatic soccer-event extraction, face recognition, cross-match visual identity, pitch calibration, xG/VAEP, universal opponent quality, speed/distance or universal talent rank. Spatial action values are unavailable unless an operator explicitly fits and activates a compatible authorized research artifact. More games update reviewed knowledge, not automatic detector retraining.

## Fresh local setup

### Login-free public demonstration

The separate static demo is built with `npm --prefix frontend run build:demo`. It uses only the explicitly fictional snapshot in `frontend/demo-public/scoutroom-demo.json`: four invented players and three authored 20-minute fixtures. Profiles, date-bounded histories, event filters, pass comparisons and provisional duel estimates are interactive; accounts, uploads, editing and the private API are not included in this bundle. No video is analyzed by the public demo.

Regenerate the snapshot from an isolated temporary database with `uv run --project backend --frozen --extra dev python -m app.demo_export --output frontend/demo-public/scoutroom-demo.json`. This command accepts no source database/workspace and uses the real seeded API/analytics engines; credentials, reviewer account IDs and private paths are removed. Its input is not real footage or personal data. Normal `npm --prefix frontend run build` builds the private application and does not copy the public demo JSON.

To publish only the static demo, build it, copy **only** `frontend/dist/` into a clean deployment directory, then apply `frontend/demo-vercel.json` as that directory's `vercel.json`. Do not deploy the repository root, private storage, database or API with these settings. The public deployment intentionally has no write endpoints.

### Private application

Requirements: Python **3.11**, recent `uv` (validated with **0.13.5**, supporting lock revision 5), Node **>=22.12** (validated with **24.22.1**), npm, GNU Make, and system **ffmpeg + ffprobe**. On Debian/Ubuntu the operator can install the system package with `sudo apt-get install ffmpeg`; use a currently patched OS package. No external credentials are needed.

From the repository root:

```sh
make setup             # frozen Python lock + npm ci; no models or app processes
make db                # bootstrap schema v3 or explicit additive v1/v2 migration
# If necessary: make setup PYTHON=/absolute/path/to/python3.11
```

If Node is installed through nvm, activate it first (`source ~/.nvm/nvm.sh` then `nvm use 24`). Default database: `.local/scout.db`. Private media: `.local/storage`. Both are git-ignored. Defaults work without `.env`; copy `.env.example` to root `.env` only for overrides. A private local random key is generated on API startup; no fixed development secret is shipped. Never put backend secrets in Vite variables.

Start three separate terminals, each at the repository root:

```sh
make api               # FastAPI, 127.0.0.1:8000
make worker            # separate durable DB-backed worker
make web               # Vite, 127.0.0.1:5173
```

Open **http://localhost:5173**. Vite proxies `/api`, including private media, to the loopback API. All frontend API URLs are relative and session cookies stay on the same origin. `make worker-once` processes at most one job and exits. The API does not run an in-process worker. Stopping/restarting the API/worker preserves the database, sessions, jobs, corrections and uploaded media.

### Walk through real evidence

1. Create an account; it starts with an empty private workspace. Create a player and match in the same age band/data mode.
2. Add the player to the authorized match roster. Declare played/reviewed seconds honestly; zero reviewed seconds is valid initially. These are human declarations, not measured tracking coverage.
3. Confirm each footage/guardian-or-club/opponent/roster authorization statement and a retention obligation. These are recorded declarations, not legal verification.
4. Upload a locally authorized MP4/MOV/WebM file. The UI shows transfer/probe/worker status; successful metadata is not labeled player analysis. The default worker settles at `needs_configuration`, not invented statistics; optional configured detection reports source coverage separately.
5. Open the video-centered evidence workspace. Its source-time scrubber, frame controls, sample boxes, selected image-space trail, event markers, and inspector expose stored detector/reviewer evidence. Select a track for its exact observation, model version, detector score and identity decision; use **Review track identity** to carry the source sample into the existing review form. Create a timestamped manual event and approve only verified actor/outcome evidence. Draft/rejected events do not count.
6. Select a known player in the review room to inspect their prior reviewed record/relationships. If anonymous tracks exist, inspect source observations and make a reasoned identity proposal/confirmation. Linking an event returns it to draft; correcting identity invalidates linked approvals and requires fresh attribution/review.
7. Open the player profile and matching age/role/competition/season leaderboard. A tiny sample is provisional and unranked. Explicit duel contest IDs enable narrow matchup evidence; all-roles admits cross-role duels but is not role adjustment. Correct events and inspect recomputed history/uncertainty. Source timestamps and evidence IDs support review.
8. Export private JSON if needed; delete matches/footage when the retention obligation requires. Synthetic demo seeding is a separate explicitly labeled action, not analysis of your upload.

## Validation and commands

```sh
make check             # Python Ruff + mypy; frontend ESLint + TypeScript;
                       # backend pytest; frontend Vitest; production Vite build
make audit             # pip-audit installed ordinary environment + npm audit
# Individual targets: lint, typecheck, test, build
# Focused real-video/private-persistence test:
uv run --project backend --frozen --extra dev pytest backend/tests/test_integrated_product.py -q
```

Tests use isolated temporary databases/storage and an actual FFmpeg-generated tiny video, never private local data. The integrated test runs the actual database CLI and separate worker CLI, signup → roster/rights → private upload → metadata job → approved event → profile/leaderboard → correction → recomputation → API restart persistence → deletion. It also checks unauthenticated/second-workspace media and record isolation. Frontend tests run in jsdom; **no browser-driven tests** are used.

The expanded suites cover three-match reviewed memory, identity correction/re-review, coherent snapshots, tenant/mode/as-of boundaries, explicit inverse-duel deduplication, artifact activation/staleness, source pagination/retry, and frontend identity/history/relationship/semantics controls. Synthetic trainer/evaluator CLI tests run offline and verify private artifacts and whole-match chronological holdouts. Optional Kloppy tests skip when its package is absent. Starlette's test client currently emits an httpx deprecation warning. Optional actual CPU/random-weight mechanics checks and audit caveats are documented in [FULL_MATCH.md](docs/FULL_MATCH.md); none establishes soccer accuracy or GPU readiness.

CI in `.github/workflows/ci.yml` performs locked setup, all quality gates and audits on Ubuntu with FFmpeg. `backend/pyproject.toml`/`backend/uv.lock` and `frontend/package.json`/`frontend/package-lock.json` pin dependencies. Root `npm run setup`, `dev`, `dev:api`, `dev:worker`, `db:init`, `lint`, `typecheck`, `test`, `build`, `check` and `audit` delegate to the same Makefile targets.

## What the video overlays mean

Overlays load a bounded, authorized source-time window rather than downloading a full match's observations. Playback uses the browser's presented-frame clock when available. Boxes and trails are transformed from raw decoded coordinates into the probed display orientation (including right-angle rotation and reflection); sample aspect ratio is included. The viewer checks the resulting aspect against browser video dimensions, hiding overlays for unknown, unsupported or mismatched geometry. For legacy jobs, **Revalidate source geometry** verifies the original private file's hash and metadata and refreshes its geometry without reprocessing or deleting observations, identities or review history. Unsupported transforms remain hidden. A selected trail connects only observed points from the same track, class and chunk, breaks on large gaps, and is **not** calibrated pitch movement. Frame stepping uses the reported average FPS; variable-frame-rate sources can have irregular frame spacing.

Recent boxes may be held for at most the sampling interval or 0.5 seconds, whichever is smaller, with an explicit age and dashed styling. Disable **Hold recent sample** for sampled-frame-only inspection. There is no interpolation or off-screen prediction. Missing boxes, errors, seeking, incomplete response windows and unresolved identities must never become invented positions or player records. Names require a unique, reviewer-confirmed same-upload identity at the observation's timestamp; otherwise tracks remain anonymous.

The timeline separates sampled processing segments from approved/draft/rejected event markers. The Models inspector distinguishes source probing, detection/tracking, human identity review and reviewed statistics. Automatic event extraction, possession, speed/distance, calibrated movement and automatic jersey identification remain unavailable. This is an interactive display of persisted outputs, not a live inference feed or a burned-in video export. The public fictional demo contains no private footage and does not expose this private video endpoint.

## What the comparison means

Methodology **`reviewed-actions-beta-v1.0.0`** uses Beta(successes + 1, failures + 1) for pass/duel rates with a disclosed uniform mathematical regularizer, **not** a learned cohort average. Raw completion percentages remain visible. Eligible pass comparison orders by the **lower endpoint of the 95% posterior credible interval**, not raw completion or predicted talent. Default pass eligibility requires at least 3 matches, 30 pass opportunities, 1,800 reviewed seconds, 60% reviewer-declared coverage, no single match contributing over 60% of opportunities, and a known role. Duel eligibility separately requires 20 duel opportunities.

Every cohort is age group + actual appearance role + competition + season; historical match cohorts are not rewritten by a player's current age/primary role. Thresholds and methodology are returned in `policy`. Below-threshold rows have null rank/comparison score. Legacy pass comparisons and directional outcome breakdowns remain descriptive. The separate explicit-duel panel fits only reviewed, deduplicated contest outcomes; it never adjusts pass completion or invents opponent quality from match results. Credible intervals reflect the stated count model, not annotation bias, observation dependence or calibrated talent certainty. Selective clips and subjective coverage can bias any comparison; this is decision support, not an autonomous selection system.

## Privacy and safeguarding

- Each account has one private workspace; API authorization checks every record and relationship. Other-workspace IDs return 404. Sensitive routes require sessions; health/auth are the intentionally public operational endpoints.
- Opaque HttpOnly cookies, server-side hashed session tokens, finite expiry, exact-Origin checks and separate CSRF tokens protect writes. No auth tokens live in localStorage. Local HTTP cookies are only for loopback development.
- Media is private API storage, never static/public. Names are not storage paths. File size/duration/dimensions, multipart bodies, path traversal/symlinks and probe/worker execution are bounded. No user-supplied executables or remote media URLs are accepted.
- Roster IDs establish identity, not face recognition. Prefer pseudonyms; avoid dates of birth/contact data or unnecessary identifying notes. The operator is responsible for permission, opponent privacy, safeguarding and lawful processing.
- Retention expiry blocks playback/retry/new review, including an already-open review editor. **No automatic deletion scheduler exists.** Deleting a match removes its DB evidence and private upload. Live worker leases temporarily block deletion; expired or missing leases do not, and delayed worker results cannot recreate deleted evidence. Backups, exports and exceptional inaccessible quarantine-file cleanup remain operator responsibilities. Account-level erasure/backup-expiry workflows are not implemented.
- Data is not encrypted at rest by this application. Use protected volumes, least-privilege OS accounts, encrypted backups and controlled exports before processing sensitive footage. Decoder limits and offline research code are not substitutes for OS sandboxing or security review.

## Preview is not production

Local Vite preview is a development surface, **not a production deployment**. No external deployment is automated or authorized. Keep API/worker private. For an explicitly approved remote preview, expose only Vite (`make web WEB_HOST=0.0.0.0`), configure the **exact HTTPS origin** in `SCOUT_ALLOWED_ORIGINS`, and use secure cookies over TLS. Vite's host allowlist must include the specific preview host; never disable Origin/CSRF or use wildcard credentialed CORS. `vite preview` is also not a production server.

Before production: use a maintained reverse proxy serving the built frontend and private `/api` on one TLS origin, `SCOUT_ENVIRONMENT=production`, a supplied random `SCOUT_SECRET_KEY` of at least 32 characters, `SCOUT_COOKIE_SECURE=true`, exact HTTPS origins, persistent private volumes, restricted signup, backups/restore drills, monitoring, patching, shared abuse controls, worker isolation, reviewed migrations and youth safeguarding/legal review. Local rate limits are per-process, not a distributed abuse service. Production configuration fails closed when required settings are absent; it does not implement all those operations for you.

SQLite works immediately for single-machine development. PostgreSQL uses the optional pinned psycopg extra; no live PostgreSQL service was provisioned/validated here:

```sh
make setup BACKEND_EXTRAS="--extra dev --extra postgres"
# Set SCOUT_DATABASE_URL=postgresql+psycopg://... privately in .env/environment.
make db BACKEND_EXTRAS="--extra dev --extra postgres"
# Pass the same BACKEND_EXTRAS to make api and make worker.
```

Schema version 3 includes explicit additive v1/v2 migrations for qualified event semantics, identity assignments and the full-source chunk ledger. Existing rows retain unknown semantics; migrations never infer real contest IDs or train models. Back up private databases and review/test upgrades before production; unsupported versions fail closed.

## Integration inventory and contracts

- [`docs/ALGORITHMS.md`](docs/ALGORITHMS.md): integrated methods, evidence gates, formulas, operator activation, evaluation protocol and honest gaps.
- [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md): capability/source/license inventory and the opt-in patched CPU runtime. GPU execution remains disabled.
- [`docs/FULL_MATCH.md`](docs/FULL_MATCH.md): full-span processing, bounded chunks, retries and synthetic mechanics proof.
- [`docs/API.md`](docs/API.md): HTTP routes, private-session/review/export contracts.
- [`backend/app/schemas.py`](backend/app/schemas.py) and [`frontend/src/types.ts`](frontend/src/types.ts): wire models, including evidence uncertainty and policy fields.
- [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md): internal module/worker contracts and original collaboration notes; executable code/tests are authoritative.

The supplied research report at `/home/ubuntu/Documents/youth-soccer-analytics-research.md` informed the integration inventory; it is environment-local, not a bundled dependency or a youth-domain validation study.
