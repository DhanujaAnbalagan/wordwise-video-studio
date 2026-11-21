"""Runtime configuration, read from environment variables (see ``.env.example``).

Nothing secret has a real default. In production (``ENV=production``) the
service refuses to start with insecure settings (wildcard CORS, MinIO storage
without credentials).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    database_url: str = Field(default="sqlite:///./data/videoflow.db", description="SQLAlchemy URL")
    data_dir: Path = Field(default=Path("./data"), description="Working directory for local storage, renders and caches")

    storage_backend: Literal["local", "minio"] = "local"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "videoflow"
    minio_secure: bool = False

    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"

    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:8080", "http://localhost:5173"])
    max_upload_mb: int = 1024
    worker_threads: int = 2
    job_timeout_s: int = 3600
    idempotency_ttl_hours: int = 24

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):  # accept "a,b" from env
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.env == "production":
            if "*" in self.cors_origins:
                raise ValueError("CORS_ORIGINS must list explicit origins in production.")
            if self.storage_backend == "minio" and not (self.minio_access_key and self.minio_secret_key):
                raise ValueError("MINIO_ACCESS_KEY and MINIO_SECRET_KEY are required in production.")
        return self

    def safe_dict(self) -> dict:
        """Settings without secrets (for logs / readiness output)."""
        d = self.model_dump()
        for k in list(d):
            if any(s in k for s in ("secret", "password", "token", "access_key")):
                d[k] = "***" if d[k] else ""
        d["data_dir"] = str(d["data_dir"])
        return d


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
