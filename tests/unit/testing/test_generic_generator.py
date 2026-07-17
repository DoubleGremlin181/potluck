"""Generic-folder generator (#150): determinism, closed forms, member set."""

from pathlib import Path

from potluck.testing.generic import (
    EXPECTED_IMAGE_ITEMS,
    MAIL_COUNT,
    expected_note_count,
    generic_members,
    write_generic_folder,
)


def test_deterministic_same_args() -> None:
    assert generic_members(6, seed=7) == generic_members(6, seed=7)


def test_seed_changes_content() -> None:
    a = generic_members(6, seed=7)
    b = generic_members(6, seed=8)
    assert a.keys() == b.keys()
    assert a != b


def test_expected_note_count_closed_form() -> None:
    members = generic_members(8, seed=7)
    note_names = [n for n in members if n.endswith((".txt", ".md", ".markdown"))]
    assert len(note_names) == expected_note_count(8)


def test_image_and_mail_member_set() -> None:
    members = generic_members(0, seed=7)
    image_names = [n for n in members if n.lower().endswith((".jpg", ".png", ".webp"))]
    # 4 unique items + the byte-identical copy + the corrupt skip = 6 members.
    assert len(image_names) == EXPECTED_IMAGE_ITEMS + 2
    assert members["Pictures/2024/exif-gps.jpg"] == members["Pictures/copy/exif-gps.jpg"]
    assert (
        members["mail/archive.mbox"].count(b"\nMessage-ID:")
        + members["mail/archive.mbox"].count(b"\nMessage-Id:")
        <= MAIL_COUNT
    )  # some entries deliberately lack one


def test_oversize_member_only_on_request() -> None:
    assert "Notes/huge-trace.txt" not in generic_members(0, seed=7)
    members = generic_members(0, seed=7, oversize=True)
    assert len(members["Notes/huge-trace.txt"]) == 10 * 1024 * 1024 + 1


def test_write_formats_agree(tmp_path: Path) -> None:
    from potluck.ingest.readers import open_archive

    folder = write_generic_folder(tmp_path / "d", 2, seed=7, fmt="dir")
    archive = write_generic_folder(tmp_path / "z", 2, seed=7, fmt="zip")
    assert set(open_archive(folder).iter_names()) == set(open_archive(archive).iter_names())
