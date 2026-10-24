# Full-match processing and optional CPU inference

The default upload cap is 8 GiB (operator configurable up to 16 GiB); source duration defaults to 3 hours, including halftime, stoppage time and pre/post-match footage. Source-video timestamps are not football match-clock timestamps. Uploads spool to disk and copy in bounded blocks; upload transport itself is not resumable yet, and proxy/body/time limits must also support the chosen size. Allow temporary disk space for both multipart spool and final private storage.

The optional detector worker plans the entire probed duration into half-open source intervals. The policy defaults to 120-second segments and 0.5-second sampling, but the planner shortens segments to fit worst-case frame/detection budgets. With the current default detector budgets, segments are 100 seconds. No first-900-frame cutoff stands in for a complete match. Each worker turn executes one segment, commits its observations and completion atomically, then releases/requeues the job. Completed segments and their chunk-scoped identities survive restart/retry. Source/model/policy fingerprint changes fail closed instead of mixing results. Pending/failed work has bounded retries and stale workers cannot persist after losing the lease.

Completed detection means sampled processing of every source segment, NOT continuous tracking, reviewed event coverage, confirmed identities, calibrated pitch coordinates, or complete match analysis. Tracklets reset across segments; reviewers may map several tracklets to the same rostered player. Subsecond events can be missed by sampling. No real-time throughput or youth accuracy has been established.

## Optional runtime

Ordinary `make setup` does not install models or GPU libraries. The CPU requirements include a compatible core runtime plus pinned RF-DETR 1.9.2, Supervision 0.29.1, PyAV 16.0.1, Transformers 5.20.2 and PyTorch 2.14.1/torchvision 0.29.1 CPU wheels:

```sh
uv venv .local/vision-venv --python 3.11
uv pip install --python .local/vision-venv/bin/python --torch-backend cpu -r backend/requirements-vision.txt
uv pip install --python .local/vision-venv/bin/python --no-deps -e backend
.local/vision-venv/bin/python -m app.worker
```

Checkpoints remain separately supplied operator-controlled local files. Require reviewed code/model/training-data rights, an exact SHA-256, explicit class mapping and the `SCOUT_VISION_ENABLED`, `LICENSE_REVIEWED`, and `SECURITY_REVIEWED` attestations. They are not user upload inputs. Default remains disabled. The `base` architecture is the legacy RFDETRBase; `large` maps specifically to RFDETRLargeDeprecated, NOT the changed 2026 Large model. The adapter refuses partial/mismatched tensor states and unsafe checkpoint deserialization (`weights_only=True`, `trust_checkpoint=False`). Incompatible old checkpoints must be safely exported by their owner, not loaded using a pickle bypass. No model download or hosted inference is allowed by the adapter. GPU execution is not validated/enabled by this runtime.

## Verification boundaries

The patched package set imports successfully. Actual offline CPU forward passes and the adapter's safe local checkpoint loader succeeded for both RFDETRBase and RFDETRLargeDeprecated with deterministic RANDOM weights. The combined core/optional environment's audit covered 69 dependencies with no known findings; PyPI skips local `+cpu` versions, so underlying public torch/torchvision versions were checked separately with no known findings. Advisory results are time-bound, not a security guarantee. No trained detector quality or GPU compatibility was established.

An HTTP/API plus separate-worker-CLI run also processed a real 5,700-second **synthetic color video** through all ten 600-second segments at a deliberately coarse 10-second cadence: 570 actual CPU forward passes, 5,700 seconds covered, final `needs_review`, zero approved events. It took about 440 seconds on the session machine. This is an integration/mechanics smoke with untrained random weights and blank frames, not representative soccer throughput, tracking quality, or a benchmark of the default 0.5-second cadence. The run was isolated from all user records.

Deterministic orchestration tests cover a synthetic 95-minute timeline across 57 independently committed segments, restart recovery, stale result fencing, unchanged completed identities, model/policy mismatch and failure preservation. These are fake detector fixtures, not footage benchmarks. Before production, measure throughput, peak memory, disk usage and tracking/identity errors on authorized labeled full-length games, and validate safeguarding, retention/deletion, authentication, storage and OS-level worker isolation. A Python network deny-list and bounded subprocess are not a complete sandbox.
