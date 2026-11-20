"""Configuration module for VideoFlow AI backend.
It uses Pydantic's BaseSettings to read environment variables.
All values have sensible defaults for local development; no credentials are
hard‑coded – they must be supplied via the environment when running in
production.
"""

from __future__ import annotations

import os
from pathlib import Path
from pydantic import BaseSettings, Field, AnyUrl, validator

class Settings(BaseSettings):
    # Core service settings
    database_url: str = Field(
        default="sqlite:///videoflow.db",
        env="DATABASE_URL",
        description="SQLAlchemy database URL",
    )

    # MinIO / S3 compatible storage
    minio_endpoint: AnyUrl = Field(
        default="http://localhost:9000",
        env="MINIO_ENDPOINT",
        description="MinIO server endpoint",
    )
    minio_access_key: str = Field(
        default="",
        env="MINIO_ACCESS_KEY",
        description="MinIO access key",
    )
    minio_secret_key: str = Field(
        default="",
        env="MINIO_SECRET_KEY",
        description="MinIO secret key",
    )
    minio_bucket: str = Field(
        default="videoflow",
        env="MINIO_BUCKET",
        description="Default bucket for video assets",
    )

    # Executable paths – assumes they are on $PATH for dev.
    ffmpeg_path: str = Field(
        default="ffmpeg",
        env="FFMPEG_PATH",
        description="Path to ffmpeg executable",
    )
    ffprobe_path: str = Field(
        default="ffprobe",
        env="FFPROBE_PATH",
        description="Path to ffprobe executable",
    )

    # Whisper settings – placeholder values.
    whisper_provider: str = Field(
        default="openai",
        env="WHISPER_PROVIDER",
        description="Provider for Whisper transcription",
    )
    whisper_model: str = Field(
        default="base",
        env="WHISPER_MODEL",
        description="Model name to use with the Whisper provider",
    )

    class Config:
        env_file = ".env"
        case_sensitive = False
        # Allow loading from a .env file in the project root.
        env_file_encoding = "utf-8"

    @validator("minio_access_key", "minio_secret_key")
    def non_empty_credentials(cls, v, values, field):
        # In development the credentials can be empty; otherwise raise.
        if not v and os.getenv("ENV") == "production":
            raise ValueError(f"{field.name} must be set in production")
        return v

# Export a singleton for convenient import throughout the codebase.
settings = Settings()
