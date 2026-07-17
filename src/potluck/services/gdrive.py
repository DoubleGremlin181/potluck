"""Drive-pull service (#152): status, runtime toggle, puller lifecycle, auth.

The seam the API routes, CLI (`potluck gdrive …`) and serve lifespan share.
Configuration (client credentials, folder name, interval, prune, the enabled
DEFAULT) is config-file-owned; only the enabled flag has a runtime override
(app_settings KV, same pattern as watch_enabled). The OAuth token lives in a
0600 file under config_dir() — never in the database (decision doc §3).
"""

import logging
import os
import secrets
from datetime import UTC, datetime

import httpx

from potluck.core.config import Settings
from potluck.core.errors import GDriveAuthError
from potluck.core.paths import gdrive_token_path
from potluck.ingest.gdrive import (
    DRIVE_SCOPE_FULL,
    DRIVE_SCOPE_READONLY,
    DriveClient,
    build_auth_url,
    exchange_code,
    load_token,
    make_pkce,
    save_token,
)
from potluck.models.gdrive import (
    GDriveAuthRequest,
    GDriveAuthState,
    GDriveEnabledSource,
    GDrivePullRecord,
    GDriveStatus,
)
from potluck.services.context import AppContext
from potluck.services.gdrive_manager import PullerOps
from potluck.storage import app_settings as _storage_app_settings
from potluck.storage import gdrive_pulls as _storage_gdrive_pulls

_logger = logging.getLogger(__name__)

GDRIVE_ENABLED_KEY = "gdrive_enabled"


def gdrive_configured(settings: Settings) -> bool:
    """Whether the user supplied their OAuth client (the feature's on-switch)."""
    return bool(settings.gdrive_client_id and settings.gdrive_client_secret)


def effective_gdrive_enabled(ctx: AppContext) -> tuple[bool, GDriveEnabledSource]:
    """(enabled, source): the persisted runtime toggle if set, else config."""
    with ctx.db.read() as conn:
        value = _storage_app_settings.get_setting(conn, GDRIVE_ENABLED_KEY)
    if isinstance(value, bool):
        return value, "runtime"
    return ctx.settings.gdrive_enabled, "config"


def get_gdrive_status(ctx: AppContext) -> GDriveStatus:
    """Configuration + auth + this process's puller runtime (GET /api/gdrive).

    Runtime fields (last_check_at / last_pull_at / offline / backoff /
    last_error) are meaningful only inside `potluck serve` — the sole process
    that pulls. A CLI status call reports them empty, which is the truth for
    that process.
    """
    enabled, source = effective_gdrive_enabled(ctx)
    runtime = ctx.puller.snapshot()
    token = load_token(gdrive_token_path())
    with ctx.db.read() as conn:
        pulled = _storage_gdrive_pulls.count_pulls(conn)
    return GDriveStatus(
        configured=gdrive_configured(ctx.settings),
        auth_state=_auth_state(ctx.settings, runtime.reauth_required),
        enabled=enabled,
        effective_enabled_source=source,
        prune=ctx.settings.gdrive_prune,
        prune_scope_granted=token is not None and DRIVE_SCOPE_FULL in token.scopes,
        folder_name=ctx.settings.gdrive_folder_name,
        interval_s=ctx.settings.gdrive_interval_s,
        downloads_dir=str(ctx.settings.gdrive_downloads_dir),
        pulled_files=pulled,
        last_check_at=runtime.last_check_at,
        last_pull_at=runtime.last_pull_at,
        offline=runtime.offline,
        backoff_cycles=runtime.backoff_cycles,
        last_error=runtime.last_error,
    )


def _auth_state(settings: Settings, reauth_required: bool) -> GDriveAuthState:
    if not gdrive_configured(settings):
        return "unconfigured"
    token = load_token(gdrive_token_path())
    if token is None or token.client_id != settings.gdrive_client_id:
        # No token yet, an unreadable one, or one minted for a DIFFERENT
        # client (the user rotated credentials): re-auth either way.
        return "unauthorized"
    if reauth_required:
        return "reauth_required"
    return "ok"


def set_gdrive_enabled(ctx: AppContext, enabled: bool) -> GDriveStatus:
    """Persist the runtime toggle (PATCH /api/gdrive); return the new status.

    Takes effect on the puller's next cycle — it re-reads the effective value
    every cycle, so no thread restart is involved.
    """
    ctx.db.write(lambda conn: _storage_app_settings.set_setting(conn, GDRIVE_ENABLED_KEY, enabled))
    return get_gdrive_status(ctx)


# ---------------------------------------------------------------------------
# Authorization (decision doc §2): the CLI owns browser/loopback mechanics;
# these two functions own everything protocol-shaped.
# ---------------------------------------------------------------------------


