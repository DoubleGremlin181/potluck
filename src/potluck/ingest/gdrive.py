"""Google Drive REST client for Takeout auto-pull (#152).

Raw REST over httpx — deliberately NOT google-api-python-client (decision doc
§1: the SDK stack is a heavy permanent core dependency for four HTTPS calls;
absolute rule 2 forbids optional deps). The whole surface:

- ``POST oauth2.googleapis.com/token`` — code exchange + refresh
- ``GET  www.googleapis.com/drive/v3/files`` — list (q filter, paginated)
- ``GET  www.googleapis.com/drive/v3/files/{id}?alt=media`` — streamed download
- ``DELETE www.googleapis.com/drive/v3/files/{id}`` — pruning only

Auth is the installed-app loopback flow with PKCE (§2 — the device flow's
scope allowlist excludes drive.readonly, so it is structurally unusable for
Takeout files). The token file (0600, under config_dir()) is owned here too:
secrets never touch the database.

Everything network-shaped takes an injectable ``httpx.BaseTransport`` so the
test suite runs against ``httpx.MockTransport`` — no network in tests, ever.
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import ValidationError

from potluck.core.errors import GDriveApiError, GDriveAuthError
from potluck.models.gdrive import StoredToken

_logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
FILES_URL = "https://www.googleapis.com/drive/v3/files"

# Least privilege that can see Takeout's files (drive.file cannot — they are
# not created by this client); FULL is requested only for pruning (§2/§6).
DRIVE_SCOPE_READONLY = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_SCOPE_FULL = "https://www.googleapis.com/auth/drive"

_FOLDER_MIME = "application/vnd.google-apps.folder"
_LIST_FIELDS = "nextPageToken,files(id,name,size,md5Checksum)"
_PAGE_SIZE = 100
_TIMEOUT_S = 30.0  # per-operation; streamed downloads time out per chunk read
_CHUNK_BYTES = 1 << 20  # 1 MiB: stream to disk, never archive-sized memory
# Refresh the access token this many seconds before its stated expiry.
_EXPIRY_MARGIN_S = 60.0


# ---------------------------------------------------------------------------
# Token file (decision doc §3): 0600 from birth, atomic replace, never in DB.
# ---------------------------------------------------------------------------


def load_token(path: Path) -> StoredToken | None:
    """The stored token, or None when missing/unreadable/malformed.

    Malformed is deliberately non-fatal (warned): status then reports
    'unauthorized' and re-auth overwrites the bad file.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return StoredToken.model_validate_json(raw)
    except ValidationError:
        _logger.warning("malformed gdrive token file ignored: %s", path)
        return None


def save_token(path: Path, token: StoredToken) -> None:
    """Write the token file atomically with 0600 permissions from birth.

    A same-directory temp file is created O_EXCL with mode 0600 (no chmod
    window), then os.replace()d over the destination — a crash never leaves
    a half-written or world-readable token.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)  # a leftover from a crashed writer
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token.model_dump_json(indent=2))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Authorization flow pieces (§2): PKCE, consent URL, code exchange.
# ---------------------------------------------------------------------------


def make_pkce() -> tuple[str, str]:
    """A fresh PKCE pair: (code_verifier, S256 code_challenge)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_auth_url(
    *,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
    code_challenge: str,
) -> str:
    """The Google consent URL the user's browser opens.

    ``access_type=offline`` + ``prompt=consent`` guarantee a refresh token on
    every (re-)auth — without the prompt, Google only returns one on the very
    first consent.
    """
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    transport: httpx.BaseTransport | None = None,
) -> StoredToken:
    """Exchange the authorization code for tokens; return the StoredToken.

    Scopes come from the response's ``scope`` field — the GRANTED set, which
    the user may have narrowed on the consent screen.
    """
    with httpx.Client(transport=transport, timeout=_TIMEOUT_S) as http:
        response = http.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
        )
    payload = _token_payload(response, what="code exchange")
    refresh_token = payload.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise GDriveAuthError(
            "Google returned no refresh token — re-run `potluck gdrive auth` "
            "(the request always sends prompt=consent, so this is unexpected)"
        )
    scope = payload.get("scope")
    return StoredToken(
        refresh_token=refresh_token,
        client_id=client_id,
        scopes=scope.split() if isinstance(scope, str) else [],
        obtained_at=datetime.now(UTC),
    )


