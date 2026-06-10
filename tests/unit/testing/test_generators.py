"""Synthetic generators: deterministic, stdlib-only, shipped in the package."""

from potluck.testing.generators import synthetic_notes


def test_same_seed_same_output() -> None:
    a = list(synthetic_notes(50, seed=7))
    b = list(synthetic_notes(50, seed=7))
    assert a == b
    assert len(a) == 50


def test_different_seed_different_output() -> None:
    assert list(synthetic_notes(50, seed=7)) != list(synthetic_notes(50, seed=8))


def test_note_shape() -> None:
    note = next(iter(synthetic_notes(1)))
    assert set(note) == {"title", "text", "ts"}
    assert note["title"]
    assert note["text"].endswith(".")
    assert note["ts"].startswith("20")
