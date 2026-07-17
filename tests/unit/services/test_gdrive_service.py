"""Drive-pull service seam (#152): status, toggle, auth flow, puller wiring,
and the #151 watcher composition (effective folders include the downloads dir).
"""

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from potluck.core.errors import GDriveAuthError
from potluck.core.paths import gdrive_token_path
from potluck.ingest.gdrive import (
    DRIVE_SCOPE_FULL,
    DRIVE_SCOPE_READONLY,
    save_token,
)
from potluck.models.gdrive import StoredToken
from potluck.services import gdrive as gdrive_service
from potluck.services import watch as watch_service
from potluck.services.context import AppContext
from tests.conftest import MockDrive


def _configured(ctx: AppContext, **overrides: object) -> AppContext:
    updates: dict[str, object] = {
        "gdrive_client_id": "cid-1",
        "gdrive_client_secret": "csecret-1",
    }
    updates.update(overrides)
    return AppContext(settings=ctx.settings.model_copy(update=updates), db=ctx.db)


def _write_token(scopes: list[str] | None = None, client_id: str = "cid-1") -> None:
    save_token(
        gdrive_token_path(),
        StoredToken(
            refresh_token="rtok-1",
            client_id=client_id,
            scopes=scopes if scopes is not None else [DRIVE_SCOPE_READONLY],
            obtained_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )


# ---------------------------------------------------------------------------
# Status: auth_state ladder + enabled toggle
# ---------------------------------------------------------------------------


def test_status_unconfigured_by_default(ctx: AppContext) -> None:
    status = gdrive_service.get_gdrive_status(ctx)
    assert status.configured is False
    assert status.auth_state == "unconfigured"
    assert status.enabled is True
    assert status.effective_enabled_source == "config"
    assert status.prune is False
    assert status.prune_scope_granted is False
    assert status.pulled_files == 0
    assert status.folder_name == "Takeout"
    assert status.interval_s == 86400.0
    assert status.last_check_at is None


def test_status_configured_without_token_is_unauthorized(ctx: AppContext) -> None:
    status = gdrive_service.get_gdrive_status(_configured(ctx))
    assert status.configured is True
    assert status.auth_state == "unauthorized"


def test_status_token_for_other_client_is_unauthorized(ctx: AppContext) -> None:
    """Rotated client credentials invalidate the old token (decision doc §3)."""
    _write_token(client_id="cid-OLD")
    status = gdrive_service.get_gdrive_status(_configured(ctx))
    assert status.auth_state == "unauthorized"


def test_status_with_token_is_ok_and_reports_prune_scope(ctx: AppContext) -> None:
    _write_token()
    gctx = _configured(ctx)
    status = gdrive_service.get_gdrive_status(gctx)
    assert status.auth_state == "ok"
    assert status.prune_scope_granted is False

    _write_token(scopes=[DRIVE_SCOPE_READONLY, DRIVE_SCOPE_FULL])
    assert gdrive_service.get_gdrive_status(gctx).prune_scope_granted is True


def test_set_enabled_persists_as_runtime_override(ctx: AppContext) -> None:
    status = gdrive_service.set_gdrive_enabled(ctx, False)
    assert status.enabled is False
    assert status.effective_enabled_source == "runtime"
    # And the effective value drives the puller's per-cycle check.
    assert gdrive_service.effective_gdrive_enabled(ctx) == (False, "runtime")


# ---------------------------------------------------------------------------
# Authorization: build (scopes/PKCE/state) + complete (token file 0600)
# ---------------------------------------------------------------------------


def test_build_authorization_requires_configuration(ctx: AppContext) -> None:
    with pytest.raises(GDriveAuthError):
        gdrive_service.build_authorization(ctx, prune=False, redirect_uri="http://127.0.0.1:1/")


def test_build_authorization_scopes_follow_prune_flag(ctx: AppContext) -> None:
    gctx = _configured(ctx)
    plain = gdrive_service.build_authorization(
        gctx, prune=False, redirect_uri="http://127.0.0.1:1/"
    )
    assert plain.scopes == [DRIVE_SCOPE_READONLY]  # least privilege by default
    pruning = gdrive_service.build_authorization(
        gctx, prune=True, redirect_uri="http://127.0.0.1:1/"
    )
    assert pruning.scopes == [DRIVE_SCOPE_READONLY, DRIVE_SCOPE_FULL]

    params = {k: v[0] for k, v in parse_qs(urlsplit(plain.url).query).items()}
    assert params["state"] == plain.state
    assert params["code_challenge_method"] == "S256"
    # Every attempt gets fresh one-time material.
    assert plain.state != pruning.state
    assert plain.code_verifier != pruning.code_verifier


def test_complete_authorization_saves_0600_token_and_reports_ok(ctx: AppContext) -> None:
    mock = MockDrive()
    gctx = _configured(ctx)
    auth = gdrive_service.build_authorization(
        gctx, prune=False, redirect_uri="http://127.0.0.1:4242/"
    )
    status = gdrive_service.complete_authorization(
        gctx,
        code="authcode-1",
        redirect_uri=auth.redirect_uri,
        code_verifier=auth.code_verifier,
        transport=mock.transport(),
    )
    assert status.auth_state == "ok"
    token_file = gdrive_token_path()
    assert (token_file.stat().st_mode & 0o777) == 0o600
    assert "rtok-1" not in str(ctx.settings.db_path.read_bytes())  # never in the DB


# ---------------------------------------------------------------------------
# Puller wiring + watcher composition (decision doc §4)
# ---------------------------------------------------------------------------


def test_start_puller_unconfigured_is_a_noop(ctx: AppContext) -> None:
    assert gdrive_service.start_puller(ctx) is False
    assert ctx.puller.is_running() is False


def test_start_puller_creates_downloads_dir_and_thread(ctx: AppContext, tmp_path: Path) -> None:
    downloads = tmp_path / "gdrive-downloads"
    gctx = _configured(ctx, gdrive_downloads_dir=downloads, gdrive_interval_s=60.0)
    try:
        assert gdrive_service.start_puller(gctx, transport=MockDrive().transport()) is True
        assert downloads.is_dir()  # pre-created so the watcher never warns
        assert gctx.puller.is_running() is True
    finally:
        gctx.puller.stop()
        gctx.puller.join(5.0)


def test_effective_watch_folders_include_gdrive_downloads_dir(
    ctx: AppContext, tmp_path: Path
) -> None:
    plain = watch_service.effective_watch_folders(ctx)
    assert plain == ()  # unconfigured: nothing implicit

    downloads = tmp_path / "gdrive-downloads"
    gctx = _configured(ctx, gdrive_downloads_dir=downloads)
    assert watch_service.effective_watch_folders(gctx) == (downloads,)

    # Explicitly listed too: no duplicate entry.
    both = _configured(
        ctx, gdrive_downloads_dir=downloads, watch_folders=[downloads, tmp_path / "drop"]
    )
    assert watch_service.effective_watch_folders(both) == (downloads, tmp_path / "drop")

    # And the watch status surface reports it like any other folder.
    status = watch_service.get_watch_status(gctx)
    assert [f.path for f in status.folders] == [str(downloads)]
