"""Validated configuration; production security settings fail closed."""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ROOT = PROJECT_ROOT / ".local"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCOUT_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    environment: str = "development"
    database_url: str = f"sqlite:///{LOCAL_ROOT / 'scout.db'}"
    storage_root: Path = LOCAL_ROOT / "storage"
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    secret_key: str | None = Field(default=None, repr=False)
    cookie_secure: bool = False
    session_ttl_hours: int = Field(default=24, ge=1, le=168)
    max_upload_bytes: int = Field(default=8 * 1024**3, ge=1024, le=16 * 1024**3)
    max_video_seconds: float = Field(default=10800, gt=0, le=14400)
    max_video_dimension: int = Field(default=4096, ge=320, le=8192)
    probe_timeout_seconds: int = Field(default=30, ge=1, le=120)
    ffprobe_binary: str = "ffprobe"
    worker_poll_seconds: float = Field(default=2, ge=0.1, le=60)
    worker_lease_seconds: int = Field(default=300, ge=30, le=3600)
    max_job_attempts: int = Field(default=3, ge=1, le=10)
    processing_chunk_seconds: float = Field(default=120, ge=10, le=600)
    processing_sample_interval_seconds: float = Field(default=0.5, ge=0.05, le=10)
    processing_max_chunks: int = Field(default=1440, ge=1, le=1440)
    processing_observation_batch_size: int = Field(default=500, ge=1, le=2000)
    processing_max_chunk_attempts: int = Field(default=3, ge=1, le=10)
    allow_signup: bool = True
    # Operator-only, strict scoped JSON; never a client path/model upload.
    action_value_artifact_path: Path | None = None

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        if value not in {"development", "test", "production"}:
            raise ValueError("environment must be development, test, or production")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith(("sqlite:///", "sqlite+pysqlite:///", "postgresql+psycopg://")):
            raise ValueError("use sqlite:/// or postgresql+psycopg://")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("at least one explicit browser origin is required")
        result = []
        for value in values:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "*" in value
            ):
                raise ValueError("origins must be exact http(s) origins without paths or wildcards")
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError("invalid origin port") from exc
            result.append(value)
        return list(dict.fromkeys(result))

    @field_validator("storage_root")
    @classmethod
    def normalize_storage_root(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("ffprobe_binary")
    @classmethod
    def validate_probe_binary(cls, value: str) -> str:
        if not value or "\x00" in value or "://" in value:
            raise ValueError("ffprobe must be a local executable, not a URL")
        return value

    @model_validator(mode="after")
    def validate_security(self) -> Settings:
        if self.secret_key is not None and len(self.secret_key) < 32:
            raise ValueError("configured secret key must contain at least 32 characters")
        if self.worker_lease_seconds <= self.probe_timeout_seconds + 10:
            raise ValueError("worker lease must exceed probe timeout by more than 10 seconds")
        if self.environment == "production":
            if not self.secret_key:
                raise ValueError("production requires SCOUT_SECRET_KEY")
            if not self.cookie_secure:
                raise ValueError("production requires SCOUT_COOKIE_SECURE=true")
            if any(not origin.startswith("https://") for origin in self.allowed_origins):
                raise ValueError("production requires explicit HTTPS origins")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def ensure_local_secret(settings: Settings) -> str:
    """Return configured secret or atomically create a private development key.

    Raw session tokens still belong only in HttpOnly cookies; this key is available
    to the API for domain-separated signing/HMAC if needed. Never log its value.
    """
    if settings.secret_key:
        return settings.secret_key
    if settings.environment == "production":
        raise RuntimeError("production security configuration missing")
    root = settings.storage_root.parent
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / "secret.key"
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | nofollow, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY | nofollow)
        with os.fdopen(fd, "r", encoding="ascii") as stream:
            if os.fstat(stream.fileno()).st_mode & 0o077:
                raise RuntimeError("local secret file must be private (chmod 600)")
            value = stream.read(513).strip()
        if not 32 <= len(value) <= 512:
            raise RuntimeError("invalid local secret file")
        return value
    value = secrets.token_urlsafe(48)
    with os.fdopen(fd, "w", encoding="ascii") as stream:
        stream.write(value + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return value
