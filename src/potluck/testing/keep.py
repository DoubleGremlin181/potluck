"""Synthetic Google Keep data generator and archive builder.

Ships inside ``potluck.testing`` so tests, committed fixtures, and bench
scenarios share one deterministic source.  Same arguments → identical output
on every machine, forever.  Never put real personal data here.

Allowed synthetic domains:
- ``@potluck.test``
- ``@example.com``

Regenerate the golden fixture::

    python -c "
    from pathlib import Path
    from potluck.testing.keep import write_keep_takeout
    write_keep_takeout(Path('tests/fixtures/keep'), 12, seed=7, fmt='dir')
    "
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from potluck.testing.archives import split_parts, write_archive
from potluck.testing.generators import WORDS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_BASE_TS = datetime(2020, 1, 1, tzinfo=UTC)

_COLORS = ["DEFAULT", "DEFAULT", "DEFAULT", "DEFAULT", "GRAY", "BLUE", "GREEN"]
_FIXED_LABELS = ["Inspiration", "Work", "Personal"]
_SHAREE_USERS = [
    "alice@potluck.test",
    "bob@potluck.test",
    "carol@example.com",
    "dave@example.com",
]


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


def synthetic_keep_notes(
    count: int,
    seed: int = 42,
    *,
    list_ratio: float = 0.3,
    trashed_ratio: float = 0.05,
    empty_ratio: float = 0.02,
    labeled_ratio: float = 0.2,
) -> Iterator[dict[str, Any]]:
    """Yield ``count`` Keep-format JSON dicts using the real schema keys.

    All output is deterministic given the same ``(count, seed, *ratio)`` args.
    Generated content uses only synthetic data safe for committing:
    - Emails only at ``@potluck.test`` / ``@example.com``
    - URLs only at ``https://example.com/...``

    Args:
        count:        Number of notes to generate.
        seed:         RNG seed for reproducibility.
        list_ratio:   Fraction of notes that use ``listContent`` (rest use ``textContent``).
        trashed_ratio: Fraction of notes with ``isTrashed=True``.
        empty_ratio:  Fraction of notes with empty text AND empty title (parser skip targets).
        labeled_ratio: Fraction of notes with labels.

    Yields:
        Dicts matching the real Google Keep Takeout JSON schema.
    """
    rng = random.Random(seed)

    for i in range(count):
        # Timestamps (µs since epoch) — spread notes across 2020 onwards
        created_dt = _BASE_TS + timedelta(minutes=i * 11 + rng.randint(0, 9))
        created_usec = int((created_dt - _EPOCH).total_seconds() * 1_000_000)

        # userEditedTimestampUsec: sometimes 0/absent (odd Keep behaviour)
        if rng.random() < 0.1:
            user_edited_usec = 0
        else:
            user_edited_dt = created_dt + timedelta(minutes=rng.randint(0, 60))
            user_edited_usec = int((user_edited_dt - _EPOCH).total_seconds() * 1_000_000)

        note: dict[str, Any] = {
            "color": rng.choice(_COLORS),
            "isTrashed": rng.random() < trashed_ratio,
            "isPinned": rng.random() < 0.08,
            "isArchived": rng.random() < 0.06,
            "createdTimestampUsec": created_usec,
            "userEditedTimestampUsec": user_edited_usec,
        }

        is_empty = rng.random() < empty_ratio
        is_list = not is_empty and rng.random() < list_ratio

        if is_empty:
            # Skip target: no text, no title
            note["title"] = ""
        elif is_list:
            # Title: some list notes have empty titles
            if rng.random() < 0.25:
                note["title"] = ""
            else:
                note["title"] = " ".join(rng.choices(WORDS, k=3)).title()
            # List content
            item_count = rng.randint(2, 5)
            items: list[dict[str, Any]] = []
            for _ in range(item_count):
                item_text = " ".join(rng.choices(WORDS, k=rng.randint(2, 6)))
                items.append(
                    {
                        "text": item_text,
                        "textHtml": f"<p>{item_text}</p>",
                        "isChecked": rng.random() < 0.4,
                    }
                )
            note["listContent"] = items
        else:
            # Text note
            if rng.random() < 0.25:
                note["title"] = ""
            else:
                note["title"] = " ".join(rng.choices(WORDS, k=3)).title()
            text_words = rng.choices(WORDS, k=rng.randint(6, 20))
            text = " ".join(text_words).capitalize() + "."
            note["textContent"] = text
            note["textContentHtml"] = f"<p>{text}</p>"

        # Labels
        if rng.random() < labeled_ratio:
            label_count = rng.randint(1, 2)
            chosen = rng.sample(_FIXED_LABELS, min(label_count, len(_FIXED_LABELS)))
            note["labels"] = [{"name": lbl} for lbl in chosen]

        # Sharees (with allowed-domain emails)
        if rng.random() < 0.08:
            sharee_count = rng.randint(1, 2)
            chosen_users = rng.sample(_SHAREE_USERS, min(sharee_count, len(_SHAREE_USERS)))
            note["sharees"] = [{"email": u} for u in chosen_users]

        # Annotations (WEBLINK only; URLs use example.com)
        if rng.random() < 0.1:
            slug = "-".join(rng.choices(WORDS, k=2))
            desc = " ".join(rng.choices(WORDS, k=rng.randint(4, 8))).capitalize() + "."
            link_title = " ".join(rng.choices(WORDS, k=2)).title()
            note["annotations"] = [
                {
                    "source": "WEBLINK",
                    "url": f"https://example.com/{slug}",
                    "title": link_title,
                    "description": desc,
                }
            ]

        # Attachments: last note always gets one; others at low probability
        if i == count - 1 or rng.random() < 0.08:
            hex1 = rng.randrange(0x10000000, 0xFFFFFFFF)
            hex2 = rng.randrange(0x10000000, 0xFFFFFFFF)
            file_path = f"{hex1:08x}{hex2:08x}.{hex1 % 0x100:02x}{hex2 % 0x100:02x}.jpg"
            note["attachments"] = [{"filePath": file_path, "mimetype": "image/jpeg"}]

        yield note


# ---------------------------------------------------------------------------
# Archive builder
# ---------------------------------------------------------------------------


def _build_members(
    notes: list[dict[str, Any]],
    seed: int,
) -> dict[str, bytes]:
    """Build the ``{posix_path: bytes}`` members dict for a Keep Takeout archive."""
    rng = random.Random(seed + 9999)  # separate rng for naming (does not shift note rng)
    members: dict[str, bytes] = {}
    used_names: set[str] = set()

    for i, note in enumerate(notes):
        title: str = str(note.get("title") or "")
        created_usec: int = int(note.get("createdTimestampUsec") or 0)

        # Decide filename style deterministically
        if title and rng.random() < 0.45:
            # Title-derived name (keep spaces, truncate)
            sanitized = title[:30].replace("/", "_").replace("\\", "_")
            base_name = f"Takeout/Keep/{sanitized}.json"
        else:
            # Timestamp-style name
            if created_usec:
                dt = _EPOCH + timedelta(microseconds=created_usec)
                ts_str = dt.strftime("%Y-%m-%dT%H_%M_%S.000+00_00")
            else:
                ts_str = f"2020-01-01T00_00_00.000+00_00_{i:04d}"
            base_name = f"Takeout/Keep/{ts_str}.json"

        # Ensure uniqueness
        name = base_name
        suffix = 1
        while name in used_names:
            stem = base_name[: -len(".json")]
            name = f"{stem}_{suffix}.json"
            suffix += 1
        used_names.add(name)

        members[name] = json.dumps(note, indent=2).encode()

        # Decoy .html for every 3rd note
        if i % 3 == 0:
            html_name = name.replace(".json", ".html")
            members[html_name] = b"<html><body>placeholder</body></html>"

        # Attachment .jpg decoys
        for att in note.get("attachments") or []:
            if isinstance(att, dict) and att.get("filePath"):
                jpg_path = f"Takeout/Keep/{att['filePath']}"
                # Minimal JPEG header (3 bytes magic + padding)
                members[jpg_path] = b"\xff\xd8\xff" + b"\x00" * 10

    # Labels.txt — include when any note has labels
    labels_used: set[str] = set()
    for note in notes:
        for lbl in note.get("labels") or []:
            if isinstance(lbl, dict) and lbl.get("name"):
                labels_used.add(str(lbl["name"]))
    if labels_used:
        members["Takeout/Keep/Labels.txt"] = "\n".join(sorted(labels_used)).encode()

    # Decoy: Takeout/Other/ignored.txt (should not be parsed by Keep plugin)
    members["Takeout/Other/ignored.txt"] = b"this file should be ignored by the parser\n"

    return members


def write_keep_takeout(
    dest_dir: Path,
    count: int,
    seed: int = 42,
    *,
    fmt: Literal["zip", "tgz", "dir"] = "zip",
    parts: int = 1,
    labeled_ratio: float = 0.2,
) -> Path:
    """Materialise a synthetic Keep Takeout archive in ``dest_dir``.

    Args:
        dest_dir:      Destination directory (created if absent).
        count:         Number of note JSON files to generate.
        seed:          RNG seed; same seed → identical output.
        fmt:           Archive format: ``"zip"``, ``"tgz"``, or ``"dir"``.
        parts:         Number of parts (``>1`` splits via round-robin; dir format
                       always produces a single directory regardless of this value).
        labeled_ratio: Fraction of notes that receive labels (forwarded to
                       ``synthetic_keep_notes``).

    Returns:
        Path to the first (or only) archive part.  For ``fmt="dir"`` this is
        the root directory of the extracted tree.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    notes = list(synthetic_keep_notes(count, seed, labeled_ratio=labeled_ratio))
    members = _build_members(notes, seed)

    if fmt == "dir":
        # Directory archives are always single-part
        dest = dest_dir / "takeout-synth-001"
        write_archive(dest, members, "dir")
        return dest

    ext = "zip" if fmt == "zip" else "tgz"

    if parts == 1:
        dest = dest_dir / f"takeout-synth-001.{ext}"
        write_archive(dest, members, fmt)
        return dest

    # Multi-part
    part_dicts = split_parts(members, parts)
    first: Path | None = None
    for idx, part_dict in enumerate(part_dicts, start=1):
        part_path = dest_dir / f"takeout-synth-{idx:03d}.{ext}"
        write_archive(part_path, part_dict, fmt)
        if first is None:
            first = part_path

    if first is None:
        raise AssertionError("split_parts returned an empty list")  # unreachable
    return first
