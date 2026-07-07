"""Background import manager (#132): lifecycle, conflict, failure, recovery.

Service-level tests over ``services.imports.start_import`` and friends — the
same seam the REST adapter uses. No blind sleeps anywhere: every wait polls a
condition against a deadline. Gated toy plugins (a parse generator that blocks
on an Event between batches) make batch-level progress observable
deterministically.
"""

import io
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from potluck.core.config import Settings
from potluck.core.errors import (
    ImportInProgressError,
    ImportNotFoundError,
    UnsupportedArchiveError,
    UploadTooLargeError,
)
from potluck.ingest.plugins import Glob, ParseContext, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import NoteDraft
from potluck.models.items import ItemKind
from potluck.services import imports as imports_service
from potluck.services.context import AppContext, create_context
from potluck.testing.archives import write_archive
from tests.conftest import insert_import, insert_source

_DEADLINE_S = 30.0
_BATCH = 1000  # engine DEFAULT_BATCH_SIZE — first commit lands at this count


def _wait_for(predicate: Callable[[], bool], what: str) -> None:
    """Poll *predicate* until true or the deadline expires (no blind sleeps)."""
    deadline = time.monotonic() + _DEADLINE_S
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def _register_gated_source(release: threading.Event, *, second: int = 600) -> None:
    """Toy plugin yielding one full engine batch, blocking on *release*, then
    yielding *second* more drafts — progress is observable mid-import."""

    def parse(archive: Archive, pctx: ParseContext) -> Iterator[NoteDraft]:
        for i in range(_BATCH):
            yield NoteDraft(title=f"note {i}", text=f"gated body {i}")
        release.wait(timeout=_DEADLINE_S)
        for i in range(_BATCH, _BATCH + second):
            yield NoteDraft(title=f"note {i}", text=f"gated body {i}")

    source(name="gated", detect=Glob("*Gated/*.txt"), kinds=(ItemKind.NOTE,))(parse)


def _gated_archive(tmp_path: Path) -> Path:
    return write_archive(tmp_path / "gated.zip", {"Takeout/Gated/x.txt": b"x"}, fmt="zip")


def _progress(ctx: AppContext) -> tuple[int, str] | None:
    """(items_done, status) of the single imports row, or None before it exists."""
    with ctx.db.read() as conn:
        row = conn.execute(
            "SELECT items_new + items_duplicate + items_updated + items_skipped AS done,"
            " status FROM imports"
        ).fetchone()
    if row is None or row["done"] is None:
        return None
    return int(row["done"]), str(row["status"])


# ---------------------------------------------------------------------------
# Lifecycle: start -> batch-level progress -> terminal completed
# ---------------------------------------------------------------------------


def test_background_import_progress_then_completed(
    ctx: AppContext, tmp_path: Path, clean_registry: dict[str, Any]
) -> None:
    release = threading.Event()
    _register_gated_source(release)
    archive = _gated_archive(tmp_path)

    try:
        task = imports_service.start_import(ctx, archive)
        assert task.status == "running"
        assert task.path == str(archive)
        assert task.import_ids == []

        # The first committed batch is visible on the row while the parse
        # generator is still blocked — batch-level progress, not end-only.
        _wait_for(lambda: _progress(ctx) == (_BATCH, "running"), "first batch on the row")
    finally:
        release.set()

    _wait_for(
        lambda: (imports_service.import_status(ctx) or task).status != "running",
        "background task to finish",
    )
    done = imports_service.import_status(ctx)
    assert done is not None
    assert done.status == "completed"
    assert done.error is None
    assert done.finished_at is not None

    [import_id] = done.import_ids
    run = imports_service.get_import(ctx, import_id)
    assert run.status == "completed"
    assert run.items_done == _BATCH + 600
    assert run.items_new == _BATCH + 600
    assert run.items_total is None  # never pre-scanned just to count
    ctx.import_manager.join(_DEADLINE_S)


# ---------------------------------------------------------------------------
# Failure paths land on the task (detection errors have no row to carry them)
# ---------------------------------------------------------------------------


