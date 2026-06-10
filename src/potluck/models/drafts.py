"""Draft DTOs — what a source plugin yields before the engine assigns ids/hashes."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from potluck.models.items import ItemKind


class BaseDraft(BaseModel):
    """What a source plugin yields; the engine owns ids/hashes/ledger."""

    model_config = ConfigDict(frozen=True)

    kind: ItemKind
    external_id: str | None = None
    ts: AwareDatetime | None = None
    title: str | None = None
    text: str | None = None
    lat: float | None = None
    lon: float | None = None
    parent_external_id: str | None = None
    meta: dict[str, JsonValue] = Field(default_factory=dict)


class NoteDraft(BaseDraft):
    """Draft for a note item."""

    kind: Literal[ItemKind.NOTE] = ItemKind.NOTE


# Becomes a kind-discriminated union when EmailDraft lands (P2)
type ItemDraft = NoteDraft
