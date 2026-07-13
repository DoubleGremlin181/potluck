"""Draft DTOs — what a source plugin yields before the engine assigns ids/hashes."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

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


class PostDraft(BaseDraft):
    """Draft for a social post item (authored posts, comments, follows).

    Everything lives in the base fields: title = post title (None for
    comments), text = body, ts = creation time; source-specific context
    (community, permalink, parent pointers) rides in meta. No satellite
    table, so no extra_hash_parts — meta changes reconcile through the
    engine's identity path.
    """

    kind: Literal[ItemKind.POST] = ItemKind.POST


class BookmarkDraft(BaseDraft):
    """Draft for a saved-link/bookmark item.

    A bookmark records THAT something was saved, not the saved content
    itself (exports carry the pointer only). text is typically None; the
    exact link belongs in meta, with title carrying whatever human-readable
    name the source provides or the link implies.
    """

    kind: Literal[ItemKind.BOOKMARK] = ItemKind.BOOKMARK


class ActivityDraft(BaseDraft):
    """Draft for a usage-activity item (browser history, app usage).

    Everything lives in the base fields: title = page/app title, text = the
    searchable title + URL composition, ts = when the visit happened;
    source-specific context (transition type, device client id) rides in
    meta. No satellite table, so no extra_hash_parts — meta changes reconcile
    through the engine's identity path.
    """

    kind: Literal[ItemKind.ACTIVITY] = ItemKind.ACTIVITY


class EventDraft(BaseDraft):
    """Draft for a calendar event item.

    Everything lives in the base fields: title = summary, text = the
    searchable description + location composition, ts = the event start
    (DTSTART) in UTC; source-specific context (calendar name, status, end
    instant, all-day flag, recurrence rule/counts, attendee count) rides in
    meta. No satellite table, so no extra_hash_parts — meta changes reconcile
    through the engine's identity path. Recurring series are ONE draft per
    VEVENT (master + explicit overrides), never expanded occurrences — see
    potluck.ingest.sources.calendar for the policy.
    """

    kind: Literal[ItemKind.EVENT] = ItemKind.EVENT


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
    from_name: str | None = None
    to_addrs: tuple[str, ...] = ()
    to_names: tuple[str, ...] = ()  # positionally aligned with to_addrs; "" = no name
    cc_addrs: tuple[str, ...] = ()
    cc_names: tuple[str, ...] = ()
    bcc_addrs: tuple[str, ...] = ()
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
            self.from_name or "",
            "\x1f".join(self.to_addrs),
            "\x1f".join(self.to_names),
            "\x1f".join(self.cc_addrs),
            "\x1f".join(self.cc_names),
            "\x1f".join(self.bcc_addrs),
            "\x1f".join(self.labels),
            # filename and mime are persisted to the files satellite, so they
            # must be hash-covered like every other satellite field; \x1d
            # separates fields within one attachment (sha256 is fixed-length
            # hex, so the encoding stays injective).
            "\x1f".join(
                f"{a.sha256}\x1d{a.filename or ''}\x1d{a.mime or ''}" for a in self.attachments
            ),
        )


class TransactionDraft(BaseDraft):
    """Draft for a financial transaction; satellite fields land in the
    transactions table.

    Money discipline (#144): ``amount_milliunits`` is the exact signed amount
    in integer milliunits (1/1000 of the budget's currency unit; outflows
    negative). ``strict=True`` rejects float input outright — no float ever
    carries money through the pipeline. title = payee, text = searchable
    memo/category composition, ts = transaction date; the register carries no
    currency column (it is a budget-level setting), so none is stored.
    """

    kind: Literal[ItemKind.TRANSACTION] = ItemKind.TRANSACTION
    amount_milliunits: int = Field(strict=True)
    account: str | None = None
    payee: str | None = None
    category: str | None = None
    category_group: str | None = None

    def extra_hash_parts(self) -> tuple[str, ...]:
        # Covers EVERY satellite-persisted field (transactions row) — see
        # BaseDraft.extra_hash_parts. All parts are fixed-position scalars.
        return (
            str(self.amount_milliunits),
            self.account or "",
            self.payee or "",
            self.category or "",
            self.category_group or "",
        )


class LocationDraft(BaseDraft):
    """Draft for a location item (timeline visit / route / raw position);
    satellite fields land in the locations table.

    The base lat/lon fields are narrowed to REQUIRED, strict, range-validated
    floats: a location item without coordinates is meaningless, and a parsing
    bug (degree-sign string passed through unparsed, impossible latitude)
    must die at the DTO boundary instead of entering storage. Routes carry
    both end coordinates or neither (lat/lon = start, end_lat/end_lon = end);
    title is the human place/activity name, ts the visit/route start; the
    meta.type discriminator (visit | route | position) separates the flavors.
    """

    kind: Literal[ItemKind.LOCATION] = ItemKind.LOCATION
    lat: float = Field(strict=True, ge=-90.0, le=90.0)
    lon: float = Field(strict=True, ge=-180.0, le=180.0)
    end_lat: float | None = Field(default=None, strict=True, ge=-90.0, le=90.0)
    end_lon: float | None = Field(default=None, strict=True, ge=-180.0, le=180.0)
    place_id: str | None = None
    semantic_type: str | None = None
    distance_m: float | None = Field(default=None, strict=True, ge=0.0)

    @model_validator(mode="after")
    def _ends_come_paired(self) -> "LocationDraft":
        if (self.end_lat is None) != (self.end_lon is None):
            raise ValueError("end_lat and end_lon must be set together (route ends are pairs)")
        return self

    def extra_hash_parts(self) -> tuple[str, ...]:
        # Covers EVERY satellite-persisted field (locations row) — see
        # BaseDraft.extra_hash_parts. lat/lon are base fields, already inside
        # the base hash; only the satellite-only columns need covering here.
        # repr() matches the base hash's float encoding.
        return (
            repr(self.end_lat) if self.end_lat is not None else "",
            repr(self.end_lon) if self.end_lon is not None else "",
            self.place_id or "",
            self.semantic_type or "",
            repr(self.distance_m) if self.distance_m is not None else "",
        )


class MessageMedia(BaseModel):
    """Media reference carried on a MessageDraft; metadata only, never bytes.

    Chat exports name the media file but expose neither size nor content at
    parse time (pixels are deferred to P6), so this is deliberately thinner
    than EmailAttachment.
    """

    model_config = ConfigDict(frozen=True)

    filename: str
    mime: str | None = None


class MessageDraft(BaseDraft):
    """Draft for a chat message; satellite fields land in the messages table.

    text = message body (None for bare media placeholders), ts = message
    timestamp, title unused. chat_key is the deterministic conversation key —
    every message in one chat shares it (chats are linear; no parent_id
    chaining). sender is the display name exactly as exported (contact name
    or phone string).
    """

    kind: Literal[ItemKind.MESSAGE] = ItemKind.MESSAGE
    chat_key: str
    chat_name: str | None = None
    sender: str | None = None
    is_media: bool = False
    media: tuple[MessageMedia, ...] = ()

    def extra_hash_parts(self) -> tuple[str, ...]:
        # Covers EVERY satellite-persisted field (messages row + media files
        # rows) — see BaseDraft.extra_hash_parts. Same separator scheme as
        # EmailDraft: \x1f between media entries, \x1d within one entry.
        return (
            self.chat_key,
            self.chat_name or "",
            self.sender or "",
            "1" if self.is_media else "0",
            "\x1f".join(f"{m.filename}\x1d{m.mime or ''}" for m in self.media),
        )


type ItemDraft = (
    NoteDraft
    | EmailDraft
    | MessageDraft
    | PostDraft
    | BookmarkDraft
    | TransactionDraft
    | ActivityDraft
    | EventDraft
    | LocationDraft
)
