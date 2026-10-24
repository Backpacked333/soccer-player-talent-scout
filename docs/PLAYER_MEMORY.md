# Reviewed player memory and canonical track identity

This implementation accumulates **reviewed knowledge**, not a trained youth talent model. Canonical identity is `Player.id`; each `Appearance.player_id` references that identity in a specific match. Jersey, side, team and role changes do not create another player. A track is anonymous until a reviewer explicitly confirms an interval against that match's authorized roster. No face identification, jersey recognition, biological/health inference or detector retraining is implemented.

## Integration interfaces

Core implementations are `backend/app/intelligence/identity.py` and `backend/app/intelligence/memory.py`. The integrated application now supplies authenticated routes, coherent snapshots, audit/approval fences and frontend identity/history views through `service.py`, `routes/intelligence.py` and `frontend/src/{Knowledge,IdentityReview}.tsx`. See API.md and ALGORITHMS.md for the implemented application contract.

```python
from app.intelligence.identity import TrackIdentityService, IdentityError
from app.intelligence.memory import ReviewedPlayerMemoryEngine

identity = TrackIdentityService(
    db, workspace_id=principal.workspace_id,
    mode="demo" if match.is_synthetic else "real",
    reviewer_id=principal.account.id,
)
proposal = identity.propose(match.id, track_identity_write)
confirmed = identity.confirm(
    match.id, proposal.id, expected_revision=proposal.revision,
    reason="Reviewer explanation referencing the inspected private footage",
)
db.commit()  # The service NEVER commits on the caller's behalf.

knowledge = ReviewedPlayerMemoryEngine().build(
    evidence_snapshot, player_id=player.id, as_of=None,
)
```

`PlayerMemoryEngine` in `memory.py` is also an alias for this implementation; it satisfies the shared `PlayerMemoryEngine` protocol. `build(snapshot, *, player_id, as_of=None)` returns `ReviewedPlayerKnowledge`, a `PlayerKnowledge` subclass. Its inherited `history` and `relationships` preserve the exact shared contract. Additional `match_details`, `relationship_details`, `role_segments`, `tendencies`, support dates/counts, duplicate/excluded event IDs and exclusion reasons are JSON-safe. Use this subclass as the response model to expose the additional fields; a base-only response intentionally drops them.

### Identity methods

All methods require authenticated, server-derived workspace/account and an explicit mode. `IdentityError` exposes `code` and `status_code`; return a safe scoped error and roll back the caller transaction. Pydantic validation errors are invalid inputs, not successful partial decisions.

- `candidates(match_id, data: TrackIdentityWrite, *, jersey_number=None, side=None)` returns `IdentityCandidates`. The data's `player_id` is **not** a candidate filter or prediction. Optional number/side filters are explicit reviewer hints, never outputs invented for the detector. Number 7 on both teams yields both players unless the reviewer provides a side. Zero/one/many candidates all remain unresolved; no inferred probabilities are attached.
- `propose(match_id, data: TrackIdentityWrite)` only accepts `status="proposed"`. A `model_proposal` and confidence can be recorded, but cannot bypass review.
- `confirm`, `reject`, and `unassign` take `(match_id, assignment_id, *, expected_revision, reason)`.
- `reassign` also requires `player_id`. It explicitly confirms the corrected identity, not any actions.
- `correct(match_id, assignment_id, data: TrackIdentityCorrection)` is the generic revisioned operation.
- `bind_event(match_id, event_id, assignment_id, *, expected_revision, reason)` requires the event already have the same actor and a fully contained timestamp interval. It writes the linkage and **makes the event draft**. It never copies an identity's player into an event or transfers action approval.
- `assert_event_attribution(match_id, event: ReviewedEvent)` is the **approval gate** for linked actions. It validates the caller's current actor/time, current confirmed identity revision, source and conflicts without approving or overwriting the event. Call it in the same transaction as event review. Manual events with no identity linkage still require the application's existing roster/video/event validation.
- `invalidate_upload(match_id, upload_id, *, reason)` must run **before** source replacement/deletion, in that transaction. It rejects retained decisions and drafts their linked events, auditing both. It does not manage media storage, deletion or worker leases.
- `lock_match(match_id)` is a reusable transaction fence. On PostgreSQL, roster, upload, event and identity writers must take this match-row lock **before** reading or validating attribution-relevant state. SQLite writes serialize globally; unrelated writers cannot safely be assumed to follow the PostgreSQL lock protocol.

