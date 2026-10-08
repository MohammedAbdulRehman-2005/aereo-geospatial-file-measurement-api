"""Application configuration using Pydantic Settings."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application configuration. Fails fast on invalid critical values."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    # Database
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/aereo_geo",
        description="SQLAlchemy async-compatible PostgreSQL DSN.",
    )

    # File processing limits
    max_upload_mb: int = Field(default=25, ge=1, le=500)
    max_extracted_mb: int = Field(default=100, ge=1, le=2000)
    max_features: int = Field(default=100_000, ge=1)

    # AI configuration
    ai_enabled: bool = False
    ai_provider: Literal["none", "openai", "gemini", "mock"] = "none"
    ai_model: str = ""
    ai_api_key: str = ""
    ai_max_output_tokens: int = Field(default=800, ge=100, le=8192)

    # Temporary storage
    temp_dir: str = ""  # Empty means system default

    @model_validator(mode="after")
    def validate_ai_config(self) -> "Settings":
        if self.ai_enabled and self.ai_provider not in ("none", "mock"):
            if not self.ai_api_key:
                raise ValueError(
                    "AI_API_KEY must be set when AI_ENABLED=true and "
                    f"AI_PROVIDER={self.ai_provider}"
                )
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_extracted_bytes(self) -> int:
        return self.max_extracted_mb * 1024 * 1024


# Singleton instance
settings = Settings()
