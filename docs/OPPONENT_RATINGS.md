# Reviewed opponent-duel estimates

## What this implements

`backend/app/intelligence/ratings.py` implements `contracts.DuelRatingEngine` as `BradleyTerryEngine` (also exported as `ratings.DuelRatingEngine`). It estimates **relative performance in selected, reviewed, explicitly opponent-attributed direct duels**, within one authorized cohort. It does not estimate universal talent, potential, future professional success, maturity, health or match-winning ability. Match scores are not inputs.

This is a new, transparent Gaussian-prior Bradley–Terry implementation, not a wrapper around a pretrained youth model. The numerical method is tested; predictive validity on real youth data is **not established**. Every report has `validation_status="unvalidated_observed_duel_estimator"`. Accumulating reviewed matches refits this narrow estimator; it never retrains a detector or identifies faces.

The engine is stateless. Durable canonical players, reviewed identities and event revisions come from the authorized coherent database snapshot service. The application now exposes it through authenticated `/api/intelligence/duels` and the leaderboard matchup panel; memory consumes the same accepted contest selection for direct relationships. See API.md and ALGORITHMS.md for integration.

## Integration API

```python
from app.intelligence.ratings import BradleyTerryEngine, OpponentRatingReport

engine = BradleyTerryEngine()
report = engine.fit(snapshot)  # EvidenceSnapshot; optional policy=DuelRatingPolicy(...)
comparison = engine.predict(report, player_id=player_id, opponent_player_id=opponent_id)
evaluation = engine.evaluate(snapshot, test_fraction=0.25)

# JSON, never pickle or executable model serialization.
restored = OpponentRatingReport.model_validate_json(report.model_dump_json())
```

`fit` returns the shared `DuelRatingReport` **subtype** `OpponentRatingReport`. Its additions include raw wins/losses, per-player evidence, contest records, exclusion reasons, component covariance and validation status. Serializing through a base-only response model can drop these additions: preserve the subtype or explicitly construct an auditable presentation schema. Covariance can remain on the server; it is required for pairwise uncertainty, not for every table render. Treat fitted reports as trusted server outputs, not client-supplied artifacts.

`evaluate` returns `DuelEvaluationReport`, an `EvaluationReport` subtype with training fingerprint, training/test contest counts, baseline probability, scored event IDs and cold-start/disconnected exclusions. This is a **separate training-only fit**, not validation of the full-data `fit(snapshot)` report. Report validation rejects attaching an evaluation whose test matches were used to fit that report. Preserve the specialized evaluation separately to retain its extra audit fields.

Catch `RatingCapacityError` and present an explicit bounded-cohort error, not a partial ranking or HTTP 500. Invalid scope/roster contracts raise Pydantic validation errors. No eligible evidence or numerical nonconvergence produces a report with unavailable numeric estimates and reasons.

## Exactly what becomes evidence

1. One `CohortScope` fixes workspace, real/demo mode, season, age group, competition and role. The entire snapshot is revalidated, including instances built using validation-bypassing Pydantic methods.
2. Both canonical players must be distinct, authorized members of that match's roster, on opposite sides. No name/jersey guesses, similarity matches or inferred opponents are used.
3. Only `kind="duel"`, `status="approved"`, reviewer-audited, known outcomes enter. A successful actor wins; an unsuccessful actor loses and the explicitly named opponent wins. One accepted contest contributes **one complete paired outcome**, never two independent outcomes.
4. Tackles, headers, recoveries, interceptions, passes, shots and other events are not silently converted into direct contests. Neither match outcomes nor track detections provide outcomes.
5. A stable explicit `contest_id` is required. Its namespace is the match. Legacy duels without it are excluded with `explicit_contest_id_required_no_timestamp_join`, rather than guessing that nearby timestamps identify a contest.
6. Group accepted annotations by `(match_id, contest_id)` only. Mirrored or repeated approved annotations with the same pair, winner and compatible explicit period count once. Every source event ID is retained; all but the lexically first ID are marked duplicates. Times may differ for an explicitly shared contest ID; the explicit reviewed ID, not timestamp equality, declares their physical identity. Distinct contest IDs at the same second are never merged; the same ID in different matches is distinct. However, IDs must not bypass inverse-duplicate detection: reversed actors at the exact same `(match, timestamp, unordered pair)`, with compatible periods but different or missing contest IDs, make all affected groups **unresolved** (`ambiguous_inverse_annotations_across_contest_ids`). Exclude them rather than double-counting or forcing a timestamp join. This conservative diagnostic does not match unrelated pairs, distinct known periods, nearby timestamps, or same-actor collisions. The reviewer can correct shared IDs, reject mistaken annotations, or correct timing/period metadata for genuinely distinct contests.
7. Different pairs, different winners, or two different explicit periods for the same contest ID are contradictions. Exclude **all** approved annotations in that contest; return every conflicting event ID and reason. Never average contradictions into a draw. Draft/rejected annotations are excluded, not evidence of a contradiction. Rejecting a mistaken duplicate or correcting all conflicting approved records repairs the contest on the next fit.
8. If any approved annotation in a contest has ineligible identity/role/interval semantics, exclude the whole contest rather than cherry-picking its valid mirror.
9. A track-linked event must reference a currently confirmed identity assignment, its current revision, the same match and canonical actor, and an identity interval containing the entire event interval. Missing, proposed, rejected, stale or reattributed assignments invalidate that evidence. `model_candidate` provenance requires such a reviewed track identity. Manual reviewed roster attribution is allowed without a track identity. The explicit reviewed opponent is checked against the opposite-side roster; the shared event contract has no separate opponent-track assignment field.
10. For a role-specific scope, **both** appearances must have that role. `all_roles` explicitly pools roles and is not a role adjustment. Event starts must lie in `[0, match_duration)`; an event end may equal the duration.
11. Real-mode synthetic evidence is refused by the shared contract. Tests and examples here are explicitly synthetic demo fixtures; they are never mixed into real records.

