"""Tests for the synthetic Google Keep data generator."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GUARD = REPO_ROOT / "scripts" / "check_fixtures.py"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_same_seed_same_notes() -> None:
    """synthetic_keep_notes is deterministic: same args → identical output."""
    from potluck.testing.keep import synthetic_keep_notes

    notes_a = list(synthetic_keep_notes(50, seed=42))
    notes_b = list(synthetic_keep_notes(50, seed=42))
    assert notes_a == notes_b
    assert len(notes_a) == 50


def test_different_seeds_different_notes() -> None:
    """Different seeds produce different outputs."""
    from potluck.testing.keep import synthetic_keep_notes

    assert list(synthetic_keep_notes(50, seed=1)) != list(synthetic_keep_notes(50, seed=2))


def test_byte_identical_archives_same_seed(tmp_path: Path) -> None:
    """write_keep_takeout is byte-identical for the same arguments (zip)."""
    from potluck.testing.keep import write_keep_takeout

    p1 = write_keep_takeout(tmp_path / "a", 20, seed=1, fmt="zip")
    p2 = write_keep_takeout(tmp_path / "b", 20, seed=1, fmt="zip")
    assert p1.read_bytes() == p2.read_bytes()


def test_byte_identical_archives_tgz(tmp_path: Path) -> None:
    """write_keep_takeout tgz is byte-identical for same args."""
    from potluck.testing.keep import write_keep_takeout

    p1 = write_keep_takeout(tmp_path / "a", 10, seed=5, fmt="tgz")
    p2 = write_keep_takeout(tmp_path / "b", 10, seed=5, fmt="tgz")
    assert p1.read_bytes() == p2.read_bytes()


# ---------------------------------------------------------------------------
# Ratio checks (count=200 for statistical stability, deterministic seed)
# ---------------------------------------------------------------------------


def test_trashed_ratio_honored(count: int = 200, seed: int = 42) -> None:
    """trashed_ratio=0.05 → roughly 5% of notes are trashed.

    Tolerance is ±5 (~1.5 std-devs for Binomial(200, 0.05)) to accommodate
    the natural spread of the deterministic RNG at this count.
    """
    from potluck.testing.keep import synthetic_keep_notes

    notes = list(synthetic_keep_notes(count, seed=seed, trashed_ratio=0.05))
    trashed = sum(1 for n in notes if n.get("isTrashed"))
    expected = round(count * 0.05)  # 10
    assert abs(trashed - expected) <= 5, f"trashed={trashed}, expected ~{expected}"


def test_list_ratio_honored(count: int = 200, seed: int = 42) -> None:
    """list_ratio=0.3 → roughly 30% of (non-empty) notes use listContent."""
    from potluck.testing.keep import synthetic_keep_notes

    notes = list(synthetic_keep_notes(count, seed=seed, list_ratio=0.3, empty_ratio=0.0))
    list_notes = sum(1 for n in notes if "listContent" in n and n["listContent"])
    expected = round(count * 0.3)  # 60
    assert abs(list_notes - expected) <= 6, f"list_notes={list_notes}, expected ~{expected}"


def test_labeled_ratio_honored(count: int = 200, seed: int = 42) -> None:
    """labeled_ratio=0.2 → roughly 20% of notes have labels."""
    from potluck.testing.keep import synthetic_keep_notes

    notes = list(synthetic_keep_notes(count, seed=seed, labeled_ratio=0.2))
    labeled = sum(1 for n in notes if n.get("labels"))
    expected = round(count * 0.2)  # 40
    assert abs(labeled - expected) <= 6, f"labeled={labeled}, expected ~{expected}"


def test_empty_ratio_honored(count: int = 200, seed: int = 42) -> None:
    """empty_ratio=0.02 → roughly 2% of notes have neither text nor title."""
    from potluck.testing.keep import synthetic_keep_notes

    notes = list(synthetic_keep_notes(count, seed=seed, empty_ratio=0.02))
    empty = sum(
        1 for n in notes if not (n.get("textContent") or n.get("listContent") or n.get("title"))
    )
    expected = round(count * 0.02)  # 4
    assert abs(empty - expected) <= 2, f"empty={empty}, expected ~{expected}"


# ---------------------------------------------------------------------------
# PII policy compliance
# ---------------------------------------------------------------------------


def test_all_emails_use_allowed_domains() -> None:
    """Every sharee email ends with @potluck.test or @example.com."""
    from potluck.testing.keep import synthetic_keep_notes

    notes = list(synthetic_keep_notes(200, seed=99))
    for note in notes:
        for sharee in note.get("sharees") or []:
            if isinstance(sharee, dict):
                email: str = str(sharee.get("email", ""))
                if email:
                    assert email.endswith("@potluck.test") or email.endswith("@example.com"), (
                        f"Disallowed email domain in sharee: {email}"
                    )


def test_all_annotation_urls_use_example_com() -> None:
    """Every annotation URL starts with https://example.com."""
    from potluck.testing.keep import synthetic_keep_notes

    notes = list(synthetic_keep_notes(200, seed=99))
    for note in notes:
        for ann in note.get("annotations") or []:
            if isinstance(ann, dict):
                url: str = str(ann.get("url", ""))
                if url:
                    assert url.startswith("https://example.com"), (
                        f"Annotation URL doesn't use example.com: {url}"
                    )


