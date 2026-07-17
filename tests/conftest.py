"""Shared fixtures for the Potluck test suite.

Patterns established here are reused by every later phase:

- ``isolated_dirs`` (autouse): every test gets private platformdirs roots under
  ``tmp_path`` and a clean ``POTLUCK_*`` environment, so tests never touch real
  user data and are safe under pytest-xdist.
"""

import hashlib
import os
import sqlite3
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from potluck.api.app import create_app
from potluck.core.config import Settings
from potluck.ingest.engine import DEFAULT_BATCH_SIZE, run_import
from potluck.models.drafts import EmailAttachment, EmailDraft, ItemDraft
from potluck.services.context import AppContext, create_context
from potluck.services.imports import import_path
from potluck.testing.keep import write_keep_takeout

# ---------------------------------------------------------------------------
# Plain FK-scaffolding helpers (importable by any test module, reused across
# storage and ingest-layer tests).
# ---------------------------------------------------------------------------


def ingest_keep_corpus(ctx: AppContext, tmp_path: Path, count: int = 20, seed: int = 42) -> None:
    """Ingest a synthetic Keep corpus into *ctx*.

    Shared by any test module that needs a populated FTS corpus.  Uses a
    deterministic RNG seed so results are identical across runs.
    """
    archive = write_keep_takeout(tmp_path / "keep_takeout", count, seed=seed, fmt="dir")
    import_path(ctx, archive)


# Sentinel: lets email_draft() distinguish "use the n-derived default ts"
# from an explicit ts=None (undated draft).
_TS_DEFAULT: Final = object()


def email_draft(
    n: int = 1,
    *,
    message_id: str | None = None,
    thread_key: str | None = None,
    in_reply_to: str | None = None,
    ts: datetime | None | object = _TS_DEFAULT,
    title: str | None = None,
    text: str | None = None,
    from_addr: str | None = None,
    from_name: str | None = None,
    to_addrs: tuple[str, ...] = (),
    to_names: tuple[str, ...] = (),
    cc_addrs: tuple[str, ...] = (),
    cc_names: tuple[str, ...] = (),
    bcc_addrs: tuple[str, ...] = (),
    labels: tuple[str, ...] = (),
    attachments: tuple[EmailAttachment, ...] = (),
) -> EmailDraft:
    """THE deterministic EmailDraft factory for engine/service/adapter tests.

    Defaults derive from *n*: message id ``m{n}@potluck.test``, external id
    ``mid:<message id>``, thread_key = the message id, subject/body
    ``subject {n}`` / ``body {n}``, sender ``sender{n}@potluck.test``, and a
    ts ordered by *n* (2024-01-01 + n hours). Pass ``ts=None`` for an undated
    draft; pass any field explicitly to override its default.
    """
    mid = message_id or f"m{n}@potluck.test"
    if ts is _TS_DEFAULT:
        resolved_ts: datetime | None = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=n)
    else:
        assert ts is None or isinstance(ts, datetime)
        resolved_ts = ts
    return EmailDraft(
        external_id=f"mid:{mid}",
        message_id=mid,
        in_reply_to=in_reply_to,
        thread_key=thread_key or mid,
        ts=resolved_ts,
        title=f"subject {n}" if title is None else title,
        text=f"body {n}" if text is None else text,
        from_addr=f"sender{n}@potluck.test" if from_addr is None else from_addr,
        from_name=from_name,
        to_addrs=to_addrs,
        to_names=to_names,
        cc_addrs=cc_addrs,
        cc_names=cc_names,
        bcc_addrs=bcc_addrs,
        labels=labels,
        attachments=attachments,
    )


