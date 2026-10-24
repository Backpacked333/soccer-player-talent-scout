# Independent validation review

Review date: 2026-10-24. Verdict: **software/regression checks pass; youth-soccer predictive validity is not established.** This review read the equations and API/UI evidence flow, then added independent exact-arithmetic, seeded simulation and adversarial integration checks. No live youth footage, trained weights, browser testing, deployment or git writes were used. UI checks below are jsdom component tests, not browser/end-to-end tests.

## What was independently checked

- **Spatial Markov / xT-style units:** `V[i] = (goals[i] + sum(successful_moves[i,j] * V[j])) / (all_attempts[i] + alpha)`. Goals terminate at one; non-goal shots, failed moves and the positive zero-terminal prior terminate at zero. Failure attempts stay in the denominator. The positive prior makes the substochastic transition operator contractive, including cycles. Values are conditional eventual-goal probabilities in this simplified chain; scored retained moves are `V[end]-V[start]`, not xG, VAEP, talent or future potential. Unobserved cells have a computational zero but are unavailable for scoring below disclosed support thresholds.
- **Spatial semantics:** explicit shot goal/no-goal labels, possession retention, complete both-side streams, training permissions and coordinate provenance are required. Right-to-left pitch normalization rotates **both** axes. Highlight-only, partial, unknown-coordinate, incompatible-scope, draft and stale-identity data do not become training observations. The held-out shot-cell conversion diagnostic is **not validation of long-horizon xT values**.
- **Bradley–Terry:** for `d=theta[i]-theta[j]`, the pair loss is `n*softplus(d)-wins*d`; its derivative is `n*sigmoid(d)-wins`, and curvature is `n*p*(1-p)`. Adding positive Gaussian prior precision gives a strictly convex objective and finite separated-sample MAP. The centered covariance is `P H^-1 P = H^-1 - sigma^2/n * 11'`. Independent exact rational inversion of a balanced three-player precision matrix agrees with implementation. Existing scalar, finite-difference, player-ID relabeling, input-order, tied-rank and disconnected-component checks were also reviewed and rerun.
- **Statistical limits:** these are selected reviewed duels, not match wins attributed to individuals. Pairwise probabilities are plug-in MAP propensities, not posterior-integrated predictions. Laplace and Beta intervals assume independent observations; neither is match-cluster calibrated. A new perfectly correlated match fixture demonstrates that repeated within-match outcomes can narrow the nominal interval without more independent match information; the dependence-adjusted effective N correctly remains null. Rank eligibility thresholds are product guardrails, not a validation certificate.
- **Leakage/provenance:** evaluation splits whole distinct match dates, never shuffled events. Train/test fixture matches are disjoint. Test-label reversal leaves training fingerprints and baseline unchanged. Baselines use training outcomes only and are scored on the same held-out observations. Source/policy/scope/identity fingerprints are checked before serving spatial artifacts; current training eligibility is now rechecked even for a matching older artifact. Bounded JSON is not executable serialization, and checksums are not authorization or proof of trustworthy training.
- **Identity and history:** authenticated API tests cover persistent canonical IDs across three matches; same-number opposing jerseys remain separate; proposals remain unresolved; event binding requires confirmation/revision and returns the event to draft. Reassignment does not silently transfer approval. Corrections, deletion and roster-side/role changes rebuild history/relationships. Event/identity/roster correction audits retain before/after state. Whole-match privacy deletion intentionally removes identifying audits rather than retaining them forever.
- **Scope and meaning:** tenant, real/demo, season, match age, competition and role isolation remain enforced. No-data/unknown-role/unreviewed records do not acquire ranks; synthetic provenance is refused in real snapshots. Detector confidence is not a field in the rating evidence contract. Shared-roster relationships are not simultaneous exposure, chemistry or friendship; direct-duel relationships require explicit accepted contests, not an inferred network comparison.
- **As-of caveat:** this means *today's corrected evidence from matches dated on/before the cutoff*. Future matches and their evidence are excluded before history/fingerprint construction. Later review/corrections of an older match still affect its earlier-date history. This is **not bitemporal reconstruction of what a reviewer knew then**; retrospective evaluation is current-corrected, not an honest historical decision-time deployment replay.

## Confirmed defects fixed