The reviewer/integrator must assign contest IDs to real contests. Beyond exact inverse ambiguity checks, the engine cannot detect a mislabeled duplicate given a new ID and different timing or same-actor annotation, or prove that a reviewer observed every duel. This implementation deliberately does not use the optional legacy timestamp-only fallback mentioned in the architecture notes: legacy observations remain unavailable until reviewed contest identity is supplied. An action-stream completeness declaration does not supply the denominator of all possible duels.

## Model, prior and numerical solver

For a contest between players `i` and `j`:

```text
P(i wins | theta) = sigmoid(theta_i - theta_j)
theta_i independently ~ Normal(0, sigma^2)
lambda = 1 / sigma^2

negative log posterior, up to a constant:
L(theta) = (lambda / 2) * sum_i theta_i^2
           + sum_pairs [wins_i * softplus(theta_j - theta_i)
                        + losses_i * softplus(theta_i - theta_j)]
```

The default `sigma` is **1.5 log-odds units**, hence `lambda = 4/9`. The prior strength is a disclosed modeling choice, not a learned youth-population baseline. Stronger priors shrink more; they do not add wins or inflate observed sample counts. Both all-win and all-loss separated samples have finite unique MAP solutions because `lambda > 0` makes the objective strictly convex and coercive.

We aggregate integer outcomes by canonical unordered pair, then solve each undirected opponent-graph component independently using damped Newton steps and Cholesky solves. The Hessian is the probability-weighted graph Laplacian plus `lambda * I`. Stable sigmoid/softplus operations avoid overflow and separated-sample cancellation. Sorted players, components, pairs and evidence make refits deterministic and input-order invariant; relabeling changes identifiers but not the mathematical estimates.

Defaults/bounds are recorded in the shared `DuelRatingPolicy`:

| Setting | Default | Bound / meaning |
| --- | --- | --- |
| Prior standard deviation | 1.5 | 0.1–5 log-odds units |
| Players | 128 maximum | Includes in-role roster players with no eligible contests |
| Contests | 10,000 maximum | Accepted unique contests; shared snapshot also caps input events at 10,000 |
| Newton iterations | 100 maximum | Policy may set 1–200, per component |
| Convergence | `1e-8` | Absolute maximum gradient coordinate; policy range `(0, 1e-3]` |
| Interval mass | 95% | Fixed by the shared contract |

Each Newton iteration permits at most 40 Armijo backtracking steps. The Armijo coefficient is `1e-4`; an objective roundoff allowance of `1e-13 * max(1, abs(objective))` does not bypass the gradient convergence criterion. There is no unbounded optimizer or external numerical dependency. Component factorization is cubic in at most 128 players; pair aggregation caps likelihood work. Evaluation indexes direct evidence and computes at most one posterior comparison per oriented held-out pair, rather than joining every test event against every training event.

