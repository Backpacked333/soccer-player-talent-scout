# Integrated reviewed scouting algorithms

## Product contract

This private application accumulates **reviewed knowledge**, not autonomous visual learning, universal talent, or predicted potential. Canonical `Player.id` persists across match appearances. Anonymous tracklets never establish identity by themselves. No face recognition, ethnicity/health/maturity inference, public minors' profiles, hosted media/model service or automatic event approval exists.

Actual integration is in `backend/app/intelligence/service.py`, `backend/app/routes/intelligence.py`, the current profile/leaderboard snapshot paths, and `frontend/src/{Knowledge,IdentityReview,EventSemantics}.tsx`. Exact routes are in [API.md](API.md). Mathematical implementations and detailed contracts are in [PLAYER_MEMORY.md](PLAYER_MEMORY.md), [OPPONENT_RATINGS.md](OPPONENT_RATINGS.md), [ACTION_VALUE.md](ACTION_VALUE.md), and `backend/app/intelligence/contracts.py`.

Every read has a server-derived workspace and an explicit real/demo, season, match-age, competition and role boundary. Fresh SQLite read transactions or PostgreSQL repeatable-read/read-only transactions pin related queries to one snapshot; live PostgreSQL deployment has not been exercised. Future matches are excluded. `as_of` means **current-corrected evidence dated on/before the cutoff**, not an historical reconstruction of reviewer knowledge. A later correction to an older match changes its history. No player-ID-only cache can preserve stale derived totals.

## Evidence hierarchy and uncertainty

1. Private source metadata proves container properties only.
2. Completed detector chunks prove sampled execution over specified source intervals only.
3. Track observations are anonymous image boxes, not pitch positions or player identity.
4. Reviewer-confirmed canonical identity requires source support, current roster/rights and reasoned revisioned attribution.
5. Approved, known-outcome events become descriptive evidence only after applicable current identity and interval checks.
6. Narrow models additionally require their own cohort, sample, graph, semantics, training-permission and freshness gates.

A complete detector ledger is **not reviewed-event coverage**. Reviewer-declared exposure is not inferred visibility. Missing opportunity/exposure denominators return null/unavailable, not zero. Counts, rates, model estimates and uncertainty have separate labels. Selected highlights, repeated actions within one match, annotation error and missing opportunities can dominate nominal intervals.

## Canonical identity and current player memory

`TrackIdentityService` locks the match before decisions and uses expected revisions/ORM compare-and-swap. Proposals may coexist unresolved; exact source track and sample-supported interval must be verified. A same-track or same-player overlapping confirmed assignment conflicts. Jersey numbers/team/role are appearance context, not cross-game identity. Reviewer proposals have no fabricated confidence probability.

Every correction is audited. Linking an event makes it draft. Reassigning/rejecting/unassigning a track drafts linked approvals but retains their old actor and reviewed revision. To repair: correct the actor as a draft, bind the current confirmed assignment, then explicitly approve again. Approval never silently transfers to another child. Unlinked manual actor errors must be corrected separately; there is no provenance to infer their originating track. Model-candidate events without confirmed attribution cannot be approved or contribute.

`ReviewedPlayerMemoryEngine` rebuilds chronological match history, raw action tendencies, historical role segments and relationships. With `s` successes among `n` accepted observed outcomes:

```text
observed rate = s / n
posterior = Beta(s + 1, n - s + 1)
95% interval = equal-tail beta quantiles at 0.025 and 0.975
```

The Beta(1,1) prior is a disclosed regularizer, not a learned population average. For `n=0`, rate/posterior/interval are null. These are conditional independent-action intervals, not match-cluster-calibrated uncertainty or goal probabilities. A successful shot is not a goal.

Co-rostered teammate/opponent edges have actual shared match IDs, but imply neither simultaneous exposure, chemistry nor friendship. Direct-duel edges use the same accepted explicit contests as ratings, never indirect graph comparisons. Current changes to events, identities, rosters or deletions rebuild supporting IDs/counts and evidence fingerprints. New reviewed games update the record, not a detector or a silent spatial-model retrain.

## Opponent-conditioned direct-duel model