def ingest_email_drafts(
    ctx: AppContext,
    *drafts: ItemDraft,
    source_name: str = "gmail-test",
    parser_version: int = 1,
    path: str = "/tmp/t.mbox",
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """run_import wrapper for draft-level tests; returns the import id.

    Accepts any ItemDraft (mixed-kind corpora ingest notes alongside emails).
    """
    return run_import(
        ctx.db,
        source_name=source_name,
        parser_version=parser_version,
        drafts=iter(drafts),
        path=path,
        file_hash=None,
        batch_size=batch_size,
    )


def email_item_id(ctx: AppContext, message_id: str) -> int:
    """Resolve an ingested email's item id via its emails satellite row."""
    with ctx.db.read() as conn:
        row = conn.execute(
            "SELECT item_id FROM emails WHERE message_id = ?", (message_id,)
        ).fetchone()
    assert row is not None, f"no emails row for message_id {message_id!r}"
    return int(row[0])


def insert_source(conn: sqlite3.Connection, name: str = "test-src") -> int:
    """Insert a row into ``sources`` and return its rowid."""
    conn.execute("INSERT INTO sources (name) VALUES (?)", (name,))
    row = conn.execute("SELECT last_insert_rowid()").fetchone()
    return int(row[0])


def insert_import(conn: sqlite3.Connection, source_id: int) -> int:
    """Insert a row into ``imports`` and return its rowid."""
    conn.execute(
        """INSERT INTO imports (source_id, path, parser_version, started_at)
           VALUES (?, '/tmp/x', 1, '2024-01-01T00:00:00.000Z')""",
        (source_id,),
    )
    row = conn.execute("SELECT last_insert_rowid()").fetchone()
    return int(row[0])


def insert_item(
    conn: sqlite3.Connection,
    source_id: int,
    import_id: int,
    *,
    content_hash: str,
    kind: str = "note",
    external_id: str | None = None,
    ts: str | None = None,
    title: str | None = None,
    text: str | None = None,
) -> int:
    """Insert a minimal ``items`` row and return its rowid.

    THE direct-SQL item helper for storage/search-layer tests (the ingest
    engine is deliberately bypassed; engine-level tests use run_import).
    """
    cursor = conn.execute(
        """INSERT INTO items (source_id, import_id, kind, external_id, content_hash,
                              ts, title, text)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (source_id, import_id, kind, external_id, content_hash, ts, title, text),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


@pytest.fixture
def clean_registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Swap the plugin registry for an empty dict and empty the sources
    package path, so tests can register toy plugins via @source without real
    plugins (google_keep, …) leaking in through discover().

    monkeypatch restores both on teardown.
    """
    import potluck.ingest.plugins as plugins_mod
    import potluck.ingest.sources as sources_pkg

    fresh: dict[str, Any] = {}
    monkeypatch.setattr(plugins_mod, "_registry", fresh)
    monkeypatch.setattr(sources_pkg, "__path__", [])
    return fresh


@pytest.fixture
def isolated_sources(
    clean_registry: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Path]:
    """clean_registry plus module discovery rooted at tmp_path: write toy
    plugin modules into the returned directory and discover() imports them.

    Modules imported during the test are dropped from sys.modules on teardown
    so each test sees fresh import side effects.
    """
    import potluck.ingest.sources as sources_pkg

    before_modules = set(sys.modules.keys())
    monkeypatch.setattr(sources_pkg, "__path__", [str(tmp_path)])

    yield tmp_path

    for key in list(sys.modules.keys()):
        if key not in before_modules:
            del sys.modules[key]


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate platformdirs + POTLUCK_* env for each test.

    Potluck resolves all filesystem locations through the functions in
    ``potluck.core.paths`` at call time (never import-time constants), so
    patching the environment here is sufficient isolation.

    POTLUCK_DB_PATH is pinned explicitly (mirroring the Linux XDG layout, so
    it equals default_db_path() there): platformdirs' Windows backend ignores
    XDG_*, and env beats config.toml — without this, Windows tests would hit
    the user's real %LOCALAPPDATA% database.
    """
    for key in [k for k in os.environ if k.startswith("POTLUCK_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("POTLUCK_DB_PATH", str(tmp_path / "data" / "potluck" / "potluck.db"))
    return tmp_path


@pytest.fixture
def settings(isolated_dirs: Path) -> Settings:
    """Zero-config Settings resolving inside the isolated tmp dirs."""
    return Settings()


@pytest.fixture
def ctx(settings: Settings) -> Iterator[AppContext]:
    """AppContext on a fresh tmp-path SQLite database.

    This is THE fixture for service-layer tests (and everything above them):
    real Settings, real Database, fully isolated, closed on teardown.
    """
    context = create_context(settings)
    yield context
    context.db.close()


@pytest.fixture
def api_client(ctx: AppContext, tmp_path: Path) -> Iterator[TestClient]:
    """FastAPI TestClient over the ctx fixture (lifespan runs; no SPA build).

    ``web_dist`` is pinned to a nonexistent directory so the app is hermetic
    even when the repo has a real ``web/dist`` build lying around.
    """
    no_spa = AppContext(
        settings=ctx.settings.model_copy(update={"web_dist": tmp_path / "no-spa"}),
        db=ctx.db,
    )
    with TestClient(create_app(no_spa)) as client:
        yield client


# ---------------------------------------------------------------------------
# Mock Google Drive (#152): an in-memory Drive v3 + OAuth token endpoint
# behind httpx.MockTransport. An importable helper (not a fixture) — client,
# puller and integration tests build their own instances and inject
# ``.transport()`` into DriveClient. NO network in tests, ever.
# ---------------------------------------------------------------------------


class MockDrive:
    """Simulates exactly the surface potluck.ingest.gdrive touches.

    Knobs (set between requests to script failure modes):

    - ``refresh_error``: token-endpoint error code for refresh attempts
      (e.g. ``"invalid_grant"``).
    - ``rotate_refresh_to``: next refresh response carries this new refresh
      token (Google-style rotation); subsequent refreshes require it.
    - ``fail_next_status``: one-shot HTTP status for the next Drive call.
    - ``fail_download_ids``: file ids whose download always 503s.
    - ``offline``: every request raises httpx.ConnectError.
    - ``page_size``: forces files.list pagination at this size.
    """

    def __init__(
        self,
        *,
        refresh_token: str = "rtok-1",
        scopes: tuple[str, ...] = ("https://www.googleapis.com/auth/drive.readonly",),
        auth_code: str = "authcode-1",
    ) -> None:
        self.refresh_token = refresh_token
        self.scopes = list(scopes)
        self.auth_code = auth_code
        self.access_token = "atok-1"
        self._token_serial = 1
        self.folders: dict[str, str] = {}  # id -> name
        # file id -> (folder_id, name, content)
        self.files: dict[str, tuple[str, str, bytes]] = {}
        self._next_id = 0
        self.deleted: list[str] = []
        self.refresh_calls = 0
        self.exchange_calls: list[dict[str, str]] = []
        self.list_queries: list[str] = []
        self.download_ranges: list[tuple[str, str | None]] = []
        self.refresh_error: str | None = None
        self.rotate_refresh_to: str | None = None
        self.fail_next_status: int | None = None
        self.fail_download_ids: set[str] = set()
        self.offline = False
        self.page_size: int | None = None

    # -- corpus construction --------------------------------------------------

    def add_folder(self, name: str) -> str:
        self._next_id += 1
        folder_id = f"folder-{self._next_id}"
        self.folders[folder_id] = name
        return folder_id

    def add_file(self, folder_id: str, name: str, content: bytes) -> str:
        self._next_id += 1
        file_id = f"file-{self._next_id}"
        self.files[file_id] = (folder_id, name, content)
        return file_id

    def expire_access(self) -> None:
        """Invalidate the current access token (old bearers now 401)."""
        self._token_serial += 1
        self.access_token = f"atok-{self._token_serial}"

    def file_md5(self, file_id: str) -> str:
        return hashlib.md5(self.files[file_id][2], usedforsecurity=False).hexdigest()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    # -- request handling ------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("mock drive is offline", request=request)
        if request.url.host == "oauth2.googleapis.com" and request.url.path == "/token":
            return self._token_endpoint(request)
        if self.fail_next_status is not None:
            status = self.fail_next_status
            self.fail_next_status = None
            return httpx.Response(status, json={"error": {"message": f"scripted {status}"}})
        if request.headers.get("Authorization") != f"Bearer {self.access_token}":
            return httpx.Response(401, json={"error": {"message": "Invalid Credentials"}})
        path = request.url.path
        if request.method == "GET" and path == "/drive/v3/files":
            return self._list(request)
        if path.startswith("/drive/v3/files/"):
            file_id = path.removeprefix("/drive/v3/files/")
            if request.method == "GET" and request.url.params.get("alt") == "media":
                return self._download(request, file_id)
            if request.method == "DELETE":
                if file_id not in self.files:
                    return httpx.Response(404, json={"error": {"message": "not found"}})
                del self.files[file_id]
                self.deleted.append(file_id)
                return httpx.Response(204)
        return httpx.Response(400, json={"error": {"message": f"unhandled {request.url}"}})

    def _token_endpoint(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form.get("grant_type") == "refresh_token":
            self.refresh_calls += 1
            if self.refresh_error is not None:
                return httpx.Response(400, json={"error": self.refresh_error})
            if form.get("refresh_token") != self.refresh_token:
                return httpx.Response(400, json={"error": "invalid_grant"})
            payload: dict[str, object] = {
                "access_token": self.access_token,
                "expires_in": 3600,
                "scope": " ".join(self.scopes),
                "token_type": "Bearer",
            }
            if self.rotate_refresh_to is not None:
                self.refresh_token = self.rotate_refresh_to
                payload["refresh_token"] = self.rotate_refresh_to
                self.rotate_refresh_to = None
            return httpx.Response(200, json=payload)
        if form.get("grant_type") == "authorization_code":
            self.exchange_calls.append(form)
            if form.get("code") != self.auth_code:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={
                    "access_token": self.access_token,
                    "refresh_token": self.refresh_token,
                    "expires_in": 3600,
                    "scope": " ".join(self.scopes),
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def _list(self, request: httpx.Request) -> httpx.Response:
        q = request.url.params.get("q") or ""
        self.list_queries.append(q)
        if "mimeType = 'application/vnd.google-apps.folder'" in q:
            wanted = q.split("name = '", 1)[1].split("'", 1)[0]
            entries = [
                {"id": fid, "name": name}
                for fid, name in sorted(self.folders.items())
                if name == wanted
            ]
        elif "' in parents" in q:
            parent = q.split("'", 1)[1].split("'", 1)[0]
            entries = [
                {
                    "id": fid,
                    "name": name,
                    "size": str(len(content)),
                    "md5Checksum": self.file_md5(fid),
                }
                for fid, (folder_id, name, content) in sorted(self.files.items())
                if folder_id == parent
            ]
        else:
            entries = []
        body: dict[str, object] = {}
        if self.page_size is not None:
            offset = int(request.url.params.get("pageToken") or "0")
            page = entries[offset : offset + self.page_size]
            if offset + self.page_size < len(entries):
                body["nextPageToken"] = str(offset + self.page_size)
            body["files"] = page
        else:
            body["files"] = entries
        return httpx.Response(200, json=body)

    def _download(self, request: httpx.Request, file_id: str) -> httpx.Response:
        if file_id in self.fail_download_ids:
            return httpx.Response(503, json={"error": {"message": "scripted download failure"}})
        if file_id not in self.files:
            return httpx.Response(404, json={"error": {"message": "not found"}})
        content = self.files[file_id][2]
        range_header = request.headers.get("Range")
        self.download_ranges.append((file_id, range_header))
        if range_header is not None:
            start = int(range_header.removeprefix("bytes=").split("-", 1)[0])
            if start >= len(content):
                return httpx.Response(416)
            return httpx.Response(
                206,
                content=content[start:],
                headers={"Content-Range": f"bytes {start}-{len(content) - 1}/{len(content)}"},
            )
        return httpx.Response(200, content=content)
