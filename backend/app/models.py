"""Persistent private workspace records; no synthetic data is seeded on import.

Tenant, mode and roster consistency must also be checked in scoped API/worker
queries. A valid foreign key alone does not authorize access to another account.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


def new_id() -> str:
    return str(uuid4())


class IdentityMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)


class CreatedMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class WorkspaceMixin:
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False)


class SyntheticMixin:
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)


class SchemaVersion(Base):
    __tablename__ = "schema_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (CheckConstraint("id = 1", name="schema_singleton"),)


class SchemaMigration(Base):
    """Database-level migration ledger; contains no workspace/player evidence."""
    __tablename__ = "schema_migrations"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Workspace(IdentityMixin, CreatedMixin, Base):
    __tablename__ = "workspaces"
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    demo_seeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Account(IdentityMixin, CreatedMixin, WorkspaceMixin, Base):
    __tablename__ = "accounts"
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("workspace_id", name="one_account_per_workspace"),)


class AuthSession(CreatedMixin, WorkspaceMixin, Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)


class Player(IdentityMixin, CreatedMixin, WorkspaceMixin, SyntheticMixin, Base):
    __tablename__ = "players"
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    age_group: Mapped[str] = mapped_column(String(20), nullable=False)
    primary_role: Mapped[str] = mapped_column(String(20), default="unknown", nullable=False)
    team_name: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    __table_args__ = (
        CheckConstraint("primary_role IN ('goalkeeper','defender','midfielder','forward','unknown')", name="player_role"),
        Index("ix_players_workspace_mode", "workspace_id", "is_synthetic"),
    )


class Match(IdentityMixin, CreatedMixin, WorkspaceMixin, SyntheticMixin, Base):
    __tablename__ = "matches"
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    match_date: Mapped[date] = mapped_column(Date, nullable=False)
    competition: Mapped[str] = mapped_column(String(100), nullable=False)
    season: Mapped[str] = mapped_column(String(40), nullable=False)
    age_group: Mapped[str] = mapped_column(String(20), nullable=False)
    team_name: Mapped[str] = mapped_column(String(100), nullable=False)
    opponent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, default=5400, nullable=False)
    __table_args__ = (
        CheckConstraint("duration_seconds > 0 AND duration_seconds <= 14400", name="match_duration"),
        Index("ix_matches_workspace_cohort", "workspace_id", "is_synthetic", "age_group", "competition", "season"),
    )


class Appearance(IdentityMixin, WorkspaceMixin, SyntheticMixin, Base):
    __tablename__ = "appearances"
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True, nullable=False)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"), index=True, nullable=False)
    jersey_number: Mapped[int | None] = mapped_column(Integer)
    side: Mapped[str] = mapped_column(String(8), default="home", nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="unknown", nullable=False)
    played_seconds: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    reviewed_seconds: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    __table_args__ = (
        UniqueConstraint("match_id", "player_id", name="one_appearance_per_player_match"),
        UniqueConstraint("match_id", "side", "jersey_number", name="unique_match_side_jersey"),
        CheckConstraint("jersey_number IS NULL OR (jersey_number >= 0 AND jersey_number <= 99)", name="jersey_bounds"),
        CheckConstraint("side IN ('home','away')", name="appearance_side"),
        CheckConstraint("role IN ('goalkeeper','defender','midfielder','forward','unknown')", name="appearance_role"),
        CheckConstraint("played_seconds >= 0 AND reviewed_seconds >= 0 AND reviewed_seconds <= played_seconds", name="reviewed_coverage_bounds"),
    )


class RightsAttestation(IdentityMixin, CreatedMixin, WorkspaceMixin, SyntheticMixin, Base):
    __tablename__ = "rights_attestations"
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), unique=True, nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    permission_to_process: Mapped[bool] = mapped_column(Boolean, nullable=False)
    guardian_or_club_authorization: Mapped[bool] = mapped_column(Boolean, nullable=False)
    opponent_privacy_review: Mapped[bool] = mapped_column(Boolean, nullable=False)
    authorized_roster: Mapped[bool] = mapped_column(Boolean, nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    __table_args__ = (CheckConstraint("retention_days >= 1 AND retention_days <= 365", name="retention_bounds"),)


class Upload(IdentityMixin, CreatedMixin, WorkspaceMixin, SyntheticMixin, Base):
    __tablename__ = "uploads"
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), unique=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(180), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="stored", nullable=False)
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="upload_nonempty"),
        CheckConstraint("status IN ('stored','probed','rejected')", name="upload_status"),
    )


class ProcessingJob(IdentityMixin, CreatedMixin, WorkspaceMixin, SyntheticMixin, Base):
    __tablename__ = "processing_jobs"
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True, nullable=False)
    upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id", ondelete="CASCADE"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(50), default="queued", nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    message: Mapped[str] = mapped_column(String(500), default="Waiting for private processing", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','needs_configuration','needs_review','completed','failed')", name="job_status"),
        CheckConstraint("progress >= 0 AND progress <= 1", name="job_progress"),
        CheckConstraint("attempts >= 0", name="job_attempts"),
        Index("ix_jobs_claim", "status", "created_at"),
    )


class ProcessingChunk(IdentityMixin, CreatedMixin, WorkspaceMixin, SyntheticMixin, Base):
    """Durable [start,end) detector work; completion is NOT reviewed event coverage.

    IDs/geometry are immutable after planning. A retry may claim only unfinished
    chunks. Fence writes by lease_token + revision and the live parent-job lease;
    commit observations and completion together. Never delete completed chunks
    or confirmed identities on retry. track_id uses chunk:<chunk UUID>:<local ID>.
    """
    __tablename__ = "processing_chunks"
    job_id: Mapped[str] = mapped_column(ForeignKey("processing_jobs.id", ondelete="CASCADE"), index=True, nullable=False)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True, nullable=False)
    upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    sample_interval_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    plan_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    processed_through_seconds: Mapped[float | None] = mapped_column(Float)
    decoded_frames: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sampled_frames: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    worker_id: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model_version: Mapped[str | None] = mapped_column(String(120))
    result_fingerprint: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(80))
    message: Mapped[str] = mapped_column(String(500), default="Waiting for bounded processing", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    __mapper_args__ = {"version_id_col": revision}
    __table_args__ = (
        UniqueConstraint("job_id", "sequence", name="one_chunk_per_job_sequence"),
        CheckConstraint("sequence >= 0 AND sequence < 1440", name="chunk_sequence"),
        CheckConstraint("start_seconds >= 0 AND end_seconds > start_seconds AND end_seconds <= 14400 AND end_seconds - start_seconds <= 600", name="chunk_interval"),
        CheckConstraint("sample_interval_seconds >= 0.05 AND sample_interval_seconds <= 10", name="chunk_sampling"),
        CheckConstraint("status IN ('pending','running','completed','failed')", name="chunk_status"),
        CheckConstraint("progress >= 0 AND progress <= 1", name="chunk_progress"),
        CheckConstraint("processed_through_seconds IS NULL OR (processed_through_seconds >= start_seconds AND processed_through_seconds <= end_seconds)", name="chunk_absolute_coverage"),
        CheckConstraint("decoded_frames >= 0 AND sampled_frames >= 0 AND sampled_frames <= decoded_frames AND sampled_frames <= 10000 AND observation_count >= 0 AND observation_count <= 50000", name="chunk_frame_counts"),
        CheckConstraint("attempts >= 0 AND revision >= 1", name="chunk_attempt_revision"),
        CheckConstraint("status <> 'running' OR (lease_token IS NOT NULL AND worker_id IS NOT NULL AND lease_expires_at IS NOT NULL AND attempts > 0)", name="chunk_running_lease"),
        CheckConstraint("status <> 'completed' OR (progress = 1 AND processed_through_seconds IS NOT NULL AND processed_through_seconds = end_seconds AND sampled_frames > 0 AND model_version IS NOT NULL AND result_fingerprint IS NOT NULL AND finished_at IS NOT NULL)", name="chunk_complete_evidence"),
        Index("ix_chunks_claim", "job_id", "status", "sequence"),
    )


class TrackObservation(IdentityMixin, WorkspaceMixin, SyntheticMixin, Base):
    """Optional raw evidence only; no inference is installed by this model."""
    __tablename__ = "track_observations"
    processing_chunk_id: Mapped[str | None] = mapped_column(ForeignKey("processing_chunks.id", ondelete="SET NULL"))
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True, nullable=False)
    upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id", ondelete="CASCADE"), nullable=False)
    player_id: Mapped[str | None] = mapped_column(ForeignKey("players.id", ondelete="SET NULL"))
    track_id: Mapped[str] = mapped_column(String(80), nullable=False)
    timestamp_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    label: Mapped[str] = mapped_column(String(30), nullable=False)
    bbox: Mapped[list] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    pitch_x: Mapped[float | None] = mapped_column(Float)
    pitch_y: Mapped[float | None] = mapped_column(Float)
    calibration_valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    model_version: Mapped[str] = mapped_column(String(120), nullable=False)
    __table_args__ = (
        CheckConstraint("timestamp_seconds >= 0", name="observation_time"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="observation_confidence"),
        Index("ix_track_match_time", "match_id", "track_id", "timestamp_seconds"),
        Index("ix_observation_chunk_time", "processing_chunk_id", "timestamp_seconds", "id"),
    )


class TrackIdentityAssignment(IdentityMixin, CreatedMixin, WorkspaceMixin, SyntheticMixin, Base):
    """Current reviewer decision for a tracklet interval, NEVER face identity.

    Persist before/after/reason in AuditLog in the SAME transaction. Canonical
    player links survive roster replacement (Appearance IDs currently do not).
    The identity service must verify scoped roster membership, observed interval,
    overlap conflicts and use the revision as a compare-and-swap precondition.
    For chunked runs track_id MUST be chunk:<chunk UUID>:<local track ID>, not
    the tracker-local number. Completed chunk retries must preserve this record.
    """
    __tablename__ = "track_identity_assignments"
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True, nullable=False)
    upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id", ondelete="CASCADE"), nullable=False)
    track_id: Mapped[str] = mapped_column(String(80), nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="proposed", nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="reviewer", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    __mapper_args__ = {"version_id_col": revision}
    __table_args__ = (
        CheckConstraint("start_seconds >= 0 AND end_seconds >= start_seconds AND end_seconds <= 14400", name="identity_interval"),
        CheckConstraint("status IN ('proposed','confirmed','rejected')", name="identity_status"),
        CheckConstraint("source IN ('reviewer','model_proposal')", name="identity_source"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="identity_confidence"),
        CheckConstraint("revision >= 1", name="identity_revision"),
        CheckConstraint("status = 'proposed' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)", name="identity_requires_review"),
        Index("ix_identity_track_interval", "workspace_id", "match_id", "upload_id", "track_id", "start_seconds", "end_seconds"),
    )


class ReviewedEvent(IdentityMixin, CreatedMixin, WorkspaceMixin, SyntheticMixin, Base):
    __tablename__ = "reviewed_events"
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True, nullable=False)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"), index=True, nullable=False)
    opponent_player_id: Mapped[str | None] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), default="unknown", nullable=False)
    timestamp_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_timestamp_seconds: Mapped[float | None] = mapped_column(Float)
    start_x: Mapped[float | None] = mapped_column(Float)
    start_y: Mapped[float | None] = mapped_column(Float)
    end_x: Mapped[float | None] = mapped_column(Float)
    end_y: Mapped[float | None] = mapped_column(Float)
    # Legacy coordinates are intentionally not promoted to calibrated pitch data.
    coordinate_system: Mapped[str] = mapped_column(String(20), default="unknown", server_default="unknown", nullable=False)
    coordinate_provenance: Mapped[str | None] = mapped_column(String(500))
    period: Mapped[int | None] = mapped_column(Integer)
    attacking_direction: Mapped[str | None] = mapped_column(String(20))
    possession_retained: Mapped[bool | None] = mapped_column(Boolean)
    shot_outcome: Mapped[str | None] = mapped_column(String(10))
    contest_id: Mapped[str | None] = mapped_column(String(80))
    identity_assignment_id: Mapped[str | None] = mapped_column(ForeignKey("track_identity_assignments.id", ondelete="SET NULL"))
    identity_assignment_revision: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    provenance: Mapped[str] = mapped_column(String(30), default="manual", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    model_version: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (
        CheckConstraint("kind IN ('pass','cross','shot','tackle','interception','recovery','carry','duel','block','header')", name="event_kind"),
        CheckConstraint("outcome IN ('successful','unsuccessful','unknown')", name="event_outcome"),
        CheckConstraint("status IN ('draft','approved','rejected')", name="event_status"),
        CheckConstraint("provenance IN ('manual','model_candidate','synthetic')", name="event_provenance"),
        CheckConstraint("timestamp_seconds >= 0 AND (end_timestamp_seconds IS NULL OR end_timestamp_seconds >= timestamp_seconds)", name="event_time"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="event_confidence"),
        CheckConstraint("opponent_player_id IS NULL OR opponent_player_id <> player_id", name="event_distinct_opponent"),
        CheckConstraint("status <> 'approved' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND outcome <> 'unknown')", name="approval_requires_review"),
        Index("ix_events_workspace_player_review", "workspace_id", "player_id", "status", "is_synthetic"),
    )


class AuditLog(IdentityMixin, CreatedMixin, WorkspaceMixin, SyntheticMixin, Base):
    __tablename__ = "audit_logs"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    match_id: Mapped[str | None] = mapped_column(ForeignKey("matches.id", ondelete="SET NULL"), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    before_json: Mapped[dict | None] = mapped_column(JSON)
    after_json: Mapped[dict | None] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
