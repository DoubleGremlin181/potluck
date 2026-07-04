"""REST imports endpoints (#132): start (server path or upload), poll, history.

Thin adapter over ``services.imports`` — the import itself runs on the
service-level background manager; these handlers only translate HTTP.
"""

from pathlib import Path as FsPath
from typing import Annotated

from fastapi import APIRouter, File, Path, Query, UploadFile

from potluck.api.deps import CtxDep
from potluck.api.errors import error_responses
from potluck.models.imports import (
    ImportListResponse,
    ImportRun,
    ImportTask,
    SourceInfo,
    StartImportRequest,
)
from potluck.services import imports as imports_service

router = APIRouter(tags=["imports"])

_IMPORT_404 = {404: "No import run with this id exists."}
_START_400 = {400: "The path does not exist, or the upload filename is unusable."}


@router.post(
    "/imports",
    status_code=202,
    summary="Start an import from a server path",
    responses=error_responses(400, 409, 422, overrides=_START_400),
)
def start_import(ctx: CtxDep, req: StartImportRequest) -> ImportTask:
    """Import an archive (or multi-part set, via its first part) already on
    this machine. Localhost-only by design: the API binds 127.0.0.1 and its
    only client is the machine's owner (v1 threat model).

    Runs in the background — the 202 body is the initial task snapshot. Poll
    `/api/imports/status` for the operation (including archive detection and
    its failures), and the per-source rows in `/api/imports` for batch-level
    progress (`items_done` / `items_total`; `items_total` null = unknown).
    """
    return imports_service.start_import(ctx, FsPath(req.path))


@router.post(
    "/imports/upload",
    status_code=202,
    summary="Upload an archive and import it",
    responses=error_responses(400, 409, 422, overrides=_START_400),
)
def upload_import(
    ctx: CtxDep,
    file: Annotated[
        UploadFile,
        File(description="Archive file (zip / tgz); stored in the managed uploads directory."),
    ],
) -> ImportTask:
    """Store the uploaded archive under the data directory, then import it in
    the background exactly like the server-path variant. Only the basename of
    the client filename is used (path-traversal sanity)."""
    return imports_service.start_upload_import(ctx, file.filename or "", file.file)


@router.get(
    "/imports/status",
    summary="Poll the background import operation",
)
def import_status(ctx: CtxDep) -> ImportTask | None:
    """Snapshot of the current (or most recent) background import operation;
    `null` if none has started since the server came up.

    This is the only place detection-phase failures (unrecognised or corrupt
    archive) surface — they happen before any import row exists. Once
    `import_ids` is populated, poll those rows for per-source progress."""
    return imports_service.import_status(ctx)


@router.get("/imports", summary="Import history", responses=error_responses(422))
def list_imports(
    ctx: CtxDep,
    limit: Annotated[int, Query(ge=1, le=200, description="Maximum rows per page.")] = 50,
    offset: Annotated[int, Query(ge=0, description="Rows to skip before the page.")] = 0,
) -> ImportListResponse:
    """Import runs, newest first, with per-run counters, progress and errors.

    `total` is the unpaginated run count, so pages exist while
    `offset < total`."""
    return imports_service.list_imports(ctx, limit=limit, offset=offset)


@router.get(
    "/imports/{import_id}",
    summary="Fetch one import run",
    responses=error_responses(404, 422, overrides=_IMPORT_404),
)
def get_import(
    ctx: CtxDep,
    import_id: Annotated[int, Path(description="Import run id, as listed in the history.")],
) -> ImportRun:
    """One import row including its progress fields — the UI's poll target
    while the run is `running` (`items_done` advances once per committed
    batch; the row survives restarts)."""
    return imports_service.get_import(ctx, import_id)


@router.get("/sources", summary="Registered source plugins")
def list_sources(ctx: CtxDep) -> list[SourceInfo]:
    """Every registered source plugin and the item kinds it produces —
    what an archive COULD import, independent of any database content."""
    return imports_service.list_sources(ctx)
