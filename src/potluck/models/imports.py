"""Import run DTOs."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

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
    error: str | None
