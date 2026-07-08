"""Tests for potluck.ingest.plugins: Glob, SourcePlugin, source, discover, detect_sources."""

from collections.abc import Iterator
from typing import IO, Any

import pytest

from potluck.ingest.plugins import ParseContext
from potluck.ingest.readers import Archive, Member
from potluck.models.drafts import NoteDraft
from potluck.models.items import ItemKind

# ---------------------------------------------------------------------------
# Fake archive helpers implementing the Archive Protocol
# ---------------------------------------------------------------------------


class FakeArchive:
    """Minimal Archive stub for testing detect_sources."""

    def __init__(self, names: list[str]) -> None:
        self._names = names

    def iter_names(self) -> Iterator[str]:
        yield from self._names

    def iter_members(self, pattern: str) -> Iterator[tuple[Member, IO[bytes]]]:
        return iter([])


class CountingArchive:
    """Archive that records how many names were served — tests early-exit behaviour."""

    def __init__(self, names: list[str]) -> None:
        self._names = names
        self.served: int = 0

    def iter_names(self) -> Iterator[str]:
        for name in self._names:
            self.served += 1
            yield name

    def iter_members(self, pattern: str) -> Iterator[tuple[Member, IO[bytes]]]:
        return iter([])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_glob_matches() -> None:
    from potluck.ingest.plugins import Glob

    g = Glob("*Keep/*.json")
    assert g.matches("Takeout/Keep/a.json") is True
    assert g.matches("Takeout/Keep/a.html") is False
    # fnmatch: '*' crosses '/', so 'Keep/a.json' also matches
    assert g.matches("Keep/a.json") is True
    assert g.matches("Other/a.json") is False


def test_glob_alternation_matches_any_of() -> None:
    """'|' separates alternative patterns (#142): export layouts legitimately
    vary per platform (WhatsApp Android vs iOS naming)."""
    from potluck.ingest.plugins import Glob

    g = Glob("*WhatsApp Chat*.txt|_chat.txt|*/_chat.txt")
    assert g.matches("WhatsApp Chat with Ada.txt") is True
    assert g.matches("_chat.txt") is True
    assert g.matches("WhatsApp Chat - Ada/_chat.txt") is True
    assert g.matches("my_chat.txt") is False
    assert g.matches("notes.txt") is False
    # single-pattern globs behave exactly as before
    assert Glob("*.json").matches("a.json") is True


def test_glob_matches_case_sensitive_on_every_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Archive member names are virtual posix paths: matching must not pick up
    the host platform's case folding (os.path.normcase lowercases on Windows,
    which would make imports diverge per platform)."""
    import os.path

    from potluck.ingest.plugins import Glob

    monkeypatch.setattr(os.path, "normcase", lambda s: s.lower())  # simulate Windows
    assert Glob("*Keep/*.json").matches("Takeout/KEEP/a.JSON") is False
    assert Glob("*Keep/*.json").matches("Takeout/Keep/a.json") is True


def test_source_decorator_registers(clean_registry: dict[str, Any]) -> None:
    from potluck.ingest.plugins import Glob, SourcePlugin, source

    @source(
        name="test_reg_plugin",
        detect=Glob("*Test/*.txt"),
        kinds=(ItemKind.NOTE,),
        parser_version=1,
    )
    def parse(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    assert "test_reg_plugin" in clean_registry
    plugin: SourcePlugin = clean_registry["test_reg_plugin"]
    assert plugin.name == "test_reg_plugin"
    assert plugin.parser_version == 1
    assert plugin.kinds == (ItemKind.NOTE,)
    # Decorator returns the function unchanged
    assert callable(parse)


def test_duplicate_name_raises(clean_registry: dict[str, Any]) -> None:
    from potluck.core.errors import DuplicateSourceError
    from potluck.ingest.plugins import Glob, source

    @source(name="dup_plugin", detect=Glob("*.txt"), kinds=(ItemKind.NOTE,))
    def parse1(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    with pytest.raises(DuplicateSourceError, match="dup_plugin"):

        @source(name="dup_plugin", detect=Glob("*.txt"), kinds=(ItemKind.NOTE,))
        def parse2(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
            notes: list[NoteDraft] = []
            yield from notes


def test_detect_sources_collects_all_matches(clean_registry: dict[str, Any]) -> None:
    """#195: combined archives (Keep+Mail) must surface EVERY matching plugin."""
    from potluck.ingest.plugins import Glob, detect_sources, source

    @source(name="zzz_mail", detect=Glob("*Mail/*.mbox"), kinds=(ItemKind.EMAIL,))
    def parse_mail(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    @source(name="aaa_keep", detect=Glob("*Keep/*.json"), kinds=(ItemKind.NOTE,))
    def parse_keep(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    combined = FakeArchive(
        ["Takeout/Mail/All mail.mbox", "Takeout/Keep/x.json", "Takeout/Other/y.txt"]
    )
    result = detect_sources(combined)
    # deterministic run order: sorted by plugin name
    assert [p.name for p in result] == ["aaa_keep", "zzz_mail"]

    single = FakeArchive(["Takeout/Keep/x.json"])
    assert [p.name for p in detect_sources(single)] == ["aaa_keep"]

    # Archive with no matching names -> empty list
    assert detect_sources(FakeArchive(["README.md", "data.csv"])) == []


def test_detect_sources_single_pass_early_exit(clean_registry: dict[str, Any]) -> None:
    """Iteration stops as soon as every registered plugin has matched."""
    from potluck.ingest.plugins import Glob, detect_sources, source

    @source(name="early_exit_plugin", detect=Glob("match.txt"), kinds=(ItemKind.NOTE,))
    def parse(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
        notes: list[NoteDraft] = []
        yield from notes

    # The only registered plugin matches the first name — stop after 1 name.
    counting = CountingArchive(["match.txt", "second.txt", "third.txt"])
    result = detect_sources(counting)

    assert [p.name for p in result] == ["early_exit_plugin"]
    assert counting.served == 1, (
        f"Expected early exit after 1 archive name, got {counting.served} served"
    )