Diagnostics include convergence, maximum component iteration count, summed negative log posterior, maximum component gradient residual and warnings. Factorization failure, exhausted line search or iteration limit suppresses **all** numerical ratings and component artifacts in that report, including otherwise converged components; raw evidence counts remain inspectable. Empty evidence is unavailable, not a successful prior-only fit.

## Meaning of the estimate and its uncertainty

The likelihood identifies differences, not a shared absolute ability. For each connected component, report the MAP of `theta_i - mean_component(theta)`. The zero-mean Gaussian prior fixes the optimization gauge, but it does **not** make separate components empirically comparable. The MAP component mean is zero.

At the MAP, let `H` be the Hessian, `n` the component size, and `P = I - 11'/n`. The Laplace covariance of centered effects is:

```text
C = P H^-1 P = H^-1 - (sigma^2 / n) * 11'
```

The equality follows from `H * 1 = lambda * 1`. Removing this unlearned common-mode prior variance makes the reported marginal uncertainty correspond to the actual **component-centered** estimand. The singular all-ones direction in stored `C` is intentional, not a failed inverse.

Per-player `posterior_sd = sqrt(C_ii)`; log-odds intervals are MAP ± `1.9599639845 * posterior_sd`. For players in the same identified component:

```text
delta = theta_i - theta_j
variance(delta) = C_ii + C_jj - 2*C_ij
probability = sigmoid(delta)
probability interval = sigmoid(delta ± 1.9599639845 * sqrt(variance(delta)))
```

These are **approximate posterior intervals conditional on the Gaussian prior, Bradley–Terry assumptions and independent selected contests**. They are not calibrated frequentist confidence intervals, not match-cluster bootstrap intervals and not a coverage guarantee. Small or separated samples can depart materially from the normal approximation. Repeated duels from one match, opponent, tactical context or reviewer are dependent; selection/highlight bias and unmodeled context can dominate the nominal interval. No empirical youth calibration has been performed.

`win_probability` is a plug-in MAP propensity, **not** a posterior-integrated predictive mean. The interval describes uncertainty in that propensity, not an interval for the next binary outcome. `probability_against_component_center` compares against a hypothetical zero centered effect, not an actual opponent, percentile, league-wide baseline or 0–100 ability score.

`likelihood_contests` is the unique-contest count entering the model under its independence assumption. `dependence_adjusted_effective_sample_size` is explicitly null: no defensible dependence correction is estimated. `duel_capture_fraction` is also null because all possible duels are not enumerated. Match count, distinct opponents, largest single-match share and audit IDs are supplied rather than inventing an effective N or missing-data score.

## Eligibility, ranks and pairwise output

Default rank-support requirements are **20 contests, 3 matches, 3 distinct opponents, and no match supplying more than 60% of a player's contests**. These are disclosed product guardrails, not statistically validated sufficiency/fairness thresholds. All are in `report.policy`.

- No data or nonconvergence: `eligibility="unavailable"`; numeric estimate, interval, rank and probabilities are null.
- A fitted player below any support threshold: `eligibility="provisional"`, reasons and approximate interval are visible, rank is null.
- A fitted player meeting all thresholds: `eligibility="eligible"` means eligible for a **within-component point-estimate rank**, not a validated youth model or a confident talent ordering.
- Ranks are among eligible players only in that component. Values tied within `1e-10` log-odds share competition rank; names do not break ties. `rank_tie_tolerance` is exposed in the report. Overlapping intervals are not evidence of a reliable rank difference.
- Distinct graph components have independent identifiers and no joint rank or pairwise prediction. A prior does not create an observed comparison edge.
- Unseen, isolated, same-player, disconnected or unfitted comparisons return unavailable with null probability, never an empty-data 50%. A fitted balanced comparison can legitimately equal 50%.
- Pairwise output labels `direct_and_network_evidence` versus `indirect_network_evidence`. The latter is a conditional model comparison across a connected path, **not** proof that the players ever met. `direct_contests`, `direct_match_ids` and `direct_event_ids` make that distinction inspectable. Indirect comparisons have zero observed direct contests; unavailable comparisons have null direct count.

Adding matches can change opponent estimates, components and their centering as well as a player's own record. A changed centered estimate is therefore not automatically evidence that the player's underlying performance improved. Compare raw longitudinal observations and model context, not a fabricated universal score history.

## Corrections, provenance and relationship history

