"""Google Drive Takeout auto-pull DTOs (#152).

See docs/decisions/gdrive-takeout-pull.md for the decisions these shapes
implement (token file layout §3, status surface §8, pull records §4).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# unconfigured: no client id/secret in config. unauthorized: configured but
# no (usable) token file — `potluck gdrive auth` has not run, or the token
# belongs to a different client. reauth_required: the refresh token was
# rejected (revoked/expired) — re-run the auth command.
type GDriveAuthState = Literal["unconfigured", "unauthorized", "ok", "reauth_required"]
type GDriveEnabledSource = Literal["config", "runtime"]


class StoredToken(BaseModel):
    """The 0600 token file at ``gdrive_token_path()`` — never in the DB.

    Access tokens are deliberately absent: they live ~1 h and are kept in
    memory only (persisting them would rewrite the file hourly for nothing).
    """

    refresh_token: str
    client_id: str = Field(
        description="Client the token was minted for; a mismatch with the configured client forces re-auth."
    )
    scopes: list[str]
    obtained_at: datetime


class GDriveAuthRequest(BaseModel):
    """One prepared authorization attempt (service → CLI): the URL to open
    plus the one-time values the CLI must hold onto for the code exchange."""

    url: str
    state: str = Field(description="CSRF guard: must match the redirect's state param.")
    code_verifier: str = Field(description="PKCE verifier for the code exchange.")
    redirect_uri: str
    scopes: list[str]


class GDrivePullRecord(BaseModel):
    """One pulled Drive file (a row of the gdrive_pulls table)."""

    file_id: str
    name: str
    md5: str | None = Field(default=None, description="Drive md5Checksum at pull time.")
    set_stem: str = Field(
        description="parse_part_name grouping key (file name for single archives)."
    )
    local_path: str
    pulled_at: datetime
    pruned_at: datetime | None = None


class GDriveRuntime(BaseModel):
    """The puller thread's runtime snapshot (empty when no puller runs —
    CLI/one-shot processes never poll; only `potluck serve` pulls)."""

    last_check_at: datetime | None = None
    last_pull_at: datetime | None = None
    # Offline is a NORMAL state for a local-first app (laptop without a
    # network) — a status fact, never last_error (decision doc §8).
    offline: bool = False
    reauth_required: bool = False
    backoff_cycles: int | None = Field(
        default=None, description="Cycles left before the next retry after failures."
    )
    last_error: str | None = None


class GDriveStatus(BaseModel):
    """Full Drive-pull status: configuration + auth + runtime (GET /api/gdrive)."""

    configured: bool = Field(description="Client id + secret present in config.")
    auth_state: GDriveAuthState
    enabled: bool = Field(description="Effective value (runtime override if set, else config).")
    effective_enabled_source: GDriveEnabledSource
    prune: bool = Field(description="gdrive_prune config flag (destructive; default off).")
    prune_scope_granted: bool = Field(
        description="Whether the token carries the full drive scope pruning needs."
    )
    folder_name: str
    interval_s: float
    downloads_dir: str
    pulled_files: int = Field(description="Drive files recorded as pulled (gdrive_pulls rows).")
    last_check_at: datetime | None = None
    last_pull_at: datetime | None = None
    offline: bool = False
    backoff_cycles: int | None = None
    last_error: str | None = None


class GDriveToggleRequest(BaseModel):
    """PATCH /api/gdrive body: the persisted runtime enable toggle."""

    enabled: bool
