"""Isolated optional RF-DETR 1.9.2 + Supervision ByteTrack + PyAV adapter.

No network, auto-downloads, face recognition, jersey guesses, or event heuristics.
Only operator-reviewed tensor checkpoints load with weights_only=True.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import socket
import sys
from pathlib import Path
from typing import Any

from app.config import Settings
from app.integrations import VisionSettings, detector_readiness
from app.processing_contracts import chunk_scoped_track_id
from app.schemas import ChunkRequest, ChunkResult, ObservationWrite, VideoMetadata


def _offline(*_args, **_kwargs):
    raise RuntimeError("network disabled in the inference adapter")


def detect(path: Path, metadata: VideoMetadata, config: VisionSettings, chunk: ChunkRequest | None = None) -> dict:
    status, detail = detector_readiness(config, Settings(_env_file=None, environment="test", storage_root=path.parent))
    if status != "ready":
        raise ValueError(detail)
    return _detect_research(path, metadata, config, chunk=chunk)


def _load_detector(checkpoint: Path, config: VisionSettings):
    import torch
    from rfdetr import RFDETRBase, RFDETRLargeDeprecated

    # Upstream permits partial loads for training; inference must not silently
    # leave a random detection head or backbone behind.
    payload = torch.load(checkpoint, map_location="cpu", weights_only=True)
    state = payload.get("model") if isinstance(payload, dict) else None
    if not isinstance(state, dict) or not state or any(not isinstance(value, torch.Tensor) for value in state.values()):
        raise ValueError("a complete tensor-only model state is required")
    head = state.get("class_embed.weight")
    if head is None or head.shape[0] != config.num_classes + 1:
        raise ValueError("checkpoint class count differs from configured labels")
    detector_class = RFDETRBase if config.architecture == "base" else RFDETRLargeDeprecated
    detector = detector_class(pretrain_weights=str(checkpoint), num_classes=config.num_classes, device=config.device, trust_checkpoint=False)
    expected = detector.model.model.state_dict()
    missing = set(expected) - set(state)
    if missing - {"_kp_active_mask"} or set(state) - set(expected):
        raise ValueError("checkpoint tensor keys do not match the selected architecture")
    if any(state[key].shape != expected[key].shape for key in state):
        raise ValueError("checkpoint tensor shapes do not match the selected architecture")
    return detector


def _detect_research(path: Path, metadata: VideoMetadata, config: VisionSettings, chunk: ChunkRequest | None = None) -> dict:
    """Internal inference loop; public entrypoint enforces runtime compatibility."""
    checkpoint = config.checkpoint_path()
    if not config.enabled or not config.license_reviewed or not config.security_reviewed or checkpoint is None:
        raise ValueError("trusted local model configuration required")
    with checkpoint.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != config.checkpoint_sha256:
        raise ValueError("checkpoint checksum mismatch")
    # Deny network before optional imports. Model dependencies cannot silently
    # download pretrained encoders or call hosted inference/telemetry services.
    socket.create_connection = _offline
    # Deliberate child-only method replacement; not a normal instance assignment.
    socket.socket.connect = _offline  # type: ignore[method-assign]
    socket.socket.connect_ex = _offline  # type: ignore[method-assign]
    socket.socket.sendto = _offline  # type: ignore[method-assign]
    import av
    import numpy as np
    import supervision as sv
    import torch
    torch.set_num_threads(2)
    detector = _load_detector(checkpoint, config)
    # ByteTrack is class-agnostic: separate trackers prevent a ball/referee
    # detection from inheriting a player's anonymous track across frames.
    label_classes = {label: [key for key, value in config.labels.items() if value == label] for label in sorted(set(config.labels.values()))}
    def new_trackers():
        rate = min(metadata.fps, 1 / chunk.sample_interval_seconds) if chunk else metadata.fps
        return {label: sv.ByteTrack(frame_rate=max(1, round(rate))) for label in label_classes}
    trackers = new_trackers()
    epoch = 0
    observations: list[dict[str, Any]] = []
    warnings = ["Anonymous tracks are local to this upload, not confirmed roster identities. No events or player metrics were inferred.", "Image boxes are normalized x1,y1,x2,y2; no pitch calibration, distance, speed or possession is available.", "Confidence is a detector score, not identity confidence or a calibrated probability. Camera cuts and occlusions require human track review."]
    model_version = f"rf1.9.2-bt0.29.1-v2/{config.architecture}/sha256:{digest}"
    processed = 0
    decoded = 0
    last_time = -1.0
    first_in_segment = None
    last_in_segment = None
    next_sample = chunk.start_seconds if chunk else 0.0
    limited = False
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as media, av.open(media, mode="r", options={"protocol_whitelist": "file", "format_whitelist": "mov,matroska,webm,avi"}) as container:
        stream = container.streams.video[0]
        stream.thread_type = "SLICE"
        stream.thread_count = 2
        start_pts = stream.start_time
        # Container time origin preserves video/audio offsets in browser seeks.
        # PyAV container.start_time is in AV_TIME_BASE microseconds, not frame PTS.
        origin_seconds = float(container.start_time) / 1_000_000 if container.start_time is not None else None
        if chunk and chunk.start_seconds > 0:
            if origin_seconds is None:
                if start_pts is None or stream.time_base is None:
                    raise ValueError("absolute source clock unavailable for seek")
                origin_seconds = float(start_pts * stream.time_base)
            container.seek(int((chunk.start_seconds + origin_seconds) / float(stream.time_base)), stream=stream, backward=True, any_frame=False)
        with torch.inference_mode():
            for frame in container.decode(stream):
                if not chunk and processed >= config.max_frames:
                    limited = True
                    break
                if frame.pts is None or frame.time_base is None:
                    raise ValueError("source frame timestamps unavailable")
                if start_pts is None:
                    start_pts = frame.pts
                if origin_seconds is None:
                    origin_seconds = float(start_pts * frame.time_base)
                timestamp = float(frame.pts * frame.time_base) - origin_seconds
                if not math.isfinite(timestamp) or timestamp < 0 or timestamp <= last_time or timestamp > metadata.duration_seconds:
                    raise ValueError("invalid source frame timestamps")
                if chunk and timestamp >= chunk.end_seconds:
                    break
                if chunk and timestamp < chunk.start_seconds:
                    last_time = timestamp
                    continue
                if frame.width != metadata.width or frame.height != metadata.height:
                    raise ValueError("changing frame dimensions are unsupported")
                if last_time >= 0 and timestamp - last_time > max(config.tracking_gap_seconds, 3 / metadata.fps):
                    # Never join identities across a significant unobserved gap.
                    trackers = new_trackers()
                    epoch += 1
                last_time = timestamp
                decoded += 1
                if first_in_segment is None:
                    first_in_segment = timestamp
                last_in_segment = timestamp
                if chunk:
                    if timestamp + 1e-8 < next_sample:
                        continue
                    next_sample = chunk.start_seconds + (math.floor((timestamp - chunk.start_seconds) / chunk.sample_interval_seconds + 1e-8) + 1) * chunk.sample_interval_seconds
                    if processed >= chunk.max_sampled_frames:
                        limited = True
                        break
                rgb = frame.to_ndarray(format="rgb24")
                detections = detector.predict(rgb, threshold=config.threshold)
                if detections.class_id is None or detections.confidence is None:
                    raise ValueError("detector output missing class/confidence")
                allowed = np.isin(detections.class_id, list(config.labels))
                detections = detections[allowed]
                order = np.argsort(-detections.confidence)[:config.max_detections_per_frame]
                detections = detections[order]
                for label, class_ids in label_classes.items():
                    # Update even on empty detections so lost-track buffers age.
                    tracked = trackers[label].update_with_detections(detections[np.isin(detections.class_id, class_ids)])
                    if tracked.tracker_id is None:
                        continue
                    for box, confidence, track_id in zip(tracked.xyxy, tracked.confidence, tracked.tracker_id):
                        if len(observations) >= (chunk.max_observations if chunk else config.max_observations):
                            limited = True
                            break
                        x1, y1, x2, y2 = box.astype(float)
                        if not all(math.isfinite(float(value)) for value in (x1, y1, x2, y2, confidence)):
                            raise ValueError("nonfinite detector output")
                        normalized = (max(0.0, min(1.0, x1 / metadata.width)), max(0.0, min(1.0, y1 / metadata.height)), max(0.0, min(1.0, x2 / metadata.width)), max(0.0, min(1.0, y2 / metadata.height)))
                        if normalized[2] <= normalized[0] or normalized[3] <= normalized[1]:
                            continue
                        local_track_id = f"{label}-e{epoch}-{int(track_id)}"
                        observation = ObservationWrite(processing_chunk_id=chunk.chunk_id if chunk else None, player_id=None,
                            track_id=chunk_scoped_track_id(chunk.chunk_id, local_track_id) if chunk else local_track_id,
                            timestamp_seconds=timestamp, label=label, bbox=normalized, confidence=float(confidence), model_version=model_version)
                        observations.append(observation.model_dump(mode="json"))
                    if limited:
                        break
                processed += 1
                if limited:
                    break
    if processed == 0:
        raise ValueError("no decoded video frames")
    if chunk:
        tolerance = max(3 / metadata.fps, chunk.sample_interval_seconds) + 1e-6
        complete = not limited and first_in_segment is not None and last_in_segment is not None and first_in_segment <= chunk.start_seconds + tolerance and last_in_segment >= chunk.end_seconds - tolerance
        return ChunkResult(chunk_id=chunk.chunk_id, sequence=chunk.sequence, start_seconds=chunk.start_seconds,
            end_seconds=chunk.end_seconds, status="completed" if complete else "failed",
            processed_through_seconds=chunk.end_seconds if complete else last_in_segment,
            decoded_frames=decoded, sampled_frames=processed, model_version=model_version,
            observations=[ObservationWrite.model_validate(row) for row in observations], error_code=None if complete else "segment_incomplete",
            message="Source segment sampled; identities and events remain unreviewed." if complete else "The entire segment could not be sampled within the configured budget.",
            warnings=warnings + ["Track IDs are segment-local. Sampling can miss brief actions; it is not complete event coverage."]).model_dump(mode="json")
    if limited:
        warnings.append(f"Processing budget reached: only the first {processed} frames, through source time {last_time:.3f}s, were inspected. Do not treat this as full-match coverage.")
    else:
        warnings.append(f"Inspected {processed} decoded frames through source time {last_time:.3f}s. Visibility and event-review coverage remain unknown.")
    if epoch:
        warnings.append(f"Tracking restarted at {epoch} presentation-timestamp gaps. Track IDs on opposite sides of those gaps are intentionally unrelated.")
    if not observations:
        warnings.append("No retained detections. This does not prove that players or actions were absent.")
    return {"observations": observations, "warnings": warnings}


def main():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError("input too large")
        payload = json.loads(raw)
        with open(os.devnull, "w") as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
            chunk = ChunkRequest.model_validate(payload["chunk"]) if payload.get("chunk") else None
            result = detect(Path(payload["path"]), VideoMetadata(**payload["metadata"]), VisionSettings(_env_file=None, **payload["config"]), chunk=chunk)
        print(json.dumps(result))
    except Exception:
        print(json.dumps({"error": "detector_unavailable"}))


if __name__ == "__main__":
    main()