| Defect | Fix and regression evidence |
|---|---|
| Legacy/imported unlinked model candidates could enter spatial fitting despite HTTP approval guards | `backend/app/intelligence/action_value.py`: require current reviewer-confirmed identity in fit/score; recheck training gates for old compatible artifacts. `test_independent_validation.py` checks both paths. |
| Conflicting/out-of-duration identity assignments were not rejected consistently by all engines | Shared closed-interval `conflicting_identity_ids` sweep in `contracts.py`, used by spatial, duel and memory engines. Cross-engine overlap/duration regressions. |
| Memory's explicit-contest grouping disagreed with rating selection for unequal mirror timestamps and ineligible mirrors | Reuse authoritative contest integrity selection and deduplicate contest/actor records. Preserve factual cross-role relationships when filtering the subject's historical role; rating comparisons still require both roles. Engine and authenticated API regressions. |
| Unknown appearance roles could obtain eligible duel ranks in mixed/unknown role scopes | Retain descriptive fitted evidence but suppress ranks with `reviewed_appearance_role_required_for_rank`. Both scope variants tested. |
| Normal event creation/correction/deletion invalidated the old model but left its complete-stream declaration usable for a biased refit | `service.py` now binds versioned completeness signatures to the exact eligible-action rows, not just source/rights/roster. An authenticated regression failed before the fix; deletion blocks refitting, and creation/update/deletion all require fresh declarations while retaining audits. |
| Snapshot loader could skip malformed/orphan legacy eligible actions while retaining a declared complete training stream | Withdraw stream completeness when an unrejected eligible row is skipped; preserve the source row for correction. API regressions cover malformed coordinates, orphan revision and invalid opposite-side attribution. |
| Identity UI computed success/fresh-review messages without displaying them | Render the existing accessible `Success` component after proposal/correction/binding. Component regression checks confirmation-only binding, actor/interval options, required reason, exact API revision and visible return-to-draft message. |

The v1→v2→v3 preservation fixture now contains old roster, upload, job JSON, raw anonymous observation and audit records, not only a generic event and empty pipeline tables. Every original selected column is compared before/after both migrations; foreign keys remain valid, no identities/chunks are fabricated, and generic successful legacy shots are not relabeled goals. Existing rollback, concurrent migration and optimistic-revision tests pass. Common video/audio/checkpoint extensions are also ignored by git as accidental-commit prevention; this is not a security boundary or a substitute for reviewing staged files.

## Independent synthetic benchmarks

`backend/tests/test_independent_validation.py` constructs a four-cell chain with self-loops, goals, misses and failed moves; exact `Fraction` Gaussian elimination and 12,000 seeded absorbing-chain simulations per starting cell independently check the Bellman solution. Seed 1729; tolerance fixed at six binomial SDs plus `1/N`, not tuned after seeing results. A single-shot-cell holdout correctly **ties** the global baseline rather than manufacturing a spatial win.

The duel generator fixes four fictional strengths `[-1.2,-0.4,0.4,1.2]`, random pairings, 48 daily fixtures and 20 duels/fixture. The first 36 fixtures (720 duels) train; the last 12 (240 duels) test. Prior/default policy is not tuned on the holdout. Worlds are stable Bradley–Terry, equal-strength null, and test-period strength reversal. Repeated players are intentional for a longitudinal target, not evidence of cold-start generalization. Independent loss calculations match the evaluator. **No test requires the model to beat a baseline.**

| World | Seed | Model / baseline Brier | Model / baseline log loss |
|---|---:|---:|---:|
| Stable | 1729 | 0.178253 / 0.249292 | 0.536863 / 0.691731 |
| Null | 1729 | 0.250187 / 0.254084 | 0.693516 / 0.701331 |
| Reversal | 1729 | 0.506671 / 0.250908 | 1.342026 / 0.694963 |
| Stable | 2026 | 0.194549 / 0.250675 | 0.578129 / 0.694497 |
| Null | 2026 | 0.254221 / 0.250232 | 0.701726 / 0.693612 |
| Reversal | 2026 | 0.534084 / 0.250986 | 1.425251 / 0.695120 |

Lower is better. The model loses badly under reversal and does not consistently improve on the null baseline. Stable-world success is expected under its own assumptions; it proves neither youth accuracy nor calibration, fairness, rank reliability or development prediction. Two seeds are an implementation diagnostic, not a statistical accuracy study.

## Commands actually run after fixes

From `backend` (existing ordinary Python environment):

