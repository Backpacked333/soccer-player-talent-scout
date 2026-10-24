# Vision integrations and evidence boundaries

## What actually runs

The ordinary install runs a durable database-backed worker and local CPU-only ffprobe metadata probing. It does **not** install a detector, download checkpoints, identify players, calibrate a pitch, extract events, or compute talent. Valid video processing ends at **`needs_configuration` / `metadata_ready`**, with real metadata and zero invented candidates or observations. Authorized manual event review remains available.

Run `make worker` separately from the API, or `make worker-once` for at most one queued job. `run_once(settings: Settings | None = None) -> bool` reports whether a job was claimed, not whether inference succeeded. Claims are atomic, leased, retry-bounded, fenced against stale workers, and revalidated for workspace/match/upload/mode/roster/rights/retention before processing and persistence. Child deadlines finish before their worker lease. SQLite concurrency is tested locally; a live PostgreSQL service was not provisioned or tested here.

The probe uses a no-follow file descriptor, argument lists rather than a shell, file-only protocols, a container allowlist, allocation limits and a timeout. Errors suppress decoder output and local paths. This is defense in depth, not a native-code sandbox. Keep FFmpeg patched; run workers unprivileged with OS-level storage/process/network limits. Media is API-controlled private storage, never a public static directory.

## Registry and license inventory

`GET /api/integrations` requires a private session. `ready` describes the named runtime capability, **not** model accuracy, legal clearance, identity, calibration or valid downstream metrics. `optional_not_installed` means absent; `requires_model_configuration` includes incompatible/blocked configurations; `license_blocked` means not adopted pending code/checkpoint/data review. GPL is not inherently noncommercial.