def test_background_failure_unknown_source(
    ctx: AppContext, tmp_path: Path, clean_registry: dict[str, Any]
) -> None:
    archive = write_archive(tmp_path / "mystery.zip", {"whatever/x.bin": b"?"}, fmt="zip")

    task = imports_service.start_import(ctx, archive)
    assert task.status == "running"

    _wait_for(
        lambda: (imports_service.import_status(ctx) or task).status != "running",
        "background task to finish",
    )
    done = imports_service.import_status(ctx)
    assert done is not None
    assert done.status == "failed"
    assert done.error is not None and "no source plugin recognises" in done.error
    assert done.import_ids == []
    ctx.import_manager.join(_DEADLINE_S)


def test_background_failure_corrupt_archive(
    ctx: AppContext, tmp_path: Path, clean_registry: dict[str, Any]
) -> None:
    bad = tmp_path / "corrupt.zip"
    bad.write_bytes(b"PK\x03\x04" + b"\x00" * 32)

    imports_service.start_import(ctx, bad)
    _wait_for(
        lambda: (
            (status := imports_service.import_status(ctx)) is not None
            and status.status != "running"
        ),
        "background task to finish",
    )
    done = imports_service.import_status(ctx)
    assert done is not None
    assert done.status == "failed"
    assert done.error is not None and "corrupt or unreadable" in done.error
    ctx.import_manager.join(_DEADLINE_S)


def test_start_import_missing_path_raises_synchronously(ctx: AppContext) -> None:
    with pytest.raises(UnsupportedArchiveError, match="no such file"):
        imports_service.start_import(ctx, Path("/nope/missing.zip"))
    assert imports_service.import_status(ctx) is None  # nothing ever started


# ---------------------------------------------------------------------------
# Conflict: one import at a time; the manager is reusable afterwards
# ---------------------------------------------------------------------------


def test_second_start_conflicts_then_manager_reusable(
    ctx: AppContext, tmp_path: Path, clean_registry: dict[str, Any]
) -> None:
    release = threading.Event()
    _register_gated_source(release)
    archive = _gated_archive(tmp_path)

    try:
        imports_service.start_import(ctx, archive)
        with pytest.raises(ImportInProgressError):
            imports_service.start_import(ctx, archive)
    finally:
        release.set()

    _wait_for(
        lambda: (
            (status := imports_service.import_status(ctx)) is not None
            and status.status != "running"
        ),
        "first import to finish",
    )
    ctx.import_manager.join(_DEADLINE_S)

    # A terminal task no longer blocks new starts.
    again = imports_service.start_import(ctx, archive)
    assert again.status == "running"
    _wait_for(
        lambda: (
            (status := imports_service.import_status(ctx)) is not None
            and status.status != "running"
        ),
        "second import to finish",
    )
    final = imports_service.import_status(ctx)
    assert final is not None and final.status == "completed"
    ctx.import_manager.join(_DEADLINE_S)


# ---------------------------------------------------------------------------
# Recovery runs ONLY at write-ownership entrypoints (#132 review):
# read-only contexts never sweep; taking import ownership does.
# ---------------------------------------------------------------------------


def test_read_only_context_does_not_sweep_running_rows(settings: Settings) -> None:
    """`potluck status` (or any read-only invocation) while another process
    imports must NEVER mark that live 'running' row interrupted."""
    ctx1 = create_context(settings)
    try:
        iid = ctx1.db.write(lambda conn: insert_import(conn, insert_source(conn)))
    finally:
        ctx1.db.close()

    # A plain context open is not write ownership — the row stays 'running'.
    ctx2 = create_context(settings)
    try:
        run = imports_service.get_import(ctx2, iid)
        assert run.status == "running"
        assert run.error is None
        assert run.finished_at is None
    finally:
        ctx2.db.close()


