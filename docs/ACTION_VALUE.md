# Provisional spatial action value

## What exists, and what does not

`backend/app/intelligence/action_value.py` implements a real, bounded spatial Markov fit from authorized reviewed event streams. It ships **no pitch-value grid, fitted youth weights, empirical accuracy claim, xG, VAEP, universal player ranking or talent prediction**. Without a compatible fitted artifact and observed cell support, the public value is `null` with reasons. The synthetic tests prove mathematics and refusal behavior, not performance on teen matches.

Primary references:

- [socceraction xT documentation](https://socceraction.readthedocs.io/en/stable/documentation/valuing_actions/xT.html): spatial Markov values, successful possession-retaining movement differences, attacking-direction normalization.
- [Karun Singh's original xT exposition](https://karun.in/blog/expected-threat.html): shot reward plus movement continuation.
- [socceraction](https://github.com/ML-KULeuven/socceraction): established MIT implementation. This module independently implements the equations rather than copying its code or inventing missing SPADL period/body-part/goal semantics to use its adapter.
- [VAEP documentation](https://socceraction.readthedocs.io/en/stable/documentation/valuing_actions/vaep.html): next-actions score/concede classifiers and contextual features are a different method and remain unavailable here.

## Exact model and deliberate deviations

Eligible attempts are ordinary **passes, crosses, carries and explicit shots**. Let, in starting cell `i`:

- `N_i` = all eligible reviewed attempts, including failed moves and non-goal shots;
- `G_i` = explicit ordinary-shot goals, not generic `outcome='successful'` shots;
- `C_ij` = successful **and explicitly possession-retaining** moves from `i` to `j`;
- `alpha > 0` = the disclosed zero-reward terminal pseudo-count per cell.

The fitted equation is:

```text
r_i = G_i / (N_i + alpha)
P_ij = C_ij / (N_i + alpha)
V = r + P V
public successful-move value = V(end cell) - V(start cell)
```

Without regularization, `G_i/N_i` is the familiar shot-selection probability multiplied by conditional goal probability. Failed moves, explicitly non-retained successful moves and shots have no continuation. Non-goal shots, including rebounds, terminate this narrow action chain: we do not invent rebound/possession labels. An unsuccessful-but-retained move is ambiguous under these semantics and must be resolved rather than guessed. All shot labels must be `goal` or `no_goal`; a goal contradicting an unsuccessful shot is rejected. A successful non-goal shot is legitimate and is not counted as a goal. The input contract has no own-goal/penalty subtype, so operators must provide only the ordinary-shot universe declared by `StreamDeclaration`; the program cannot independently detect mislabeled penalties or own goals.

This is **`spatial-markov-v1`, a regularized xT-style variant**, not numerically interchangeable with a socceraction default model. Differences are the positive terminal pseudo-count, available action taxonomy, strict complete-stream/rights gates and conservative support masking. No possession ID, body part, speed, pressure or period is fabricated.

Row sums satisfy `q = max_i sum_j P_ij < 1` because `alpha > 0`. Starting at zero, fixed-point iteration is nonnegative and bounded by one. The solver stops on the Bellman infinity-norm residual, or refuses to create an artifact on iteration exhaustion. It exposes iterations, residual, contraction bound and `residual / (1-q)`, a **numerical solution error bound, not statistical uncertainty**. There are no calibrated confidence intervals. Default `alpha=1`, tolerance `1e-10`, maximum 1,000 iterations; policy hard cap 2,000. Sparse transitions are stored only for observed source/destination pairs. At most 192 cells, 10,000 input events and 1,000 matches are accepted; no dense inversion or network dependency is used.

Internal unobserved cells solve to zero because they have no reward/transition observations. This is an internal regularization state, **not an observed zero-threat estimate**: both queried cells must meet the minimum observed starting-action support. Continuation into an unobserved cell is zero in this fit and can depress upstream values; support thresholds are not a guarantee of unbiased or precise estimates. Negative supported differences are preserved, including legitimate backward movement. A same-cell supported move has an actual zero difference, unlike an unavailable move.

## Input and scope gates

The engine revalidates even already-instantiated/mutated Pydantic inputs. Training fails closed for the entire supplied snapshot if an eligible stream is incomplete; it does not silently filter out unsuccessful, unreviewed or malformed attempts until a plausible model remains.

Required for every supplied match:

1. Auditable `complete_match` declaration: all eligible attempts, both sides, declarer/time/source, and **separate model-training permission plus its rights reference**. Ordinary private processing permission alone is insufficient. Both authorized rosters and eligible observed actions from both sides must exist. These checks cannot prove that an operator's completeness declaration is true.
2. Approved, known-outcome eligible actions with reviewed provenance and real/demo isolation. Draft eligible actions prevent fitting. Rejected candidates are not attempts, but their current state remains in the source fingerprint.
3. Qualified pitch coordinates and their provenance. `pitch_normalized` requires explicit period and attack direction. Right-to-left attack rotates **both axes**: `(x,y) -> (1-x,1-y)`. Direction must be consistent for a side/period and opposite for opponents. `attack_normalized` means the provider already performed normalization; no second rotation occurs. Image bounding boxes are not pitch coordinates.
4. Every attempt needs a start location. Retained successful moves additionally need an end location. Move possession retention and shot goal labels must be explicit. Coordinates must be finite and inside `[0,1]`; invalid values are never clipped to the pitch. Bins are lower-inclusive in each normalized axis, with `1` assigned to the final bin. Row-major indexing is `y*columns+x`.
5. If identity-linked, the current confirmed assignment must match workspace/mode/match/canonical actor, reviewed revision and the full event interval. A proposed, rejected, missing, stale or reattributed identity cannot supply training evidence.
6. Duplicate event IDs fail schema validation. Distinct IDs at the same actor/match/exact timestamp are treated as ambiguous rather than independent attempts; operators must resolve collisions. There is no fuzzy duplicate merge. Duplicate appearance IDs also fail provenance validation.

The model is explicitly **`all_roles` only**. A role-filtered stream breaks the complete possession-transition population, so role-specific spatial fitting is refused rather than silently training on an incomplete sequence or presenting a mixed-role model as role-adjusted. The other cohort fields must match exactly: workspace, real/demo mode, season, age group and competition. These boundaries do not establish fairness.

Default provisional sufficiency gates are 3 matches, 200 actions, 20 explicit shots, 3 goals and 5 observed starting attempts in **each** scored cell. They are visible in every artifact's policy and are engineering defaults, **not empirically validated youth thresholds**. The tiny oracle tests explicitly use lower legal thresholds; they are not production defaults. No-goal, empty or insufficient streams do not produce a prior-only artifact.

## Integration and correction contract

Module functions and `SpatialMarkovEngine` satisfy the shared engine protocol:

```python
fit(snapshot, *, policy=None) -> ActionValueArtifact
score(event, *, scope, artifact) -> ScoredActionValue
evaluate(snapshot, *, policy=None, test_fraction=0.25) -> EvaluationReport
```

`ActionValueUnavailable` is a `ValueError` with an inspectable fit refusal reason. Invalid input schemas raise Pydantic validation errors. A failed fit never returns a fabricated model. Scoring returns `unavailable`/`null` and reasons for missing/incompatible artifacts or unsupported actions.

**Preferred application entry point:**

```python
score_from_snapshot(snapshot, *, event_id, artifact) -> ScoredActionValue
```

The integrator must construct `EvidenceSnapshot` from an authorized, atomically read database snapshot containing **all original training matches plus the queried match**. Never trust an arbitrary client-supplied snapshot, model checksum or file path as authorization. `score_from_snapshot` verifies the current source fingerprint and current identity revision before scoring. Corrections, rejected/deleted events, reattribution, roster changes, source/permission changes and identity revisions make old models unavailable until an operator explicitly refits. A changed or missing training match cannot silently leave a numeric stale value. Adding new matches does not silently train the model; supported moves in those matches can be scored against the still-current old training artifact, with current roster/identity checks.

The protocol's lower-level `score` cannot inspect a database or infer match season/age/competition from an event alone: its trusted caller must already authorize the event's full cohort and verify model freshness. It refuses identity-linked events without snapshot context and labels `current_evidence_checked=false`; use the snapshot entry point for actual application scoring. New reviewed games update longitudinal player memory through the separate memory engine, **not automatic detector retraining or silent spatial-model training**.

`ScoredActionValue` extends the shared result with evidence ID, start/end support and cell indices, training support, normalization provenance, freshness-check flag and limitations. A serializer restricted to base `ActionValueResult` may discard subclass drilldown fields; preserve the concrete return type when exposing those details. The application integration now preserves this concrete response at `/api/events/{id}/action-value`, displays values or unavailable reasons in review, and supports only server-side `SCOUT_ACTION_VALUE_ARTIFACT_PATH` activation. See ALGORITHMS.md; no client model upload/path/URL is accepted.

Training provenance hashes the canonical sorted scope, policy, matches/declarations, appearances, all events and current identity records. The stable model fingerprint additionally covers learned values, support and diagnostics, but excludes wall-clock `trained_at` and separate evaluation metadata. Equivalent reordered source lists reproduce exactly the same model fingerprint. Hashes are integrity/correction identifiers, **not signatures, permission grants or anonymization**; IDs and provenance must stay private.

## Offline CLI and artifacts

Run from `backend` using the existing environment; no dependency addition is needed:

```sh
.venv/bin/python -m app.intelligence.action_value_cli --help

# Fit only the earlier training partition and attach a later-match diagnostic.
.venv/bin/python -m app.intelligence.action_value_cli train \
  --input /private/rights-cleared-reviewed-snapshot.json \
  --output /private/new-provisional-model.json \
  --evaluate --test-fraction 0.25

# Optional explicit --policy /private/policy.json; never tune against the holdout.
# Without --evaluate, train uses all supplied matches and makes no holdout claim.

.venv/bin/python -m app.intelligence.action_value_cli evaluate \
  --input /private/rights-cleared-reviewed-snapshot.json \
  --output /private/new-shot-diagnostic.json
```

Those paths are operator examples, not shipped data. The input is the exact shared `EvidenceSnapshot` JSON schema; policy is `ActionValuePolicy` JSON. Input files must be ordinary local files; snapshots are capped at 32 MiB before JSON decoding, policies at 64 KiB. Outputs are exclusively created, mode `0600`; existing paths/symlinks are never overwritten. The CLI performs no network, external upload, database write or activation, and logs a short status/model fingerprint instead of whole player records. Schema errors omit input values. A refusal exits 2; completed commands exit 0. Evaluation can successfully produce an `unavailable` diagnostic, which must not be interpreted as a validated model.

The persisted `ArtifactDocument` contains a strict version, the shared artifact and a SHA-256 checksum of its full canonical JSON content. `load_artifact_json(data, authorized_scope=...)` caps artifact JSON at 8 MiB, validates version, finite arrays, shape, support/counts, convergence diagnostics, source rights metadata, checksum and exact caller-authorized scope. No pickle, executable deserialization, automatic request-selected file paths or shipped model weights are used. A malicious party can recompute a checksum; private artifact storage and operator authorization remain necessary. Import validation cannot prove truthful annotations or training permission. Every artifact remains `validation_status='unvalidated_research'` even after a held-out diagnostic.

## Honest temporal evaluation

The input has no explicit possession-ending goal labels. We therefore **do not pretend to validate long-horizon Markov value or move differences**. The diagnostic target is explicit goal/no-goal conversion on ordinary held-out shots, the model's local shot-reward component only.

Whole matches are divided chronologically by **whole match dates**, so same-date matches never straddle train/test. No event-level random split, test-data training, test-only support or automatic hyperparameter search occurs. Training must pass all fit gates independently. Each scored test shot requires at least `min_cell_actions` **training shots** in its starting cell; unsupported held-out shots are excluded and counted, not assigned a baseline/filler prediction. Model probability is `train_cell_goals/(train_cell_shots+alpha)`; the constant comparator is `train_goals/(train_shots+alpha)` on exactly the same supported test rows. Brier uses the actual probabilities. Log loss alone is clipped to `[1e-12, 1-1e-12]` for numeric reporting. The report states sample/exclusion counts, match IDs, dates, target, baseline and limitations.

This is not calibration of xT continuation, individual player value or future potential. `train --evaluate` writes an artifact fit **only** on train matches, never a later full-data refit carrying a misleading held-out label. The shared artifact validator also rejects overlapping test/training match IDs. Synthetic held-out tests check this computation and leakage behavior, not real-world generalization.

## Reproducible checks

```sh
.venv/bin/pytest -q tests/test_action_value.py tests/test_action_value_cli.py
.venv/bin/mypy app/intelligence/action_value.py app/intelligence/action_value_cli.py
.venv/bin/ruff check app/intelligence/action_value.py app/intelligence/action_value_cli.py \
  tests/test_action_value.py tests/test_action_value_cli.py
```

The analytic two-state fixture has `V_A=4V_B/7`, `V_B=(2+2V_A)/7`, giving `V_A=8/41`, `V_B=14/41`. Tests also exercise failed-attempt denominators, negative differences, axis/rotation boundaries, same-side/opponent direction contradictions, unknown/contradictory goals, malformed/nonfinite artifacts and coordinates, support masks, convergence refusal, duplicate evidence, incompatible cohorts, source/identity corrections, deterministic hashes, exact train-only held-out baselines, input bounds and private exclusive CLI output. All fixtures are explicitly synthetic demo evidence; no real youth footage or trained model is bundled.