This is a bounded Gaussian-regularized Bradley–Terry fit over selected reviewed **direct duels**, not universal opponent quality. A duel requires two opposite-side canonical rostered players, a known reviewed outcome and an explicit match-scoped contest ID. Consistent inverse annotations collapse once while retaining all supporting event IDs. Contradictory groups and exact inverse ambiguities across inconsistent IDs are excluded, not averaged or counted twice. Legacy duels with no contest ID remain excluded. Newly generated demo duels use explicitly fictional IDs; no real IDs are inferred/backfilled.

```text
P(i wins) = sigmoid(theta_i - theta_j)
theta_i ~ Normal(0, sigma^2), default sigma = 1.5
negative log posterior = paired logistic loss + sum(theta_i^2)/(2*sigma^2)
H = weighted opponent-graph Laplacian + I/sigma^2
```

Damped Newton/Cholesky solves are deterministic and bounded. Independent connected components are centered separately. Approximate centered Laplace covariance is `C = H^-1 - (sigma^2/n) 11'`. For a pair in one component, log-odds variance is `C_ii + C_jj - 2*C_ij`; apply sigmoid to `delta ± 1.95996*sqrt(variance)` for the conditional propensity interval. This is a MAP plug-in propensity, not a posterior-integrated future prediction. Nonconvergence suppresses numeric estimates rather than returning plausible-looking values.

Default rank gates: 20 contests, 3 matches, 3 distinct opponents, at most 60% from one match. These are product guardrails, not validated youth sufficiency thresholds. Below them, estimates are provisional and ranks null. Disconnected/unseen players have no pairwise estimate, not an invented 50%. `all_roles` admits cross-role contests and explicitly is **not role adjustment**; a role-specific fit requires both appearances in that role. Maximum 128 players and 10,000 contests; capacity overflow fails explicitly.

