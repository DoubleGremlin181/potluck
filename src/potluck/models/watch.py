"""Watch-folder DTOs (#151)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

type WatchEnabledSource = Literal["config", "runtime"]
type WatchPendingState = Literal["stabilizing", "backoff"]


class WatchFolder(BaseModel):
    """One configured watch folder and whether it currently exists."""

    path: str
    exists: bool = Field(description="False = configured but missing on disk (warned, skipped).")


class WatchPendingSet(BaseModel):
    """An archive set the watcher has seen but not (yet) imported."""

    path: str = Field(description="Representative file of the set (first part).")
    state: WatchPendingState = Field(
        description="'stabilizing' = waiting for the two-scan debounce (or the "
        "import manager); 'backoff' = a failed import is cooling down."
    )
    retry_in_cycles: int | None = Field(
        default=None, description="Backoff only: polling cycles left before the retry."
    )


class WatchRuntime(BaseModel):
    """The watcher thread's runtime snapshot (empty when no watcher runs —
    CLI/one-shot processes never poll)."""

    last_scan_at: datetime | None = None
    pending: list[WatchPendingSet] = Field(default_factory=list)
    last_error: str | None = None


class WatchStatus(BaseModel):
    """Full watch-folder status: configuration plus runtime, GET /api/watch."""

    enabled: bool = Field(description="Effective value (runtime override if set, else config).")
    effective_enabled_source: WatchEnabledSource = Field(
        description="'runtime' when a persisted toggle overrides config.toml."
    )
    interval_s: float = Field(description="Polling interval (config-file-owned).")
    folders: list[WatchFolder] = Field(description="Configured folders (config-file-owned).")
    last_scan_at: datetime | None = Field(
        default=None,
        description="Completion time of the newest scan; null before the first "
        "(and always null outside `potluck serve` — only the server polls).",
    )
    pending: list[WatchPendingSet] = Field(default_factory=list)
    last_error: str | None = Field(
        default=None, description="Most recent watcher-submitted import failure."
    )


class WatchToggleRequest(BaseModel):
    """Body of PATCH /api/watch: persist the runtime enable/disable toggle."""

    enabled: bool
