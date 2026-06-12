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

    def extra_hash_parts(self) -> tuple[str, ...]:
        """Kind-specific identity parts appended to the content hash.

        Any draft field persisted OUTSIDE the items row (satellite tables)
        must be covered here, or a change to it would dedup away as
        "unchanged". The default () appends nothing, keeping base-field-only
        kinds byte-identical to their P1 hashes.
        """
        return ()


class NoteDraft(BaseDraft):
    """Draft for a note item."""

    kind: Literal[ItemKind.NOTE] = ItemKind.NOTE


class EmailAttachment(BaseModel):
    """Attachment metadata carried on an EmailDraft; bodies never enter the DB."""

    model_config = ConfigDict(frozen=True)

    filename: str | None = None
    mime: str | None = None
    size_bytes: int
    sha256: str


class EmailDraft(BaseDraft):
    """Draft for an email item; satellite fields land in the emails table.

    title = subject, text = body, ts = Date header. message_id/in_reply_to
    are normalized (no angle brackets); thread_key is the deterministic
    conversation key computed by the source plugin from the message's own
    headers.
    """

    kind: Literal[ItemKind.EMAIL] = ItemKind.EMAIL
    message_id: str | None = None
    in_reply_to: str | None = None
    thread_key: str
    from_addr: str | None = None
    to_addrs: tuple[str, ...] = ()
    cc_addrs: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    attachments: tuple[EmailAttachment, ...] = ()

    def extra_hash_parts(self) -> tuple[str, ...]:
        # Variable-length groups are joined with a unit separator so values
        # can never shift across field boundaries (to vs cc vs labels).
        return (
            self.message_id or "",
            self.in_reply_to or "",
            self.thread_key,
            self.from_addr or "",
            "\x1f".join(self.to_addrs),
            "\x1f".join(self.cc_addrs),
            "\x1f".join(self.labels),
            "\x1f".join(a.sha256 for a in self.attachments),
        )


type ItemDraft = NoteDraft | EmailDraft
