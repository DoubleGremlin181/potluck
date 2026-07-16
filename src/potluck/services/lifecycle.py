"""Data-lifecycle service (#153): rm/forget over imports, sources, and items.

The seam the CLI and API adapters use for destructive operations. Three
selectors, one semantic split:

- **rm** deletes rows (satellites/files/FTS follow via cascades + triggers)
  and keeps the door open for the content to RETURN on a re-import of the
  same archive (import/source selectors delete the ledger rows that power
  the #126 short-circuit; the item selector clears the owning ``file_hash``).
- **forget** (``forget=True``) additionally records the deleted items'
  content hashes in ``suppressed_hashes`` — the ingest engine anti-joins
  every batch against it, so forgotten content never re-ingests.

Every delete is one transaction on the writer thread: validation (unknown
ids, still-running imports) happens inside it, so it cannot race a starting
import, and readers never observe a half-deleted state.

Deliberately NOT exposed over MCP: destructive operations stay out of the
AI-facing surface for v1 (an AI client asked to "clean up" must not be able
to irreversibly delete user data; the human-facing CLI/API/UI all confirm).
"""

from functools import partial

from potluck.models.lifecycle import RemoveResult
from potluck.services.context import AppContext
from potluck.storage import lifecycle as _storage_lifecycle


def remove_import(ctx: AppContext, import_id: int, *, forget: bool = False) -> RemoveResult:
    """Delete one import run: every item it ingested plus its history row.

    Deleting the ledger row is the issue's "import-history delete" AND what
    lets a re-import of the same archive re-ingest (short-circuit key gone).

    Raises:
        ImportNotFoundError: if no import run has this id (HTTP 404).
        ImportRunningError: if the run is still 'running' (HTTP 409).
    """
    items, suppressed = ctx.db.write(
        partial(_storage_lifecycle.delete_import, import_id=import_id, forget=forget)
    )
    return RemoveResult(items_deleted=items, imports_deleted=1, hashes_suppressed=suppressed)


def remove_source(ctx: AppContext, source_name: str, *, forget: bool = False) -> RemoveResult:
    """Delete a source's items and its whole import history (sources row stays).

    Raises:
        SourceNotFoundError: if no source with this name exists in the DB.
        ImportRunningError: if one of the source's runs is still 'running'.
    """
    items, imports, suppressed = ctx.db.write(
        partial(_storage_lifecycle.delete_source, source_name=source_name, forget=forget)
    )
    return RemoveResult(items_deleted=items, imports_deleted=imports, hashes_suppressed=suppressed)


def remove_items(ctx: AppContext, item_ids: list[int], *, forget: bool = False) -> RemoveResult:
    """Delete individual items (atomic over the whole list).

    Plain rm means "may return on re-import": the owning runs' ``file_hash``
    is cleared so the same archive re-scans instead of short-circuiting.
    With ``forget=True`` the content is suppressed and never comes back.

    Raises:
        ItemNotFoundError: if any id is unknown — nothing is deleted (404).
        ImportRunningError: if an owning run is still 'running' (409).
    """
    items, suppressed = ctx.db.write(
        partial(_storage_lifecycle.delete_items, item_ids=item_ids, forget=forget)
    )
    return RemoveResult(items_deleted=items, imports_deleted=0, hashes_suppressed=suppressed)
