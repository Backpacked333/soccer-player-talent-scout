"""Version-one API and worker contracts. Keep frontend/src/types.ts in sync."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.intelligence.contracts import EventSemantics
from app.processing_contracts import ChunkInterval, ProcessingChunkRead, ProcessingCoverage, chunk_scoped_track_id

Id = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")]
Role = Literal["goalkeeper", "defender", "midfielder", "forward", "unknown"]
Mode = Literal["real", "demo"]
EventKind = Literal["pass", "cross", "shot", "tackle", "interception", "recovery", "carry", "duel", "block", "header"]
Outcome = Literal["successful", "unsuccessful", "unknown"]
ReviewStatus = Literal["draft", "approved", "rejected"]
JobStatus = Literal["queued", "running", "needs_configuration", "needs_review", "completed", "failed"]
IntegrationStatus = Literal["ready", "optional_not_installed", "requires_model_configuration", "license_blocked"]
UnitInterval = Annotated[float, Field(ge=0, le=1)]
Seconds = Annotated[float, Field(ge=0, le=14400)]
METHOD_VERSION = "reviewed-actions-beta-v1.0.0"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, str_strip_whitespace=True, allow_inf_nan=False)

    @field_validator("*", mode="before")
    @classmethod
    def normalize_database_timestamps(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class LoginRequest(Contract):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128, repr=False)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("enter a valid email address")
        return value

    # Password whitespace is meaningful; do not apply Contract's string stripping.
    model_config = ConfigDict(extra="forbid", from_attributes=True, str_strip_whitespace=False, allow_inf_nan=False)


class SignupRequest(LoginRequest):
    password: str = Field(min_length=12, max_length=128, repr=False)
    workspace_name: str = Field(min_length=1, max_length=120)

    @field_validator("workspace_name")
    @classmethod
    def clean_workspace_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("workspace name is required")
        return value


class AccountRead(Contract):
    id: Id
    email: str
    workspace_id: Id


class WorkspaceRead(Contract):
    id: Id
    name: str


class SessionResponse(Contract):
    account: AccountRead
    workspace: WorkspaceRead
    csrf_token: str


class PlayerCreate(Contract):
    display_name: str = Field(min_length=1, max_length=100)
    age_group: str = Field(min_length=2, max_length=20)
    primary_role: Role = "unknown"
    team_name: str = Field(default="", max_length=100)
    notes: str = Field(default="", max_length=2000)


class PlayerRead(PlayerCreate):
    id: Id
    workspace_id: Id
    is_synthetic: bool
    created_at: datetime


class MatchCreate(Contract):
    title: str = Field(min_length=1, max_length=160)
    match_date: date
    competition: str = Field(min_length=1, max_length=100)
    season: str = Field(min_length=1, max_length=40)
    age_group: str = Field(min_length=2, max_length=20)
    team_name: str = Field(min_length=1, max_length=100)
    opponent_name: str = Field(min_length=1, max_length=100)
    duration_seconds: float = Field(default=5400, gt=0, le=14400)


class MatchRead(MatchCreate):
    id: Id
    workspace_id: Id
    is_synthetic: bool
    created_at: datetime


class AppearanceCreate(Contract):
    player_id: Id
    jersey_number: int | None = Field(default=None, ge=0, le=99)
    side: Literal["home", "away"] = "home"
    role: Role = "unknown"
    played_seconds: Seconds = 0
    reviewed_seconds: Seconds = 0
    notes: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_coverage(self) -> AppearanceCreate:
        if self.reviewed_seconds > self.played_seconds:
            raise ValueError("reviewed seconds cannot exceed played seconds")
        return self


class AppearanceRead(AppearanceCreate):
    id: Id
    workspace_id: Id
    match_id: Id
    is_synthetic: bool


class RosterWrite(Contract):
    appearances: list[AppearanceCreate] = Field(max_length=100)

    @model_validator(mode="after")
    def validate_unique_players_and_jerseys(self) -> RosterWrite:
        players = [row.player_id for row in self.appearances]
        jerseys = [(row.side, row.jersey_number) for row in self.appearances if row.jersey_number is not None]
        if len(players) != len(set(players)):
            raise ValueError("a player can appear only once per match")
        if len(jerseys) != len(set(jerseys)):
            raise ValueError("jersey numbers must be unique within each side")
        return self


class AttestationCreate(Contract):
    permission_to_process: bool = Field(strict=True)
    guardian_or_club_authorization: bool = Field(strict=True)
    opponent_privacy_review: bool = Field(strict=True)
    authorized_roster: bool = Field(strict=True)
    retention_days: int = Field(default=30, ge=1, le=365)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def require_explicit_permissions(self) -> AttestationCreate:
        if not all((self.permission_to_process, self.guardian_or_club_authorization, self.opponent_privacy_review, self.authorized_roster)):
            raise ValueError("all rights and roster declarations must be explicitly confirmed")
        return self


class AttestationRead(AttestationCreate):
    id: Id
    workspace_id: Id
    match_id: Id
    account_id: Id
    is_synthetic: bool
    created_at: datetime


class UploadRead(Contract):
    id: Id
    workspace_id: Id
    match_id: Id
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    duration_seconds: float | None
    width: int | None
    height: int | None
    fps: float | None
    status: Literal["stored", "probed", "rejected"]
    is_synthetic: bool
    created_at: datetime


class JobRead(Contract):
    id: Id
    workspace_id: Id
    match_id: Id
    upload_id: Id
    status: JobStatus
    stage: str
    progress: UnitInterval
    message: str
    attempts: int
    result: dict[str, Any] = Field(default_factory=dict, validation_alias=AliasChoices("result", "result_json"))
    processing_coverage: ProcessingCoverage | None = None
    is_synthetic: bool
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ProcessingChunkPage(Contract):
    items: list[ProcessingChunkRead] = Field(default_factory=list, max_length=100)
    next_cursor: str | None = Field(default=None, max_length=200)


class UploadResponse(Contract):
    upload: UploadRead
    job: JobRead


class EventWrite(Contract, EventSemantics):
    player_id: Id
    kind: EventKind
    outcome: Outcome = "unknown"
    timestamp_seconds: Seconds
    end_timestamp_seconds: Seconds | None = None
    status: ReviewStatus = "draft"
    opponent_player_id: Id | None = None
    start_x: UnitInterval | None = None
    start_y: UnitInterval | None = None
    end_x: UnitInterval | None = None
    end_y: UnitInterval | None = None
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_evidence(self) -> EventWrite:
        if self.end_timestamp_seconds is not None and self.end_timestamp_seconds < self.timestamp_seconds:
            raise ValueError("event end cannot precede its start")
        if self.opponent_player_id == self.player_id:
            raise ValueError("opponent must differ from actor")
        if (self.start_x is None) != (self.start_y is None) or (self.end_x is None) != (self.end_y is None):
            raise ValueError("coordinates must be supplied in x/y pairs")
        if self.status == "approved" and self.outcome == "unknown":
            raise ValueError("approved events require a reviewed outcome")
        if self.shot_outcome is not None and self.kind != "shot":
            raise ValueError("only explicit shots have shot outcomes")
        if self.contest_id is not None and self.kind != "duel":
            raise ValueError("contest identity is reserved for explicit duels")
        return self


class EventRead(EventWrite):
    # Linkage is set by an audited identity service, not an arbitrary client ID.
    identity_assignment_id: Id | None = None
    identity_assignment_revision: int | None = Field(default=None, ge=1)
    id: Id
    workspace_id: Id
    match_id: Id
    provenance: Literal["manual", "model_candidate", "synthetic"]
    confidence: UnitInterval | None
    model_version: str | None
    created_by: Id
    reviewed_by: Id | None
    reviewed_at: datetime | None
    is_synthetic: bool
    created_at: datetime
    updated_at: datetime


class AuditRead(Contract):
    id: Id
    workspace_id: Id
    account_id: Id
    match_id: Id | None
    entity_type: str
    entity_id: str
    action: str
    before: dict[str, Any] | None = Field(default=None, validation_alias=AliasChoices("before", "before_json"))
    after: dict[str, Any] | None = Field(default=None, validation_alias=AliasChoices("after", "after_json"))
    reason: str
    is_synthetic: bool
    created_at: datetime


class Cohort(Contract):
    age_group: str
    role: Role
    competition: str
    season: str | None = None


class EligibilityPolicy(Contract):
    min_matches: int = Field(default=3, ge=1)
    min_pass_attempts: int = Field(default=30, ge=1)
    min_reviewed_seconds: float = Field(default=1800, gt=0)
    min_coverage_fraction: UnitInterval = 0.6
    ranking_metric: Literal["pass_completion_posterior_lower_95"] = "pass_completion_posterior_lower_95"
    min_duel_attempts: int = Field(default=20, ge=1)
    max_match_opportunity_share: float = Field(default=0.6, gt=0, le=1)
    prior_alpha: float = Field(default=1.0, gt=0)
    prior_beta: float = Field(default=1.0, gt=0)
    credible_mass: float = Field(default=0.95, ge=0.95, le=0.95)
    methodology_version: str = METHOD_VERSION
    description: str = "Order eligible players by the 2.5th percentile of Beta(successes+prior_alpha, failures+prior_beta), not raw completion or talent. Apply the explicit thresholds in this policy independently within the requested age/appearance-role/competition/season cohort. Thresholds are provisional engineering safeguards, not validated scouting science. Unknown roles are unranked."


class RateEstimate(Contract):
    metric: Literal["pass_completion", "duel_success"]
    attempts: int = Field(ge=0)
    successes: int = Field(ge=0)
    failures: int = Field(ge=0)
    relevant_match_count: int = Field(ge=0)
    max_match_opportunity_share: float | None
    observed_pct: float | None
    posterior_mean_pct: float | None
    credible_low_pct: float | None
    credible_high_pct: float | None
    credible_mass: float = 0.95
    prior_alpha: float = 1.0
    prior_beta: float = 1.0
    posterior_alpha: float | None
    posterior_beta: float | None
    reviewed_seconds: float
    coverage_fraction: float | None
    observed_attempts_per_90_reviewed_minutes: float | None
    eligibility: Literal["unranked", "provisional", "eligible"]
    confidence: Literal["insufficient", "limited", "moderate"]
    eligibility_reasons: list[str]
    methodology_version: str = METHOD_VERSION
    interpretation: str = "Conditional estimate for selected reviewed actions; not a population percentile or a forecast. The disclosed Beta(prior_alpha,prior_beta) prior is a mathematical regularizer, not an assumed cohort average; defaults are uniform Beta(1,1). No estimate is shown without observations. Per-90 uses declared review time, not full-match projection."


class MetricSummary(Contract):
    player_id: Id
    cohort: Cohort
    is_synthetic: bool
    match_count: int = Field(ge=0)
    approved_event_count: int = Field(ge=0)
    pass_attempts: int = Field(ge=0)
    successful_passes: int = Field(ge=0)
    pass_completion_pct: float | None = Field(ge=0, le=100)
    played_seconds: float = Field(ge=0)
    reviewed_seconds: float = Field(ge=0)
    coverage_fraction: UnitInterval | None
    confidence: Literal["insufficient", "limited", "moderate"]
    eligibility: Literal["unranked", "provisional", "eligible"]
    eligibility_reasons: list[str]
    rank: int | None = Field(default=None, ge=1)
    methodology_version: str = METHOD_VERSION
    policy: EligibilityPolicy = Field(default_factory=EligibilityPolicy)
    pass_rate: RateEstimate
    duel_rate: RateEstimate
    observed_events: dict[str, dict[str, int]]
    comparison_score: float | None
    comparison_score_label: str = "Lower endpoint of the 95% pass-completion credible interval (%)"
    exposure_source: str = "Authorized appearance played_seconds and reviewer-declared reviewed_seconds; not inferred visibility"
    evidence_gaps: list[str] = Field(default_factory=lambda: ["Reviewed intervals and annotation completeness are not independently verified", "No adjustment for opponent quality, game state, selection bias or within-match correlation"])
    opponent_breakdowns: list[dict] = Field(default_factory=list)


class LeaderboardEntry(MetricSummary):
    display_name: str
    team_name: str


class LeaderboardResponse(Contract):
    cohort: Cohort
    mode: Mode
    is_synthetic: bool
    policy: EligibilityPolicy
    entries: list[LeaderboardEntry]
    methodology_version: str = METHOD_VERSION
    unavailable_metrics: list[str]
    disclaimer: str


class PlayerProfile(Contract):
    player: PlayerRead
    metrics: list[MetricSummary]
    policy: EligibilityPolicy = Field(default_factory=EligibilityPolicy)
    methodology_version: str = METHOD_VERSION
    timeline: list[EventRead]
    unavailable_metrics: list[str]
    disclaimer: str


class IntegrationRead(Contract):
    id: str
    name: str
    status: IntegrationStatus
    license: str
    purpose: str
    detail: str
    source_url: str | None = None


class DemoSeedResponse(Contract):
    players_created: int
    matches_created: int
    message: str


class WorkspaceExport(Contract):
    schema_version: Literal["1.0"] = "1.0"
    exported_at: datetime
    mode: Mode
    synthetic_notice: str | None
    workspace: WorkspaceRead
    players: list[PlayerRead]
    matches: list[MatchRead]
    appearances: list[AppearanceRead]
    events: list[EventRead]
    attestations: list[AttestationRead]


# Internal worker/vision contracts; these are not upload request bodies.
class DisplayGeometry(Contract):
    transform: Literal["identity", "rotate_90_ccw", "rotate_180", "rotate_270_ccw", "flip_x", "flip_y", "transpose", "transverse"]
    aspect_ratio: float = Field(gt=0, le=128)


class VideoMetadata(Contract):
    duration_seconds: float = Field(gt=0, le=14400)
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)
    fps: float = Field(gt=0, le=240)
    codec: str = Field(min_length=1, max_length=80)
    display_geometry: DisplayGeometry | None = None


class CandidateEvent(Contract):
    player_id: Id | None = None
    opponent_player_id: Id | None = None
    kind: EventKind
    outcome: Outcome = "unknown"
    timestamp_seconds: Seconds
    end_timestamp_seconds: Seconds | None = None
    confidence: UnitInterval
    model_version: str = Field(min_length=1, max_length=120)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_candidate(self) -> CandidateEvent:
        if self.end_timestamp_seconds is not None and self.end_timestamp_seconds < self.timestamp_seconds:
            raise ValueError("candidate end cannot precede start")
        if self.player_id is not None and self.player_id == self.opponent_player_id:
            raise ValueError("candidate opponent must differ from actor")
        return self


class ObservationWrite(Contract):
    processing_chunk_id: Id | None = None
    player_id: Id | None = None
    track_id: str = Field(min_length=1, max_length=80)
    timestamp_seconds: Seconds
    label: Literal["player", "goalkeeper", "ball", "referee"]
    bbox: tuple[UnitInterval, UnitInterval, UnitInterval, UnitInterval]
    confidence: UnitInterval
    pitch_x: float | None = None
    pitch_y: float | None = None
    calibration_valid: bool = False
    model_version: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_geometry(self) -> ObservationWrite:
        x1, y1, x2, y2 = self.bbox
        if x2 <= x1 or y2 <= y1:
            raise ValueError("box must have positive normalized area")
        if (self.pitch_x is None) != (self.pitch_y is None):
            raise ValueError("pitch coordinates must be paired")
        if self.pitch_x is not None and not self.calibration_valid:
            raise ValueError("pitch coordinates require valid calibration")
        return self


class ChunkRequest(Contract, ChunkInterval):
    """Internal bounded detector request; timestamps always use the source clock."""
    chunk_id: Id
    source_duration_seconds: float = Field(gt=0, le=14400)
    sample_interval_seconds: float = Field(default=0.5, ge=0.05, le=10)
    max_sampled_frames: int = Field(default=900, ge=1, le=10000)
    max_observations: int = Field(default=20000, ge=1, le=50000)
    plan_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_budget(self) -> ChunkRequest:
        import math

        if self.end_seconds > self.source_duration_seconds:
            raise ValueError("chunk extends beyond the probed source")
        if math.ceil((self.end_seconds - self.start_seconds) / self.sample_interval_seconds) > self.max_sampled_frames:
            raise ValueError("frame budget cannot cover this interval at the requested cadence")
        return self


class ChunkResult(Contract, ChunkInterval):
    """One bounded chunk only. Never aggregate a full match's observations here."""
    chunk_id: Id
    status: Literal["completed", "failed"]
    processed_through_seconds: Seconds | None = None
    decoded_frames: int = Field(ge=0)
    sampled_frames: int = Field(ge=0, le=10000)
    model_version: str | None = Field(default=None, min_length=1, max_length=120)
    observations: list[ObservationWrite] = Field(default_factory=list, max_length=50000)
    error_code: str | None = Field(default=None, max_length=80)
    message: str = Field(max_length=500)
    warnings: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_evidence(self) -> ChunkResult:
        if self.processed_through_seconds is not None and not self.start_seconds <= self.processed_through_seconds <= self.end_seconds:
            raise ValueError("chunk progress must be an absolute source timestamp")
        if self.sampled_frames > self.decoded_frames:
            raise ValueError("sampled frames cannot exceed decoded frames")
        if self.status == "completed" and (self.processed_through_seconds != self.end_seconds or not self.sampled_frames or not self.model_version or self.error_code is not None):
            raise ValueError("complete chunk requires actual detector coverage through its end")
        for observation in self.observations:
            if observation.processing_chunk_id != self.chunk_id or not self.start_seconds <= observation.timestamp_seconds < self.end_seconds:
                raise ValueError("observation crosses chunk identity or absolute time boundaries")
            prefix = f"chunk:{self.chunk_id}:"
            if not observation.track_id.startswith(prefix) or observation.track_id != chunk_scoped_track_id(self.chunk_id, observation.track_id[len(prefix):]):
                raise ValueError("raw local tracker IDs must not cross chunk boundaries")
            if observation.model_version != self.model_version:
                raise ValueError("observation model version differs from its chunk result")
        return self


class ObservationRead(ObservationWrite):
    id: Id
    workspace_id: Id
    match_id: Id
    upload_id: Id
    is_synthetic: bool


class ObservationPage(Contract):
    items: list[ObservationRead] = Field(default_factory=list, max_length=500)
    next_cursor: str | None = Field(default=None, max_length=200)


class TimelineSegment(Contract):
    start_seconds: Seconds
    end_seconds: Seconds
    status: str


class TimelineWindow(Contract):
    job_id: Id
    upload_id: Id
    start_seconds: Seconds
    end_seconds: Seconds
    items: list[ObservationRead] = Field(default_factory=list, max_length=5000)
    truncated: bool = False
    display_geometry: DisplayGeometry | None = None
    segments: list[TimelineSegment] = Field(default_factory=list, max_length=1440)


class VisionResult(Contract):
    status: Literal["needs_configuration", "needs_review", "completed"]
    stage: str = Field(max_length=50)
    message: str = Field(max_length=500)
    metadata: VideoMetadata
    candidates: list[CandidateEvent] = Field(default_factory=list, max_length=10000)
    observations: list[ObservationWrite] = Field(default_factory=list, max_length=100000)
    warnings: list[str] = Field(default_factory=list, max_length=100)