def test_import_run_sweeps_stale_running_rows(
    ctx: AppContext, tmp_path: Path, clean_registry: dict[str, Any]
) -> None:
    """Running an import takes write ownership: the stale 'running' row left
    by a crash is failed('interrupted') before the new run begins — this is
    the CLI import path AND what the background manager worker executes."""
    stale = ctx.db.write(lambda conn: insert_import(conn, insert_source(conn)))

    release = threading.Event()
    release.set()  # no gating needed; reuse the registered toy source
    _register_gated_source(release, second=0)
    [run] = imports_service.import_path(ctx, _gated_archive(tmp_path))

    assert run.status == "completed"
    recovered = imports_service.get_import(ctx, stale)
    assert recovered.status == "failed"
    assert recovered.error == "interrupted"
    assert recovered.finished_at is not None


def test_recover_interrupted_imports_service(settings: Settings) -> None:
    """The explicit ownership seam sweeps and reports the count; idempotent."""
    ctx = create_context(settings)
    try:
        iid = ctx.db.write(lambda conn: insert_import(conn, insert_source(conn)))
        assert imports_service.recover_interrupted_imports(ctx) == 1
        run = imports_service.get_import(ctx, iid)
        assert (run.status, run.error) == ("failed", "interrupted")
        assert run.finished_at is not None
        assert imports_service.recover_interrupted_imports(ctx) == 0
    finally:
        ctx.db.close()


# ---------------------------------------------------------------------------
# get_import / list_imports service contracts
# ---------------------------------------------------------------------------


def test_get_import_unknown_id_raises(ctx: AppContext) -> None:
    with pytest.raises(ImportNotFoundError, match="999999"):
        imports_service.get_import(ctx, 999999)


def test_list_imports_offset_and_total(ctx: AppContext) -> None:
    def _setup(conn: sqlite3.Connection) -> None:
        sid = insert_source(conn)
        for _ in range(3):
            insert_import(conn, sid)

    ctx.db.write(_setup)

    page = imports_service.list_imports(ctx, limit=2, offset=0)
    assert page.total == 3
    assert [r.id for r in page.runs] == [3, 2]  # newest first

    rest = imports_service.list_imports(ctx, limit=2, offset=2)
    assert rest.total == 3
    assert [r.id for r in rest.runs] == [1]


# ---------------------------------------------------------------------------
# Upload storage: managed dir + path-traversal sanity
# ---------------------------------------------------------------------------


def test_store_upload_confines_filename_to_uploads_dir(ctx: AppContext) -> None:
    stored = imports_service.store_upload(ctx, "../../../evil.zip", io.BytesIO(b"data"))
    assert stored.name == "evil.zip"
    assert stored.read_bytes() == b"data"
    assert stored.is_relative_to(ctx.settings.uploads_dir)


def test_store_upload_rejects_empty_and_dot_names(ctx: AppContext) -> None:
    for bad in ("", ".", "..", "a/../.."):
        with pytest.raises(UnsupportedArchiveError, match="filename"):
            imports_service.store_upload(ctx, bad, io.BytesIO(b"x"))


def test_store_upload_over_limit_rejected_and_partial_removed(ctx: AppContext) -> None:
    """The copy stops at max_upload_bytes; nothing is left in the store."""
    small = AppContext(
        settings=ctx.settings.model_copy(update={"max_upload_bytes": 1024}), db=ctx.db
    )

    with pytest.raises(UploadTooLargeError, match="max_upload_bytes"):
        imports_service.store_upload(small, "big.zip", io.BytesIO(b"x" * 2048))

    uploads = small.settings.uploads_dir
    leftovers = list(uploads.rglob("*")) if uploads.exists() else []
    assert leftovers == [], f"partial upload left behind: {leftovers}"

    # At exactly the limit the upload is accepted.
    stored = imports_service.store_upload(small, "ok.zip", io.BytesIO(b"x" * 1024))
    assert stored.read_bytes() == b"x" * 1024


# ---------------------------------------------------------------------------
# Registered sources: the thin service over the plugin registry
# ---------------------------------------------------------------------------


def test_list_sources_reports_real_plugins(ctx: AppContext) -> None:
    sources = imports_service.list_sources(ctx)
    by_name = {s.name: s for s in sources}
    assert "gmail" in by_name and "google_keep" in by_name
    assert ItemKind.EMAIL in by_name["gmail"].kinds
    assert ItemKind.NOTE in by_name["google_keep"].kinds
    assert [s.name for s in sources] == sorted(s.name for s in sources)