| Capability | Code/model license boundary | Actual product state |
|---|---|---|
| [FFmpeg / ffprobe](https://ffmpeg.org/legal.html) | Build-dependent LGPL-2.1-or-later / GPL | Local metadata only; ready when binary exists |
| [RF-DETR 1.9.2](https://github.com/roboflow/rf-detr/tree/1.9.2) | Apache-2.0 code; checkpoint and training-data rights separate | Opt-in patched CPU anonymous detection, disabled by default; no bundled weights |
| [Supervision / ByteTrack](https://github.com/roboflow/supervision) | MIT; model rights separate | Library availability/version check; optional CPU adapter links segment-local anonymous tracks |
| [Roboflow Sports](https://github.com/roboflow/sports) | MIT examples; included models/dependencies separate | Reference only, no pipeline imported |
| [Kloppy 3.18.0](https://github.com/PySport/kloppy) | BSD-3-Clause | Lazy Python adapter for externally calibrated/reviewed tracking, not an HTTP workflow |
| [socceraction](https://github.com/ML-KULeuven/socceraction) | MIT | Not wired through socceraction; a separate native spatial Markov engine is operator-gated. No xG/VAEP |
| [Qwen2.5-VL-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct) | Model card Apache-2.0; other variants need separate review | Not installed/integrated; no hosted statistics generation |
| [SoccerNet sn-gamestate](https://github.com/SoccerNet/sn-gamestate) | GPL-3.0; dependencies/checkpoint/data separate | License-blocked pending adoption review |
| [Broadcast2Pitch](https://github.com/yinmayoo185/SoccernetGSR) | No root code license identified during review; checkpoint/dependency rights unresolved | Research only, license-blocked |
| [FOOTPASS](https://github.com/JeremieOchin/FOOTPASS) | Apache LICENSE conflicts with CC BY-NC README; dataset NDA-gated | Blocked until terms/data authorization are resolved in writing |
| [SoccerMaster](https://github.com/haolinyang-hlyang/SoccerMaster) | Apache weights-card label does not clear code or embedded dependencies | Research only, license-blocked |

No weights or datasets were downloaded. Code licenses, checkpoint licenses, training-data rights, footage permissions and distribution obligations are separate questions. This inventory is not legal clearance. Broadcast2Pitch's 2025 SoccerNet result motivates research, not adoption; its CUDA, YOLOX, OSNet, keypoint, CLIP and LLaMA dependencies require individual review.

## RF-DETR: patched optional CPU runtime

The former unsafe recipe is superseded, not re-enabled unchanged. The new separate `backend/requirements-vision.txt` pins the compatible core/CPU stack: RF-DETR 1.9.2, Supervision 0.29.1, PyAV 16.0.1, Transformers 5.20.2 and torch/torchvision 2.14.1/0.29.1 CPU wheels, with one OpenCV provider. Ordinary setup still does not install it. Exact commands, random-weight compatibility evidence and time-bound audit caveats are in [FULL_MATCH.md](FULL_MATCH.md).

`detector_readiness(...)` remains disabled by default. Enabling requires `SCOUT_VISION_ENABLED`, `SCOUT_VISION_LICENSE_REVIEWED`, `SCOUT_VISION_SECURITY_REVIEWED`, an operator-controlled absolute model root separate from upload storage, basename checkpoint, exact SHA-256 and class mapping. All these are server/operator configuration, not user requests. Safe loading uses `weights_only=True`/`trust_checkpoint=False`, exact architecture keys/shapes and no model downloads. Legacy base and legacy Large are supported; the newer 2026 Large architecture is not silently substituted. These checks are not legal verification or a native-code sandbox.

### Optional GPU setup boundary

GPU execution is **not enabled or validated**; the optional runtime is CPU-only. Do not provision a GPU just to make the registry green. The metadata/manual-review product works without optional inference. Before a future GPU deployment:

1. Select reviewed code and licensed checkpoint/data; resolve a compatible patched Torch/torchvision/Transformers/accelerate set in a separate environment, remove duplicate OpenCV providers and commit a new audited lock.
2. Run import/API compatibility tests, offline checkpoint loading and synthetic-video inference before any youth footage. Never download weights implicitly. Validate CPU first where possible; then validate GPU driver/CUDA compatibility, memory budgets and failure behavior on the actual hardware.
3. Preserve a trusted absolute model root separate from upload storage, basename-only checkpoint selection, independently verified SHA-256, exact class mapping, offline mode, OS network isolation, time/frame/observation limits and worker lease fencing. A checksum identifies bytes; it does not establish model trust.
4. A reviewed code change and hardware validation are needed to enable GPU execution. Re-run known-advisory audits and actual inference tests; separately validate youth-domain detection/tracking accuracy and safeguarding. No soccer accuracy or production-throughput claim is made here.

## Anonymous detection semantics

`analyze_video(video_path: Path, *, settings: Settings) -> VisionResult` remains compatible and probes first. Configured whole-source processing uses independently leased/persisted chunks, not a first-frame-budget prefix. Dependency-double regressions exercise:

- source frame PTS/time base relative to container start rather than guessed nominal FPS; rejection of missing/nonfinite/non-increasing timing and changing dimensions;
- separate ByteTrack instances per semantic label, plus new track epochs after large timing gaps;
- normalized image boxes and version/checkpoint provenance, with null player IDs, null pitch coordinates and `calibration_valid=false`;
- explicit frame/observation budgets and partial-processing warnings.

This is not camera-cut detection, cross-match re-identification, jersey assignment, ball possession, pitch calibration, speed/distance or event extraction. Even contiguous high-confidence tracks can switch people. Confidence is not calibrated identity probability or a talent score. Automatic results remain `needs_review`; unresolved candidates stay unresolved and approved automatic events/assignments are rejected. Current statistics use only reviewer-approved evidence, with current confirmed attribution required for model candidates. The CPU random-weight forwards/full-span synthetic mechanics proof described in FULL_MATCH.md establishes neither trained accuracy nor identity quality.

## Reviewed intelligence integration

The registry also lists local reviewed memory, direct-duel estimation and spatial action value. [ALGORITHMS.md](ALGORITHMS.md) documents their real authenticated routes/UI. Canonical identity is reviewer-confirmed, not face recognition; accumulated records are current-reviewed knowledge, not autonomous visual learning. Bradley–Terry estimates consume explicit deduplicated contests and do not imply universal opponent quality. Spatial values require an exact-scope, operator-activated bounded JSON artifact and current evidence; a configured path alone is not readiness. No live user model uploads, remote model URLs, shipped trained grid or fake action values exist.

## Kloppy boundary

`app.adapters.kloppy_export.to_kloppy(...)` is a lazy Python adapter, not an HTTP endpoint. With Kloppy 3.18.0 it returns a `TrackingDataset` only for finite metric coordinates within explicit pitch dimensions, validated calibration flags, explicit source-time period bounds and reviewer-attributed roster assignments. Conflicting player/team/jersey assignments and duplicate simultaneous player observations are rejected. The caller must authenticate reviewer authority and verify calibration provenance; constructing a typed assignment is not authenticated approval. Referee export is rejected. Missing players are absent, not interpolated. Do not guess youth pitch dimensions or relabel image pixels as meters. Raw detector boxes fail this contract.

## Validation

`make check` runs shell/API tests using isolated SQLite databases, generated synthetic video and dependency doubles. It covers atomic claims, lease recovery/fencing, retries, permissions, tenant/mode/path/retention/size boundaries, real ffprobe, subprocess failures/timeouts, metadata-only honesty, reviewed correction/recomputation and persistence. Two actual Kloppy-domain tests skip when the optional package is absent. The base environment remains independent of it. These tests do not validate a detector, full game-state reconstruction, GPU execution, calibration quality or youth-domain inference accuracy.
