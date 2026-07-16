"""Import run DTOs."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from potluck.models.items import ItemKind

type ImportStatus = Literal["running", "completed", "failed"]


class ImportRun(BaseModel):
    """A fully-hydrated import run row returned from the storage layer."""

    id: int
    source: str  # sources.name via JOIN — DTOs speak names, not FK ids
    path: str
    file_hash: str | None
    parser_version: int
    started_at: datetime
    finished_at: datetime | None
    status: ImportStatus
    items_new: int
    items_duplicate: int
    items_updated: int
    items_skipped: int
    # Drafts dropped because their content hash is in suppressed_hashes
    # (forgotten content, #153) — its own counter, never folded into skipped.
    items_suppressed: int = 0
    # Expected item count for progress bars; None = unknown (#132) — streaming
    # sources cannot know it upfront and the engine never pre-scans to count.
    items_total: int | None = None
    error: str | None
    # Whether the run extracted attachment blobs (parse-affecting setting;
    # participates in the completed-run short-circuit).
    extract_attachments: bool = False

    @computed_field(  # type: ignore[prop-decorator]
        description="Items processed so far (sum of the per-batch counters) — "
        "the poll-side progress numerator; items_total is the denominator "
        "when known."
    )
    @property
    def items_done(self) -> int:
        """Progress numerator, derived from the existing per-batch counters."""
        return (
            self.items_new
            + self.items_duplicate
            + self.items_updated
            + self.items_skipped
            + self.items_suppressed
        )


class ImportTask(BaseModel):
    """In-memory snapshot of the background import operation (#132).

    Covers the WHOLE operation — archive detection (before any imports row
    exists) through the per-source runs — so detection-phase failures have
    somewhere to land. Deliberately not durable: the imports table is the
    durable progress record, and startup recovery marks rows orphaned by a
    crash as failed.
    """

    path: str = Field(description="Archive path being imported.")
    status: ImportStatus = Field(description="running until every detected source finishes.")
    error: str | None = Field(default=None, description="Failure detail once status is 'failed'.")
    import_ids: list[int] = Field(
        default_factory=list,
        description="Ids of the per-source import rows; filled in on completion.",
    )
    started_at: datetime
    finished_at: datetime | None = None


class ImportListResponse(BaseModel):
    """One page of import history (newest first)."""

    runs: list[ImportRun]
    total: int = Field(description="Unpaginated run count under no filters.")


class StartImportRequest(BaseModel):
    """Body of POST /api/imports: import an archive already on this machine."""

    path: str = Field(
        min_length=1,
        description="Server-local path to the archive (zip / tgz) or directory. "
        "Localhost-only by design: the API binds 127.0.0.1 and its only "
        "client is the machine's owner (v1 threat model).",
    )


class SourceInfo(BaseModel):
    """A registered source plugin, as reported by GET /api/sources."""

    name: str
    kinds: list[ItemKind] = Field(description="Item kinds this source produces.")