### Identity decisions and corrections

Every mutation obtains a match-row write lock before conflict checks. A no-op UPDATE establishes a real SQLite write transaction before a SAVEPOINT; this avoids accidental commit-on-first-savepoint-release. Changes and their audit entries share a savepoint, and the caller owns commit/rollback. Revision compare-and-swap prevents stale review screens from overwriting a newer decision; the model's ORM version column adds another stale-write fence.

Confirmation requires an active same-workspace reviewer, authorized scoped roster/player, current probed upload, current processing/roster/privacy permission declarations, unexpired declared media retention, and observed player/goalkeeper samples for the exact upload and track. Intervals may not extrapolate beyond observed support and must contain a sample; sampled support is **not** proof of continuous visibility between samples. Time is absolute source time and bounded by both match and upload duration.

For chunked sources, the identifier must be `chunk:<canonical chunk UUID>:<local track ID>`. The chunk must belong to the newest job for that upload, be completed, and contain the exact observation linkage and absolute interval inside `[chunk.start, chunk.end)`. Completed chunks may be reviewed while other chunks are pending/running; review does not mutate their evidence. Legacy local IDs have no job provenance: only a single finished `needs_review`/`completed` job is accepted; multiple legacy jobs are ambiguous rather than guessed.

Confirmed overlapping intervals for the same track **or** the same player on another track are refused. Intervals are conservatively closed for identity conflict checks: sharing an endpoint also conflicts. Multiple proposals can coexist unresolved. Nonoverlapping tracklets may link the same canonical player. `reject`/`unassign` remain available when footage/observations are stale or missing, so broken sources cannot prevent removal of a mistaken attribution.

Every correction drafts all explicitly linked events, retaining their old actor and old reviewed identity revision. Before/after identity and full event state, reviewer and reason are audited in the same transaction. Reassignment cannot automatically credit a different child with an approved action. The reviewer must separately correct the action actor, bind the current identity revision and approve the action. Raw `TrackObservation.player_id` is not written by this service: confirmed assignment records are the authority, not a denormalized detector guess.

An unlinked manual event has no track provenance to propagate. If a reviewer corrects a manual actor, that action must be corrected through the event-review flow. No service can infer which manually attributed events were actually based on a mistaken unlinked track. The UI should bind actions that depend on a track decision.

## Memory algorithm

The engine accepts **one coherent, bounded, authorized `EvidenceSnapshot`** and revalidates it, including mutated Pydantic instances. It does not read the database itself. The integrator must load all input in repeatable-read / a serialized SQLite read transaction, not independent committed queries, and must enforce:

1. Workspace, real/demo, season, match-age and competition scope; authorized canonical players and opposite-side event opponents.
2. Current roster permission, valid source/upload/chunk provenance and appropriate deletion/retention policy. Do not pass obsolete confirmed identities from replacement uploads. The pure snapshot cannot inspect a processing lease or media file.
3. Orphan/partial event identity links must not become manual approved evidence. On source deletion the FK can become null while the reviewed revision remains; exclude/report those events, do not erase the revision to fabricate an unlinked manual action. Use source invalidation first.
4. Every event status, actor, outcome, semantic correction, identity revision and deletion is reflected in the new snapshot. Never cache totals by player ID alone.

The algorithm selects the requested player's historical appearance roles and match dates before deriving any outputs or fingerprints. `as_of` means **current-corrected evidence from matches on/before that date**. It is not a historical reconstruction of what reviewers knew on that date; late corrections to older games appropriately change earlier-game histories. Future matches, peers, role changes, events and identities cannot influence an as-of fingerprint.

Only known-outcome, reviewer-approved events enter tendencies. Identity-linked actions require a present confirmed, nonconflicting identity, exact actor/match/revision and full interval containment. Out-of-duration actions are excluded. Duplicate event IDs fail snapshot validation. Exact semantic copies count once, explicit contest/actor copies count once even if annotation metadata differs, conflicting duplicate outcomes and contradictory explicit duels count zero. Consistent mirrored duels remain their actors' own observed records; the engine does not invent the opponent's result. Exclusion reasons and duplicate IDs are exposed. Identical real actions at identical timestamps/geometry cannot be distinguished without better annotation; this conservative deduplication is disclosed.

