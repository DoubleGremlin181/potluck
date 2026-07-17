"""Deterministic synthetic generic-folder generator (#150).

Ships inside ``potluck.testing`` so tests, the committed fixture, and bench
scenarios share one deterministic source. Same arguments → identical bytes
on one machine and Pillow release (image bytes depend on the installed
encoder; cross-version byte identity is pinned by the COMMITTED fixture,
never by regeneration — the photos posture). Never put real personal data
here — names are fixture names, coordinates live on the fictional
(40.x, -74/75.x) grid, mail comes from the synthetic mbox generator's
``potluck.test`` domains.

The member set is a folder that no specific source recognizes — the shape
the generic tier (#150) exists for — exercising every parser policy:

- **Notes**: markdown with an H1, markdown without one, plain txt, the long
  ``.markdown`` extension, a 0-byte file (title-only note), a latin-1 byte
  that must decode via ``errors="replace"``, and ``count`` bulk notes
  (``Notes/bulk/…``, md/txt alternating, H1 on every fourth). Decoy:
  ``WhatsApp Chat ideas.md`` — the name baits WhatsApp's glob but the
  extension misses it (``*WhatsApp Chat*.txt``), so it must import as a
  note, proving detection precision rather than tripping tier fallback.
- **Images**: an EXIF+GPS JPEG, a byte-identical copy of it in another
  folder (the dedup pair: one item, one engine duplicate), a no-EXIF PNG
  (ts falls back to member mtime), a WEBP, an uppercase ``.JPG`` (DCIM
  naming), and a corrupt ``.jpg`` that must SKIP with one warning.
- **Mbox**: ``mail/archive.mbox`` with MAIL_COUNT messages from the P2
  synthetic mbox generator (reply chains, HTML-only bodies, attachments).
- Neutral decoys nothing matches: ``misc/data.csv``, an
  ``archive_browser.html``.
- ``oversize=True`` adds ``Notes/huge-trace.txt`` just over the notes size
  cap (10 MiB) — skipped with a warning. Excluded from the committed
  fixture: the PII guard caps fixture files at 1 MiB, so the cap case lives
  in unit tests over generated archives.

Regenerate the golden fixture::

    python -c "
    from pathlib import Path
    from potluck.testing.generic import write_generic_folder
    write_generic_folder(Path('tests/fixtures/generic'), 8, seed=7)
    "
"""

from pathlib import Path
from typing import Literal

from potluck.testing.archives import write_archive
from potluck.testing.generators import WORDS
from potluck.testing.mbox import synthetic_mbox_messages
from potluck.testing.photos import tiny_image

MAIL_COUNT = 6

# Fixed specials contribute this many note items on top of the bulk count:
# journal H1, no-heading, todo txt, .markdown guide, empty, WhatsApp-named
# decoy, latin-1 replacement.
_SPECIAL_NOTES = 7
# Unique image items (the byte-identical copy dedups; the corrupt one skips).
EXPECTED_IMAGE_ITEMS = 4
EXPECTED_IMAGE_DUPLICATES = 1  # the Pictures/copy re-yield
EXPECTED_IMAGE_WARNINGS = 1  # the corrupt .jpg skip

# Just over the notes source's 10 MiB cap (kept literal here: potluck.testing
# depends on no ingest module).
_OVERSIZE_BYTES = 10 * 1024 * 1024 + 1


def expected_note_count(count: int) -> int:
    """Note items the parser yields for a *count*-bulk folder."""
    return count + _SPECIAL_NOTES


def _words(salt: int, i: int, k: int) -> str:
    return " ".join(WORDS[(salt + i * 7 + j * 3) % len(WORDS)] for j in range(k))


def _note_members(count: int, salt: int) -> dict[str, bytes]:
    members: dict[str, bytes] = {
        "Notes/journal/synth-plan.md": (
            "Preamble before the heading.\n\n# Synthetic Plan\n\n" + _words(salt, 1, 8) + "\n"
        ).encode(),
        "Notes/no-heading.md": ("## only a subheading\n" + _words(salt, 2, 6) + "\n").encode(),
        "Notes/todo-list.txt": b"- buy synthetic groceries\n- water the fixture plants\n",
        "Notes/guide.markdown": ("# Field Guide\n" + _words(salt, 3, 6) + "\n").encode(),
        "Notes/empty.txt": b"",
        "Notes/WhatsApp Chat ideas.md": (_words(salt, 4, 5) + "\n").encode(),
        "Notes/latin-caf.txt": b"caf\xe9 plans with deliberately invalid utf-8\n",
    }
    for i in range(count):
        if i % 2 == 0:
            heading = f"# Bulk Note {i:04d}\n" if i % 4 == 0 else ""
            members[f"Notes/bulk/note-{i:04d}.md"] = (
                heading + _words(salt, 10 + i, 7) + "\n"
            ).encode()
        else:
            members[f"Notes/bulk/note-{i:04d}.txt"] = (_words(salt, 10 + i, 7) + "\n").encode()
    return members


def _image_members(salt: int) -> dict[str, bytes]:
    exif_gps = tiny_image(
        color=(salt % 256, 40, 200),
        make="SynthCam",
        model="SC-G",
        taken="2024:03:01 08:00:00",
        gps=(40.71, -74.29, 12.0),
    )
    return {
        "Pictures/2024/exif-gps.jpg": exif_gps,
        "Pictures/copy/exif-gps.jpg": exif_gps,  # byte-identical: ONE item
        "Pictures/2024/no-exif.png": tiny_image("PNG", color=(10, (salt // 3) % 256, 60)),
        "Pictures/webp-shot.webp": tiny_image("WEBP", color=(200, 10, (salt // 7) % 256)),
        "Pictures/UPPER-CASE.JPG": tiny_image(color=(90, 90, salt % 256)),
        "Pictures/corrupt.jpg": b"synthetic bytes that are not an image\n",
    }


def generic_members(count: int, seed: int = 42, *, oversize: bool = False) -> dict[str, bytes]:
    """The member set of one synthetic generic folder ({posix_name: content})."""
    salt = seed * 1009
    members = _note_members(count, salt)
    members.update(_image_members(salt))
    members["mail/archive.mbox"] = b"".join(synthetic_mbox_messages(MAIL_COUNT, seed))
    members["misc/data.csv"] = b"a,b\n1,2\n"
    members["misc/archive_browser.html"] = b"<html>synthetic decoy</html>"
    if oversize:
        members["Notes/huge-trace.txt"] = b"x" * _OVERSIZE_BYTES
    return members


def write_generic_folder(
    dest_dir: Path,
    count: int,
    seed: int = 42,
    *,
    fmt: Literal["zip", "tgz", "dir"] = "dir",
    oversize: bool = False,
) -> Path:
    """Materialise a synthetic generic folder (or archive) in *dest_dir*.

    Returns the archive path (or the directory root for ``fmt="dir"``).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    members = generic_members(count, seed, oversize=oversize)
    if fmt == "dir":
        dest = dest_dir / "generic-synth-001"
        write_archive(dest, members, "dir")
        return dest
    ext = "zip" if fmt == "zip" else "tgz"
    dest = dest_dir / f"generic-synth-001.{ext}"
    write_archive(dest, members, fmt)
    return dest
