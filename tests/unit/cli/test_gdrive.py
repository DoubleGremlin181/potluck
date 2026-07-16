"""`potluck gdrive` CLI (#152): status output, redirect parsing, the
--no-browser auth flow (exchange faked at the service seam — no network)."""

import json
from datetime import UTC, datetime

import pytest
import typer
from typer.testing import CliRunner

from potluck.cli.app import _parse_redirect, app
from potluck.core.paths import gdrive_token_path
from potluck.ingest.gdrive import DRIVE_SCOPE_READONLY, load_token
from potluck.models.gdrive import StoredToken

runner = CliRunner()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_gdrive_status_unconfigured() -> None:
    result = runner.invoke(app, ["gdrive", "status"])
    assert result.exit_code == 0
    assert "gdrive: not configured" in result.output
    assert "gdrive auth: unconfigured" in result.output
    assert "gdrive prune: off" in result.output


def test_gdrive_status_json() -> None:
    result = runner.invoke(app, ["gdrive", "status", "--json"])
    assert result.exit_code == 0
    body = json.loads(result.output)
    assert body["configured"] is False
    assert body["auth_state"] == "unconfigured"
    assert body["pulled_files"] == 0


def test_gdrive_status_configured_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POTLUCK_GDRIVE_CLIENT_ID", "cid-1")
    monkeypatch.setenv("POTLUCK_GDRIVE_CLIENT_SECRET", "csecret-1")
    result = runner.invoke(app, ["gdrive", "status"])
    assert result.exit_code == 0
    assert "gdrive: configured" in result.output
    assert "gdrive auth: unauthorized" in result.output


# ---------------------------------------------------------------------------
# redirect parsing (the loopback capture and the --no-browser paste share it)
# ---------------------------------------------------------------------------


def test_parse_redirect_extracts_code() -> None:
    url = "http://127.0.0.1:8085/?state=s-1&code=c-1&scope=x"
    assert _parse_redirect(url, "s-1") == "c-1"


def test_parse_redirect_rejects_error_missing_and_mismatch() -> None:
    with pytest.raises(typer.BadParameter, match="access_denied"):
        _parse_redirect("http://127.0.0.1:8085/?error=access_denied", "s-1")
    with pytest.raises(typer.BadParameter, match="no code/state"):
        _parse_redirect("http://127.0.0.1:8085/", "s-1")
    with pytest.raises(typer.BadParameter, match="state mismatch"):
        _parse_redirect("http://127.0.0.1:8085/?code=c-1&state=EVIL", "s-1")


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


def test_gdrive_auth_unconfigured_fails_cleanly() -> None:
    result = runner.invoke(app, ["gdrive", "auth", "--no-browser"])
    assert result.exit_code == 1
    assert "not configured" in result.output


def test_gdrive_auth_no_browser_paste_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full --no-browser round trip: consent URL printed, redirect pasted,
    exchange performed (faked at the service seam — NO network), token saved."""
    monkeypatch.setenv("POTLUCK_GDRIVE_CLIENT_ID", "cid-1")
    monkeypatch.setenv("POTLUCK_GDRIVE_CLIENT_SECRET", "csecret-1")
    # Pin the CSRF state so the pasted redirect can echo it.
    monkeypatch.setattr("potluck.services.gdrive.secrets.token_urlsafe", lambda n: "fixed-state")
    seen: dict[str, str] = {}

    def fake_exchange(
        *,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        transport: object = None,
    ) -> StoredToken:
        seen.update(code=code, redirect_uri=redirect_uri, code_verifier=code_verifier)
        return StoredToken(
            refresh_token="rtok-cli",
            client_id=client_id,
            scopes=[DRIVE_SCOPE_READONLY],
            obtained_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    monkeypatch.setattr("potluck.services.gdrive.exchange_code", fake_exchange)

    pasted = "http://127.0.0.1:8085/?code=cli-code&state=fixed-state\n"
    result = runner.invoke(app, ["gdrive", "auth", "--no-browser"], input=pasted)
    assert result.exit_code == 0, result.output
    assert "accounts.google.com/o/oauth2/v2/auth" in result.output
    assert "Authorized" in result.output

    assert seen["code"] == "cli-code"
    assert seen["redirect_uri"] == "http://127.0.0.1:8085/"
    assert seen["code_verifier"]  # the PKCE verifier travelled to the exchange
    token_file = gdrive_token_path()
    assert (token_file.stat().st_mode & 0o777) == 0o600
    loaded = load_token(token_file)
    assert loaded is not None and loaded.refresh_token == "rtok-cli"


def test_gdrive_auth_state_mismatch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POTLUCK_GDRIVE_CLIENT_ID", "cid-1")
    monkeypatch.setenv("POTLUCK_GDRIVE_CLIENT_SECRET", "csecret-1")
    pasted = "http://127.0.0.1:8085/?code=cli-code&state=WRONG\n"
    result = runner.invoke(app, ["gdrive", "auth", "--no-browser"], input=pasted)
    assert result.exit_code != 0
    assert not gdrive_token_path().exists()  # nothing persisted on failure