def build_authorization(ctx: AppContext, *, prune: bool, redirect_uri: str) -> GDriveAuthRequest:
    """Prepare one authorization attempt: consent URL + PKCE/state material.

    ``prune`` requests the full drive scope (files.delete needs it); plain
    auth stays at drive.readonly — least privilege that can see Takeout files.
    """
    client_id = ctx.settings.gdrive_client_id
    if not client_id or not ctx.settings.gdrive_client_secret:
        raise GDriveAuthError(
            "gdrive is not configured — set gdrive_client_id and "
            "gdrive_client_secret in config.toml first (see docs/gdrive-setup.md)"
        )
    scopes = [DRIVE_SCOPE_READONLY] + ([DRIVE_SCOPE_FULL] if prune else [])
    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(16)
    url = build_auth_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        scopes=scopes,
        state=state,
        code_challenge=challenge,
    )
    return GDriveAuthRequest(
        url=url, state=state, code_verifier=verifier, redirect_uri=redirect_uri, scopes=scopes
    )


def complete_authorization(
    ctx: AppContext,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    transport: httpx.BaseTransport | None = None,
) -> GDriveStatus:
    """Exchange the captured code and persist the token file (0600, atomic)."""
    client_id = ctx.settings.gdrive_client_id
    client_secret = ctx.settings.gdrive_client_secret
    if not client_id or not client_secret:
        raise GDriveAuthError("gdrive is not configured — cannot complete authorization")
    token = exchange_code(
        client_id=client_id,
        client_secret=client_secret,
        code=code,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
        transport=transport,
    )
    save_token(gdrive_token_path(), token)
    _logger.info("gdrive authorized; token saved to %s", gdrive_token_path())
    return get_gdrive_status(ctx)


# ---------------------------------------------------------------------------
# Puller wiring (serve lifespan only)
# ---------------------------------------------------------------------------


def start_puller(ctx: AppContext, *, transport: httpx.BaseTransport | None = None) -> bool:
    """Configure and start the pull thread; False when gdrive isn't configured.

    Serve-lifespan only (the same write-ownership reasoning as the watcher:
    scheduled work belongs to the serving process). Started even when a token
    doesn't exist yet or the runtime toggle is off — auth/toggle can happen
    while serving and the next cycle picks both up; an idle cycle costs one
    KV read. *transport* exists for the mock-Drive test tier.
    """
    if not gdrive_configured(ctx.settings):
        _logger.info("gdrive: no OAuth client configured; puller not started")
        return False
    # Pre-create the landing dir so the watcher (which polls it from the same
    # lifespan) never warns about a missing folder before the first pull.
    ctx.settings.gdrive_downloads_dir.mkdir(parents=True, exist_ok=True)
    ctx.puller.configure(
        ops=PullerOps(
            enabled=lambda: effective_gdrive_enabled(ctx)[0],
            make_client=lambda: _make_client(ctx.settings, transport),
            token_fingerprint=_token_fingerprint,
            filter_pulled=lambda ids: _filter_pulled(ctx, ids),
            record_pulls=lambda records: _record_pulls(ctx, records),
            prune_enabled=lambda: ctx.settings.gdrive_prune,
            list_prunable=lambda: _list_prunable(ctx),
            mark_pruned=lambda ids: _mark_pruned(ctx, ids),
        ),
        downloads_dir=ctx.settings.gdrive_downloads_dir,
        folder_name=ctx.settings.gdrive_folder_name,
        interval_s=ctx.settings.gdrive_interval_s,
    )
    ctx.puller.start()
    _logger.info(
        "gdrive: polling folder %r every %.0f s into %s",
        ctx.settings.gdrive_folder_name,
        ctx.settings.gdrive_interval_s,
        ctx.settings.gdrive_downloads_dir,
    )
    return True


def _make_client(settings: Settings, transport: httpx.BaseTransport | None) -> DriveClient | None:
    """A fresh per-cycle client, or None while un-authorized.

    Re-reading the token file every cycle is the self-healing seam: a
    `potluck gdrive auth` run in another process is picked up on the next
    cycle without restarting the server.
    """
    client_id = settings.gdrive_client_id
    client_secret = settings.gdrive_client_secret
    if not client_id or not client_secret:
        return None
    token = load_token(gdrive_token_path())
    if token is None or token.client_id != client_id:
        return None
    return DriveClient(
        client_id=client_id,
        client_secret=client_secret,
        token=token,
        token_path=gdrive_token_path(),
        transport=transport,
    )


def _token_fingerprint() -> object:
    """Changes iff the token file changed — the reauth latch key."""
    try:
        stat = os.stat(gdrive_token_path())
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _filter_pulled(ctx: AppContext, file_ids: list[str]) -> set[str]:
    with ctx.db.read() as conn:
        return _storage_gdrive_pulls.filter_pulled(conn, file_ids)


def _record_pulls(ctx: AppContext, records: list[GDrivePullRecord]) -> None:
    ctx.db.write(lambda conn: _storage_gdrive_pulls.record_pulls(conn, records))


def _list_prunable(ctx: AppContext) -> list[GDrivePullRecord]:
    with ctx.db.read() as conn:
        return _storage_gdrive_pulls.list_prunable(conn)


def _mark_pruned(ctx: AppContext, file_ids: list[str]) -> None:
    now = datetime.now(UTC)
    ctx.db.write(lambda conn: _storage_gdrive_pulls.mark_pruned(conn, file_ids, now))
