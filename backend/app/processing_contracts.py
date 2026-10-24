"""Bounded full-source processing contracts, not detector/worker implementation.

A complete detector scan at a disclosed cadence is NOT complete event analysis,
continuous tracking, identity confirmation, or an authorized complete event stream.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from app.intelligence.contracts import Digest, Identifier, IntelligenceContract, Seconds

PROCESSING_CONTRACT_VERSION = "touchline-fullmatch-v1"


class FullMatchPolicy(IntelligenceContract):
    chunk_seconds: float = Field(default=120, ge=10, le=600)
    sample_interval_seconds: float = Field(default=0.5, ge=0.05, le=10)
    max_chunks: int = Field(default=1440, ge=1, le=1440)
    max_sampled_frames_per_chunk: int = Field(default=900, ge=1, le=10000)
    max_observations_per_chunk: int = Field(default=20000, ge=1, le=50000)
    observation_batch_size: int = Field(default=500, ge=1, le=2000)
    max_chunk_attempts: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def bounded_sampling(self) -> FullMatchPolicy:
        if math.ceil(self.chunk_seconds / self.sample_interval_seconds) > self.max_sampled_frames_per_chunk:
            raise ValueError("chunk cadence exceeds its frame budget; shorten chunks, never truncate coverage")
        if self.observation_batch_size > self.max_observations_per_chunk:
            raise ValueError("observation batch exceeds per-chunk storage budget")
        return self


class ChunkInterval(IntelligenceContract):
    sequence: int = Field(ge=0, lt=1440)
    start_seconds: Seconds
    end_seconds: Seconds

    @model_validator(mode="after")
    def validate_interval(self) -> ChunkInterval:
        if not 0 < self.end_seconds - self.start_seconds <= 600:
            raise ValueError("chunk must be a positive half-open interval of at most 600 seconds")
        return self


class FullMatchPlan(IntelligenceContract):
    schema_version: Literal["touchline-fullmatch-v1"] = "touchline-fullmatch-v1"
    source_duration_seconds: float = Field(gt=0, le=14400)
    source_sha256: Digest
    policy: FullMatchPolicy
    chunks: list[ChunkInterval] = Field(min_length=1, max_length=1440)
    timestamp_convention: Literal["absolute_source_seconds_half_open"] = "absolute_source_seconds_half_open"

    @model_validator(mode="after")
    def validate_complete_partition(self) -> FullMatchPlan:
        if len(self.chunks) > self.policy.max_chunks:
            raise ValueError("full-source plan exceeds the configured chunk count")
        boundary = 0.0
        for sequence, chunk in enumerate(self.chunks):
            if chunk.sequence != sequence or not math.isclose(chunk.start_seconds, boundary, rel_tol=0, abs_tol=1e-6):
                raise ValueError("chunk plan must be ordered, gap-free, nonoverlapping and start at zero")
            if chunk.end_seconds - chunk.start_seconds > self.policy.chunk_seconds + 1e-6:
                raise ValueError("chunk exceeds the disclosed duration budget")
            boundary = chunk.end_seconds
        if not math.isclose(boundary, self.source_duration_seconds, rel_tol=0, abs_tol=1e-6):
            raise ValueError("chunk plan must cover the entire probed source including stoppage time")
        return self


class ProcessingChunkRead(ChunkInterval):
    """Bounded read projection; no secret lease token or observation list."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, from_attributes=True)
    id: Identifier
    workspace_id: Identifier
    job_id: Identifier
    match_id: Identifier
    upload_id: Identifier
    is_synthetic: bool
    sample_interval_seconds: float = Field(ge=0.05, le=10)
    plan_fingerprint: Digest
    status: Literal["pending", "running", "completed", "failed"]
    progress: float = Field(ge=0, le=1)
    processed_through_seconds: Seconds | None
    decoded_frames: int = Field(ge=0)
    sampled_frames: int = Field(ge=0, le=10000)
    observation_count: int = Field(ge=0, le=50000)
    attempts: int = Field(ge=0)
    revision: int = Field(ge=1)
    model_version: str | None
    result_fingerprint: Digest | None
    error_code: str | None
    message: str = Field(max_length=500)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @model_validator(mode="after")
    def validate_progress(self) -> ProcessingChunkRead:
        if self.processed_through_seconds is not None and not self.start_seconds <= self.processed_through_seconds <= self.end_seconds:
            raise ValueError("coverage uses absolute source timestamps within this chunk")
        if self.sampled_frames > self.decoded_frames:
            raise ValueError("sampled frame count exceeds decoded frames")
        if self.status == "completed" and (self.progress != 1 or self.processed_through_seconds != self.end_seconds or not self.sampled_frames or not self.model_version or not self.result_fingerprint or self.finished_at is None):
            raise ValueError("completion requires bounded detector evidence through the chunk end")
        return self


class ProcessingCoverage(IntelligenceContract):
    """Small job-level summary; observation/chunk browsing must be paginated."""
    schema_version: Literal["touchline-fullmatch-v1"] = "touchline-fullmatch-v1"
    status: Literal["not_started", "metadata_only", "partial", "complete", "unavailable"] = "not_started"
    source_duration_seconds: float | None = Field(default=None, gt=0, le=14400)
    covered_seconds: Seconds = 0
    planned_chunks: int = Field(default=0, ge=0, le=1440)
    completed_chunks: int = Field(default=0, ge=0, le=1440)
    failed_chunks: int = Field(default=0, ge=0, le=1440)
    sample_interval_seconds: float | None = Field(default=None, ge=0.05, le=10)
    sampled_frames: int = Field(default=0, ge=0, le=14400000)
    observation_count: int = Field(default=0, ge=0, le=72000000)
    reviewed_event_coverage: Literal["not_established_by_detection"] = "not_established_by_detection"
    interpretation: str = "Coverage is sampled detector processing of source video, not continuous observation, complete event analysis, player identity, or scouting validity."

    @model_validator(mode="after")
    def validate_coverage(self) -> ProcessingCoverage:
        if self.completed_chunks + self.failed_chunks > self.planned_chunks:
            raise ValueError("terminal chunk counts exceed planned chunks")
        if self.covered_seconds and (self.source_duration_seconds is None or self.covered_seconds > self.source_duration_seconds or self.sample_interval_seconds is None):
            raise ValueError("detector coverage needs a source duration and disclosed cadence")
        if self.status in {"not_started", "metadata_only", "unavailable"} and (self.covered_seconds or self.completed_chunks or self.sampled_frames or self.observation_count):
            raise ValueError("metadata or unavailable processing cannot claim detection evidence")
        if self.status == "complete" and (not self.planned_chunks or self.completed_chunks != self.planned_chunks or self.covered_seconds != self.source_duration_seconds or not self.sampled_frames or self.sample_interval_seconds is None):
            raise ValueError("full coverage requires every planned chunk through the actual source end")
        return self


def chunk_scoped_track_id(chunk_id: str, local_track_id: str) -> str:
    """Canonical anonymous scope for observations AND reviewed identity decisions.

    Stable chunk IDs survive retries. Local tracker numbering is never cross-cut
    player identity. Legacy non-chunk tracks remain untouched by migration.
    """
    chunk_uuid = str(UUID(chunk_id))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,37}", local_track_id):
        raise ValueError("local track ID must be 1-37 ASCII letters/digits/_.- characters")
    return f"chunk:{chunk_uuid}:{local_track_id}"
