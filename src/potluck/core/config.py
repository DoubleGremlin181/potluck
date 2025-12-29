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
    sync_database_url: str | None = Field(
        default=None,
        description="Sync database connection URL (for Alembic). Falls back to DATABASE_URL if not set.",
    )

    @property
    def sync_db_url(self) -> str:
        """Get sync database URL, falling back to DATABASE_URL if needed."""
        import os

        # Priority: SYNC_DATABASE_URL > DATABASE_URL env var > convert from async URL
        if self.sync_database_url:
            return self.sync_database_url
        if db_url := os.environ.get("DATABASE_URL"):
            return db_url
        # Convert async URL to sync by removing +asyncpg
        return self.database_url.replace("+asyncpg", "")

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
