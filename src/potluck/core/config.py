"""Application configuration using pydantic-settings."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database (using str for flexibility with different drivers)
    database_url: str = Field(
        default="postgresql+asyncpg://potluck:potluck@localhost:5432/potluck",
        description="Async database connection URL",
    )
    sync_database_url: str = Field(
        default="postgresql://potluck:potluck@localhost:5432/potluck",
        description="Sync database connection URL (for Alembic)",
    )

    # Redis
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for Celery broker",
    )

    # Web server
    web_host: str = Field(
        default="0.0.0.0",
        description="Web server bind host",
    )
    web_port: int = Field(
        default=8000,
        description="Web server port",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level",
    )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
