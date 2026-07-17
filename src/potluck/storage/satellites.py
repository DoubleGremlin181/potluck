"""Per-kind satellite writers and readers: kind-specific tables riding items.

The engine stays kind-agnostic: after writing items rows it hands each
(draft, item_id) group to its kind's writer inside the same transaction —
a new satellite kind is one entry here, zero engine changes. Kinds without
an entry (e.g. NOTE) pay nothing.

Reads mirror writes (#200): SATELLITE_READERS hydrates a kind's detail DTO
onto item detail — one entry per kind, zero service changes.
"""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from potluck.models.drafts import (
    EmailDraft,
    ItemDraft,
    LocationDraft,
    MessageDraft,
    PhotoDraft,
    TransactionDraft,
)
from potluck.models.items import (
    AttachmentDetail,
    EmailDetail,
    ItemKind,
    LocationDetail,
    MediaDetail,
    MessageDetail,
    TransactionDetail,
)
from potluck.storage.emails import (
    draft_to_email_row,
    get_email_row,
    insert_emails,
    resolve_email_parents,
)
from potluck.storage.files import (
    FileRow,
    delete_files_for_items,
    insert_files,
    list_files_for_item,
)
from potluck.storage.locations import (
    draft_to_location_row,
    get_location_row,
    insert_locations,
)
from potluck.storage.media import draft_to_media_row, get_media_row, insert_media
from potluck.storage.messages import draft_to_message_row, get_message_row, insert_messages
from potluck.storage.transactions import (
    draft_to_transaction_row,
    get_transaction_row,
    insert_transactions,
)


@dataclass(frozen=True)
class SatelliteWriter:
    """Batch hooks for one ItemKind's satellite table.

    write_batch persists rows for (draft, item_id) pairs inside the batch
    transaction (batch-first: one executemany per statement shape).
    finalize, when present, runs once at end of import — set-based
    reconciliation that needs the whole corpus (e.g. email parent linking).
    """

    write_batch: Callable[[sqlite3.Connection, list[tuple[ItemDraft, int]]], None]
    finalize: Callable[[sqlite3.Connection, int], None] | None = None


def _write_email_batch(conn: sqlite3.Connection, pairs: list[tuple[ItemDraft, int]]) -> None:
    emails = [(draft, item_id) for draft, item_id in pairs if isinstance(draft, EmailDraft)]
    insert_emails(conn, [draft_to_email_row(draft, item_id) for draft, item_id in emails])

    # Attachment metadata (#124): replace each item's file set wholesale —
    # the DELETE is a no-op for fresh inserts. Unnamed parts get a stable
    # positional member_path.
    delete_files_for_items(conn, [item_id for _, item_id in emails])
    file_rows = [
        FileRow(
            item_id=item_id,
            member_path=att.filename or f"part-{position}",
            mime=att.mime,
            size_bytes=att.size_bytes,
            sha256=att.sha256,
        )
        for draft, item_id in emails
        for position, att in enumerate(draft.attachments, start=1)
    ]
    insert_files(conn, file_rows)


def _finalize_emails(conn: sqlite3.Connection, source_id: int) -> None:
    resolve_email_parents(conn, source_id)


def _write_message_batch(conn: sqlite3.Connection, pairs: list[tuple[ItemDraft, int]]) -> None:
    messages = [(draft, item_id) for draft, item_id in pairs if isinstance(draft, MessageDraft)]
    insert_messages(conn, [draft_to_message_row(draft, item_id) for draft, item_id in messages])

    # Media references (#142): metadata-only files rows (chat exports name the
    # file but expose no size/bytes — pixels are deferred to P6), replaced
    # wholesale on update exactly like email attachments. No finalize: chats
    # are linear, nothing to reconcile at end of run.
    delete_files_for_items(conn, [item_id for _, item_id in messages])
    file_rows = [
        FileRow(
            item_id=item_id,
            member_path=medium.filename,
            mime=medium.mime,
            size_bytes=None,
            sha256=None,
        )
        for draft, item_id in messages
        for medium in draft.media
    ]
    insert_files(conn, file_rows)


def _write_transaction_batch(conn: sqlite3.Connection, pairs: list[tuple[ItemDraft, int]]) -> None:
    # Register rows (#144): one satellite row per transaction, rewritten in
    # place on update (INSERT OR REPLACE on the item_id PK). No files, no
    # finalize: a register is flat, nothing to reconcile at end of run.
    txns = [(draft, item_id) for draft, item_id in pairs if isinstance(draft, TransactionDraft)]
    insert_transactions(conn, [draft_to_transaction_row(draft, item_id) for draft, item_id in txns])


def _write_location_batch(conn: sqlite3.Connection, pairs: list[tuple[ItemDraft, int]]) -> None:
    # Timeline rows (#148): one satellite row per location item, rewritten in
    # place on update (INSERT OR REPLACE on the item_id PK). No files, no
    # finalize: a timeline is flat, nothing to reconcile at end of run.
    locs = [(draft, item_id) for draft, item_id in pairs if isinstance(draft, LocationDraft)]
    insert_locations(conn, [draft_to_location_row(draft, item_id) for draft, item_id in locs])


