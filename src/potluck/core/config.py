"""Application settings. Zero configuration is required for first run.

Precedence: explicit kwargs > ``POTLUCK_*`` env vars > ``config.toml`` > defaults.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from potluck.core.paths import (
    config_dir,
    default_attachments_dir,
    default_db_path,
    default_uploads_dir,
)


class Settings(BaseSettings):
    """Potluck runtime settings."""

    model_config = SettingsConfigDict(env_prefix="POTLUCK_")

    db_path: Path = Field(default_factory=default_db_path)
    host: str = "127.0.0.1"
    port: int = 8765
    web_dist: Path | None = None
    # Attachment policy (#124): metadata always lands in the files table;
    # blob extraction to attachments_dir is opt-in.
    extract_attachments: bool = False
    attachments_dir: Path = Field(default_factory=default_attachments_dir)
    # Managed landing directory for archives uploaded via the API (#132).
    uploads_dir: Path = Field(default_factory=default_uploads_dir)
    # MIME parse worker processes (#199): 0 = auto (min(4, cpu_count) —
    # measured flat beyond 4 workers); 1 = sequential, no pool.
    ingest_workers: int = 0

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        toml_source = TomlConfigSettingsSource(settings_cls, toml_file=config_dir() / "config.toml")
        return (init_settings, env_settings, toml_source)
