"""DriveClient + token file + OAuth helpers (#152) over MockDrive.

Everything runs against httpx.MockTransport — NO network, ever. The token
file is exercised for perms (acceptance: 0600, never in DB), atomic rewrite,
and rotation; the client for pagination, lazy refresh, Range resume, md5
verification and idempotent delete.
"""

import base64
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from potluck.core.errors import GDriveApiError, GDriveAuthError
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
from potluck.models.gdrive import StoredToken
from tests.conftest import MockDrive


def _token(refresh: str = "rtok-1", scopes: list[str] | None = None) -> StoredToken:
    return StoredToken(
        refresh_token=refresh,
        client_id="cid-1",
        scopes=scopes if scopes is not None else [DRIVE_SCOPE_READONLY],
        obtained_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _client(mock: MockDrive, token_path: Path, token: StoredToken | None = None) -> DriveClient:
    return DriveClient(
        client_id="cid-1",
        client_secret="csecret-1",
        token=token if token is not None else _token(),
        token_path=token_path,
        transport=mock.transport(),
    )


# ---------------------------------------------------------------------------
# Token file: 0600, atomic, rotation-aware (acceptance: never in DB plaintext)
# ---------------------------------------------------------------------------


def test_save_token_creates_0600_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "cfg" / "gdrive_token.json"
    save_token(path, _token())
    assert (path.stat().st_mode & 0o777) == 0o600
    loaded = load_token(path)
    assert loaded is not None
    assert loaded.refresh_token == "rtok-1"
    assert loaded.scopes == [DRIVE_SCOPE_READONLY]


def test_save_token_overwrite_is_atomic_and_stays_0600(tmp_path: Path) -> None:
    path = tmp_path / "gdrive_token.json"
    save_token(path, _token("old"))
    path.chmod(0o644)  # even a user-loosened file is re-tightened on rewrite
    save_token(path, _token("new"))
    assert (path.stat().st_mode & 0o777) == 0o600
    loaded = load_token(path)
    assert loaded is not None and loaded.refresh_token == "new"
    # No temp droppings left beside the token file.
    assert [p.name for p in tmp_path.iterdir()] == ["gdrive_token.json"]


def test_load_token_missing_or_malformed_is_none(tmp_path: Path) -> None:
    assert load_token(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_token(bad) is None
    wrong_shape = tmp_path / "wrong.json"
    wrong_shape.write_text('{"hello": "world"}')
    assert load_token(wrong_shape) is None


# ---------------------------------------------------------------------------
# PKCE + authorization URL (loopback installed-app flow, decision doc §2)
# ---------------------------------------------------------------------------


def test_make_pkce_s256_relationship() -> None:
    verifier, challenge = make_pkce()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected
    assert len(verifier) >= 43  # RFC 7636 minimum


def test_build_auth_url_carries_offline_consent_pkce() -> None:
    url = build_auth_url(
        client_id="cid-1",
        redirect_uri="http://127.0.0.1:4242/",
        scopes=[DRIVE_SCOPE_READONLY, DRIVE_SCOPE_FULL],
        state="state-xyz",
        code_challenge="challenge-abc",
    )
    split = urlsplit(url)
    assert (split.scheme, split.netloc, split.path) == (
        "https",
        "accounts.google.com",
        "/o/oauth2/v2/auth",
    )
    params = {k: v[0] for k, v in parse_qs(split.query).items()}
    assert params["client_id"] == "cid-1"
    assert params["redirect_uri"] == "http://127.0.0.1:4242/"
    assert params["response_type"] == "code"
    assert params["access_type"] == "offline"  # refresh token, always
    assert params["prompt"] == "consent"  # re-auth must re-issue one too
    assert params["state"] == "state-xyz"
    assert params["code_challenge"] == "challenge-abc"
    assert params["code_challenge_method"] == "S256"
    assert params["scope"] == f"{DRIVE_SCOPE_READONLY} {DRIVE_SCOPE_FULL}"


def test_exchange_code_posts_secret_and_verifier_and_builds_token() -> None:
    mock = MockDrive(scopes=(DRIVE_SCOPE_READONLY,))
    token = exchange_code(
        client_id="cid-1",
        client_secret="csecret-1",
        code="authcode-1",
        redirect_uri="http://127.0.0.1:4242/",
        code_verifier="verifier-1",
        transport=mock.transport(),
    )
    [form] = mock.exchange_calls
    assert form["grant_type"] == "authorization_code"
    assert form["client_secret"] == "csecret-1"
    assert form["code_verifier"] == "verifier-1"
    assert form["redirect_uri"] == "http://127.0.0.1:4242/"
    assert token.refresh_token == "rtok-1"
    assert token.client_id == "cid-1"
    assert token.scopes == [DRIVE_SCOPE_READONLY]  # granted scopes from the response


def test_exchange_code_rejected_raises_auth_error() -> None:
    mock = MockDrive()
    with pytest.raises(GDriveAuthError):
        exchange_code(
            client_id="cid-1",
            client_secret="csecret-1",
            code="WRONG",
            redirect_uri="http://127.0.0.1:4242/",
            code_verifier="v",
            transport=mock.transport(),
        )


# ---------------------------------------------------------------------------
# Listing: folder lookup, children, pagination
# ---------------------------------------------------------------------------


def test_list_folders_and_children(tmp_path: Path) -> None:
    mock = MockDrive()
    takeout = mock.add_folder("Takeout")
    mock.add_folder("Other")
    fid = mock.add_file(takeout, "takeout-20260101T000000Z-001.zip", b"zipbytes")
    with _client(mock, tmp_path / "tok.json") as client:
        [folder] = client.list_folders("Takeout")
        assert folder.id == takeout
        [child] = client.list_children(takeout)
    assert child.id == fid
    assert child.name == "takeout-20260101T000000Z-001.zip"
    assert child.size == len(b"zipbytes")
    assert child.md5 == mock.file_md5(fid)
    # The folder query filters server-side on name + folder type + not trashed.
    assert any("name = 'Takeout'" in q and "trashed = false" in q for q in mock.list_queries)


def test_list_children_follows_pagination(tmp_path: Path) -> None:
    mock = MockDrive()
    folder = mock.add_folder("Takeout")
    ids = [mock.add_file(folder, f"takeout-2026-{n}-001.zip", b"x" * n) for n in (1, 2, 3)]
    mock.page_size = 1
    with _client(mock, tmp_path / "tok.json") as client:
        children = client.list_children(folder)
    assert [c.id for c in children] == ids


def test_folder_name_single_quote_escaped(tmp_path: Path) -> None:
    mock = MockDrive()
    with _client(mock, tmp_path / "tok.json") as client:
        client.list_folders("My 'Takeout'")
    [q] = mock.list_queries
    assert "name = 'My \\'Takeout\\''" in q


# ---------------------------------------------------------------------------
# Auth plumbing: lazy refresh, 401 retry, invalid_grant, rotation
# ---------------------------------------------------------------------------


def test_expired_access_token_refreshes_once_and_retries(tmp_path: Path) -> None:
    mock = MockDrive()
    folder = mock.add_folder("Takeout")
    with _client(mock, tmp_path / "tok.json") as client:
        client.list_folders("Takeout")
        assert mock.refresh_calls == 1  # lazy initial refresh
        mock.expire_access()  # server-side invalidation between calls
        [f] = client.list_folders("Takeout")
        assert f.id == folder
    assert mock.refresh_calls == 2  # exactly one re-refresh, then the retry


def test_invalid_grant_raises_gdrive_auth_error(tmp_path: Path) -> None:
    mock = MockDrive()
    mock.refresh_error = "invalid_grant"
    with _client(mock, tmp_path / "tok.json") as client, pytest.raises(GDriveAuthError):
        client.list_folders("Takeout")


def test_rate_limit_raises_gdrive_api_error(tmp_path: Path) -> None:
    mock = MockDrive()
    mock.add_folder("Takeout")
    with _client(mock, tmp_path / "tok.json") as client:
        client.list_folders("Takeout")  # prime the access token
        mock.fail_next_status = 429
        with pytest.raises(GDriveApiError):
            client.list_folders("Takeout")


def test_refresh_rotation_rewrites_token_file_0600(tmp_path: Path) -> None:
    token_path = tmp_path / "tok.json"
    save_token(token_path, _token())
    mock = MockDrive()
    mock.rotate_refresh_to = "rtok-2"
    mock.add_folder("Takeout")
    with _client(mock, token_path) as client:
        client.list_folders("Takeout")
    rewritten = load_token(token_path)
    assert rewritten is not None and rewritten.refresh_token == "rtok-2"
    assert (token_path.stat().st_mode & 0o777) == 0o600


# ---------------------------------------------------------------------------
# Download: streaming, md5 verify, Range resume, 416 restart
# ---------------------------------------------------------------------------


def test_download_streams_and_verifies_md5(tmp_path: Path) -> None:
    mock = MockDrive()
    folder = mock.add_folder("Takeout")
    content = b"archive-bytes" * 1000
    fid = mock.add_file(folder, "t.zip", content)
    dest = tmp_path / "t.zip.part"
    with _client(mock, tmp_path / "tok.json") as client:
        client.download(fid, dest, expected_md5=mock.file_md5(fid))
    assert dest.read_bytes() == content


def test_download_md5_mismatch_removes_dest_and_raises(tmp_path: Path) -> None:
    mock = MockDrive()
    folder = mock.add_folder("Takeout")
    fid = mock.add_file(folder, "t.zip", b"real bytes")
    dest = tmp_path / "t.zip.part"
    with _client(mock, tmp_path / "tok.json") as client, pytest.raises(GDriveApiError):
        client.download(fid, dest, expected_md5="0" * 32)
    assert not dest.exists()  # a corrupt partial must not poison later resumes


def test_download_resumes_from_existing_partial(tmp_path: Path) -> None:
    mock = MockDrive()
    folder = mock.add_folder("Takeout")
    content = b"0123456789" * 100
    fid = mock.add_file(folder, "t.zip", content)
    dest = tmp_path / "t.zip.part"
    dest.write_bytes(content[:300])  # an interrupted earlier attempt
    with _client(mock, tmp_path / "tok.json") as client:
        client.download(fid, dest, expected_md5=mock.file_md5(fid))
    assert dest.read_bytes() == content
    assert (fid, "bytes=300-") in mock.download_ranges  # resumed, not re-pulled


def test_download_preflight_skips_already_complete_partial(tmp_path: Path) -> None:
    """A .part already carrying the exact expected bytes (a retry after the
    set's OTHER member failed) is verified locally — zero HTTP downloads."""
    mock = MockDrive()
    folder = mock.add_folder("Takeout")
    content = b"complete content"
    fid = mock.add_file(folder, "t.zip", content)
    dest = tmp_path / "t.zip.part"
    dest.write_bytes(content)
    with _client(mock, tmp_path / "tok.json") as client:
        client.download(fid, dest, expected_md5=mock.file_md5(fid))
    assert mock.download_ranges == []
    assert dest.read_bytes() == content


def test_download_416_restarts_from_zero(tmp_path: Path) -> None:
    """A stale .part LONGER than the file (Drive re-uploaded a smaller one,
    or local corruption): 416 → restart the download from scratch."""
    mock = MockDrive()
    folder = mock.add_folder("Takeout")
    content = b"short"
    fid = mock.add_file(folder, "t.zip", content)
    dest = tmp_path / "t.zip.part"
    dest.write_bytes(b"x" * 100)  # longer than the real file
    with _client(mock, tmp_path / "tok.json") as client:
        client.download(fid, dest, expected_md5=mock.file_md5(fid))
    assert dest.read_bytes() == content


# ---------------------------------------------------------------------------
# Delete: exact ids, idempotent on 404 (pruning re-runs after partial failure)
# ---------------------------------------------------------------------------


def test_delete_removes_and_404_is_idempotent(tmp_path: Path) -> None:
    mock = MockDrive()
    folder = mock.add_folder("Takeout")
    fid = mock.add_file(folder, "t.zip", b"bytes")
    with _client(mock, tmp_path / "tok.json") as client:
        client.delete(fid)
        client.delete(fid)  # already gone: success, not an error
    assert mock.deleted == [fid]


def test_has_scope(tmp_path: Path) -> None:
    mock = MockDrive()
    with _client(mock, tmp_path / "tok.json", _token(scopes=[DRIVE_SCOPE_FULL])) as client:
        assert client.has_scope(DRIVE_SCOPE_FULL)
        assert not client.has_scope("https://www.googleapis.com/auth/nonexistent")