def _write_media_batch(conn: sqlite3.Connection, pairs: list[tuple[ItemDraft, int]]) -> None:
    # Photo rows (#149): one satellite row per photo/video item, rewritten in
    # place on update (INSERT OR REPLACE on the item_id PK). No files rows
    # (the item IS its file — see migration 014), no finalize: an album tree
    # is flat, nothing to reconcile at end of run.
    photos = [(draft, item_id) for draft, item_id in pairs if isinstance(draft, PhotoDraft)]
    insert_media(conn, [draft_to_media_row(draft, item_id) for draft, item_id in photos])


SATELLITE_WRITERS: Final[dict[ItemKind, SatelliteWriter]] = {
    ItemKind.EMAIL: SatelliteWriter(write_batch=_write_email_batch, finalize=_finalize_emails),
    ItemKind.MESSAGE: SatelliteWriter(write_batch=_write_message_batch),
    ItemKind.TRANSACTION: SatelliteWriter(write_batch=_write_transaction_batch),
    ItemKind.LOCATION: SatelliteWriter(write_batch=_write_location_batch),
    ItemKind.PHOTO: SatelliteWriter(write_batch=_write_media_batch),
}


def _read_email_detail(conn: sqlite3.Connection, item_id: int) -> EmailDetail | None:
    """Hydrate the emails row + attachment list for one item (#200)."""
    row = get_email_row(conn, item_id)
    if row is None:
        return None
    return EmailDetail(
        message_id=row["message_id"],
        in_reply_to=row["in_reply_to"],
        thread_key=row["thread_key"],
        from_addr=row["from_addr"],
        from_name=row["from_name"],
        to_addrs=json.loads(row["to_json"]),
        to_names=json.loads(row["to_names_json"]),
        cc_addrs=json.loads(row["cc_json"]),
        cc_names=json.loads(row["cc_names_json"]),
        bcc_addrs=json.loads(row["bcc_json"]),
        labels=json.loads(row["labels_json"]),
        attachments=[
            AttachmentDetail(
                filename=f["member_path"],
                mime=f["mime"],
                size_bytes=f["size_bytes"],
                sha256=f["sha256"],
            )
            for f in list_files_for_item(conn, item_id)
        ],
    )


def _read_message_detail(conn: sqlite3.Connection, item_id: int) -> MessageDetail | None:
    """Hydrate the messages row + media references for one item (#142)."""
    row = get_message_row(conn, item_id)
    if row is None:
        return None
    return MessageDetail(
        chat_key=row["chat_key"],
        chat_name=row["chat_name"],
        sender=row["sender"],
        is_media=bool(row["is_media"]),
        media=[
            AttachmentDetail(
                filename=f["member_path"],
                mime=f["mime"],
                size_bytes=f["size_bytes"],
                sha256=f["sha256"],
            )
            for f in list_files_for_item(conn, item_id)
        ],
    )


def _read_transaction_detail(conn: sqlite3.Connection, item_id: int) -> TransactionDetail | None:
    """Hydrate the transactions row for one item (#144)."""
    row = get_transaction_row(conn, item_id)
    if row is None:
        return None
    return TransactionDetail(
        amount_milliunits=row["amount_milliunits"],
        account=row["account"],
        payee=row["payee"],
        category=row["category"],
        category_group=row["category_group"],
    )


def _read_location_detail(conn: sqlite3.Connection, item_id: int) -> LocationDetail | None:
    """Hydrate the locations row for one item (#148)."""
    row = get_location_row(conn, item_id)
    if row is None:
        return None
    return LocationDetail(
        lat=row["lat"],
        lon=row["lon"],
        end_lat=row["end_lat"],
        end_lon=row["end_lon"],
        place_id=row["place_id"],
        semantic_type=row["semantic_type"],
        distance_m=row["distance_m"],
    )


def _read_media_detail(conn: sqlite3.Connection, item_id: int) -> MediaDetail | None:
    """Hydrate the media row for one item (#149)."""
    row = get_media_row(conn, item_id)
    if row is None:
        return None
    return MediaDetail(
        width=row["width"],
        height=row["height"],
        camera_make=row["camera_make"],
        camera_model=row["camera_model"],
        gps_alt=row["gps_alt"],
        mime=row["mime"],
        size_bytes=row["size_bytes"],
        sha256=row["sha256"],
    )


# Detail DTO union grows with reader kinds; the service assigns by type.
SATELLITE_READERS: Final[
    dict[
        ItemKind,
        Callable[
            [sqlite3.Connection, int],
            EmailDetail | MessageDetail | TransactionDetail | LocationDetail | MediaDetail | None,
        ],
    ]
] = {
    ItemKind.EMAIL: _read_email_detail,
    ItemKind.MESSAGE: _read_message_detail,
    ItemKind.TRANSACTION: _read_transaction_detail,
    ItemKind.LOCATION: _read_location_detail,
    ItemKind.PHOTO: _read_media_detail,
}
