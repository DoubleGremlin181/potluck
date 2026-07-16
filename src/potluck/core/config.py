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
    default_gdrive_downloads_dir,
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
    # Upload size cap for POST /api/imports/upload: the copy into uploads_dir
    # stops (and the partial file is removed) beyond this. 10 GiB default —
    # real Takeout exports split into 2/4/10 GB parts.
    max_upload_bytes: int = 10 * 1024**3
    # MIME parse worker processes (#199): 0 = auto (min(4, cpu_count) —
    # measured flat beyond 4 workers); 1 = sequential, no pool.
    ingest_workers: int = 0
    # Watch-folder auto-import (#151): folders polled for dropped export
    # archives while `potluck serve` runs (stdlib polling — CLI/one-shot
    # contexts never poll; write ownership lives with the server). The folder
    # list and interval are config-file-owned; enabled is only the DEFAULT —
    # a runtime toggle persisted in the database (app_settings KV, settable
    # from the UI/API) overrides it when present.
    watch_folders: list[Path] = Field(default_factory=list)
    # 10 s ships the #151 acceptance ("drop zip → import starts < 30 s") with
    # margin: the two-scan debounce claims within 2 intervals of an atomic
    # drop (= 20 s worst case). A cycle costs one os.scandir per folder plus
    # one single-row KV read — negligible at this cadence.
    watch_interval_s: float = 10.0
    watch_enabled: bool = True
    # Google Drive Takeout auto-pull (#152) — flat gdrive_* keys, matching the
    # watch_* family (decision doc §2/§4). The user supplies their own OAuth
    # "Desktop app" client (docs/gdrive-setup.md); installed-app client
    # secrets are non-confidential by Google's own definition, so config.toml
    # is a proportionate home. Both None = feature off (puller never starts).
    gdrive_client_id: str | None = None
    gdrive_client_secret: str | None = None
    # Drive folder scheduled Takeout exports land in ("Takeout" unless the
    # user renamed it).
    gdrive_folder_name: str = "Takeout"
    # Daily: exports arrive every 2 months, so daily polling bounds pickup
    # delay at a level nobody notices on that cadence; one cycle also runs
    # immediately at serve startup (decision doc §5).
    gdrive_interval_s: float = 86400.0
    # Managed landing dir; automatically part of the watcher's effective
    # folders when the puller is configured — puller downloads, watcher
    # imports (decision doc §4).
    gdrive_downloads_dir: Path = Field(default_factory=default_gdrive_downloads_dir)
    # DESTRUCTIVE when enabled: permanently deletes pulled archives from
    # Drive (files.delete — trash would keep consuming quota) once the whole
    # set's import completed. Requires the full drive scope: re-auth with
    # `potluck gdrive auth --prune` (decision doc §6).
    gdrive_prune: bool = False
    # Config DEFAULT for the puller; a runtime toggle in the app_settings KV
    # overrides it when present (same pattern as watch_enabled).
    gdrive_enabled: bool = True

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