| Command | Result |
|---|---|
| `.venv/bin/pytest -q -rs` | **405 passed, 2 skipped**, 35.26s |
| `.venv/bin/ruff check app tests` | Passed |
| `.venv/bin/mypy app` | Passed, 32 source files |
| `.venv/bin/pytest -q -s tests/test_independent_validation.py` | **18 passed**; printed the benchmark table above |

The two skips are actual Kloppy-domain tests: the optional `kloppy` package is absent. One existing Starlette/httpx TestClient deprecation warning remains; no package was changed to suppress it. Initial runs caught a historical memory-reason mismatch, test fixture/field mistakes and a shared migration-fixture collision; these were corrected before the full passing run.

From `frontend`, with the existing Node v24.22.1 / npm 11.19.1 installed under `/home/ubuntu/.nvm/versions/node/v24.22.1/bin` added to `PATH` (the default shell had no `npm`):

| Command | Result |
|---|---|
| `npm run lint` | Passed, zero warnings |
| `npm run typecheck` | Passed |
| `npm test` | **61 passed**, 9 files; jsdom only |
| `npm run build` | Passed; 1,595 modules transformed |

`git check-ignore -v --no-index` confirmed representative MP4/MOV/WAV/checkpoint paths are ignored while TypeScript sources are not. No installs, lockfile changes, dependency audits, trained inference, browser sessions, commits, branches, pushes or deployment were performed by this review.

## Operational go/no-go gates and remaining limits

1. **Private reviewed-evidence pilot only:** require authorized roster/footage/guardian-or-club and opponent privacy review, explicit retention, authenticated workspace access, controlled source review and correction/audit drills. Never publish minors' profiles or turn provisional estimates into selection/talent scores. No face recognition, ethnicity/health/biological-maturity inference or autonomous detector learning is implemented or justified.
2. **Before predictive claims:** obtain separately authorized, labeled complete youth matches. Pre-register outcomes, annotation rubric, missingness rules and baselines; use independent double review and adjudication of player/contest identity, direction, shots and failures. Reserve untouched later matches (and separate club/player cohorts for any cold-start claim). Compare proper scoring rules and calibration with match/club-cluster uncertainty and age/role/competition coverage. Current likelihood intervals, sparse cell thresholds and rank guardrails are not validated sufficiency thresholds.
3. **Before spatial activation:** verify full eligible-action capture for both sides, pitch dimensions/orientation/calibration provenance, possession/goal semantics and separate training rights. Completeness remains a reviewer declaration, not something the software can prove from sparse video. Correct/redeclare incomplete records; refit explicitly after evidence changes. Existing old-signature declarations now fail closed and must be explicitly redeclared: this is intentional compatibility behavior, not a new database migration. No bundled grid, automatic model selection or automatic retraining is supplied. Shot-only held-out conversion is insufficient to validate the full transition/value model.
4. **Before production video use:** test throughput, memory/disk/proxy limits, identity switches, missed actions, sample cadence and correction burden on authorized labeled full games; test backup/restore and retention/deletion. SQLite is exercised here; a live PostgreSQL service, GPU runtime and default-cadence trained detector were not. Bounded subprocesses and Python network controls are defense in depth, not an OS sandbox.
5. **Licenses and runtime:** the patched opt-in CPU configuration and parent-run random-weight Base/legacy-Large loader/forward proofs are documented in [FULL_MATCH.md](FULL_MATCH.md), including the isolated 5,700-second synthetic-color, ten-segment, 570-forward smoke at 10-second cadence. This review read that handoff; it did **not rerun or convert it into trained accuracy/default-cadence throughput evidence**. Default detection stays disabled and existing explicit enabled/license/security gates remain intact. Do not restore unsafe pickle loading. Model/checkpoint/data/footage rights are separate from code licenses; FFmpeg build obligations also vary. SoccerNet GSR, Broadcast2Pitch and other research components remain subject to the unresolved adoption/license boundaries in [INTEGRATIONS.md](INTEGRATIONS.md); none was installed/copied/downloaded here. Re-run advisory audits and actual configured-runtime checks at release; prior zero findings are time-bound, not blanket clearance.
6. **Known semantics to preserve:** manual events without track links cannot be repaired automatically from a later track correction; reviewers must correct them explicitly. Different IDs/times can conceal duplicate real-world contests. As-of is match-date/current-corrected, not review-time. No total duel exposure, dependence-adjusted effective N, simultaneous field-time relationships, universal cross-component ranking or causal development forecast is available. Missing evidence stays unavailable, not zero or 50th percentile.