def _token_payload(response: httpx.Response, *, what: str) -> dict[str, Any]:
    """Parse a token-endpoint response; map OAuth errors onto our exceptions.

    ``invalid_grant``/``invalid_client`` mean the credential itself is dead —
    only re-auth (or fixing config) recovers, hence GDriveAuthError. Anything
    else (5xx, rate limits) is transient: GDriveApiError.
    """
    try:
        payload: dict[str, Any] = response.json()
    except json.JSONDecodeError:
        payload = {}
    if response.status_code == 200:
        return payload
    error = payload.get("error")
    if error in ("invalid_grant", "invalid_client"):
        raise GDriveAuthError(f"{what} rejected by Google ({error}) — re-run `potluck gdrive auth`")
    raise GDriveApiError(f"{what} failed: HTTP {response.status_code} {error or ''}".strip())


# ---------------------------------------------------------------------------
# The Drive client
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriveFile:
    """One Drive file/folder as the puller needs it."""

    id: str
    name: str
    size: int | None = None
    md5: str | None = None  # md5Checksum; verification target for downloads


class DriveClient:
    """Authenticated Drive v3 calls over one httpx.Client.

    Access tokens live in memory only (~1 h); the refresh token comes from
    the injected StoredToken and any rotation Google performs is persisted
    back to *token_path* immediately (0600, atomic). Auth failures raise
    GDriveAuthError; transient API trouble raises GDriveApiError; transport
    problems (offline) surface as httpx.TransportError for the caller.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        token: StoredToken,
        token_path: Path,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token = token
        self._token_path = token_path
        self._http = httpx.Client(transport=transport, timeout=_TIMEOUT_S)
        self._access_token: str | None = None
        self._expires_at = 0.0

    # -- lifecycle ------------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "DriveClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- scopes ---------------------------------------------------------------

    def has_scope(self, scope: str) -> bool:
        """Whether the stored grant carries *scope* (pruning gate, §6)."""
        return scope in self._token.scopes

    # -- auth plumbing ----------------------------------------------------------

    def _refresh(self) -> None:
        response = self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._token.refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        payload = _token_payload(response, what="token refresh")
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise GDriveApiError("token refresh returned no access_token")
        self._access_token = access_token
        expires_in = payload.get("expires_in")
        lifetime = float(expires_in) if isinstance(expires_in, int | float) else 0.0
        self._expires_at = _now_s() + max(lifetime - _EXPIRY_MARGIN_S, 0.0)
        rotated = payload.get("refresh_token")
        if isinstance(rotated, str) and rotated and rotated != self._token.refresh_token:
            # Google rotated the refresh token: persist or the next refresh dies.
            self._token = self._token.model_copy(
                update={"refresh_token": rotated, "obtained_at": datetime.now(UTC)}
            )
            save_token(self._token_path, self._token)

    def _bearer(self) -> dict[str, str]:
        if self._access_token is None or _now_s() >= self._expires_at:
            self._refresh()
        assert self._access_token is not None
        return {"Authorization": f"Bearer {self._access_token}"}

    def _get(self, url: str, *, params: dict[str, str]) -> httpx.Response:
        """GET with bearer; one forced refresh + retry on 401 (expired/revoked
        server-side between cycles); non-2xx maps to GDriveApiError."""
        response = self._http.get(url, params=params, headers=self._bearer())
        if response.status_code == 401:
            self._refresh()
            response = self._http.get(url, params=params, headers=self._bearer())
        return _checked(response)

    # -- listing ----------------------------------------------------------------

    def list_folders(self, name: str) -> list[DriveFile]:
        """Non-trashed folders named exactly *name* (usually one 'Takeout')."""
        escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        return self._list(f"name = '{escaped}' and mimeType = '{_FOLDER_MIME}' and trashed = false")

    def list_children(self, folder_id: str) -> list[DriveFile]:
        """Non-trashed direct children of *folder_id*."""
        return self._list(f"'{folder_id}' in parents and trashed = false")

    def _list(self, q: str) -> list[DriveFile]:
        files: list[DriveFile] = []
        page_token: str | None = None
        while True:
            params = {
                "q": q,
                "fields": _LIST_FIELDS,
                "pageSize": str(_PAGE_SIZE),
                "spaces": "drive",
            }
            if page_token is not None:
                params["pageToken"] = page_token
            payload: dict[str, Any] = self._get(FILES_URL, params=params).json()
            for entry in payload.get("files", []):
                size = entry.get("size")  # int64 arrives as a JSON string
                files.append(
                    DriveFile(
                        id=str(entry["id"]),
                        name=str(entry["name"]),
                        size=int(size) if size is not None else None,
                        md5=entry.get("md5Checksum"),
                    )
                )
            token = payload.get("nextPageToken")
            if not isinstance(token, str) or not token:
                return files
            page_token = token

    # -- download -----------------------------------------------------------------

    def download(self, file_id: str, dest: Path, *, expected_md5: str | None = None) -> None:
        """Stream *file_id* to *dest* (chunked — nothing archive-sized in memory).

        An existing *dest* is resumed via ``Range`` (multi-GB parts on flaky
        links); 416 (stale/oversized partial) restarts from zero. The final
        file is md5-verified against Drive's checksum when one is known — a
        mismatch removes *dest* (a corrupt partial must never poison later
        resumes) and raises GDriveApiError.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        if expected_md5 is not None and dest.exists() and _file_md5(dest) == expected_md5:
            return  # a retry after the SET's other member failed: already complete
        self._stream_to(file_id, dest)
        if expected_md5 is not None:
            actual = _file_md5(dest)
            if actual != expected_md5:
                dest.unlink(missing_ok=True)
                raise GDriveApiError(
                    f"download integrity failure for {dest.name}: "
                    f"md5 {actual} != Drive's {expected_md5}"
                )

    def _stream_to(self, file_id: str, dest: Path, *, retry_auth: bool = True) -> None:
        offset = dest.stat().st_size if dest.exists() else 0
        headers = self._bearer()
        if offset:
            headers["Range"] = f"bytes={offset}-"
        url = f"{FILES_URL}/{file_id}"
        with self._http.stream("GET", url, params={"alt": "media"}, headers=headers) as response:
            if response.status_code == 401 and retry_auth:
                self._refresh()
                self._stream_to(file_id, dest, retry_auth=False)
                return
            if response.status_code == 416:
                # Partial longer than the remote file: stale — restart clean.
                dest.unlink(missing_ok=True)
                self._stream_to(file_id, dest, retry_auth=retry_auth)
                return
            if response.status_code not in (200, 206):
                response.read()
                _checked(response)  # raises with the mapped error
            # 206 appends after our offset; a 200 despite Range means the
            # server sent the whole file — start over.
            mode = "ab" if response.status_code == 206 else "wb"
            with dest.open(mode) as handle:
                for chunk in response.iter_bytes(_CHUNK_BYTES):
                    handle.write(chunk)

    # -- delete (pruning only, §6) ---------------------------------------------

    def delete(self, file_id: str) -> None:
        """Permanently delete *file_id*. 404 = already gone: success — a
        prune re-run after a partial failure must be idempotent."""
        response = self._http.delete(f"{FILES_URL}/{file_id}", headers=self._bearer())
        if response.status_code == 401:
            self._refresh()
            response = self._http.delete(f"{FILES_URL}/{file_id}", headers=self._bearer())
        if response.status_code in (204, 404):
            return
        _checked(response)


def _checked(response: httpx.Response) -> httpx.Response:
    """Map non-2xx Drive responses onto GDriveApiError (auth handled upstream)."""
    if response.is_success:
        return response
    detail = ""
    try:
        error = response.json().get("error")
        if isinstance(error, dict):
            detail = f": {error.get('message', '')}"
    except json.JSONDecodeError:
        pass
    raise GDriveApiError(f"Drive API returned HTTP {response.status_code}{detail}")


def _file_md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _now_s() -> float:
    return time.monotonic()