Reference: Bradley & Terry (1952), [Rank Analysis of Incomplete Block Designs](https://doi.org/10.1093/biomet/39.3-4.324). Full assumptions, exclusions, covariance derivation and evaluation are in [OPPONENT_RATINGS.md](OPPONENT_RATINGS.md).

## Operator-activated spatial action value

The independent `spatial-markov-v1` engine is a regularized xT-style research variant, not socceraction's default numerics, xG or VAEP. There is **no bundled fitted grid**, and an unavailable result is correct until an authorized model exists.

For starting cell `i`, let `N_i` include ALL eligible attempts, `G_i` explicit ordinary-shot goals and `C_ij` successful explicitly retained moves. With positive terminal pseudo-count alpha (default 1):

```text
r_i = G_i / (N_i + alpha)
P_ij = C_ij / (N_i + alpha)
V = r + P V
successful retained move value = V(end) - V(start)
```

Failed moves and non-goal shots are denominator/terminal evidence, not filtered away. A generic successful shot is never promoted to goal. The bounded fixed-point solver reports residual and contraction-derived **numerical** error, not statistical uncertainty. Negative differences are retained. Unobserved cells' internal regularization zeros are not published as observed zero-threat estimates; both queried cells require real training support.

Qualified pitch coordinates require provenance. Fixed-reference coordinates require period and attack direction; right-to-left rotates both axes `(x,y) -> (1-x,1-y)`. Already attack-normalized coordinates are not rotated again. Detector image boxes never meet this contract. Training is all-roles only: role filtering would break the declared complete population. Default gates require 3 training matches, 200 actions, 20 shots, 3 goals and 5 starting actions in each scored cell. These thresholds are not empirically validated. Caps are 192 cells, 10,000 input events, 1,000 matches and 2,000 iterations.

Each supplied training match needs an auditable complete ordinary-action stream for both sides, including failed actions, and **separate training permission**. Processing permission alone is insufficient. Declare through `PUT /api/matches/{id}/action-stream`; authenticated reviewer/time override any submitted identity. Source/context/roster/rights changes invalidate that declaration. The application cannot independently verify truthful completeness or legal authorization.

### Operator workflow

1. Obtain authorized, complete, correctly normalized reviewed streams; resolve attribution/semantics and record separate training rights. Do not train from the shipped illustrative demo as though it were calibrated soccer data.
2. Export the exact private cohort through authenticated `GET /api/intelligence/snapshot?mode=real&season=...&age_group=...&competition=...&role=all_roles`. Keep the JSON private; no bearer/session material belongs in it.
3. Use the existing backend environment offline (paths below are examples, not bundled files):

```sh
.venv/bin/python -m app.intelligence.action_value_cli train \
  --input /private/reviewed-snapshot.json \
  --output /private/operator-models/new-research-model.json \
  --evaluate --test-fraction 0.25
.venv/bin/python -m app.intelligence.action_value_cli evaluate \
  --input /private/reviewed-snapshot.json \
  --output /private/new-shot-diagnostic.json
```

Optional `--policy /private/policy.json` is explicit; never tune against the holdout. The CLI exclusively creates private mode-0600 JSON files, refuses existing outputs, never activates a model, never accesses network and never trains on its held-out matches. A successful command may return an unavailable evaluation; it is not validation.
4. Review scope, rights, source fingerprint, support and limitations. To activate intentionally, set **server-side** `SCOUT_ACTION_VALUE_ARTIFACT_PATH=/private/operator-models/new-research-model.json` and restart the API with its usual private environment. The path must be a bounded regular JSON file outside upload storage; symlinks, oversized/invalid JSON and incompatible scope fail closed. Do not configure a client upload/URL. Leave unset to keep values unavailable. Clearing the setting disables activation.
5. `GET /api/events/{id}/action-value` derives authorized scope from the owned match, validates the artifact before exposing its metadata and checks all original training matches' current fingerprints. Correction, deletion, reattribution or changed source/permission makes the old model unavailable until explicit refitting. New reviewed games do not silently alter the fitted model.

Artifacts have checksums, deterministic provenance and `unvalidated_research` status, not cryptographic trust or anonymization. A model for another workspace/mode/season/age/competition is refused with a generic safe message. One operator path serves only its exact scope; there is no multi-tenant model registry or automatic online learning.

References: [Karun Singh's xT exposition](https://karun.in/blog/expected-threat.html), [socceraction xT documentation](https://socceraction.readthedocs.io/en/stable/documentation/valuing_actions/xT.html), [VAEP documentation](https://socceraction.readthedocs.io/en/stable/documentation/valuing_actions/vaep.html). Details and deviations: [ACTION_VALUE.md](ACTION_VALUE.md).

## Evaluation and validation protocol

The executable engine suites use independent mathematical oracles, deterministic synthetic evidence and adversarial corrections. The API suite (`tests/test_intelligence_api.py`) exercises three historical matches, known-player accumulation, relationships, wrong-assignment repair, stale revisions, future/as-of exclusion, tenant/mode isolation, inverse-duel conflicts, chunk/observation pages, retry preservation, artifact activation/refusal/staleness and coherent SQLite snapshots. Frontend jsdom tests exercise identity states/reasons, profile history/relationships, correct semantic wire fields and detector-vs-reviewed coverage. None is a trained-youth benchmark.

For any future empirical study: obtain explicit authorized annotated data; pre-register the action/contest population, completeness rules, support gates and metrics; split by whole match dates and additionally evaluate unseen-player/club domains; keep same-date matches together; fit only training evidence; report all cold-start, disconnected and unsupported exclusions; compare against training-only baselines and report dependence/selection limitations. Never choose thresholds from held-out results and then relabel that set validation.

Duel evaluation uses a chronological training-only graph and home-side outcomes, comparing Brier/log loss with the training-only smoothed home-win baseline on the same scorable rows. Spatial evaluation tests only the local ordinary-shot conversion reward component against a training-only constant baseline. It **does not validate long-horizon continuation values or movement differences**, because possession-ending labels do not exist. No youth calibration, prospective selection outcome, fairness guarantee or empirical model-quality result is claimed.

Reproduce from existing environments:

```sh
make check
# Focused authenticated integration and offline synthetic CLI checks:
.venv/bin/pytest -q tests/test_intelligence_api.py tests/test_action_value_cli.py
# Run the preceding pytest command from backend/.
```

Ordinary setup/locks remain independent of inference dependencies; existing CI invokes full checks. Optional CPU detection installation/mechanics/audit boundaries are in [FULL_MATCH.md](FULL_MATCH.md). No browser testing, deployment, downloaded weights or public minors' data is necessary. Before production, validate authorized real footage, operational throughput, OS worker isolation, storage/backups/retention, PostgreSQL concurrency and youth safeguarding; the current research estimates are not a substitute.
