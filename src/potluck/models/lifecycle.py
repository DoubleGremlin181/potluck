"""Data-lifecycle DTOs (#153): the result shape of rm/forget operations."""

from pydantic import BaseModel, Field


class RemoveResult(BaseModel):
    """Counts from one rm/forget call — what actually left the database."""

    items_deleted: int = Field(description="Item rows deleted (satellites/FTS cascade).")
    imports_deleted: int = Field(
        description="Import-history rows deleted (0 for item-id deletes, which "
        "instead clear file_hash on the owning run so a re-import can restore)."
    )
    hashes_suppressed: int = Field(
        description="Content hashes newly added to suppressed_hashes (forget "
        "only; 0 when a hash was already suppressed or for plain rm)."
    )