def test_pii_guard_compliance(tmp_path: Path) -> None:
    """Generated dir archive passes scripts/check_fixtures.py (no PII, no oversized files)."""
    from potluck.testing.keep import write_keep_takeout

    archive_path = write_keep_takeout(tmp_path / "fixture", count=30, seed=42, fmt="dir")
    proc = subprocess.run(
        [sys.executable, str(GUARD), str(archive_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"PII guard failed:\n{proc.stdout}"


# ---------------------------------------------------------------------------
# Archive structure
# ---------------------------------------------------------------------------


def test_archive_contains_keep_json_members(tmp_path: Path) -> None:
    """Generated zip archive contains Takeout/Keep/*.json members."""
    from potluck.ingest.readers import open_archive
    from potluck.testing.keep import write_keep_takeout

    path = write_keep_takeout(tmp_path / "arch", count=5, seed=3, fmt="zip")
    names = list(open_archive(path).iter_names())
    keep_jsons = [n for n in names if n.startswith("Takeout/Keep/") and n.endswith(".json")]
    assert len(keep_jsons) == 5


def test_archive_contains_labels_txt(tmp_path: Path) -> None:
    """Generated archive includes Takeout/Keep/Labels.txt when notes have labels."""
    from potluck.ingest.readers import open_archive
    from potluck.testing.keep import write_keep_takeout

    # labeled_ratio=1.0: every note has labels → Labels.txt must be present
    path = write_keep_takeout(tmp_path / "arch", count=5, seed=3, fmt="zip", labeled_ratio=1.0)
    names = list(open_archive(path).iter_names())
    assert "Takeout/Keep/Labels.txt" in names


def test_archive_contains_decoy_other_file(tmp_path: Path) -> None:
    """Generated archive includes Takeout/Other/ignored.txt as a decoy."""
    from potluck.ingest.readers import open_archive
    from potluck.testing.keep import write_keep_takeout

    path = write_keep_takeout(tmp_path / "arch", count=5, seed=3, fmt="zip")
    names = list(open_archive(path).iter_names())
    assert "Takeout/Other/ignored.txt" in names


def test_archive_has_attachment_jpg_decoy(tmp_path: Path) -> None:
    """At least one note has an attachment with a matching .jpg member in the archive."""
    from potluck.ingest.readers import open_archive
    from potluck.testing.keep import synthetic_keep_notes, write_keep_takeout

    count = 20
    seed = 7
    path = write_keep_takeout(tmp_path / "arch", count=count, seed=seed, fmt="zip")

    notes = list(synthetic_keep_notes(count, seed=seed))
    expected_jpgs = {
        f"Takeout/Keep/{att['filePath']}"
        for note in notes
        for att in (note.get("attachments") or [])
        if isinstance(att, dict) and att.get("filePath")
    }
    assert len(expected_jpgs) >= 1, "Expected at least one note with attachments"

    archive_names = set(open_archive(path).iter_names())
    for jpg_path in expected_jpgs:
        assert jpg_path in archive_names, f"Missing attachment decoy: {jpg_path}"


def test_multipart_write_creates_correct_files(tmp_path: Path) -> None:
    """parts=2 creates two tgz files named takeout-synth-001.tgz, takeout-synth-002.tgz."""
    from potluck.testing.keep import write_keep_takeout

    result = write_keep_takeout(tmp_path, count=10, seed=42, fmt="tgz", parts=2)
    assert result == tmp_path / "takeout-synth-001.tgz"
    assert (tmp_path / "takeout-synth-001.tgz").exists()
    assert (tmp_path / "takeout-synth-002.tgz").exists()


def test_write_keep_takeout_dir_returns_directory(tmp_path: Path) -> None:
    """fmt='dir' returns a directory path."""
    from potluck.testing.keep import write_keep_takeout

    result = write_keep_takeout(tmp_path / "dest", count=5, seed=1, fmt="dir")
    assert result.is_dir()


def test_write_keep_takeout_dir_is_openable(tmp_path: Path) -> None:
    """fmt='dir' archive is openable with open_archive and has correct members."""
    from potluck.ingest.readers import open_archive
    from potluck.testing.keep import write_keep_takeout

    result = write_keep_takeout(tmp_path / "dest", count=5, seed=1, fmt="dir")
    names = list(open_archive(result).iter_names())
    keep_jsons = [n for n in names if n.startswith("Takeout/Keep/") and n.endswith(".json")]
    assert len(keep_jsons) == 5


# ---------------------------------------------------------------------------
# Note dict structure
# ---------------------------------------------------------------------------


def test_note_has_required_fields() -> None:
    """Every generated note dict has all documented required fields."""
    from potluck.testing.keep import synthetic_keep_notes

    notes = list(synthetic_keep_notes(10, seed=42))
    for note in notes:
        assert "color" in note
        assert "isTrashed" in note
        assert "isPinned" in note
        assert "isArchived" in note
        # createdTimestampUsec must always be present (may be 0 for odd cases)
        assert "createdTimestampUsec" in note


def test_labels_txt_content(tmp_path: Path) -> None:
    """Labels.txt contains one label name per line matching the generator's fixed label set."""
    from potluck.ingest.readers import open_archive
    from potluck.testing.keep import write_keep_takeout

    path = write_keep_takeout(tmp_path / "arch", count=20, seed=5, fmt="zip", labeled_ratio=1.0)
    archive = open_archive(path)
    for _member, stream in archive.iter_members("Takeout/Keep/Labels.txt"):
        content = stream.read().decode()
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        # Labels are from the fixed set: Inspiration, Work, Personal
        valid_labels = {"Inspiration", "Work", "Personal"}
        for label in lines:
            assert label in valid_labels, f"Unexpected label: {label}"
        break