For each appearance, history contains approved event IDs and cumulative reviewed counts, distinct supported matches, pass attempts/successes, declared exposure and observed rates. `cumulative_matches` counts games with accepted **actor** actions, while `cumulative_roster_matches` and `roster_match_count` describe roster presence. First/last observed dates also require accepted actor actions. `match_details` includes original appearance ID, role, side and jersey so a coach can inspect changing context. Present-day `Player.age_group`, `primary_role` or `team_name` never rewrite these histories.

Each action kind gets a descriptive observed rate and a Beta(1,1) posterior with equal-tail 95% credible limits, reusing the existing tested incomplete-beta quantile implementation. With `n` reviewed opportunities and `s` successes the posterior is Beta(s+1,n-s+1). The prior contributes two pseudo-observations. Missing opportunities produce **null** rates, posterior means and intervals, not zero or a prior-only 50%. Support includes distinct matches and maximum single-match opportunity share. These are provisional summaries of selected outcomes, not ranking eligibility, predicted talent, validated calibration, or shot goal probability. The intervals assume independent exchangeable actions and do not handle match clustering, annotation bias, selection bias or opponent strength.

All-role aggregates are explicitly mixed-role and also carry separate historical role segments. Season/age/competition remain separate scope requests; no implicit pooling across those boundaries occurs. No threshold silently promotes the memory to a validated model. `predictive_model_status` remains `not_trained_or_validated`. Trajectories describe different reviewed samples, never causal improvement or future potential.

The evidence/version fingerprint includes the method, prior, interval mass, scope, player, as-of and sorted correction-relevant scoped matches, appearances, events and identities. Adding reviewed games updates knowledge; correction, reattribution or deletion rebuilds it from source evidence. No append-only learned totals can retain a mistaken attribution.

## Relationships and bounds

Relationship edges are built only for the requested canonical player:

- `co_rostered_teammate` / `co_rostered_opponent` use actual authorized appearances in the same match and their historical sides. They do **not** establish simultaneous field time, exposure facing an opponent, friendship, chemistry or passing partnerships.
- `direct_duel` requires an explicitly opponent-attributed approved duel on opposite sides. Each edge gives distinct match IDs/count, first/last match dates, event IDs and event count. `explicit_contest_count` is available only when all supporting events carry contest IDs; consistent mirrored records can increase event count but not explicit contest count. No recipient IDs means no inferred pass partnerships.

Input contracts cap 1,000 matches, 10,000 appearances/events/identities. The player-centered graph caps 10,000 edges and fails closed if exceeded, rather than returning hidden partial history. Identity service bounds are 100 roster entries, 10,000 decisions per match, 10,000 linked events per decision, 1,000 processing jobs per upload and 50,000 observations per exact track. SQL queries select aggregates or bounded lists, not whole video observations. Identity overlap detection in memory is an O(n log n) interval sweep, tested against a quadratic oracle; it does not construct an all-player quadratic graph.

## Tests and remaining integration responsibilities

`backend/tests/test_player_memory.py` covers three-game accumulation, jersey/side/role change, repeated direct matchups, teammate/opponent history, corrections/deletions, null rates, Beta oracle, duplicate/contradictory evidence, stale/conflicting identity, bounded graphs, historical role segmentation, future-game exclusion and deterministic order invariance.

`backend/tests/test_track_identity.py` exercises persistent propose/confirm/reject/unassign/reassign audit trails, conservative candidates, permissions, retention, account/workspace/mode separation, observed intervals, chunk/stale/replacement sources, explicit event binding, mistaken-identity history/relationship repair, approval gates, caller rollback, injected audit failure and competing concurrent confirmations using file-backed SQLite.

These are deterministic synthetic software tests, not trained-model or youth subgroup validation. No PostgreSQL server, detector runtime, UI/browser or production upload was exercised by this lane. API/UI integration must actually call the service gates and source-invalidation helper, preserve full response types, implement the shared write-lock protocol and rebuild coherent snapshots after review. Existing ordinary analytics must not bypass these attribution fences when rendering identity-linked records. No shared API/frontend files were modified by this implementation.