Every fit reconstructs accepted contests and the graph from the current coherent snapshot. Corrections, deletions, rejected reviews, changed roles and identity revisions can remove edges, split components, change sample eligibility and repair all downstream estimates. No old winner, opponent, rank or covariance is incrementally retained. A local evaluation comparison cache exists only within that call and is never reused after corrections.

`evidence_fingerprint` hashes the method, complete policy, scope, and sorted matches/appearances/events/identity records, including correction-relevant metadata. It is deterministic but is neither an authorization mechanism nor a signature. Input changes, including excluded evidence corrections, change provenance even when the numeric fit is unchanged. `training_match_ids` records the actual input match set.

For player-memory integration, `report.contests` is the authoritative accepted contest list **for this scoped fit**. Each record has winner, loser, both canonical players, match ID, contest ID and all reviewed source event IDs. `player_details` supplies wins/losses, actual match/opponent sets and source IDs. `exclusions`, `excluded_event_ids`, `duplicate_event_ids`, `contradictory_event_ids` and `ambiguous_event_ids` explain all rejected/deduplicated/unresolved evidence. Use actual accepted contests to construct direct-duel relationships; never turn an indirect model comparison into shared-match or relationship history. Durable identity auditing and transactional snapshots remain the identity/memory service's responsibility.

The integrator must take one coherent authorized database snapshot, refit after corrections (or invalidate any external cache using complete evidence/policy provenance), and re-check workspace access when serving results. Do not cache only by player ID. All drilldowns remain private to the authorized workspace; there is no public minors-profile API here.

## Grouped chronological evaluation

Evaluation partitions **whole distinct match dates**: the earliest fraction is training, later dates are test, and same-date matches never cross the boundary. Fewer than two dates is unavailable. No random event split, test result, test-only roster, test graph edge or test identity enters fitting.

Test contests receive predictions only if both players had eligible training evidence (a fitted provisional estimate is allowed) and are connected **in the training graph**. A test duel cannot connect two training components. Cold-start/disconnected contests are explicitly counted and excluded; the evaluation is conditional on this scorable subset, not a claim about generalization to unseen children.

Outcomes are oriented by the **home roster side**, not arbitrary canonical ID order or which player the reviewer annotated. The comparison baseline is the training-only home-side duel win rate with a disclosed `Beta(1,1)` smoothing prior:

```text
baseline = (training_home_wins + 1) / (training_contests + 2)
```

Brier score and binary log loss are computed on exactly the same held-out scored contests for model and baseline. Log loss clips probabilities to `[1e-15, 1 - 1e-15]` solely for numerical safety. No held-out tuning or automatic model selection is performed.

`sample_count` counts scored unique contests. `excluded_count` counts unscored test annotation rows **after accepted mirrored duplicates are removed**, so invalid/contradictory annotations remain visible; these two units are explicitly disclosed. `test_contests` counts valid deduplicated test contests before cold-start/component filtering. No scores are emitted for an unavailable/nonconverged training fit or an empty scorable holdout.

The temporal holdout is descriptive, not a calibration study. Repeated players across time are intentional for this longitudinal prediction target, but matches and review decisions may still be dependent. No rank-confidence, fairness or professional-success claim follows from these scores. The schema does not supply an event-review history reconstruction, so this is a split of **current-corrected** historical evidence, not a reconstruction of exactly what reviewers knew at a past date.

## Executable checks

From `backend`, using the existing environment:

```text
.venv/bin/pytest -q tests/test_opponent_ratings.py
.venv/bin/mypy app/intelligence/ratings.py tests/test_opponent_ratings.py
.venv/bin/ruff check app/intelligence/ratings.py tests/test_opponent_ratings.py
```

The tests cover independent scalar MAP/covariance oracles, finite-difference derivatives, finite separation solutions under extreme allowed counts/priors, monotonicity, prior shrinkage, opponent-strength distinction, order/relabel/outcome-inversion/translation symmetries, exact balanced ties, disconnected graphs, mirrored and contradictory annotations, cross-ID exact inverse ambiguity without unrelated timestamp joins, legitimate roster/role/identity gates, corrections/deletions/reattributions, unavailable states, nonconvergence/factorization failure, JSON roundtrips, max-player and near-max combined work bounds, same-date holdouts, manual Brier/log-loss oracles, cold-start/component exclusions, training-input spying, test-result/roster leakage checks and evaluation provenance refusal.

All numerical test fixtures are synthetic algorithm checks. They are not trained youth weights or evidence of real predictive accuracy. No dependency install, model download, vision security-hold change, deployment or browser test is required by this implementation.
